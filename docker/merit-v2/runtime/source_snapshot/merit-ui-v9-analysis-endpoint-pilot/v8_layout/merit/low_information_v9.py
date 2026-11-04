"""Low-information features of one matrix (one analysis from one endpoint), Analytical QC. Scored (Reviewer 1, comment 3).

Per feature, on the ML-eligible samples after the sample edits in force (values read with the manuscript's Table 4 readers;
missing and invalid cells left out; zeros are values on every endpoint):
  not judged       fewer than 3 values (left to sample-level missingness)
  constant         one distinct value                                                  (caret zeroVar)
  near-zero var.   freqRatio > R and percentUnique <= U                                (caret nearZeroVar; defaults R = 95/5 = 19,
                   freqRatio = count of the most common value / count of the second     U = 10; R/nearZeroVar.R, topepo/caret
                   percentUnique = 100 x distinct values / all ML-eligible samples      commit cf7bf44)
Score = 100 x (1 - (constant + near-zero variance) / features judged); not assessed with fewer than 5 ML-eligible samples or
no feature judged. R and U are adjustable (PARAMETERS) within 5-99 and 1-25.

Stored per matrix (scripts/add_low_information_from_deposit.py): every feature of a matrix of up to 1,000,000 cells (all but 289
matrices); in a larger matrix, every feature that is flagged under the loosest adjustable values (R > 5, U <= 25) on the ML-eligible
samples or on all measured positions, as one code per position (0 = its most common
value over the measured positions, 1 = the next, ...; 254 = a rarer value, 255 = missing), with the values of codes 0-7; for every
other feature with a missing value, one presence bit per position; and the number of other features without a missing value.
The page recomputes from these: a change of R or U is exact everywhere, and sample edits are exact for every matrix up to 1,000,000
cells; in a larger matrix a feature outside the stored set is taken as not flagged after sample edits (it is not flagged under any
adjustable value on the deposited ML-eligible samples), while the judged count stays exact.

Caution, not scored: relative spread. For a matrix whose declared units are linear (concentration or amount; intensity, area or
counts; the family of the measurement-scale metric) and that has no negative value, features that are not already flagged and
whose CV (SD / mean, ddof 1, mean > 0, >= 3 values) is below C% (default 1%, adjustable 0.1-5) are counted and named: often
internal standards or fixed or rounded values, sometimes tightly regulated metabolites. Not checked for log-scaled, normalised,
other or undeclared units, or when a value is negative (there a CV does not measure relative spread). Stored: the values of every
feature (matrices up to 1,000,000 cells; sample edits exact) or of every feature with a CV below 5% on the ML-eligible samples or on
all measured positions (larger matrices).
A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
"""
from __future__ import annotations

import collections
import math
import warnings
from typing import Any

import numpy as np

from merit import structural_v9 as sv

PARAMETERS: dict[str, tuple[float, float, float, float]] = {
    "low_information_freq_ratio": (19.0, 5.0, 99.0, 1.0),
    "low_information_unique_pct": (10.0, 1.0, 25.0, 1.0),
    "low_information_cv_pct": (1.0, 0.1, 5.0, 0.1),
}
DEFAULT_PARAMS: dict[str, float] = {k: v[0] for k, v in PARAMETERS.items()}
LOOSEST = (PARAMETERS["low_information_freq_ratio"][1], PARAMETERS["low_information_unique_pct"][2])
MIN_VALUES, MIN_SAMPLES, TOP_CODES, OTHER, MISSING = 3, 5, 8, 254, 255
CV_MAX = PARAMETERS["low_information_cv_pct"][2]        # features with a CV below this are stored with their values
LINEAR_FAMILIES = ("Concentration or amount", "Intensity, area or counts")
STORE_ALL_CELLS = 1_000_000          # matrices up to this many cells (positions x features) store every feature


def _params(params: dict | None) -> tuple[float, float]:
    p = params or {}
    return (float(p.get("low_information_freq_ratio", DEFAULT_PARAMS["low_information_freq_ratio"])),
            float(p.get("low_information_unique_pct", DEFAULT_PARAMS["low_information_unique_pct"])))


def cv_pct(params: dict | None) -> float:
    return float((params or {}).get("low_information_cv_pct", DEFAULT_PARAMS["low_information_cv_pct"]))


