from __future__ import annotations

import heapq
import json
import re
from math import isfinite, log10
from pathlib import Path
from typing import Any

import numpy as np

from merit.models import CanonicalStudy, MetricResult
from merit.utils import is_usable_class_label, percentile, sample_is_qc_like

from .base import MetricPlugin

_BATCH_KEYS = {
    "batch", "run", "injection", "order", "sequence", "plate", "acquisition",
    "run_order", "injection_order", "run order", "batch_id", "batch_number",
    "ms_run", "analysis_order", "queue", "worklist",
}
_METABATCH_MISSING_BATCH_VALUES = {"", "-"}
_METABATCH_FACTOR_ATTRS = (
    "factor_string",
    "class_string",
    "tabular_primary_label",
    "endpoint_label",
)
_METABATCH_TECHNICAL_BATCH_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bbatch(?:[\s_-]*(?:id|number|num|no\.?))?\b",
        r"\b(?:run|injection|analysis)[\s_-]*order\b",
        r"\binjection[\s_-]*(?:sequence|id|number|num|no\.?)\b",
        r"\bms[\s_-]*run\b",
        r"\bacquisition(?:[\s_-]*(?:date|time|id|number|num|no\.?|order|sequence))?\b",
        r"\bplate(?:[\s_-]*(?:id|number|num|no\.?|position))?\b",
        r"\bqueue\b",
        r"\bworklist\b",
        r"\braw[\s_-]*file\b",
    )
)
_QC_CONTROL_KEYWORDS = (
    "qc",
    "pool",
    "nist",
    "reference",
    "quality control",
    "pooled qc",
    "ltr",
    "sst",
    "calibration standard",
    "system suitability",
    "drift",
    "standard mixture",
    "external standard",
    "empty run",
    "equilibration",
    "conditioning",
    "wash",
)
_BLANK_CONTROL_KEYWORDS = (
    "blank",
    "solvent",
    "process blank",
    "method blank",
    "reagent blank",
)

_MISSING_TOKEN_HINTS = [
    "",
    "NA",
    "N/A",
    "null",
    "None",
    ".",
    "nan",
    "NaN",
    "nd",
    "ND",
    "bdl",
    "BDL",
    "bql",
    "BQL",
    "lod",
    "<lod",
    "<LOD",
    "llod",
    "<llod",
    "<LLOD",
    "lloq",
    "<lloq",
    "<LLOQ",
    "bloq",
    "BLOQ",
    "nq",
    "NQ",
    "loq",
    "LOQ",
    "not detected",
    "missing",
]

_MISSING_TOKENS_LOWER: frozenset[str] = frozenset(t.lower().strip() for t in _MISSING_TOKEN_HINTS)


def _is_missing(value: Any, count_zero: bool = False) -> bool:
    """Canonical missingness: None, non-finite numeric, empty string, or known
    below-detection token.

    Zero handling is source-aware:
    - datatable zeros are treated as valid (curated structural fill).
    - mwTab / untarg_data zeros are treated as missing (below detection).

    Empirical justification: cell-by-cell matching of 4,464 paired
    mwTab/datatable analyses showed that 73.9% of retained explicit mwTab
    missing tokens (nd, na, null, etc.) are replaced with zero in the
    corresponding datatable cell, with no evidence of statistical imputation.
    The remaining 17.1% are explained by feature dropping (features with 100%
    mwTab missingness removed from datatable before deposition).

    Treated as missing:
    - None
    - NaN / Inf
    - Empty string
    - Any token in _MISSING_TOKENS_LOWER (nd, bdl, bql, lod, lloq, bloq, nq, loq, etc.)
    - Any string starting with '<' (e.g. <LOD, <0.01) — below-detection values
    - Zero (only when count_zero=True, i.e. mwTab/untarg sources)
    """
    if value is None:
        return True
    if isinstance(value, (int, np.integer)):
        return count_zero and int(value) == 0
    if isinstance(value, (float, np.floating)):
        v = float(value)
        if not isfinite(v):
            return True
        return count_zero and v == 0.0
    text = str(value).strip()
    if not text:
        return True
    text_lower = text.lower()
    if text_lower in _MISSING_TOKENS_LOWER:
        return True
    if text_lower.startswith("<"):
        return True
    try:
        numeric = float(text)
    except ValueError:
        return True
    if not isfinite(numeric):
        return True
    return count_zero and numeric == 0.0


def _finite_values(values: list[Any], count_zero: bool = False) -> list[float]:
    cleaned: list[float] = []
    for value in values:
        if _is_missing(value, count_zero=count_zero):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(numeric):
            cleaned.append(numeric)
    return cleaned


def _source_kind_counts_zero(source_kind: str | None) -> bool:
    """Return True if zeros should be treated as missing for the given source.

    datatable: zeros are curated structural fill values (count_zero=False).
    mwtab, untarg, results: zeros indicate below-detection (count_zero=True).

    Empirical basis: 73.9% of retained explicit mwTab missing tokens map to
    datatable zero in a strict 4,464-cohort cell-by-cell match.
    """
    if not source_kind:
        return False
    return source_kind.lower() in {"mwtab", "untarg", "results"}


def _sample_record_lookup(study: CanonicalStudy) -> dict[str, Any]:
    return {sample.sample_id: sample for sample in study.samples}


def _factor_pairs_from_text(text: Any) -> dict[str, str]:
    """Parse MW pipe-delimited factor strings into key/value pairs."""
    pairs: dict[str, str] = {}
    for part in str(text or "").split("|"):
        token = part.strip()
        if not token or ":" not in token:
            continue
        key, _, value = token.partition(":")
        key = key.strip()
        value = value.strip()
        if key:
            pairs[key] = value
    return pairs


def _load_workbench_factor_pairs(study: CanonicalStudy) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], str]:
    """Load study-level factors.json and return maps keyed by local and MW sample IDs.

    MetaBatch/StdMW obtains candidate batch annotations from the Workbench
    allfactors endpoint. In the local MW confirmation dump that endpoint is
    represented as <study>/factors.json.
    """
    study_id = str(study.study.study_id or "").strip()
    candidates: list[Path] = []
    for manifest_item in study.provenance.file_manifest or []:
        path = Path(str(manifest_item))
        if path.name == "factors.json":
            candidates.append(path)
    source_root = Path(str(study.provenance.source_root or ""))
    if study_id:
        candidates.append(source_root / study_id / "factors.json")

    rows: list[dict[str, Any]] = []
    source_path = ""
    for candidate in dict.fromkeys(candidates):
        try:
            if not candidate.exists():
                continue
            payload = json.loads(candidate.read_text())
            if isinstance(payload, dict):
                rows = [item for item in payload.values() if isinstance(item, dict)]
            elif isinstance(payload, list):
                rows = [item for item in payload if isinstance(item, dict)]
            else:
                rows = []
            source_path = str(candidate)
            break
        except Exception:
            continue

    by_local: dict[str, dict[str, str]] = {}
    by_mb: dict[str, dict[str, str]] = {}
    for row in rows:
        factors = _factor_pairs_from_text(row.get("factors") or row.get("Factors") or "")
        local_id = str(row.get("local_sample_id") or row.get("sample_id") or "").strip()
        mb_id = str(row.get("mb_sample_id") or row.get("Subject ID") or "").strip()
        if local_id and factors:
            by_local[local_id] = factors
        if mb_id and factors:
            by_mb[mb_id] = factors
    return by_local, by_mb, source_path


