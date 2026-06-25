# Structural Characterization of Tabular Data Availability in the mwtab Format Across Metabolomics Workbench[#](#structural-characterization-of-tabular-data-availability-in-the-mwtab-format-across-metabolomics-workbench "Copy link")

## 1\. Background and Motivation[#](#1-background-and-motivation "Copy link")

Metabolomics Workbench (MW) exposes study data through three programmatic access mechanisms: (i) the datatable REST API, (ii) downloadable Results.txt files, and (iii) the mwtab section-delimited format. The mwtab format serves as MW’s canonical archival representation, bundling both metadata and quantitative matrices in a single text file per analysis. However, the mere existence of an mwtab file does not guarantee the presence of a quantitative feature matrix — a critical distinction for any automated ingestion pipeline.

This section presents a systematic structural audit of all mwtab files across the entire MW repository to characterize the availability, format, and consistency of tabular metabolite data.

## 2\. Scope and Methodology[#](#2-scope-and-methodology "Copy link")

### 2.1 Corpus[#](#21-corpus "Copy link")

-   **Total registered analyses**: 6,696
-   **Analyses with both `*_mwtab.txt` and `*_mwtab.json` representations**: 6,686
-   **JSON parse failures** (irrecoverable): 52

The 10 analyses lacking mwtab files and the 52 with unparseable JSON representations indicate non-trivial gaps in the repository’s archival completeness.

### 2.2 Audit Procedure[#](#22-audit-procedure "Copy link")

Each mwtab text file was parsed to extract:

1.  All `#`\-prefixed section header tags (21 unique tags identified)
2.  The presence and content of recognized metabolite data sections, identified by `*_DATA_START` / `*_DATA_END` boundary markers
3.  Row counts within each data section (excluding header rows, factor rows, and blank lines)
4.  Parallel comparison against the corresponding mwtab JSON representation

Five metabolite data section types were evaluated:

-   `MS_METABOLITE_DATA` — mass spectrometry quantitative matrix
-   `NMR_METABOLITE_DATA` — NMR quantitative matrix
-   `NMR_BINNED_DATA` — NMR binned spectral data
-   `DIRECT_INFUSION_METABOLITE_DATA` — direct infusion MS data
-   `METABOLITE_DATA` — generic metabolite data

Additionally, two extended (long-format) section types were identified:

-   `EXTENDED_MS_METABOLITE_DATA` — vertical MS data (one row per metabolite-sample pair)
-   `EXTENDED_NMR_METABOLITE_DATA` — vertical NMR data

## 3\. mwtab Section Tag Inventory[#](#3-mwtab-section-tag-inventory "Copy link")

A complete enumeration of all 21 unique `#`\-prefixed section header tags across the 6,686 mwtab text files is presented below. Tags are ordered by frequency to illustrate the hierarchical structure of the format.

### Table 1. Complete inventory of mwtab section header tags[#](#table-1-complete-inventory-of-mwtab-section-header-tags "Copy link")

| Tag | Files (n) | Category |
| --- | --- | --- |
| `#ANALYSIS` | 6,717 | Structural |
| `#END` | 6,712 | Structural |
| `#METABOLOMICS` | 6,710 | Format header |
| `#PROJECT` | 6,710 | Study metadata |
| `#STUDY` | 6,710 | Study metadata |
| `#SUBJECT` | 6,710 | Subject metadata |
| `#SUBJECT_SAMPLE_FACTORS:` | 6,710 | Sample-factor mapping |
| `#COLLECTION` | 6,710 | Collection metadata |
| `#TREATMENT` | 6,710 | Treatment metadata |
| `#SAMPLEPREP` | 6,710 | Sample preparation |
| `#CHROMATOGRAPHY` | 6,567 | Analytical metadata |
| `#MS` | 6,413 | MS parameters |
| `#METABOLITES` | 4,968 | Annotation table |
| `#MS_METABOLITE_DATA` | 4,801 | **Quantitative matrix** |
| `#FACTORS` | 823 | Factor definitions |
| `#NMR` | 294 | NMR parameters |
| `#NMR_METABOLITE_DATA` | 167 | **Quantitative matrix** |
| `#NMR_BINNED_DATA` | 52 | **Quantitative matrix** |
| `#_1` | 2 | Formatting error |
| `#END#_4` | 1 | Formatting error |
| `#MS:MS_RESULTS_FILE` | 1 | Formatting error |

