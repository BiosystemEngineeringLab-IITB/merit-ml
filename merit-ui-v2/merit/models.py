from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


@dataclass
class MappingRecord:
    raw_identifier: str = ""
    normalized_name: str = ""
    mapped_reference_id: str = ""
    mapping_confidence: float = 0.0
    namespace: str = ""


@dataclass
class ProvenanceRecord:
    source: str
    study_id: str
    source_root: str
    file_manifest: list[str]
    parser_version: str
    connector_name: str
    content_hash: str = ""
    notes: list[str] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)


@dataclass
class StudyRecord:
    study_id: str
    title: str
    description: str = ""
    organism: str = ""
    disease: str = ""
    repository: str = ""
    analysis_type: str = ""
    platform: str = ""
    publication_date: str = ""
    raw_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class SampleRecord:
    sample_id: str
    label: str = ""
    disease: str = ""
    disease_stage: str = ""
    sample_type: str = ""
    organism: str = ""
    organism_part: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    source_file: str = ""


@dataclass
class AssayRecord:
    assay_id: str
    name: str = ""
    platform: str = ""
    polarity: str = ""
    technology: str = ""
    measurement_type: str = ""
    feature_matrix_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetaboliteAnnotationRecord:
    feature_id: str
    raw_name: str = ""
    normalized_name: str = ""
    database_identifier: str = ""
    mapped_reference_id: str = ""
    mapping_confidence: float = 0.0
    chemical_formula: str = ""
    smiles: str = ""
    inchi: str = ""
    reliability: str = ""
    mass_to_charge: float | None = None
    retention_time: float | None = None
    ambiguity_flags: list[str] = field(default_factory=list)


@dataclass
class FeatureMatrix:
    matrix_id: str
    assay_id: str
    sample_ids: list[str]
    feature_ids: list[str]
    values: list[list[float | None]]
    labels: dict[str, str] = field(default_factory=dict)
    source_file: str = ""
    matrix_type: str = "abundance"
    source_kind: str = ""  # "datatable", "mwtab", "untarg" — drives source-aware zero handling


@dataclass
class CanonicalStudy:
    schema_version: str
    study: StudyRecord
    samples: list[SampleRecord]
    assays: list[AssayRecord]
    feature_matrices: list[FeatureMatrix]
    annotations: list[MetaboliteAnnotationRecord]
    mappings: list[MappingRecord]
    provenance: ProvenanceRecord
    score_defaults: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricResult:
    family: str
    name: str
    score: float
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    thresholds: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    informational: bool = False  # if True, excluded from composite readiness score


@dataclass
class SourceAssessment:
    """Assessment result for a single data source of one analysis.

    Holds the canonical representation, assessment report, and readiness score
    for one source (datatable, mwtab, or untarg_data) independently.
    source_tier is retained as provenance/context (tier1 vs tier2) and does
    not switch section weight tables.
    """
    source: str                        # "datatable" | "mwtab" | "untarg_data"
    source_tier: str                   # "tier1" | "tier2"
    canonical: "CanonicalStudy"
    report: "AssessmentReport"
    readiness_score: dict[str, Any]    # output of compute_readiness_score()


@dataclass
class AssessmentReport:
    source: dict[str, Any]
    ingestion_summary: dict[str, Any]
    schema_validation: list[MetricResult]
    metadata_readiness: list[MetricResult]
    analytical_readiness: list[MetricResult]
    annotation_readiness: list[MetricResult]
    cohort_bias: list[MetricResult]
    ml_readiness: list[MetricResult]
    class_separability: list[MetricResult]
    cross_study_harmonization: list[MetricResult]
    remediations_applied: list[dict[str, Any]]
    software_versions: dict[str, Any]
    content_hash: str = ""


def dataclass_to_dict(value: Any) -> Any:
    if is_dataclass(value):
        result = {}
        for key, item in asdict(value).items():
            result[key] = dataclass_to_dict(item)
        return result
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {str(key): dataclass_to_dict(item) for key, item in value.items()}
    return value
