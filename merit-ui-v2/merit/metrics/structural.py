from __future__ import annotations

from collections import Counter
import re

from merit.models import CanonicalStudy, MetricResult
from merit.utils import sample_is_qc_like, sample_object_is_qc_like

from .base import MetricPlugin


_MISSING_SUBJECT_IDS = {"", "-", "na", "n/a", "none", "null", "unknown", "not applicable"}
_SUBJECT_ID_PATTERN = re.compile(r"\b(patient|subject|donor|participant|volunteer|individual)\b\s*[-_: ]*\w+", re.I)
_GENERIC_SUBJECT_TERMS = (
    "quality control",
    "study sample",
    "blank",
    "pool",
    "nist",
    "reference",
    "standard",
)


def _matrix_sample_ids(study: CanonicalStudy) -> list[str]:
    sample_ids = {
        str(sample_id).strip()
        for matrix in study.feature_matrices
        for sample_id in (matrix.sample_ids or [])
        if str(sample_id).strip()
    }
    return sorted(sample_ids)


def _matrix_backed_samples(study: CanonicalStudy):
    sample_ids = set(_matrix_sample_ids(study))
    if not sample_ids:
        return list(study.samples)
    return [s for s in study.samples if str(getattr(s, "sample_id", "")).strip() in sample_ids]


def _subject_level_id(sample: object) -> str:
    attrs = getattr(sample, "attributes", {}) or {}
    if not isinstance(attrs, dict):
        return ""
    subject_id = str(attrs.get("subject_id", "") or "").strip()
    if subject_id.casefold() in _MISSING_SUBJECT_IDS:
        return ""
    folded = subject_id.casefold()
    if any(term in folded for term in _GENERIC_SUBJECT_TERMS):
        return ""
    if not _SUBJECT_ID_PATTERN.search(subject_id):
        return ""
    return subject_id


def _subject_repeat_summary(samples: list[object]) -> dict[str, object]:
    subject_to_samples: dict[str, list[str]] = {}
    for sample in samples:
        subject_id = _subject_level_id(sample)
        if not subject_id:
            continue
        sample_id = str(getattr(sample, "sample_id", "") or "").strip()
        subject_to_samples.setdefault(subject_id, []).append(sample_id)
    repeated = {
        subject_id: sorted(sample_ids)
        for subject_id, sample_ids in subject_to_samples.items()
        if len(sample_ids) > 1
    }
    repeated_occurrences = sum(len(sample_ids) - 1 for sample_ids in repeated.values())
    return {
        "subject_to_sample_ids": subject_to_samples,
        "repeated_subject_ids": repeated,
        "repeated_subject_occurrences": repeated_occurrences,
        "n_subject_level_ids": sum(len(sample_ids) for sample_ids in subject_to_samples.values()),
        "n_unique_subject_level_ids": len(subject_to_samples),
    }


def _effective_biological_sample_count(samples: list[object]) -> tuple[int, dict[str, object]]:
    summary = _subject_repeat_summary(samples)
    if int(summary["repeated_subject_occurrences"]) <= 0:
        return len(samples), summary
    n_missing_subject = len(samples) - int(summary["n_subject_level_ids"])
    return int(summary["n_unique_subject_level_ids"]) + max(0, n_missing_subject), summary


class SchemaIntegrityMetric(MetricPlugin):
    family = "Structural"
    name = "schema_integrity"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        required_checks = {
            "study_id": bool(study.study.study_id),
            "title": bool(study.study.title),
            "samples": len(study.samples) > 0,
            "assays": len(study.assays) > 0,
            "feature_matrices": len(study.feature_matrices) > 0,
        }
        passed = sum(1 for ok in required_checks.values() if ok)
        score = passed / len(required_checks)
        if score == 1.0:
            status = "pass"
        elif score >= 0.6:
            status = "warn"
        else:
            status = "fail"
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=f"{passed}/{len(required_checks)} core schema checks passed.",
            details={"checks": required_checks},
            thresholds={"pass_score": 1.0},
            recommendations=[] if score == 1.0 else ["Populate missing core study fields before benchmarking."],
        )