**Notes:**

-   Counts for `#ANALYSIS` and `#END` exceed the file count (6,686) because some files contain multiple analysis blocks.
-   Three tags (`#_1`, `#END#_4`, `#MS:MS_RESULTS_FILE`) are formatting errors in specific files (AN001890, AN002654, AN001881) and are not part of the mwtab specification.
-   `#METABOLITES` (4,968 files) is an annotation table containing identifiers (PubChem, InChI, KEGG) but not a quantitative feature matrix.
-   `#SUBJECT_SAMPLE_FACTORS:` contains structured sample-factor mappings but not quantitative data.

## 4\. Tabular Data Availability in mwtab Text Files[#](#4-tabular-data-availability-in-mwtab-text-files "Copy link")

### 4.1 Overall Availability[#](#41-overall-availability "Copy link")

Of the 6,686 mwtab text files, **5,009 (74.9%)** contain at least one metabolite data section with actual data rows. The remaining **1,677 (25.1%)** are metadata-only shells.

### Table 2. Tabular data availability breakdown[#](#table-2-tabular-data-availability-breakdown "Copy link")

| Status | Analyses (n) | % |
| --- | --- | --- |
| Has tabular data (≥1 section with data rows) | 5,009 | 74.9 |
| No tabular data — no data section header | 1,675 | 25.1 |
| No tabular data — header present, section empty | 2 | <0.1 |
| **Total** | **6,686** | **100.0** |

### 4.2 Breakdown by Section Type[#](#42-breakdown-by-section-type "Copy link")

### Table 3. Metabolite data sections: tag presence vs. data availability (mwtab.txt)[#](#table-3-metabolite-data-sections-tag-presence-vs-data-availability-mwtabtxt "Copy link")

| Section | Tag present | Non-empty (has data rows) | Empty (tag, no data) |
| --- | --- | --- | --- |
| `MS_METABOLITE_DATA` | 4,792 | 4,790 | 2 |
| `NMR_METABOLITE_DATA` | 167 | 167 | 0 |
| `NMR_BINNED_DATA` | 52 | 52 | 0 |
| `EXTENDED_MS_METABOLITE_DATA` | 10 | 10 | 0 |
| `EXTENDED_NMR_METABOLITE_DATA` | 11 | 11 | 0 |
| `DIRECT_INFUSION_METABOLITE_DATA` | 0 | 0 | 0 |
| `METABOLITE_DATA` (generic) | 0 | 0 | 0 |

**Key observations:**

-   MS-based analyses dominate: `MS_METABOLITE_DATA` accounts for 95.6% (4,790/5,009) of all files with tabular data.
-   NMR analyses have **100% data completeness**: all 167 `NMR_METABOLITE_DATA` and all 52 `NMR_BINNED_DATA` files contain data rows.
-   `DIRECT_INFUSION_METABOLITE_DATA` and generic `METABOLITE_DATA` sections were **not found in any file** despite being part of the mwtab specification.
-   Only 2 files have an MS data section header with empty content (AN000421 and AN000486).

### 4.3 Empty mwtab Files: Structural Analysis[#](#43-empty-mwtab-files-structural-analysis "Copy link")

Of the 1,677 files lacking tabular data:

-   **1,675 (99.9%)** contain no metabolite data section header whatsoever. These files consist exclusively of metadata sections (`#PROJECT`, `#STUDY`, `#SUBJECT`, `#MS`/`#NMR`, `#CHROMATOGRAPHY`, etc.) — they are archival metadata records with no associated quantitative matrix.
-   **2 files (0.1%)** — AN000421 (ST000264) and AN000486 (ST000307) — contain the `#MS_METABOLITE_DATA` header and `MS_METABOLITE_DATA_START`/`END` markers with units declarations (“integrated peak counts” and “Peak area”, respectively), but have zero metabolite data rows between the markers.

These 1,675 metadata-only files span the entire study ID range (ST000043 to ST004661), indicating this is not an era-specific artifact but a persistent pattern throughout MW’s operational history.

### 4.4 Extended (Long-Format) Data Sections[#](#44-extended-long-format-data-sections "Copy link")

Twenty-one files contain **two** data sections: a standard wide-format matrix paired with an extended long-format representation:

-   10 files pair `MS_METABOLITE_DATA` with `EXTENDED_MS_METABOLITE_DATA`
-   11 files pair `NMR_METABOLITE_DATA` with `EXTENDED_NMR_METABOLITE_DATA`

The extended format stores data vertically (one row per metabolite-sample pair) with columns including `metabolite_name`, `concentration`, `concentration%type`, `concentration%units` (MS) or `chemical_shift`, `peak_area`, `peak_height`, `peak_width` (NMR). These are confined to a single study cluster (AN002417–AN002449, AN004409).

## 5\. Cross-Format Comparison: mwtab Text vs. JSON[#](#5-cross-format-comparison-mwtab-text-vs-json "Copy link")

To assess consistency between the two mwtab representations, we compared the text (`.txt`) and JSON (`.json`) files block-by-block.

### Table 4. Block-wise comparison of mwtab.txt vs. mwtab.json[#](#table-4-block-wise-comparison-of-mwtabtxt-vs-mwtabjson "Copy link")

| Block | txt present | txt non-empty | json present | json non-empty | Both non-empty | Exact row-count match | Row-count mismatch | txt-only non-empty | json-only non-empty |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `MS_METABOLITE_DATA` | 4,792 | 4,790 | 4,745 | 4,745 | 4,745 | 4,733 | 12 | 45 | 0 |
| `NMR_BINNED_DATA` | 52 | 52 | 52 | 52 | 52 | 0 | 52 | 0 | 0 |
| `NMR_METABOLITE_DATA` | 167 | 167 | 165 | 164 | 164 | 153 | 11 | 3 | 0 |
| `DIRECT_INFUSION_METABOLITE_DATA` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `METABOLITE_DATA` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

### Table 5. Aggregate cross-format concordance[#](#table-5-aggregate-cross-format-concordance "Copy link")

| Metric | Count |
| --- | --- |
| txt non-empty (any block) | 5,009 |
| json non-empty (any block) | 4,961 |
| Both non-empty | 4,961 |
| **txt-only non-empty** | **48** |
| **json-only non-empty** | **0** |

### 5.1 Interpretation[#](#51-interpretation "Copy link")

**JSON is a strict subset of TXT for tabular data.** Every analysis with a non-empty JSON metabolite block also has a non-empty TXT block, but **48 analyses have tabular data in TXT only** — the JSON representation either lacks the block entirely or failed to parse. No analysis has JSON-only tabular data.

**Row-count mismatches are systematic, not random:**

-   `NMR_BINNED_DATA`: All 52 files show mismatches. The JSON representation stores NMR binned data in a legacy top-level `Data` object with a structurally different framing from the TXT row format, making direct row-count comparison invalid.
-   `MS_METABOLITE_DATA`: 12 mismatches out of 4,745 shared files (0.25%) — likely due to parser differences in handling edge-case rows.
-   `NMR_METABOLITE_DATA`: 11 mismatches out of 164 shared files (6.7%) — a higher mismatch rate suggesting less standardized NMR data formatting.

**52 JSON parse failures** across the corpus further reduce JSON reliability as a sole data source.

## 6\. Tabular Data Sources: Three-Way Overlap Analysis[#](#6-tabular-data-sources-three-way-overlap-analysis "Copy link")