def _factor_pairs_for_sample(sample: Any | None) -> dict[str, str]:
    if sample is None:
        return {}
    attrs = getattr(sample, "attributes", {})
    if not isinstance(attrs, dict):
        return {}
    pairs: dict[str, str] = {}
    for attr in _METABATCH_FACTOR_ATTRS:
        pairs.update(_factor_pairs_from_text(attrs.get(attr, "")))
    for key, value in attrs.items():
        key_text = str(key or "").strip()
        if not key_text or key_text in _METABATCH_FACTOR_ATTRS:
            continue
        if any(batch_key in key_text.lower() for batch_key in _BATCH_KEYS):
            value_text = str(value or "").strip()
            if value_text:
                pairs[key_text] = value_text
    return pairs


def _is_metabatch_technical_batch_like(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _METABATCH_TECHNICAL_BATCH_PATTERNS)


def _is_biological_sample_row(sample_id: str, label: str, sample_type: str) -> bool:
    """Return True if this sample row is a biological sample (not QC/blank/pool/reference)."""
    return not sample_is_qc_like(
        sample_id=sample_id,
        label=label,
        sample_type=sample_type,
        class_string=label,
    )


def _control_flags_for_sample(sample_id: str, sample: Any | None) -> tuple[bool, bool]:
    """Return (is_qc_control, is_blank_control) for QC presence reporting.

    Avoid classifying rows as QC solely from noisy sample_type values like
    "Pooled Sample" when biological class/factor labels are present.
    """
    label = str((getattr(sample, "label", "") if sample is not None else "") or "")
    sample_type = str((getattr(sample, "sample_type", "") if sample is not None else "") or "")
    attrs = getattr(sample, "attributes", {}) if sample is not None else {}
    if not isinstance(attrs, dict):
        attrs = {}
    class_string = str(attrs.get("class_string", "") or "")
    factor_string = str(attrs.get("factor_string", "") or "")

    primary_text = " ".join([sample_id, label, class_string, factor_string]).lower()
    sample_type_text = sample_type.lower()
    has_class_context = (
        is_usable_class_label(class_string)
        or is_usable_class_label(label)
        or bool(factor_string.strip())
    )

    is_blank = any(keyword in primary_text for keyword in _BLANK_CONTROL_KEYWORDS)
    if not is_blank and not has_class_context:
        is_blank = any(keyword in sample_type_text for keyword in _BLANK_CONTROL_KEYWORDS)

    is_qc_like = sample_is_qc_like(
        sample_id=sample_id,
        label=label,
        sample_type=sample_type,
        class_string=class_string,
        factor_string=factor_string,
        attributes=attrs,
    )
    is_qc_control = is_qc_like and not is_blank
    return is_qc_control, is_blank


def _metric_missingness_definition() -> dict[str, Any]:
    return {
        "missing_semantics": (
            "A value is missing if it is None, non-finite (NaN/Inf), an empty string, "
            "a known below-detection check (nd, bdl, bql, lod, lloq, bloq, nq, loq, etc.), "
            "or any string starting with '<' (e.g. <LOD, <LLOQ). "
            "Zero handling is source-aware: datatable zeros are treated as valid (curated structural fill); "
            "mwTab and untarg_data zeros are treated as missing (below detection)."
        ),
        "parser_token_hints": _MISSING_TOKEN_HINTS,
        "parser_note": (
            "The full metabolomics missing-value token set is applied; strings starting with '<' "
            "are always treated as missing regardless of suffix."
        ),
    }


class QcPresenceMetric(MetricPlugin):
    family = "Analytical QC"
    name = "qc_blank_presence"
    informational = True  # QC/blank presence is reportable context, not an ML-readiness criterion

    def compute(self, study: CanonicalStudy) -> MetricResult:
        sample_lookup = _sample_record_lookup(study)
        per_analysis: list[dict[str, Any]] = []

        for matrix in study.feature_matrices:
            qc_count = 0
            blank_count = 0
            for sample_id in matrix.sample_ids:
                sample = sample_lookup.get(sample_id)
                is_qc_control, is_blank = _control_flags_for_sample(str(sample_id), sample)
                if is_qc_control:
                    qc_count += 1
                if is_blank:
                    blank_count += 1
            n_samples = len(matrix.sample_ids)
            analysis_score = (0.5 if qc_count > 0 else 0.0) + (0.5 if blank_count > 0 else 0.0)
            per_analysis.append(
                {
                    "analysis_id": matrix.assay_id,
                    "n_samples": n_samples,
                    "qc_count": qc_count,
                    "blank_count": blank_count,
                    "qc_ratio": (qc_count / n_samples) if n_samples else 0.0,
                    "blank_ratio": (blank_count / n_samples) if n_samples else 0.0,
                    "score": analysis_score,
                }
            )

        total = len(study.samples)
        qc_count = 0
        blank_count = 0
        for sample in study.samples:
            is_qc_control, is_blank = _control_flags_for_sample(str(sample.sample_id), sample)
            if is_qc_control:
                qc_count += 1
            if is_blank:
                blank_count += 1

        score = 0.0
        if qc_count > 0:
            score += 0.5
        if blank_count > 0:
            score += 0.5
        qc_ratio = qc_count / total if total else 0.0
        blank_ratio = blank_count / total if total else 0.0

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.5 else "warn",
            summary=f"Detected {qc_count} QC/pool/reference controls and {blank_count} blanks across {total} samples.",
            details={
                "qc_count": qc_count,
                "blank_count": blank_count,
                "qc_ratio": qc_ratio,
                "blank_ratio": blank_ratio,
                "per_analysis": per_analysis,
                "qc_keywords": sorted(set(_QC_CONTROL_KEYWORDS)),
                "blank_keywords": sorted(set(_BLANK_CONTROL_KEYWORDS)),
            },
            thresholds={"qc_present": True, "blank_present": True},
            recommendations=[] if score == 1.0 else ["Include pooled QC and blank samples for robust analytical diagnostics when possible."],
        )


