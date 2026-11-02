"""The plan the LLM writes, and how code turns it into typed edits.

The LLM never writes coordinates. It refers to places returned by search tools
(refs like "p3") or clicked on the map ("click1"). Code resolves the refs.
"""

from typing import Literal

import shapely
from pydantic import BaseModel, Field
from pyproj import Transformer

from backend.network.edits import AddFacility, AddRoad, RoadSpeedUpgrade, RemoveFacility, RemoveRoad, to_xy

TO_LONLAT = Transformer.from_crs(32631, 4326, always_xy=True)


class EditSpec(BaseModel):
    op: Literal["add_facility", "remove_facility", "add_road", "remove_road", "road_speed_upgrade"]
    category: Literal["hospital", "clinic", "pharmacy", "school"] | None = Field(
        None, description="add_facility only")
    place: str | None = Field(None, description="place ref. add_facility: where. remove_road/road_speed_upgrade: "
                                                "centre of an area, used with radius_m")
    radius_m: float | None = Field(None, description="area radius around place, metres, 50 to 2000")
    road: str | None = Field(None, description="ref of a road returned by search_places (kind=road)")
    start: str | None = Field(None, description="add_road: place ref of one end")
    end: str | None = Field(None, description="add_road: place ref of the other end")
    facility_id: str | None = Field(None, description="remove_facility: id from search results")
    speed_kph: float | None = Field(None, description="add_road speed, or road_speed_upgrade target speed")
    factor: float | None = Field(None, description="road_speed_upgrade multiplier above 1, e.g. 1.5 for 50% faster")
    modes: list[Literal["drive", "walk"]] | None = Field(None, description="add_road/remove_road; default both")
    name: str | None = None


class PlanSpec(BaseModel):
    """Submit the final edit plan."""
    summary: str = Field(description="one sentence describing the change")
    base: Literal["current", "factual"] = Field(
        "factual", description="'current' extends the scenario the user is looking at; 'factual' starts fresh")
    edits: list[EditSpec]


def circle(lon: float, lat: float, r: float) -> list[tuple[float, float]]:
    ring = shapely.Point(*to_xy((lon, lat))).buffer(r, 16).exterior.coords
    return [TO_LONLAT.transform(x, y) for x, y in ring]


def resolve(spec: PlanSpec, places: dict[str, dict]) -> tuple[list, list[str]]:
    """Typed edits plus a list of problems (unknown refs, missing fields)."""
    edits, problems = [], []

    def pt(ref, i, what):
        if ref not in places:
            problems.append(f"edit {i}: {what} ref {ref!r} is not a place returned by a tool")
            return None
        return (places[ref]["lon"], places[ref]["lat"])

    for i, e in enumerate(spec.edits):
        try:
            if e.op == "add_facility":
                loc = pt(e.place, i, "place")
                if loc and e.category:
                    edits.append(AddFacility(category=e.category, location=loc, name=e.name or "New facility"))
                elif not e.category:
                    problems.append(f"edit {i}: add_facility needs a category")
            elif e.op == "remove_facility":
                edits.append(RemoveFacility(facility_id=e.facility_id or ""))
            elif e.op == "add_road":
                a, b = pt(e.start, i, "start"), pt(e.end, i, "end")
                if a and b:
                    edits.append(AddRoad(start=a, end=b, speed_kph=e.speed_kph or 40,
                                         modes=e.modes or ["drive", "walk"], name=e.name or "New road"))
            elif e.op in ("remove_road", "road_speed_upgrade"):
                sel: dict = {}
                if e.road:
                    if e.road in places and places[e.road].get("way_ids"):
                        sel["way_ids"] = places[e.road]["way_ids"]
                    else:
                        problems.append(f"edit {i}: road ref {e.road!r} is not a road returned by search_places")
                if e.place and e.radius_m:
                    c = pt(e.place, i, "place")
                    if c:
                        sel["area"] = circle(*c, min(max(e.radius_m, 50), 2000))
                if not sel:
                    problems.append(f"edit {i}: {e.op} needs a road ref or a place with radius_m")
                    continue
                if e.op == "remove_road":
                    edits.append(RemoveRoad(**sel, modes=e.modes or ["drive", "walk"]))
                else:
                    edits.append(RoadSpeedUpgrade(**sel, factor=e.factor, speed_kph=e.speed_kph if not e.factor else None))
        except ValueError as err:
            problems.append(f"edit {i}: {err}")
    return edits, problems
