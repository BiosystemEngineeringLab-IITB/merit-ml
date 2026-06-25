# MERIT UI/backend freeze

Freeze date: 2026-04-27
Local recovery update: 2026-05-01

Purpose:
Freeze the current local MERIT UI/backend code as the reference version so future local launches do not require repeatedly pulling or diffing the deployed Vercel UI.

Reference precomputed cache root:
/home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7

Remote policy:
- No R2 cache changes were made for this freeze.
- No Vercel deployment changes were made for this freeze.
- Do not pull/diff the remote Vercel UI unless explicitly requested or needed for a new remote-sync task.
- Do not use or discuss v6 unless explicitly requested.

Frozen UI/backend features:
- Vercel-matching local report labels.
- Expanded Find Similar Studies sidebar filters: organism, disease, analysis type, ion mode, chromatography, instrument, sample type, project type, institute, and band.
- Free-text similar-study search box.
- Mass/RT-like metadata presence is available as a study-browser facet and as a Metadata/FAIR reuse metric.
- Mass/RT-like metadata tooltip includes example analysis IDs and explicitly reports RT unit metadata when present, or states that RT units are not available in mwTab MS_RESULTS_FILE metadata.
- MetaBatch annotation compatibility replaces the legacy batch-info availability display. It is an Analytical QC informational metric, uses MetaBatch/StdMW-style Workbench factor usability filters, links to the original MetaBatch tool, and does not affect readiness or reusability scores.
- The MetaBatch metric's explicit technical batch-like key flag is MERIT-specific context based on batch/run/order/plate/injection/acquisition-like factor names or values; it does not control pass/fail.
- Correct Metadata and FAIR Reusability tooltip wording.
- Correct FAIR checklist label: Project type / experimental design.
- Sample-level-only outlier burden tooltip and table.
- Outlier burden scoring is sample-level only; legacy feature-level outlier payloads are not part of the active v7 cache JSON.
- Tooltip scrollbar fix: scrollable popups use pointer-events:auto and contained overscroll.
- Class Separability is not shown as a UI tab/section.
- Overview Missingness card uses the selected source's `missingness_structure` metric when available instead of stale ingestion-level missing_rate.
- Data-source cards display Matrices (this source) separately from total matrices.
- Scale diagnostics values render in scientific notation throughout the local UI.
- Readiness gate UI includes gate thresholds/summaries and labels G5 as non-catastrophic missingness.
- No Data studies keep core ML readiness at 0.000 while still showing non-zero Metadata/FAIR reusability when metadata are present.
- No Data studies apply an explicit scoring policy: all non-reuse metric and section scores are set to 0; only Metadata/FAIR reuse metrics retain their computed scores.
- Local default precomputed root should remain v7.

Local v7 cache recovery notes from 2026-04-30:
- Authoritative local cache root remains `/home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7`.
- Local cache denominator is 4,121 studies; `index.json` and `study_metadata_index.json` have `generated_at_utc=2026-04-30T09:35:43Z`.
- Workbench TSV/mwTab parsing treats unmatched quotes as literal text to prevent malformed metabolite-name fields from collapsing rows/columns into giant feature blobs.
- ST002461 local cache validation: datatable=2 analyses, mwTab=2 analyses, untarg_data=0 analyses, primary source datatable, score=0.924 Conditional, no giant feature-name blobs.
- ST001431 local cache validation: G4 minimum-group gate uses canonical `group_size_support` counts, smallest class=80, gate_summary pass=5/fail=0, final band Ready.
- ST001385 local cache validation: datatable=2, mwTab=1, untarg_data=2; source counts intentionally differ because only one mwTab quantitative matrix is valid.
- ST001994 and ST001995 local cache validation: No Data core score=0.000 and reusability_score=0.571.
- ST001994 and ST001995 local cache validation after No Data policy: Structural, Analytical QC, Annotation, Cohort, and ML Task Readiness section scores are all 0.000; Metadata/FAIR reuse remains 0.571.
- ST004105 local UI/cache validation target: datatable missingness=0.0%; mwTab missingness=50.9% from source-specific `missingness_structure`.
- No Data policy cache update (`2026-04-30T10:56:30Z`): 148 local No Data workflow/readiness JSON pairs were updated so non-reuse metric scores are zeroed while raw metric evidence is retained in metric details under `raw_score_before_no_data_policy`.
- Remote R2 and Vercel were not modified during this local recovery.

