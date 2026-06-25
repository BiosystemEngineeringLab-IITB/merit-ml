# Feature Name Extraction — Design Decisions

**Purpose:** Document exactly which rows/columns constitute metabolite features in each of the three MW data sources, including all edge cases encountered in the real data and the decisions taken to handle them. All counts refer to the canonical dump at `/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version`.

---

## 1. datatable (`{ST}/{AN}/tabular/{AN}_datatable.tsv`)

### Orientation
Samples × Features — each row is one sample, each column from index 2 onward is one feature.

### Metadata columns (excluded)

| Position | Header value | Always? | Decision |
|----------|-------------|---------|----------|
| col 0 | `Samples` | Yes — confirmed across all 4,872 valid files | Skip by position |
| col 1 | `Class` | Yes — confirmed across all 4,872 valid files | Skip by position |

### Feature columns
`header[2:]` — all columns from index 2 onward with non-empty names are counted as features.

### Edge cases found in real data

| Pattern | Example | Count (all 4,872 files) | Decision |
|---------|---------|------------------------|----------|
| Adduct suffix `[+-]\d+\.\d+$` | `Leucine-2.2727776`, `Isobutyrylcarnitine+5.3823495` | 8,094 feature names | **Count raw** — suffix is part of the deposited feature identity; deduplication would require chemical matching beyond scope |
| Replicate suffix `_R\d+$` | `Aspartic acid_R1`, `Aspartic acid_R2` | 3,220 feature names | **Count raw** — each column is a distinct measurement even if it represents the same metabolite |
| mz/RT token `^\d+\.?\d*[_/]\d+\.?\d*$` | — | 0 | Not present — datatable is curated named metabolites only |
| m/z-only `^\d{2,4}\.\d{3,6}$` | — | 0 | Not present |
| NMR bin `X...Y` | — | 0 | Not present |
| Empty feature names | — | 0 | Not present — all column names are non-empty after strip |

### Summary rule
> Feature count = `len([h for h in header[2:] if h.strip()])`

No header rows inside the data body need to be skipped — the file is purely row-oriented.

---

## 2. mwtab (`{ST}/{AN}/json/{AN}_mwtab.txt`)

### Orientation
Features × Samples — inside a recognised metabolite data block. Each row from index 2 onward represents one feature; col 0 of that row is the feature name.

### Block detection
Five recognised block types:

| Block start tag | Block end tag |
|----------------|--------------|
| `MS_METABOLITE_DATA_START` | `MS_METABOLITE_DATA_END` |
| `NMR_METABOLITE_DATA_START` | `NMR_METABOLITE_DATA_END` |
| `NMR_BINNED_DATA_START` | `NMR_BINNED_DATA_END` |
| `EXTENDED_MS_METABOLITE_DATA_START` | `EXTENDED_MS_METABOLITE_DATA_END` |
| `EXTENDED_NMR_METABOLITE_DATA_START` | `EXTENDED_NMR_METABOLITE_DATA_END` |

In the 200-file survey: 0 files had multiple blocks. MS blocks: 195/200; NMR blocks: 5/200.

### Metadata rows (excluded)

| Row index in block | Expected col 0 value | Always? | Decision |
|--------------------|---------------------|---------|----------|
| Row 0 | `Samples` | 176/200 — 3 files have `metabolite_name` or `Metabolite Name` | **Skip by position** (row 0 is always the sample-ID header regardless of label) |
| Row 1 | `Factors` | 178/200 — 1 file had a metabolite name (`(S)-LACTATE`) as row 1 | **Conditional skip**: skip row 1 only if `col0.strip() == "Factors"`; otherwise count from row 1 |

### Feature rows
All rows from index 2 onward (or index 1 if Factors row is absent) where col 0 is non-empty. Features are collected into a **set** to deduplicate identical names across multiple block reads (though multi-block files were not observed in practice).

### Edge cases found in real data

| Pattern | Example | Decision |
|---------|---------|----------|
| Adduct suffix `[+-]\d+\.\d+$` | `25-hydroxyvitaminD225-...(beta-glucuronide)+2.0046735`, `Leucine-2.2727776` | **Count raw** — same policy as datatable |
| Row 0 label variability (`metabolite_name`, `Metabolite Name`) | 3/200 files | Handled by positional skip — irrelevant to feature extraction |
| `Factors` row absent (row 1 is a metabolite) | 1/200 — `(S)-LACTATE` at row 1 | Conditional check: if `col0 != "Factors"`, include row 1 as a feature |
| NMR binned features `X...Y` | `0.46...0.52`, `0.52...0.54` | **Count as features** — they are valid spectral bins, not metadata. Appear in `NMR_BINNED_DATA` blocks |
| mwtab `min=0` analyses | Files with block present but no data rows after Samples+Factors | Retained in distribution (min=0); excluded from violin (>0 filter applied) |
| Numeric sample IDs in row 0 | `421724`, `421728` | Not relevant to feature extraction — row 0 is always skipped |

