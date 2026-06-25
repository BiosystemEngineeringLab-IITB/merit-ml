# MERIT: A Metabolomics Data Readiness and Benchmarking Framework for Machine Learning Applications

**Shayantan Banerjee**¹

¹ *Affiliation (to be completed)*

---

## Abstract

The rapid expansion of public metabolomics repositories has created a wealth of untapped datasets for machine learning (ML) research. However, the fitness of these datasets for supervised modelling remains poorly characterized. Existing tools focus on data processing or statistical analysis but provide no systematic, repository-aware assessment of ML readiness. We present **MERIT** (MachinE learning ReadIness for Tabular metabolomics data), an open-source Python framework that ingests metabolomics studies directly from the Metabolomics Workbench, normalizes them into a canonical schema with full data provenance, and evaluates ML fitness across six scored quality dimensions: structural integrity, FAIR metadata compliance, analytical quality control, annotation interoperability, cohort bias, and ML task readiness, plus two informational dimensions (class separability and cross-study harmonization). Applying MERIT to all 4,121 publicly available Metabolomics Workbench studies, we present the first repository-scale landscape analysis of ML readiness in public metabolomics data. The composite ReadinessScore (ReadinessScore) is a weighted mean across six dimensions with empirically calibrated weights (analytical QC: 0.24, annotation: 0.17, cohort: 0.16, metadata: 0.15, ML readiness: 0.15, structural: 0.13), producing a 0–1 score classified into five bands: Ready (≥0.85), Conditional (≥0.70), Fragile (≥0.50), Not Ready (<0.50), and No Data (no feature matrix). MERIT further supports auditable remediation — label normalization, feature deduplication, and missingness filtering — enabling quantification of readiness changes before and after correction. All 28 metric scores, remediation audit logs, within-study ML benchmarks, and class separability analyses are precomputed and stored as machine-readable JSON, enabling longitudinal comparison as the repository evolves. MERIT is available as a Python package with a local browser UI, a CLI, and a JSON output format designed for computational pipelines.

---

## Introduction

### The Reproducibility Gap in Public Metabolomics

Untargeted and targeted metabolomics experiments generate high-dimensional profiles of small molecules that can serve as powerful biomarker candidates for clinical classification, disease stratification, and drug-response prediction. Over the past decade, the Metabolomics Workbench (National Metabolomics Data Repository, USA) has accumulated over 4,100 studies comprising thousands of samples and millions of quantified features. In principle, this creates an unprecedented opportunity for applying supervised machine learning to metabolomics data at scale. In practice, however, researchers consistently encounter fragmented metadata, inconsistent normalization, missing class labels, and incomplete annotation standards when attempting to reuse publicly deposited data for ML purposes.

Several tools address preprocessing and statistical analysis of metabolomics data — including XCMS, MetaboAnalyst, and mzMine — but none systematically assesses whether a deposited study is fit for ML before any model is trained. Generic tabular data quality frameworks such as AIDRIN treat metadata and missingness broadly, but lack the domain knowledge necessary to evaluate metabolomics-specific concerns: whether QC pool samples are present (indicative of analytical drift control), whether metabolite annotations carry standard database identifiers (required for cross-study harmonization), or whether the normalization state of the data is appropriate for a given ML task (raw ion counts require log transformation before distance-based models).

### Limitations of Existing Approaches

Existing quality assessment approaches for metabolomics data fall into two broad categories. First, study-level metadata checklists — such as those enforced by MetaboLights during submission — verify the presence of study descriptors but do not inspect tabular feature data or sample-level class distributions. Second, preprocessing pipelines — such as NOREVA or MetNorm — address normalization but operate within a single study and do not generate a portable readiness certificate that can be compared across datasets or used to inform dataset selection.

A third emerging approach, multi-modal ML readiness scoring, has been applied in genomics and clinical data science. The AIDRIN framework, for instance, scores readiness across bias, completeness, and technical quality dimensions for arbitrary tabular datasets. However, it treats metabolomics data as generic numerical matrices and misses critical domain context: annotation quality tiers (named metabolites vs. mz/RT pairs vs. unannotated features), assay polarity and chromatographic method comparability, RefMet chemical class distributions, and the distinction between biological and non-biological samples.

### Contribution of This Work

We introduce MERIT, a publication-first metabolomics data readiness framework that:

1. Provides a repository-native connector for the Metabolomics Workbench via three official REST API endpoints (mwTab text, datatable, and untarg_data) with local-first, content-addressed archive storage and per-analysis manifest tracking.
2. Normalizes studies into a canonical dataclass schema with cryptographic provenance hashes, enabling fully reproducible assessment.
3. Computes 28 readiness metrics across six scored quality dimensions plus two informational dimensions (class separability and cross-study harmonization), each with an interpretable 0–1 score, pass/warn/fail status, and actionable recommendations. QC/blank sample detection (16-keyword filter) ensures that non-biological samples do not contaminate class-based metrics.
4. Computes a weighted composite ReadinessScore (ReadinessScore) with empirically calibrated per-dimension weights and fixed-denominator section scoring for profile-stable comparisons.
5. Applies auditable remediations (label normalization, duplicate collapse, missingness filtering) and re-scores the remediated dataset, quantifying improvement via delta scores.
6. Executes within-study ML benchmarks (logistic regression, random forest) and class separability analysis (Fisher discriminant + cross-validated AUROC) to validate readiness scores against empirical model performance.
7. Outputs machine-readable JSON assessment reports with deterministic content hashes, enabling longitudinal comparison as the repository evolves.

---

## Results

### Repository-Scale Assessment of 4,121 Metabolomics Workbench Studies

We applied MERIT to all 4,121 publicly available studies in the Metabolomics Workbench repository, using the full assessment profile (28 metrics across 6 scored dimensions plus 2 informational dimensions). Each study was ingested from the local MW archive (`mw-dump-latest/`), normalized to the canonical schema, assessed pre- and post-remediation, benchmarked for within-study ML performance, and scored with the composite ReadinessScore. All results — bundle, canonical study, assessment reports, remediation audit logs, benchmark results, readiness_scores, and UI replay state — are stored as machine-readable JSON in the `merit-full-run-mw/` cache directory (8 JSON files per study).

