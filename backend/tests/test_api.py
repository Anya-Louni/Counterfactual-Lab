"""API end to end with the scripted model from test_agent."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.network.model import DATA

from .test_agent import Scripted, call, good_story

pytestmark = pytest.mark.skipif(not (DATA / "drive_edges.parquet").exists(), reason="run scripts/fetch_data.py")


def events(resp) -> list[tuple[str, dict]]:
    out = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture(scope="module")
def client():
    from backend.agent.graph import build
    from backend.api import app as api
    with TestClient(api.app) as c:
        api.STATE["graph"] = build(api.STATE["lab"], Scripted([
            call("search_places", {"query": "Baraki", "kind": "commune"}),
            call("PlanSpec", {"summary": "New pharmacy at the clicked point", "edits": [
                {"op": "add_facility", "category": "pharmacy", "place": "click1"}]}),
            good_story,
        ]))
        yield c


def test_flow(client):
    base = client.get("/api/base").json()
    n = len(base["cells"]["h3"])
    assert n > 1000 and base["facilities"] and base["boundary"]["features"]

    r = client.post("/api/ask", json={"request": "pharmacy here", "clicks": [{"lon": 3.0236, "lat": 36.6728}]})
    ev = events(r)
    assert ev[-1][0] == "confirm", ev[-1]
    tid = ev[-1][1]["thread_id"]
    assert ev[-1][1]["edits"][0]["location"] == [3.0236, 36.6728]  # the click, not an invented point
    assert any(e == "progress" for e, _ in ev)

    ev = events(client.post("/api/confirm", json={"thread_id": tid, "action": "approve"}))
    kind, done = ev[-1]
    assert kind == "done" and done["status"] == "done", ev[-1]
    sid = done["scenario_id"]
    stages = [d.get("stage") for e, d in ev if e == "progress"]
    assert stages == ["apply", "compute", "compare", "narrate"]

    m = client.get(f"/api/scenarios/{sid}/metrics", params={"mode": "walk", "category": "pharmacy"}).json()
    assert len(m["time_min"]) == n
    c = client.get(f"/api/scenarios/{sid}/compare", params={"mode": "walk", "category": "pharmacy"}).json()
    deltas = [d for d in c["delta_min"] if d is not None]
    assert deltas and max(deltas) <= 0  # a new pharmacy only shortens trips
    geo = client.get(f"/api/scenarios/{sid}/edits").json()
    assert geo["features"][0]["properties"]["kind"] == "new_facility"
    reach = client.get(f"/api/scenarios/{sid}/reach",
                       params={"mode": "walk", "lon": 3.0236, "lat": 36.6728, "max_min": 15}).json()
    assert 0 < sum(t is not None for t in reach["time_min"]) < n
    tree = client.get("/api/scenarios").json()
    assert [s["id"] for s in tree] == ["factual", sid] and tree[1]["parent"] == "factual"
    assert client.get(f"/api/scenarios/{sid}/provenance").json()
