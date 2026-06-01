from __future__ import annotations

import argparse
from pathlib import Path

from merit.assessment import assess_study
from merit.connectors import available_sources, create_bundle, normalize_bundle
from merit.connectors.workbench import (
    backfill_latest_dump_disease,
    backfill_latest_dump_factors,
    backfill_latest_dump_metabolites,
)
from merit.mw_full_run import run_mw_full_cache
from merit.mw_archive import (
    create_workbench_snapshot,
    init_mw_archive,
    install_workbench_snapshot,
    pull_workbench_study,
    rebuild_workbench_index,
    sync_workbench_archive,
)
from merit.remediation import load_actions, remediate_study
from merit.reporting import render_html, render_markdown, write_rendered_report
from merit.serialization import (
    load_assessment_report,
    load_canonical_study,
    read_json,
    write_dataclass_json,
)
from merit.utils import stable_json_dumps, write_json


def _default_output(prefix: str, suffix: str) -> Path:
    path = Path("outputs") / f"{prefix}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def cmd_ingest(args: argparse.Namespace) -> None:
    bundle = create_bundle(
        source=args.source,
        study_id=args.study_id,
        workspace=Path.cwd(),
        root=args.root,
        fetch_mode=args.fetch_mode,
        download_root=args.download_root,
    )
    output = Path(args.output) if args.output else _default_output(args.study_id.lower(), "_bundle.json")
    write_json(output, bundle)
    print(output)


def _load_bundle(path: str) -> dict:
    return read_json(path)


def cmd_normalize(args: argparse.Namespace) -> None:
    bundle = _load_bundle(args.bundle)
    canonical = normalize_bundle(bundle)
    output = Path(args.output) if args.output else _default_output(bundle["study_id"].lower(), "_canonical.json")
    write_dataclass_json(output, canonical)
    print(output)


def cmd_assess(args: argparse.Namespace) -> None:
    bundle = _load_bundle(args.bundle)
    canonical = normalize_bundle(bundle)
    report = assess_study(canonical, profile=args.profile)
    output = Path(args.output) if args.output else _default_output(bundle["study_id"].lower(), "_assessment.json")
    write_dataclass_json(output, report)
    if args.canonical_output:
        write_dataclass_json(args.canonical_output, canonical)
    print(output)


def cmd_remediate(args: argparse.Namespace) -> None:
    source_path = Path(args.bundle)
    if source_path.name.endswith("_canonical.json"):
        canonical = load_canonical_study(source_path)
        source_id = canonical.study.study_id
    else:
        bundle = _load_bundle(args.bundle)
        canonical = normalize_bundle(bundle)
        source_id = bundle["study_id"]
    actions = load_actions(args.actions)
    remediated, log = remediate_study(canonical, actions)
    output = Path(args.output) if args.output else _default_output(source_id.lower(), "_remediated_canonical.json")
    write_dataclass_json(output, remediated)
    if args.assessment_output:
        report = assess_study(remediated, profile=args.profile, remediations_applied=log)
        write_dataclass_json(args.assessment_output, report)
    print(output)


def cmd_report(args: argparse.Namespace) -> None:
    report = load_assessment_report(args.assessment)
    if args.format == "json":
        text = stable_json_dumps(read_json(args.assessment)) + "\n"
    elif args.format == "html":
        text = render_html(report)
    else:
        text = render_markdown(report)
    output = Path(args.output) if args.output else _default_output(report.source["study_id"].lower(), f"_report.{args.format if args.format != 'md' else 'md'}")
    write_rendered_report(output, text)
    print(output)


def cmd_ui(args: argparse.Namespace) -> None:
    from merit.ui import serve_ui

    serve_ui(host=args.host, port=args.port)


def cmd_mw_sync(args: argparse.Namespace) -> None:
    result = sync_workbench_archive(
        root=args.root,
        study_ids=args.study_id,
        limit=args.limit,
        force=args.force,
        include_mwtab=not args.skip_mwtab,
        workspace=Path.cwd(),
        verbose=args.verbose,
        quiet=args.quiet,
        log_path=args.log_file,
    )
    print(stable_json_dumps(result))


def cmd_mw_pull(args: argparse.Namespace) -> None:
    result = pull_workbench_study(
        study_id=args.study_id,
        root=args.root,
        force=args.force,
        include_mwtab=not args.skip_mwtab,
        workspace=Path.cwd(),
        verbose=args.verbose,
        quiet=args.quiet,
        log_path=args.log_file,
    )
    print(stable_json_dumps(result))


def cmd_mw_rebuild_index(args: argparse.Namespace) -> None:
    result = rebuild_workbench_index(root=args.root, workspace=Path.cwd())
    print(stable_json_dumps(result))


def cmd_mw_backfill_metabolites(args: argparse.Namespace) -> None:
    result = backfill_latest_dump_metabolites(
        root=args.root,
        workspace=Path.cwd(),
        study_ids=args.study_id,
        limit=args.limit,
        force=args.force,
        allow_remote=args.remote,
        verbose=args.verbose,
    )
    print(stable_json_dumps(result))


