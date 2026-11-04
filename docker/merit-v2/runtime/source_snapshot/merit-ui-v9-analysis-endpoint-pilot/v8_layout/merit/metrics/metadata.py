from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from merit import deposited_raw_files
from merit import deposited_summaries
from merit import manuscript_feature_labels as ms_labels
from merit import manuscript_refmet as ms_refmet
from merit.feature_names import classify_feature_name
from merit.models import CanonicalStudy, MappingRecord, MetaboliteAnnotationRecord, MetricResult
from merit.utils import is_usable_class_label, normalize_label, sample_object_is_biological

from .base import MetricPlugin

def _is_biological_sample(sample: object) -> bool:
    return sample_object_is_biological(sample)


_STUDY_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "workbench": re.compile(r"^ST\d{6}$", re.IGNORECASE),
}
_UNKNOWN_IDENTIFIER_VALUES = {
    "",
    "-",
    "na",
    "n/a",
    "none",
    "unknown",
    "unknown metabolite",
    "unknown compound",
    "unidentified",
    "unassigned",
}
_TRUSTED_IDENTIFIER_NAMESPACES = {
    "refmet",
    "hmdb",
    "chebi",
    "kegg",
    "pubchem",
    "inchi",
    "inchikey",
    "metlin",
    "lipidmaps",
}


def _load_workbench_metabolites_rows(study: CanonicalStudy) -> list[dict[str, Any]] | None:
    source = (study.provenance.source or study.study.repository or "").strip().lower()
    if source != "workbench":
        return None
    study_id = str(study.study.study_id or "").strip().upper()
    source_root = str(study.provenance.source_root or "").strip()
    if not study_id or not source_root:
        return None
    path = Path(source_root).expanduser() / study_id / "metabolites.json"
    if not path.exists():
        return None
    try:
        json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    # One decoder for the three shapes of the cached file (2026-10-07): a list of records, a dictionary of records,
    # or one record object (32 studies have a single metabolite record). Before, a single record object was read
    # as "no records" and the metric fell back to the annotation-based path.
    return ms_labels.load_records(path)


_MASS_RT_REPOSITORY_PREVALENCE = {
    # manuscript Figure 3C (top): 1,310 both + 368 mass/mz only + 154 RT/RI only of 4,121 studies (was 1833 / 44.49)
    "present_studies": 1832,
    "total_studies": 4121,
    "present_percent": 44.46,
}

_MASS_RT_EMPTY_VALUES = {
    "",
    "-",
    "na",
    "n/a",
    "none",
    "null",
    "unknown",
}


def _normalise_mwtab_field_name(field_name: str) -> tuple[str, str]:
    raw = str(field_name or "").strip().lower()
    spaced = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    return spaced, compact


def _classify_mass_rt_like_field(field_name: str) -> str:
    """Conservative classifier for source-deposited mass, m/z, RT, or RI fields.

    The metric is intentionally named "mass/RT-like" rather than "m/z available"
    because MW users deposit a broad mix of field labels, including m/z-like
    coordinates, neutral/exact mass fields, retention time, and retention index.
    """
    spaced, compact = _normalise_mwtab_field_name(field_name)
    raw = str(field_name or "").strip().lower()
    if not spaced and not compact:
        return ""
    if spaced in {"ri type", "rt type", "moverz type", "mz type", "m z type", "formula type"}:
        return ""

    # m/z-like fields.  MW often serializes "m/z" as "moverz" or
    # "moverz_quant"; those are treated as m/z-like coordinate metadata.
    if compact in {"mz", "moverz", "moverzquant", "moverzratio", "masstocharge", "masstochargequant", "mrm"}:
        return "mz-like"
    if compact.endswith("mz") and "mzcloud" not in compact:
        return "mz-like"
    if re.search(r"(^|\b)m\s*/\s*z($|\b)", raw):
        return "mz-like"
    if re.search(r"\b(mz|m z)\b", spaced) or "mass to charge" in spaced:
        return "mz-like"
    if re.search(r"\b(precursor|fragment|product|parent|quantified|quant|target|observed|average)\s+(mz|m z|m/z)\b", spaced):
        return "mz-like"
    if re.search(r"\b(mz|m z|m/z)\s+(quant|ratio|value|observed|measured)\b", spaced):
        return "mz-like"
    if "precursor ion" in spaced or "product ion" in spaced or "fragment ion" in spaced or "parent ion" in spaced:
        return "mz-like"

    # Retention-time / retention-index fields.  Retention index is included
    # because it is the GC-MS analogue of RT-like chromatographic position.
    if compact in {
        "rt",
        "ri",
        "retentiontime",
        "retentionindex",
        "rettime",
        "rettimes",
        "rindex",
        "retindex",
        "medrt",
        "basert",
        "rtimes",
    }:
        return "rt-like"
    if re.search(r"(^|\b)r\.?t\.?($|\b)", raw):
        return "rt-like"
    if re.search(r"\b(rt|ri)\b", spaced):
        return "rt-like"
    if "retention time" in spaced or "retention index" in spaced or "ret time" in spaced:
        return "rt-like"
    if re.search(r"\b(base|median|med|mean|average)\s*rt\b", spaced):
        return "rt-like"

    invalid_tokens = {
        "spectrometry",
        "instrument",
        "platform",
        "method",
        "protocol",
        "ion mode",
        "polarity",
        "adduct",
        "formula",
        "smiles",
        "inchi",
        "inchikey",
        "kegg",
        "hmdb",
        "pubchem",
        "chebi",
        "compound name",
        "metabolite name",
        "metabolite",
        "name",
        "description",
        "pathway",
        "class",
        "cas",
        "ri type",
        "rt type",
        "moverz type",
        "formula type",
    }
    if any(token in spaced for token in invalid_tokens):
        return ""

    # Mass-like fields are not equivalent to an m/z coordinate, but they still
    # help reuse and reannotation, so they are included under the deliberately
    # cautious "mass-like" label.
    if compact in {
        "mass",
        "exactmass",
        "neutralmass",
        "molecularmass",
        "molecularweight",
        "monoisotopicmass",
        "averagemass",
        "formulaweight",
        "neutralmasses",
        "molecularweights",
        "calcmw",
        "calculatedmw",
    }:
        return "mass-like"
    if re.search(
        r"\b(exact|neutral|molecular|monoisotopic|average|calc(?:ulated)?|target|observed|measured|nominal|parent)\s+mass\b",
        spaced,
    ):
        return "mass-like"
    if re.search(r"\bmass\b", spaced) or "molecular weight" in spaced or "formula weight" in spaced:
        return "mass-like"
    if re.search(r"\b(m|mass)\s+meas", spaced) or re.search(r"\bmw\b", spaced):
        return "mass-like"

    # Narrow ambiguous coordinate-like labels retained from the manual audit.
    if compact in {"q1", "q3", "precursor", "product", "fragment", "parent", "transition", "compound", "identifier"}:
        return "ambiguous"
    return ""


