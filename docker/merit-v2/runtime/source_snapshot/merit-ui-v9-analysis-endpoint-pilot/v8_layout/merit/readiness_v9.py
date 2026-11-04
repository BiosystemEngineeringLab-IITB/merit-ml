"""Readiness profile per matrix (analysis x endpoint), decided by the author on 2026-10-25 and revised the same day after an
external review. Nothing is averaged or combined across metrics, matrices, analyses or the study; no matrix is called "ready".

Three profiles, reported separately (Lawrence N.D., "Data Readiness Levels", arXiv:1705.02245, 2017, separates the faithfulness of
the data, band B, from its suitability for a task, band A, which can only be judged once the task is defined):
  analytical review alerts   label-independent. A valid matrix is required (else "no data"). An alert is raised when sample-level
            missingness, low-information features, redundant features (kept by the author on 2026-10-25) or outlier samples are below a
            percentile of the reference cohort, in two tiers (2026-10-25): extreme below the 2.5th, low below the 10th (Show tools:
            2.5, 5, 7.5 or 10 each) (all valid
            matrices of the 9 March 2026 pull at the default settings; strict comparison, so matrices tied at the cut-off are not
            alerted). The cut-offs are frozen in the manifest (scripts/add_readiness_reference.py): adding studies does not change them.
            Alerts are cohort-relative: "unusually low relative to the reference cohort", not "unusable". Units declared is a
            documentation note beside the alerts, not an alert (its absence does not prevent within-study ML).
  cross-validation sample counts   under the classes in force (by default the deposited labels): met (at least 2 classes and the
            smallest class has at least k sample IDs, k = the folds setting), not met (one class, or the smallest class below k), or
            unresolved (the classes cannot be assessed: no labelled sample, or a chosen class absent from the matrix). Counts only:
            subject-disjoint splitting is not checked (renamed from "task feasibility" after a review on 2026-10-25). Task profile (values, never
            alerts): class support, class separability with its chance level, and whether the feature-association test is available
            (at least one feature was tested by the test actually used: Kruskal-Wallis, or the paired / repeated-measures test with
            confirmed subjects).
  interpretation and reuse   values only: named-feature coverage, RefMet mapping, and the analysis's Metadata & FAIR items.
Endpoints of an analysis share a result when their alerts, task result and its reason are the same; their values are still shown per
endpoint where they differ. The non-compensatory logic follows the OECD/JRC Handbook on Constructing Composite Indicators (2008,
pp. 33 and 103-104: additive aggregation lets a deficit be offset by a surplus); RoB 2 (Cochrane Handbook v6.5, ch. 8, Table 8.2.b)
is cited as an analogy only (an overall judgement that strong domains cannot rescue).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

ALERT_METRICS = (("missingness", "Missing entries and zeros"), ("low_information", "Low-information features"),
                 ("redundancy", "Redundant features"), ("outliers", "Outlier samples"))
PERCENTILE = 2.5
PERCENTILES = (2.5, 5.0, 7.5, 10.0)          # reference cut-offs are kept for each (scripts/add_readiness_reference.py)
# two alert tiers (decided by the author on 2026-10-25): extreme = below the 2.5th percentile, low = below the 10th; both in Show tools
PARAMETERS: dict = {"readiness_extreme_pct": (2.5, 2.5, 10.0, 2.5), "readiness_low_pct": (10.0, 2.5, 10.0, 2.5)}


def _snap(v, default: float) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = default
    return min(PERCENTILES, key=lambda p: (abs(p - v), p))


# custom score cut-offs (decided by the author on 2026-10-31): a user may give, per check and tier, a score below which the alert is
# raised instead of the reference percentile; an empty field keeps the reference. Not a new reference: R1 stays fixed and is the default,
# and every alert says which cut-off raised it. Low is never stricter than extreme (a lower low cut-off is raised to the extreme one).
CUSTOM_TIERS = ("extreme", "low")
CUSTOM_KEYS = tuple(f"readiness_cut_{key}_{t}" for key, _ in ALERT_METRICS for t in CUSTOM_TIERS)


def custom(params: dict | None) -> dict:
    """{(check, tier): score} of the custom cut-offs in force: numbers from 0 to 100; anything else (empty, text, out of range) is ignored."""
    out = {}
    for key, _ in ALERT_METRICS:
        for t in CUSTOM_TIERS:
            raw = str((params or {}).get(f"readiness_cut_{key}_{t}", "") or "").strip()
            try:
                v = float(raw)
            except ValueError:
                continue
            if 0.0 <= v <= 100.0:
                out[(key, t)] = v
    return out


def custom_ignored(params: dict | None) -> list[tuple[str, str, str]]:
    """[(check, tier, entry)] of the custom fields that hold something other than a number from 0 to 100 (ignored: the reference is used)."""
    out = []
    for key, _ in ALERT_METRICS:
        for t in CUSTOM_TIERS:
            raw = str((params or {}).get(f"readiness_cut_{key}_{t}", "") or "").strip()
            if raw and (key, t) not in custom(params):
                out.append((key, t, raw))
    return out


def tiers(params: dict | None) -> tuple[float, float]:
    """(extreme, low) percentiles in force, each snapped to a kept percentile; low is never stricter than extreme."""
    p = params or {}
    ext = _snap(p.get("readiness_extreme_pct", 2.5), 2.5); low = _snap(p.get("readiness_low_pct", 10.0), 10.0)
    return ext, max(low, ext)


def _reference_cutoffs(params: dict | None, which: str) -> dict:
    ext, low = tiers(params); p = ext if which == "extreme" else low
    return ((reference().get("sensitivity") or {}).get(f"{p:g}") or {}).get("cutoffs") or {}


def cutoffs(params: dict | None = None, which: str = "extreme") -> dict:
    """The cut-offs of one tier in force: the reference percentile's ({} when the reference is missing), with any custom score cut-off
    in its place; a low cut-off is never below the extreme one of the same check."""
    if not _reference_cutoffs(params, "extreme"):
        return {}
    cus = custom(params)
    ext = {k: cus.get((k, "extreme"), v) for k, v in _reference_cutoffs(params, "extreme").items()}
    if which == "extreme":
        return ext
    return {k: max(cus.get((k, "low"), v), ext.get(k, v)) for k, v in _reference_cutoffs(params, "low").items()}


def cutoff_sources(params: dict | None = None) -> dict:
    """{(check, tier): 'custom' or 'reference'}: where each cut-off in force comes from."""
    cus = custom(params)
    return {(k, t): ("custom" if (k, t) in cus else "reference") for k, _ in ALERT_METRICS for t in CUSTOM_TIERS}


@lru_cache(maxsize=1)
def reference() -> dict:
    """The frozen reference cut-offs (2.5th percentile of the reference cohort, default settings) and their sensitivity counts."""
    root = Path(os.environ.get("MERIT_V9_CACHE_ROOT", "/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/merit-cache-workbench-full-v9"))
    try:
        return (json.loads((root / "manifest.json").read_text())["sections"].get("readiness") or {})
    except (OSError, ValueError, KeyError):
        return {}


def data_result(items: dict, params: dict | None = None) -> dict:
    """Analytical review alerts of one matrix from the Overview items (the values its tabs show); units as a documentation note.
    Results: "extreme alerts" (at least one extreme); "low alerts" (only low); "no alerts" (all three checks assessed); "no alerts (partial)" (no alert among the checks that could be
    assessed, some could not); "not assessable" (none of the three could be computed); "reference unavailable" (the cut-offs are
    missing from the manifest: nothing is judged, rather than silently reporting no alerts)."""
    cut = cutoffs(params, "extreme"); cut_low = cutoffs(params, "low"); ext_p, low_p = tiers(params)
    alerts = []; not_assessed = []; values = {}
    for key, name in ALERT_METRICS:
        it = items.get(key) or {}
        values[key] = it.get("value")
        if it.get("value") is None:
            not_assessed.append(name)
        elif key in cut and it["value"] < cut[key]:
            alerts.append({"key": key, "name": name, "value": it["value"], "cutoff": cut[key], "tier": "extreme", "percentile": ext_p,
                           "custom": cut[key] != _reference_cutoffs(params, "extreme").get(key)})
        elif key in cut_low and it["value"] < cut_low[key]:
            alerts.append({"key": key, "name": name, "value": it["value"], "cutoff": cut_low[key], "tier": "low", "percentile": low_p,
                           "custom": cut_low[key] != _reference_cutoffs(params, "low").get(key)})
    units = items.get("units") or {}
    assessed = len(ALERT_METRICS) - len(not_assessed)
    if not all(k in cut and k in cut_low for k, _ in ALERT_METRICS):
        result = "reference unavailable"; alerts = []
    elif any(a["tier"] == "extreme" for a in alerts):
        result = "extreme alerts"
    elif alerts:
        result = "low alerts"
    elif not assessed:
        result = "not assessable"
    elif assessed < len(ALERT_METRICS):
        result = "no alerts (partial)"
    else:
        result = "no alerts"
    return {"result": result, "alerts": alerts, "not_assessed": not_assessed, "assessed": assessed, "values": values,
            "units_declared": None if units.get("value") is None else bool(units.get("flag"))}


def task_result(lab: dict | None, ml: dict | None, folds: int) -> dict:
    """Cross-validation sample counts and task profile of one matrix under the classes in force (its Label Structure and ML Task Readiness records)."""
    lab = lab or {}; ml = ml or {}
    n_classes = lab.get("n_classes") or 0; smallest = lab.get("smallest")
    if lab.get("class_support") is None:
        state, reason = "unresolved", lab.get("not_assessed") or "the classes cannot be assessed"
    elif n_classes < 2:
        state, reason = "not met", "one class"
    elif smallest is None or smallest < folds:
        state, reason = "not met", f"smallest class {smallest} < {folds} folds"
    else:
        state, reason = "met", None
    d = ml.get("discriminative") or {}
    return {"result": state, "reason": reason, "n_classes": n_classes, "smallest": smallest, "class_support": lab.get("class_support"),
            "separability": ml.get("score"), "chance": ml.get("chance"), "association_available": bool(d.get("judged")),
            "associated": d.get("significant"), "tested": d.get("judged"), "adjusted": bool(d.get("adjusted")),
            "shared_subjects": ml.get("subjects_with_several_samples") or 0, "subjects_confirmed": bool(ml.get("subjects_confirmed"))}


# wording signed off on 2026-10-29 (external review of the layout): "count requirement", "cannot assess class counts"
COUNTS_PART = {"met": "Count requirement met", "not met": "Count requirement not met", "unresolved": "Cannot assess class counts"}


def summary_parts(data: dict, task: dict) -> dict:
    """The two results of one matrix side by side (decided by the author on 2026-10-25, replacing six named labels and their priority
    rule): the sample-count result, the strongest alert tier raised with the number of checks raising each tier, and how many of the
    four checks could be assessed. Neither part ranks or hides the other."""
    n_ext = sum(a["tier"] == "extreme" for a in data["alerts"]); n_low = sum(a["tier"] == "low" for a in data["alerts"])
    tier = {"extreme alerts": "extreme", "low alerts": "low", "no alerts": "none", "no alerts (partial)": "none"}.get(data["result"], data["result"])
    return {"counts": task["result"], "alerts": tier, "n_extreme": n_ext, "n_low": n_low, "assessed": data["assessed"], "checks": len(ALERT_METRICS)}


def summary(data: dict, task: dict) -> str:
    """The literal summary, e.g. 'Count requirement met · 1 extreme + 2 low alerts · 4/4 checks assessed'. Any extreme alert makes the
    alert result extreme, otherwise any low alert makes it low; the counts give the breadth. With no alert and some checks not
    assessed it reads 'no alerts among N/4 assessed checks'. 'Reference unavailable' is an error, not a result."""
    p = summary_parts(data, task); n = p["checks"]
    plural = lambda k: "alert" if k == 1 else "alerts"
    if p["alerts"] == "reference unavailable":
        return f"{COUNTS_PART[p['counts']]} · error: reference unavailable, no alert judged"
    if p["alerts"] == "not assessable":
        return f"{COUNTS_PART[p['counts']]} · analytical checks not assessable"
    if p["n_extreme"] and p["n_low"]:
        mid = f"{p['n_extreme']} extreme + {p['n_low']} low {plural(p['n_extreme'] + p['n_low'])}"
    elif p["n_extreme"]:
        mid = f"{p['n_extreme']} extreme {plural(p['n_extreme'])}"
    elif p["n_low"]:
        mid = f"{p['n_low']} low {plural(p['n_low'])}"
    elif p["assessed"] == n:
        mid = "no alerts"
    else:
        return f"{COUNTS_PART[p['counts']]} · no alerts among {p['assessed']}/{n} assessed checks"
    return f"{COUNTS_PART[p['counts']]} · {mid} · {p['assessed']}/{n} checks assessed"


SUMMARY_FORMAT = ("cross-validation sample-count support (Count requirement met / Count requirement not met / Cannot assess class counts) · "
                  "analytical alerts (no alerts · N/4 checks assessed / no alerts among N/4 assessed checks / N low alerts / N extreme alerts / "
                  "N extreme + M low alerts / analytical checks not assessable)")


def settings(params: dict | None, folds: int, deposited_labels: bool, class_definition: str = "") -> dict:
    """What the readiness results of a page or download were computed with: the tiers, k, the classes, every cut-off and the reference version."""
    ref = reference(); ext, low = tiers(params)
    return {"extreme_percentile": ext, "low_percentile": low, "folds": folds, "comparison": "strictly below the cut-off",
            "classes": "the deposited labels" if deposited_labels else (class_definition or "the chosen classes"),
            "cutoffs_in_force": {"extreme": cutoffs(params, "extreme"), "low": cutoffs(params, "low")},
            "custom_cutoffs": {f"{k}:{t}": v for (k, t), v in custom(params).items()},
            "cutoffs_set_by_user": [{"check": k, "tier": t, "alert_below": v, "reference_value": _reference_cutoffs(params, t).get(k)}
                                    for (k, t), v in custom(params).items()],
            "cutoffs_by_percentile": {p: v.get("cutoffs") for p, v in (ref.get("sensitivity") or {}).items()},
            "reference": {k: ref.get(k) for k in ("reference_version", "reference_cohort", "reference_matrices", "n_matrices_with_a_value",
                                                  "reference_cohort_sha256", "created_at")},
            "summary_format": SUMMARY_FORMAT}


def signature(data: dict, task: dict) -> tuple:
    """What endpoints must share for one result to be shown for the analysis (values are still shown per endpoint): the alert result,
    which alerts, which checks could not be assessed, the sample-count result and its reason, and whether the feature test ran."""
    return (data["result"], tuple((a["key"], a["tier"]) for a in data["alerts"]), tuple(data["not_assessed"]), task["result"], task["reason"],
            task["association_available"])