def cmd_mw_backfill_disease(args: argparse.Namespace) -> None:
    result = backfill_latest_dump_disease(
        root=args.root,
        workspace=Path.cwd(),
        study_ids=args.study_id,
        limit=args.limit,
        force=args.force,
        allow_remote=args.remote,
        verbose=args.verbose,
    )
    print(stable_json_dumps(result))


def cmd_mw_backfill_factors(args: argparse.Namespace) -> None:
    result = backfill_latest_dump_factors(
        root=args.root,
        workspace=Path.cwd(),
        study_ids=args.study_id,
        limit=args.limit,
        force=args.force,
        allow_remote=args.remote,
        verbose=args.verbose,
    )
    print(stable_json_dumps(result))


def cmd_mw_full_run(args: argparse.Namespace) -> None:
    result = run_mw_full_cache(
        dump_root=args.dump_root,
        output_root=args.output_root,
        study_ids=args.study_id,
        limit=args.limit,
        profile=args.profile,
        enable_remediation=not args.disable_remediation,
        missingness_threshold=args.missingness_threshold,
        skip_existing=not args.overwrite_existing,
        keep_scratch_runs=args.keep_scratch_runs,
        verbose=not args.quiet,
    )
    print(stable_json_dumps(result))


def cmd_mw_snapshot_create(args: argparse.Namespace) -> None:
    root = init_mw_archive(args.root, workspace=Path.cwd())
    output = Path(args.output) if args.output else _default_output("mw_snapshot", ".tar.gz")
    snapshot = create_workbench_snapshot(
        output_path=output,
        root=root,
        study_ids=args.study_id,
        workspace=Path.cwd(),
    )
    print(snapshot)