class MissingnessMetric(MetricPlugin):
    family = "Analytical QC"
    name = "missingness_structure"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        sample_lookup = _sample_record_lookup(study)
        class_gap_weighted_numer = 0.0
        class_gap_weighted_denom = 0.0
        sample_missing_all: list[dict[str, Any]] = []
        sample_missing_rates_all: list[float] = []
        per_analysis: list[dict[str, Any]] = []
        analysis_scores: list[float] = []

        for matrix in study.feature_matrices:
            count_zero = _source_kind_counts_zero(getattr(matrix, "source_kind", ""))

            # Sample-level missingness: for each biological sample (row),
            # compute the fraction of features that are missing.
            # QC/blank/pool/reference samples are excluded — they are not
            # ML training examples and would distort readiness assessment.
            sample_missing_rows: list[dict[str, Any]] = []
            class_missing: dict[str, dict[str, float]] = {}
            local_sample_rates: list[float] = []
            n_excluded_qc = 0
            for idx, row in enumerate(matrix.values):
                sample_id = matrix.sample_ids[idx] if idx < len(matrix.sample_ids) else f"row_{idx + 1}"

                # Resolve label and sample_type for biological-sample check.
                label = ""
                sample_type = ""
                if isinstance(matrix.labels, dict):
                    label = str(matrix.labels.get(sample_id, "") or "").strip()
                sample_rec = sample_lookup.get(sample_id)
                if sample_rec is not None:
                    if not label:
                        label = str(sample_rec.label or "").strip()
                    sample_type = str(getattr(sample_rec, "sample_type", "") or "").strip()

                if not _is_biological_sample_row(sample_id, label, sample_type):
                    n_excluded_qc += 1
                    continue

                n_features = len(row)
                n_missing = sum(1 for value in row if _is_missing(value, count_zero=count_zero))
                sample_rate = (n_missing / n_features) if n_features > 0 else 1.0
                local_sample_rates.append(sample_rate)
                sample_missing_rates_all.append(sample_rate)
                rec = {
                    "analysis_id": matrix.assay_id,
                    "sample_id": sample_id,
                    "missing_rate": sample_rate,
                    "missing": n_missing,
                    "total": n_features,
                }
                sample_missing_rows.append(rec)
                sample_missing_all.append(rec)

                label_norm = label.lower()
                if label and label_norm not in {"unknown", "na", "n/a", "none", "null"}:
                    stats = class_missing.setdefault(label, {"missing": 0.0, "total": 0.0, "n_samples": 0.0})
                    stats["missing"] += n_missing
                    stats["total"] += n_features
                    stats["n_samples"] += 1

            class_missing_rates = {
                label: (stats["missing"] / stats["total"]) if stats["total"] > 0 else 1.0
                for label, stats in class_missing.items()
                if stats.get("total", 0.0) > 0
            }
            class_gap = None
            if len(class_missing_rates) >= 2:
                min_rate = min(class_missing_rates.values())
                max_rate = max(class_missing_rates.values())
                class_gap = max_rate - min_rate
                n_features_in_matrix = len(matrix.values[0]) if matrix.values else 0
                n_cells = len(matrix.sample_ids) * n_features_in_matrix
                if n_cells > 0:
                    class_gap_weighted_numer += class_gap * n_cells
                    class_gap_weighted_denom += n_cells

            # Per-analysis score: 1 − median(per-sample missingness rates).
            median_sample_missing = (
                float(np.median(local_sample_rates))
                if local_sample_rates else 1.0
            )
            mean_sample_missing = (
                float(np.mean(local_sample_rates))
                if local_sample_rates else 1.0
            )
            analysis_score = 1.0 - median_sample_missing
            analysis_scores.append(analysis_score)

            sample_missing_top10 = sorted(
                sample_missing_rows,
                key=lambda item: (-float(item.get("missing_rate", 0.0)), str(item.get("sample_id", ""))),
            )[:10]

            per_analysis.append(
                {
                    "analysis_id": matrix.assay_id,
                    "source_kind": getattr(matrix, "source_kind", "") or "datatable",
                    "zero_treated_as_missing": count_zero,
                    "n_biological_samples": len(local_sample_rates),
                    "n_excluded_qc_blank": n_excluded_qc,
                    "n_features": len(matrix.feature_ids),
                    "mean_sample_missingness_rate": mean_sample_missing,
                    "median_sample_missingness_rate": median_sample_missing,
                    "class_missingness_by_label": class_missing_rates,
                    "class_dependent_gap": class_gap,
                    "sample_missing_top10": sample_missing_top10,
                    "score": analysis_score,
                }
            )

        # Aggregate score: mean of per-analysis scores (each based on median
        # sample-level missingness).
        score = float(np.mean(analysis_scores)) if analysis_scores else 0.0
        class_dependent_gap_weighted = (
            class_gap_weighted_numer / class_gap_weighted_denom
            if class_gap_weighted_denom > 0 else 0.0
        )
        global_median_sample_rate = (
            float(np.median(sample_missing_rates_all))
            if sample_missing_rates_all else 1.0
        )
        global_mean_sample_rate = (
            float(np.mean(sample_missing_rates_all))
            if sample_missing_rates_all else 1.0
        )

        sample_missing_top10_global = sorted(
            sample_missing_all,
            key=lambda item: (-float(item.get("missing_rate", 0.0)), str(item.get("analysis_id", "")), str(item.get("sample_id", ""))),
        )[:10]

        recommendations: list[str] = []
        if score < 0.9:
            recommendations.append("Filter or impute high-missingness features before modeling.")
        if class_dependent_gap_weighted >= 0.1:
            recommendations.append("Class-dependent missingness gap >= 10%; audit acquisition/preprocessing artifacts before ML.")

        status = "pass" if score >= 0.85 else "warn"
        if class_dependent_gap_weighted >= 0.1:
            status = "warn"

        n_total_samples = len(sample_missing_rates_all)
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=(
                f"Median sample-level missingness: {global_median_sample_rate:.1%} "
                f"across {n_total_samples} samples; "
                f"class-dependent gap={class_dependent_gap_weighted:.1%}."
            ),
            details={
                "n_total_samples": n_total_samples,
                "global_median_sample_missingness_rate": global_median_sample_rate,
                "global_mean_sample_missingness_rate": global_mean_sample_rate,
                "mean_sample_missingness_rate": global_mean_sample_rate,
                "median_sample_missingness_rate": global_median_sample_rate,
                "class_dependent_gap_weighted": class_dependent_gap_weighted,
                "sample_missing_top10_global": sample_missing_top10_global,
                "per_analysis": per_analysis,
                **_metric_missingness_definition(),
            },
            thresholds={"recommended_minimum": 0.85, "class_dependent_gap_warn": 0.1},
            recommendations=recommendations,
        )


