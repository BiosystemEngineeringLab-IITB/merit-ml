# Figure 3 — Study Architecture and Source Multiplicity

**Theme A: Study Architecture & Multiplicity**
Repository-scale characterisation of how analyses are distributed across studies and how data sources are structured within and across studies. All statistics computed from `outputs/diagnostics/mw_6696_source_presence.tsv` across all 6,696 study-AN bundles and 4,121 unique studies. The three cross-attributed AN IDs (AN004586, AN007493, AN007494) each appear in two studies with different samples, features, and disease endpoints — they are retained as distinct bundles throughout.

---

## Panel A1 — Analyses-per-study distribution

**File:** `figure3_A1_analyses_per_study.pdf / .png`
**Script:** `/tmp/make_figure3a1.py`

### What it shows

A dual-axis figure combining:
- **Left y-axis (blue bars):** Histogram of the number of analyses per study, covering the full range 1–10 across all 4,120 studies.
- **Right y-axis (orange ECDF line):** Empirical cumulative distribution function (ECDF) of analyses per study, stepping from 0% to 100% as analysis count increases.

X-axis ticks are discrete integers (1–10) since analysis count is always a whole number. P50, P75, and P90 are marked with dashed vertical lines and annotated on the ECDF curve. A stats box in the top-right reports n, median, mean, P75, P90, and max.

### Key numbers

| Metric | Value |
|---|---|
| Total studies | 4,120 |
| Studies with exactly 1 analysis | 2,376 (57.7%) |
| Studies with exactly 2 analyses | 1,316 (31.9%) |
| Studies with 3 analyses | 128 (3.1%) |
| Studies with 4 analyses | 250 (6.1%) |
| Studies with 5 analyses | 24 (0.6%) |
| Studies with >5 analyses | 26 (0.6%) |
| Median analyses per study | 1 |
| Mean analyses per study | 1.62 |
| P75 | 2 |
| P90 | 3 |
| Maximum | 10 (one study) |

### Interpretation

The distribution is strongly right-skewed: **89.6% of studies have at most 2 analyses** (P50=1, P75=2), and the ECDF reaches ~90% by x=3. This means that for the vast majority of studies, the "study" and the "analysis" are effectively the same ML unit — there is no within-study multi-platform replication to exploit.

The long tail — 26 studies with more than 5 analyses — represents a qualitatively different resource. These are studies where multiple chromatographic methods (e.g., RPLC positive, RPLC negative, HILIC, GC-MS) were applied to the same cohort. For ML purposes, this subset offers:
1. **Within-study cross-assay harmonization** opportunities: the same samples measured on multiple platforms.
2. **Tier mixing**: studies in the tail are more likely to have both Tier 1 (datatable/mwTab) and Tier 2 (untarg_data) analyses present (see Panel A3).
3. **Feature redundancy testing**: the same biological metabolite may be quantified in multiple assays, enabling within-study redundancy assessment.

The 57.7% singleton-study fraction also justifies the design decision to treat the **analysis — not the study — as the atomic ML unit** in MERIT. A study-level feature matrix would require merging heterogeneous assay matrices, which is technically ill-posed when methods differ (different features, different scales, different ionisation modes). The per-analysis canonical bundle avoids this problem entirely.

---

## Panel A2 — Source richness per study

**File:** `figure3_A2A3_source_richness_tier_composition.pdf / .png` (left panel)
**Script:** `/tmp/make_figure3a2a3.py`

### What it shows

A bar chart showing how many of the three valid REST API sources (datatable, mwTab text, untarg_data) are available per study. A source is counted as present if at least one analysis within that study returned a valid tabular matrix from that source. The four categories (None / One / Two / Three sources) are plotted with progressively darker blue shading.

### Key numbers

| Source richness | Count | % of studies |
|---|---|---|
| 0 sources (no valid data) | 164 | 4.0% |
| 1 source | 814 | 19.8% |
| 2 sources | 2,935 | 71.2% |
| 3 sources (all present) | 207 | 5.0% |

### Interpretation

The dominant category — **71.2% of studies (2,935) have exactly two valid sources** — almost exclusively reflects the datatable + mwTab co-occurrence. This is structurally expected: both endpoints serve Tier 1 curated data (named metabolites, same underlying values, different orientation), so any study that successfully submitted quantitative data tends to have both. For ML purposes this is **redundant coverage**, not additive information — one source is selected by priority (datatable first) and the other discarded.

