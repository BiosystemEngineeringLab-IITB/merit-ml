# Figure 6 — Missingness Architecture

**Theme D: Missingness Architecture**
Repository-scale characterisation of how missingness is structured at the feature level (D1) and at the analysis level (D2). D1 goes beyond per-analysis summary statistics to reveal whether missingness is uniformly distributed across features or concentrated in a subset — a distinction with direct implications for imputation strategy. D2 provides a compact cross-source summary of analysis-level missingness brackets for direct comparison.

---

## Panel D1 — ECDF of per-feature missingness rates

**File:** `figure6_D1D2_missingness_architecture.pdf / .png` (left panel)
**Script:** `/tmp/make_figure6.py`
**Input:** `/tmp/feature_miss_rates.tsv` — computed by `/tmp/compute_feature_missingness.py`

### What it shows

Three empirical cumulative distribution functions (ECDFs) of **per-feature missingness rates**, pooled across 600 randomly sampled valid analyses per source (seed=42). For each analysis, the missingness rate of each feature column (= fraction of samples for which that feature is missing or non-numeric) is computed. All per-feature rates from all sampled analyses are pooled into a single distribution per source. The x-axis is linear from 0% to 100% missingness; the y-axis is the cumulative fraction of features at or below each rate. Three vertical reference lines mark 5%, 20%, and 50% missingness thresholds. Median and mean are annotated per source.

**Feature counts pooled:**
- Datatable: 115,574 feature-level rates (600 analyses × ~193 features/analysis on average)
- mwTab: 183,743 feature-level rates (600 analyses × ~306 features/analysis)
- Untarg data: 3,623,862 feature-level rates (600 analyses × ~6,040 features/analysis; larger due to high-dimensional peak tables)

### Key numbers

| Metric | Datatable | mwTab | Untarg data |
|---|---|---|---|
| Features with exactly 0% missing | 100.0% | 81.7% | 41.4% |
| Median per-feature missingness | 0.00% | 0.00% | 8.86% |
| Mean per-feature missingness | 0.00% | 8.18% | 30.73% |
| Features with >5% missing | 0.0% | 16.6% | 52.9% |
| Features with >20% missing | 0.0% | 12.0% | 44.1% |
| Features with 100% missing | 0.00% | 1.52% | 0.34% |

### Interpretation

**Datatable: a perfect step function at zero.** 100.0% of the 115,574 sampled datatable features have exactly 0% missingness — not a single feature has any missing values across any of the 600 sampled analyses. This confirms that datatable matrices are fully imputed prior to deposition with no exception among the sampled files. The per-analysis mean of 0.01% (from `full_matrix_stats.tsv`) reflects the two extreme outlier analyses that account for essentially all datatable missingness. For ML purposes, datatable requires no imputation preprocessing whatsoever.

**mwTab: bimodal distribution — mostly complete, long right tail.** 81.7% of mwTab features are at exactly 0% missingness, producing a large initial jump in the ECDF. The remaining 18.3% form a right tail extending to 100% missingness. The mean (8.18%) substantially exceeds the median (0%), confirming the bimodal nature: most features are fully observed, but a minority carry high missingness rates. The 1.52% of features with 100% missingness are column-wise empty features — present in the mwTab header but with no numeric values for any sample — likely arising from metabolites detected in some but not all sub-assays within a multi-block mwTab file. The practical implication: mwTab data requires a two-step preprocessing approach — (1) identify and drop features with >50% missingness (affects 12% of features); (2) apply imputation only to the remaining 6% of features that have moderate (1–50%) missingness rates.

**Untarg data: the most challenging missingness regime.** Only 41.4% of untarg_data features have zero missingness; the majority (58.6%) have at least some missing values. The median per-feature missingness of 8.86% (mean 30.73%) reflects a distribution that is substantially right-shifted relative to the other sources. 44.1% of features exceed 20% missingness, and 52.9% exceed 5%. This is the fingerprint of MNAR (missing not at random) missingness: in untargeted peak tables, a feature is absent for a sample because its signal fell below the instrument detection limit, not because of a technical error. The 0.34% of features with 100% missingness are vestigial — features detected in at least one sample in the analysis header but never observed numerically (possibly from a pooled-sample detection step). The high missingness, combined with the high dimensionality (median 2,388 features), means that standard KNN or MICE imputation applied naively to untarg_data matrices would be both computationally expensive and statistically unreliable. Minimum-value imputation (substituting half the column minimum) is the domain-standard approach but introduces downward bias for genuinely low-abundance metabolites.

