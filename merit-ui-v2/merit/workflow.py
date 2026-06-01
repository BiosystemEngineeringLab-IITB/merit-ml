from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

from merit.assessment import assess_study
from merit.connectors import create_bundle, normalize_bundle
from merit.metrics.analytical import _is_missing, _source_kind_counts_zero
from merit.readiness_score import compute_readiness_score
from merit.reporting import render_markdown
from merit.serialization import write_dataclass_json
from merit.utils import write_json


def _run_dir(study_id: str, base_dir: str | Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else Path("outputs") / "ui_runs"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / f"{study_id.lower()}_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _infer_source_tier(bundle: dict[str, Any]) -> str:
    """Derive whether the priority-selected source is Tier 1 or Tier 2.

    Only counts sources that actually contain tabular feature data
    (selected_has_features=True) — presence of a mwtab file alone is not
    sufficient if its data block is empty.
    """
    resolution = bundle.get("tabular_resolution") or []
    tier1_kinds = {"datatable", "mwtab"}
    has_tier1 = any(
        item.get("selected_kind", "").lower() in tier1_kinds
        and item.get("selected_has_features", False)
        for item in resolution
    )
    if has_tier1:
        return "tier1"
    has_tier2 = any(
        item.get("selected_kind", "").lower() in {"results", "untarg"}
        and item.get("selected_has_features", False)
        for item in resolution
    )
    return "tier2" if has_tier2 else "tier1"


def _source_availability(bundle: dict[str, Any]) -> dict[str, Any]:
    """Summarise which data sources are present for each analysis in the bundle."""
    resolution = bundle.get("tabular_resolution") or []
    sources: dict[str, list[str]] = {"datatable": [], "mwtab": [], "untarg_data": []}
    kind_map = {
        "datatable": "datatable",
        "mwtab": "mwtab",
        "results": "untarg_data",
        "untarg": "untarg_data",
    }
    for item in resolution:
        kind = item.get("selected_kind", "").lower()
        bucket = kind_map.get(kind)
        an = item.get("analysis_id", "")
        has_features = item.get("selected_has_features", False)
        if bucket and an and has_features:
            sources[bucket].append(an)

    counts = {k: len(v) for k, v in sources.items()}
    tier = "tier1" if (sources["datatable"] or sources["mwtab"]) else "tier2"
    return {
        "datatable_count": counts["datatable"],
        "mwtab_count": counts["mwtab"],
        "untarg_data_count": counts["untarg_data"],
        "priority_tier": tier,
        "analyses_by_source": sources,
    }


# ---------------------------------------------------------------------------
# Per-source assessment
# ---------------------------------------------------------------------------

#: Maps logical source name → (bundle _source_filter kinds, score tier)
_SOURCE_CONFIGS: dict[str, tuple[set[str], str]] = {
    "datatable":  ({"datatable"},         "tier1"),
    "mwtab":      ({"mwtab"},             "tier1"),
    "untarg_data":({"results", "untarg"}, "tier2"),
}


def _assess_one_source(
    bundle: dict[str, Any],
    source_name: str,
    filter_kinds: set[str],
    source_tier: str,
    profile: str,
) -> dict[str, Any] | None:
    """Run normalize → assess → score for a single source type on raw data.

    Returns a dict with keys: source, source_tier, report, readiness_score,
    ingestion_summary, canonical. Returns None if no files match this source
    filter.
    """
    filtered_bundle = dict(bundle)
    filtered_bundle["_source_filter"] = filter_kinds
    try:
        canonical = normalize_bundle(filtered_bundle)
    except Exception:
        return None

    if not canonical.feature_matrices:
        return None
    # IMPORTANT: keep source assessments strictly source-local.
    # Some studies carry a broader canonical sample pool from factors metadata
    # than what is actually present in a given source matrix (datatable/mwtab/
    # untarg). For per-source scoring and summaries we must only use sample IDs
    # that occur in this source's selected matrices.
    matrix_sample_ids = {
        str(sid).strip()
        for matrix in canonical.feature_matrices
        for sid in (matrix.sample_ids or [])
        if str(sid).strip()
    }
    if matrix_sample_ids:
        canonical.samples = [
            sample
            for sample in canonical.samples
            if str(getattr(sample, "sample_id", "")).strip() in matrix_sample_ids
        ]
    # Source is considered assessable only if at least one matrix has at least
    # one usable numeric value after source-aware missingness rules.
    has_usable_values = False
    for matrix in canonical.feature_matrices:
        count_zero = _source_kind_counts_zero(getattr(matrix, "source_kind", ""))
        for row in matrix.values:
            for value in row:
                if not _is_missing(value, count_zero=count_zero):
                    has_usable_values = True
                    break
            if has_usable_values:
                break
        if has_usable_values:
            break
    if not has_usable_values:
        return None

    report = assess_study(canonical, profile=profile)
    score = compute_readiness_score(report, source_tier=source_tier)
    return {
        "source": source_name,
        "source_tier": source_tier,
        "report": report,
        "readiness_score": score,
        "ingestion_summary": report.ingestion_summary,
        # Keep canonical in-memory so run_guided_workflow can persist the
        # primary source canonical without re-running normalize_bundle.
        "canonical": canonical,
    }


def _run_all_sources(
    bundle: dict[str, Any],
    profile: str,
) -> dict[str, Any | None]:
    """Assess all three sources independently on raw data.

    Returns dict: source_name → assessment dict (or None if unavailable).
    """
    avail = _source_availability(bundle)
    results: dict[str, Any | None] = {}

    for src_name, (filter_kinds, tier) in _SOURCE_CONFIGS.items():
        count_key = f"{src_name}_count"
        if avail.get(count_key, 0) == 0:
            results[src_name] = None
            continue
        results[src_name] = _assess_one_source(
            bundle, src_name, filter_kinds, tier, profile,
        )

    return results


def _source_availability_from_assessments(source_assessments: dict[str, Any | None]) -> dict[str, Any]:
    """Derive source availability from matrix-backed, actually scored analyses."""
    analyses_by_source: dict[str, list[str]] = {"datatable": [], "mwtab": [], "untarg_data": []}
    for src in analyses_by_source:
        assessment = source_assessments.get(src)
        if not assessment:
            continue
        report = assessment.get("report")
        summary = getattr(report, "ingestion_summary", {}) if report is not None else {}
        per_analysis = (summary or {}).get("per_analysis", []) or []
        ids: set[str] = set()
        for item in per_analysis:
            if not isinstance(item, dict):
                continue
            # Keep only matrix-backed analyses.
            try:
                n_features = float(item.get("n_features", 0) or 0)
            except Exception:
                n_features = 0.0
            if n_features <= 0:
                continue
            analysis_id = str(item.get("analysis_id", "")).strip()
            if analysis_id:
                ids.add(analysis_id)
        analyses_by_source[src] = sorted(ids)

    counts = {k: len(v) for k, v in analyses_by_source.items()}
    priority_tier = "tier1" if (counts["datatable"] or counts["mwtab"]) else "tier2"
    return {
        "datatable_count": counts["datatable"],
        "mwtab_count": counts["mwtab"],
        "untarg_data_count": counts["untarg_data"],
        "priority_tier": priority_tier,
        "analyses_by_source": analyses_by_source,
    }


def run_guided_workflow(
    source: str,
    study_id: str,
    profile: str = "full",
    fetch_mode: str = "auto",
    root: str | None = None,
    download_root: str | None = None,
    output_root: str | None = None,
) -> dict[str, Any]:
    run_dir = _run_dir(study_id, output_root)
    bundle = create_bundle(
        source=source,
        study_id=study_id,
        workspace=Path.cwd(),
        root=root,
        fetch_mode=fetch_mode,
        download_root=download_root,
    )
    bundle_path = run_dir / f"{study_id.lower()}_bundle.json"
    write_json(bundle_path, bundle)

    raw_source_avail = _source_availability(bundle)

    # --- Assess all three sources independently on raw data ---
    source_assessments = _run_all_sources(bundle, profile)
    source_avail = _source_availability_from_assessments(source_assessments)

    # Primary source: first available in priority order (datatable > mwtab > untarg_data)
    primary_src = next(
        (s for s in ("datatable", "mwtab", "untarg_data") if source_assessments.get(s)),
        None,
    )
    primary = source_assessments.get(primary_src) if primary_src else None

    if primary is None:
        # Fallback: run without source filter so the connector uses its own priority
        canonical = normalize_bundle(bundle)
        canonical_path = run_dir / f"{study_id.lower()}_canonical.json"
        write_dataclass_json(canonical_path, canonical)
        report = assess_study(canonical, profile=profile)
        initial_report_path = run_dir / f"{study_id.lower()}_assessment.json"
        markdown_path = run_dir / f"{study_id.lower()}_report.md"
        source_tier = _infer_source_tier(bundle)
        readiness_score = compute_readiness_score(
            report,
            source_tier=source_tier,
            source_availability=source_avail,
        )
        write_dataclass_json(initial_report_path, report)
        markdown_path.write_text(render_markdown(report))
        final_report = report
    else:
        final_report = primary["report"]
        readiness_score = primary["readiness_score"]
        source_tier = primary["source_tier"]
        canonical_path = run_dir / f"{study_id.lower()}_canonical.json"
        initial_report_path = run_dir / f"{study_id.lower()}_assessment.json"
        markdown_path = run_dir / f"{study_id.lower()}_report.md"
        # Write canonical for the primary source so downstream consumers (UI,
        # cache replay) can read it, reusing already-normalized canonical for
        # speed.
        try:
            _primary_canonical = primary.get("canonical")
            if _primary_canonical is not None:
                write_dataclass_json(canonical_path, _primary_canonical)
        except Exception as _exc:
            _log.warning(
                "Could not write primary canonical for %s (%s): %s. "
                "Continuing without cached primary canonical.",
                primary_src, canonical_path, _exc,
            )
        write_dataclass_json(initial_report_path, final_report)
        markdown_path.write_text(render_markdown(final_report))

    return {
        "run_dir": str(run_dir),
        "profile": profile,
        "requested_fetch_mode": fetch_mode,
        "bundle_path": str(bundle_path),
        "canonical_path": str(canonical_path),
        "assessment_path": str(initial_report_path),
        "report_md_path": str(markdown_path),
        "bundle": bundle,
        "final_report": final_report,
        "initial_report": final_report,
        "remediations": [],
        "source_tier": source_tier,
        "source_availability": source_avail,
        "source_availability_raw": raw_source_avail,
        "primary_source": primary_src,
        # Per-source independent assessments — keyed by source name
        "source_assessments": {
            k: {
                "source": v["source"],
                "source_tier": v["source_tier"],
                "readiness_score": v["readiness_score"],
                "ingestion_summary": v["ingestion_summary"],
                # report is kept as Python object for UI rendering; not serialised
                "_report": v["report"],
            }
            if v else None
            for k, v in source_assessments.items()
        },
        "readiness_score": readiness_score,
    }
