#!/usr/bin/python3
"""Provenance of every datatable zero, per sample row, for the sample-level missingness score (MERIT-ML v9).

The manuscript's zero-origin analysis (Text S16; outputs/table4_descriptors_20260927/zero_origin.py) counts datatable zeros per
matrix. The score needs the same counts per sample. This script runs the manuscript's own pairing code
(outputs/datatable_vs_mwtab_values_20260925/datatable_vs_mwtab_values.py: readers, sample and feature matching, tolerances and
outcome rules, unchanged) and records each datatable zero against its sample ID:
  also_zero      the paired mwTab cell is 0                                          (zero_origin: reported_zero)
  hidden_empty   the paired mwTab cell is empty                                      (filled_empty)
  hidden_token   the paired mwTab cell is a missing-value token (NA, ND, ...)        (filled_token)
  hidden_text    the paired mwTab cell is other non-numeric text                     (filled_other_text)
  rounded        the paired mwTab cell is a non-zero number that rounds to 0 at the datatable's printed precision
  negative       the paired mwTab cell is a negative number, written as 0 in datatable
  positive       the paired mwTab cell is a positive number (not rounding), written as 0 in datatable
                 (rounded + negative + positive = zero_origin: value_to_zero)
  rounded_negative  the part of 'rounded' whose mwTab value is negative (added 2026-10-17: the negative-value warning covers it)
Repeated names and IDs (added 2026-10-17). The manuscript pairs one-to-one only: a feature name that occurs twice (after normalisation, in
either source) or a sample ID repeated in the mwTab header is left untraced. Here such a datatable zero is compared with EVERY mwTab cell
that has the same sample ID and the same (normalised) feature name; when all of them give the same kind, the zero gets that kind and is
counted in the r_* columns (r_hidden_mixed / r_replaced_mixed: every candidate has no number, or is a non-zero number, but of different
kinds). When the candidates disagree, the zero stays untraced. Example: ST000014 / AN000031 lists 3-hydroxybutanoic acid twice; for five
samples one row is empty and the other holds 'N', and the datatable writes 0: hiding a missing value either way.
The one-to-one columns (also_zero ... rounded_negative) are the manuscript's and still sum to zero_origin_per_analysis.tsv.
Zeros of a sample row that cannot be paired (feature not in mwTab, sample absent from mwTab, candidates disagree) are "not traced"; zeros in Tier 1
sample alignment rows (all-zero rows whose sample is absent from the mwTab block) are counted by the roster, not here.
Scope: every analysis with a valid datatable and a valid mwTab (4,856; Figure 2B validity), frozen dump of 18 Sep 2026.
Output (this folder): datatable_zero_provenance_per_sample.tsv.gz, one row per (study, analysis, sample ID) with at least one count.
Check (assert): per matrix, the sums equal the manuscript's zero_origin_per_analysis.tsv for every category.
"""
from __future__ import annotations

import collections
import csv
import gzip
import importlib.util
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path("/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot")
HERE = Path(__file__).resolve().parent
_sp = importlib.util.spec_from_file_location("dvm", ROOT / "outputs/datatable_vs_mwtab_values_20260925/datatable_vs_mwtab_values.py")
dvm = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(dvm)
ZO = ROOT / "outputs/table4_descriptors_20260927/zero_origin_per_analysis.tsv"
KINDS = ("also_zero", "hidden_empty", "hidden_token", "hidden_text", "rounded", "negative", "positive", "rounded_negative")
RESOLVED = ("r_also_zero", "r_hidden_empty", "r_hidden_token", "r_hidden_text", "r_hidden_mixed", "r_rounded", "r_negative", "r_positive",
            "r_rounded_negative", "r_replaced_mixed")
FAMILY = {"also_zero": "zero", "hidden_empty": "hidden", "hidden_token": "hidden", "hidden_text": "hidden", "rounded": "replaced", "negative": "replaced", "positive": "replaced"}
HIDDEN = {"empty": "hidden_empty", "missing_token": "hidden_token", "other_text": "hidden_text"}


def kind_of(mt: str, dv: float, dq: float, q_an: float) -> tuple[str, bool]:
    """Kind of a datatable zero against one mwTab token (the manuscript's rules), and whether the mwTab value is negative."""
    ms = dvm.mw_state(mt)
    mv, mq = dvm.parse(mt)
    if ms in HIDDEN:
        return HIDDEN[ms], False
    if dvm.same_as_printed(mv, dv):
        return "also_zero", False
    if dvm.close(mv, mq, dv, max(dq, q_an)):
        return "rounded", mv < 0
    return ("negative" if mv < 0 else "positive"), mv < 0


def mwtab_candidates(path: Path) -> dict[tuple[str, str], list[str]]:
    """{(sample ID, normalised feature name): every token}: every block row with that name and every column with that ID (read_mwtab's layout)."""
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    cand: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    i = 0
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
            sids = uniq if (len(uniq) < len(hdr) and width == len(uniq)) else hdr
            for p in block:
                if p[0].strip() and p[0].strip().lower() != "factors":
                    for k, sid in enumerate(sids):
                        if sid:
                            cand[(sid, dvm.key(p[0]))].append(p[k + 1] if k + 1 < len(p) else "")
            i = j
        i += 1
    return cand


