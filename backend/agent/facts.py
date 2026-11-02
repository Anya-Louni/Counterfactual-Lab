"""The compact, fully computed fact sheet the narrator may quote from."""

from backend.metrics.accessibility import THRESHOLDS_MIN
from backend.network.edits import AddFacility, RemoveFacility

KEEP = ("pop_within_15", "median_min", "gini", "p90_p10", "sfca_median", "sfca_gini", "pop_unreachable")
MAX_KEYS = 4


def build(comparison: dict, edits: list, pop_total: float) -> dict:
    summary = comparison["summary"]
    facility_only = all(isinstance(e, (AddFacility, RemoveFacility)) for e in edits)
    keys = list(summary)
    if facility_only:
        cats = {getattr(e, "category", None) for e in edits}
        keys = [k for k in keys if k.split(".")[1] in cats] or keys
    keys.sort(key=lambda k: -(summary[k]["pop_gaining"] + summary[k]["pop_losing"]))
    results = {}
    for k in keys[:MAX_KEYS]:
        s = summary[k]
        r = {m: s[m] for m in KEEP if m in s}
        for m in ("pop_gaining", "pop_losing", "pop_newly_within_15", "pop_no_longer_within_15",
                  "largest_gain_min", "largest_loss_min"):
            r[m] = s[m]
        r["pct_gaining"] = round(100 * s["pop_gaining"] / pop_total, 1)
        r["pct_losing"] = round(100 * s["pop_losing"] / pop_total, 1)
        top = comparison["top"].get(k, {})
        r["gaining_communes"] = top.get("gaining_communes", [])[:3]
        r["losing_communes"] = top.get("losing_communes", [])[:3]
        mode, cat = k.split(".")
        results[f"{cat} by {'car' if mode == 'drive' else 'foot'}"] = r
    return {
        "population_total": round(pop_total),
        "thresholds_min": list(THRESHOLDS_MIN),
        "results": results,
        "no_change": not results,
    }


def template(facts: dict) -> str:
    """Plain fallback narrative, used when the LLM keeps quoting numbers it was not given."""
    if facts["no_change"]:
        return "This change does not move anyone's travel time to the nearest facility by half a minute or more."
    lines = []
    for name, r in facts["results"].items():
        ch = r["median_min"]["change"] if "median_min" in r else 0
        lines.append(f"{name}: {r['pop_gaining']:,} people get faster access and {r['pop_losing']:,} slower. "
                     f"Median time changes by {ch} min; {r['pop_newly_within_15']:,} people are newly within 15 min.")
    return " ".join(lines)
