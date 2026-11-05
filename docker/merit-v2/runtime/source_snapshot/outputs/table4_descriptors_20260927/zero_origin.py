#!/usr/bin/python3
"""Origin of every datatable zero, traced to the paired mwTab cell. Frozen cache only.

Cohort: analyses with a valid datatable AND a valid mwTab (Figure 2B validity), any untarg_data status.
Cell pairing uses datatable_vs_mwtab_values.analyse() (validated earlier; shared samples x shared features, names
normalized). Datatable zero cells are assigned to:
  padding          zero in an all-zero datatable row whose sample is absent from the mwTab block
  reported_zero    mwTab cell is 0, datatable 0                        (zero carried over)
  filled_missing   mwTab cell empty / NA-type token / other text, datatable 0   (missing written as 0); also split into
                   filled_empty, filled_token (explicit missing-value tokens, cf. Text S10) and filled_other_text
  value_to_zero    mwTab cell is a non-zero number, datatable 0 (below print precision, or negative)
  unattributed     remaining zeros (features or rows that could not be paired one-to-one)
Also counted: mwTab missing cells that became a non-zero datatable number (filled_missing_nonzero).
Output (this folder): zero_origin_per_analysis.tsv
"""
from __future__ import annotations

import csv
import importlib.util
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot")
HERE = Path(__file__).resolve().parent
_sp = importlib.util.spec_from_file_location(
    "dvm", ROOT / "outputs/datatable_vs_mwtab_values_20260925/datatable_vs_mwtab_values.py")
dvm = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(dvm)


def one(pair: tuple[str, str, int, int, int]) -> dict:
    st, an, n_f, n_s, n_zero = pair
    try:
        r = dvm.analyse((st, an))
    except Exception as e:
        return {"study_id": st, "analysis_id": an, "error": f"{type(e).__name__}: {e}"}
    g = lambda k: int(r.get(k, 0) or 0)
    padding = g("datatable_only_rows_all_zero") * n_f
    reported = g("zero->same_as_printed")
    filled = g("empty->zero") + g("missing_token->zero") + g("other_text->zero")
    to_zero = g("number->rounds_to_zero") + g("number->negative_to_zero") + g("number->positive_to_zero")
    return {"study_id": st, "analysis_id": an, "n_features": n_f, "n_samples": n_s, "cells": n_f * n_s,
            "datatable_zero_cells": n_zero, "padding_rows": g("datatable_only_rows_all_zero"),
            "padding": padding, "reported_zero": reported, "filled_missing": filled, "value_to_zero": to_zero,
            "filled_empty": g("empty->zero"), "filled_token": g("missing_token->zero"), "filled_other_text": g("other_text->zero"),
            "unattributed": n_zero - padding - reported - filled - to_zero,
            "filled_missing_nonzero": g("empty->different_number") + g("missing_token->different_number")
            + g("other_text->different_number"),
            "n_shared_cells": g("n_shared_cells"), "sample_relation": r.get("sample_relation", "")}


def main() -> None:
    assert sum(len(f) for _, _, f in os.walk(dvm.CACHE)) == 42559, "frozen cache changed"
    v = {(r["study_id"], r["analysis_id"]): r for r in csv.DictReader(dvm.VALIDITY.open(), delimiter="\t")}
    t4 = [r for r in csv.DictReader((HERE / "table4_per_analysis.tsv").open(), delimiter="\t") if r["source"] == "datatable"]
    jobs = [(r["study_id"], r["analysis_id"], int(r["n_features"]), int(r["n_samples"]), int(r["n_zero"]))
            for r in t4 if v[(r["study_id"], r["analysis_id"])]["mwtab"] == "valid"]
    print("datatable analyses with a valid mwTab:", len(jobs), "of", len(t4))
    with ProcessPoolExecutor(max_workers=24) as ex:
        rows = list(ex.map(one, jobs, chunksize=8))
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with (HERE / "zero_origin_per_analysis.tsv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n", restval="")
        w.writeheader()
        w.writerows(rows)
    ok = [r for r in rows if "error" not in r]
    tot = {k: sum(r[k] for r in ok) for k in ("cells", "datatable_zero_cells", "padding", "reported_zero",
                                              "filled_missing", "value_to_zero", "unattributed", "filled_missing_nonzero")}
    print("errors", len(rows) - len(ok))
    z = tot["datatable_zero_cells"]
    print(f"datatable cells {tot['cells']:,}; zero cells {z:,} ({100 * z / tot['cells']:.2f}% of cells)")
    for k in ("padding", "reported_zero", "filled_missing", "value_to_zero", "unattributed"):
        print(f"  {k:15s} {tot[k]:>12,}  {100 * tot[k] / z:5.1f}% of zeros  {100 * tot[k] / tot['cells']:5.2f}% of cells"
              f"  analyses {sum(1 for r in ok if r[k] > 0):,}")
    print(f"  mwTab missing -> non-zero datatable number: {tot['filled_missing_nonzero']:,}")


if __name__ == "__main__":
    main()