class OutlierMetric(MetricPlugin):
    family = "Analytical QC"
    name = "outlier_burden"
    profiles = ("full",)

    def compute(self, study: CanonicalStudy) -> MetricResult:
        total_sample_summaries = 0
        total_sample_outliers = 0
        outlier_samples_all: list[dict[str, Any]] = []
        per_analysis: list[dict[str, Any]] = []

        for matrix in study.feature_matrices:
            if not matrix.values:
                continue
            count_zero = _source_kind_counts_zero(getattr(matrix, "source_kind", ""))
            arr = np.array(
                [[np.nan if _is_missing(value, count_zero=count_zero) else float(value) for value in row] for row in matrix.values],
                dtype=float,
            )
            if arr.size == 0:
                continue

            analysis_sample_outliers: list[str] = []

            # Sample-level outliers using per-sample median abundance.
            sample_medians: list[tuple[str, float]] = []
            for idx, sample_id in enumerate(matrix.sample_ids):
                row = arr[idx, :] if idx < arr.shape[0] else np.array([], dtype=float)
                finite = row[np.isfinite(row)]
                if finite.size == 0:
                    continue
                sample_medians.append((sample_id, float(np.median(finite))))

            sample_outliers = 0
            sample_total = len(sample_medians)
            if sample_total >= 4:
                sample_values = [value for _, value in sample_medians]
                q1 = percentile(sample_values, 0.25)
                q3 = percentile(sample_values, 0.75)
                if q1 is not None and q3 is not None:
                    iqr = q3 - q1
                    lower = q1 - 1.5 * iqr
                    upper = q3 + 1.5 * iqr
                    for sample_id, value in sample_medians:
                        if value < lower or value > upper:
                            sample_outliers += 1
                            analysis_sample_outliers.append(sample_id)
                            outlier_samples_all.append(
                                {
                                    "analysis_id": matrix.assay_id,
                                    "sample_id": sample_id,
                                    "sample_median": value,
                                    "lower": lower,
                                    "upper": upper,
                                }
                            )

            sample_score = 1.0 - (sample_outliers / sample_total) if sample_total else 1.0

            total_sample_summaries += sample_total
            total_sample_outliers += sample_outliers

            per_analysis.append(
                {
                    "analysis_id": matrix.assay_id,
                    "sample_outliers": sample_outliers,
                    "sample_total": sample_total,
                    "sample_outlier_rate": (sample_outliers / sample_total) if sample_total else 0.0,
                    "score": sample_score,
                    "outlier_samples": analysis_sample_outliers[:20],
                }
            )

        sample_component = 1.0 - (total_sample_outliers / total_sample_summaries) if total_sample_summaries else 1.0
        score = sample_component

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.9 else "warn",
            summary=(
                "Outlier burden is based on sample-level IQR outliers on per-sample "
                f"median intensities ({total_sample_outliers}/{total_sample_summaries})."
            ),
            details={
                "sample_outliers": total_sample_outliers,
                "sample_total": total_sample_summaries,
                "sample_component": sample_component,
                "formula": (
                    "Sample-level only: per-sample median intensity -> Tukey 1.5×IQR "
                    "fences across samples; score = 1 - sample_outlier_rate."
                ),
                "per_analysis": per_analysis,
                "outlier_samples_top50": outlier_samples_all[:50],
                "scoring_basis": "sample_level_only",
            },
            thresholds={"recommended_minimum": 0.9, "iqr_multiplier": 1.5},
            recommendations=[] if score >= 0.9 else ["Inspect outlier samples before training and verify no acquisition artifacts dominate."],
        )


class AssayComparabilityMetric(MetricPlugin):
    family = "Analytical QC"
    name = "assay_platform_comparability"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        assay_medians: dict[str, float] = {}
        per_analysis: list[dict[str, Any]] = []

        for matrix in study.feature_matrices:
            count_zero = _source_kind_counts_zero(getattr(matrix, "source_kind", ""))
            filtered_values: list[float] = []
            for row in matrix.values:
                for value in row:
                    if _is_missing(value, count_zero=count_zero):
                        continue
                    try:
                        numeric = float(value)
                    except (TypeError, ValueError):
                        continue
                    if not isfinite(numeric):
                        continue
                    if numeric <= 0:
                        continue
                    filtered_values.append(numeric)

            if filtered_values:
                log_median = float(np.median([log10(value) for value in filtered_values]))
                assay_medians[matrix.matrix_id] = log_median
                per_analysis.append(
                    {
                        "analysis_id": matrix.assay_id,
                        "log10_median": log_median,
                        "positive_values": len(filtered_values),
                    }
                )

        n_usable = len(assay_medians)
        spread: float | None = None

        if n_usable == 0:
            score = 0.0
            summary = "No usable positive values were found across analyses after missing/non-finite/non-positive filtering."
        elif n_usable == 1:
            score = 1.0
            summary = "Only one analysis had usable positive values; cross-analysis scale comparability is treated as pass by definition."
        else:
            medians = list(assay_medians.values())
            spread = max(medians) - min(medians)
            # Smooth, single-formula scoring:
            # score = 1 / (1 + spread), where spread is in log10 units.
            score = 1.0 / (1.0 + float(spread))
            if score >= 0.5:
                summary = "Analyses are broadly comparable in central value scale."
            else:
                summary = (
                    "Analyses show substantial differences in central value scale and may require separation or "
                    "harmonization before joint modeling."
                )

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.5 else "warn",
            summary=summary,
            details={
                "assay_log10_medians": assay_medians,
                "n_usable_analyses": n_usable,
                "spread_log10_median": spread,
                "per_analysis": per_analysis,
                "scoring_formula": "score = 1 / (1 + spread_log10_median)",
                "pass_threshold": 0.5,
            },
            thresholds={"recommended_minimum": 0.5},
            recommendations=[] if score >= 0.5 else ["Consider analyzing assays separately or applying scale harmonization before joint modeling."],
        )


