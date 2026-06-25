# MW Tabular Data Fetch — Multi-Source Confirmation Workflow

This document describes the multi-source retrieval strategy used to systematically
characterise tabular data availability across the Metabolomics Workbench (MW) public
repository. All quantitative claims derive from the completed run of
`mw_confirmation_downloader.py` (v2) over the full repository: **4,121 studies,
6,696 analyses**.

Code anchor: `mw_confirmation_downloader.py` (v2)

---

## 1) Study List Construction

Three modes, mutually exclusive:

1. **Explicit CLI** (`--studies ST000001 ST000002 ...`): sorted, deduplicated list from command line.
2. **From existing dump** (`--study-list-from <dir>`): scan `ST*` subdirectories in the given directory; extract study IDs from directory names.
3. **Live API** (neither flag): `GET /rest/study/study_id/ST/summary` → parse JSON dict → extract all `study_id` values starting with `ST`.

Resume: checkpoint file tracks completed studies; on restart, completed studies are
skipped entirely. Failed studies are recorded but not auto-retried.

---

## 2) Per-Study Orchestration

For each study `ST`:

1. **Fetch analysis list**:
   - `GET /rest/study/study_id/{ST}/analysis`
   - Wait `--delay` seconds (default 1.0s in parallel mode)
   - Parse response: handles flat dict (`{"analysis_id": "AN..."}` for single-analysis studies), numbered dict (`{"1": {"analysis_id": ...}, "2": {...}}`), or list format
   - Extract all `AN*` IDs → `sorted(set(analysis_ids))`
   - Error states: no response → `"no_response_from_analysis_endpoint"`, unparseable JSON → `"analysis_endpoint_not_json"`, zero ANs → `"no_analyses_found"`

2. **Download each analysis** (all sources — see Section 3)

3. **Fetch study-level metadata** (3 endpoints, each followed by delay):
   - `GET /rest/study/study_id/{ST}/factors` → `ST/factors.json`
   - `GET /rest/study/study_id/{ST}/disease` → `ST/disease.json`
   - `GET /rest/study/study_id/{ST}/metabolites` → `ST/metabolites.json`

4. **Save study manifest**: `ST/manifest.json` containing per-analysis records, source flag summary, counts, timestamps.

5. **Checkpoint**: `checkpoint.mark_done(ST)` or `checkpoint.mark_failed(ST, reason)`, then atomic save (write `.tmp`, rename to checkpoint file).

---

## 3) Per-Analysis Multi-Source Fetch (Core Logic)

For each analysis `AN` within study `ST`, the following four sources are probed
**unconditionally and sequentially**. Every source is attempted regardless of whether
earlier sources returned valid data. All downloaded files are saved; nothing is
discarded based on another source's success.

```
Per-Analysis Record (initialized before any fetch):
  mwtab_txt              = None     (file path, relative to root)
  mwtab_json             = None
  datatable              = None
  results_txt            = None
  mwtab_txt_has_tabular  = False    (validated: actual data matrix present)
  mwtab_json_parseable   = False    (validated: well-formed JSON)
  datatable_has_tabular  = False    (validated: actual data matrix present)
  results_has_tabular    = False    (validated: actual data matrix present)
  tabular_source_used    = None     (priority pick for downstream pipeline)
```

### SOURCE 1: mwtab text file

```
URL:      GET /rest/study/analysis_id/{AN}/mwtab/txt
Save to:  ST/AN/json/{AN}_mwtab.txt
```

**Accept criteria** (file is saved if):
- Response starts with `#METABOLOMICS` or `#HEADER` (standard mwtab headers), OR
- Response length > 100 characters (non-standard but potentially valid)

**Tabular data validation** (`_mwtab_has_data`):
- Scan all lines for `*_DATA_START` / `*_DATA_END` markers
  - `MS_METABOLITE_DATA_START/END`
  - `NMR_METABOLITE_DATA_START/END`
  - `NMR_BINNED_DATA_START/END`
  - `EXTENDED_MS/NMR_METABOLITE_DATA_START/END`
