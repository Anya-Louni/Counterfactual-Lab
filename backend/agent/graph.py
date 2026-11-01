"""LangGraph agent: interpret -> validate -> confirm (interrupt) -> apply -> compute -> compare -> narrate.

The LLM is called in interpret (one short tool loop) and narrate. All numbers come from code.
"""

import json
import operator
import os
import time
from typing import Annotated, Any, TypedDict

import numpy as np
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import TypeAdapter

from backend.geocode.local import Place
from backend.geocode.nominatim import geocode
from backend.network.edits import Edit, to_xy
from backend.network.validate import validate as validate_edits

from . import facts as facts_mod
from .numbers import unsupported
from .plan import PlanSpec, resolve
from .prompts import INTERPRET, NARRATE

EDITS = TypeAdapter(list[Edit])
MAX_TOOL_ROUNDS = 6


class LabState(TypedDict, total=False):
    request: str
    current_id: str  # scenario the user is looking at
    clicks: list[dict]
    places: dict[str, dict]
    plan: dict
    edits: list[dict]
    problems: list[dict]
    repairs: int
    parent_id: str
    scenario_id: str
    facts: dict
    narrative: str
    status: str  # cancelled | failed | done
    provenance: Annotated[list[dict], operator.add]


def default_model():
    from langchain.chat_models import init_chat_model
    # Few retries on the main model: when the provider is busy, switching to the fallback is
    # faster than backing off six times.
    main = init_chat_model(os.environ.get("LAB_MODEL", "google_genai:gemini-3.1-flash-lite"), temperature=0,
                           max_retries=1, timeout=30)
    fb = os.environ.get("LAB_FALLBACK_MODEL")
    return (main, init_chat_model(fb, temperature=0, max_retries=2, timeout=60)) if fb else (main, None)


def _emit(**kw):
    try:
        get_stream_writer()(kw)
    except Exception:  # outside a streaming run
        pass


