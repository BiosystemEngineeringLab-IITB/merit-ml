#!/usr/bin/python3
"""UNITS declarations of the untargeted results file (untarg_data), from the mwTab RESULTS_FILE line. 18 Sep 2026 cache.

The untarg_data table itself carries no unit. The declaration that belongs to it is the UNITS attribute of the mwTab line
naming the results file, e.g.
    MS:MS_RESULTS_FILE <tab> ST000258_AN000409_Results.txt <tab> UNITS:normalized peak area <tab> Has m/z:Yes <tab> ...
(NM:NMR_RESULTS_FILE for NMR). This is a different field from the data-block line '<BLOCK>:UNITS' read by
extract_units.py, which describes the mwTab data block.

For each of the 1,887 analyses with a valid untarg_data table (Figure 2B): the analysis's mwTab file is read (split on
'\\n' only); the first line whose key is MS:MS_RESULTS_FILE or NM:NMR_RESULTS_FILE is taken (a '#...' prefix before the
key, found in a few files, is accepted; repeated lines are identical); its tab-separated attributes are parsed and the
text after 'UNITS:' is the declaration. The declaration is cleaned and classified with the SAME rules as the data-block
declarations (extract_units.clean / classify / FAMILY): placeholder entries and numbers alone -> not declared.
Outputs (this folder): untarg_units_per_analysis.tsv, untarg_units_raw_by_family.tsv, untarg_units_summary.txt
"""
from __future__ import annotations

import collections
import csv
import re
from pathlib import Path

import extract_units as eu

CACHE = eu.CACHE
VALIDITY = eu.VALIDITY
OUT = Path(__file__).resolve().parent
RESULTS_KEY = re.compile(r"^(#\S*\s+)?(?:MS:MS|NM:NMR)_RESULTS_FILE\b")


def results_units(path: Path) -> tuple[str, str, str, str]:
    """(status, results file name, raw UNITS text, prefix found before the key) for one mwTab file."""
    if not path.is_file():
        return "no mwTab file", "", "", ""
    for ln in path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n"):
        k = RESULTS_KEY.match(ln)
        if k:
            prefix = (k.group(1) or "").strip()
            parts = [p.strip() for p in ln.split("\t")[1:]]
            fname = parts[0] if parts else ""
            for p in parts[1:]:
                m = re.match(r"^UNITS\s*:\s*(.*)$", p, re.I)
                if m:
                    return "results-file line with UNITS", fname, m.group(1).strip(), prefix
            return "results-file line without UNITS", fname, "", prefix
    return "no results-file line", "", "", ""


def main() -> None:
    eu.self_test()
    recs = [r for r in csv.DictReader(VALIDITY.open(), delimiter="\t") if r["untarg_data"] == "valid"]
    assert len(recs) == 1887
    block = {(r["study_id"], r["analysis_id"]): r for r in csv.DictReader((OUT / "units_per_analysis.tsv").open(), delimiter="\t")}
    rows = []
    for r in recs:
        st, an = r["study_id"], r["analysis_id"]
        status, fname, raw, prefix = results_units(CACHE / st / an / "mwtab" / f"{an}_mwtab.txt")
        decl = eu.clean(raw)
        c = eu.classify(raw)
        if raw and not decl:
            status = "results-file line with a placeholder or a number"
        b = block[(st, an)]
        rows.append({"study_id": st, "analysis_id": an, "source_of_declaration": status, "results_file": fname,
                     "units_raw": decl, "units_as_written": raw, "units_family": eu.FAMILY[c["display_category"]],
                     "units_category": c["display_category"], "measurement_type": c["measurement_type"],
                     "processing_flags": c["processing_flags"], "rules_fired": c["rules"], "interpretation_note": c["note"],
                     "data_block_units_raw": b["units_raw"], "data_block_units_family": b["units_family"],
                     "prefix_before_key": prefix})
    with (OUT / "untarg_units_per_analysis.tsv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    dec = [r for r in rows if r["units_raw"]]
    distinct = collections.Counter(r["units_raw"] for r in dec)
    first = {}
    for r in dec:
        first.setdefault(r["units_raw"], r)
    known = {r["units_raw"] for r in block.values() if r["units_raw"]}
    with (OUT / "untarg_units_raw_by_family.tsv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["units_raw", "analyses", "declaration_family", "fine_category", "measurement_type", "processing_flags",
                    "rules_fired", "interpretation_note", "also_a_data_block_declaration"])
        for v, n in sorted(distinct.items(), key=lambda x: (first[x[0]]["units_family"], -x[1], x[0])):
            f = first[v]
            w.writerow([v, n, f["units_family"], f["units_category"], f["measurement_type"], f["processing_flags"],
                        f["rules_fired"], f["interpretation_note"], "yes" if v in known else "no"])
    status = collections.Counter(r["source_of_declaration"] for r in rows)
    fam = collections.Counter(r["units_family"] for r in dec)
    famd = collections.Counter(first[v]["units_family"] for v in distinct)
    both = [r for r in dec if r["data_block_units_raw"]]
    txt = [f"valid untarg_data analyses: {len(rows):,}; with a results-file UNITS declaration: {len(dec):,} "
           f"({100 * len(dec) / len(rows):.1f}%); not declared: {len(rows) - len(dec):,}",
           f"distinct declarations: {len(distinct):,} ({len({v.lower().strip() for v in distinct}):,} ignoring capitalisation; "
           f"{sum(1 for n in distinct.values() if n == 1):,} used by one analysis; {sum(1 for v in distinct if v not in known):,} "
           f"not among the data-block declarations)", "", "where the declaration was looked for:"]
    txt += [f"  {k:52s} {v:>6,}" for k, v in status.most_common()]
    txt += ["", "UNITS declaration family (results file):"]
    txt += [f"  {k:32s} {v:>6,} analyses ({100 * v / len(dec):5.1f}%)  {famd[k]:>4,} distinct declarations" for k, v in fam.most_common()]
    txt += ["", f"analyses with both a results-file and a data-block declaration: {len(both):,}; same text ignoring capitalisation: "
                f"{sum(1 for r in both if r['units_raw'].lower() == r['data_block_units_raw'].lower()):,}; same family: "
                f"{sum(1 for r in both if r['units_family'] == r['data_block_units_family']):,}"]
    (OUT / "untarg_units_summary.txt").write_text("\n".join(txt) + "\n")
    print("\n".join(txt))


if __name__ == "__main__":
    main()