<!-- PLACEHOLDER: The subsections below will be populated with actual results
     from the 4,121-study full run. The structure follows the expected results
     outlined in results-plots-ideas.md. Key numbers (medians, percentages,
     band distributions) will be filled in once the batch run completes. -->

### ReadinessScore Distribution Across the Repository

**[Figure 1 — to be populated]** The composite ReadinessScore distribution across all 4,121 MW studies, classified into five bands: Ready (≥0.85), Conditional (≥0.70), Fragile (≥0.50), Not Ready (<0.50), and No Data (no parseable feature matrix). Expected: a right-skewed distribution with the majority of studies in the Conditional and Fragile bands.

### Per-Dimension Score Distributions Reveal Systematic Bottlenecks

**[Figure 2 — to be populated]** Violin/ridge plots of per-dimension scores (structural, metadata, analytical, annotation, cohort, ML readiness) across all studies with feature data. This analysis identifies which quality dimension is the universal bottleneck for ML readiness.

### QC/Blank Sample Prevalence

QC and blank sample detection uses a 16-keyword filter applied to the concatenation of sample_id, label, and sample_type fields. QC/blank samples are excluded from all class-based metrics (class balance, confounding risk, label suitability, stratified split feasibility, feature-to-sample ratio, factor label harmonization, disease endpoint extractability) and from sample-level missingness assessment to prevent non-biological samples from contaminating study quality scores.

**[To be populated: percentage of MW studies with detected QC/blank samples, distribution of QC sample counts]**

### Normalization Status Landscape

The normalization status heuristic is now intentionally binary: each analysis is inferred as either raw (score 0.0) or likely_transformed (score 1.0) from min/median/p90/max over biological samples (QC/blank excluded). Raw is called only when strong raw-like signatures are present (high median, high p90, very high max). All other numeric scales are grouped as likely_transformed. Near-zero-variance and low-signal features are reported as diagnostics but do not affect the score. Declared units/value scale from mwTab JSON are displayed separately and not used directly in scoring.

**[To be populated: fraction of studies in each normalization tier, implications for preprocessing recommendations]**

### Annotation Quality Tiers

Feature annotation is classified into three tiers: named_metabolites (≥70% named, score 1.0), mixed_mz_rt (some named, score 0.5), and mostly_unannotated (score 0.2). Feature redundancy is assessed within-assay only — the same metabolite appearing across different assays (e.g., positive/negative mode) is not penalized.

**[To be populated: distribution of annotation tiers across MW, correlation with analytical platform type]**

### Cohort and Class Structure

Class balance is computed as min/max class count ratio after QC/blank exclusion. Confounding risk uses Cramer's V between class labels and sample-type markers, with QC samples excluded from the contingency table. Age and biological sex metadata coverage receives a neutral score (0.5) when both are completely absent — a repository infrastructure gap rather than a study design flaw — and scores below 0.5 only when partial demographic data indicates incomplete collection.

**[To be populated: class balance distribution, fraction of studies with confounding risk, demographic metadata availability rates]**

### ML Task Readiness and Feature-to-Sample Ratio

Feature-to-sample ratio is computed per-matrix with sample-weighted aggregation across matrices, using biological sample counts (QC excluded). Scoring tiers: ≤10 (1.0), ≤50 (0.8), ≤200 (0.5), >200 (max(0.1, 1.0 - ratio/1000)). Multi-class studies with 3–10 classes receive the same score (1.0) as binary studies; only >20 classes (typically a label-parsing artifact) are penalized.

**[To be populated: F:S ratio distribution, recommended ML task type breakdown, stratified split feasibility rates]**

### ReadinessScore vs. Empirical ML Performance

**[Figure — to be populated]** Scatter plot of ReadinessScore vs. within-study cross-validated AUROC (from ClassSeparabilityMetric, full profile). This validates whether the composite readiness score is predictive of actual ML performance.

### Temporal Trends in Data Quality

**[Figure — to be populated]** ReadinessScore vs. study submission date, showing whether MW data quality has improved over time.

### Readiness Band Distribution and Score Confidence

Score confidence reflects how trustworthy the composite score is, based on: (1) number of dimensions with meaningful signal (not at neutral 0.5 defaults), (2) biological sample count, (3) metadata and analytical section strength. Estimated ML difficulty is assessed independently using 6 factors: cohort size, class balance, F:S ratio, missingness, annotation quality, and class cardinality.

**[To be populated: band distribution pie chart, confidence level distribution, difficulty level distribution]**

### Remediation Impact at Scale

MERIT's remediation module applies three auditable transformations: (1) label normalization — converting raw factor strings to compact snake_case identifiers; (2) feature deduplication — collapsing repeated within-assay feature names to their first occurrence; (3) high-missingness feature dropping — removing features exceeding a configurable threshold (default 20%). Each transformation is logged in the audit trail.

**[To be populated: pre/post remediation ReadinessScore deltas, fraction of studies where remediation improves band classification]**

---

## Methods

### System Architecture

MERIT is implemented in Python ≥ 3.10 and organized as a pip-installable package (`pip install -e .`). The core pipeline is sequential: (1) `create_bundle()` — fetch and hash-index all repository files into a deterministic JSON bundle; (2) `normalize_bundle()` — map to the `CanonicalStudy` dataclass schema; (3) `assess_study()` — run all `MetricPlugin` instances, returning an `AssessmentReport`; (4) `remediate_study()` — apply auditable transformations; (5) `assess_study()` again — produce post-remediation assessment; (6) `compute_readiness_score()` — compute the weighted composite ReadinessScore; (7) `render_markdown()` / `render_html()` — generate the output report. The full pipeline is orchestrated by `workflow.py:run_guided_workflow()`, which is also the entry point for the local browser UI. Batch execution across all MW studies uses `mw_full_run.py:run_mw_full_cache()`, which discovers all ST* directories, runs the guided workflow for each, and caches all JSON artifacts in a centralized output directory.

### Data Ingestion Pipeline

#### Study and Analysis Discovery