Beyond the mwtab format, MW exposes quantitative data through two additional endpoints: the datatable REST API and downloadable Results.txt files. We analyzed the overlap across all three sources at both the analysis and study level.

### 6.1 Source Definitions[#](#61-source-definitions "Copy link")

-   **datatable**: TSV files retrieved via the MW REST API (`/rest/study/analysis_id/<AN_ID>/datatable/`)
-   **Results.txt**: Downloadable result files from the MW study page
-   **mwtab (tabular)**: mwtab files containing at least one non-empty metabolite data section (as defined in Section 4)

### Table 6. Three-way tabular data source overlap — per analysis (N = 6,696)[#](#table-6-three-way-tabular-data-source-overlap-—-per-analysis-n-6696 "Copy link")

| Source combination | Analyses (n) | % |
| --- | --- | --- |
| datatable + mwtab | 4,797 | 71.6 |
| Results.txt only | 1,554 | 23.2 |
| mwtab only | 148 | 2.2 |
| No tabular source | 123 | 1.8 |
| datatable only | 73 | 1.1 |
| Results.txt + mwtab | 1 | <0.1 |
| **Total** | **6,696** | **100.0** |

### Table 7. Three-way tabular data source overlap — per study (N = 4,121)[#](#table-7-three-way-tabular-data-source-overlap-—-per-study-n-4121 "Copy link")

| Source combination | Studies (n) | % |
| --- | --- | --- |
| datatable + mwtab | 3,059 | 74.2 |
| Results.txt only | 831 | 20.2 |
| mwtab only | 97 | 2.4 |
| datatable only | 35 | 0.8 |
| All three (datatable + Results.txt + mwtab) | 31 | 0.8 |
| No tabular source | 68 | 1.7 |
| **Total** | **4,121** | **100.0** |

### 6.2 Critical Findings[#](#62-critical-findings "Copy link")

1.  **datatable and Results.txt are mutually exclusive.** Zero analyses are served by both endpoints (excluding the 31 studies with all three). These two API endpoints partition the repository into two entirely disjoint access populations.
    
2.  **mwtab provides the broadest single-source coverage** (5,009 analyses, 74.9%) but still misses 25.1% of analyses.
    
3.  **148 analyses (97 studies) have tabular data only in mwtab** — not accessible through datatable or Results.txt. These would be invisible to any ingestion tool relying solely on the REST API.
    
4.  **73 analyses (35 studies) have tabular data only in datatable** — not in mwtab despite mwtab files existing as metadata-only shells.
    
5.  **68 studies (1.7%) have no tabular data from any source.** These studies are registered in MW’s index but have no downloadable quantitative matrix through any programmatic mechanism.
    

## 7\. Implications for Automated Ingestion[#](#7-implications-for-automated-ingestion "Copy link")

These findings have direct implications for the design of metabolomics data ingestion pipelines:

1.  **No single source is sufficient.** An ingestion engine relying exclusively on any one of the three sources would miss between 20–25% of available tabular data. MERIT’s multi-source connector architecture with fallback logic (datatable → mwtab → Results.txt) was designed to address this heterogeneity.
    
2.  **Format verification is essential.** The presence of an mwtab file cannot be equated with the availability of quantitative data. Of the 6,686 mwtab files, 25.1% are metadata-only shells. Ingestion pipelines must verify the presence of actual `*_DATA_START`/`*_DATA_END` sections with populated rows.
    
3.  **Cross-format consistency cannot be assumed.** The 48 txt-only tabular analyses, 52 JSON parse failures, and systematic row-count mismatches in NMR data demonstrate that the text and JSON mwtab representations are not interchangeable. The text format should be treated as the authoritative source.
    
4.  **Source selection must be study-aware.** The mutual exclusivity of datatable and Results.txt means that the correct source must be determined per-study, not configured globally. This necessitates a probing or registry-based approach to source selection.
    

---

## Figure Concepts[#](#figure-concepts "Copy link")

