"""Sanity properties on the real Algiers data. Skipped when data/ has not been fetched."""

import networkx as nx
import numpy as np
import pytest

from backend.network.edits import AddFacility, AddRoad, RemoveFacility, RemoveRoad
from backend.network.model import DATA, WALK_MPS

pytestmark = pytest.mark.skipif(not (DATA / "drive_edges.parquet").exists(), reason="run scripts/fetch_data.py")

CAT = "hospital"


@pytest.fixture(scope="module")
def lab():
    from backend.lab import Lab
    return Lab()


def hosp_times(lab, scenario, mode):
    net = scenario.networks[mode]
    fac = scenario.facilities[scenario.facilities.category == CAT]
    return lab.engine._one(net, net.reversed_matrix(extra_nodes=1), fac, CAT)["time_min"].to_numpy()


@pytest.fixture(scope="module")
def base(lab):
    return {m: hosp_times(lab, lab.tree.factual, m) for m in ("drive", "walk")}


def check(lab, base, edits, sign):
    s = lab.tree.branch("factual", edits)
    moved = 0.0
    for mode in ("drive", "walk"):
        d = hosp_times(lab, s, mode) - base[mode]
        d = d[np.isfinite(d)]
        if sign < 0:
            assert d.max() <= 1e-9, f"{mode}: some cell got slower"
        else:
            assert d.min() >= -1e-9, f"{mode}: some cell got faster"
        moved = max(moved, np.abs(d).max())
    assert moved > 0, "nothing changed in either mode"


def test_adding_facility_never_slows(lab, base):
    check(lab, base, [AddFacility(category=CAT, location=(3.0236, 36.6728))], -1)  # Baraki


def test_removing_facility_never_speeds(lab, base):
    f = lab.tree.factual.facilities
    fid = f[f.category == CAT].index[0]
    check(lab, base, [RemoveFacility(facility_id=fid)], +1)


def test_adding_road_never_slows(lab, base):
    # Straight road from the worst-served populated cell to the nearest hospital as the crow flies.
    c = lab.cells.iloc[int(np.nanargmax(np.where(lab.cells["pop"] > 100, base["drive"], np.nan)))]
    f = lab.tree.factual.facilities
    h = f[f.category == CAT]
    h = h.iloc[int(np.argmin(np.hypot(h.x - c.x, h.y - c.y)))]
    check(lab, base, [AddRoad(start=(c.lon, c.lat), end=(h.lon, h.lat), speed_kph=50)], -1)


def test_removing_road_never_speeds(lab, base):
    e = lab.tree.factual.networks["drive"].edges
    motorways = sorted({w for ids in e[e.highway.astype(str).str.contains("motorway")].osmid for w in ids})
    check(lab, base, [RemoveRoad(way_ids=motorways)], +1)


def test_matches_networkx(lab, base):
    """Cell times from the engine equal NetworkX Dijkstra on the same graph."""
    s = lab.tree.factual
    rng = np.random.default_rng(1)
    for mode in ("drive", "walk"):
        net = s.networks[mode]
        fac = s.facilities[s.facilities.category == CAT]
        f_pos, f_d = net.snap_multi(fac.x.to_numpy(), fac.y.to_numpy())
        g = nx.DiGraph()
        for u, v, w in net.edges.groupby(["u", "v"])["travel_time"].min().reset_index().itertuples(index=False):
            g.add_edge(v, u, weight=max(w, 1e-6))  # reversed, as in the engine
        for p, d in zip(f_pos.ravel(), f_d.ravel()):
            if not np.isfinite(d):
                continue
            node = net.nodes.index[p]
            w = d / WALK_MPS + 1e-6
            if not g.has_edge("SRC", node) or g["SRC"][node]["weight"] > w:
                g.add_edge("SRC", node, weight=w)
        dist = nx.single_source_dijkstra_path_length(g, "SRC")
        cell_pos, cell_acc = lab.engine._cell_snap(net)
        sample = rng.choice(len(lab.cells), 300, replace=False)
        ref = np.array([min(dist.get(net.nodes.index[p], np.inf) + a for p, a in zip(cell_pos[i], cell_acc[i])) / 60
                        for i in sample])
        assert np.allclose(base[mode][sample], ref, atol=1e-6)
