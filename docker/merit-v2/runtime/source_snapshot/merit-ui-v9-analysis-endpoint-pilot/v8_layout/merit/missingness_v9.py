"""Missing entries and zeros (named "sample-level missingness" until 2026-10-27) of one matrix (one analysis from one endpoint): the first
scored metric of Analytical QC. The rule is unchanged by the rename; zeros are reported as zeros and never counted as missing entries.

Every cell of an ML-eligible sample is in one state. Four items are scored, each 100 x (1 - share), where share = the item's cells /
all cells of the ML-eligible samples (since 2026-10-17; before, items 1-2 used the median sample, which hid samples without any number,
e.g. ST000014 / AN000031 mwTab: 5 of 72 samples empty, median sample clean, score 100). The metric is the lowest item that applies.
Reasoning: merit/manuscript/supplementary/sample_missingness_scoring/sample_missingness_scoring.md.
  I1 missing entries       empty or non-numeric (the six classes of Table S2C, scored together, reported apart); every endpoint.
  I2 zeros, meaning unknown  zeros the deposit cannot explain: every mwTab and untarg_data zero (each endpoint on its own); in a
                           datatable, zeros that are also 0 in mwTab, not traced, or in a datatable without a paired mwTab.
  I3 zeros hiding a missing value   datatable zeros whose paired mwTab cell has no number (empty, token, text). Paired datatables only.
  I4 zeros replacing a value        datatable zeros whose paired mwTab cell is a non-zero number (negative, positive, or a value
                           that rounds to 0 at the datatable's printed precision). Paired datatables only;
                           flagged whenever a negative mwTab value was written as 0, also when it rounds to 0 (datatable files hold no
                           negative number, so rounding and clipping cannot be told apart in the datatable).
Zeros are never counted as missing entries: I2 to I4 are zero categories (Table 4 and Figure 4D are unchanged).
The whole-matrix counts (every position, as Table 4) are kept beside the items. Tier 1 sample alignment rows are not samples and
are not scored; their number is reported.
A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
Datatable zeros are traced to the paired mwTab cell with the same sample ID and the same feature name; where a name or an ID is repeated,
every candidate cell is compared and the zero is classified only when all agree (otherwise it stays "not traced").
Inputs: the v9 roster (numeric cells), per-position cell counts (merit/cell_counts_v9.py) and, for paired datatables, per-position
zero provenance (merit/manuscript/supplementary/sample_missingness_scoring/datatable_zero_provenance_per_sample.py).
"""
from __future__ import annotations

import statistics
from typing import Any

from merit import structural_v9 as sv

CLASSES = ("empty", "missing-value token", "detection-limit entry", "non-finite number", "malformed number", "other text")
ZERO = len(CLASSES)                  # index of the zero count in a per-position count list (classes first, zero last)
PROVENANCE = ("also_zero", "hidden_empty", "hidden_token", "hidden_text", "rounded", "negative", "positive", "rounded_negative",   # one-to-one (manuscript)
              "r_also_zero", "r_hidden_empty", "r_hidden_token", "r_hidden_text", "r_hidden_mixed", "r_rounded", "r_negative", "r_positive",
              "r_rounded_negative", "r_replaced_mixed")   # a repeated name or ID, every candidate mwTab cell agreeing; *_rounded_negative are parts of *_rounded
_P = {k: i for i, k in enumerate(PROVENANCE)}
ITEMS = {"invalid": "Missing entries", "unknown_zero": "Zeros, meaning unknown", "hidden": "Zeros hiding a missing value",
         "replaced": "Zeros replacing a value"}
PARAMETERS: dict[str, tuple[float, float, float, float]] = {}
DEFAULT_PARAMS: dict[str, float] = {}


