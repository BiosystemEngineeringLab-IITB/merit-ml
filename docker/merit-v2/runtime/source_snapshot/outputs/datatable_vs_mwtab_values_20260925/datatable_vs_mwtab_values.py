#!/usr/bin/python3
"""Is /datatable/ a processed version of mwTab? Sample, cell and imputation comparison. Frozen cache only.

Scope: the 4,464 analyses where mwTab and datatable are valid and untarg_data is not (Figure 2C
mwtab ∩ datatable). Inputs: <AN>_datatable_slash.txt and <AN>_mwtab.txt (main, non-EXTENDED *_DATA
blocks; the datatable follows the main block). Sample IDs are whitespace-stripped in both sources.

Samples  datatable row IDs vs mwTab sample-column IDs (exact match): identical / datatable ⊃ mwTab /
         mwTab ⊃ datatable / partial / disjoint; duplicate IDs; datatable-only rows that are all zero.
Features names matched after strip, lowercase, whitespace collapse, ',' -> '_', "'" -> '^'; a name that
         occurs twice in either source is excluded as ambiguous.
Cells    shared samples x shared features. mwTab state: missing_token | empty | zero | number | other_text.
         datatable outcome for numeric mwTab cells: same_as_printed (rel. 1e-6 or 4 s.f.) |
         rounded_to_printed_precision | rounds_to_zero | negative_to_zero | positive_to_zero |
         different_number | missing; tolerance = half a unit at the datatable's dominant printed precision
         plus the mwTab token's own half-unit. For non-numeric mwTab cells: zero | different_number | missing.
Imputation  for mwTab missing/empty cells whose datatable value is a non-zero number, the first matching
         candidate (same tolerance) among: feature min, min/2, min/5, min/10, feature mean, feature median,
         matrix min, matrix min/2 -- where feature statistics use that feature's finite, positive mwTab
         values on shared samples. Otherwise 'no simple rule'.
Outputs  per_analysis.tsv, transitions.tsv, imputation_rules.tsv, summary.txt (this folder)
"""
from __future__ import annotations

import collections
import csv
import math
import os
import re
import statistics
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot")
CACHE = ROOT / "mw-dump-latest-18thSep-2026-version"
VALIDITY = ROOT / "merit/manuscript/figures/Figure-2/Used-in-manuscript/figure2b_source_availability_per_analysis.tsv"
OUT = Path(__file__).resolve().parent
MISSING_TOKENS = {"na", "n/a", "null", "nan", "nd", "n.d.", "bdl", "bql", "nq", "loq", "lod", "llod", "lloq",
                  "bloq", "<lod", "<loq", "<lloq", "missing", "not detected", ".", "-", "none"}
CANDIDATES = ("feature_min", "feature_min/2", "feature_min/5", "feature_min/10", "feature_mean",
              "feature_median", "matrix_min", "matrix_min/2")


def key(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower()).replace(",", "_").replace("'", "^")


def parse(tok: str) -> tuple[float | None, float]:
    t = tok.strip()
    try:
        v = float(t)
    except ValueError:
        return None, 0.0
    if not math.isfinite(v):
        return None, 0.0
    try:
        e = Decimal(t).as_tuple().exponent
        q = 0.5 * 10.0 ** e if isinstance(e, int) and e < 0 else 0.0
    except InvalidOperation:
        q = 0.0
    return v, q


def mw_state(tok: str) -> str:
    t = tok.strip()
    if t == "":
        return "empty"
    if t.lower() in MISSING_TOKENS:
        return "missing_token"
    v, _ = parse(t)
    if v is None:
        return "other_text"
    return "zero" if v == 0 else "number"


def close(a: float, qa: float, b: float, qb: float) -> bool:
    return abs(a - b) <= qa + qb + 1e-9 * max(abs(a), abs(b))


