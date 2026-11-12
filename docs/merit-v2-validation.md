# Browser architecture documentation — 12 November 2026

Added a source-checked technical manual chapter describing direct public API retrieval, Pyodide/WebAssembly execution in a worker, local IndexedDB/virtual filesystem storage, report bridging, setting dependencies, per-matrix limits and recovery, and the exact distinction between in-page checkpoint recovery and closing/reloading the tab. Reference assets are distinguished from newly fetched study responses. All existing metric/control tables and calculations are unchanged. The manuscript-ready architecture is also supplied as docs/web-architecture.md. Documentation checks and final publication evidence are retained separately.

# Reader-focused manual — 12 November 2026

This documentation-only revision removes internal development versions, historical-cache discussion, local server addresses, release/build instructions and source-file paths from the user guide and its downloadable index. The guide starts from entering a public study ID. Actual interface labels, all metric definitions, 13 numerical controls, eight custom cut-offs and computing-limit guidance remain. Public metadata contains only reader-facing metric names, settings and chapter links; the full source audit is kept separately.

All scientific table numbers are unchanged. HTML/PDF/Markdown, mobile/dark layout, document routes, outgoing credentials and published bytes are checked. No scientific formula, default, threshold, reference value or source data changes. Earlier release verification follows.

# User manual release — 12 November 2026

`v2-live-20261112-r13` bundles the new 32-page searchable PDF and responsive HTML manual, Markdown source and a metric/control coverage manifest. Top navigation and the report heading link to `/manual/`. The guide covers 19 scored items, separate Structural/FAIR summaries, all 37 previous reference-table items, newer informational/interface features, 13 registered numerical settings, eight custom score cut-offs and native resource settings.

The documentation matches the rendered input defaults/bounds/steps and implementation registry. All 427 table cells are found in the PDF, with no out-of-page text. Light/dark, mobile overflow and contents links pass. Cached/native routes serve identical document bytes and reject non-manual paths. The reference report's scientific JSON and form inputs are unchanged. Docker serves the same four files and renders an existing test matrix report with the manual link. These are documentation/targeted regression checks, not a new all-cohort recalculation or fresh-input numerical certification. No metric formula, default, threshold or R1 changes.

The guide is bundled locally, including PDF/Markdown downloads, rather than depending on the public website to serve it in Docker. Earlier release evidence follows.

# Walkthrough release — 8 November 2026

`v2-live-20261108-r12` adds presentation-only search and report tours. The default one-time lifecycle, Back/Next/Skip, Escape, opt-out, replay, reload/new-study suppression, mobile/dark rendering, report iframe readiness and storage fallback passed 31 targeted browser checks. A complete ST000096 exported scientific state is identical to the prior 8782 baseline; completing the tour leaves scientific state and form inputs byte-identical. Only the shared page wrapper and new tour assets changed among the captured calculation/UI sources. No scientific formula, default, threshold or R1 changed.

Docker smoke verifies its landing/report embed exactly the same tour source and a completed matrix report renders from an existing validation fixture. This is not a new all-cohort metric recalculation or a fresh-input numerical validation. The prior calculation validations remain below. Tour state is browser/site-local; a different Docker host port is a different browser origin.

## 6 November 2026 — r10 dark-report contrast repair

Added one shared report stylesheet scoped entirely to `html[data-theme="dark"]` and included it in the existing renderer. No calculation, label definition, threshold, sample rule, light-mode rule or layout setting changed. Inline foreground colors and pale backgrounds are paired with dark surfaces; red/amber/green hue distinctions are retained. Readiness cards, sticky analysis labels, check markers, tabs, buttons, help popups and consent text are covered. Score heatmap fills remain unchanged; their original white/dark numeric-text choices are retained.

Chrome checks covered 63 report-tab views across ST000096, ST003741 and ST000009, including datatable, mwTab and untarg_data and expanded Details. The simple composited-color audit recorded 26,703 text comparisons with no remaining failures at 4.5:1 for ordinary text or 3:1 for large text. These are targeted rendered checks, not universal accessibility certification. Enabling/disabling this stylesheet in light mode left all measured colors and geometry exactly identical. Before/after scientific JSON exports also matched exactly on 8782 and 8791.

The public landing markup, scripts, worker, download/checkpoint code and SVG logos are byte-identical to r17; only the report-code archive/configuration changes. Docker landing/logo/report smoke passes with the new stylesheet present. Browser release `20261106-r18`; native/Docker `v2-live-20261106-r10`. Evidence and final public checks: `ML-ready/outputs/report_dark_mode_20261106/`. Existing automatic ports, sudo instructions and resource safeguards are unchanged.

## 6 November 2026 — r9 supplied SVG branding and theme synchronization

The supplied `merit-logo.svg` replaces the old PNG branding on the landing page, report corner and tools sidebar. The original remains unchanged on the source server; derivative provenance is recorded in `docker/merit-v2/logo_provenance.json`. The full-page pale raster background and export-signature metadata were removed from the derivatives. A crop with padding preserves the artwork. The dark variant lightens lettering and raster brightness for contrast; both variants have transparent backgrounds. Landing text remains “Evaluating ML readiness in tabular metabolomics datasets from Metabolomics Workbench”.

