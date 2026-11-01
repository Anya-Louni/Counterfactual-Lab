"""Population-weighted inequality measures."""

import numpy as np


def weighted_quantile(x, w, q: float) -> float:
    o = np.argsort(x)
    x, w = np.asarray(x)[o], np.asarray(w)[o]
    cw = np.cumsum(w)
    return float(x[np.searchsorted(cw, q * cw[-1])])


def weighted_gini(x, w) -> float:
    """Gini from the Lorenz curve of population-weighted values. 0 = everyone equal."""
    o = np.argsort(x)
    x, w = np.asarray(x, float)[o], np.asarray(w, float)[o]
    share_w = w / w.sum()
    lor = np.cumsum(w * x) / np.sum(w * x)
    prev = np.r_[0.0, lor[:-1]]
    return float(1 - np.sum(share_w * (prev + lor)))
