from __future__ import annotations

from typing import Iterable

from merit.models import AssessmentReport, MetricResult

# NOTE ON DESIGN:
# Annotation is intentionally included in the core ML readiness score.
# In metabolomics, a model can technically train without named metabolites,
# but poor annotation severely limits biological interpretability, reuse, and
# scientific value of model outputs. The core score therefore reflects both
# predictive feasibility and interpretability.

# Fixed metric counts per section based on the full profile.
# This ensures the denominator is stable regardless of which profile was
# actually run, preventing score drift between core and full runs.
_SECTION_METRIC_COUNTS: dict[str, int] = {
    "structural": 5,
    "metadata": 3,
    "analytical": 5,  # QC/MetaBatch/scale diagnostics are informational
    "annotation": 4,
    "cohort": 3,  # class_balance, group_size_support, label_entropy
    "ml_feasibility": 4,  # disease_endpoint_extractability, factor_label_harmonizability, label_suitability, feature_to_sample_ratio
}

_ML_SCORING_METRICS = {
    "disease_endpoint_extractability",
    "factor_label_harmonizability",
    "label_suitability",
    "feature_to_sample_ratio",
}

_CORE_SECTION_KEYS = ("structural", "analytical", "annotation", "cohort", "ml_feasibility")
_REUSABILITY_SECTION_KEYS = ("metadata",)
_NON_REUSE_REPORT_FIELDS = (
    "schema_validation",
    "analytical_readiness",
    "annotation_readiness",
    "cohort_bias",
    "ml_readiness",
    "class_separability",
    "cross_study_harmonization",
)

_BAND_ORDER = {
    "No Data": 0,
    "Not Ready": 1,
    "Fragile": 2,
    "Conditional": 3,
    "Ready": 4,
}

def _mean_score(metrics: Iterable[MetricResult], fixed_count: int = 0) -> float:
    items = [m for m in metrics if not getattr(m, "informational", False)]
    if not items:
        return 0.0
    denominator = max(len(items), fixed_count) if fixed_count > 0 else len(items)
    return sum(metric.score for metric in items) / denominator


def _metric_by_name(metrics: Iterable[MetricResult], name: str) -> MetricResult | None:
    for metric in metrics:
        if metric.name == name:
            return metric
    return None


def apply_no_data_metric_policy(report: AssessmentReport) -> None:
    """For No Data reports, only Metadata/FAIR reuse metrics retain scores.

    Non-reuse metrics may contain useful raw evidence (for example named
    metabolite metadata), but without at least one usable quantitative matrix
    they should not contribute positive readiness signal.
    """
    policy_note = (
        "No usable tabular source matrix is available; non-reuse metric score "
        "set to 0 by No Data policy. Metadata/FAIR reuse metrics are retained."
    )
    for field_name in _NON_REUSE_REPORT_FIELDS:
        for metric in getattr(report, field_name, []) or []:
            details = dict(metric.details or {})
            if details.get("no_data_policy") != "non_reuse_score_zeroed":
                details["no_data_policy"] = "non_reuse_score_zeroed"
                details["raw_score_before_no_data_policy"] = metric.score
                details["raw_status_before_no_data_policy"] = metric.status
                details["raw_summary_before_no_data_policy"] = metric.summary
            metric.score = 0.0
            metric.status = "no_data"
            if not str(metric.summary or "").startswith("No usable tabular source matrix"):
                metric.summary = f"{policy_note} Raw metric evidence: {metric.summary}"
            metric.details = details


def _band_from_score(score: float) -> str:
    if score >= 0.85:
        return "Ready"
    if score >= 0.7:
        return "Conditional"
    if score >= 0.5:
        return "Fragile"
    return "Not Ready"


def _cap_band(provisional_band: str, ceiling_band: str | None) -> str:
    if not ceiling_band:
        return provisional_band
    current_rank = _BAND_ORDER.get(provisional_band, 1)
    ceiling_rank = _BAND_ORDER.get(ceiling_band, current_rank)
    capped_rank = min(current_rank, ceiling_rank)
    for name, rank in _BAND_ORDER.items():
        if rank == capped_rank:
            return name
    return provisional_band