class CompletenessMetric(MetricPlugin):
    family = "Structural"
    name = "required_field_completeness"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        study_field_checks = {
            "title": bool(str(study.study.title or "").strip()),
            "description": bool(str(study.study.description or "").strip()),
            "organism": bool(str(study.study.organism or "").strip()),
            "disease": bool(str(study.study.disease or "").strip()),
            "analysis_type": bool(str(study.study.analysis_type or "").strip()),
            "platform": bool(str(study.study.platform or "").strip()),
        }
        study_present = sum(1 for ok in study_field_checks.values() if ok)
        study_score = study_present / len(study_field_checks) if study_field_checks else 0.0

        source_samples = _matrix_backed_samples(study)
        matrix_ids = _matrix_sample_ids(study)
        n_samples = len(matrix_ids) if matrix_ids else len(source_samples)
        label_cov = sum(1 for s in source_samples if str(s.label or "").strip())
        stype_cov = sum(1 for s in source_samples if str(s.sample_type or "").strip())
        org_cov = sum(1 for s in source_samples if str((s.organism or study.study.organism) or "").strip())
        sample_total = 3 * n_samples
        sample_present = label_cov + stype_cov + org_cov
        sample_score = sample_present / sample_total if sample_total else 0.0

        # Equal weight to study-level and sample-level completeness so that
        # missing study metadata (disease, organism, platform) is not drowned
        # out by high sample field coverage.
        score = 0.5 * study_score + 0.5 * sample_score

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.85 else "warn",
            summary=f"Study-level: {study_present}/{len(study_field_checks)}; sample-level: {sample_present}/{sample_total} fields populated.",
            details={
                "study_score": round(study_score, 4),
                "sample_score": round(sample_score, 4),
                "study_present": study_present,
                "study_total": len(study_field_checks),
                "sample_present": sample_present,
                "sample_total": sample_total,
                "study_field_checks": study_field_checks,
                "sample_field_coverage": {
                    "label": f"{label_cov}/{n_samples}",
                    "sample_type": f"{stype_cov}/{n_samples}",
                    "organism": f"{org_cov}/{n_samples}",
                },
            },
            thresholds={"recommended_minimum": 0.85},
            recommendations=[] if score >= 0.85 else ["Fill missing disease, sample type, and descriptive fields to improve comparability."],
        )


class DuplicateEntityMetric(MetricPlugin):
    family = "Structural"
    name = "duplicate_entities"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        source_samples = _matrix_backed_samples(study)
        sample_counts = Counter(sample.sample_id for sample in source_samples)
        duplicate_samples = {key: count for key, count in sample_counts.items() if count > 1}
        subject_summary = _subject_repeat_summary(source_samples)
        repeated_subject_occurrences = int(subject_summary["repeated_subject_occurrences"])
        duplicate_features: dict[str, int] = {}
        total_features = 0
        total_duplicate_features = 0
        for matrix in study.feature_matrices:
            feature_counts = Counter(matrix.feature_ids)
            total_features += len(matrix.feature_ids)
            dupes = {key: count for key, count in feature_counts.items() if count > 1}
            duplicate_features.update(dupes)
            total_duplicate_features += sum(count - 1 for count in dupes.values())
        exact_duplicate_occurrences = sum(count - 1 for count in duplicate_samples.values())
        total_duplicates = exact_duplicate_occurrences + total_duplicate_features + repeated_subject_occurrences
        denominator = max(1, len(source_samples) + total_features)
        score = max(0.0, 1.0 - (total_duplicates / denominator))
        if repeated_subject_occurrences:
            summary = (
                f"Found {exact_duplicate_occurrences + total_duplicate_features} duplicate sample/feature identifiers "
                f"and {repeated_subject_occurrences} repeated subject-level sample rows."
            )
        else:
            summary = f"Found {total_duplicates} duplicate sample/feature identifiers."
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if total_duplicates == 0 else "warn",
            summary=summary,
            details={
                "duplicate_samples": duplicate_samples,
                "duplicate_features": duplicate_features,
                "duplicate_sample_occurrences": exact_duplicate_occurrences,
                "duplicate_feature_occurrences": total_duplicate_features,
                "repeated_subject_ids": subject_summary["repeated_subject_ids"],
                "repeated_subject_occurrences": repeated_subject_occurrences,
                "n_subject_level_ids": subject_summary["n_subject_level_ids"],
                "n_unique_subject_level_ids": subject_summary["n_unique_subject_level_ids"],
            },
            thresholds={"max_duplicates": 0},
            recommendations=[] if total_duplicates == 0 else ["Deduplicate repeated identifiers before any train/test split."],
        )