class FeatureCorrelationMetric(MetricPlugin):
    family = "Analytical QC"
    name = "feature_correlation_burden"
    profiles = ("full",)
    # Safety guard for repository-scale batch runs. Full NxN correlation is O(F^2) memory.
    MAX_FEATURES_FOR_FULL_CORR = 12000
    # Additional guard before dense matrix construction.
    MAX_CELLS_FOR_CORR_INPUT = 20_000_000

    def compute(self, study: CanonicalStudy) -> MetricResult:
        feature_name_by_id: dict[str, str] = {}
        for ann in study.annotations:
            feature_id = str(getattr(ann, "feature_id", "") or "").strip()
            if not feature_id or feature_id in feature_name_by_id:
                continue
            candidates = [
                str(getattr(ann, "raw_name", "") or "").strip(),
                str(getattr(ann, "normalized_name", "") or "").strip(),
                str(getattr(ann, "mapped_reference_id", "") or "").strip(),
            ]
            for name in candidates:
                if not name:
                    continue
                if name.lower() in {"unknown", "na", "n/a", "none", "null"}:
                    continue
                feature_name_by_id[feature_id] = name
                break

        high_corr_pairs = 0
        sampled_pairs = 0
        skipped_large_analyses = 0
        per_analysis: list[dict[str, Any]] = []

        for matrix in study.feature_matrices:
            if not matrix.values or len(matrix.feature_ids) < 2:
                continue
            n_rows = len(matrix.values)
            first_row_len = len(matrix.values[0]) if matrix.values and matrix.values[0] is not None else 0
            n_cols_est = max(len(matrix.feature_ids), first_row_len)
            if n_cols_est > self.MAX_FEATURES_FOR_FULL_CORR:
                skipped_large_analyses += 1
                per_analysis.append(
                    {
                        "analysis_id": matrix.assay_id,
                        "high_correlation_pairs": 0,
                        "sampled_pairs": 0,
                        "high_correlation_rate": None,
                        "score": None,
                        "top_correlated_pairs": [],
                        "skipped": True,
                        "skip_reason": (
                            f"estimated_feature_count={n_cols_est} exceeds safe full-correlation limit "
                            f"({self.MAX_FEATURES_FOR_FULL_CORR})"
                        ),
                    }
                )
                continue
            if (n_rows * max(1, n_cols_est)) > self.MAX_CELLS_FOR_CORR_INPUT:
                skipped_large_analyses += 1
                per_analysis.append(
                    {
                        "analysis_id": matrix.assay_id,
                        "high_correlation_pairs": 0,
                        "sampled_pairs": 0,
                        "high_correlation_rate": None,
                        "score": None,
                        "top_correlated_pairs": [],
                        "skipped": True,
                        "skip_reason": (
                            f"matrix_size={n_rows}x{n_cols_est} exceeds safe input-cell limit "
                            f"({self.MAX_CELLS_FOR_CORR_INPUT})"
                        ),
                    }
                )
                continue
            count_zero = _source_kind_counts_zero(getattr(matrix, "source_kind", ""))
            arr = np.array(
                [[np.nan if _is_missing(value, count_zero=count_zero) else float(value) for value in row] for row in matrix.values],
                dtype=float,
            )
            if arr.shape[0] < 3:
                continue
            if arr.shape[1] > self.MAX_FEATURES_FOR_FULL_CORR:
                skipped_large_analyses += 1
                per_analysis.append(
                    {
                        "analysis_id": matrix.assay_id,
                        "high_correlation_pairs": 0,
                        "sampled_pairs": 0,
                        "high_correlation_rate": None,
                        "score": None,
                        "top_correlated_pairs": [],
                        "skipped": True,
                        "skip_reason": (
                            f"feature_count={arr.shape[1]} exceeds safe full-correlation limit "
                            f"({self.MAX_FEATURES_FOR_FULL_CORR})"
                        ),
                    }
                )
                continue

            local_high = 0
            local_pairs = 0
            top_corr_heap: list[tuple[float, int, int]] = []
            for idx in range(arr.shape[1]):
                column = arr[:, idx]
                mask = np.isnan(column)
                if mask.any():
                    fill_value = np.nanmean(column)
                    if np.isnan(fill_value):
                        fill_value = 0.0
                    column[mask] = fill_value
                    arr[:, idx] = column

            corr = np.corrcoef(arr, rowvar=False)
            for i in range(corr.shape[0]):
                for j in range(i + 1, corr.shape[1]):
                    local_pairs += 1
                    corr_ij = float(corr[i, j])
                    if not isfinite(corr_ij):
                        continue
                    abs_corr = abs(corr_ij)
                    if abs_corr >= 0.95:
                        local_high += 1
                        entry = (abs_corr, i, j)
                        if len(top_corr_heap) < 5:
                            heapq.heappush(top_corr_heap, entry)
                        elif abs_corr > top_corr_heap[0][0]:
                            heapq.heapreplace(top_corr_heap, entry)

            top_pairs: list[dict[str, Any]] = []
            for abs_corr, i, j in sorted(top_corr_heap, key=lambda item: item[0], reverse=True):
                feature_a = str(matrix.feature_ids[i]) if i < len(matrix.feature_ids) else f"f{i + 1}"
                feature_b = str(matrix.feature_ids[j]) if j < len(matrix.feature_ids) else f"f{j + 1}"
                top_pairs.append(
                    {
                        "feature_a": feature_a,
                        "feature_a_name": feature_name_by_id.get(feature_a, ""),
                        "feature_b": feature_b,
                        "feature_b_name": feature_name_by_id.get(feature_b, ""),
                        "abs_r": abs_corr,
                    }
                )

            high_corr_pairs += local_high
            sampled_pairs += local_pairs
            per_analysis.append(
                {
                    "analysis_id": matrix.assay_id,
                    "high_correlation_pairs": local_high,
                    "sampled_pairs": local_pairs,
                    "high_correlation_rate": (local_high / local_pairs) if local_pairs else 0.0,
                    "score": 1.0 - (local_high / local_pairs) if local_pairs else 1.0,
                    "top_correlated_pairs": top_pairs,
                }
            )

        if sampled_pairs:
            score = 1.0 - (high_corr_pairs / sampled_pairs)
        elif skipped_large_analyses > 0:
            # No analyzable pair matrix due to safety skip; keep metric informative but conservative.
            score = 0.5
        else:
            score = 1.0

        summary = f"Observed {high_corr_pairs}/{sampled_pairs} highly correlated sampled feature pairs."
        if skipped_large_analyses > 0:
            summary += f" Skipped {skipped_large_analyses} large analysis matrices for memory safety."

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.85 else "warn",
            summary=summary,
            details={
                "high_correlation_pairs": high_corr_pairs,
                "sampled_pairs": sampled_pairs,
                "skipped_large_analyses": skipped_large_analyses,
                "max_features_for_full_corr": self.MAX_FEATURES_FOR_FULL_CORR,
                "per_analysis": per_analysis,
            },
            thresholds={"absolute_correlation_cutoff": 0.95},
            recommendations=[] if score >= 0.85 else ["Collapse redundant features before benchmarking to reduce leakage and instability."],
        )