### NMR binned data note
`NMR_BINNED_DATA` blocks produce features with chemical shift range labels (`0.46...0.52`). These are legitimate quantitative features (integrated peak areas per spectral bin) and are counted as features. They are not named metabolites — this is intentional for a raw count analysis.

### Summary rule
```
in_block = False; rows_seen = 0; feat_set = set()
for line:
    if BLOCK_START: in_block=True; rows_seen=0; continue
    if BLOCK_END:   in_block=False; continue
    if in_block:
        rows_seen += 1
        if rows_seen == 1: continue                          # Samples header
        col0 = line.split('\t')[0].strip()
        if rows_seen == 2 and col0 == 'Factors': continue   # Factors row (conditional)
        if col0: feat_set.add(col0)
feature_count = len(feat_set)
```

---

## 3. untarg_data (`{ST}/{AN}/tabular/{AN}_untarg_data.tsv`)

### Orientation
Samples × Features — same layout as datatable.

### Metadata columns (excluded)

| Position | Header value | Always? | Decision |
|----------|-------------|---------|----------|
| col 0 | `Samples` | Yes — confirmed across all surveyed files | Skip by position |
| col 1 | `group` | Yes — confirmed invariant across 200 surveyed files | Skip by position |

### Feature columns
`header[2:]` — all columns from index 2 onward.

### Feature types found in real data

| Type | Pattern | Example | Decision |
|------|---------|---------|----------|
| mz/RT token | `^\d+\.?\d*[_/]\d+\.?\d*$` | `180.063_2.75`, `70.065/0.83` | **Count** — valid untargeted feature identifier |
| m/z-only (no RT) | `^\d{2,4}\.\d{3,6}$` | `100.0239`, `108.9018` | **Count** — high-resolution exact mass feature; no RT because DI-MS or rt not deposited |
| Replicate peak suffix `_R\d+$` | `97.0291_R1`, `97.0291_R2`, `100.0756_R3` | **Count raw** — each replicate column is deposited as a separate feature; deduplication would require choosing a representative replicate |
| Named metabolites | `(-)-Annonaine`, `(+)-Bornane-2,5-dione_1` | Some studies annotated their untarg data before depositing | **Count** — these are valid feature identifiers; `_1`, `_2` suffixes on named metabolites are treated as separate features (same policy as replicate suffix) |
| NMR shifts | `0.820`, `1.233` | Not observed as dominant pattern in untarg_data files | Would count if present |

### Critical distinction: m/z-only vs NMR shift
Both look like `NNN.NNN`. Discrimination:
- **m/z-only** (MS): values typically 50–2,000 Da, e.g. `100.0239`, `983.5951`
- **NMR chemical shift**: values 0–12 ppm, e.g. `7.24`, `3.56`

For the current raw count analysis, both are counted as features regardless of type.

### Summary rule
> Feature count = `len([h for h in header[2:] if h.strip()])`

---

## 4. Counting policy: raw vs deduplicated

All feature counts reported in the figure and manuscript use **raw counts** — each column (datatable, untarg_data) or each row in the block (mwtab) is counted once, regardless of whether it shares a base name with another feature after stripping adduct or replicate suffixes.

**Rationale:** The raw count reflects what a practitioner sees when they load the matrix — the actual dimensionality of the feature space before any harmonisation step. Deduplication would require:
- For adduct suffixes: stripping `[+-]\d+\.\d+$` and taking unique base names
- For replicate suffixes: stripping `_R\d+$` and taking unique base names
- Cross-source matching: not attempted here

These could be computed as a separate "deduplicated" count in a future analysis.

---

## 5. Final counts

| Source | n analyses | Median (raw) | Mean (raw) | Min | Max |
|--------|-----------|-------------|-----------|-----|-----|
| datatable | 4,872 | 93 | 195 | 1 | 9,007 |
| mwtab | 4,990 | 101 | 312 | 0 | 44,005 |
| untarg_data | 1,885 | 2,379 | 6,890 | 1 | 615,898 |

mwtab analyses with 0 features (n=45, header-only blocks) are excluded from the violin plot but retained in the table.

The 25× difference in median feature count between Tier 1 (datatable/mwtab, ~93–101) and Tier 2 (untarg_data, ~2,379) reflects the fundamental difference between curated named metabolite tables and raw untargeted peak tables.
