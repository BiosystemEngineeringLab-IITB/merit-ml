# MERIT Scoring Design Decisions — Module-by-Module Reference

This document records every non-trivial design choice in the MERIT scoring
pipeline. Organized by source module so changes can be traced back to code.
Intended as a reference for the Methods / Supplementary Methods section of the
manuscript.

---

## 1. Composite Score (`merit/metaboscore.py`)

### 1.1 Section Weights

| Section | Weight | Rationale |
|---------|--------|-----------|
| Structural | 0.13 | Prerequisite checks (schema, completeness); most studies pass |
| Metadata / FAIR | 0.15 | Drives reproducibility and cross-study reuse |
| Analytical QC | 0.24 | Largest weight — analytical quality is the primary differentiator for ML fitness |
| Annotation | 0.17 | Named metabolites vs mz/RT features determines interpretability |
| Cohort / Bias | 0.16 | Class balance, confounding, demographic coverage |
| ML Task Readiness | 0.15 | Label suitability, feature-to-sample ratio, leakage risk |

Total = 1.00. Weights were set based on the relative impact each dimension has
on downstream ML model validity, informed by common failure modes in published
metabolomics ML studies.

### 1.2 Fixed-Denominator Section Scoring

Each section score is computed as `sum(metric_scores) / fixed_count`, where
`fixed_count` is the number of metrics in the **full** profile:

| Section | Fixed Count | Core-profile metrics | Full-only metrics |
|---------|-------------|---------------------|-------------------|
| structural | 5 | 5 | 0 |
| metadata | 5 | 5 | 0 |
| analytical | 8 | 5 | 3 (Outlier, FeatureCorrelation, FeatureLevelMissingness) |
| annotation | 4 | 4 | 0 |
| cohort | 4 | 4 | 0 |
| ml | 5 | 5 | 0 |

**Why:** Ensures that composite scores are directly comparable between core and
full profile runs. In core mode, absent full-only metrics contribute 0 to the
numerator while the denominator stays at 8 for analytical. This means
core-profile analytical scores are structurally lower — by design, so a "core"
score is a conservative lower bound on the "full" score.

### 1.3 Readiness Bands

| Band | Threshold | Meaning |
|------|-----------|---------|
| Ready | >= 0.85 | Suitable for ML modeling with minimal preprocessing |
| Conditional | >= 0.70 | Usable with targeted remediation |
| Fragile | >= 0.50 | Significant quality gaps; results may not generalize |
| Not Ready | < 0.50 | Major issues across multiple dimensions |
| No Data | TabularDataAvailability = 0.0 | Study has metadata but no parseable feature matrix |

### 1.4 "No Data" Override

When `TabularDataAvailabilityMetric` scores 0.0, the band is forced to
"No Data" regardless of the composite score. This prevents studies with only
metadata (no feature matrix) from receiving misleadingly moderate scores based
on structural/metadata checks alone.

---

## 2. QC/Blank Sample Filtering

### 2.1 Detection Strategy (`_QC_BLANK_KEYWORDS`)

A 16-term keyword list is matched against the concatenation of `sample_id`,
`label`, and `sample_type` (case-insensitive substring match):

```
qc, blank, pool, nist, reference, solvent, quality control, pooled qc,
ltr, sst, calibration standard, system suitability, process blank,
method blank, reagent blank, drift
```

**Why keyword-based:** MW does not have a structured `sample_type` field that
reliably distinguishes QC from biological samples. The keyword list was derived
from manual inspection of ~50 MW studies with known QC samples (e.g., ST002024,
ST000081, ST003390).

### 2.2 Where Filtering Is Applied

The `_is_biological_sample()` helper excludes QC/blank samples in:

| Metric | Module | Why filtering matters |
|--------|--------|----------------------|
| ClassBalanceMetric | cohort.py | QC labels ("QC", "pooled_qc") would appear as spurious classes |
| ConfoundingRiskMetric | cohort.py | QC samples inflate Cramer's V contingency table |
| AgeBiologicalSexMetadataMetric | cohort.py | QC rows lack demographics; would depress coverage |
| SampleMatrixHomogeneityMetric | cohort.py | QC matrix types differ from biological samples |
| LabelSuitabilityMetric | ml_readiness.py | QC labels are not valid classification targets |
| RecommendedMLTaskMetric | ml_readiness.py | QC labels inflate class count |
| StratifiedSplitFeasibilityMetric | ml_readiness.py | QC samples should not enter train/test splits |
| FeatureToSampleRatioMetric | ml_readiness.py | F:S ratio should reflect biological sample count |
| MinimumSampleThresholdMetric | structural.py | Minimum threshold is about biological samples |
| MissingnessMetric | analytical.py | QC/blank missingness patterns differ from biological; score should reflect ML-usable data quality |
| NormalizationStatusMetric | analytical.py | Value-scale classification should reflect biological data, not QC/reference standards |
| FactorLabelHarmonizationMetric | metadata.py | QC labels contaminate harmonization counts |
| DiseaseEndpointMetric | metadata.py | QC labels inflate distinct_groups count |

