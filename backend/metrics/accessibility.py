"""Accessibility for one scenario: every populated H3 cell to every facility category, both modes.

Time from a cell = walk from cell centre to its nearest network node
                 + network time to the facility's node
                 + walk from that node to the facility.
Dijkstra runs on the reversed graph from the facilities, so one run gives every cell.
"""

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from pandas.util import hash_pandas_object
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

from backend.network.model import CATEGORIES, MODES, WALK_MPS, Network

from .equity import weighted_gini, weighted_quantile

THRESHOLDS_MIN = (10, 15, 30)
CATCHMENT_MIN = {"drive": 15, "walk": 15}  # 2SFCA and cumulative-opportunity threshold
CHUNK = 64


@dataclass
class Metrics:
    cells: pd.DataFrame  # index h3. per (mode, category): time_min, cum, sfca
    summary: dict[str, float]  # flat keys "drive.hospital.pop_within_15"
    seconds: dict[str, float] = field(default_factory=dict)


def _fingerprint(df: pd.DataFrame, cols) -> int:
    return int(hash_pandas_object(df[cols], index=True).sum())


class Engine:
    """Computes metrics and caches them per (mode, network state, facility set)."""

    def __init__(self, cells: pd.DataFrame):
        self.cells = cells
        self._snap: dict[str, tuple] = {}
        self._cache: dict[tuple, pd.DataFrame] = {}

    def _cell_snap(self, net: Network):
        if net.mode not in self._snap:  # nodes never change, so one snap per mode
            pos, d = net.snap(self.cells.x.to_numpy(), self.cells.y.to_numpy())
            self._snap[net.mode] = (pos, d / WALK_MPS)
        return self._snap[net.mode]

    def _one(self, net: Network, mat: csr_matrix, fac: pd.DataFrame) -> pd.DataFrame:
        n = len(net.nodes)
        cell_pos, cell_acc = self._cell_snap(net)
        out = pd.DataFrame(index=self.cells.index)
        if fac.empty:
            out["time_min"], out["cum"], out["sfca"] = np.inf, 0, 0.0
            return out
        f_pos, f_d = net.snap(fac.x.to_numpy(), fac.y.to_numpy())
        f_acc = f_d / WALK_MPS

        # Nearest: super source linked to each facility node at its access cost.
        best = pd.Series(f_acc).groupby(f_pos).min()
        sup = csr_matrix((best.to_numpy() + 1e-6, (np.full(len(best), n), best.index.to_numpy())), shape=mat.shape)
        d = dijkstra(mat + sup, indices=n)
        out["time_min"] = (d[cell_pos] + cell_acc) / 60

        # Catchments: facility x cell travel times up to the threshold.
        lim = CATCHMENT_MIN[net.mode] * 60
        rows, cols, vals = [], [], []
        for i in range(0, len(fac), CHUNK):
            sl = slice(i, i + CHUNK)
            m = dijkstra(mat, indices=f_pos[sl], limit=lim)[:, cell_pos]
            t = m + f_acc[sl, None] + cell_acc[None, :]
            r, c = np.nonzero(t <= lim)
            rows.append(r + i), cols.append(c), vals.append(t[r, c])
        r, c = np.concatenate(rows), np.concatenate(cols)
        reach = csr_matrix((np.ones(len(r)), (r, c)), shape=(len(fac), len(self.cells)))
        out["cum"] = np.asarray(reach.sum(axis=0)).ravel().astype(int)
        # 2SFCA: supply/demand ratio per facility, summed over reachable facilities, per 10,000 people.
        demand = reach @ self.cells["pop"].to_numpy()
        ratio = np.divide(fac.capacity.to_numpy(), demand, out=np.zeros(len(fac)), where=demand > 0)
        out["sfca"] = (reach.T @ ratio) * 10_000
        return out

    @staticmethod
    def keys(networks: dict[str, Network], facilities: pd.DataFrame) -> dict[tuple, tuple]:
        """Cache key per (mode, category): network state plus that category's facilities."""
        out = {}
        for mode in MODES:
            nkey = _fingerprint(networks[mode].edges, ["u", "v", "travel_time"])
            for cat in CATEGORIES:
                fac = facilities[facilities.category == cat]
                out[(mode, cat)] = (mode, nkey, _fingerprint(fac, ["x", "y", "capacity"]))
        return out

    def seed(self, networks, facilities, metrics: Metrics) -> None:
        """Fill the cache from metrics loaded off disk."""
        for (mode, cat), key in self.keys(networks, facilities).items():
            part = metrics.cells[[f"{mode}.{cat}.{k}" for k in ("time_min", "cum", "sfca")]]
            self._cache[key] = part.set_axis(["time_min", "cum", "sfca"], axis=1)

    def compute(self, networks: dict[str, Network], facilities: pd.DataFrame) -> Metrics:
        parts, secs, mats = {}, {}, {}
        for (mode, cat), key in self.keys(networks, facilities).items():
            t = time.perf_counter()
            if key not in self._cache:
                if mode not in mats:
                    mats[mode] = networks[mode].reversed_matrix(extra_nodes=1)
                fac = facilities[facilities.category == cat]
                self._cache[key] = self._one(networks[mode], mats[mode], fac)
            parts[(mode, cat)] = self._cache[key]
            secs[f"{mode}.{cat}"] = round(time.perf_counter() - t, 2)
        cells = pd.concat(parts, axis=1)
        cells.columns = [".".join(c) for c in cells.columns]
        return Metrics(cells, summarize(cells, self.cells["pop"]), secs)


def summarize(cells: pd.DataFrame, pop: pd.Series) -> dict[str, float]:
    s: dict[str, float] = {"pop_total": round(float(pop.sum()))}
    w = pop.to_numpy()
    for mode in MODES:
        for cat in CATEGORIES:
            k = f"{mode}.{cat}"
            t = cells[f"{k}.time_min"].to_numpy()
            ok = np.isfinite(t)
            s[f"{k}.pop_unreachable"] = round(float(w[~ok].sum()))
            for th in THRESHOLDS_MIN:
                s[f"{k}.pop_within_{th}"] = round(float(w[ok & (t <= th)].sum()))
            if ok.any():
                s[f"{k}.median_min"] = round(weighted_quantile(t[ok], w[ok], 0.5), 1)
                s[f"{k}.mean_min"] = round(float(np.average(t[ok], weights=w[ok])), 1)
                p10, p90 = weighted_quantile(t[ok], w[ok], 0.1), weighted_quantile(t[ok], w[ok], 0.9)
                s[f"{k}.p90_p10"] = round(p90 / p10, 2) if p10 > 0 else float("nan")
                s[f"{k}.gini"] = round(weighted_gini(t[ok], w[ok]), 3)
            s[f"{k}.mean_cum"] = round(float(np.average(cells[f"{k}.cum"], weights=w)), 2)
            s[f"{k}.mean_sfca"] = round(float(np.average(cells[f"{k}.sfca"], weights=w)), 3)
    return s
