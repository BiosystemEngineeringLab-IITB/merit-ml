# Supplementary Note: Source-Aware Missingness Semantics (mwTab vs Datatable)

## 1. Scope and cohorts analyzed
All checks were run on the local latest dump at:
`/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version`
and the locked source-presence table:
`/home/shayantan/metabolomics/ML-ready/outputs/diagnostics/mw_6696_source_presence.tsv`.

### Cohort sizes
| Cohort definition | Analyses (n) |
|---|---:|
| Full catalogue | 6,696 |
| File-paired (`mwtab.txt` + `datatable.tsv` present) | 4,864 |
| Matrix-valid overlap (`mwtab_valid_present=1` and `datatable_valid_present=1`) | 4,856 |
| Strict mwTab↔datatable-only cohort (`mwtab_valid=1`, `datatable_valid=1`, `untarg_valid=0`; combo 110) | **4,464** |

The strict 4,464 cohort was used for the final source-aware missingness inference.

## 2. Cell-level mapping method (orientation-aware)
For each study-analysis pair:

1. `mwtab` parsed as **features × samples** from the quantitative metabolite block:
   - Sample IDs from first row (`Samples\t...`)
   - Feature IDs from first column of data rows
   - `Factors` row skipped
2. `datatable` parsed as **samples × features**:
   - Sample IDs from first column
   - Feature IDs from header (columns 3+)
3. Exact cell mapping by `(study_id, analysis_id, sample_id, feature_name)`.

This avoids position-based mismatch from the opposite matrix orientation.

## 3. Missing-token logic used
Explicit mwTab missing tokens were taken from MERIT logic (`merit.metrics.analytical._MISSING_TOKENS_LOWER`), excluding empty strings for this specific experiment:

`na, n/a, null, nan, nd, bdl, bql, nq, loq, lod, llod, lloq, bloq, missing, not detected, .` (+ case-folded variants).

For this analysis, only explicit token cells were counted as “mwTab missing-token cells”.

## 4. Hard metrics supporting source-aware missingness
### 4.1 Strict 4,464 cohort: explicit mwTab token cells
Total explicit mwTab token cells: **1,148,914**

| Datatable outcome for matched mwTab token cell | Count | Percent |
|---|---:|---:|
| `datatable_zero` | 703,551 | **61.24%** |
| `datatable_nonzero_numeric` | 248,822 | 21.66% |
| `feature_dropped_in_datatable` | 196,541 | 17.11% |
| `sample_missing_in_datatable_feature` | 0 | 0.00% |
| `datatable_missing_token` | 0 | 0.00% |
| `datatable_nonnumeric_other` | 0 | 0.00% |

Among retained matched cells (zero + nonzero), zero share is:

- `703,551 / (703,551 + 248,822) = 73.87%`

### 4.2 Token-specific conversion (strict 4,464 cohort)
| mwTab token | Total | To datatable zero | To datatable nonzero | Dropped feature |
|---|---:|---:|---:|---:|
| `na` | 894,685 | 66.56% | 21.27% | 12.17% |
| `.` | 90,999 | 66.78% | 31.70% | 1.52% |
| `n/a` | 90,492 | 11.93% | 3.09% | 84.98% |
| `null` | 30,769 | 64.00% | 31.23% | 4.77% |
| `nd` | 20,757 | 70.94% | 17.83% | 11.23% |
| `nan` | 19,972 | 9.67% | 67.61% | 22.71% |
| `bloq` | 1,184 | 8.45% | 6.25% | 85.30% |

## 5. Explicit cell examples (mwTab token → datatable outcome)
Examples were extracted directly from exact matched cells in the strict 4,464 cohort.

| Study | Analysis | Sample | Feature | mwTab token | Datatable value | Outcome |
|---|---|---|---|---|---|---|
| ST000309 | AN000489 | MM66-3S1 | 11-Octadecenoic acid | `nd` | `0` | datatable_zero |
| ST000309 | AN000489 | MM66-3S1 | 2(1H)-Pyrimidinone, 1-b-D-ribofuranosyl-4-hydroxy-5'-P | `nd` | *(absent)* | feature_dropped_in_datatable |
| ST000337 | AN000544 | 121356 | Alanine | `n/a` | `3.70` | datatable_nonzero_numeric |
| ST000337 | AN000544 | 121356 | Arginine | `n/a` | `0` | datatable_zero |
| ST000337 | AN000544 | 121356 | Citrulline | `n/a` | *(absent)* | feature_dropped_in_datatable |
| ST000351 | AN000567 | 1 | Citrulline | `bloq` | `133.35` | datatable_nonzero_numeric |
| ST000351 | AN000567 | 2 | Citrulline | `bloq` | `0` | datatable_zero |
| ST000351 | AN000568 | 1 | C3-DC | `bloq` | *(absent)* | feature_dropped_in_datatable |
| ST000432 | AN000682 | sample50 | 24.25.DHVD.D2 | `na` | `0` | datatable_zero |
| ST000432 | AN000682 | sample50 | 24.25.DHVD.D3 | `na` | `1.21` | datatable_nonzero_numeric |
| ST000508 | AN000777 | 1.2 | 1-palmitoylglycerol (1-monopalmitin) | `null` | `176234.00` | datatable_nonzero_numeric |
| ST000508 | AN000777 | 2.2 | 1-palmitoylglycerol (1-monopalmitin) | `null` | `0` | datatable_zero |

## 6. Interpretation for MERIT source-aware missingness
These data justify source-aware treatment:

- **Datatable zeros should be treated as non-missing numeric values** (post-curation fill/imputed representation for retained cells).
- **mwTab (and untarg) zeros should be treated as missing/non-detect**, together with explicit missing tokens.
- Nonnumeric explicit tokens are missing in all sources.

This preserves biological missingness in raw-like sources (mwTab/untarg) while avoiding inflation of missingness in curated datatable matrices.

## 7. Reproducibility artifacts generated
- `outputs/diagnostics/mwtab_datatable_missing_transition_all_pairs.tsv`
- `outputs/diagnostics/mwtab_datatable_missing_transition_combo110_4464.tsv`
- `outputs/diagnostics/source_aware_missingness_metrics_combo110.tsv`
- `outputs/diagnostics/source_aware_missingness_examples_combo110.tsv`