### 2.3 Where Filtering Is NOT Applied (by design)
- **IdentifierCoverageMetric, AnnotationAmbiguityMetric** — These assess
  feature-level annotation quality, independent of sample composition.
- **CrossStudyHarmonizationMetric** — Informational only, not in composite.

---

## 3. Structural Metrics (`merit/metrics/structural.py`)

### 3.1 SchemaIntegrityMetric

Checks 5 core schema requirements (study_id, title, samples, assays,
feature_matrices). Score = passed / 5.

- **Status thresholds:** score < 0.6 -> "fail"; 0.6-0.99 -> "warn"; 1.0 -> "pass"
- **Design choice:** "fail" at < 0.6 (fewer than 3 of 5 checks) because
  studies missing multiple core fields cannot be meaningfully scored.

### 3.2 CompletenessMetric

50/50 weighting between study-level and sample-level field completeness:

```
score = 0.5 * study_score + 0.5 * sample_score
```

- **Study-level fields (6):** study_id, title, organism, disease, platform,
  publication_date
- **Sample-level fields (3 x n_samples):** sample_id, label, sample_type
- **Why 50/50:** Without this, a study with 1000 samples and complete
  sample-level fields but missing disease/platform/organism would score ~0.95,
  masking important study-level gaps.

### 3.3 MinimumSampleThresholdMetric

Counts biological samples only (QC/blank excluded). Uses graduated scoring:

| Biological samples | Score |
|-------------------|-------|
| >= 30 | 1.0 |
| 20-29 | 0.7 |
| 10-19 | 0.4 |
| < 10 | 0.1 |

---

## 4. Metadata / FAIR Metrics (`merit/metrics/metadata.py`)

### 4.1 FairStudyMetadataComplianceMetric

5 non-redundant FAIR checks:

1. F1: Study identifier matches repository pattern (ST\d{6} for MW)
2. A1: Distribution manifest present
3. A1.1: File hashes present
4. R1.2: Parser version and connector name versioned
5. A1/I1: Repository source and source root recorded

Score = passed / 5.

### 4.2 FairMetaboliteIdentifierResolvabilityMetric

For MW studies, uses **explicit RefMet evidence** from `metabolites.json`
(refmet_name, regnos, refmet_match_count). Does NOT count lexical
normalization as a "resolved" identifier.

Composite score:
```
score = 0.45 * id_coverage + 0.20 * uri_coverage + 0.20 * consistency + 0.15 * provenance_coverage
```

**Why explicit-only:** Lexical name matching (e.g., "glucose" -> RefMet:Glucose)
has high false-positive rates for ambiguous compound names. Counting it as
"resolved" would inflate coverage.

### 4.3 FactorLabelHarmonizationMetric

```
score = 0.6 * label_quality + 0.4 * compactness
```

- `label_quality`: fraction of biological samples with a usable (non-unknown) label
- `compactness`: normalized_unique / raw_unique (how well normalization reduces label variants)
- QC/blank samples are excluded before counting.

### 4.4 DiseaseEndpointMetric

Checks whether a disease/condition endpoint is extractable for ML:

| Condition | Score |
|-----------|-------|
| No disease field AND label coverage < 50% | 0.0 (fail) |
| >= 2 groups AND label coverage >= 80% | 1.0 (pass) |
| >= 2 groups AND label coverage >= 50% | 0.7 (warn) |
| Otherwise | 0.3 (warn) |

QC/blank samples excluded from label counting.

### 4.5 FactorVariableRichnessMetric

Counts distinct factor variable keys across all samples, excluding
connector-internal keys:

```
Excluded: mb_sample_id, raw_data, class_string, factor_string,
          endpoint_label, endpoint_label_key, original_sample_id,
          sample_source, raw_file
```

These keys are injected by the MW connector during parsing and do not represent
true experimental factor variables.

| Factor types | Score |
|-------------|-------|
| >= 3 | 1.0 |
| 2 | 0.7 |
| 1 | 0.4 |
| 0 | 0.0 |

---

## 5. Analytical QC Metrics (`merit/metrics/analytical.py`)

### 5.1 NormalizationStatusMetric

Binary inferred scale scoring (intentionally simplified for interpretability).
QC/blank samples are excluded. NZV and low-signal features are diagnostics only
and do not affect the score.

