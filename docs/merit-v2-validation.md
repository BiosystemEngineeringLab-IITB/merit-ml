# MERIT v2 live Docker validation — 5 November 2026

This is the native Docker release at `deploy/merit-v2-docker-2026-11-05`, separate from both running development services. No 8782/8791 code, historical cache or frozen study dump was changed.

## Interface release r5 — 5 November 2026

The r5 release adds the shared MERIT-ML search landing page, recent Workbench releases, About/publication/GitHub links, a persistent light/dark theme, and a compact assessment report entry. The original complete workspace remains at `/workspace`; its forms, bulk tools and downloads are retained. The browser memory notice applies only to the browser edition. The scientific implementations and fixed R1 are unchanged.

Desktop and phone-width browser checks passed for the new page, themes, navigation, recent-study selection, queued selection during engine startup, and report controls. The complete embedded ST000096 assessment state from 8782 was identical before and after the presentation change. The native Docker image built and served its landing page, workspace and health route on a separate smoke-test port. Evidence is retained in the development workspace at `outputs/landing_redesign_20261105/`.

Public browser build: `20261105-r9`; Docker/native build: `v2-live-20261105-r5`. Both use the same captured assessment UI source. No new R2 reference prefix was uploaded; the existing verified `20261104` assets are retained. The current source upload archive is produced by `scripts/package_merit_v2.py`; source and image archives remain separate artifacts.

The earlier numerical and failure-isolation validation below remains historical evidence; it was not repeated across the full cohort for this presentation update.

## Earlier numerical scope and evidence

The 161-file pinned metric/UI source bundle and the native live bridge were captured from the current online tool. Only code, control rules, R1 and two global RefMet assets are distributed. No study matrices, cohort assessment results, historical reviewed study-design labels or Pan-ReDU study snapshots are included.

The release integration test passed **20/20 checks**, recorded with all raw responses, recalculations, logs and comparator outputs in the developer checkout's ignored `docker/merit-v2/runtime/runs/v2_validation_clean/`, `v2_validation_fault/` and `release-artifacts/validation.json`.

- ST000001 was rebuilt from previously downloaded *raw live API responses*, with no assessment imported into the calculator. The complete endpoint `sections` and rosters matched the earlier native calculation exactly, separately for datatable and mwTab. The expected JSON was read only by a separate post-build comparator.
- The full page rendered, including all tabs and Data at a glance. Changing folds and the correlation threshold still rendered. The protected low-information, redundancy and class-separability functions matched direct calls to the same source implementations.
- An injected datatable redundancy `MemoryError` affected only that check. Its stored section was absent rather than scored zero. Subsequent independent checks, annotation and separability completed. The complete mwTab sections were unchanged. The partial page rendered and named the failure; the feature-list export also completed.
- A deliberately stuck process was terminated by its configured timeout.

A final forced report-memory check also confirmed that the QC panel renders when every QC calculation is unavailable; its required analysis metadata remains present.

An additional four-case comparison passed **4/4**: selected factors/classes, sample exclusion in ML task results, sample exclusion in redundancy, and sample exclusion in missing/zero-entry results. These used the actual selector and override schemas. Results are in `release-artifacts/edit_validation.json`; the direct and protected outputs are retained alongside the fresh calculation.

The **AMD64 Docker image built successfully**. Its runtime reports Python **3.10.12**, NumPy **2.2.6**, SciPy **1.15.3** and scikit-learn **1.7.2**. A separate localhost test container fetched ST000001 directly from MW, completed **30/30 initial stages without a partial result**, and served a complete study page with HTTP 200. It had no mounted historical input or assessment cache. The test container used port 18891; the existing 8782 and 8791 servers were untouched.

## What this establishes and what remains

These tests establish the tested scientific-output parity and the matrix/check failure boundaries. They are **not an all-cohort parity claim** or proof that every possible matrix can finish on finite hardware. ARM64 is configured in the GitHub workflow; its successful build must be verified there before claiming that platform tested. External API changes or service outages can still affect a live fetch. Input fetch failures are reported rather than converted to absent data.

Global chemical references and R1 are deliberately versioned. Live Pan-ReDU evidence and rule-based design context can differ from the historical reviewed values. This difference is explicitly described in the interface and README. No historical result is silently substituted.

A per-matrix report/export memory estimate also protects the serving process before loading unusually large result lists. A report/export that cannot fit is marked not assessed for that matrix; its successfully calculated files remain in the job. Overall container termination, host failure or forceful shutdown can still interrupt a request. On-disk checkpoints preserve completed calculations; transparent automatic mid-stage resume is not claimed.

## Reproduce