class NormalizationStatusMetric(MetricPlugin):
    family = "Analytical QC"
    name = "scale_diagnostics"
    informational = True  # scale diagnostics are interpretive guidance, not a scored readiness criterion

    NZV_MAD_REL_THRESHOLD = 1e-3

    @staticmethod
    def _classify(min_val: float, median_val: float, p90_val: float, max_val: float) -> tuple[str, float, str | None]:
        """Binary scale classification used for ML-readiness messaging.

        Returns (status_label, score, recommendation), where:
        - status_label: "raw" | "likely_transformed"
        - score: 0.0 (raw) or 1.0 (likely_transformed)
        """
        # Strong raw-count signature: large central tendency and wide upper tail.
        if min_val >= 0 and median_val >= 100 and p90_val >= 1000 and max_val >= 5000:
            return "raw", 0.0, "Values appear to be raw ion counts. Apply log transformation and normalization before ML."

        # Sparse/raw-like signature: very large dynamic range with high tail.
        if min_val >= 0 and max_val > 1000 and (p90_val > 100 or median_val > 10):
            return "raw", 0.0, "Values span a wide dynamic range (raw-like). Apply transformation/normalization before ML."

        # Everything else is treated as likely transformed/normalized for downstream use.
        # This intentionally groups log-transformed and peak-normalized outputs together.
        return "likely_transformed", 1.0, None

    def compute(self, study: CanonicalStudy) -> MetricResult:
        sample_lookup = _sample_record_lookup(study)
        assay_by_id = {a.assay_id: a for a in study.assays}
        # Build feature name lookup from annotations.
        feature_name_by_id: dict[str, str] = {}
        for ann in study.annotations:
            fid = str(getattr(ann, "feature_id", "") or "").strip()
            if not fid or fid in feature_name_by_id:
                continue
            for attr in ("raw_name", "normalized_name"):
                name = str(getattr(ann, attr, "") or "").strip()
                if name and name.lower() not in {"unknown", "na", "n/a", "none", "null"}:
                    feature_name_by_id[fid] = name
                    break
        per_analysis: list[dict[str, Any]] = []
        all_values: list[float] = []
        all_scores: list[float] = []
        recommendations: list[str] = []

        for matrix in study.feature_matrices:
            count_zero = _source_kind_counts_zero(getattr(matrix, "source_kind", ""))
            assay_rec = assay_by_id.get(matrix.assay_id)
            declared_units = ""
            if assay_rec is not None:
                declared_units = str((assay_rec.metadata or {}).get("units", "") or "").strip()

            # Collect values from biological samples only (QC/blank excluded).
            bio_values: list[float] = []
            for idx, row in enumerate(matrix.values):
                sample_id = matrix.sample_ids[idx] if idx < len(matrix.sample_ids) else f"row_{idx + 1}"
                label = ""
                sample_type = ""
                if isinstance(matrix.labels, dict):
                    label = str(matrix.labels.get(sample_id, "") or "").strip()
                sample_rec = sample_lookup.get(sample_id)
                if sample_rec is not None:
                    if not label:
                        label = str(sample_rec.label or "").strip()
                    sample_type = str(getattr(sample_rec, "sample_type", "") or "").strip()
                if not _is_biological_sample_row(sample_id, label, sample_type):
                    continue
                for value in row:
                    if not _is_missing(value, count_zero=count_zero):
                        try:
                            numeric = float(value)
                        except (TypeError, ValueError):
                            continue
                        if isfinite(numeric):
                            bio_values.append(numeric)

            if not bio_values:
                per_analysis.append(
                    {
                        "analysis_id": matrix.assay_id,
                        "declared_units": declared_units or "unknown",
                        "status": "no_numeric_values",
                        "score": 0.5,
                        "median": None,
                        "p90": None,
                        "max": None,
                        "min": None,
                        "n_values": 0,
                        "low_signal_feature_count": 0,
                        "low_signal_features_top20": [],
                    }
                )
                all_scores.append(0.5)
                continue

            arr = np.array(bio_values, dtype=float)
            median_val = float(np.median(arr))
            p90_val = float(np.percentile(arr, 90))
            max_val = float(np.max(arr))
            min_val = float(np.min(arr))
            status_label, score, rec = self._classify(min_val, median_val, p90_val, max_val)
            all_values.extend(bio_values)
            all_scores.append(score)

            # Low-signal feature flags (diagnostic, not scored).
            feature_p90: list[tuple[str, float]] = []
            feature_count = min(len(matrix.feature_ids), len(matrix.values[0]) if matrix.values else 0)
            near_zero_variance_features: list[dict[str, Any]] = []
            for fi in range(feature_count):
                col_vals = _finite_values(
                    [row[fi] for row in matrix.values if fi < len(row)],
                    count_zero=count_zero,
                )
                if not col_vals:
                    continue
                col_arr = np.array(col_vals, dtype=float)
                feature_p90.append((matrix.feature_ids[fi], float(np.percentile(col_arr, 90))))
                median_val_col = float(np.median(col_arr))
                mad = float(np.median(np.abs(col_arr - median_val_col)))
                scale = float(np.median(np.abs(col_arr))) + 1e-8
                mad_rel = mad / scale
                q75 = float(np.percentile(col_arr, 75))
                q25 = float(np.percentile(col_arr, 25))
                iqr = q75 - q25
                is_nzv = (mad_rel < self.NZV_MAD_REL_THRESHOLD) or (iqr == 0.0)
                if is_nzv:
                    near_zero_variance_features.append(
                        {
                            "feature_id": matrix.feature_ids[fi],
                            "feature_name": feature_name_by_id.get(matrix.feature_ids[fi], ""),
                            "mad_relative_variability": mad_rel,
                            "iqr": iqr,
                            "is_nzv": True,
                        }
                    )
            low_signal_features: list[dict[str, Any]] = []
            if feature_p90:
                threshold = float(np.percentile(np.array([item[1] for item in feature_p90], dtype=float), 10))
                for feature_id, p90_feature in feature_p90:
                    if p90_feature <= threshold:
                        low_signal_features.append({
                            "feature_id": feature_id,
                            "feature_name": feature_name_by_id.get(feature_id, ""),
                            "feature_p90": p90_feature,
                        })
                low_signal_features = sorted(low_signal_features, key=lambda item: float(item["feature_p90"]))
            else:
                threshold = None
            near_zero_variance_features = sorted(
                near_zero_variance_features,
                key=lambda item: (float(item["mad_relative_variability"]), float(item["iqr"])),
            )
            nzv_count = len(near_zero_variance_features)
            nzv_fraction = (nzv_count / feature_count) if feature_count > 0 else 0.0

            rec_parts: list[str] = []
            if rec:
                rec_parts.append(rec)
            if nzv_count > 0:
                rec_parts.append(
                    f"Detected {nzv_count}/{feature_count} near-zero-variance features; remove them before modeling."
                )
            if rec_parts:
                recommendations.append(" ".join(rec_parts))

            per_analysis.append(
                {
                    "analysis_id": matrix.assay_id,
                    "declared_units": declared_units or "unknown",
                    "status": status_label,
                    "score": score,
                    "median": median_val,
                    "p90": p90_val,
                    "max": max_val,
                    "min": min_val,
                    "log10_median_intensity": round(log10(median_val), 3) if median_val > 0 else None,
                    "n_values": int(arr.size),
                    "median_to_p90_ratio": (median_val / p90_val) if p90_val > 0 else None,
                    "low_signal_feature_threshold_p90": threshold,
                    "low_signal_feature_count": len(low_signal_features),
                    "low_signal_features_top20": low_signal_features[:20],
                    "near_zero_variance_feature_count": nzv_count,
                    "near_zero_variance_fraction": nzv_fraction,
                    "near_zero_variance_features_top20": near_zero_variance_features[:20],
                }
            )

        if not all_values:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=0.5,
                status="warn",
                summary="No numeric values found to assess normalization status.",
                details={"per_analysis": per_analysis},
                thresholds={},
                recommendations=["Verify feature matrices are populated with numeric abundance values."],
            )

        global_arr = np.array(all_values, dtype=float)
        global_median = float(np.median(global_arr))
        global_p90 = float(np.percentile(global_arr, 90))
        global_max = float(np.max(global_arr))
        global_min = float(np.min(global_arr))
        score = float(np.mean(all_scores)) if all_scores else 0.5
        status_label, _, _ = self._classify(global_min, global_median, global_p90, global_max)

        summary = (
            f"Normalization status: {status_label} "
            f"(median={global_median:.2f}, p90={global_p90:.2f}, max={global_max:.2f})."
        )

        dedup_recs = []
        seen: set[str] = set()
        for rec in recommendations:
            if rec not in seen:
                seen.add(rec)
                dedup_recs.append(rec)

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score > 0.5 else "warn",
            summary=summary,
            details={
                "status": status_label,
                "median": global_median,
                "p90": global_p90,
                "max": global_max,
                "min": global_min,
                "log10_median_intensity": round(log10(global_median), 3) if global_median > 0 else None,
                "has_negative_values": global_min < 0,
                "median_to_p90_ratio": (global_median / global_p90) if global_p90 > 0 else None,
                "per_analysis": per_analysis,
            },
            thresholds={},
            recommendations=dedup_recs,
        )


