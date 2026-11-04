#!/usr/bin/python3
"""Value-level consistency of declared units, number-format corruption and duplicate matrices (mwTab). 18 Sep 2026 cache.

Per valid mwTab matrix (4,990; Figure 2B), using
  ../table4_descriptors_20260927/table4_per_analysis.tsv  median_nonzero, pct_negative_of_nonzero (positional reader)
  units_per_analysis.tsv (extract_units.py, revision 3)    measurement type, processing flags, display category
and the raw mwTab data block (split on '\\n' only; non-EXTENDED, non-METABOLITES '<BLOCK>_DATA_START' ... '_DATA_END').

Value rules (a matrix is flagged 'inconsistent' when any fires; thresholds are physical or scale limits, not fitted):
  V1 raw signal with negative values: type intensity/area/counts, no processing flag, median < 0 or > 5% of non-zero
     values negative (raw peak areas, heights and counts cannot be negative).
  V2 raw MS signal below 1: type intensity/area/counts, no processing flag, MS data block, 0 <= median < 1.
  V3 log-labelled values not on a log scale: log-transformed flag and |median| > 50 (log2 of 1e15 = 50).
  V4 physically impossible concentration or content, after converting the declared unit (CONVERT): molarity > 1 mol/L,
     mass per volume > 100 g/L, amount per mass > 1 mmol/g, mass per mass > 0.1 g/g. Units per cell, vial, organ or
     sample are not convertible and are not tested.
  V5 ratio or internal-standard-normalised unit with raw-scale values: type relative/ratio with a ratio term
     (ratio, %, percent, fraction, fold), or normalised by an internal standard ('/IS', 'internal standard'),
     and median > 1e4.
Annotations (not 'inconsistent'):
  A1 bare 'relative <signal>' label with raw-scale values (median > 1e3): ambiguous, not an error.
  A2 NMR_BINNED block declared 'Peak area' (possible template default).
Number-format corruption: share of data cells written with dots as thousands separators (-3.079.561.234) or as integers
  with a leading zero (082111098, decimal point lost); flagged when >= 5% of non-empty cells.
Duplicates: matrices whose numeric content is identical (each row's cells after the first column sorted, rows sorted,
  hashed; insensitive to sample and feature order and to feature names; >= 10 cells) form a duplicate group; the first record (study_id, analysis_id order) is the canonical copy.
Thresholds are named constants (NEG_PCT, MS_MIN, LOG_MAX, LIMIT, RATIO_MAX, FORMAT_PCT) with their reasons in RATIONALE;
threshold_sensitivity.tsv gives the number of matrices flagged by each rule under tighter and looser thresholds.
Outputs (this folder): value_consistency_per_matrix.tsv, value_consistency_summary.txt, duplicate_groups.tsv,
threshold_sensitivity.tsv
"""
from __future__ import annotations

import collections
import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot")
CACHE = ROOT / "mw-dump-latest-18thSep-2026-version"
OUT = Path(__file__).resolve().parent
K = ["study_id", "analysis_id"]
MULTIDOT = re.compile(r"^-?\d{1,3}(?:\.\d{3}){2,}$")
LEAD0 = re.compile(r"^-?0\d+$")
START = re.compile(r"^([A-Z_]+_DATA)_START")

P = {"": 1, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "a": 1e-18}
WORD = {"milli": "m", "micro": "u", "nano": "n", "pico": "p", "femto": "f", "atto": "a"}
DEN = {"l": 1, "ml": 1e-3, "dl": 0.1, "ul": 1e-6, "µl": 1e-6, "μl": 1e-6, "g": 1, "mg": 1e-3, "kg": 1e3, "ug": 1e-6,
       "µg": 1e-6, "μg": 1e-6, "ng": 1e-9}
LIMIT = {"mol/L": 1.0, "g/L": 100.0, "mol/g": 1e-3, "g/g": 0.1}
# Thresholds of the value rules, with the reason for each and the alternatives reported in threshold_sensitivity.tsv.
NEG_PCT, MS_MIN, LOG_MAX, RATIO_MAX, FORMAT_PCT, REL_RAW = 5.0, 1.0, 50.0, 1e4, 5.0, 1e3
RATIONALE = {
    "V1": "Raw peak areas, heights and counts cannot be negative; a few negative cells can follow baseline or blank "
          "correction, so the rule needs a negative median or more than 5% negative non-zero values.",
    "V2": "Ion counts and integrated areas from MS detectors are >= 1 by construction; a matrix median below 1 means the "
          "values were divided by something (normalised, scaled or ratioed).",
    "V3": "log2 of 1e15 is 50 and log10 of 1e15 is 15; no MS or NMR signal exceeds 1e15, so a log-scale median cannot "
          "exceed 50 in absolute value.",
    "V4": "Physical limits for a MEDIAN metabolite: 1 mol/L (pure water is 55 mol/L; urea in urine, the most concentrated "
          "common metabolite, is about 0.3 mol/L), 100 g/L, 1 mmol/g and 0.1 g/g of material.",
    "V5": "A percentage is at most about 1e2 and a ratio to an internal standard or reference is of order 1e-3 to 1e2; "
          "1e4 leaves two orders of magnitude of margin.",
    "F": "A few leading-zero or multi-dot cells can be genuine identifiers or typing slips; 5% of cells indicates that a "
         "whole block was written with the wrong number format.",
}
SENSITIVITY = {"V1": [1.0, 5.0, 10.0, 25.0], "V2": [0.1, 1.0, 10.0, 100.0], "V3": [30.0, 50.0, 100.0, 1000.0],
               "V4": [0.1, 1.0, 10.0], "V5": [1e3, 1e4, 1e5], "F": [1.0, 5.0, 10.0, 50.0]}


