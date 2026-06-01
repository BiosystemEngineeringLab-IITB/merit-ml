from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any, TypeVar

from .models import (
    AssessmentReport,
    AssayRecord,
    CanonicalStudy,
    FeatureMatrix,
    MappingRecord,
    MetaboliteAnnotationRecord,
    MetricResult,
    ProvenanceRecord,
    SampleRecord,
    StudyRecord,
    dataclass_to_dict,
)
from .utils import ensure_path, sha256_text, stable_json_dumps

T = TypeVar("T")


def write_dataclass_json(path: str | Path, value: Any) -> None:
    path = ensure_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dataclass_to_dict(value)
    path.write_text(stable_json_dumps(payload))


def read_json(path: str | Path) -> Any:
    path = ensure_path(path)
    return json.loads(path.read_text())


def _build(cls: type[T], payload: dict[str, Any]) -> T:
    kwargs: dict[str, Any] = {}
    for field in fields(cls):
        value = payload.get(field.name)
        if cls is CanonicalStudy:
            if field.name == "study":
                kwargs[field.name] = _build(StudyRecord, value)
                continue
            if field.name == "samples":
                kwargs[field.name] = [_build(SampleRecord, item) for item in value]
                continue
            if field.name == "assays":
                kwargs[field.name] = [_build(AssayRecord, item) for item in value]
                continue
            if field.name == "feature_matrices":
                kwargs[field.name] = [_build(FeatureMatrix, item) for item in value]
                continue
            if field.name == "annotations":
                kwargs[field.name] = [_build(MetaboliteAnnotationRecord, item) for item in value]
                continue
            if field.name == "mappings":
                kwargs[field.name] = [_build(MappingRecord, item) for item in value]
                continue
            if field.name == "provenance":
                kwargs[field.name] = _build(ProvenanceRecord, value)
                continue
        if cls is AssessmentReport:
            metric_sections = {
                "schema_validation",
                "metadata_readiness",
                "analytical_readiness",
                "annotation_readiness",
                "cohort_bias",
                "ml_readiness",
                "class_separability",
                "cross_study_harmonization",
            }
            if field.name in metric_sections:
                items = value if isinstance(value, list) else []
                kwargs[field.name] = [_build(MetricResult, item) for item in items]
                continue
        kwargs[field.name] = value
    return cls(**kwargs)


def load_canonical_study(path: str | Path) -> CanonicalStudy:
    payload = read_json(path)
    return _build(CanonicalStudy, payload)


def load_assessment_report(path: str | Path) -> AssessmentReport:
    payload = read_json(path)
    return _build(AssessmentReport, payload)


def assessment_report_from_dict(payload: dict[str, Any]) -> AssessmentReport:
    return _build(AssessmentReport, payload)

def compute_content_hash(value: Any) -> str:
    return sha256_text(stable_json_dumps(dataclass_to_dict(value)))