def cv(sub: np.ndarray) -> np.ndarray:
    """CV in % per column (rows = samples, NaN = missing): SD (ddof 1) / mean; NaN with fewer than 3 values or a mean <= 0."""
    ok = ~np.isnan(sub); nobs = ok.sum(axis=0)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)       # all-missing columns: NaN, handled below
        mean = np.nanmean(sub, axis=0); sd = np.nanstd(sub, axis=0, ddof=1)
        out = 100.0 * sd / mean
    return np.where((nobs >= MIN_VALUES) & (mean > 0), out, np.nan)


def flags(sub: np.ndarray, ratio_cut: float, unique_pct: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Direct rule on values (rows = samples, NaN = missing): (judged, constant, near-zero variance) per column."""
    n, f = sub.shape
    ok = ~np.isnan(sub); nobs = ok.sum(axis=0)
    s = np.sort(sub, axis=0)
    change = (s[1:] != s[:-1]) & ~np.isnan(s[1:]) & ~np.isnan(s[:-1])
    distinct = np.where(nobs > 0, 1 + change.sum(axis=0), 0)
    judged = nobs >= MIN_VALUES
    constant = judged & (distinct == 1)
    nzv = np.zeros(f, dtype=bool)
    for j in np.where(judged & (distinct >= 2) & (100.0 * distinct / max(n, 1) <= unique_pct))[0]:
        _, c = np.unique(sub[ok[:, j], j], return_counts=True)
        c = np.sort(c)[::-1]
        nzv[j] = c[0] / c[1] > ratio_cut
    return judged, constant, nzv


def encode(column: np.ndarray, measured: np.ndarray) -> tuple[np.ndarray, list[float]]:
    """Codes per position for one feature: values ranked by their count over the measured positions (ties by value)."""
    codes = np.full(column.shape[0], MISSING, dtype=np.uint8)
    have = ~np.isnan(column)
    if not have.any():
        return codes, []
    u, inv, c = np.unique(column[have], return_inverse=True, return_counts=True)   # Tier 1 alignment rows hold no value
    order = np.lexsort((u, -c))                                                     # most common first, ties by value
    rank = np.empty(len(u), dtype=np.int64); rank[order] = np.arange(len(u))
    codes[have] = np.minimum(rank[inv], OTHER).astype(np.uint8)
    return codes, [float(u[i]) for i in order[:TOP_CODES]]


def _value_text(v: float) -> str:
    return str(int(v)) if float(v).is_integer() and abs(v) < 1e15 else f"{v:.6g}"


def _code_counts(c: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Per row of codes (features x samples): number of values, distinct values, the two largest value counts, and the code of the
    most common value. Every code counts (a rarer value, code 254, counts as a value of its own); 255 = missing."""
    k, n = c.shape
    s = np.sort(c, axis=1)
    valid = s != MISSING
    new = np.ones_like(valid); new[:, 1:] = (s[:, 1:] != s[:, :-1]) | (s[:, 1:] == OTHER)
    run = np.cumsum(new, axis=1) - 1
    cnt = np.bincount((np.arange(k)[:, None] * n + run)[valid], minlength=k * n).reshape(k, n)
    order = np.sort(cnt, axis=1)[:, ::-1]
    if order.shape[1] < 2:                                  # one sample: no second value
        order = np.concatenate([order, np.zeros((k, 2 - order.shape[1]), dtype=order.dtype)], axis=1)
    top_run = np.argmax(cnt, axis=1)
    first = np.argmax(run == top_run[:, None], axis=1)
    return valid.sum(axis=1), (cnt > 0).sum(axis=1), order[:, :2], s[np.arange(k), first]


def compute(stored: dict, data: dict, names: list[str], roster: list[list],
            overrides: dict | None = None, params: dict | None = None) -> dict[str, Any]:
    """Score of one matrix for the parameters and sample edits in force, from its stored data: codes of the features that can be
    flagged, presence bits of the other features with a missing value, and the number of other features without any."""
    ratio_cut, unique_pct = _params(params)
    keep = np.array([p["eligible"] and not p["excluded"] and not p["row"][sv.I_ALIGN] for p in sv.effective_positions(roster, overrides)],
                    dtype=bool)
    n = int(keep.sum())
    out: dict[str, Any] = {"ml_eligible_samples": n, "features": stored["features"], "freq_ratio": ratio_cut, "unique_pct": unique_pct}
    codes, tops = data["codes"], data["top_values"]
    k = codes.shape[0] if codes.ndim == 2 else 0
    if k and n:
        nobs, distinct, top2, dom = _code_counts(codes[:, keep])
        judged_c = nobs >= MIN_VALUES
        constant = judged_c & (distinct == 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(top2[:, 1] > 0, top2[:, 0] / np.maximum(top2[:, 1], 1), 0.0)
        nzv = judged_c & (distinct >= 2) & (ratio > ratio_cut) & (100.0 * distinct / n <= unique_pct)
    else:                                                     # no stored feature, or no ML-eligible sample
        judged_c = constant = nzv = np.zeros(k, dtype=bool); dom = np.zeros(k, dtype=np.uint8)
        nobs = distinct = np.zeros(k, dtype=np.int64); top2 = np.zeros((k, 2), dtype=np.int64); ratio = np.zeros(k)
    bits = data.get("present_bits")
    judged_m = 0
    if bits is not None and bits.size and n:
        present = np.unpackbits(bits, axis=1, count=len(roster)).astype(bool)[:, keep]
        judged_m = int((present.sum(axis=1) >= MIN_VALUES).sum())
    judged = int(judged_c.sum()) + judged_m + (int(stored["complete_outside"]) if n >= MIN_VALUES else 0)
    flagged = constant | nzv
    out.update({"judged": judged, "not_judged": stored["features"] - judged, "constant": int(constant.sum()),
                "near_zero_variance": int(nzv.sum()), "flagged": int(flagged.sum())})
    out["score"] = (100.0 * (1 - flagged.sum() / judged)) if judged and n >= MIN_SAMPLES else None
    out["not_assessed"] = (None if out["score"] is not None else
                           ("fewer than 5 ML-eligible samples" if n < MIN_SAMPLES else "no feature with 3 or more values"))
    value = lambda i: _value_text(float(tops[i, dom[i]])) if dom[i] < TOP_CODES and not np.isnan(tops[i, dom[i]]) else "another value"
    groups = collections.Counter(value(i) for i in np.where(flagged)[0])
    out["repeated_values"] = [{"value": v, "features": c_} for v, c_ in groups.most_common()]
    idx = data["index"] if "index" in data else np.arange(k)
    out["flagged_index"] = [int(idx[i]) for i in np.where(flagged)[0]]       # original feature positions (Redundant features excludes them)
    def reason(i):
        v = value(i)
        if constant[i]:
            return f"constant: “{v}” in all {int(nobs[i]):,} samples with a value"
        return (f"near-zero variance: “{v}” in {int(top2[i, 0]):,} of {int(nobs[i]):,} samples with a value, {ratio[i]:.1f} times the next "
                f"value; {int(distinct[i]):,} distinct values ({100.0 * distinct[i] / n:.1f}% of {n:,} samples)")
    fl = np.where(flagged)[0]
    order = sorted(fl, key=lambda i: (0 if constant[i] else 1, -int(nobs[i]) if constant[i] else -float(ratio[i])))  # constant first, then the most dominated
    out["flagged_features"] = [{"feature": names[i], "kind": "constant" if constant[i] else "near-zero variance", "repeated_value": value(i),
                                "samples_with_value": int(nobs[i]), "most_common_count": int(top2[i, 0]), "second_count": int(top2[i, 1]),
                                "distinct_values": int(distinct[i]), "reason": reason(i)} for i in order]
    # caution, not scored: relative spread of the features not flagged, on linear declared units without negative values
    gate = stored.get("cv_gate") or {}
    cut = cv_pct(params)
    out["cv"] = {"checked": bool(gate.get("checked")), "reason": gate.get("reason", ""), "units": gate.get("units", ""), "cv_pct": cut}
    if gate.get("checked"):
        vals, idx = data.get("cv_values"), data.get("cv_index")
        flagged_idx = set(int(data["index"][i]) for i in np.where(flagged)[0]) if "index" in data else set()
        low = []
        if vals is not None and vals.size and n:
            c = cv(vals[:, keep].T.astype(float))
            names_cv = data["cv_names"]
            low = [(str(names_cv[i]), float(c[i])) for i in np.where(c < cut)[0] if int(idx[i]) not in flagged_idx]
        out["cv"].update({"features_below": len(low), "features": [{"feature": f, "cv_pct": round(v, 3)} for f, v in sorted(low, key=lambda x: x[1])]})
    return out
