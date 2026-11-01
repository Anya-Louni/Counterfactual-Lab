"""Code checks on an edit plan before anything is applied."""

import numpy as np
import shapely
from pydantic import BaseModel
from scipy.sparse.csgraph import connected_components

from .edits import AddFacility, AddRoad, ChangeSpeed, RemoveFacility, RemoveRoad, select_edges, to_xy
from .sandbox import ScenarioTree

MAX_SNAP_M = 300  # a new road end or facility further than this from any road is suspicious
DISCONNECT_WARN = 0.005  # share of nodes cut off from the main component


class Problem(BaseModel):
    edit_index: int
    severity: str  # "error" blocks the plan, "warning" is shown to the user
    message: str


def validate(plan_edits: list, tree: ScenarioTree, parent_id: str, boundary_utm) -> list[Problem]:
    parent = tree.nodes[parent_id]
    out: list[Problem] = []

    def add(i, sev, msg):
        out.append(Problem(edit_index=i, severity=sev, message=msg))

    for i, e in enumerate(plan_edits):
        points = []
        if isinstance(e, AddFacility):
            points = [e.location]
        elif isinstance(e, AddRoad):
            points = [e.start, e.end]
        for p in points:
            x, y = to_xy(p)
            if not boundary_utm.contains(shapely.Point(x, y)):
                add(i, "error", f"point {p} is outside the study area (Algiers wilaya)")
                continue
            for mode in getattr(e, "modes", ["walk", "drive"]):
                _, d = parent.networks[mode].snap(x, y)
                if d[0] > MAX_SNAP_M:
                    add(i, "error", f"point {p} is {d[0]:.0f} m from the nearest {mode} road")
        if isinstance(e, AddRoad):
            for mode in e.modes:
                net = parent.networks[mode]
                (a, b), _ = net.snap(*zip(to_xy(e.start), to_xy(e.end)))
                if a == b:
                    add(i, "error", f"both ends snap to the same {mode} node; the road adds nothing")
        if isinstance(e, RemoveFacility) and e.facility_id not in parent.facilities.index:
            add(i, "error", f"facility {e.facility_id} does not exist in this scenario")
        if isinstance(e, (RemoveRoad, ChangeSpeed)):
            modes = e.modes if isinstance(e, RemoveRoad) else ["drive"]
            for mode in modes:
                if len(select_edges(parent.networks[mode], e)) == 0:
                    add(i, "error", f"the road selection matches no {mode} edges")

    if any(p.severity == "error" for p in out):
        return out

    # Connectivity: apply to a throwaway branch and measure what falls off the main component.
    trial = tree.branch(parent_id, plan_edits)
    try:
        for mode, net in trial.networks.items():
            m = net.reversed_matrix()
            _, lab = connected_components(m, directed=True, connection="strong")
            share = 1 - np.bincount(lab).max() / len(lab)
            _, lab0 = connected_components(parent.networks[mode].reversed_matrix(), directed=True, connection="strong")
            base = 1 - np.bincount(lab0).max() / len(lab0)
            if share - base > DISCONNECT_WARN:
                add(-1, "warning", f"{mode}: {100 * (share - base):.1f}% of network nodes become unreachable")
    finally:
        del tree.nodes[trial.id]
    return out
