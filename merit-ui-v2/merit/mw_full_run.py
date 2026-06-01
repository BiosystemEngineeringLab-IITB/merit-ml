from __future__ import annotations

import csv
import re
import shutil
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from merit.models import dataclass_to_dict
from merit.serialization import read_json
from merit.utils import write_json
from merit.workflow import run_guided_workflow


_STUDY_RE = re.compile(r"^ST\d{6}$", flags=re.IGNORECASE)


@dataclass
class FullRunPaths:
    root: Path
    json_dir: Path
    logs_dir: Path
    scratch_runs: Path
    status_tsv: Path
    failures_tsv: Path
    run_log: Path
    manifest_json: Path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _init_paths(output_root: str | Path) -> FullRunPaths:
    root = Path(output_root)
    json_dir = root / "json"
    logs_dir = root / "logs"
    scratch_runs = root / "_scratch_runs"
    for path in (root, json_dir, logs_dir, scratch_runs):
        path.mkdir(parents=True, exist_ok=True)
    paths = FullRunPaths(
        root=root,
        json_dir=json_dir,
        logs_dir=logs_dir,
        scratch_runs=scratch_runs,
        status_tsv=logs_dir / "status.tsv",
        failures_tsv=logs_dir / "failures.tsv",
        run_log=logs_dir / "run.log",
        manifest_json=root / "manifest.json",
    )
    if not paths.status_tsv.exists():
        paths.status_tsv.write_text("timestamp\tstudy_id\tstatus\tduration_sec\tmessage\tstate_json\n")
    if not paths.failures_tsv.exists():
        paths.failures_tsv.write_text("timestamp\tstudy_id\terror_type\terror\n")
    return paths


def _log_line(paths: FullRunPaths, message: str, *, verbose: bool = True) -> None:
    line = f"[{_utc_now_iso()}] {message}"
    with paths.run_log.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    if verbose:
        print(line)


def _append_status(
    paths: FullRunPaths,
    *,
    study_id: str,
    status: str,
    duration_sec: float,
    message: str,
    state_json: str = "",
) -> None:
    with paths.status_tsv.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow([_utc_now_iso(), study_id, status, f"{duration_sec:.2f}", message, state_json])


def _append_failure(paths: FullRunPaths, *, study_id: str, error_type: str, error: str) -> None:
    with paths.failures_tsv.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow([_utc_now_iso(), study_id, error_type, error.replace("\n", " ")[:8000]])


def discover_study_ids(dump_root: str | Path) -> list[str]:
    root = Path(dump_root)
    if not root.exists():
        raise FileNotFoundError(f"MW dump root does not exist: {root}")
    studies = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name.upper()
        if _STUDY_RE.fullmatch(name):
            studies.append(name)
    return studies


def _copy_json_artifacts(
    state: dict[str, Any],
    json_dir: Path,
    study_id: str,
    *,
    include_bundle: bool = True,
    include_assessment: bool = True,
    include_canonical: bool = True,
) -> dict[str, str]:
    study_key = study_id.lower()
    copies: dict[str, str] = {}
    mapping: list[tuple[str, str]] = []
    if include_bundle:
        mapping.append(("bundle_path", f"{study_key}_bundle.json"))
    if include_canonical:
        mapping.extend([
            ("canonical_path", f"{study_key}_canonical.json"),
            ("remediated_canonical_path", f"{study_key}_remediated_canonical.json"),
        ])
    if include_assessment:
        mapping.extend([
            ("assessment_path", f"{study_key}_assessment.json"),
            ("remediated_assessment_path", f"{study_key}_remediated_assessment.json"),
        ])
    for key, filename in mapping:
        src = state.get(key)
        if not src:
            continue
        src_path = Path(str(src))
        if not src_path.exists():
            continue
        dst_path = json_dir / filename
        shutil.copy2(src_path, dst_path)
        copies[key] = str(dst_path)

    readiness_score_path = json_dir / f"{study_key}_readiness_score.json"
    write_json(readiness_score_path, state.get("readiness_score", {}))
    copies["readiness_score_path"] = str(readiness_score_path)
    return copies


def _serialize_source_assessments(state: dict[str, Any]) -> dict[str, Any]:
    """Convert per-source workflow results into JSON-safe payload."""
    source_assessments = state.get("source_assessments") or {}
    if not isinstance(source_assessments, dict):
        return {}

    out: dict[str, Any] = {}
    for source_name, item in source_assessments.items():
        if not item:
            out[source_name] = None
            continue
        report_obj = item.get("_report")
        report_payload = dataclass_to_dict(report_obj) if report_obj is not None else None
        out[source_name] = {
            "source": item.get("source", source_name),
            "source_tier": item.get("source_tier", "tier1"),
            "readiness_score": item.get("readiness_score", {}),
            "ingestion_summary": item.get("ingestion_summary", {}),
            "report": report_payload,
        }
    return out


