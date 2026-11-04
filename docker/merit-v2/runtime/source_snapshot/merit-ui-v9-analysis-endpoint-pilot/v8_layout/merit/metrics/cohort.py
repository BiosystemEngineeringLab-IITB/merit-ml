from __future__ import annotations

import re
from collections import Counter, defaultdict
from math import log, sqrt
from typing import Callable, Iterator

from merit.models import CanonicalStudy, MetricResult
from merit.utils import is_usable_class_label, normalize_label, sample_object_is_biological

from .base import MetricPlugin

_SEX_KEYS = {"sex", "gender", "biological_sex", "sex_at_birth"}
_AGE_KEYS = {
    "age",
    "age_years",
    "age_in_years",
    "age_yrs",
    "participant_age",
    "subject_age",
    "host_age",
    "chronological_age",
    "age_at_sampling",
    "age_at_collection",
}
_MALE_TERMS = {"male", "m", "man", "men"}
_FEMALE_TERMS = {"female", "f", "woman", "women"}
_OTHER_SEX_TERMS = {"other", "non_binary", "nonbinary", "intersex"}
_UNKNOWN_VALUE_TERMS = {
    "unknown",
    "na",
    "n_a",
    "none",
    "null",
    "missing",
    "not_available",
    "not_collected",
    "not_provided",
    "not_applicable",
    "nd",
    "n_d",
    "not_reported",
    "-",
}


def _is_biological_sample(sample: object) -> bool:
    return sample_object_is_biological(sample)


def _iter_factor_pairs(factor_string: str) -> Iterator[tuple[str, str]]:
    for part in str(factor_string or "").split("|"):
        token = part.strip()
        if not token or ":" not in token:
            continue
        key, _, value = token.partition(":")
        yield normalize_label(key), value.strip()


def _is_sex_key(key: str) -> bool:
    return normalize_label(key) in _SEX_KEYS


def _is_age_key(key: str) -> bool:
    key_norm = normalize_label(key)
    if key_norm in _AGE_KEYS:
        return True
    # Accept common variants such as "characteristics_age" or "age_group_years".
    return bool(re.search(r"(^|_)age(_|$)", key_norm))


def _extract_sample_value(sample: object, key_predicate: Callable[[str], bool]) -> str:
    attrs = getattr(sample, "attributes", {})
    if not isinstance(attrs, dict):
        return ""

    for key, val in attrs.items():
        if str(key).strip().lower() == "factor_string":
            continue
        if key_predicate(str(key)):
            text = str(val).strip()
            if text:
                return text

    factor_str = str(attrs.get("factor_string", "") or "")
    for key_norm, value in _iter_factor_pairs(factor_str):
        if key_predicate(key_norm):
            text = str(value).strip()
            if text:
                return text
    return ""


def _is_present_metadata_value(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return normalize_label(text) not in _UNKNOWN_VALUE_TERMS


def _biological_label_counts(study: CanonicalStudy) -> Counter[str]:
    return Counter(
        normalize_label(sample.label)
        for sample in study.samples
        if _is_biological_sample(sample) and is_usable_class_label(sample.label)
    )


class ClassBalanceMetric(MetricPlugin):
    family = "Cohort / Bias"
    name = "class_balance"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        counts = _biological_label_counts(study)
        if not counts:
            score = 0.0
        elif len(counts) == 1:
            score = 0.25
        else:
            score = min(counts.values()) / max(counts.values())
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.4 else "warn",
            summary=f"Class balance score is {score:.3f} across {len(counts)} labeled groups.",
            details={"counts": dict(counts)},
            thresholds={"recommended_minimum": 0.4},
            recommendations=[] if score >= 0.4 else ["Rebalance or stratify the cohort before training predictive models."],
        )


