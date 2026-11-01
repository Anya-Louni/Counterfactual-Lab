"""Search names already in the local data: facilities, communes, roads."""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

import pandas as pd


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


@dataclass
class Place:
    kind: str  # facility | commune | road | geocoder | click
    name: str
    lon: float
    lat: float
    id: str | None = None
    category: str | None = None
    way_ids: list[int] | None = None
    score: float = 0.0

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v is not None}
        d["lon"], d["lat"], d["score"] = round(float(self.lon), 6), round(float(self.lat), 6), round(float(self.score), 2)
        if self.way_ids is not None:
            d["way_ids"] = [int(w) for w in self.way_ids]
        return d


class LocalIndex:
    def __init__(self, facilities: pd.DataFrame, communes, drive_edges: pd.DataFrame, drive_nodes: pd.DataFrame):
        rows = []
        for fid, r in facilities.iterrows():
            if isinstance(r["name"], str):
                rows.append(Place("facility", r["name"], r.lon, r.lat, id=fid, category=r.category))
        c = communes.to_crs(32631)
        cent = c.representative_point().to_crs(4326)
        for label, p in zip(c.label, cent):
            rows.append(Place("commune", label, p.x, p.y))
        named = drive_edges.dropna(subset=["name"])
        for name, g in named.groupby("name"):
            ways = sorted({w for ids in g.osmid for w in ids})
            mid = drive_nodes.loc[g.u.iloc[len(g) // 2]]
            rows.append(Place("road", name, mid.lon, mid.lat, way_ids=ways))
        self.places = rows
        self._keys = [norm(p.name) for p in rows]

    def search(self, query: str, kind: str | None = None, category: str | None = None, k: int = 5) -> list[Place]:
        q = norm(query)
        qt = set(q.split())
        scored = []
        for p, key in zip(self.places, self._keys):
            if (kind and p.kind != kind) or (category and p.category != category):
                continue
            kt = set(key.split())
            overlap = len(qt & kt) / max(len(qt), 1)
            s = 0.6 * overlap + 0.4 * SequenceMatcher(None, q, key).ratio()
            if q and q in key:
                s += 0.2
            if s > 0.45:
                scored.append((s, p))
        scored.sort(key=lambda t: -t[0])
        out = []
        for s, p in scored[:k]:
            out.append(Place(**{**p.__dict__, "score": s}))
        return out
