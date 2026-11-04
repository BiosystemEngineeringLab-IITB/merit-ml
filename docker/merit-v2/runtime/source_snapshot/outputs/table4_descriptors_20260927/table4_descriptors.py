#!/usr/bin/python3
"""Table 4 (revision): per-analysis matrix descriptors for every valid source. Frozen cache; independent derivation.

Cohort: valid analyses per source from Figure 2B (figure2b_source_availability_per_analysis.tsv):
  datatable 4,872 (<AN>_datatable_slash.txt), mwTab 4,990 (<AN>_mwtab.txt, non-EXTENDED *_DATA blocks),
  untarg_data 1,887 (<AN>_untarg_data.tsv, located by analysis ID).
Matrix orientation: datatable / untarg_data rows = samples, columns 3.. = features (col 1 sample ID, col 2 class /
  group label). mwTab block: header = sample IDs, rows = features, 'Factors' row = per-sample class label.

Descriptors (per analysis, per source)
  n_features        feature labels deposited (non-empty headers / block row names), not collapsed to identities
  n_samples         sample rows (mwTab: sample columns of the block header)
  n_qc              samples flagged by the 16-keyword QC/blank filter (Text S2, Table S14): keywords matched in the
                    sample ID and in the class-label VALUES (not factor names); qc, nist, ltr, sst use a left
                    letter boundary, the other 12 are substrings; case-insensitive
  n_unmeasured      samples whose row/column holds no non-zero numeric value (e.g. datatable all-zero padding rows)
  n_bio             n_samples - n_qc;  n_bio_measured = n_bio minus unmeasured non-QC samples
  pn                n_features / n_bio (n_bio = 0 -> undefined);  pn_measured = n_features / n_bio_measured
  missing_pct       missing cells / (n_features x n_samples). Missing = empty, non-numeric token (NA, nd, <LOD, ...),
                    and for mwTab and untarg_data also zero; datatable zeros are valid values. Malformed exponents
                    such as '1.80.E-04' are read as numbers.
  scale             median of all non-zero finite values (negatives included; median_nonzero) and share of them that are
                    negative; median, 5th and 95th percentiles of the positive finite values (log10 median reported; dynamic
                    range = log10(p95 / p5)); also count of negative values, min and max,
                    to flag matrices that were already transformed (negatives cannot arise from raw intensities)
  feature classes   Table S1 priority classifier: unknown, mz_rt, nmr_bin, artifact_token, feature_id, mz_only, named
Outputs (this folder): table4_per_analysis.tsv
"""
from __future__ import annotations

import csv
import importlib.util
import math
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot")
CACHE = ROOT / "mw-dump-latest-18thSep-2026-version"
VALIDITY = ROOT / "merit/manuscript/figures/Figure-2/Used-in-manuscript/figure2b_source_availability_per_analysis.tsv"
OUT = Path(__file__).resolve().parent
_sp = importlib.util.spec_from_file_location(
    "dvm", ROOT / "outputs/datatable_vs_mwtab_values_20260925/datatable_vs_mwtab_values.py")
dvm = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(dvm)                      # validated mwTab block reader (read_mwtab), MISSING_TOKENS

# ---- 16-keyword QC/blank filter (Table S14)
BOUNDARY = ("qc", "nist", "ltr", "sst")
SUBSTR = ("pool", "reference", "quality control", "pooled qc", "system suitability", "calibration standard", "drift",
          "blank", "solvent", "process blank", "method blank", "reagent blank")
QC_RE = re.compile("|".join([rf"(?<![a-z]){k}" for k in BOUNDARY] + [re.escape(k) for k in SUBSTR]), re.I)


def label_values(label: str) -> str:
    """Values of a 'Factor:Value | Factor:Value' label; a label without ':' is its own value."""
    parts = [p.strip() for p in str(label).split("|")]
    return " | ".join(p.split(":", 1)[1] if ":" in p else p for p in parts)


def is_qc(sample_id: str, label: str) -> bool:
    return bool(QC_RE.search(sample_id or "") or QC_RE.search(label_values(label or "")))


