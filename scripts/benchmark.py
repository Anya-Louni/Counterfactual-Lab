"""Compare routing engines on the cached Algiers networks.

For each network (drive, walk) and engine it times:
  build    : graph construction from edge arrays (paid again after every edit)
  nearest  : travel time from every node to the nearest facility of one type
  catchment: every facility's 15 minute catchment (needed for 2SFCA)
and checks each engine against NetworkX Dijkstra on a node sample.
Writes data/benchmark.json.
"""

import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import igraph as ig
import networkx as nx
import numpy as np
import pandana
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

DATA = Path(__file__).resolve().parents[1] / "data"
CRS = 32631
CATCHMENT_S = 900
SAMPLE = 300


def clock(fn):
    t = time.perf_counter()
    out = fn()
    return out, round(time.perf_counter() - t, 3)


def load(kind):
    nodes = gpd.read_parquet(DATA / f"{kind}_nodes.parquet")
    edges = gpd.read_parquet(DATA / f"{kind}_edges.parquet").reset_index()
    idx = pd.Series(np.arange(len(nodes)), index=nodes.index)
    # Parallel edges: keep the fastest, as routing would.
    e = pd.DataFrame({"u": idx[edges.u].values, "v": idx[edges.v].values, "w": edges.travel_time.values})
    e = e.groupby(["u", "v"], as_index=False)["w"].min()
    if kind == "walk":  # walking ignores one-way restrictions
        e = pd.concat([e, e.rename(columns={"u": "v", "v": "u"})]).groupby(["u", "v"], as_index=False)["w"].min()
    xy = nodes.to_crs(CRS).geometry
    return nodes, e, np.c_[xy.x, xy.y]


def sources(nodes_xy, ftype):
    f = gpd.read_file(DATA / "facilities.gpkg")
    f = f[f.category == ftype].to_crs(CRS)
    _, nn = cKDTree(nodes_xy).query(np.c_[f.geometry.x, f.geometry.y])
    return np.unique(nn)


def run(kind, ftype):
    nodes, e, xy = load(kind)
    n = len(nodes)
    src = sources(xy, ftype)
    rng = np.random.default_rng(0)
    sample = rng.choice(n, SAMPLE, replace=False)
    out = {"nodes": n, "edges": len(e), "facility_type": ftype, "facility_nodes": len(src)}

    # Reference: NetworkX on the reversed graph gives time from each node to its nearest source.
    def nx_build():
        g = nx.DiGraph()
        g.add_weighted_edges_from(zip(e.v, e.u, e.w))
        return g
    g, t_b = clock(nx_build)
    ref, t_q = clock(lambda: nx.multi_source_dijkstra_path_length(g, set(src.tolist()), weight="weight"))
    ref_arr = np.array([ref.get(i, np.inf) for i in range(n)])
    out["networkx"] = {"build_s": t_b, "nearest_s": t_q}

    # scipy csgraph
    mat, t_b = clock(lambda: csr_matrix((e.w, (e.v, e.u)), shape=(n, n)))
    d, t_q = clock(lambda: dijkstra(mat, indices=src, min_only=True))

    def sp_catch():
        hits = 0
        for i in range(0, len(src), 64):
            m = dijkstra(mat, indices=src[i:i + 64], limit=CATCHMENT_S)
            hits += int(np.isfinite(m).sum())
        return hits
    hits, t_c = clock(sp_catch)
    out["scipy"] = {"build_s": t_b, "nearest_s": t_q, "catchment_s": t_c, "catchment_pairs": hits,
                    "max_abs_diff_s": float(np.nanmax(np.abs(d[sample] - ref_arr[sample])))}

    # igraph with a zero-cost super sink behind every source.
    def ig_build():
        gi = ig.Graph(n=n + 1, edges=list(zip(e.u, e.v)) + [(s, n) for s in src], directed=True)
        gi.es["w"] = list(e.w) + [0.0] * len(src)
        return gi
    gi, t_b = clock(ig_build)
    di, t_q = clock(lambda: np.array(gi.distances(source=[n], weights="w", mode="in")[0][:n]))
    out["igraph"] = {"build_s": t_b, "nearest_s": t_q,
                     "max_abs_diff_s": float(np.nanmax(np.abs(di[sample] - ref_arr[sample])))}

    # pandana: contraction hierarchy built at construction.
    def pd_build():
        net = pandana.Network(pd.Series(xy[:, 0]), pd.Series(xy[:, 1]), e.u, e.v, e[["w"]], twoway=False)
        net.precompute(CATCHMENT_S)
        return net
    net, t_b = clock(pd_build)

    def pd_nearest():
        net.set_pois("f", 7200, 1, pd.Series(xy[src, 0]), pd.Series(xy[src, 1]))
        return net.nearest_pois(7200, "f", num_pois=1).iloc[:, 0].to_numpy()
    dp, t_q = clock(pd_nearest)
    dp = np.where(dp >= 7200, np.inf, dp)

    def pd_catch():  # up to 50 facilities per node within the catchment
        net.set_pois("c", CATCHMENT_S, 50, pd.Series(xy[src, 0]), pd.Series(xy[src, 1]))
        r = net.nearest_pois(CATCHMENT_S, "c", num_pois=50).to_numpy()
        return int((r < CATCHMENT_S).sum())
    pd_hits, t_c = clock(pd_catch)
    ok = np.isfinite(ref_arr[sample])
    out["pandana"] = {"build_s": t_b, "nearest_s": t_q, "catchment_s": t_c, "catchment_pairs": pd_hits,
                      "max_abs_diff_s": float(np.max(np.abs(dp[sample][ok] - ref_arr[sample][ok]))),
                      "unreached_mismatch": int((np.isfinite(dp[sample]) != ok).sum())}
    return out


def main():
    ftype = sys.argv[1] if len(sys.argv) > 1 else "hospital"
    res = {}
    for kind in ("drive", "walk"):
        res[kind] = run(kind, ftype)
        print(json.dumps({kind: res[kind]}, indent=2), flush=True)
    (DATA / f"benchmark_{ftype}.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
