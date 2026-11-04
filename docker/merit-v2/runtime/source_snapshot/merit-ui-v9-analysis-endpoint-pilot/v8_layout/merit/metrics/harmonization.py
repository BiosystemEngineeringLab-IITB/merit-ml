from __future__ import annotations

from collections import Counter

from merit.models import CanonicalStudy, MetricResult
from merit.utils import normalize_label

from .base import MetricPlugin


class CrossStudyHarmonizationMetric(MetricPlugin):
    family = "Cross-Study Harmonization"
    name = "harmonization_feasibility"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        label_ratio = 0.0
        if study.samples:
            normalized_labels = [normalize_label(sample.label) for sample in study.samples]
            harmonizable = [label for label in normalized_labels if label != "unknown"]
            label_ratio = len(harmonizable) / len(normalized_labels)
        mapping_ratio = 0.0
        if study.annotations:
            mapping_ratio = sum(1 for annotation in study.annotations if annotation.mapped_reference_id) / len(study.annotations)
        platform_defined = 1.0 if study.study.platform else 0.0
        score = 0.4 * label_ratio + 0.4 * mapping_ratio + 0.2 * platform_defined
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.7 else "warn",
            summary=f"Cross-study harmonization feasibility score is {score:.3f}.",
            details={"label_ratio": label_ratio, "mapping_ratio": mapping_ratio, "platform_defined": platform_defined},
            thresholds={"recommended_minimum": 0.7},
            recommendations=[] if score >= 0.7 else ["Improve label normalization, platform metadata, and identifier mapping before cross-study merges."],
        )


class PathwayMappabilityMetric(MetricPlugin):
    family = "Cross-Study Harmonization"
    name = "pathway_mappability_proxy"

    def compute(self, study: CanonicalStudy) -> MetricResult:
        namespaces = Counter(mapping.namespace for mapping in study.mappings if mapping.namespace)
        recognized = sum(count for namespace, count in namespaces.items() if namespace.lower() in {"chebi", "hmdb", "kegg", "refmet", "lexical"})
        total = sum(namespaces.values())
        score = recognized / total if total else 0.0
        return MetricResult(
            family=self.family,
            name=self.name,
            score=score,
            status="pass" if score >= 0.75 else "warn",
            summary=f"Pathway mappability proxy score is {score:.3f}.",
            details={"namespaces": namespaces},
            thresholds={"recommended_minimum": 0.75},
            recommendations=[] if score >= 0.75 else ["Increase standard namespace coverage for downstream pathway interpretation."],
        )