def read_datatable(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")   # newline only: stray control characters do not break a line
    feats = lines[0].split("\t")[2:]
    ids, rows = [], []
    for ln in lines[1:]:
        if ln.strip():
            p = ln.split("\t")
            ids.append(p[0].strip())
            rows.append(p[2:] + [""] * (len(feats) - len(p[2:])))
    return ids, feats, rows


def read_mwtab(path: Path):
    """Main (non-EXTENDED) data blocks only; sample IDs stripped.

    Returns (sample IDs as listed, feature names, {(sample, feature_raw): token}, ambiguous IDs).
    A header that repeats each sample ID while the value rows carry one column per unique ID is
    aligned on the unique IDs; any other repeated ID is ambiguous and excluded from cell pairing.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")   # newline only: stray control characters do not break a line
    samples, feats, cell, ambiguous, i = [], [], {}, set(), 0
    while i < len(lines):
        s0 = lines[i].strip()
        if s0.endswith("_DATA_START") and not s0.startswith("EXTENDED"):
            hdr = [x.strip() for x in (lines[i + 1].split("\t")[1:] if i + 1 < len(lines) else [])]
            j, block = i + 2, []
            while j < len(lines) and not lines[j].strip().endswith("_DATA_END"):
                block.append(lines[j].split("\t"))
                j += 1
            uniq = list(dict.fromkeys(x for x in hdr if x))
            width = max((len(p) - 1 for p in block if p[0].strip().lower() != "factors"), default=len(hdr))
            if len(uniq) < len(hdr) and width == len(uniq):
                sids = uniq                                   # repeated header, one value column per ID
            else:
                sids = hdr
                ambiguous |= {x for x, c in collections.Counter(x for x in hdr if x).items() if c > 1}
            samples += [x for x in sids if x and x not in samples]   # empty header cells are not samples
            for p in block:
                name = p[0]
                if name.strip() and name.strip().lower() != "factors":
                    feats.append(name)
                    for k, sid in enumerate(sids):
                        if not sid:
                            continue
                        cell.setdefault((sid, name), p[k + 1] if k + 1 < len(p) else "")
            i = j
        i += 1
    return samples, feats, cell, ambiguous


def print_half_unit(rows: list[list[str]]) -> float:
    """Half a unit in the datatable's dominant printed decimal place (e.g. 0.005 for 2 decimals).

    Zero tokens are ignored: zero-filled cells are often printed as a bare "0" and would otherwise
    make a 2-decimal table look like an integer table.
    """
    dec = collections.Counter(len(t.split(".")[1]) if "." in t else 0
                              for r in rows for t in (x.strip() for x in r)
                              if re.fullmatch(r"-?\d+(\.\d+)?", t) and float(t) != 0)
    return 0.5 * 10.0 ** -(dec.most_common(1)[0][0] if dec else 0)


def same_as_printed(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(abs(a), abs(b)) or f"{a:.4g}" == f"{b:.4g}"


def analyse(pair: tuple[str, str]) -> dict:
    st, an = pair
    d_ids, d_feats, d_rows = read_datatable(CACHE / st / an / "tabular" / f"{an}_datatable_slash.txt")
    m_ids, m_feats, m_cell, m_amb = read_mwtab(CACHE / st / an / "mwtab" / f"{an}_mwtab.txt")
    q_an = print_half_unit(d_rows)
    rec: dict = {"study_id": st, "analysis_id": an}

    # ---- samples
    dcount, mcount = collections.Counter(d_ids), collections.Counter(m_ids)
    D, M = set(d_ids), set(m_ids)
    rec.update(n_samples_datatable=len(d_ids), n_samples_mwtab=len(m_ids),
               dup_ids_datatable=sum(v > 1 for v in dcount.values()), n_shared_samples=len(D & M))
    rec["sample_relation"] = ("identical" if D == M else "datatable_superset" if D > M else
                              "mwtab_superset" if M > D else "disjoint" if not D & M else "partial")
    dt_only = [i for i, s in enumerate(d_ids) if s not in M]
    rec["datatable_only_rows"] = len(dt_only)
    rec["datatable_only_rows_all_zero"] = sum(
        all(parse(x)[0] == 0 for x in d_rows[i]) for i in dt_only)   # every cell a numeric zero; a non-numeric cell is not a zero

    # ---- features
    dk, mk = collections.Counter(key(f) for f in d_feats), collections.Counter(key(f) for f in m_feats)
    shared_f = [f for f in dk if dk[f] == 1 and mk.get(f) == 1]
    d_col = {key(f): j for j, f in enumerate(d_feats)}
    m_raw = {key(f): f for f in m_feats}
    shared_s = [s for s in d_ids if dcount[s] == 1 and s in M and s not in m_amb]
    d_row = {s: i for i, s in enumerate(d_ids)}
    rec["n_shared_features"] = len(shared_f)

    trans = collections.Counter()
    rules = collections.Counter()
    # matrix-wide positive minimum over mwTab shared cells
    feat_pos: dict[str, list[float]] = {}
    for f in shared_f:
        vals = [parse(m_cell.get((s, m_raw[f]), ""))[0] for s in shared_s]
        feat_pos[f] = [v for v in vals if v is not None and v > 0]
    allpos = [v for vs in feat_pos.values() for v in vs]
    mat_min = min(allpos) if allpos else None

    for f in shared_f:
        pos = feat_pos[f]
        stats = {}
        if pos:
            mn = min(pos)
            stats = {"feature_min": mn, "feature_min/2": mn / 2, "feature_min/5": mn / 5, "feature_min/10": mn / 10,
                     "feature_mean": statistics.fmean(pos), "feature_median": statistics.median(pos)}
        if mat_min is not None:
            stats.update({"matrix_min": mat_min, "matrix_min/2": mat_min / 2})
        for s in shared_s:
            mt = m_cell.get((s, m_raw[f]), "")
            dt = d_rows[d_row[s]][d_col[f]]
            ms = mw_state(mt)
            dv, dq = parse(dt)
            mv, mq = parse(mt)
            dq = max(dq, q_an)                      # tolerance at the datatable's printed precision
            if dv is None:
                out = "missing"
            elif ms not in ("number", "zero"):
                out = "zero" if dv == 0 else "different_number"
            elif same_as_printed(mv, dv):
                out = "same_as_printed"
            elif close(mv, mq, dv, dq):
                out = "rounds_to_zero" if dv == 0 else "rounded_to_printed_precision"
            elif dv == 0:
                out = "negative_to_zero" if mv < 0 else "positive_to_zero"
            else:
                out = "different_number"
            trans[(ms, out)] += 1
            if ms in ("missing_token", "empty") and out == "different_number":
                rule = next((c for c in CANDIDATES if c in stats and close(stats[c], 0.0, dv, dq + 1e-12)),
                            "no simple rule")
                rules[rule] += 1
    rec["n_shared_cells"] = sum(trans.values())
    for (ms, out), v in trans.items():
        rec[f"{ms}->{out}"] = v
    for r, v in rules.items():
        rec[f"impute:{r}"] = v
    return rec


def main() -> None:
    assert sum(len(f) for _, _, f in os.walk(CACHE)) == 42559, "frozen cache changed"
    rows = list(csv.DictReader(VALIDITY.open(), delimiter="\t"))
    pairs = [(r["study_id"], r["analysis_id"]) for r in rows
             if r["mwtab"] == r["datatable"] == "valid" and r["untarg_data"] != "valid"]
    with ProcessPoolExecutor(max_workers=24) as ex:
        recs = list(ex.map(analyse, pairs, chunksize=8))
    fields = sorted({k for r in recs for k in r}, key=lambda k: (k.count("->") + 2 * k.startswith("impute:"), k))
    for k in ("analysis_id", "study_id"):
        fields.remove(k)
        fields.insert(0, k)
    with (OUT / "per_analysis.tsv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t", lineterminator="\n", restval=0)
        w.writeheader()
        w.writerows(recs)

    lines = []
    say = lines.append
    n = len(recs)
    say(f"analyses: {n}")
    rel = collections.Counter(r["sample_relation"] for r in recs)
    say("sample relation (datatable rows vs mwTab sample columns): " + str(dict(rel.most_common())))
    say(f"  same sample count: {sum(r['n_samples_datatable'] == r['n_samples_mwtab'] for r in recs)}"
        f" | datatable more: {sum(r['n_samples_datatable'] > r['n_samples_mwtab'] for r in recs)}"
        f" | mwTab more: {sum(r['n_samples_datatable'] < r['n_samples_mwtab'] for r in recs)}")
    dto = sum(r["datatable_only_rows"] for r in recs)
    dtz = sum(r["datatable_only_rows_all_zero"] for r in recs)
    say(f"  datatable-only sample rows: {dto:,} in {sum(r['datatable_only_rows'] > 0 for r in recs)} analyses; "
        f"all-zero: {dtz:,} ({100 * dtz / max(dto, 1):.1f}%)")
    say(f"  analyses with duplicate datatable sample IDs: {sum(r['dup_ids_datatable'] > 0 for r in recs)}")

    trans = collections.Counter()
    for r in recs:
        for k, v in r.items():
            if "->" in k:
                trans[k] += v
    say(f"\nshared cells: {sum(trans.values()):,}")
    by_state = collections.defaultdict(dict)
    for k, v in trans.items():
        ms, out = k.split("->")
        by_state[ms][out] = v
    with (OUT / "transitions.tsv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["mwtab_state", "datatable_outcome", "cells", "share_of_state", "analyses"])
        for ms, outs in sorted(by_state.items(), key=lambda x: -sum(x[1].values())):
            tot = sum(outs.values())
            say(f"  mwTab {ms:13s} {tot:>12,}  ->  " + ", ".join(
                f"{o} {v:,} ({100 * v / tot:.1f}%)" for o, v in sorted(outs.items(), key=lambda x: -x[1])))
            for o, v in outs.items():
                w.writerow([ms, o, v, round(v / tot, 6), sum(1 for r in recs if r.get(f"{ms}->{o}", 0) > 0)])

    rules = collections.Counter()
    dominant = collections.Counter()
    for r in recs:
        rr = {k[7:]: v for k, v in r.items() if k.startswith("impute:")}
        rules.update(rr)
        if rr:
            dominant[max(rr, key=rr.get)] += 1
    tot = sum(rules.values())
    say(f"\nmwTab missing/empty -> non-zero datatable value: {tot:,} cells")
    with (OUT / "imputation_rules.tsv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["rule", "cells", "share", "analyses_where_dominant"])
        for k, v in rules.most_common():
            say(f"  {k:16s} {v:>9,} ({100 * v / tot:.1f}%)  dominant in {dominant[k]} analyses")
            w.writerow([k, v, round(v / tot, 6), dominant[k]])
    (OUT / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
