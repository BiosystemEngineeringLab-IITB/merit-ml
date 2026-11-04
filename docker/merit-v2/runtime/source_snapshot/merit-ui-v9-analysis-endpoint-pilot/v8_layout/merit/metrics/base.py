from __future__ import annotations

from abc import ABC, abstractmethod

from merit.models import CanonicalStudy, MetricResult


class MetricPlugin(ABC):
    family: str
    name: str
    profiles: tuple[str, ...] = ("core", "full")
    informational: bool = False  # if True, metric is excluded from composite score

    def validate(self, study: CanonicalStudy) -> list[str]:
        return []

    @abstractmethod
    def compute(self, study: CanonicalStudy) -> MetricResult:
        raise NotImplementedError
