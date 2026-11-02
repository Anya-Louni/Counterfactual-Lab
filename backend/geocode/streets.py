"""Resolve a street name to every road segment that carries it.

OSM spells one street many ways: "Rue Hassiba Ben Bouali", "Rue Hassiba Benbouali",
"شارع حسيبة بن بوعلي", or both scripts in one tag. Each spelling is reduced to a key:
accents and case removed, the street-type word ("Rue", "Boulevard", "شارع", ...) dropped,
spaces removed. A French key and an Arabic key are treated as the same street when OSM
pairs them in one name tag with nothing else, so both scripts resolve to the same segments.
A segment matches when any of its names has a matching key. Simplified segments that merge
several streets match each of them; closing one closes the whole merged segment.
"""

import re
import unicodedata
from collections import defaultdict

import pandas as pd

LATIN_TYPES = {"rue", "boulevard", "bd", "bld", "avenue", "av", "chemin", "route", "place", "impasse", "allee",
               "cite", "rocade", "voie", "street", "road", "de", "du", "des", "la", "le", "l", "d"}
ARABIC_TYPES = {"شارع", "نهج", "طريق", "ساحة", "زنقة", "ممر"}
ARABIC = re.compile(r"[؀-ۿ]")
MIN_KEY = 6  # shorter keys ("rue1") are too ambiguous to find inside a sentence


def variants(raw: str) -> list[str]:
    """Split an OSM name value (maybe a list or ';'-separated) into single names."""
    s = str(raw).strip()
    if s.startswith("["):
        s = s.strip("[]")
        parts = re.findall(r"'([^']*)'|\"([^\"]*)\"", s)
        names = [a or b for a, b in parts]
    else:
        names = [s]
    return [p.strip() for n in names for p in n.split(";") if p.strip()]


def keys_of(name: str) -> tuple[set[str], set[str]]:
    """Latin and Arabic keys found in one name (a name can carry both scripts)."""
    latin_part = "".join(ch if not ARABIC.match(ch) else " " for ch in name)
    arabic_part = "".join(ch if ARABIC.match(ch) or ch == " " else " " for ch in name)
    lat = unicodedata.normalize("NFKD", latin_part).encode("ascii", "ignore").decode().lower()
    lat_words = [w for w in re.split(r"[^a-z0-9]+", lat) if w]
    while lat_words and lat_words[0] in LATIN_TYPES:
        lat_words.pop(0)
    ar = re.sub(r"[ً-ْـ]", "", arabic_part)  # diacritics and tatweel
    ar_words = [w for w in ar.split() if w]
    while ar_words and ar_words[0] in ARABIC_TYPES:
        ar_words.pop(0)
    lk = "".join(lat_words)
    ak = "".join(ar_words)
    return ({lk} if len(lk) >= MIN_KEY else set()), ({ak} if len(ak) >= 3 else set())


def query_key(text: str) -> str:
    lat, ar = keys_of(text)
    return next(iter(lat or ar), "")


class StreetIndex:
    def __init__(self, edge_tables: list[pd.DataFrame]):
        self.cat_keys: dict[str, set[str]] = {}
        self.translations: dict[str, set[str]] = defaultdict(set)
        self.display: dict[str, str] = {}
        self.edges_by_cat: dict[str, list[pd.DataFrame]] = defaultdict(list)
        for e in edge_tables:
            named = e.dropna(subset=["name"])
            for cat, g in named.groupby("name", observed=True):
                self.edges_by_cat[cat].append(g)
        for cat in self.edges_by_cat:
            lat_all, ar_all = set(), set()
            for v in variants(cat):
                lat, ar = keys_of(v)
                lat_all |= lat
                ar_all |= ar
                for k in lat | ar:
                    self.display.setdefault(k, v)
            self.cat_keys[cat] = lat_all | ar_all
            if len(lat_all) == 1 and len(ar_all) == 1:  # one street, two scripts
                (a,), (b,) = lat_all, ar_all
                self.translations[a].add(b)
                self.translations[b].add(a)

    def resolve(self, name: str) -> dict:
        """Way ids and matched spellings for a street name. Empty when nothing matches."""
        q = query_key(name)
        if not q:
            return {"way_ids": [], "names": [], "segments": 0}
        keys = {q} | self.translations.get(q, set())
        ways, names, segs = set(), set(), 0
        for cat, k in self.cat_keys.items():
            if k & keys:
                names.add(cat)
                for g in self.edges_by_cat[cat]:
                    segs += len(g)
                    ways.update(w for ids in g["osmid"] for w in ids)
        return {"way_ids": sorted(ways), "names": sorted(names), "segments": segs}

    def mentioned(self, text: str) -> list[str]:
        """Streets whose name appears in free text, e.g. the user's question."""
        flat_lat = "".join(w for w in re.split(r"[^a-z0-9]+", unicodedata.normalize("NFKD", text)
                                               .encode("ascii", "ignore").decode().lower()) if w)
        flat_ar = re.sub(r"\s+", "", re.sub(r"[ً-ْـ]", "", text))
        hits = []
        for k, shown in self.display.items():
            hay = flat_ar if ARABIC.match(k[0]) else flat_lat
            if len(k) >= (4 if ARABIC.match(k[0]) else 8) and k in hay:
                hits.append(shown)
        return sorted(set(hits), key=len, reverse=True)