def cmd_mw_snapshot_install(args: argparse.Namespace) -> None:
    result = install_workbench_snapshot(
        snapshot_path=args.snapshot,
        root=args.root,
        workspace=Path.cwd(),
    )
    print(stable_json_dumps(result))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="merit")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Create a deterministic ingest bundle for a repository study.")
    ingest.add_argument("--source", choices=available_sources(), default="workbench")
    ingest.add_argument("--study-id", required=True)
    ingest.add_argument("--fetch-mode", choices=["auto", "local", "remote"], default="auto")
    ingest.add_argument("--root")
    ingest.add_argument("--download-root")
    ingest.add_argument("--output")
    ingest.set_defaults(func=cmd_ingest)

    normalize = sub.add_parser("normalize", help="Normalize a previously created ingest bundle into the canonical schema.")
    normalize.add_argument("--bundle", required=True)
    normalize.add_argument("--output")
    normalize.set_defaults(func=cmd_normalize)

    assess = sub.add_parser("assess", help="Normalize a bundle and compute a readiness report.")
    assess.add_argument("--bundle", required=True)
    assess.add_argument("--profile", choices=["core", "full"], default="core")
    assess.add_argument("--output")
    assess.add_argument("--canonical-output")
    assess.set_defaults(func=cmd_assess)

    remediate = sub.add_parser("remediate", help="Apply auditable remediations to a canonical study or bundle.")
    remediate.add_argument("--bundle", required=True)
    remediate.add_argument("--actions")
    remediate.add_argument("--profile", choices=["core", "full"], default="core")
    remediate.add_argument("--output")
    remediate.add_argument("--assessment-output")
    remediate.set_defaults(func=cmd_remediate)

    report = sub.add_parser("report", help="Render a JSON assessment report into markdown, html, or json.")
    report.add_argument("--assessment", required=True)
    report.add_argument("--format", choices=["json", "html", "md"], default="md")
    report.add_argument("--output")
    report.set_defaults(func=cmd_report)

    ui = sub.add_parser("ui", help="Start the local MERIT testing UI.")
    ui.add_argument("--host", default="0.0.0.0")
    ui.add_argument("--port", type=int, default=8765)
    ui.set_defaults(func=cmd_ui)

    mw = sub.add_parser("mw", help="Manage the local Metabolomics Workbench archive.")
    mw_sub = mw.add_subparsers(dest="mw_command", required=True)

    mw_sync = mw_sub.add_parser("sync", help="JSON-first incremental sync of the Workbench archive.")
    mw_sync.add_argument("--root")
    mw_sync.add_argument("--study-id", action="append")
    mw_sync.add_argument("--limit", type=int)
    mw_sync.add_argument("--force", action="store_true")
    mw_sync.add_argument("--skip-mwtab", action="store_true")
    mw_sync.add_argument("--verbose", action="store_true")
    mw_sync.add_argument("--quiet", action="store_true")
    mw_sync.add_argument("--log-file")
    mw_sync.set_defaults(func=cmd_mw_sync)

    mw_pull = mw_sub.add_parser("pull", help="Pull or refresh a single Workbench study into the managed archive.")
    mw_pull.add_argument("study_id")
    mw_pull.add_argument("--root")
    mw_pull.add_argument("--force", action="store_true")
    mw_pull.add_argument("--skip-mwtab", action="store_true")
    mw_pull.add_argument("--verbose", action="store_true")
    mw_pull.add_argument("--quiet", action="store_true")
    mw_pull.add_argument("--log-file")
    mw_pull.set_defaults(func=cmd_mw_pull)

    mw_rebuild = mw_sub.add_parser("rebuild-index", help="Rebuild catalog.sqlite from managed manifests or a legacy dump.")
    mw_rebuild.add_argument("--root")
    mw_rebuild.set_defaults(func=cmd_mw_rebuild_index)

    mw_backfill = mw_sub.add_parser(
        "backfill-metabolites",
        help="Create or refresh mw-dump-latest/ST*/metabolites.json for local latest-dump studies.",
    )
    mw_backfill.add_argument("--root")
    mw_backfill.add_argument("--study-id", action="append")
    mw_backfill.add_argument("--limit", type=int)
    mw_backfill.add_argument("--force", action="store_true")
    mw_backfill.add_argument(
        "--remote",
        action="store_true",
        help="Allow remote REST fetch before falling back to local legacy/latest dump sources.",
    )
    mw_backfill.add_argument("--verbose", action="store_true")
    mw_backfill.set_defaults(func=cmd_mw_backfill_metabolites)

    mw_backfill_disease = mw_sub.add_parser(
        "backfill-disease",
        help="Create or refresh mw-dump-latest/ST*/disease.json for local latest-dump studies.",
    )
    mw_backfill_disease.add_argument("--root")
    mw_backfill_disease.add_argument("--study-id", action="append")
    mw_backfill_disease.add_argument("--limit", type=int)
    mw_backfill_disease.add_argument("--force", action="store_true")
    mw_backfill_disease.add_argument(
        "--remote",
        action="store_true",
        help="Allow remote REST fetch from /rest/study/study_id/STxxxxxx/disease.",
    )
    mw_backfill_disease.add_argument("--verbose", action="store_true")
    mw_backfill_disease.set_defaults(func=cmd_mw_backfill_disease)

    mw_backfill_factors = mw_sub.add_parser(
        "backfill-factors",
        help="Create or refresh mw-dump-latest/ST*/factors.json for local latest-dump studies.",
    )
    mw_backfill_factors.add_argument("--root")
    mw_backfill_factors.add_argument("--study-id", action="append")
    mw_backfill_factors.add_argument("--limit", type=int)
    mw_backfill_factors.add_argument("--force", action="store_true")
    mw_backfill_factors.add_argument(
        "--remote",
        action="store_true",
        help="Allow remote REST fetch before falling back to local legacy/latest dump sources.",
    )
    mw_backfill_factors.add_argument("--verbose", action="store_true")
    mw_backfill_factors.set_defaults(func=cmd_mw_backfill_factors)

    mw_full_run = mw_sub.add_parser(
        "full-run",
        help="Run the full MERIT workflow for all local MW studies and cache JSON artifacts for UI replay.",
    )
    mw_full_run.add_argument("--dump-root", required=True, help="Path to mw-dump-latest root containing ST* study folders.")
    mw_full_run.add_argument(
        "--output-root",
        default="merit-full-run-mw",
        help="Output folder for centralized JSON cache and logs.",
    )
    mw_full_run.add_argument("--study-id", action="append", help="Optional study ID filter; repeat for multiple studies.")
    mw_full_run.add_argument("--limit", type=int, help="Optional cap on number of studies to run.")
    mw_full_run.add_argument("--profile", choices=["core", "full"], default="full")
    mw_full_run.add_argument(
        "--disable-remediation",
        action="store_true",
        help="Skip remediation and keep pre-remediation report as final.",
    )
    mw_full_run.add_argument("--missingness-threshold", type=float, default=0.2)
    mw_full_run.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Re-run studies even if workflow_state JSON already exists.",
    )
    mw_full_run.add_argument(
        "--keep-scratch-runs",
        action="store_true",
        help="Keep intermediate timestamped run folders produced during execution.",
    )
    mw_full_run.add_argument("--quiet", action="store_true")
    mw_full_run.set_defaults(func=cmd_mw_full_run)

    mw_snapshot_create = mw_sub.add_parser("snapshot-create", help="Create a portable Workbench archive snapshot.")
    mw_snapshot_create.add_argument("--root")
    mw_snapshot_create.add_argument("--study-id", action="append")
    mw_snapshot_create.add_argument("--output")
    mw_snapshot_create.set_defaults(func=cmd_mw_snapshot_create)

    mw_snapshot_install = mw_sub.add_parser("snapshot-install", help="Install a portable Workbench archive snapshot.")
    mw_snapshot_install.add_argument("--root")
    mw_snapshot_install.add_argument("--snapshot", required=True)
    mw_snapshot_install.set_defaults(func=cmd_mw_snapshot_install)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
