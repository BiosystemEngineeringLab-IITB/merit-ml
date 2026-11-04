from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import date
from typing import Any

from merit.feature_names import classify_feature_name
from merit.metrics import metrics_for_profile
from merit.metrics.annotation import matrix_label_summaries
from merit.models import AssessmentReport, CanonicalStudy, MetricResult
from merit.serialization import compute_content_hash
from merit.manuscript_sample_rules import (
    ALIGNMENT_KEY,
    alignment_for_matrix,
    feature_to_sample_ratio,
    matrix_distinct_sample_ids,
    matrix_qc_flags,
    ratio_samples,
)
from merit.utils import sample_is_qc_like, sample_object_is_qc_like
from merit.version import __version__

_UNKNOWN_TERMS = {
    "unknown", "na", "n/a", "", "unidentified", "unnamed",
    "not identified", "unknown metabolite", "unknown compound",
    "unassigned", "unannotated", "noname", "no name",
}


SECTION_MAP = {
    "Structural": "schema_validation",
    "Metadata / FAIR": "metadata_readiness",
    "Analytical QC": "analytical_readiness",
    "Annotation / Interoperability": "annotation_readiness",
    "Cohort / Bias": "cohort_bias",
    "ML Task Readiness": "ml_readiness",
    "Class Separability": "class_separability",
    "Cross-Study Harmonization": "cross_study_harmonization",
}


EMPTY_SECTIONS = {
    "schema_validation": [],
    "metadata_readiness": [],
    "analytical_readiness": [],
    "annotation_readiness": [],
    "cohort_bias": [],
    "ml_readiness": [],
    "class_separability": [],
    "cross_study_harmonization": [],
}


def _annotation_tier(annotations: list) -> str:
    """Classify a list of MetaboliteAnnotationRecords as 'named', 'mixed', or 'unannotated'."""
    named = mz_rt = unannotated = 0
    for ann in annotations:
        raw = (ann.raw_name or "").strip()
        if not raw or raw.lower() in _UNKNOWN_TERMS:
            unannotated += 1
            continue

        feature_class = classify_feature_name(raw)
        if feature_class["is_mz_rt"]:
            mz_rt += 1
        elif feature_class["is_non_metabolite"]:
            unannotated += 1
        else:
            named += 1

    total = named + mz_rt + unannotated
    if total == 0:
        return "no_annotations"
    if named / total >= 0.7:
        return "named"
    if named > 0 and (named + mz_rt) / total >= 0.7:
        return "mixed"
    return "unannotated"


