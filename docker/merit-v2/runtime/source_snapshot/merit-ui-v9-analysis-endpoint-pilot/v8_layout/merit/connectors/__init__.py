from __future__ import annotations

from pathlib import Path

from .base import RepositoryConnector
from .workbench import MetabolomicsWorkbenchConnector


CONNECTORS: dict[str, RepositoryConnector] = {
    "workbench": MetabolomicsWorkbenchConnector(),
}


def available_sources() -> list[str]:
    return sorted(CONNECTORS.keys())


def get_connector(source: str) -> RepositoryConnector:
    try:
        return CONNECTORS[source]
    except KeyError as exc:
        raise ValueError(f"Unsupported source: {source}") from exc


def create_bundle(
    source: str,
    study_id: str,
    workspace: Path,
    root: str | None = None,
    fetch_mode: str = "auto",
    download_root: str | None = None,
) -> dict:
    return get_connector(source).create_bundle(
        study_id=study_id,
        workspace=workspace,
        root=root,
        fetch_mode=fetch_mode,
        download_root=download_root,
    )


def normalize_bundle(bundle: dict) -> object:
    source = bundle.get("source")
    return get_connector(source).normalize_bundle(bundle)