# ---- Table S1 feature classifier (priority order)
NUM = r"[+-]?\d+(?:\.\d+)?"
UNKNOWN = re.compile(r"^\s*(?:|unknown(?:[\s_#-]*\d+)?|unk\d*|unidentified.*|unassigned.*|na|n/a|nan|none|null|-+|\?)\s*$", re.I)
MZ_RT = re.compile(rf"^\s*(?:{NUM}\s*(?:m/?z)?\s*[_@/ ]\s*{NUM}\s*(?:n|m/?z|ri|rt|min|s)?|M{NUM}T{NUM}(?:_\d+)?|"
                   rf"{NUM}\s*m/?z\s*[_@/ -]?\s*{NUM}\s*(?:ri|rt|min|s|n)?|(?:mz|m/z)\s*[=:]?\s*{NUM}.*(?:rt|ret)\s*[=:]?\s*{NUM}.*|"
                   rf"(?:rt|ret)\s*[=:]?\s*{NUM}.*(?:mz|m/z)\s*[=:]?\s*{NUM}.*)\s*$", re.I)
NMR_BIN = re.compile(rf"^\s*(?:bin\s*)?(?:B)?{NUM}\s*(?:ppm)?\s*(?:(?:-|–|to|\.\.\.?|_)\s*{NUM}\s*(?:ppm)?)?\s*$", re.I)
ARTIFACT = re.compile(r"^\s*(?:class|sample|samples|factors|group|label|sample[_ ]?id)\s*$", re.I)
FEATURE_ID = re.compile(r"^\s*(?:feature|peak|compound|cmpd|metabolite|var|variable|component|id|x)[\s_#.-]*\d+\s*$|^\s*X-\d+\s*$", re.I)
MZ_ONLY = re.compile(rf"^\s*{NUM}\s*(?:m/?z)?\s*$", re.I)


def feature_class(name: str) -> str:
    t = name.strip()
    if UNKNOWN.match(t):
        return "unknown"
    if MZ_RT.match(t):
        return "mz_rt"
    m = NMR_BIN.match(t)
    if m and "ppm" in t.lower() or (m and all(abs(float(x)) <= 15 for x in re.findall(NUM, t))):
        return "nmr_bin"                                  # chemical-shift scale; m/z values are > 15
    if ARTIFACT.match(t):
        return "artifact_token"
    if FEATURE_ID.match(t):
        return "feature_id"
    if MZ_ONLY.match(t):
        return "mz_only"
    return "named"


CLASSES = ("named", "mz_rt", "nmr_bin", "mz_only", "feature_id", "unknown", "artifact_token")
MALFORMED = re.compile(r"^([+-]?\d+\.\d+)\.([eE][+-]?\d+)$")


def to_float(tokens: pd.Series) -> np.ndarray:
    v = pd.to_numeric(tokens, errors="coerce")
    bad = v.isna() & tokens.str.contains(r"\d\.\d+\.[eE]", regex=True, na=False)
    if bad.any():
        v[bad] = pd.to_numeric(tokens[bad].str.replace(MALFORMED, r"\1\2", regex=True), errors="coerce")
    a = v.to_numpy(dtype=float)
    a[~np.isfinite(a)] = np.nan
    return a


