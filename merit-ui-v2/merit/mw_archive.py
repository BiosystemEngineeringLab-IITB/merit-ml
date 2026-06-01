from __future__ import annotations

import csv
import gzip
import io
import json
import os
import sqlite3
import sys
import tarfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, TextIO

from merit.remote import RemoteFetchError, fetch_json, fetch_text
from merit.utils import read_delimited_table, sha256_file, sha256_text, stable_json_dumps
from merit.version import __version__

API_BASE = "https://www.metabolomicsworkbench.org/rest"
ROOT_ENV = "METABODRIN_MW_ROOT"
ROOT_ALIASES = ("mw-dump-latest-confirmation", "mw_dump", "mw-dump")
MWTAB_DATA_STARTS = {
    "MS_METABOLITE_DATA_START",
    "NMR_BINNED_DATA_START",
    "NMR_METABOLITE_DATA_START",
    "EXTENDED_MS_METABOLITE_DATA_START",
    "EXTENDED_NMR_METABOLITE_DATA_START",
    "DIRECT_INFUSION_METABOLITE_DATA_START",
    "METABOLITE_DATA_START",
}
ASSET_PRIORITY = {
    "results": 0,
    "datatable": 1,
    "mwtab": 2,
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _quote_study_id(study_id: str) -> str:
    return study_id.strip().upper()


def resolve_mw_archive_root(root: str | Path | None = None, workspace: Path | None = None) -> Path:
    if root:
        return Path(root).expanduser()
    env_root = os.environ.get(ROOT_ENV, "").strip()
    if env_root:
        return Path(env_root).expanduser()
    if workspace is not None:
        for alias in ROOT_ALIASES:
            candidate = workspace / alias
            if candidate.exists():
                return candidate
    return Path.home() / ".cache" / "merit" / "mw"


def catalog_path(root: str | Path) -> Path:
    return resolve_mw_archive_root(root) / "catalog.sqlite"


def manifest_path(root: str | Path, study_id: str) -> Path:
    return resolve_mw_archive_root(root) / "studies" / _quote_study_id(study_id) / "manifest.json"


def init_mw_archive(root: str | Path | None = None, workspace: Path | None = None) -> Path:
    store_root = resolve_mw_archive_root(root, workspace=workspace)
    for relative in ("objects/json", "objects/tabular", "studies", "snapshots", "logs"):
        (store_root / relative).mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(catalog_path(store_root)) as conn:
        _ensure_schema(conn)
    return store_root


def _default_log_path(root: Path, prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return root / "logs" / f"{prefix}_{stamp}.jsonl"


def _short_message(value: str, limit: int = 88) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


class SyncReporter:
    def __init__(
        self,
        total: int,
        *,
        label: str,
        log_path: Path | None = None,
        verbose: bool = False,
        quiet: bool = False,
        stream: TextIO | None = None,
    ) -> None:
        self.total = total
        self.label = label
        self.log_path = log_path
        self.verbose = verbose
        self.quiet = quiet
        self.stream = stream if stream is not None else sys.stderr
        self.completed = 0
        self.successful = 0
        self.failed = 0
        self._last_was_progress = False
        self._log_handle: TextIO | None = None
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _write_log(self, event: str, payload: dict[str, Any]) -> None:
        if self._log_handle is None:
            return
        record = {
            "timestamp": _now_utc(),
            "event": event,
            **payload,
        }
        self._log_handle.write(
            json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n"
        )
        self._log_handle.flush()

    def _emit_line(self, text: str) -> None:
        if self.quiet:
            return
        if self._last_was_progress:
            self.stream.write("\n")
            self._last_was_progress = False
        self.stream.write(text + "\n")
        self.stream.flush()

    def _emit_progress(self, current_label: str) -> None:
        if self.quiet:
            return
        width = 24
        filled = 0 if self.total == 0 else int(width * self.completed / self.total)
        bar = "#" * filled + "-" * (width - filled)
        message = (
            f"[{bar}] {self.completed}/{self.total} "
            f"ok={self.successful} fail={self.failed} "
            f"{_short_message(current_label, 44)}"
        )
        if getattr(self.stream, "isatty", lambda: False)():
            self.stream.write("\r" + message.ljust(120))
            self.stream.flush()
            self._last_was_progress = True
        else:
            self.stream.write(message + "\n")
            self.stream.flush()
            self._last_was_progress = False

    def start(self, *, root: Path, requested_study_count: int) -> None:
        self._write_log(
            "sync_start",
            {
                "label": self.label,
                "root": str(root),
                "requested_study_count": requested_study_count,
                "log_path": str(self.log_path) if self.log_path else "",
            },
        )
        if self.quiet:
            return
        self._emit_line(
            f"{self.label}: starting {requested_study_count} study sync(s) under {root}"
        )
        if self.log_path:
            self._emit_line(f"{self.label}: checkpoint log -> {self.log_path}")

    def study_start(self, index: int, study_id: str) -> None:
        self._write_log("study_start", {"index": index, "study_id": study_id})
        if self.verbose:
            self._emit_line(f"{self.label}: [{index}/{self.total}] updating {study_id}")
        else:
            self._emit_progress(f"updating {study_id}")

    def study_result(self, index: int, result: dict[str, Any]) -> None:
        self.completed += 1
        if result.get("status") == "failed":
            self.failed += 1
        else:
            self.successful += 1
        self._write_log("study_result", {"index": index, "result": result})
        study_id = result.get("study_id", "")
        status = result.get("status", "")
        detail = f"{study_id} {status}"
        if self.verbose or status == "failed":
            if status == "failed":
                detail = f"{detail}: {_short_message(result.get('error', ''), 120)}"
            elif result.get("selected_asset_count") is not None:
                detail = (
                    f"{detail}: analyses={result.get('analysis_count', 0)} "
                    f"selected_assets={result.get('selected_asset_count', 0)}"
                )
            self._emit_line(f"{self.label}: [{index}/{self.total}] {detail}")
        self._emit_progress(detail)

    def finalize(self, summary: dict[str, Any]) -> None:
        self._write_log("sync_complete", {"summary": summary})
        if self._last_was_progress and not self.quiet:
            self.stream.write("\n")
            self.stream.flush()
            self._last_was_progress = False
        if self.quiet:
            return
        self._emit_line(
            f"{self.label}: completed {summary.get('requested_study_count', 0)} study sync(s) "
            f"ok={summary.get('successful_study_count', 0)} fail={summary.get('failed_study_count', 0)}"
        )


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS studies (
            study_id TEXT PRIMARY KEY,
            repository TEXT NOT NULL,
            title TEXT,
            organism TEXT,
            disease TEXT,
            analysis_type TEXT,
            submit_date TEXT,
            release_date TEXT,
            json_sha256 TEXT,
            json_object_path TEXT,
            manifest_path TEXT,
            n_analyses INTEGER NOT NULL DEFAULT 0,
            preferred_asset_count INTEGER NOT NULL DEFAULT 0,
            selected_asset_kinds TEXT,
            last_synced_at TEXT,
            sync_status TEXT,
            parser_version TEXT
        );
        CREATE TABLE IF NOT EXISTS analyses (
            study_id TEXT NOT NULL,
            analysis_id TEXT NOT NULL,
            analysis_type TEXT,
            ion_mode TEXT,
            chromatography_type TEXT,
            units TEXT,
            ms_type TEXT,
            preferred_asset_kind TEXT,
            preferred_asset_path TEXT,
            reported_n_metabolites TEXT,
            PRIMARY KEY (study_id, analysis_id)
        );
        CREATE TABLE IF NOT EXISTS assets (
            study_id TEXT NOT NULL,
            analysis_id TEXT NOT NULL,
            asset_kind TEXT NOT NULL,
            original_name TEXT,
            object_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            compression TEXT NOT NULL,
            has_features INTEGER NOT NULL DEFAULT 0,
            selected INTEGER NOT NULL DEFAULT 0,
            source_url TEXT,
            created_at TEXT,
            PRIMARY KEY (study_id, analysis_id, asset_kind, sha256)
        );
        CREATE TABLE IF NOT EXISTS study_versions (
            study_id TEXT NOT NULL,
            json_sha256 TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            synced_at TEXT NOT NULL,
            is_current INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (study_id, manifest_sha256)
        );
        CREATE TABLE IF NOT EXISTS sync_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            mode TEXT NOT NULL,
            requested_study_count INTEGER NOT NULL DEFAULT 0,
            successful_study_count INTEGER NOT NULL DEFAULT 0,
            failed_study_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_assets_study_selected ON assets(study_id, selected);
        CREATE INDEX IF NOT EXISTS idx_analyses_study ON analyses(study_id);
        """
    )
    conn.commit()


def _study_endpoint(study_id: str, output_item: str) -> str:
    return f"{API_BASE}/study/study_id/{_quote_study_id(study_id)}/{output_item}"


def _analysis_endpoint(analysis_id: str, output_item: str) -> str:
    return f"{API_BASE}/study/analysis_id/{analysis_id}/{output_item}"


def _raw_download_page(study_id: str) -> str:
    return (
        "https://www.metabolomicsworkbench.org/data/DRCCStudySummary.php"
        f"?Mode=SetupRawDataDownload&StudyID={_quote_study_id(study_id)}"
    )


def _available_studies_endpoint() -> str:
    return f"{API_BASE}/study/study_id/ST/available"


def _as_rows(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        rows = []
        for item in payload:
            if isinstance(item, dict):
                rows.append(item)
            elif isinstance(item, str):
                rows.append({"study_id": item})
        return rows
    if isinstance(payload, dict):
        if payload and all(isinstance(value, dict) for value in payload.values()):
            return [value for value in payload.values() if isinstance(value, dict)]
        return [payload]
    return []


def _extract_study_id(row: dict[str, Any]) -> str:
    for key in ("study_id", "STUDY_ID", "Study ID", "study"):
        value = row.get(key)
        if value:
            return str(value).strip().upper()
    return ""


def _extract_analysis_id(row: dict[str, Any]) -> str:
    for key in ("analysis_id", "ANALYSIS_ID", "Analysis_ID", "Analysis ID"):
        value = row.get(key)
        if value:
            return str(value).strip().upper()
    return ""


def list_available_workbench_studies(limit: int | None = None) -> list[str]:
    payload = fetch_json(_available_studies_endpoint())
    study_ids = []
    for row in _as_rows(payload):
        study_id = _extract_study_id(row)
        if study_id:
            study_ids.append(study_id)
    if not study_ids and isinstance(payload, list):
        study_ids = [str(item).strip().upper() for item in payload if str(item).strip().upper().startswith("ST")]
    study_ids = sorted(dict.fromkeys(study_ids))
    if limit is not None:
        study_ids = study_ids[:limit]
    return study_ids


def _parse_delimited_text(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    sample = text[:4096]
    delimiter = "\t"
    first_line = sample.splitlines()[0] if sample else ""
    tab_count = first_line.count("\t")
    comma_count = first_line.count(",")
    if sample:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters="\t,")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "\t"
    if delimiter == "," and tab_count >= 2 and tab_count > comma_count:
        delimiter = "\t"
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [dict(row) for row in reader]


def _parse_mwtab_text(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    in_section = False
    data_lines: list[str] = []
    for line in lines:
        token = line.strip()
        upper = token.upper()
        if not in_section:
            if upper in MWTAB_DATA_STARTS:
                in_section = True
            continue
        if upper.endswith("_END"):
            break
        if token:
            data_lines.append(line)
    if not data_lines:
        return []
    return _parse_delimited_text("\n".join(data_lines))


def _has_feature_columns(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    return len(rows[0].keys()) > 2


def _results_filename_from_html(html: str, study_id: str, analysis_id: str) -> str | None:
    import re

    pattern = rf"{study_id}_{analysis_id}_Results\.txt"
    match = re.search(pattern, html, re.IGNORECASE)
    return match.group(0) if match else None


def _relative_path(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _write_gzip_object(path: Path, payload: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with gzip.open(path, "wb") as handle:
            handle.write(payload)
    return path.stat().st_size


def _store_text_object(root: Path, namespace: str, suffix: str, text: str) -> dict[str, Any]:
    payload = text.encode("utf-8")
    digest = sha256(payload).hexdigest()
    object_path = root / "objects" / namespace / digest[:2] / f"{digest}{suffix}"
    size_bytes = _write_gzip_object(object_path, payload)
    return {
        "sha256": digest,
        "path": _relative_path(root, object_path),
        "compression": "gzip",
        "size_bytes": size_bytes,
    }


def _store_json_object(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    text = stable_json_dumps(payload)
    stored = _store_text_object(root, "json", ".json.gz", text)
    stored["raw_sha256"] = sha256_text(text)
    return stored


def _load_json_file(path: Path) -> dict[str, Any]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.loads(handle.read())


def load_workbench_manifest(root: str | Path, study_id: str) -> dict[str, Any]:
    manifest_file = manifest_path(root, study_id)
    if not manifest_file.exists():
        raise FileNotFoundError(f"Workbench manifest not found: {manifest_file}")
    return json.loads(manifest_file.read_text())


def _fetch_study_metadata(study_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    summary_rows = _as_rows(fetch_json(_study_endpoint(study_id, "summary")))
    if not summary_rows:
        raise RemoteFetchError(f"No summary rows returned for {_quote_study_id(study_id)}")
    summary = summary_rows[0]
    disease_rows = _as_rows(fetch_json(_study_endpoint(study_id, "disease")))
    factors_rows = _as_rows(fetch_json(_study_endpoint(study_id, "factors")))
    analyses_rows = _as_rows(fetch_json(_study_endpoint(study_id, "analysis")))
    metabolites_rows = _as_rows(fetch_json(_study_endpoint(study_id, "metabolites")))
    factors_payload: dict[str, Any] = {str(index): row for index, row in enumerate(factors_rows, start=1)}
    analyses_payload: dict[str, Any] = {str(index): row for index, row in enumerate(analyses_rows, start=1)}
    combined_payload = {
        "input": _quote_study_id(study_id),
        "generated_by": "merit_mw_sync",
        "type": "STUDY",
        "summary": summary,
        "disease": disease_rows[0] if disease_rows else {},
        "factors": factors_payload,
        "analyses": analyses_payload,
        "n_metabolites": {},
        "metabolites": metabolites_rows,
        "project_summary": summary.get("study_title", ""),
        "collection_summary": "",
        "sampleprep_summary": "",
        "project_block": None,
        "collection_block": None,
        "sampleprep_block": None,
        "species": {
            "Study ID": _quote_study_id(study_id),
            "Latin name": summary.get("species", ""),
            "Common name": summary.get("species", ""),
        },
        "source": {"Study ID": _quote_study_id(study_id), "Sample source": ""},
    }
    return combined_payload, analyses_rows, metabolites_rows


def _fetch_analysis_assets(
    root: Path,
    study_id: str,
    analysis_row: dict[str, Any],
    include_mwtab: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | int]:
    analysis_id = _extract_analysis_id(analysis_row)
    if not analysis_id:
        return [], None, ""
    assets: list[dict[str, Any]] = []
    raw_download_html: str | None = None
    n_metabolites: str | int = ""

    try:
        datatable_text = fetch_text(_analysis_endpoint(analysis_id, "datatable/file"))
        datatable_rows = _parse_delimited_text(datatable_text)
        stored = _store_text_object(root, "tabular", ".datatable.tsv.gz", datatable_text)
        assets.append(
            {
                "analysis_id": analysis_id,
                "asset_kind": "datatable",
                "original_name": f"{analysis_id}.datatable.tsv.gz",
                "source_url": _analysis_endpoint(analysis_id, "datatable/file"),
                "has_features": _has_feature_columns(datatable_rows),
                **stored,
            }
        )
    except RemoteFetchError:
        datatable_rows = []

    if not _has_feature_columns(datatable_rows):
        try:
            raw_download_html = raw_download_html or fetch_text(_raw_download_page(study_id))
            filename = _results_filename_from_html(raw_download_html, _quote_study_id(study_id), analysis_id)
            if filename:
                results_text = fetch_text(f"https://www.metabolomicsworkbench.org/studydownload/{filename}")
                results_rows = _parse_delimited_text(results_text)
                stored = _store_text_object(root, "tabular", ".txt.gz", results_text)
                assets.append(
                    {
                        "analysis_id": analysis_id,
                        "asset_kind": "results",
                        "original_name": filename,
                        "source_url": f"https://www.metabolomicsworkbench.org/studydownload/{filename}",
                        "has_features": _has_feature_columns(results_rows),
                        **stored,
                    }
                )
        except RemoteFetchError:
            pass

    if include_mwtab:
        try:
            mwtab_text = fetch_text(_analysis_endpoint(analysis_id, "mwtab/txt"))
            mwtab_rows = _parse_mwtab_text(mwtab_text)
            stored = _store_text_object(root, "tabular", ".mwtab.txt.gz", mwtab_text)
            assets.append(
                {
                    "analysis_id": analysis_id,
                    "asset_kind": "mwtab",
                    "original_name": f"{analysis_id}.mwtab.txt.gz",
                    "source_url": _analysis_endpoint(analysis_id, "mwtab/txt"),
                    "has_features": _has_feature_columns(mwtab_rows),
                    **stored,
                }
            )
        except RemoteFetchError:
            pass

    selected = _select_preferred_asset(assets)
    if selected is not None:
        if selected["asset_kind"] == "mwtab":
            rows = _parse_mwtab_text(_read_text_object(root / selected["path"]))
        else:
            rows = _parse_delimited_text(_read_text_object(root / selected["path"]))
        if rows:
            n_metabolites = max(0, len(rows[0].keys()) - 2)
    return assets, selected, n_metabolites


def _read_text_object(path: Path) -> str:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return handle.read()


def _select_preferred_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not assets:
        return None
    with_features = [asset for asset in assets if asset.get("has_features")]
    candidates = with_features if with_features else assets
    return sorted(candidates, key=lambda item: (ASSET_PRIORITY.get(item["asset_kind"], 99), item["original_name"]))[0]


def _manifest_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary", {}) or {}
    disease_payload = payload.get("disease", {}) or {}
    return {
        "title": summary.get("study_title", ""),
        "organism": summary.get("species", ""),
        "disease": disease_payload.get("Disease", ""),
        "analysis_type": summary.get("analysis_type", ""),
        "submit_date": (
            summary.get("submit_date") or summary.get("study_submit_date") or
            summary.get("submission_date") or summary.get("date_submitted") or ""
        ),
        "release_date": (
            summary.get("release_date") or summary.get("study_release_date") or
            summary.get("public_release_date") or summary.get("date_released") or ""
        ),
    }


def _write_manifest(root: Path, study_id: str, manifest: dict[str, Any]) -> Path:
    study_dir = root / "studies" / _quote_study_id(study_id)
    study_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = study_dir / "manifest.json"
    manifest_file.write_text(stable_json_dumps(manifest))
    return manifest_file


def _upsert_manifest_catalog(root: Path, manifest: dict[str, Any]) -> None:
    init_mw_archive(root)
    manifest_relpath = _relative_path(root, manifest_path(root, manifest["study_id"]))
    summary = manifest.get("summary", {})
    selected_assets = manifest.get("selected_assets", [])
    selected_kinds = ",".join(sorted(dict.fromkeys(item.get("selected_kind", "") for item in selected_assets if item.get("selected_kind"))))
    with sqlite3.connect(catalog_path(root)) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO studies (
                study_id, repository, title, organism, disease, analysis_type,
                submit_date, release_date, json_sha256, json_object_path,
                manifest_path, n_analyses, preferred_asset_count, selected_asset_kinds,
                last_synced_at, sync_status, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(study_id) DO UPDATE SET
                repository=excluded.repository,
                title=excluded.title,
                organism=excluded.organism,
                disease=excluded.disease,
                analysis_type=excluded.analysis_type,
                submit_date=excluded.submit_date,
                release_date=excluded.release_date,
                json_sha256=excluded.json_sha256,
                json_object_path=excluded.json_object_path,
                manifest_path=excluded.manifest_path,
                n_analyses=excluded.n_analyses,
                preferred_asset_count=excluded.preferred_asset_count,
                selected_asset_kinds=excluded.selected_asset_kinds,
                last_synced_at=excluded.last_synced_at,
                sync_status=excluded.sync_status,
                parser_version=excluded.parser_version
            """,
            (
                manifest["study_id"],
                manifest.get("repository", "workbench"),
                summary.get("title", ""),
                summary.get("organism", ""),
                summary.get("disease", ""),
                summary.get("analysis_type", ""),
                summary.get("submit_date", ""),
                summary.get("release_date", ""),
                manifest.get("json", {}).get("sha256", ""),
                manifest.get("json", {}).get("path", ""),
                manifest_relpath,
                len(manifest.get("analysis_index", {})),
                len(selected_assets),
                selected_kinds,
                manifest.get("last_synced_at", ""),
                manifest.get("sync_status", ""),
                manifest.get("parser_version", __version__),
            ),
        )
        conn.execute("DELETE FROM analyses WHERE study_id = ?", (manifest["study_id"],))
        for analysis_id, analysis in sorted((manifest.get("analysis_index") or {}).items()):
            selected = next((item for item in selected_assets if item.get("analysis_id") == analysis_id), {})
            reported_n = (manifest.get("reported_n_metabolites") or {}).get(analysis_id, "")
            conn.execute(
                """
                INSERT INTO analyses (
                    study_id, analysis_id, analysis_type, ion_mode, chromatography_type,
                    units, ms_type, preferred_asset_kind, preferred_asset_path, reported_n_metabolites
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest["study_id"],
                    analysis_id,
                    analysis.get("analysis_type", ""),
                    analysis.get("ion_mode", ""),
                    analysis.get("chromatography_type", ""),
                    analysis.get("units", ""),
                    analysis.get("ms_type", ""),
                    selected.get("selected_kind", ""),
                    selected.get("selected_path", ""),
                    str(reported_n),
                ),
            )
        conn.execute("DELETE FROM assets WHERE study_id = ?", (manifest["study_id"],))
        for asset in manifest.get("tabular_assets", []):
            conn.execute(
                """
                INSERT INTO assets (
                    study_id, analysis_id, asset_kind, original_name, object_path,
                    sha256, size_bytes, compression, has_features, selected, source_url, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest["study_id"],
                    asset.get("analysis_id", ""),
                    asset.get("asset_kind", ""),
                    asset.get("original_name", ""),
                    asset.get("path", ""),
                    asset.get("sha256", ""),
                    int(asset.get("size_bytes", 0)),
                    asset.get("compression", ""),
                    int(bool(asset.get("has_features"))),
                    int(bool(asset.get("selected"))),
                    asset.get("source_url", ""),
                    manifest.get("last_synced_at", ""),
                ),
            )
        manifest_hash = sha256_text(stable_json_dumps(manifest))
        conn.execute("UPDATE study_versions SET is_current = 0 WHERE study_id = ?", (manifest["study_id"],))
        conn.execute(
            """
            INSERT OR REPLACE INTO study_versions (
                study_id, json_sha256, manifest_sha256, synced_at, is_current
            ) VALUES (?, ?, ?, ?, 1)
            """,
            (
                manifest["study_id"],
                manifest.get("json", {}).get("sha256", ""),
                manifest_hash,
                manifest.get("last_synced_at", ""),
            ),
        )
        conn.commit()


def sync_workbench_study(
    study_id: str,
    root: str | Path | None = None,
    force: bool = False,
    include_mwtab: bool = True,
    workspace: Path | None = None,
) -> dict[str, Any]:
    store_root = init_mw_archive(root, workspace=workspace)
    study_id = _quote_study_id(study_id)
    existing = None
    manifest_file = manifest_path(store_root, study_id)
    if manifest_file.exists():
        existing = json.loads(manifest_file.read_text())

    metadata_payload, analyses_rows, _ = _fetch_study_metadata(study_id)
    metadata_sha256 = sha256_text(stable_json_dumps(metadata_payload))
    if existing and not force and existing.get("metadata_sha256") == metadata_sha256:
        existing["last_synced_at"] = _now_utc()
        existing["sync_status"] = "unchanged"
        _write_manifest(store_root, study_id, existing)
        _upsert_manifest_catalog(store_root, existing)
        return {
            "study_id": study_id,
            "status": "unchanged",
            "analysis_count": len(existing.get("analysis_index", {})),
            "selected_asset_count": len(existing.get("selected_assets", [])),
            "manifest_path": str(manifest_file),
            "catalog_path": str(catalog_path(store_root)),
        }

    selected_assets: list[dict[str, Any]] = []
    tabular_assets: list[dict[str, Any]] = []
    reported_n_metabolites: dict[str, Any] = {}
    n_metabolites_payload: dict[str, Any] = {}
    analysis_index: dict[str, dict[str, Any]] = {}
    analysis_payload = metadata_payload.get("analyses", {}) or {}
    for index, row in enumerate(analyses_rows, start=1):
        analysis_id = _extract_analysis_id(row)
        if not analysis_id:
            continue
        analysis_index[analysis_id] = row
        assets, selected, n_metabolites = _fetch_analysis_assets(
            store_root,
            study_id,
            row,
            include_mwtab=include_mwtab,
        )
        for asset in assets:
            asset["selected"] = bool(selected and asset["sha256"] == selected["sha256"])
        tabular_assets.extend(assets)
        if selected is not None:
            selected_assets.append(
                {
                    "analysis_id": analysis_id,
                    "selected_path": selected["path"],
                    "selected_name": selected["original_name"],
                    "selected_kind": selected["asset_kind"],
                    "selected_has_features": bool(selected.get("has_features")),
                    "candidates": [asset["path"] for asset in assets],
                }
            )
        reported_n_metabolites[analysis_id] = n_metabolites
        n_metabolites_payload[str(index)] = n_metabolites
        n_metabolites_payload[analysis_id] = n_metabolites
        if str(index) in analysis_payload and isinstance(analysis_payload[str(index)], dict):
            analysis_payload[str(index)]["analysis_id"] = analysis_id

    metadata_payload["n_metabolites"] = n_metabolites_payload
    json_object = _store_json_object(store_root, metadata_payload)
    summary = _manifest_summary(metadata_payload)
    manifest = {
        "schema_version": "1",
        "repository": "workbench",
        "study_id": study_id,
        "metadata_sha256": metadata_sha256,
        "json": json_object,
        "summary": summary,
        "analysis_index": analysis_index,
        "reported_n_metabolites": reported_n_metabolites,
        "selected_assets": selected_assets,
        "tabular_assets": tabular_assets,
        "tabular_message": (
            f"Resolved {len(selected_assets)} preferred tabular asset(s) from the managed archive."
            if selected_assets
            else "No tabular assets were resolved for this study."
        ),
        "last_synced_at": _now_utc(),
        "sync_status": "updated",
        "parser_version": __version__,
    }
    _write_manifest(store_root, study_id, manifest)
    _upsert_manifest_catalog(store_root, manifest)
    return {
        "study_id": study_id,
        "status": "updated",
        "analysis_count": len(analysis_index),
        "selected_asset_count": len(selected_assets),
        "manifest_path": str(manifest_path(store_root, study_id)),
        "catalog_path": str(catalog_path(store_root)),
    }


def sync_workbench_archive(
    root: str | Path | None = None,
    study_ids: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    include_mwtab: bool = True,
    workspace: Path | None = None,
    verbose: bool = False,
    quiet: bool = False,
    log_path: str | Path | None = None,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    store_root = init_mw_archive(root, workspace=workspace)
    resolved_log_path = Path(log_path) if log_path else _default_log_path(store_root, "mw_sync")
    if study_ids:
        requested_ids = [_quote_study_id(study_id) for study_id in study_ids]
    else:
        requested_ids = list_available_workbench_studies(limit=limit)
    if limit is not None:
        requested_ids = requested_ids[:limit]
    reporter = SyncReporter(
        total=len(requested_ids),
        label="mw-sync",
        log_path=resolved_log_path,
        verbose=verbose,
        quiet=quiet,
        stream=stream,
    )
    started_at = _now_utc()
    try:
        reporter.start(root=store_root, requested_study_count=len(requested_ids))
        with sqlite3.connect(catalog_path(store_root)) as conn:
            _ensure_schema(conn)
            cursor = conn.execute(
                "INSERT INTO sync_runs (started_at, mode, requested_study_count, notes) VALUES (?, ?, ?, ?)",
                (started_at, "full" if not study_ids else "targeted", len(requested_ids), ""),
            )
            run_id = int(cursor.lastrowid)
            conn.commit()
        successful = 0
        failed = 0
        results: list[dict[str, Any]] = []
        for index, study_id in enumerate(requested_ids, start=1):
            reporter.study_start(index, study_id)
            try:
                result = sync_workbench_study(
                    study_id,
                    root=store_root,
                    force=force,
                    include_mwtab=include_mwtab,
                    workspace=workspace,
                )
                results.append(result)
                successful += 1
            except Exception as exc:
                failed += 1
                result = {"study_id": study_id, "status": "failed", "error": str(exc)}
                results.append(result)
            reporter.study_result(index, result)
        completed_at = _now_utc()
        summary = {
            "root": str(store_root),
            "catalog_path": str(catalog_path(store_root)),
            "requested_study_count": len(requested_ids),
            "successful_study_count": successful,
            "failed_study_count": failed,
            "results": results,
            "log_path": str(resolved_log_path),
        }
        with sqlite3.connect(catalog_path(store_root)) as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET completed_at = ?, successful_study_count = ?, failed_study_count = ?, notes = ?
                WHERE run_id = ?
                """,
                (completed_at, successful, failed, stable_json_dumps(results), run_id),
            )
            conn.commit()
        reporter.finalize(summary)
        return summary
    finally:
        reporter.close()


def pull_workbench_study(
    study_id: str,
    root: str | Path | None = None,
    force: bool = False,
    include_mwtab: bool = True,
    workspace: Path | None = None,
    verbose: bool = False,
    quiet: bool = False,
    log_path: str | Path | None = None,
    stream: TextIO | None = None,
) -> dict[str, Any]:
    store_root = init_mw_archive(root, workspace=workspace)
    resolved_log_path = Path(log_path) if log_path else _default_log_path(store_root, f"mw_pull_{_quote_study_id(study_id).lower()}")
    reporter = SyncReporter(
        total=1,
        label="mw-pull",
        log_path=resolved_log_path,
        verbose=verbose,
        quiet=quiet,
        stream=stream,
    )
    try:
        reporter.start(root=store_root, requested_study_count=1)
        reporter.study_start(1, _quote_study_id(study_id))
        try:
            result = sync_workbench_study(
                study_id=study_id,
                root=store_root,
                force=force,
                include_mwtab=include_mwtab,
                workspace=workspace,
            )
        except Exception as exc:
            result = {"study_id": _quote_study_id(study_id), "status": "failed", "error": str(exc)}
            reporter.study_result(1, result)
            summary = {
                "root": str(store_root),
                "catalog_path": str(catalog_path(store_root)),
                "requested_study_count": 1,
                "successful_study_count": 0,
                "failed_study_count": 1,
                "results": [result],
                "log_path": str(resolved_log_path),
            }
            reporter.finalize(summary)
            raise
        reporter.study_result(1, result)
        summary = {
            "root": str(store_root),
            "catalog_path": str(catalog_path(store_root)),
            "requested_study_count": 1,
            "successful_study_count": 1,
            "failed_study_count": 0,
            "results": [result],
            "log_path": str(resolved_log_path),
        }
        reporter.finalize(summary)
        result["log_path"] = str(resolved_log_path)
        return result
    finally:
        reporter.close()


def _table_kind_from_name(name: str) -> str:
    lowered = name.lower()
    if "_results.txt" in lowered:
        return "results"
    if ".datatable.tsv" in lowered:
        return "datatable"
    if (
        ".mwtab" in lowered
        or "_mwtab" in lowered
        or ("mwtab" in lowered and (lowered.endswith(".txt") or lowered.endswith(".txt.gz") or lowered.endswith(".mwtab")))
    ):
        return "mwtab"
    return "unknown"


def _infer_analysis_id_from_name(name: str, study_id: str) -> str:
    import re

    match = re.search(r"(AN\d+)", name, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    lowered = name.lower()
    if ".mwtab" in lowered or "_mwtab" in lowered or "mwtab" in lowered:
        return study_id
    return Path(name).stem.upper()


def _read_legacy_rows(path: Path) -> list[dict[str, Any]]:
    lowered = path.name.lower()
    if ".mwtab" in lowered or "_mwtab" in lowered or "mwtab" in lowered:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            return _parse_mwtab_text(handle.read())
    return read_delimited_table(path)


def _legacy_manifest(root: Path, study_id: str) -> dict[str, Any] | None:
    json_path = root / "json" / f"{study_id}.json"
    if not json_path.exists():
        return None
    payload = _load_json_file(json_path)
    datatable_dir = root / "datatable" / study_id
    candidates: dict[str, list[Path]] = {}
    if datatable_dir.exists():
        for path in sorted(datatable_dir.iterdir()):
            if not path.is_file():
                continue
            kind = _table_kind_from_name(path.name)
            if kind == "unknown":
                continue
            analysis_id = _infer_analysis_id_from_name(path.name, study_id)
            candidates.setdefault(analysis_id, []).append(path)
    selected_assets = []
    tabular_assets = []
    for analysis_id, paths in sorted(candidates.items()):
        selected = None
        for path in sorted(paths, key=lambda item: (ASSET_PRIORITY.get(_table_kind_from_name(item.name), 99), item.name)):
            rows = _read_legacy_rows(path)
            has_features = _has_feature_columns(rows)
            asset = {
                "analysis_id": analysis_id,
                "asset_kind": _table_kind_from_name(path.name),
                "original_name": path.name,
                "path": _relative_path(root, path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
                "compression": "gzip" if path.suffix == ".gz" else "none",
                "has_features": has_features,
                "selected": False,
                "source_url": "",
            }
            tabular_assets.append(asset)
            if selected is None and has_features:
                selected = asset
        if selected is None and paths:
            first = sorted(paths, key=lambda item: (ASSET_PRIORITY.get(_table_kind_from_name(item.name), 99), item.name))[0]
            selected = next(asset for asset in tabular_assets if asset["path"] == _relative_path(root, first))
        if selected is not None:
            selected["selected"] = True
            selected_assets.append(
                {
                    "analysis_id": analysis_id,
                    "selected_path": selected["path"],
                    "selected_name": selected["original_name"],
                    "selected_kind": selected["asset_kind"],
                    "selected_has_features": bool(selected.get("has_features")),
                    "candidates": [asset["path"] for asset in tabular_assets if asset["analysis_id"] == analysis_id],
                }
            )
    summary = _manifest_summary(payload)
    return {
        "schema_version": "1",
        "repository": "workbench",
        "study_id": study_id,
        "metadata_sha256": sha256_text(stable_json_dumps(payload)),
        "json": {
            "sha256": sha256_file(json_path),
            "path": _relative_path(root, json_path),
            "compression": "none",
            "size_bytes": json_path.stat().st_size,
        },
        "summary": summary,
        "analysis_index": {
            _extract_analysis_id(analysis): analysis
            for analysis in (payload.get("analyses") or {}).values()
            if isinstance(analysis, dict) and _extract_analysis_id(analysis)
        },
        "reported_n_metabolites": {},
        "selected_assets": selected_assets,
        "tabular_assets": tabular_assets,
        "tabular_message": (
            f"Indexed {len(selected_assets)} preferred tabular asset(s) from legacy dump."
            if selected_assets
            else "No legacy tabular assets found."
        ),
        "last_synced_at": _now_utc(),
        "sync_status": "indexed",
        "parser_version": __version__,
    }


def rebuild_workbench_index(root: str | Path | None = None, workspace: Path | None = None) -> dict[str, Any]:
    store_root = init_mw_archive(root, workspace=workspace)
    with sqlite3.connect(catalog_path(store_root)) as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM studies")
        conn.execute("DELETE FROM analyses")
        conn.execute("DELETE FROM assets")
        conn.commit()
    manifest_files = sorted((store_root / "studies").glob("*/manifest.json"))
    indexed_ids: set[str] = set()
    managed_indexed = 0
    legacy_indexed = 0

    for manifest_file in manifest_files:
        manifest = json.loads(manifest_file.read_text())
        study_id = _quote_study_id(manifest.get("study_id", ""))
        if not study_id or study_id in indexed_ids:
            continue
        _upsert_manifest_catalog(store_root, manifest)
        indexed_ids.add(study_id)
        managed_indexed += 1

    json_dir = store_root / "json"
    if json_dir.exists():
        for json_file in sorted(json_dir.glob("ST*.json")):
            study_id = json_file.stem.upper()
            if study_id in indexed_ids:
                continue
            manifest = _legacy_manifest(store_root, study_id)
            if manifest is None:
                continue
            _upsert_manifest_catalog(store_root, manifest)
            indexed_ids.add(study_id)
            legacy_indexed += 1

    if managed_indexed and legacy_indexed:
        source = "managed+legacy"
    elif managed_indexed:
        source = "managed"
    else:
        source = "legacy"

    indexed = managed_indexed + legacy_indexed
    return {
        "root": str(store_root),
        "catalog_path": str(catalog_path(store_root)),
        "indexed_study_count": indexed,
        "managed_indexed_study_count": managed_indexed,
        "legacy_indexed_study_count": legacy_indexed,
        "source": source,
    }


def _safe_members(archive: tarfile.TarFile, target_root: Path) -> list[tarfile.TarInfo]:
    safe: list[tarfile.TarInfo] = []
    for member in archive.getmembers():
        destination = (target_root / member.name).resolve()
        if not str(destination).startswith(str(target_root.resolve())):
            raise ValueError(f"Unsafe snapshot entry: {member.name}")
        safe.append(member)
    return safe


def create_workbench_snapshot(
    output_path: str | Path,
    root: str | Path | None = None,
    study_ids: list[str] | None = None,
    workspace: Path | None = None,
) -> Path:
    store_root = init_mw_archive(root, workspace=workspace)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    selected_ids = {_quote_study_id(study_id) for study_id in (study_ids or [])}
    manifests = []
    for manifest_file in sorted((store_root / "studies").glob("*/manifest.json")):
        manifest = json.loads(manifest_file.read_text())
        if selected_ids and manifest.get("study_id") not in selected_ids:
            continue
        manifests.append(manifest)
    if not manifests:
        raise FileNotFoundError("No managed study manifests available for snapshot creation.")
    members: set[Path] = {catalog_path(store_root)}
    snapshot_manifest = {
        "created_at": _now_utc(),
        "study_ids": [manifest["study_id"] for manifest in manifests],
        "study_count": len(manifests),
        "parser_version": __version__,
    }
    for manifest in manifests:
        members.add(manifest_path(store_root, manifest["study_id"]))
        members.add(store_root / manifest["json"]["path"])
        for asset in manifest.get("tabular_assets", []):
            members.add(store_root / asset["path"])
    snapshot_manifest_path = store_root / "snapshots" / "__snapshot_manifest__.json"
    snapshot_manifest_path.write_text(stable_json_dumps(snapshot_manifest))
    members.add(snapshot_manifest_path)
    with tarfile.open(output, "w:gz") as archive:
        for path in sorted(members):
            archive.add(path, arcname=_relative_path(store_root, path))
    snapshot_manifest_path.unlink(missing_ok=True)
    return output


def install_workbench_snapshot(
    snapshot_path: str | Path,
    root: str | Path | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    store_root = init_mw_archive(root, workspace=workspace)
    snapshot = Path(snapshot_path)
    with tarfile.open(snapshot, "r:gz") as archive:
        archive.extractall(store_root, members=_safe_members(archive, store_root))
    rebuild = rebuild_workbench_index(store_root)
    return {
        "root": str(store_root),
        "catalog_path": str(catalog_path(store_root)),
        "indexed_study_count": rebuild["indexed_study_count"],
        "snapshot_path": str(snapshot),
    }


__all__ = [
    "catalog_path",
    "create_workbench_snapshot",
    "init_mw_archive",
    "install_workbench_snapshot",
    "list_available_workbench_studies",
    "load_workbench_manifest",
    "manifest_path",
    "pull_workbench_study",
    "rebuild_workbench_index",
    "resolve_mw_archive_root",
    "sync_workbench_archive",
    "sync_workbench_study",
]