class GroupSizeSupportMetric(MetricPlugin):
    family = "Cohort / Bias"
    name = "group_size_support"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        counts = _biological_label_counts(study)
        n_classes = len(counts)
        if n_classes < 2:
            score = 0.0
            min_n = 0
            summary = "Group-size support unavailable (fewer than 2 labeled classes)."
            recs = ["At least 2 labeled classes are required to assess class-size support."]
        else:
            min_n = min(counts.values())
            if min_n >= 20:
                score = 1.0
            elif min_n >= 10:
                score = 0.7
            elif min_n >= 5:
                score = 0.4
            else:
                score = 0.1
            summary = f"Smallest class has {min_n} samples across {n_classes} labeled groups."
            recs = [] if score >= 0.7 else [
                "Increase samples in the smallest class (target >=10, ideally >=20) for reliable modeling."
            ]

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.7 else "warn",
            summary=summary,
            details={
                "counts": dict(counts),
                "n_classes": n_classes,
                "min_group_size": min_n,
            },
            thresholds={
                "strong_support_min_n": 20,
                "moderate_support_min_n": 10,
                "weak_support_min_n": 5,
            },
            recommendations=recs,
        )


class LabelEntropyMetric(MetricPlugin):
    family = "Cohort / Bias"
    name = "label_entropy"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        counts = _biological_label_counts(study)
        n_classes = len(counts)
        total_samples = sum(counts.values())
        if n_classes < 2 or total_samples == 0:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=0.0,
                status="warn",
                summary="Label entropy unavailable (fewer than 2 labeled classes).",
                details={
                    "counts": dict(counts),
                    "n_classes": n_classes,
                    "total_samples": total_samples,
                    "entropy": 0.0,
                    "entropy_max": 0.0,
                    "entropy_norm": 0.0,
                },
                thresholds={"recommended_minimum": 0.7},
                recommendations=["Use labels with >=2 classes and enough samples per class."],
            )

        probs = [value / total_samples for value in counts.values() if value > 0]
        entropy = -sum(p * log(p) for p in probs)
        entropy_max = log(n_classes)
        entropy_norm = (entropy / entropy_max) if entropy_max > 0 else 0.0
        entropy_norm = max(0.0, min(1.0, entropy_norm))
        status = "pass" if entropy_norm >= 0.7 else "warn"

        recommendations = [] if status == "pass" else [
            "Class distribution is dominance-heavy; rebalance labels or use class-weighted training/evaluation."
        ]
        return MetricResult(
            family=self.family,
            name=self.name,
            score=entropy_norm,
            status=status,
            summary=(
                f"Normalized label entropy is {entropy_norm:.3f} across "
                f"{n_classes} classes ({total_samples} ML-eligible samples)."
            ),
            details={
                "counts": dict(counts),
                "n_classes": n_classes,
                "total_samples": total_samples,
                "entropy": entropy,
                "entropy_max": entropy_max,
                "entropy_norm": entropy_norm,
            },
            thresholds={"recommended_minimum": 0.7},
            recommendations=recommendations,
        )


