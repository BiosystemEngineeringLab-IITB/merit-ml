"""Reviewed analytical context, kept separate from all readiness metrics.

This module copies evidence; it never classifies an instrument, searches text,
infers acquisition, or modifies a score. The reviewed TSV remains authoritative.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any


CONTEXT_KEY = "analytical_acquisition_context"
INDEX_FILENAME = "analysis_context_index.json"
SCHEMA = "merit.analytical_acquisition_context.v1"
MS2_FIRM = {"MS2 detected", "No MS2 reported; complete file-roster match"}

# Every reviewed column is assigned to one disclosure section, exactly once.
FIELD_GROUPS = {
    "Analysis context": (
        "study_id", "analysis_id", "modality", "analysis_type_field", "ms_type_field",
        "ion_mode_field", "chromatography_type_field",
    ),
    "Mass-analyser capability": (
        "resolution_class", "resolution_class_basis", "analyser_family", "class_from_type",
        "class_from_name", "instrument_type_field", "instrument_name_field",
        "resolution_setting_field", "mass_accuracy_field", "resolution_statement_in_text",
    ),
    "Acquisition wording": (
        "study_type_field", "project_type_field", "acquisition_wording_any_section",
        "acquisition_wording_analysis_sections", "acquisition_wording_level",
        "untargeted_stated_in_sections", "targeted_stated_in_sections",
        "targeted_word_any_sense_in_sections", "targeted_wording_in_type_field",
        "mrm_srm_prm_mentioned_analysis_sections", "mrm_srm_prm_mentioned_any_section",
        "ms2_or_dda_dia_mentioned_analysis_sections",
    ),
    "Scan-mode terms": (
        "reported_scan_mode_evidence", "scan_mode_terms_detected", "scan_mode_matched_fields",
        "scan_mode_excerpts",
    ),
    "Repository marker": (
        "mwtab_source", "datatable_source", "untarg_data_source", "results_file_line",
        "results_file_has_mz", "results_file_has_rt", "results_file_rt_units",
        "results_file_units", "results_file_units_declared", "datatable_pct_named_labels",
        "mwtab_pct_named_labels", "untarg_data_pct_named_labels", "untarg_data_label_type",
        "repository_marker",
    ),
    "Evidence inventory": ("items_detected", "acquisition_evidence_profile"),
    "MS2 in linked raw files": (
        "raw_file_names_in_mwtab", "redu_raw_files_matched", "raw_file_set_shared_with_another_analysis",
        "panredu_ms2_evidence_status", "panredu_ms2_link_scope", "panredu_ms2_raw_file_coverage_pct",
        "panredu_ms2_shared_raw_files", "panredu_ms2_analysis_shortlist",
    ),
    "Pan-ReDU: analysis-linked files": tuple("redu_analysis_" + suffix for suffix in (
        "files", "instrument_models", "resolution_class", "chromatography", "ionization",
        "acquisition_calls", "files_ms2_present", "files_no_ms2", "files_zero_count_unclassified",
        "files_not_determined", "ms2_status_strict", "ms2_status_lenient",
    )) + ("analyser_class_vs_redu_analysis",),
    "Pan-ReDU: study-wide files": ("redu_study_listed",) + tuple("redu_study_" + suffix for suffix in (
        "files", "instrument_models", "resolution_class", "chromatography", "ionization",
        "acquisition_calls", "files_ms2_present", "files_no_ms2", "files_zero_count_unclassified",
        "files_not_determined", "ms2_status_strict", "ms2_status_lenient",
    )) + ("analyser_class_vs_redu_study",),
}

TRACK_TIPS = {
    "Mass-analyser capability": "Primary classification from the declared instrument type and model only. HRMS-capable describes instrument capability, not the resolution used, and does not mean untargeted acquisition. Nominal/unit-resolution does not mean targeted acquisition.",
    "Acquisition wording": "Supporting text evidence from mwTab. Terms can occur in study or project descriptions rather than this analysis. Both means both wordings were found, not that both acquisition strategies were used. Negation is not resolved.",
    "Scan-mode terms": "Supporting terms in AN, CH and MS fields, excluding instrument type and name. Selected-mode: MRM/SRM, PRM or SIM. Other terms: full scan/MS1, DDA or DIA. A term can describe only part of the acquisition; no targeted/untargeted status is inferred.",
    "Repository marker": "A deposited results-file declaration and/or a retrieved valid untarg_data matrix. A declaration alone is not a valid matrix, and neither establishes acquisition type. Its m/z and RT attributes are not populated metabolite-annotation coverage.",
    "MS2 in linked raw files": "Supporting Pan-ReDU file evidence matched by study accession and normalized raw filename. Detected requires a positive MS2 count. No MS2 reported requires every declared filename matched, every linked record labelled NO_MS2, and no shared filenames. Unknown and partial links remain separate. This does not establish acquisition type.",
}

FIELD_TIPS = {
    "resolution_class_basis": "How instrument type and name produced the class, including agreement, a model-resolved TOF, missing information or conflicting declarations.",
    "class_from_type": "Intermediate reading of MS:INSTRUMENT_TYPE only: HR, NOM, TOF, MIXED or IND. This is not the final capability class.",
    "class_from_name": "Intermediate reading of MS:INSTRUMENT_NAME only: HR, NOM, MIXED or IND. This is not the final capability class.",
    "resolution_setting_field": "MS:RESOLUTION_SETTING as deposited; no actual resolving power is verified.",
    "mass_accuracy_field": "MS:MASS_ACCURACY as deposited; no measured accuracy is verified.",
    "resolution_statement_in_text": "First resolution-related numerical text fragment in MS/AN sections; not validated and not used to assign analyser capability.",
    "acquisition_wording_level": "Analysis sections means AN/CH/MS contained wording. Other sections only means applicability to this analysis was not established.",
    "targeted_word_any_sense_in_sections": "Loose keyword search retained for audit. It may refer to biological targeting, treatment or another non-acquisition meaning; not used as the displayed wording call.",
    "scan_mode_matched_fields": "First matching mwTab field for each term. Terms are matched within a field, not across field boundaries.",
    "scan_mode_excerpts": "Matched term with surrounding deposited text; shown for inspection, not a manual validation of acquisition.",
    "results_file_units": "Raw UNITS attribute on MS:MS_RESULTS_FILE or NM:NMR_RESULTS_FILE. Separate from the quantitative data-block UNITS declaration; preserved even when it is a placeholder.",
    "results_file_units_declared": "Results-file UNITS text after removing placeholder declarations, including a number alone. Describes the referenced file; does not prove abundance units in every untarg_data cell.",
    "panredu_ms2_raw_file_coverage_pct": "100 × distinct matched normalized raw filenames / distinct filenames declared in the mwTab factor block. Link coverage, not sample coverage or MS2 coverage. Blank means no raw-file list is declared; 0 means a list exists but no names match.",
    "redu_raw_files_matched": "Number of matched Pan-ReDU file records, not distinct filenames. Several records or folders can match the same normalized name.",
    "raw_file_names_in_mwtab": "Distinct normalized RAW_FILE_NAME entries in SUBJECT_SAMPLE_FACTORS. These are declared filenames, not a count of biological samples.",
    "raw_file_set_shared_with_another_analysis": "Another analysis declares exactly the same filename set. The stricter MS2 link-scope flag also detects partial overlap.",
    "panredu_ms2_shared_raw_files": "Distinct matched filenames also declared by another analysis in this study. Shared-file evidence is withheld from a firm per-analysis MS2 call.",
    "panredu_ms2_link_scope": "Analysis-specific filename match, shared across analyses, or no matching files. Folder names are not used in matching; folder attribution may remain ambiguous.",
    "panredu_ms2_analysis_shortlist": "Conservative analysis-level evidence. A partial no-MS2 result is not complete absence; shared and unresolved files are not translated into an analysis-level absence call.",
    "acquisition_evidence_profile": "Description of acquisition wording plus repository-marker presence only. Analyser class, scan-mode terms and MS2 evidence are not inputs. This is not an acquisition type.",
    "items_detected": "Inventory of wording, repository, label and Pan-ReDU observations. Items do not assign an acquisition type.",
}


def field_label(field: str) -> str:
    labels = {
        "resolution_class": "Analyser capability", "resolution_class_basis": "Assignment basis",
        "analyser_family": "Analyser family", "instrument_type_field": "Declared instrument type",
        "instrument_name_field": "Declared instrument model", "acquisition_wording_level": "Wording scope",
        "panredu_ms2_analysis_shortlist": "Analysis-level MS2 evidence",
        "panredu_ms2_raw_file_coverage_pct": "Declared raw filenames matched (%)",
        "results_file_units": "Raw results-file units", "results_file_units_declared": "Declared results-file units",
    }
    return labels.get(field, field.removeprefix("redu_analysis_").removeprefix("redu_study_").replace("_", " ").capitalize())


def field_tip(field: str) -> str:
    if field in FIELD_TIPS:
        return FIELD_TIPS[field]
    if field.endswith("_ms2_status_lenient"):
        return "Legacy lenient summary: zero-count unclassified mzML/mzXML files are also treated as absent. Not used for the displayed conservative analysis-level MS2 assessment."
    if field.endswith("_ms2_status_strict"):
        return "Legacy strict summary: positive MS2 counts indicate present and NO_MS2 labels indicate absent. The newer evidence status additionally distinguishes unresolved files."
    if field.startswith("redu_study_"):
        return "Pan-ReDU evidence across all exported files of the study. Applicability to this individual analysis was not established."
    if field.startswith("redu_analysis_"):
        return "Pan-ReDU evidence from files matched to this analysis by study accession and normalized filename. Shared names and folder ambiguity can limit attribution."
    if field.endswith("_pct_named_labels"):
        return "Percentage of eligible feature labels classified as reported metabolite names in this source. A blank does not mean zero coverage."
    if field.endswith("_source"):
        return "Frozen source availability: valid matrix, file present but invalid/empty, or no file. Availability is not an acquisition label."
    return "Reviewed evidence field: " + field + ". Empty entries mean that no value was recorded by the extraction rules."


def coverage(records: list[dict[str, str]]) -> dict[str, Any]:
    ms = [r for r in records if r["modality"] == "MS"]
    counts = {key: dict(sorted(Counter(r[key] for r in ms).items())) for key in (
        "resolution_class", "acquisition_wording_any_section", "reported_scan_mode_evidence",
        "repository_marker", "panredu_ms2_analysis_shortlist", "acquisition_evidence_profile",
    )}
    numerators = {
        "analyser_classified": sum(r["resolution_class"] in {"HRMS-capable", "Nominal/unit-resolution"} for r in ms),
        "acquisition_wording_detected": sum(bool(r["acquisition_wording_any_section"]) for r in ms),
        "scan_mode_terms_detected": sum(r["reported_scan_mode_evidence"] != "No scan-mode term detected" for r in ms),
        "repository_marker_present": sum(r["repository_marker"] != "none" for r in ms),
        "ms2_firm_analysis_evidence": sum(r["panredu_ms2_analysis_shortlist"] in MS2_FIRM for r in ms),
    }
    return {
        "records": len(records), "studies": len({r["study_id"] for r in records}),
        "ms_records": len(ms), "modality_counts": dict(Counter(r["modality"] for r in records)),
        "counts_ms_only": counts,
        "coverage_ms_only": {key: {"count": n, "denominator": len(ms), "percent": round(100*n/len(ms), 1) if ms else None} for key, n in numerators.items()},
    }


def read_reviewed_records(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fields = list(reader.fieldnames or [])
        records = list(reader)
    grouped = [field for group in FIELD_GROUPS.values() for field in group]
    if len(fields) != 84 or len(grouped) != len(set(grouped)) or set(fields) != set(grouped):
        raise ValueError("Reviewed TSV schema differs from the documented 84-field context schema")
    keys = [(r["study_id"], r["analysis_id"]) for r in records]
    if len(keys) != len(set(keys)) or any(None in r.values() or None in r for r in records):
        raise ValueError("Duplicate record keys or malformed reviewed evidence rows")
    return fields, sorted(records, key=lambda r: (r["study_id"], r["analysis_id"]))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=4)
def _read_index(path: str, modified_ns: int) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def context_for_study(cache_root: Path, study_id: str) -> dict[str, Any] | None:
    """Preserve reviewed context during future local v8 cache builds."""
    path = cache_root / INDEX_FILENAME
    if not path.is_file():
        return None
    index = _read_index(str(path.resolve()), path.stat().st_mtime_ns)
    records = index.get("studies", {}).get(study_id.upper())
    if records is None:
        return None
    return {"schema": SCHEMA, "informational": True, "study_id": study_id.upper(),
            "provenance": index["provenance"], "records": records}