The native report's theme-button presence check previously matched its CSS selector and skipped the button itself; it now checks for an actual element ID. The browser's main theme button now updates the report document as well as the landing page, with the duplicate in-frame control hidden. Scientific formulas, thresholds, R1 and frozen data are unchanged.

Local Chrome checks covered both themes and actual theme buttons for landing/report pages on 8782 and 8791, plus mobile landing overflow. Scientific report exports from ST000001 were exactly identical before/after on both services, and SVG endpoints matched the intended bytes. The new logo is visible in the report heading even with the tools sidebar closed. The native service remains under its existing persistent systemd unit; the cache pilot retained its original launch settings.

Browser release `20261106-r17` and native/Docker `v2-live-20261106-r9` share the same captured scientific UI and SVG assets. The previous automatic localhost-port selection and sudo instructions remain in place. Evidence: `ML-ready/outputs/logo_replacement_20261106/`. Final deployment/image checks and screenshots are retained there; bounded smoke checks do not constitute all-study validation.

## 6 November 2026 — automatic localhost port allocation

The standard Docker run command and Compose configuration now publish `127.0.0.1::8791`. Docker allocates an available local host port atomically; the container continues to listen on 8791. This is a deployment/documentation change only: runtime capture, image `v2-live-20261106-r8`, formulas, UI and existing server installations are unchanged.

With the native service already occupying 8791, the isolated smoke test started both Docker run and Compose containers simultaneously on distinct available localhost ports (32772 and 32773). Both health/version checks passed. Restarting the first container selected port 32774 and passed health again; accordingly the README instructs users to check `docker port` after restart/recreation. The original 8791 service remained available. Only the temporary test containers and their test volumes were removed. Compose configuration parsing passed.

Evidence: `ML-ready/outputs/docker_autoport_20261106/check_autoport.py` and `checks.json`. The source bundle has the distinct `r8-autoport` name so the preceding source archive is preserved. No new registry image is needed for a host-side port mapping.

## 6 November 2026 — r8 disease metadata and bulk study reports

The maintainer authorized publication of both the disease repair and the previously previewed bulk-report links. The public browser release is `20261106-r14`; the native/Docker release is `v2-live-20261106-r8`.

- The official ST000010 disease endpoint declares `Cancer`. A blank retained per-source summary had overwritten the correctly parsed top-level declaration; the study index also ignored the flat response's capitalized `Disease` key. A shared parser now feeds the combined input, source summaries, index and render path. Retained summaries use the deposited input at render time. Disease is never inferred from class labels or the study title.
- Eleven parser cases and a before/after comparison on ST000010 passed: scientific UI exports are identical, and all three available source headers now show Cancer. A full raw-response replay also reproduced every endpoint's metric sections and roster. Its study-level evidence differs only in rebuilt timestamps and provenance paths. All source summaries and the study index retain Cancer. Frozen 8782 already showed the correct declaration; its scientific cache was not modified.
- Bulk completion lists one underlined new-tab interactive report link per study. Original fold/class/sample settings and the completed job are retained. The link metadata survives process restart while underlying run files remain; an expired run is reported explicitly. No new fetch is made just to open the retained report. Native two-study browser checks verified the actual new-tab click, k=3 restoration and k=10 recalculation, and link reopening after restart. Browser bulk remains disabled in favor of the full application.
- The Docker capture contains 205 code/rule/global-reference files. Source packaging uses an explicit allowlist and credential scanning; no study inputs, computed study results or server credentials are included. Fixed R1 and reference URLs are unchanged. The release does not change a scientific score, threshold or sample-eligibility rule.

Development evidence: `ML-ready/outputs/disease_metadata_fix_20261106/` and `ML-ready/outputs/bulk_interactive_reports_20261106/`. The first folder distinguishes raw-response fixture replay from the additional real public-browser fetch.

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

## Full-screen report and original logo, r6

The browser report occupies the full viewport below the 58 px navigation bar. The inner report no longer has a 1,320/1,800 px width cap, in either tools mode. A New study control returns to the search page. The unchanged original PNG from `merit-ui-v8/static/merit-logo-sidebar.png` is used on the search page and in the browser build. These are presentation changes; all calculation implementations and controls are retained. Browser release: `20261105-r11`; native/Docker release: `v2-live-20261105-r6`.

## Progress and bulk-entry release r7

The landing progress region is directly below search and the recent-study/bulk sections are hidden while assessing. Generating report is a separate visible UI step, including while the rendering request is pending. Legacy bulk and Find Similar Studies panels are removed from study reports; the legacy workspace URL redirects to the landing page. Native/cache bulk entry checks up to 500 distinct IDs and runs available entries using default settings and deposited labels, retaining per-analysis × endpoint profiles. Unknown network outcomes are not labelled invalid or embargoed. Browser bulk controls are disabled and blurred with a Docker link. Native and cached actual ST000001 bulk paths each returned two current per-matrix profiles; six UI checks passed, including a deliberately delayed renderer. Source/archive scans remain required. Browser build r13; native/Docker r7. The browser generation step remains active until the report document signals DOM readiness.

The final tour styles explicitly preserve Next-button text contrast against the report’s pre-existing dark-mode button override. The correction affects only the tour.
