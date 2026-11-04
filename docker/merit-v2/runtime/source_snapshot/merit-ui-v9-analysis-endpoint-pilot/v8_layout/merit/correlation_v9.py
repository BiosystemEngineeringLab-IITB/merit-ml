"""Redundant features (multicollinearity) of one matrix (one analysis from one endpoint), Analytical QC. Scored (Reviewer 1, comment 3).

Samples: the ML-eligible positions after the sample edits in force (Tier 1 sample alignment rows and QC-like, blank-like and
standard-like positions excluded). Features evaluated: those with at least 3 values that are not flagged by Low-information
features (constant or near-zero variance). Values are read with the manuscript's Table 4 readers; missing and invalid cells are
left out, zeros are values.

Correlation: Spearman. Each feature is ranked once over its own values (average ranks for ties); the correlation of two features is
the Pearson correlation of these ranks over the samples where both have a value (at least 10 such samples, else the pair is not
evaluated). Without missing cells this is the usual Spearman correlation.
Redundant: caret findCorrelation, fast version (topepo/caret R/findCorrelation.R; Kuhn 2008, J Stat Softw 28(5), section 3): for
every pair with |rho| above the cut-off, the feature of the pair with the larger mean |rho| to all features (itself included) is
flagged. Score = 100 x (1 - redundant / features evaluated); not assessed with fewer than 10 ML-eligible samples or fewer than 2
features evaluated. Cut-off adjustable (PARAMETERS, 0.80-0.99, default 0.90).

Computation: blocked matrix products (float32 without missing cells, float64 with), so the full correlation matrix is never held:
per feature the sum of |rho| over evaluated pairs, and every pair with |rho| >= STORE_MIN (0.80).
A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
"""
from __future__ import annotations

import warnings
from typing import Any

import numpy as np
from scipy.stats import rankdata

PARAMETERS: dict[str, tuple[float, float, float, float]] = {"redundancy_cutoff": (0.90, 0.80, 0.99, 0.01)}
DEFAULT_PARAMS: dict[str, float] = {k: v[0] for k, v in PARAMETERS.items()}
STORE_MIN = PARAMETERS["redundancy_cutoff"][1]
MIN_PAIR, MIN_SAMPLES, BLOCK_ELEMENTS = 10, 10, 8_000_000   # block of features x features: < 1 GB per process
EXAMPLES = 5        # pairs shown under Examples
TIE = 1e-5        # |rho| within 1e-5 of the cut-off counts as equal to it, not above: rank correlations are often exactly 0.9,
                  # and correlations are computed in single precision without missing values (error about 1e-6)


def cutoff(params: dict | None) -> float:
    return float((params or {}).get("redundancy_cutoff", DEFAULT_PARAMS["redundancy_cutoff"]))