Study identifiers were extracted using the Metabolomics Workbench public REST API summary endpoint (`/rest/study/study_id/ST/summary`), yielding **4,121 studies** comprising **6,696 analyses** in total (as of 9 March 2026). For each study, the pipeline queried the analysis endpoint (`/rest/study/study_id/{ST}/analysis`) to identify all associated analytical runs, each assigned a unique identifier with the prefix AN.

#### Multi-Source Tabular Data Retrieval

Tabular metabolite abundance matrices were retrieved for each analysis using three distinct REST API endpoints, probed **independently and unconditionally** for every analysis regardless of whether any other source returned valid data. `_Results.txt` files were explicitly excluded from the pipeline; only the three official REST API sources described below are used.

**Source 1 — mwTab text file** (`/rest/study/analysis_id/{AN}/mwtab/txt`): A tab-delimited format containing structured experimental metadata and quantitative matrices delimited by block markers: `MS_METABOLITE_DATA_START/END`, `NMR_METABOLITE_DATA_START/END`, `NMR_BINNED_DATA_START/END`, `EXTENDED_MS_METABOLITE_DATA_START/END`, and `METABOLITE_DATA_START/END`. The mwTab file is fetched unconditionally for every analysis and serves dual purpose: as a source of the quantitative matrix (Priority 2) and as the exclusive source of structured metadata regardless of which source provides the matrix.

**Source 2 — Datatable REST API** (`/rest/study/analysis_id/{AN}/datatable/file`): Returns a curated tab-delimited abundance matrix in samples × features orientation, with a `Class` column encoding group labels. Responses may be gzip-compressed or plain text; both are decoded transparently. This endpoint returns **Tier 1 data**: named, curated, identified metabolites.

**Source 3 — Untargeted data REST API** (`/rest/study/analysis_id/{AN}/untarg_data/`): Returns raw pre-identification feature matrices with features encoded as mass-to-charge ratio and retention time tokens (mz_RT format, e.g. `70.065_2.75`). This endpoint returns **Tier 2 data**: unidentified peaks from untargeted analytical workflows, structurally disjoint from Tier 1 features. The use of this endpoint was confirmed via direct communication with the Metabolomics Workbench team (E. Fahy, personal communication, March 2026).

All responses were stored as plain UTF-8 text files under a per-analysis directory structure (`{ST}/{AN}/tabular/` for matrix files, `{ST}/{AN}/json/` for mwTab). Study-level metadata was additionally retrieved from three per-study endpoints: `/rest/study/study_id/{ST}/factors`, `/rest/study/study_id/{ST}/disease`, and `/rest/study/study_id/{ST}/metabolites`.

#### Validation Criteria

A response was classified as containing valid tabular data only if it satisfied the following criteria simultaneously, applied to the full matrix (no row or column subsampling):

For **TSV sources** (datatable and untarg_data):
- The header row contained more than two tab-separated columns
- At least one non-header data row was present
- That row contained at least one numerically parseable value in columns 2 through N

For **mwTab text files**:
- The file contained at least one recognised data block (as defined by the block markers above)
- Inside that block, at least one non-header, non-empty row contained a numeric value in columns 2 through N

In both cases, rows whose first column matched known metadata labels (`Samples`, `Sample`, `Factors`, `Factor`, `Class`, `Classes`, `Group`, `Groups`) were excluded from data row counting. A value was considered non-numeric if it matched any token in the following set (case-insensitive): `NA`, `NaN`, `null`, `none`, `n/a`, `#N/A`, `missing`, `unknown`, `.`, `-`, `ND`, `N.D.` (not detected), `LOD`, `<LOD`, `BLOD` (below limit of detection), `LOQ`, `<LOQ`, `BLOQ` (below limit of quantification), `BDL`, `BQL`, `inf`, `-inf`, `#VALUE!`, `#REF!`, `#DIV/0!`. Missingness was computed over the full matrix using these tokens.

#### Source Availability Across the Repository

Strict content validation was applied across all 6,696 study-AN bundles. Three AN IDs (AN004586, AN007493, AN007494) each appear under two different study IDs in the Metabolomics Workbench metadata; however, inspection confirmed that these pairs carry distinct study contexts — different disease endpoints, sample metadata, and in the case of AN007493 and AN007494, different untarg_data availability across their two parent studies (present in ST004470, absent in ST004471). They are therefore retained as 6 distinct study-AN bundles throughout. All 6,696 bundles had directories on disk and were fully processed. Results are summarised in Figure 2A:

| Source | Valid | Invalid (file present, fails validation) | Missing (no file) |
|---|---|---|---|
| mwTab text | 5,004 (74.7%) | 1,680 (25.1%) | 9 (0.1%) — AN002312, AN005082, AN005098, AN005099, AN005557, AN006051, AN006148, AN006279, AN006593 |
| datatable | 4,867 (72.7%) | 2 (< 0.1%) | 1,824 (27.2%) |
| untarg_data | 1,860 (27.8%) | 0 | 4,833 (72.2%) — includes 4,498 analyses never probed by the untarg endpoint |

**Key observations:** (1) Nearly all datatable files that were present passed validation (only 2 failed), confirming that the endpoint reliably returns well-formed matrices. (2) All untarg_data files that were successfully retrieved passed validation — 0 failures among 1,860 downloads. (3) Of the 1,860 valid untarg_data analyses, 1,434 (77.1%) had no valid datatable matrix, making untarg_data the sole quantitative source for those analyses. (4) The high co-occurrence between datatable and mwTab (4,471 analyses with both valid, representing 91.8% of datatable-valid analyses) confirms that these sources carry largely redundant Tier 1 content.

The untarg_data endpoint was probed for 2,198 analyses total (1,860 completed, 338 failed: 333 `no_response`, 5 `empty_or_invalid_tsv`). The remaining 4,498 analyses were not probed as they were identified as targeted studies via datatable availability. The UpSet plot of strict-valid source combinations (Figure 2B) shows that the dominant intersection is datatable ∩ mwTab (4,471 analyses), followed by untarg_data-only (1,434), all-three (383), and mwTab-only (110).

#### Priority Selection and Canonical Study Bundle

