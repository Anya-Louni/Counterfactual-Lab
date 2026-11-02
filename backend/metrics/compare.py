"""Factual vs counterfactual: per-cell change, summary deltas, who gains and who loses."""

import geopandas as gpd
import numpy as np
import pandas as pd

from backend.network.model import CATEGORIES, MODES

from .accessibility import Metrics

MIN_CHANGE = 0.5  # minutes; smaller changes count as no change
TOP_N = 8


def commune_of(cells: pd.DataFrame, communes: gpd.GeoDataFrame) -> pd.Series:
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(cells.lon, cells.lat), index=cells.index, crs=4326)
    # Nearest commune, so coastal cells just outside a polygon still get a name.
    j = gpd.sjoin_nearest(pts.to_crs(32631), communes.to_crs(32631), how="left")
    return j[~j.index.duplicated()]["label"].reindex(cells.index)


def compare(fact: Metrics, cf: Metrics, cells: pd.DataFrame, commune: pd.Series) -> dict:
    pop = cells["pop"]
    out: dict = {"summary": {}, "layers": {}, "top": {}}
    for mode in MODES:
        for cat in CATEGORIES:
            k = f"{mode}.{cat}"
            a, b = fact.cells[f"{k}.time_min"], cf.cells[f"{k}.time_min"]
            # Cells that lose all access get +inf; keep them out of averages, count them separately.
            delta = (b - a).where(np.isfinite(a) & np.isfinite(b))
            changed = delta.abs() >= MIN_CHANGE
            if not changed.any() and (np.isfinite(a) == np.isfinite(b)).all():
                continue
            s = {}
            for key in ("pop_within_10", "pop_within_15", "pop_within_30", "median_min", "mean_min",
                        "gini", "p90_p10", "mean_cum", "sfca_median", "sfca_gini", "pop_unreachable"):
                fa, cb = fact.summary.get(f"{k}.{key}"), cf.summary.get(f"{k}.{key}")
                if fa is not None and cb is not None:
                    s[key] = {"factual": fa, "counterfactual": cb, "change": round(cb - fa, 3)}
            s["pop_gaining"] = round(float(pop[delta <= -MIN_CHANGE].sum()))
            s["pop_losing"] = round(float(pop[delta >= MIN_CHANGE].sum()))
            s["pop_newly_within_15"] = round(float(pop[(a > 15) & (b <= 15)].sum()))
            s["pop_no_longer_within_15"] = round(float(pop[(a <= 15) & (b > 15)].sum()))
            s["largest_gain_min"] = round(float(-delta.min()), 1) if delta.notna().any() else 0.0
            s["largest_loss_min"] = round(float(delta.max()), 1) if delta.notna().any() else 0.0
            out["summary"][k] = s
            out["layers"][k] = delta[changed].round(2).to_dict()

            df = pd.DataFrame({"delta": delta, "pop": pop, "commune": commune})[changed]
            df["pop_x_min"] = df["pop"] * df["delta"]
            by_c = df.groupby("commune").apply(
                lambda g: pd.Series({"pop_affected": round(float(g["pop"].sum())),
                                     "mean_change_min": round(float(np.average(g["delta"], weights=g["pop"])), 1)}),
                include_groups=False,
            )
            out["top"][k] = {
                "gaining_communes": by_c.sort_values("mean_change_min").head(TOP_N)
                .query("mean_change_min < 0").reset_index().to_dict("records"),
                "losing_communes": by_c.sort_values("mean_change_min", ascending=False).head(TOP_N)
                .query("mean_change_min > 0").reset_index().to_dict("records"),
            }
    return out