def _build_ui_state_json(
    state: dict[str, Any],
    copied: dict[str, str],
    study_id: str,
    *,
    output_path: Path,
) -> dict[str, Any]:
    bundle = state.get("bundle") or {}
    final_report = state.get("final_report")
    initial_report = state.get("initial_report")
    payload = {
        "schema": "merit.ui_state.v1",
        "generated_at_utc": _utc_now_iso(),
        "study_id": study_id,
        "profile": state.get("profile", "full"),
        "source": "workbench",
        "requested_fetch_mode": state.get("requested_fetch_mode", "local"),
        "bundle_path": copied.get("bundle_path", state.get("bundle_path", "")),
        "canonical_path": copied.get("canonical_path", state.get("canonical_path", "")),
        "assessment_path": copied.get("assessment_path", state.get("assessment_path", "")),
        "remediated_assessment_path": copied.get(
            "remediated_assessment_path",
            state.get("remediated_assessment_path", ""),
        ),
        "remediated_canonical_path": copied.get(
            "remediated_canonical_path",
            state.get("remediated_canonical_path", ""),
        ),
        "readiness_score_path": copied.get("readiness_score_path", ""),
        "bundle": {
            "source_root": bundle.get("source_root", ""),
            "acquisition_source": bundle.get("acquisition_source", ""),
            "tabular_message": bundle.get("tabular_message", ""),
            "tabular_resolution": bundle.get("tabular_resolution", []),
        },
        "source_tier": state.get("source_tier", "tier1"),
        "source_availability": state.get("source_availability", {}),
        "primary_source": state.get("primary_source", ""),
        "source_assessments": _serialize_source_assessments(state),
        "readiness_score": state.get("readiness_score", {}),
        "remediations": state.get("remediations", []),
        "initial_report": dataclass_to_dict(initial_report) if initial_report is not None else None,
        "final_report": dataclass_to_dict(final_report) if final_report is not None else None,
    }
    write_json(output_path, payload)
    return payload


