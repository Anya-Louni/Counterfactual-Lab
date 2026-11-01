"""Download and cache every input for the Algiers study area.

OSM data comes from a pinned Geofabrik Algeria extract parsed locally, not the
public Overpass API, so a run is reproducible and does not depend on Overpass
load. Graphs are built with OSMnx's own filters and build steps.

Writes to data/: boundary, communes, drive and walk networks (GeoParquet),
facilities, WorldPop raster clipped to the study area, H3 population grids,
and fetch_report.json with counts and timings.
"""

import json
import pickle
import re
import time
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import osmium
import osmnx as ox
import pandas as pd
import rasterio
import requests
import shapely
from osmnx import _overpass, graph, simplification, stats, truncate, utils_geo
from rasterio.mask import mask

DATA = Path(__file__).resolve().parents[1] / "data"
# Wilaya d'Alger. Pinned by id: a name search for "Algiers Province" returns the wilaya office building.
BOUNDARY_OSM_ID = "R157062"
CRS = 32631  # WGS 84 / UTM 31N, covers Algiers (3 deg E)
WALK_KMH = 4.5
PBF_URL = "https://download.geofabrik.de/africa/algeria-latest.osm.pbf"
WORLDPOP_URL = (
    "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/DZA/v1/"
    "100m/constrained/dza_pop_2025_CN_100m_R2025A_v1.tif"
)
H3_RESOLUTIONS = (8, 9)
FACILITY_TAGS = {
    "amenity": {"hospital", "clinic", "doctors", "pharmacy", "school"},
    "healthcare": {"hospital", "clinic"},
}
UA = {"User-Agent": "counterfactual-lab/0.1 (github.com/Anya-Louni)"}

ox.settings.use_cache = True
ox.settings.cache_folder = str(DATA / "osm_cache")
ox.settings.http_user_agent = UA["User-Agent"]

report: dict = {}


def timed(key):
    def wrap(fn):
        def run(*a, **k):
            t = time.perf_counter()
            out = fn(*a, **k)
            report.setdefault("seconds", {})[key] = round(time.perf_counter() - t, 1)
            print(f"{key}: {report['seconds'][key]} s", flush=True)
            return out
        return run
    return wrap


def download(url, dest_dir, name=None):
    with requests.get(url, stream=True, timeout=120, headers=UA) as r:
        r.raise_for_status()
        dest = dest_dir / (name or r.url.rsplit("/", 1)[-1])  # Geofabrik redirects to a dated file
        if not dest.exists():
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
    return dest


@timed("boundary")
def boundary():
    b = ox.geocode_to_gdf(BOUNDARY_OSM_ID, by_osmid=True)[["geometry", "display_name", "osm_id"]]
    report["study_area_km2"] = round(b.to_crs(CRS).area.iloc[0] / 1e6, 1)
    if report["study_area_km2"] < 500:
        raise RuntimeError(f"boundary is {report['study_area_km2']} km2, expected the whole wilaya")
    b.to_file(DATA / "boundary.gpkg")
    return b.geometry.iloc[0]


def facility_type(tags):
    a, h = tags.get("amenity"), tags.get("healthcare")
    if a == "hospital" or h == "hospital":
        return "hospital"
    if a in ("clinic", "doctors") or h == "clinic":
        return "clinic"
    return a if a in ("pharmacy", "school") else None