When multiple sources returned valid matrices for the same analysis, a single source was selected by strict priority: **datatable (Priority 1) > mwTab embedded matrix (Priority 2) > untarg_data (Priority 3)**. Sources were never merged. This priority reflects data curation quality: datatable matrices contain curated, named metabolites with class labels; mwTab matrices contain the same Tier 1 data in transposed format; untarg_data matrices contain raw mz/RT tokens incompatible with Tier 1 features. Among the 383 analyses for which all three sources returned valid data, 341 (89.0%) showed zero column-name overlap between datatable and untarg_data features (Jaccard similarity J=0), confirming that the two tiers are structurally disjoint. Of the remaining 42 analyses (11.0%), 31 showed low partial overlap (J<0.1) driven by partially annotated untarg_data files where a minority of peaks carried named metabolite identifiers, and 11 showed high overlap (J≥0.1) because their untarg_data endpoint returned fully named metabolite content misrouted through the untarg endpoint rather than raw mz/RT peak tables.

The outputs of the ingestion pipeline were consolidated into a **Canonical Study Bundle** per analysis — a structured, self-contained representation comprising: (i) the quantitative feature matrix from the highest-priority valid source, (ii) harmonised metadata extracted from the mwTab text file covering experimental design, instrument parameters, sample factors, and metabolite annotations, and (iii) a provenance record documenting the source endpoint selected, validation status of each probed source, retrieval timestamp, and file paths. The per-analysis `manifest.json` records three boolean validity flags (`mwtab_txt_has_tabular`, `datatable_has_tabular`, `untarg_has_tabular`) and the selected `tabular_source_used` field.

#### Feature Type Classification

Feature column names were classified as mz/RT tokens or named metabolites using the following rules. A column name was classified as a **mz/RT token** if it matched the regular expression `^\d+(\.\d+)?_\d+(\.\d+)?$` exactly after whitespace stripping (e.g., `70.065_2.75`, `182.0790_4.19`). All remaining names — including those carrying adduct suffixes (`[M+H]+`, `[M-H]-`, `[M+NH4]+`), lipid shorthand notation (LIPID MAPS format: `CE(16:0)`, `PC(36:2)`), or isotopologue labels (`M0`, `M+1`, `M+2`) — were classified as **named metabolites**. In 4,867 valid datatable analyses, 0% of features matched the mz/RT pattern (median; all named). In 5,004 valid mwTab analyses, 0% matched at the median (mean 1.5%), with the small mean driven by a minority of mwTab files containing NMR binned spectra encoded as frequency_ppm tokens. In 1,860 valid untarg_data analyses, the median was 100% mz/RT (mean 86.0%); the 14% deviation from full mz/RT reflects partially annotated untargeted datasets where putative metabolite names were substituted for peak identifiers prior to deposition.

#### Repository-Scale Tabular Data Characteristics

Full-matrix statistics were computed across all valid analyses using the complete feature matrix (no row or column subsampling) via parallel execution across 28 CPU workers (`ProcessPoolExecutor`). All 20,088 analysis-source records (6,696 study-AN bundles × 3 sources) were processed and stored in `outputs/diagnostics/full_matrix_stats.tsv`. Results are summarised in Table 1 and Figures 2C–2D.

| Metric | datatable (n = 4,867) | mwTab (n = 5,004) | untarg_data (n = 1,860) |
|---|---|---|---|
| Feature count (median) | 93 | 103 | 2,388 |
| Feature count (mean ± range) | 195 (1–9,007) | 343 (1–44,005) | 6,920 (1–615,898) |
| Sample count (median) | 36 | 32 | 40 |
| Sample count (mean ± range) | 88 (1–3,501) | 77 (1–3,501) | 140 (2–13,554) |
| p/n ratio (median) | 2.20 | 2.76 | 49.40 |
| % analyses with p/n > 1 | 65.8% | 69.5% | 96.5% |
| % mz/RT features (median) | 0% | 0% | 100% |
| log₁₀(median intensity) (median) | 4.26 | 4.31 | 4.57 |
| Missing values % (median) | 0% | 0% | 4.17% |
| Missing values % (mean) | 0.01% | 6.77% | 16.01% |

**Feature count:** datatable analyses had a median of 93 features (mean 195, range 1–9,007), consistent with curated targeted panels. mwTab analyses yielded a comparable median of 103 features (mean 343), with a wider range reaching 44,005 due to large NMR binned datasets. untarg_data analyses had a median of 2,388 features (mean 6,920, range 1–615,898), reflecting the high-dimensional nature of pre-identification peak tables — approximately 26× more features than datatable at the median.

**Sample count:** Median sample count was similar across sources (datatable: 36, mwTab: 32, untarg_data: 40). However, for analyses where both datatable and untarg_data were valid, sample counts did not match in 38% of cases (mean difference: −22 samples, untarg minus datatable). Inspection revealed two causes: (1) datatable includes technical replicates at individual injection level while untarg_data aggregates to biological sample level (e.g., AN000665: 222 vs 28 samples); (2) the two sources represent different sample subsets for the same analysis (e.g., AN001776: 846 biological samples in datatable vs 423 reference/QC samples in untarg_data). This confirms that even when both sources are valid, they are not interchangeable.

**Feature-to-sample (p/n) ratio:** The median p/n ratio was 2.20 for datatable and 2.76 for mwTab — well-posed for ML — versus 49.40 for untarg_data. 96.5% of untarg_data analyses exceeded the conventional high-dimensionality threshold of p/n = 1, compared with 65.8% and 69.5% for datatable and mwTab respectively. This poses a fundamental challenge for supervised ML on untarg_data without prior feature selection or dimensionality reduction.

**Data scale:** The median log₁₀ of median feature intensity was 4.26 for datatable (≈18,200 AU), 4.31 for mwTab (≈20,400 AU), and 4.57 for untarg_data (≈37,200 AU). All three sources exhibited wide value distributions spanning 7–13 orders of magnitude across the corpus, reflecting heterogeneous normalization practices. This rules out any assumption of pre-normalization for any source.