def _compute_gates(
    report: AssessmentReport,
    source_availability: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    gates: list[dict[str, object]] = []

    tabular = _metric_by_name(report.schema_validation, "tabular_data_availability")
    if source_availability:
        n_with_data = int(
            (source_availability.get("datatable_count", 0) or 0)
            + (source_availability.get("mwtab_count", 0) or 0)
            + (source_availability.get("untarg_data_count", 0) or 0)
        )
        g1_status = "pass" if n_with_data > 0 else "fail"
    else:
        n_with_data = int((tabular.details or {}).get("n_with_data", 0)) if tabular else 0
        g1_status = "pass" if tabular and tabular.score > 0 else "fail"
    gates.append(
        {
            "id": "G1",
            "name": "tabular_data_availability",
            "status": g1_status,
            "value": n_with_data,
            "rule": ">= 1 usable assay matrix",
            "summary": f"{n_with_data} usable matrix/matrices found.",
        }
    )

    min_sample = _metric_by_name(report.schema_validation, "minimum_sample_count")
    min_sample_details = (min_sample.details or {}) if min_sample else {}
    n_bio = int(min_sample_details.get("n_biological_samples", 0))
    threshold = int(min_sample_details.get("threshold", 20))
    if n_bio >= threshold:
        g2_status = "pass"
    elif n_bio >= max(5, threshold // 2):
        g2_status = "warn"
    else:
        g2_status = "fail"
    gates.append(
        {
            "id": "G2",
            "name": "sufficient_biological_sample_count",
            "status": g2_status,
            "value": n_bio,
            "rule": f">= {threshold} preferred; < {max(5, threshold // 2)} severe",
            "summary": f"{n_bio} ML-eligible samples.",
        }
    )

    endpoint = _metric_by_name(report.ml_readiness, "disease_endpoint_extractability")
    endpoint_details = (endpoint.details or {}) if endpoint else {}
    n_groups = int(endpoint_details.get("distinct_label_groups", 0))
    g3_status = "pass" if n_groups >= 2 else "fail"
    gates.append(
        {
            "id": "G3",
            "name": "deposited_groups",
            "status": g3_status,
            "value": n_groups,
            "rule": ">= 2 groups",
            "summary": f"{n_groups} distinct deposited groups.",
        }
    )

    group_support = _metric_by_name(report.cohort_bias, "group_size_support")
    group_details = (group_support.details or {}) if group_support else {}
    suitability = _metric_by_name(report.ml_readiness, "label_suitability")
    suitability_details = (suitability.details or {}) if suitability else {}
    class_counts = group_details.get("counts") or suitability_details.get("counts", {}) or {}
    if hasattr(class_counts, "items"):
        counts_dict = dict(class_counts)
    else:
        counts_dict = {}
    min_class_n = int(group_details.get("min_group_size", 0) or 0) if group_details else 0
    if not min_class_n:
        min_class_n = min(counts_dict.values()) if counts_dict else 0
    min_class_required = int(suitability_details.get("minimum_class_count", 5))
    if min_class_n >= min_class_required and len(counts_dict) >= 2:
        g4_status = "pass"
    elif min_class_n >= 3 and len(counts_dict) >= 2:
        g4_status = "warn"
    else:
        g4_status = "fail"
    gates.append(
        {
            "id": "G4",
            "name": "minimum_per_group_support",
            "status": g4_status,
            "value": min_class_n,
            "rule": f"min class >= {min_class_required}",
            "summary": f"Smallest class has {min_class_n} samples across {len(counts_dict)} labeled groups.",
        }
    )

    missingness = _metric_by_name(report.analytical_readiness, "missingness_structure")
    missing_details = (missingness.details or {}) if missingness else {}
    median_missing = missing_details.get("global_median_sample_missingness_rate")
    if median_missing is None:
        g5_status = "warn"
        median_missing = 1.0
    elif float(median_missing) <= 0.5:
        g5_status = "pass"
    elif float(median_missing) <= 0.8:
        g5_status = "warn"
    else:
        g5_status = "fail"
    gates.append(
        {
            "id": "G5",
            "name": "non_catastrophic_missingness",
            "status": g5_status,
            "value": float(median_missing),
            "rule": "median sample missingness <= 50% preferred; >80% catastrophic",
            "summary": f"Median sample missingness {float(median_missing):.1%}.",
        }
    )

    return gates


def compute_readiness_score(
    report: AssessmentReport,
    source_tier: str = "tier1",
    source_availability: dict[str, object] | None = None,
) -> dict[str, object]:
    """Compute the ML Readiness Score for an assessment report.

    Args:
        report: The completed AssessmentReport.
        source_tier: retained for backward compatibility in report payloads.
            Composite scoring is now section-unweighted.
    """
    gates = _compute_gates(report, source_availability=source_availability)
    gate_counts = {"pass": 0, "warn": 0, "fail": 0}
    for gate in gates:
        status = str(gate.get("status", "warn"))
        if status not in gate_counts:
            continue
        gate_counts[status] += 1

    has_g1_fail = any(g["id"] == "G1" and g["status"] == "fail" for g in gates)
    has_any_fail = gate_counts["fail"] > 0
    has_any_warn = gate_counts["warn"] > 0

    if has_g1_fail:
        apply_no_data_metric_policy(report)

    sections = {
        "structural": _mean_score(report.schema_validation, _SECTION_METRIC_COUNTS["structural"]),
        "metadata": _mean_score(report.metadata_readiness, _SECTION_METRIC_COUNTS["metadata"]),
        "analytical": _mean_score(report.analytical_readiness, _SECTION_METRIC_COUNTS["analytical"]),
        "annotation": _mean_score(report.annotation_readiness, _SECTION_METRIC_COUNTS["annotation"]),
        "cohort": _mean_score(report.cohort_bias, _SECTION_METRIC_COUNTS["cohort"]),
        "ml_feasibility": _mean_score(
            [metric for metric in report.ml_readiness if metric.name in _ML_SCORING_METRICS],
            _SECTION_METRIC_COUNTS["ml_feasibility"],
        ),
    }
    core_ml_readiness_score = sum(sections[key] for key in _CORE_SECTION_KEYS) / len(_CORE_SECTION_KEYS)
    reusability_score = sum(sections[key] for key in _REUSABILITY_SECTION_KEYS) / len(_REUSABILITY_SECTION_KEYS)

    provisional_band = _band_from_score(core_ml_readiness_score)

    if has_g1_fail:
        gate_ceiling = "No Data"
        final_band = "No Data"
        reported_core_score = 0.0
    elif has_any_fail:
        gate_ceiling = "Not Ready"
        final_band = _cap_band(provisional_band, gate_ceiling)
        reported_core_score = core_ml_readiness_score
    elif has_any_warn:
        gate_ceiling = "Conditional"
        final_band = _cap_band(provisional_band, gate_ceiling)
        reported_core_score = core_ml_readiness_score
    else:
        gate_ceiling = None
        final_band = provisional_band
        reported_core_score = core_ml_readiness_score

    if final_band == "No Data":
        recommendation = (
            "No usable tabular feature data found. Core ML readiness is not meaningful "
            "without at least one parseable assay matrix."
        )
    elif final_band == "Ready":
        recommendation = (
            "Dataset is strong enough for baseline ML and cross-study screening with normal review controls."
        )
    elif final_band == "Conditional":
        recommendation = (
            "Dataset is usable, but one or more feasibility gates are borderline. "
            "Address flagged issues before publication-grade modeling."
        )
    elif final_band == "Fragile":
        recommendation = (
            "Dataset may support exploratory work only; remediation and confound review are required first."
        )
    else:
        recommendation = (
            "Dataset should not be used for publication-grade ML until major feasibility constraints are resolved."
        )

    weak_sections = [name for name, value in sections.items() if value < 0.7]
    actions = []
    if "metadata" in weak_sections:
        actions.append(
            "Strengthen study-level FAIR metadata, metabolite identifier resolvability, "
            "and mass/RT-like metabolite metadata."
        )
    if "analytical" in weak_sections:
        actions.append("Review QC, blanks, missingness, and assay comparability before merging or training.")
    if "annotation" in weak_sections:
        actions.append("Improve annotation quality by resolving ambiguous, unknown, and duplicate metabolite labels.")
    if "cohort" in weak_sections or "ml_feasibility" in weak_sections:
        actions.append("Rebalance labels and validate split strategy to reduce leakage and bias.")
    failing_gates = [gate for gate in gates if gate.get("status") == "fail"]
    warning_gates = [gate for gate in gates if gate.get("status") == "warn"]
    if failing_gates:
        actions.insert(0, "Resolve failed feasibility gates before relying on readiness bands.")
    elif warning_gates:
        actions.insert(0, "Review gate warnings; they cap readiness at Conditional even with strong section scores.")
    if not actions:
        actions.append("Proceed to external validation and holdout analysis.")

    section_scores = {key: round(value, 3) for key, value in sections.items()}
    # Backward-compatible alias for older payload consumers.
    section_scores["ml"] = section_scores.get("ml_feasibility", 0.0)

    core_section_scores = {key: round(sections[key], 3) for key in _CORE_SECTION_KEYS}
    core_section_scores["ml"] = core_section_scores.get("ml_feasibility", 0.0)

    return {
        # Backward-compatible aliases used in UI and cached payloads.
        "score": round(reported_core_score, 3),
        "band": final_band,
        # New framework fields.
        "core_ml_readiness_score": round(reported_core_score, 3),
        "reusability_score": round(reusability_score, 3),
        "provisional_band": provisional_band,
        "final_band": final_band,
        "gate_ceiling": gate_ceiling,
        "gates": gates,
        "gate_summary": {
            "pass": gate_counts["pass"],
            "warn": gate_counts["warn"],
            "fail": gate_counts["fail"],
        },
        "source_tier": source_tier,
        "weights_used": {},
        "section_scores": section_scores,
        "core_section_scores": core_section_scores,
        "reusability_section_scores": {key: round(sections[key], 3) for key in _REUSABILITY_SECTION_KEYS},
        "recommendation": recommendation,
        "actions": actions,
        "status_note": (
            "Core ML readiness combines structural, analytical QC, annotation, cohort, and ML-feasibility sections. "
            "FAIR metadata is reported separately as a reusability score. "
            "Final band is gate-ceiling-adjusted from the provisional band."
        ),
    }
