"""Hand-checked travel times on the toy city (see toy.py for the layout)."""

import math

import numpy as np
import pytest
import shapely

from backend.metrics.accessibility import Engine
from backend.metrics.equity import weighted_gini, weighted_quantile
from backend.network.edits import AddFacility, AddRoad, ChangeSpeed, RemoveFacility, RemoveRoad
from backend.network.sandbox import ScenarioTree
from backend.network.validate import validate

from . import toy

INF = math.inf


@pytest.fixture
def tree():
    return ScenarioTree({m: toy.network(m) for m in ("drive", "walk")}, toy.facilities())


def times(tree, sid, mode="drive"):
    s = tree.nodes[sid]
    m = Engine(toy.cells()).compute(s.networks, s.facilities)
    return list(m.cells[f"{mode}.hospital.time_min"] * 60), m


def close(a, b):
    return all((x == y) or abs(x - y) < 1e-3 for x, y in zip(a, b))


def test_factual(tree):
    t, m = times(tree, "factual")
    assert close(t, [0, 100, 200, 300, INF])
    w, _ = times(tree, "factual", "walk")
    assert close(w, [0, 800, 1600, 2400, INF])
    # 15 min drive catchment holds A to D: demand 1000, supply 1, so 10 per 10,000 people.
    assert list(m.cells["drive.hospital.cum"]) == [1, 1, 1, 1, 0]
    assert np.allclose(m.cells["drive.hospital.sfca"], [10, 10, 10, 10, 0])
    # 15 min walk reaches A and B only: demand 300.
    assert np.allclose(m.cells["walk.hospital.sfca"], [10000 / 300] * 2 + [0] * 3)
    assert m.summary["drive.hospital.pop_within_10"] == 1000
    assert m.summary["drive.hospital.pop_unreachable"] == 500
    assert m.summary["walk.hospital.pop_within_15"] == 300


def test_add_facility(tree):
    s = tree.branch("factual", [AddFacility(category="hospital", location=toy.lonlat("D"))])
    t, _ = times(tree, s.id)
    assert close(t, [0, 100, 100, 0, INF])


def test_remove_facility(tree):
    s = tree.branch("factual", [RemoveFacility(facility_id="h1")])
    t, m = times(tree, s.id)
    assert all(x == INF for x in t)
    assert m.summary["drive.hospital.pop_unreachable"] == 1500


def test_add_road(tree):
    s = tree.branch("factual", [AddRoad(start=toy.lonlat("D"), end=toy.lonlat("E"), speed_kph=36)])
    t, _ = times(tree, s.id)
    assert close(t, [0, 100, 200, 300, 500])  # D to E is 2 km at 10 m/s
    w, _ = times(tree, s.id, "walk")
    assert close(w, [0, 800, 1600, 2400, 4000])


def test_remove_road_by_way(tree):
    s = tree.branch("factual", [RemoveRoad(way_ids=[101])])  # B-C, both directions
    t, _ = times(tree, s.id)
    assert close(t, [0, 100, INF, INF, INF])
    assert len(s.networks["drive"].edges) == 6


def test_remove_road_by_area(tree):
    bx, by = toy.lonlat("B")
    box = [(bx - 0.001, by - 0.001), (bx + 0.001, by - 0.001), (bx + 0.001, by + 0.001), (bx - 0.001, by + 0.001)]
    s = tree.branch("factual", [RemoveRoad(area=box, modes=["drive"])])
    assert len(s.networks["drive"].edges) == 4  # C-D and E-F left
    assert len(s.networks["walk"].edges) == 8


def test_change_speed(tree):
    s = tree.branch("factual", [ChangeSpeed(way_ids=[100], factor=2)])
    t, _ = times(tree, s.id)
    assert close(t, [0, 50, 150, 250, INF])


def test_factual_never_mutated(tree):
    before = tree.factual.networks["drive"].edges.copy()
    tree.branch("factual", [RemoveRoad(way_ids=[100, 101, 102, 103]), RemoveFacility(facility_id="h1")])
    assert tree.factual.networks["drive"].edges.equals(before)
    assert "h1" in tree.factual.facilities.index


def test_branch_chain(tree):
    s1 = tree.branch("factual", [AddRoad(start=toy.lonlat("D"), end=toy.lonlat("E"), speed_kph=36)])
    s2 = tree.branch(s1.id, [ChangeSpeed(way_ids=[100], factor=2)])
    t, _ = times(tree, s2.id)
    assert close(t, [0, 50, 150, 250, 450])
    assert len(tree.all_edits(s2.id)) == 2


def test_validation(tree):
    bound = shapely.box(toy.ORIGIN[0] - 1000, toy.ORIGIN[1] - 1000, toy.ORIGIN[0] + 6000, toy.ORIGIN[1] + 1000)
    far = (toy.lonlat("A")[0], toy.lonlat("A")[1] + 0.005)  # about 550 m north of any road
    out = validate([
        AddFacility(category="clinic", location=(10.0, 10.0)),
        AddFacility(category="clinic", location=far),
        RemoveFacility(facility_id="nope"),
        RemoveRoad(way_ids=[999]),
        AddRoad(start=toy.lonlat("A"), end=toy.lonlat("A")),
    ], tree, "factual", bound)
    msgs = {p.edit_index: p.message for p in out}
    assert "outside the study area" in msgs[0]
    assert "from the nearest" in msgs[1]
    assert "does not exist" in msgs[2]
    assert "matches no" in msgs[3]
    assert "same" in msgs[4]
    warn = validate([RemoveRoad(way_ids=[101])], tree, "factual", bound)
    assert warn and warn[0].severity == "warning" and "unreachable" in warn[0].message
    assert list(tree.nodes) == ["factual"]  # the trial branch is discarded


def test_equity():
    assert weighted_gini([0, 1, 2], [1, 1, 1]) == pytest.approx(4 / 9)
    assert weighted_gini([5, 5, 5], [1, 2, 3]) == pytest.approx(0)
    assert weighted_gini([1, 2], [1, 1]) == pytest.approx(weighted_gini([1, 1, 2, 2], [1, 1, 1, 1]))
    assert weighted_quantile([1, 2, 3, 4], [1, 1, 1, 1], 0.5) == 2


def test_isolated_node_resnaps(tree):
    # Cut the C-D link: D's node keeps no roads in or out. D's cell is 1 km from C, beyond the
    # re-snap limit, so it stays cut off. A cell 200 m from C snaps to C directly.
    s = tree.branch("factual", [RemoveRoad(way_ids=[102])])
    t, _ = times(tree, s.id)
    assert close(t, [0, 100, 200, INF, INF])
    import pandas as pd
    cells = toy.cells()
    x, y = toy.xy("D")
    cells.loc["D", ["x", "y"]] = [x - 800, y]  # 200 m from C, 800 m from D
    m = Engine(cells).compute(s.networks, s.facilities)
    assert abs(m.cells.loc["D", "drive.hospital.time_min"] * 60 - (200 + 200 / 1.25)) < 1e-3
