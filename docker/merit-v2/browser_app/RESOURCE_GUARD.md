# Browser resource handling — v2-live-20261105-r2

The guard and execution notes apply only to the browser application. They do not impose the browser's memory or file limits on 8782, 8791 or the native Docker application. No metric formula, scientific default, R1 cut-off or canonical 8782 source file was changed.

## Decisions and availability

Every analysis × endpoint is considered separately. The original estimate remains 330 MiB runtime + 60 bytes per cell + 4 bytes per input-file byte + 55 bytes per estimated stored feature pair + 16 bytes per squared sample-position count. Pair density is probed using up to 1,000 features, seed 0, when necessary. The admission budget is 1 GiB, or 0.5 GiB on devices reporting less than 4 GB RAM. This is an estimate, not a measured or guaranteed peak.

Responses are streamed. A declared response larger than 50 MB can be refused before its body is collected; otherwise collection stops at the same limit. A refused mwTab text response may be streamed to completion to retain a bounded metadata-only view. That view is explicitly identified as derived, never described as the raw response. Complete responses have a full SHA-256. Interrupted bodies have a received-byte hash with an explicit partial scope and no fabricated full-response hash.

Skipped mwTab quantitative rows are removed before bootstrap parsing; sample headers and metadata are retained. A datatable's zero provenance is not guessed when its paired mwTab cannot be assessed. Failed transfers and unassessed checks remain distinct from absent files and zero scores.

## Report and recovery

Only reports with memory/file-limit skips receive the prominent browser notice naming the affected analyses and endpoints. It says that empty results do not mean absent data and links to https://github.com/BiosystemEngineeringLab-IITB/merit-ml. Ordinary studies and the native applications do not receive that browser notice.

Initial calculations call the same `matrix_worker.execute` scientific stages as the native supervisor. Successful stages are merged and checkpointed atomically into IndexedDB on the user's device. Read-only Blob-backed files avoid loading every restored file into the WebAssembly heap; up to a small bounded amount is preloaded to avoid repeated tiny reads. A worker is replaced when the heap grows beyond the recycling threshold, when beginning another study, or when a failed worker must be recovered. The interrupted matrix/check is named; remaining tasks continue from the checkpoint. Shared metadata failures are identified as shared.

Interactive reporting normally runs the original implementation once. Endpoint access is tracked so exceptions or an interrupted worker identify the affected result. Eligibility or low-information changes that would require excessive worst-case pair storage are reported as not assessed; changing labels or subjects alone does not trigger that resource exclusion. A new study cannot receive an old worker's results.

These checkpoints support recovery within the running page. An operating-system tab kill, unavailable IndexedDB, exhausted browser disk quota or a browser-wide failure can still interrupt the application. The guard cannot promise unlimited execution or universal recovery. The full native application remains the alternative for such cases.

## Downloads, discovery and provenance

ZIP, CSV/text and JSON output writers enforce the 50 MB browser output limit during construction. Large feature-pair lists receive an additional preflight estimate. Run records include retained inputs, successful results, omission/refusal reasons and the fetch/checkpoint records. They do not claim to include bodies that were never retained.

The study browser uses live public discovery metadata, with unknown fields kept unknown; an ID-only fallback is available if the summary transfer fails. Context reference files are checked against a versioned SHA-256 index shared with the native runtime. Historical reviewed context and freshly computed programmatic context are distinguished.

Release staging uses explicit runtime allowlists, recursive ZIP-content scanning and server-secret matching without logging secret values. Credential/key filenames and private-key material are refused. Server deployment credentials are not browser assets or image inputs.

## Validation and coverage boundary

Independent evidence is in `outputs/browser_reliability_v2_20261105/` and `online-tool/runs/reliability_v2_20261105/`. Tests cover normal studies, ST000923's four skipped untarg_data matrices with eight retained matrices, altered classes/folds/settings, partial HTTP failures, native worker failure and timeout handling. Exact source hashes and numeric comparisons are recorded there.

The original 5 November coverage figures (98.9% of matrices admitted) describe the preceding guard. They are not recertified coverage for this revision, which adds a streaming input cap and different execution handling. Do not quote them as a new all-cohort validation. The original guard document is preserved in `online-tool/releases/20261105-browser-reliability-v2/before/browser_app/RESOURCE_GUARD.md`.
