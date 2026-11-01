"""Typed edits and how each one changes a sandbox copy of the city.

Coordinates are (lon, lat) in WGS 84. Roads are picked by OSM way id, by edge id,
or by an area polygon. Way ids work across the drive and walk graphs.
"""

from itertools import count
from typing import Annotated, Literal

import numpy as np
import pandas as pd
import shapely
from pydantic import BaseModel, Field, model_validator
from pyproj import Transformer

from .model import WALK_MPS, Network

Mode = Literal["drive", "walk"]
Category = Literal["hospital", "clinic", "pharmacy", "school"]
LonLat = tuple[float, float]

TO_UTM = Transformer.from_crs(4326, 32631, always_xy=True)
_new_ids = count(1)


class AddFacility(BaseModel):
    op: Literal["add_facility"] = "add_facility"
    category: Category
    location: LonLat
    name: str = "New facility"
    capacity: float = Field(1.0, gt=0)


class RemoveFacility(BaseModel):
    op: Literal["remove_facility"] = "remove_facility"
    facility_id: str


class AddRoad(BaseModel):
    op: Literal["add_road"] = "add_road"
    start: LonLat
    end: LonLat
    speed_kph: float = Field(40.0, gt=0, le=130)
    modes: list[Mode] = ["drive", "walk"]
    name: str = "New road"


class RoadSelection(BaseModel):
    way_ids: list[int] = []
    edge_ids: list[str] = []
    area: list[LonLat] | None = None  # polygon ring; edges with an end inside are selected


class RemoveRoad(RoadSelection):
    op: Literal["remove_road"] = "remove_road"
    modes: list[Mode] = ["drive", "walk"]


class ChangeSpeed(RoadSelection):
    """Driving only. Proxy for a road upgrade or a bus corridor."""
    op: Literal["change_speed"] = "change_speed"
    factor: float | None = Field(None, gt=0, le=5)
    speed_kph: float | None = Field(None, gt=0, le=130)

    @model_validator(mode="after")
    def one_target(self):
        if (self.factor is None) == (self.speed_kph is None):
            raise ValueError("give exactly one of factor or speed_kph")
        return self


Edit = Annotated[AddFacility | RemoveFacility | AddRoad | RemoveRoad | ChangeSpeed, Field(discriminator="op")]


class EditPlan(BaseModel):
    summary: str
    edits: list[Edit]


def to_xy(lonlat: LonLat) -> tuple[float, float]:
    return TO_UTM.transform(*lonlat)


def select_edges(net: Network, sel: RoadSelection) -> pd.Index:
    """Edge ids picked by the selection, plus the opposite direction of each picked road."""
    e = net.edges
    mask = e.index.isin(sel.edge_ids)
    if sel.way_ids:
        ways = set(sel.way_ids)
        mask |= e["osmid"].map(lambda ids: not ways.isdisjoint(ids)).to_numpy()
    if sel.area:
        ring = shapely.Polygon([to_xy(p) for p in sel.area])
        nx_, ny_ = net.nodes.x, net.nodes.y
        inside = pd.Series(shapely.contains_xy(ring, nx_.to_numpy(), ny_.to_numpy()), index=net.nodes.index)
        mask |= (inside[e.u].to_numpy() | inside[e.v].to_numpy())
    picked = e[mask]
    pairs = set(zip(picked.u, picked.v))
    twins = [(v, u) in pairs for u, v in zip(e.u, e.v)]
    return e.index[mask | np.array(twins, dtype=bool)]


def apply_edit(edit, networks: dict[str, Network], facilities: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Apply one edit in place on sandbox copies. Returns the new facilities table and a log line."""
    if isinstance(edit, AddFacility):
        x, y = to_xy(edit.location)
        fid = f"new:{next(_new_ids)}"
        row = pd.DataFrame({"category": edit.category, "name": edit.name, "x": x, "y": y,
                            "lon": edit.location[0], "lat": edit.location[1],
                            "capacity": edit.capacity, "merged": 1}, index=[fid])
        return pd.concat([facilities, row]), f"added {edit.category} {fid} at {edit.location}"

    if isinstance(edit, RemoveFacility):
        return facilities.drop(index=edit.facility_id), f"removed facility {edit.facility_id}"

    if isinstance(edit, AddRoad):
        notes = []
        for mode in edit.modes:
            net = networks[mode]
            (a, b), _ = net.snap(*zip(to_xy(edit.start), to_xy(edit.end)))
            ua, ub = net.nodes.index[a], net.nodes.index[b]
            length = float(np.hypot(*(net.nodes.iloc[a][["x", "y"]] - net.nodes.iloc[b][["x", "y"]])))
            speed = edit.speed_kph if mode == "drive" else 4.5
            tt = length / (speed / 3.6) if mode == "drive" else length / WALK_MPS
            n = next(_new_ids)
            new = pd.DataFrame({"u": [ua, ub], "v": [ub, ua], "length": length, "speed_kph": speed,
                                "travel_time": tt, "name": edit.name, "highway": "new", "osmid": [(), ()]},
                               index=[f"new-{ua}-{ub}-{n}", f"new-{ub}-{ua}-{n}"])
            net.edges = pd.concat([net.edges, new])
            notes.append(f"{mode}: road {ua} to {ub}, {length:.0f} m, {tt:.0f} s")
        return facilities, "added road; " + "; ".join(notes)

    if isinstance(edit, RemoveRoad):
        notes = []
        for mode in edit.modes:
            net = networks[mode]
            ids = select_edges(net, edit)
            net.edges = net.edges.drop(index=ids)
            notes.append(f"{mode}: {len(ids)} edges removed")
        return facilities, "closed road; " + "; ".join(notes)

    if isinstance(edit, ChangeSpeed):
        net = networks["drive"]
        ids = select_edges(net, edit)
        e = net.edges.copy()  # never write into a table a parent scenario may share
        new_speed = e.loc[ids, "speed_kph"] * edit.factor if edit.factor else edit.speed_kph
        e.loc[ids, "speed_kph"] = new_speed
        e.loc[ids, "travel_time"] = e.loc[ids, "length"] / (e.loc[ids, "speed_kph"] / 3.6)
        net.edges = e
        return facilities, f"drive: speed changed on {len(ids)} edges"

    raise TypeError(f"unknown edit {edit!r}")