| Condition | Label | Score | Rationale |
|-----------|-------|-------|-----------|
| `min >= 0`, `median >= 100`, `p90 >= 1000`, `max >= 5000` | `raw` | 0.0 | Strong raw-count signature |
| `min >= 0`, `max > 1000`, and (`p90 > 100` or `median > 10`) | `raw` | 0.0 | Wide dynamic-range raw-like signature |
| Otherwise | `likely_transformed` | 1.0 | Includes transformed and normalized positive scales |

Status: `pass` if aggregate score `> 0.5`, `warn` otherwise.

Declared value scale/units (from mwTab JSON `ANALYSIS.units`) are surfaced in
the UI separately and are not used directly in the inferred score.

### 5.2 BatchInfoAvailabilityMetric

Informational only — not included in the readiness score. Scans sample
attributes for batch/run-order/plate/injection/acquisition identifiers and
reports presence and matched keys. Absence of batch metadata is a repository
infrastructure gap, not necessarily a study quality flaw.

### 5.3 Full-Profile-Only Metrics

Three analytical metrics run only in `profile="full"`:

- **OutlierMetric** — IQR-based outlier detection per feature
- **FeatureCorrelationMetric** — Pairwise correlation to detect collinear blocks
- **FeatureLevelMissingnessMetric** — Per-feature missing rate distribution

These are computationally expensive for large matrices and are excluded from
core profile to keep single-study assessment fast.

---

## 6. Annotation Metrics (`merit/metrics/annotation.py`)

### 6.1 FeatureRedundancyMetric — Within-Assay Only

Redundancy is counted **within each assay** only. The same metabolite name
appearing across different assays (e.g., positive and negative ionization mode)
is expected and is NOT penalized.

```
score = 1.0 - (within_assay_redundant / total_annotations)
```

**Why:** Multi-mode LC-MS studies routinely detect the same compounds in both
positive and negative mode. Counting cross-assay overlap as redundancy would
penalize well-designed multi-modal studies.

### 6.2 FeatureAnnotationTypeMetric

Classifies annotations into three tiers:

| Tier | Condition | Score |
|------|-----------|-------|
| named_metabolites | >= 70% named | 1.0 |
| mixed_mz_rt | some named, >= 70% named+mz/RT | 0.5 |
| mostly_unannotated | otherwise | 0.2 |

Uses `classify_feature_name()` from `merit/feature_names.py` to distinguish
named metabolites from mz/RT identifiers and non-metabolite tokens.

---

## 7. Cohort / Bias Metrics (`merit/metrics/cohort.py`)

### 7.1 ClassBalanceMetric

```
score = min(class_count) / max(class_count)
```

- Single-class studies: score = 0.25
- QC/blank samples excluded
- Label "unknown" is dropped before counting

### 7.2 ConfoundingRiskMetric

Uses Cramer's V between class labels and sample-type/organism-part markers:

```
score = 1.0 - cramer_v
```

- Single marker across all classes: score = 1.0 (no confounding signal)
- QC/blank samples excluded (prevents "QC" from appearing as a spurious class
  in the contingency table)

### 7.3 AgeBiologicalSexMetadataMetric

```
score = (age_coverage + sex_coverage) / 2
```

**Special case — both completely absent:**

When neither age nor sex metadata is found for any biological sample, a
**neutral score of 0.5** is assigned. This prevents the metric from dominating
the cohort section score for ~95% of MW studies where the repository simply
does not expose demographic fields.

- Score 0.5: repository infrastructure gap (no demographic fields available)
- Score < 0.5: partial data present but incomplete (data attempted, not finished)
- Score > 0.5: demographic metadata present with reasonable coverage

**Why not 0.0:** Assigning 0.0 would make the cohort section score crash for
nearly every MW study, conflating "repository doesn't collect this data" with
"study has poor design." The 0.5 neutral score acknowledges the gap without
unfairly penalizing individual studies.

### 7.4 BiologicalSexDistributionMetric

**Note:** This metric is defined in `cohort.py` but is NOT registered in
`DEFAULT_METRICS`. It exists as a finer-grained companion to
AgeBiologicalSexMetadataMetric but was not included in the scored composite
because sex distribution data is rarely available in MW.

---

## 8. ML Task Readiness Metrics (`merit/metrics/ml_readiness.py`)

### 8.1 RecommendedMLTaskMetric

| Classes | Score | Task Label |
|---------|-------|------------|
| 2 | 1.0 | binary_classification |
| 3-10 | 1.0 | multi_class_classification |
| 11-20 | 0.7 | high_cardinality_classification |
| > 20 | 0.4 | excessive_classes |
| 1 | 0.25 | regression_only |
| 0 | 0.0 | no_labels |