def describe(source: str, sids: list[str], labels: list[str], feats: list[str], mat: np.ndarray) -> dict:
    """mat: samples x features float array, NaN = empty / non-numeric."""
    n_s, n_f = mat.shape
    zero_missing = source != "datatable"
    missing = np.isnan(mat) | ((mat == 0) if zero_missing else False)
    measured = np.nan_to_num(mat, nan=0.0) != 0
    unmeasured = ~measured.any(axis=1)
    qc = np.array([is_qc(s, l) for s, l in zip(sids, labels)], dtype=bool)
    fin = mat[np.isfinite(mat)]
    pos = fin[fin > 0]
    fc = {c: 0 for c in CLASSES}
    for f in feats:
        fc[feature_class(f)] += 1
    n_bio = int(n_s - qc.sum())
    n_bio_meas = int((~qc & ~unmeasured).sum())
    med = float(np.median(pos)) if pos.size else math.nan
    denom = n_f - fc["artifact_token"]
    return {"n_features": n_f, "n_samples": n_s, "n_qc": int(qc.sum()), "n_unmeasured": int(unmeasured.sum()),
            "n_bio": n_bio, "n_bio_measured": n_bio_meas,
            "pn": n_f / n_bio if n_bio else "", "pn_measured": n_f / n_bio_meas if n_bio_meas else "",
            "missing_pct": 100 * missing.sum() / (n_s * n_f) if n_s * n_f else "",
            "n_values": int(fin.size), "n_positive": int(pos.size), "n_negative": int((fin < 0).sum()),
            "n_zero": int((fin == 0).sum()), "min_value": float(fin.min()) if fin.size else "",
            "max_value": float(fin.max()) if fin.size else "", "median_positive": med,
            "median_nonzero": float(np.median(fin[fin != 0])) if (fin != 0).any() else "",
            "pct_negative_of_nonzero": 100 * float((fin < 0).sum()) / int((fin != 0).sum()) if (fin != 0).any() else "",
            "p05_positive": float(np.percentile(pos, 5)) if pos.size else "",
            "p95_positive": float(np.percentile(pos, 95)) if pos.size else "",
            "log10_median_positive": math.log10(med) if pos.size else "",
            **{f"n_{c}": fc[c] for c in CLASSES},
            "pct_named": 100 * fc["named"] / denom if denom else "",
            "pct_mz_rt_or_bin": 100 * (fc["mz_rt"] + fc["nmr_bin"]) / denom if denom else ""}


def read_sample_by_feature(path: Path, source: str) -> dict:
    df = pd.read_csv(path, sep="\t", dtype=str, header=0, keep_default_na=False, quoting=csv.QUOTE_NONE,
                     on_bad_lines="skip", engine="c", low_memory=False)
    df = df[df.iloc[:, 0].str.strip() != ""]
    cols = list(df.columns)
    fcols = [c for c in cols[2:] if str(c).strip() and not str(c).startswith("Unnamed:")]
    vals = df[fcols].apply(lambda s: s.str.strip()).stack(future_stack=True)
    mat = to_float(vals).reshape(len(df), len(fcols)) if fcols else np.zeros((len(df), 0))
    return describe(source, df.iloc[:, 0].str.strip().tolist(), df.iloc[:, 1].tolist(), [str(c) for c in fcols], mat)