def convert(units: str) -> tuple[str, float] | None:
    """Declared unit -> (base, factor) with base mol/L, g/L, mol/g or g/g; None when not convertible."""
    s = units.lower().replace("?g", "µg").replace(" per ", "/")
    s = re.sub(r"\b(milli|micro|nano|pico|femto|atto)\s*", lambda m: WORD[m.group(1)], s)
    s = re.sub(r"moles?\b|mols\b", "mol", s)
    s = re.sub(r"grams?\b", "g", s)
    m = re.search(r"(?<![a-z])([munpfaµμ]?)(mol|g)\s*/\s*(\d*)\s*(l|ml|dl|ul|µl|μl|kg|g|mg|ug|µg|μg|ng)(?![a-z])", s)
    if m:
        base = f"{m.group(2)}/{'L' if m.group(4).endswith('l') else 'g'}"
        return base, P[m.group(1)] / (DEN[m.group(4)] * float(m.group(3) or 1))
    m = re.fullmatch(r"\s*(?:concentration\s*)?\(?\s*([munpfµμ])m\s*\)?\s*|\s*([munpfµμ])?molar\s*", s)
    if m and re.search(r"(?<![A-Za-z])[munpfµμ]M(?![A-Za-z])|molar", units):
        return "mol/L", P[(m.group(1) or m.group(2) or "").replace("u", "u")]
    return None


def scan(path: Path) -> tuple[int, int, int, str]:
    """Non-empty data cells, multi-dot cells, leading-zero cells, content hash of the mwTab data block(s)."""
    rows, n, a, b, blk = [], 0, 0, 0, False
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        m = START.match(line)
        if m:
            blk = not ("METABOLITES" in m.group(1) or "EXTENDED" in m.group(1))
            continue
        if "_DATA_END" in line[:60]:
            blk = False
            continue
        if not blk or line.startswith(("Samples", "Factors")):
            continue
        cells = [c.strip() for c in line.rstrip("\r").split("\t")[1:]]
        rows.append("\t".join(sorted(cells)))                  # cells sorted: insensitive to sample (column) order
        for c in cells:
            if c:
                n += 1
                a += bool(MULTIDOT.match(c))
                b += bool(LEAD0.match(c))
    h = hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest() if n >= 10 else ""
    return n, a, b, h


