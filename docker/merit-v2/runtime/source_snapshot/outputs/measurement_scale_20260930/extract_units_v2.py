#!/usr/bin/python3
"""Declared measurement units per analysis, from the mwTab data-block UNITS line. 18 Sep 2026 frozen cache.

For every study-analysis record (Figure 2B, 6,696), the analysis's mwTab file is read (split on '\\n' only). For each
non-EXTENDED data block '<BLOCK>_START', the units value is the text after the first tab of the line starting with
'<BLOCK>:UNITS' (e.g. 'MS_METABOLITE_DATA:UNITS<spaces><tab>Peak height'), stripped of whitespace and '\\r'. If the line
is absent, or the value is empty, units are left BLANK. Several blocks with different units are joined with ' | '.
The declared units describe the analysis's mwTab matrix; the datatable is derived from it and shares them. They are NOT
assumed to describe untarg_data, which is a separately deposited feature table (Figure S panel B uses mwTab only).
  Rule 1 pre-empts non-abundance declarations (whole-value m/z, Da, ppm, ppb; presence/absence). For rules 2-5 the text
  is matched after removing 'm/z' (so 'Peak area (m/z)' is an area, not molar). Undeclared tokens ('-', 'NA',
  'Not applicable', 'Unspecified', ...) are left blank; the mis-encoded micro sign 'Âµ' / 'Î¼' is read as 'µ'.
  Known edge case kept by the first-match rule: a mixed declaration such as 'ng/mL ... (in peak area ratio)' is
  classed normalised/relative.
Outputs (this folder): units_per_analysis.tsv, units_raw_by_category.tsv (every distinct raw value, its category and
count, for review), units_summary.txt
"""
from __future__ import annotations

import collections
import csv
import re
from pathlib import Path

CACHE = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/mw-dump-latest-18thSep-2026-version")
VALIDITY = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit/manuscript/figures/Figure-2/Used-in-manuscript/"
                "figure2b_source_availability_per_analysis.tsv")
OUT = Path(__file__).resolve().parent
NOT_DECLARED = {"", "-", "--", "na", "n/a", "none", "not applicable", "not available", "unknown", "unspecified", "."}
NOT_DECLARED_PREFIX = re.compile(r"^(?:n/a|na|not applicable)\s*\(", re.I)   # 'N/A (structure elucidation)' -> blank
RULES = [
    ("other declared", r"^\s*m/z\s*$|^\s*m/[δΔ]m\s*$|^\s*da\s*$|^\s*ppm\s*$|^\s*ppb\s*$|presence|absence"),
    ("log-transformed", r"\blog|\bln\b"),
    ("normalised/relative", r"normali[sz]|relative|\bratios?\b|fold|percent|%|scaled|z-?score|\bcorrected\b|\bnorm\b|"
                            r"per (?:tic|total)|/\s*(?:is|istd|internal)|arbitrary.*normal|rescaled|"
                            r"enrichment|labell?ing|isotopomer|\bmpe\b|proportion|"
                            r"(?:area|height|intensit|count|\bcpm|\bcps|\bauc|signal|abundan)\w*\s*(?:/|per)\s*"
                            r"(?:\d|[mnupfµμ]?g|ml|cells?|cfu|protein|tissue|dna|wet)"),
    ("concentration", r"mols?\b|mol/|mol per|moles?\b|molars?\b|\b[mnupfµμ]?m\b|\b[mnupfµμ]?g\s*/|\b[mnupfµμ]?g\s+per|"
                      r"/\s*(?:ml|l|dl|mg|g)\b|concentration|\bmg\b|\bug\b|\bng\b|\bpg\b|"
                      r"\b(?:nano|pico|micro|milli|femto)?grams?\b|parts per (?:million|billion|trillion)|\bppbv\b"),
    ("intensity/area/counts", r"area|height|intensit|intersity|abundan|abudan|abundace|count|\bau\b|a\.u\.|arbitrary|"
                              r"\barb\w*\s+units?\b|response|signal|\bion|\bauc\b|^\s*peaks?\s*$|\bsi\b|\bcps\b|\bcpm\b|\bua\b"),
]
COMPILED = [(c, re.compile(p, re.I)) for c, p in RULES]


