"""QC reproducibility of one matrix (one analysis from one endpoint): Sample eligibility & analytical controls, informational.

Measurement reproducibility across technical replicates (Reviewer 1, comment 3), following Broadhurst D et al. Metabolomics
2018;14(6):72, doi:10.1007/s11306-018-1367-3 (read in full): per feature, the relative standard deviation (RSD) across repeated
injections of a pooled QC sample, with the acceptance criteria "detection rate > 70%" and "RSD < 20% … or < 30% … depending on the
sample type".
Replicates: the QC-like positions of the matrix after the sample edits in force (pooled QC and reference material recognised by
Sample eligibility & analytical controls; blank-like and standard-like positions are never used), grouped by their deposited label
(one QC material per label, the sample ID when there is no label); the largest group is used, and it needs at least 3 injections.
Shown only when everything is satisfied: >= 3 injections of one QC material, and declared units on a linear scale with no negative
value: a concentration or amount; an intensity, area or count; or normalised, relative or ratio units (the measurement-scale
family). An RSD does not change when values are multiplied by a constant (normalisation to an internal standard, a total or an
amount), but it has no meaning on log-scaled or centred values, so log-scaled, other or undeclared units and negative values are not
shown. Decided 2026-10-21: normalised, relative or ratio units added (before, only the two linear families of the Low-information CV
gate were used); the scale is taken from the declared units only, never inferred from the values.
Per feature: detected = a finite, non-zero value; detection rate = detected injections / injections; RSD = SD (ddof 1) / mean x
100 over the detected values. A feature passes when its detection rate is > 70% and its RSD is below the cut-off (default 20%,
adjustable 10-40%). Not scored: more passing features = better measurement reproducibility.
Stored per matrix (scripts/add_qc_reproducibility_from_deposit.py): the values of every QC-like position (float32), the names.
"""
from __future__ import annotations

import collections
from typing import Any

import numpy as np

from merit import qc_blank_v9 as qb
from merit import structural_v9 as sv

PARAMETERS: dict[str, tuple[float, float, float, float]] = {"qc_rsd_cutoff": (20.0, 10.0, 40.0, 1.0)}
DEFAULT_PARAMS: dict[str, float] = {k: v[0] for k, v in PARAMETERS.items()}
DETECTION_PCT = 70.0          # Broadhurst et al. 2018: detection rate > 70%
MIN_INJECTIONS = 3
QC_FAMILIES = ("Concentration or amount", "Intensity, area or counts", "Normalised, relative or ratio")   # measurement-scale families
FAMILY_REASON = {"Log indicated": "declared units are log-scaled", "Other or mixed": "declared units are other or mixed"}


def gate(family: str, negative: bool) -> tuple[bool, str]:
    """(shown allowed, reason when not) from the declared units family and whether a measured value is negative."""
    if family in QC_FAMILIES:
        return (not negative, "negative values are present" if negative else "")
    return False, FAMILY_REASON.get(family, "no units declared")


def cutoff(params: dict | None) -> float:
    return float((params or {}).get("qc_rsd_cutoff", DEFAULT_PARAMS["qc_rsd_cutoff"]))


def material(roster: list[list], i: int) -> str:
    return str(roster[i][sv.I_LABEL] or "").strip() or str(roster[i][sv.I_ID])


def qc_group(roster: list[list], overrides: dict | None, study_id: str) -> tuple[str, list[int], int]:
    """(material, positions of its largest group, QC-like positions in all) after the sample edits in force."""
    kinds = qb.position_kinds(roster, overrides, study_id)
    by: dict[str, list[int]] = collections.OrderedDict()
    for i, k in enumerate(kinds):
        if k == "qc":
            by.setdefault(material(roster, i), []).append(i)
    if not by:
        return "", [], 0
    name, pos = max(by.items(), key=lambda kv: len(kv[1]))
    return name, pos, sum(len(v) for v in by.values())


def compute(roster: list[list], stored: dict, data: dict | None, study_id: str, overrides: dict | None = None,
            params: dict | None = None, features: bool = False) -> dict[str, Any]:
    cut = cutoff(params)
    name, pos, n_qc = qc_group(roster, overrides, study_id)
    out: dict[str, Any] = {"qc_like_positions": n_qc, "material": name, "injections": len(pos), "rsd_cutoff": cut,
                           "detection_pct": DETECTION_PCT, "shown": False}
    if len(pos) < MIN_INJECTIONS:
        out["reason"] = (f"fewer than {MIN_INJECTIONS} injections of one QC material" if n_qc else "no QC-like position")
        return out
    if not stored.get("gate"):
        out["reason"] = stored.get("gate_reason") or "declared units not linear"
        return out
    if data is None or "values" not in data:
        out["reason"] = "values not stored"
        return out
    rows = [int(np.where(data["positions"] == p)[0][0]) for p in pos if p in set(data["positions"].tolist())]
    v = data["values"][rows].astype(np.float64)
    det = np.isfinite(v) & (v != 0)
    k = det.sum(axis=0); rate = 100.0 * k / len(rows)
    x = np.where(det, v, np.nan)
    with np.errstate(all="ignore"):
        mean = np.nanmean(x, axis=0); sd = np.nanstd(x, axis=0, ddof=1)
        rsd = np.where(k >= 2, 100.0 * sd / mean, np.nan)
    detected_ok = rate > DETECTION_PCT
    passing = detected_ok & (rsd < cut)
    f = v.shape[1]
    out.update({"shown": True, "features": int(f), "passing": int(passing.sum()), "failing_detection": int((~detected_ok).sum()),
                "failing_rsd": int((detected_ok & ~(rsd < cut)).sum()),
                "median_rsd": float(np.nanmedian(rsd[detected_ok])) if detected_ok.any() else None})
    if features:
        names = data["names"]
        out["per_feature"] = [{"feature": str(names[j]), "detection_pct": round(float(rate[j]), 1),
                               "rsd_pct": None if not np.isfinite(rsd[j]) else round(float(rsd[j]), 2), "passes": bool(passing[j])}
                              for j in np.argsort(np.where(np.isfinite(rsd), rsd, np.inf), kind="stable")]
    return out
