# MERIT Scoring Logic and Design Rules

## Scope

This document describes how MERIT generates the readiness outputs used in the current codebase. It is intended as a methods-facing technical reference for writing the scoring methodology. It covers the active scoring pipeline, metric definitions, section aggregation, band assignment, gate logic, and the UI-level confidence indicator. It does **not** discuss empirical results or repository-wide observations.

Important clarification: the **final core ML readiness score is not per-metric weighted**. It is the simple unweighted mean of 5 section scores: Structural, Analytical, Annotation, Cohort, and ML Task Readiness. Any `0.5`, `0.4`, or similar coefficients described later refer only to **internal formulas inside individual metrics**, not to the final cross-section readiness aggregation.

Primary implementation files:

- `merit/workflow.py`
- `merit/assessment.py`
- `merit/readiness_score.py`
- `merit/metrics/structural.py`
- `merit/metrics/metadata.py`
- `merit/metrics/analytical.py`
- `merit/metrics/annotation.py`
- `merit/metrics/cohort.py`
- `merit/metrics/ml_readiness.py`
- `merit/ui.py`
- `merit/utils.py`

---

## 1. High-Level Scoring Pipeline

MERIT evaluates each available tabular source independently and only then derives the displayed readiness summary.

### 1.1 Source-local assessment

For a study bundle, MERIT attempts separate assessments for:

- `datatable`
- `mwtab`
- `untarg_data`

This is implemented in `merit/workflow.py` through `_run_all_sources()` and `_assess_one_source()`.

Important design rule:

- **Each source is scored strictly on its own matrices and its own matrix-backed samples.**
- MERIT explicitly trims the canonical sample pool to sample IDs that are actually present in the selected source matrices before scoring.
- Therefore, `datatable`, `mwtab`, and `untarg_data` are not merged into a canonical study-wide sample matrix for scoring.

This rule prevents score inflation or distortion when study-level metadata contains more samples than a given source actually carries.

### 1.2 Assessability requirement

A source is considered assessable only if:

- it has at least one feature matrix, and
- at least one numeric value remains after source-aware missingness handling.

If no usable value remains, that source is treated as unavailable for scoring.

### 1.3 Primary source used for default study score

When a workflow artifact is written for a study, MERIT chooses the first available source in this priority order:

1. `datatable`
2. `mwtab`
3. `untarg_data`

The score shown as the study’s default readiness score is therefore the readiness score of the **primary available source**, not a merged cross-source score.

At the same time, the UI can expose source-specific readiness scores separately, because each source is scored independently.

---

## 2. Canonical Data Model Used by Scoring

MERIT scores a normalized `CanonicalStudy` object containing:

- study record
- sample records
- assay records
- feature matrices
- annotation records
- mapping records
- provenance
- score defaults

Key scoring defaults set by the Workbench connector include:

- `minimum_class_count = 5`
- `missingness_threshold = 0.25`
- `high_confidence_mapping = 0.8`

Of these, `minimum_class_count` is directly used by the active readiness metrics.

---

## 3. Sample Filtering and Biological Sample Definition

A recurring design rule across structural, analytical, cohort, and ML-feasibility metrics is that many calculations are performed on **biological samples only**.

Biological-vs-non-biological status is decided by `sample_is_qc_like()` in `merit/utils.py`.

### 3.1 QC-like / non-biological detection

A sample is treated as QC-like if QC/blank keywords are detected in:

- `sample_id`
- `label`
- `class_string`
- `factor_string`

The keyword logic includes terms such as:

- `qc`
- `blank`
- `pool`
- `reference`
- `solvent`
- `process blank`
- `system suitability`
- `wash`

Important guardrail:

- `sample_type` is **not** used as the primary QC signal when class/factor context is present.
- This prevents genuine biological rows labeled with noisy `sample_type` values such as `Pooled Sample` from being incorrectly excluded.

### 3.2 Consequence for metrics

The following types of calculations frequently exclude QC/blank/pool/reference rows:

