"""Nominatim search limited to the Algiers wilaya. One request per second at most, results cached on disk."""

import json
import threading
import time

import requests

from backend.network.model import DATA

from .local import Place

URL = "https://nominatim.openstreetmap.org/search"
UA = "counterfactual-lab/0.1 (github.com/Anya-Louni)"
VIEWBOX = "2.79,36.83,3.39,36.57"  # west, north, east, south of the wilaya
CACHE = DATA / "geocode_cache.json"
_lock = threading.Lock()
_last = [0.0]


def _load() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def geocode(query: str, k: int = 3) -> list[Place]:
    key = query.strip().lower()
    with _lock:
        cache = _load()
        if key not in cache:
            wait = 1.1 - (time.monotonic() - _last[0])
            if wait > 0:
                time.sleep(wait)
            r = requests.get(URL, params={"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "dz",
                                          "viewbox": VIEWBOX, "bounded": 1},
                             headers={"User-Agent": UA}, timeout=20)
            _last[0] = time.monotonic()
            r.raise_for_status()
            cache[key] = [{"name": x["display_name"], "lon": float(x["lon"]), "lat": float(x["lat"])}
                          for x in r.json()]
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return [Place("geocoder", x["name"][:120], x["lon"], x["lat"]) for x in cache[key][:k]]