def _q(values: list[float], f: float) -> float:
    v = sorted(values)
    k = (len(v) - 1) * f
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def compute(roster: list[list], counts: list[list[int]], n_features: int, overrides: dict[str, dict[str, Any]] | None = None,
            provenance: list[list[int]] | None = None) -> dict[str, Any]:
    """provenance: per position, counts in PROVENANCE order (paired datatable); None for any other matrix."""
    cells = n_features * len(roster)
    numeric = sum(int(r[sv.I_NUM]) for r in roster)
    zeros = sum(int(x[ZERO]) for x in counts)
    non_numeric = cells - numeric
    rec: dict[str, Any] = {
        "n_features": n_features, "paired_with_mwtab": provenance is not None,
        "tier1_alignment_rows": sum(1 for r in roster if r[sv.I_ALIGN]),
        "matrix": {"positions": len(roster), "cells": cells, "empty_or_non_numeric": non_numeric, "zero": zeros,
                   "by_class": {c: sum(int(x[i]) for x in counts) for i, c in enumerate(CLASSES)},
                   "pct_empty_or_non_numeric": 100 * non_numeric / cells if cells else None, "pct_zero": 100 * zeros / cells if cells else None},
    }
    positions = sv.effective_positions(roster, overrides)
    inv, unk = [], []
    no_number = only_zeros = 0
    tot = {"invalid": 0, "unknown_zero": 0, "hidden": 0, "replaced": 0}
    hit = {k: 0 for k in tot}
    sub = {"invalid": dict.fromkeys(CLASSES, 0),
           "unknown_zero": {"also zero in mwTab": 0, "not traced": 0, "deposited zero": 0, "of which via a repeated name or ID": 0},
           "hidden": {"mwTab empty": 0, "mwTab token": 0, "mwTab text": 0, "mwTab cells of different kinds, none a number": 0, "of which via a repeated name or ID": 0},
           "replaced": {"negative": 0, "positive": 0, "rounded": 0, "numbers of different kinds": 0, "of which via a repeated name or ID": 0}}
    negative_rounded = 0
    for i, (p, c) in enumerate(zip(positions, counts)):
        row = p["row"]
        if row[sv.I_ALIGN] or p["excluded"] or not p["eligible"] or not n_features:
            continue
        raw = provenance[i] if provenance is not None else []
        g = lambda k: int(raw[_P[k]]) if _P[k] < len(raw) else 0
        hid_via = g("r_hidden_empty") + g("r_hidden_token") + g("r_hidden_text") + g("r_hidden_mixed")
        rep_via = g("r_rounded") + g("r_negative") + g("r_positive") + g("r_replaced_mixed")
        n_inv = n_features - int(row[sv.I_NUM])
        n_hid = g("hidden_empty") + g("hidden_token") + g("hidden_text") + hid_via
        n_rep = g("rounded") + g("negative") + g("positive") + rep_via
        n_unk = int(c[ZERO]) - n_hid - n_rep
        for k, n in (("invalid", n_inv), ("unknown_zero", n_unk), ("hidden", n_hid), ("replaced", n_rep)):
            tot[k] += n; hit[k] += n > 0
        inv.append(n_inv / n_features); unk.append(n_unk / n_features)
        no_number += int(row[sv.I_NUM]) == 0
        only_zeros += int(row[sv.I_NUM]) > 0 and int(row[sv.I_NUM]) == int(c[ZERO])   # every numeric value is 0; other cells may be invalid
        for j, cl in enumerate(CLASSES): sub["invalid"][cl] += int(c[j])
        also = g("also_zero") + g("r_also_zero")
        if provenance is not None:
            sub["unknown_zero"]["also zero in mwTab"] += also; sub["unknown_zero"]["not traced"] += n_unk - also
            sub["unknown_zero"]["of which via a repeated name or ID"] += g("r_also_zero")
        else:
            sub["unknown_zero"]["deposited zero"] += n_unk
        sub["hidden"]["mwTab empty"] += g("hidden_empty") + g("r_hidden_empty"); sub["hidden"]["mwTab token"] += g("hidden_token") + g("r_hidden_token")
        sub["hidden"]["mwTab text"] += g("hidden_text") + g("r_hidden_text"); sub["hidden"]["mwTab cells of different kinds, none a number"] += g("r_hidden_mixed")
        sub["hidden"]["of which via a repeated name or ID"] += hid_via
        sub["replaced"]["rounded"] += g("rounded") + g("r_rounded"); sub["replaced"]["negative"] += g("negative") + g("r_negative")
        sub["replaced"]["positive"] += g("positive") + g("r_positive"); sub["replaced"]["numbers of different kinds"] += g("r_replaced_mixed")
        sub["replaced"]["of which via a repeated name or ID"] += rep_via
        negative_rounded += g("rounded_negative") + g("r_rounded_negative")
    n = len(inv)
    elig_cells = n * n_features
    rec["samples"] = {"ml_eligible": n, "cells": elig_cells, "with_no_numeric_value": no_number, "with_only_zeros": only_zeros}
    items: dict[str, dict[str, Any]] = {}
    if n:
        items["invalid"] = {"share": tot["invalid"] / elig_cells, "statistic": "all cells",
                            "median_sample": statistics.median(inv), "q1_sample": _q(inv, .25), "q3_sample": _q(inv, .75), "max_sample": max(inv)}
        items["unknown_zero"] = {"share": tot["unknown_zero"] / elig_cells, "statistic": "all cells",
                                 "median_sample": statistics.median(unk), "q1_sample": _q(unk, .25), "q3_sample": _q(unk, .75), "max_sample": max(unk)}
        if provenance is not None:
            items["hidden"] = {"share": tot["hidden"] / elig_cells, "statistic": "all cells"}
            items["replaced"] = {"share": tot["replaced"] / elig_cells, "statistic": "all cells"}
        for k, it in items.items():
            it.update(score=100 * (1 - it["share"]), cells=tot[k], samples=hit[k], by=sub[k], label=ITEMS[k])
    rec["items"] = items
    rec["negatives_written_as_zero"] = sub["replaced"]["negative"] + negative_rounded > 0
    rec["negative_cells_written_as_zero"] = {"beyond rounding": sub["replaced"]["negative"], "by rounding": negative_rounded}
    if items:
        low = min(it["score"] for it in items.values())
        rec["score"] = low
        rec["set_by"] = [k for k, it in items.items() if it["score"] == low]
        rec["not_assessable"] = None
    else:
        rec["score"], rec["set_by"] = None, []
        rec["not_assessable"] = "no feature" if not n_features else "no ML-eligible sample"
    rec["edited_positions"] = sum(1 for p in positions if p["edited"])
    return rec
