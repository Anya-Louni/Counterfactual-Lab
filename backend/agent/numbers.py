"""Every number in the narrative must be a (possibly rounded) number from the computed facts."""

import re

NUM = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")


def collect(obj, out: set[float] | None = None) -> set[float]:
    out = set() if out is None else out
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(abs(float(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            collect(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect(v, out)
    elif isinstance(obj, str):  # numbers inside names, e.g. "Bab Ezzouar 2"
        for m in NUM.finditer(obj):
            out.add(abs(float(m.group().replace(",", ""))))
    return out


def unsupported(text: str, facts: dict) -> list[str]:
    allowed = collect(facts)
    bad = []
    for m in NUM.finditer(text):
        raw = m.group()
        val = abs(float(raw.replace(",", "")))
        dec = len(raw.split(".")[1]) if "." in raw else 0
        if not any(round(a, dec) == val for a in allowed):
            bad.append(raw)
    return bad