**Missingness:** Missing value rates diverged substantially across sources, reflecting fundamental differences in how each endpoint handles measurements below detection. The datatable source is structurally complete by design: all valid analyses showed 0% missingness. To determine whether this completeness reflects genuine measurement coverage or systematic zero-fill imputation, we performed a cell-by-cell, orientation-aware match between paired mwTab and datatable matrices across the strict 4,464-cohort (mwtab_valid=1, datatable_valid=1, untarg_valid=0). Each mwTab token cell was matched by exact (study_id, analysis_id, sample_id, feature_name) to the corresponding datatable cell. Of 1,148,914 explicit mwTab missing-token cells (NA, nd, null, bloq, etc.), 703,551 (61.24%) mapped to datatable zero, 248,822 (21.66%) mapped to a nonzero numeric value, and 196,541 (17.11%) were explained by feature dropping — features with 100% missingness in mwTab that were systematically removed from datatable before deposition. Among retained matched cells (excluding dropped features), 73.87% of explicit mwTab missing tokens were replaced with zero in datatable, with no evidence of statistical imputation strategies such as mean, median, or KNN substitution. Manual inspection of representative analyses confirmed this pattern: in AN004987 (ST003040), 1,039 features present in the mwTab block (85% missingness overall) were absent from the datatable; every dropped feature had 100% missingness in mwTab, including undetected lipid species such as BMP 36:9 and CDPDAG 22:7.

Based on this evidence, MERIT applies source-aware zero handling: datatable zeros are treated as valid curated fill values (not missing), while mwTab and untarg_data zeros are treated as missing (below detection). All explicit nonnumeric missing tokens (NA, nd, bdl, bloq, <LOD, etc.) are treated as missing regardless of source. This policy ensures that missingness scores are consistent with the curation state of each source: datatable reflects post-imputation completeness, while mwTab and untarg_data preserve the pre-imputation state.

Further, mwTab analyses, which preserve the pre-imputation state, showed a median missingness of 2% (mean 11.5%); 67% of analyses carried at least one missing value, and 353 analyses (7.1%) exceeded 50% missingness. untarg_data analyses were the most affected: median 4.5%, mean 16.4%, with 78% of analyses carrying some missingness and 230 analyses (12.2%) exceeding 50%. Despite the higher mean, the untarg_data distribution is strongly right-skewed, with the majority of analyses clustering below 20%, with a sparse tail of severely affected studies (57 analyses, 3.0%, exceeding 80% missingness).

#### Repository Connector and Factor Label Selection

The **MetabolomicsWorkbenchConnector** implements the `RepositoryConnector` abstract base class. Study metadata is parsed from the study JSON produced by the Metabolomics Workbench REST API. The connector implements a coverage-based factor label selection rule: rather than hardcoding a factor variable name, it scans all per-sample factor strings (`Key:Value` pipe-delimited pairs from `factors.json`), counts non-empty values per factor key, and selects the key with the highest coverage as the class label source. Biologically meaningful keys (`Group`, `Diagnosis`, `Disease`, `Condition`, `Phenotype`, `Class`, `Status`) are preferred over demographic/technical keys (`Age`, `Sex`, `BMI`, `Batch`), and the selected key must produce ≥2 distinct non-unknown values. Study-level disease is extracted exclusively from `disease.json` (the MW REST disease endpoint), not from mwTab metadata keys, to ensure source-of-truth consistency across the corpus.

### Canonical Data Model

The `CanonicalStudy` dataclass composes: a `StudyRecord` (study-level descriptors), a list of `SampleRecord` (per-sample ID, label, sample type, organism, structured attributes), a list of `AssayRecord` (per-analysis platform/polarity/chromatography metadata), a list of `FeatureMatrix` (samples × features abundance matrices with parallel feature ID and sample ID lists), a list of `MetaboliteAnnotationRecord` (per-feature identifier and name mappings), and a `ProvenanceRecord` (content hash, source paths, fetched-at timestamp). All canonical study objects are serialized to JSON and indexed by content hash.

### Metric Architecture

Metrics are implemented as `MetricPlugin` subclasses, each with a `family` attribute mapping to one of 8 quality dimensions and a `compute(study: CanonicalStudy) -> MetricResult` method. All metrics return a score in [0, 1], a status (pass / warn / fail), a plain-language summary, a structured `details` dict, and a list of recommendations. 28 metrics are registered in `metrics/__init__.py:DEFAULT_METRICS` and filtered by assessment profile (`core` or `full`) at runtime. The `full` profile adds three computationally expensive analytical metrics (outlier detection, feature correlation, feature-level missingness) and the class separability metric.

**QC/blank sample filtering.** A 16-keyword filter (`_QC_BLANK_KEYWORDS`: qc, blank, pool, nist, reference, solvent, quality control, pooled qc, ltr, sst, calibration standard, system suitability, process blank, method blank, reagent blank, drift) is applied as a case-insensitive substring match against the concatenation of sample_id, label, and sample_type. The `_is_biological_sample()` helper excludes matched samples from all 12 class-based and sample-level metrics (ClassBalance, ConfoundingRisk, AgeBiologicalSexMetadata, SampleMatrixHomogeneity, LabelSuitability, RecommendedMLTask, FeatureToSampleRatio, StratifiedSplitFeasibility, MinimumSampleThreshold, FactorLabelHarmonization, DiseaseEndpoint, MissingnessMetric). NormalizationStatusMetric also excludes QC/blank samples to ensure that value-scale classification reflects biological data only.

**Structural metrics (5):** schema_integrity (5-check Boolean verification; score < 0.6 → "fail"); completeness (50/50 weighting between 6 study-level fields and 3 per-sample fields); duplicate_entities; tabular_data_availability; minimum_sample_threshold (graduated: ≥30 → 1.0, 20–29 → 0.7, 10–19 → 0.4, <10 → 0.1; biological samples only).

**Metadata / FAIR metrics (5):** fair_study_metadata_compliance (5-check FAIR checklist: identifier pattern, distribution manifest, file hashes, parser provenance, source root); fair_metabolite_identifier_resolvability (composite: 0.45×id_coverage + 0.20×uri_coverage + 0.20×consistency + 0.15×provenance_coverage; uses explicit RefMet evidence from `metabolites.json` only for MW studies); disease_endpoint_extractability (requires ≥2 groups across ≥80% of biological samples for score = 1.0; QC excluded); factor_label_harmonizability (0.6×label_quality + 0.4×compactness; QC excluded); factor_variable_richness (≥3 types → 1.0, 2 → 0.7, 1 → 0.4, 0 → 0.0; connector-internal keys excluded from count).