**19.8% of studies (814) have only one valid source.** This is split between:
- Studies with only mwTab (the datatable endpoint returned nothing, often because the study predates the datatable REST endpoint's broad deployment)
- Studies with only untarg_data (no curated datatable, raw peaks only)

**5.0% of studies (207) have all three sources.** These are the most data-rich studies in the repository — they have curated named metabolites (datatable/mwTab) AND raw untargeted peaks (untarg_data). Within these 207 studies, the Tier 1 and Tier 2 matrices represent the same samples at different levels of annotation completeness, making them ideal for evaluating the impact of annotation on ML performance.

**4.0% of studies (164) have no valid source at all.** These studies deposited metadata (study descriptors, factors, disease information) but no recoverable quantitative feature matrix from any of the three endpoints. They receive the "No Data" MetaboScore band and are excluded from all quantitative ML analyses.

---

## Panel A3 — Study tier composition

**File:** `figure3_A2A3_source_richness_tier_composition.pdf / .png` (right panel)
**Script:** `/tmp/make_figure3a2a3.py`

### What it shows

A grouped bar chart showing the four mutually exclusive tier-composition categories for each study:
- **Tier 1 only:** at least one valid datatable or mwTab analysis; no valid untarg_data analysis
- **Tier 2 only:** at least one valid untarg_data analysis; no valid datatable or mwTab analysis
- **Mixed (Tier 1 + 2):** at least one valid Tier 1 analysis AND at least one valid Tier 2 analysis within the same study
- **No valid source:** no valid analysis from any source

Two sets of bars are shown side by side for each category: **all 4,120 studies** (solid bars) and **multi-analysis studies only (≥2 analyses, n=1,744)** (hatched/faded bars). This split isolates whether multi-analysis studies are disproportionately represented in the mixed category.

### Key numbers

| Tier composition | All studies | % | Multi-analysis (≥2) | % of multi |
|---|---|---|---|---|
| Tier 1 only | 2,987 | 72% | 1,068 | 61% |
| Tier 2 only | 741 | 18% | 437 | 25% |
| Mixed (Tier 1 + 2) | 228 | 6% | 200 | 11% |
| No valid source | 164 | 4% | 39 | 2% |

### Interpretation

**72% of studies are Tier 1 only** — named, curated metabolite matrices with no untargeted peak counterpart. These studies are the core ML resource: features carry biochemical identities, enabling pathway analysis, RefMet mapping, and cross-study harmonisation on a shared metabolite name space.

**18% are Tier 2 only** — raw mz/RT peak tables with no curated datatable or mwTab. These studies are analytically rich (high feature count, median 2,388 features) but informationally poor for cross-study work: features cannot be matched across studies without a shared mass-RT alignment step. They are the most challenging studies for ML in terms of feature interpretability and leakage risk.

**The 228 mixed-tier studies (6%) are the highest-value harmonisation substrate in the repository.** These studies applied both a curated identification pipeline (producing a datatable) and retained the raw peak table (untarg_data) for the same samples. Within these studies it is possible to directly measure the information gain from annotation: the same biological signal is available at annotation levels 0 (raw mz/RT), 1 (putative identification), and 2 (confirmed named metabolite). Crucially, 200 of these 228 studies (87.7%) are multi-analysis studies — showing that cross-tier coverage almost always arises from multi-platform study designs, not from a single analysis being available through both endpoints simultaneously.

The shift from 6% mixed (all studies) to 11% mixed (multi-analysis studies) confirms that **study architectural complexity — more analyses per study — is the primary predictor of cross-tier data availability.** This has a direct practical implication: dataset selection strategies for cross-tier benchmarking should filter on n_analyses ≥ 2 before checking source availability.

---

## Generation

All panels were generated from:
- **Input:** `outputs/diagnostics/mw_6696_source_presence.tsv` (6,696 study-AN bundles across 4,121 studies; no deduplication applied)
- **Scripts:** `/tmp/make_figure3a1.py` (A1), `/tmp/make_figure3a2a3.py` (A2 + A3)
- **Style:** DejaVu Sans; bold titles 14pt; bold axis labels 12pt; bold tick labels 11pt; bold bar annotations 10pt
- **Output resolution:** 300 DPI PNG + vector PDF