class FeatureLevelMissingnessMetric(MetricPlugin):
    family = "Analytical QC"
    name = "feature_level_missingness"
    profiles = ("full",)
    HIGH_MISSING_THRESHOLD = 0.3

    def compute(self, study: CanonicalStudy) -> MetricResult:
        feature_name_by_id: dict[str, str] = {}
        for ann in study.annotations:
            feature_id = str(getattr(ann, "feature_id", "") or "").strip()
            if not feature_id or feature_id in feature_name_by_id:
                continue
            candidates = [
                str(getattr(ann, "raw_name", "") or "").strip(),
                str(getattr(ann, "normalized_name", "") or "").strip(),
                str(getattr(ann, "mapped_reference_id", "") or "").strip(),
            ]
            for name in candidates:
                if not name:
                    continue
                if name.lower() in {"unknown", "na", "n/a", "none", "null"}:
                    continue
                feature_name_by_id[feature_id] = name
                break

        feature_missing_rates: list[float] = []
        high_missing = 0
        per_analysis: list[dict[str, Any]] = []
        high_missing_examples_all: list[dict[str, Any]] = []
        top_missing_examples_all: list[dict[str, Any]] = []

        for matrix in study.feature_matrices:
            if not matrix.values or not matrix.feature_ids:
                continue
            count_zero = _source_kind_counts_zero(getattr(matrix, "source_kind", ""))
            n_samples = len(matrix.values)
            matrix_rates: list[float] = []
            matrix_high_examples: list[dict[str, Any]] = []
            matrix_all_examples: list[dict[str, Any]] = []

            for fi in range(len(matrix.feature_ids)):
                vals = [row[fi] for row in matrix.values if fi < len(row)]
                n_missing = sum(1 for v in vals if _is_missing(v, count_zero=count_zero))
                rate = n_missing / n_samples if n_samples > 0 else 1.0
                feature_missing_rates.append(rate)
                matrix_rates.append(rate)
                record = {
                    "analysis_id": matrix.assay_id,
                    "feature_id": matrix.feature_ids[fi],
                    "feature_name": feature_name_by_id.get(matrix.feature_ids[fi], ""),
                    "missing_rate": rate,
                    "n_missing": n_missing,
                    "n_samples": n_samples,
                }
                matrix_all_examples.append(record)
                top_missing_examples_all.append(record)
                if rate > self.HIGH_MISSING_THRESHOLD:
                    high_missing += 1
                    matrix_high_examples.append(record)
                    high_missing_examples_all.append(record)

            mean_missing_matrix = float(np.mean(matrix_rates)) if matrix_rates else 1.0
            median_missing_matrix = float(np.median(matrix_rates)) if matrix_rates else 1.0
            per_analysis.append(
                {
                    "analysis_id": matrix.assay_id,
                    "n_total_features": len(matrix.feature_ids),
                    "n_high_missing_features": len(matrix_high_examples),
                    "mean_missingness_rate": mean_missing_matrix,
                    "median_missingness_rate": median_missing_matrix,
                    "score": 1.0 - mean_missing_matrix,
                    "high_missing_features_top20": sorted(
                        matrix_high_examples,
                        key=lambda item: (-float(item["missing_rate"]), str(item["feature_id"])),
                    )[:20],
                    "top_missing_features_top20": sorted(
                        matrix_all_examples,
                        key=lambda item: (-float(item["missing_rate"]), str(item["feature_id"])),
                    )[:20],
                }
            )

        if not feature_missing_rates:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=0.0,
                status="warn",
                summary="No feature data to assess.",
                details={"per_analysis": per_analysis, **_metric_missingness_definition()},
                thresholds={},
                recommendations=[],
            )

        n_total = len(feature_missing_rates)
        mean_missing = float(np.mean(feature_missing_rates))
        median_missing = float(np.median(feature_missing_rates))
        score = 1.0 - mean_missing
        pct_high = high_missing / n_total if n_total > 0 else 0.0

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if pct_high < 0.1 else "warn",
            summary=(
                f"{high_missing}/{n_total} features have >{self.HIGH_MISSING_THRESHOLD:.0%} missingness "
                "(missing = empty/non-numeric or non-finite values after ingestion cleanup)."
            ),
            details={
                "n_total_features": n_total,
                "n_high_missing_features": high_missing,
                "mean_missingness_rate": mean_missing,
                "median_missingness_rate": median_missing,
                "pct_features_over_threshold": pct_high,
                "threshold": self.HIGH_MISSING_THRESHOLD,
                "per_analysis": per_analysis,
                "high_missing_features_top50": sorted(
                    high_missing_examples_all,
                    key=lambda item: (-float(item["missing_rate"]), str(item["feature_id"])),
                )[:50],
                "top_missing_features_top50": sorted(
                    top_missing_examples_all,
                    key=lambda item: (-float(item["missing_rate"]), str(item["feature_id"])),
                )[:50],
                **_metric_missingness_definition(),
            },
            thresholds={"high_missing_threshold": self.HIGH_MISSING_THRESHOLD},
            recommendations=[
                f"Drop or impute {high_missing} features with >{self.HIGH_MISSING_THRESHOLD:.0%} missingness before training."
            ] if high_missing > 0 else [],
        )