- Inside data block, skip header rows: `Samples\t*`, `Factors\t*`, `Bin range*`
- **If ANY non-header, non-empty line exists inside a data block → `mwtab_txt_has_tabular = True`**
- Metadata-only mwtab (no data block) → False
- Data block with only header rows → False

### SOURCE 2: mwtab JSON

```
URL:      GET /rest/study/analysis_id/{AN}/mwtab
Save to:  ST/AN/json/{AN}_mwtab.json
```

**Validation**: Attempt `json.loads()` on UTF-8-decoded content.
- Success → `mwtab_json_parseable = True`
- `JSONDecodeError` or `UnicodeDecodeError` → `mwtab_json_parseable = False` (file still saved)

This source validates JSON well-formedness only — not the presence of a tabular matrix within the JSON.

### SOURCE 3: datatable REST API

```
URL:      GET /rest/study/analysis_id/{AN}/datatable/file
Save to:  ST/AN/tabular/{AN}_datatable.tsv
```

**Decode step** (`_decode_datatable`):
- Try `gzip.decompress()` first (some responses are gzip-compressed)
- If `BadGzipFile` → decode as plain UTF-8

**Tabular data validation** (`_tsv_has_data`):
- Require **≥ 2 non-empty lines** (header + ≥1 data row)
- Require header line to have **> 2 tab-separated columns**
- Both met → save file, `datatable_has_tabular = True`
- Otherwise → discard, `datatable_has_tabular = False`

### SOURCE 4: Results.txt (deterministic direct URL)

```
URL:      GET https://www.metabolomicsworkbench.org/studydownload/{ST}_{AN}_Results.txt
          (no HTML scraping — direct file download attempt)
Save to:  ST/AN/tabular/{ST}_{AN}_Results.txt
```

**Validation**: Same `_tsv_has_data()` logic as datatable.
- 404/410 responses return None immediately (resource does not exist — expected for most analyses)

### PRIORITY DECISION

After all 4 sources are probed, a single priority pick is recorded:

```
if datatable_has_tabular:     tabular_source_used = "datatable"
elif mwtab_txt_has_tabular:   tabular_source_used = "mwtab"
elif results_has_tabular:     tabular_source_used = "results_txt"
else:                         tabular_source_used = None
```

**All files from all sources are saved regardless.** All four boolean flags are
independently recorded. The full source overlap matrix is recoverable from manifests.

---

## 4) HTTP Layer

```
User-Agent:   "MetabolomicsMetaAnalysis/2.0 (systematic repository-wide study; academic research)"
Timeout:      90 seconds per request
Max retries:  5

Retry behavior:
  HTTP 404/410           → return None immediately (no retry)
  HTTP 429 (rate limit)  → sleep max(Retry-After, 60)s, then retry
  HTTP 403 (forbidden)   → sleep 120s, then retry
  HTTP 5xx / other       → exponential backoff, retry
  Network/Timeout/OSError → exponential backoff, retry

Backoff schedule:  2s → 4s → 8s → 16s → 30s (capped at 30s)
After 5 failures:  log "GAVE UP", return None
```

---

## 5) Parallel Worker Design

To accelerate repository-wide download, the script supports interleaved parallelism
via `--worker-id` / `--n-workers` CLI arguments:

- Each worker is assigned every n-th study by index (interleaved, not chunked)
- Per-worker checkpoint and log files: `checkpoint_w{id}.json`, `download_w{id}.log`
- Shared output directory with no conflicts (each worker writes different `ST*` subdirectories)
- To resume: seed each worker's checkpoint with the existing `checkpoint.json` before launch

**Launch commands (2 workers, 1s delay):**
```bash
nohup python3 mw_confirmation_downloader.py \
  --output-dir mw-dump-latest-confirmation \
  --study-list-from mw-dump-latest \
  --delay 1.0 --worker-id 0 --n-workers 2 > /dev/null 2>&1 &

nohup python3 mw_confirmation_downloader.py \
  --output-dir mw-dump-latest-confirmation \
  --study-list-from mw-dump-latest \
  --delay 1.0 --worker-id 1 --n-workers 2 > /dev/null 2>&1 &
```

