"""Agent graph with a scripted model: no network, no API key."""

import json
import re

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command

from backend.agent.graph import build, run_until_pause
from backend.agent.numbers import unsupported
from backend.network.model import DATA

pytestmark = pytest.mark.skipif(not (DATA / "drive_edges.parquet").exists(), reason="run scripts/fetch_data.py")


class Scripted:
    """Returns scripted replies in order. A callable entry gets the messages and returns a reply."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, msgs):
        self.calls += 1
        step = self.script.pop(0)
        return step(msgs) if callable(step) else step


def call(name, args, i=0):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


def good_story(msgs):
    facts = json.loads(re.search(r"FACTS: (.*)", msgs[1].content).group(1))
    name, r = next(iter(facts["results"].items()))
    return AIMessage(f"For {name}, {r['pop_gaining']:,} people gain and the gini goes from "
                     f"{r['gini']['factual']} to {r['gini']['counterfactual']}.")


@pytest.fixture(scope="module")
def lab():
    from backend.lab import Lab
    return Lab()


def test_full_run_with_number_check(lab):
    model = Scripted([
        call("search_places", {"query": "Baraki", "kind": "commune"}),
        call("PlanSpec", {"summary": "New hospital in Baraki", "edits": [
            {"op": "add_facility", "category": "hospital", "place": "p1"}]}),
        AIMessage("About 999,999 people gain."),  # invented number, must be rejected
        good_story,
    ])
    g = build(lab, model)
    cfg = {"configurable": {"thread_id": "t1"}}
    out = run_until_pause(g, cfg, {"request": "What if a new hospital opened in Baraki?"})
    assert out["interrupt"]["edits"][0]["op"] == "add_facility"
    out = run_until_pause(g, cfg, Command(resume={"action": "approve"}))
    final = out["final"]
    assert final["status"] == "done"
    assert "999,999" not in final["narrative"]
    assert unsupported(final["narrative"], final["facts"]) == []
    checks = [p for p in final["provenance"] if p.get("llm") == "narrate"]
    assert checks[0]["unsupported_numbers"] == ["999,999"] and checks[1]["unsupported_numbers"] == []
    r = next(iter(final["facts"]["results"].values()))
    assert r["pop_losing"] == 0 and r["pop_gaining"] > 0  # adding a hospital never hurts anyone
    assert model.calls == 4


def test_repair_then_cancel(lab):
    model = Scripted([
        call("PlanSpec", {"summary": "x", "edits": [{"op": "add_facility", "category": "clinic", "place": "p99"}]}),
        call("search_places", {"query": "Bab Ezzouar", "kind": "commune"}),
        call("PlanSpec", {"summary": "Clinic in Bab Ezzouar", "edits": [
            {"op": "add_facility", "category": "clinic", "place": "p1"}]}, 1),
    ])
    g = build(lab, model)
    cfg = {"configurable": {"thread_id": "t2"}}
    out = run_until_pause(g, cfg, {"request": "clinic in Bab Ezzouar"})
    assert "interrupt" in out
    out = run_until_pause(g, cfg, Command(resume={"action": "cancel"}))
    assert out["final"]["status"] == "cancelled"
    assert "scenario_id" not in out["final"]


def test_unrepairable_plan_fails(lab):
    bad = call("PlanSpec", {"summary": "x", "edits": [{"op": "remove_facility", "facility_id": "nope"}]})
    g = build(lab, Scripted([bad, bad]))
    out = run_until_pause(g, {"configurable": {"thread_id": "t3"}}, {"request": "close nope"})
    assert out["final"]["status"] == "failed"


def test_numbers():
    facts = {"a": 12345, "b": {"change": -0.034}, "name": "Bab Ezzouar 2"}
    assert unsupported("12,345 people, gini down 0.03, in Bab Ezzouar 2", facts) == []
    assert unsupported("12,346 people", facts) == ["12,346"]


def test_named_street_area_plan_is_repaired_and_cars_only(lab):
    q = "What if Rue Hassiba Ben Bouali were closed to traffic?"
    area = call("PlanSpec", {"summary": "close the area", "edits": [
        {"op": "remove_road", "place": "p1", "radius_m": 300}]})
    street = call("PlanSpec", {"summary": "close the street", "edits": [
        {"op": "remove_road", "street": "Rue Hassiba Ben Bouali", "modes": ["drive", "walk"]}]}, 1)
    model = Scripted([call("search_places", {"query": "Rue Hassiba Ben Bouali", "kind": "road"}), area, street])
    g = build(lab, model)
    cfg = {"configurable": {"thread_id": "t5"}}
    out = run_until_pause(g, cfg, {"request": q})
    assert "interrupt" in out, out
    edit = out["interrupt"]["edits"][0]
    assert edit["area"] is None and edit["modes"] == ["drive"]  # cars only, by the request's wording
    assert edit["way_ids"] == lab.streets.resolve("Rue Hassiba Ben Bouali")["way_ids"]
    state = g.get_state(cfg).values
    first = [p for p in state["provenance"] if p.get("node") == "validate"][0]
    assert "names a street" in first["problems"][0]["message"]


def test_unknown_street_fails(lab):
    bad = call("PlanSpec", {"summary": "x", "edits": [{"op": "remove_road", "street": "Rue Qui N'existe Pas"}]})
    g = build(lab, Scripted([bad, bad]))
    out = run_until_pause(g, {"configurable": {"thread_id": "t6"}}, {"request": "close Rue Qui N'existe Pas"})
    assert out["final"]["status"] == "failed"
    assert "no street named" in out["final"]["problems"][0]["message"]


def test_street_spellings(lab):
    a = lab.streets.resolve("Rue Hassiba Ben Bouali")
    for q in ("hassiba benbouali", "Boulevard Hassiba Ben Bouali", "شارع حسيبة بن بوعلي"):
        assert lab.streets.resolve(q)["way_ids"] == a["way_ids"], q
    amirouche = set(lab.streets.resolve("Boulevard Colonel Amirouche")["way_ids"])
    assert len(amirouche - set(a["way_ids"])) > 0  # a merged segment does not pull in the other street