Use `scripts/test_merit_v2.py --fixture PATH_TO_RAW_LIVE_JOB --expected PATH_TO_INDEPENDENT_ASSESSMENT`. The fixture must contain `inputs/` and `live_registry.json`. Only those raw inputs are copied. The test requires the fixture ST000001/AN000001 used above; it is a targeted release test, not a generic all-study audit.

Run `scripts/test_merit_v2_edits.py --run docker/merit-v2/runtime/runs/v2_validation_clean` for the additional interactive cases. Both scripts must use the release's scientific Python environment. `runtime_capture.json` identifies source inputs and the native integration files; the Docker build verifies path relocation against `source_snapshot_manifest.json`.

The build workflow publishes a separate `v2-live-20261105` tag. This document records local build/test evidence; successful remote workflow publication and package visibility must be checked separately.

## Remote publication status

All 161 required source-snapshot files were verified against the committed Git blobs, and the Docker image was also rebuilt from a clean `git archive` export. The first CI run exposed the legacy ignore rule excluding 23 required source scripts; that packaging error was corrected. The subsequent [GitHub run](https://github.com/BiosystemEngineeringLab-IITB/merit-ml/actions/runs/41908102994) initially reached publication but was denied `write_package`. After the maintainer corrected package access, **attempt 2 completed successfully** and published the AMD64 and ARM64 images. The image is now publicly downloadable without a GitHub login.

Published image: `ghcr.io/biosystemengineeringlab-iitb/merit-ml:v2-live-20261105`. Multi-platform index digest: `sha256:00c555dd72e1322405d2bb240a40a3c16b8df217fd7c41faa57bd0ace86e03e0`.

Anonymous access was checked using a newly created empty Docker configuration, independent of saved account credentials. The published AMD64 image was also checked by starting it on a separate localhost port and requesting its health endpoint and landing page. Publication evidence and the pull log are retained in the developer checkout's ignored `release-artifacts/publication_retry_20261105.json` and `public_docker_pull_20261105.log`. ARM64 built and imported its dependencies in CI; a full ARM64 study calculation is not claimed.

The dated deployment folder also contains a code-only source archive and a fully built AMD64 Docker image archive, each with SHA-256. The former requires a Docker build; the latter can be installed using `docker load`. Both exclude historical study results and credentials. The existing frozen-input services were left running; only the isolated test container was stopped after its evidence was copied into the release folder.

## 5 November reliability revision (`v2-live-20261105-r3`)

The native and browser entry points now share the checkpoint merge rules and partial-input handling. Failed transfers are not interpreted as missing deposits. Native context references follow the browser/8791 versioned policy, checked against a bundled checksum index and retrieved on demand. Quantitative matrices and metric results are not distributed in the image.

The native validation passed 20/20 checks, including exact complete endpoint sections/rosters for ST000001, an injected datatable redundancy-worker failure with the other endpoint preserved, interactive-function parity, and timeout handling. Independent Chrome checks on ST000001, ST000096, ST003741 and ST000923 reproduce the retained scientific fields. ST000923 explicitly skips four untarg_data matrices while retaining its eight datatable/mwTab matrices. An injected HTTP 503 for the ST000001 datatable leaves mwTab available. Different folds/classes and all thirteen settings were exercised. These are targeted tests, not an all-cohort or all-operating-system certification.

Browser resource skips receive a conditional notice explaining that unassessed data are not absent and linking to this repository. The browser additionally uses bounded streaming downloads, local checkpoint files, worker recovery, and bounded output writers. These browser constraints are not native scientific gates. All 161 captured canonical UI/metric files remain identical to the 8782 sources after the documented path relocation.

Source/image/deployment candidates are scanned for credential filenames, private-key material, token patterns and known server-secret values; values are never logged. GitHub Actions repeats a credential scan before building. The capture allowlist excludes study runs and credentials. Older deployment artifacts containing authentication material must be treated separately from this clean release; this revision does not claim to revoke an existing SSH key or erase provider-retained historical files.
# v2-live-20261105-r4 — browser filesystem compatibility

The ST005375 live-browser run exposed a shared `live_context_and_indexes` failure
because the Emscripten filesystem does not support the directory symlink used
to expose the freshly fetched mwTab JSON to the design-context harvester. The
runtime now copies only that analysis's small mwTab JSON file into the expected
`json/` directory. This changes file staging only; metric calculations and
reference inputs are unchanged.

Validation evidence:

- Public browser r5: ST005375 completed all 15 assessment steps; four matrices,
  no matrix memory skips, and `matrix_execution.json` reports no failed checks.
- Native 8791: health endpoint responds and the served version is
  `v2-live-20261105-r4`.
- The release capture was regenerated with 200 allowlisted code/rule/reference
  files; its credential scan reports no credentials or run data.

The historical r2 validation record above is retained as release history.