def main() -> None:
    t = pd.read_csv(ROOT / "outputs/table4_descriptors_20260927/table4_per_analysis.tsv", sep="\t")
    t = t[t.source == "mwtab"][K + ["n_features", "n_samples", "median_nonzero", "pct_negative_of_nonzero"]]
    for c in ("median_nonzero", "pct_negative_of_nonzero"):
        t[c] = pd.to_numeric(t[c], errors="coerce")
    u = pd.read_csv(OUT / "units_per_analysis.tsv", sep="\t", keep_default_na=False)
    d = t.merge(u[K + ["mwtab_blocks", "units_raw", "measurement_type", "processing_flags", "units_category"]], on=K, how="left")
    assert len(d) == 4990
    sc = [scan(CACHE / s / a / "mwtab" / f"{a}_mwtab.txt") for s, a in zip(d.study_id, d.analysis_id)]
    d["cells_nonempty"], d["cells_multidot"], d["cells_leading_zero"], d["content_hash"] = zip(*sc)
    d["pct_format_corrupt"] = 100 * (d.cells_multidot + d.cells_leading_zero) / d.cells_nonempty.replace(0, np.nan)
    med, neg, fl = d.median_nonzero, d.pct_negative_of_nonzero.fillna(0), d.processing_flags
    raw = (d.measurement_type == "intensity/area/counts") & (fl == "")
    ratio_term = d.units_raw.str.contains(r"ratio|%|percent|fraction|\bfold", case=False, regex=True)
    is_norm = d.units_raw.str.contains(r"/\s*(?:is|istd)\b|internal standard", case=False, regex=True)
    conv = [convert(x) if mt in ("concentration", "amount/content") else None for x, mt in zip(d.units_raw, d.measurement_type)]
    d["converted_base"] = [c[0] if c else "" for c in conv]
    d["median_converted"] = [med.iat[i] * c[1] if c and pd.notna(med.iat[i]) else np.nan for i, c in enumerate(conv)]
    lim = d.converted_base.map(LIMIT)
    ms, logf = d.mwtab_blocks.str.contains("MS_"), fl.str.contains("log-transformed")
    ratio_like = (d.measurement_type == "relative/ratio") & ratio_term | is_norm
    v = {"V1": lambda x: raw & ((med < 0) | (neg > x)), "V2": lambda x: raw & ms & (med >= 0) & (med < x),
         "V3": lambda x: logf & (med.abs() > x), "V4": lambda x: d.median_converted > lim * x,
         "V5": lambda x: ratio_like & (med > x), "F": lambda x: d.pct_format_corrupt >= x}
    used = {"V1": NEG_PCT, "V2": MS_MIN, "V3": LOG_MAX, "V4": 1.0, "V5": RATIO_MAX, "F": FORMAT_PCT}
    unit = {"V1": "% of non-zero values negative", "V2": "median below", "V3": "|median| above",
            "V4": "multiple of the physical limits", "V5": "median above", "F": "% of cells corrupted"}
    sens = [{"rule": k, "threshold": x, "threshold_meaning": unit[k], "used": "yes" if x == used[k] else "",
             "matrices_flagged": int(v[k](x).sum()), "rationale": RATIONALE[k] if x == used[k] else ""}
            for k in v for x in SENSITIVITY[k]]
    pd.DataFrame(sens).to_csv(OUT / "threshold_sensitivity.tsv", sep="\t", index=False)
    rules = {
        "V1 raw signal with negative values": v["V1"](NEG_PCT),
        "V2 raw MS signal below 1": v["V2"](MS_MIN),
        "V3 log-labelled values not on a log scale": v["V3"](LOG_MAX),
        "V4 physically impossible concentration or content": v["V4"](1.0),
        "V5 ratio or IS-normalised unit with raw-scale values": v["V5"](RATIO_MAX),
    }
    notes = {
        "A1 bare 'relative' label with raw-scale values": (d.measurement_type == "relative (unspecified)") & (med > REL_RAW),
        "A2 NMR_BINNED block declared 'Peak area'": d.mwtab_blocks.str.contains("NMR_BINNED") & d.units_raw.str.fullmatch("(?i)peak area"),
        "F number-format corruption (>= 5% of cells)": v["F"](FORMAT_PCT),
    }
    d["value_flags"] = ["; ".join(k for k, v in rules.items() if v.iat[i]) for i in range(len(d))]
    d["annotations"] = ["; ".join(k for k, v in notes.items() if v.iat[i]) for i in range(len(d))]
    d["inconsistent"] = (d.value_flags != "") | (d.pct_format_corrupt >= FORMAT_PCT)
    d = d.sort_values(K).reset_index(drop=True)
    grp = d[d.content_hash != ""].groupby("content_hash")
    canon = grp.analysis_id.transform("first")
    d["duplicate_group_size"] = d.content_hash.map(d[d.content_hash != ""].content_hash.value_counts()).fillna(1).astype(int)
    d["canonical_analysis"] = d.analysis_id
    d.loc[canon.index, "canonical_analysis"] = canon
    d["is_duplicate_copy"] = (d.duplicate_group_size > 1) & (d.canonical_analysis != d.analysis_id) | d.duplicated(["analysis_id"])
    dup = d[d.duplicate_group_size > 1]
    groups = dup.groupby("content_hash").agg(records=("analysis_id", "size"), analyses=("analysis_id", lambda s: ", ".join(s)),
                                            studies=("study_id", lambda s: ", ".join(dict.fromkeys(s))),
                                            units=("units_raw", lambda s: " || ".join(dict.fromkeys(s))),
                                            categories=("units_category", lambda s: " || ".join(dict.fromkeys(s))))
    groups["category_conflict"] = groups.categories.str.contains(r"\|\|")
    groups.sort_values("records", ascending=False).to_csv(OUT / "duplicate_groups.tsv", sep="\t")
    d.drop(columns=["content_hash"]).to_csv(OUT / "value_consistency_per_matrix.tsv", sep="\t", index=False)
    txt = [f"valid mwTab matrices: {len(d):,}; with a non-zero median: {int(med.notna().sum()):,}",
           f"duplicate groups: {len(groups):,} covering {int(groups.records.sum()):,} records; copies removed: {int(d.is_duplicate_copy.sum()):,}; "
           f"groups whose copies carry different display categories: {int(groups.category_conflict.sum()):,}",
           f"flagged inconsistent (any V rule or format corruption): {int(d.inconsistent.sum()):,} "
           f"(canonical copies: {int((d.inconsistent & ~d.is_duplicate_copy).sum()):,})", ""]
    for k, v in {**rules, **notes}.items():
        txt.append(f"  {k:55s} {int(v.sum()):>5,}")
    txt += ["", "by display category (canonical copies): matrices / inconsistent / annotated A1"]
    c = d[~d.is_duplicate_copy & med.notna()]
    for cat, g in c.groupby(c.units_category.replace("", "not declared")):
        txt.append(f"  {cat:26s} {len(g):>5,} {int(g.inconsistent.sum()):>5,} {int(g.annotations.str.contains('A1').sum()):>5,}")
    (OUT / "value_consistency_summary.txt").write_text("\n".join(txt) + "\n")
    print("\n".join(txt))


if __name__ == "__main__":
    main()
