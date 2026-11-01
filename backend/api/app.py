"""HTTP API. Long agent runs stream progress as server-sent events.

POST /api/ask      starts a run; streams stages and ends with the plan awaiting confirmation
POST /api/confirm  approve, edit or cancel; streams stages and ends with the result
Everything else is plain JSON for the map.
"""

import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache

import geopandas as gpd
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from backend.agent.graph import build
from backend.lab import Lab
from backend.network.edits import AddFacility, AddRoad, ChangeSpeed, RemoveFacility, RemoveRoad, select_edges, to_xy
from backend.network.model import CATEGORIES, DATA, MODES

STATE: dict = {}
LOCK = threading.Lock()  # one run at a time: the scenario tree and metric cache are shared


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    lab = Lab()
    STATE["lab"] = lab
    STATE["graph"] = build(lab)
    STATE["runs"] = {}  # scenario id -> final agent state
    yield


app = FastAPI(title="Counterfactual Accessibility Lab", lifespan=lifespan)
ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])


def lab() -> Lab:
    return STATE["lab"]


def clean(x):
    """JSON-safe: numpy scalars to Python, NaN and inf to None."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else round(float(x), 4)
    return x


# ---------------- static layers ----------------

@app.get("/api/health")
def health():
    return {"ok": "lab" in STATE}


@app.get("/api/base")
def base():
    lb = lab()
    f = lb.tree.factual.facilities
    return clean({
        "boundary": json.loads(gpd.read_file(DATA / "boundary.gpkg")[["geometry"]].to_json()),
        "communes": json.loads(lb.communes.to_crs(4326).to_json()),
        "cells": {"h3": list(lb.cells.index), "pop": lb.cells["pop"].round(1).tolist(),
                  "commune": lb.cells["commune"].tolist()},
        "facilities": facilities_json(f),
        "modes": MODES, "categories": CATEGORIES,
        "load_seconds": lb.load_s,
    })


def facilities_json(f: pd.DataFrame) -> list[dict]:
    return [{"id": i, "category": r.category, "name": r.name if isinstance(r.name, str) else None,
             "lon": round(r.lon, 6), "lat": round(r.lat, 6)} for i, r in zip(f.index, f.itertuples())]


# ---------------- scenarios ----------------

def scenario_or_404(sid: str):
    if sid not in lab().tree.nodes:
        raise HTTPException(404, f"no scenario {sid}")
    return lab().tree.nodes[sid]


@app.get("/api/scenarios")
def scenarios():
    out = []
    for sid, s in lab().tree.nodes.items():
        run = STATE["runs"].get(sid, {})
        out.append({"id": sid, "parent": s.parent, "log": s.log, "edits": [e.model_dump() for e in s.edits],
                    "summary": run.get("plan", {}).get("summary", "Real city" if sid == "factual" else ""),
                    "request": run.get("request"), "narrative": run.get("narrative")})
    return clean(out)


@app.get("/api/scenarios/{sid}/metrics")
def metrics(sid: str, mode: str, category: str):
    """Per-cell values aligned with /api/base cells."""
    scenario_or_404(sid)
    m = lab().metrics_for(sid)
    k = f"{mode}.{category}"
    if f"{k}.time_min" not in m.cells:
        raise HTTPException(400, "unknown mode or category")
    return clean({"time_min": m.cells[f"{k}.time_min"].round(2).tolist(), "cum": m.cells[f"{k}.cum"].tolist(),
                  "sfca": m.cells[f"{k}.sfca"].round(3).tolist(),
                  "summary": {key.split(".", 2)[2]: v for key, v in m.summary.items() if key.startswith(k + ".")},
                  "facilities": facilities_json(lab().tree.nodes[sid].facilities.query("category == @category"))})


@app.get("/api/scenarios/{sid}/compare")
def compare(sid: str, mode: str, category: str):
    scenario_or_404(sid)
    c = lab().comparison(sid)
    k = f"{mode}.{category}"
    delta = c["layers"].get(k, {})
    run = STATE["runs"].get(sid, {})
    return clean({"delta_min": [delta.get(h) for h in lab().cells.index], "summary": c["summary"].get(k),
                  "top": c["top"].get(k), "changed": list(c["summary"]), "facts": run.get("facts"),
                  "narrative": run.get("narrative")})


@lru_cache(maxsize=2)
def edge_geoms(mode: str) -> gpd.GeoSeries:
    e = gpd.read_parquet(DATA / f"{mode}_edges.parquet", columns=["geometry"]).reset_index()
    return gpd.GeoSeries(e.geometry.values, index=[f"{u}-{v}-{k}" for u, v, k in zip(e.u, e.v, e.key)], crs=4326)


@app.get("/api/scenarios/{sid}/edits")
def edits_geo(sid: str):
    """GeoJSON of every edit in this scenario's lineage, for drawing on the map."""
    scenario_or_404(sid)
    lb = lab()
    feats = []
    fac_all = pd.concat([s.facilities for s in lb.tree.lineage(sid)])
    fac_all = fac_all[~fac_all.index.duplicated()]
    for e in lb.tree.all_edits(sid):
        if isinstance(e, AddFacility):
            feats.append(point(e.location, kind="new_facility", category=e.category, name=e.name))
        elif isinstance(e, RemoveFacility) and e.facility_id in fac_all.index:
            r = fac_all.loc[e.facility_id]
            feats.append(point((r.lon, r.lat), kind="closed_facility", category=r.category, name=r["name"]))
        elif isinstance(e, AddRoad):
            feats.append({"type": "Feature", "properties": {"kind": "new_road", "name": e.name},
                          "geometry": {"type": "LineString", "coordinates": [list(e.start), list(e.end)]}})
        elif isinstance(e, (RemoveRoad, ChangeSpeed)):
            mode = "drive" if isinstance(e, ChangeSpeed) or "drive" in e.modes else "walk"
            ids = select_edges(lb.tree.factual.networks[mode], e)
            g = edge_geoms(mode).reindex(ids).dropna()
            kind = "closed_road" if isinstance(e, RemoveRoad) else "faster_road" if (e.factor or 2) > 1 else "slower_road"
            for geom in g:
                feats.append({"type": "Feature", "properties": {"kind": kind}, "geometry": geom.__geo_interface__})
    return clean({"type": "FeatureCollection", "features": feats})