class TabularDataAvailabilityMetric(MetricPlugin):
    family = "Structural"
    name = "tabular_data_availability"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        n_matrices = len(study.feature_matrices)
        n_with_data = sum(
            1 for m in study.feature_matrices
            if len(m.sample_ids) > 0 and len(m.feature_ids) > 0 and len(m.values) > 0
        )
        score = n_with_data / n_matrices if n_matrices > 0 else 0.0
        status = "pass" if n_with_data > 0 else "fail"
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=f"{n_with_data}/{n_matrices} assay matrices contain usable tabular data.",
            details={"n_matrices": n_matrices, "n_with_data": n_with_data},
            thresholds={"minimum_with_data": 1},
            recommendations=[] if n_with_data > 0 else [
                "No tabular feature data found. Check that *.datatable.tsv.gz, mwtab, or *_Results.txt files are present and parseable."
            ],
        )


class MinimumSampleThresholdMetric(MetricPlugin):
    family = "Structural"
    name = "minimum_sample_count"
    THRESHOLD = 20

    def compute(self, study: CanonicalStudy) -> MetricResult:
        sample_lookup = {
            str(getattr(s, "sample_id", "")).strip(): s
            for s in study.samples
            if str(getattr(s, "sample_id", "")).strip()
        }
        matrix_ids = _matrix_sample_ids(study)
        if matrix_ids:
            sample_rows = [(sid, sample_lookup.get(sid)) for sid in matrix_ids]
        else:
            sample_rows = [(str(getattr(s, "sample_id", "") or ""), s) for s in study.samples]
        bio_samples = []
        for sid, sample in sample_rows:
            if sample is not None:
                if not sample_object_is_qc_like(sample):
                    bio_samples.append((sid, sample))
                continue
            if not sample_is_qc_like(sample_id=sid):
                bio_samples.append((sid, sample))
        bio_sample_objects = [sample for _, sample in bio_samples if sample is not None]
        n_rows = len(bio_samples)
        n, subject_summary = _effective_biological_sample_count(bio_sample_objects)
        score = min(1.0, n / self.THRESHOLD)
        n_total_samples = len(matrix_ids) if matrix_ids else len(study.samples)
        if int(subject_summary["repeated_subject_occurrences"]) > 0:
            summary = (
                f"{n} independent biological units detected from {n_rows} ML-eligible rows "
                f"(threshold: {self.THRESHOLD})."
            )
        else:
            summary = f"{n} ML-eligible samples detected (threshold: {self.THRESHOLD})."
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if n >= self.THRESHOLD else "warn",
            summary=summary,
            details={
                "n_biological_samples": n,
                "n_biological_sample_rows": n_rows,
                "n_total_samples": n_total_samples,
                "threshold": self.THRESHOLD,
                "repeated_subject_occurrences": subject_summary["repeated_subject_occurrences"],
                "n_subject_level_ids": subject_summary["n_subject_level_ids"],
                "n_unique_subject_level_ids": subject_summary["n_unique_subject_level_ids"],
                "repeated_subject_ids": subject_summary["repeated_subject_ids"],
            },
            thresholds={"minimum_biological_samples": self.THRESHOLD},
            recommendations=[] if n >= self.THRESHOLD else [
                f"Fewer than {self.THRESHOLD} ML-eligible samples detected. ML models may be unreliable at this scale."
            ],
        )