def run_mw_full_cache(
    *,
    dump_root: str | Path,
    output_root: str | Path,
    study_ids: list[str] | None = None,
    limit: int | None = None,
    profile: str = "full",
    enable_remediation: bool = True,
    missingness_threshold: float = 0.2,
    skip_existing: bool = True,
    keep_scratch_runs: bool = False,
    verbose: bool = True,
) -> dict[str, Any]:
    paths = _init_paths(output_root)
    requested = [study.upper() for study in (study_ids or []) if str(study).strip()]
    if requested:
        studies = [study for study in requested if _STUDY_RE.fullmatch(study)]
    else:
        studies = discover_study_ids(dump_root)
    if limit is not None:
        studies = studies[: max(limit, 0)]

    manifest: dict[str, Any] = {
        "schema": "merit.full_run_mw.manifest.v1",
        "generated_at_utc": _utc_now_iso(),
        "dump_root": str(Path(dump_root).resolve()),
        "output_root": str(paths.root.resolve()),
        "profile": profile,
        "enable_remediation": enable_remediation,
        "missingness_threshold": float(missingness_threshold),
        "studies_requested": len(studies),
        "studies": [],
    }
    index_payload: dict[str, Any] = {
        "schema": "merit.precomputed_index.v1",
        "generated_at_utc": _utc_now_iso(),
        "output_root": str(paths.root.resolve()),
        "profile": profile,
        "studies": {},
    }
    write_json(paths.manifest_json, manifest)
    _log_line(paths, f"Starting full MW run for {len(studies)} study/studies", verbose=verbose)

    success = 0
    failed = 0
    skipped = 0
    started = perf_counter()

    for idx, study_id in enumerate(studies, start=1):
        study_key = study_id.lower()
        state_json_path = paths.json_dir / f"{study_key}_workflow_state.json"
        if skip_existing and state_json_path.exists():
            skipped += 1
            msg = "skipped_existing_state"
            cached_score = None
            cached_band = None
            try:
                existing_payload = read_json(state_json_path)
                if isinstance(existing_payload, dict):
                    rs = existing_payload.get("readiness_score") or {}
                    if isinstance(rs, dict):
                        cached_score = rs.get("score")
                        cached_band = rs.get("band")
            except Exception:
                pass
            _append_status(
                paths,
                study_id=study_id,
                status="skipped",
                duration_sec=0.0,
                message=msg,
                state_json=str(state_json_path),
            )
            _log_line(paths, f"[{idx}/{len(studies)}] {study_id}: {msg}", verbose=verbose)
            manifest["studies"].append(
                {
                    "study_id": study_id,
                    "status": "skipped",
                    "state_json": str(state_json_path),
                    "message": msg,
                    "score": cached_score,
                    "band": cached_band,
                }
            )
            index_payload["studies"][study_id.upper()] = {
                "state_path": str(state_json_path),
                "score": cached_score,
                "band": cached_band,
                "profile": profile,
                "updated_at_utc": _utc_now_iso(),
            }
            write_json(paths.root / "index.json", index_payload)
            continue

        run_started = perf_counter()
        _log_line(paths, f"[{idx}/{len(studies)}] {study_id}: running", verbose=verbose)
        try:
            state = run_guided_workflow(
                source="workbench",
                study_id=study_id,
                profile=profile,
                fetch_mode="local",
                root=str(Path(dump_root)),
                download_root=None,
                output_root=str(paths.scratch_runs),
            )

            # Guardrail: do not emit cached JSON for studies with no valid source matrices.
            source_availability = state.get("source_availability") or {}
            dt_count = int(source_availability.get("datatable_count") or 0)
            mw_count = int(source_availability.get("mwtab_count") or 0)
            ut_count = int(source_availability.get("untarg_data_count") or 0)
            if dt_count == 0 and mw_count == 0 and ut_count == 0:
                raise ValueError("no_valid_sources_for_study")

            copied = _copy_json_artifacts(state, paths.json_dir, study_id)
            payload = _build_ui_state_json(state, copied, study_id, output_path=state_json_path)
            if not keep_scratch_runs:
                scratch_dir = state.get("run_dir")
                if scratch_dir:
                    shutil.rmtree(str(scratch_dir), ignore_errors=True)

            duration = perf_counter() - run_started
            success += 1
            _append_status(
                paths,
                study_id=study_id,
                status="success",
                duration_sec=duration,
                message="ok",
                state_json=str(state_json_path),
            )
            _log_line(
                paths,
                f"[{idx}/{len(studies)}] {study_id}: success in {duration:.1f}s "
                f"(score={payload.get('readiness_score', {}).get('score', 'n/a')})",
                verbose=verbose,
            )
            manifest["studies"].append(
                {
                    "study_id": study_id,
                    "status": "success",
                    "duration_sec": round(duration, 3),
                    "state_json": str(state_json_path),
                    "score": payload.get("readiness_score", {}).get("score"),
                    "band": payload.get("readiness_score", {}).get("band"),
                }
            )
            index_payload["studies"][study_id.upper()] = {
                "state_path": str(state_json_path),
                "score": payload.get("readiness_score", {}).get("score"),
                "band": payload.get("readiness_score", {}).get("band"),
                "profile": payload.get("profile", profile),
                "updated_at_utc": _utc_now_iso(),
            }
            write_json(paths.manifest_json, manifest)
            write_json(paths.root / "index.json", index_payload)
        except Exception as exc:
            duration = perf_counter() - run_started
            failed += 1
            err_type = exc.__class__.__name__
            err_msg = str(exc)
            _append_failure(paths, study_id=study_id, error_type=err_type, error=err_msg)
            _append_status(
                paths,
                study_id=study_id,
                status="failed",
                duration_sec=duration,
                message=f"{err_type}: {err_msg}",
                state_json="",
            )
            error_trace = paths.logs_dir / f"{study_key}_error.log"
            error_trace.write_text(traceback.format_exc())
            _log_line(
                paths,
                f"[{idx}/{len(studies)}] {study_id}: FAILED ({err_type}) {err_msg}",
                verbose=verbose,
            )
            manifest["studies"].append(
                {
                    "study_id": study_id,
                    "status": "failed",
                    "duration_sec": round(duration, 3),
                    "error_type": err_type,
                    "error": err_msg,
                    "error_log": str(error_trace),
                }
            )
            write_json(paths.manifest_json, manifest)
            index_payload["studies"][study_id.upper()] = {
                "state_path": "",
                "error_type": err_type,
                "error": err_msg,
                "updated_at_utc": _utc_now_iso(),
            }
            write_json(paths.root / "index.json", index_payload)
            continue

    elapsed = perf_counter() - started
    summary = {
        "schema": "merit.full_run_mw.summary.v1",
        "generated_at_utc": _utc_now_iso(),
        "dump_root": str(Path(dump_root).resolve()),
        "output_root": str(paths.root.resolve()),
        "studies_requested": len(studies),
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "elapsed_sec": round(elapsed, 2),
        "manifest_path": str(paths.manifest_json),
        "status_tsv": str(paths.status_tsv),
        "failures_tsv": str(paths.failures_tsv),
        "run_log": str(paths.run_log),
        "json_dir": str(paths.json_dir),
    }
    write_json(paths.root / "summary.json", summary)
    _log_line(
        paths,
        f"Run complete: success={success}, failed={failed}, skipped={skipped}, elapsed={elapsed:.1f}s",
        verbose=verbose,
    )
    index_payload["generated_at_utc"] = _utc_now_iso()
    write_json(paths.root / "index.json", index_payload)
    return summary
