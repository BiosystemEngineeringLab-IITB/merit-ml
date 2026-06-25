# MERIT v7 Threshold Sensitivity Analysis

Source cache: `merit-cache-workbench-full-v7`

Unit: one study-level workflow state from `index.json` (`n=4,121` studies).

This is a post hoc sensitivity analysis. It does not recompute source parsing or metric extraction. It varies only the directly threshold-dependent components:

- `minimum_sample_count` score and G2 biological-sample gate: thresholds 10, 20, 30.
- `label_suitability` score and G4 minimum-class gate: thresholds 3, 5, 10.
- G5 sample-level missingness is held at the MERIT v7 default: pass <=50%, warn <=80%, fail >80%.

The default cell is sample threshold 20 and minimum-class threshold 5.

Default-validation mismatches against the cached MERIT v7 final band: `0`.

## Key Results

- Default band counts: Ready 1,464, Conditional 1,422, Fragile 12, Not Ready 1,075, No Data 148.
- Most permissive profile (`N>=10`, class `>=3`) changes 1,206 studies (29.3%): 1,206 improve and 0 worsen.
- Strictest profile (`N>=30`, class `>=10`) changes 1,093 studies (26.5%): 0 improve and 1,093 worsen.
- Maximum change over the 3x3 sensitivity grid occurs for `sample10_class3_missing50_80`: 1,206 studies (29.3%).

## Files

- `threshold_sensitivity_per_study.tsv`: per-study band under each threshold profile.
- `threshold_sensitivity_scenario_summary.tsv`: band counts and changed-study counts per profile.
- `threshold_sensitivity_transition_summary.tsv`: current-band to scenario-band transition counts.
- `figure5_threshold_sensitivity_band_composition.*`: stacked band composition across scenarios.
- `figure5_threshold_sensitivity_changed_heatmap.*`: percent of studies whose final band changes.
- `figure5_threshold_sensitivity_transition_extremes.*`: transitions for permissive and strict profiles.