class ConfoundingRiskMetric(MetricPlugin):
    family = "Cohort / Bias"
    name = "sample_type_confounding_risk"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        contingency: dict[str, Counter[str]] = defaultdict(Counter)
        label_totals: Counter[str] = Counter()
        marker_totals: Counter[str] = Counter()
        for sample in study.samples:
            if not _is_biological_sample(sample):
                continue
            label = normalize_label(sample.label)
            if label == "unknown":
                continue
            marker = normalize_label(sample.sample_type or sample.organism_part or "unknown")
            contingency[label][marker] += 1
            label_totals[label] += 1
            marker_totals[marker] += 1

        if not contingency:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=0.0,
                status="warn",
                summary="Sample-type confounding score is 0.000 (no labeled samples available).",
                details={"dominant_marker_fraction_by_label": {}, "cramers_v": 1.0},
                thresholds={"recommended_minimum": 0.6},
                recommendations=["Check whether sample source or acquisition strata are entangled with labels."],
            )

        dominance: dict[str, float] = {}
        for label, marker_counts in contingency.items():
            total_for_label = sum(marker_counts.values())
            dominance[label] = (max(marker_counts.values()) / total_for_label) if total_for_label else 0.0

        unique_markers = len(marker_totals)
        unique_labels = len(label_totals)
        n_total = sum(label_totals.values())

        # If there is only one sample-matrix marker across all classes, there is no
        # class-vs-matrix entanglement signal to penalize.
        if unique_markers <= 1:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=1.0,
                status="pass",
                summary="Sample-type confounding score is 1.000 (single matrix/source marker across all labels).",
                details={
                    "dominant_marker_fraction_by_label": dominance,
                    "cramers_v": 0.0,
                    "unique_labels": unique_labels,
                    "unique_markers": unique_markers,
                    "contingency": {label: dict(counter) for label, counter in contingency.items()},
                },
                thresholds={"recommended_minimum": 0.6},
                recommendations=[],
            )

        # Association-based confounding proxy using Cramer's V between class and marker.
        chi2 = 0.0
        for label, marker_counts in contingency.items():
            row_total = label_totals[label]
            for marker, col_total in marker_totals.items():
                observed = marker_counts.get(marker, 0)
                expected = (row_total * col_total / n_total) if n_total else 0.0
                if expected > 0:
                    chi2 += (observed - expected) ** 2 / expected
        denom = min(unique_labels - 1, unique_markers - 1)
        cramers_v = sqrt((chi2 / n_total) / denom) if n_total > 0 and denom > 0 else 0.0
        cramers_v = max(0.0, min(1.0, cramers_v))
        score = 1.0 - cramers_v
        status = "pass" if score >= 0.6 else "warn"

        recs = [] if status == "pass" else [
            "Check whether sample source or acquisition strata are entangled with labels."
        ]
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=f"Sample-type confounding score is {score:.3f} (Cramer's V={cramers_v:.3f}).",
            details={
                "dominant_marker_fraction_by_label": dominance,
                "cramers_v": cramers_v,
                "unique_labels": unique_labels,
                "unique_markers": unique_markers,
                "contingency": {label: dict(counter) for label, counter in contingency.items()},
            },
            thresholds={"recommended_minimum": 0.6},
            recommendations=recs,
        )


class AgeBiologicalSexMetadataMetric(MetricPlugin):
    family = "Cohort / Bias"
    name = "age_biological_sex_metadata"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        bio_samples = [sample for sample in study.samples if _is_biological_sample(sample)]
        if not bio_samples:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=0.0,
                status="warn",
                summary="No ML-eligible samples found for age/sex metadata coverage.",
                details={
                    "n_biological_samples": 0,
                    "age_present": 0,
                    "sex_present": 0,
                    "age_coverage": 0.0,
                    "sex_coverage": 0.0,
                },
                thresholds={"recommended_minimum": 0.8},
                recommendations=[
                    "Ensure ML-eligible samples are correctly labeled (exclude QC/blank rows from cohort metrics).",
                ],
            )

        age_present = 0
        sex_present = 0
        for sample in bio_samples:
            age_val = _extract_sample_value(sample, _is_age_key)
            sex_val = _extract_sample_value(sample, _is_sex_key)
            if _is_present_metadata_value(age_val):
                age_present += 1
            if _is_present_metadata_value(sex_val):
                sex_present += 1

        n_bio = len(bio_samples)
        age_cov = age_present / n_bio
        sex_cov = sex_present / n_bio

        # When the repository provides no demographic metadata at all, assign a
        # neutral score (0.5) so that the absence of repository-level fields
        # does not dominate the cohort section.  A score below 0.5 is reserved
        # for studies that *have* partial demographic metadata (showing the data
        # was attempted but is incomplete).
        if age_present == 0 and sex_present == 0:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=0.5,
                status="warn",
                summary=f"No age or biological sex metadata found across {n_bio} ML-eligible samples.",
                details={
                    "n_biological_samples": n_bio,
                    "age_present": 0,
                    "sex_present": 0,
                    "age_coverage": 0.0,
                    "sex_coverage": 0.0,
                    "scoring_note": "Neutral score (0.5) assigned because the repository does not provide demographic fields; this is an infrastructure gap, not a study design flaw.",
                },
                thresholds={"recommended_minimum": 0.8},
                recommendations=[
                    "No age or biological sex metadata found. Demographic covariates cannot be assessed for confounding."
                ],
            )

        score = (age_cov + sex_cov) / 2
        status = "pass" if score >= 0.8 else "warn"

        recommendations: list[str] = []
        if age_cov < 0.8:
            recommendations.append(
                f"Age metadata is present for only {age_present}/{n_bio} ML-eligible samples; add age or age-bin factors."
            )
        if sex_cov < 0.8:
            recommendations.append(
                f"Biological sex metadata is present for only {sex_present}/{n_bio} ML-eligible samples; add sex-at-birth or biological sex fields."
            )

        summary = (
            f"Age coverage: {age_present}/{n_bio} ({age_cov:.0%}); "
            f"biological sex coverage: {sex_present}/{n_bio} ({sex_cov:.0%})."
        )
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=summary,
            details={
                "n_biological_samples": n_bio,
                "age_present": age_present,
                "sex_present": sex_present,
                "age_coverage": round(age_cov, 4),
                "sex_coverage": round(sex_cov, 4),
            },
            thresholds={"recommended_minimum": 0.8},
            recommendations=recommendations,
        )


