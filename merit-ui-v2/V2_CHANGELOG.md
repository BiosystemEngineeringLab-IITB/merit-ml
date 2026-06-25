# merit-ui-v2 changelog

Date: 2026-05-02; updated 2026-05-04
Scope: local-only experimental UI copy. Root UI, remote UI, and v7 cache were not edited.

| Area | Change | Notes |
|---|---|---|
| UI isolation | Created `merit-ui-v2` from the current local runtime code | Experimental copy only. |
| v1 preservation | Created `merit-local-v1` reference copy | Do not edit; use as local reference. |
| Band labels | Replaced displayed band names with diplomatic labels | Ready -> ML-ready; Conditional -> ML-ready with caveats; Fragile -> Exploratory ML use; Not Ready -> Class-support limited; No Data -> Metadata-only record. |
| Sidebar | Removed the Acquire/Normalize/Assess/Report stepper in v2 | Replaced with scoring parameter controls. |
| Tunable parameters | Added grouped parameter controls | Band cutoffs, G2/G4/G5 gates, label-structure thresholds, p/n thresholds, analytical status thresholds, annotation thresholds. |
| Recalculation | Added server-side v2 rescoring from cached report objects | Cache JSONs are not modified; rendered report/download includes `v2_scoring_params`. |
| Dynamic text | Metric descriptors, selected tooltips, gate rules, radar band text, source badges update from current parameters | Default text remains available when default values are used. |
| Validation | Python compile and render smoke tests passed | Tested with `ST000043` default and custom parameter profile. |
| Scope clarification | Added explicit supervised classification / feature-selection scope text | Clarifies that triplicate time-course, cell-culture, or 13C-tracing designs may be scientifically valid for their original objective but remain limited for supervised ML training, validation, and feature selection. |

Known limitation: thresholds that require full raw per-feature distributions not preserved in cached JSON are not fully recomputed from raw data in this UI-only sandbox. Those controls update status/guidance only where safe from cached evidence. Full repository-wide recomputation would require backend/cache regeneration.
- Terminology rename: display labels now use `ML-eligible sample count` and `Label Structure and Class Support`; internal cache/schema keys remain unchanged.
