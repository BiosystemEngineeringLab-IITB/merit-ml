#!/usr/bin/python3
"""Every entry counted as missing in Table 4 ('empty or non-numeric'), by token and source. 18 Sep 2026 frozen cache.

A cell is counted as missing when, after stripping whitespace, it is not a finite number (table4_descriptors.to_float).
This script re-reads every valid matrix (datatable 4,872; mwTab 4,990; untarg_data 1,887) with the SAME readers as
table4_descriptors.py and records, for each such cell, the text it contains. Texts are compared case-insensitively.
Each text is assigned to a class:
  empty                   empty or whitespace-only cell
  missing-value token     a word or abbreviation standing for a missing value (TOKEN list below)
  detection-limit entry   an entry stating a limit: '<LOD', '< 0.5', '>1000', 'below ...', 'trace'
  non-finite number       'nan', 'inf' (parsed as numbers but not finite)
  malformed number        digits with several decimal points or thousands separators, or a number with a stray character
  other text              any other text
Outputs (this folder): missing_tokens_by_source.tsv (one row per distinct text and source),
missing_token_classes_by_source.tsv (cells and distinct entries per class and source), missing_token_scan_summary.txt.
'--classify-only' re-assigns the classes from the saved entry list without re-reading the matrices.
"""
from __future__ import annotations

import collections
import csv
import html
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import table4_descriptors as t4

OUT = Path(__file__).resolve().parent
TOKENS = {"na", "n/a", "n.a.", "n.a", "null", "none", "nd", "n.d.", "n.d", "bdl", "bql", "nq", "loq", "lod", "llod", "lloq", "bloq",
          "uloq", "missing", "not detected", "not determined", "not available", "not applicable", "not quantified", "not found",
          "not measured", "undetected", "n/f", "nf", "n/d", "nr", "n/q", "#n/a", "#na", "#value!", "#div/0!", "#num!", "#ref!",
          ".", "?", "*", "x", "tr", "blank", "empty", "no data", "nodata", "no peak", "absent",
          "n", "missing value", "no sample", "no good pk", "#wert!"}                 # 'n': single-letter placeholder (31 mwTab matrices)
DASHES = re.compile(r"^-+$")
NONFINITE = {"nan", "inf", "-inf", "+inf", "infinity", "-infinity", "1.#inf", "nan%"}
LIMIT = re.compile(r"^\s*[<>≤≥]|below|above|\btrace\b|\bb?lod\b|\bloq\b|\blloq\b|\buloq\b", re.I)
MALFORMED_NUM = re.compile(r"^[\-+]?[\d.,%\seE+\-]*\d[\d.,%\seE+\-]*[a-z%*#]?$", re.I)
SRC = ["datatable", "mwtab", "untarg_data"]
ORDER = ["empty", "missing-value token", "detection-limit entry", "non-finite number", "malformed number", "other text"]
_captured: collections.Counter = collections.Counter()
_orig = t4.to_float


def klass(tok: str) -> str:
    t = html.unescape(tok).strip().lower()          # '&lt; LOD' is '< LOD'
    if t == "":
        return "empty"
    if t in NONFINITE:
        return "non-finite number"
    if t in TOKENS or DASHES.match(t):
        return "missing-value token"
    if LIMIT.search(t):
        return "detection-limit entry"
    if MALFORMED_NUM.match(t) and re.search(r"\d", t):
        return "malformed number"
    return "other text"


def capture(tokens: pd.Series) -> np.ndarray:
    a = _orig(tokens)
    bad = np.isnan(a)
    if bad.any():
        _captured.update(tokens.to_numpy()[bad].tolist())
    return a


def one(job: tuple[str, str, str]) -> tuple[str, str, str, dict]:
    _captured.clear()
    t4.to_float = capture
    t4.one(job)
    c = collections.Counter()
    for tok, n in _captured.items():
        c[str(tok).strip().lower()] += n
    return job[0], job[1], job[2], dict(c)


def scan() -> None:
    """Re-read every valid matrix and write missing_tokens_by_source.tsv (entry, source, cells, matrices, first analysis)."""
    v = list(csv.DictReader(t4.VALIDITY.open(), delimiter="\t"))
    jobs = [(r["study_id"], r["analysis_id"], s) for r in v for s in ("datatable", "mwtab", "untarg_data") if r[s] == "valid"]
    jobs.sort(key=lambda j: j[2] != "untarg_data")
    with ProcessPoolExecutor(max_workers=24) as ex:
        res = list(ex.map(one, jobs, chunksize=4))
    cells: dict = collections.defaultdict(collections.Counter)
    mats: dict = collections.defaultdict(collections.Counter)
    examples: dict = {}
    for st, an, src, c in res:
        for tok, n in c.items():
            cells[src][tok] += n
            mats[src][tok] += 1
            examples.setdefault((src, tok), f"{st}/{an}")
    with (OUT / "missing_tokens_by_source.tsv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["entry (lower case)", "class", "source", "cells", "matrices", "first analysis"])
        for src in SRC:
            for tok, n in sorted(cells[src].items(), key=lambda x: -x[1]):
                w.writerow([tok if tok else "(empty)", klass(tok), src, n, mats[src][tok], examples[(src, tok)]])


def classify() -> None:
    """(Re)assign the class of every entry of missing_tokens_by_source.tsv and write the class table and the summary."""
    e = pd.read_csv(OUT / "missing_tokens_by_source.tsv", sep="\t", keep_default_na=False)
    e["entry (lower case)"] = e["entry (lower case)"].map(lambda x: x if x == "(empty)" else html.unescape(x))
    e["class"] = e["entry (lower case)"].map(lambda x: klass("" if x == "(empty)" else x))
    e = e.groupby(["entry (lower case)", "class", "source"], as_index=False).agg(          # '&lt; lod' and '< lod' merge
        cells=("cells", "sum"), matrices=("matrices", "max"), **{"first analysis": ("first analysis", "first")})
    e["o"] = e.source.map(SRC.index)
    e = e.sort_values(["o", "cells"], ascending=[True, False]).drop(columns="o")
    e.to_csv(OUT / "missing_tokens_by_source.tsv", sep="\t", index=False)
    t = pd.read_csv(OUT / "table4_per_analysis.tsv", sep="\t")
    t["miss"] = t.n_features * t.n_samples - pd.to_numeric(t.n_values, errors="coerce")
    rows, txt = [], []
    for src in SRC:
        g = e[e.source == src]
        assert int(g.cells.sum()) == int(t[t.source == src].miss.sum()), src            # every cell counted as missing in Table 4
        txt.append(f"{src}: {int(g.cells.sum()):,} cells counted as missing in {int((t[t.source == src].miss > 0).sum()):,} of "
                   f"{int((t.source == src).sum()):,} matrices; {len(g):,} distinct entries")
        for k in ORDER:
            h = g[g["class"] == k]
            rows.append({"class": k, "source": src, "cells": int(h.cells.sum()), "distinct entries": len(h),
                         "largest number of matrices sharing one entry": int(h.matrices.max()) if len(h) else 0})
            txt.append(f"   {k:24s} {int(h.cells.sum()):>12,} cells  {len(h):>6,} distinct entries")
    pd.DataFrame(rows).to_csv(OUT / "missing_token_classes_by_source.tsv", sep="\t", index=False)
    (OUT / "missing_token_scan_summary.txt").write_text("\n".join(txt) + "\n")
    print("\n".join(txt))


def main() -> None:
    if "--classify-only" not in sys.argv:
        scan()
    classify()


if __name__ == "__main__":
    main()