class BiologicalSexDistributionMetric(MetricPlugin):
    family = "Cohort / Bias"
    name = "biological_sex_distribution"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        male = female = other = unknown = 0
        for sample in study.samples:
            if not _is_biological_sample(sample):
                continue
            sex_val = _extract_sample_value(sample, _is_sex_key).strip().lower()
            if not _is_present_metadata_value(sex_val):
                unknown += 1
                continue

            sex_norm = normalize_label(sex_val)
            if sex_norm in _MALE_TERMS:
                male += 1
            elif sex_norm in _FEMALE_TERMS:
                female += 1
            elif sex_norm in _OTHER_SEX_TERMS:
                other += 1
            else:
                unknown += 1

        total_bio = male + female + other + unknown
        known_binary = male + female
        if total_bio == 0 or known_binary == 0:
            score, status = 0.0, "warn"
            if total_bio == 0:
                summary = "No ML-eligible samples found for biological sex distribution."
            else:
                summary = "No binary biological sex information found in sample metadata."
            recs = ["Add biological sex as a factor variable to enable population bias detection."]
        else:
            single_sex = male == 0 or female == 0
            balance = min(male, female) / max(male, female) if max(male, female) > 0 else 0.0
            score = 0.5 + 0.5 * balance
            status = "warn" if single_sex else "pass"
            summary = f"Sex distribution: {male} male, {female} female, {other} other, {unknown} unknown."
            recs = ["Dataset appears single-sex. Findings may not generalise across biological sexes."] if single_sex else []

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=summary,
            details={"male": male, "female": female, "other": other, "sex_unknown": unknown},
            thresholds={},
            recommendations=recs,
        )


class SampleMatrixHomogeneityMetric(MetricPlugin):
    family = "Cohort / Bias"
    name = "sample_matrix_homogeneity"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        bio_samples = [s for s in study.samples if _is_biological_sample(s)]
        if not bio_samples:
            return MetricResult(
                family=self.family, name=self.name, score=0.0, status="warn",
                summary="No ML-eligible samples found.", details={}, thresholds={}, recommendations=[],
            )

        matrix_types: Counter[str] = Counter(
            normalize_label(s.organism_part or s.sample_type or "unknown")
            for s in bio_samples
        )
        n_distinct = len([k for k in matrix_types if k not in ("unknown", "")])
        dominant = max(matrix_types.values()) / len(bio_samples)
        score = dominant if n_distinct <= 1 else dominant * 0.7

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if n_distinct <= 1 or dominant >= 0.9 else "warn",
            summary=f"{n_distinct} distinct sample matrix type(s): {dict(matrix_types.most_common(5))}.",
            details={"matrix_types": dict(matrix_types), "n_distinct": n_distinct, "dominant_fraction": dominant},
            thresholds={"recommended_max_matrix_types": 1},
            recommendations=[
                "Mixed sample matrices (e.g. serum + plasma) can confound ML results. Subset to a single matrix type."
            ] if n_distinct > 1 and dominant < 0.9 else [],
        )
