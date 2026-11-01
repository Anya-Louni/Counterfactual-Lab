"""Loads the cached city once and holds everything a session needs."""

import json
import time
from functools import cached_property

import geopandas as gpd
import pandas as pd

from backend.metrics.accessibility import Engine, Metrics
from backend.metrics.compare import commune_of, compare
from backend.network.model import CRS, DATA, MODES, load_cells, load_communes, load_facilities, load_network
from backend.geocode.local import LocalIndex
from backend.network.sandbox import ScenarioTree


class Lab:
    def __init__(self, h3_res: int = 9):
        t = time.perf_counter()
        self.cells = load_cells(h3_res)
        self.communes = load_communes()
        self.cells["commune"] = commune_of(self.cells, self.communes)
        self.tree = ScenarioTree({m: load_network(m) for m in MODES}, load_facilities())
        self.engine = Engine(self.cells)
        self.metrics: dict[str, Metrics] = {}
        self._comparisons: dict[tuple, dict] = {}
        cached = DATA / f"factual_r{h3_res}.parquet"
        f = self.tree.factual
        if cached.exists():
            meta = json.loads(cached.with_suffix(".json").read_text())
            # Use the cache only if it was built from exactly this data.
            if meta["keys"] == self.factual_keys():
                m = Metrics(pd.read_parquet(cached), meta["summary"], meta["seconds"])
                self.engine.seed(f.networks, f.facilities, m)
                self.metrics["factual"] = m
        self.load_s = round(time.perf_counter() - t, 1)

    def factual_keys(self) -> list[str]:
        f = self.tree.factual
        return [str(k) for k in self.engine.keys(f.networks, f.facilities).values()]

    def save_factual(self, h3_res: int = 9) -> None:
        m = self.metrics_for("factual")
        path = DATA / f"factual_r{h3_res}.parquet"
        m.cells.to_parquet(path)
        path.with_suffix(".json").write_text(json.dumps(
            {"keys": self.factual_keys(), "summary": m.summary, "seconds": m.seconds}, indent=1))

    @cached_property
    def boundary_utm(self):
        return gpd.read_file(DATA / "boundary.gpkg").to_crs(CRS).geometry.iloc[0]

    def metrics_for(self, sid: str) -> Metrics:
        if sid not in self.metrics:
            s = self.tree.nodes[sid]
            self.metrics[sid] = self.engine.compute(s.networks, s.facilities)
        return self.metrics[sid]

    @cached_property
    def index(self) -> LocalIndex:
        f = self.tree.factual
        return LocalIndex(f.facilities, self.communes, f.networks["drive"].edges, f.networks["drive"].nodes)

    def comparison(self, sid: str, base: str = "factual") -> dict:
        key = (sid, base)
        if key not in self._comparisons:
            self._comparisons[key] = compare(self.metrics_for(base), self.metrics_for(sid), self.cells,
                                             self.cells["commune"])
        return self._comparisons[key]
