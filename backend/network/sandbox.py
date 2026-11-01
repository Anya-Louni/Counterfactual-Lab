"""Scenario tree. The factual city is the root and is never mutated."""

from dataclasses import dataclass, field

import pandas as pd

from .edits import apply_edit
from .model import Network


@dataclass
class Scenario:
    id: str
    parent: str | None
    edits: list  # edits added at this node only
    networks: dict[str, Network]
    facilities: pd.DataFrame
    log: list[str] = field(default_factory=list)


class ScenarioTree:
    def __init__(self, networks: dict[str, Network], facilities: pd.DataFrame):
        for n in networks.values():
            n.tree  # snapping is fixed by the factual city and shared by every branch
        self.nodes: dict[str, Scenario] = {
            "factual": Scenario("factual", None, [], networks, facilities)
        }
        self._n = 0

    @property
    def factual(self) -> Scenario:
        return self.nodes["factual"]

    def branch(self, parent_id: str, edits: list) -> Scenario:
        """New scenario = parent state + edits, applied to copies."""
        parent = self.nodes[parent_id]
        nets = {m: n.copy() for m, n in parent.networks.items()}
        fac = parent.facilities.copy()
        log = []
        for e in edits:
            fac, line = apply_edit(e, nets, fac)
            log.append(line)
        self._n += 1
        s = Scenario(f"s{self._n}", parent_id, list(edits), nets, fac, log)
        self.nodes[s.id] = s
        return s

    def lineage(self, sid: str) -> list[Scenario]:
        out = []
        while sid is not None:
            out.append(self.nodes[sid])
            sid = self.nodes[sid].parent
        return out[::-1]

    def all_edits(self, sid: str) -> list:
        return [e for s in self.lineage(sid) for e in s.edits]
