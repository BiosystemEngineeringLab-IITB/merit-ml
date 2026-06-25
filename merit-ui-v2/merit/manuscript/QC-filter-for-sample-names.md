# QC and Blank Sample Exclusion Filter

**Purpose:** Document the design, implementation, known limitations, and empirical impact of the filter used to exclude QC, blank, and reference samples from sample-count and feature/sample ratio calculations across the Metabolomics Workbench repository (6,696 analyses, 4,872 valid datatables).

---

## 1. Background and Motivation

MW datatable files are Samples × Features matrices. The first two columns are:

| Column | Header | Content |
|--------|--------|---------|
| 0 | `Samples` | Sample identifier (free text, lab-assigned) |
| 1 | `Class` | Pipe-delimited factor string, e.g. `FactorA:ValueA \| FactorB:ValueB` |

Studies routinely include non-biological samples in the same datatable:

- **Pooled QC samples** — created by mixing aliquots of all study samples; used to monitor instrument drift
- **Blank samples** — solvent/reagent injections; used to assess background contamination
- **Reference standards** — certified reference materials (e.g. NIST SRM 1950); used for cross-batch harmonisation
- **Calibration standards** — known-concentration injections for quantitative methods

Including these in sample counts inflates denominators and distorts feature/sample ratio and missingness statistics.

---

## 2. Data Sources Checked

The filter was applied to **datatable** files only (Samples × Features orientation, col 1 = class label). The same keyword set is applicable to untarg_data (col 1 = `group`) and mwtab (Factors row per sample), but those were not analysed here.

---

## 3. The 16-Keyword Set

Sourced from `merit/metrics/analytical.py:_QC_BLANK_KEYWORDS` (lines 18–23). The 16 terms cover:

### 3a. QC / Instrument-control terms

| # | Keyword | Rationale | Matching type |
|---|---------|-----------|---------------|
| 1 | `qc` | Universal abbreviation for Quality Control | Letter-boundary |
| 2 | `pool` | Pooled QC sample; pooled reference aliquot | Substring |
| 3 | `nist` | NIST SRM reference materials (e.g. SRM 1950) | Letter-boundary |
| 4 | `reference` | Generic reference sample label | Substring |
| 5 | `quality control` | Full phrase form of QC | Substring |
| 6 | `pooled qc` | Explicit full phrase | Substring |
| 7 | `ltr` | Long-term reference; common in large cohort studies | Letter-boundary |
| 8 | `sst` | System suitability test sample | Letter-boundary |
| 9 | `system suitability` | Full phrase form of SST | Substring |
| 10 | `calibration standard` | Quantitative calibration injection | Substring |
| 11 | `drift` | Drift correction sample (sometimes labelled separately) | Substring |

### 3b. Blank / Background terms

| # | Keyword | Rationale | Matching type |
|---|---------|-----------|---------------|
| 12 | `blank` | Generic blank (solvent, reagent, method) | Substring |
| 13 | `solvent` | Solvent blank injection | Substring |
| 14 | `process blank` | Full phrase for process/extraction blank | Substring |
| 15 | `method blank` | Full phrase | Substring |
| 16 | `reagent blank` | Full phrase | Substring |

### 3c. Matching strategy by token length

Short tokens (≤4 characters or common substrings of unrelated words) use **letter-boundary** matching (`(?<![a-zA-Z])TOKEN`) rather than simple substring matching. Longer phrases are safe as substrings.

| Token | Problem with substring | Solution |
|-------|----------------------|----------|
| `nist` | Matches `admi**nist**ration` — any treatment label with "administration" (e.g. `Metformin 100mg/kg (oral administration)`) | `(?<![a-zA-Z])nist` — requires no preceding letter |
| `qc` | `qc` is short but uncommon in non-QC contexts | `(?<![a-zA-Z])qc` |
| `ltr` | Short; could appear in other contexts | `(?<![a-zA-Z])ltr` |
| `sst` | Short; could appear in other contexts | `(?<![a-zA-Z])sst` |
| `blank`, `pool`, etc. | Long enough; rare false positives | Plain substring |

**Verified case:** `Treatment:Metformin 100mg/kg (oral administration)` — substring `nist` fires inside `admini**nist**ration`. Letter-boundary pattern correctly returns no match.

---