- biological sample count
- class counts
- label quality metrics
- sample-level missingness
- normalization diagnostics
- some cohort metrics
- feature-to-sample ratio denominator

This means MERIT’s `n_samples` and `n_biological_samples` are intentionally different quantities.

---

## 4. Active Metric Registry

The active registry is defined in `merit/metrics/__init__.py` as `DEFAULT_METRICS`.

### 4.1 Sections actively used in readiness generation

These sections feed the readiness workflow:

- Structural
- Metadata / FAIR
- Analytical QC
- Annotation / Interoperability
- Cohort / Bias
- ML Task Readiness

### 4.2 Additional sections computed but excluded from the readiness score

These are computed separately but are **not** included in the readiness aggregate:

- Class Separability
- Cross-Study Harmonization

### 4.3 Informational metrics

Some active metrics are reported but explicitly excluded from section scoring via `informational = True`:

- `qc_blank_presence`
- `scale_diagnostics`
- `metabatch_batch_annotation_compatibility`

### 4.4 Full-profile-only metrics

The following metrics are included only in the `full` profile:

- `feature_correlation_burden`
- `outlier_burden`
- `feature_level_missingness`
- `class_separability`

Production artifacts such as the v6 dump are generated with the `full` profile.

---

## 5. Section-Level Metric Definitions

## 5.1 Structural Section

Active structural metrics:

1. `schema_integrity`
2. `tabular_data_availability`
3. `required_field_completeness`
4. `duplicate_entities`
5. `minimum_sample_count`

### 5.1.1 `schema_integrity`

File: `merit/metrics/structural.py`

Checks 5 required schema elements:

- study ID present
- title present
- samples present
- assays present
- feature matrices present

Formula:

- `score = passed_checks / 5`

Status:

- `pass` if score = 1.0
- `warn` if score >= 0.6 and < 1.0
- `fail` otherwise

### 5.1.2 `tabular_data_availability`

Counts matrices that contain all of:

- at least one sample ID
- at least one feature ID
- at least one value row

Formula:

- `score = n_usable_matrices / n_total_matrices`

Status:

- `pass` if at least one usable matrix exists
- `fail` otherwise

This metric is closely related to gate `G1`, but the gate uses source availability logic rather than the metric score directly when source availability is known.

### 5.1.3 `required_field_completeness`

This metric uses equal weighting of study-level and sample-level completeness.

Study-level fields checked:

- title
- description
- organism
- disease
- analysis type
- platform

Study-level component:

- `study_score = study_fields_present / 6`

Sample-level fields checked over matrix-backed samples:

- label
- sample type
- organism (sample-level organism or study-level organism fallback)

Sample-level component:

- `sample_score = sample_fields_present / (3 * n_samples)`

Final formula:

- `score = 0.5 * study_score + 0.5 * sample_score`

Status:

- `pass` if score >= 0.85
- `warn` otherwise

### 5.1.4 `duplicate_entities`

Counts duplicate sample identifiers and duplicate feature identifiers.

Formula:

- `total_duplicates = duplicate_sample_occurrences + duplicate_feature_occurrences`
- `denominator = n_samples + n_features`
- `score = max(0, 1 - total_duplicates / denominator)`

Status:

- `pass` if no duplicates are found
- `warn` otherwise

### 5.1.5 `minimum_sample_count`

This metric operates on **biological samples only**.

Default threshold:

- `THRESHOLD = 20`

Formula:

- `score = min(1, n_biological_samples / 20)`

Status:

- `pass` if `n_biological_samples >= 20`
- `warn` otherwise

This metric is separate from gate `G2`, which uses pass/warn/fail ranges.

---

## 5.2 Metadata / FAIR Section

Active metadata metrics:

1. `fair_study_metadata_compliance`
2. `fair_metabolite_identifier_resolvability`

This section is **not included in the core ML readiness score**. Instead, it is reported separately as the **reusability score**.

### 5.2.1 `fair_study_metadata_compliance`

This metric checks 7 binary FAIR-style metadata conditions:

1. DOI registered
2. linked publication present
3. funding source declared
4. contributors listed
5. study type declared
6. substantive study description (>=20 words)
7. raw data format recorded in assay metadata