def clean(raw: str) -> str:
    """Strip; repair the common UTF-8/Latin-1 mojibake of the micro sign ('Âµ' -> 'µ'); undeclared tokens -> ''."""
    t = raw.strip().replace("Âµ", "µ").replace("Î¼", "µ")
    return "" if t.lower() in NOT_DECLARED or NOT_DECLARED_PREFIX.match(t) else t


def category(raw: str) -> str:
    raw = clean(raw)
    if not raw:
        return ""
    no_mz = re.sub(r"m\s*/\s*(?:z|[δΔ]m)", " ", raw, flags=re.I)
    for i, (c, rx) in enumerate(COMPILED):
        if rx.search(raw if i == 0 else no_mz):
            return c
    return "other declared"


def units_of(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "", ""
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    decl = {}
    for ln in lines:
        m = re.match(r"^([A-Z_]+_DATA):UNITS\b[^\t]*\t(.*)$", ln)
        if m:
            decl.setdefault(m.group(1), m.group(2).strip().strip("\r").strip())
    blocks = [ln.strip()[:-len("_START")] for ln in lines
              if ln.strip().endswith("_DATA_START") and not ln.strip().startswith("EXTENDED")]
    vals = list(dict.fromkeys(decl.get(b, "") for b in blocks))
    vals = [clean(v) for v in vals]
    vals = [v for v in dict.fromkeys(vals) if v]
    return " | ".join(vals), "|".join(dict.fromkeys(blocks))


def main() -> None:
    recs = list(csv.DictReader(VALIDITY.open(), delimiter="\t"))
    assert len(recs) == 6696
    rows = []
    for r in recs:
        st, an = r["study_id"], r["analysis_id"]
        raw, blocks = units_of(CACHE / st / an / "mwtab" / f"{an}_mwtab.txt")
        cats = sorted({category(v.strip()) for v in raw.split(" | ")} - {""}) if raw else []
        rows.append({"study_id": st, "analysis_id": an, "mwtab_blocks": blocks, "units_raw": raw,
                     "units_category": cats[0] if len(cats) == 1 else ("multiple: " + "; ".join(cats) if cats else "")})
    with (OUT / "units_per_analysis.tsv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    raw_counts = collections.Counter((category(r["units_raw"]) if " | " not in r["units_raw"] else "(multiple)", r["units_raw"])
                                     for r in rows if r["units_raw"])
    with (OUT / "units_raw_by_category.tsv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["units_category", "units_raw", "analyses"])
        for (c, v), n in sorted(raw_counts.items(), key=lambda x: (x[0][0], -x[1])):
            w.writerow([c, v, n])
    cat = collections.Counter(r["units_category"] or "(blank)" for r in rows)
    txt = [f"analyses: {len(rows):,}; with a units value: {sum(1 for r in rows if r['units_raw']):,}; blank: {cat['(blank)']:,} "
           f"(of which no mwTab data block: {sum(1 for r in rows if not r['mwtab_blocks']):,})",
           f"distinct raw values: {len(raw_counts):,}"]
    txt += [f"  {k:32s} {v:>6,}" for k, v in cat.most_common()]
    for c, _ in RULES + [("other declared", "")]:
        top = sorted(((n, v) for (cc, v), n in raw_counts.items() if cc == c), reverse=True)[:15]
        txt.append(f"{c}: " + " | ".join(f"{v} ({n})" for n, v in top))
    txt += ["", "RULES as run (order = priority):"] + [f"  {c}: {p}" for c, p in RULES]
    (OUT / "units_summary.txt").write_text("\n".join(txt) + "\n")
    print("\n".join(txt))


if __name__ == "__main__":
    main()