**Analytical QC metrics (5 core, 8 full):** qc_blank_presence (informational; not included in readiness score); missingness_structure (sample-level: for each biological sample, compute the fraction of features that are missing; per-analysis score = 1 − median of per-sample missingness rates; aggregate = mean of per-analysis scores; QC/blank/pool/reference samples excluded; source-aware zero handling applied; class-dependent missingness gap reported as a separate diagnostic warning, not blended into the score); normalization_status (binary inferred scale classifier; score = 0.0 when raw-like signatures are present, else 1.0 as likely_transformed; status pass only if aggregate score > 0.5; QC/blank samples excluded; declared units/value scale from mwTab JSON reported separately; NZV and low-signal features reported as diagnostics, not scored); batch_info_availability (informational; not included in readiness score); assay_platform_comparability; feature_correlation_burden (full only); outlier_burden (full only); feature_level_missingness (full only; feature-level: score = 1 − mean of per-feature missingness rates; status = warn if ≥10% of features exceed 30% missingness; source-aware zero handling applied).

The two missingness metrics use different summary statistics by design. The sample-level metric uses the **median** because each sample is a training example; a few outlier samples (QC failures, processing errors) are routinely dropped and should not distort the readiness score — the median represents the typical sample's completeness. The feature-level metric uses the **mean** because every missing feature cell is a modelling gap that cannot be recovered by dropping a single observation; the mean captures the total missing burden across all features, whereas a median would mask a long tail of badly incomplete features behind a healthy majority.

**Annotation / Interoperability metrics (4):** feature_annotation_type (named ≥70% → 1.0, mixed → 0.5, mostly_unannotated → 0.2); annotation_ambiguity_burden; unknown_feature_fraction; feature_redundancy (within-assay only — same metabolite across different assays is not penalized).