**Why 3-10 classes score 1.0 (same as binary):** Multi-class classification
is a legitimate and common task in metabolomics (e.g., disease subtypes,
treatment groups). Penalizing it would contradict standard ML practice.

**Why > 20 classes score 0.4:** In MW, > 20 distinct labels almost always
indicates a factor-string parsing issue (e.g., composite date-prefixed labels
like "2019-01-15_Treatment_GroupA") rather than a genuine 20+ class problem.

### 8.2 FeatureToSampleRatioMetric

Per-matrix computation with sample-weighted aggregation:

```python
for each feature_matrix:
    ratio = n_features / n_biological_samples_in_matrix
    matrix_score = ratio_score(ratio)
    weighted_sum += matrix_score * n_samples
    weight_sum += n_samples
score = weighted_sum / weight_sum
```

Scoring function:
| Ratio | Score |
|-------|-------|
| <= 10 | 1.0 |
| <= 50 | 0.8 |
| <= 200 | 0.5 |
| > 200 | max(0.1, 1.0 - ratio/1000) |

**Why per-matrix:** A study with one 50-feature targeted panel and one
5000-feature untargeted scan should not have both assessments averaged into a
single misleading ratio. Sample-weighted aggregation gives more weight to
matrices with more samples.

**Previous approach (sum across all matrices):** Was incorrect — summing
features across positive and negative mode assays doubled the apparent ratio
for multi-assay studies.

### 8.3 StratifiedSplitFeasibilityMetric

Checks whether the smallest class has enough samples for stratified k-fold
cross-validation (k=5). QC/blank samples excluded.

### 8.4 LeakageRiskMetric

Checks for duplicate sample IDs within feature matrices. Duplicate samples in
the same matrix could lead to train-test leakage if the same biological sample
appears in both folds.

---

## 9. UI Indicators (`merit/ui.py`)

### 9.1 Score Confidence

Reflects how trustworthy the composite score is (not the score itself).

**Signals used:**
1. Number of feature matrices (0 = Low confidence)
2. Number of biological samples (< 10 = Low, affects metric stability)
3. Number of informative dimensions (sections not at neutral ~0.5 default)
4. Metadata section score (< 0.5 = sparse metadata)
5. Analytical section score (< 0.5 = weak analytical signal)

| Level | Condition |
|-------|-----------|
| Low | No feature matrix, OR < 10 bio samples, OR <= 2 informative dimensions + another weakness |
| High | >= 5 informative dimensions AND >= 50 samples AND metadata >= 0.65 |
| Moderate | Everything else |

**Why this matters:** A study can score 0.7 (Conditional) but have Low
confidence if the score is derived from only 2-3 dimensions with real signal
and the rest are neutral defaults.

### 9.2 Estimated ML Difficulty

A-priori assessment of how hard it will be to build a useful ML model,
independent of the ReadinessScore.

**Factors assessed (6):**
1. Cohort size: < 30 = hard, >= 100 = easy
2. Class balance: < 0.2 = severe, < 0.4 = moderate, >= 0.5 = easy
3. Feature-to-sample ratio: > 200 = very high, > 50 = high, <= 10 = easy
4. Missingness: > 30% = high, > 15% = moderate, < 5% = easy
5. Annotation quality: mz/RT-only = hard, named metabolites = easy
6. Class cardinality: > 20 = excessive (likely parsing issue), > 10 = high

| Level | Condition |
|-------|-----------|
| Hard | >= 3 hard factors |
| Moderate | >= 1 hard factor |
| Easy | 0 hard factors AND >= 2 easy factors |

---

## 10. Cross-Cutting Design Principles

### 10.1 Neutral Scores for Repository Gaps vs. Study Flaws

Several metrics assign a neutral score (typically 0.5) when the data is absent
due to repository infrastructure limitations rather than study design flaws:

- **AgeBiologicalSexMetadata:** 0.5 when no demographic fields exist
- **BatchInfoAvailability:** 0.5 base when no batch metadata
- **NormalizationStatus:** Scores data as-found, does not penalize for being
  unnormalized (0.3) as harshly as for being missing (0.0)

**Why:** MW does not uniformly expose demographic metadata, batch identifiers,
or normalization status. Scoring these as 0.0 would conflate "the repository
doesn't track this" with "the study has a quality problem," making repository-
level issues dominate the composite score.

### 10.2 Profile Stability

The full-profile denominator is used for both core and full profiles so that:
- A core-profile score is a conservative lower bound
- Scores from core and full runs can be compared directly
- Upgrading from core to full never decreases a score (it can only increase as
  new metrics contribute positive scores)

### 10.3 Auditable Remediation

All remediation actions (label normalization, feature deduplication, high-
missing feature dropping) are logged in an audit trail. Post-remediation
assessment re-runs the full metric suite so the delta is transparent.