def _mass_rt_value_is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _MASS_RT_EMPTY_VALUES
    return True


def _iter_mwtab_metabolite_blocks(payload: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    if not isinstance(payload, dict):
        return []
    blocks: list[tuple[str, list[dict[str, Any]]]] = []
    for block_name, block_payload in payload.items():
        if not isinstance(block_payload, dict):
            continue
        rows = block_payload.get("Metabolites")
        if isinstance(rows, list):
            blocks.append((str(block_name), [row for row in rows if isinstance(row, dict)]))
    return blocks


def _load_workbench_mass_rt_like_evidence(study: CanonicalStudy) -> dict[str, Any] | None:
    source = (study.provenance.source or study.study.repository or "").strip().lower()
    if source != "workbench":
        return None
    study_id = str(study.study.study_id or study.provenance.study_id or "").strip().upper()
    source_root = str(study.provenance.source_root or "").strip()
    if not study_id or not source_root:
        return None

    study_root = Path(source_root).expanduser() / study_id
    paths = sorted(study_root.glob("AN*/mwtab/*_mwtab.json"))
    if not paths:
        return {
            "present": False,
            "study_id": study_id,
            "source": "mwtab Metabolites metadata",
            "files_scanned": 0,
            "blocks_scanned": 0,
            "fields": [],
            "field_classes": {},
            "examples": [],
            "repository_prevalence": _MASS_RT_REPOSITORY_PREVALENCE,
        }

    fields: dict[str, str] = {}
    examples: list[dict[str, Any]] = []
    blocks_scanned = 0
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for block_name, rows in _iter_mwtab_metabolite_blocks(payload):
            blocks_scanned += 1
            for row in rows:
                for field_name, value in row.items():
                    field_class = _classify_mass_rt_like_field(field_name)
                    if not field_class or not _mass_rt_value_is_present(value):
                        continue
                    field_label = str(field_name)
                    fields.setdefault(field_label, field_class)
                    if len(examples) < 12:
                        examples.append(
                            {
                                "analysis_id": path.parent.parent.name,
                                "file": path.name,
                                "block": block_name,
                                "field_name": field_label,
                                "field_class": field_class,
                                "example_value": str(value)[:80],
                            }
                        )

    return {
        "present": bool(fields),
        "study_id": study_id,
        "source": "mwtab Metabolites metadata",
        "files_scanned": len(paths),
        "blocks_scanned": blocks_scanned,
        "fields": sorted(fields),
        "field_classes": {field: fields[field] for field in sorted(fields)},
        "examples": examples,
        "repository_prevalence": _MASS_RT_REPOSITORY_PREVALENCE,
    }




def _looks_like_known_identifier(value: str) -> bool:
    token = str(value or "").strip()
    if not token:
        return False
    lowered = token.lower()
    if lowered in _UNKNOWN_IDENTIFIER_VALUES:
        return False
    return True


def _infer_namespace(identifier: str) -> str:
    token = str(identifier or "").strip()
    lowered = token.lower()
    upper = token.upper()
    if lowered.startswith("refmet:"):
        return "refmet"
    if upper.startswith("HMDB"):
        return "hmdb"
    if upper.startswith("CHEBI:") or lowered.startswith("chebi:"):
        return "chebi"
    if re.match(r"^C\d{5}$", upper):
        return "kegg"
    if lowered.startswith("pubchem:") or lowered.startswith("cid:"):
        return "pubchem"
    if lowered.startswith("inchikey=") or re.match(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$", token):
        return "inchikey"
    if lowered.startswith("inchi="):
        return "inchi"
    if re.match(r"^lm[a-z]{2}\d+$", lowered):
        return "lipidmaps"
    return ""


def _namespace_identifier_candidates(
    annotation: MetaboliteAnnotationRecord,
    mapping: MappingRecord | None,
) -> list[tuple[str, str, str]]:
    pairs: list[tuple[str, str, str]] = []
    namespace_hint = (mapping.namespace or "").strip().lower() if mapping else ""
    candidates = [
        ("annotation.database_identifier", annotation.database_identifier),
        ("annotation.mapped_reference_id", annotation.mapped_reference_id),
        ("mapping.mapped_reference_id", mapping.mapped_reference_id if mapping else ""),
        ("mapping.raw_identifier", mapping.raw_identifier if mapping else ""),
    ]
    seen: set[tuple[str, str, str]] = set()
    for source_key, candidate in candidates:
        token = str(candidate or "").strip()
        if not _looks_like_known_identifier(token):
            continue
        inferred = _infer_namespace(token)
        namespace = namespace_hint or inferred
        pair = (source_key, namespace, token)
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return pairs


def _identifier_uri(namespace: str, identifier: str) -> str:
    ns = (namespace or "").strip().lower()
    token = str(identifier or "").strip()
    if not token:
        return ""

    if not ns:
        ns = _infer_namespace(token)

    if ns == "hmdb":
        hmdb = token.upper()
        if not hmdb.startswith("HMDB"):
            return ""
        return f"https://hmdb.ca/metabolites/{quote(hmdb)}"
    if ns == "chebi":
        digits = re.sub(r"^CHEBI:\s*", "", token, flags=re.IGNORECASE).strip()
        if not digits.isdigit():
            return ""
        return f"https://www.ebi.ac.uk/chebi/searchId.do?chebiId=CHEBI:{quote(digits)}"
    if ns == "kegg":
        code = token.upper()
        if not re.match(r"^C\d{5}$", code):
            return ""
        return f"https://www.kegg.jp/entry/{quote(code)}"
    if ns == "pubchem":
        cid = token
        cid = re.sub(r"^(pubchem:|cid:)\s*", "", cid, flags=re.IGNORECASE).strip()
        if not cid.isdigit():
            return ""
        return f"https://pubchem.ncbi.nlm.nih.gov/compound/{quote(cid)}"
    if ns == "lipidmaps":
        return f"https://www.lipidmaps.org/databases/lmsd/{quote(token)}"
    if ns == "inchikey":
        key = re.sub(r"^inchikey=\s*", "", token, flags=re.IGNORECASE).strip()
        if not re.match(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$", key):
            return ""
        return f"https://pubchem.ncbi.nlm.nih.gov/#query={quote(key)}"
    if ns == "inchi":
        inchi = re.sub(r"^inchi=\s*", "", token, flags=re.IGNORECASE).strip()
        if not inchi:
            return ""
        return f"https://pubchem.ncbi.nlm.nih.gov/#query={quote(inchi)}"
    if ns == "refmet":
        return (
            "https://www.metabolomicsworkbench.org/databases/refmet/"
            f"refmet_details.php?refmet_name={quote(token)}"
        )
    return ""


class FairStudyMetadataComplianceMetric(MetricPlugin):
    family = "Metadata / FAIR"
    name = "fair_study_metadata_compliance"

    # Minimum word count for the PROJECT_SUMMARY or the STUDY_SUMMARY to be considered substantive.
    _MIN_DESCRIPTION_WORDS = 20

    def compute(self, study: CanonicalStudy) -> MetricResult:
        rf = study.study.raw_fields or {}

        # --- F1: DOI registered ---
        # Metabolomics Workbench assigns dataset-level DOIs under the 10.21228/ prefix
        # when a study is submitted; a small fraction of studies instead carry a
        # publication DOI.  Either makes the dataset findable via a persistent resolver
        # independent of the MW accession number.
        # Source: PROJECT.DOI in mwtab — present in ~10% of MW studies.
        doi = str(rf.get("doi", "") or "").strip()
        has_doi = bool(doi)

        # --- R1.2: Linked publication ---
        # A PUBLICATIONS entry means the methodology is traceable to a peer-reviewed
        # paper. Source: PROJECT.PUBLICATIONS in mwtab — present in ~8.6% of studies.
        publications = str(rf.get("publications", "") or "").strip()
        has_publications = bool(publications)

        # --- R1: Funding source declared ---
        # Funder declaration is a standard RDM requirement and signals the study
        # underwent institutional review. Source: PROJECT.FUNDING_SOURCE — ~24% of studies.
        funding = str(rf.get("funding_source", "") or "").strip()
        has_funding = bool(funding)

        # --- R1: Contributors listed ---
        # Named contributors beyond the PI (co-authors, data managers) are a FAIR
        # best-practice for attribution. Source: PROJECT.CONTRIBUTORS — ~11% of studies.
        contributors = str(rf.get("contributors", "") or "").strip()
        has_contributors = bool(contributors)

        # --- R1: Study type / experiment type declared ---
        # A non-empty PROJECT_TYPE (e.g. "timecourse study", "case-control") provides
        # essential reuse context. Source: PROJECT.PROJECT_TYPE — ~37% of studies.
        project_type = str(rf.get("project_type", "") or "").strip()
        has_project_type = bool(project_type)

        # --- F2: Substantive study description (≥20 words) ---
        # Passes when the deposited PROJECT_SUMMARY or the deposited STUDY_SUMMARY has ≥20 words
        # (either one is enough). Both are read from the study's mwTab text files; a study without
        # such a file falls back to the description of the canonical study record.
        deposited = deposited_summaries.of_study(study)
        if deposited is None:
            description = str(study.study.description or "").strip()
            project_summary_words = study_summary_words = None
            description_words = len(description.split()) if description else 0
        else:
            project_summary_words = int(deposited["project_summary"]["words"])
            study_summary_words = int(deposited["study_summary"]["words"])
            description_words = max(project_summary_words, study_summary_words)
        has_description = description_words >= self._MIN_DESCRIPTION_WORDS

        # --- A1.1: Raw data documented ---
        # Passes when any analysis declares ANALYSIS.DATA_FORMAT or names a raw file for a sample under any
        # RAW_FILE... key of SUBJECT_SAMPLE_FACTORS (either one is enough). Read from the study's mwTab text
        # files; a study without such a file falls back to the format recorded on its assays.
        # A declaration documents the raw data; it does not show that the files can be downloaded.
        raw_declarations = deposited_raw_files.of_study(study)
        if raw_declarations is None:
            raw_data_formats = sorted({
                str(a.metadata.get("data_format", "") or "").strip()
                for a in study.assays
                if str(a.metadata.get("data_format", "") or "").strip()
            })
            raw_file_evidence = None
            has_raw_format = bool(raw_data_formats)
        else:
            with_format = [r for r in raw_declarations if r["data_format"]]
            with_files = [r for r in raw_declarations if r["samples_with_raw_file"]]
            extensions = sorted({e for r in with_files for e in r["raw_file_extensions"]})
            raw_data_formats = sorted({r["data_format"] for r in with_format}) + [e for e in extensions]
            if with_files and not extensions and not with_format:
                raw_data_formats = ["raw_files_linked"]
            raw_file_evidence = {
                "n_analyses": len(raw_declarations),
                "n_analyses_with_data_format": len(with_format),
                "n_analyses_with_raw_file_names": len(with_files),
                "n_analyses_with_either": sum(1 for r in raw_declarations if r["data_format"] or r["samples_with_raw_file"]),
            }
            has_raw_format = bool(with_format or with_files)

        checks: dict[str, bool] = {
            "f1_doi_registered": has_doi,
            "r1_2_linked_publication": has_publications,
            "r1_funding_source_declared": has_funding,
            "r1_contributors_listed": has_contributors,
            "r1_study_type_declared": has_project_type,
            "f2_substantive_description": has_description,
            "a1_1_raw_data_format_recorded": has_raw_format,
        }

        passed = sum(1 for ok in checks.values() if ok)
        total = len(checks)
        score = passed / total if total else 0.0
        status = "pass" if score >= 0.8 else "warn" if score >= 0.6 else "fail"

        recommendations: list[str] = []
        if not has_doi:
            recommendations.append(
                "Add a DOI to the PROJECT block (PROJECT.DOI in mwtab). Metabolomics Workbench "
                "assigns dataset DOIs under the 10.21228/ prefix at submission; contact MW support "
                "if the field is missing. A linked publication DOI is also accepted."
            )
        if not has_publications:
            recommendations.append(
                "Link a publication (PUBLICATIONS field) so the methodology is traceable to "
                "peer-reviewed documentation."
            )
        if not has_funding:
            recommendations.append(
                "Declare the funding source (FUNDING_SOURCE) to meet standard RDM requirements "
                "and enable cross-study funding-body filtering."
            )
        if not has_contributors:
            recommendations.append(
                "List co-authors/contributors beyond the PI in the CONTRIBUTORS field for "
                "proper attribution."
            )
        if not has_project_type:
            recommendations.append(
                "Specify the experiment design type (PROJECT_TYPE, e.g. 'case-control', "
                "'timecourse', 'dose-response') to aid reuse."
            )
        if not has_description:
            recommendations.append(
                f"Give a project summary or a study summary of ≥{self._MIN_DESCRIPTION_WORDS} words "
                f"(longest deposited: {description_words}). The title alone is not a sufficient summary."
            )
        if not has_raw_format:
            recommendations.append(
                "Document the raw data: record the format in ANALYSIS.DATA_FORMAT (e.g. '.mzML', '.raw', '.d') "
                "or name the raw file of each sample (RAW_FILE_NAME in SUBJECT_SAMPLE_FACTORS)."
            )

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=(
                f"Study-level FAIR compliance: {passed}/{total} checks passed "
                f"(score = {passed}/{total} = {score:.3f})."
            ),
            details={
                "checks": checks,
                "passed": passed,
                "total": total,
                "field_evidence": {
                    "doi": doi,
                    "publications": publications[:120] if publications else "",
                    "funding_source": funding,
                    "contributors": contributors[:120] if contributors else "",
                    "project_type": project_type,
                    "description_words": description_words,
                    "project_summary_words": project_summary_words,
                    "study_summary_words": study_summary_words,
                    "description_threshold": self._MIN_DESCRIPTION_WORDS,
                    "raw_data_formats": raw_data_formats,
                    "raw_file_evidence": raw_file_evidence,
                },
            },
            thresholds={"recommended_minimum": 0.8},
            recommendations=recommendations,
        )


NOT_ASSESSABLE = "not_assessable"   # nothing to score: left out of the section mean (readiness_score._mean_score)
_REFMET_SCORE_DEFINITION = (
    "per analysis: metabolite records with a valid RefMet name / deposited metabolite records; source score: mean of "
    "the analysis shares; an analysis without metabolite records is left out"
)
_REFMET_RECORD_SOURCE = (
    "Metabolite records of the study (Metabolomics Workbench REST endpoint /study/study_id/<ST>/metabolites): one "
    "record per deposited metabolite per analysis, with the repository's own RefMet name. A RefMet name is valid "
    "unless it is blank, '-', 'Unknown', 'Standard' or 'Protein'."
)


def _refmet_recommendations(not_valid: Counter) -> list[str]:
    """Advice that follows the reported category of the records without a valid RefMet name (Table S1e)."""
    def count(n: int, rest_one: str, rest_many: str) -> str:
        return f"{n:,} record {rest_one}" if n == 1 else f"{n:,} records {rest_many}"

    out = []
    n_names = not_valid["blank_named"]
    n_codes = not_valid["blank_formula"] + not_valid["blank_code"] + not_valid["blank_other"]
    if n_names:
        out.append(
            count(n_names, "is a chemical name", "are chemical names") + " without a RefMet entry (often a drug, a "
            "synthetic compound or a long systematic name). Manual mapping is needed before cross-study use."
        )
    if n_codes:
        out.append(
            count(n_codes, "is", "are") + " a formula, a code, an m/z–RT pair or a class or tentative label without a "
            "RefMet name. Identification is needed before mapping."
        )
    if not_valid["standard"]:
        out.append(count(not_valid["standard"], "is", "are") + " marked as an internal standard (RefMet name \"Standard\"), not a metabolite to map.")
    if not_valid["unknown"]:
        out.append(count(not_valid["unknown"], "is", "are") + " marked \"Unknown\" by the repository.")
    if not_valid["protein"]:
        out.append(count(not_valid["protein"], "is a protein entry", "are protein entries") + " (RefMet name \"Protein\"), not a metabolite.")
    return out


def _refmet_records_of_source(study: CanonicalStudy) -> list[tuple[str, list[dict]]]:
    """(analysis ID, its metabolite records) for the analyses of this source: the assessed matrices, or every analysis
    with records when the report has no matrix (metadata-only record)."""
    study_id = str(study.study.study_id or "").strip().upper()
    by_analysis = ms_refmet.records_by_analysis(Path(str(study.provenance.source_root or "")).expanduser() / study_id)
    analysis_ids = list(dict.fromkeys(str(m.assay_id or "").strip().upper() for m in study.feature_matrices))
    if not analysis_ids:
        analysis_ids = sorted(k for k in by_analysis if k)
    return [(analysis_id, by_analysis.get(analysis_id, [])) for analysis_id in analysis_ids]


def _refmet_per_analysis(study: CanonicalStudy) -> tuple[list[dict[str, Any]], Counter, Counter]:
    per_analysis, totals, not_valid = [], Counter(), Counter()
    for analysis_id, records in _refmet_records_of_source(study):
        summary = ms_refmet.mapping_summary(records)
        per_analysis.append({
            "analysis_id": analysis_id.lower(),
            "n_records": summary["n_records"],
            "n_valid": summary["n_valid"],
            "share": (summary["n_valid"] / summary["n_records"]) if summary["n_records"] else None,
            "not_valid": summary["not_valid"],
        })
        totals["n_records"] += summary["n_records"]
        totals["n_valid"] += summary["n_valid"]
        not_valid.update(summary["not_valid"])
    return per_analysis, totals, not_valid


class FairMetaboliteIdentifierResolvabilityMetric(MetricPlugin):
    family = "Metadata / FAIR"
    name = "fair_metabolite_identifier_resolvability"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        explicit_rows = _load_workbench_metabolites_rows(study)

        # --- Metabolomics Workbench: metabolite records of the study, by the RefMet rule of the manuscript ---
        # (Text S3 "RefMet Mappings"; rewritten 2026-10-07)
        #   share of an analysis = records with a valid RefMet name / deposited metabolite records of the analysis
        #   score of the source  = mean of the analysis shares (every analysis with records counts equally)
        # A RefMet name is valid unless it is blank, '-', 'Unknown', 'Standard' or 'Protein'. An analysis without a
        # metabolite record has no share and is left out; when no analysis has records the metric is "not_assessable".
        # Records present but none valid scores 0.
        if explicit_rows is not None:
            per_analysis, totals, not_valid_totals = _refmet_per_analysis(study)
            shares = [x["share"] for x in per_analysis if x["share"] is not None]
            if not shares:
                return MetricResult(
                    family=self.family,
                    name=self.name,
                    score=0.0,
                    status=NOT_ASSESSABLE,
                    summary="Not assessable: no metabolite records are deposited for the analyses of this source.",
                    details={
                        "not_assessable": True, "endpoint_entries": len(explicit_rows), "named_metabolites": 0,
                        "refmet_matched": 0, "n_analyses_with_records": 0,
                        "n_analyses_without_records": len(per_analysis), "per_analysis": per_analysis,
                        "score_definition": _REFMET_SCORE_DEFINITION, "record_source": _REFMET_RECORD_SOURCE,
                    },
                    recommendations=[],
                )
            score = sum(shares) / len(shares)
            status = "pass" if score >= 0.7 else "warn" if score >= 0.5 else "fail"
            n_records, n_valid = totals["n_records"], totals["n_valid"]
            return MetricResult(
                family=self.family,
                name=self.name,
                score=round(score, 4),
                status=status,
                summary=(
                    f"{n_valid:,} of {n_records:,} deposited metabolite records have a valid RefMet name "
                    f"({100 * n_valid / n_records:.1f}% pooled); mean of {len(shares)} analyses {100 * score:.1f}%."
                ),
                details={
                    "not_assessable": False,
                    "endpoint_entries": len(explicit_rows),
                    # kept under the earlier key names for the exports: records and valid RefMet names of the scored analyses
                    "named_metabolites": n_records,
                    "refmet_matched": n_valid,
                    "n_records": n_records,
                    "n_valid": n_valid,
                    "pooled_share": n_valid / n_records,
                    "not_valid": dict(not_valid_totals),
                    "n_analyses_with_records": len(shares),
                    "n_analyses_without_records": len(per_analysis) - len(shares),
                    "per_analysis": per_analysis,
                    "score_definition": _REFMET_SCORE_DEFINITION,
                    "record_source": _REFMET_RECORD_SOURCE,
                },
                recommendations=[] if score >= 0.7 else _refmet_recommendations(not_valid_totals),
            )

        # --- Fallback: use annotations ---
        annotations = study.annotations or []
        mappings = study.mappings or []
        named_total = 0
        resolvable = 0
        for idx, annotation in enumerate(annotations):
            token = annotation.raw_name or annotation.normalized_name
            feature_class = classify_feature_name(token)
            if not feature_class["is_named_metabolite"]:
                continue
            named_total += 1
            mapping = mappings[idx] if idx < len(mappings) else None
            pairs = _namespace_identifier_candidates(annotation, mapping)
            for _source_key, namespace, _identifier in pairs:
                ns = (namespace or "").strip().lower()
                if ns in _TRUSTED_IDENTIFIER_NAMESPACES:
                    resolvable += 1
                    break

        if named_total == 0:
            return MetricResult(
                family=self.family,
                name=self.name,
                score=0.0,
                status="warn",
                summary="No named metabolite features detected; identifier resolvability cannot be evaluated.",
                details={"named_metabolites": 0, "resolvable": 0},
                recommendations=["Use named metabolite annotations to enable FAIR interoperability."],
            )

        score = resolvable / named_total
        status = "pass" if score >= 0.7 else "warn" if score >= 0.5 else "fail"
        recommendations = []
        if score < 0.7:
            recommendations.append(
                f"Only {resolvable}/{named_total} named metabolites map to a trusted identifier namespace "
                "(HMDB/ChEBI/KEGG/RefMet/PubChem)."
            )
        return MetricResult(
            family=self.family,
            name=self.name,
            score=round(score, 4),
            status=status,
            summary=f"Identifier resolvability: {resolvable}/{named_total} named metabolites map to a trusted namespace.",
            details={"named_metabolites": named_total, "resolvable": resolvable},
            recommendations=recommendations,
        )


class ExternalCrossReferencesViaRefmetMetric(MetricPlugin):
    """External database cross-references, via RefMet, of the records with a valid RefMet name
    (manuscript: Text S3 "External identifiers of RefMet-mapped records", Figure S7). Scored since 2026-10-07.

        share of an analysis = records with AT LEAST ONE of six identifiers / records with a valid RefMet name
        score of the source  = mean of the analysis shares (every analysis with such records counts equally)

    The six identifiers are ChEBI, InChIKey, PubChem CID, HMDB, KEGG and LIPID MAPS; one is enough, no particular
    database is required, and "all six" is reported for information only. The identifiers are looked up by RefMet
    name in the frozen RefMet identifier table; they were not necessarily deposited with the feature. Not assessable
    when no record has a valid RefMet name. Status: pass from 50%, warn below, never fail (a lipid reported as a sum
    composition has no single structure and RefMet holds no identifier for it).
    """

    family = "Metadata / FAIR"
    name = "external_cross_references_via_refmet"
    PASS_FROM = 0.5

    def compute(self, study: CanonicalStudy) -> MetricResult:
        per_analysis = []
        totals: Counter = Counter()
        per_identifier: Counter = Counter()
        if _load_workbench_metabolites_rows(study) is not None:
            for analysis_id, records in _refmet_records_of_source(study):
                summary = ms_refmet.external_id_summary(records)
                n = summary["n_valid_records"]
                per_analysis.append({
                    "analysis_id": analysis_id.lower(),
                    "n_valid_records": n,
                    "n_with_any": summary["n_with_any"],
                    "n_with_all_six": summary["n_with_all_six"],
                    "share": (summary["n_with_any"] / n) if n else None,
                })
                for key in ("n_valid_records", "n_with_any", "n_with_all_six", "n_refmet_entry_without_external_id",
                            "n_name_not_in_current_refmet"):
                    totals[key] += summary[key]
                per_identifier.update(summary["per_identifier"])
        shares = [x["share"] for x in per_analysis if x["share"] is not None]
        assessable = bool(shares)
        score = (sum(shares) / len(shares)) if assessable else 0.0
        n = totals["n_valid_records"]
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=NOT_ASSESSABLE if not assessable else ("pass" if score >= self.PASS_FROM else "warn"),
            summary=(
                f"{totals['n_with_any']:,} of {n:,} records with a valid RefMet name have at least one external "
                f"identifier via RefMet ({100 * totals['n_with_any'] / n:.1f}% pooled); mean of {len(shares)} analyses "
                f"{100 * score:.1f}%."
                if assessable else "Not assessable: no metabolite record of this source has a valid RefMet name."
            ),
            details={
                "not_assessable": not assessable,
                "n_analyses_with_valid_records": len(shares),
                "n_valid_records": n,
                "n_with_any": totals["n_with_any"],
                "n_with_all_six": totals["n_with_all_six"],
                "pooled_share": (totals["n_with_any"] / n) if n else None,
                "per_identifier": {ms_refmet.FIELD_LABELS[f]: per_identifier[f] for f in ms_refmet.FIELDS},
                "n_refmet_entry_without_external_id": totals["n_refmet_entry_without_external_id"],
                "n_name_not_in_current_refmet": totals["n_name_not_in_current_refmet"],
                "per_analysis": per_analysis,
                "reference_tables": (
                    f"RefMet identifier table (/rest/refmet/all_ids, {ms_refmet.ID_TABLE_DATE}) and full RefMet name "
                    f"list (/rest/refmet/all, {ms_refmet.NAME_LIST_DATE}), stored with the code."
                ),
                "score_definition": (
                    "per analysis: records with at least one of the six identifiers / records with a valid RefMet "
                    "name; source score: mean of the analysis shares; an analysis without such records is left out"
                ),
            },
            thresholds={"pass_from": self.PASS_FROM},
            recommendations=[] if (not assessable or score >= self.PASS_FROM) else [
                "Most RefMet names of this source have no external identifier in RefMet. This is typical for lipids "
                "reported as a sum composition (such as PC 36:4); structure-level cross-referencing needs resolved species."
            ],
        )


class MassRtLikeMetadataPresenceMetric(MetricPlugin):
    family = "Metadata / FAIR"
    name = "mass_rt_like_metadata_presence"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        from merit.coordinate_metrics import coordinate_metric
        return coordinate_metric(study)



# Backward-compatible class name for previously serialized outputs.
class FairMetadataMetric(FairStudyMetadataComplianceMetric):
    name = "fair_metadata_coverage"


def _label_simplicity(factor_strings: list[str]) -> float:
    """Score based on the number of pipe-separated factor dimensions.

    Each '|' in the factor string adds one dimension.  The average pipe count
    across all samples determines the step-function score:

        1 dimension  (0 pipes) → 1.0   e.g. "Group:NAFLD"
        2 dimensions (1 pipe)  → 0.7   e.g. "Infection:Control | Sex:Male"
        3 dimensions (2 pipes) → 0.4   e.g. "FCS:FCS | Hours:72 | REFED:NO_REFED" (wait — that's 2 pipes, so here: ST000083)
        ≥4 dimensions (3+ pipes) → 0.1  e.g. ST000010: 4 factors

    No predefined keyword or type rules — purely structural.
    """
    if not factor_strings:
        return 1.0
    avg_pipes = sum(s.count("|") for s in factor_strings) / len(factor_strings)
    if avg_pipes < 1:
        return 1.0
    if avg_pipes < 2:
        return 0.7
    if avg_pipes < 3:
        return 0.4
    return 0.1


class FactorLabelHarmonizationMetric(MetricPlugin):
    """Factor label quality assessed on two dimensions:

    score = 0.5 × label_quality + 0.5 × simplicity

    label_quality  = fraction of ML-eligible samples with a valid non-unknown label
    simplicity     = step function on average token count of unique raw label values
                     (see _label_simplicity); purely numeric label sets score 0.

    Factor variable richness (number of distinct factor keys) is captured in
    details["n_factor_keys"] for informational use; it is not a scored component
    because every study has at least the primary label key, giving near-uniform
    richness scores that add no discrimination.
    """
    family = "ML Task Readiness"
    name = "factor_label_harmonizability"

    _SKIP_KEYS = {
        "mb_sample_id", "raw_data", "class_string",
        "factor_string", "endpoint_label", "endpoint_label_key",
        "original_sample_id", "sample_source", "raw_file",
    }

    def compute(self, study: CanonicalStudy) -> MetricResult:
        raw_labels = [
            sample.label for sample in study.samples
            if sample.label and _is_biological_sample(sample)
        ]
        valid_normalized = [normalize_label(lbl) for lbl in raw_labels if is_usable_class_label(lbl)]

        label_quality = len(valid_normalized) / len(raw_labels) if raw_labels else 0.0
        unique_raw = sorted(set(raw_labels))
        factor_strings = [
            sample.attributes.get("factor_string", "")
            for sample in study.samples
            if sample.label and _is_biological_sample(sample)
            and sample.attributes.get("factor_string", "")
        ]
        simplicity = _label_simplicity(factor_strings)
        avg_pipe_count = (
            sum(s.count("|") for s in factor_strings) / len(factor_strings)
            if factor_strings else 0.0
        )
        # Collect actual factor dimension names (keys from factor_string only)
        factor_dim_keys: dict[str, set[str]] = {}
        for sample in study.samples:
            fstr = sample.attributes.get("factor_string", "")
            if fstr and ":" in fstr:
                for part in fstr.split("|"):
                    if ":" in part:
                        k = part.split(":")[0].strip()
                        v = part.split(":", 1)[1].strip()
                        if k:
                            factor_dim_keys.setdefault(k, set()).add(v)
        score = 0.5 * label_quality + 0.5 * simplicity

        # factor_dim_keys already built above — no additional loop needed

        # Endpoint discrepancy: tabular label vs factors-endpoint label
        mismatched: list[dict[str, str]] = []
        endpoint_key = ""
        for sample in study.samples:
            ep = sample.attributes.get("endpoint_label", "")
            if ep and ep != sample.label:
                mismatched.append({
                    "sample_id": sample.sample_id,
                    "tabular": sample.label,
                    "endpoint": ep,
                })
                if not endpoint_key:
                    endpoint_key = sample.attributes.get("endpoint_label_key", "")

        recs: list[str] = []
        if score < 0.75:
            recs.append("Simplify or curate label strings before building ML models.")
        if mismatched:
            key_note = f" (factor key: '{endpoint_key}')" if endpoint_key else ""
            recs.append(
                f"{len(mismatched)} sample(s) have class labels that differ between tabular data "
                f"and the factors endpoint{key_note}. Tabular data is used as the authoritative source."
            )

        unique_norm = sorted(set(valid_normalized))
        n_dims = round(avg_pipe_count) + 1
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.75 else "warn",
            summary=(
                f"{len(unique_raw)} unique label(s) across {n_dims} factor dimension(s). "
                f"Label quality: {label_quality:.0%}, simplicity: {simplicity:.1f}."
            ),
            details={
                "raw_unique_labels": unique_raw[:20],
                "normalized_unique_labels": unique_norm[:20],
                "label_quality": round(label_quality, 4),
                "simplicity": simplicity,
                "avg_pipe_count": round(avg_pipe_count, 2),
                "n_factor_dimensions": n_dims,
                "factor_dimensions": {
                    k: sorted(v)[:10] for k, v in sorted(factor_dim_keys.items())
                },
                "example_factor_strings": sorted({
                    s for s in factor_strings if s
                })[:2],
                "endpoint_discrepancy_count": len(mismatched),
                "endpoint_discrepant_samples": mismatched[:10],
            },
            thresholds={"recommended_minimum": 0.75},
            recommendations=recs,
        )


