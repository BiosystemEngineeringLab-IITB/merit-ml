from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from merit.models import CanonicalStudy


class RepositoryConnector(ABC):
    source_name: str = ""
    connector_name: str = ""
    bundle_version: str = "1"

    @abstractmethod
    def default_root(self, workspace: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def create_bundle(
        self,
        study_id: str,
        workspace: Path,
        root: str | None = None,
        fetch_mode: str = "auto",
        download_root: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def normalize_bundle(self, bundle: dict[str, Any]) -> CanonicalStudy:
        raise NotImplementedError