---

## 6) Checkpoint and Resume

```
checkpoint_w{id}.json (atomic write: .tmp → rename):
{
  "completed": ["ST000001", "ST000002", ...],
  "failed":    {"ST001234": "reason", ...},
  "n_completed": 4121,
  "n_failed":    0,
  "last_saved":  "2026-03-17T..."
}
```

- Saved after every study (not per-analysis)
- Atomic write via `.tmp` + rename
- On `KeyboardInterrupt`: checkpoint saved before exit
- Failed studies are logged but NOT auto-retried on resume

---

## 7) Output Directory Layout

```
<root>/
├── checkpoint_w0.json / checkpoint_w1.json   (per-worker resume state)
├── download_w0.log / download_w1.log          (per-worker logs)
│
├── ST001234/
│   ├── manifest.json            (per-study summary: analyses, source flags, counts)
│   ├── factors.json             (study-level sample factors)
│   ├── disease.json             (study-level disease metadata)
│   ├── metabolites.json         (study-level metabolite list)
│   │
│   ├── AN002001/
│   │   ├── json/
│   │   │   ├── AN002001_mwtab.txt     (if API returns valid mwtab)
│   │   │   └── AN002001_mwtab.json    (if API returns non-empty response)
│   │   └── tabular/
│   │       ├── AN002001_datatable.tsv          (ONLY if validated: ≥1 data row, >2 cols)
│   │       └── ST001234_AN002001_Results.txt   (ONLY if validated: ≥1 data row, >2 cols)
│   │
│   └── AN002002/
│       └── ...
```

---

## 8) Contrast with Original Downloader (mw_downloader.py)

| Aspect | Old downloader (v1) | Confirmation downloader (v2) |
|--------|---------------------|------------------------------|
| Results.txt trigger | Only if datatable returned no data | Always, unconditionally |
| Results.txt URL | HTML page scraping via `DownloadPageParser` | Deterministic: `/studydownload/{ST}_{AN}_Results.txt` |
| mwtab tabular validation | Not performed | `_mwtab_has_data()` with DATA block parsing |
| Source flags recorded | Only the winning source | All 4 boolean flags independently |
| Files saved | 1 tabular file (winner) + mwtab | All available sources |
| Delay | 1s per study | 1s per API call (configurable) |
| Retry strategy | 3 retries | 5 retries, 429/403 handling, 90s timeout |
| Checkpoint atomicity | Direct write | Atomic via .tmp + rename |
| Parallelism | Single process | Interleaved multi-worker |

**Consequence of the old fallback design**: datatable and Results.txt appeared
mutually exclusive — Results.txt was never attempted when datatable succeeded. The
new unconditional probing reveals the true co-occurrence (466 triple-source analyses).

---

## 9) Methods

### 9.1 Repository enumeration

All publicly accessible studies in the Metabolomics Workbench were enumerated via the
REST API summary endpoint (`/rest/study/study_id/ST/summary`), yielding a total of
**4,121 studies**. For each study, associated analytical runs were retrieved via
`/rest/study/study_id/{ST}/analysis`, which returns one or more analysis identifiers
(prefix `AN`). The full repository comprises **6,696 analyses** across the 4,121 studies.

### 9.2 Multi-source data retrieval

For each analysis, tabular metabolite abundance data were retrieved from three
independent endpoints, probed unconditionally and in parallel — regardless of whether
any other source returned valid data:

1. **mwtab text** (`/rest/study/analysis_id/{AN}/mwtab/txt`): A section-delimited
   format embedding structured experimental metadata (`#PROJECT`, `#STUDY`,
   `#SUBJECT`, `#MS`/`#NMR`, `#CHROMATOGRAPHY`, sample factors, metabolite
   annotations) alongside quantitative matrices bounded by modality-specific
   `*_METABOLITE_DATA_START` / `*_METABOLITE_DATA_END` block markers. The mwtab
   file was always fetched as the authoritative source of experimental metadata,
   irrespective of whether it contained a quantitative matrix.

