"""Compute and cache factual metrics, then draw travel time to the nearest hospital.

Writes data/factual_r9.parquet (+ .json) and docs/img/factual_hospital.png.
"""

import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import h3
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.lab import Lab  # noqa: E402
from backend.network.model import CRS, DATA  # noqa: E402

DOCS = Path(__file__).resolve().parents[1] / "docs" / "img"
BG, FG, MUTED = "#0e1116", "#e6e8eb", "#7d8590"
BINS = [0, 5, 10, 15, 30, 60, np.inf]
LABELS = ["< 5", "5 to 10", "10 to 15", "15 to 30", "30 to 60", "60 +"]


def hexes(cells):
    polys = [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in cells.index]
    return gpd.GeoDataFrame(cells.reset_index(drop=True), geometry=polys, crs=4326).to_crs(CRS)


def panel(ax, gdf, col, title, boundary, communes, fac, cmap, norm):
    boundary.plot(ax=ax, color="#161b22", edgecolor="none")
    gdf.plot(ax=ax, column=col, cmap=cmap, norm=norm, linewidth=0)
    communes.boundary.plot(ax=ax, color=BG, linewidth=0.4, alpha=0.8)
    boundary.boundary.plot(ax=ax, color=MUTED, linewidth=0.8)
    ax.scatter(fac.x, fac.y, s=9, marker="P", color="white", edgecolor=BG, linewidth=0.4, zorder=5)
    ax.set_title(title, color=FG, fontsize=12, loc="left")
    ax.set_axis_off()
    # 5 km scale bar, bottom left.
    x0, y0 = ax.get_xlim()[0] + 2000, ax.get_ylim()[0] + 1500
    ax.plot([x0, x0 + 5000], [y0, y0], color=FG, linewidth=2.5, solid_capstyle="butt")
    ax.text(x0 + 2500, y0 + 500, "5 km", color=FG, ha="center", fontsize=8)
    ax.annotate("N", xy=(0.95, 0.93), xytext=(0.95, 0.83), xycoords="axes fraction", color=FG, ha="center",
                fontsize=10, arrowprops={"arrowstyle": "-|>", "color": FG})


def main():
    t = time.perf_counter()
    lab = Lab()
    lab.metrics.pop("factual", None)
    lab.engine._cache.clear()
    load_s = time.perf_counter() - t
    t = time.perf_counter()
    m = lab.metrics_for("factual")
    run_s = time.perf_counter() - t
    lab.save_factual()
    print(f"load {load_s:.1f} s, factual metrics {run_s:.1f} s", m.seconds)

    gdf = hexes(lab.cells.join(m.cells))
    boundary = gpd.read_file(DATA / "boundary.gpkg").to_crs(CRS)
    communes = lab.communes.to_crs(CRS)
    f = lab.tree.factual.facilities
    fac = f[f.category == "hospital"]
    cmap = ListedColormap(plt.get_cmap("magma")(np.linspace(0.18, 1, len(LABELS))))
    norm = BoundaryNorm(BINS[:-1] + [1e9], cmap.N)

    fig, axes = plt.subplots(1, 2, figsize=(15, 5.6), facecolor=BG)
    s = m.summary
    for ax, mode, verb in zip(axes, ("drive", "walk"), ("Driving", "Walking")):
        k = f"{mode}.hospital"
        panel(ax, gdf, f"{k}.time_min",
              f"{verb}: median {s[k + '.median_min']} min, "
              f"{100 * s[k + '.pop_within_15'] / s['pop_total']:.0f}% of people within 15 min",
              boundary, communes, fac, cmap, norm)
    handles = [Patch(color=cmap(i), label=lab_) for i, lab_ in enumerate(LABELS)]
    handles.append(plt.Line2D([], [], marker="P", color="white", linestyle="", label=f"hospital ({len(fac)})"))
    fig.legend(handles=handles, loc="lower center", ncol=7, frameon=False, labelcolor=FG,
               title="minutes to the nearest hospital", title_fontsize=9, fontsize=9).get_title().set_color(FG)
    fig.suptitle("Algiers wilaya: travel time to the nearest hospital", color=FG, fontsize=15, x=0.01, y=0.97, ha="left")
    fig.text(0.98, 0.945, "OSM (Geofabrik 2026-10-31), WorldPop 2025 constrained 100 m, H3 res 9. "
             "Free-flow speeds, no traffic.", color=MUTED, fontsize=7, ha="right")
    fig.subplots_adjust(left=0.01, right=0.99, top=0.86, bottom=0.11, wspace=0.03)
    DOCS.mkdir(parents=True, exist_ok=True)
    fig.savefig(DOCS / "factual_hospital.png", dpi=150, facecolor=BG)
    print(json.dumps({k: v for k, v in s.items() if ".hospital." in k or k == "pop_total"}, indent=1))


if __name__ == "__main__":
    main()
