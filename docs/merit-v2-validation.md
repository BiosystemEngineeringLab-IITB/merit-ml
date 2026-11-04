# MERIT v2 live Docker validation — 5 November 2026

This is the native Docker release at `deploy/merit-v2-docker-2026-11-05`, separate from both running development services. No 8782/8791 code, historical cache or frozen study dump was changed.

## Scope and evidence

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
