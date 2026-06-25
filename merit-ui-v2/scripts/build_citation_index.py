#!/usr/bin/env python3
"""Build a lightweight study-level Workbench citation index for MERIT UI."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path("/home/shayantan/metabolomics/ML-ready")
DUMP_ROOT = ROOT / "mw-dump-latest-confirmation-latest-version"
CACHE_ROOT = ROOT / "merit-cache-workbench-full-v7"
OUT_PATH = CACHE_ROOT / "citation_index.json"
WORKBENCH_AVAILABLE_URL = "https://www.metabolomicsworkbench.org/rest/study/study_id/ST/available"

PROJECT_ID_RE = re.compile(r"\bPROJECT_ID\s*:\s*([A-Za-z0-9_-]+)")
DOI_RE = re.compile(r"\b10\.21228/[A-Za-z0-9][A-Za-z0-9._/-]*", re.IGNORECASE)
GENERIC_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
PUBMED_URL_RE = re.compile(r"(?:pubmed(?:\.ncbi\.nlm\.nih\.gov)?/(?:pubmed/)?)\s*(\d+)", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d+)\b", re.IGNORECASE)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalise_doi(value: str) -> str:
    match = DOI_RE.search(value or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,;)")


def _strip_html(value: Any) -> str:
    return _clean(HTML_TAG_RE.sub(" ", str(value or "")))


def _normalise_publication_doi(value: str) -> str:
    for match in GENERIC_DOI_RE.finditer(value or ""):
        doi = match.group(0).rstrip(".,;)")
        # Project DOIs are already represented separately; this field is for
        # associated publication DOIs.
        if not doi.lower().startswith("10.21228/"):
            return doi
    return ""


def _extract_pubmed_id(value: str) -> str:
    for pattern in (PUBMED_URL_RE, PMID_RE):
        match = pattern.search(value or "")
        if match:
            return match.group(1)
    return ""


def _publication_entry(value: Any, source: str) -> dict[str, str] | None:
    raw = str(value or "").strip()
    citation = _strip_html(raw)
    if not citation:
        return None
    # Ignore empty placeholders that occur in some Workbench records.
    if citation.lower() in {"na", "n/a", "none", "not available", "null"}:
        return None
    publication_doi = _normalise_publication_doi(raw)
    pubmed_id = _extract_pubmed_id(raw)
    return {
        "citation": citation,
        "doi": publication_doi,
        "doi_url": f"https://doi.org/{publication_doi}" if publication_doi else "",
        "pubmed_id": pubmed_id,
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pubmed_id}/" if pubmed_id else "",
        "source": source,
    }


def _add_publication(publications: list[dict[str, str]], value: Any, source: str) -> None:
    entry = _publication_entry(value, source)
    if not entry:
        return
    key = (
        entry.get("doi", "").lower(),
        entry.get("pubmed_id", ""),
        entry.get("citation", "").lower(),
    )
    for existing in publications:
        existing_key = (
            existing.get("doi", "").lower(),
            existing.get("pubmed_id", ""),
            existing.get("citation", "").lower(),
        )
        if existing_key == key:
            return
    publications.append(entry)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _fetch_workbench_available_registry() -> dict[str, list[str]]:
    """Return study -> project IDs from the official Workbench available registry."""
    try:
        request = Request(
            WORKBENCH_AVAILABLE_URL,
            headers={"User-Agent": "MERIT citation index builder"},
        )
        with urlopen(request, timeout=90) as handle:
            payload = json.loads(handle.read().decode("utf-8", errors="replace"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        print(f"Warning: could not fetch Workbench available registry: {exc}")
        return {}
    by_study: dict[str, set[str]] = defaultdict(set)
    if isinstance(payload, dict):
        iterable = payload.values()
    elif isinstance(payload, list):
        iterable = payload
    else:
        iterable = []
    for item in iterable:
        if not isinstance(item, dict):
            continue
        study_id = _clean(item.get("study_id")).upper()
        project_id = _clean(item.get("project_id"))
        if study_id and project_id:
            by_study[study_id].add(project_id)
    return {study: sorted(ids) for study, ids in by_study.items()}


def _extract_from_study(study_dir: Path) -> dict[str, Any]:
    study_id = study_dir.name.upper()
    project_ids: list[str] = []
    project_dois: list[str] = []
    project_titles: list[str] = []
    publications: list[dict[str, str]] = []

    combined = _load_json(study_dir / "__merit_combined.json")
    project_block = combined.get("project_block") if isinstance(combined.get("project_block"), dict) else {}
    summary = combined.get("summary") if isinstance(combined.get("summary"), dict) else {}
    if project_block:
        project_titles.append(_clean(project_block.get("PROJECT_TITLE")))
        for key, value in project_block.items():
            key_text = str(key).upper()
            value_text = _clean(value)
            if "DOI" in key_text or "DOI" in value_text or "10.21228/" in value_text:
                doi = _normalise_doi(value_text)
                if doi:
                    project_dois.append(doi)
            if "PUBLICATION" in key_text:
                _add_publication(publications, value, f"{study_id}/__merit_combined.json:project_block.{key}")

    for json_path in sorted(study_dir.glob("AN*/json/*_mwtab.json")):
        payload = _load_json(json_path)
        header = payload.get("METABOLOMICS WORKBENCH")
        if isinstance(header, dict):
            project_id = _clean(header.get("PROJECT_ID"))
            if project_id:
                project_ids.append(project_id)
        project = payload.get("PROJECT")
        if isinstance(project, dict):
            project_titles.append(_clean(project.get("PROJECT_TITLE")))
            for key, value in project.items():
                key_text = str(key).upper()
                value_text = _clean(value)
                if "DOI" in key_text or "DOI" in value_text or "10.21228/" in value_text:
                    doi = _normalise_doi(value_text)
                    if doi:
                        project_dois.append(doi)
                if "PUBLICATION" in key_text:
                    _add_publication(publications, value, f"{json_path.relative_to(DUMP_ROOT)}:PROJECT.{key}")
        text = json.dumps(payload, ensure_ascii=False)
        project_dois.extend(_normalise_doi(m.group(0)) for m in DOI_RE.finditer(text))

    for txt_path in sorted(study_dir.glob("AN*/json/*_mwtab.txt")):
        text = txt_path.read_text(encoding="utf-8", errors="ignore")
        for match in PROJECT_ID_RE.finditer(text[:2000]):
            project_ids.append(_clean(match.group(1)))
        project_dois.extend(_normalise_doi(m.group(0)) for m in DOI_RE.finditer(text))
        for line in text.splitlines():
            if "PUBLICATION" in line.upper():
                parts = re.split(r"\s*[:=]\s*", line, maxsplit=1)
                if len(parts) == 2:
                    _add_publication(publications, parts[1], f"{txt_path.relative_to(DUMP_ROOT)}")

    def unique(values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            cleaned = _clean(value)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                out.append(cleaned)
        return out

    project_ids_u = unique(project_ids)
    project_dois_u = unique(project_dois)
    project_titles_u = unique(project_titles)

    return {
        "study_id": study_id,
        "project_id": project_ids_u[0] if project_ids_u else "",
        "project_ids_all": project_ids_u,
        "project_id_source": "local_mwtab_header" if project_ids_u else "",
        "project_doi": project_dois_u[0] if project_dois_u else "",
        "project_dois_all": project_dois_u,
        "project_doi_source": "local_mwtab_metadata" if project_dois_u else "",
        "project_title": project_titles_u[0] if project_titles_u else "",
        "study_title": _clean(summary.get("study_title")),
        "doi_url": f"https://doi.org/{project_dois_u[0]}" if project_dois_u else "",
        "related_publications": publications,
        "n_related_publications": len(publications),
        "citation_template_version": "metabolomics_workbench_project_template",
    }


def main() -> None:
    registry_project_ids = _fetch_workbench_available_registry()
    studies: dict[str, dict[str, Any]] = {}
    for study_dir in sorted(DUMP_ROOT.glob("ST*")):
        if study_dir.is_dir():
            item = _extract_from_study(study_dir)
            registry_ids = registry_project_ids.get(item["study_id"], [])
            if registry_ids:
                merged = []
                for value in list(item.get("project_ids_all", [])) + registry_ids:
                    if value and value not in merged:
                        merged.append(value)
                item["project_ids_all"] = merged
                if not item.get("project_id"):
                    item["project_id"] = registry_ids[0]
                    item["project_id_source"] = "workbench_available_rest_api"
                elif item.get("project_id") in registry_ids:
                    item["project_id_source"] = str(item.get("project_id_source") or "local_mwtab_header")
                else:
                    item["project_id_source"] = str(item.get("project_id_source") or "local_mwtab_header") + "+workbench_available_rest_api"
            studies[item["study_id"]] = item

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_dump": str(DUMP_ROOT.name),
        "workbench_available_registry_url": WORKBENCH_AVAILABLE_URL,
        "n_registry_studies": len(registry_project_ids),
        "n_studies": len(studies),
        "n_with_project_id": sum(1 for row in studies.values() if row.get("project_id")),
        "n_project_ids_from_workbench_available_api": sum(
            1 for row in studies.values()
            if row.get("project_id_source") == "workbench_available_rest_api"
        ),
        "n_with_project_doi": sum(1 for row in studies.values() if row.get("project_doi")),
        "n_with_related_publications": sum(
            1 for row in studies.values() if row.get("related_publications")
        ),
        "studies": studies,
    }
    OUT_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    print(
        f"studies={summary['n_studies']} "
        f"project_id={summary['n_with_project_id']} "
        f"project_doi={summary['n_with_project_doi']} "
        f"related_publications={summary['n_with_related_publications']}"
    )


if __name__ == "__main__":
    main()