### Figure 1: mwtab File Composition — Tabular Data Presence[#](#figure-1-mwtab-file-composition-—-tabular-data-presence "Copy link")

**Prompt:** A stacked bar chart showing the composition of 6,686 mwtab files. The primary bar is split into: “Has tabular data” (5,009, green) and “Metadata-only shell” (1,677, gray). A secondary exploded view of the 5,009 files shows the breakdown by section type: MS\_METABOLITE\_DATA (4,790, blue), NMR\_METABOLITE\_DATA (167, orange), NMR\_BINNED\_DATA (52, yellow), EXTENDED sections (21, purple). A small callout shows the 2 files with headers but empty data sections. Use a clean, publication-quality style with muted colors.

### Figure 2: Three-Way Source Overlap — UpSet Plot[#](#figure-2-three-way-source-overlap-—-upset-plot "Copy link")

**Prompt:** An UpSet plot (preferred over Venn diagram for >2 sets) showing the intersection of three tabular data sources (datatable, Results.txt, mwtab) across 6,696 analyses. The horizontal bars show set sizes: mwtab (4,946), datatable (4,870), Results.txt (1,555). The vertical bars show intersection sizes: datatable∩mwtab (4,797), Results.txt-only (1,554), mwtab-only (148), none (123), datatable-only (73), Results.txt∩mwtab (1). Highlight the zero-overlap between datatable and Results.txt with a visual annotation. Use a black-and-white or two-color scheme suitable for journal reproduction.

### Figure 3: mwtab Section Tag Frequency Heatmap[#](#figure-3-mwtab-section-tag-frequency-heatmap "Copy link")

**Prompt:** A horizontal bar chart or heatmap showing all 21 mwtab section tags ordered by frequency (6,717 down to 1). Color-code bars by category: structural (gray), study metadata (blue), analytical metadata (teal), annotation (orange), quantitative matrix (red/bold), errors (hatched). Annotate the three quantitative matrix tags (MS\_METABOLITE\_DATA, NMR\_METABOLITE\_DATA, NMR\_BINNED\_DATA) with their non-empty counts. This figure communicates the hierarchical structure of the mwtab format and the relative rarity of quantitative data sections.

### Figure 4: Cross-Format Concordance — TXT vs. JSON[#](#figure-4-cross-format-concordance-—-txt-vs-json "Copy link")

**Prompt:** A concordance matrix or paired bar chart comparing mwtab.txt and mwtab.json for each metabolite data block type. For each block (MS, NMR, NMR\_BINNED), show side-by-side bars for txt-present, json-present, both-non-empty, txt-only, and json-only. Annotate row-count mismatches (12 for MS, 11 for NMR, 52 for NMR\_BINNED). Include a callout box explaining the NMR\_BINNED structural difference. Use a paired color scheme (e.g., blue for txt, orange for json).

### Figure 5: Metadata-Only mwtab Files Across Study ID Range[#](#figure-5-metadata-only-mwtab-files-across-study-id-range "Copy link")

**Prompt:** A histogram or density plot showing the distribution of metadata-only mwtab files (n=1,675) across the study ID range (ST000043 to ST004661), binned by 500-study intervals. Overlay the total study count per bin as a reference line. This demonstrates that metadata-only files are not era-specific but are distributed across MW’s entire operational history. Use a gray histogram with a dashed reference line.

### Figure 6: Study-Level Source Coverage Treemap[#](#figure-6-study-level-source-coverage-treemap "Copy link")

**Prompt:** A treemap showing the 4,121 studies partitioned by their tabular data source combination. The largest rectangle is “datatable + mwtab” (3,059, 74.2%), followed by “Results.txt only” (831, 20.2%), “mwtab only” (97, 2.4%), “No source” (68, 1.7%), “datatable only” (35, 0.8%), and “All three” (31, 0.8%). Each rectangle is labeled with count and percentage. Use distinct but muted colors. This provides an immediate visual impression of source heterogeneity at the study level.