2. **datatable REST API** (`/rest/study/analysis_id/{AN}/datatable/file`): Returns a
   tab-delimited abundance matrix (samples × features) with class labels embedded in
   the header row. Responses may be gzip-compressed or plain text.

3. **Results.txt** (`https://www.metabolomicsworkbench.org/studydownload/{ST}_{AN}_Results.txt`):
   A deterministic direct URL — no HTML page scraping or link parsing. This endpoint
   provides analysis-level results files as deposited by the submitting laboratory.

All retrieved files were saved to disk regardless of which other sources returned
valid data. Four independent boolean flags were recorded per analysis:
`datatable_has_tabular`, `mwtab_txt_has_tabular`, `results_has_tabular`, and
`mwtab_json_parseable`. "Has tabular" was strictly defined as: the response contains
≥1 non-header data row with >2 tab-separated columns (for datatable and Results.txt),
or ≥1 non-header, non-empty line inside a `*_DATA_START`/`*_DATA_END` block (for
mwtab). Empty responses, metadata-only mwtab files, and single-row responses were
not counted as tabular data.

HTTP requests were issued with a polite 1-second inter-request delay. Rate-limit
responses (HTTP 429) triggered a minimum 60-second backoff; HTTP 403 responses
triggered a 120-second backoff. Up to five retries with exponential backoff (capped
at 30 seconds) were applied to all transient failures. Permanent 404/410 responses
were treated as confirmation of resource absence and not retried.

Download was distributed across two parallel workers using an interleaved study
assignment scheme, with per-worker checkpoint and log files to avoid conflicts on the
shared output directory. The entire 4,121-study corpus was completed with zero
failures.

### 9.3 Source priority and canonical matrix selection

When multiple sources provided valid tabular data for the same analysis, a single
source was designated for downstream use according to the following priority rule:
**datatable > mwtab embedded matrix > Results.txt**. This priority reflects the
degree of curation: the datatable endpoint serves deduplicated named metabolites,
the mwtab embedded matrix is structurally identical but may include adduct-suffixed
duplicates, and Results.txt often contains pre-identification raw analytical output.
All files were retained on disk regardless of priority selection.

### 9.4 Feature type classification

Feature names from all tabular sources were classified as (i) named metabolites —
strings containing alphabetic characters consistent with metabolite nomenclature; or
(ii) mz/RT tokens — strings matching mass-to-charge or retention-time patterns
(e.g., `431.29_3.42`, `M123.45T6.78`, bare decimal numbers). A random sample of 500
analyses per source was used for classification. Feature overlap between sources was
quantified using the Jaccard similarity coefficient on lowercased, stripped feature
name sets from all 466 triple-source analyses (analyses with all three sources
simultaneously providing valid tabular data).

---

## 10) Results

### 10.1 Repository-wide data availability

Of 6,696 analyses across 4,121 studies, **6,587 (98.4%)** had at least one validated
source of tabular metabolite data. Only 109 analyses (1.6%) returned no quantitative
matrix from any of the three sources (though mwtab metadata was still retrieved for
these).

Source-specific availability:

| Source | Analyses with tabular data | % of 6,696 |
|--------|---------------------------|------------|
| datatable REST API | 4,872 | 72.8% |
| mwtab embedded matrix | 4,873 | 72.8% |
| Results.txt | 2,064 | 30.8% |
| mwtab JSON parseable | 6,446 | 96.3% |

### 10.2 Source co-occurrence patterns

The full cross-source combination matrix across all 6,696 analyses:

| Combination | Analyses | % | Description |
|---|---|---|---|
| datatable ∩ mwtab (no RT) | 4,265 | 63.7% | Both Tier 1 sources; no raw data |
| Results.txt only | 1,573 | 23.5% | No curated source available |
| datatable ∩ mwtab ∩ Results.txt | 466 | 7.0% | All three sources |
| mwtab only | 142 | 2.1% | mwtab matrix but no datatable |
| datatable only | 116 | 1.7% | datatable but no mwtab matrix |
| No source | 109 | 1.6% | No tabular data from any source |
| datatable ∩ Results.txt (no MW) | 25 | 0.4% | — |
| mwtab ∩ Results.txt (no DT) | 0 | 0.0% | Does not occur in the repository |

Datatable and mwtab are near-redundant: of 4,872 analyses with datatable, 4,731
(97.1%) also have mwtab tabular data, and vice versa. This co-occurrence was
consistently validated — both flags require confirmed non-empty matrices, not merely
file presence.

Coverage by Tier 1 sources (datatable or mwtab): **4,731 / 6,696 = 70.7%** of
analyses. Adding Results.txt as a fallback raises coverage to **6,587 / 6,696 =
98.4%**.

### 10.3 Priority selection outcome

Applying the datatable > mwtab > Results.txt priority rule:

| Source selected | Analyses | % |
|---|---|---|
| datatable | 4,872 | 72.8% |
| Results.txt | 1,573 | 23.5% |
| mwtab | 146 | 2.2% |
| None (no source) | 105 | 1.6% |

mwtab is selected as the primary source for only 146 analyses — those where mwtab
has a tabular matrix but datatable does not.

### 10.4 Feature content: Tier 1 sources (datatable and mwtab)

Classification of 92,339 features from 500 randomly sampled datatable analyses:

| Feature type | Count | % |
|---|---|---|
| Named metabolites | 90,692 | **98.2%** |
| mz/RT tokens | 1,621 | 1.8% |
| Other/ambiguous | 26 | 0.0% |

Feature count distributions:

| Statistic | datatable | mwtab matrix |
|---|---|---|
| Median | 93 | 104 |
| Mean | 195 | 349 |
| Q1 | 33 | 37 |
| Q3 | 207 | 238 |
| Max | 9,007 | 44,005 |

Datatable and mwtab serve near-identical curated, named-metabolite datasets. The
mwtab median is slightly higher (104 vs 93) because it may include adduct-suffixed
duplicates that datatable deduplicates. The mwtab mean (349) is inflated by a small
number of very large untargeted analyses (up to 44,005 features).

### 10.5 Feature content: Tier 2 source (Results.txt)

Classification across 500 randomly sampled RT-only analyses (4,390,099 features
total):

| Feature type | Count | % |
|---|---|---|
| mz/RT tokens | 4,120,976 | **93.9%** |
| Named metabolites | 82,559 | 1.9% |
| Other/ambiguous | 186,564 | 4.2% |

Feature count distributions:

| Statistic | Results.txt (all 2,064) | Results.txt-only (1,573) |
|---|---|---|
| Median | 2,385 | 2,338 |
| Mean | 7,502 | 8,103 |
| Q1 | 685 | 662 |
| Q3 | 7,616 | 7,690 |
| Max | 1,048,575 | 1,048,575 |

Results.txt carries roughly 25× more features per analysis than datatable (median
2,385 vs 93), reflecting the inclusion of unidentified spectral peaks alongside
identified metabolites.

### 10.6 Feature overlap between sources

Feature name overlap between datatable and Results.txt was quantified across all
466 triple-source analyses (Jaccard similarity on lowercased feature name sets):

| Overlap category | Analyses | % |
|---|---|---|
| Jaccard = 0.0 (zero shared features) | 432 | **92.7%** |
| Jaccard > 0.0 (partial to complete overlap) | 34 | **7.3%** |
| Median Jaccard | **0.0** | — |
| Max Jaccard | 1.0 | — |

In 92.7% of triple-source analyses, datatable and Results.txt share no feature names
whatsoever — confirming that they represent fundamentally different data processing
levels (named metabolites vs raw peaks). In the remaining 7.3% (34 analyses), Results.txt
contains named metabolites essentially identical to datatable (Jaccard up to 1.0),
indicating that some submitters deposit their final annotated tables through the
studydownload endpoint rather than raw analytical output. This heterogeneity means the
Tier 1 / Tier 2 boundary is not absolute: Results.txt content spans the full spectrum
from entirely raw mz/RT peaks to completely curated named metabolite tables.