def build(lab, model=None, checkpointer=None):
    """lab: backend.lab.Lab. model: chat model or (model, fallback); defaults from LAB_MODEL."""
    model, fallback = model if isinstance(model, tuple) else (model, None)
    if model is None:
        model, fallback = default_model()

    def llm(tools=None):
        m = model.bind_tools(tools) if tools else model
        if fallback is not None:
            fm = fallback.bind_tools(tools) if tools else fallback
            m = m.with_fallbacks([fm])
        return m

    # ---------------- interpret ----------------
    def interpret(state: LabState) -> dict:
        _emit(stage="interpret")
        places = dict(state.get("places") or {})
        for i, c in enumerate(state.get("clicks") or [], start=1):
            places.setdefault(f"click{i}", {"kind": "click", "name": f"map click {i}", "lon": c["lon"], "lat": c["lat"]})
        prov = []

        def register(found: list[Place]) -> list[dict]:
            out = []
            for p in found:
                ref = f"p{len(places) + 1}"
                places[ref] = p.as_dict()
                out.append({"ref": ref, **p.as_dict()})
            return out

        @tool
        def search_places(query: str, kind: str | None = None) -> str:
            """Find places in Algiers by name: facilities, communes, roads (kind = facility|commune|road).
            Falls back to the OpenStreetMap geocoder when nothing local matches well."""
            found = lab.index.search(query, kind=kind)
            if (not found or found[0].score < 0.75) and kind in (None, "commune"):
                try:
                    found += geocode(query)
                except Exception as e:  # network trouble must not kill the run
                    prov.append({"tool": "geocode", "error": str(e)})
            res = register(found)
            prov.append({"tool": "search_places", "args": {"query": query, "kind": kind}, "result": res})
            return json.dumps(res, ensure_ascii=False)

        @tool
        def nearby_facilities(place: str, category: str, k: int = 5) -> str:
            """Facilities of a category (hospital|clinic|pharmacy|school) nearest to a place ref, with distance."""
            if place not in places:
                return json.dumps({"error": f"unknown ref {place}"})
            x, y = to_xy((places[place]["lon"], places[place]["lat"]))
            f = lab.tree.nodes[state.get("current_id", "factual")].facilities
            f = f[f.category == category]
            d = np.hypot(f.x - x, f.y - y)
            near = f.assign(dist_m=d.round()).nsmallest(k, "dist_m")
            res = register([Place("facility", n if isinstance(n, str) else "(unnamed)", r.lon, r.lat, id=fid,
                                  category=category) for fid, r, n in zip(near.index, near.itertuples(), near["name"])])
            for r, dm in zip(res, near.dist_m):
                r["dist_m"] = float(dm)
            prov.append({"tool": "nearby_facilities", "args": {"place": place, "category": category}, "result": res})
            return json.dumps(res, ensure_ascii=False)

        tools = [search_places, nearby_facilities, PlanSpec]
        by_name = {"search_places": search_places, "nearby_facilities": nearby_facilities}
        current = lab.tree.all_edits(state.get("current_id", "factual"))
        ctx = {"current_scenario": state.get("current_id", "factual"),
               "current_edits": [e.model_dump() for e in current],
               "clicked_places": {k: v for k, v in places.items() if k.startswith("click")}}
        msgs = [SystemMessage(INTERPRET), HumanMessage(f"Context: {json.dumps(ctx)}\n\nRequest: {state['request']}")]
        if state.get("problems"):
            msgs.append(HumanMessage("Your previous plan had problems. Fix them and submit again.\n"
                                     f"Plan: {json.dumps(state.get('plan'))}\nProblems: {json.dumps(state['problems'])}\n"
                                     f"Known places: {json.dumps(places, ensure_ascii=False)}"))
        bound = llm(tools)
        plan = None
        for _ in range(MAX_TOOL_ROUNDS):
            t = time.perf_counter()
            ai: AIMessage = bound.invoke(msgs)
            prov.append({"llm": "interpret", "seconds": round(time.perf_counter() - t, 2)})
            msgs.append(ai)
            if not ai.tool_calls:
                msgs.append(HumanMessage("Call submit_plan (the PlanSpec tool) to finish."))
                continue
            for tc in ai.tool_calls:
                if tc["name"] == "PlanSpec":
                    plan = PlanSpec.model_validate(tc["args"])
                    msgs.append(ToolMessage("received", tool_call_id=tc["id"]))
                else:
                    out = by_name[tc["name"]].invoke(tc["args"]) if tc["name"] in by_name else "unknown tool"
                    msgs.append(ToolMessage(out, tool_call_id=tc["id"]))
            if plan:
                break
        if plan is None:
            plan = PlanSpec(summary="Could not build a plan from the request.", edits=[])
        _emit(stage="plan", plan=plan.model_dump())
        return {"plan": plan.model_dump(), "places": places, "provenance": prov}

    # ---------------- validate ----------------
    def validate(state: LabState) -> Command:
        _emit(stage="validate")
        plan = PlanSpec.model_validate(state["plan"])
        parent = state.get("current_id", "factual") if plan.base == "current" else "factual"
        if parent not in lab.tree.nodes:
            parent = "factual"
        edits, msgs = resolve(plan, state.get("places", {}))
        problems = [{"edit_index": -1, "severity": "error", "message": m} for m in msgs]
        if not edits and not problems:
            problems.append({"edit_index": -1, "severity": "error", "message": "the plan has no edits"})
        if edits and not problems:
            problems = [p.model_dump() for p in validate_edits(edits, lab.tree, parent, lab.boundary_utm)]
        errors = [p for p in problems if p["severity"] == "error"]
        upd = {"edits": [e.model_dump() for e in edits], "problems": problems, "parent_id": parent,
               "provenance": [{"node": "validate", "problems": problems}]}
        if errors:
            if state.get("repairs", 0) < 1:
                return Command(goto="interpret", update={**upd, "repairs": state.get("repairs", 0) + 1})
            return Command(goto=END, update={**upd, "status": "failed"})
        return Command(goto="confirm", update=upd)

    # ---------------- confirm (human in the loop) ----------------
    def confirm(state: LabState) -> Command:
        answer = interrupt({"plan": state["plan"], "edits": state["edits"], "parent_id": state["parent_id"],
                            "warnings": [p for p in state["problems"] if p["severity"] == "warning"],
                            "places": state.get("places", {})})
        action = answer.get("action") if isinstance(answer, dict) else answer
        if action == "approve":
            return Command(goto="apply", update={"provenance": [{"node": "confirm", "action": "approve"}]})
        if action == "edit":
            plan = answer["plan"]
            return Command(goto="validate", update={"plan": plan, "repairs": 1,
                                                    "places": {**state.get("places", {}), **answer.get("places", {})},
                                                    "provenance": [{"node": "confirm", "action": "edit", "plan": plan}]})
        return Command(goto=END, update={"status": "cancelled", "provenance": [{"node": "confirm", "action": "cancel"}]})

    # ---------------- apply / compute / compare ----------------
    def apply(state: LabState) -> dict:
        _emit(stage="apply")
        t = time.perf_counter()
        s = lab.tree.branch(state["parent_id"], EDITS.validate_python(state["edits"]))
        return {"scenario_id": s.id, "provenance": [{"node": "apply", "scenario": s.id, "log": s.log,
                                                     "seconds": round(time.perf_counter() - t, 2)}]}

    def compute(state: LabState) -> dict:
        _emit(stage="compute")
        t = time.perf_counter()
        m = lab.metrics_for(state["scenario_id"])
        return {"provenance": [{"node": "compute", "seconds": round(time.perf_counter() - t, 2), "parts": m.seconds}]}

    def compare(state: LabState) -> dict:
        _emit(stage="compare")
        c = lab.comparison(state["scenario_id"])
        edits = lab.tree.all_edits(state["scenario_id"])
        f = facts_mod.build(c, edits, float(lab.cells["pop"].sum()))
        return {"facts": f, "provenance": [{"node": "compare", "facts": f}]}

    # ---------------- narrate ----------------
    def narrate(state: LabState) -> dict:
        _emit(stage="narrate")
        f = state["facts"]
        if f["no_change"]:
            return {"narrative": facts_mod.template(f), "status": "done",
                    "provenance": [{"node": "narrate", "source": "template"}]}
        msgs = [SystemMessage(NARRATE), HumanMessage(f"Scenario: {state['plan']['summary']}\nFACTS: {json.dumps(f)}")]
        prov = []
        for attempt in range(2):
            t = time.perf_counter()
            text = llm().invoke(msgs).text.strip()
            bad = unsupported(text, {**f, "plan": state["plan"]["summary"]})
            prov.append({"llm": "narrate", "attempt": attempt, "seconds": round(time.perf_counter() - t, 2),
                         "unsupported_numbers": bad})
            if not bad:
                return {"narrative": text, "status": "done", "provenance": prov}
            msgs += [AIMessage(text), HumanMessage(f"These numbers are not in FACTS: {bad}. Rewrite using only FACTS numbers.")]
        prov.append({"node": "narrate", "source": "template"})
        return {"narrative": facts_mod.template(f), "status": "done", "provenance": prov}

    g = StateGraph(LabState)
    for name, fn in [("interpret", interpret), ("validate", validate), ("confirm", confirm), ("apply", apply),
                     ("compute", compute), ("compare", compare), ("narrate", narrate)]:
        g.add_node(name, fn)
    g.add_edge(START, "interpret")
    g.add_edge("interpret", "validate")
    g.add_edge("apply", "compute")
    g.add_edge("compute", "compare")
    g.add_edge("compare", "narrate")
    g.add_edge("narrate", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


def run_until_pause(graph, config: dict, inp: Any) -> dict[str, Any]:
    """Helper for scripts and tests: run, return the interrupt payload or the final state."""
    for _ in graph.stream(inp, config, stream_mode="updates"):
        pass
    snap = graph.get_state(config)
    if snap.interrupts:
        return {"interrupt": snap.interrupts[0].value}
    return {"final": snap.values}
