"""Routing network and facility tables. Plain DataFrames so a sandbox copy is cheap."""

import re
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

DATA = Path(__file__).resolve().parents[2] / "data"
CRS = 32631  # WGS 84 / UTM 31N
WALK_MPS = 4.5 / 3.6  # walking speed, also used for access legs to and from the network
MODES = ("drive", "walk")
CATEGORIES = ("hospital", "clinic", "pharmacy", "school")
SNAP_K = 4  # a cell or facility links to up to 4 street nodes,
SNAP_RADIUS_M = 300  # those within 300 m, and always the nearest one,
MAX_NEAREST_M = 1000  # unless even the nearest is over 1 km away: then it has no road access
HOSPITAL_MERGE_M = 250  # OSM maps many hospital departments as separate hospitals


@dataclass
class Network:
    mode: str
    nodes: pd.DataFrame  # index: node id. columns: x, y (UTM metres), lon, lat
    edges: pd.DataFrame  # index: edge id "u-v-key". columns: u, v, length, speed_kph, travel_time, name, highway, osmid (way ids)
    _tree: cKDTree | None = field(default=None, repr=False)

    def copy(self) -> "Network":
        # Copy on write: edits replace self.edges with a new frame and never change it in place,
        # so branches share tables until they edit them. Nodes are never edited.
        return Network(self.mode, self.nodes, self.edges, self._tree)

    @property
    def tree(self) -> tuple[cKDTree, np.ndarray]:
        """KD-tree over nodes people can reach from the street: any node on a non-motorway road."""
        if self._tree is None:
            local = self.edges[~self.edges.highway.astype(str).str.contains("motorway")]
            ok = np.flatnonzero(self.nodes.index.isin(pd.Index(local.u).union(pd.Index(local.v))))
            self._tree = (cKDTree(self.nodes[["x", "y"]].to_numpy()[ok]), ok)
        return self._tree

    def snap(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        """Nearest snappable node position (row in self.nodes) and straight-line distance in metres."""
        tree, ok = self.tree
        d, i = tree.query(np.c_[np.atleast_1d(x), np.atleast_1d(y)])
        return ok[i], d

    def snap_multi(self, x, y, k: int = SNAP_K, radius: float = SNAP_RADIUS_M, max_nearest: float = MAX_NEAREST_M):
        """Up to k snappable nodes per point: the nearest, plus others within radius.
        Returns node positions (n, k) and distances (n, k), inf where a slot is unused.
        A point with no node within max_nearest gets all inf (no road access)."""
        tree, ok = self.tree
        d, i = tree.query(np.c_[np.atleast_1d(x), np.atleast_1d(y)], k=k)
        d, i = np.atleast_2d(d), np.atleast_2d(i)
        used = (d <= radius) | (np.arange(k) == 0)
        used &= d[:, :1] <= max_nearest
        i = np.where(i < len(ok), i, 0)
        return ok[i], np.where(used, d, np.inf)

    def reversed_matrix(self, extra_nodes: int = 0) -> csr_matrix:
        """Travel-time matrix with every edge flipped. Dijkstra from a facility on it
        gives the time from each node to that facility. Parallel edges keep the fastest."""
        pos = pd.Series(np.arange(len(self.nodes)), index=self.nodes.index)
        e = pd.DataFrame({"r": pos[self.edges.v].to_numpy(), "c": pos[self.edges.u].to_numpy(),
                          "w": self.edges.travel_time.to_numpy()})
        e = e.groupby(["r", "c"], as_index=False)["w"].min()
        n = len(self.nodes) + extra_nodes
        # csgraph treats explicit zeros as missing edges, so floor at a tiny positive cost.
        return csr_matrix((np.maximum(e.w.to_numpy(), 1e-6), (e.r, e.c)), shape=(n, n))


def edge_id(u, v, k) -> str:
    return f"{u}-{v}-{k}"


def load_network(mode: str) -> Network:
    nodes = gpd.read_parquet(DATA / f"{mode}_nodes.parquet")
    xy = nodes.to_crs(CRS).geometry
    nodes = pd.DataFrame({"x": xy.x, "y": xy.y, "lon": nodes.x, "lat": nodes.y}, index=nodes.index)
    e = gpd.read_parquet(DATA / f"{mode}_edges.parquet").reset_index()
    edges = pd.DataFrame({
        "u": e["u"].to_numpy(), "v": e["v"].to_numpy(), "length": e["length"].to_numpy(),
        "speed_kph": e["speed_kph"].to_numpy(), "travel_time": e["travel_time"].to_numpy(),
        "name": e["name"].replace("nan", None).to_numpy(), "highway": e["highway"].to_numpy(),
        "osmid": [tuple(int(t) for t in re.findall(r"\d+", s)) for s in e["osmid"].astype(str)],
    }, index=[edge_id(u, v, k) for u, v, k in zip(e.u, e.v, e.key)])
    return Network(mode, nodes, edges)


def merge_close(f: pd.DataFrame, dist_m: float) -> pd.DataFrame:
    """Merge points closer than dist_m (single linkage) into one facility at their mean position."""
    if len(f) < 2:
        return f
    pairs = cKDTree(f[["x", "y"]].to_numpy()).query_pairs(dist_m, output_type="ndarray")
    adj = csr_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(f), len(f)))
    _, label = connected_components(adj, directed=False)
    f = f.assign(_g=label)
    first = f.groupby("_g").head(1).set_index("_g")
    agg = f.groupby("_g").agg(x=("x", "mean"), y=("y", "mean"), lon=("lon", "mean"), lat=("lat", "mean"),
                              merged=("name", "size"))
    named = f.dropna(subset=["name"]).groupby("_g")["name"].first()
    out = first.drop(columns=["x", "y", "lon", "lat", "name"]).join(agg).join(named)
    return out.set_index(f.groupby("_g").head(1).index)


def load_facilities() -> pd.DataFrame:
    g = gpd.read_file(DATA / "facilities.gpkg")
    xy = g.to_crs(CRS).geometry
    f = pd.DataFrame({
        "category": g.category, "name": g["name:fr"].fillna(g["name"]),
        "x": xy.x, "y": xy.y, "lon": g.geometry.x, "lat": g.geometry.y,
        "capacity": 1.0,  # no capacity data in OSM; equal capacity for every facility
    })
    f.index = [f"osm:{e}/{i}" for e, i in zip(g.element, g.id)]
    hosp = merge_close(f[f.category == "hospital"], HOSPITAL_MERGE_M)
    f = pd.concat([hosp, f[f.category != "hospital"]])
    f["merged"] = f["merged"].fillna(1).astype(int)
    return f


def load_cells(res: int = 9) -> pd.DataFrame:
    g = gpd.read_parquet(DATA / f"h3_pop_r{res}.parquet")
    xy = g.to_crs(CRS).geometry
    return pd.DataFrame({"pop": g["pop"].to_numpy(), "x": xy.x.to_numpy(), "y": xy.y.to_numpy(),
                         "lon": g.geometry.x.to_numpy(), "lat": g.geometry.y.to_numpy()},
                        index=pd.Index(g.h3, name="h3"))


def load_communes() -> gpd.GeoDataFrame:
    c = gpd.read_file(DATA / "communes.gpkg")
    c["label"] = c["name:fr"].fillna(c["name"])
    return c[["label", "geometry"]]