def summarize_study(study: CanonicalStudy) -> dict[str, Any]:
    feature_total = sum(len(matrix.feature_ids) for matrix in study.feature_matrices)
    value_total = sum(len(row) for matrix in study.feature_matrices for row in matrix.values)
    matrix_sample_ids = {
        str(sample_id).strip()
        for matrix in study.feature_matrices
        for sample_id in (matrix.sample_ids or [])
        if str(sample_id).strip()
    }
    sample_by_id = {
        str(sample.sample_id).strip(): sample
        for sample in study.samples
        if str(sample.sample_id).strip()
    }
    matrix_samples = [sample_by_id[sid] for sid in matrix_sample_ids if sid in sample_by_id]

    n_bio_samples = sum(
        1
        for sid in matrix_sample_ids
        if (
            not sample_object_is_qc_like(sample_by_id[sid])
            if sid in sample_by_id
            else not sample_is_qc_like(sample_id=sid)
        )
    )
    platforms = list({a.platform for a in study.assays if a.platform})
    polarities = list({a.polarity for a in study.assays if a.polarity})
    analysis_types: list[str] = []
    for assay in study.assays:
        analysis_block = assay.metadata.get("analysis", {}) if isinstance(assay.metadata, dict) else {}
        raw_type = str((analysis_block if isinstance(analysis_block, dict) else {}).get("analysis_type", "")).strip()
        if not raw_type:
            raw_type = str(assay.platform or "").strip()
        if not raw_type:
            continue
        for token in re.split(r"[;/,]+", raw_type):
            normalized = token.strip()
            if normalized and normalized not in analysis_types:
                analysis_types.append(normalized)
    if not analysis_types:
        raw_study_type = str(study.study.analysis_type or "").strip()
        for token in re.split(r"[;/,]+", raw_study_type):
            normalized = token.strip()
            if normalized and normalized not in analysis_types:
                analysis_types.append(normalized)
    analysis_type_label = "; ".join(analysis_types)

    # Pre-build annotation index by assay_id (feature_id format: "assay_id::fN")
    annotations_by_assay: dict[str, list] = {}
    for ann in study.annotations:
        aid = ann.feature_id.split("::")[0] if "::" in ann.feature_id else ann.feature_id
        annotations_by_assay.setdefault(aid, []).append(ann)

    # Class distribution shown in Overview:
    # prefer raw tabular class strings (from datatable/mwtab "Class"/"Factors"),
    # fallback to parsed sample labels only when class_string is absent.
    def _compact_class_string(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "|" in text:
            parts = [part.strip() for part in text.split("|") if part.strip()]
            if parts:
                return " | ".join(parts)
        return text

    bio_labels: list[str] = []
    normalized_to_display: dict[str, str] = {}
    for sample in matrix_samples:
        if sample_object_is_qc_like(sample):
            continue
        attrs = sample.attributes if isinstance(sample.attributes, dict) else {}
        raw_class = attrs.get("class_string") or sample.label
        display = _compact_class_string(raw_class)
        if not display:
            continue
        norm = re.sub(r"\s+", " ", display).strip().lower()
        if not norm:
            continue
        normalized_to_display.setdefault(norm, display)
        bio_labels.append(norm)

    _ARTIFACT_LABEL = re.compile(r"^factor\d+[_\s]\w+$", re.IGNORECASE)
    class_counts_raw: Counter[str] = Counter(
        lbl for lbl in bio_labels
        if lbl and lbl != "unknown" and not _ARTIFACT_LABEL.match(lbl)
    )
    class_counts = {
        normalized_to_display.get(label, label): count
        for label, count in class_counts_raw.most_common(20)
    }

    # Study dates
    raw_fields = study.study.raw_fields or {}
    submission_date = raw_fields.get("submission_date", "") or ""
    release_date = study.study.publication_date or raw_fields.get("release_date", "") or ""
    accessed_date = date.today().isoformat()
    # Compute study-level polarity label: if mix of positive+negative → "mixed"
    pol_set = {p.lower() for p in polarities}
    if "positive" in pol_set and "negative" in pol_set:
        polarity_label = "mixed"
    elif len(polarities) == 1:
        polarity_label = polarities[0]
    else:
        polarity_label = ", ".join(polarities) if polarities else ""
    tissues = list({s.organism_part or s.sample_type for s in matrix_samples if (s.organism_part or s.sample_type)})

    # Factors endpoint context (Workbench): expose keys and a compact example
    # so UI users can see what class labels were derived from.
    factor_key_set: set[str] = set()
    factor_examples: list[str] = []
    seen_factor_examples: set[str] = set()
    for sample in matrix_samples:
        attrs = sample.attributes if isinstance(sample.attributes, dict) else {}
        raw_factor = str(attrs.get("factor_string", "") or "").strip()
        if not raw_factor:
            continue
        parts: list[str] = []
        for token in raw_factor.split("|"):
            token = token.strip()
            if not token:
                continue
            if ":" in token:
                key, _, value = token.partition(":")
                key = key.strip()
                value = value.strip()
                if key:
                    factor_key_set.add(key)
                    parts.append(f"{key}:{value or '-'}")
                else:
                    parts.append(token)
            else:
                parts.append(token)
        compact = " | ".join(parts) if parts else raw_factor
        if compact and compact not in seen_factor_examples:
            seen_factor_examples.add(compact)
            factor_examples.append(compact)

    # Per-analysis breakdown: one entry per feature matrix / assay pair
    assay_by_id = {a.assay_id: a for a in study.assays}
    per_analysis: list[dict[str, Any]] = []
    # final label classes of every assessed matrix (manuscript rules); shared with the named-feature coverage metric
    try:
        label_summaries = {str(x.get("analysis_id")): x for x in matrix_label_summaries(study) if x.get("file_found")}
    except Exception:                                       # a source other than the Workbench dump has no deposited file to read
        label_summaries = {}
    for matrix in study.feature_matrices:
        assay = assay_by_id.get(matrix.assay_id)
        n_samples = len(matrix.sample_ids)
        n_features = len(matrix.feature_ids)
        n_values = sum(len(row) for row in matrix.values)
        n_missing = sum(1 for row in matrix.values for v in row if v is None)
        missing_rate = round(n_missing / n_values, 4) if n_values > 0 else 0.0
        # Value range for normalization hint
        flat = [v for row in matrix.values for v in row if v is not None]
        val_min = round(min(flat), 4) if flat else None
        val_max = round(max(flat), 4) if flat else None
        val_median = round(statistics.median(flat), 4) if flat else None

        reported_nm = ""
        units = ""
        analysis_id = matrix.assay_id
        analysis_type = ""
        polarity = ""
        platform = ""
        chromatography = ""
        chromatography_system = ""
        chromatography_column = ""
        ms_type = ""
        ms_instrument_type = ""
        ms_instrument_name = ""
        nmr_experiment_type = ""
        nmr_instrument_type = ""
        nmr_spectrometer_frequency = ""
        nmr_solvent = ""
        nmr_pulse_sequence = ""
        nmr_water_suppression = ""
        nmr_reference_compound = ""
        nmr_temperature = ""
        nmr_data_block = ""
        class_distribution: dict[str, int] = {}
        if assay:
            reported_nm = str(assay.metadata.get("reported_n_metabolites", ""))
            units = assay.metadata.get("units", "") or ""
            analysis_id = assay.metadata.get("analysis_id", "") or assay.assay_id
            analysis_type = assay.metadata.get("analysis_type", "") or assay.platform or ""
            polarity = assay.polarity or ""
            platform = assay.platform or ""
            chromatography = assay.metadata.get("chromatography_type", "") or ""
            chromatography_system = assay.metadata.get("chromatography_system", "") or ""
            chromatography_column = assay.metadata.get("chromatography_column", "") or ""
            ms_type = assay.metadata.get("ms_type", "") or ""
            ms_instrument_type = assay.metadata.get("ms_instrument_type", "") or ""
            ms_instrument_name = assay.metadata.get("ms_instrument_name", "") or ""
            nmr_experiment_type = assay.metadata.get("nmr_experiment_type", "") or ""
            nmr_instrument_type = assay.metadata.get("nmr_instrument_type", "") or ""
            nmr_spectrometer_frequency = assay.metadata.get("nmr_spectrometer_frequency", "") or ""
            nmr_solvent = assay.metadata.get("nmr_solvent", "") or ""
            nmr_pulse_sequence = assay.metadata.get("nmr_pulse_sequence", "") or ""
            nmr_water_suppression = assay.metadata.get("nmr_water_suppression", "") or ""
            nmr_reference_compound = assay.metadata.get("nmr_reference_compound", "") or ""
            nmr_temperature = assay.metadata.get("nmr_temperature", "") or ""
            nmr_data_block = assay.metadata.get("nmr_data_block", "") or ""
            class_distribution = assay.metadata.get("class_distribution", {}) or {}

        n_qc_blank = sum(matrix_qc_flags(matrix))
        alignment = alignment_for_matrix(study, matrix)
        n_aligned_not_qc = int(alignment.get("all_zero_absent_rows_not_qc_or_blank", 0) or 0)
        n_aligned_all = int(alignment.get("all_zero_rows_absent_from_mwtab_block", 0) or 0)
        alignment_summary = {
            "datatable_rows": int(alignment.get("datatable_rows", 0) or 0),
            "mwtab_block_samples": int(alignment.get("mwtab_block_samples", 0) or 0),
            "aligned_rows": int(alignment.get("all_zero_rows_absent_from_mwtab_block", 0) or 0),
            "aligned_rows_not_qc_blank": n_aligned_not_qc,
            "aligned_sample_ids": list(alignment.get("all_zero_absent_sample_ids", []) or []),
            "aligned_sample_ids_not_qc_blank": list(alignment.get("all_zero_absent_sample_ids_not_qc_or_blank", []) or []),
        } if alignment else {}
        # The Tier 1 sample alignment rows were removed from this matrix before assessment (workflow), so n_samples
        # and n_qc_blank already leave them out and nothing more is subtracted here.
        pn_ratio = feature_to_sample_ratio(n_features, n_samples, n_qc_blank) if n_features else None
        pn_positions = pn_ratio
        n_distinct_ids, n_distinct_not_qc = matrix_distinct_sample_ids(matrix)
        pn_scored = (n_features / n_distinct_not_qc) if n_features and n_distinct_not_qc > 0 else None

        # Annotation label of this analysis, from the same per-matrix result as the named-feature coverage metric
        # (label and context rules of the manuscript, Text S3 / Table S1; 2026-10-07). The label values are kept:
        #   named           coverage >= 70% (the earlier cut)        mixed        some named features, below 70%
        #   unannotated     eligible features, none named            no_annotations   no eligible feature
        label_summary = label_summaries.get(str(matrix.assay_id), {})
        n_named_features = int(label_summary.get("n_named", 0) or 0)
        n_eligible_features = int(label_summary.get("n_eligible", 0) or 0)
        named_coverage = (n_named_features / n_eligible_features) if n_eligible_features else None
        if named_coverage is None:
            ann_tier = "no_annotations"
        elif named_coverage >= 0.7:
            ann_tier = "named"
        elif n_named_features > 0:
            ann_tier = "mixed"
        else:
            ann_tier = "unannotated"

        per_analysis.append({
            "analysis_id": analysis_id,
            "file": matrix.source_file,
            "analysis_type": analysis_type,
            "platform": platform,
            "polarity": polarity,
            "chromatography": chromatography,
            "chromatography_system": chromatography_system,
            "chromatography_column": chromatography_column,
            "ms_type": ms_type,
            "ms_instrument_type": ms_instrument_type,
            "ms_instrument_name": ms_instrument_name,
            "nmr_experiment_type": nmr_experiment_type,
            "nmr_instrument_type": nmr_instrument_type,
            "nmr_spectrometer_frequency": nmr_spectrometer_frequency,
            "nmr_solvent": nmr_solvent,
            "nmr_pulse_sequence": nmr_pulse_sequence,
            "nmr_water_suppression": nmr_water_suppression,
            "nmr_reference_compound": nmr_reference_compound,
            "nmr_temperature": nmr_temperature,
            "nmr_data_block": nmr_data_block,
            "units": units,
            # sample rows of the assessed matrix; for datatable the Tier 1 sample alignment rows are removed
            "n_samples": n_samples,
            "n_features": n_features,
            # all deposited sample rows, and the Tier 1 sample alignment rows removed from them (datatable only)
            "n_samples_deposited": n_samples + n_aligned_all,
            "n_tier1_alignment_rows_removed": n_aligned_all,
            # QC/blank rows by the 16-keyword filter, among the rows of the assessed matrix and among all deposited rows
            "n_qc_blank_samples": n_qc_blank,
            "n_qc_blank_samples_deposited": n_qc_blank + (n_aligned_all - n_aligned_not_qc),
            "n_samples_not_qc_blank": n_samples - n_qc_blank,
            "tier1_sample_alignment": alignment_summary,
            # the manuscript's p/n: features / sample rows after QC/blank removal and Tier 1 sample alignment
            "n_samples_for_feature_ratio": ratio_samples(n_samples, n_qc_blank),
            "feature_to_sample_ratio": (round(pn_ratio, 4) if pn_ratio is not None else None),
            # the same ratio, kept under its earlier key (by matrix position, after QC/blank removal and alignment)
            "feature_to_sample_ratio_positions": (round(pn_positions, 4) if pn_positions is not None else None),
            # a sample ID can occupy several positions of one matrix
            "n_distinct_sample_ids": n_distinct_ids,
            "n_distinct_sample_ids_not_qc_blank": n_distinct_not_qc,
            # ratio that the feature_to_sample_ratio metric scores: features / distinct sample IDs not QC/blank
            "n_samples_for_scored_ratio": n_distinct_not_qc,
            "feature_to_sample_ratio_scored": (round(pn_scored, 4) if pn_scored is not None else None),
            "reported_n_metabolites": reported_nm,
            "missing_rate": missing_rate,
            "val_min": val_min,
            "val_max": val_max,
            "val_median": val_median,
            "class_distribution": class_distribution,
            "annotation_tier": ann_tier,
            "named_feature_coverage": named_coverage,
            "n_named_features": n_named_features,
            "n_eligible_features": n_eligible_features,
            "n_feature_labels_excluded": int(label_summary.get("n_excluded", 0) or 0),
        })

    # Optional legacy behavior: include metadata-only assays (no feature matrix)
    # in per_analysis. Keep default OFF so source-specific availability audits
    # and source tabs reflect only matrix-backed, actually scored analyses.
    if study.score_defaults.get("include_unscored_assays_in_summary", False):
        matrix_assay_ids = {m.assay_id for m in study.feature_matrices}
        for assay in study.assays:
            if assay.assay_id in matrix_assay_ids:
                continue
            aid = assay.metadata.get("analysis_id", "") or assay.assay_id
            per_analysis.append({
                "analysis_id": aid,
                "file": assay.name,
                "analysis_type": assay.metadata.get("analysis_type", "") or assay.platform or "",
                "platform": assay.platform or "",
                "polarity": assay.polarity or "",
                "chromatography": assay.metadata.get("chromatography_type", "") or "",
                "chromatography_system": assay.metadata.get("chromatography_system", "") or "",
                "chromatography_column": assay.metadata.get("chromatography_column", "") or "",
                "ms_type": assay.metadata.get("ms_type", "") or "",
                "ms_instrument_type": assay.metadata.get("ms_instrument_type", "") or "",
                "ms_instrument_name": assay.metadata.get("ms_instrument_name", "") or "",
                "nmr_experiment_type": assay.metadata.get("nmr_experiment_type", "") or "",
                "nmr_instrument_type": assay.metadata.get("nmr_instrument_type", "") or "",
                "nmr_spectrometer_frequency": assay.metadata.get("nmr_spectrometer_frequency", "") or "",
                "nmr_solvent": assay.metadata.get("nmr_solvent", "") or "",
                "nmr_pulse_sequence": assay.metadata.get("nmr_pulse_sequence", "") or "",
                "nmr_water_suppression": assay.metadata.get("nmr_water_suppression", "") or "",
                "nmr_reference_compound": assay.metadata.get("nmr_reference_compound", "") or "",
                "nmr_temperature": assay.metadata.get("nmr_temperature", "") or "",
                "nmr_data_block": assay.metadata.get("nmr_data_block", "") or "",
                "units": assay.metadata.get("units", "") or "",
                "n_samples": 0,
                "n_features": 0,
                "reported_n_metabolites": "",
                "missing_rate": None,
                "val_min": None,
                "val_max": None,
                "val_median": None,
                "class_distribution": {},
                "annotation_tier": "none",
                "no_feature_data": True,
            })

    return {
        "study_id": study.study.study_id,
        "title": study.study.title,
        "source": study.provenance.source,
        "description": study.study.description or "",
        "organism": study.study.organism or "",
        "disease": study.study.disease or "",
        "analysis_type": analysis_type_label,
        "analysis_types": analysis_types,
        "platform": study.study.platform or "",
        "platforms": platforms,
        "polarities": polarities,
        "polarity_label": polarity_label,
        "tissues": tissues[:10],
        "factor_variables": sorted(factor_key_set)[:12],
        "factor_example": factor_examples[0] if factor_examples else "",
        "factor_examples": factor_examples[:3],
        "publication_date": study.study.publication_date or "",
        "has_disease_endpoint": bool(study.study.disease and study.study.disease.strip()),
        "n_samples": len(matrix_sample_ids) if matrix_sample_ids else len(study.samples),
        "n_biological_samples": n_bio_samples,
        "n_assays": len(study.assays),
        "n_feature_matrices": len(study.feature_matrices),
        # Tier 1 sample alignment over the datatable matrices of this source (rows removed before assessment)
        "tier1_sample_alignment": {
            "n_analyses": sum(1 for item in per_analysis if (item.get("tier1_sample_alignment") or {}).get("aligned_rows", 0) > 0),
            "aligned_rows": sum((item.get("tier1_sample_alignment") or {}).get("aligned_rows", 0) for item in per_analysis),
            "aligned_rows_not_qc_blank": sum((item.get("tier1_sample_alignment") or {}).get("aligned_rows_not_qc_blank", 0) for item in per_analysis),
        } if study.score_defaults.get(ALIGNMENT_KEY) else {},
        "sample_rules": "manuscript_2026-10-06d",
        "n_features": feature_total,
        "n_values": value_total,
        "content_hash": study.provenance.content_hash,
        "per_analysis": per_analysis,
        # Dates & provenance
        "submission_date": submission_date,
        "release_date": release_date,
        "accessed_date": accessed_date,
        # Class distribution
        "class_counts": class_counts,
        "n_classes": len(class_counts_raw),
        "n_labeled_samples": sum(class_counts_raw.values()),
    }


def assess_study(
    study: CanonicalStudy,
    profile: str = "core",
    remediations_applied: list[dict[str, Any]] | None = None,
) -> AssessmentReport:
    grouped: dict[str, list[MetricResult]] = defaultdict(list)
    for metric in metrics_for_profile(profile):
        result = metric.compute(study)
        result.informational = getattr(metric, "informational", False)
        grouped[SECTION_MAP[result.family]].append(result)

    payload = {key: list(value) for key, value in EMPTY_SECTIONS.items()}
    payload.update(grouped)
    report = AssessmentReport(
        source={
            "repository": study.provenance.source,
            "study_id": study.study.study_id,
            "title": study.study.title,
            "connector": study.provenance.connector_name,
        },
        ingestion_summary=summarize_study(study),
        schema_validation=payload["schema_validation"],
        metadata_readiness=payload["metadata_readiness"],
        analytical_readiness=payload["analytical_readiness"],
        annotation_readiness=payload["annotation_readiness"],
        cohort_bias=payload["cohort_bias"],
        ml_readiness=payload["ml_readiness"],
        class_separability=payload["class_separability"],
        cross_study_harmonization=payload["cross_study_harmonization"],
        remediations_applied=remediations_applied or [],
        software_versions={
            "merit": __version__,
            "schema": study.schema_version,
            "parser": study.provenance.parser_version,
            "profile": profile,
        },
    )
    report.content_hash = compute_content_hash(report)
    return report