class MetabatchBatchAnnotationCompatibilityMetric(MetricPlugin):
    family = "Analytical QC"
    name = "metabatch_batch_annotation_compatibility"
    informational = True  # MetaBatch compatibility is reported context, not a readiness criterion

    @staticmethod
    def _factor_summary_for_matrix(
        matrix: Any,
        sample_lookup: dict[str, Any],
        factors_by_local: dict[str, dict[str, str]],
        factors_by_mb: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        sample_ids = [str(sample_id).strip() for sample_id in matrix.sample_ids if str(sample_id).strip()]
        n_samples = len(sample_ids)
        factor_values: dict[str, list[str]] = {}
        samples_with_any_factor = 0
        matched_from_endpoint = 0

        for sample_id in sample_ids:
            pairs = factors_by_local.get(sample_id) or factors_by_mb.get(sample_id)
            if pairs:
                matched_from_endpoint += 1
            else:
                pairs = _factor_pairs_for_sample(sample_lookup.get(sample_id))
            if pairs:
                samples_with_any_factor += 1
            for key, value in pairs.items():
                factor_values.setdefault(key, []).append(str(value or "").strip())

        factor_rows: list[dict[str, Any]] = []
        usable_factor_keys: list[str] = []
        technical_like_keys: list[str] = []
        usable_technical_like_keys: list[str] = []

        for key in sorted(factor_values, key=str.casefold):
            values = factor_values[key]
            usable_values = [
                value for value in values
                if value.strip() not in _METABATCH_MISSING_BATCH_VALUES
            ]
            distinct_values = sorted({value for value in usable_values}, key=str.casefold)
            coverage = (len(usable_values) / n_samples) if n_samples else 0.0
            n_distinct = len(distinct_values)
            high_cardinality = bool(n_samples and n_distinct > n_samples * 0.9)
            single_level = n_distinct < 2
            low_coverage = bool(n_samples and len(usable_values) < n_samples * 0.6)
            usable = bool(n_samples and not single_level and not high_cardinality and not low_coverage)
            factor_text_for_batch_scan = " ".join([key, *distinct_values])
            technical_like = _is_metabatch_technical_batch_like(factor_text_for_batch_scan)

            if usable:
                usable_factor_keys.append(key)
            if technical_like:
                technical_like_keys.append(key)
            if usable and technical_like:
                usable_technical_like_keys.append(key)

            factor_rows.append(
                {
                    "factor_key": key,
                    "n_values_present": len(usable_values),
                    "n_distinct_values": n_distinct,
                    "coverage": coverage,
                    "technical_batch_like": technical_like,
                    "metabatch_usable": usable,
                    "filtered_reason": (
                        "single_level" if single_level else
                        "high_cardinality" if high_cardinality else
                        "low_coverage" if low_coverage else
                        ""
                    ),
                    "example_values": distinct_values[:6],
                }
            )

        sample_factor_coverage = (samples_with_any_factor / n_samples) if n_samples else 0.0
        return {
            "analysis_id": matrix.assay_id,
            "n_samples": n_samples,
            "samples_with_factor_annotation": samples_with_any_factor,
            "sample_factor_coverage": sample_factor_coverage,
            "samples_matched_from_factors_endpoint": matched_from_endpoint,
            "n_factor_keys_total": len(factor_rows),
            "n_metabatch_usable_factor_keys": len(usable_factor_keys),
            "n_technical_batch_like_keys": len(technical_like_keys),
            "n_usable_technical_batch_like_keys": len(usable_technical_like_keys),
            "metabatch_compatible": bool(usable_factor_keys),
            "technical_batch_like_present": bool(technical_like_keys),
            "usable_factor_keys": usable_factor_keys[:20],
            "technical_batch_like_keys": technical_like_keys[:20],
            "usable_technical_batch_like_keys": usable_technical_like_keys[:20],
            "factor_rows": factor_rows[:50],
        }

    def compute(self, study: CanonicalStudy) -> MetricResult:
        sample_lookup = _sample_record_lookup(study)
        factors_by_local, factors_by_mb, factors_source = _load_workbench_factor_pairs(study)

        per_analysis = [
            self._factor_summary_for_matrix(matrix, sample_lookup, factors_by_local, factors_by_mb)
            for matrix in study.feature_matrices
        ]
        n_analyses = len(per_analysis)
        n_compatible = sum(1 for item in per_analysis if item["metabatch_compatible"])
        n_with_technical = sum(1 for item in per_analysis if item["technical_batch_like_present"])

        if n_analyses == 0:
            score = 0.0
        else:
            score = n_compatible / n_analyses

        if n_compatible:
            summary = (
                f"MetaBatch-style factor annotations are usable in {n_compatible}/{n_analyses} analyses; "
                f"explicit technical batch-like keys appear in {n_with_technical}/{n_analyses} analyses."
            )
        else:
            summary = "No Workbench factor column met MetaBatch-style usefulness filters for the active matrices."

        recommendations: list[str] = []
        if n_compatible and not n_with_technical:
            recommendations.append(
                "Usable Workbench factor columns were found, but none are explicitly technical batch/run/plate/order keys; treat them as covariates, not proven batch variables."
            )
        elif not n_compatible:
            recommendations.append(
                "Add explicit batch, run order, plate, injection sequence, or acquisition metadata if batch-effect diagnostics/correction are planned."
            )

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if n_compatible else "warn",
            summary=summary,
            details={
                "metabatch_compatible": n_compatible > 0,
                "technical_batch_like_present": n_with_technical > 0,
                "n_analyses_compatible": n_compatible,
                "n_analyses_with_technical_batch_like_keys": n_with_technical,
                "n_analyses_total": n_analyses,
                "factors_source": factors_source,
                "rules": {
                    "source": "MetaBatch/StdMW-style Workbench allfactors to batches.tsv filtering",
                    "drop_single_level": True,
                    "drop_high_cardinality_threshold": ">90% of samples have distinct values",
                    "drop_low_coverage_threshold": "<60% of samples have usable values",
                    "missing_values": sorted(_METABATCH_MISSING_BATCH_VALUES),
                },
                "per_analysis": per_analysis,
            },
            thresholds={
                "minimum_coverage": 0.6,
                "maximum_distinct_fraction": 0.9,
                "minimum_distinct_values": 2,
            },
            recommendations=recommendations,
        )