## 4. Two-Channel Detection: Sample Name AND Class Label

### 4a. Why both channels are needed

The Class label (col 1) is semantically rich but can be ambiguous when factors are complex. The sample name (col 0) is often more structured, but can be an opaque lab code.

| Analysis | Sample name (col 0) | Class label (col 1) | Which channel works? |
|----------|--------------------|--------------------|----------------------|
| AN006011 | `Experimental Blank-1` | `Treatment:Blank \| Sample source:LC buffer` | **Both** |
| AN006339 | `2024-11-25_Wills-15-Blank_NEG_001` | `Sample source:Blank \| Condition:-` | **Both** |
| AN007405 | `Blank1_PC3_LC` | `Sample source:blank \| Factor:-` | **Both** |
| AN003931 | `C220181016_Blank_1` | `treatment:injection blank` | **Both** |
| AN001414 | `PlasmaRef_20160726_1` | `Sample Type:Reference Sample` | **Both** |
| AN005041 | `1`, `18`, `19` (numeric) | `Sample source:blank` | **Class label only** |
| AN003487 | `1`, `10`, `11` (numeric) | `type:Nistplasma` | **Class label only** |
| AN005769 | `D01P`, `D02P` (opaque codes) | `Factor:NIST_Recovery` | **Class label only** |

Using sample name alone would miss all cases where labs use opaque numeric or alphanumeric IDs. The union of both channels provides the best coverage.

**Empirical breakdown across all 4,872 valid datatable analyses:**

| Signal source | QC sample rows |
|---------------|---------------|
| Sample name only (col 0 hits, col 1 does not) | 5,317 |
| Class label only (col 1 hits, col 0 does not) | 5,134 |
| Both channels hit | 9,457 |
| **Total QC/blank rows removed** | **19,908** |

Sample name and class label contribute roughly equally when considered independently, and ~47% of QC rows are confirmed by both.

### 4b. Class label parsing: value-only matching

The Class column contains pipe-delimited `FactorName:Value` pairs. Matching the entire string is incorrect because factor **names** can contain QC keywords while the **value** indicates a study sample.

**Problematic case (old approach):**

```
pool aliquot:no  →  "pool" hits on the factor name "pool aliquot"
                 →  treatment:DCIS | pool aliquot:no  flagged as QC
                 →  FALSE POSITIVE: these are biological study samples
```

**Fix:** split on `|`, then split each pair on the first `:`, and check the **right-hand side (value) only**.

```
"treatment:DCIS  | pool aliquot:no"
  pair 1 → key="treatment"    value="dcis"        → no match
  pair 2 → key="pool aliquot" value="no"          → no match
  result → NOT QC  ✓
```

```
"treatment:Total Pool1  | pool aliquot:yes"
  pair 1 → key="treatment"    value="total pool1" → "pool" hits → QC  ✓
```

### 4c. Sample Type priority rule

Some studies include an explicit `Sample Type` factor (e.g. `Sample Type:Study Sample`, `Sample Type:QC`). When this key is present it overrides all other factor values.

**Rationale:** A sample can belong to `Pool Group:Pool 1` (a biological batching variable) while being declared `Sample Type:Study Sample`. Without this rule, the value `Pool 1` incorrectly triggers the `pool` keyword.

**Case that prompted the rule — AN001414 (ST000826):**

| Class label | Old result | With priority rule |
|-------------|------------|-------------------|
| `Pool Group:Pool 1 \| Sample Type:Study Sample` | QC (false positive) | **OK** (study sample) |
| `Pool Group:Pool 2 \| Sample Type:Study Sample` | QC (false positive) | **OK** (study sample) |
| `Pool Group:Pool 1 \| Sample Type:Pool` | QC | QC ✓ |
| `Pool Group:CHEAR Reference \| Sample Type:Reference Sample` | QC | QC ✓ |

**Priority rule keys recognised (case-insensitive):** `sample type`, `sampletype`, `sample_type`, `type`

**Study-sample values that suppress exclusion:** `study sample`, `study_sample`, `subject`, `biological sample`

If none of these override keys are present, fall back to full value-scanning.

---

## 5. Decision Logic (Full Algorithm)

