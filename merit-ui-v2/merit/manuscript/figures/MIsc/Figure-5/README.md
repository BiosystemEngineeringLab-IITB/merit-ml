# Figure 5 — Tier Separation and Source Independence

**Theme C: Tier Separation & Source Independence**
Empirical verification that datatable (Tier 1, named metabolites) and untarg_data (Tier 2, mz/RT tokens) are structurally disjoint and cannot be naively merged as feature vectors. Computed across all 383 triple-source analyses — those for which datatable, mwTab, and untarg_data all passed strict content validation simultaneously. Feature sets were read from the header row only (no full matrix load) for efficiency.

---

## Panel C1 — Jaccard similarity distribution for datatable ↔ untarg_data feature overlap

**File:** `figure5_C1_jaccard_tier_separation.pdf / .png`
**Script:** `/tmp/make_figure5.py`
**Input data:** `/tmp/triple_jaccard.tsv` (383 rows, computed by header-level feature set comparison)

### What it shows

A histogram of pairwise Jaccard similarity — defined as |A ∩ B| / |A ∪ B| where A = datatable column name set and B = untarg_data column name set — for all 383 triple-source analyses. The x-axis uses a pseudo-log scale: the leftmost bar (J=0, exact zero overlap) is positioned separately to avoid log(0) issues, while the non-zero bins are placed on a log₁₀ scale from J=0.001 to J=1.0. A zoomed inset panel shows the non-zero tail (n=42, 11.0%) at full resolution, divided at the J=0.1 threshold into two mechanistically distinct categories:

- **Blue bar (J=0):** 341 analyses with exact zero overlap between datatable feature names and untarg_data feature names
- **Orange bars (0 < J < 0.1):** 31 analyses with partial annotation overlap — untarg_data contains a mix of mz/RT tokens and a minority of named metabolites matching datatable features
- **Red bars (J ≥ 0.1):** 11 analyses where untarg_data returned Tier 1 named metabolite content misrouted through the untarg_data endpoint

### Key numbers

| Category | Count | % of 383 |
|---|---|---|
| Exact zero overlap (J = 0) | 341 | 89.0% |
| Partial annotation (0 < J < 0.1) | 31 | 8.1% |
| Misclassified Tier 1 (J ≥ 0.1) | 11 | 2.9% |
| **Total non-zero** | **42** | **11.0%** |

**Note on prior claim:** An earlier version of this analysis reported 343/383 (89.6%) zero-overlap analyses. The correct figure, computed by reading actual column headers from all 383 file pairs, is **341/383 (89.0%)**. The discrepancy arose from the prior analysis reading only a random subset of rows rather than the full header. Both figures support the same qualitative conclusion.

### Interpretation

**The 89.0% zero-overlap result is the core empirical justification for treating Tier 1 and Tier 2 as structurally disjoint.** Named metabolite identifiers (e.g., `Glucose`, `Alanine`, `CE(16:0)`) and mz/RT tokens (e.g., `180.063_2.75`, `70.065_0.83`) exist in completely separate feature name spaces. There is no meaningful column-level join possible between a datatable matrix and an untarg_data matrix for 89% of the repository. Merging these sources would require mass-based metabolite annotation of the mz/RT tokens first — a non-trivial bioinformatics step outside the scope of ingestion.

**The 31 partially annotated analyses (J < 0.1)** represent untarg_data files where the depositing lab has substituted putative metabolite names for a subset of peak identifiers prior to deposition — a common intermediate state in untargeted metabolomics workflows where LC-MS/MS confirmation has been completed for some peaks but not all. The named subset is sufficient to produce non-zero Jaccard but too small to change the Tier classification: these analyses are still correctly processed as Tier 2 (untarg_data priority), but the named fraction could in principle be harmonised with Tier 1 features post-hoc.

**The 11 misclassified Tier 1 analyses (J ≥ 0.1)** are studies (primarily ST000311 and ST000335) where the `/untarg_data/` REST API endpoint returned a fully named metabolite matrix — the same underlying data as the datatable endpoint, differing only in minor punctuation encoding (commas vs underscores in metabolite names containing special characters, e.g., `(+)-Bornane-2,5-dione` in untarg_data vs `(+)-Bornane-2_5-dione` in datatable). Inspection of raw feature name sets confirms that these untarg_data files contain no mz/RT tokens whatsoever. The percentage mz/RT column in `full_matrix_stats.tsv` would correctly show ~0% for these analyses. These cases do not affect the Tier 1 / Tier 2 analysis: the priority selection rule (datatable > mwTab > untarg_data) means datatable is used for these analyses and the untarg_data is discarded. However, they should be flagged in future audits as studies where the untarg_data endpoint is returning redundant Tier 1 content rather than raw peak tables.

**Methodological note on Jaccard computation:** Feature sets were extracted by reading only the header row of each file (no full matrix load), reducing I/O by ~100× vs full file reads. The `HEADER_LABELS` exclusion set (`samples`, `sample`, `factors`, `factor`, `class`, `classes`, `group`, `groups`) was applied to both sources before computing set intersection and union, ensuring that metadata columns do not contribute spurious overlap.

---

## Corrections to manuscript from this analysis

The following claims in `manuscript.md` should be updated:

| Location | Old claim | Corrected claim |
|---|---|---|
| Methods > Priority Selection | "343 (89.6%) showed zero column-name overlap" | "341 (89.0%) showed zero column-name overlap" |
| Methods > Feature Type Classification | implicit 100% disjointness | 89.0% exact disjointness; 11.0% partial overlap with two mechanistic explanations |

---

## Generation

- **Input:** `outputs/diagnostics/mw_6696_source_presence.tsv` (to identify 383 triple-source analyses); `mw-dump-latest-confirmation/{ST}/{AN}/tabular/` (header-only file reads)
- **Intermediate output:** `/tmp/triple_jaccard.tsv` (383 rows: an, st, status, n_dt, n_ut, n_intersect, jaccard)
- **Script:** `/tmp/make_figure5.py`
- **Style:** DejaVu Sans; bold titles 14pt; bold axis labels 12pt; bold tick labels 11pt; 300 DPI PNG + vector PDF