def per_sample(pair: tuple[str, str]) -> tuple[tuple[str, str], dict[str, collections.Counter]]:
    """The pairing of datatable_vs_mwtab_values.analyse(), with each datatable zero kept against its sample ID."""
    st, an = pair
    d_ids, d_feats, d_rows = dvm.read_datatable(dvm.CACHE / st / an / "tabular" / f"{an}_datatable_slash.txt")
    m_ids, m_feats, m_cell, m_amb = dvm.read_mwtab(dvm.CACHE / st / an / "mwtab" / f"{an}_mwtab.txt")
    q_an = dvm.print_half_unit(d_rows)
    dcount, M = collections.Counter(d_ids), set(m_ids)
    dk, mk = collections.Counter(dvm.key(f) for f in d_feats), collections.Counter(dvm.key(f) for f in m_feats)
    shared_f = [f for f in dk if dk[f] == 1 and mk.get(f) == 1]
    d_col = {dvm.key(f): j for j, f in enumerate(d_feats)}
    m_raw = {dvm.key(f): f for f in m_feats}
    shared_s = [s for s in d_ids if dcount[s] == 1 and s in M and s not in m_amb]
    d_row = {s: i for i, s in enumerate(d_ids)}
    out: dict[str, collections.Counter] = {}
    for f in shared_f:
        for s in shared_s:
            dv, dq = dvm.parse(d_rows[d_row[s]][d_col[f]])
            if dv != 0:                                   # only datatable zeros (a non-numeric datatable cell is not a zero)
                continue
            mt = m_cell.get((s, m_raw[f]), "")
            ms = dvm.mw_state(mt)
            mv, mq = dvm.parse(mt)
            dq = max(dq, q_an)
            if ms in HIDDEN:
                kind = HIDDEN[ms]
            elif dvm.same_as_printed(mv, dv):             # mwTab 0 (a non-zero number never equals 0 as printed)
                kind = "also_zero"
            elif dvm.close(mv, mq, dv, dq):
                kind = "rounded"
                if mv < 0:
                    out.setdefault(s, collections.Counter())["rounded_negative"] += 1
            else:
                kind = "negative" if mv < 0 else "positive"
            out.setdefault(s, collections.Counter())[kind] += 1
    # zeros the one-to-one pairing leaves untraced because a name or an ID is repeated: compare every candidate mwTab cell
    cand = None
    for i, s in enumerate(d_ids):
        if s not in M:
            continue                                        # sample absent from mwTab: Tier 1 row, or not traceable
        for j, f in enumerate(d_feats):
            kf = dvm.key(f)
            if mk.get(kf, 0) == 0 or not (mk[kf] > 1 or dk[kf] > 1 or dcount[s] > 1 or s in m_amb):
                continue                                    # paired above, or the feature is not in mwTab
            dv, dq = dvm.parse(d_rows[i][j])
            if dv != 0:
                continue
            if cand is None:
                cand = mwtab_candidates(dvm.CACHE / st / an / "mwtab" / f"{an}_mwtab.txt")
            kinds = [kind_of(t, dv, dq, q_an) for t in cand.get((s, kf), [])]
            if not kinds:
                continue
            names = {k for k, _ in kinds}
            fams = {FAMILY[k] for k in names}
            c = out.setdefault(s, collections.Counter())
            if len(names) == 1:
                k = names.pop()
                c["r_" + k] += 1
                if k == "rounded" and all(neg for _, neg in kinds):
                    c["r_rounded_negative"] += 1
            elif fams == {"hidden"}:
                c["r_hidden_mixed"] += 1
            elif fams == {"replaced"}:
                c["r_replaced_mixed"] += 1                  # every candidate a non-zero number, of different kinds
    return pair, out


if __name__ == "__main__":
    zo = {(r["study_id"], r["analysis_id"]): r for r in csv.DictReader(ZO.open(), delimiter="\t")}
    with Pool(40) as pool:
        res = pool.map(per_sample, sorted(zo), chunksize=8)
    bad = []
    with gzip.open(HERE / "datatable_zero_provenance_per_sample.tsv.gz", "wt", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(("study_id", "analysis_id", "sample_id") + KINDS + RESOLVED)
        for (st, an), per in res:
            tot = collections.Counter()
            for s, c in per.items():
                w.writerow((st, an, s) + tuple(c[k] for k in KINDS + RESOLVED))
                tot.update(c)
            z = zo[(st, an)]
            want = (int(z["reported_zero"]), int(z["filled_empty"]), int(z["filled_token"]), int(z["filled_other_text"]), int(z["value_to_zero"]))
            got = (tot["also_zero"], tot["hidden_empty"], tot["hidden_token"], tot["hidden_text"], tot["rounded"] + tot["negative"] + tot["positive"])
            if got != want:
                bad.append((st, an, got, want))
    r_tot = collections.Counter()
    for _, per in res:
        for c in per.values():
            r_tot.update({k: v for k, v in c.items() if k.startswith("r_")})
    print(f"{len(res):,} paired analyses; {sum(len(p) for _, p in res):,} sample rows with at least one traced zero; matrices whose one-to-one counts differ from zero_origin: {len(bad)}")
    print("resolved through a repeated name or ID (every candidate agrees):", dict(r_tot))
    for b in bad[:10]:
        print("  ", b)
    sys.exit(1 if bad else 0)