def point(lonlat, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": list(lonlat)}}


@app.get("/api/scenarios/{sid}/reach")
def reach(sid: str, mode: str, lon: float, lat: float, max_min: float = 30):
    """Travel time from every cell to one point, for the growing isochrone animation."""
    s = scenario_or_404(sid)
    lb = lab()
    x, y = to_xy((lon, lat))
    fac = pd.DataFrame({"category": ["x"], "x": [x], "y": [y], "capacity": [1.0]})
    net = s.networks[mode]
    t = lb.engine._one(net, net.reversed_matrix(extra_nodes=1), fac)["time_min"]
    t = t.where(t <= max_min)
    return clean({"time_min": t.round(2).tolist()})


@app.get("/api/scenarios/{sid}/provenance")
def provenance(sid: str):
    run = STATE["runs"].get(sid)
    if not run:
        raise HTTPException(404, "no agent run for this scenario")
    return clean(run.get("provenance", []))


# ---------------- agent runs (SSE) ----------------

class AskBody(BaseModel):
    request: str = Field(min_length=3, max_length=500)
    current_id: str = "factual"
    clicks: list[dict] = []


class ConfirmBody(BaseModel):
    thread_id: str
    action: str = Field(pattern="^(approve|edit|cancel)$")
    plan: dict | None = None
    places: dict | None = None


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(clean(data), ensure_ascii=False)}\n\n"


def stream_run(inp, thread_id: str):
    graph = STATE["graph"]
    cfg = {"configurable": {"thread_id": thread_id}}
    if not LOCK.acquire(timeout=1):
        yield sse("error", {"message": "another scenario is running, try again in a moment"})
        return
    try:
        for mode, chunk in graph.stream(inp, cfg, stream_mode=["updates", "custom"]):
            if mode == "custom":
                yield sse("progress", chunk)
            elif "__interrupt__" not in chunk:
                yield sse("node", {"node": next(iter(chunk))})
        snap = graph.get_state(cfg)
        if snap.interrupts:
            yield sse("confirm", {"thread_id": thread_id, **snap.interrupts[0].value})
            return
        final = snap.values
        if final.get("scenario_id"):
            STATE["runs"][final["scenario_id"]] = final
        yield sse("done", {"thread_id": thread_id, "status": final.get("status"),
                           "scenario_id": final.get("scenario_id"), "narrative": final.get("narrative"),
                           "facts": final.get("facts"), "problems": final.get("problems"),
                           "plan": final.get("plan")})
    except Exception as e:  # surface failures to the client instead of a dropped stream
        yield sse("error", {"message": f"{type(e).__name__}: {e}"})
    finally:
        LOCK.release()


@app.post("/api/ask")
def ask(body: AskBody):
    scenario_or_404(body.current_id)
    tid = uuid.uuid4().hex[:12]
    inp = {"request": body.request, "current_id": body.current_id, "clicks": body.clicks}
    return StreamingResponse(stream_run(inp, tid), media_type="text/event-stream")


@app.post("/api/confirm")
def confirm(body: ConfirmBody):
    resume = {"action": body.action, "plan": body.plan, "places": body.places or {}}
    return StreamingResponse(stream_run(Command(resume=resume), body.thread_id), media_type="text/event-stream")