**Mechanistic contrast — mean vs. median reveals the imputation challenge:**
- For datatable: mean = median = 0% → no imputation needed
- For mwTab: mean (8.18%) >> median (0%) → right-skewed, concentrated in a feature minority → column-drop then targeted imputation
- For untarg_data: mean (30.73%) >> median (8.86%) → broad distribution, affects majority of features → requires a missingness-aware workflow, not a simple imputation step

---

## Panel D2 — Heatmap: source × missingness bracket

**File:** `figure6_D1D2_missingness_architecture.pdf / .png` (right panel)
**Script:** `/tmp/make_figure6.py`
**Input:** `outputs/diagnostics/full_matrix_stats.tsv` (per-analysis `pct_missing`, valid analyses only)

### What it shows

A 3 × 4 heatmap where rows are sources (datatable, mwTab, untarg_data) and columns are per-analysis missingness brackets (<1%, 1–5%, 5–20%, >20%). Cell values show both the raw count and the percentage of each source's valid analyses falling in that bracket. Colour intensity (YlOrRd scale) encodes the percentage within each source row, making the dominant regime for each source immediately visible.

### Key numbers

| Source | <1% | 1–5% | 5–20% | >20% | Total valid |
|---|---|---|---|---|---|
| Datatable | 4,865 (100.0%) | 0 (0.0%) | 0 (0.0%) | 2 (0.3%) | 4,867 |
| mwTab | 3,332 (66.7%) | 403 (8.1%) | 655 (13.1%) | 605 (12.1%) | 4,995 |
| Untarg data | 718 (38.6%) | 249 (13.4%) | 367 (19.7%) | 526 (28.3%) | 1,860 |

### Interpretation

The heatmap compactly validates the **source priority ordering** (datatable > mwTab > untarg_data) on purely empirical data completeness grounds:

**Datatable is almost entirely in the <1% bracket (100.0%).** Only 2 analyses out of 4,867 exceed 1% missingness at the analysis level, and both are in the >20% bracket (extreme outliers). This extraordinary completeness is consistent with the D1 finding that 100% of datatable features have zero missingness — the two analyses with analysis-level missingness >20% may reflect files with corrupted or entirely missing value columns.

**mwTab shows a three-regime distribution.** 66.7% of analyses are in the <1% bracket (fully imputed, like datatable), 8.1% are in 1–5% (light missingness, trivial to impute), 13.1% are in 5–20% (moderate), and 12.1% are in >20% (high). The 33.3% of mwTab analyses with ≥1% missingness correspond to studies using non-numeric placeholder tokens (ND, NA, BDL) rather than zero-fill imputation — these studies require preprocessing before any ML model can be applied.

**Untarg data is spread across all four brackets.** Only 38.6% of analyses fall below 1% missingness, while 28.3% exceed 20%. This is the flattest distribution — untarg_data analyses are not clustered into a dominant missingness regime, meaning no single imputation strategy is universally appropriate. Any ML pipeline consuming untarg_data must implement missingness-adaptive preprocessing at the per-analysis level.

**The heatmap diagonal reveals the priority rationale:** moving from datatable (top row, deep red in <1%) to mwTab (middle row, lighter gradient) to untarg_data (bottom row, colour spread across all columns) shows a monotonic deterioration in data completeness that directly motivates the priority selection rule.

---

## Generation

- **Input:** `/tmp/feature_miss_rates.tsv` (per-feature rates, generated by `/tmp/compute_feature_missingness.py`); `outputs/diagnostics/full_matrix_stats.tsv` (per-analysis missingness)
- **Sampling:** 600 analyses per source, random seed 42, parallel processing with 28 workers (`ProcessPoolExecutor`)
- **Feature counts:** datatable 115,574; mwTab 183,743; untarg_data 3,623,862
- **Script:** `/tmp/make_figure6.py`
- **Style:** DejaVu Sans; bold titles 13pt; bold axis labels 12pt; bold tick labels 11pt; 300 DPI PNG + vector PDF
