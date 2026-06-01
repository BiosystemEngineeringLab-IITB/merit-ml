from __future__ import annotations

from .analytical import (
    AssayComparabilityMetric,
    FeatureCorrelationMetric,
    FeatureLevelMissingnessMetric,
    MetabatchBatchAnnotationCompatibilityMetric,
    MissingnessMetric,
    NormalizationStatusMetric,
    OutlierMetric,
    QcPresenceMetric,
)
from .annotation import (
    AnnotationAmbiguityMetric,
    FeatureAnnotationTypeMetric,
    FeatureRedundancyMetric,
    UnknownFeatureFractionMetric,
)
from .base import MetricPlugin
from .cohort import (
    ClassBalanceMetric,
    GroupSizeSupportMetric,
    LabelEntropyMetric,
)
from .harmonization import CrossStudyHarmonizationMetric, PathwayMappabilityMetric
from .metadata import (
    DiseaseEndpointMetric,
    FairMetaboliteIdentifierResolvabilityMetric,
    FairStudyMetadataComplianceMetric,
    FactorLabelHarmonizationMetric,
    MassRtLikeMetadataPresenceMetric,
)
from .ml_readiness import (
    FeatureToSampleRatioMetric,
    LabelSuitabilityMetric,
)
from .separability import ClassSeparabilityMetric
from .structural import (
    CompletenessMetric,
    DuplicateEntityMetric,
    MinimumSampleThresholdMetric,
    SchemaIntegrityMetric,
    TabularDataAvailabilityMetric,
)


DEFAULT_METRICS: list[MetricPlugin] = [
    # Structural
    SchemaIntegrityMetric(),
    TabularDataAvailabilityMetric(),
    CompletenessMetric(),
    DuplicateEntityMetric(),
    MinimumSampleThresholdMetric(),
    # Metadata / FAIR
    FairStudyMetadataComplianceMetric(),
    FairMetaboliteIdentifierResolvabilityMetric(),
    MassRtLikeMetadataPresenceMetric(),
    # Analytical QC
    QcPresenceMetric(),
    MissingnessMetric(),
    NormalizationStatusMetric(),
    MetabatchBatchAnnotationCompatibilityMetric(),
    AssayComparabilityMetric(),
    FeatureCorrelationMetric(),
    OutlierMetric(),
    FeatureLevelMissingnessMetric(),
    # Annotation / Interoperability
    FeatureAnnotationTypeMetric(),
    AnnotationAmbiguityMetric(),
    UnknownFeatureFractionMetric(),
    FeatureRedundancyMetric(),
    # Cohort / Bias
    ClassBalanceMetric(),
    GroupSizeSupportMetric(),
    LabelEntropyMetric(),
    # ML Task Readiness
    DiseaseEndpointMetric(),
    FactorLabelHarmonizationMetric(),
    LabelSuitabilityMetric(),
    FeatureToSampleRatioMetric(),
    # Class Separability
    ClassSeparabilityMetric(),
    # Cross-Study Harmonization
    CrossStudyHarmonizationMetric(),
    PathwayMappabilityMetric(),
]


def metrics_for_profile(profile: str) -> list[MetricPlugin]:
    return [metric for metric in DEFAULT_METRICS if profile in metric.profiles]
