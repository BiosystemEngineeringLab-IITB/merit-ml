# Figure 7 — Platform & Analytical Method Stratification

**Theme E: Platform & Analytical Method Stratification**
Platform is the largest single confounder of the feature-count, p/n ratio, and missingness distributions shown in Figures 2–6. This theme breaks down the repository by analytical method to reveal which platforms drive which ML-readiness regimes, and whether untarg_data availability is platform-confounded. Platform metadata parsed from `AN:ANALYSIS_TYPE`, `CH:CHROMATOGRAPHY_TYPE`, and `MS:ION_MODE` fields in mwTab text files using parallel processing (28 workers, all 6,696 study-AN bundles). Classified results saved to `/tmp/platform_classified.tsv`.

---

## Panel E1a — Analytical platform landscape (treemap)

**File:** `figure7_E1_platform_landscape.pdf / .png` (left panel)
**Script:** `/tmp/make_figure7.py`

### What it shows

A squarify treemap where tile area is proportional to analysis count. Tile colour indicates platform family (blue = LC-MS, green = GC-MS, purple = NMR, orange = CE-MS, teal = DI-MS, grey = other/unknown); shade within each family encodes ionisation polarity (darker = positive mode, lighter = negative, lightest = unspecified). Tile labels show the platform name, polarity, and raw count.

### Key numbers

| Platform | Count | % of 6,696 |
|---|---|---|
| LC-MS (reversed phase) | 3,469 | 51.8% |
| LC-MS (HILIC) | 1,739 | 26.0% |
| GC-MS | 582 | 8.7% |
| NMR | 243 | 3.6% |
| DI-MS | 174 | 2.6% |
| LC-MS (normal phase) | 103 | 1.5% |
| CE-MS | 93 | 1.4% |
| LC-MS (other) | 137 | 2.0% |
| Other/Unknown | 62 | 0.9% |
| **Total LC-MS** | **~5,600** | **~83.7%** |

Polarity breakdown for LC-MS analyses:
- LC-MS RP: 1,893 positive / 1,421 negative / 151 unspecified
- LC-MS HILIC: 749 positive / 743 negative / 245 unspecified
- GC-MS: 507 positive / 54 unspecified (GC-MS is inherently EI, always positive)

### Interpretation

LC-MS dominates the Metabolomics Workbench at 83.7% of all analyses. The near-equal positive/negative split within both RP and HILIC classes reflects standard practice: many studies run both polarities on the same samples, contributing two AN entries per sample set. This is an important consideration for feature redundancy — a metabolite detected in both positive and negative mode appears twice in the repository count, inflating the apparent feature diversity. The HILIC/RP split (~26%/52%) reflects the complementary coverage of these separation modes: RP captures lipids and hydrophobic metabolites; HILIC captures polar and ionic metabolites (amino acids, nucleotides, organic acids).

GC-MS (8.7%) is represented almost exclusively as positive mode (electron ionisation), consistent with GC-EI being the standard GC-MS configuration. NMR (3.6%) and DI-MS (2.6%) are niche but meaningful: NMR studies typically have small feature sets (median p=38, see E2) while DI-MS studies can reach very high feature counts (median p=180, mean p=405) due to untargeted MS1 profiling without chromatographic separation. CE-MS (1.4%) is a specialised platform for ionic metabolites.

---

## Panel E1b — Source combination by platform (stacked bar)

**File:** `figure7_E1_platform_landscape.pdf / .png` (right panel)
**Script:** `/tmp/make_figure7.py`

### What it shows

A 100% stacked horizontal bar chart showing the distribution of source combinations (DT+mwTab, All three, Untarg only, mwTab only, etc.) within each major platform group. Addresses the confounding question: is untarg_data availability platform-specific?

### Key numbers

| Platform | DT+mwTab (%) | All three (%) | Untarg only (%) | No valid (%) |
|---|---|---|---|---|
| LC-MS (RP) | 61% | 8% | ~27% | ~4% |
| LC-MS (HILIC) | 70% | ~8% | ~18% | ~4% |
| GC-MS | 90% | — | ~2% | — |
| NMR | 47% | — | — | ~34% |
| CE-MS | 82% | — | ~14% | — |
| DI-MS | 68% | — | ~15% | ~15% |

Untarg-only breakdown (1,436 analyses):
- LC-MS RP: 929 (64.7%)
- LC-MS HILIC: 378 (26.3%)
- LC-MS NP: 36 (2.5%)
- GC-MS: 35 (2.4%)
- DI-MS: 30 (2.1%)
- CE-MS: 14 (1.0%)

### Interpretation

**Untarg_data availability is strongly platform-confounded with LC-MS.** 91% of untarg-only analyses (929 + 378 = 1,307 out of 1,436) are LC-MS (RP or HILIC). This is not coincidental: the `/untarg_data/` REST endpoint returns raw mz/RT peak tables from untargeted LC-MS experiments where full annotation has not been completed. GC-MS studies almost never appear as untarg-only (only 35 out of 582 GC-MS analyses, ~6%) because GC-MS data is routinely library-matched and submitted as a named datatable; the GC-MS domain has standardised spectral libraries (NIST, Golm) that enable near-complete annotation.

**NMR has the highest "no valid source" rate (~34%)** — a structural limitation of the mwTab format for NMR: many NMR studies deposit spectra-level data (chemical shifts, peak widths) in formats that do not parse as numeric abundance matrices under the strict validation criteria.

