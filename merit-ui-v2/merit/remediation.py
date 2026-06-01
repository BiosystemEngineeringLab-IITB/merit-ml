from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from merit.models import CanonicalStudy, FeatureMatrix
from merit.serialization import compute_content_hash
from merit.utils import normalize_label


def load_actions(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"normalize_labels": True}
    payload = json.loads(Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError("Remediation actions file must be a JSON object.")
    return payload


def _annotation_name_map(study: CanonicalStudy) -> dict[str, str]:
    return {
        annotation.feature_id: (str(annotation.raw_name or "").strip() or annotation.feature_id)
        for annotation in study.annotations
    }


def _rebuild_feature_matrix(matrix: FeatureMatrix, keep_indices: list[int], keep_feature_ids: list[str]) -> FeatureMatrix:
    matrix.feature_ids = keep_feature_ids
    matrix.values = [[row[index] for index in keep_indices] for row in matrix.values]
    return matrix


def remediate_study(study: CanonicalStudy, actions: dict[str, Any]) -> tuple[CanonicalStudy, list[dict[str, Any]]]:
    updated = copy.deepcopy(study)
    log: list[dict[str, Any]] = []

    if actions.get("normalize_labels", False):
        changed = 0
        for sample in updated.samples:
            before = sample.label
            after = normalize_label(before)
            if before != after:
                sample.label = after
                sample.disease = normalize_label(sample.disease or before)
                changed += 1
        for matrix in updated.feature_matrices:
            matrix.labels = {sample_id: normalize_label(label) for sample_id, label in matrix.labels.items()}
        log.append({"action": "normalize_labels", "changed_samples": changed})

    if actions.get("deduplicate_features", False):
        name_map = _annotation_name_map(updated)
        removed = 0
        kept_feature_ids = set()
        for matrix in updated.feature_matrices:
            seen_names = set()
            keep_indices = []
            keep_ids = []
            for index, feature_id in enumerate(matrix.feature_ids):
                name = name_map.get(feature_id, feature_id)
                if name in seen_names:
                    removed += 1
                    continue
                seen_names.add(name)
                keep_indices.append(index)
                keep_ids.append(feature_id)
                kept_feature_ids.add(feature_id)
            _rebuild_feature_matrix(matrix, keep_indices, keep_ids)
        updated.annotations = [annotation for annotation in updated.annotations if annotation.feature_id in kept_feature_ids]
        updated.mappings = [mapping for mapping in updated.mappings if mapping.mapped_reference_id or mapping.raw_identifier]
        log.append({"action": "deduplicate_features", "removed_features": removed})

    if "drop_high_missing_features" in actions:
        threshold = float(actions["drop_high_missing_features"])
        removed = 0
        kept_feature_ids = set()
        for matrix in updated.feature_matrices:
            keep_indices = []
            keep_ids = []
            for index, feature_id in enumerate(matrix.feature_ids):
                values = [row[index] for row in matrix.values]
                missing = sum(1 for value in values if value is None)
                ratio = missing / len(values) if values else 1.0
                if ratio > threshold:
                    removed += 1
                    continue
                keep_indices.append(index)
                keep_ids.append(feature_id)
                kept_feature_ids.add(feature_id)
            _rebuild_feature_matrix(matrix, keep_indices, keep_ids)
        updated.annotations = [annotation for annotation in updated.annotations if annotation.feature_id in kept_feature_ids]
        log.append({"action": "drop_high_missing_features", "threshold": threshold, "removed_features": removed})

    updated.provenance.notes.append("Remediated with actions: " + ", ".join(entry["action"] for entry in log))
    updated.provenance.content_hash = compute_content_hash(updated)
    return updated, log