Formula:

- `score = passed_checks / 7`

Status:

- `pass` if score >= 0.8
- `warn` if score >= 0.6 and < 0.8
- `fail` otherwise

This metric measures study-level documentation quality, not ML feasibility.

### 5.2.2 `fair_metabolite_identifier_resolvability`

This metric prefers repository-provided metabolite metadata when available.

#### Workbench mode

If `metabolites.json` exists for a Workbench study, the metric uses it directly.

Definitions:

- `named_total` = number of metabolite rows with usable metabolite names
- `refmet_matched` = number of named metabolite rows with a non-empty RefMet match

Formula:

- `score = refmet_matched / named_total`

Status:

- `pass` if score >= 0.7
- `warn` if score >= 0.5 and < 0.7
- `fail` otherwise

#### Fallback mode

If explicit metabolite endpoint rows are unavailable, the metric falls back to annotations/mappings.

A named metabolite is considered resolvable if at least one trusted namespace is present, including:

- RefMet
- HMDB
- ChEBI
- KEGG
- PubChem
- InChI
- InChIKey
- Metlin
- LipidMaps

Formula:

- `score = resolvable_named_metabolites / total_named_metabolites`

Status is assigned with the same thresholds as above.

---

## 5.3 Analytical QC Section

Active analytical metrics:

Scored:

1. `missingness_structure`
2. `assay_platform_comparability`
3. `feature_correlation_burden` (`full` only)
4. `outlier_burden` (`full` only)
5. `feature_level_missingness` (`full` only)

Informational only:

- `qc_blank_presence`
- `scale_diagnostics`
- `metabatch_batch_annotation_compatibility`

### 5.3.1 Source-aware zero and missingness semantics

These semantics are central to several analytical metrics and are implemented in `merit/metrics/analytical.py`.

A value is treated as missing if it is:

- `None`
- non-finite (`NaN`, `Inf`)
- an empty string
- a known token such as `NA`, `ND`, `BDL`, `BQL`, `LOD`, `LLOQ`, `BLOQ`, `NQ`
- any string beginning with `<` such as `<LOD`

Additional source-aware rule:

- `datatable` zeros are treated as **valid values**
- `mwtab` and `untarg_data` zeros are treated as **missing / below-detection values**

This rule affects:

- sample-level missingness
- feature-level missingness
- outlier detection inputs
- scale diagnostics
- assay comparability

### 5.3.2 `qc_blank_presence` (informational)

This metric reports whether QC-type and blank-type controls are present.

Per analysis:

- +0.5 if at least one QC/pool/reference/system-suitability control is present
- +0.5 if at least one blank is present

Study-level formula:

- `score = 0.5 * I(QC present) + 0.5 * I(blank present)`

Status:

- `pass` if score >= 0.5
- `warn` otherwise

Important note:

- This metric is informational and does **not** contribute to the readiness score.
- QC presence is relevant to drift correction and QC-based normalization.
- Blank presence is relevant to contaminant screening and blank subtraction.

### 5.3.3 `missingness_structure`

This is the primary scored sample-level missingness metric.

For each biological sample row in each matrix:

- `sample_missing_rate = n_missing_features / n_features`

Per analysis:

- `analysis_score = 1 - median(sample_missing_rates)`

Aggregate analytical metric score:

- `score = mean(per_analysis_scores)`

Additional diagnostic:

- class-dependent missingness gap is computed when at least two label groups exist
- for each class, class missingness is estimated as `missing_cells / total_cells`
- the per-analysis class gap is `max(class_rates) - min(class_rates)`
- the global class-dependent gap is a cell-count-weighted average across analyses

Status:

- `pass` if score >= 0.85 and class-dependent gap < 0.1
- `warn` otherwise

### 5.3.4 `assay_platform_comparability`

This metric assesses whether assays are on broadly comparable abundance scales.

For each analysis:

- retain finite, positive, non-missing values only
- compute `log10_median` of those values

If multiple analyses are usable:

- `spread = max(log10_median) - min(log10_median)`
- `score = 1 / (1 + spread)`

Special cases:

- no usable analyses -> score 0.0
- exactly one usable analysis -> score 1.0 by definition

Status:

- `pass` if score >= 0.5
- `warn` otherwise

### 5.3.5 `feature_correlation_burden` (`full` profile only)

This metric estimates redundancy among features.

Procedure:

- build a feature matrix
- apply source-aware missingness handling
- impute missing values column-wise with column medians
- remove constant features
- compute pairwise feature-feature correlations
- count pairs with `|r| >= 0.95`

Formula:

- `score = 1 - (high_correlation_pairs / sampled_pairs)`

Memory-safety guards:

- skipped if feature count exceeds `12000`
- skipped if matrix cells exceed `20,000,000`

Status:

- `pass` if score >= 0.85
- `warn` otherwise

If all analyzable matrices are skipped for safety, a conservative fallback score of `0.5` is used.

### 5.3.6 `outlier_burden` (`full` profile only)

This metric has two components.

#### Sample-level component

For each sample:

- summarize the sample by its median finite abundance

Across sample medians within an analysis:

- compute `Q1`, `Q3`, `IQR = Q3 - Q1`
- mark sample as outlier if outside `[Q1 - 1.5*IQR, Q3 + 1.5*IQR]`

Per analysis:

- `sample_score = 1 - sample_outliers / sample_total`
- `analysis_score = sample_score`

Aggregate score:

- `sample_component = 1 - total_sample_outliers / total_sample_summaries`
- `score = sample_component`

Feature-level outlier burden is not mixed into this score. Feature-level distribution issues are handled by other analytical diagnostics and by feature-level missingness.

Status:

- `pass` if score >= 0.9
- `warn` otherwise

### 5.3.7 `scale_diagnostics` (informational)

This metric does not contribute to readiness scoring. It is a heuristic diagnostic for whether values appear raw-like or already transformed.

Using global and per-analysis distribution summaries:

- `median`
- `p90`
- `max`
- `min`

Classification heuristic:

- raw-like if values show large central tendency and high upper tail
- otherwise treated as likely transformed/normalized

The metric also reports:

- low-signal features (bottom decile of per-feature P90)
- near-zero-variance features using relative MAD and IQR tests

This output is intended for interpretation and preprocessing guidance, not section scoring.

### 5.3.8 `feature_level_missingness` (`full` profile only)

For each feature in each analysis:

- `feature_missing_rate = n_missing_samples / n_samples`

Aggregate score:

- `score = 1 - mean(feature_missing_rates)`

High-missingness threshold:

- feature flagged if `missing_rate > 0.30`

Status:

- `pass` if fewer than 10% of features exceed the threshold
- `warn` otherwise

### 5.3.9 `metabatch_batch_annotation_compatibility` (informational)

This metric reports whether Workbench factor annotations can be converted into
MetaBatch-style batch/covariate tables for the samples present in each active
feature matrix.

The metric uses the local Workbench `factors.json` file, which mirrors the
Workbench `allfactors` endpoint used by StdMW/MetaBatch. A factor column is
reported as MetaBatch-usable when it:

- has at least two non-empty levels
- covers at least 60% of matrix samples
- is not nearly one unique value per sample (>90% distinct values)

Explicit technical batch-like keys are reported separately by a MERIT-specific
conservative scan of factor names and values for batch/run/order/plate/injection/
acquisition-like text. This technical-like flag is not a separate MetaBatch rule;
it is added to avoid overinterpreting generic biological covariates as technical
batch metadata.

Reference tool: https://bioinformatics.mdanderson.org/public-software/metabatch/

This metric is informational and excluded from analytical section scoring.

---

## 5.4 Annotation / Interoperability Section

Active annotation metrics:

1. `feature_annotation_type`
2. `annotation_ambiguity_burden`
3. `unknown_feature_fraction`
4. `feature_redundancy`

### 5.4.1 `feature_annotation_type`

MERIT classifies feature names into categories such as:

- named metabolites
- mz/RT-style tokens
- NMR spectral bins
- unannotated / unknown
- non-metabolite placeholders

Scoring logic:

- if >=50% of features are NMR bins -> score `0.65` (`nmr_binned` tier)
- else if named fraction >=70% -> score `1.0` (`named_metabolites` tier)
- else if named + mz/RT fraction >=70% and at least one named feature exists -> score `0.5` (`mixed_mz_rt` tier)
- else -> score `0.2` (`mostly_unannotated` tier)

Status:

- `pass` if score >= 0.65
- `warn` otherwise

### 5.4.2 `annotation_ambiguity_burden`

This metric counts annotations carrying ambiguity flags.

Formula:

- `score = 1 - ambiguous_annotations / total_annotations`

Status:

- `pass` if score >= 0.7
- `warn` otherwise

For NMR-binned studies, this metric is treated as not applicable and returns a pass-like score of `1.0` because spectral bins are not ambiguous in the metabolite identity sense.

### 5.4.3 `unknown_feature_fraction`

Counts features whose raw names are unknown placeholders.

Formula:

- `score = 1 - unknown_features / total_features`

Important rule:

- NMR spectral bins are **not** treated as unknown features

Status:

- `pass` if score >= 0.8
- `warn` otherwise

### 5.4.4 `feature_redundancy`

This metric counts repeated raw feature names **within the same assay**.

Key design rule:

- the same metabolite name appearing across different assays is allowed and is **not** penalized
- only within-assay duplication contributes to redundancy burden

Formula:

- `score = 1 - redundant_within_assay_occurrences / total_annotations`

Status:

- `pass` if score >= 0.85
- `warn` otherwise

---

## 5.5 Cohort / Bias Section

Active cohort metrics:

1. `class_balance`
2. `group_size_support`
3. `label_entropy`

Only these three metrics are active in the current registry for cohort scoring.

### 5.5.1 `class_balance`

This metric uses only labeled biological samples.

If no labeled groups exist:

- `score = 0.0`

If exactly one class exists:

- `score = 0.25`

Otherwise:

- `score = min(class_count) / max(class_count)`

Status:

- `pass` if score >= 0.4
- `warn` otherwise

This metric captures worst-case minority-vs-majority imbalance.

### 5.5.2 `group_size_support`

This metric focuses on the smallest class size.

If fewer than 2 classes exist:

- `score = 0.0`

Otherwise, let `m = min_class_size`:

- if `m >= 20` -> score `1.0`
- if `10 <= m < 20` -> score `0.7`
- if `5 <= m < 10` -> score `0.4`
- if `m < 5` -> score `0.1`

Status:

- `pass` if score >= 0.7
- `warn` otherwise

This metric is the basis for gate `G4` but the gate uses its own pass/warn/fail rule.

### 5.5.3 `label_entropy`

This metric uses normalized Shannon entropy of class proportions.

Definitions:

- `p_i = class_i_count / total_labeled_samples`
- `H = -sum(p_i * ln(p_i))`
- `H_max = ln(K)` where `K` is the number of classes
- `score = H / H_max`

If fewer than 2 classes exist:

- `score = 0.0`

Status:

- `pass` if score >= 0.7
- `warn` otherwise

Interpretation:

- values near 1 indicate evenly distributed classes
- values near 0 indicate dominance by one or a few classes

---

## 5.6 ML Task Readiness Section

Active ML-feasibility metrics:

1. `disease_endpoint_extractability`
2. `factor_label_harmonizability`
3. `label_suitability`
4. `feature_to_sample_ratio`

### 5.6.1 `disease_endpoint_extractability`

This metric asks whether a usable supervised endpoint can be derived.

Inputs:

- study-level disease field presence
- biological sample labels
- label coverage
- number of distinct usable label groups

Definitions:

- `label_coverage = usable_labeled_samples / biological_samples_with_labels`
- `distinct_groups = number of usable label groups`

Scoring rule:

- if disease field absent and label coverage < 0.5 -> score `0.0`, `fail`
- if `distinct_groups >= 2` and `label_coverage >= 0.8` -> score `1.0`, `pass`
- if `distinct_groups >= 2` and `label_coverage >= 0.5` -> score `0.7`, `warn`
- otherwise -> score `0.3`, `warn`

### 5.6.2 `factor_label_harmonizability`

This metric scores the quality and simplicity of factor-derived labels.

It combines two components:

1. `label_quality`
2. `simplicity`

#### Label quality

- `label_quality = valid_usable_biological_labels / raw_biological_labels`

#### Simplicity

This is based on the number of pipe-separated label dimensions.

- 0 pipes (1 dimension) -> `1.0`
- 1 pipe (2 dimensions) -> `0.7`
- 2 pipes (3 dimensions) -> `0.4`
- >=3 pipes (4+ dimensions) -> `0.1`

Final formula:

- `score = 0.5 * label_quality + 0.5 * simplicity`

Status:

- `pass` if score >= 0.75
- `warn` otherwise

This metric also reports discrepancies between tabular labels and endpoint-derived labels, but tabular labels remain authoritative.

### 5.6.3 `label_suitability`

This metric evaluates whether class sizes are adequate for modeling.

It uses only biological samples with usable labels.

Let:

- `counts = class counts`
- `minimum_class_count = study.score_defaults["minimum_class_count"]`, default `5`
- `observed_min = min(counts.values())`

Scoring rule:

- if fewer than 2 classes exist -> score `0.0`
- else `score = min(1, observed_min / minimum_class_count)`

Status:

- `pass` if score >= 1.0
- `warn` otherwise

### 5.6.4 `feature_to_sample_ratio`

This metric is scored **per analysis**, then aggregated.

For each analysis matrix:

- `ratio_i = n_features_in_matrix / n_samples_in_matrix`

Per-analysis score mapping:

- if `ratio_i <= 10` -> `1.0`
- if `10 < ratio_i <= 50` -> `0.8`
- if `50 < ratio_i <= 200` -> `0.5`
- if `ratio_i > 200` -> `max(0.1, 1 - ratio_i / 1000)`

Aggregate score:

- `final_score = sample_weighted_mean(per_analysis_scores)`

Important design note:

- the scored ratio uses **per-analysis sample counts**, not a global study-level feature/sample ratio
- a study-level global ratio is still reported for context, but it is not the scoring input

Status:

- `pass` if score >= 0.8
- `warn` otherwise

---

## 6. Section Aggregation

Section aggregation is implemented in `merit/readiness_score.py`.

### 6.1 Informational metrics are excluded

Before section means are computed, MERIT removes any metric marked as `informational=True`.

### 6.2 Fixed section denominators

Important distinction:

- MERIT does **not** assign differential weights to the 5 sections in the final core readiness score.
- MERIT also does **not** assign a second layer of cross-metric weighting at the section-combination stage.
- The only weighted expressions in the code are internal to specific metric definitions (for example, 0.5/0.5 splits inside a metric) or source-specific aggregation formulas such as sample-weighted per-analysis summaries.


The readiness framework uses fixed section metric counts:

- Structural = 5
- Metadata = 2
- Analytical = 5
- Annotation = 4
- Cohort = 3
- ML Feasibility = 4

Formula:

- `section_score = sum(noninformational metric scores) / max(actual_scored_metrics, fixed_section_count)`

Design intention:

- denominators remain stable across profile differences and cached artifact types
- score drift is prevented when some metrics are omitted by profile

In normal production use, MERIT is run in `full` profile, so the analytical denominator of 5 corresponds to:

- `missingness_structure`
- `assay_platform_comparability`
- `feature_correlation_burden`
- `outlier_burden`
- `feature_level_missingness`

If a non-full profile is run, the denominator still remains fixed rather than shrinking. This preserves comparability across artifacts and prevents denominator drift.

---

## 7. Composite Readiness Score and Reusability Score

### 7.1 Core ML readiness score

The core ML readiness score uses **five** section scores:

- structural
- analytical
- annotation
- cohort
- ml_feasibility