@timed("parse_pbf")
def parse_pbf(pbf, poly):
    """One pass over the extract: highway ways, facilities, commune polygons."""
    minx, miny, maxx, maxy = utils_geo.buffer_geometry(poly, 1000).bounds

    def inside(loc):
        return loc.valid() and minx <= loc.lon <= maxx and miny <= loc.lat <= maxy

    wkb = osmium.geom.WKBFactory()
    nodes, ways, facs, communes = {}, [], [], []
    fp = (osmium.FileProcessor(str(pbf)).with_locations().with_areas()
          .with_filter(osmium.filter.KeyFilter("highway", "amenity", "healthcare", "boundary")))
    for o in fp:
        tags = dict(o.tags)
        if o.is_node():
            if facility_type(tags) and inside(o.location):
                facs.append(("node", o.id, tags, shapely.Point(o.location.lon, o.location.lat)))
        elif o.is_way():
            if "highway" in tags and any(inside(n.location) for n in o.nodes):
                if not all(n.location.valid() for n in o.nodes):
                    continue
                for n in o.nodes:
                    nodes[n.ref] = (n.location.lat, n.location.lon)
                ways.append({"type": "way", "id": o.id, "nodes": [n.ref for n in o.nodes], "tags": tags})
        elif o.is_area():
            kind = facility_type(tags)
            is_commune = tags.get("boundary") == "administrative" and tags.get("admin_level") == "8"
            if not (kind or is_commune):
                continue
            try:
                geom = shapely.from_wkb(wkb.create_multipolygon(o))
            except RuntimeError:  # broken multipolygon in OSM
                continue
            if not geom.intersects(shapely.box(minx, miny, maxx, maxy)):
                continue
            elem = "way" if o.from_way() else "relation"
            if kind:
                facs.append((elem, o.orig_id(), tags, geom))
            if is_commune and not o.from_way():
                communes.append({"id": o.orig_id(), **{k: tags.get(k) for k in ("name", "name:fr", "name:ar")},
                                 "geometry": geom})
    if not ways or not nodes:
        raise RuntimeError("no highway data parsed; check the extract")
    return nodes, ways, facs, communes


def way_matches(tags, flt):
    """Evaluate an Overpass tag filter string like OSMnx's network filters."""
    for key, op, rx in re.findall(r'\["([^"]+)"(?:(!~|~)"([^"]*)")?\]', flt):
        val = tags.get(key)
        if not op and val is None:
            return False
        if op == "!~" and val is not None and re.search(rx, val):
            return False
        if op == "~" and (val is None or not re.search(rx, val)):
            return False
    return True


def save_communes(communes, poly):
    c = gpd.GeoDataFrame(communes, crs=4326).to_crs(CRS)
    share = c.intersection(gpd.GeoSeries([poly], crs=4326).to_crs(CRS).iloc[0]).area / c.area
    c = c[share > 0.5].to_crs(4326)
    c.to_file(DATA / "communes.gpkg")
    report["communes"] = len(c)


def save_facilities(facs, poly):
    rows = []
    for elem, oid, tags, geom in facs:
        rows.append({"element": elem, "id": oid, "category": facility_type(tags),
                     **{k: tags.get(k) for k in ("name", "name:fr", "name:ar", "beds", "capacity")},
                     "geometry": geom})
    f = gpd.GeoDataFrame(rows, crs=4326).drop_duplicates(subset=["element", "id"])
    # Polygons become their centroid; computed in metres then back to WGS 84.
    f["geometry"] = f.to_crs(CRS).geometry.centroid.to_crs(4326)
    f = f[f.within(poly)]  # the network stops at the boundary
    f.to_file(DATA / "facilities.gpkg")
    report["facilities"] = {
        t: {"count": int((f.category == t).sum()), "named": int(((f.category == t) & f["name"].notna()).sum())}
        for t in ("hospital", "clinic", "pharmacy", "school")
    }
    report["facilities"]["hospital"]["with_beds_tag"] = int(((f.category == "hospital") & f["beds"].notna()).sum())


