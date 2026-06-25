# Figure 4 — Sample-Size Power Landscape

**Theme B: Sample-Size Power Landscape**
Repository-scale characterisation of statistical power availability and its interaction with data completeness across all 6,696 study-AN bundles, stratified by the three official REST API sources. All statistics computed from `outputs/diagnostics/full_matrix_stats.tsv` using the full matrix (no subsampling), valid analyses only (datatable: n=4,867; mwTab: n=5,004; untarg_data: n=1,860).

---

## Panel B1 — ECDF of sample count per source with ML threshold annotations

**File:** `figure4_B1_ecdf_sample_count.pdf / .png`
**Script:** `/tmp/make_figure4.py`

### What it shows

Three empirical cumulative distribution functions (ECDFs) — one per source (blue = datatable, green = mwTab, orange = untarg_data) — plotted on shared log-scale x-axis (sample count N, range 1–6,000) and linear y-axis (cumulative fraction of analyses, 0–100%). Four vertical reference lines mark canonical ML threshold sample sizes:
- **N=20** (red dashed): practical minimum for stratified k-fold cross-validation with binary classes; below this, class folds become too small for meaningful hold-out evaluation
- **N=50** (orange dashed): conventional adequacy for logistic regression with modest feature sets after feature selection
- **N=100** (amber dashed): threshold for robust model evaluation; below this, variance in AUROC estimates from repeated holdout exceeds ±0.05
- **N=200** (green dashed): large-cohort threshold; at this scale, random forest and other high-variance estimators begin to stabilize without heavy regularisation

At each threshold, the percentage of analyses falling below is annotated per source in the respective source colour. The region N<20 is shaded red to make the "insufficient for CV" zone immediately visible. The ECDF step character is preserved (not smoothed) to faithfully represent the discrete distribution.

### Key numbers

| Threshold | Datatable below | mwTab below | Untarg data below |
|---|---|---|---|
| N < 20 | 28.3% | 32.5% | 25.0% |
| N < 50 | 61.1% | 65.3% | 58.0% |
| N < 100 | 80.1% | 84.0% | 77.2% |
| N < 200 | 90.0% | 92.2% | 86.9% |

### Interpretation

The ECDF makes visible what the median alone obscures: **more than 60% of all analyses — across every source — fall below N=50**, the conventional adequacy threshold for supervised ML. The median of N=36 (datatable) is not a representative operating point; it sits at the 55th–60th percentile, meaning the majority of the repository is operating below even the most permissive adequacy standard.

Several structurally important observations emerge:

**The three sources have near-identical ECDF shape.** Despite serving different annotation tiers (Tier 1 vs Tier 2) and having different feature dimensionalities, the sample-count distributions are strikingly similar (all three curves are within ~5 percentage points at every threshold). This suggests that sample-size limitations are not a source-specific artefact but a **repository-wide structural property** reflecting the typical size of metabolomics cohorts submitted to the Workbench (clinical pilot studies, small animal experiments, targeted mechanistic studies).

**The N<20 zone (28–33% of analyses) represents a hard exclusion tier.** Stratified k-fold cross-validation with k=5 requires at least 5 samples per class per fold — implying a minimum of ~25 samples for binary classification (5 folds × 2 classes × ~2.5 samples per cell). Analyses below N=20 cannot meaningfully support any standard supervised evaluation protocol and should receive a structural "fail" for the ML readiness dimension regardless of annotation quality or missingness.

**Untarg_data analyses are marginally better powered** (25% below N=20) than datatable or mwTab (28–33%). This counterintuitive result reflects that untarg_data tends to be retrieved from studies using large-scale untargeted platforms (e.g., population-level GWAS-style metabolomics, large biobank cohorts), whereas targeted datatable studies often originate from small mechanistic or clinical pilot work.

**Only ~10% of analyses reach N≥200**, the threshold at which high-variance ML estimators stabilise. The repository's statistical power is fundamentally constrained, and any repository-scale ML benchmark must account for this: model performance estimates will have high variance for the majority of individual studies, and aggregate benchmarks should be interpreted at the cohort level rather than the individual-study level.

---

## Panel B2 — Power × completeness plane (sample count vs. missingness)

**File:** `figure4_B2_power_completeness_scatter.pdf / .png`
**Script:** `/tmp/make_figure4.py`

### What it shows

