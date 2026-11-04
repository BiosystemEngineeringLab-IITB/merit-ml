"""Outlier samples of one matrix (one analysis from one endpoint), Analytical QC. Scored (Reviewer 1, comment 3).

v8's rule (merit-ui-v8/merit/metrics/analytical.py:554, outlier_burden), on the data as deposited and on the ML-eligible samples
after the sample edits in force: each sample is summarised by the median of its numeric values over all features (zeros included;
missing and invalid entries are not numbers and do not enter); a sample is an outlier when its median lies below Q1 - k x IQR or
above Q3 + k x IQR of the medians of the ML-eligible samples (Tukey fences; k = 1.5 as in v8, adjustable 1.0-3.0). The same rule
for every endpoint. Score = 100 x (1 - outlier samples / samples evaluated); not assessed with fewer than 4 samples with a median
(v8). When the IQR is 0 (more than half of the medians equal, usually 0) the fences collapse onto that value; this is stated.
Medians per position are stored in the v9 cache (endpoints[*].sample_medians_all, scripts/add_outliers_from_deposit.py).
A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from merit import structural_v9 as sv

PARAMETERS: dict[str, tuple[float, float, float, float]] = {"outlier_iqr_k": (1.5, 1.0, 3.0, 0.1)}
DEFAULT_PARAMS: dict[str, float] = {k: v[0] for k, v in PARAMETERS.items()}
MIN_SAMPLES = 4
EXAMPLES = 5


def k_of(params: dict | None) -> float:
    return float((params or {}).get("outlier_iqr_k", DEFAULT_PARAMS["outlier_iqr_k"]))


def compute(roster: list[list], medians: list[float | None], overrides: dict | None = None, params: dict | None = None) -> dict[str, Any]:
    k = k_of(params)
    pos = [(p["sample_id"], m) for p, m in zip(sv.effective_positions(roster, overrides), medians)
           if p["eligible"] and not p["excluded"] and not p["row"][sv.I_ALIGN]]
    have = [(s, float(m)) for s, m in pos if m is not None and np.isfinite(m)]
    out: dict[str, Any] = {"ml_eligible_samples": len(pos), "samples_evaluated": len(have), "without_numeric_value": len(pos) - len(have), "k": k}
    if len(have) < MIN_SAMPLES:
        out.update({"score": None, "not_assessed": "fewer than 4 samples with a numeric value", "outliers": 0, "outlier_samples": []})
        return out
    m = np.array([x for _, x in have])
    q1, q3 = (float(x) for x in np.percentile(m, [25, 75]))
    iqr = q3 - q1; lo, hi = q1 - k * iqr, q3 + k * iqr
    flagged = []
    for s, x in have:
        if x < lo or x > hi:
            flagged.append({"sample_id": s, "median": x, "side": "below" if x < lo else "above", "fence": lo if x < lo else hi,
                            "distance_iqr": (abs(x - (lo if x < lo else hi)) / iqr) if iqr > 0 else None})
    flagged.sort(key=lambda d: -abs(d["median"] - (q1 + q3) / 2))               # the most extreme first
    out.update({"q1": q1, "q3": q3, "iqr": iqr, "lower_fence": lo, "upper_fence": hi, "collapsed": iqr == 0,
                "outliers": len(flagged), "score": 100.0 * (1 - len(flagged) / len(have)), "not_assessed": None, "outlier_samples": flagged})
    return out
