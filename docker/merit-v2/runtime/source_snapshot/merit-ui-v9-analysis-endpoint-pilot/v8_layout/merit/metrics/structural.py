from __future__ import annotations

from collections import Counter

from merit.manuscript_sample_rules import matrix_qc_flags
from merit.models import CanonicalStudy, MetricResult
from merit.utils import sample_is_qc_like, sample_object_is_qc_like

from .base import MetricPlugin


# Tokens that mean "no subject ID" in the Subject ID field of SUBJECT_SAMPLE_FACTORS
_MISSING_SUBJECT_IDS = {"", "-", "na", "n/a", "none", "null", "unknown", "not applicable"}

# Where the identifiers of the repeated-identifier metric come from (shown in the UI)
SAMPLE_ID_SOURCE = (
    "Sample identifiers as deposited in the assessed matrix: first column of the datatable and untarg_data rows; "
    "header cells of the mwTab data block. Compared exactly (case-sensitive) after trimming surrounding whitespace."
)
SUBJECT_ID_SOURCE = (
    "Subject ID field (first field) of the SUBJECT_SAMPLE_FACTORS lines of the mwTab file, matched to the sample "
    "identifier exactly. Shown as deposited; not verified as subjects and not used in any score."
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


def _subject_ids_as_deposited(samples: list[object]) -> dict[str, object]:
    """Informational summary of the deposited Subject ID field over the given samples. Never used in a score."""
    by_subject: dict[str, list[str]] = {}
    n_with = 0
    for sample in samples:
        attrs = getattr(sample, "attributes", {}) or {}
        subject_id = str((attrs.get("subject_id", "") if isinstance(attrs, dict) else "") or "").strip()
        if subject_id.casefold() in _MISSING_SUBJECT_IDS:
            continue
        n_with += 1
        by_subject.setdefault(subject_id, []).append(str(getattr(sample, "sample_id", "") or "").strip())
    shared = {subject_id: sorted(ids) for subject_id, ids in by_subject.items() if len(ids) > 1}
    return {
        "n_samples": len(samples),
        "n_samples_with_subject_id": n_with,
        "n_subject_ids": len(by_subject),
        "n_subject_ids_shared_by_several_samples": len(shared),
        "n_samples_sharing_a_subject_id": sum(len(ids) for ids in shared.values()),
        "one_subject_id_for_all_samples": len(by_subject) == 1 and n_with == len(samples) and n_with > 1,
        # examples only: up to 20 shared subject IDs, up to 6 sample IDs each
        "shared_subject_id_examples": {subject_id: ids[:6] for subject_id, ids in sorted(shared.items())[:20]},
        "source": SUBJECT_ID_SOURCE,
    }


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


# status of a metric that has nothing to score; such a metric is left out of its section mean
NOT_ASSESSABLE = "not_assessable"


class DuplicateEntityMetric(MetricPlugin):
    """Repeated sample identifiers within a matrix (metric key kept as ``duplicate_entities``).

    Rewritten 2026-10-07. A position is one sample row of a datatable or untarg_data matrix, or one sample column of an
    mwTab data block. Inside each assessed matrix, the ML-eligible positions (not QC/blank; datatable Tier 1 sample
    alignment rows are already removed) are counted per deposited sample identifier.

        score of an analysis = distinct sample identifiers / ML-eligible positions of its matrix
        score of the source  = mean of the analysis scores (every analysis counts equally)

    (100 when every position has its own identifier, 50 when every sample is listed twice, 33 when three times). An
    analysis without an ML-eligible position has no score and is left out of the mean; when no analysis has one the
    metric is "not_assessable" and is left out of the Structural section mean (readiness_score._mean_score). Until
    2026-10-07 16:00 both numbers were pooled over the matrices of the source instead of averaged per analysis.
    An identifier used in two different analyses is not a repeat. The score
    describes suitability for ordinary row-wise splitting: with repeated identifiers a row-wise split can put positions
    of one identifier in both the training and the test fold. A repeat does not establish biological duplication.
    Feature names are not part of this metric (see the annotation metrics). The deposited Subject ID field is reported
    for information only.
    """

    family = "Structural"
    name = "duplicate_entities"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        per_analysis: list[dict[str, object]] = []
        n_eligible = n_once = n_repeated_ids = n_distinct = 0
        for matrix in study.feature_matrices:
            ids = [str(x).strip() for x in (matrix.sample_ids or [])]
            eligible = [sid for sid, is_qc in zip(ids, matrix_qc_flags(matrix)) if not is_qc]
            counts = Counter(eligible)
            once = sum(1 for sid in eligible if counts[sid] == 1)
            repeated = {sid: n for sid, n in counts.items() if n > 1}
            per_analysis.append({
                "analysis_id": matrix.assay_id,
                "n_eligible_positions": len(eligible),
                "n_sample_ids": len(counts),
                # distinct sample identifiers / ML-eligible positions of this matrix; None without such a position
                "score": (len(counts) / len(eligible)) if eligible else None,
                "n_positions_with_identifier_occurring_once": once,
                "n_repeated_sample_ids": len(repeated),
                "n_positions_of_repeated_sample_ids": len(eligible) - once,
                # examples only: up to 10 repeated identifiers with their number of positions
                "repeated_sample_id_examples": dict(sorted(repeated.items(), key=lambda kv: (-kv[1], kv[0]))[:10]),
            })
            n_eligible += len(eligible)
            n_once += once
            n_repeated_ids += len(repeated)
            n_distinct += len(counts)
        analysis_scores = [item["score"] for item in per_analysis if item["score"] is not None]
        assessable = bool(analysis_scores)
        score = (sum(analysis_scores) / len(analysis_scores)) if assessable else 0.0
        n_affected = sum(1 for item in per_analysis if item["n_repeated_sample_ids"])
        eligible_samples = [s for s in _matrix_backed_samples(study) if not sample_object_is_qc_like(s)]
        if not assessable:
            summary = "Not assessable: no analysis has an ML-eligible sample position."
        elif n_repeated_ids == 0:
            summary = f"All {n_eligible} ML-eligible positions have a sample identifier that occurs once in its matrix."
        else:
            summary = (
                f"{n_eligible} ML-eligible positions hold {n_distinct} sample identifiers; "
                f"{n_repeated_ids} identifiers are repeated in {n_affected} of {len(per_analysis)} analyses "
                f"(mean of {len(analysis_scores)} analysis scores)."
            )
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=NOT_ASSESSABLE if not assessable else ("pass" if n_repeated_ids == 0 else "warn"),
            summary=summary,
            details={
                "not_assessable": not assessable,
                "n_analyses_scored": len(analysis_scores),
                "n_analyses_without_eligible_position": len(per_analysis) - len(analysis_scores),
                "n_eligible_positions": n_eligible,
                "n_sample_ids": n_distinct,
                "n_positions_with_identifier_occurring_once": n_once,
                "n_repeated_sample_ids": n_repeated_ids,
                "n_analyses_with_repeated_sample_ids": n_affected,
                "per_analysis": per_analysis,
                "sample_id_source": SAMPLE_ID_SOURCE,
                "score_definition": (
                    "per analysis: distinct sample identifiers / ML-eligible positions of its matrix; source score: mean of "
                    "the analysis scores; an analysis without an ML-eligible position is left out"
                ),
                # informational only
                "subject_ids_as_deposited": _subject_ids_as_deposited(eligible_samples),
            },
            thresholds={"repeated_sample_ids": 0},
            recommendations=[] if n_repeated_ids == 0 else [
                "Keep all positions of a repeated sample identifier in the same cross-validation fold (grouped splitting). "
                "Average them only when their identity and measurement compatibility are established."
            ],
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
        # ML-eligible sample identifiers of the source. The deposited Subject ID field is informational only and no
        # longer reduces this count (2026-10-07): it cannot be verified as a subject without inference.
        n = n_rows = len(bio_samples)
        score = min(1.0, n / self.THRESHOLD)
        n_total_samples = len(matrix_ids) if matrix_ids else len(study.samples)
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
            },
            thresholds={"minimum_biological_samples": self.THRESHOLD},
            recommendations=[] if n >= self.THRESHOLD else [
                f"Fewer than {self.THRESHOLD} ML-eligible samples detected. ML models may be unreliable at this scale."
            ],
        )