def network(nodes, ways, poly, kind):
    """Same steps as ox.graph_from_polygon, fed from the local extract."""
    t = time.perf_counter()
    flt = _overpass._get_network_filter(kind)
    keep = [w for w in ways if way_matches(w["tags"], flt)]
    used = {n for w in keep for n in w["nodes"]}
    elements = [{"type": "node", "id": n, "lat": nodes[n][0], "lon": nodes[n][1], "tags": {}} for n in used] + keep
    poly_buff = utils_geo.buffer_geometry(poly, 500)
    g_buff = graph._create_graph([{"elements": elements}], kind in ox.settings.bidirectional_network_types)
    g_buff = truncate.truncate_graph_polygon(g_buff, poly_buff)
    g_buff = truncate.largest_component(g_buff, strongly=False)
    raw = (g_buff.number_of_nodes(), g_buff.number_of_edges())
    t_build = time.perf_counter() - t
    t = time.perf_counter()
    g_buff = simplification.simplify_graph(g_buff)
    g = truncate.truncate_graph_polygon(g_buff, poly)
    # Strongly connected for driving so every cell can reach and be reached.
    g = truncate.largest_component(g, strongly=kind == "drive")
    spn = stats.count_streets_per_node(g_buff, nodes=g.nodes)
    for n, c in spn.items():
        g.nodes[n]["street_count"] = c
    t_simp = time.perf_counter() - t
    if kind == "drive":
        g = ox.add_edge_speeds(g)
        g = ox.add_edge_travel_times(g)
    else:
        for _, _, d in g.edges(data=True):
            d["speed_kph"] = WALK_KMH
            d["travel_time"] = d["length"] / (WALK_KMH / 3.6)
    gn, ge = ox.graph_to_gdfs(g)
    for df in (gn, ge):
        for col in df.columns:  # list-valued OSM tags break parquet
            if df[col].dtype == object and col != "geometry":
                df[col] = df[col].astype(str)
    gn.to_parquet(DATA / f"{kind}_nodes.parquet")
    ge.to_parquet(DATA / f"{kind}_edges.parquet")
    report[kind] = {
        "unsimplified_nodes": raw[0], "unsimplified_edges": raw[1],
        "nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
        "build_s": round(t_build, 1), "simplify_s": round(t_simp, 1),
    }
    print(kind, report[kind], flush=True)


@timed("worldpop")
def population(poly):
    raw = download(WORLDPOP_URL, DATA, "worldpop_dza_2025.tif")
    with rasterio.open(raw) as src:
        arr, tf = mask(src, [poly], crop=True, nodata=0)
        meta = src.meta | {"height": arr.shape[1], "width": arr.shape[2], "transform": tf, "nodata": 0}
    with rasterio.open(DATA / "worldpop_algiers.tif", "w", **meta) as dst:
        dst.write(arr)
    pop = np.where(arr[0] > 0, arr[0], 0).astype("float64")
    rows, cols = np.nonzero(pop)
    xs, ys = rasterio.transform.xy(tf, rows, cols)  # pixel centres, WGS 84
    vals = pop[rows, cols]
    report["population"] = {"total": round(float(vals.sum())), "pixels": len(vals)}
    for res in H3_RESOLUTIONS:
        cells = [h3.latlng_to_cell(y, x, res) for x, y in zip(xs, ys)]
        df = pd.DataFrame({"h3": cells, "pop": vals}).groupby("h3", as_index=False)["pop"].sum()
        lat, lng = zip(*(h3.cell_to_latlng(c) for c in df.h3))
        gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(lng, lat), crs=4326)
        gdf.to_parquet(DATA / f"h3_pop_r{res}.parquet")
        report["population"][f"h3_r{res}_cells"] = len(gdf)
        report["population"][f"h3_r{res}_edge_m"] = round(h3.average_hexagon_edge_length(res, "m"))


def main():
    DATA.mkdir(exist_ok=True)
    poly = boundary()
    t = time.perf_counter()
    pbf = download(PBF_URL, DATA)
    report["osm_extract"] = pbf.name
    report.setdefault("seconds", {})["download_pbf"] = round(time.perf_counter() - t, 1)
    cache = DATA / f"{pbf.stem}.parsed.pkl"
    if not cache.exists():
        cache.write_bytes(pickle.dumps(parse_pbf(pbf, poly)))
    nodes, ways, facs, communes = pickle.loads(cache.read_bytes())
    save_communes(communes, poly)
    save_facilities(facs, poly)
    population(poly)
    for kind in ("drive", "walk"):
        network(nodes, ways, poly, kind)
    (DATA / "fetch_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
