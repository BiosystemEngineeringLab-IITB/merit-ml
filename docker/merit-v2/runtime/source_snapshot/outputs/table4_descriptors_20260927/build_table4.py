#!/usr/bin/python3
"""Table 4 (revision, FINAL definitions, 2026-09-30), 18 Sep 2026 frozen cache.

Median (Q1–Q3), with [min–max] where the range is informative (Reviewer 1, Minor Comment 5). Inputs (all per matrix):
  table4_per_analysis.tsv (this folder; table4_descriptors.py, positional mwTab reader)
  ../pn_recompute_20260930/pn_per_matrix.tsv (pn_recompute.py): n_aligned = sample rows after the 16-keyword QC/blank
      filter and Tier 1 sample alignment (datatable rows absent from the paired mwTab block removed; Table SX); P2 = p/n
  ../annotation_coverage_20260928/class_counts_per_analysis.tsv (classifier v2; Text S3, Table S1)
  zero_origin_per_analysis.tsv (this folder; zero_origin.py): datatable zeros traced to the paired mwTab cell
Rows:
  feature count            all deposited feature labels
  sample rows              all deposited rows; and after QC/blank removal + Tier 1 sample alignment (p/n denominator)
  annotation               % matrices (with eligible features) whose eligible features are >= 90% named; pooled named %
  p/n                      P2: all features / samples after QC/blank removal + Tier 1 sample alignment; % > 1, % > 10;
                           pooled (sum p / sum n); matrices with no remaining samples excluded
  completeness             empty or non-numeric entries (empty cells, NA-type tokens, other text) and zero entries, % of
                           cells. Datatable zeros without a corresponding numeric value in the paired mwTab block (zeros at
                           empty or non-numeric mwTab entries + zeros in rows removed by Tier 1 sample alignment; 4,856
                           paired matrices) are summarised in datatable_zero_provenance_summary.tsv for Text SX, not here
  measurement scale        median of all non-zero values per matrix (negatives included), deposited scale;
                           % matrices containing negative values
Outputs (this folder): table4_revised.tsv, datatable_zero_provenance_summary.tsv
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PN = HERE.parent / "pn_recompute_20260930/pn_per_matrix.tsv"
ANNOTATION = HERE.parent / "annotation_coverage_20260928/class_counts_per_analysis.tsv"
ZERO = HERE / "zero_origin_per_analysis.tsv"
SRC = ["datatable", "mwtab", "untarg_data"]
K = ["study_id", "analysis_id", "source"]


def sci(x: float) -> str:
    if x == 0:
        return "0"
    e = int(np.floor(np.log10(abs(x))))
    if -1 <= e < 4:
        return f"{x:,.1f}" if e < 2 else f"{x:,.0f}"
    return f"{x / 10 ** e:.1f}×10^{e}"


def mqr(s: pd.Series, fmt="{:,.0f}", rng=True, f=None) -> str:
    s = pd.to_numeric(s, errors="coerce").dropna()
    g = f or (lambda v: fmt.format(v))
    out = f"{g(s.median())} ({g(s.quantile(.25))}–{g(s.quantile(.75))})"
    return out + (f" [{g(s.min())}–{g(s.max())}]" if rng else "")


def main() -> None:
    d = pd.read_csv(HERE / "table4_per_analysis.tsv", sep="\t")
    pn = pd.read_csv(PN, sep="\t")[K + ["n_aligned", "P2"]]
    ann = pd.read_csv(ANNOTATION, sep="\t")[K + ["n_named", "n_without_name"]].rename(
        columns={"n_named": "v2_named", "n_without_name": "v2_without_name"})     # classifier v2 counts only
    d = d.merge(pn, on=K).merge(ann, on=K)
    assert len(d) == 11749
    for c in ("median_nonzero", "n_negative", "n_values", "n_zero"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["cells"] = d.n_features * d.n_samples
    d["explicit_pct"] = 100 * (d.cells - d.n_values) / d.cells
    d["zero_pct"] = 100 * d.n_zero / d.cells
    d["elig"] = d.v2_named + d.v2_without_name
    g = {s: d[d.source == s] for s in SRC}
    z = pd.read_csv(ZERO, sep="\t")
    nm = 100 * (z.filled_missing + z.padding) / z.cells
    pct = lambda m, base: f"{100 * m.sum() / base:.1f}%"
    rows = [("", "Analyses (matrices), n", *[f"{len(g[s]):,}" for s in SRC]),
            ("Dimensionality", "Feature count", *[mqr(g[s].n_features) for s in SRC]),
            ("", "Sample rows, all deposited", *[mqr(g[s].n_samples) for s in SRC]),
            ("", "Sample rows after QC/blank removal and Tier 1 sample alignment", *[mqr(g[s].n_aligned) for s in SRC])]
    def named90(s):
        e = g[s][g[s].elig > 0]
        k = int((100 * e.v2_named / e.elig >= 90).sum())
        return f"{100 * k / len(e):.1f}% ({k:,}/{len(e):,})"
    rows += [("Annotation", "Matrices with ≥90% named features (of matrices with eligible features)", *[named90(s) for s in SRC]),
             ("", "Named features, % of eligible features (pooled)",
              *[f"{100 * g[s].v2_named.sum() / g[s].elig.sum():.2f}%" for s in SRC])]
    rows += [("Feature-to-sample ratio", "p/n", *[mqr(g[s].P2, "{:.2f}", rng=False) for s in SRC]),
             ("", "Matrices with p/n > 1", *[pct(g[s].P2.dropna() > 1, g[s].P2.notna().sum()) for s in SRC]),
             ("", "Matrices with p/n > 10", *[pct(g[s].P2.dropna() > 10, g[s].P2.notna().sum()) for s in SRC]),
             ("", "Pooled p/n (Σp / Σn)", *[f"{g[s][g[s].P2.notna()].n_features.sum() / g[s][g[s].P2.notna()].n_aligned.sum():.2f}" for s in SRC]),
             ("", "Matrices without remaining samples (p/n undefined)", *[f"{int(g[s].P2.isna().sum())}" for s in SRC])]
    rows += [("Data completeness", "Empty or non-numeric entries, % of cells",
              *[f"{mqr(g[s].explicit_pct, '{:.1f}', rng=False)}; mean {g[s].explicit_pct.mean():.2f}" for s in SRC]),
             ("", "Zero entries, % of cells", *[f"{mqr(g[s].zero_pct, '{:.1f}', rng=False)}; mean {g[s].zero_pct.mean():.2f}" for s in SRC])]
    # Paired datatable-mwTab zero provenance is reported in Text SX / Table SX, not in Table 4 (written to a side file).
    prov = pd.DataFrame([{"paired datatable matrices": len(z), "with at least one such zero": int((nm > 0).sum()),
                          "% with at least one": round(100 * float((nm > 0).mean()), 1), "with >= 50% of cells": int((nm >= 50).sum()),
                          "% with >= 50% of cells": round(100 * float((nm >= 50).mean()), 1), "median % of cells": round(float(nm.median()), 2),
                          "q1": round(float(nm.quantile(.25)), 2), "q3": round(float(nm.quantile(.75)), 2), "mean % of cells": round(float(nm.mean()), 2),
                          "rows removed by Tier 1 sample alignment": int(z.padding_rows.sum()), "cells in those rows": int(z.padding.sum()),
                          "zeros at empty or non-numeric mwTab entries": int(z.filled_missing.sum())}])
    prov.to_csv(HERE / "datatable_zero_provenance_summary.tsv", sep="\t", index=False)
    rows += [("Measurement scale", "Median of non-zero values per matrix (deposited scale)",
              *[mqr(g[s].median_nonzero, rng=False, f=sci) for s in SRC]),
             ("", "Matrices containing negative values", *[f"{int((g[s].n_negative > 0).sum()):,} ({100 * (g[s].n_negative > 0).mean():.1f}%)" for s in SRC])]
    with (HERE / "table4_revised.tsv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["Group", "Descriptor", "datatable", "mwTab", "untarg_data"])
        w.writerows(rows)
    for r in rows:
        print(" | ".join(r))


if __name__ == "__main__":
    main()
