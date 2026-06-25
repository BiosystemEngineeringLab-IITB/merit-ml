# MERIT

MERIT (MachinE learning ReadIness for Tabular metabolomics data) is a publication-first framework inspired by AIDRIN, specialized for public metabolomics repositories. It provides:

- repository-aware ingestion for MetaboLights and Metabolomics Workbench
- canonical normalization into a common study schema
- domain-specific readiness scoring across fixed score families
- auditable remediations with before/after deltas
- baseline benchmark execution for ML suitability checks
- machine-readable reports and Markdown/HTML summaries

## Public Docker Distribution

The public MERIT-ML Docker image is a thin local UI container. It does not
bundle the Workbench v7 cache, raw Metabolomics Workbench source dumps, or
generated export files. At runtime, the container reads hosted MERIT-derived
assessment artifacts from the configured MERIT/R2 artifact endpoint and fetches
source-specific tabular details only on demand from Metabolomics Workbench REST
for MERIT-derived export generation.

```bash
docker pull banerjee28/merit-ml:v7
docker run -d --name merit-ml -p 8780:8773 banerjee28/merit-ml:v7
```

Then open `http://localhost:8780`. See `docker/README.md` for the full Docker
run, build, verification, and publication workflow.

This repository ships a working v0 foundation designed around the local source data already present in this workspace:

- `metabolights_data/mtbls_metadata/MTBLS2262`
- `mw-dump-latest/ST001814/AN002942/tabular/AN002942_datatable.tsv`
- `mw-dump/json/ST000356.json`
- `mw-dump/datatable/ST000356/*.datatable.tsv.gz`

For Metabolomics Workbench, ingestion now defaults to local archives and prefers `mw-dump-latest/` when present, then `mw_dump/`, then `mw-dump/`. The connector supports:

1. Latest nested dump layout: `STUDY/ANALYSIS/json` + `STUDY/ANALYSIS/tabular` with a study `manifest.json`
2. Managed archive layout (`catalog.sqlite` + `studies/<ST>/manifest.json`)
3. Legacy dump layout (`json/` + `datatable/`)

Tabular selection priority remains:

1. `*_Results.txt`
2. `*_datatable.tsv` / `*.datatable.tsv.gz`
3. `*.mwtab` / `*.mwtab.txt`

For local-first operation during MW downtime, use `--fetch-mode local` and point `--root` at your dump. `merit mw rebuild-index` now merges managed manifests and legacy `json/datatable` entries so one catalog can cover all locally available studies.

The preferred long-term MW storage model is now a managed archive:

- `catalog.sqlite` for lightweight study and asset indexing
- `studies/<STUDY_ID>/manifest.json` for per-study current state
- `objects/json/<sha-prefix>/<sha>.json.gz` for compressed JSON payloads
- `objects/tabular/<sha-prefix>/<sha>...` for compressed tabular assets

This keeps the Python package small while preserving the full JSON and raw tabular payloads outside the git-tracked source tree.

## Readiness Scoring Overview

MERIT reports two top-level scores:

- **Core ML readiness score**: unweighted mean of Structural, Analytical QC, Annotation, Cohort/Bias, and ML Readiness section scores.
- **Reusability score**: Metadata / FAIR section score (reported separately from core ML readiness).

Annotation is intentionally part of the core score: in metabolomics, weak annotation does not always prevent model training, but it directly limits interpretability and biological usability of model outputs.

MERIT also applies feasibility gates (tabular data availability, biological sample count, group count, minimum per-group support, and non-catastrophic missingness). Gates do not change the numeric score; they can cap the final readiness band.

## Install

```bash
python3 -m pip install -e .
```

## Quick Start

```bash
merit ingest --source metabolights --study-id MTBLS2262 --fetch-mode auto --output outputs/mtbls2262_bundle.json
merit assess --bundle outputs/mtbls2262_bundle.json --profile full --output outputs/mtbls2262_assessment.json
merit ingest --source workbench --study-id ST000356 --fetch-mode auto --output outputs/st000356_bundle.json
merit assess --bundle outputs/st000356_bundle.json --profile full --output outputs/st000356_assessment.json
merit report --assessment outputs/st000356_assessment.json --format md --output outputs/st000356_report.md
merit mw sync --root ~/.cache/merit/mw --limit 10
merit mw pull ST004241 --root ~/.cache/merit/mw
merit mw rebuild-index --root ~/.cache/merit/mw
merit mw snapshot-create --root ~/.cache/merit/mw --output outputs/mw_snapshot.tar.gz
merit mw snapshot-install --root ~/.cache/merit/mw --snapshot outputs/mw_snapshot.tar.gz
merit mw full-run --dump-root /path/to/mw-dump-latest --output-root /path/to/merit-full-run-mw --profile full
merit ui --port 8765
```

## CLI Commands

- `merit ingest`
- `merit normalize`
- `merit assess`
- `merit remediate`
- `merit benchmark`
- `merit report`
- `merit ui`
- `merit mw sync`
- `merit mw pull`
- `merit mw rebuild-index`
- `merit mw snapshot-create`
- `merit mw snapshot-install`
- `merit mw full-run`

`merit mw full-run` creates a centralized JSON cache (`<output-root>/json/*_workflow_state.json`) plus logs (`<output-root>/logs/`) so the UI can replay reports from cached JSON without touching raw dump files.

## Scope of This Version

This implementation delivers the initial end-to-end scientific core:

- canonical data model
- dual-source local connectors
- metrics across all planned score families
- JSON assessment reports with reproducible content hashes
- safe remediations (label normalization, duplicate feature collapse, missingness filtering)
- baseline within-study and leave-one-study-out benchmarking
- tests for parsers, metrics, and benchmark orchestration
- remote accession fetchers with local cache materialization
- JSON-first incremental Metabolomics Workbench archive sync
- `catalog.sqlite` index plus content-addressed compressed object storage for MW
- portable MW snapshot create/install commands for lightweight distribution
- local testing UI with a directed workflow and experimental MetaboScore panel

It does not yet ship a production web application.
