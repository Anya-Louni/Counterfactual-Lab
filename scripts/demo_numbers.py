"""Headline numbers for the three README demos, without the LLM.

Edits come from docs/demo_scenarios.json (the plans the agent produced and that were approved).

uv run python scripts/demo_numbers.py              # current settings
uv run python scripts/demo_numbers.py --sensitivity  # catchment 10, 15, 20, 30 min for every layer
"""

import json
import sys
from pathlib import Path

from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.lab import Lab  # noqa: E402
from backend.metrics import accessibility as acc  # noqa: E402
from backend.metrics.accessibility import Engine  # noqa: E402
from backend.metrics.compare import compare  # noqa: E402
from backend.metrics.equity import weighted_gini, weighted_quantile  # noqa: E402
from backend.network.edits import Edit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EDITS = TypeAdapter(list[Edit])
KEYS = ("pop_gaining", "pop_losing", "pop_newly_within_15", "pop_no_longer_within_15", "largest_loss_min",
        "largest_gain_min")


def headline(lab, engine, demos) -> dict:
    f = lab.tree.factual
    fact = engine.compute(f.networks, f.facilities)
    out = {}
    for d in demos:
        s = lab.tree.branch("factual", EDITS.validate_python(d["edits"]))
        cf = engine.compute(s.networks, s.facilities)
        c = compare(fact, cf, lab.cells, lab.cells["commune"])["summary"][d["layer"]]
        row = {k: c[k] for k in KEYS}
        for k in ("median_min", "gini", "mean_cum"):
            row[k] = c[k]["change"]
        # Computed from cells so old and new code report the same measures.
        w = lab.cells["pop"].to_numpy()
        col = f"{d['layer']}.sfca"
        for name, fn in (("sfca_median", lambda x: weighted_quantile(x, w, 0.5)), ("sfca_gini", lambda x: weighted_gini(x, w))):
            a, b = (fn(m.cells[col].to_numpy()) for m in (fact, cf))
            row[name] = f"{a:.4f} -> {b:.4f}"
        out[d["name"]] = row
        del lab.tree.nodes[s.id]
    return out


def main():
    demos = json.loads((ROOT / "docs" / "demo_scenarios.json").read_text(encoding="utf-8"))
    lab = Lab()
    if "--sensitivity" not in sys.argv:
        print(json.dumps(headline(lab, Engine(lab.cells), demos), indent=1))
        return
    rows = {}
    for d0 in (10, 15, 20, 30):
        acc.CATCHMENT_OVERRIDE = d0
        rows[d0] = headline(lab, Engine(lab.cells), demos)  # fresh engine: no cached catchments
        print(d0, json.dumps({n: {k: r[k] for k in ("pop_gaining", "pop_losing", "sfca_median", "sfca_gini", "mean_cum")}
                              for n, r in rows[d0].items()}), flush=True)
    (ROOT / "docs" / "sensitivity.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