**GC-MS is the most complete platform (90% DT+mwTab, ~0% no-valid-source),** confirming that GC-MS studies are the most reliably formatted for ML pipelines. The near-total datatable+mwTab co-occurrence indicates systematic curation before deposition.

---

## Panel E2 — Feature count ECDF stratified by analytical platform

**File:** `figure7_E2_feature_count_ecdf_by_platform.pdf / .png`
**Script:** `/tmp/make_figure7.py`

### What it shows

Six ECDFs of feature count per analysis, one per major platform (LC-MS RP, LC-MS HILIC, GC-MS, NMR, CE-MS, DI-MS), drawn exclusively from datatable-source valid analyses to hold the source constant. The x-axis is log-scale; three vertical reference lines mark p=50, p=200, and p=500. Median feature count is annotated in the legend for each platform.

### Key numbers

| Platform | n analyses | Median p | Mean p | p<50 | p>200 | p>500 |
|---|---|---|---|---|---|---|
| NMR | 116 | 38 | 52 | 65% | 3% | 0% |
| GC-MS | 528 | 73 | 135 | 41% | 12% | 2% |
| CE-MS | 76 | 85 | 100 | 28% | 7% | 1% |
| LC-MS (RP) | 2,398 | 95 | 215 | 32% | 30% | 10% |
| LC-MS (HILIC) | 1,315 | 104 | 192 | 28% | 27% | 7% |
| DI-MS | 118 | 180 | 405 | 18% | 46% | 26% |

### Interpretation

The E2 ECDF makes the platform-specific ML regimes directly visible:

**NMR is the most tractable platform for ML.** 65% of NMR analyses have fewer than 50 features, and 97% have fewer than 200. NMR typically quantifies 20–100 metabolites (standard 1D ¹H spectra: 30–50 bins; targeted quantification: 15–40 metabolites). The low dimensionality means that p/n is well-posed for even small cohorts, and no feature selection is required before applying standard classifiers. NMR is also the most analytically reproducible platform, making it the safest substrate for ML benchmarking.

**GC-MS occupies a moderate, tractable regime.** With median p=73 and 88% of analyses below p=200, GC-MS studies are generally well-posed for supervised ML. The near-complete annotation (library matching against NIST/Golm) means features carry reliable biochemical identities. The 2% of GC-MS analyses exceeding p=500 likely reflect derivatisation-based workflows with multiple fragment ions per metabolite counted as separate features.

**CE-MS is similarly tractable,** with a compact feature range (median p=85, 99% below p=500) and consistent targeting of specific metabolite classes (amino acids, organic acids, nucleotides).

**LC-MS (RP and HILIC) span a wide range.** Both show broad ECDFs stretching from p<10 to p>5,000, reflecting the heterogeneity of LC-MS study designs on the Workbench — from highly targeted panels (10–50 metabolites) to semi-targeted or annotated untargeted profiles (200–2,000 metabolites). The 10% of LC-MS RP analyses exceeding p=500 represent large annotated untargeted datasets where metabolite IDs have been assigned to peaks but the full high-dimensional matrix is deposited. The nearly overlapping ECDF curves for RP and HILIC confirm that chromatographic mode (RP vs HILIC) does not per se determine feature count — study design (targeted vs untargeted) is the dominant factor.

**DI-MS is the most extreme platform in terms of feature count.** Median p=180, mean p=405, and 26% of analyses exceeding p=500. Direct infusion MS without chromatographic separation produces wide-survey MS1 spectra with many detected masses. Without retention time separation, feature identity is more ambiguous (isomers co-elute), and the high p/n ratio is compounded by lower reproducibility than chromatographic methods. DI-MS is the most challenging platform for supervised ML in the datatable source.

**Practical implication for dataset selection:** The ECDF curves allow researchers to set platform-specific p/n thresholds rather than applying the aggregate p/n ≤ 10 rule uniformly. For NMR and CE-MS studies, a cohort of N≥20 is sufficient for well-posed classification. For LC-MS RP studies with p>500, N≥100 is the minimum for stable model training with any reasonable feature selection step.

---

## Generation

- **Platform parsing:** Parallel mwTab header scan (28 workers, all 6,696 analyses) → `/tmp/platform_classified.tsv`; fields: `AN:ANALYSIS_TYPE`, `CH:CHROMATOGRAPHY_TYPE`, `MS:ION_MODE`
- **Classification rules:** NMR if `AN:ANALYSIS_TYPE=NMR`; then chromatography type: GC→GC-MS, CE→CE-MS, direct infusion→DI-MS, HILIC/pHILIC→LC-MS (HILIC), reversed phase/UPLC→LC-MS (RP), normal phase→LC-MS (NP), ion pair→LC-MS (IP), ion exchange→LC-MS (IEX); polarity normalised from `MS:ION_MODE`
- **Inputs:** `/tmp/platform_classified.tsv`, `outputs/diagnostics/full_matrix_stats.tsv`, `outputs/diagnostics/mw_6696_source_presence.tsv`
- **Script:** `/tmp/make_figure7.py`; requires `squarify` (`pip install squarify`)
- **Style:** DejaVu Sans; bold titles 13pt; bold axis labels 11–12pt; bold tick labels 9–11pt; 300 DPI PNG + vector PDF