class DiseaseEndpointMetric(MetricPlugin):
    family = "ML Task Readiness"
    name = "disease_endpoint_extractability"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        disease_field = bool(study.study.disease and study.study.disease.strip())
        biological_samples = [s for s in study.samples if _is_biological_sample(s)]
        labels = [normalize_label(s.label) for s in biological_samples]
        valid_labels = [normalize_label(s.label) for s in biological_samples if is_usable_class_label(s.label)]
        label_coverage = len(valid_labels) / len(labels) if labels else 0.0
        distinct_groups = len(set(valid_labels))

        # Capture concrete examples of non-usable labels so users can quickly
        # identify parsing/curation problems.
        unusable_counter: Counter[str] = Counter()
        unusable_examples: dict[str, list[str]] = {}
        for sample in study.samples:
            if not _is_biological_sample(sample):
                continue
            raw_label = str(sample.label or "").strip()
            if not raw_label:
                continue
            if is_usable_class_label(raw_label):
                continue
            key = raw_label if raw_label else "<empty>"
            unusable_counter[key] += 1
            unusable_examples.setdefault(key, [])
            if len(unusable_examples[key]) < 5:
                unusable_examples[key].append(str(sample.sample_id or ""))

        if not disease_field and label_coverage < 0.5:
            score, status = 0.0, "fail"
        elif distinct_groups >= 2 and label_coverage >= 0.8:
            score, status = 1.0, "pass"
        elif distinct_groups >= 2 and label_coverage >= 0.5:
            score, status = 0.7, "warn"
        else:
            score, status = 0.3, "warn"

        recs = []
        if not disease_field:
            recs.append("No disease field found in study metadata. Label-based ML tasks may be undefined.")
        if distinct_groups < 2:
            recs.append("Only one distinct label group found — binary or multi-class classification is not possible.")
        if label_coverage < 0.8:
            recs.append(f"Only {label_coverage:.0%} of samples have a usable label. Check factor variable parsing.")
            if unusable_counter:
                parts: list[str] = []
                for raw_label, _count in unusable_counter.most_common():
                    for sid in (unusable_examples.get(raw_label) or []):
                        parts.append(f"label '{raw_label}' (sample {sid})")
                        if len(parts) >= 5:
                            break
                    if len(parts) >= 5:
                        break
                recs.append(
                    "Top non-usable label examples: " + ", ".join(parts) + "."
                )

        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status=status,
            summary=(
                f"Study disease metadata {'present' if disease_field else 'absent'}. "
                f"Label endpoint extractability: {distinct_groups} group(s) across {label_coverage:.0%} of samples."
            ),
            details={
                "study_disease_field": study.study.disease,
                "distinct_label_groups": distinct_groups,
                "label_coverage": label_coverage,
                "label_counts": dict(Counter(valid_labels).most_common(10)),
                "non_usable_label_counts": dict(unusable_counter),
                "non_usable_label_examples": {
                    key: values[:5] for key, values in unusable_examples.items()
                },
            },
            thresholds={"minimum_label_coverage": 0.8, "minimum_groups": 2},
            recommendations=recs if score < 1.0 else [],
        )


class LcMethodContextMetric(MetricPlugin):
    family = "Metadata / FAIR"
    name = "lc_method_context_presence"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        from merit.coordinate_metrics import lc_metric
        return lc_metric(study)
