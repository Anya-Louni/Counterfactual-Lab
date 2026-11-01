# Data

Nothing here is committed except this file. Run `uv run python scripts/fetch_data.py` to rebuild it.

| File | Source | Notes |
|---|---|---|
| `boundary.gpkg` | OSM via Nominatim, "Algiers Province, Algeria" | Study area |
| `communes.gpkg` | OSM `boundary=administrative`, `admin_level=8` | Communes more than half inside the study area |
| `drive_*.parquet`, `walk_*.parquet` | OSM via OSMnx | Simplified, largest component. Drive speeds imputed by OSMnx from `maxspeed` and road class. Walk speed fixed at 4.5 km/h |
| `facilities.gpkg` | OSM `amenity` and `healthcare` tags | Polygons reduced to centroids |
| `worldpop_*.tif` | WorldPop R2025A, 2025, 100 m constrained, Algeria | Clipped to the study area |
| `h3_pop_r8.parquet`, `h3_pop_r9.parquet` | WorldPop summed to H3 cells | Point geometry is the cell centre |
| `fetch_report.json` | | Counts and timings of the last fetch |

Metric work uses EPSG:32631 (WGS 84 / UTM 31N).

Licences: OSM data is ODbL. WorldPop data is CC BY 4.0.