def prepare(sub: np.ndarray) -> dict[str, Any]:
    """Ranks of each feature over its own values (samples x features); standardised ranks without missing cells, else the parts
    needed for correlations over the samples both features have."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        r = rankdata(sub, axis=0, nan_policy="omit") if sub.size else sub.copy()
    mask = ~np.isnan(r)
    if mask.all():
        z = r - r.mean(axis=0)
        sd = np.sqrt((z ** 2).sum(axis=0)); sd[sd == 0] = np.inf
        return {"complete": True, "n": sub.shape[0], "z": np.ascontiguousarray((z / sd).astype(np.float32))}
    x0 = np.where(mask, r, 0.0)
    return {"complete": False, "n": sub.shape[0], "x0": x0, "m": mask.astype(np.float64), "x2": x0 ** 2}


def block_size(f: int) -> int:
    return max(1, min(f, BLOCK_ELEMENTS // max(f, 1)))


def compute_block(prep: dict[str, Any], a: int, b: int, store_min: float = STORE_MIN):
    """Features a..b-1 against all features: (sum |rho|, evaluated pairs, pair rows i, pair columns j, rho), pairs i < j only."""
    if prep["complete"]:
        z = prep["z"]
        c = z[:, a:b].T @ z
        valid = np.full(c.shape, prep["n"] >= MIN_PAIR)
    else:
        x0, m, x2 = prep["x0"], prep["m"], prep["x2"]
        nn = m[:, a:b].T @ m
        sx = x0[:, a:b].T @ m; sy = m[:, a:b].T @ x0
        sxx = x2[:, a:b].T @ m; syy = m[:, a:b].T @ x2; sxy = x0[:, a:b].T @ x0
        with np.errstate(all="ignore"):
            num = nn * sxy - sx * sy
            den = np.sqrt((nn * sxx - sx ** 2) * (nn * syy - sy ** 2))
            c = num / den
        valid = (nn >= MIN_PAIR) & np.isfinite(c) & (den > 0)
        del nn, sx, sy, sxx, syy, sxy, num, den
    c = np.clip(np.where(valid, c, 0.0), -1.0, 1.0)
    rows = np.arange(a, b)
    c[rows - a, rows] = 1.0; valid[rows - a, rows] = True        # each feature with itself, as caret's colMeans of the matrix
    ac = np.abs(c)
    ii, jj = np.nonzero((ac >= store_min) & valid)
    keep = jj > ii + a
    ii, jj = ii[keep], jj[keep]
    return ac.sum(axis=1), valid.sum(axis=1), (ii + a).astype(np.int32), jj.astype(np.int32), c[ii, jj].astype(np.float32)


def spearman_blocks(sub: np.ndarray, store_min: float = STORE_MIN) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """sub: samples x features (NaN = missing). Returns (sum |rho| per feature over evaluated pairs incl. itself, number of evaluated
    pairs per feature incl. itself, pairs (i, j) with i < j and |rho| >= store_min, their rho)."""
    f = sub.shape[1]; prep = prepare(sub)
    sums = np.zeros(f); cnt = np.zeros(f, dtype=np.int64); I, J, V = [], [], []
    bs = block_size(f)
    for a in range(0, f, bs):
        b = min(f, a + bs)
        s_, c_, i_, j_, v_ = compute_block(prep, a, b, store_min)
        sums[a:b] = s_; cnt[a:b] = c_; I.append(i_); J.append(j_); V.append(v_)
    return merge_blocks(sums, cnt, I, J, V)


def merge_blocks(sums, cnt, I, J, V):
    cat = lambda xs, dt: np.concatenate(xs).astype(dt) if xs else np.zeros(0, dt)
    return sums, cnt, np.stack([cat(I, np.int32), cat(J, np.int32)], axis=1), cat(V, np.float32)


def find_correlation_fast(mean_abs: np.ndarray, pairs: np.ndarray, rho: np.ndarray, cut: float) -> np.ndarray:
    """caret findCorrelation_fast: for each pair (i < j) above the cut-off, flag j when its mean |rho| is larger, else i
    (colsToDiscard <- averageCorr[col] > averageCorr[row]; ties flag the first feature of the pair)."""
    sel = np.abs(rho) > cut + TIE
    if not sel.any():
        return np.zeros(0, dtype=np.int64)
    p = pairs[sel]
    rank = np.unique(mean_abs, return_inverse=True)[1]            # as.numeric(as.factor(averageCorr)): ties share a rank
    second_larger = rank[p[:, 1]] > rank[p[:, 0]]
    return np.unique(np.where(second_larger, p[:, 1], p[:, 0]))


def largest_group(pairs: np.ndarray, rho: np.ndarray, cut: float, f: int) -> int:
    """Size of the largest set of features connected by pairs above the cut-off (connected component)."""
    sel = np.abs(rho) > cut + TIE
    if not sel.any():
        return 0
    parent = np.arange(f)
    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i, j in pairs[sel]:
        ri, rj = root(int(i)), root(int(j))
        if ri != rj:
            parent[ri] = rj
    roots = [root(int(x)) for x in np.unique(pairs[sel])]
    return int(np.bincount(roots).max()) if roots else 0


def summarise(n: int, evaluated: int, sums: np.ndarray, cnt: np.ndarray, pairs: np.ndarray, rho: np.ndarray, cut: float,
              names: list[str] | None = None, all_pairs: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"ml_eligible_samples": n, "features_evaluated": evaluated, "cutoff": cut}
    if n < MIN_SAMPLES or evaluated < 2:
        out.update({"score": None, "not_assessed": "fewer than 10 ML-eligible samples" if n < MIN_SAMPLES else "fewer than 2 features to compare",
                    "redundant": 0, "pairs_above": 0, "largest_group": 0})
        return out
    mean_abs = np.where(cnt > 0, sums / np.maximum(cnt, 1), 0.0)
    red = find_correlation_fast(mean_abs, pairs, rho, cut)
    out.update({"redundant": int(red.size), "pairs_above": int((np.abs(rho) > cut + TIE).sum()),
                "largest_group": largest_group(pairs, rho, cut, evaluated), "score": 100.0 * (1 - red.size / evaluated), "not_assessed": None})
    if names is not None:
        sel = np.abs(rho) > cut + TIE
        top = np.argsort(-np.abs(rho[sel]), kind="stable")[:EXAMPLES]
        ps, vs = pairs[sel], rho[sel]
        out["top_pairs"] = [{"feature_a": names[int(ps[t, 0])], "feature_b": names[int(ps[t, 1])], "rho": round(float(vs[t]), 4)} for t in top]
        best = {}
        for (i, j), v in zip(pairs[sel], rho[sel]):
            for a_, b_ in ((int(i), int(j)), (int(j), int(i))):
                if a_ not in best or abs(v) > abs(best[a_][1]):
                    best[a_] = (b_, float(v))
        out["redundant_features"] = [{"feature": names[k], "strongest_partner": names[best[k][0]], "rho": round(best[k][1], 4)}
                                     for k in red.tolist() if k in best]
        if all_pairs:                                         # every pair above the cut-off (download only; built on request)
            out["pairs_above_cutoff"] = [{"feature_a": names[int(i)], "feature_b": names[int(j)], "rho": round(float(v), 4)}
                                         for (i, j), v in zip(ps[np.argsort(-np.abs(vs), kind="stable")], vs[np.argsort(-np.abs(vs), kind="stable")])]
    return out


def compute_from_values(sub: np.ndarray, names: list[str], flagged: set[int], params: dict | None = None, all_pairs: bool = False) -> dict[str, Any]:
    """Score from the values of the ML-eligible samples (rows) of every feature (columns); flagged = Low-information features."""
    n = sub.shape[0]
    have = (~np.isnan(sub)).sum(axis=0) >= 3 if sub.size else np.zeros(sub.shape[1], dtype=bool)
    ev = np.array([j for j in range(sub.shape[1]) if have[j] and j not in flagged], dtype=np.int64)
    cut = cutoff(params)
    if n < MIN_SAMPLES or ev.size < 2:
        return summarise(n, int(ev.size), np.zeros(0), np.zeros(0), np.zeros((0, 2), np.int32), np.zeros(0, np.float32), cut)
    sums, cnt, pairs, rho = spearman_blocks(sub[:, ev])
    return summarise(n, int(ev.size), sums, cnt, pairs, rho, cut, [names[j] for j in ev], all_pairs)