**Cohort / Bias metrics (4):** class_balance (min/max ratio; QC excluded; single-class → 0.25); sample_type_confounding_risk (Cramer's V; QC excluded; single marker → 1.0); age_biological_sex_metadata (neutral 0.5 when both completely absent — repository gap, not study flaw; <0.5 reserved for partial data); sample_matrix_homogeneity.

**ML Task Readiness metrics (5):** label_suitability (all classes ≥5 samples; QC excluded); recommended_ml_task (binary → 1.0, 3–10 classes → 1.0, 11–20 → 0.7, >20 → 0.4; QC excluded); feature_to_sample_ratio (per-matrix computation with sample-weighted aggregation; ≤10 → 1.0, ≤50 → 0.8, ≤200 → 0.5, >200 → max(0.1, 1−ratio/1000)); stratified_split_feasibility; leakage_risk.

**Class Separability (1, full profile only):** class_separability (0.4×Fisher_score + 0.6×predictive_separability; Fisher from tr(S_B)/tr(S_W+λI); predictive from repeated holdout logistic regression AUROC; preprocessing: QC exclusion, median imputation, z-scoring, top-2000 variance features).

**Cross-Study Harmonization (2, informational):** harmonization_feasibility; pathway_mappability_proxy. These are not included in the composite score.

### ReadinessScore Computation

The composite ReadinessScore (ReadinessScore) is a weighted arithmetic mean over six dimension scores, each computed as `sum(metric_scores) / fixed_count` where `fixed_count` is the full-profile metric count for that dimension (ensuring profile-stable comparisons):

| Dimension | Weight | Fixed count |
|-----------|--------|-------------|
| Analytical QC | 0.24 | 8 |
| Annotation | 0.17 | 4 |
| Cohort / Bias | 0.16 | 4 |
| Metadata / FAIR | 0.15 | 5 |
| ML Readiness | 0.15 | 5 |
| Structural | 0.13 | 5 |

Analytical QC receives the highest weight because analytical quality is the primary differentiator for ML fitness. Score bands: Ready (≥0.85), Conditional (≥0.70), Fragile (≥0.50), Not Ready (<0.50), No Data (TabularDataAvailability = 0.0). The "No Data" band is forced when a study has metadata but no parseable feature matrix, preventing misleadingly moderate scores from structural/metadata checks alone.

Two UI-level indicators supplement the composite score: **Score Confidence** (Low/Moderate/High, based on number of informative dimensions, sample size, and metadata/analytical strength) and **Estimated ML Difficulty** (Easy/Moderate/Hard, based on cohort size, class balance, F:S ratio, missingness, annotation quality, and class cardinality).

### Remediation

The remediation module operates on a deep copy of `CanonicalStudy`. Three actions are supported: `normalize_labels` (convert class labels to snake_case, strip whitespace, collapse variants); `deduplicate_features` (retain only the first occurrence of each normalized feature name per matrix); `drop_high_missing_features` (remove features with missing fraction exceeding a configurable threshold). Each action is recorded in an audit log with before/after counts. The audit log is embedded in the `AssessmentReport.remediations_applied` list and written to the output JSON.

### Benchmarking

Within-study benchmarks use stratified k-fold cross-validation (k = 5) with logistic regression (L2 regularization, C = 1.0) and random forest (100 estimators) classifiers from scikit-learn. Evaluation metrics are AUROC, AUPRC, Brier score, and calibration error. The benchmark is skipped if fewer than 2 distinct classes are present, if any class has fewer than 5 samples, or if no feature matrix passes the tabular data availability check.

### Local Archive, Batch Execution, and CLI

Metabolomics Workbench studies are stored in a local archive organized as `mw-dump-latest-confirmation/{ST}/{AN}/tabular/` (for `_datatable.tsv` and `_untarg_data.tsv`) and `{ST}/{AN}/json/` (for `_mwtab.txt`), with per-study `manifest.json`, `disease.json`, `factors.json`, and `metabolites.json` alongside. Each `manifest.json` records per-analysis validity flags (`mwtab_txt_has_tabular`, `datatable_has_tabular`, `untarg_has_tabular`), file paths, and `tabular_source_used`. This enables fully offline operation and deterministic ingestion without live API calls.

Batch execution across all 4,121 MW studies uses `run_mw_full_cache()`, which discovers all ST* directories, runs the guided workflow for each study, and stores 8 JSON artifacts per study (bundle, canonical, assessment, remediated canonical, remediated assessment, benchmark, readiness_score, workflow_state) in a centralized cache. Per-study status (success/failure/duration) is logged in TSV format; failures are captured with detailed tracebacks. The workflow state JSON enables full UI replay of any study without re-computation.

The CLI provides subcommands spanning the full pipeline: `ingest`, `normalize`, `assess`, `remediate`, `benchmark`, `report`, `ui`, and `mw full-run` for batch execution.

---

## Discussion

### Repository-Scale Readiness Assessment Reveals Systematic Quality Patterns

<!-- PLACEHOLDER: To be populated with actual findings from the 4,121-study run.
     Expected discussion themes based on scoring design: -->

The repository-scale analysis of all 4,121 Metabolomics Workbench studies reveals systematic patterns that are invisible at the individual study level. The ReadinessScore distribution, combined with per-dimension breakdowns, enables identification of universal bottlenecks and actionable improvement targets for the metabolomics community.

**Analytical QC as the primary differentiator.** The analytical QC dimension carries the highest weight (0.24) in the composite score because it most directly affects ML model validity. The normalization status heuristic is intentionally coarse but operational: studies with strong raw-like signatures (high median, p90, and max) are flagged as raw and should be transformed/normalized before modeling; all other numeric scales are treated as likely_transformed. This simplification improves interpretability and reduces threshold overfitting while still separating clearly raw assays from model-ready scales.

**QC sample contamination as a methodological concern.** A key design decision was the systematic exclusion of QC/blank samples from all class-based metrics. Without this filter, QC samples with labels like "QC" or "pooled_qc" contaminate class balance calculations, inflate Cramer's V contingency tables, and distort feature-to-sample ratios. The 16-keyword filter was validated on studies with known QC samples (e.g., ST002024: 56 QC/blank samples correctly identified out of 146 total).

**Neutral scoring for repository infrastructure gaps.** Several metrics assign neutral scores (0.5) when data is absent due to repository limitations rather than study design flaws: age/biological sex metadata (MW does not uniformly expose demographic fields), batch information (MW does not standardize batch identifiers). This prevents repository-level gaps from dominating the composite score and unfairly penalizing individual studies.

**Within-assay vs. cross-assay distinction.** Feature redundancy is assessed within-assay only, reflecting the fact that multi-mode LC-MS studies routinely detect the same compounds in positive and negative ionization mode. Penalizing this cross-assay overlap would incorrectly flag well-designed multi-modal studies. Similarly, feature-to-sample ratio is computed per-matrix with sample-weighted aggregation, avoiding the misleading inflation that occurs when features are summed across assays.

### Limitations

MERIT has several limitations. The cross-study harmonization metrics (harmonization_feasibility, pathway_mappability_proxy) are proxy-based, relying on annotation coverage rather than actual feature overlap. The normalization status heuristic is a value-distribution-based classifier that cannot distinguish between intentionally normalized data and data that happens to fall within normalized value ranges. Batch effect correction is beyond the remediation module's scope, limited by the systematic absence of batch metadata from MW deposits. The `BiologicalSexDistributionMetric` is implemented but not registered in the scored metric set because sex distribution data is rarely available in MW. The `_QC_BLANK_KEYWORDS` list is duplicated across 5 source files; future refactoring should extract it to a shared module.

### Future Directions

Phase 2 will validate the ReadinessScore weights against empirical ML performance using the ReadinessScore-vs-AUROC correlation from the class separability analysis. Phase 3 will add batch effect correction as an auditable remediation step. Harmonization metrics will be computed against a reference feature vocabulary built from the RefMet ontology, enabling quantitative cross-study overlap scoring. Extension to MetaboLights (connector exists but is currently disabled) will enable cross-repository comparison of ML readiness patterns.

---

## Figure Captions

<!-- PLACEHOLDER: Figure numbers and details to be finalized after 4,121-study
     results are available. See results-plots-ideas.md for the full figure plan. -->

**Figure 1. ReadinessScore distribution across all 4,121 Metabolomics Workbench studies.** (A) Histogram of composite ReadinessScore (0–1), color-filled by readiness band (Ready ≥0.85, Conditional ≥0.70, Fragile ≥0.50, Not Ready <0.50, No Data). Annotated with median, IQR, and percentage in each band. (B) Per-dimension score distributions as violin/ridge plots for the six scored dimensions (structural, metadata, analytical, annotation, cohort, ML readiness), identifying the universal bottleneck dimension.

**Figure 2. Repository-scale data ingestion and tabular data characteristics across 6,696 Metabolomics Workbench analyses.** **(A)** Stacked horizontal bar chart showing the strict per-source availability of the three official REST API endpoints across all 6,696 analyses: mwTab text (5,004 valid / 1,680 invalid / 9 missing), datatable (4,867 valid / 2 invalid / 1,824 missing), and untarg_data (1,860 valid / 0 invalid / 4,833 missing). **(B)** UpSet plot of strict-valid source co-occurrence across all 6,696 analyses. The dominant intersection is datatable ∩ mwTab (4,471 analyses); the largest exclusive set is untarg_data-only (1,434 analyses), representing targeted pre-identification data with no Tier 1 counterpart; 244 analyses have no valid source. **(C)** Six-panel violin-plus-boxplot figure showing the distribution of feature count, sample count, feature-to-sample ratio, proportion of mz/RT features, data scale (log₁₀ median intensity), and missing value rate across all valid analyses stratified by source (blue = datatable, green = mwTab, orange = untarg_data). Violin bodies show the full distribution; embedded boxes show median, IQR, and Tukey whiskers. Computed on full matrices with no subsampling (datatable: n = 4,867; mwTab: n = 5,004; untarg_data: n = 1,860). **(D)** ML-readiness summary. Left: log–log scatter of sample count (n) vs feature count (p) for all valid analyses; dashed lines mark p = n and p = 10n. Right: histogram of the feature-to-sample ratio (p/n) on a log scale; vertical lines at p/n = 1 and p/n = 10. 96.5% of untarg_data analyses have p/n > 1, compared with 65.8% (datatable) and 69.5% (mwTab).

**Figure 3. Annotation quality tiers across the repository.** Stacked bar showing the fraction of features classified as Named Metabolite (green), mz/RT pair (amber), or Unannotated (red) across all studies with feature data, grouped by analytical platform (LC-MS, GC-MS, NMR).

**Figure 4. ReadinessScore vs. empirical ML performance.** Scatter plot of ReadinessScore vs. within-study cross-validated AUROC (from ClassSeparabilityMetric), with point size proportional to biological sample count and color by readiness band. Tests whether the composite readiness score is predictive of actual ML separability.

**Figure 5. Temporal trends in data quality.** ReadinessScore vs. study submission date, showing temporal evolution of deposited data quality. Binned by year with trend line and confidence interval.

**Figure 6. Effect of remediation on readiness scores.** Distribution of pre-vs-post remediation ReadinessScore deltas across all studies where remediation applies. Shows which dimension benefits most from automated remediation.

**Figure 7. Score confidence and ML difficulty landscape.** Two-panel figure: (left) distribution of Score Confidence levels (Low/Moderate/High) across all studies; (right) distribution of Estimated ML Difficulty levels (Easy/Moderate/Hard/Unknown). Cross-tabulated with readiness bands.

---

## Supplementary

### Supplementary Table 1. Full metric scores for all evaluated studies

A machine-readable table of all 28 metric scores, statuses, and summaries for each of the 4,121 evaluated studies is available as assessment JSON files in `merit-full-run-mw/json/`. Canonical JSON bundles with content hashes are provided to enable exact reproduction of all reported scores.

### Supplementary Table 2. ReadinessScore weights and fixed-denominator counts

Complete specification of dimension weights (structural: 0.13, metadata: 0.15, analytical: 0.24, annotation: 0.17, cohort: 0.16, ML: 0.15), fixed-denominator counts per section (5, 5, 8, 4, 4, 5), readiness band thresholds, and the No Data override rule.

### Supplementary Table 3. QC/blank detection keywords and validation

The 16-keyword filter used for QC/blank sample detection, with validation results on studies with known QC samples (ST002024: 56/146, ST000081: 6/140, ST003246: 0 — clean).

### Supplementary Note 1. Tabular data format handling and edge cases

MERIT ingests tabular data from three REST API sources only; `_Results.txt` files are explicitly excluded. The two TSV sources (datatable and untarg_data) use samples-as-rows orientation; the mwTab embedded matrix uses features-as-rows (transposed). Orientation is detected from the presence of the `Samples`/`Factors` row marker in mwTab blocks rather than heuristic row-count comparison.

**API failure modes encountered during ingestion (untarg_data endpoint):** Of 2,198 analyses probed, 338 failed: 333 returned `no_response` (HTTP timeout or 404) and 5 returned `empty_or_invalid_tsv` (AN007492, AN007547, AN007548, AN007549, AN007804). These failures are recorded in `checkpoint_untarg.json` and flagged as `untarg_has_tabular: false` in each analysis `manifest.json`.

**mwTab files with no quantitative block:** Of 6,687 study-AN bundles with a mwTab file present, 1,700 (25.4%) failed strict validation — the file existed but contained no recognised data block with numeric content (metadata-only mwTab, or data blocks containing exclusively header rows). These are classified as `invalid` rather than `missing`.

**Nine analyses with no mwTab file despite having an AN directory:** ST001385/AN002312, ST003104/AN005082, ST003113/AN005098, ST003113/AN005099, ST003389/AN005557, ST003687/AN006051, ST003744/AN006148, ST003819/AN006279, ST003999/AN006593. These are flagged as `mwTab_txt_has_tabular: false`, `status: missing` in the manifest.

**Three cross-attributed AN IDs retained as distinct study-AN bundles:** The REST API summary endpoint returned 6,696 entries; three AN IDs appear under two study IDs each — AN004586 under ST002817 and ST002818, and AN007493 and AN007494 under ST004470 and ST004471. Each was downloaded independently per study path. Inspection of the downloaded content confirmed that these pairs are not equivalent: AN007493 and AN007494 have `untarg_data` files present in ST004470 but absent in ST004471, and all three ANs inherit different disease metadata, sample factor strings, and disease endpoints from their respective parent studies. Because the study-AN bundle (not the AN in isolation) is the unit of ML analysis in MERIT — defining sample labels, disease context, and available data sources — all 6,696 bundles are retained as distinct records. All counts reported in this paper reflect 6,696 study-AN bundles.

**Sample count discrepancy between datatable and untarg_data for the same analysis:** In 38% of the 390 analyses where both sources were valid, sample counts differed. Two root causes were identified: (1) injection-level replicates in datatable vs. biological-level aggregation in untarg_data; (2) different sample subsets (biological vs. QC/reference) captured by each endpoint. This is a property of the repository data, not a pipeline artefact.

### Supplementary Note 2. Label harmonization and factor selection

The Workbench connector scans all `Key:Value` factor strings across the sample list and selects the `Key` with the highest non-empty coverage, preferring biologically meaningful keys (Group, Diagnosis, Disease, Condition, Phenotype, Class, Status) over demographic/technical keys (Age, Sex, BMI, Batch). The selected key must produce ≥2 distinct non-unknown values. Connector-internal keys (mb_sample_id, raw_data, class_string, factor_string, endpoint_label, endpoint_label_key, original_sample_id, sample_source, raw_file) are excluded from factor variable richness counting.

### Supplementary Note 3. Scoring design decisions

A comprehensive reference of all scoring design decisions organized by module is provided in `merit/manuscript/scoring-design-decisions.md`, covering composite score weights, QC filtering policy, neutral scoring for repository gaps, within-assay vs. cross-assay distinction, normalization heuristic order, and all threshold choices with rationale.

---

*MERIT source code, documentation, and all assessment outputs are available at [repository URL to be added]. All 4,121 study assessment JSON files are stored in `merit-full-run-mw/json/`.*