Formula:

- `core_ml_readiness_score = mean(structural, analytical, annotation, cohort, ml_feasibility)`

There are no additional weights applied at this stage. Each of the five section scores contributes equally.

Metadata / FAIR is **not** included in this core score.

### 7.2 Reusability score

The reusability score is computed separately from the metadata section alone.

Formula:

- `reusability_score = mean(metadata)`

Since only one section contributes, this is effectively the metadata section score.

### 7.3 Why these are separate

MERIT deliberately separates:

- **modelability / ML readiness** from
- **FAIR-style reusability / metadata quality**

A study may therefore have:

- high ML readiness but low reusability, or
- low ML readiness but good metadata documentation

This is by design.

---

## 8. Provisional Bands

The provisional band is based only on the core ML readiness score.

Thresholds:

- `score >= 0.85` -> `Ready`
- `0.70 <= score < 0.85` -> `Conditional`
- `0.50 <= score < 0.70` -> `Fragile`
- `score < 0.50` -> `Not Ready`

This band is provisional because it can still be capped by feasibility gates.

---

## 9. Feasibility Gates

The final band is adjusted by five gates implemented in `_compute_gates()`.

These gates are not simple aliases for section scores. They use explicit decision rules.

## 9.1 G1: tabular data availability

Inputs:

- source availability counts if known, otherwise tabular metric details

Rule:

- `pass` if at least 1 usable matrix exists
- `fail` otherwise

No `warn` state.

## 9.2 G2: sufficient biological sample count

Input:

- `n_biological_samples`

Preferred threshold:

- default `20`

Rule:

- `pass` if `n_bio >= 20`
- `warn` if `10 <= n_bio < 20`
- `fail` if `n_bio < 10`

## 9.3 G3: deposited groups

Input:

- `distinct_label_groups` from `disease_endpoint_extractability`

Rule:

- `pass` if at least 2 groups exist
- `fail` otherwise

No `warn` state.

## 9.4 G4: minimum per-group support

Inputs:

- canonical class counts from `group_size_support`; `label_suitability` counts are used only as a fallback
- `minimum_class_count` default `5`

Rule:

- `pass` if smallest class >= 5 and at least 2 classes exist
- `warn` if smallest class >= 3 and at least 2 classes exist
- `fail` otherwise

## 9.5 G5: non-catastrophic missingness

Input:

- global median sample missingness rate from `missingness_structure`

Rule:

- `pass` if median sample missingness <= 50%
- `warn` if 50% < median sample missingness <= 80%
- `fail` if median sample missingness > 80%
- if missingness is unavailable, gate defaults to `warn`

---

## 10. Gate Ceiling Hierarchy and Final Band

Gate logic is hierarchical.

### 10.1 Ceiling logic

MERIT evaluates the gates and applies the following ceiling logic:

1. If `G1` fails -> final band = `No Data`
2. Else if **any** gate fails -> ceiling = `Not Ready`
3. Else if **any** gate warns -> ceiling = `Conditional`
4. Else -> no ceiling is applied

### 10.2 Final band assignment

The final band is:

- the provisional band if no ceiling applies, or
- the lower-ranked of the provisional band and the gate ceiling

Band rank order:

- `No Data` < `Not Ready` < `Fragile` < `Conditional` < `Ready`

Thus gates can only **downgrade**, never upgrade, the provisional band.

Important implementation note:

- Individual metric `status` labels (`pass`, `warn`, `fail`) are descriptive and section-local.
- They influence the final readiness output only through their numeric scores, except for the five explicit feasibility gates, which are the only rule-based mechanism that can directly cap the final band.

### 10.3 Consequence

A study can have a high numerical readiness score but still finish as `Not Ready` if a critical gate fails. This is expected behavior.

---

## 11. Score Confidence (UI-Level Indicator)

`Score confidence` is computed in `merit/ui.py`. It is **not** part of the readiness score or band calculation.

It is a trust indicator for how interpretable the composite score is.

### 11.1 Hard low-confidence floors

Confidence is immediately `Low` if:

- `n_feature_matrices = 0`, or
- `n_biological_samples < 10`

### 11.2 Informative-dimension count

The UI inspects six section scores:

- structural
- metadata
- analytical
- annotation
- cohort
- ml_feasibility

A section is treated as informative if:

- `score > 0.55`, or
- `score < 0.45`

Scores near 0.5 are treated as near-neutral / weak-signal dimensions.

### 11.3 Low confidence logic

Potential low-confidence reasons include:

- only <=2 of 6 dimensions are informative
- cohort size <30
- metadata score <0.5
- analytical score <0.5

If at least two of these reasons apply, confidence is labeled `Low`.

### 11.4 High confidence logic

Confidence is `High` only if all of the following hold:

- informative dimensions >=5 of 6
- `n_biological_samples >= 50`
- `metadata_score >= 0.65`

### 11.5 Moderate confidence logic

All remaining cases are labeled `Moderate`.

The UI now reports the first unmet criterion explicitly, for example:

- informative dimensions below target
- cohort size below high-confidence threshold
- metadata score below high-confidence threshold

Important interpretation:

- `score confidence` measures trust in the composite score
- it is not itself a quality score
- it does not alter section scores, band, gates, or final band

---

## 12. Non-Scoring Sections and Excluded Outputs

### 12.1 Class Separability

`class_separability` is computed in `merit/metrics/separability.py` in the full profile only.

It uses labeled biological samples, PCA-based visualization output, and a repeated train/test AUROC proxy. It is useful diagnostically but is **not included** in any readiness aggregate or gate.

### 12.2 Cross-Study Harmonization

Two harmonization metrics are computed:

- `harmonization_feasibility`
- `pathway_mappability_proxy`

These are also **excluded** from the readiness score, reusability score, and gates.

### 12.3 Inactive metric classes present in the codebase

Some metric classes exist in the codebase but are not currently included in `DEFAULT_METRICS`, and therefore do not contribute to the current MERIT outputs. Examples include:

- `IdentifierCoverageMetric`
- `ConfoundingRiskMetric`
- `AgeBiologicalSexMetadataMetric`
- `BiologicalSexDistributionMetric`
- `SampleMatrixHomogeneityMetric`
- `LeakageRiskMetric`
- `RecommendedMLTaskMetric`
- `StratifiedSplitFeasibilityMetric`

These should not be described as active components of the current readiness framework unless the metric registry changes.

---

## 13. Final Output Fields Generated by the Readiness Layer

The readiness layer writes the following key outputs:

- `score` (alias of core ML readiness score)
- `band` (alias of final band)
- `core_ml_readiness_score`
- `reusability_score`
- `provisional_band`
- `final_band`
- `gate_ceiling`
- `gates`
- `gate_summary`
- `section_scores`
- `core_section_scores`
- `reusability_section_scores`
- `recommendation`
- `actions`
- `status_note`

These are returned by `compute_readiness_score()` in `merit/readiness_score.py`.

---

## 14. Concise Methods Summary

In the current MERIT implementation, each deposited source (`datatable`, `mwtab`, `untarg_data`) is normalized and scored independently using only the samples present in that source’s assay matrices. Biological samples are separated from QC/blank-like controls using sample- and label-aware heuristics. Active metrics are grouped into Structural, Metadata/FAIR, Analytical QC, Annotation/Interoperability, Cohort/Bias, and ML Task Readiness sections. Informational metrics are excluded from scoring. Section scores are computed as fixed-denominator means of non-informational metric scores. The **core ML readiness score** is the unweighted mean of the Structural, Analytical, Annotation, Cohort, and ML Task Readiness section scores, while the **reusability score** is reported separately from the Metadata/FAIR section. A provisional band is assigned from the core ML readiness score and then capped by five feasibility gates covering tabular data availability, biological sample count, number of deposited groups, minimum per-group support, and catastrophic missingness. The final band therefore reflects both continuous section scoring and hard feasibility constraints. Separability and cross-study harmonization are computed as diagnostics but are not included in the final readiness score.