Local v7 cache update from 2026-05-01:
- Local cache was patched in place to remove the retired batch-info availability metric from all workflow-state reports and insert `metabatch_batch_annotation_compatibility`.
- Patch audit directory: `/home/shayantan/metabolomics/ML-ready/outputs/v7_metabatch_metric_patch_20260430T184238Z`.
- Patch summary: 4,121 workflow-state files examined and changed; 15,570 retired metric objects removed; 15,570 MetaBatch metric objects inserted; 11 stale spot-recompute MetaBatch objects replaced; 0 per-study errors.
- Cache scan after patch confirmed 0 files under `/home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7` mention the retired metric name and 4,121 workflow-state files mention `metabatch_batch_annotation_compatibility`.
- The patch did not recompute readiness scores or bands. The MetaBatch metric is informational only, so core ML readiness and Metadata/FAIR reusability scores remain unchanged.
- Three accidental subset-backup files from an earlier interrupted index/manifest repair were moved out of the cache root into the patch audit directory before remote comparison.
- Corrective cleanup before final remote diff restored `index.json` and the three accidentally spot-recomputed readiness-score JSONs for ST000001, ST002692, and ST004430 from the remote snapshot, then re-applied only the MetaBatch workflow-state metric replacement for those three studies.
- Remote-excluded local precompute auxiliary files (`manifest.json`, `summary.json`, `checkpoint.json`, and `logs/`) were moved into the patch audit directory so the local cache tree has the same 8,244-file shape as remote R2.
- Final local-vs-remote cache diff audit: `/home/shayantan/metabolomics/ML-ready/outputs/local_remote_v7_cache_diff_after_metabatch_20260501T002600IST`. Result: same file set, 4,121 workflow-state JSON size/hash differences only, 0 readiness-score JSON differences, 0 index/study-metadata-index differences.
- Remote R2 and Vercel were not modified during this local cache update.

Remote deployment update from 2026-05-01:
- Patched v7 cache was uploaded to new R2 prefix `merit-cache/releases/v7.2026-04-30-190939.metabatch-annotation-compatibility`.
- Production Vercel env `MERIT_PRECOMPUTED_ROOT` was updated to `https://pub-acf151eb41e04ee795a86a8049d54039.r2.dev/merit-cache/releases/v7.2026-04-30-190939.metabatch-annotation-compatibility/`; `MERIT_CACHE_ONLY=1` was retained.
- Production Vercel deployment was completed and aliased to `https://www.merit-ml.in`; final deployment ID `dpl_A6JG5FXWvGSZsDH8gp1PiX9dFoR3`.
- `.vercelignore` was tightened to exclude non-runtime cache/manuscript/release/archive artifacts from Vercel packaging and explicitly re-include `Logo.png`, preventing the app bundle from exceeding Vercel's upload limit while preserving the local/remote logo rendering.
- `deploy/config.env` now defaults to the v7 cache root and the current versioned R2 prefix, so future deploys do not fall back to v6.
- Remote sync audit directory: `/home/shayantan/metabolomics/ML-ready/outputs/remote_sync_verify_metabatch_20260501T005300IST`.
- Final cache diff after remote upload: local and R2 both 8,244 files and 1,476,165,782 bytes; 0 missing, 0 extra, 0 size mismatches, 0 SHA-256 mismatches.
- Final rendered UI diff after Vercel deployment: 6 sampled pages returned HTTP 200 locally and remotely; all normalized HTML matched exactly after replacing only the local cache path vs public R2 cache root.

Launch command:
MERIT_PRECOMPUTED_ROOT=/home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7 python3 -m merit ui --host 127.0.0.1 --port 8772

Verification performed before freeze:
- python3 -m py_compile merit/ui.py
- Live local homepage contained expanded filters and no stale facet-platform dropdown.
- Live local ST004410 report contained sample-level outlier tooltip/table and corrected FAIR wording.

Verification performed after 2026-04-30 local recovery:
- python3 -m py_compile merit/ui.py merit/utils.py merit/connectors/workbench.py merit/metrics/analytical.py merit/metrics/cohort.py merit/metrics/ml_readiness.py merit/readiness_score.py tests/test_assessment.py
- python3 -m unittest tests.test_assessment
- Active v7 JSON scan confirmed zero occurrences of `feature_component`, `outlier_features_top50`, `legacy_feature_level_excluded_from_score`, `feature_outlier_features`, `feature_outlier_points`, and `outlier_features_top20`.
- No Data policy validation confirmed zero No Data studies with non-reuse section scores greater than 0.

Verification performed after 2026-05-01 MetaBatch local cache update:
- Local cache scan confirmed zero `batch_info_availability` occurrences under `merit-cache-workbench-full-v7`.
- Spot checks on ST000001, ST000019, ST002692, and ST004430 confirmed exactly one `metabatch_batch_annotation_compatibility` metric in initial and final reports, and one per available source report.
- Patch audit `changed_files.tsv` reported zero error rows.
- Final remote-diff audit confirmed local has 0 retired metric objects and 15,570 MetaBatch metric objects; remote has 15,570 retired metric objects and 0 MetaBatch metric objects. This is the only intended cache content difference.
- Final remote sync validation confirmed remote R2 now matches local exactly and production `https://www.merit-ml.in` matches the local UI exactly on the sampled pages.

Sensitive files:
- deploy/credentials.env was intentionally not included.