```
is_qc_blank(sample_id, class_label):

  1. Check sample_id (col 0):
       sid = sample_id.strip().lower()
       if _hits(sid): return True

  2. Parse class_label (col 1):
       split on '|' → list of pairs
       for each pair: split on first ':' → (key, value)
       build dict: parsed[key.lower()] = value.lower()

  3. Priority check — if an explicit Sample Type key exists:
       for key in {sample type, sampletype, sample_type, type}:
           if key in parsed:
               if parsed[key] in {study sample, study_sample,
                                   subject, biological sample}:
                   return False      ← explicitly a study sample
               else:
                   return _hits(parsed[key])

  4. Fallback — scan all values:
       for val in parsed.values():
           if _hits(val): return True
       return False

_hits(text):
  if any substring_kw in text: return True
  if any letter_boundary_pattern matches text: return True
  return False
```

---

## 6. Known Remaining False Positives

Despite the three-layer design, a small number of analyses are entirely excluded (100% of rows flagged) due to biological "pool" terminology in sample names:

| Analysis | Example sample name | Actual meaning | Verdict |
|----------|--------------------|--------------|---------|
| AN002856 (ST001753) | `Pooled Human Plasma 1` | Pooled reference QC | **Correctly excluded** |
| AN002921 (ST001799) | `Blank_Buffer_M419_1` / `type:calibration std` | Calibration standard | **Correctly excluded** |
| AN003904 (ST002397) | `u0.027_pool1_PS` with `Treatment:Glucose` | Biological sample from a pooled treatment group | **False positive** |
| AN005618 (ST003419) | `WholeCell HeLa Wild-Type pool MYC R` | Biological replicate pool | **False positive** |
| AN005623 (ST003424) | `HeLa CLN8 KO pool + CLN8-HA Replica` | Biological replicate pool | **False positive** |

The term `pool` cannot be disambiguated without additional context about whether it refers to:
- A **QC pooled reference** (mixture of all study samples → exclude), or
- A **biological pooled replicate** (technical pooling of biological replicates → keep)

In the absence of an explicit `Sample Type` factor, this distinction requires study-level metadata not available in the datatable. The false-positive rate is small (3 of 992 affected analyses, 0.3%) and the affected analyses are very small (≤57 rows).

---

## 7. Empirical Impact: With vs Without Filter

Applied across all **4,872 valid datatable analyses** (4,872 / 6,696 total, those with `datatable_valid_present = 1`).

### 7a. Summary

| Metric | Without filter | With filter | Absolute diff | Relative diff |
|--------|---------------|-------------|---------------|---------------|
| Analyses with ≥1 QC/blank removed | — | **992** (20.4%) | — | — |
| Total QC/blank samples removed | — | **19,908** | — | 4.6% of all rows |
| Median samples / analysis | 36 | **35** | −1 | −2.8% |
| Mean samples / analysis | 88.4 | **84.3** | −4.1 | −4.6% |
| Total sample rows (sum) | 430,729 | **410,821** | −19,908 | −4.6% |
| Min samples (post-filter) | 1 | 0 | — | — |
| Max samples | 3,501 | 2,941 | −560 | −16.0% |

### 7b. Percentile breakdown

| Percentile | Without filter | With filter | Diff |
|-----------|---------------|-------------|------|
| p10 | 10 | 10 | 0 |
| p25 | 18 | 16 | −2 |
| p50 (median) | 36 | 35 | −1 |
| p75 | 80 | 75 | −5 |
| p90 | 199 | 196 | −3 |
| p95 | 348 | 322 | −26 |

### 7c. Analyses most affected by absolute QC count

| Study | Analysis | Total rows | QC removed | Study samples | % removed |
|-------|----------|-----------|-----------|---------------|-----------|
| ST002866 | AN004698 | 3,501 | 560 | 2,941 | 16.0% |
| ST002866 | AN004699 | 3,501 | 560 | 2,941 | 16.0% |
| ST003177 | AN005215 | 2,492 | 302 | 2,190 | 12.1% |
| ST003177 | AN005216 | 2,492 | 302 | 2,190 | 12.1% |
| ST002700 | AN004376 | 1,089 | 263 | 826 | 24.2% |

### 7d. Analyses most affected by percentage

