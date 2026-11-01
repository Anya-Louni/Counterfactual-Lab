"""A tiny city where every travel time can be checked by hand.

Nodes on a line, 1 km apart, x in metres (UTM), y = 0:

    A(0) --- B(1000) --- C(2000) --- D(3000)        E(5000) isolated until a road is added

Drive edges both ways at 36 km/h = 10 m/s, so each link is 100 s.
Walk edges both ways at 4.5 km/h = 1.25 m/s, so each link is 800 s.
Cells sit exactly on nodes (zero access time). One hospital sits on A.
"""

import pandas as pd

from backend.network.edits import TO_UTM
from backend.network.model import Network

ORIGIN = TO_UTM.transform(3.05, 36.75)  # somewhere in Algiers, so lon/lat edits work
X = {"A": 0, "B": 1000, "C": 2000, "D": 3000, "E": 5000}
IDS = {k: i + 1 for i, k in enumerate(X)}


def xy(name):
    return ORIGIN[0] + X[name], ORIGIN[1]


def lonlat(name):
    from pyproj import Transformer
    return Transformer.from_crs(32631, 4326, always_xy=True).transform(*xy(name))


def network(mode: str) -> Network:
    speed = 10.0 if mode == "drive" else 1.25
    nodes = pd.DataFrame({"x": [xy(k)[0] for k in X], "y": [xy(k)[1] for k in X],
                          "lon": [lonlat(k)[0] for k in X], "lat": [lonlat(k)[1] for k in X]},
                         index=[IDS[k] for k in X])
    rows = []
    for way, (a, b) in enumerate([("A", "B"), ("B", "C"), ("C", "D")], start=100):
        for u, v in ((a, b), (b, a)):
            rows.append({"id": f"{IDS[u]}-{IDS[v]}-0", "u": IDS[u], "v": IDS[v], "length": 1000.0,
                         "speed_kph": speed * 3.6, "travel_time": 1000 / speed,
                         "name": f"road {a}{b}", "highway": "primary", "osmid": (way,)})
    edges = pd.DataFrame(rows).set_index("id")
    return Network(mode, nodes, edges)


def cells() -> pd.DataFrame:
    return pd.DataFrame({"pop": [100.0, 200.0, 300.0, 400.0, 500.0],
                         "x": [xy(k)[0] for k in X], "y": [xy(k)[1] for k in X],
                         "lon": [lonlat(k)[0] for k in X], "lat": [lonlat(k)[1] for k in X]},
                        index=pd.Index(list(X), name="h3"))


def facilities() -> pd.DataFrame:
    x, y = xy("A")
    lon, lat = lonlat("A")
    return pd.DataFrame({"category": ["hospital"], "name": ["H-A"], "x": [x], "y": [y], "lon": [lon],
                         "lat": [lat], "capacity": [1.0], "merged": [1]}, index=["h1"])