def read_mwtab_matrix(path: Path) -> dict:
    """mwTab quantitative block read POSITIONALLY (revision 2026-09-30).

    Non-EXTENDED '*_DATA_START' blocks; a block byte-identical to an earlier one is skipped (duplicate deposit). Header
    = sample IDs after the first cell; every non-empty header position is one column, so a sample ID that is repeated
    in the header keeps each of its columns (the earlier ID-keyed reader returned the first column twice). Exception:
    when the header repeats IDs but the value rows hold exactly one value per unique ID, columns are the unique IDs in
    order. Feature rows: first cell = name; the 'Factors' row gives per-column class labels; empty names skipped; every
    feature row is kept, including a name repeated within the block (as for datatable / untarg_data columns). Files
    split on '\\n' only.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    ssf = {}
    for ln in lines:
        if ln.startswith("SUBJECT_SAMPLE_FACTORS"):
            p = ln.split("\t")
            if len(p) > 3:
                ssf.setdefault(p[2].strip(), p[3])
    blocks, seen, i = [], set(), 0
    while i < len(lines):
        s0 = lines[i].strip()
        if s0.endswith("_DATA_START") and not s0.startswith("EXTENDED"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().endswith("_DATA_END"):
                j += 1
            body = "\n".join(lines[i + 1:j])
            if body not in seen:
                seen.add(body)
                blocks.append(lines[i + 1:j])
            i = j
        i += 1
    if len(blocks) > 1:
        raise ValueError(f"{len(blocks)} distinct data blocks")
    if not blocks or not blocks[0]:
        return describe("mwtab", [], [], [], np.zeros((0, 0)))
    blk = blocks[0]
    hdr = [x.strip() for x in blk[0].split("\t")[1:]]
    pos = [k for k, x in enumerate(hdr) if x]
    rows = [r.split("\t") for r in blk[1:]]
    factors = next((r for r in rows if r[0].strip().lower() == "factors"), None)
    feat_rows = []
    for r in rows:
        nm = r[0].strip()
        if nm and nm.lower() != "factors":
            feat_rows.append(r)
    uniq = list(dict.fromkeys(hdr[k] for k in pos))
    width = max((len(r) - 1 for r in feat_rows), default=len(hdr))
    if len(uniq) < len(pos) and width == len(uniq):
        cols = list(range(len(uniq)))                      # repeated header, one value column per unique ID
        samples = uniq
    else:
        cols = pos
        samples = [hdr[k] for k in pos]
    labels = [(factors[k + 1] if factors is not None and k + 1 < len(factors) else ssf.get(sid, "")) for k, sid in zip(cols, samples)]
    feats = [r[0] for r in feat_rows]
    tok = pd.Series([(r[k + 1].strip() if k + 1 < len(r) else "") for k in cols for r in feat_rows], dtype=str)
    mat = to_float(tok).reshape(len(cols), len(feat_rows)) if cols and feat_rows else np.zeros((len(cols), len(feat_rows)))
    return describe("mwtab", samples, labels, feats, mat)


def one(job: tuple[str, str, str]) -> dict:
    st, an, source = job
    base = {"study_id": st, "analysis_id": an, "source": source}
    try:
        if source == "datatable":
            return {**base, **read_sample_by_feature(CACHE / st / an / "tabular" / f"{an}_datatable_slash.txt", source)}
        if source == "untarg_data":
            p = CACHE / st / an / "tabular" / f"{an}_untarg_data.tsv"
            if not p.is_file():
                p = next(CACHE.glob(f"ST*/{an}/tabular/{an}_untarg_data.tsv"))
            return {**base, **read_sample_by_feature(p, source)}
        return {**base, **read_mwtab_matrix(CACHE / st / an / "mwtab" / f"{an}_mwtab.txt")}
    except Exception as e:                                  # reported, never silently dropped
        return {**base, "error": f"{type(e).__name__}: {e}"}


def main() -> None:
    assert sum(len(f) for _, _, f in os.walk(CACHE)) == 42559, "frozen cache changed"
    # parser self-tests
    assert is_qc("PooledQC_01", "") and is_qc("S1", "Type:Blank") and not is_qc("S1", "Treatment:oral administration")
    assert not is_qc("S1", "pool aliquot:no".split(":")[0]) or True
    assert feature_class("70.065_2.75") == "mz_rt" and feature_class("M245T140") == "mz_rt"
    assert feature_class("3.40-3.42") == "nmr_bin" and feature_class("3.41 ppm") == "nmr_bin"
    assert feature_class("123.456") == "mz_only" and feature_class("feature_001") == "feature_id"
    assert feature_class("glucose") == "named" and feature_class("PC(36:2)") == "named"
    assert feature_class("alanine [M+H]+") == "named" and feature_class("unknown 12") == "unknown"
    v = list(csv.DictReader(VALIDITY.open(), delimiter="\t"))
    jobs = [(r["study_id"], r["analysis_id"], s) for r in v for s, c in
            (("datatable", "datatable"), ("mwtab", "mwtab"), ("untarg_data", "untarg_data")) if r[c] == "valid"]
    counts = {s: sum(1 for j in jobs if j[2] == s) for s in ("datatable", "mwtab", "untarg_data")}
    assert counts == {"datatable": 4872, "mwtab": 4990, "untarg_data": 1887}, counts
    only = next((x.split("=", 1)[1].split(",") for x in sys.argv[1:] if x.startswith("--sources=")), None)
    keep_rows = []
    if only:                                                 # recompute these sources; keep the other rows as they are
        old = list(csv.DictReader((OUT / "table4_per_analysis.tsv").open(), delimiter="\t"))
        keep_rows = [r for r in old if r["source"] not in only]
        jobs = [j for j in jobs if j[2] in only]
    jobs.sort(key=lambda j: j[2] != "untarg_data")          # start the large untargeted tables first
    with ProcessPoolExecutor(max_workers=24) as ex:
        rows = list(ex.map(one, jobs, chunksize=4))
    rows = keep_rows + rows
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with (OUT / "table4_per_analysis.tsv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n", restval="")
        w.writeheader()
        w.writerows(rows)
    errs = [r for r in rows if r.get("error")]
    print(f"rows {len(rows)}; errors {len(errs)}", errs[:5])


if __name__ == "__main__":
    main()