| Study | Analysis | Total rows | QC removed | Study samples | % removed |
|-------|----------|-----------|-----------|---------------|-----------|
| ST001799 | AN002921 | 57 | 57 | 0 | 100% |
| ST001753 | AN002856 | 8 | 8 | 0 | 100% |
| ST002397 | AN003904 | 9 | 9 | 0 | 100%* |
| ST003419 | AN005618 | 37 | 37 | 0 | 100%* |
| ST003514 | AN005769 | 61 | 51 | 10 | 83.6% |

*Likely false positives (biological pool terminology in sample names).

### 7e. Interpretation

The filter produces negligible change at the median (36 → 35 samples) and a modest 4.6% reduction at the mean. The distribution shift is concentrated at the upper tail (p95: 348 → 322). This indicates that QC samples represent a small but non-trivial fraction in a minority of large studies.

For figure panels reporting sample counts, using the filtered value is methodologically correct and is the reported number. The median and distribution shape are not materially affected.

---

## 8. Implementation Reference

The final filter function used for all sample-count analyses is reproduced below for reproducibility.

```python
import re

SUBSTRING_KWS = (
    'blank', 'pool', 'reference', 'solvent', 'quality control', 'pooled qc',
    'calibration standard', 'system suitability', 'process blank',
    'method blank', 'reagent blank', 'drift',
)
LB_PATTERNS = [
    re.compile(r'(?<![a-zA-Z])' + re.escape(k), re.IGNORECASE)
    for k in ('qc', 'nist', 'ltr', 'sst')
]
STUDY_SAMPLE_VALS = {'study sample', 'study_sample', 'subject', 'biological sample'}
SAMPLE_TYPE_KEYS  = {'sample type', 'sampletype', 'sample_type', 'type'}

def _hits(val: str) -> bool:
    if any(k in val for k in SUBSTRING_KWS):
        return True
    if any(p.search(val) for p in LB_PATTERNS):
        return True
    return False

def is_qc_blank_label(label: str) -> bool:
    """Check the Class/group column value (pipe-delimited FactorName:Value string)."""
    pairs = [p.strip() for p in label.split('|')]
    parsed = {}
    for pair in pairs:
        if ':' in pair:
            k, v = pair.split(':', 1)
            parsed[k.strip().lower()] = v.strip().lower()
    # Priority: explicit Sample Type key overrides all other factors
    for key in SAMPLE_TYPE_KEYS:
        if key in parsed:
            val = parsed[key]
            if val in STUDY_SAMPLE_VALS:
                return False
            return _hits(val)
    # Fallback: scan all factor values
    for val in parsed.values():
        if _hits(val):
            return True
    return False

def is_qc_blank(sample_id: str, label: str) -> bool:
    """Union of sample-name and class-label signals."""
    if _hits(sample_id.strip().lower()):
        return True
    return is_qc_blank_label(label)
```

---

## 9. Validation Test Cases

All cases below must pass before using the filter in any downstream analysis.

| Label / Sample ID | Expected | Reason |
|-------------------|----------|--------|
| `Treatment:Metformin 100mg/kg (oral administration)` | **False** | `nist` inside `administration` — letter-boundary prevents match |
| `Treatment:Saline (oral administration)` | **False** | Same — `nist` in `administration` |
| `Factor:NIST_Recovery` | **True** | NIST reference, value-side match |
| `type:Nistplasma` | **True** | NIST reference, value starts with `nist` |
| `treatment:pooled QC` | **True** | Explicit QC pool |
| `Treatment:Blank` | **True** | Blank sample |
| `Sample source:QC \| Condition:-` | **True** | QC source |
| `Pool Group:Pool 1 \| Sample Type:Study Sample` | **False** | `Sample Type:Study Sample` priority override |
| `Pool Group:Pool 2 \| Sample Type:Pool` | **True** | `Sample Type:Pool` → QC |
| `treatment:Total Pool1 \| pool aliquot:yes` | **True** | `pool` in value `Total Pool1` |
| `treatment:DCIS \| pool aliquot:no` | **False** | Value of `pool aliquot` is `no` — not QC |
| `Sample source:standard reference material \| Factor:Quality Control` | **True** | `quality control` in value |
| sample_id=`Blank1_PC3_LC`, any label | **True** | `blank` in sample name |
| sample_id=`D01P`, label=`Factor:NIST_Recovery` | **True** | Caught by label channel |
| sample_id=`1`, label=`Sample source:blank` | **True** | Caught by label channel |