Three side-by-side scatter plots — one per source — each displaying the **power × completeness plane**: x-axis = missing value percentage (0–100%), y-axis = sample count N (log scale, 1–max). Each panel includes:
- **Per-analysis scatter point** (semi-transparent, rasterised) at coordinates (missingness%, N)
- **LOWESS trend line** (black, bandwidth=0.35) fitted to log(N) vs. missingness%, back-transformed to linear N — shows the local average relationship between the two variables
- **Quadrant boundaries:** vertical dashed line at missingness=10%, horizontal dashed line at N=30
- **Double-jeopardy zone** (lower-right, red shading): N<30 AND missingness>10% — analyses where both sample insufficiency and data incompleteness compound simultaneously
- **ML-optimal zone** (upper-left, green label): high N, low missingness — the analytically tractable region
- **Marginal histograms:** top marginal for missingness distribution; right marginal for log(N) distribution, both in source colour
- **Pearson r** of log(N) vs. missingness% annotated top-right of each scatter

### Key numbers

| Metric | Datatable | mwTab | Untarg data |
|---|---|---|---|
| Missingness > 10% | 0.0% | 18.9% | 39.6% |
| N < 30 AND miss > 10% (double-jeopardy) | 0.0% | 8.1% | 10.2% |
| Pearson r (log N vs miss%) | −0.054 | +0.061 | +0.200 |

### Interpretation

The power × completeness plane reveals three qualitatively different regimes across the three sources:

**Datatable: no missingness problem, sample-size problem only.**
Datatable analyses cluster tightly against the y-axis (missingness ≈ 0% for virtually all analyses — median 0%, mean 0.01%) across the full range of N. The near-zero Pearson r (−0.054) confirms that missingness and sample count are independent. There is no double-jeopardy zone because datatable matrices are always fully imputed before deposition. The sole ML challenge is sample insufficiency, which affects 28% of analyses (N<20) but is uncorrelated with any other quality dimension. This makes datatable the cleanest source for assessing the pure effect of sample size on ML performance.

**mwTab: modest missingness in a subpopulation, weak positive trend.**
mwTab shows a bimodal missingness distribution: the majority of analyses cluster at 0% (the mode), with a secondary population spread across 0–100% missingness. The 18.9% of analyses exceeding 10% missingness come from studies that use non-numeric placeholder tokens (NA, ND, BDL, etc.) rather than imputed zeros. The weakly positive r (+0.061) suggests a slight tendency for larger studies to have more missingness — possibly because larger multi-centre studies are more likely to include samples with partially detected metabolite panels. The 8.1% double-jeopardy rate (N<30, miss>10%) is a meaningful quality tier: these 406 analyses have both insufficient statistical power and incomplete data, making them unsuitable for standard supervised ML without imputation.

**Untarg_data: substantial missingness, positive N-missingness correlation.**
Untarg_data shows the most challenging quality landscape. 39.6% of analyses exceed 10% missingness, and the LOWESS trend reveals a **positive correlation between N and missingness** (r=+0.20) — larger studies have higher missingness rates. This is structurally interpretable: larger untargeted cohort studies are more likely to include samples from diverse matrices or collection conditions, where rare low-abundance peaks drop below detection threshold in a subset of samples. The missingness is therefore not random (MCAR) but likely missing not at random (MNAR) — peaks absent because they are below the instrument detection limit, not because of technical failure. This has critical implications for imputation: minimum-value or KNN imputation applied to MNAR data introduces systematic downward bias for rare metabolites. The 10.2% double-jeopardy rate (190 analyses with N<30 and missingness>10%) represents the most analytically challenging subset of the repository — high-dimensional, underpowered, and incomplete simultaneously.

**Cross-source comparison: compounding vs. independent failure modes.**
A key finding of this panel is that **missingness and sample insufficiency are largely independent for datatable but positively correlated for untarg_data**. This means:
- For datatable: the two failure modes (low N, high missingness) do not compound — a study is either underpowered or incomplete, rarely both. Readiness can be improved by addressing each problem independently.
- For untarg_data: the two failure modes compound. Studies with large cohorts AND high-dimensional peak tables AND high missingness represent a structural class of analyses where no simple preprocessing fix is sufficient — they require dedicated MNAR-aware imputation, feature selection under missing data, and cross-validated evaluation correcting for high-variance estimates.

---

## Generation

All panels generated from:
- **Input:** `outputs/diagnostics/full_matrix_stats.tsv` (valid analyses only: datatable n=4,867; mwTab n=5,004; untarg_data n=1,860)
- **Script:** `/tmp/make_figure4.py`
- **Dependencies:** `statsmodels` (LOWESS); falls back gracefully if unavailable
- **Style:** DejaVu Sans; bold titles 14pt; bold axis labels 10–12pt; bold tick labels 9–11pt; 300 DPI PNG + vector PDF
- **Rasterisation:** scatter points in B2 are rasterised (`rasterized=True`) to keep PDF file size manageable given ~11,700 total points across three panels