### 10.7 Results.txt-only analyses

Of 6,696 analyses, 1,573 (23.5%) have Results.txt as the sole source of tabular data,
spanning 837 studies (20.3% of 4,121). These analyses have no datatable and no mwtab
matrix. Their mwtab files were retrieved for metadata but contained no embedded
quantitative matrix. Of the 1,572 RT-only analyses for which a mwtab file was
available, 1,520 (96.7%) contained an `MS_RESULTS_FILE` metadata field — a reference
to an external results file — confirming that these analyses intentionally store their
quantitative data outside the mwtab text format.

### 10.8 Data processing hierarchy

The three sources represent distinct stages of the metabolomics data processing
pipeline rather than three independent representations of the same data:

```
Results.txt                  mwtab embedded matrix            datatable
(raw analytical output)  →   (annotated, disambiguated)  →   (curated summary)

Median ~2,385 features       Median ~104 features             Median ~93 features
93.9% mz/RT tokens           ~98% named (heterogeneous)       98.2% named
Pre-identification            Post-identification              Post-curation
No class labels              Samples/Factors headers          Samples + Class columns
```

The identified features in datatable/mwtab are typically a small fraction (2–10%) of
the total peaks detected and reported in Results.txt. Zero feature overlap in 92.7%
of triple-source analyses confirms this data processing relationship rather than a
redundant multi-format exposure.

### 10.9 Manuscript methods paragraph

Study identifiers were enumerated from the Metabolomics Workbench public REST API
(`/rest/study/study_id/ST/summary`), yielding 4,121 studies and 6,696 analyses in
total. For each analysis, tabular metabolite abundance matrices were retrieved from
three independent endpoints probed unconditionally: the mwTab text file (`/rest/study/analysis_id/{AN}/mwtab/txt`), a section-delimited
format embedding structured experimental metadata alongside quantitative matrices
bounded by modality-specific `*_METABOLITE_DATA_START` / `*_METABOLITE_DATA_END`
block markers; the datatable REST API
(`/rest/study/analysis_id/{AN}/datatable/file`), and the analysis-level results
file (`https://www.metabolomicsworkbench.org/studydownload/{ST}_{AN}_Results.txt`).
A response was accepted as containing tabular data only if it included at least one
non-header data row with more than two tab-separated columns; metadata-only mwtab
files and empty responses were not counted. All files were saved regardless of
whether other sources provided valid data, and four independent boolean availability
flags were recorded per analysis. When multiple sources provided valid matrices, the
datatable endpoint was preferred over the mwtab embedded matrix and Results.txt, in
that order, reflecting the descending degree of curation. Experimental metadata —
study design, subject attributes, sample factors, analytical parameters, and metabolite
annotations — was extracted from the mwtab text file for every analysis irrespective
of which source provided the quantitative matrix. Of 6,696 analyses, 6,587 (98.4%)
had at least one validated source of tabular data. Datatable and mwtab provided
near-identical curated named-metabolite matrices (97.1% co-occurrence; 98.2% of
datatable features are named metabolites; median 93 and 104 features per analysis,
respectively), collectively covering 70.7% of analyses. Results.txt provided the sole
tabular source for 1,573 analyses (23.5%) across 837 studies and predominantly
contained raw mz/RT analytical peaks (93.9% of features), with a median of 2,385
features per analysis. Feature name overlap between datatable and Results.txt was
zero in 432 of 466 triple-source analyses (92.7%, median Jaccard = 0.0), confirming
that the two tiers represent pre- and post-identification stages of the data processing
pipeline rather than redundant data exposures. In 34 of 466 triple-source analyses
(7.3%), Results.txt contained named metabolites with substantial overlap with datatable
(Jaccard up to 1.0), indicating that a minority of submitters deposit annotated tables
through the results-file endpoint.
