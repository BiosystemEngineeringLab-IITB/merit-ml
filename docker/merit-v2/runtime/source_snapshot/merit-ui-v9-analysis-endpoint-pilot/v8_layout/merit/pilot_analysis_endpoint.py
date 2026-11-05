"""Analysis x endpoint pilot: Structural panel, sample editor, derived inputs, Overview cards and placeholders.

Every valid matrix (one analysis from one endpoint) is scored on its own; endpoints are never averaged and have no
order of preference. The study level is a descriptive summary. Data: the v9 cache (merit-cache-workbench-full-v9,
built by ../scripts/build_v9_cache.py). The Structural items are recomputed from the stored position rosters for the
current parameters and sample edits (merit/structural_v9.py); with defaults they equal what the cache stores.
"""
from __future__ import annotations

import collections
import copy
import functools
import json
import os
import re
import statistics
import threading
from functools import lru_cache
from html import escape as _e
from pathlib import Path

import numpy as np

from merit import missingness_v9 as mv
from merit import scale_v9 as scv
from merit import qc_blank_v9 as qb
from merit import low_information_v9 as li
from merit import correlation_v9 as cr
from merit import outliers_v9 as ov
from merit import overview_v9 as ovw
from merit import qc_reproducibility_v9 as qr
from merit import sample_annotations_v9 as sa
from merit import structural_v9 as sv
from merit import annotation_v9 as anv
from merit import separability_v9 as sp
from merit import labels_v9 as lb
from merit import manuscript_feature_labels as mfl
from merit import readiness_v9 as rd

V9_ROOT = Path(os.environ.get("MERIT_V9_CACHE_ROOT", "/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-cache-workbench-full-v9"))
SOURCES = (("datatable", "datatable"), ("mwtab", "mwTab"), ("untarg_data", "untarg_data"))
# (key, name, one-line definition)
ITEMS = (
    ("sample_support", "Sample support",
     "Distinct ML-eligible sample IDs ÷ {N}, capped at 100. {N} is a working threshold (parameter panel), not proof that every class has enough samples."),
    ("samples_with_factor", "Samples with a factor label",
     "ML-eligible samples with a non-empty factor label (from the matrix or the analysis's sample table) ÷ ML-eligible samples. Only nothing or '-' is "
     "empty; words such as None or NA are deposited values and count. Without a label a sample has no class for supervised learning; a label is "
     "not necessarily a usable class (it can be age, batch or \u201cunknown\u201d)."),
    ("numeric_values", "Numeric value cells",
     "100 − the percent of value cells holding text instead of a number. Missing entries (empty, NA, NaN, null, '-', '.') are left out; limit codes "
     "(ND, BLOQ, <LOD), spreadsheet errors (#VALUE!) and malformed numbers count as text; 1.80.E-04 is read as a number. A model cannot use text cells."),
    ("unique_sample_ids", "Duplicate sample IDs",
     "Distinct sample IDs ÷ ML-eligible positions; 100 = no sample ID entered twice. Only sample IDs are compared, not subject IDs. Repeated entries "
     "must be combined into one row per sample, or kept on one side of a train/test split (see data leakage below)."),
)
PLACEHOLDER_SECTIONS = {
    "analytical": "Analytical QC",
}
POSITION_NOTE = ("A position is one sample row (datatable, untarg_data) or one sample column (mwTab) of the deposited matrix. ML-eligible samples are "
                 "positions that are neither Tier 1 sample alignment rows nor QC-like, blank-like or standard-like; every item uses them only.")
_RED = "#c0392b"
_CARD = "background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.1);border-radius:14px;padding:12px 14px"
_DASHED = "border:1px dashed rgba(19,35,39,.25);border-radius:14px;padding:12px 14px;color:#51656a;background:rgba(255,255,255,.5)"
_TH = "overflow-wrap:normal;word-break:normal;padding:8px 10px;border-bottom:1px solid rgba(19,35,39,.12);text-align:left;font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:#51656a"
_TD = "padding:8px 10px;border-bottom:1px solid rgba(19,35,39,.12);vertical-align:top;font-size:.88rem"
_SMALL = "font-size:.8rem;color:#51656a;margin-top:6px"


@lru_cache(maxsize=24)
def _raw_study(study_id: str) -> dict | None:
    path = V9_ROOT / "json" / f"{study_id.lower()}_v9.json"
    return json.loads(path.read_text()) if path.is_file() else None


_MEMO_LOCK = threading.Lock()


def _memo(fn):
    """The last results of one per-study computation, for the arguments in force: the Overview, the panel and the page data of the
    same request use one computation (redundancy is recomputed in seconds under sample edits). Each caller gets its own copy."""
    store: collections.OrderedDict = collections.OrderedDict()

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        key = json.dumps([args, kwargs], sort_keys=True, default=str)
        with _MEMO_LOCK:
            hit = store.get(key)
            if hit is not None:
                store.move_to_end(key)
        if hit is None:
            hit = fn(*args, **kwargs)
            with _MEMO_LOCK:
                store[key] = hit
                while len(store) > 8:
                    store.popitem(last=False)
        return copy.deepcopy(hit)
    return wrapped


def study_ids() -> list[str]:
    return sorted(json.loads((V9_ROOT / "index.json").read_text())["studies"])


def _item_defs(params: dict | None = None) -> tuple:
    n = float((params or {}).get("structural_sample_support_n", sv.DEFAULT_PARAMS["structural_sample_support_n"]))
    return tuple((key, label, tip.replace("{N}", f"{n:g}")) for key, label, tip in ITEMS)


AGREEMENT_KINDS = ("no sample ID in common", "partly different sample IDs", "one endpoint holds a subset of the other",
                   "same sample IDs, different number of positions", "same sample IDs")


def endpoint_agreement(raw_analysis: dict) -> dict:
    """Do the valid endpoints of one analysis hold the same measured samples? Sample IDs are compared exactly as deposited.

    Measured samples of a matrix = its positions that are not Tier 1 sample alignment rows. Deposited facts only: sample
    edits do not change this comparison.
    """
    measured = {}
    for key, label in SOURCES:
        ep = raw_analysis["endpoints"].get(key) or {}
        if "roster" in ep:
            ids = [row[sv.I_ID] for row in ep["roster"] if not row[sv.I_ALIGN]]
            measured[key] = {"label": label, "positions": len(ids), "ids": set(ids)}
    pairs = []
    keys = list(measured)
    for i, x in enumerate(keys):
        for y in keys[i + 1:]:
            a, b = measured[x], measured[y]
            common = a["ids"] & b["ids"]
            if a["ids"] == b["ids"]:
                kind = "same sample IDs" if a["positions"] == b["positions"] else "same sample IDs, different number of positions"
            elif not common:
                kind = "no sample ID in common"
            elif a["ids"] < b["ids"] or b["ids"] < a["ids"]:
                kind = "one endpoint holds a subset of the other"
            else:
                kind = "partly different sample IDs"
            pairs.append({"a": x, "b": y, "a_label": a["label"], "b_label": b["label"], "kind": kind,
                          "positions_a": a["positions"], "positions_b": b["positions"], "distinct_a": len(a["ids"]), "distinct_b": len(b["ids"]),
                          "in_common": len(common), "only_in_a": len(a["ids"] - b["ids"]), "only_in_b": len(b["ids"] - a["ids"]),
                          "examples_only_in_a": sorted(a["ids"] - b["ids"])[:4], "examples_only_in_b": sorted(b["ids"] - a["ids"])[:4]})
    worst = next((k for k in AGREEMENT_KINDS if any(p["kind"] == k for p in pairs)), None)
    return {"endpoints": len(measured), "worst": worst, "pairs": pairs}


def _agreement_html(ag: dict) -> str:
    if ag["endpoints"] < 2:
        return ""
    if ag["worst"] == "same sample IDs":
        return ("<div style='margin-top:6px;font-size:.8rem'><span style='color:#1a7a4a;font-weight:800'>✓</span> "
                "same measured sample IDs and the same number of samples on all endpoints</div>")
    out = ""
    for p in ag["pairs"]:
        if p["kind"] == "same sample IDs":
            continue
        if p["kind"] == "same sample IDs, different number of positions":
            out += (f"<div style='margin-top:6px;font-size:.8rem'><b>{p['a_label']} and {p['b_label']}: same sample IDs, different number of positions</b> "
                    f"({p['positions_a']} and {p['positions_b']} positions for {p['distinct_a']} sample IDs): one endpoint repeats sample IDs.</div>")
            continue
        def side(label, n, examples):
            return (f"{n} sample ID{'s' if n != 1 else ''} only in {label}" + (f" ({_e(', '.join(examples))}{'…' if n > len(examples) else ''})" if examples else "")) if n else ""
        detail = "; ".join(x for x in (side(p["a_label"], p["only_in_a"], p["examples_only_in_a"]), side(p["b_label"], p["only_in_b"], p["examples_only_in_b"])) if x)
        if p["kind"] == "no sample ID in common":
            head = f"⚠ {p['a_label']} and {p['b_label']} share no sample ID"
            body = (f"Measured samples: {p['a_label']} {p['positions_a']}, {p['b_label']} {p['positions_b']}. "
                    "The two files of this analysis do not list the same samples under the same IDs; check both before using either.")
        else:
            head = f"⚠ {p['a_label']} and {p['b_label']} do not hold the same measured samples"
            body = f"{p['positions_a']} and {p['positions_b']} measured samples, {p['in_common']} sample IDs in common."
        out += _red_note(head, f"{body} {detail}.", "margin-top:6px;font-size:.8rem")
    return out


def _study(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    """The study with, for every valid matrix, its record `m`: deposited evidence + the items for these parameters and edits."""
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    analyses = []
    for a in raw["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            if "roster" not in ep:
                endpoints[key] = {"availability": ep.get("availability", "")}
                continue
            rec = sv.compute(ep["roster"], params, overrides, ep.get("sample_table_lines"))
            m = {**ep["sections"]["structural"]["evidence"], **rec, "n_features": ep["n_features"], "file": ep["file"],
                 "file_in_study_folder": ep.get("file_in_study_folder")}
            m["edited"] = bool(rec["edited_positions"])
            if m["edited"]:            # the deposited breakdown of text entries no longer describes the edited sample set
                m.pop("text_cells_by_class", None); m.pop("text_examples", None)
            m["curated_corrections"] = ep.get("curated_corrections")
            endpoints[key] = {"availability": "valid", "m": m, "annotation_evidence": ep.get("annotation_evidence")}
        analyses.append({"analysis_id": a["analysis_id"], "endpoints": endpoints, "agreement": endpoint_agreement(a),
                         "metadata": (a.get("sections") or {}).get("metadata"), "unnamed_coordinates": _unnamed_coordinates(a)})
    return {"study_id": sid, "analyses": analyses, "study_metadata": (raw.get("study_sections") or {}).get("metadata")}


def _unnamed_coordinates(a: dict) -> dict | None:
    """m/z and RT of unnamed features of one analysis: the Has m/z / Has RT declaration of the results-file line and the features
    named by an m/z-RT pair (manuscript label classifier). Shown beside Mass/RT metadata, which covers the annotation table of
    named metabolites only; nothing here changes a score."""
    ae = ((a.get("sections") or {}).get("analytical") or {}).get("acquisition_evidence") or {}
    yes = lambda v: str(v or "").strip().lower() == "yes"
    counts = [int((((ep.get("sections") or {}).get("annotation") or {}).get("class_counts") or {}).get("mz_rt") or 0)
              for ep in (a.get("endpoints") or {}).values()]
    rt_units = str(ae.get("results_file_rt_units") or "").strip()
    out = {"has_mz": yes(ae.get("results_file_has_mz")), "has_rt": yes(ae.get("results_file_has_rt")),
           "rt_units": rt_units if any(c.isalpha() for c in rt_units) else "", "n_mz_rt_features": max(counts or [0])}
    return out if out["has_mz"] or out["has_rt"] or out["n_mz_rt_features"] else None


def _unnamed_coordinates_text(c: dict | None) -> str:
    """Why unnamed-feature m/z and RT do not count towards Mass/RT metadata (empty when the analysis has none)."""
    if not c:
        return ""
    parts = []
    declared = " and ".join(w for w, f in (("m/z", c["has_mz"]), ("RT", c["has_rt"])) if f)
    if declared:
        parts.append(f"the results-file line declares {declared}" + (f" (RT units: {c['rt_units']})" if c["has_rt"] and c["rt_units"] else ""))
    if c["n_mz_rt_features"]:
        parts.append(f"{c['n_mz_rt_features']:,} features are named by an m/z–RT pair")
    return ". Not counted, because this item covers annotated, named metabolites only: " + "; ".join(parts)


def _red_note(summary: str, body: str = "", style: str = "") -> str:
    """A red note, collapsed: one summary line is shown, the rest opens on click. Every red note of the page uses it."""
    return (f"<details class='v9-red-note' style='color:{_RED};{style}'><summary style='cursor:pointer'><b>{summary}</b></summary>"
            + (f"<div style='margin-top:2px'>{body}</div>" if body else "") + "</details>")


def _note(summary: str, body: str) -> str:
    """An amber note of the Sample eligibility & analytical controls cells, collapsed like the red notes."""
    return (f"<details class='v9-amber-note' style='color:#8a5a00;font-size:.78rem;margin-top:4px'><summary style='cursor:pointer'>{summary}</summary>"
            f"<div style='margin-top:2px'>{body}</div></details>")


def _descriptions(body: str) -> str:
    """Score descriptions, closed until the reader opens them."""
    return ("<details style='margin:0 0 14px;border:1px solid rgba(19,35,39,.14);border-radius:12px;background:rgba(255,255,255,.6)'>"
            "<summary style='cursor:pointer;padding:10px 14px;font-weight:700;color:#113e52;font-size:.9rem'>"
            "Score descriptions <span style='font-weight:400;color:#51656a'>· click to expand: what each score measures and how it is calculated</span></summary>"
            f"<div style='padding:0 14px 12px;font-size:.88rem;color:#132327'>{body}</div></details>")


def _pct(value) -> str:
    if value is None:
        return "—"
    text = f"{100 * value:.1f}"
    return "99.9" if text == "100.0" and value < 1 else text   # a value below 100 is never shown as 100.0


def _bar(value) -> str:
    if value is None:
        return ""
    return ("<div style='height:6px;border-radius:4px;background:rgba(19,35,39,.10);margin-top:4px;min-width:70px'>"
            f"<span style='display:block;height:6px;border-radius:4px;background:#0d6e6e;width:{max(0, min(100, 100 * value)):.1f}%'></span></div>")


def _summary(values: list[float]) -> str:
    if not values:
        return "<span style='color:#51656a;font-style:italic'>no scored analysis</span>"
    values = sorted(values)
    if len(values) < 4:
        return "values: " + ", ".join(_pct(v) for v in values)
    q = statistics.quantiles(values, n=4, method="inclusive")
    return f"median {_pct(q[1])} · IQR {_pct(q[0])}–{_pct(q[2])} · range {_pct(values[0])}–{_pct(values[-1])}"


def _mark(ok: bool | None, text: str) -> str:
    if ok is None:
        return f"<div><span style='color:#51656a;font-style:italic'>not applicable</span> · {text}</div>"
    icon = "<span style='color:#1a7a4a;font-weight:800'>✓</span>" if ok else f"<span style='color:{_RED};font-weight:800'>✗</span>"
    return f"<div>{icon} {text}</div>"


def _dup_pct(m: dict) -> str:
    if m.get("unique_sample_ids") is None:
        return "—"
    value = 1 - m["unique_sample_ids"]
    text = f"{100 * value:.1f}"
    return "<0.1" if text == "0.0" and value > 0 else text


def _item_detail(key: str, m: dict) -> str:
    if key == "sample_support":
        return f"{m['distinct_eligible_ids']} distinct ML-eligible sample IDs ÷ {m.get('sample_support_full_marks_at', 20):g}"
    if key == "samples_with_factor":
        text = f"{m['n_with_factor']} of {m['eligible_positions']} ML-eligible samples have a factor label"
        if m.get("samples_without_factor_examples"):
            text += f"; without one: {_e(', '.join(m['samples_without_factor_examples']))}"
        return text
    if key == "numeric_values":
        counted = m["value_cells"] - m["missing_entry_cells"]
        text = f"{m['text_cells']:,} text cells of {counted:,} value cells of ML-eligible samples ({m['missing_entry_cells']:,} missing entries left out)"
        if m.get("text_cells_by_class"):
            text += "; " + ", ".join(f"{_e(k)} {v:,}" for k, v in m["text_cells_by_class"].items())
        if m.get("text_examples"):
            text += f"; entries found: {_e(', '.join(m['text_examples']))}"
        if m.get("malformed_exponent_cells"):
            text += (f"; {m['malformed_exponent_cells']:,} values written like {_e(', '.join(m.get('malformed_exponent_examples', [])[:2]))} "
                     "are read as numbers")
        return text
    if key == "unique_sample_ids":
        text = (f"{m['repeated_positions']} of {m['eligible_positions']} ML-eligible positions are a duplicate entry of a sample ID already present "
                f"({_dup_pct(m)}%)")
        if m.get("positions_with_repeated_id"):
            text += f"; {m['sample_ids_repeated']} sample IDs occur more than once, on {m['positions_with_repeated_id']} positions"
        if m.get("repeated_id_examples"):
            text += f"; duplicated: {_e(', '.join(m['repeated_id_examples']))}"
        if m.get("repeated_positions"):
            text += _red_note("Needs handling before ML: duplicated sample IDs",
                              "Combine the entries of each duplicated sample ID into one row per sample "
                              "(for example by averaging them, or by keeping one). If they are kept as separate rows, keep them on the same side of a train/test split. "
                              "First check that the entries are the same sample: their values usually differ, and they can carry different labels.", "margin-top:3px")
        return text
    return ""


def _subject_note(m: dict) -> str:
    given, distinct = m.get("subject_ids_given_for", 0), m.get("distinct_eligible_ids", 0)
    if not given:
        return ("<div><b>Sample ID ↔ subject ID:</b> no subject ID is deposited for these samples, so it cannot be told "
                "whether several samples come from one subject.</div>")
    partial = "" if given == distinct else f" Subject IDs are deposited for {given} of {distinct} sample IDs."
    if not m.get("subjects_with_several_sample_ids"):
        extra = ""
        if given != distinct:
            extra += f" {distinct - given} of {distinct} sample IDs have no subject ID."
        if m.get("repeated_positions"):
            extra += " This matrix has duplicate sample IDs (item 4)."
        return (f"<div><b>Sample ID ↔ subject ID:</b> among the {given} sample IDs that have a subject ID, each subject ID links to one sample ID "
                f"({m['distinct_subject_ids']} subject IDs). This does not establish that the samples are independent, "
                f"and it does not rule out repeated sample positions.{extra}</div>")
    if m.get("distinct_subject_ids") == 1 and given > 1:
        only = m.get("subject_examples", [{}])[0].get("subject_id", "")
        return (f"<div><b>Sample ID ↔ subject ID:</b> all {given} sample IDs that have a subject ID carry the same one ({_e(str(only))}). "
                f"The field does not separate subjects in this matrix (it can hold a species, tissue, group or cell-line label), so it cannot guide a train/test split.{partial}</div>")
    examples = "; ".join(
        f"{_e(x['subject_id'])} → {_e(', '.join(x['sample_ids']))}" + (f" (+{x['n_sample_ids'] - len(x['sample_ids'])} more)" if x["n_sample_ids"] > len(x["sample_ids"]) else "")
        for x in m.get("subject_examples", []))
    return _red_note(f"⚠ Sample ID ↔ subject ID: {m['subjects_with_several_sample_ids']} subject IDs have more than one sample ID",
                     f"{m['sample_ids_sharing_a_subject']} sample IDs. If these are genuine subject IDs, split train and test by subject ID: samples of one subject on both sides can inflate test performance (data leakage). "
                     f"Examples: {examples}.{partial}")


def _endpoint_cell(ep: dict, source: str, study_id: str = "") -> str:
    if "m" not in ep:
        text = ep.get("note") or {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
        return f"<td style='{_TD};color:#51656a;font-style:italic'>{_e(text)}</td>"
    m = ep["m"]
    stored = ""
    if m.get("file_in_study_folder") and m["file_in_study_folder"] != study_id:
        stored = (f"<div style='{_SMALL}'>The repository lists this analysis ID under both studies; the file is read from {_e(m['file_in_study_folder'])}.</div>")
    edited = ""
    if m.get("edited"):
        edited = _red_note(f"Sample edits applied: {m['edited_positions']} of {m['positions']} positions differ from the deposit",
                           f"{m['excluded_positions']} left out. Scores below use the edited samples.", "font-size:.8rem;margin-top:6px")
    if not m.get("eligible_positions"):
        return (f"<td style='{_TD}'><b>Structural not assessable</b><div style='font-size:.82rem'>valid matrix · no ML-eligible sample</div>"
                f"<div style='{_SMALL}'>None of the {m['positions']} positions is ML-eligible: {m['qc_blank_positions']} QC-like, blank-like, standard-like or marked not eligible, "
                f"{m['tier1_alignment_rows']} Tier 1 sample alignment rows, {m['excluded_positions']} left out by a sample edit. "
                f"The four items cannot be scored. {int(m.get('n_features') or 0):,} features.</div>{edited}{stored}</td>")
    rows = "".join(
        f"<tr><td style='padding:4px 8px 4px 0;vertical-align:top'><span title='{_e(tip)}' style='border-bottom:1px dotted #51656a;cursor:help'>{_e(label)}</span>"
        f"<div style='font-size:.76rem;color:#51656a'>{_item_detail(key, m)}</div></td>"
        f"<td style='padding:4px 0 4px 10px;text-align:right;vertical-align:top;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap'>{_pct(m.get(key))}</td></tr>"
        for key, label, tip in _item_defs({"structural_sample_support_n": m.get("sample_support_full_marks_at", 20)}))
    tier1 = ""
    if source == "datatable" and m.get("tier1_alignment_rows"):
        examples = ", ".join(m.get("tier1_alignment_examples", []))
        more = "" if m["tier1_alignment_rows"] <= len(m.get("tier1_alignment_examples", [])) else f" (+{m['tier1_alignment_rows'] - len(m['tier1_alignment_examples'])} more)"
        tier1 = _red_note(f"Tier 1 sample alignment: {m['tier1_alignment_rows']} of {m['positions']} sample IDs",
                          "These sample IDs are in this datatable with all values zero, and they are not present in the paired mwTab data block of the same analysis. "
                          f"They are not measured samples and are left out of every count and score here. IDs: {_e(examples)}{more}.", "font-size:.8rem;margin-top:6px")
    declared = m.get("declared_samples_measured")
    other = (
        _mark(None if m.get("sample_ids_in_sample_table") is None else m["sample_ids_in_sample_table"] >= 0.99999,
              "every ML-eligible sample ID has a line in the sample table" + ("" if m.get("sample_ids_in_sample_table") in (None, 1.0) else f" ({_pct(m['sample_ids_in_sample_table'])}%)"))
        + _mark(None if m.get("every_factor_value_filled") is None else m["every_factor_value_filled"] >= 0.99999,
                "every factor of the label has a value" + ("" if m.get("every_factor_value_filled") in (None, 1.0) else f" ({_pct(m['every_factor_value_filled'])}% of ML-eligible samples)"))
        + _mark(None if m.get("ids_agree_with_other_endpoints") is None else m["ids_agree_with_other_endpoints"] >= 0.99999,
                "sample IDs found in the other endpoints of this analysis" + ("" if m.get("ids_agree_with_other_endpoints") in (None, 1.0) else f" ({_pct(m['ids_agree_with_other_endpoints'])}%)"))
        + ("" if declared is None else
           f"<div>· {_pct(declared)}% of the {int(m.get('declared_non_qc_samples') or 0)} non-QC samples listed in the sample table have a measured position here "
           "(an analysis may measure only part of the study's samples)</div>"))
    counts = (f"{m['positions']} positions · {m['measured_positions']} measured · {m['qc_blank_positions']} QC-like, blank-like or standard-like · "
              f"{m['eligible_positions']} ML-eligible · {int(m.get('n_features') or 0):,} features · {m['value_cells']:,} value cells of ML-eligible samples")
    return (f"<td style='{_TD}'><span style='font-weight:700;font-size:1.05rem;white-space:nowrap'>{_pct(m.get('structural'))}</span>{_bar(m.get('structural'))}"
            f"{tier1}{edited}{_curated_note(m.get('curated_corrections'), 'structural')}"
            "<details style='margin-top:6px'><summary style='cursor:pointer;color:#113e52;font-size:.8rem'>scored items and notes</summary>"
            f"<table style='border-collapse:collapse;font-size:.82rem;margin-top:6px;width:100%'>{rows}</table>"
            f"<div style='{_SMALL}'><b>Shown, not scored</b>{_subject_note(m)}{other}</div>"
            f"<div style='{_SMALL}'>{_e(counts)}</div>{stored}</details></td>")


def _per_endpoint_rows(study: dict) -> str:
    out = ""
    for key, label in SOURCES:
        scored = [(a["analysis_id"], a["endpoints"][key]["m"]["structural"]) for a in study["analyses"]
                  if a["endpoints"][key].get("m", {}).get("structural") is not None]
        extremes = ""
        if len(scored) > 1 and max(v for _, v in scored) - min(v for _, v in scored) > 1e-9:
            low, high = min(scored, key=lambda x: x[1]), max(scored, key=lambda x: x[1])
            extremes = f" · lowest {_e(low[0])} ({_pct(low[1])}), highest {_e(high[0])} ({_pct(high[1])})"
        out += (f"<tr><td style='{_TD}'><b>{label}</b></td><td style='{_TD};text-align:right'>{len(scored)}</td>"
                f"<td style='{_TD}'>{_summary([v for _, v in scored])}{extremes}</td></tr>")
    return out


def _intro(params: dict | None = None, overrides: dict | None = None) -> str:
    items = "".join(f"<li><b>{_e(label)}.</b> {_e(tip)}</li>" for _, label, tip in _item_defs(params))
    body = (
        "<p style='margin:8px 0'>The Structural score of a matrix is the mean of four items (0–100). A matrix without an ML-eligible sample is "
        "\u201cStructural not assessable\u201d.</p>"
        f"<ol style='margin:0 0 8px 18px;padding:0'>{items}</ol>"
        f"<p style='margin:0 0 8px;color:{_RED}'><b>Data leakage.</b> A sample or subject on both sides of a train/test split makes the test result "
        "look better than it is. Two deposit patterns carry this risk: duplicate sample IDs (item 4), and several sample IDs under one subject ID "
        "(shown, not scored, under \u201cShown, not scored\u201d). Combine or keep together the entries of one sample, and keep all samples of one "
        "subject on the same side of the split.</p>"
        "<p style='margin:0 0 8px'><b>Endpoint agreement</b> (not scored). The last column compares the measured sample IDs and the feature counts "
        "of an analysis's endpoints, which describe the same measurement; a difference is flagged in red.</p>"
        f"<p style='margin:0;color:#51656a'>{_e(POSITION_NOTE)}</p>")
    return (
        "<p style='font-size:.88rem;margin:0 0 10px'>Each valid matrix (one analysis from one endpoint) is scored on its own; endpoints are not "
        "averaged. The Structural score is reported on its own and does not enter the readiness profile (cross-validation sample counts "
        "and analytical review alerts).</p>"
        + _descriptions(body)
        + (_red_note(f"Sample edits are active for {len(overrides)} sample IDs", "An edit applies to that sample ID in every "
                     "analysis and endpoint where it occurs; the matrices it changes are marked below. Clear the edits in Adjust Matrix Properties.",
                     "font-size:.88rem;margin:0 0 12px") if overrides else ""))


def structural_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    study = _study(study_id, params, overrides)
    if study is None:
        return "<div style='" + _DASHED + "'>No analysis × endpoint data for this study.</div>"
    rows = ""
    for a in study["analyses"]:
        scores = [ep["m"]["structural"] for ep in a["endpoints"].values() if ep.get("m", {}).get("structural") is not None]
        n_valid = sum(1 for ep in a["endpoints"].values() if ep.get("availability") == "valid")
        if len(scores) > 1:
            spread = f"{_pct(min(scores))}–{_pct(max(scores))}" if max(scores) - min(scores) > 1e-9 else "same on all scored endpoints"
        elif scores:
            spread = "one scored endpoint"
        elif n_valid:
            spread = "Structural not assessable: valid matrix, no ML-eligible sample"
        else:
            spread = "no valid matrix"
        feats = [(label, int(a["endpoints"][k]["m"].get("n_features") or 0)) for k, label in SOURCES if "m" in a["endpoints"][k]]
        if len(feats) > 1 and len({n for _, n in feats}) > 1:
            spread += ("<div style='margin-top:6px;font-size:.8rem'><b>The endpoints do not hold the same number of features:</b> "
                       + ", ".join(f"{label} {n:,}" for label, n in feats) + ".</div>")
        rows += (f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>" + "".join(_endpoint_cell(a["endpoints"][k], k, study["study_id"]) for k, _ in SOURCES)
                 + f"<td style='{_TD};color:#51656a;min-width:240px'>{spread}{_agreement_html(a['agreement'])}</td></tr>")
    return (
        _intro(params, overrides)
        + "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Across analyses, by endpoint</h4>"
        f"<div style='overflow-x:auto;margin-bottom:16px'><table style='border-collapse:collapse;width:100%'><tr><th style='{_TH}'>Endpoint</th>"
        f"<th style='{_TH};text-align:right'>Scored analyses</th><th style='{_TH}'>Structural score</th></tr>{_per_endpoint_rows(study)}</table></div>"
        "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Every analysis × endpoint</h4>"
        f"<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;min-width:980px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
        f"<th style='{_TH};white-space:nowrap;min-width:230px'>datatable</th><th style='{_TH};white-space:nowrap;min-width:230px'>mwTab</th>"
        f"<th style='{_TH};white-space:nowrap;min-width:230px'>untarg_data</th><th style='{_TH};white-space:nowrap'>Across endpoints</th></tr>{rows}</table></div>")


def _agree(values: list[tuple[str, object]], fmt=lambda v: f"{v:,}") -> str:
    """One value when every endpoint of the analysis gives the same one; each endpoint's value, marked, when they differ."""
    if not values:
        return "<span style='color:#51656a'>—</span>"
    if len({v for _, v in values}) == 1:
        return f"<span style='font-weight:700;font-variant-numeric:tabular-nums'>{fmt(values[0][1])}</span>"
    # the badge compares datatable with mwTab only (user, 2026-11-01): they hold the same data in two files; untarg_data is another
    # file (untargeted features) and is expected to differ, so it is listed without a badge
    v = dict(values)
    tier1_differ = "datatable" in v and "mwTab" in v and v["datatable"] != v["mwTab"]
    badge = ("<span title='datatable and mwTab hold the same analysis in two files and do not give the same value here; untarg_data is "
             "another file and is not compared.' style='display:inline-block;font-size:.66rem;font-weight:700;padding:1px 6px;border-radius:999px;"
             "background:rgba(210,125,45,.14);color:#995b00;white-space:nowrap;margin-left:6px'>datatable ≠ mwTab</span>")
    lines = "".join(f"<div style='white-space:nowrap;line-height:1.35'><span style='color:#51656a'>{label}</span> <b style='font-variant-numeric:tabular-nums'>{fmt(x)}</b>"
                    f"{badge if tier1_differ and label == 'mwTab' else ''}</div>" for label, x in values)
    return f"<div class='v9-differs'>{lines}</div>"


def overview_strip(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    """Overview: three study facts that hold whichever endpoint is read, then one compact row per analysis."""
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    study = _study(sid, params, overrides) if raw else None
    if raw is None or study is None:
        return ""
    by_endpoint = {key: 0 for key, _ in SOURCES}
    measured_ids: set = set(); eligible_ids: set = set(); eligible_deposited: set = set()
    for a in raw["analyses"]:
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            if "roster" not in ep:
                continue
            by_endpoint[key] += 1
            for pos in sv.effective_positions(ep["roster"], overrides):
                if pos["row"][sv.I_ALIGN] or pos["excluded"]:
                    continue
                measured_ids.add(pos["sample_id"])
                if pos["eligible"]:
                    eligible_ids.add(pos["sample_id"])
            if overrides:
                eligible_deposited.update(p["sample_id"] for p in sv.effective_positions(ep["roster"], None) if p["eligible"] and not p["row"][sv.I_ALIGN])
    n_matrices = sum(by_endpoint.values())
    n_without = sum(1 for a in raw["analyses"] if not any("roster" in (a["endpoints"].get(key) or {}) for key, _ in SOURCES))
    n_all = len(raw["analyses"])
    if n_without == 0:
        valid_note = "all with a valid matrix"
    elif n_without == n_all:
        valid_note = "none has a valid matrix"
    else:
        valid_note = f"{n_all - n_without} of {n_all} with a valid matrix"
    tile = ("padding:12px;border-radius:14px;background:rgba(13,110,110,.06);text-align:center")
    big = "font-size:1.4rem;font-weight:700;color:#0d6e6e"
    label = "font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a;margin-top:4px"
    small = "font-size:.74rem;color:#51656a;margin-top:2px"
    tiles = (
        f"<div style='{tile}' title='Analyses listed for this study, with or without a valid matrix.'><div style='{big}'>{len(raw['analyses']):,}</div><div style='{label}'>Analyses</div>"
        f"<div style='{small}'>{valid_note}</div></div>"
        f"<div style='{tile}' title='A valid matrix is one analysis read from one endpoint that holds at least one numeric value.'><div style='{big}'>{n_matrices:,}</div><div style='{label}'>Valid matrices</div>"
        f"<div style='{small}'>" + " · ".join(f"{lab} {by_endpoint[key]}" for key, lab in SOURCES) + "</div></div>"
        f"<div style='{tile}' title='Distinct sample IDs with a measured position in at least one matrix of the study. ML-eligible: not QC-like, blank-like or standard-like and not a Tier 1 sample alignment row, in at least one matrix.'>"
        f"<div style='{big}'>{len(measured_ids):,}</div><div style='{label}'>Sample IDs in the study</div><div style='{small}'>{len(eligible_ids):,} ML-eligible"
        + (f" (as deposited {len(eligible_deposited):,})" if overrides else "") + "</div></div>")
    chip_on = "display:inline-block;font-size:.66rem;font-weight:700;padding:1px 6px;border-radius:999px;background:#0d6e6e;color:#fff;margin:1px 2px 1px 0;white-space:nowrap"
    chip_off = "display:inline-block;font-size:.66rem;padding:1px 6px;border-radius:999px;border:1px dashed rgba(19,35,39,.25);color:#9aa8ab;margin:1px 2px 1px 0;white-space:nowrap"
    td = "padding:6px 8px;border-bottom:1px solid rgba(19,35,39,.08);vertical-align:middle;font-size:.84rem"
    th = _TH + ";padding:6px 8px;line-height:1.2"
    rows = ""
    for a in study["analyses"]:
        valid = [(key, lab, a["endpoints"][key]["m"]) for key, lab in SOURCES if "m" in a["endpoints"][key]]
        chips = ""
        for key, lab in SOURCES:
            ep = a["endpoints"][key]
            status = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
            chips += (f"<span style='{chip_on}'>{lab}</span>" if "m" in ep else f"<span style='{chip_off}' title='{_e(lab)}: {_e(status or 'not available')}'>{lab}</span>")
        if valid:
            cells = (f"<td style='{td}'>" + _agree([(lab, m["distinct_eligible_ids"]) for _, lab, m in valid]) + "</td>"
                     f"<td style='{td}'>" + _agree([(lab, int(m.get("n_features") or 0)) for _, lab, m in valid]) + "</td>")
        else:
            cells = f"<td style='{td};color:#51656a;font-style:italic' colspan='2'>no valid matrix on any endpoint</td>"
        rows += f"<tr><td style='{td};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td><td style='{td}'>{chips}</td>{cells}</tr>"
    return (
        f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:16px'>{tiles}</div>"
        + _edit_note(overrides, len(eligible_ids), len(eligible_deposited)) +
        "<div style='margin-top:14px;border:1px solid rgba(19,35,39,.1);border-radius:14px;background:rgba(255,255,255,.7);overflow:hidden'>"
        "<div style='padding:8px 12px;font-size:.78rem;color:#51656a;border-bottom:1px solid rgba(19,35,39,.08)'><b style='color:#132327;text-transform:uppercase;letter-spacing:.06em;font-size:.74rem'>Every analysis</b> · "
        "one value is shown when all endpoints of an analysis agree; each endpoint is shown when they differ. Nothing is averaged and no endpoint is preferred.</div>"
        f"<div style='overflow:auto;max-height:420px'><table class='v9-overview-table' style='border-collapse:collapse;width:100%'><tr><th style='{th}'>Analysis</th><th style='{th}' title='Endpoints that hold a valid matrix for this analysis; a dashed chip means no valid matrix on that endpoint.'>Endpoints</th>"
        f"<th style='{th}' title='Distinct ML-eligible sample IDs of the matrix.'>ML-eligible samples</th><th style='{th}' title='Feature columns (datatable, untarg_data) or feature rows (mwTab data block) of the matrix.'>Features</th></tr>{rows}</table></div></div>")


def _edit_note(overrides: dict | None, eligible: int, deposited: int) -> str:
    """Overview note while sample edits are in force: what was edited and the effect on the ML-eligible sample IDs."""
    if not overrides:
        return ""
    kinds = collections.Counter()
    for e in overrides.values():
        kinds["marked ML-eligible"] += e.get("eligible") is True
        kinds["marked not ML-eligible"] += e.get("eligible") is False
        kinds["left out"] += bool(e.get("excluded"))
        kinds["given a factor label"] += bool(str(e.get("label", "") or "").strip())
        kinds["given a batch-like label"] += bool(str(e.get("batch", "") or "").strip())
    what = ", ".join(f"{n:,} {k}" for k, n in kinds.items() if n)
    ids = sorted(overrides)
    listed = ", ".join(_e(i) for i in ids[:40]) + (f" (+{len(ids) - 40:,} more)" if len(ids) > 40 else "")
    return ("<details class='v9-edit-note' style='margin-top:12px;padding:8px 12px;border-radius:12px;border:1px solid rgba(13,110,110,.35);background:rgba(13,110,110,.06);font-size:.84rem'>"
            f"<summary style='cursor:pointer'><b>Sample edits in force:</b> {len(overrides):,} sample ID{'s' if len(overrides) != 1 else ''} edited ({what}). "
            f"ML-eligible sample IDs: <b>{eligible:,}</b> (as deposited {deposited:,}).</summary>"
            f"<div style='margin-top:4px;color:#51656a'>Every panel, score and download on this page uses the edited samples; the deposited files are not changed. "
            f"Change or clear the edits in Adjust Matrix Properties. Edited sample IDs: {listed}.</div></details>")


def placeholder_panel(tid: str) -> str:
    return (f"<div style='{_DASHED}'><b>{_e(PLACEHOLDER_SECTIONS.get(tid, tid))}</b><br>"
            "Not shown yet. This section will be filled per analysis × endpoint.</div>")


def overview_section_cards(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    """The sections that are not filled yet (Analytical QC and Metadata are in the Overview scores; Structural is not in the Overview)."""
    return "".join(f"<div style='{_DASHED}'><div style='font-size:.72rem;text-transform:uppercase;letter-spacing:.05em'>{_e(label)}</div>"
                   "<div style='font-size:.8rem;margin-top:4px'>not shown yet</div></div>"
                   for tid, label in PLACEHOLDER_SECTIONS.items() if tid != "analytical")


# ---------------------------------------------------------------- Overview: every scored item of Structural, Analytical QC and Metadata & FAIR
# One grid (rows = analyses). Structural and Analytical QC have one group of tiles per endpoint, Metadata & FAIR one group per analysis.
# Each tile shows the value its own tab shows (same computation, same rounding); nothing is combined, averaged or ranked.
_OV_STRUCT = (("sample_support", "S", "Sample support"), ("samples_with_factor", "F", "Samples with a factor label"),
              ("numeric_values", "N", "Numeric value cells"), ("unique_sample_ids", "Id", "Duplicate sample IDs"))
_OV_ANALYTICAL = (("missingness", "Mz", "Missing entries and zeros"), ("low_information", "L", "Low-information features"),
                  ("redundancy", "R", "Redundant features"), ("outliers", "O", "Outlier samples"))
_OV_ANNOTATION = (("named_coverage", "Cv", "Named-feature coverage"), ("refmet_mapping", "Rm", "RefMet mapping of named features"))
_OV_LABELS = (("class_support", "Cs", "Class support"), ("cv_feasible", "Kf", "Cross-validation feasible"))
_OV_ML = (("class_separability", "Sp", "Class separability"),)
_OV_FAIR = (("fair_metabolite_identifier_resolvability", "Rf"), ("external_cross_references_via_refmet", "Ex"),
            ("mass_rt_like_metadata_presence", "MR"), ("lc_method_context_presence", "LC"))
_OV_LC_CHECKS = (("column", "column"), ("mobile_phase", "mobile phases"), ("gradient", "gradient"))
_OV_AVAILABILITY = {"no_file": "no file on this endpoint", "invalid_or_empty": "invalid or empty file: no valid matrix"}
_OV_STOPS = ((0, (122, 31, 61)), (50, (196, 85, 58)), (75, (227, 168, 63)), (90, (190, 214, 182)), (100, (221, 233, 228)))


def _ov_colour(v: float) -> str:
    """Continuous colour of a 0-100 value: low values strong, high values pale (no thresholds)."""
    v = max(0.0, min(100.0, float(v)))
    for (x1, c1), (x2, c2) in zip(_OV_STOPS, _OV_STOPS[1:]):
        if v <= x2:
            t = (v - x1) / (x2 - x1)
            return "rgb(" + ",".join(str(round(a + t * (b - a))) for a, b in zip(c1, c2)) + ")"
    return "rgb(221,233,228)"


def _ov_availability(av: str) -> str:
    return _OV_AVAILABILITY.get(av, (av or "not available").replace("_", " "))


def _ov_definitions(params: dict | None) -> dict:
    n = float((params or {}).get("structural_sample_support_n", sv.DEFAULT_PARAMS["structural_sample_support_n"]))
    return {
        "sample_support": f"Distinct ML-eligible sample IDs ÷ {n:g}, capped at 100.",
        "samples_with_factor": "ML-eligible samples with a non-empty factor label ÷ ML-eligible samples.",
        "numeric_values": "100 − the percent of value cells of ML-eligible samples that hold text (missing entries left out).",
        "unique_sample_ids": "Distinct sample IDs ÷ ML-eligible positions (a position is one sample row, or one mwTab sample column); 100 = no sample ID entered twice.",
        "missingness": "Lowest applicable category: missing entries (empty or non-numeric), zeros meaning unknown and, for datatables paired with mwTab, zeros hiding a missing value and zeros replacing a value.",
        "low_information": "Features that are neither constant nor near-zero variance ÷ features judged (caret nearZeroVar).",
        "redundancy": f"Features not redundant at Spearman |ρ| > {cr.cutoff(params):g} ÷ features evaluated (caret findCorrelation).",
        "outliers": f"ML-eligible samples whose median lies inside Tukey's fences (k = {ov.k_of(params):g}) ÷ samples evaluated.",
        "units": "Units declared in the mwTab file: ✓ = 100, ✕ = 0.",
        "class_support": f"100 × H(smallest vs largest class) × min(1, smallest ÷ {lb.folds(params)}): class balance (binary entropy of the smallest against the largest class; 1:1 = 100, 1:2 = 91.8, 1:19 = 28.6) times the share of the fold target ({lb.folds(params)} folds) met by the smallest class; equals class balance once the smallest class has {lb.folds(params)} samples; one class: 0; a MERIT-ML heuristic; classes as chosen in Label Structure.",
        "class_separability": "100 × mean over classes of (1 − R-value of the class): share of each class's samples whose {k} nearest neighbours are not mostly ({t} or fewer of them) in another class (Oh 2011), averaged over classes; higher = stronger local separation of the chosen classes, not an accuracy estimate (a batch effect aligned with the classes can raise it; a low value does not mean a model cannot learn them); chance level on hover; classes as chosen in Label Structure.".format(k=sp.settings(params)[0], t=sp.settings(params)[1]),
        "cv_feasible": f"At least two classes and the smallest has at least {lb.folds(params)} samples (stratified {lb.folds(params)}-fold cross-validation possible): ✓ = 100, ✕ = 0.",
        "named_coverage": "Named features ÷ eligible features of the matrix (manuscript classifier, Text S3).",
        "refmet_mapping": "Named features of the matrix with a RefMet name in the repository's records ÷ named features.",
        "fair_metabolite_identifier_resolvability": "Metabolite records of the analysis with a RefMet name ÷ metabolite records.",
        "external_cross_references_via_refmet": "Records with a RefMet name that RefMet links to an outside database ÷ records with a RefMet name.",
        "mass_rt_like_metadata_presence": "The mwTab annotation table (named metabolites) has a mass/m/z-like or RT/RI-like field: ✓ = 100, ✕ = 0; unnamed untargeted m/z–RT features are not counted.",
        "lc_method_context_presence": "LC analysis: column, both mobile phases and gradient reported (one third each).",
    }


def _ov_structural_detail(key: str, m: dict) -> str:
    if key == "sample_support":
        return f"{m['distinct_eligible_ids']:,} distinct ML-eligible sample IDs; 100 at {m['sample_support_full_marks_at']:g}"
    if key == "samples_with_factor":
        return f"{m['n_with_factor']:,} of {m['eligible_positions']:,} ML-eligible samples have a factor label"
    if key == "numeric_values":
        return f"{m['text_cells']:,} text cells of {m['value_cells'] - m['missing_entry_cells']:,} value cells"
    return f"{m['repeated_positions']:,} of {m['eligible_positions']:,} ML-eligible positions repeat a sample ID"


def _ov_structural_reason(key: str, m: dict) -> str:
    if not m.get("eligible_positions"):
        return "no ML-eligible sample (every position is QC-like, blank-like, standard-like, a Tier 1 sample alignment row or left out)"
    if key == "numeric_values":
        return "no value cell with a number or text (only missing entries)"
    return "no ML-eligible sample ID"


def _ov_analytical_detail(key: str, r: dict) -> str:
    if key == "missingness":
        if r["score"] >= 100:
            return "every item 100"
        by = [mv.ITEMS.get(k, r["items"][k]["label"]) for k in r.get("set_by") or [] if k in r["items"]]
        return ("lowest item: " + ", ".join(by)) if by else ""
    if key == "low_information":
        return f"{r['flagged']:,} of {r['judged']:,} features show (almost) no variation"
    if key == "redundancy":
        return f"{r['redundant']:,} of {r['features_evaluated']:,} features redundant (|ρ| > {r['cutoff']:g})"
    return f"{r['outliers']:,} of {r['samples_evaluated']:,} samples outside the fences (k = {r['k']:g})"


def _ov_units(rec: dict) -> tuple[bool, str]:
    u = rec.get("units") or {}; dep = u.get("deposited") or {}
    if u.get("declared"):
        return True, f"declared: “{dep.get('text') or u.get('raw') or ''}”"
    if dep.get("text"):
        return False, f"“{dep['text']}” is a placeholder, not counted"
    return False, dep.get("state") or "nothing deposited"


def _overview_data(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    """Every scored item of every matrix (Structural, Analytical QC) and analysis (Metadata & FAIR), with the value each tab shows,
    a short count, or the reason it is not available. Computed by the tabs' own functions for these parameters and sample edits."""
    sid = str(study_id or "").strip().upper()
    study = _study(sid, params, overrides)
    if study is None:
        return None
    blocks = {"missingness": _missingness_study(sid, overrides), "units": _scale_study(sid, overrides),
              "low_information": _low_information_study(sid, params, overrides), "redundancy": _redundancy_study(sid, params, overrides),
              "outliers": _outlier_study(sid, params, overrides)}
    by_id = {k: {a["analysis_id"]: a for a in (v or [])} for k, v in blocks.items()}
    ann = {a["analysis_id"]: a for a in (_annotation_study(sid) or [])}
    lbd = _labels_study(sid, params, overrides) or {"analyses": [], "choice": None}
    lbs = {a["analysis_id"]: a for a in lbd["analyses"]}
    mls = {a["analysis_id"]: a for a in ((_ml_study(sid, params, overrides) or {}).get("analyses") or [])}
    analyses = []
    for a in study["analyses"]:
        aid = a["analysis_id"]; endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"][key]
            if "m" not in ep:
                endpoints[key] = {"availability": ep.get("availability", ""), "reason": _ov_availability(ep.get("availability", ""))}
                continue
            m = ep["m"]; items = {}
            for k, _, _ in _OV_STRUCT:
                v = m.get(k)
                items[k] = ({"value": 100 * v, "shown": _pct(v), "detail": _ov_structural_detail(k, m)} if v is not None
                            else {"value": None, "shown": _pct(None), "reason": _ov_structural_reason(k, m)})
            recs = {k: ((by_id[k].get(aid) or {}).get("endpoints", {}).get(key) or {}).get("rec") for k in blocks}
            for k, _, _ in _OV_ANALYTICAL:
                r = recs[k]
                if not r:
                    items[k] = {"value": None, "shown": "–", "reason": "no result for this matrix"}
                elif r.get("score") is None:
                    items[k] = {"value": None, "shown": "–", "reason": r.get("not_assessed") or r.get("not_assessable") or "not assessed"}
                else:
                    items[k] = {"value": r["score"], "shown": _floor1(r["score"]), "detail": _ov_analytical_detail(k, r)}
                    if k == "redundancy" and r.get("note"):
                        items[k]["note"] = r["note"]
            if recs["units"]:
                ok, text = _ov_units(recs["units"])
                items["units"] = {"value": recs["units"]["score"], "shown": f"{recs['units']['score']:.0f}", "flag": ok, "detail": text}
            else:
                items["units"] = {"value": None, "shown": "–", "reason": "no result for this matrix"}
            ar = ((ann.get(aid) or {}).get("endpoints", {}).get(key) or {}).get("rec")
            for k, sub, what, det in (("named_coverage", "coverage", "named", lambda r: f"{r['named']:,} of {r['eligible']:,} features named"),
                                      ("refmet_mapping", "refmet", "mapped", lambda r: f"{r['refmet']['mapped']:,} of {r['named']:,} named features have a RefMet name")):
                if not ar:
                    items[k] = {"value": None, "shown": "–", "reason": "no result for this matrix"}
                elif ar[sub]["score"] is None:
                    items[k] = {"value": None, "shown": "–", "reason": ar[sub]["not_assessed"]}
                else:
                    items[k] = {"value": ar[sub]["score"], "shown": _floor1(ar[sub]["score"]), "detail": det(ar)}
            mr = ((mls.get(aid) or {}).get("endpoints", {}).get(key) or {}).get("rec")
            if not mr:
                items["class_separability"] = {"value": None, "shown": "–", "reason": "no result for this matrix"}
            elif mr.get("score") is None:
                items["class_separability"] = {"value": None, "shown": "–", "reason": mr.get("not_assessed") or "not assessed"}
            else:
                items["class_separability"] = {"value": mr["score"], "shown": _floor1(mr["score"]),
                                               "detail": f"chance level {_floor1(mr['chance'])}; {_ml_num(mr['in_overlap'])} of {mr['samples']:,} samples in an overlap area; {len(mr['classes']):,} classes"}
            lab_rec = (((lbs.get(aid) or {}).get("endpoints") or {}).get(key) or {}).get("rec")
            readiness = {"data": rd.data_result(items, params), "task": rd.task_result(lab_rec, mr, lb.folds(params))}
            readiness["summary"] = rd.summary(readiness["data"], readiness["task"]); readiness["summary_parts"] = rd.summary_parts(readiness["data"], readiness["task"])
            endpoints[key] = {"availability": "valid", "items": items, "edited": bool(m.get("edited")), "readiness": readiness}
            # values the readiness profile shows beside the results (display only; nothing here enters a result)
            eligible = m.get("distinct_eligible_ids") or 0
            endpoints[key]["profile"] = {
                "features": m.get("n_features"), "eligible_sample_ids": eligible,
                "labelled_sample_ids": sum((lab_rec or {}).get("classes", {}).values()) if lab_rec else 0,
                "features_per_sample": (mr or {}).get("features_per_sample") if eligible else None,
                "measured_positions": m.get("measured_positions"), "qc_blank_positions": m.get("qc_blank_positions"),
                "tier1_alignment_rows": m.get("tier1_alignment_rows") or 0, "malformed_matrix": bool((ar or {}).get("malformed_matrix")),
                "subjects_with_several_samples": (mr or {}).get("subjects_with_several_samples") or 0,
                "most_shared_subject": (mr or {}).get("most_shared_subject"), "subjects_confirmed": bool((mr or {}).get("subjects_confirmed")),
                "other_factors": (lab_rec or {}).get("other_factors"),
                "missingness_set_by": (recs.get("missingness") or {}).get("set_by") or []}
        metrics = ((a.get("metadata") or {}).get("metrics") or {})
        fair = {}
        for k, _ in _OV_FAIR:
            mt = metrics.get(k) or {}
            if not mt.get("assessable") or mt.get("score") is None:
                fair[k] = {"value": None, "shown": "not assessed", "reason": mt.get("reason") or "not assessable"}
                continue
            shown, _note = _fair_value(k, mt, a.get("unnamed_coordinates"))
            fair[k] = {"value": 100 * mt["score"], "shown": shown, "detail": _note}
            if k == "mass_rt_like_metadata_presence":
                fair[k]["flag"] = bool(mt.get("present"))
            if k == "lc_method_context_presence":
                fair[k]["checks"] = {c: bool(mt.get(c)) for c, _ in _OV_LC_CHECKS}
        if fair["lc_method_context_presence"].get("value") is None:      # why LC-method context does not apply, as declared
            fair["lc_method_context_presence"]["declared"] = _lc_declared(sid, aid)
        la = lbs.get(aid); labels = {}
        for k, _, _ in _OV_LABELS:
            if not la or not la["valid"]:
                labels[k] = {"value": None, "shown": "–", "absent": True, "reason": "no valid matrix on any endpoint"}
            elif la["endpoints_agree"] or len({_lb_value(la["endpoints"][e]["rec"], k) for e in la["valid"]}) == 1:
                r = la["endpoints"][la["valid"][0]]["rec"]       # the value is the same on every endpoint (counts may differ)
                labels[k] = ({"value": r[k], "shown": _floor1(r[k]), "detail": f"{_lb_used(r)}; smallest {r['smallest']:,}, largest {r['largest']:,}"
                              + ("" if la["endpoints_agree"] else " (the same value on every endpoint; their samples differ slightly)")}
                             if r.get(k) is not None else {"value": None, "shown": "–", "reason": r.get("not_assessed") or "not assessed"})
            else:
                parts = [f"{dict(SOURCES)[e]} {_lb_value(la['endpoints'][e]['rec'], k)}" for e in la["valid"]]
                labels[k] = {"value": None, "shown": "–", "differs": True, "reason": "endpoints differ: " + " · ".join(parts)}
        valid = [k for k, _ in SOURCES if (endpoints.get(k) or {}).get("readiness")]
        agree = len({rd.signature(endpoints[k]["readiness"]["data"], endpoints[k]["readiness"]["task"]) for k in valid}) <= 1
        analyses.append({"analysis_id": aid, "endpoints": endpoints, "labels": labels, "fair": fair,
                         "readiness_endpoints_agree": agree if valid else None})
    meta = ((study.get("study_metadata") or {}).get("fair_study_metadata_compliance") or {})
    return {"study_id": sid, "analyses": analyses, "study_metadata": meta, "class_definition": _lb_choice_text(lbd.get("choice")),
            "deposited_labels": lbd.get("choice") is None, "label_choice": lbd if "items" in lbd else None}


def _ov_tip(name: str, where: str, it: dict, definition: str) -> str:
    """Hover card of one tile, as escaped HTML in an attribute."""
    if it.get("value") is None:
        body = f"<div class='v9-ovg-tv'>not available</div><div>{_e(it.get('reason') or '')}</div>"
    else:
        body = f"<div class='v9-ovg-tv'>{_e(it['shown'])}</div>" + (f"<div>{_e(it['detail'])}</div>" if it.get("detail") else "")
        if it.get("note"):
            body += f"<div>{_e(it['note'])}</div>"
    return _e(f"<b>{_e(name)}</b><div>{_e(where)}</div>{body}<div class='v9-ovg-tdef'>{_e(definition)}</div>", quote=True)


def _ov_tile(name: str, where: str, it: dict, definition: str, key: str) -> str:
    tip = _ov_tip(name, where, it, definition)
    if it.get("value") is None:
        return f"<td><span class='v9-ovg-tile v9-ovg-na' tabindex='0' data-k='{key}' data-shown='' data-tip='{tip}'></span></td>"
    v = it["value"]
    stale = " v9-ovg-stale" if it.get("note") else ""
    return (f"<td><span class='v9-ovg-tile{stale}' tabindex='0' data-k='{key}' data-shown='{_e(it['shown'])}' data-tip='{tip}' "
            f"style='background:{_ov_colour(v)};color:{'#ffffff' if v < 50 else '#14211f'}'><i>{_e(it['shown'])}</i></span></td>")


def _ov_flag(name: str, where: str, it: dict, definition: str, key: str, yes: str, no: str) -> str:
    tip = _ov_tip(name, where, it, definition)
    if it.get("value") is None:
        return f"<td><span class='v9-ovg-flag v9-ovg-na' tabindex='0' data-k='{key}' data-shown='' data-tip='{tip}'></span></td>"
    ok = it["flag"]
    return (f"<td><span class='v9-ovg-flag {'v9-ovg-yes' if ok else 'v9-ovg-no'}' tabindex='0' data-k='{key}' data-shown='{_e(it['shown'])}' "
            f"aria-label='{yes if ok else no}' data-tip='{tip}'>{'✓' if ok else '✕'}</span></td>")


def _ov_goto(tab: str, label: str) -> str:
    """A section heading that opens its tab (the tab button of the same view is clicked)."""
    return f"<a href='#' class='v9-ovg-goto' data-goto='{tab}' title='Open the {label.replace('&amp;', '&')} tab'>{label} ›</a>"


def _ov_absent(n: int, reason: str, analysis_id: str, label: str) -> str:
    tip = _e(f"<b>{_e(analysis_id)} · {_e(label)}</b><div class='v9-ovg-tv'>no valid matrix</div><div>{_e(reason)}</div>", quote=True)
    return "".join(f"<td><span class='v9-ovg-tile v9-ovg-absent' tabindex='0' data-k='absent' data-tip='{tip}'></span></td>" for _ in range(n))


_OV_CSS = """<style>
.v9-ovg{margin:18px 0 16px;min-width:0;max-width:100%}
.v9-ovg h4{margin:0 0 4px;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em}
.v9-ovg-lede{font-size:.81rem;color:#51656a;margin:0 0 8px;max-width:80ch}
.v9-ovg-bar{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;font-size:.76rem;color:#51656a;margin:0 0 8px}
.v9-ovg-key{display:inline-flex;gap:5px;align-items:center;white-space:nowrap}
.v9-ovg-ramp{display:inline-block;width:120px;height:10px;border-radius:3px;vertical-align:middle}
.v9-ovg-scroll{overflow-x:auto;contain:inline-size;max-width:100%;border:1px solid rgba(19,35,39,.1);border-radius:12px;background:rgba(255,255,255,.85)}
.v9-ovg table{border-collapse:separate;border-spacing:0;font-size:.78rem;margin:6px 0}
.v9-ovg th{font-weight:600;color:#51656a;font-size:.7rem;padding:2px 1px;text-align:center;white-space:nowrap}
.v9-ovg th.v9-ovg-sec{text-transform:uppercase;letter-spacing:.05em;color:#132327;font-size:.68rem;border-bottom:2px solid rgba(19,35,39,.25);padding:4px 6px}
.v9-ovg th.v9-ovg-ep{border-bottom:1px solid rgba(19,35,39,.12);font-weight:500;padding:2px 4px}
.v9-ovg th.v9-ovg-l{cursor:help}
.v9-ovg td{padding:2px 1px;text-align:center;vertical-align:middle}
.v9-ovg .v9-ovg-an{position:sticky;left:0;z-index:1;background:#fbfcfc;text-align:left;padding:2px 10px 2px 8px;font:500 .76rem ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap}
.v9-ovg .v9-ovg-s1{padding-left:12px;border-left:2px solid rgba(19,35,39,.18)}
.v9-ovg .v9-ovg-s2{padding-left:7px}
.v9-ovg-tile{display:inline-block;width:17px;height:17px;border-radius:3px;vertical-align:middle;cursor:default;font:500 .6rem/17px ui-monospace,SFMono-Regular,Menlo,monospace;text-align:center;overflow:hidden}
.v9-ovg-tile i{font-style:normal;display:none}
.v9-ovg-nums .v9-ovg-tile{width:34px}
.v9-ovg-nums .v9-ovg-tile i{display:inline}
.v9-ovg-na{background:repeating-linear-gradient(135deg,rgba(19,35,39,.16) 0 2px,transparent 2px 5px);border:1px solid rgba(19,35,39,.2)}
.v9-ovg-absent{background:transparent;border:1px dashed rgba(19,35,39,.22)}
.v9-ovg-differs{background:#eef1f0;border:1px solid rgba(19,35,39,.35);color:#132327;font:600 .7rem/15px ui-monospace,SFMono-Regular,Menlo,monospace}
.v9-ovg-stale{outline:2px dashed #8a5a00;outline-offset:-2px}
.v9-ovg-flag{display:inline-block;width:17px;height:17px;line-height:15px;border-radius:50%;font-size:.7rem;font-weight:700;vertical-align:middle;cursor:default;text-align:center}
.v9-ovg-yes{border:1.5px solid #0d6e6e;color:#0d6e6e}
.v9-ovg-no{background:#9b2335;border:1.5px solid #9b2335;color:#fff}
.v9-ovg-flag.v9-ovg-na{border-radius:50%}
.v9-ovg-pips{display:inline-flex;gap:2px;vertical-align:middle;cursor:default;padding:2px 1px}
.v9-ovg-pip{width:5px;height:13px;border-radius:2px;border:1px solid #0d6e6e;display:inline-block}
.v9-ovg-pip.on{background:#0d6e6e}
.v9-ovg-btn{font:500 .76rem inherit;border:1px solid rgba(19,35,39,.25);border-radius:8px;background:#fff;color:#132327;padding:3px 9px;cursor:pointer}
.v9-ovg-btn[aria-pressed=true]{background:#0d6e6e;color:#fff;border-color:#0d6e6e}
.v9-ovg-tile:focus-visible,.v9-ovg-flag:focus-visible,.v9-ovg-pips:focus-visible,.v9-ovg-btn:focus-visible{outline:2px solid #0d6e6e;outline-offset:2px}
.v9-ovg-tip{position:fixed;z-index:9999;pointer-events:none;background:#132327;color:#f1f6f4;border-radius:8px;padding:8px 10px;font-size:.78rem;line-height:1.4;max-width:300px;box-shadow:0 6px 20px rgba(0,0,0,.25);text-align:left}
.v9-ovg-tip .v9-ovg-tv{font:500 .98rem ui-monospace,SFMono-Regular,Menlo,monospace}
.v9-ovg-tip .v9-ovg-tdef{opacity:.75;margin-top:4px;font-size:.72rem}
.v9-ovg-keys{font-size:.74rem;color:#51656a;margin:6px 0 0;line-height:1.6}
.v9-ovg-goto{color:#0d6e6e;text-decoration:none;border-bottom:1px dotted #0d6e6e;cursor:pointer}
.v9-ovg-goto:hover{border-bottom-style:solid}
.v9-ovg-goto:focus-visible{outline:2px solid #0d6e6e;outline-offset:2px}
.v9-ovg-keys b{color:#132327}
</style>"""
_OV_JS = """<script>
(function(){if(window.__v9ovg)return;window.__v9ovg=1;
var tip=document.createElement('div');tip.className='v9-ovg-tip';tip.style.display='none';document.body.appendChild(tip);
function show(el,x,y){tip.innerHTML=el.getAttribute('data-tip');tip.style.display='block';var r=tip.getBoundingClientRect(),p=12,l=x+14,t=y+14;
if(l+r.width>innerWidth-p)l=Math.max(p,x-r.width-14);if(t+r.height>innerHeight-p)t=Math.max(p,y-r.height-14);tip.style.left=l+'px';tip.style.top=t+'px';}
function hide(){tip.style.display='none';}
document.addEventListener('pointermove',function(e){if(e.pointerType!=='mouse')return;var el=e.target.closest&&e.target.closest('.v9-ovg [data-tip]');if(el)show(el,e.clientX,e.clientY);else hide();});
document.addEventListener('click',function(e){var g=e.target.closest&&e.target.closest('.v9-ovg-goto');if(g){e.preventDefault();hide();
var p=g.closest('.tab-panel'),sfx=(p&&p.id&&p.id.indexOf('tab-overview-')===0)?p.id.slice(13):'main';
var t=document.querySelector('[data-tab="'+g.getAttribute('data-goto')+'-'+sfx+'"]');if(t){t.click();t.scrollIntoView({block:'start'});}
var op=g.getAttribute('data-open');if(op)setTimeout(function(){var d=[].slice.call(document.querySelectorAll('details.'+op)).filter(function(x){return x.offsetParent!==null;})[0];if(d){d.open=true;d.scrollIntoView({block:'start'});}},120);return;}
var b=e.target.closest&&e.target.closest('.v9-ovg-btn');if(b){var w=b.closest('.v9-ovg');w.classList.toggle('v9-ovg-nums');b.setAttribute('aria-pressed',w.classList.contains('v9-ovg-nums')?'true':'false');return;}
var el=e.target.closest&&e.target.closest('.v9-ovg [data-tip]');if(el){var r=el.getBoundingClientRect();show(el,r.right,r.bottom);}else hide();});
document.addEventListener('focusin',function(e){var el=e.target.closest&&e.target.closest('.v9-ovg [data-tip]');if(el){var r=el.getBoundingClientRect();show(el,r.right,r.bottom);}});
document.addEventListener('focusout',hide);addEventListener('scroll',hide,{passive:true});})();
</script>"""


def _overview_description(params: dict | None) -> str:
    d = _ov_definitions(params)
    struct = "".join(f"<li><b>{l}</b> {_e(n)}: {_e(d[k])}</li>" for k, l, n in _OV_STRUCT)
    anal = "".join(f"<li><b>{l}</b> {_e(n)}: {_e(d[k])}</li>" for k, l, n in _OV_ANALYTICAL) + f"<li><b>Un</b> Units declared: {_e(d['units'])}</li>"
    annot = "".join(f"<li><b>{l}</b> {_e(n)}: {_e(d[k])}</li>" for k, l, n in _OV_ANNOTATION)
    labs = "".join(f"<li><b>{l}</b> {_e(n)}: {_e(d[k])}</li>" for k, l, n in _OV_LABELS)
    mlt = "".join(f"<li><b>{l}</b> {_e(n)}: {_e(d[k])}</li>" for k, l, n in _OV_ML)
    fair = "".join(f"<li><b>{l}</b> {_e(label)}: {_e(d[k])}</li>" for (k, l), label in zip(_OV_FAIR, ("RefMet mapping", "External IDs", "Mass/RT metadata", "LC-method context")))
    return _descriptions(
        "<p style='margin:8px 0'>Each tile is one scored item of one matrix (an analysis read from one endpoint), or of one analysis for Label "
        "structure and Metadata &amp; FAIR, with the value its tab shows for the parameters and sample edits in force. Values run 0–100, higher = "
        "better; colour runs from dark red (0) to pale (100), with no thresholds. Items that are only 0 or 100 show ✓ / ✕, and LC-method context "
        "three pips (column, mobile phases, gradient). Nothing is combined: no section, mean or overall score.</p>"
        f"<p style='margin:0 0 4px'><b>Structural</b> (per matrix)</p><ul style='margin:0 0 8px 18px;padding:0'>{struct}</ul>"
        f"<p style='margin:0 0 4px'><b>Analytical QC</b> (per matrix)</p><ul style='margin:0 0 8px 18px;padding:0'>{anal}</ul>"
        f"<p style='margin:0 0 4px'><b>Annotation</b> (per matrix; feature labels only, so sample edits do not change it)</p><ul style='margin:0 0 8px 18px;padding:0'>{annot}</ul>"
        f"<p style='margin:0 0 4px'><b>ML task readiness</b> (per matrix; classes as chosen in Label Structure)</p><ul style='margin:0 0 8px 18px;padding:0'>{mlt}</ul>"
        f"<p style='margin:0 0 4px'><b>Label structure</b> (per analysis, classes as chosen in its tab; ≠ = its endpoints give different values, "
        f"listed on hover)</p>"
        f"<ul style='margin:0 0 8px 18px;padding:0'>{labs}</ul>"
        f"<p style='margin:0 0 4px'><b>Metadata &amp; FAIR</b> (per analysis; the same for every endpoint)</p><ul style='margin:0 0 8px 18px;padding:0'>{fair}</ul>"
        "<p style='margin:0'>Hatched: not scored (hover for the reason). Dashed outline: no valid matrix on that endpoint. Informational blocks "
        "have no tile; they are in their tabs.</p>")


def _rd_cut_panel(params: dict | None) -> str:
    """The alert cut-offs, editable beside the chips (user, 2026-10-31): per check, what a score means in one plain sentence, and the
    two fields (empty = the reference percentile, shown in grey). Apply copies them into the Show tools fields and recalculates; Use
    defaults clears them. The 'What the score means' header opens the Analytical QC descriptions."""
    rho, kf = _rd_cut_settings(params)
    ext, low = rd._reference_cutoffs(params, "extreme"), rd._reference_cutoffs(params, "low")
    tools = "<a href='#' class='v9-ovg-goto v9-open-tools' data-group='Analytical QC'>Show tools</a>"
    meaning = {"missingness": _e("A score of 30 means at least 70% of the cells are empty or zero."),
               "low_information": _e("A score of 70 means 70% of the features vary across the samples; the other 30% are constant or nearly constant."),
               "redundancy": _e(f"A score of 20 means 80% of the evaluated features are redundant (Spearman |ρ| > {rho:.2f}).")
                             + f" The {rho:.2f} cut-off can be changed in {tools}.",
               "outliers": _e(f"A score of 80 means 20% of the sample positions with a numeric median are outliers: their median value is beyond "
                              f"{kf:g} × IQR from the others' (a position is one sample row or column of the file; a sample ID deposited twice has "
                              "two).") + f" The {kf:g} × IQR fence can be changed in {tools}."}
    th = "padding:3px 6px;text-align:left;font-size:.74rem;font-weight:600;color:#3f565b;border-bottom:1px solid rgba(19,35,39,.15)"
    rows = ""
    for key, name in rd.ALERT_METRICS:
        cells = ""
        for tier, ref in (("extreme", ext), ("low", low)):
            n = f"readiness_cut_{key}_{tier}"; val = str((params or {}).get(n, "") or "")
            cells += (f"<td style='padding:3px 6px'><input class='v9-rd-cut' type='number' min='0' max='100' step='any' data-name='{n}' "
                      f"value='{_e(val)}' placeholder='{ref[key]:.1f}' aria-label='{_e(name)}: a value below this gets analytical alert = {tier}' "
                      "style='width:4.8em;font:inherit;font-size:.8rem;padding:2px 4px'></td>")
        rows += (f"<tr><td style='padding:3px 6px;font-size:.8rem;white-space:nowrap'><b>{_e(name)}</b></td>"
                 f"<td style='padding:3px 6px;font-size:.76rem;color:#51656a'>{meaning[key]}</td>{cells}</tr>")
    btn = "font:inherit;font-size:.8rem;padding:3px 12px"
    ext_p, low_p = rd.tiers(params)
    refs = {f"{float(q):g}": v.get("cutoffs") for q, v in (rd.reference().get("sensitivity") or {}).items()}
    opt = lambda cur: "".join(f"<option value='{q:g}'{' selected' if q == cur else ''}>{q:g}</option>" for q in rd.PERCENTILES)
    sel = "font:inherit;font-size:.8rem;font-weight:700;padding:0 4px;width:auto;min-width:0;height:auto;display:inline-block"
    pcts = ("<div style='font-size:.8rem;margin:2px 0 6px'><b>Reference cut-offs:</b> extreme below the "
            f"<select class='v9-rd-pct' data-name='readiness_extreme_pct' aria-label='Extreme alert percentile' style='{sel}'>{opt(ext_p)}</select>th "
            f"percentile, low below the <select class='v9-rd-pct' data-name='readiness_low_pct' aria-label='Low alert percentile' style='{sel}'>{opt(low_p)}</select>th "
            "percentile of the reference cohort (R1). The grey values in the table follow.</div>"
            "<div style='font-size:.8rem;margin:0 0 6px'>If you want custom cut-offs, you may enter them below:</div>")
    return (f"<div class='v9-rd-cuts' hidden data-refs='{_e(json.dumps(refs))}' style='margin:-4px 0 12px;padding:8px 10px;border:1px solid rgba(19,35,39,.12);border-radius:10px'>"
            "<div style='font-size:.8rem;margin-bottom:4px'><b>Alert cut-offs.</b> Scores run from 0 to 100; higher means fewer problems. "
            "Leave a field empty to use the reference value (in grey). Values you enter replace the reference for that check and are "
            "recorded as yours on this page, in the Analytical QC tab and in the downloaded JSON; low is never stricter than extreme.</div>"
            + pcts + f"<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%'><tr><th style='{th}'>Check</th>"
            f"<th style='{th}'><a href='#' class='v9-ovg-goto' data-goto='analytical' data-open='v9-desc-analytical' "
            "title='Open the Analytical QC descriptions'>What the score means</a></th>"
            f"<th style='{th}'>A value below this gets analytical alert = extreme</th><th style='{th}'>A value below this gets analytical alert = low</th></tr>"
            f"{rows}</table></div>"
            f"<div style='margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap'><button type='button' class='v9-rd-cut-apply' style='{btn}'>"
            f"Apply</button><button type='button' class='v9-rd-cut-defaults' style='{btn}'>Use defaults</button>"
            "<span style='font-size:.74rem;color:#51656a'>The same fields as in Show tools; every matrix of the study is recalculated.</span>"
            "</div></div>")

def _rd_custom_text(params: dict | None, lead: str = "; custom score cut-offs: ") -> str:
    """The custom score cut-offs in force, named per check ('' when none): they replace the reference percentile for those checks only."""
    cus = rd.custom(params); bad = rd.custom_ignored(params)
    names = dict(rd.ALERT_METRICS); order = [x for x, _ in rd.ALERT_METRICS]
    out = (lead + "; ".join(f"{names[k].lower()} {t} below {_lb_num(v)}" for (k, t), v in
                            sorted(cus.items(), key=lambda kv: (order.index(kv[0][0]), kv[0][1] != "extreme")))) if cus else ""
    if bad:
        out += ("; ignored (not a number from 0 to 100, so the reference is used): "
                + "; ".join(f"{names[k].lower()} {t} “{raw}”" for k, t, raw in bad))
    return out


def _readiness_description(params: dict | None) -> str:
    ref = rd.reference(); cut = rd.cutoffs(params, "extreme"); cut_low = rd.cutoffs(params, "low"); k = lb.folds(params)
    sens = ref.get("sensitivity") or {}; ext_p, low_p = rd.tiers(params)
    refname = (f"reference {ref['reference_version']}: the {ref['reference_matrices']:,} valid matrices of the 9 March 2026 pull, at the default settings"
               if ref.get("reference_version") and ref.get("reference_matrices") else "all valid matrices assessed here, at the default settings")
    c = lambda key: (f"{cut[key]:.1f}" + (f" (low: below {cut_low[key]:.1f})" if key in cut_low and low_p > ext_p else "")) if key in cut else "–"
    sline = "; ".join(f"at the {p}th percentile {v['matrices_with_an_alert']:,} of {v['matrices']:,} matrices have an alert"
                      for p, v in sens.items()) if sens else ""
    s_low = sens.get(f"{low_p:g}") or {}          # any alert, either tier: the low percentile in force
    share = (f" At the percentiles in force, {s_low['matrices_with_an_alert']:,} of the {s_low['matrices']:,} reference matrices "
             f"({100 * s_low['matrices_with_an_alert'] / s_low['matrices']:.1f}%) receive at least one alert across the four checks."
             if s_low.get("matrices") else "")
    return _descriptions(
        "<p style='margin:8px 0'><b>What this is.</b> MERIT-ML reports matrix properties, class-count support under the selected labels, and "
        "cohort-relative analytical alerts. It does not assign a universal ML-readiness score or certify predictive performance. "
        "Three parts per matrix (an analysis read from one data file), never averaged: "
        "<b>cross-validation sample-count support</b> (under the classes in force: the deposited "
        "labels unless you choose classes in Label Structure), <b>analytical alerts</b> (independent of the labels) and <b>selected annotation and metadata indicators</b>. There is no overall score, no average and "
        "no weight, and no matrix is called ready: a typical value elsewhere never offsets an alert. The summary shows the "
        "sample-count result and the alert result side by side, neither ranked above the other. Nothing is combined across matrices, analyses or the study.</p>"
        f"<p style='margin:8px 0'><b>Analytical alerts</b>, against a fixed reference, in two tiers. A valid matrix is required (otherwise: no data). An "
        f"<b>extreme</b> alert is raised when a value is below the {ext_p:g}th percentile of the reference cohort ({_e(refname)}), that is, lower than {100 - ext_p:g}% of them; a <b>low</b> alert when it is below the {low_p:g}th "
        f"percentile but not the {ext_p:g}th. Cut-offs: missing entries and zeros below {c('missingness')}, low-information features below "
        f"{c('low_information')}, redundant features below {c('redundancy')}, outlier samples below {c('outliers')}. Both percentiles are set "
        "in the parameter panel (2.5, 5, 7.5 or 10; defaults 2.5 and 10), where a score cut-off of your own can also be given per check and tier: it "
        "replaces the reference percentile for that check only, is named as custom wherever an alert uses it, and is not a reference (an empty "
        "field keeps the reference)" + _e(_rd_custom_text(params, "; in force: ")) + ". "
        "“Below” is strict, so matrices tied at a cut-off are not alerted. The cut-offs are fixed and versioned: adding studies does not change them, and a "
        "new reference gets a new version name. An alert means unusually low relative to the reference cohort and prompts a review; it does not mean the data cannot "
        "be used (an outlier sample can reflect biology). "
        f"The {ext_p:g}th and {low_p:g}th percentiles are disclosed screening conventions against a fixed "
        "reference, not validated failure thresholds or false-positive rates." + share + " A check that cannot be computed is listed as not assessed and never counts as passed: "
        "the result then reads “no alerts among N/4 assessed checks”, or “analytical checks not assessable” when none of the four could be computed; if the "
        "reference cut-offs are missing, it reads “reference unavailable” instead of judging. With changed settings or "
        "sample edits the values change, the cut-offs do not. Units declared is shown as a documentation note, not an alert: its absence does "
        f"not prevent ML within the study, but matters for scale-dependent use. Sensitivity: {sline}.</p>"
        f"<p style='margin:8px 0'><b>Cross-validation sample-count support</b> (under the classes in force). <b>Count requirement met</b>: at least two classes and the smallest has "
        f"at least {k} samples (stratified {k}-fold cross-validation needs a sample of every class in every fold; the number of folds is set "
        "in Show tools under Cross-validation folds). <b>Count requirement not met</b>: one class, or the smallest class is below that. <b>Cannot assess class counts</b>: the classes cannot be "
        "assessed (no ML-eligible or labelled sample, or a chosen class has no sample in this matrix). The values beside it (classes, samples, features, p/n, class support, and class "
        "separability with its chance level) describe the question asked, not the quality of the data: choosing other classes recomputes them. "
        "<b>Count requirement met</b> establishes only this count condition for the chosen number of folds: it does not establish enough samples for a precise "
        "or powered estimate, independent subjects, or successful prediction. "
        "This counts sample IDs only: it does not check that folds can keep each subject's samples together (subject-disjoint splitting), "
        "and when sample IDs share a deposited subject ID the cell says so (verify the subjects first); such data need grouped cross-validation, in which all samples of a subject "
        "stay in one fold (for example scikit-learn's GroupKFold). No modelling workflow is assumed, so missing-value handling is not judged "
        "here.</p>"
        "<p style='margin:8px 0'><b>Selected annotation and metadata indicators</b> (values only, not a score): named-feature coverage and "
        "named features mapped to RefMet for each data file, and the analysis's external IDs, mass/RT field and LC-method context, each "
        "percentage on a 0–100 bar. It is a selection; the full profile is in Metadata and FAIR Reusability.</p>"
        "<p style='margin:8px 0'><b>Summary</b> (one line per matrix: both results as they are, never ranked, merged or averaged). First the "
        "count result (<b>Count requirement met</b>, <b>Count requirement not met</b> or <b>Cannot assess class counts</b>), then the alerts "
        "and how many of the four checks could be assessed, e.g. “Count requirement met · 1 extreme + 2 low alerts · 4/4 checks assessed” or "
        "“Count requirement not met · no alerts among 3/4 assessed checks”. Any extreme alert makes the alert result extreme, otherwise any "
        "low alert makes it low; the numbers show how many checks raised each. The alerts are relative to the reference: a matrix without an "
        "alert can still have substantial missingness or many low-information features (the values are in Analytical QC). Count requirement "
        "met with no alert says only that these checks raised nothing and the classes have enough samples for the folds; it does not say that "
        "the data have no problem or that a model will perform well.</p>"
        "<p style='margin:8px 0'><b>Data files.</b> The data files of an analysis (datatable, mwTab, untarg_data) are alternative files of the "
        "same analysis. When their summaries agree, one row is shown with “same summary”; values that differ are still listed per file. "
        "Otherwise each data file has its own row, and none is preferred.</p>"
        "<p style='margin:8px 0'><b>Why no single score.</b> In an average or a weighted sum, a deficit in one item is offset by a surplus in "
        "another (OECD/JRC Handbook on Constructing Composite Indicators, 2008); the alerts here are kept apart instead. By analogy only, the "
        "overall judgement of RoB 2 (Cochrane Handbook v6.5, chapter 8) is not rescued by strong domains, and several concerns can together "
        "raise it. Separating the data from the task follows Lawrence's data readiness levels (2017), where suitability for a task can only "
        "be judged once the task is defined. These sources support the structure; the cut-offs are this resource's own choice.</p>")


_NON_LC_DECLARED = {"gc", "gcxgc", "ce", "none", "none (direct infusion)", "fia", "flow injection analysis", "flow induction analysis", "gas phase"}


def _lc_declared(study_id: str, analysis_id: str) -> str:
    """Why LC-method context is not assessed for an analysis, from its declared chromatography type (wording signed off on 2026-10-29:
    'not applicable' only for an explicit non-LC declaration; an empty field is 'LC not explicitly identified')."""
    lc = ((_fair_evidence(study_id).get(analysis_id) or {}).get("lc") or {})
    ct = str(lc.get("chromatography_type") or "").strip()
    rec = next((r for r in ((acquisition_context(study_id) or {}).get("records") or []) if r.get("analysis_id") == analysis_id), {})
    if ct.lower() in ("", "-", "unspecified"):
        return "not applicable: NMR analysis" if rec.get("modality") == "NMR" else "LC not explicitly identified"
    if ct.lower() in _NON_LC_DECLARED:
        return f"not applicable: “{ct}” declared"
    return f"not assessed: chromatography type declared “{ct}”"


def _rd_by_ep(eps: list, fn) -> str:
    """One value when every endpoint of the row gives it, else 'datatable v, mwTab v'."""
    vals = [(lab, fn(ep)) for lab, ep in eps]
    return vals[0][1] if len({v for _, v in vals}) == 1 else ", ".join(f"{lab} {v}" for lab, v in vals)


def _rd_info(text: str) -> str:
    """An (i) that opens on hover, keyboard focus, click or tap (the page's .minfo popup)."""
    return ("<span class='minfo' tabindex='0' role='button' aria-label='" + _e(text, quote=True) + "'><span class='minfo-icon' aria-hidden='true'>i</span>"
            "<span class='minfo-popup' style='text-transform:none;letter-spacing:0;font-weight:400'>" + _e(text) + "</span></span>")


_RD_AMBER = "#8a5a00"; _RD_GREY = "#6f7e83"; _RD_SMALL = "font-size:.76rem;color:#51656a"
_RD_COL = {"met": "#0d6e6e", "not met": _RED, "unresolved": _RD_GREY}


def _rd_alert_head(dr: dict) -> str:
    n = len(rd.ALERT_METRICS)
    if dr["result"] == "reference unavailable":
        return f"<b style='color:{_RD_AMBER}'>reference unavailable</b>"
    if dr["result"] == "not assessable":
        return f"<b style='color:{_RD_GREY}'>analytical checks not assessable</b>"
    if dr["result"] == "no alerts (partial)":
        return f"<b style='color:{_RD_AMBER}'>no alerts among {dr['assessed']}/{n} assessed checks</b>"
    if dr["result"] == "no alerts":
        return "<b style='color:#0d6e6e'>no alerts</b>"
    n_ext = sum(a["tier"] == "extreme" for a in dr["alerts"]); n_low = len(dr["alerts"]) - n_ext
    word = lambda k: "alert" if k == 1 else "alerts"
    txt = (f"{n_ext} extreme + {n_low} low {word(n_ext + n_low)}" if n_ext and n_low else f"{n_ext} extreme {word(n_ext)}" if n_ext
           else f"{n_low} low {word(n_low)}")
    return f"<b style='color:{_RED if n_ext else _RD_AMBER}'>{txt}</b>"


def _rd_subjects(eps: list) -> str:
    """The subject-ID note of the row: deposited (or edited) subject IDs linking several sample IDs; after the user's confirmation, keep
    each subject's samples together."""
    pr = [ep["profile"] for _, ep in eps]
    if not any(p["subjects_with_several_samples"] for p in pr):
        return ""
    n = _rd_by_ep(eps, lambda ep: f"{ep['profile']['subjects_with_several_samples']:,}")
    one = all(p["subjects_with_several_samples"] == 1 for p in pr)
    if all(p["subjects_confirmed"] for p in pr):
        text = (f"{n} confirmed subject{'' if one else 's'} {'has' if one else 'have'} several samples: keep each subject's samples together "
                "in one fold (grouped cross-validation).")
    else:
        text = (f"{n} deposited subject ID{'' if one else 's'} link{'s' if one else ''} several samples; verify subjects before choosing a split.")
        top = pr[0].get("most_shared_subject") or {}
        if one and top.get("subject") and all((p.get("most_shared_subject") or {}).get("subject") == top["subject"] for p in pr):
            text = (f"1 deposited subject ID (“{top['subject']}”) links {top.get('sample_ids', 0):,} samples; verify subjects before choosing a "
                    "split. If there is truly one subject, no split creates independent subjects.")
    return (f"<div class='v9-rd-subj' style='margin-top:4px;font-size:.74rem;font-weight:600;color:{_RD_AMBER};background:rgba(246,199,68,.14);"
            f"border:1px solid rgba(246,199,68,.45);border-radius:8px;padding:2px 8px'>{_e(text)}</div>")


def _rd_summary_cell(eps: list) -> str:
    r = eps[0][1]["readiness"]; summ = rd.summary(r["data"], r["task"]); parts = summ.split(" · ")
    out = f"<b style='color:{_RD_COL[r['task']['result']]}'>{_e(parts[0])}</b> · " + _rd_alert_head(r["data"])
    extra = []
    if len(parts) > 2:
        extra.append(f"<span style='{_RD_SMALL}'>{_e(parts[2])}</span>")
    if any(ep["profile"]["subjects_with_several_samples"] for _, ep in eps):
        extra.append(f"<b style='color:{_RD_AMBER}'>subject IDs to verify</b>" if not all(ep["profile"]["subjects_confirmed"] for _, ep in eps)
                     else f"<b style='color:{_RD_AMBER}'>grouped split needed</b>")
    if extra:
        out += "<br>" + " · ".join(extra)
    if any(ep["profile"]["malformed_matrix"] for _, ep in eps):
        which = ", ".join(lab for lab, ep in eps if ep["profile"]["malformed_matrix"])
        out += (f"<div style='margin-top:5px'><a href='#' class='v9-ovg-goto v9-rd-struct' data-goto='structural' style='display:inline-block;"
                f"font-size:.74rem;font-weight:700;color:{_RED};border:1px solid rgba(192,57,43,.35);border-radius:8px;padding:2px 8px;"
                f"background:rgba(192,57,43,.05);text-decoration:none'>Structural warning: malformed {_e(which)}; inspect source ›</a></div>")
    return out


def _rd_count_values(ep: dict) -> list[tuple[str, str, str]]:
    """The count evidence of one data file: (key, label, shown value)."""
    t = ep["readiness"]["task"]; pr = ep["profile"]
    pn = pr["features_per_sample"]
    out = [("classes", "Classes", f"{t['n_classes']:,}"),
           ("smallest", "Smallest class", "–" if t["smallest"] is None or t["n_classes"] < 1 else f"{t['smallest']:,}"),
           ("samples", "Samples", f"{pr['labelled_sample_ids']:,}"),
           ("features", "Features", f"{pr['features'] or 0:,}"),
           ("pn", "p/n", "not assessable" if pn is None else (f"{pn:.2f}" if pn < 10 else f"{pn:.1f}"))]
    if t.get("class_support") is not None:
        out.append(("class_support", "Class support", _floor1(t["class_support"])))
    if t["result"] != "unresolved":
        ch = t["chance"]
        out.append(("separability", "Separability (chance)", "not assessable" if t["separability"] is None else
                    f"{_floor1(t['separability'])} ({'<0.1' if ch is not None and 0 < ch < 0.1 else _floor1(ch)})"))
    return out


def _rd_count_cell(eps: list, k: int) -> str:
    """Count requirement of the row; its evidence on two lines for one data file (or files that agree on every value), else as a small
    table with one column per data file, so no value of one file is shown for another (fixed 2026-10-29: shared rows showed the first
    file's class count and smallest class)."""
    t = eps[0][1]["readiness"]["task"]
    flag = f"<b style='color:{_RD_COL[t['result']]}'>{_e(rd.COUNTS_PART[t['result']])}</b>"
    head = ""
    no_eligible = t["result"] == "unresolved" and not any(ep["profile"]["eligible_sample_ids"] for _, ep in eps)
    vals = [(lab, _rd_count_values(ep)) for lab, ep in eps]
    same = len({tuple(v for _, _, v in vs) for _, vs in vals}) == 1
    go_set = (" <a href='#' class='v9-ovg-goto v9-rd-goto-settings' title='Open the class, fold and alert settings of the readiness "
              "profile'>Adjust here</a>")
    if t["result"] == "not met" and all(ep["readiness"]["task"]["n_classes"] < 2 for _, ep in eps):
        head += f"Only one class; cross-validation needs at least two.{go_set}"
    elif t["result"] in ("met", "not met"):
        n = t["smallest"]
        head += ((f"The smallest class has {n:,} ML-eligible sample{'s' if n != 1 else ''}; " if same else "")
                 + f"{k}-fold cross-validation needs at least {k}{' in the smallest class' if not same else ''}.{go_set}")
    elif no_eligible:
        flag += " · no ML-eligible samples remain"
    else:
        flag += f" · {_e(t['reason'] or 'the classes cannot be assessed')}"
    if no_eligible:
        controls = _rd_by_ep(eps, lambda ep: f"{ep['profile']['qc_blank_positions'] or 0:,}")
        measured = _rd_by_ep(eps, lambda ep: f"{ep['profile']['measured_positions'] or 0:,}")
        allc = all((ep["profile"]["qc_blank_positions"] or 0) == (ep["profile"]["measured_positions"] or 0) for _, ep in eps)
        feats = _rd_by_ep(eps, lambda ep: f"{ep['profile']['features'] or 0:,}")
        body = (f"<div style='{_RD_SMALL}'>" + (f"the {measured} measured samples are all QC-, blank- or standard-like" if allc
                else f"measured samples: {measured}, of which QC-, blank- or standard-like: {controls}") + f" · features: {feats} · p/n not assessable</div>")
    elif same:
        v = {key: val for key, _, val in vals[0][1]}
        one = "1 class" if v["classes"] == "1" else f"{v['classes']} classes"
        line1 = [one, ("1 sample" if v["samples"] == "1" else f"{v['samples']} samples"), f"{v['features']} features", f"p/n {v['pn']}"]
        line2 = ([f"class support {v['class_support']}"] if "class_support" in v else []) + ([f"separability {v['separability'].replace(' (', ' (chance ')}"] if "separability" in v else [])
        body = f"<div style='{_RD_SMALL}'>{' · '.join(line1)}</div>" + (f"<div style='{_RD_SMALL}'>{' · '.join(line2)}</div>" if line2 else "")
    else:
        th = "padding:1px 6px;text-align:right;font-weight:600;color:#3f565b;border-bottom:1px solid rgba(19,35,39,.15);white-space:nowrap"
        td = "padding:1px 6px;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap"
        rows_ = ""
        for i, (key, label, _) in enumerate(vals[0][1]):
            cells = [dict((kk, vv) for kk, _, vv in vs).get(key, "–") for _, vs in vals]
            diff = len(set(cells)) > 1
            rows_ += (f"<tr><td style='padding:1px 6px 1px 0;color:#51656a;white-space:nowrap'>{_e(label)}</td>"
                      + "".join(f"<td class='v9-rd-epv' data-ep='{_e(lab)}' data-v='{key}' style='{td}{';font-weight:700;color:#132327' if diff else ''}'>{_e(c)}</td>"
                                for (lab, _), c in zip(vals, cells)) + "</tr>")
        body = ("<table class='v9-rd-eptab' style='border-collapse:collapse;margin-top:3px;font-size:.74rem;width:auto;min-width:0'><tr>"
                f"<th style='{th};text-align:left'></th>" + "".join(f"<th style='{th}'>{_e(lab)}</th>" for lab, _ in vals) + "</tr>" + rows_ + "</table>"
                f"<div style='{_RD_SMALL};font-size:.7rem'>values that differ between data files in bold</div>")
    align = [(lab, ep["profile"]["tier1_alignment_rows"]) for lab, ep in eps if ep["profile"]["tier1_alignment_rows"]]
    notes = "".join(f"<div style='{_RD_SMALL}'>{n:,} additional {_e(lab)} row{'s' if n != 1 else ''} "
                    f"{'meet' if n != 1 else 'meets'} the Tier 1 alignment rule and {'are' if n != 1 else 'is'} excluded.</div>" for lab, n in align)
    of = [(lab, ep["profile"].get("other_factors")) for lab, ep in eps]
    other = (_lb_other_html(of[0][1], compact=True) if len({json.dumps(o, sort_keys=True) for _, o in of}) == 1 else
             "".join(_lb_other_html(o, compact=True).replace("Other factors</span>", f"Other factors ({_e(lab)})</span>", 1) for lab, o in of))
    detail = (f"<div>{head}</div>" if head else "") + body + other + notes
    return flag + (_rd_more(detail) if detail else "") + _rd_subjects(eps)


def _rd_cut_settings(params: dict | None) -> tuple[float, float]:
    """(redundancy |rho| cut-off, outlier fence k) in force, for the plain-words meaning of the scores."""
    p = params or {}
    return float(p.get("redundancy_cutoff", cr.DEFAULT_PARAMS["redundancy_cutoff"])), float(p.get("outlier_iqr_k", ov.DEFAULT_PARAMS["outlier_iqr_k"]))


def _rd_check_sentences(eps: list, params: dict | None) -> list[str]:
    """One plain sentence per assessed check, as the share with the problem (100 - score) (user, 2026-11-01); an alert is named in
    brackets after it ('your cut-off' when a cut-off of the user's raised it); alerts first, extreme before low."""
    dr = eps[0][1]["readiness"]["data"]; rho, kf = _rd_cut_settings(params)
    alerts = {a["key"]: a for a in dr["alerts"]}
    set_by = {x for _, ep in eps for x in (ep["profile"].get("missingness_set_by") or [])}
    kind = ("empty or zero" if "invalid" in set_by and set_by - {"invalid"} else "empty or non-numeric" if set_by == {"invalid"}
            else "zeros" if set_by else "empty or zero")
    say = {"missingness": (lambda v: f"At least {v} of the cells are {kind}", "No empty or zero cells"),
           "low_information": (lambda v: f"{v} of the features are constant or nearly constant", "Every feature varies across samples"),
           "redundancy": (lambda v: f"{v} of the evaluated features are redundant (Spearman |ρ| > {rho:.2f})", f"No pair of features has |ρ| > {rho:.2f}"),
           "outliers": (lambda v: f"{v} of the sample positions with a numeric median are outliers (beyond {kf:g} × IQR)", f"No outlier sample positions (beyond {kf:g} × IQR)")}
    order = sorted(rd.ALERT_METRICS, key=lambda m: 0 if alerts.get(m[0], {}).get("tier") == "extreme" else 1 if m[0] in alerts else 2)
    out = []
    for key, _ in order:
        vals = [ep["readiness"]["data"]["values"].get(key) for _, ep in eps]
        if all(x is None for x in vals):
            continue
        if all(x is not None and x >= 100 for x in vals):
            text = _e(say[key][1])
        else:
            share = [(lab, "–" if ep["readiness"]["data"]["values"].get(key) is None else _share_pct(100.0 - float(ep['readiness']['data']['values'][key])))   # from the unrounded score, formatted as in Data at a glance
                     for lab, ep in eps]
            v = share[0][1] if len({x for _, x in share}) == 1 else ", ".join(f"{x} ({lab})" for lab, x in share)
            text = _e(say[key][0](v))
        a = alerts.get(key)
        tag = (f" <b style='color:{_RED if a['tier'] == 'extreme' else _RD_AMBER}'>({a['tier']} alert{', your cut-off' if a.get('custom') else ''})</b>"
               if a else "")
        out.append(f"<div>{text}{tag}.</div>")
    return out


def _rd_units(eps: list) -> str:
    """The units of the values in words: the declared text, or what was found instead."""
    def one(ep):
        u = (ep.get("items") or {}).get("units") or {}
        if ep["readiness"]["data"]["units_declared"] is None:
            return "units not assessable"
        det = str(u.get("detail") or "")
        if u.get("flag"):
            return "units: " + det.replace("declared: ", "", 1)
        return (f"units of the values given as {det.split(' is a placeholder')[0]}, which is not a unit" if "placeholder" in det
                else "units of the values not given in the deposit")
    return _rd_by_ep(eps, one)

def _rd_alerts(eps: list, params: dict | None = None) -> str:
    """Alerts cell: one plain sentence per assessed check (the share with the problem; an alert named in brackets), then how many checks
    were assessed and the units in words (user, 2026-11-01)."""
    dr = eps[0][1]["readiness"]["data"]; n = len(rd.ALERT_METRICS)
    if dr["result"] in ("extreme alerts", "low alerts"):
        head = "".join(_rd_check_sentences(eps, params))
    elif dr["result"] == "not assessable" and not any(ep["profile"]["eligible_sample_ids"] for _, ep in eps):
        head = f"<b style='color:{_RD_GREY}'>Not assessable</b> (0/{n} checks assessed)<div style='{_RD_SMALL}'>No ML-eligible samples remain</div>"
    elif dr["result"] == "not assessable":
        head = f"<b style='color:{_RD_GREY}'>Not assessable</b> (0/{n} checks assessed)"
    elif dr["result"] in ("no alerts", "no alerts (partial)"):
        head = "".join(_rd_check_sentences(eps, params))
    else:
        head = _rd_alert_head(dr).replace("no alerts", "No alerts", 1)
    tail = []
    if dr["result"] not in ("not assessable", "reference unavailable", "no alerts (partial)"):
        tail.append(f"{dr['assessed']}/{n} checks assessed")
    if dr["not_assessed"] and dr["result"] != "not assessable":
        tail.append("not assessed: " + ", ".join(dr["not_assessed"]).lower())
    tail.append(_rd_units(eps))
    body = head + f"<div style='{_RD_SMALL};margin-top:2px'>{_e(' · '.join(tail))}</div>"
    if dr["result"] in ("extreme alerts", "low alerts", "no alerts", "no alerts (partial)"):
        return _rd_alert_flag(dr) + _rd_more(body)
    return body


def _rd_more(body: str) -> str:
    """The plain-language detail of a readiness cell, folded under 'Show more' (user, 2026-11-01)."""
    return (f"<details class='v9-rd-more'><summary style='cursor:pointer;font-size:.74rem;color:#0d6e6e;font-weight:600'>Show more</summary>"
            f"<div style='margin-top:3px'>{body}</div></details>")


def _rd_alert_flag(dr: dict) -> str:
    """The visible line of the alerts cell: the number and kind of alerts, or no alerts."""
    names = dict(rd.ALERT_METRICS)
    if not dr["alerts"]:
        return "<div><b style='color:#0d6e6e'>No alerts</b></div>"
    out = []
    for tier, colour in (("extreme", _RED), ("low", _RD_AMBER)):
        ks = [a["key"] for a in dr["alerts"] if a["tier"] == tier]
        if ks:
            out.append(f"<div><b style='color:{colour}'>{len(ks)} {tier} alert{'s' if len(ks) != 1 else ''}</b>: "
                       + _e(", ".join(names[k].lower() for k in ks)) + "</div>")
    return "".join(out)


def _rd_bar(label: str, values: list) -> str:
    """One 0-100 bar (or one per data file when they differ) with its value; 'not assessable' without a value."""
    def one(name, it, sub=False):
        lab = f"<span style='color:#51656a{';padding-left:12px;color:#7a8a8e' if sub else ''}'>{_e(name)}</span>"
        if it.get("value") is None:
            return f"<div class='v9-rd-bi'>{lab}<span class='v9-rd-tx' style='color:{_RD_GREY};font-weight:600'>not assessable</span></div>"
        v = max(0.0, min(100.0, float(it["value"])))
        return (f"<div class='v9-rd-bi'>{lab}<span class='v9-rd-bt'><span class='v9-rd-bf' style='width:{v:.1f}%'></span></span>"
                f"<span class='v9-rd-bv'>{_e(it.get('shown') or _floor1(it['value']))}</span></div>")
    if len(values) == 1 or len({(it.get("value"), it.get("shown")) for _, it in values}) == 1:
        return one(label, values[0][1])
    return f"<div class='v9-rd-bi'><span style='color:#51656a'>{_e(label)}</span><span></span><span></span></div>" + "".join(one(n, it, True) for n, it in values)


def _rd_reuse(eps: list, fair: dict) -> str:
    out = _rd_bar("Named-feature coverage (%)", [(lab, ep["items"].get("named_coverage") or {}) for lab, ep in eps])
    out += _rd_bar("Named features mapped to RefMet (%)", [(lab, ep["items"].get("refmet_mapping") or {}) for lab, ep in eps])
    # the next three describe the analysis, not this data file (user, 2026-11-01): marked as such; External IDs come from the analysis's
    # metabolite records, so a file without any named feature (untarg_data: m/z_RT labels) has nothing they could apply to
    out += ("<div class='v9-rd-bi v9-rd-per-an'><span style='font-size:.68rem;color:#7a8a8e;text-transform:uppercase;letter-spacing:.05em'>"
            "Per analysis</span></div>")
    named = [(ep["items"].get("named_coverage") or {}).get("value") for _, ep in eps]
    if all(v is None or v == 0 for v in named):
        out += ("<div class='v9-rd-bi'><span style='color:#51656a'>External IDs (%)</span><span class='v9-rd-tx' style='color:#51656a'>"
                "not applicable: no named features in this file</span></div>")
    else:
        out += _rd_bar("External IDs (%)", [("", fair.get("external_cross_references_via_refmet") or {})])
    mr = fair.get("mass_rt_like_metadata_presence") or {}
    mr_txt = ("<span class='v9-rd-tx' style='color:#51656a'>not assessable</span>" if mr.get("value") is None else
              ("<b class='v9-rd-tx' style='color:#0d6e6e'>✓ present</b>" if mr.get("flag") else f"<b class='v9-rd-tx' style='color:{_RED}'>✕ absent</b>"))
    out += f"<div class='v9-rd-bi'><span style='color:#51656a'>Mass/RT field</span>{mr_txt}</div>"
    lc = fair.get("lc_method_context_presence") or {}
    if lc.get("value") is None:
        lc_txt = f"<span class='v9-rd-tx' style='color:{_RD_GREY};font-weight:600'>{_e(lc.get('declared') or 'not assessed')}</span>"
    else:
        on = [n for c, n in _OV_LC_CHECKS if (lc.get("checks") or {}).get(c)]
        pips = "".join(f"<span class='v9-rd-pip{' on' if (lc.get('checks') or {}).get(c) else ''}' title='{_e(n)}'></span>" for c, n in _OV_LC_CHECKS)
        lc_txt = (f"<span class='v9-rd-tx'>{pips} {len(on)} of {len(_OV_LC_CHECKS)} <span style='{_RD_SMALL}'>({_e(', '.join(on).lower() or 'none')})</span></span>")
    out += f"<div class='v9-rd-bi'><span style='color:#51656a'>LC-method context</span>{lc_txt}</div>"
    return f"<div class='v9-rd-ind'>{out}</div>"


_RD_CSS = ("<style>.v9-rd table{border-collapse:collapse;width:100%;min-width:1080px;table-layout:fixed}"
           ".v9-rd th{vertical-align:bottom}.v9-rd td{line-height:1.45}"
           ".v9-rd-ind{display:grid;gap:3px}.v9-rd-bi{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr) 40px;align-items:center;gap:6px;font-size:.76rem}"
           ".v9-rd-tx{grid-column:2 / span 2}.v9-rd-pip{display:inline-block;width:9px;height:9px;border-radius:50%;background:rgba(19,35,39,.14);margin-right:2px;vertical-align:middle}.v9-rd-pip.on{background:#0d6e6e}.v9-rd-bt{display:block;height:8px;border-radius:4px;background:rgba(19,35,39,.08);overflow:hidden}"
           ".v9-rd-bf{display:block;height:100%;background:#0d6e6e;border-radius:4px}.v9-rd-bv{text-align:right;font-variant-numeric:tabular-nums}"
           ".v9-rd-chips{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:0 0 10px;align-items:stretch}.v9-rd-chips .v9-rd-chip{display:flex;flex-direction:column;justify-content:space-between;gap:4px;padding:7px 10px;border:1px solid rgba(19,35,39,.14);border-radius:10px;background:rgba(255,255,255,.7);font-size:.8rem}.v9-rd-chips .v9-rd-act{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.v9-rd-chips .v9-rd-act .v9-rd-adjust,.v9-rd-chips .v9-rd-act .v9-rd-default{margin-left:0}@media (max-width:820px){.v9-rd-chips{grid-template-columns:minmax(0,1fr)}}.v9-rd-chips select.v9-rd-k{display:inline-block;width:auto;min-width:0;height:auto;min-height:0;margin:0 2px;padding:0 4px;font:inherit;font-weight:700;border:1px solid rgba(47,111,143,.45);border-radius:6px;background:#fff;vertical-align:baseline;box-shadow:none}.v9-rd-adjust{margin-left:4px;font-weight:600}.v9-rd-default{margin-left:6px;font:inherit;font-size:.72rem;font-weight:600;color:#0d6e6e;background:#fff;border:1px solid rgba(13,110,110,.4);border-radius:999px;padding:0 8px;cursor:pointer;line-height:1.5}.v9-rd-default:hover{background:rgba(13,110,110,.08)}.v9-rd-default:focus-visible{outline:2px solid #0d6e6e;outline-offset:1px}.v9-rd-default[disabled]{color:#9aa7aa;border-color:rgba(19,35,39,.15);cursor:default;background:transparent}.v9-rd-chip{font-size:.76rem;background:rgba(47,111,143,.08);"
           "border:1px solid rgba(47,111,143,.22);border-radius:999px;padding:3px 10px}"
           ".v9-rd-scope{margin-top:8px;font-size:.78rem;color:#51656a;padding:7px 10px;border-left:3px solid rgba(19,35,39,.2);background:rgba(19,35,39,.03)}</style>")


def readiness_block(d: dict, params: dict | None = None) -> str:
    """Readiness profile per matrix (layout signed off on 2026-10-29): cross-validation sample-count support under the classes in force,
    analytical alerts against the fixed reference, and selected annotation and metadata indicators. Calculations are unchanged."""
    rows = ""; k = lb.folds(params); ext_p, low_p = rd.tiers(params); ref = rd.reference()
    for a in d["analyses"]:
        aid = a["analysis_id"]; eps = [(k_, lab) for k_, lab in SOURCES if (a["endpoints"].get(k_) or {}).get("readiness")]
        if not eps:
            rows += (f"<tr data-rd-analysis='{_e(aid)}' data-rd-ep='none'><td style='{_TD};white-space:nowrap'><b>{_e(aid)}</b></td><td style='{_TD}' colspan='5'>"
                     f"<b style='color:{_RED}'>no data</b> <span style='{_RD_SMALL}'>(no valid matrix in any data file)</span></td></tr>")
            continue
        agree = a.get("readiness_endpoints_agree") and len(eps) > 1
        groups = [(", ".join(lab for _, lab in eps), "all", eps)] if agree else [(lab, k_, [(k_, lab)]) for k_, lab in eps]
        for i, (label, tag, members) in enumerate(groups):
            row_eps = [(lab, a["endpoints"][k_]) for k_, lab in members]; r = row_eps[0][1]["readiness"]
            note = f" <span style='{_RD_SMALL}'>(same summary)</span>" if agree else ""   # "(data files differ)" dropped (user, 2026-11-01)
            summ = rd.summary(r["data"], r["task"])
            rows += (f"<tr class='v9-rd-row' data-rd-analysis='{_e(aid)}' data-rd-ep='{tag}' data-rd-data='{_e(r['data']['result'])}' "
                     f"data-rd-flags='{_e(','.join(x['key'] + ':' + x['tier'] for x in r['data']['alerts']))}' data-rd-task='{_e(r['task']['result'])}' "
                     f"data-rd-summary='{_e(summ)}'>"
                     f"<td style='{_TD};font-size:.82rem'>{f'<b>{_e(aid)}</b>' if i == 0 else ''}</td><td style='{_TD};font-size:.82rem'>{_e(label)}{note}</td>"
                     f"<td style='{_TD};font-size:.82rem'>{_rd_summary_cell(row_eps)}</td>"
                     f"<td style='{_TD};font-size:.82rem'>{_rd_count_cell(row_eps, k)}</td><td style='{_TD};font-size:.82rem'>{_rd_alerts(row_eps, params)}</td>"
                     f"<td style='{_TD};font-size:.82rem'>{_rd_reuse(row_eps, a['fair'])}</td></tr>")
    refname = (f"reference {ref['reference_version']}: the {ref['reference_matrices']:,} valid matrices of the 9 March 2026 pull assessed when it was made"
               if ref.get("reference_version") and ref.get("reference_matrices") else "the fixed reference")
    tips = {
        "cv": (f"Stratified {k}-fold cross-validation needs every class in every fold, so every class needs at least {k} distinct labelled "
               "ML-eligible sample IDs. This is a count only: it does not establish independent subjects, statistical power or predictive "
               "performance. Class support and class separability (with its chance level) describe the selected classes."),
        "alerts": (f"Four checks (missing entries and zeros, low-information features, redundant features, sample-median outliers), each compared "
                   f"with fixed cut-offs from {refname}: extreme below its {ext_p:g}th percentile, low below its {low_p:g}th. An alert prompts a review; it "
                   "does not mean the data cannot be used."),
        "ind": ("Named-feature coverage: the share of the data file's features that carry a metabolite name (not only m/z, retention time "
                "or an ID). Named features mapped to RefMet: the share of those named features that have a RefMet name. External IDs, the "
                "mass/RT field and LC-method context are per analysis."),
    }
    w = ("8%", "9%", "16%", "22%", "17%", "28%")
    th = lambda i, title, tip=None: (f"<th style='{_TH};width:{w[i]}'>{title}{_rd_info(tip) if tip else ''}</th>")
    classes = "the deposited sample labels" if d.get("deposited_labels") else _e(d.get("class_definition") or "the chosen classes")
    lo_k, hi_k = int(lb.PARAMETERS["cv_folds"][1]), int(lb.PARAMETERS["cv_folds"][2])
    k_opts = "".join(f"<option value='{v}'{' selected' if v == k else ''}>{v}</option>" for v in range(lo_k, hi_k + 1))
    # classes: a link to Label Structure; folds: a selector that recalculates the page with that k (the Show tools value is set too)
    k_def = int(lb.DEFAULT_PARAMS["cv_folds"]); dep = bool(d.get("deposited_labels"))
    dflt = lambda what, off, title, text: (f"<button type='button' class='v9-rd-default' data-default='{what}'"
                                           + (" disabled aria-disabled='true'" if off else "") + f" title='{_e(title)}'>{_e(text)}</button>")
    # classes: a link to Label Structure; folds: a selector that recalculates the page with that k (the Show tools value is set too);
    # each with a Default button (greyed out when already at the default)
    sel = _lb_selector(d.get("study_id", ""), d["label_choice"], inline=True) if d.get("label_choice") else ""
    adjust = ("<a href='#' class='v9-ovg-goto v9-rd-adjust v9-rd-open' aria-expanded='false' "
              "title='Choose the classes here (the same choice as in Label Structure)'>Adjust here</a>" if sel else
              "<a href='#' class='v9-ovg-goto v9-rd-adjust' data-goto='cohort' title='Open Label Structure to choose the classes'>Adjust here</a>")
    # three columns (user, 2026-11-01): what is in force on top, the actions below
    chips = (f"<div class='v9-rd-chips'><div class='v9-rd-chip'><div><b>Classes:</b> {classes}</div><div class='v9-rd-act'>" + adjust
             + dflt("classes", dep, "Back to the deposited sample labels (the whole factor string); sample edits are kept", "Default") + "</div></div>"
             f"<div class='v9-rd-chip'><div><b>Folds:</b> <select class='v9-rd-k' data-k='{k}' aria-label='Number of cross-validation folds k'>"
             + k_opts + "</select> "
             "<span style='color:#51656a'>change k to see its effect</span></div><div class='v9-rd-act'>"
             + dflt("folds", k == k_def, f"Back to {k_def} folds", f"Default ({k_def})") + "</div></div>"
             f"<div class='v9-rd-chip'><div><b>Alert cut-offs:</b> {ext_p:g}th (extreme) and {low_p:g}th (low) percentiles of reference "
             f"{_e(ref.get('reference_version') or '')}{_rd_custom_text(params)}</div><div class='v9-rd-act'>"
             "<a href='#' class='v9-ovg-goto v9-rd-adjust v9-rd-cut-open' aria-expanded='false' title='Set your own score cut-offs here "
             "(the same fields as in Show tools)'>Adjust here</a>"
             + dflt("cuts", not rd.custom(params) and (ext_p, low_p) == (2.5, 10.0), "Back to the reference cut-offs", "Default") + "</div></div></div>"
             + _rd_cut_panel(params)
             + (f"<div class='v9-rd-classes' hidden>{sel}"
                "<div style='font-size:.78rem;margin:-6px 0 12px'><a href='#' class='v9-ovg-goto' data-goto='cohort' "
                "title='Open the Label Structure tab'>Class sizes and factors per analysis in Label Structure ›</a></div></div>" if sel else "")
             + "<script>(function(){var box=document.currentScript.previousElementSibling;while(box&&!box.classList.contains('v9-rd-chips'))box=box.previousElementSibling;"
             "var pan=null,cpan=null;for(var x=box.nextElementSibling;x&&x.tagName!=='SCRIPT';x=x.nextElementSibling){if(x.classList.contains('v9-rd-classes'))pan=x;if(x.classList.contains('v9-rd-cuts'))cpan=x;}"
             "var c=box.querySelector('.v9-rd-k'),o=box.querySelector('.v9-rd-open'),co=box.querySelector('.v9-rd-cut-open');"
             "if(co&&cpan){co.addEventListener('click',function(e){e.preventDefault();cpan.hidden=!cpan.hidden;co.setAttribute('aria-expanded',String(!cpan.hidden));"
             "co.textContent=cpan.hidden?'Adjust here':'Close';});"
             "function field(n){var t=document.querySelector('input.v2-custom-cut[name=\"'+n+'\"]');if(!t){t=document.createElement('input');t.type='hidden';t.name=n;"
             "t.className='v2-custom-cut';t.setAttribute('form','run-form');document.body.appendChild(t);}return t;}"
             "cpan.querySelectorAll('.v9-rd-cut').forEach(function(i){var t=document.querySelector('input.v2-custom-cut[name=\"'+i.getAttribute('data-name')+'\"]');if(t)i.value=t.value;});"
             "function pct(n,v){document.querySelectorAll('[name='+n+']').forEach(function(x){x.value=v;});}"
             "var refs=JSON.parse(cpan.getAttribute('data-refs')||'{}'),pe=cpan.querySelector('.v9-rd-pct[data-name=readiness_extreme_pct]'),pl=cpan.querySelector('.v9-rd-pct[data-name=readiness_low_pct]');"
             "function grey(){if(!pe||!pl)return;var e=refs[pe.value]||{},l=refs[pl.value]||{};cpan.querySelectorAll('.v9-rd-cut').forEach(function(i){"
             "var m=i.getAttribute('data-name').match(/^readiness_cut_(.+)_(extreme|low)$/);if(!m)return;var v=m[2]==='extreme'?e[m[1]]:Math.max(l[m[1]],e[m[1]]);if(v!=null&&!isNaN(v))i.placeholder=(+v).toFixed(1);});}"
             "if(pe)pe.addEventListener('change',grey);if(pl)pl.addEventListener('change',grey);"
             "var ap=cpan.querySelector('.v9-rd-cut-apply');if(ap)ap.addEventListener('click',function(){cpan.querySelectorAll('.v9-rd-cut').forEach(function(i){field(i.getAttribute('data-name')).value=i.value.trim();});"
             "if(pe)pct('readiness_extreme_pct',pe.value);if(pl)pct('readiness_low_pct',pl.value);go();});"
             "var ud=cpan.querySelector('.v9-rd-cut-defaults');if(ud)ud.addEventListener('click',function(){document.querySelectorAll('input.v2-custom-cut,.v9-rd-cut').forEach(function(i){i.value='';});"
             "pct('readiness_extreme_pct','2.5');pct('readiness_low_pct','10');go();});"
             "cpan.querySelectorAll('.v9-open-tools').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();e.stopPropagation();"
             "var t=document.getElementById('tool-rail-toggle');if(t&&t.getAttribute('aria-expanded')!=='true')t.click();"
             "var g=[].slice.call(document.querySelectorAll('details.v2-tune-group')).filter(function(d){var s=d.querySelector('summary');return s&&s.textContent.trim()===a.getAttribute('data-group');})[0];"
             "if(g){g.open=true;setTimeout(function(){g.scrollIntoView({block:'start',behavior:'smooth'});},250);}});});}"
             "if(o&&pan)o.addEventListener('click',function(e){e.preventDefault();pan.hidden=!pan.hidden;o.setAttribute('aria-expanded',String(!pan.hidden));"
             "o.textContent=pan.hidden?'Adjust here':'Close';});"
             "var me=box.closest('.v9-rd');document.addEventListener('click',function(e){var g=e.target.closest&&e.target.closest('.v9-rd-goto-settings');"
             "if(!g||g.closest('.v9-rd')!==me)return;e.preventDefault();[[o,pan],[co,cpan]].forEach(function(x){if(x[0]&&x[1]&&x[1].hidden)x[0].click();});"
             "box.scrollIntoView({block:'start',behavior:'smooth'});});"
             "function go(){var f=document.getElementById('run-form');if(!f)return;if(f.requestSubmit)f.requestSubmit();else f.submit();}"
             "function setK(v){document.querySelectorAll('[name=cv_folds]').forEach(function(x){x.value=v;});go();}"
             "if(c)c.addEventListener('change',function(){setK(c.value);});"
             f"box.querySelectorAll('.v9-rd-default').forEach(function(b){{b.addEventListener('click',function(){{if(b.disabled)return;"
             f"if(b.getAttribute('data-default')==='folds'){{setK('{k_def}');return;}}"
             "if(b.getAttribute('data-default')==='cuts'){document.querySelectorAll('input.v2-custom-cut,.v9-rd-cut').forEach(function(i){i.value='';});"
             "document.querySelectorAll('[name=readiness_extreme_pct]').forEach(function(x){x.value='2.5';});document.querySelectorAll('[name=readiness_low_pct]').forEach(function(x){x.value='10';});go();return;}"
             "var h=document.getElementById('label-classes-field');if(!h){h=document.createElement('input');h.type='hidden';h.name='label_classes';"
             "h.id='label-classes-field';h.setAttribute('form','run-form');document.body.appendChild(h);}h.value='';go();});});})();</script>")
    return ("<div class='v9-rd' style='margin:14px 0 18px'>" + _RD_CSS +
            "<h4 style='margin:0 0 6px;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em'>Readiness profile</h4>"
            + chips + _readiness_description(params)
            + "<div style='overflow-x:auto;contain:inline-size'><table><tr>"
            + th(0, "Analysis") + th(1, "Endpoint") + th(2, "Summary")
            + th(3, "Cross-validation sample-count support", tips["cv"]) + th(4, "Analytical alerts · fixed reference", tips["alerts"])
            + th(5, "Selected annotation and metadata indicators", tips["ind"])
            + f"</tr>{rows}</table></div>"
            "<div class='v9-rd-scope'><span aria-hidden='true' style='color:#c98a00;font-weight:700;margin-right:4px'>⚠</span>Count support does not establish statistical power or independent samples; batch confounding and other "
            "dependence require separate review. An analytical alert prompts a review; it does not mean the data cannot be used. Class "
            "separability is the class R-value of <a href='https://doi.org/10.1016/j.compbiomed.2010.12.006' target='_blank' rel='noopener'>"
            "Oh (Comput Biol Med 2011)</a>, a measure of how much the classes overlap among each sample's nearest neighbours; it is not an "
            "estimate of predictive accuracy.</div></div>")


def overview_scores(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    d = _overview_data(study_id, params, overrides)
    if d is None:
        return ""
    defs = _ov_definitions(params)
    present = [(k, lab) for k, lab in SOURCES if any(a["endpoints"][k].get("availability") == "valid" for a in d["analyses"])]
    absent = [(k, lab) for k, lab in SOURCES if (k, lab) not in present]
    letters = lambda group: "".join(f"<th class='v9-ovg-l{' v9-ovg-s2' if i == 0 else ''}' title='{_e(n)}'>{l}</th>" for i, (_, l, n) in enumerate(group))
    ns, na, nn, nm = len(_OV_STRUCT), len(_OV_ANALYTICAL) + 1, len(_OV_ANNOTATION), len(_OV_ML)
    head = "<tr><th rowspan='3' class='v9-ovg-an' style='vertical-align:bottom'>Analysis</th>"
    if present:
        head += (f"<th class='v9-ovg-sec v9-ovg-s1' colspan='{ns * len(present)}'>{_ov_goto('structural', 'Structural')}</th>"
                 f"<th class='v9-ovg-sec v9-ovg-s1' colspan='{na * len(present)}'>{_ov_goto('analytical', 'Analytical QC')}</th>"
                 f"<th class='v9-ovg-sec v9-ovg-s1' colspan='{nn * len(present)}'>{_ov_goto('annotation', 'Annotation')}</th>"
                 f"<th class='v9-ovg-sec v9-ovg-s1' colspan='{nm * len(present)}'>{_ov_goto('ml', 'ML task readiness')}</th>")
    head += (f"<th class='v9-ovg-sec v9-ovg-s1' colspan='{len(_OV_LABELS)}'>{_ov_goto('cohort', 'Label structure')}</th>"
             f"<th class='v9-ovg-sec v9-ovg-s1' colspan='4'>{_ov_goto('metadata', 'Metadata &amp; FAIR')}</th></tr><tr>")
    for group_n in (ns, na, nn, nm) if present else ():
        head += "".join(f"<th class='v9-ovg-ep{' v9-ovg-s1' if i == 0 else ' v9-ovg-s2'}' colspan='{group_n}'>{lab}</th>" for i, (_, lab) in enumerate(present))
    head += (f"<th class='v9-ovg-ep v9-ovg-s1' colspan='{len(_OV_LABELS)}'>per analysis</th>"
             "<th class='v9-ovg-ep v9-ovg-s1' colspan='4'>per analysis</th></tr><tr>")
    if present:
        head += "".join(letters(_OV_STRUCT).replace("v9-ovg-l v9-ovg-s2", "v9-ovg-l v9-ovg-s1" if i == 0 else "v9-ovg-l v9-ovg-s2", 1) for i in range(len(present)))
        head += "".join(letters(_OV_ANALYTICAL).replace("v9-ovg-l v9-ovg-s2", "v9-ovg-l v9-ovg-s1" if i == 0 else "v9-ovg-l v9-ovg-s2", 1)
                        + "<th class='v9-ovg-l' title='Units declared'>Un</th>" for i in range(len(present)))
        head += "".join(letters(_OV_ANNOTATION).replace("v9-ovg-l v9-ovg-s2", "v9-ovg-l v9-ovg-s1" if i == 0 else "v9-ovg-l v9-ovg-s2", 1) for i in range(len(present)))
        head += "".join(letters(_OV_ML).replace("v9-ovg-l v9-ovg-s2", "v9-ovg-l v9-ovg-s1" if i == 0 else "v9-ovg-l v9-ovg-s2", 1) for i in range(len(present)))
    head += letters(_OV_LABELS).replace("v9-ovg-l v9-ovg-s2", "v9-ovg-l v9-ovg-s1", 1)
    head += ("<th class='v9-ovg-l v9-ovg-s1' title='RefMet mapping'>Rf</th><th class='v9-ovg-l' title='External IDs'>Ex</th>"
             "<th class='v9-ovg-l' title='Mass/RT metadata'>MR</th><th class='v9-ovg-l' title='LC-method context'>LC</th></tr>")
    rows = ""
    for a in d["analyses"]:
        aid = a["analysis_id"]; cells = ""
        for section in ("structural", "analytical", "annotation", "ml") if present else ():
            for i, (k, lab) in enumerate(present):
                ep = a["endpoints"][k]; where = f"{aid} · {lab}" + (" · sample edits applied" if ep.get("edited") else "")
                group = {"structural": _OV_STRUCT, "analytical": _OV_ANALYTICAL, "annotation": _OV_ANNOTATION, "ml": _OV_ML}[section]
                if ep.get("availability") != "valid":
                    part = _ov_absent(len(group) + (section == "analytical"), ep["reason"], aid, lab)
                else:
                    part = "".join(_ov_tile(n, where, ep["items"][key], defs[key], key) for key, _, n in group)
                    if section == "analytical":
                        part += _ov_flag("Units declared", where, ep["items"]["units"], defs["units"], "units", "units declared", "units not declared")
                cls = "v9-ovg-s1" if i == 0 else "v9-ovg-s2"
                cells += part.replace("<td>", f"<td class='{cls}'>", 1).replace(" data-k=", f" data-ep='{k}' data-k=")
        where = f"{aid} · per analysis (every endpoint)"
        lcells = ""
        for k, _, n in _OV_LABELS:
            it = a["labels"][k]
            if it.get("absent"):
                lcells += _ov_absent(1, it["reason"], aid, "label structure")
            elif it.get("differs"):
                tip = _ov_tip(n, where, {"value": None, "reason": it["reason"]}, defs[k])
                lcells += f"<td><span class='v9-ovg-tile v9-ovg-differs' tabindex='0' data-k='{k}' data-shown='' data-tip='{tip}'>≠</span></td>"
            elif k == "cv_feasible" and it.get("value") is not None:
                lcells += _ov_flag(n, where + " · classes: " + d.get("class_definition", ""), {**it, "flag": it["value"] >= 100}, defs[k], k,
                                   "cross-validation feasible", "cross-validation not feasible")
            else:
                lcells += _ov_tile(n, where + " · classes: " + d.get("class_definition", ""), it, defs[k], k)
        cells += lcells.replace("<td>", "<td class='v9-ovg-s1'>", 1).replace(" data-k=", " data-ep='analysis' data-k=")
        fair = ""
        for k, l in _OV_FAIR:
            it = a["fair"][k]; name = {"Rf": "RefMet mapping", "Ex": "External IDs", "MR": "Mass/RT metadata", "LC": "LC-method context"}[l]
            if k == "mass_rt_like_metadata_presence":
                fair += _ov_flag(name, where, it, defs[k], k, "mass or RT field present", "no mass or RT field")
            elif k == "lc_method_context_presence":
                tip = _ov_tip(name, where, it, defs[k])
                if it.get("value") is None:
                    fair += f"<td><span class='v9-ovg-flag v9-ovg-na' tabindex='0' data-k='{k}' data-shown='' data-tip='{tip}'></span></td>"
                else:
                    pips = "".join(f"<span class='v9-ovg-pip{' on' if it['checks'][c] else ''}' title='{n}'></span>" for c, n in _OV_LC_CHECKS)
                    fair += f"<td><span class='v9-ovg-pips' tabindex='0' data-k='{k}' data-shown='{_e(it['shown'])}' data-tip='{tip}'>{pips}</span></td>"
            else:
                fair += _ov_tile(name, where, it, defs[k], k)
        cells += fair.replace("<td>", "<td class='v9-ovg-s1'>", 1).replace(" data-k=", " data-ep='analysis' data-k=")
        rows += f"<tr data-analysis='{_e(aid)}'><td class='v9-ovg-an'>{_e(aid)}</td>{cells}</tr>"
    ramp = "linear-gradient(90deg," + ",".join(f"{_ov_colour(v)} {v}%" for v in (0, 25, 50, 62, 75, 85, 90, 100)) + ")"
    legend = (f"<div class='v9-ovg-bar'><span class='v9-ovg-key'><span class='v9-ovg-ramp' style='background:{ramp}'></span>0 → 100 · {SPAN_DIR}higher = better</span></span>"
              "<span class='v9-ovg-key'><span class='v9-ovg-flag v9-ovg-yes'>✓</span><span class='v9-ovg-flag v9-ovg-no'>✕</span> 0/100 item</span>"
              "<span class='v9-ovg-key'><span class='v9-ovg-pips'><span class='v9-ovg-pip on'></span><span class='v9-ovg-pip on'></span><span class='v9-ovg-pip'></span></span> 2 of 3 LC checks</span>"
              "<span class='v9-ovg-key'><span class='v9-ovg-tile v9-ovg-na'></span> not scored (hover: why)</span>"
              "<span class='v9-ovg-key'><span class='v9-ovg-tile v9-ovg-absent'></span> no valid matrix</span>"
              + ("<span class='v9-ovg-key'><span class='v9-ovg-tile v9-ovg-stale' style='background:#e9efed'></span> not recomputed for the sample edits</span>"
                 if any((ep.get("items") or {}).get("redundancy", {}).get("note") for a in d["analyses"] for ep in a["endpoints"].values()) else "")
              + "<button type='button' class='v9-ovg-btn' aria-pressed='false'>Show numbers</button></div>")
    keys = ("<div class='v9-ovg-keys'><b>Structural</b> S sample support · F samples with a factor label · N numeric value cells · D duplicate sample IDs"
            " &nbsp;|&nbsp; <b>Analytical QC</b> M missing entries and zeros · L low-information features · R redundant features · O outlier samples · U units declared"
            " &nbsp;|&nbsp; <b>Annotation</b> Cv named-feature coverage · Rm RefMet mapping of named features"
            " &nbsp;|&nbsp; <b>ML task readiness</b> Sp class separability"
            " &nbsp;|&nbsp; <b>Label structure</b> Cs class support · Kf cross-validation feasible"
            " &nbsp;|&nbsp; <b>Metadata &amp; FAIR</b> Rf RefMet mapping · Ex external IDs · MR mass/RT metadata · LC LC-method context</div>")
    note_absent = ""
    if absent:
        why = []
        for k, lab in absent:
            reasons = collections.Counter(a["endpoints"][k]["reason"] for a in d["analyses"])
            why.append(f"{lab}: " + "; ".join(f"{r} ({n} analys{'e' if n != 1 else 'i'}s)" for r, n in reasons.items()))
        note_absent = f"<div class='v9-ovg-keys'>Not in the grid (no valid matrix in any analysis): {_e(' · '.join(why))}.</div>"
    meta = d["study_metadata"]
    study_line = ""
    if meta:
        checks = meta.get("checks") or {}
        pips = "".join(f"<span class='v9-ovg-pip{' on' if checks.get(k) else ''}' title='{_e(lab)}: {'yes' if checks.get(k) else 'no'}'></span>" for k, lab in _FAIR_CHECKS)
        yes = ", ".join(lab for k, lab in _FAIR_CHECKS if checks.get(k)) or "none"
        no = ", ".join(lab for k, lab in _FAIR_CHECKS if not checks.get(k)) or "none"
        study_line = (f"<div class='v9-ovg-keys v9-ovg-study'><b>Metadata &amp; FAIR, study level</b> (the same for every analysis): Study metadata "
                      f"<span class='v9-ovg-pips'>{pips}</span> {meta.get('passed')} of {meta.get('total')} checks · yes: {_e(yes)} · no: {_e(no)}</div>")
    return (
        readiness_block(d, params) +
        f"{_OV_CSS}<div class='v9-ovg'>"
        "<h4>Scores · every analysis × endpoint × item</h4>"
        "<p class='v9-ovg-lede'>Every scored item of Structural, Analytical QC, Annotation, ML task readiness, Label structure and Metadata &amp; FAIR, exactly as in its tab. Hover or tap a tile for its value, "
        "the count behind it and what it measures. Nothing is combined or averaged.</p>"
        + _overview_description(params) + legend
        + f"<div class='v9-ovg-scroll'><table class='v9-ovg-table'>{head}{rows}</table></div>{study_line}{keys}{note_absent}</div>{_OV_JS}")


def export_overview_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    d = _overview_data(study_id, params, overrides)
    if d is None:
        return None
    return {"schema": "merit.overview_analysis_endpoint.pilot.v2", "higher_is_better": True,
            "note": "every scored item as its tab shows it: Structural and Analytical QC per matrix (analysis x endpoint), Metadata & FAIR per analysis, "
                    "study metadata once; 'shown' is the value as displayed, 'reason' says why an item is not available. Nothing is combined, averaged or ranked.",
            "sample_edits": overrides or {}, "study_metadata": {k: d["study_metadata"].get(k) for k in ("passed", "total", "score", "checks")},
            "readiness_settings": rd.settings(params, lb.folds(params), d.get("deposited_labels", True), d.get("class_definition", "")),
            "analyses": d["analyses"]}


MAX_EDITOR_ROWS = 3000


# the editor shows QC-like, blank-like and standard-like positions as one status (Analytical QC keeps the three counts apart)
_KIND_NAMES = (("sample", "ML-eligible"), ("control", "QC-like/blank-like/standard-like (not ML-eligible)"), ("alignment", "Tier 1 sample alignment (not ML-eligible)"),
               ("set_aside", "not ML-eligible (edit)"), ("excluded", "left out (edit)"))
# order in which the editor groups sample IDs: not ML-eligible in any matrix first (by reason), then ML-eligible in some matrices only
# ('mixed', e.g. a Tier 1 sample alignment row in one analysis and measured in another), then ML-eligible in every matrix where it occurs
_GROUP_ORDER = ("control", "alignment", "set_aside", "excluded", "mixed", "sample")
_MIXED_NAME = "ML-eligible in some matrices only"
# the per-matrix strip of the editor: one cell per analysis x endpoint, coloured by the status of the sample ID in that matrix
_STRIP_SLOTS = (("datatable", "datatable"), ("mwtab", "mwTab"), ("untarg_data", "untarg_data"))   # full endpoint names: no one-letter codes in the UI
_STRIP_STYLE = {"sample": ("#0d6e6e", "ML-eligible"), "alignment": ("repeating-linear-gradient(45deg,#c9d3d5 0 3px,#eef2f3 3px 6px)", "Tier 1 sample alignment"),
                "control": ("#d98c1f", "QC-like/blank-like/standard-like"), "set_aside": ("#8a6d9e", "not ML-eligible (edit)"),
                "excluded": ("#5b6770", "left out (edit)")}


def _cell_kind(kinds: collections.Counter) -> str:
    """The status a strip cell shows for the positions of one sample ID in one matrix (several positions: ML-eligible when any is)."""
    m = _merged(kinds)
    return next((k for k in ("sample", "alignment", "control", "set_aside", "excluded") if m.get(k)), "")


def _strip(mats: list, cells: dict, edited: bool = False) -> str:
    """One small cell per matrix of the study: analyses in order, one slot per valid endpoint of the analysis (datatable, mwTab, untarg_data)."""
    out = []
    for aid, present in mats:
        slots = []
        for key, name in _STRIP_SLOTS:
            if key not in present:
                continue
            c = cells.get((aid, key))
            if c is None:
                slots.append("<i class='v9-mx v9-mx-none'>·</i>")
                continue
            dep, now = _cell_kind(c[0]), _cell_kind(c[1])
            n = sum(c[1].values())
            tip = f"{aid} {name}: {_STRIP_STYLE[now][1]}" + (f" ({n} positions)" if n > 1 else "") + (f"; as deposited: {_STRIP_STYLE[dep][1]}" if dep != now else "")
            slots.append(f"<i class='v9-mx v9-mx-{now}{' v9-mx-changed' if dep != now else ''}' title='{_e(tip)}'></i>")
        out.append(f"<span class='v9-mx-an'>{''.join(slots)}</span>")
    return f"<div class='v9-mx-strip'>{''.join(out)}</div>"


def _strip_head(mats: list) -> str:
    return ("<div class='v9-mx-strip v9-mx-head'>" + "".join(
        f"<span class='v9-mx-an' title='{_e(aid)}'><span class='v9-mx-aid'>{_e(aid)}</span>"
        + "".join(f"<span class='v9-mx v9-mx-letter' title='{_e(aid)} {_e(name)}'></span>" for k, name in _STRIP_SLOTS if k in present) + "</span>"
        for aid, present in mats) + "</div>")


def _where_text(mats: list, cells: dict) -> str:
    """'ML-eligible in 2 of 3 matrices · Tier 1 sample alignment in AN005215 datatable' (after the edits in force)."""
    order = [(aid, k) for aid, present in mats for k, _ in _STRIP_SLOTS if (aid, k) in cells]
    now = {m: _cell_kind(cells[m][1]) for m in order}
    n, e = len(order), sum(1 for m in order if now[m] == "sample")
    head = (f"ML-eligible in {'its matrix' if n == 1 else f'all {n} matrices'}" if e == n else
            f"ML-eligible in {e} of {n} matrices" if e else f"not ML-eligible in {'its matrix' if n == 1 else f'any of its {n} matrices'}")
    rest = []
    for k in ("alignment", "control", "set_aside", "excluded"):
        ms = [m for m in order if now[m] == k]
        if ms:
            shown = ", ".join(f"{a} {dict(SOURCES)[s]}" for a, s in ms[:4]) + (f" (+{len(ms) - 4} more)" if len(ms) > 4 else "")
            rest.append(f"{_STRIP_STYLE[k][1]} in {shown}")
    return " · ".join([head] + rest)


_STRIP_CSS = """<style>
.v9-mx-strip{display:flex;gap:6px;flex-wrap:nowrap;margin:2px 0 3px}
.v9-mx-an{display:inline-flex;gap:2px;position:relative;min-width:58px}
.v9-mx{display:inline-block;width:14px;height:12px;border-radius:3px;box-sizing:border-box;font-style:normal}
""" + "".join(f".v9-mx-{k}{{background:{bg}}}" for k, (bg, _) in _STRIP_STYLE.items()) + """
.v9-mx-none{background:none;color:#a7b2b5;font-size:.7rem;line-height:12px;text-align:center}
.v9-mx-changed{outline:2px solid #9b2335;outline-offset:1px}
.v9-mx-head .v9-mx-an{flex-direction:row;padding-top:11px}
.v9-mx-aid{position:absolute;top:0;left:0;font-size:.58rem;color:#51656a;font-weight:600;white-space:nowrap;letter-spacing:0}
.v9-mx-letter{border:1px solid #b9c5c8;background:#fff}
.v9-mx-legend{display:flex;gap:12px;flex-wrap:wrap;font-size:.76rem;color:#51656a;margin:0 0 8px}
.v9-mx-legend i.v9-mx{vertical-align:-2px;margin-right:4px}
</style>"""


def _merged(kinds) -> collections.Counter:
    out = collections.Counter({k: v for k, v in kinds.items() if k not in ("qc", "blank", "standard")})
    out["control"] = sum(kinds.get(k, 0) for k in ("qc", "blank", "standard"))
    return out


def _kind_text(kinds) -> str:
    kinds = _merged(kinds)
    return " · ".join(f"{kinds[k]} {name}" for k, name in _KIND_NAMES if kinds.get(k))


def editor_panel(study_id: str, overrides: dict | None = None, params: dict | None = None) -> str:
    """Adjust Matrix Properties: one row per sample ID of the study, across all analyses and endpoints."""
    overrides = overrides or {}
    confirmed = sp.subjects_confirmed(params)
    raw = _raw_study(str(study_id or "").strip().upper())
    if raw is None:
        return f"<div style='{_DASHED}'>No sample list for this study.</div>"
    # the status of every position comes from the same function as the QC / blank controls of Analytical QC (merit/qc_blank_v9.py),
    # so the two tabs always agree: as deposited with the curated corrections applied, and after the sample edits in force
    info: dict[str, dict] = {}
    n_matrices = 0
    sid_study = str(study_id or "").strip().upper()
    mats = [(a["analysis_id"], {k for k, _ in SOURCES if "roster" in (a["endpoints"].get(k) or {})}) for a in raw["analyses"]]
    mats = [(aid, present) for aid, present in mats if present]
    for a in raw["analyses"]:
        for key, label in SOURCES:
            ep = a["endpoints"].get(key) or {}
            if "roster" not in ep:
                continue
            n_matrices += 1
            kinds = qb.position_kinds(ep["roster"], None, sid_study)
            kinds_now = qb.position_kinds(ep["roster"], overrides, sid_study) if overrides else kinds
            curated = {c["sample_id"]: c for c in ep.get("curated_corrections") or []}
            for row, k, k_now in zip(ep["roster"], kinds, kinds_now):
                if row[sv.I_ID] not in info:          # the evidence of its first position, computed once per sample ID
                    info[row[sv.I_ID]] = {"where": [], "kinds": collections.Counter(), "kinds_now": collections.Counter(), "curated": None, "label": "",
                                          "cells": {}, "evidence": _sample_evidence(sid_study, a["analysis_id"], row)}
                d = info[row[sv.I_ID]]
                d["where"].append(f"{a['analysis_id']} {label}")
                d["kinds"][k] += 1; d["kinds_now"][k_now] += 1
                cell = d["cells"].setdefault((a["analysis_id"], key), (collections.Counter(), collections.Counter()))
                cell[0][k] += 1; cell[1][k_now] += 1
                if row[sv.I_ID] in curated: d["curated"] = curated[row[sv.I_ID]]
                if not d["label"]:
                    d["label"] = row[sv.I_LABEL] if sv.nonempty_label(row[sv.I_LABEL]) else (row[sv.I_TLABEL] if row[sv.I_TABLE] else "")
    if not info:
        return f"<div style='{_DASHED}'><b>Adjust Matrix Properties</b><br>This study has no valid matrix, so there is no sample to edit.</div>"
    pre = sa.precalculated_labels(sid_study, raw["analyses"])          # MERIT's batch-like label per sample ID (Sample annotations, Analytical QC)
    ids = list(info)
    shown = ids[:MAX_EDITOR_ROWS]
    rows = ""
    cat_count = collections.Counter()
    for i, sid in enumerate(shown):
        d = info[sid]; edit = overrides.get(sid) or {}
        now_merged = _merged(d["kinds_now"])
        per = [_cell_kind(c[1]) for c in d["cells"].values()]
        mixed = "sample" in per and any(k != "sample" for k in per)
        every = bool(per) and all(k == "sample" for k in per)       # ML-eligible in every matrix where the sample ID occurs
        cats = ([k for k in _GROUP_ORDER if now_merged.get(k) and k != "mixed"] + (["mixed"] if mixed else []) + (["every"] if every else [])
                + (["edited"] if edit else []))
        cat_count.update(cats)
        # group: not ML-eligible in any matrix (by its first reason), ML-eligible in some matrices only, ML-eligible in every matrix
        group = ("sample" if all(k == "sample" for k in per) else "mixed" if mixed else next(k for k in _GROUP_ORDER if now_merged.get(k)))
        rank = _GROUP_ORDER.index(group)
        measured = int(sum(v for k, v in d["kinds"].items() if k != "alignment") > 0)
        status = "exclude" if edit.get("excluded") else ("1" if edit.get("eligible") is True else "0" if edit.get("eligible") is False else "")
        now = _kind_text(d["kinds_now"]) if d["kinds_now"] != d["kinds"] else ""
        where_text = _where_text(mats, d["cells"])
        options = "".join(f"<option value='{v}'{' selected' if status == v else ''}>{t}</option>" for v, t in
                          (("", "as deposited"), ("1", "ML-eligible"), ("0", "not ML-eligible"), ("exclude", "leave out")))
        changed = f"background:{_RED}0d;" if edit else ""
        rows += (f"<tr data-v9-sample='{_e(sid)}' data-index='{i}' data-group-rank='{rank}' data-cats='{' '.join(cats)}' data-elig-dep='{int(d['kinds'].get('sample', 0) > 0)}' "
                 f"data-measured='{measured}' data-init-status='{status}' data-init-label='{_e(str(edit.get('label', '') or ''))}' data-init-batch='{_e(str(edit.get('batch', '') or ''))}' data-init-subject='{_e(str(edit.get('subject', '') or ''))}' data-dep-subject='{_e(d['evidence']['subject'])}' style='{changed}'><td style='{_TD};white-space:nowrap'><b>{_e(sid)}</b></td>"
                 + _evidence_cell(d["evidence"]) +
                 f"<td class='v9-sample-status' data-kinds='{_e(json.dumps(dict(d['kinds']), sort_keys=True))}' data-kinds-now='{_e(json.dumps(dict(d['kinds_now']), sort_keys=True))}' data-group='{group}' style='{_TD}'>"
                 + _strip(mats, d["cells"]) + f"<div class='v9-mx-where' style='font-size:.8rem'>{_e(where_text)}</div>"
                 + (_red_note("after your edit", _e(now), "font-size:.76rem") if now else "")
                 + (_red_note("curated correction", f"{_e('QC-like' if d['curated']['decision'] == 'qc' else d['curated']['decision'])} ({_e(d['curated']['matched'])})", "font-size:.74rem") if d["curated"] else "")
                 + "</td>"
                 f"<td style='{_TD}'><select class='v9-status' style='font:inherit;font-size:.82rem;padding:3px 6px;border-radius:8px;min-width:128px'>{options}</select></td>"
                 f"<td style='{_TD}'><input class='v9-label' type='text' value='{_e(str(edit.get('label', '') or ''))}' placeholder='{_e(d['label'] or 'no label deposited')}' "
                 "style='font:inherit;font-size:.82rem;padding:3px 6px;border-radius:8px;border:1px solid rgba(19,35,39,.2);width:100%;min-width:220px'></td>"
                 f"<td style='{_TD}'><input class='v9-batch' type='text' value='{_e(str(edit.get('batch', '') or ''))}' "
                 f"placeholder='{_e(pre[sid][0] if sid in pre else 'none pre-calculated')}' title='{_e(('MERIT pre-calculated this from the deposited field “' + str(pre[sid][1]) + '”') if sid in pre else 'MERIT found no batch-like value for this sample')}' "
                 "style='font:inherit;font-size:.82rem;padding:3px 6px;border-radius:8px;border:1px solid rgba(19,35,39,.2);width:100%;min-width:150px'>"
                 + (f"<div style='font-size:.72rem;color:#51656a'>from “{_e(str(pre[sid][1]))}”</div>" if sid in pre else "") + "</td>"
                 f"<td style='{_TD}'><input class='v9-subject' type='text' value='{_e(str(edit.get('subject', '') or ''))}' "
                 f"placeholder='{_e(d['evidence']['subject'] or 'no subject deposited')}' title='Blank: the deposited subject ID (grey). Type another subject, or “-” for none.' "
                 "style='font:inherit;font-size:.82rem;padding:3px 6px;border-radius:8px;border:1px solid rgba(19,35,39,.2);width:100%;min-width:130px'></td></tr>")
    capped = (_red_note(f"Only the first {MAX_EDITOR_ROWS:,} sample IDs are listed", f"This study has {len(ids):,} sample IDs.", "font-size:.84rem;margin:0 0 8px") if len(ids) > len(shown) else "")
    active = (_red_note(f"{len(overrides)} sample IDs are edited", "Their rows are tinted.", "font-size:.86rem;margin:0 0 8px") if overrides else "")
    return (
        "<div class='v9-editor'>"
        "<h4 style='margin:0 0 8px;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em'>Adjust Matrix Properties</h4>"
        "<p class='v9-editor-lead' style='font-size:.9rem;margin:0 0 8px;color:#113e52'><b>Check the sample type of each sample ID and choose which samples are ML-eligible "
        "(Use as), then press Apply sample edits: every metric on this page is recomputed with your choice. Or leave every row as deposited to keep MERIT's default.</b></p>"
        "<details class='v9-editor-help' style='margin:0 0 10px;border:1px solid rgba(19,35,39,.14);border-radius:12px;background:rgba(255,255,255,.6)'>"
        "<summary style='cursor:pointer;padding:8px 14px;font-weight:700;color:#113e52;font-size:.86rem'>"
        "How this table works <span style='font-weight:400;color:#51656a'>· click to expand</span></summary>"
        f"<p style='font-size:.86rem;margin:0;padding:0 14px 12px'>One row per sample ID of the study ({len(ids):,} sample IDs in {n_matrices} matrices). "
        "An edit applies to that sample ID in <b>every analysis and endpoint where it occurs</b>; the strip in the third column shows them, one cell per matrix. "
        "You can mark a sample ML-eligible or not ML-eligible, leave it out, give it a factor label, or give it a batch-like label. "
        "Apply recomputes every panel, score and download of this study from the edited samples; the deposited files are not changed. "
        "A Tier 1 sample alignment row holds no measured value and stays not ML-eligible. The status of a sample ID can differ between analyses: "
        "a Tier 1 sample alignment row in one analysis (all zeros in its datatable, absent from its mwTab) can be measured and ML-eligible in another; "
        "the strip shows each matrix separately, and such samples are grouped as “ML-eligible in some matrices only”. "
        "The status column uses the same rule as Sample eligibility &amp; analytical controls in Analytical QC (16 keywords and the curated corrections): "
        "ML-eligible, or QC-like/blank-like/standard-like (not ML-eligible), in each matrix; a cell holding several positions (repeated injections) says so on hover. "
        f"<span class='v9-batch-premise' style='color:{_RED};'>The batch-like label column is pre-calculated by MERIT (grey text, from the deposited field named under it); "
        "type a label to replace it, or “-” to give a sample none. It is used by Sample annotations (MetaBatch-style filters) in Analytical QC, which is informational and not scored.</span> "
        "Use the Show row under the column headers to show only some statuses, group the rows by status, or set the Use as value of every row shown at once. "
        "The Deposited evidence column shows what the deposit says about each sample ID: its subject ID, the factor values the rule reads, why it has its status "
        "(the keyword and where it was found, a curated decision, or no keyword), and the additional sample data of its line in the sample table. "
        "The Subject column holds the subject of each sample ID: the deposited subject ID in grey, or what you type (“-” for none). Sample IDs from the same "
        "person, animal or source are not independent observations. The deposited field is often not a subject (a sample type or a group name, the same "
        "value on most samples), so MERIT-ML uses it only after you tick the confirmation box above the table; until then results are labelled "
        "“subject dependence not adjusted” wherever sample IDs share a subject ID. Confirmed subjects change only the informational test of features "
        "associated with the selected classes (ML Task Readiness); no score changes.</p></details>"
        f"{active}{capped}"
        "<div style='display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 10px'>"
        "<input type='text' class='v9-filter' placeholder='Filter sample IDs' oninput='v9FilterSamples(this)' "
        "style='font:inherit;font-size:.84rem;padding:6px 10px;border-radius:10px;border:1px solid rgba(19,35,39,.2);width:220px'>"
        "<button type='button' class='v2-apply' onclick='v9ApplySampleEdits(this)' style='padding:8px 14px;border-radius:12px'>Apply sample edits</button>"
        "<button type='button' class='v2-reset' onclick='v9ClearSampleEdits(this)' style='padding:8px 14px;border-radius:12px'>Clear all edits</button>"
        "</div>"
        "<div class='v9-subject-box' style='font-size:.84rem;margin:0 0 8px;padding:8px 10px;border-radius:10px;border:1px solid rgba(19,35,39,.14);background:rgba(255,255,255,.6)'>"
        f"<label style='display:flex;gap:8px;align-items:flex-start;cursor:pointer'><input type='checkbox' class='v9-subjects-confirm'{' checked' if confirmed else ''} "
        f"data-init='{int(confirmed)}' style='margin-top:3px'><span><b>The Subject column holds real subjects</b> (one person, animal or source each): sample IDs "
        "that share a subject are not independent, and the test of features associated with the selected classes accounts for it.</span></label>"
        "<div class='v9-subject-map' style='margin:4px 0 6px 24px;color:#113e52'></div>"
        "<div style='display:flex;gap:6px;flex-wrap:wrap;margin-left:24px'>"
        "<button type='button' class='v9-subject-own' style='font:inherit;font-size:.78rem;padding:3px 10px;border-radius:9px;border:1px solid rgba(13,110,110,.4);background:#fff;color:#0d6e6e;cursor:pointer'>each sample ID is its own subject</button>"
        "<button type='button' class='v9-subject-none' style='font:inherit;font-size:.78rem;padding:3px 10px;border-radius:9px;border:1px solid rgba(13,110,110,.4);background:#fff;color:#0d6e6e;cursor:pointer'>no subjects</button>"
        "<button type='button' class='v9-subject-dep' style='font:inherit;font-size:.78rem;padding:3px 10px;border-radius:9px;border:1px solid rgba(13,110,110,.4);background:#fff;color:#0d6e6e;cursor:pointer'>back to deposited</button>"
        "<span style='color:#51656a;font-size:.76rem;align-self:center'>these set the Subject column of every listed sample ID; press Apply sample edits to use them</span></div></div>"
        f"<div class='v9-live' data-capped='{int(len(ids) > len(shown))}' style='font-size:.84rem;margin:0 0 8px;padding:6px 10px;border-radius:10px;background:rgba(13,110,110,.07)'></div>"
        + _STRIP_CSS + "<div class='v9-mx-legend'><b style='color:#132327'>Status per matrix</b>"
        + "".join(f"<span><i class='v9-mx v9-mx-{k}'></i>{_e(name)}</span>" for k, (bg, name) in _STRIP_STYLE.items())
        + "<span><i class='v9-mx v9-mx-none'>·</i>not in that matrix</span><span><i class='v9-mx v9-mx-sample v9-mx-changed'></i>changed by an edit in force</span>"
        + "<span style='flex-basis:100%'>Squares of each analysis, left to right: datatable, mwTab, untarg_data (only the files deposited); hover a square for the matrix. A Tier 1 sample alignment cell: "
        "all zeros in that datatable and absent from the mwTab of that analysis; the same sample ID can be measured in another analysis.</span></div>"
        + (_red_note("Tier 1 sample alignment rows are not samples: they are left out of every calculation",
                     "A Tier 1 sample alignment row is a row of the datatable whose values are all zero and whose sample ID is missing from the mwTab "
                     "of the same analysis: it holds no measurement. It must be left out of downstream ML; MERIT-ML already leaves it out of every "
                     "calculation on this page, and no edit makes it ML-eligible. The same sample ID can be measured in another analysis "
                     "(see the strip).", "font-size:.82rem;margin:0 0 8px") if cat_count.get("alignment") else "")
        + f"<div style='overflow:auto;max-height:640px;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:1100px'><tr><th style='{_TH}'>Sample ID</th>"
        f"<th style='{_TH}' title='What the deposit says about this sample ID, from its line in the sample table (SUBJECT_SAMPLE_FACTORS) of the first analysis where it occurs; the label on the position when it has no line there.'>Deposited evidence (subject, factors, why, additional data)</th>"
        f"<th style='{_TH}'>Status per matrix (as in Analytical QC){_strip_head(mats)}</th><th style='{_TH}'>Use as</th><th style='{_TH}'>Factor label (blank = as deposited)</th>"
        f"<th style='{_TH}'>Batch-like label (blank = MERIT's)</th><th style='{_TH}'>Subject (blank = deposited)</th></tr>"
        f"{_filter_row(cat_count)}{rows}</table></div>"
        "</div>")


@lru_cache(maxsize=64)
def _additional_by_sample(study_id: str, analysis_id: str) -> dict:
    """Additional sample data of every sample-table line of one analysis (mwTab text file, else JSON), raw-file fields left out
    (they are shown in the FAIR raw-data check)."""
    from merit import additional_sample_data as asd
    root = Path(os.environ.get("MERIT_MW_DUMP_ROOT", "/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/mw-dump-latest-18thSep-2026-version"))
    try:
        _, recs = asd.read(root / study_id / analysis_id / "mwtab", analysis_id)
    except Exception:
        return {}
    out: dict[str, list] = {}
    for r in recs:
        out.setdefault(str(r.get("sample", "")).strip(), [(k, v) for k, v in r.get("fields") or [] if not asd.RAW_FILE_KEY.match(k) and str(v).strip()])
    return out


def _sample_evidence(study_id: str, analysis_id: str, row: list) -> dict:
    """What the deposit says about one sample ID (its first position): subject, the factors the rule reads, why it has its kind, additional data."""
    factors = qb.rule_factors(row)
    kind, cur = qb._decision(study_id, row[sv.I_ID], factors)
    if cur is not None:
        why = (f"curated: {kind}-like ({qc_describe(cur)})" if kind != "sample" else f"keyword overruled by a curated correction ({qc_describe(cur)})")
    elif kind == "sample":
        why = "no QC or blank keyword"
    else:
        item = {"sample_id": row[sv.I_ID], "found_in": qb.found_where(study_id, row[sv.I_ID], factors)}
        why = "; ".join(("keywords " if len(w) > 1 else "keyword ") + ", ".join(f"“{x}”" for x in w) + (" in the sample ID" if place == "sample ID" else f" in {place}")
                        for place, w in qb.keywords_found(kind, item)) or item["found_in"]
    subject = str(row[sv.I_SUBJECT] or "").strip()
    return {"subject": "" if subject.casefold() in sv.NO_SUBJECT else subject, "factors": str(factors or "").strip(),
            "from_table": bool(row[sv.I_TABLE]), "why": why, "kind": kind,
            "additional": _additional_by_sample(study_id, analysis_id).get(str(row[sv.I_ID]).strip(), [])}


def qc_describe(row: dict) -> str:
    from merit.curated_corrections import describe
    return describe(row)


def _evidence_cell(ev: dict) -> str:
    lab = "font-size:.72rem;color:#51656a"
    def line(name, text, full=None):
        short = text if len(text) <= 160 else text[:159] + "…"
        return (f"<div style='font-size:.78rem;line-height:1.35'><span style='{lab}'>{name}</span> "
                f"<span title='{_e(full or text)}'>{_e(short)}</span></div>")
    out = ""
    if ev["subject"]:
        out += line("Subject ID", f"“{ev['subject']}”")
    out += line("Factors" + ("" if ev["from_table"] else " (label on the position; no sample-table line)"),
                " · ".join(p.strip() for p in ev["factors"].split("|") if p.strip()) or "none deposited")
    colour = "#132327" if ev["kind"] == "sample" else "#8a5a00"
    out += (f"<div style='font-size:.78rem;line-height:1.35'><span style='{lab}'>Why</span> <span style='color:{colour}'>{_e(ev['why'])}</span></div>")
    if ev["additional"]:
        text = " · ".join(f"{k} = {v}" for k, v in ev["additional"])
        out += line("Additional data", text)
    return f"<td class='v9-evidence' data-kind='{ev['kind']}' style='{_TD};min-width:260px;max-width:420px'>{out}</td>"


_CAT_LABELS = {k: n for k, n in _KIND_NAMES} | {"sample": "ML-eligible in at least one matrix", "mixed": _MIXED_NAME, "every": "ML-eligible in every matrix", "edited": "edited"}
_FILTER_TD = "padding:8px 10px;background:#f4f8f8;border-bottom:1px solid rgba(19,35,39,.12);vertical-align:top;font-size:.78rem;text-transform:none;letter-spacing:0;font-weight:400"
_CHIP = "display:inline-block;margin:2px 4px 2px 0;padding:3px 9px;border-radius:999px;cursor:pointer;user-select:none;white-space:nowrap;border:1px solid #0d6e6e"


def _filter_row(cat_count: collections.Counter) -> str:
    """Second header row of the editor: show or hide sample IDs by status (toggle chips with counts), group them by status,
    and set the Use as value of every row shown. Chips, not checkboxes, so the page's form styles cannot reshape them."""
    chips = "".join(
        f"<span class='v9-chip' data-cat='{k}' role='button' tabindex='0' aria-pressed='true' style='{_CHIP};background:#0d6e6e;color:#fff'>"
        f"{_e(_CAT_LABELS[k])} · {cat_count[k]:,}</span>" for k in _GROUP_ORDER + ("every", "edited") if cat_count.get(k) or k in ("every", "edited"))
    link = "cursor:pointer;color:#113e52;text-decoration:underline;margin-right:8px"
    return ("<tr class='v9-filter-row'>"
            f"<td style='{_FILTER_TD};font-weight:700;color:#51656a'>Show</td><td style='{_FILTER_TD}'></td>"
            f"<td style='{_FILTER_TD}'>{chips}<div style='margin-top:4px'><span class='v9-chip-all' role='button' tabindex='0' style='{link}'>all</span>"
            f"<span class='v9-chip-none' role='button' tabindex='0' style='{link}'>none</span>"
            f"<span class='v9-group-chip' role='button' tabindex='0' aria-pressed='false' style='{_CHIP};background:#fff;color:#0d6e6e'>group rows by status</span></div>"
            "<div style='color:#51656a;margin-top:3px'>Shown when it holds a selected status in any matrix. Grouped: not ML-eligible anywhere, then ML-eligible in some matrices only, "
            "then ML-eligible everywhere. Edited: an edit in force or not applied yet.</div></td>"
            f"<td style='{_FILTER_TD}'><select class='v9-bulk' title='Sets the Use as value of every row shown now; press Apply sample edits to use them.' "
            "style='font:inherit;font-size:.78rem;padding:3px 6px;border-radius:8px;min-width:128px'><option value='__'>set all shown rows to…</option>"
            "<option value='dep'>as deposited</option><option value='1'>ML-eligible</option><option value='0'>not ML-eligible</option><option value='exclude'>leave out</option></select></td>"
            f"<td style='{_FILTER_TD}'></td><td style='{_FILTER_TD}'></td><td style='{_FILTER_TD}'></td></tr>")


EDITOR_SCRIPT = """
<script>
window.v9Pending = function(tr) {
  return tr.querySelector('.v9-status').value !== tr.getAttribute('data-init-status')
      || String(tr.querySelector('.v9-label').value || '').trim() !== tr.getAttribute('data-init-label')
      || String(tr.querySelector('.v9-batch').value || '').trim() !== tr.getAttribute('data-init-batch')
      || String(tr.querySelector('.v9-subject').value || '').trim() !== tr.getAttribute('data-init-subject');
};
window.v9SubjectOf = function(tr) {
  var v = String(tr.querySelector('.v9-subject').value || '').trim() || tr.getAttribute('data-dep-subject') || '';
  return ['', '-', 'na', 'n/a', 'nan', 'null', 'none', 'unknown', 'not applicable'].indexOf(v.toLowerCase()) === -1 ? v : '';
};
window.v9SubjectMap = function(ed) {
  var box = ed.querySelector('.v9-subject-map'); if (!box) return 0;
  var rows = ed.querySelectorAll('tr[data-v9-sample]'), by = {}, withSubject = 0;
  rows.forEach(function(tr) { var s = window.v9SubjectOf(tr); if (s) { withSubject++; by[s] = (by[s] || 0) + 1; } });
  var names = Object.keys(by), shared = names.filter(function(n) { return by[n] > 1; }).length;
  var top = names.sort(function(a, b) { return by[b] - by[a]; })[0];
  box.innerHTML = rows.length + ' sample IDs → <b>' + names.length + ' subjects</b> (' + withSubject + ' sample IDs have a subject; ' + shared
    + ' subjects have several sample IDs' + (top && by[top] > 1 ? '; most shared: “' + top.replace(/</g, '&lt;') + '” on ' + by[top] : '') + ')'
    + (top && by[top] > rows.length / 2 ? ' · <b style="color:#8a5a00">one value on more than half of the sample IDs: probably a group or sample type, not a subject</b>' : '');
  var c = ed.querySelector('.v9-subjects-confirm');
  return (c && String(+c.checked) !== c.getAttribute('data-init')) ? 1 : 0;
};
window.v9EditorRefresh = function(ed) {
  var q = String((ed.querySelector('.v9-filter') || {}).value || '').toLowerCase();
  var on = {};
  ed.querySelectorAll('.v9-chip[data-cat]').forEach(function(c) { on[c.getAttribute('data-cat')] = c.getAttribute('aria-pressed') === 'true'; });
  var rows = Array.prototype.slice.call(ed.querySelectorAll('tr[data-v9-sample]'));
  var shown = 0, elig = 0, dep = 0, pending = 0;
  rows.forEach(function(tr) {
    var cats = (tr.getAttribute('data-cats') || '').split(' ').filter(Boolean);
    var isPending = window.v9Pending(tr);
    if (isPending && cats.indexOf('edited') === -1) cats.push('edited');
    var visible = cats.some(function(c) { return on[c]; }) && tr.getAttribute('data-v9-sample').toLowerCase().indexOf(q) !== -1;
    tr.style.display = visible ? '' : 'none';
    if (visible) shown++;
    var s = tr.querySelector('.v9-status').value;
    if (s === '1' ? tr.getAttribute('data-measured') === '1' : s === '' && tr.getAttribute('data-elig-dep') === '1') elig++;
    if (tr.getAttribute('data-elig-dep') === '1') dep++;
    if (isPending) pending++;
  });
  pending += window.v9SubjectMap(ed);
  var group = ed.querySelector('.v9-group-chip');
  var grouped = !!(group && group.getAttribute('aria-pressed') === 'true');
  if (rows.length && ed.getAttribute('data-grouped') !== String(grouped)) {
    var body = rows[0].parentNode;
    rows.sort(function(a, b) {
      return (grouped ? (+a.getAttribute('data-group-rank')) - (+b.getAttribute('data-group-rank')) : 0)
          || (+a.getAttribute('data-index')) - (+b.getAttribute('data-index'));
    });
    rows.forEach(function(tr) { body.appendChild(tr); });
    ed.setAttribute('data-grouped', String(grouped));
  }
  var live = ed.querySelector('.v9-live');
  if (live) {
    live.innerHTML = 'ML-eligible sample IDs with these settings: <b>' + elig + '</b> (as deposited: ' + dep + ')'
      + (live.getAttribute('data-capped') === '1' ? ', among the listed sample IDs' : '') + ' · ' + shown + ' of ' + rows.length + ' rows shown'
      + (pending ? ' · <b style="color:#8a5a00">' + pending + ' change' + (pending === 1 ? '' : 's') + ' not applied yet</b>: press Apply sample edits to update every panel, score and download'
                 : ' · every panel, score and download uses the settings shown');
  }
};
window.v9FilterSamples = function(input) { window.v9EditorRefresh(input.closest('.v9-editor')); };
window.v9BulkStatus = function(sel) {
  var ed = sel.closest('.v9-editor');
  if (sel.value === '__') return;
  var value = sel.value === 'dep' ? '' : sel.value;
  ed.querySelectorAll('tr[data-v9-sample]').forEach(function(tr) {
    if (tr.style.display !== 'none') tr.querySelector('.v9-status').value = value;
  });
  sel.value = '__';
  window.v9EditorRefresh(ed);
};
(function() {
  function init() {
    document.querySelectorAll('.v9-editor').forEach(function(ed) {
      if (ed.getAttribute('data-ready')) return;
      ed.setAttribute('data-ready', '1');
      ed.addEventListener('change', function(ev) {
        if (ev.target.classList.contains('v9-bulk')) { window.v9BulkStatus(ev.target); } else { window.v9EditorRefresh(ed); }
      });
      ed.addEventListener('input', function(ev) { if (ev.target.classList.contains('v9-label') || ev.target.classList.contains('v9-batch') || ev.target.classList.contains('v9-subject')) window.v9EditorRefresh(ed); });
      function press(el, on) {
        el.setAttribute('aria-pressed', on ? 'true' : 'false');
        el.style.background = on ? '#0d6e6e' : '#fff'; el.style.color = on ? '#fff' : '#0d6e6e';
      }
      function act(t) {
        if (t.classList.contains('v9-chip') || t.classList.contains('v9-group-chip')) { press(t, t.getAttribute('aria-pressed') !== 'true'); }
        else if (t.classList.contains('v9-chip-all') || t.classList.contains('v9-chip-none')) {
          var all = t.classList.contains('v9-chip-all');
          ed.querySelectorAll('.v9-chip[data-cat]').forEach(function(c) { press(c, all); });
        } else if (t.classList.contains('v9-subject-own') || t.classList.contains('v9-subject-none') || t.classList.contains('v9-subject-dep')) {
          ed.querySelectorAll('tr[data-v9-sample]').forEach(function(tr) {
            tr.querySelector('.v9-subject').value = t.classList.contains('v9-subject-own') ? tr.getAttribute('data-v9-sample') : t.classList.contains('v9-subject-none') ? '-' : '';
          });
        } else { return false; }
        window.v9EditorRefresh(ed); return true;
      }
      ed.addEventListener('click', function(ev) { if (act(ev.target)) ev.preventDefault(); });
      ed.addEventListener('keydown', function(ev) { if ((ev.key === 'Enter' || ev.key === ' ') && act(ev.target)) ev.preventDefault(); });
      window.v9EditorRefresh(ed);
    });
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); } else { init(); }
})();
window.v9SubmitSampleEdits = function(edits, confirmed) {
  var field = document.getElementById('matrix-overrides-field');
  var form = document.getElementById('run-form');
  if (!field || !form) { alert('The sample edits could not be submitted on this page.'); return; }
  field.value = JSON.stringify(edits);
  var conf = document.getElementById('subjects-confirmed-field');
  if (conf) conf.value = confirmed ? '1' : '';
  if (form.requestSubmit) { form.requestSubmit(); } else { form.submit(); }
};
window.v9ApplySampleEdits = function(button) {
  var field = document.getElementById('matrix-overrides-field');
  var edits = {};
  try { edits = JSON.parse((field && field.value) || '{}') || {}; } catch (e) { edits = {}; }
  button.closest('.v9-editor').querySelectorAll('tr[data-v9-sample]').forEach(function(tr) {
    var id = tr.getAttribute('data-v9-sample');
    var status = tr.querySelector('.v9-status').value;
    var label = String(tr.querySelector('.v9-label').value || '').trim();
    var batch = String(tr.querySelector('.v9-batch').value || '').trim();
    var subject = String(tr.querySelector('.v9-subject').value || '').trim();
    var edit = {};
    if (status === '1') edit.eligible = true;
    if (status === '0') edit.eligible = false;
    if (status === 'exclude') edit.excluded = true;
    if (label) edit.label = label;
    if (batch) edit.batch = batch;
    if (subject) edit.subject = subject;
    if (Object.keys(edit).length) { edits[id] = edit; } else { delete edits[id]; }
  });
  var c = button.closest('.v9-editor').querySelector('.v9-subjects-confirm');
  window.v9SubmitSampleEdits(edits, !!(c && c.checked));
};
window.v9ClearSampleEdits = function(button) { window.v9SubmitSampleEdits({}, false); };
</script>
"""


def derived_inputs_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    """Derived ML Assessment Inputs: every analysis x endpoint with what the matrix holds and its own download."""
    study = _study(study_id, params, overrides)
    if study is None:
        return f"<div style='{_DASHED}'>No matrix list for this study.</div>"
    rows = ""; n_valid = 0
    for a in study["analyses"]:
        for key, label in SOURCES:
            ep = a["endpoints"][key]
            if "m" not in ep:
                text = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
                rows += (f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td><td style='{_TD}'>{label}</td>"
                         f"<td style='{_TD};color:#51656a;font-style:italic' colspan='8'>{_e(text or 'not available')}</td></tr>")
                continue
            m = ep["m"]; n_valid += 1
            num = lambda v: f"<td style='{_TD};text-align:right;font-variant-numeric:tabular-nums'>{v:,}</td>"
            edited = f" <span style='color:{_RED}'>(edited)</span>" if m.get("edited") else ""
            rows += (f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td><td style='{_TD}'>{label}{edited}</td>"
                     + num(m["positions"]) + num(m["tier1_alignment_rows"]) + num(m["qc_blank_positions"]) + num(m["eligible_positions"])
                     + num(int(m.get("n_features") or 0)) + num(m["value_cells"])
                     + f"<td style='{_TD};text-align:right;white-space:nowrap'>{_pct(m.get('structural'))}</td>"
                     f"<td style='{_TD}'><form method='post' action='/download/v9-matrix' style='margin:0' "
                     "onsubmit=\"this.matrix_overrides.value=(document.getElementById('matrix-overrides-field')||{}).value||'{}'\">"
                     f"<input type='hidden' name='study_id' value='{_e(study['study_id'])}'><input type='hidden' name='analysis_id' value='{_e(a['analysis_id'])}'>"
                     f"<input type='hidden' name='endpoint' value='{key}'><input type='hidden' name='matrix_overrides' value='{{}}'>"
                     f"<input type='hidden' name='structural_sample_support_n' value='{float((params or {}).get('structural_sample_support_n', 20)):g}'>"
                     "<button type='submit' class='v2-apply' style='padding:5px 10px;border-radius:10px;font-size:.78rem;white-space:nowrap'>Download</button></form>"
                     f"<div style='font-size:.72rem;color:#51656a;overflow-wrap:anywhere'>{_e(m.get('file', ''))}</div></td></tr>")
    return (
        "<h4 style='margin:0 0 8px;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em'>Derived ML Assessment Inputs · every analysis × endpoint</h4>"
        f"<p style='font-size:.88rem;margin:0 0 10px'>All {n_valid} valid matrices of this study are listed; no endpoint is preferred. Each Download gives a ZIP for that one matrix: "
        "a table with one row per sample (sample ID, factor label, whether it is ML-eligible and why not, subject ID, then the values exactly as deposited) "
        "and a manifest with the source file, its SHA-256, the counts, the Structural items, the parameters and any sample edits in force. "
        "The values are the deposited Metabolomics Workbench values, not transformed.</p>"
        f"<div style='overflow-x:auto'><table style='border-collapse:collapse;width:100%;min-width:980px'><tr><th style='{_TH}'>Analysis</th><th style='{_TH}'>Endpoint</th>"
        f"<th style='{_TH};text-align:right'>Positions</th><th style='{_TH};text-align:right'>Tier 1 sample alignment rows</th><th style='{_TH};text-align:right'>QC-, blank- or standard-like, or not eligible</th>"
        f"<th style='{_TH};text-align:right'>ML-eligible</th><th style='{_TH};text-align:right'>Features</th><th style='{_TH};text-align:right'>Value cells (ML-eligible)</th>"
        f"<th style='{_TH};text-align:right'>Structural</th><th style='{_TH}'>File</th></tr>{rows}</table></div>"
        f"<p style='font-size:.8rem;color:#51656a;margin:8px 0 0'>{_e(POSITION_NOTE)}</p>")


# ------------------------------------------------------------------ Metadata and FAIR reusability (copied from v8)
FAIR_METRICS = (
    ("fair_study_metadata_compliance", "Study metadata", "study",
     "Seven yes/no checks on the study record: DOI, linked publication, funding, contributors, project type, a project or study summary of at "
     "least 20 words, and raw data documented (on the study page, or a data format or raw file name in the mwTab file). Score = checks passed ÷ 7, "
     "the same for every analysis."),
    ("fair_metabolite_identifier_resolvability", "RefMet mapping", "analysis",
     "Share of the analysis's metabolite records that carry a RefMet name, as assigned by Metabolomics Workbench (MERIT-ML does no mapping of its "
     "own). Standard names make the metabolites findable and comparable across studies. Not assessed without metabolite records."),
    ("external_cross_references_via_refmet", "External IDs", "analysis",
     "Of the records with a RefMet name, the share that RefMet links to ChEBI, InChIKey, PubChem, HMDB, KEGG or LIPID MAPS (links from RefMet, "
     "not the deposit). Not assessed without a RefMet name."),
    ("mass_rt_like_metadata_presence", "Mass/RT metadata", "analysis",
     "100 when the mwTab annotation table has a filled mass/m/z-like or retention-time/index-like field, else 0; these let features be matched "
     "across studies. Covers annotated, named metabolites only: the m/z and RT of unnamed untargeted features (results-file line, m/z–RT "
     "feature names) are shown but not counted. Not assessed when the annotation could not be read."),
    ("lc_method_context_presence", "LC-method context", "analysis",
     "LC analyses only: one third each for a reported column, both mobile phases and the gradient, from the dedicated chromatography fields (free "
     "text is not read). Retention times are comparable only with this context."),
)
_FAIR_CHECKS = (("f1_doi_registered", "DOI"), ("r1_2_linked_publication", "Publication"), ("r1_funding_source_declared", "Funding source"),
                ("r1_contributors_listed", "Contributors"), ("r1_study_type_declared", "Project type"), ("f2_substantive_description", "Description"),
                ("a1_1_raw_data_format_recorded", "Raw data"))


def _fair_check_value(key: str, ev: dict) -> str:
    if key == "f1_doi_registered": return _e(str(ev.get("doi") or "not given"))
    if key == "r1_2_linked_publication": return _e(str(ev.get("publications") or "not given")[:110])
    if key == "r1_funding_source_declared": return _e(str(ev.get("funding_source") or "not given")[:110])
    if key == "r1_contributors_listed": return _e(str(ev.get("contributors") or "not given")[:110])
    if key == "r1_study_type_declared": return _e(str(ev.get("project_type") or "not given"))
    if key == "f2_substantive_description":
        if ev.get("project_summary_words") is None:
            return f"{ev.get('description_words', 0)} words"
        return f"project summary {ev['project_summary_words']} words · study summary {ev.get('study_summary_words')} words (one needs 20)"
    web = ev.get("raw_data_webpage") or {}
    files = ev.get("raw_file_evidence") or {}
    page = (f"study page: raw data available {_e(web.get('raw_data_available') or 'not stated')}; file types {_e(web.get('raw_data_file_types') or 'not stated')}"
            + (f"; uploaded size {_e(web['uploaded_file_size'])}" if web.get("uploaded_file_size") else "")) if web else "study page: not read"
    mwtab = (f"mwTab files: data format in {files.get('n_analyses_with_data_format', 0)}, raw file names in {files.get('n_analyses_with_raw_file_names', 0)} "
             f"of {files.get('n_analyses', 0)} analyses") if files else "mwTab files: " + (_e(", ".join(ev.get("raw_data_formats") or [])) or "nothing declared")
    return page + " · " + mwtab


def _fair_value(key: str, metric: dict, coordinates: dict | None = None) -> tuple[str, str]:
    """(score shown, evidence in words) of one metric of one analysis."""
    if not metric.get("assessable") or metric.get("score") is None:
        return "not assessed", _e(metric.get("reason") or "not assessable")
    note = ""
    if key == "fair_metabolite_identifier_resolvability":
        note = (f"{metric.get('n_valid')} of the {metric.get('n_records')} metabolite records that the repository lists for this analysis have a RefMet name "
                "(names assigned by Metabolomics Workbench; the repository's list, not the features of a matrix)")
    elif key == "external_cross_references_via_refmet":
        note = f"{metric.get('n_with_any')} of {metric.get('n_valid_records')} records with a RefMet name have an outside identifier"
    elif key == "mass_rt_like_metadata_presence":
        category = str(metric.get("category") or "")
        what = {"both": "a mass or m/z-like field and an RT or RI-like field", "mass/mz only": "a mass or m/z-like field only", "rt/ri only": "an RT or RI-like field only"}.get(category.lower())
        note = (f"the annotation table of this analysis reports {what}" if metric.get("present") and what else
                ("the annotation table of this analysis reports such a field" if metric.get("present") else
                 ("this analysis has no metabolite annotation table in its mwTab file" if not metric.get("annotation_blocks_read") else "the annotation table of this analysis has no such field")
                 + _unnamed_coordinates_text(coordinates)))
    elif key == "lc_method_context_presence":
        mark = lambda v: "✓" if v else "✗"
        note = f"column {mark(metric.get('column'))} · mobile phases {mark(metric.get('mobile_phase'))} · gradient {mark(metric.get('gradient'))}"
    elif key == "fair_study_metadata_compliance":
        note = "study record, the same for every analysis of the study"
    return _pct(metric["score"]), note


@lru_cache(maxsize=24)
def _fair_evidence(study_id: str) -> dict:
    """Deposited values behind External IDs, Mass/RT metadata and LC-method context, per analysis (display only; built by
    scripts/add_fair_evidence_from_deposit.py with the scoring functions, which reproduce every stored score)."""
    path = V9_ROOT / "fair_evidence" / f"{str(study_id or '').strip().lower()}.json"
    return json.loads(path.read_text()) if path.is_file() else {}


_MASS_RT_CLASS = {"mz-like": "m/z-like", "mass-like": "mass-like", "rt-like": "RT/RI-like", "ambiguous": "mass or m/z, ambiguous"}
_LC_NAMES = {"column": "Column", "mobile_phase": "Mobile phases", "gradient": "Gradient / composition"}
_FAIR_VALUES_BOX = ("<div class='v9-fair-values' style='font-size:.76rem;color:#132327;margin-top:3px;padding:4px 8px;"
                    "border-left:2px solid rgba(13,110,110,.35);background:rgba(13,110,110,.04);overflow-wrap:anywhere'>{}</div>")


def _deposited(text, limit: int = 1000) -> str:
    t = str(text or "").strip()
    if not t:
        return "<i style='color:#51656a'>not reported</i>"
    return "“" + _e(t if len(t) <= limit else t[:limit]) + "”" + (f" <i style='color:#51656a'>(+{len(t) - limit:,} more characters)</i>" if len(t) > limit else "")


def _fair_values_html(key: str, metric: dict, ev: dict | None) -> str:
    """The deposited values that give the score of one metric; nothing here changes a score."""
    if not ev:
        return ""
    if key == "external_cross_references_via_refmet":
        x = ev.get("external_ids")
        if not x or not metric.get("assessable"):
            return ""
        n = x["n_valid_records"]
        items = "".join(f"<li><b>{_e(d['label'])}</b>: {d['n']:,} of {n:,}"
                        + (" (" + "; ".join(f"{_e(a)}: {_e(b)}" for a, b in d["examples"]) + ")" if d["examples"] else "") + "</li>"
                        for d in x["per_database"])
        tail = []
        for k, text in (("refmet_entry_without_external_id", "RefMet entry without any outside identifier"),
                        ("name_not_in_current_refmet", "RefMet name not in the RefMet list of 2026-09-27")):
            if x[k]["n"]:
                tail.append(f"{text}: {x[k]['n']:,} (" + "; ".join(_e(e) for e in x[k]["examples"]) + (" …" if x[k]["n"] > len(x[k]["examples"]) else "") + ")")
        return _FAIR_VALUES_BOX.format(
            f"Records with a RefMet name whose RefMet entry carries each identifier (RefMet identifier table of 2026-09-29; examples as RefMet name: identifier):"
            f"<ul style='margin:2px 0 0 16px;padding:0'>{items}</ul>" + ("<div>" + " · ".join(tail) + "</div>" if tail else ""))
    if key == "mass_rt_like_metadata_presence":
        x = ev.get("mass_rt")
        if not x or not metric.get("assessable") or not x.get("columns_read"):
            return ""
        counted = {f["name"] for f in x["fields"]}
        empty = x.get("named_like_but_empty") or []
        parts = [("Columns counted: " + " · ".join(f"<b>{_e(f['name'])}</b> ({_MASS_RT_CLASS.get(f['class'], _e(f['class']))}; first value “{_e(f['example'])}”)"
                                                for f in x["fields"])) if x["fields"] else "No annotation column is mass/m/z-like or RT/RI-like and holds a value."]
        if empty:
            parts.append("Named like a mass or RT column but every value empty: " + ", ".join(f"<b>{_e(c)}</b>" for c in empty))
        other = [c for c in x["columns_read"] if c not in counted and c not in empty]
        if other:
            parts.append("Other annotation columns read: " + ", ".join(_e(c) for c in other[:20]) + (f" (+{len(other) - 20} more)" if len(other) > 20 else ""))
        return _FAIR_VALUES_BOX.format("<br>".join(parts))
    if key == "lc_method_context_presence":
        x = ev.get("lc")
        if not x:
            return ""
        head = f"Chromatography type (CH:CHROMATOGRAPHY_TYPE): {_deposited(x['chromatography_type'])}"
        if not metric.get("assessable"):
            return _FAIR_VALUES_BOX.format(head + " · not an LC type, so the three LC items are not scored")
        from merit import manuscript_coordinate_context as _cc
        lines = ""
        for it in x["items"]:
            mark = "<span style='color:#1a7a4a;font-weight:800'>✓</span>" if it["counts"] else f"<span style='color:{_RED};font-weight:800'>✗</span>"
            vals = " · ".join(f"CH:{f} {_deposited(v)}" for f, v in it["fields"].items())
            why = ""
            if not it["counts"]:
                if it["key"] == "gradient":
                    why = it.get("why_not") or ""
                else:
                    why = ", ".join(f"CH:{f} " + ("not reported" if not str(v).strip() else "a placeholder") for f, v in it["fields"].items() if not _cc.reported(v))
            lines += f"<div>{mark} {_LC_NAMES[it['key']]}: {vals}" + (f" <span style='color:#8a5a00'>(not counted: {_e(why)})</span>" if why and why != "not reported" else "") + "</div>"
        return _FAIR_VALUES_BOX.format(f"<div>{head}</div>{lines}")
    return ""


def _fair_items_table(m: dict, ev: dict | None = None, coordinates: dict | None = None) -> str:
    rows = ""
    for key, label, scope, tip in FAIR_METRICS:
        metric = (m.get("metrics") or {}).get(key) or {}
        shown, note = _fair_value(key, metric, coordinates)
        rows += (f"<tr><td style='padding:4px 8px 4px 0;vertical-align:top'><span title='{_e(tip)}' style='border-bottom:1px dotted #51656a;cursor:help'>{_e(label)}</span> "
                 f"<span style='font-size:.72rem;color:#51656a'>({scope} level)</span><div style='font-size:.76rem;color:#51656a'>{note}</div>{_fair_values_html(key, metric, ev)}</td>"
                 f"<td class='v9-fair-value' style='padding:4px 0 4px 10px;text-align:right;vertical-align:top;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap'>{shown}</td></tr>")
    return f"<table style='border-collapse:collapse;font-size:.82rem;margin-top:6px;width:100%'>{rows}</table>"


# plain names of the feature-label classes that are not a named metabolite (manuscript label classifier)
_LABEL_CLASS_WORDS = {
    "class_level": "name of a group of compounds", "tentative": "tentative name", "formula": "formula only",
    "mz_rt": "m/z–RT pair", "mz_only": "bare m/z value", "nmr_bin": "NMR bin", "feature_id": "generic feature ID", "unknown": "unknown or placeholder",
    "unresolved_identifier": "identifier that could not be resolved", "sample_metadata": "sample metadata, not a feature", "excluded_empty": "empty label",
    "non_metabolite": "protein entry", "malformed_matrix": "label of a malformed matrix",
}
_NAMED_CLASSES = ("named", "named_by_refmet", "resolved_identifier")


def _lookup_note(specific: dict) -> str:
    """Says when feature names fail the lookup only because the matrix writes '_' where the records write ','."""
    d = specific.get("feature_name_lookup_in_repository_records") or {}
    extra = (d.get("found_when_underscore_is_read_as_comma") or 0) - (d.get("found_with_the_same_spelling") or 0)
    if extra <= 0:
        return ""
    return _red_note(f"Spelling difference: {extra:,} more feature name{'s are' if extra != 1 else ' is'} found when \u201c_\u201d is read as \u201c,\u201d",
                     f"{d['found_when_underscore_is_read_as_comma']:,} of {d['feature_names']:,} feature names instead of {d['found_with_the_same_spelling']:,}. "
                     "This matrix writes \u201c_\u201d where the repository's records write \u201c,\u201d, so the low figure reflects the spelling, not missing RefMet names.", "margin-top:3px")


def _fair_endpoint_note(ep_meta: dict | None, n_features: int | None = None) -> str:
    specific = (ep_meta or {}).get("endpoint_specific") or {}
    e = specific.get("refmet_names_among_named_features")
    if not e:
        return ""
    head = f"<div style='{_SMALL}'><b>Specific to this endpoint · shown, not scored</b>"
    total = f"This matrix has {int(n_features):,} features. " if n_features is not None else ""
    classes = specific.get("feature_label_classes") or {}
    # every kind of label that is not a named metabolite, taken from the stored counts: the wording does not depend on which kinds occur
    rows = ""; n_other = 0
    for key, count in sorted((classes.get("class_counts") or {}).items(), key=lambda kv: -kv[1]):
        if key in _NAMED_CLASSES or not count:
            continue
        n_other += count
        examples = [str(x)[:40] + ("…" if len(str(x)) > 40 else "") for x in ((classes.get("class_examples") or {}).get(key) or [])[:3]]
        rows += (f"<tr><td style='padding:2px 8px 2px 0'>{_e(_LABEL_CLASS_WORDS.get(key, key.replace('_', ' ')))}</td>"
                 f"<td style='padding:2px 8px 2px 0;text-align:right'>{count:,}</td><td style='padding:2px 0;overflow-wrap:anywhere'>{_e(', '.join(examples))}</td></tr>")
    others = ""
    if n_other:
        others = (f" The other {n_other:,} feature{'s' if n_other != 1 else ''} carr{'y' if n_other != 1 else 'ies'} a label that is not the name of one metabolite."
                  "<details style='margin:4px 0'><summary style='cursor:pointer;color:#113e52'>kinds of label and examples</summary>"
                  f"<table style='border-collapse:collapse;font-size:.76rem;margin-top:4px'><tr><td style='padding:2px 8px 2px 0'><b>Kind of label</b></td>"
                  f"<td style='padding:2px 8px 2px 0;text-align:right'><b>Features</b></td><td><b>Examples (up to three)</b></td></tr>{rows}</table></details>")
    if e.get("share") is None:
        return head + f"<div>{total}None of them is a named metabolite, so RefMet names among its features cannot be counted.{others}</div></div>"
    named = e["named_features"]
    counts = classes.get("class_counts") or {}
    eligible = sum(v for k, v in counts.items() if k not in ("excluded_empty", "sample_metadata", "non_metabolite", "malformed_matrix"))
    coverage = (f" (<span class='v9-named-coverage'>{_pct(named / eligible)}</span>% of the {eligible:,} features that count as metabolite features)" if eligible else "")
    return (head + f"<div>{total}{named:,} of them are named metabolites{coverage}.{others} "
            f"{e['named_features_with_refmet_name']:,} of the {named:,} named features are found, under exactly the same spelling, in the repository's metabolite records "
            f"of this analysis with a RefMet name (<span class='v9-fair-endpoint-share'>{_pct(e['share'])}</span>%). "
            f"MERIT-ML only looks the feature name up in those records; it does not assign RefMet names.{_lookup_note(specific)}</div></div>")


def metadata_panel(study_id: str) -> str:
    """Metadata and FAIR reusability: the study record, then one row per analysis (there is nothing endpoint-specific in this section)."""
    study = _study(study_id)
    block = (study or {}).get("study_metadata")
    if study is None or not block:
        return f"<div style='{_DASHED}'>No Metadata and FAIR results for this study.</div>"
    defs = "".join(f"<li><b>{_e(label)}</b> ({scope} level). {_e(tip)}</li>" for _, label, scope, tip in FAIR_METRICS)
    body = (
        "<p style='margin:8px 0'>The FAIR score of an analysis is the mean of its assessed metrics (0–100); a metric not assessed is left out, not counted as zero. "
        "It summarises documentation for reuse only: it does not enter the readiness profile, and its value depends on which metrics could be assessed "
        "(one to five), which is shown beside each score.</p>"
        f"<ol style='margin:0 0 8px 18px;padding:0'>{defs}</ol>"
        "<p style='margin:0'><b>One score per analysis.</b> Nothing here is read from measurement values, so every endpoint of an analysis has the "
        "same result. An analysis without a valid matrix is scored on the metrics that can be assessed.</p>")
    meta = block["fair_study_metadata_compliance"]; ev = meta.get("field_evidence") or {}
    checks = "".join(
        f"<tr><td style='{_TD};white-space:nowrap'>" + ("<span style='color:#1a7a4a;font-weight:800'>✓</span>" if meta["checks"].get(key) else f"<span style='color:{_RED};font-weight:800'>✗</span>")
        + f" {label}</td><td style='{_TD};overflow-wrap:anywhere'>{_fair_check_value(key, ev)}</td></tr>" for key, label in _FAIR_CHECKS)
    rows = ""
    evidence = _fair_evidence(study["study_id"])
    for a in study["analyses"]:
        m = a.get("metadata") or {}
        values = ""
        for key, _, _, _ in FAIR_METRICS[1:]:
            shown, _note = _fair_value(key, (m.get("metrics") or {}).get(key) or {})
            style = f"{_TD};text-align:right;white-space:nowrap" + (";color:#51656a;font-style:italic" if shown == "not assessed" else ";font-variant-numeric:tabular-nums")
            values += f"<td class='v9-fair-col' style='{style}'>{shown}</td>"
        rows += (f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>"
                 f"<td style='{_TD};min-width:300px'><span style='font-weight:700;font-size:1.05rem;white-space:nowrap'>{_pct(m.get('score'))}</span>{_bar(m.get('score'))}"
                 f"<div style='font-size:.74rem;color:#51656a;margin-top:2px'>mean of {m.get('n_assessable_metrics', 0)} assessed metrics</div>"
                 "<details style='margin-top:6px'><summary style='cursor:pointer;color:#113e52;font-size:.8rem'>scored metrics and evidence</summary>"
                 f"{_fair_items_table(m, evidence.get(a['analysis_id']), a.get('unnamed_coordinates'))}</details></td>{values}</tr>")
    scores = [a["metadata"]["score"] for a in study["analyses"] if a.get("metadata") and a["metadata"]["score"] is not None]
    return (
        "<p style='font-size:.88rem;margin:0 0 10px'>How well the study and each analysis are documented for reuse. There is one FAIR score per analysis. "
        "It is reported on its own and does not enter the readiness profile. Between one and five metrics can be assessed for an analysis, "
        "and the number assessed is shown beside each score, so analyses with different numbers of assessed metrics should not be ranked "
        "by this score alone.</p>"
        + _descriptions(body)
        + "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Study level · the same for every analysis</h4>"
        f"<div style='overflow-x:auto;margin-bottom:16px'><table style='border-collapse:collapse;width:100%'><tr><th style='{_TH}'>Study metadata · {meta['passed']} of {meta['total']} checks passed ({_pct(meta['score'])})</th>"
        f"<th style='{_TH}'>As deposited</th></tr>{checks}</table></div>"
        "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Across analyses</h4>"
        f"<p style='font-size:.88rem;margin:0 0 16px'>FAIR score of {len(scores)} analys{'e' if len(scores) != 1 else 'i'}s: {_summary(scores)}</p>"
        "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Every analysis</h4>"
        f"<div style='overflow-x:auto'><table class='v9-fair-table' style='border-collapse:collapse;width:100%;min-width:900px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
        f"<th style='{_TH};white-space:nowrap'>FAIR score</th>"
        + "".join(f"<th style='{_TH};text-align:right' title='{_e(tip)}'>{_e(label)}</th>" for _, label, _, tip in FAIR_METRICS[1:])
        + f"</tr>{rows}</table></div>")


def export_metadata_block(study_id: str) -> dict | None:
    study = _study(study_id)
    if study is None or not study.get("study_metadata"):
        return None
    return {"schema": "merit.metadata_fair_per_analysis.pilot.v1",
            "source": "study record, the repository's metabolite records and the mwTab file of each analysis; one record per analysis, none per endpoint",
            "metrics": {key: {"name": label, "level": scope, "definition": tip} for key, label, scope, tip in FAIR_METRICS},
            "analysis_score": "mean of the assessed metrics of the analysis (0-1)",
            "study_record": study["study_metadata"],
            "analyses": [{"analysis_id": a["analysis_id"], **(a.get("metadata") or {})} for a in study["analyses"]]}


# ---------------------------------------------------------------------------
# Analytical QC: two informational blocks, one record per analysis (not per endpoint), copied from the stored results
# ---------------------------------------------------------------------------
def _analytical(a: dict) -> dict:
    return (a.get("sections") or {}).get("analytical") or {}


@lru_cache(maxsize=24)
def design_context(study_id: str) -> dict:
    """Study-design context of the study (display only; built by scripts/add_design_context_from_v11.py from the frozen labels)."""
    path = V9_ROOT / "design_context" / f"{str(study_id or '').strip().lower()}.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def acquisition_context(study_id: str) -> dict | None:
    """Platform and acquisition evidence of every analysis of the study, in the form the evidence block renders."""
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    records = [_analytical(a)["acquisition_evidence"] for a in raw["analyses"] if _analytical(a).get("acquisition_evidence")]
    if not records:
        return None
    return {**(((raw.get("study_sections") or {}).get("analytical") or {}).get("acquisition_context") or {}), "study_id": sid, "records": records}


def data_summary_rows(study_id: str) -> list[dict]:
    """One row per analysis for the Per-Analysis Data Summary: the stored fields, plus the endpoints that hold a valid matrix."""
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    rows = []
    for a in (raw or {}).get("analyses") or []:
        summary = _analytical(a).get("data_summary")
        valid = [key for key, _ in SOURCES if (a["endpoints"].get(key) or {}).get("availability") == "valid"]
        row = {**(summary or {"analysis_id": a["analysis_id"]}), "_no_summary": summary is None, "_valid_endpoints": valid}
        prov = _analytical(a).get("data_summary_provenance")
        if summary is not None and prov:
            row["_type_note"] = _type_note(prov.get("analysis_type_source"), summary.get("analysis_type"))
            row["_units_title"] = _units_title(prov)
            row["_units_note"] = "placeholder, not a unit" if prov.get("units_is_placeholder") else ""
            row["_results_lines"] = _results_lines(prov.get("results_file_declarations") or [])
            row["_format_notes"] = {field: _format_note(d) for field, d in (prov.get("json_text_differences") or {}).items()}
            row["_metadata_format"] = "json" if prov.get("metadata_read_from") == "mwTab JSON" else "txt"
        # predicted analyser class: only the two classes of the acquisition evidence; anything else stays empty
        cls = ((_analytical(a).get("acquisition_evidence") or {}).get("resolution_class") or "").strip()
        row["_analyser_class"] = cls if cls in ("HRMS-capable", "Nominal/unit-resolution") else ""
        rows.append(row)
    return rows


def _type_note(source: str | None, shown_type) -> str:
    """Shown under the analysis type only when the type is not the ANALYSIS_TYPE field of the mwTab text file of the analysis."""
    if source == "mwtab_json_only":
        return "from the mwTab JSON; this analysis has no mwTab text file"
    if source == "mwtab_section_present":
        return f"ANALYSIS_TYPE not deposited; named after the {shown_type} section of the mwTab file"
    if source == "study_level_no_mwtab_file":
        return "no mwTab file for this analysis; type declared by the other analyses of the study"
    return ""


def _units_title(prov: dict) -> str:
    """Hover text of the Units cell: where the units were read."""
    source = prov.get("units_source")
    if not source:
        return "No units declared for an mwTab data block."
    text = f"Units declared for the mwTab data block: {source}, read from the {prov.get('units_read_from') or 'mwTab file'}"
    if prov.get("units_read_from") == "mwTab JSON" and not prov.get("mwtab_text_file"):
        text += " (this analysis has no mwTab text file)"
    text += ". They describe that block."
    if prov.get("units_is_placeholder"):
        text += " The deposited text is a placeholder, not a unit; it is shown as deposited."
    return text


def _results_lines(declarations: list[dict]) -> list[dict]:
    """Visible lines for the results-file declarations of the mwTab text file: one per distinct declaration that states units."""
    out = []
    for d in declarations:
        where = f"{d['line_key']} line of the mwTab text file (line {d['first_line_number']}" + (f", written {d['times_in_file']} times" if d["times_in_file"] > 1 else "") + ")"
        if d["units"]:
            out.append({"label": "results file", "value": d["units"], "note": "placeholder, not a unit" if d["units_is_placeholder"] else "",
                        "title": f"{where}. Referenced results file: {d['file_name_cell'] or 'not named'}. UNITS: {d['units']}. "
                                 "These units describe the referenced results file, not the mwTab data block."})
        elif d["units_written_inside_file_name_cell"]:
            out.append({"label": "results-file line", "value": d["file_name_cell"], "note": "UNITS written inside the file-name cell; shown as deposited",
                        "title": f"{where}. The line has no separate UNITS attribute; the file-name cell reads: {d['file_name_cell']}."})
    return out


def _format_note(difference: dict) -> str:
    """The value of the same field in the mwTab text file, when it is not what the JSON (the format read) states."""
    lines = difference.get("text_lines") or []
    if not lines:
        return "not in the mwTab text file"
    if len(lines) == 1:
        return f"mwTab text file: {lines[0]}"
    return f"mwTab text file, {len(lines)} lines: " + " | ".join(lines)


def _qc_repro_description(params: dict | None = None) -> str:
    c = qr.cutoff(params)
    return ("<p style='margin:8px 0'><b>QC reproducibility</b>, in Sample eligibility &amp; analytical controls under the QC-like count; "
            "<i>informational, not scored</i>. Measurement reproducibility across repeated injections of one QC material (the QC-like label with "
            "most injections; blank-like and standard-like positions are never used). Per feature: the detection rate (injections with a finite, "
            "non-zero value) and the RSD (SD ÷ mean of the detected values, as deposited). Headline: the <b>median QC RSD</b> over the features detected "
            f"in more than 70% of the injections ({SPAN_DIR}lower = better measurement reproducibility</span>). Details: features passing both "
            f"detection &gt; 70% and RSD &lt; {c:g}%, the acceptance criteria of Broadhurst D et al., <i>Metabolomics</i> 2018;14(6):72, "
            "<a href='https://doi.org/10.1007/s11306-018-1367-3' target='_blank' rel='noopener noreferrer' style='color:#0d6e6e'>doi:10.1007/s11306-018-1367-3</a> "
            "(“RSD … &lt; 20% … or &lt; 30% … depending on the sample type”; adjustable 10–40%). Shown only with at least 3 injections and linear "
            "declared units (a concentration; an intensity, area or count; or normalised, relative or ratio units) without negative values: an RSD is "
            "unchanged by multiplication by a constant but meaningless on log-scaled or centred values. Per-feature list in the download.</p>")


def _processing_description() -> str:
    return ("<p style='margin:8px 0'><b>Processing evidence (as deposited)</b>, last column of Sample eligibility &amp; analytical controls; "
            "<i>informational, not scored</i>, one value per analysis. In quotes, as deposited: the dedicated mwTab fields (software version, "
            "processing parameters file, analysis details, and the NMR processing fields), then software named anywhere in the analysis's metadata "
            "and any minFraction / minfrac wording; processing steps named are in Details. Processing choices change feature counts and missing "
            "values, so they matter when matrices are compared. A name is a mention, not proof that the tool produced the matrix, and a parameters "
            "file is named but not read. SAMPLEPREP:PROCESSING_METHOD is left out (it usually describes sample preparation). An amber note marks "
            "files that declare another study or analysis ID.</p>")


def _qc_blank_description(params: dict | None = None) -> str:
    return (
        "<p style='margin:12px 0 8px'><b>Sample eligibility &amp; analytical controls.</b> Positions that should not train a classifier: QC-like "
        "(pooled QC, reference and system-suitability samples), blank-like (solvent, process, extraction, method or reagent blanks) and standard-like "
        "(calibration standards, standard mixes). Counts and labels only, not scored; “-like” means recognised from deposited names, not verified. "
        "Every other metric uses the remaining, ML-eligible samples. Presence label: QC-like and blank-like, QC-like only, Blank-like only, or Neither identified.</p>"
        "<p style='margin:8px 0'><b>How sample IDs are recognised.</b> Once per sample ID, from its sample-table line (SUBJECT_SAMPLE_FACTORS; the "
        "label on its position when it has no line). The sample ID and the factor values, not the factor names, are searched for 16 keywords: "
        f"11 QC ({_e(', '.join(qb.KEYWORDS['qc']))}) and 5 blank ({_e(', '.join(qb.KEYWORDS['blank']))}); qc, nist, ltr and sst do not count "
        "directly after a letter. A keyword in the sample ID decides alone (a blank keyword gives blank-like, any other QC-like); otherwise the factor "
        "values decide the same way. Standard-like positions match no keyword and come only from the curated table. Tier 1 sample alignment rows are "
        "not measured positions. Details and Examples show where each keyword was found; the download lists every position.</p>"
        "<p style='margin:8px 0'><b>Sample table.</b> The cell first counts the sample IDs of the sample table by kind, then the measured "
        "positions, and names controls listed in the table but not measured in this matrix.</p>"
        "<p style='margin:8px 0'><b>Additional sample data.</b> The extra fields of each sample-table line (for example age, batch, run or injection "
        "number), each with its first two values, records with a value and distinct values (counts are records, not unique samples). Fields shown "
        "elsewhere (raw file names, factors, sample or subject ID) are left out; names and values are reported as deposited, nothing is inferred.</p>"
        f"<p style='margin:8px 0;color:{_RED}'><b>What it cannot tell.</b> A keyword can match a non-control (for example a tube type written "
        "SST) and miss a control without a keyword; either can be corrected in Adjust Matrix Properties. Checked exceptions (non-controls, blanks "
        "written Blk or BK, declared analytical standards) are applied from a curated table with their reasons, so counts here can differ from the "
        "manuscript's keyword rule. A count does not show where controls ran, and a deposit can omit measured controls.</p>"
        f"<p style='margin:8px 0;color:#51656a'>{_e(POSITION_NOTE)}</p>")


def analytical_descriptions(params: dict | None = None) -> str:
    body = (
            "<p style='margin:8px 0 4px'><b>Blocks on this page, in order</b></p>"
            "<ol style='margin:0 0 10px 18px;padding:0'>"
            "<li>Analytical platform and acquisition evidence: <i>informational, not scored</i> (<span class='v9-direction' style='color:#1f5fbf;font-weight:600'>no better or worse direction</span>)</li>"
            "<li>Per-analysis data summary: <i>informational, not scored</i> (<span class='v9-direction' style='color:#1f5fbf;font-weight:600'>no better or worse direction</span>)</li>"
            "<li>Sample eligibility &amp; analytical controls: <i>informational, not scored</i>, with QC reproducibility (median QC RSD; lower = better measurement reproducibility) (<span class='v9-direction' style='color:#1f5fbf;font-weight:600'>no better or worse direction</span>), with the Processing evidence column</li>"
            "<li>Missing entries and zeros: <b>scored</b>, <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>higher = a smaller share of cells in the largest category</span> (100 = no missing entry and no zero)</li>"
            "<li>Low-information features: <b>scored</b>, <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>higher = better for ML</span> (100 = no constant or near-zero-variance feature); CV caution not scored</li>"
            "<li>Redundant features (multicollinearity): <b>scored</b>, <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>higher = fewer features flagged as redundant</span> (100 = no pair of features above the cut-off)</li>"
            "<li>Outlier samples: <b>scored</b>, <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>higher = fewer sample medians outside the fences</span> (100 = no sample median outside the selected fences)</li>"
            "<li>Measurement scale: <b>scored</b> (units declared only), <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>100 = units declared, better for ML than 0; median and IQR have no better or worse value</span></li>"
            "<li>Sample annotations, MetaBatch-style filters: <i>informational, not scored</i>; nothing in it enters the readiness profile or the FAIR score (<span class='v9-direction' style='color:#1f5fbf;font-weight:600'>no better or worse direction</span>)</li>"
            "</ol>"
            "<p style='margin:8px 0'><b>Reading a score.</b> A score means nothing on its own: for every scored metric on this page, "
            "<span class='v9-direction' style='color:#1f5fbf;font-weight:600'><b>higher = less found by that metric</b> (100 = nothing found)</span>. What a finding means for a model depends on the model and the question: "
            "correlated features, for example, can coexist with strong prediction. Informational blocks have no score and "
            "<span class='v9-direction' style='color:#1f5fbf;font-weight:600'>no better or worse direction</span>: they report what was deposited.</p>"
            "<p style='margin:8px 0'><b>Analytical platform and acquisition evidence</b> (one record per analysis, the same on every endpoint). Five "
            "kinds of evidence, as found: mass-analyser capability, targeted/untargeted wording, scan-mode terms, repository marker and MS2 in linked "
            "raw files. Each is shown on its own; none is used to infer another or an acquisition type. It tells a user how comparable analyses are.</p>"
            "<p style='margin:8px 0'><b>Per-analysis data summary.</b> Analysis and MS type, ion mode, chromatography, instrument and units as "
            "deposited, with links to the repository files; where the mwTab JSON and text formats disagree, the row says so. Analyses without a valid "
            "matrix are listed too; NMR analyses also get their acquisition details.</p>"
            + _qc_blank_description(params) + _qc_repro_description(params) + _processing_description() + _missingness_description() + _low_information_description(params) + _redundancy_description(params) + _outliers_description(params) + _scale_description() + _annotations_description())
    return (analytical_cutoff_note(params) + _descriptions(body).replace("Score descriptions <span", "Descriptions <span").replace(
        "what each score measures and how it is calculated", "what each block shows").replace("<details style=", "<details class='v9-desc-analytical' style=", 1))


def analytical_cutoff_note(params: dict | None = None) -> str:
    """Analytical QC tab: the alert cut-offs the user set (none: nothing shown). The scores here do not change; the cut-offs decide the
    analytical alerts of the readiness profile on the Overview."""
    cus = rd.custom(params)
    if not cus:
        return ""
    names = dict(rd.ALERT_METRICS); ext, low = rd._reference_cutoffs(params, "extreme"), rd._reference_cutoffs(params, "low")
    items = "; ".join(f"{names[k].lower()}: {t} alert below {_lb_num(v)} (reference {(ext if t == 'extreme' else low)[k]:.1f})"
                      for (k, t), v in sorted(cus.items(), key=lambda kv: ([x for x, _ in rd.ALERT_METRICS].index(kv[0][0]), kv[0][1] != "extreme")))
    return (f"<div class='v9-cut-note' style='margin:0 0 12px;padding:7px 10px;border-left:3px solid #c98a00;background:rgba(201,138,0,.07);"
            f"font-size:.82rem;color:#132327'><b>Alert cut-offs set by you:</b> {_e(items)}. They decide the analytical alerts in the "
            "readiness profile on the Overview; the scores on this page do not change.</div>")


def analytical_pending() -> str:
    return (f"<div style='{_DASHED};margin-top:6px'><b>Other Analytical QC metrics</b><br>"
            "Not shown yet. They will be filled per analysis × endpoint.</div>")


# ---------------------------------------------------------------------------
# Analytical QC: QC / blank controls, one value per valid matrix (analysis x endpoint); information only
# ---------------------------------------------------------------------------
def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def _curated_note(corrections: list[dict] | None, section: str) -> str:
    """Visible note for positions whose QC/blank status a curated correction changed (removed or added)."""
    if not corrections:
        return ""
    groups: dict[tuple, int] = {}
    for c in corrections:
        key = (c["decision"], c["matched"], c["reason"])
        groups[key] = groups.get(key, 0) + 1
    def what(decision: str) -> str:
        if decision == "blank":
            return "are counted as blank-like, although no blank keyword matches them" if section == "qc" else "are counted as blank-like (not ML-eligible), although no blank keyword matches them"
        if decision == "standard":
            return "are counted as standard-like (analytical standards), although no keyword matches them" if section == "qc" else "are counted as standard-like (not ML-eligible), although no keyword matches them"
        if decision == "qc":
            return ("are counted as QC-like, although no keyword matches their sample ID or factors" if section == "qc"
                    else "are counted as QC-like (not ML-eligible), although no keyword matches their sample ID or factors")
        return "are not counted as QC-like or blank-like" if section == "qc" else "are counted as samples, not as QC-like or blank-like"
    return "".join(
        _red_note(f"Curated correction: {_plural(n, 'position')} matching {_e(matched)}", f"These positions {what(decision)}. {_e(reason)}",
                  "font-size:.78rem;margin-top:4px").replace("class='v9-red-note'", "class='v9-red-note v9-curated-note'", 1)
        for (decision, matched, reason), n in groups.items())


def _qc_repro(sid: str, ep: dict, overrides: dict | None, params: dict | None, features: bool = False) -> dict | None:
    st = ((ep.get("sections") or {}).get("analytical") or {}).get("qc_reproducibility")
    if not st:
        return None
    if not overrides and not params and not features:
        return st["default"]
    data = np.load(V9_ROOT / st["file"]) if st.get("file") else None
    return qr.compute(ep["roster"], st, data, sid, overrides, params, features)


def _qc_blank_study(study_id: str, overrides: dict | None = None, params: dict | None = None) -> list[dict] | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    out = []
    for a in raw["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            if "roster" not in ep:
                endpoints[key] = {"availability": ep.get("availability", "")}
                continue
            stored = ((ep.get("sections") or {}).get("analytical") or {}).get("qc_blank_controls") or {}
            q = qb.compute(ep["roster"], overrides, sid) if overrides else dict(stored)
            for k_ in ("sample_table_qc_like_ids_in_matrix", "sample_table_blank_like_ids_in_matrix", "curated_corrections"):
                if k_ in stored: q[k_] = stored[k_]
            table = _analytical(a).get("sample_table_controls") or {}
            measured = {row[sv.I_ID] for row in ep["roster"] if not row[sv.I_ALIGN]}
            tier1 = {row[sv.I_ID] for row in ep["roster"] if row[sv.I_ALIGN]}
            not_here = {kind: {"tier1": [i for i in table.get(f"{kind}_like_sample_ids") or [] if i not in measured and i in tier1],
                               "absent": [i for i in table.get(f"{kind}_like_sample_ids") or [] if i not in measured and i not in tier1]}
                        for kind in ("qc", "blank", "standard")}
            endpoints[key] = {"availability": "valid", "q": q, "repro": _qc_repro(sid, ep, overrides, params),
                              "lists": qb.listed(ep["roster"], overrides, sid), "not_here": not_here}
        rosters = [ep["roster"] for ep in a["endpoints"].values() if "roster" in ep]
        out.append({"analysis_id": a["analysis_id"], "endpoints": endpoints, "additional": _analytical(a).get("additional_sample_data") or {},
                    "sample_table": _analytical(a).get("sample_table_controls") or {},
                    "technical": sa.technical_additional_names(sid, a["analysis_id"], rosters, overrides),
                    "processing": _analytical(a).get("processing_evidence") or {}})
    return out


_CONTROL_NAMES = (("qc", "QC-like"), ("blank", "Blank-like"), ("standard", "Standard-like"))
_CONTROL_WORDS = {"qc": "QC-like", "blank": "blank-like", "standard": "standard-like"}     # inside a sentence


def _sample_table_line(table: dict) -> str:
    """The sample IDs of the analysis's sample table by kind (one kind per sample ID, measured or not): the first line of the cell."""
    if not table.get("records"):
        return "<div class='v9-sample-table-ids' style='font-size:.84rem;margin-top:4px;color:#51656a'><b>Sample table:</b> none read (no mwTab text file)</div>"
    parts = [f"<b>{len(table.get(f'{k}_like_sample_ids') or []):,}</b> {_CONTROL_WORDS[k]}" for k, _ in _CONTROL_NAMES if table.get(f"{k}_like_sample_ids")]
    return ("<div class='v9-sample-table-ids' style='font-size:.84rem;margin-top:4px'><b>Sample IDs in the sample table:</b> "
            + (" · ".join(parts) if parts else "no QC-like, blank-like or standard-like sample ID") + "</div>")


def _not_measured_line(not_here: dict | None) -> str:
    """Controls of the sample table of the analysis that have no measured position in this matrix (Tier 1 sample alignment rows, or
    sample IDs absent from the matrix); same per-sample-ID rule, so their kind is the same as everywhere else."""
    parts = []
    for kind, name in _CONTROL_NAMES:
        x = (not_here or {}).get(kind) or {}
        ids = list(x.get("tier1") or []) + list(x.get("absent") or [])
        if not ids:
            continue
        where = "; ".join(w for w in ((f"{len(x['tier1']):,} Tier 1 sample alignment rows" if x.get("tier1") else ""),
                                      (f"{len(x['absent']):,} not in this matrix" if x.get("absent") else "")) if w)
        shown = ", ".join(f"“{_e(i)}”" for i in ids[:3]) + (" …" if len(ids) > 3 else "")
        parts.append(f"{len(ids):,} {_CONTROL_WORDS[kind]} ({where}: {shown})")
    return (f"<div class='v9-not-measured' style='font-size:.78rem;color:#51656a;margin-top:4px'><b>Not measured here:</b> {'; '.join(parts)}</div>"
            if parts else "")
CONTROL_EXAMPLES = 5      # per kind in the cell; every position is in the on-demand download


def _control_example(kind: str, name: str, item: dict) -> str:
    """One listed position: the sample ID and what made it a control (the keyword and where it is, or the curated decision)."""
    found = str(item.get("found_in", ""))
    if found.startswith("curated:"):
        why = _e(found)
    else:
        parts = [("keywords " if len(w) > 1 else "keyword ") + ", ".join(f"“{_e(x)}”" for x in w)
                 + (" in the sample ID" if place == "sample ID" else f" in {_e(place)}") for place, w in qb.keywords_found(kind, item)]
        why = "; ".join(parts) or f"found in {_e(found)}"
    return f"{name}: “{_e(item['sample_id'])}” — {why}"


def _controls_box(lists: dict | None) -> str:
    """Details and Examples of the QC-like, blank-like and standard-like positions of one matrix (as in the other Analytical QC
    blocks); nothing is shown when there is none."""
    if not lists or not any(lists.get(k) for k, _ in _CONTROL_NAMES):
        return ""
    places = []
    for kind, name in _CONTROL_NAMES:
        items = lists.get(kind) or []
        if not items:
            continue
        found = [qb.keywords_found(kind, x) for x in items]          # the keywords that decide this kind (blank keywords for blank-like)
        sid = sum(1 for f in found if any(p == "sample ID" for p, _ in f))
        lab = sum(1 for f in found if any(p != "sample ID" for p, _ in f))
        cur = sum(1 for x in items if str(x["found_in"]).startswith("curated:"))
        word = "blank keyword" if kind == "blank" else "keyword"
        places.append(f"{name} {len(items):,}: {word} in the sample ID {sid:,} · in a label value {lab:,}" + (f" · curated {cur:,}" if cur else ""))
    det = ["Rule in force: one of the 16 keywords in the sample ID or in a value of the label on the position; a blank keyword makes it blank-like, "
           "any other keyword QC-like; standard-like positions and some blank-like positions come from the curated table (keywords and table: "
           "descriptions at the top of the page)",
           "Where the keyword was found (a position can count in both): " + " / ".join(places),
           "These positions are not ML-eligible: they are left out of every sample-level count and score",
           "Every position of every analysis and endpoint: “download the full list (JSON)” beside the heading"]
    ex = [_control_example(kind, name, x) for kind, name in _CONTROL_NAMES for x in (lists.get(kind) or [])[:CONTROL_EXAMPLES]]
    box = "font-size:.78rem;color:#51656a;margin-top:4px"
    return ("<div class='v9-controls-box' style='display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start'>"
            f"<details class='v9-controls-details' style='{box}'><summary style='cursor:pointer;color:#113e52'>Details</summary>"
            "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{x}</li>" for x in det) + "</ul></details>"
            f"<details class='v9-controls-examples v9-examples' style='{box}'><summary style='cursor:pointer;color:#113e52'>Examples</summary>"
            "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{x}</li>" for x in ex) + "</ul></details></div>")


_PRESENCE_COLOUR = {"QC-like and blank-like": "#1a7a4a", "QC-like only": "#8a5a00", "Blank-like only": "#8a5a00", "Neither identified": "#51656a"}


def _qc_repro_line(r: dict | None) -> str:
    """QC reproducibility under the QC-like count, only when it can be computed: the median QC RSD, one number for the whole run
    (Broadhurst et al. 2018). Not scored."""
    if not r or not r.get("shown"):
        return ""
    mat = r["material"] if len(r["material"]) <= 60 else r["material"][:59] + "…"
    med = (f"median RSD {r['median_rsd']:.1f}%" if r.get("median_rsd") is not None else "median RSD not computable (no feature detected in "
           f"&gt; {r['detection_pct']:g}% of the injections)")
    det = [f"Features passing: {r['passing']:,} of {r['features']:,} (detected in &gt; {r['detection_pct']:g}% of the injections and RSD &lt; {r['rsd_cutoff']:g}%)",
           f"Not passing: {r['failing_detection']:,} detected in {r['detection_pct']:g}% of the injections or fewer; {r['failing_rsd']:,} with RSD of {r['rsd_cutoff']:g}% or more",
           f"QC material: “{_e(r['material'])}” ({r['injections']:,} of the {r['qc_like_positions']:,} QC-like positions); blank-like and standard-like positions are not used",
           "Median RSD: the median, over the features detected in more than 70% of the injections, of each feature's RSD (SD ÷ mean of its "
           "detected values, as deposited). Criteria: Broadhurst et al. 2018, Metabolomics 14(6):72 (detection rate &gt; 70%, RSD &lt; 20% or "
           "&lt; 30% depending on the sample type)"]
    return (f"<div class='v9-qc-repro' data-median-rsd='{'' if r.get('median_rsd') is None else round(r['median_rsd'], 4)}' data-passing='{r['passing']}' "
            f"data-features='{r['features']}' style='font-size:.84rem;margin-top:4px'>"
            f"QC reproducibility: <b>{med}</b> · {SPAN_DIR}lower = better measurement reproducibility</span></div>"
            f"<div style='font-size:.76rem;color:#51656a'>across {r['injections']:,} injections of “{_e(mat)}”; features detected in &gt; {r['detection_pct']:g}% of them</div>"
            "<details style='font-size:.76rem;color:#51656a'><summary style='cursor:pointer;color:#113e52'>QC reproducibility details</summary>"
            "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{x}</li>" for x in det) + "</ul></details>")


def _qc_blank_cell(ep: dict, table: dict) -> str:
    if "q" not in ep:
        text = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
        return f"<td style='{_TD};color:#51656a;font-style:italic'>{_e(text)}</td>"
    q = ep["q"]
    notes = ""
    if q.get("edited_positions"):
        notes += _red_note(f"Sample edits applied: {q['edited_positions']} of {q['positions']} positions differ from the deposit",
                           "; ".join(x for x in ((f"{_plural(q['keyword_positions_marked_eligible'], 'position')} with a keyword counted as sample" if q["keyword_positions_marked_eligible"] else ""),
                                                 (f"{_plural(q['set_aside_positions'], 'position')} marked not ML-eligible without a keyword, counted as neither sample nor control" if q["set_aside_positions"] else "")) if x)
                           + ".", "font-size:.78rem;margin-top:4px")
    tier1 = (f"<div style='font-size:.74rem;color:#51656a;margin-top:4px'>{q['tier1_alignment_rows']:,} Tier 1 sample alignment rows not counted"
             + (f" ({q['tier1_alignment_rows_named_qc_or_blank']:,} with a QC or blank keyword)" if q["tier1_alignment_rows_named_qc_or_blank"] else "") + ".</div>") if q["tier1_alignment_rows"] else ""
    notes = _curated_note(q.get("curated_corrections"), "qc") + notes
    standards = ""
    if q.get("standard_like_positions"):
        standards = f"<div style='font-size:.84rem;margin-top:2px'>Standard-like positions: <b>{q['standard_like_positions']:,}</b> of {q['measured_positions']:,}</div>"
    return (f"<td class='v9-qc-blank-cell' data-qc='{q['qc_like_positions']}' data-blank='{q['blank_like_positions']}' data-standard='{q.get('standard_like_positions', 0)}' data-measured='{q['measured_positions']}' "
            f"data-presence='{_e(q['presence'])}' style='{_TD}'>"
            f"<span style='font-weight:700;color:{_PRESENCE_COLOUR[q['presence']]}'>{_e(q['presence'])}</span>"
            + _sample_table_line(table)
            + f"<div class='v9-measured' style='font-size:.84rem;margin-top:4px'><b>Measured here:</b> {q['measured_positions']:,} positions"
            + (" (Tier 1 sample alignment rows excluded)" if q["tier1_alignment_rows"] else "")
            + f" · <b>{q['sample_positions']:,}</b> ML-eligible</div>"
            f"<div style='font-size:.84rem;margin-top:2px'>QC-like positions: <b>{q['qc_like_positions']:,}</b> of {q['measured_positions']:,}</div>"
            + _qc_repro_line(ep.get("repro"))
            + f"<div style='font-size:.84rem;margin-top:2px'>Blank-like positions: <b>{q['blank_like_positions']:,}</b> of {q['measured_positions']:,}</div>"
            + standards + _not_measured_line(ep.get("not_here")) + _controls_box(ep.get("lists")) + notes + tier1 + "</td>")


FIELDS_SHOWN = 6          # fields shown in the cell; the others are under "more fields"


def _value(v: str) -> str:
    short = v if len(v) <= 40 else v[:39] + "…"
    return f"<span title='{_e(v)}' style='background:#eef3f4;border-radius:3px;padding:0 3px;white-space:nowrap'>{_e(short)}</span>"


def _extra_field(f: dict, records: int) -> str:
    return (f"<li style='margin:2px 0'><b>{_e(f['name'])}</b>: " + " ".join(_value(v) for v in f["examples"])
            + f" <span style='color:#51656a'>({f['records_with_value']:,} of {records:,} records; {_plural(f['distinct_values'], 'distinct value')})</span></li>")


def _additional_cell(extra: dict, table: dict, technical: set | None = None) -> str:
    sample_table = ("<div style='margin-top:6px;color:#51656a'>No mwTab text file, so no sample table was read.</div>" if not table.get("records") and table.get("note")
                    else f"<div style='margin-top:6px;color:#51656a'>Sample table: {table.get('records', 0):,} records (controls by kind: Sample eligibility &amp; analytical controls)."
                    + (f" {_plural(table['records_not_controls_by_curated_correction'], 'record')} matched by a keyword are not counted after a curated correction." if table.get("records_not_controls_by_curated_correction") else "")
                    + (f" {_plural(table['records_blank_by_curated_correction'], 'record')} are counted as blank-like after a curated correction." if table.get("records_blank_by_curated_correction") else "")
                    + (f" {_plural(table['standard_like_records'], 'record')} are standard-like after a curated correction." if table.get("standard_like_records") else "")
                    + "</div>")
    fields, records = extra.get("fields") or [], extra.get("records", 0)
    technical = technical or set()                                      # batch / plate / run fields (by name or values): shown in Sample annotations
    moved = [f for f in fields if f["name"] in technical or sa.technical_kind(f["name"])]
    fields = [f for f in fields if f not in moved]
    where = extra.get("read_from", "no mwTab file")
    if fields:
        head = f"<div><b>{_plural(len(fields), 'field')}</b> <span style='color:#51656a'>({_e(where)}, {_plural(records, 'record')})</span></div>"
        body = "<ul style='margin:4px 0 0 14px;padding:0'>" + "".join(_extra_field(f, records) for f in fields[:FIELDS_SHOWN]) + "</ul>"
        if len(fields) > FIELDS_SHOWN:
            body += (f"<details style='margin-top:2px'><summary style='cursor:pointer;color:#113e52;font-size:.78rem'>{_plural(len(fields) - FIELDS_SHOWN, 'more field')}</summary>"
                     "<ul style='margin:4px 0 0 14px;padding:0;max-height:260px;overflow:auto'>" + "".join(_extra_field(f, records) for f in fields[FIELDS_SHOWN:]) + "</ul></details>")
    elif records:
        what = "Nothing beyond what is shown elsewhere" if extra.get("not_repeated") or moved else "No additional sample data"
        head, body = f"<div><b>{what}</b> <span style='color:#51656a'>({_e(where)}, {_plural(records, 'record')})</span></div>", ""
    else:
        head, body = f"<div style='color:#51656a'>No sample table was read ({_e(where)}).</div>", ""
    if moved:
        body += (f"<div style='margin-top:4px;color:#51656a'>{', '.join('“' + _e(f['name']) + '”' for f in moved)}: "
                 "batch-, plate- or run-like field, shown in Sample annotations (MetaBatch-style filters).</div>")
    return (f"<td class='v9-qc-extra' data-fields='{len(fields)}' data-records='{records}' data-read-from='{_e(where)}' style='{_TD};font-size:.82rem;min-width:260px'>"
            + head + body + sample_table + "</td>")


_STEP_NAMES = {"annotation_identification": "annotation / identification", "quantification_calibration": "quantification / calibration",
               "peak_detection_integration": "peak detection / integration", "rt_alignment": "retention-time alignment",
               "signal_noise_thresholds": "signal-to-noise thresholds", "prevalence_missingness_filter": "prevalence / missingness filter",
               "imputation_replacement": "imputation / replacement", "drift_batch_correction": "drift / batch correction",
               "blank_background_filter": "blank / background filter", "nmr_processing": "NMR processing", "isotopes_adducts": "isotopes / adducts",
               "feature_grouping_matching": "feature grouping / matching", "statistics_downstream": "downstream statistics"}


def _processing_cell(p: dict) -> str:
    """Processing evidence of an analysis, as deposited (accepted Reviewer 1 comment 5 audit). Informational, not scored."""
    td = f"<td class='v9-processing' data-dedicated='{len(p.get('dedicated') or [])}' data-software='{len(p.get('software') or [])}' style='{_TD};font-size:.82rem;min-width:260px'>"
    if not p:
        return td + "<span style='color:#51656a'>Not read</span></td>"
    if p.get("files") == "none":
        return td + "<span style='color:#51656a'>No mwTab file deposited</span></td>"
    lines = []
    for d in p.get("dedicated") or []:
        v = d["value"]; short = v if len(v) <= 120 else v[:119] + "…"
        lines.append(f"<div><b>{_e(d['label'])}</b>: <span title='{_e(v)}'>“{_e(short)}”</span></div>")
    if not lines:
        lines.append("<div style='color:#51656a'>No dedicated processing field</div>")
    sw = p.get("software") or []
    if sw:
        shown = ", ".join(_e(x) for x in sw[:6]) + (f" and {len(sw) - 6} more" if len(sw) > 6 else "")
        lines.append(f"<div style='margin-top:3px'><span style='color:#51656a'>Software or services named:</span> {shown}</div>")
    for q in p.get("minfrac") or []:
        lines.append(f"<div style='margin-top:3px'><span style='color:#51656a'>minFraction / minfrac:</span> “…{_e(q)}…”</div>")
    note = (_note("The files of this analysis declare another study or analysis ID",
                  "Found by the source-header check of the processing audit. The evidence shown may describe another analysis; nothing was reassigned.")
            if str(p.get("header_status", "")).startswith("conflict") else "")
    steps = p.get("steps") or []
    det = [f"Processing steps named ({len(steps)}): " + (", ".join(_e(_STEP_NAMES.get(x, x.replace("_", " "))) for x in steps) if steps else "none")]
    if len(sw) > 6:
        det.append("Software or services named: " + ", ".join(_e(x) for x in sw))
    det.append(f"Read from: {_e(p.get('files', ''))}. Names and steps are mentions in the metadata of the analysis (project, study or analysis "
               "sections), not verified as performed; a parameters file is named, its contents are not read.")
    details = ("<details style='margin-top:4px;font-size:.78rem;color:#51656a'><summary style='cursor:pointer;color:#113e52'>Details</summary>"
               "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{x}</li>" for x in det) + "</ul></details>")
    return td + "".join(lines) + note + details + "</td>"


def qc_blank_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    study = _qc_blank_study(study_id, overrides, params)
    if study is None:
        return ""
    rows = "".join(
        f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>"
        + "".join(_qc_blank_cell(a["endpoints"][k], a["sample_table"]) for k, _ in SOURCES)
        + _additional_cell(a["additional"], a["sample_table"], a["technical"]) + _processing_cell(a["processing"]) + "</tr>" for a in study)
    return (
        "<div class='v9-qc-blank' style='margin:0 0 22px'>"
        "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Sample eligibility &amp; analytical controls "
        "<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Informational · counts only, not scored · <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>no better or worse direction</span>"
        f" · controls: {_feature_list_form(study_id, 'controls', params)} · QC reproducibility: {_feature_list_form(study_id, 'qc_reproducibility', params)}</span></h4>"
        "<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>Positions that should not contribute to supervised classification: QC-like, blank-like and standard-like positions per analysis and endpoint. How they are recognised is in the descriptions at the top of this page.</p>"
        f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:900px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
        f"<th style='{_TH};white-space:nowrap;min-width:200px'>datatable</th><th style='{_TH};white-space:nowrap;min-width:200px'>mwTab</th>"
        f"<th style='{_TH};white-space:nowrap;min-width:200px'>untarg_data</th><th style='{_TH};white-space:nowrap'>Additional sample data</th><th style='{_TH};white-space:nowrap'>Processing evidence (as deposited)</th></tr>{rows}</table></div></div>")


def export_qc_blank_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    study = _qc_blank_study(study_id, overrides, params)
    if study is None:
        return None
    return {"schema": "merit.qc_blank_controls_analysis_endpoint.pilot.v3",
            "note": "counts and a presence label only, nothing scored; one record per valid matrix (analysis x endpoint), with the sample edits in force; "
                    "QC-like and blank-like are keyword matches in deposited names, not verified controls",
            "keywords": {k: list(v) for k, v in qb.KEYWORDS.items()}, "presence_labels": list(qb.PRESENCE.values()), "sample_edits": overrides or {},
            "analyses": [{"analysis_id": a["analysis_id"], "additional_sample_data": a["additional"], "sample_table_controls": a["sample_table"],
                          "endpoints": {k: ({**a["endpoints"][k]["q"], "qc_reproducibility": a["endpoints"][k].get("repro")} if "q" in a["endpoints"][k]
                                        else {"availability": a["endpoints"][k]["availability"]}) for k, _ in SOURCES}}
                         for a in study]}


def export_analytical_block(study_id: str) -> dict | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None or not any(_analytical(a) for a in raw["analyses"]):
        return None
    return {"schema": "merit.analytical_informational_per_analysis.pilot.v1",
            "note": "information only, no score; one record per analysis, none per endpoint",
            "acquisition_context": ((raw.get("study_sections") or {}).get("analytical") or {}).get("acquisition_context"),
            "analyses": [{"analysis_id": a["analysis_id"], "acquisition_evidence": _analytical(a).get("acquisition_evidence"),
                          "data_summary": _analytical(a).get("data_summary"),
                          "data_summary_source": _analytical(a).get("data_summary_source"),
                          "data_summary_provenance": _analytical(a).get("data_summary_provenance")}
                         for a in raw["analyses"]]}


# parameters shown in the left panel: one group per filled section; a section adds its group when it is filled
PARAMETER_GROUPS = [
    # its own group, first and open (user, 2026-10-29: "CV fold k must be editable in the show tools … as a separate metric")
    ("Cross-validation folds", "Changes, for every matrix of the study: the count requirement of the readiness profile (requires ≥ k samples in "
     "every class), Class support (its fold target) and Cross-validation feasible. Alerts, annotation and metadata do not change.",
     [("cv_folds", "Number of cross-validation folds k",
       "Stratified k-fold cross-validation needs a sample of every class in every fold, so the count requirement is met when there are at "
       "least two classes and the smallest has at least k samples. A smallest class of m < k samples multiplies Class support by m ÷ k "
       "and makes cross-validation not feasible (✕); scikit-learn warns, and refuses the split when every class has fewer than k. "
       "Default 5. Allowed 2-10. Not the nearest-neighbour k of Class separability.")]),
    ("Structural", "Changes the Structural score of every matrix of the study.",
     [("structural_sample_support_n", "Sample support: full marks at",
       "Number of distinct ML-eligible sample IDs at which Sample support reaches 100. Default 20. Fewer samples score proportionally lower.")]),
    ("Metadata and FAIR reusability", "This section has no adjustable value.", []),
    ("Analytical QC", "Changes the Low-information features score of every matrix of the study.",
     [("low_information_freq_ratio", "Near-zero variance: most common value more than … × the second",
       "A feature is near-zero variance when its most common value is more than this many times as frequent as the second most "
       "common, and its distinct values are few (next value). Default 19 (95/5, caret nearZeroVar). Allowed 5-99."),
      ("low_information_unique_pct", "Near-zero variance: distinct values at most … % of samples",
       "Distinct values of the feature as a percentage of the ML-eligible samples (missing included). Default 10 (caret nearZeroVar). Allowed 1-25."),
      ("low_information_cv_pct", "Caution (not scored): relative spread (CV) below … %",
       "Features that are not flagged but vary by less than this percentage of their mean (CV), counted only for linear declared units "
       "without negative values. Not part of the score. Default 1. Allowed 0.1-5."),
      ("redundancy_cutoff", "Redundant features: |ρ| (Spearman) above …",
       "Two features whose Spearman correlation is above this value are a redundant pair; of each pair, the feature more correlated "
       "with all others counts as redundant. Default 0.90 (caret findCorrelation; Kuhn 2008). Allowed 0.80-0.99."),
      ("qc_rsd_cutoff", "QC reproducibility (not scored): RSD below … % for a passing feature",
       "In the details of QC reproducibility, a feature passes when it is detected in more than 70% of the QC injections and its RSD across them is below this value (the headline median RSD does not use it). "
       "Default 20%; 30% is also common (Broadhurst et al. 2018). Allowed 10-40."),
      ("outlier_iqr_k", "Outlier samples: fences at Q1 − k·IQR and Q3 + k·IQR, k = …",
       "A sample is an outlier when its median value lies outside these fences of the ML-eligible samples' medians. Default 1.5 "
       "(Tukey's fences). Allowed 1.0-3.0; 3.0 flags only far-out samples.")]),
    ("Label structure and class support", "The class definition (whole factor string, or chosen values and combinations) is set in the "
     "Label Structure tab. The number of folds k that Class support and Cross-validation feasible use is set under Cross-validation folds.", []),
    ("Readiness profile", "Changes which matrices get an analytical review alert, and its tier, on the Overview; no score changes. "
     "Under the percentiles, a score cut-off of your own can replace the reference for any check and tier (empty = reference).",
     [("readiness_extreme_pct", "Extreme alert: below the … th percentile of the reference cohort",
       "A value of missing entries and zeros, low-information features, redundant features or outlier samples below this percentile of all "
       "valid matrices assessed here (default settings) is an extreme alert. Default 2.5: lower than 97.5% of the matrices. Cut-offs are kept "
       "for 2.5, 5, 7.5 and 10; another value is taken to the nearest of these."),
      ("readiness_low_pct", "Low alert: below the … th percentile (and not extreme)",
       "A value below this percentile but not below the extreme one is a low alert. Default 10: in the lowest tenth. Never stricter than "
       "the extreme tier (a smaller value is raised to it).")]),
    ("ML task readiness", "Changes Class separability and the informational rows of every matrix. The classes are those chosen in Label Structure.",
     [("sep_k", "Class separability: nearest neighbours k (not the cross-validation folds)",
       "Each sample's k nearest neighbours (unit-agnostic rank distances) are looked at. Default 7 (Oh 2011, ten real datasets). Allowed 3-15."),
      ("sep_theta", "Class separability: a sample is in an overlap area when more than θ of its k neighbours are in another class",
       "Default 3 (Oh 2011); kept within 0 ≤ θ ≤ k/2 (Oh: the reasonable range). With k = 7 and θ = 3, the majority of neighbours must be "
       "in another class. A class needs at least k − θ + 1 samples."),
      ("sep_fdr", "Features associated with the selected classes (informational): false discovery rate q",
       "Benjamini–Hochberg false discovery rate for the Kruskal–Wallis test of each feature across the classes. Default 0.05. Allowed 0.01-0.20.")]),
]
ALL_DEFAULT_PARAMS = {**sv.DEFAULT_PARAMS, **qb.DEFAULT_PARAMS, **li.DEFAULT_PARAMS, **cr.DEFAULT_PARAMS, **ov.DEFAULT_PARAMS, **qr.DEFAULT_PARAMS,
                      **ovw.DEFAULT_PARAMS, **lb.DEFAULT_PARAMS, **sp.DEFAULT_PARAMS}
SECTIONS_WITHOUT_PARAMETERS_YET = tuple(label for key, label in PLACEHOLDER_SECTIONS.items() if key != "analytical")

EXPORT_KEY = "structural_analysis_endpoint"


def export_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    """Structural results of one study for the downloaded JSON: every analysis x endpoint with its four item scores."""
    study = _study(study_id, params, overrides)
    if study is None:
        return None
    analyses = []
    for a in study["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"][key]
            if "m" not in ep:
                endpoints[key] = {"availability": ep.get("availability")}
                continue
            m = ep["m"]
            endpoints[key] = {"availability": "valid", "structural_score": m.get("structural"),
                              "items": {k: m.get(k) for k, _, _ in ITEMS},
                              "assessable": bool(m.get("eligible_positions")), "evidence": m}
        analyses.append({"analysis_id": a["analysis_id"], "endpoints": endpoints,
                         "endpoint_agreement_of_measured_sample_ids": a["agreement"]})
    return {
        "schema": "merit.structural_analysis_endpoint.pilot.v2",
        "scoring_unit": "one valid matrix = one analysis from one endpoint; endpoints are not averaged and have no order of preference",
        "structural_score": "mean of the four items of the matrix, each 0-1; null when the matrix has no ML-eligible sample",
        "items": {k: {"name": label, "definition": tip} for k, label, tip in _item_defs(params)},
        "definitions": POSITION_NOTE,
        "parameters": {k: float((params or {}).get(k, v)) for k, v in sv.DEFAULT_PARAMS.items()},
        "sample_edits": overrides or {},
        "source": "Metabolomics Workbench deposit",
        "study_id": study["study_id"],
        "analyses": analyses,
    }


EXPORT_NOTE = ("What this page shows, block by block, with the parameters and sample edits in force: 'overview_scores' (every scored item; nothing "
               "combined), 'structural_analysis_endpoint', the Analytical QC blocks ('analytical_*'), 'annotation_per_matrix', 'label_structure' "
               "(with the class definition in force), 'ml_task_readiness' (class separability on those classes), 'metadata_fair_per_analysis', and per matrix in 'overview_scores' a 'readiness' entry (analytical review alerts, cross-validation sample counts under the classes in force, and the summary label shown on the page; nothing combined; 'overview_scores.readiness_settings' gives the alert percentiles, folds, classes, every cut-off and the reference version they were computed with; the previous scheme's overall score, band and gates are not included). Scores run from 0 to 100 and higher is better for ML, "
               "except class separability, where higher means stronger local separation of the chosen classes (not an accuracy estimate); "
               "informational blocks have no score. Long feature and sample lists are not included: use 'download the full list (JSON)' beside a "
               "block heading. The remaining fields summarise the study's assessment settings.")


# ---------------------------------------------------------------------------
# Analytical QC: missing entries and zeros (formerly sample-level missingness), one score per valid matrix (analysis x endpoint); the first scored metric
# ---------------------------------------------------------------------------
def _missingness_description() -> str:
    return (
        f"<p style='margin:12px 0 8px'><b>Missing entries and zeros (scored; {SPAN_DIR}higher = a smaller share of cells in the largest "
        "category</span>).</b> Every cell of every ML-eligible sample is put in one category, and each category is scored on its own "
        "(0–100):</p>"
        "<ol style='margin:0 0 8px 18px;padding:0'>"
        "<li><b>Missing entries</b>: empty or non-numeric (NA, ND, &lt;LOD, text). Every endpoint.</li>"
        "<li><b>Zeros, meaning unknown</b>: a zero that may be real or a non-detection. Every mwTab and untarg_data zero; in a datatable, "
        "zeros that are also 0 in mwTab, cannot be traced, or have no paired mwTab.</li>"
        "<li><b>Zeros hiding a missing value</b>: datatable only. A 0 where the paired mwTab has no number.</li>"
        "<li><b>Zeros replacing a value</b>: datatable only. A 0 where the paired mwTab has a number (negative, positive, or one that rounds "
        "to 0).</li></ol>"
        "<p style='margin:8px 0'>Each category = 100 × (1 − its cells ÷ all cells of the ML-eligible samples); the score is the <b>lowest</b> "
        "category that applies (100 − the largest category's percentage), shown rounded down. It is not 100 − the combined percentage of "
        "missing entries and zeros. mwTab and untarg_data are scored on items 1–2; a datatable paired with an mwTab on items 1–4, and one "
        "without on items 1–2. Zeros are reported as zeros: they are never counted as missing entries.</p>"
        "<p style='margin:8px 0'><b>Scope.</b> The score counts the cells of ML-eligible samples (QC-like, blank-like and standard-like "
        "samples and Tier 1 sample-alignment rows left out); <i>Details</i> also gives the whole-matrix figures of Table 4, which count "
        "every deposited position. A datatable zero is traced to the mwTab cell with the same sample ID and feature name, as in Text S16; "
        "where a sample ID or feature name is repeated, every candidate mwTab cell is compared and the zero is classified only when all "
        "agree (an extension of the manuscript's one-to-one tracing), otherwise it counts as untraced. Samples with no numeric value, or only "
        "zeros, are named under the score. Why it matters: missing entries must be imputed or removed, and zeros that stand for missing or "
        "altered values pass unnoticed into a model.</p>")


@_memo
def _missingness_study(study_id: str, overrides: dict | None = None) -> list[dict] | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    out = []
    for a in raw["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            an = (ep.get("sections") or {}).get("analytical") or {}
            if "roster" not in ep or "sample_missingness" not in an:
                endpoints[key] = {"availability": ep.get("availability", "")}
                continue
            rec = (mv.compute(ep["roster"], ep["cell_counts"], ep["n_features"], overrides, ep.get("zero_provenance"))
                   if overrides else an["sample_missingness"])
            # item names as the module names them now (stored records keep the names they were built with, e.g. "Invalid entries")
            rec = {**rec, "items": {k: {**it, "label": mv.ITEMS.get(k, it.get("label", k))} for k, it in (rec.get("items") or {}).items()}}
            endpoints[key] = {"availability": "valid", "rec": rec}
        out.append({"analysis_id": a["analysis_id"], "endpoints": endpoints})
    return out


def _p1(v) -> str:
    return "–" if v is None else f"{v:.1f}"


def _floor1(v: float) -> str:
    """One decimal, rounded down: a score below 100 is never shown as 100.0 (99.99 shows as 99.9)."""
    import math
    return f"{math.floor(v * 10 + 1e-9) / 10:.1f}"


def _share_pct(x: float) -> str:
    """A share in words, rounded to one decimal (the same in Data at a glance and the readiness profile; recheck R06, 2 Nov 2026);
    more decimals only when one decimal would read 0.0% or 100.0% for a share that is not exactly 0 or 100."""
    for d in (1, 2, 3):
        t = f"{x:.{d}f}"
        if not ((0 < x and float(t) == 0) or (x < 100 and float(t) == 100)):
            return t + "%"
    return t + "%"


def _missingness_cell(ep: dict, analysis_id: str) -> str:
    if "rec" not in ep:
        text = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
        return f"<td style='{_TD};color:#51656a;font-style:italic'>{_e(text)}</td>"
    r = ep["rec"]; m = r["matrix"]; items = r["items"]
    if r["score"] is None:
        return (f"<td class='v9-missingness-cell' data-score='' data-eligible='0' data-nonnumeric='{m['empty_or_non_numeric']}' data-zero='{m['zero']}' style='{_TD}'>"
                f"<b>Not assessable</b><div style='font-size:.82rem;color:#51656a'>{_e(r['not_assessable'] or '')}</div></td>")
    lines = ""
    for k, it in items.items():
        low = k in r["set_by"] and r["score"] < 100
        hit = f"{it['cells']:,} cells" if it["cells"] else ""
        lines += (f"<div style='display:flex;justify-content:space-between;gap:8px;font-size:.84rem;{'font-weight:700' if low else ''}'>"
                  f"<span>{_e(it['label'])}</span><span style='white-space:nowrap'>{_floor1(it['score'])}"
                  + (f"<span style='font-weight:400;color:#51656a;font-size:.76rem'> · {hit}</span>" if hit else "") + "</span></div>")
    neg = r.get("negative_cells_written_as_zero") or {}
    flag = (_red_note("Negative values written as 0",
                      f"{neg.get('beyond rounding', 0) + neg.get('by rounding', 0):,} cells: mwTab holds a negative number where this datatable writes 0"
                      f" ({neg.get('beyond rounding', 0):,} beyond rounding, {neg.get('by rounding', 0):,} that round to 0). Datatable files hold no negative number. "
                      "Use the mwTab values, not this datatable.", "font-size:.78rem;margin-top:4px") if r.get("negatives_written_as_zero") else "")
    smp = r["samples"]
    empty_line = "; ".join(x for x in (
        f"{smp.get('with_no_numeric_value', 0):,} of {smp['ml_eligible']:,} samples have no numeric value" if smp.get("with_no_numeric_value") else "",
        f"{smp.get('with_only_zeros', 0):,} of {smp['ml_eligible']:,} samples: all numeric values are 0 (other cells may be missing)" if smp.get("with_only_zeros") else "") if x)
    empty_line = f"<div style='font-size:.8rem;font-weight:700;color:#8a5a00;margin-top:3px'>{empty_line}</div>" if empty_line else ""
    detail = ""
    for k, it in items.items():
        by = " · ".join(f"{_e(c)} {n:,}" for c, n in it["by"].items() if n)
        med = (f"; median sample {100 * it['median_sample']:.2f}% (IQR {100 * it['q1_sample']:.2f}–{100 * it['q3_sample']:.2f}%, max {100 * it['max_sample']:.2f}%)"
               if "median_sample" in it else "")
        detail += (f"<li><b>{_e(it['label'])}</b>: {it['cells']:,} cells ({100 * it['share']:.2f}% of all cells) in {it['samples']:,} of {r['samples']['ml_eligible']:,} samples"
                   + (f" ({by})" if by else "") + med + "</li>")
    tier1 = f"<li>Tier 1 sample alignment rows (not samples, not scored): {r['tier1_alignment_rows']:,}</li>" if r["tier1_alignment_rows"] else ""
    whole = (f"<li>Whole matrix, as Table 4 ({m['positions']:,} positions × {r['n_features']:,} features): "
             f"empty or non-numeric {_p1(m['pct_empty_or_non_numeric'])}%, zeros {_p1(m['pct_zero'])}%</li>")
    details = (f"<details style='margin-top:4px;font-size:.78rem;color:#51656a'><summary style='cursor:pointer;color:#113e52'>Details</summary>"
               f"<ul style='margin:4px 0 0 16px;padding:0'>{detail}{tier1}{whole}</ul></details>")
    edited = (f"<div style='font-size:.76rem;color:#8a5a00;margin-top:3px'>After your sample edits</div>" if r.get("edited_positions") else "")
    return (f"<td class='v9-missingness-cell' data-score='{r['score']:.6f}' data-eligible='{r['samples']['ml_eligible']}' data-nonnumeric='{m['empty_or_non_numeric']}' "
            f"data-zero='{m['zero']}' data-set-by='{'+'.join(r['set_by'])}' style='{_TD}'>"
            f"<span style='font-size:1.15rem;font-weight:700'>{_floor1(r['score'])}</span>"
            f"<span style='font-size:.78rem;color:#51656a'> · {r['samples']['ml_eligible']:,} samples</span>"
            f"{empty_line}<div style='margin-top:4px'>{lines}</div>{flag}{edited}{details}</td>")


def missingness_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    study = _missingness_study(study_id, overrides)
    if study is None:
        return ""
    rows = "".join(f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>"
                   + "".join(_missingness_cell(a["endpoints"][k], a["analysis_id"]) for k, _ in SOURCES) + "</tr>" for a in study)
    return (
        "<div class='v9-missingness' style='margin:0 0 22px'>"
        "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Missing entries and zeros "
        "<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Scored · lowest applicable category · 100 = no missing entry and no zero · <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>higher = a smaller share in the largest category</span></span></h4>"
        "<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>Bold = the item that sets the score; grey = cells affected. "
        "Items are explained in the descriptions at the top of this page.</p>"
        f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:900px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
        + "".join(f"<th style='{_TH};white-space:nowrap;min-width:220px'>{lab}</th>" for _, lab in SOURCES)
        + f"</tr>{rows}</table></div></div>")


def export_missingness_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    study = _missingness_study(study_id, overrides)
    if study is None:
        return None
    return {"schema": "merit.sample_missingness_analysis_endpoint.pilot.v3",
            "note": "missing entries and zeros: categories on the ML-eligible samples, each 100 x (1 - cells / all cells of the ML-eligible samples): "
                    "missing entries (empty or non-numeric), zeros of unknown meaning, and for datatables paired with mwTab zeros hiding a missing value "
                    "and zeros replacing a value; score = the lowest applicable category (100 - the largest category's percentage), not 100 - the "
                    "combined percentage; zeros are never counted as missing entries; repeated sample IDs or feature names are traced only when every "
                    "candidate mwTab cell agrees; 'matrix' gives the whole-matrix counts of Table 4; with the sample edits in force",
            "sample_edits": overrides or {},
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints": {k: ({"availability": "valid", **a["endpoints"][k]["rec"]}
                                                                              if "rec" in a["endpoints"][k] else {"availability": a["endpoints"][k]["availability"]})
                                                                          for k, _ in SOURCES}} for a in study]}


# ---------------------------------------------------------------------------
# Analytical QC: measurement scale, one record per valid matrix (analysis x endpoint); scored item: units declared
# ---------------------------------------------------------------------------
def _scale_description() -> str:
    return (
        f"<p style='margin:12px 0 8px'><b>Measurement scale.</b> Scored: <b>units declared</b>, 100 when a valid UNITS text is deposited, else 0 "
        f"{SPAN_DIR}(100 is better for ML)</span>. Valid = not empty and not a placeholder (NA, not applicable, -, a number alone). mwTab and "
        "datatable use the mwTab data-block UNITS; untarg_data the results-file UNITS. Each cell quotes the text <i>from mwTab</i> as deposited "
        "and, separately, the <i>MERIT-ML reading</i>: its family under the manuscript's rules (Tables S20b–S20D), an interpretation, not a value "
        "from the file. Why it matters: without units a user cannot tell whether a log transform, a CV or a fold change is meaningful.</p>"
        "<p style='margin:8px 0'>Not scored: the deposited scale (median and IQR of the non-zero values, negative values, and in Details the "
        "spread of values and of sample medians). A scale is neither good nor bad; it shows what a user must handle, such as a log transform or "
        "per-sample normalisation.</p>")


def _num(v) -> str:
    if v is None:
        return "–"
    a = abs(v)
    if a != 0 and (a >= 1e4 or a < 1e-2):
        m, e = f"{v:.2e}".split("e")
        return f"{m}×10<sup>{int(e)}</sup>"
    if a >= 1e3:                                  # 1,000-9,999: digits, not "4.01e+03"
        return f"{v:,.0f}"
    return f"{v:.3g}"


@_memo
def _scale_study(study_id: str, overrides: dict | None = None) -> list[dict] | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    out = []
    for a in raw["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            rec = ((ep.get("sections") or {}).get("analytical") or {}).get("measurement_scale")
            if "roster" not in ep or not rec:
                endpoints[key] = {"availability": ep.get("availability", "")}
                continue
            rec = dict(rec)
            if overrides:
                rec["samples"] = scv.samples(ep["roster"], ep.get("sample_medians") or [None] * len(ep["roster"]), overrides)
            endpoints[key] = {"availability": "valid", "rec": rec}
        out.append({"analysis_id": a["analysis_id"], "endpoints": endpoints})
    return out


def _scale_cell(ep: dict, key: str) -> str:
    if "rec" not in ep:
        text = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
        return f"<td style='{_TD};color:#51656a;font-style:italic'>{_e(text)}</td>"
    r = ep["rec"]; u = r["units"]; d = r["deposited_scale"]; smp = r.get("samples") or {}
    dep = u.get("deposited") or {}
    where = "results-file UNITS" if key == "untarg_data" else "data block UNITS"
    if dep.get("text"):
        said = f"“{_e(dep['text'])}”" + ("" if u["declared"] else " <span style='color:#8a5a00'>(a placeholder, not counted)</span>")
    else:
        said = "<span style='color:#51656a'>" + _e({"no UNITS line": "no UNITS line", "empty UNITS field": "empty UNITS field",
                                                     "no results-file line": "no results-file line", "results-file line without UNITS": "results-file line without UNITS",
                                                     "no mwTab file": "no mwTab file"}.get(dep.get("state", ""), dep.get("state", "") or "nothing deposited")) + "</span>"
    reading = (f"<div style='font-size:.8rem;color:#51656a;margin-top:1px' title='Family assigned to the deposited text by the manuscript rules (Tables S20b–S20D). "
               f"An interpretation of the text, not read from the file.'><i>MERIT-ML reading:</i> {_e(u['family'])}</div>") if u["declared"] else ""
    head = (f"<div style='font-size:.84rem'><b>Units declared: {r['score']:.0f}</b></div>"
            f"<div style='font-size:.82rem;margin-top:2px'><span style='color:#51656a'>From mwTab ({where}):</span> {said}</div>{reading}")
    scale = (f"<div style='font-size:.84rem;margin-top:3px'>Median {_num(d['median'])} · IQR {_num(d['q1'])} – {_num(d['q3'])}</div>"
             if d["median"] is not None else "<div style='font-size:.84rem;margin-top:3px;color:#51656a'>No non-zero value</div>")
    neg = (f"<div style='font-size:.8rem;color:#8a5a00;margin-top:2px'>Negative values: {d['pct_negative_of_non_zero']:.1f}% of non-zero values</div>"
           if d.get("pct_negative_of_non_zero") else "")
    det = [f"Units read from the {_e(u['read_from'])}"]
    if key == "untarg_data" and u.get("data_block_family") not in (None, "Not declared") and u["data_block_family"] != u["family"]:
        det.append(f"The mwTab data block declares {_e(u['data_block_family'])}; it describes the data block and is not used for untarg_data")
    if d.get("p05_positive") and d.get("p95_positive"):
        import math
        det.append(f"Positive values, 5th–95th percentile: {_num(d['p05_positive'])} – {_num(d['p95_positive'])} "
                   f"({math.log10(d['p95_positive'] / d['p05_positive']):.1f} orders of magnitude)")
    left_out = smp.get("ml_eligible_with_a_median", 0) - smp.get("positive_medians", 0)
    left = (f" {left_out:,} sample{'s' if left_out != 1 else ''} with a zero or negative median {'are' if left_out != 1 else 'is'} left out." if left_out else "")
    if "orders_of_magnitude_p5_p95" in smp:
        det.append(f"Samples (positive medians only, {smp['positive_medians']:,} samples): their medians span {smp['orders_of_magnitude_p5_p95']:.1f} orders of magnitude "
                   f"(5th–95th percentile); {smp['more_than_10x_from_median_sample']:,} are more than 10× from the median sample.{left}")
    elif left_out:
        det.append(f"Sample spread not computed: it uses positive medians only, and fewer than two samples have one.{left}")
    dv = r.get("datatable_vs_mwtab")
    if dv and sum(dv.values()):
        t = sum(dv.values())
        det.append(f"Paired non-zero mwTab numbers: unchanged {100 * dv['unchanged'] / t:.1f}%, another number {100 * dv['another_number'] / t:.1f}%, "
                   f"0 {100 * dv['zero'] / t:.1f}% (zeros are scored in Missing entries and zeros)")
    details = ("<details style='margin-top:4px;font-size:.78rem;color:#51656a'><summary style='cursor:pointer;color:#113e52'>Details</summary>"
               "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{x}</li>" for x in det) + "</ul></details>")
    return (f"<td class='v9-scale-cell' data-score='{r['score']:.0f}' data-family='{_e(u['family'])}' data-median='{'' if d['median'] is None else repr(d['median'])}' "
            f"style='{_TD}'>{head}{scale}{neg}{details}</td>")


def scale_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    study = _scale_study(study_id, overrides)
    if study is None:
        return ""
    rows = "".join(f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>"
                   + "".join(_scale_cell(a["endpoints"][k], k) for k, _ in SOURCES) + "</tr>" for a in study)
    return (
        "<div class='v9-scale' style='margin:0 0 22px'>"
        "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Measurement scale "
        "<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Scored: units declared (100 or 0) · median and IQR are information · <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>100 (units declared) is better for ML than 0; the median and IQR have no better or worse value</span></span></h4>"
        "<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>Median and IQR of the non-zero values as deposited, no unit conversion. "
        "“From mwTab” = the UNITS text as deposited; “MERIT-ML reading” = the family assigned to it (an interpretation).</p>"
        f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:900px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
        + "".join(f"<th style='{_TH};white-space:nowrap;min-width:220px'>{lab}</th>" for _, lab in SOURCES)
        + f"</tr>{rows}</table></div></div>")


def export_scale_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    study = _scale_study(study_id, overrides)
    if study is None:
        return None
    return {"schema": "merit.measurement_scale_analysis_endpoint.pilot.v1",
            "note": "scored: units declared (100 when a valid UNITS declaration exists, else 0; mwTab and datatable: mwTab data block; untarg_data: "
                    "results-file line); deposited scale (median and Q1-Q3 of non-zero values) and the rest are information",
            "sample_edits": overrides or {},
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints": {k: ({"availability": "valid", **a["endpoints"][k]["rec"]} if "rec" in a["endpoints"][k]
                                                                              else {"availability": a["endpoints"][k]["availability"]}) for k, _ in SOURCES}} for a in study]}


# ---------------------------------------------------------------------------
# Analytical QC: sample annotations, MetaBatch-style filters, per valid matrix (analysis x endpoint); INFORMATIONAL, never scored
# ---------------------------------------------------------------------------
_METABATCH_LINK = ("<a href='https://bioinformatics.mdanderson.org/public-software/metabatch/' target='_blank' rel='noopener noreferrer' "
                   "style='color:#0d6e6e;font-weight:700'>MetaBatch</a>")


def _annotations_description() -> str:
    return (
        "<p style='margin:12px 0 8px'><b>Sample annotations, MetaBatch-style filters.</b> <i>Informational only: not scored; nothing in it "
        "enters the readiness profile or the FAIR score.</i></p>"
        f"<p style='margin:8px 0'><b>What MetaBatch is.</b> {_METABATCH_LINK} (MD Anderson) detects, shows and corrects batch effects in omics "
        "data from the matrix and its sample annotations. It uses an annotation column only when it has at least two values, a value for at "
        "least 60% of the samples, and no more distinct values than 90% of them.</p>"
        "<p style='margin:8px 0'><b>Why it is here.</b> MERIT reuses these three filters to shortlist the annotation columns that could "
        "support a comparison between groups of samples: a batch column, or a factor such as sex or treatment. MetaBatch itself is not run, "
        "so no batch effect is measured.</p>"
        "<p style='margin:8px 0'><b>One row per analysis</b>, read on the ML-eligible samples of its mwTab matrix (else datatable, else "
        "untarg_data; the row says which and names an endpoint that would differ). Each column opens with its headline: batch-, plate- or "
        "run-like fields, run order-like fields, levels of the batch-like label, and factors passing the filters (from the study's factor "
        "record, the one MetaBatch reads).</p>"
        "<p style='margin:8px 0'><b>Batch-, plate- and run-like fields</b> are MERIT's inference, hence “-like”; names and values are quoted as "
        "deposited. They are found from the field name in the factors and the additional sample data (batch, plate, run, injection or analysis "
        "order, run day; plurals included), or from the values when every value is a whole label such as “batch1” or “Plate 3”. Ambiguous names "
        "(Injection, Order, Sequence) and words inside longer values do not count. A run order is unique per sample, never passes the filters, "
        "and is shown with its coverage. A sample with two values for one field is counted as conflicting, not resolved.</p>"
        "<p style='margin:8px 0'><b>Batch-like label.</b> Pre-calculated from the first passing batch-, plate- or run-like field, and editable "
        "per sample in Adjust Matrix Properties (“-” = none). When every batch holds a single value of a factor with at least two values, a red "
        "note says that batch and factor are confounded: no two values of that factor are compared within one batch, so a model could learn the "
        "batch instead. These fields are not repeated under Additional sample data.</p>")


SPAN_DIR = "<span class='v9-direction' style='color:#1f5fbf;font-weight:600'>"
_ANN_ORDER = (("mwtab", "mwTab"), ("datatable", "datatable"), ("untarg_data", "untarg_data"))   # the matrix a row is read from


def _ann_signature(r: dict) -> tuple:
    b = r.get("batch_like_label") or {}
    return (r["usable_factors"], tuple(sorted((t["name"], t["usable"]) for t in r["technical_fields"])), b.get("distinct"), b.get("passes"))


def _annotations_study(study_id: str, overrides: dict | None = None) -> list[dict] | None:
    """One row per analysis: the factors and additional sample data belong to the analysis, so the result is read on one matrix,
    the mwTab matrix, or the datatable / untarg_data matrix when the analysis has no valid mwTab matrix; endpoints that give
    another result are named (19 of 6,459 analyses with defaults)."""
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    out = []
    for a in raw["analyses"]:
        recs = {}
        for key, _ in _ANN_ORDER:
            ep = a["endpoints"].get(key) or {}
            if "roster" in ep:
                rec = sa.compute(sid, a["analysis_id"], ep["roster"], overrides); rec.pop("_pre", None)
                recs[key] = rec
        used = next((k for k, _ in _ANN_ORDER if k in recs), None)
        row = {"analysis_id": a["analysis_id"], "read_from": used}
        if used:
            row["rec"] = recs[used]
            row["other_endpoints_differ"] = [dict(_ANN_ORDER)[k] for k in recs if k != used and _ann_signature(recs[k]) != _ann_signature(recs[used])]
        else:
            row["availability"] = {dict(_ANN_ORDER)[k]: str((a["endpoints"].get(k) or {}).get("availability", "")).replace("_", " ") for k, _ in _ANN_ORDER}
        out.append(row)
    return out


_WHERE = {"factor": "factor", "additional": "additional sample data"}


def _q(text: str) -> str:
    """A deposited name or value, quoted so it reads as deposited."""
    return f"“{_e(text)}”"


def _state(t: dict) -> str:
    return (" · <span style='color:#1d6b3a'>passes MetaBatch's filters</span>" if t["passes"]
            else f" · <span style='color:#8a5a00'>does not pass MetaBatch's filters: {_e(t['why_not'])}</span>")


def _technical_line(t: dict, n: int) -> str:
    kind = sa.KIND_LABEL[t["kind"]]
    if t["kind"] == "run order":
        state = ("" if t["usable"] else " <span style='color:#8a5a00'>(fewer than 60% of samples)</span>")
        what = f"a value for {t['with_value']:,} of {n:,} samples"
    else:
        state = _state(t)
        what = f"{_plural(t['distinct'], 'value')} · a value for {t['with_value']:,} of {n:,} samples"
    how = (f"{kind} from its values {' '.join(_q(v) for v in t.get('examples', []))}" if t.get("identified_by") == "values" else f"{kind} from its name")
    conflict = (f" <span style='color:#8a5a00'>· {_plural(t['conflicting_samples'], 'sample')} with two different values, left out</span>"
                if t.get("conflicting_samples") else "")
    return (f"<li style='margin:2px 0'><b>{_q(t['name'])}</b> <span style='color:#51656a'>({how}; {_WHERE[t['where']]})</span>: "
            f"{what}{state}{conflict}</li>")


def _none(text: str = "None") -> str:
    return f"<span style='color:#51656a'>{text}</span>"


def _batch_cell(tech: list[dict], n: int) -> str:
    bt = [t for t in tech if t["kind"] != "run order"]
    if not bt:
        return _none("None found")
    return f"<div><b>{_plural(len(bt), 'field')}</b></div><ul style='margin:3px 0 0 14px;padding:0;font-size:.8rem'>" + "".join(_technical_line(t, n) for t in bt) + "</ul>"


def _order_cell(tech: list[dict], n: int) -> str:
    ro = [t for t in tech if t["kind"] == "run order"]
    if not ro:
        return _none("None found")
    return f"<div><b>{_plural(len(ro), 'field')}</b></div><ul style='margin:3px 0 0 14px;padding:0;font-size:.8rem'>" + "".join(_technical_line(t, n) for t in ro) + "</ul>"


def _label_cell(b: dict | None, n: int) -> str:
    if not b:
        return _none("None (no batch-like field passes; you can add one in Adjust Matrix Properties)")
    src = (f"pre-calculated by MERIT from {_q(b['pre_calculated_from'])}" if b["pre_calculated_from"] else "none pre-calculated by MERIT")
    edited = f"; {_plural(b['samples_edited'], 'sample')} labelled in Adjust Matrix Properties" if b["samples_edited"] else ""
    head = (f"<b>{_plural(b['distinct'], 'level')}</b> · a value for {b['with_value']:,} of {n:,} samples{_state(b)}"
            if b["distinct"] else f"<b>No label</b> · a value for 0 of {n:,} samples{_state(b)}")
    return (f"<div>{head}</div><div style='font-size:.8rem;color:#51656a'>{src}{edited}</div>"
            f"<div style='font-size:.8rem'>{' '.join(_q(v) for v in b['examples'])}{' …' if b['distinct'] > len(b['examples']) else ''}</div>")


def _factor_cell(fac: list[dict], n: int) -> str:
    ok = [f for f in fac if f["passes"]]
    if not fac:
        return _none("No factor for these samples")
    names = ", ".join(_q(f["name"]) for f in ok) if ok else _none("none passes")
    return f"<div><b>{len(ok)} of {len(fac)}</b>: {names}</div>"


def _confounded_note(subject: str, who: str, c: dict) -> str:
    """Red note, one per field (or label) and factor: every batch holds a single value of the factor. General wording: a factor value
    may span several batches (one-to-one is said when it holds); the batches of each factor value are listed."""
    f = _q(c["factor"]); per = c["batches_per_value"]
    items = [f"{_q(v)}: {', '.join(_q(x) for x in bs)}" for v, bs in per.items()]
    shown = "; ".join(items[:12]) + (f"; … {len(items) - 12:,} more values" if len(items) > 12 else "")
    how = ("one batch per value (one-to-one)" if c["one_to_one"] else "a value can span several batches")
    summary = f"{subject} holds a single {f} value: {who} and {f} are confounded"
    body = (f"Batches per {f} value ({how}): {shown}. No two {f} values are compared within one batch, "
            f"so a model trained on {f} can learn {who} instead.")
    return _red_note(summary, body, "font-size:.78rem;margin-top:4px")


def _annotations_row(a: dict) -> str:
    aid = f"<td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b>"
    if "rec" not in a:
        why = "; ".join(f"{k}: {v or 'no file'}" for k, v in a["availability"].items())
        return aid + f"</td><td colspan='5' style='{_TD};color:#51656a;font-style:italic'>No valid matrix ({_e(why)})</td></tr>"
    r = a["rec"]; n = r["ml_eligible_samples"]; fac = r["factors"]; tech = r["technical_fields"]; b = r.get("batch_like_label")
    used = dict(_ANN_ORDER)[a["read_from"]]
    aid += (f"<div style='font-size:.76rem;color:#51656a'>{used} matrix · {n:,} ML-eligible sample{'s' if n != 1 else ''}</div>"
            + (f"<div style='font-size:.74rem;color:#8a5a00'>no valid mwTab matrix</div>" if a["read_from"] != "mwtab" else "")
            + (f"<div style='font-size:.74rem;color:#8a5a00'>{', '.join(a['other_endpoints_differ'])} gives another result</div>" if a.get("other_endpoints_differ") else "")
            + "</td>")
    conf = [(f"Every {_q(t['name'])} value", _q(t["name"]), c) for t in tech for c in t.get("confounded") or []]
    if b and b["samples_edited"]:
        conf += [("Every level of the batch-like label in force", "the batch-like label", c) for c in b.get("confounded") or []]
    notes = "".join(_confounded_note(subject, who, c) for subject, who, c in conf)
    det = []
    for f in fac:
        state = "passes" if f["passes"] else f"does not pass: {f['why_not']}"
        det.append(f"Factor <b>{_q(f['name'])}</b>: {_plural(f['distinct'], 'value')}, {f['with_value']:,} of {n:,} samples, {_e(state)}")
    join = r["factor_join"]
    det.append("Factors joined: " + "; ".join(f"{v:,} by {_e(k)}" for k, v in join.items()) if join else "No ML-eligible sample")
    add = r["additional_sample_data"]
    det.append(f"Additional sample data ({_e(add['read_from'])}): {add['samples_matched']:,} of {n:,} samples matched by sample ID")
    details = ("<details style='font-size:.78rem;color:#51656a'><summary style='cursor:pointer;color:#113e52'>Details</summary>"
               "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{x}</li>" for x in det) + "</ul></details>")
    td = f"style='{_TD};font-size:.84rem'"
    return (aid + f"<td class='v9-ann-batch' data-n='{sum(1 for t in tech if t['kind'] != 'run order')}' {td}>{_batch_cell(tech, n)}</td>"
            f"<td class='v9-ann-order' data-n='{sum(1 for t in tech if t['kind'] == 'run order')}' {td}>{_order_cell(tech, n)}</td>"
            f"<td class='v9-ann-label' data-label-edited='{b['samples_edited'] if b else 0}' {td}>{_label_cell(b, n)}{notes}</td>"
            f"<td class='v9-ann-factors' data-usable-factors='{r['usable_factors']}' {td}>{_factor_cell(fac, n)}</td>"
            f"<td {td}>{details}</td></tr>")


def annotations_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    study = _annotations_study(study_id, overrides)
    if study is None:
        return ""
    head = [("Analysis", "the matrix the row is read from: mwTab, else datatable, else untarg_data"),
            ("Batch-, plate- or run-like fields", "deposited fields MERIT reads as a batch, plate or run day (by name, or when every value is such a label)"),
            ("Run order-like fields", "deposited fields MERIT reads as a run, injection or analysis order"),
            ("Batch-like label", "one label per sample: MERIT's pre-calculated value from a passing batch-, plate- or run-like field (batch-like first), or yours"),
            ("Factors passing MetaBatch's filters", "study-design factors (factors.json) that could support a comparison between groups: ≥2 values, values for ≥60% of samples and distinct values ≤90% of samples; any factor counts (sex, treatment), not only batches"),
            ("Details", "")]
    rows = "".join(f"<tr>{_annotations_row(a)}" for a in study)
    return (
        "<div class='v9-annotations' style='margin:0 0 22px'>"
        "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Sample annotations · MetaBatch-style filters "
        "<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Informational · not scored · does not enter the readiness profile · <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>no better or worse direction</span></span></h4>"
        "<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>One row per analysis: the deposited sample annotations belong to the analysis, so they are read on "
        "the ML-eligible samples of its mwTab matrix (datatable or untarg_data when there is no valid mwTab matrix). "
        f"The filters are those of {_METABATCH_LINK}, the batch-effect tool, reused to shortlist columns that could support group comparisons; MetaBatch is not run here. "
        "Rules are in the descriptions at the top of this page.</p>"
        f"<div class='v9-annotations-premise' style='color:{_RED};font-size:.84rem;font-weight:600;margin:0 0 10px'>Batch-, plate- and run-like fields and "
        "the batch-like label are pre-calculated by MERIT from the deposited names and values (an inference, not a declaration). "
        "You can change the batch-like label of any sample in Adjust Matrix Properties.</div>"
        f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:1000px'><tr>"
        + "".join(f"<th style='{_TH}' title='{_e(t)}'>{h}</th>" for h, t in head)
        + f"</tr>{rows}</table></div></div>")


def export_annotations_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    study = _annotations_study(study_id, overrides)
    if study is None:
        return None
    return {"schema": "merit.sample_annotations_analysis.pilot.v2", "informational": True, "scored": False, "enters_ml_readiness": False,
            "note": "one row per analysis, read on the ML-eligible samples of the mwTab matrix (datatable, then untarg_data, when no valid mwTab "
                    "matrix); MetaBatch-style factor filters (>=2 values, >=60% of samples, distinct <=90% of samples); factors from factors.json; "
                    "batch-, plate-, run- and run order-like fields from field names (or whole-label values) in factors and mwTab additional sample "
                    "data; batch-like label pre-calculated by MERIT, editable per sample; MetaBatch is not run",
            "sample_edits": overrides or {},
            "analyses": [{"analysis_id": a["analysis_id"], "read_from": a["read_from"],
                          **({"other_endpoints_differ": a["other_endpoints_differ"], **a["rec"]} if "rec" in a else {"availability": a["availability"]})}
                         for a in study]}


# ---------------------------------------------------------------------------
# Analytical QC: low-information features (constant and near-zero variance), one score per valid matrix; scored
# (Reviewer 1, comment 3, metric 1)
# ---------------------------------------------------------------------------
def _low_information_description(params: dict | None = None) -> str:
    r, u = li._params(params)
    return (
        f"<p style='margin:12px 0 8px'><b>Low-information features.</b> Scored, {SPAN_DIR}higher = better for ML</span>: 100 × (1 − flagged ÷ "
        "judged features), on the ML-eligible samples; a feature is judged with at least 3 values (missing cells left out, zeros are values). "
        f"It is <i>flagged</i> when it is <b>constant</b>, or <b>near-zero variance</b>: its most common value is more than {r:g} times as frequent as "
        f"the second and its distinct values are at most {u:g}% of the samples. Source: Kuhn M. Building Predictive Models in R Using the caret "
        "Package. <i>Journal of Statistical Software</i> 2008;28(5):1–26, Section 3 (Data preparation), pp. 3–4, "
        f"{_KUHN_DOI}, which suggests 20 and 20%; MERIT uses the defaults of the caret function nearZeroVar (19 and 10%), adjustable in the "
        "parameter panel. The rule counts values only, so it works alike on raw, normalised and log-scaled data. Cells show the repeated value "
        "(“0” usually means not detected); flagged features are in the download.</p>"
        "<p style='margin:8px 0'>Why it matters: a feature that hardly varies cannot help a classifier, can become constant inside a "
        "cross-validation fold and break per-feature scaling (LDA, SVM, k-nearest neighbours), and in numbers often signals a conversion or "
        "detection problem. It differs from missingness, which asks whether a value is a measurement; a column of zeros counts in both.</p>"
        "<p style='margin:8px 0'><b>Caution, not scored: very small relative spread.</b> Values such as 99.1, 99.2, 99.3 all differ and are not "
        f"flagged above; an amber note counts the unflagged features with a CV (SD ÷ mean) below {li.cv_pct(params):g}%: often internal "
        "standards or rounded values, sometimes tightly regulated metabolites (names in the download). Checked only for linear declared units "
        "without negative values, since on log-scaled values 6–8% of features fall below 1% from the log alone; otherwise the cell says why "
        "(“CV not checked: …”). Adjustable 0.1–5%.</p>")


@_memo
def _low_information_study(study_id: str, params: dict | None = None, overrides: dict | None = None) -> list[dict] | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    out = []
    for a in raw["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            stored = ((ep.get("sections") or {}).get("analytical") or {}).get("low_information")
            if "roster" not in ep or not stored:
                endpoints[key] = {"availability": ep.get("availability", "")}
                continue
            z = np.load(V9_ROOT / stored["file"])
            rec = li.compute(stored, z, [str(x) for x in z["names"]], ep["roster"], overrides, params)
            endpoints[key] = {"availability": "valid", "rec": rec}
        out.append({"analysis_id": a["analysis_id"], "endpoints": endpoints})
    return out


def _details_and_examples(det: list[str], examples: list[str]) -> str:
    """Details and, beside it, Examples (up to 5 features or pairs; the full list is in the on-demand download)."""
    box = "font-size:.78rem;color:#51656a;margin-top:4px"
    out = (f"<details style='{box}'><summary style='cursor:pointer;color:#113e52'>Details</summary>"
           "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{x}</li>" for x in det) + "</ul></details>")
    if examples:
        out += (f"<details class='v9-examples' style='{box}'><summary style='cursor:pointer;color:#113e52'>Examples</summary>"
                "<ul style='margin:4px 0 0 16px;padding:0'>" + "".join(f"<li>{x}</li>" for x in examples) + "</ul></details>")
    return f"<div style='display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start'>{out}</div>"


def _low_info_example(f: dict) -> str:
    if f["kind"] == "constant":
        return f"“{_e(f['feature'])}”: constant, “{_e(f['repeated_value'])}” in all {f['samples_with_value']:,} samples with a value"
    ratio = f["most_common_count"] / max(f["second_count"], 1)
    return (f"“{_e(f['feature'])}”: “{_e(f['repeated_value'])}” in {f['most_common_count']:,} of {f['samples_with_value']:,} samples "
            f"({ratio:.0f}:1), {f['distinct_values']:,} distinct values")


def _feature_list_form(study_id: str, metric: str, params: dict | None) -> str:
    """A small link beside the heading: the full feature list of every analysis x endpoint, built when clicked, for the parameters and
    sample edits in force; nothing is stored."""
    known = {**li.DEFAULT_PARAMS, **cr.DEFAULT_PARAMS, **ov.DEFAULT_PARAMS, **qr.DEFAULT_PARAMS}
    p = {**known, **{k: v for k, v in (params or {}).items() if k in known}}
    hidden = "".join(f"<input type='hidden' name='{k}' value='{float(v):g}'>" for k, v in p.items())
    what = {"low_information": "flagged features with the reason", "redundancy": "redundant features and every pair above the cut-off",
            "outliers": "outlier samples with their median and the fence they cross",
            "qc_reproducibility": "detection rate and RSD of every feature across the QC injections",
            "controls": "QC-like, blank-like and standard-like positions with what made each one a control",
            "annotation": "named-feature classes, unmapped names with the reason, RefMet names and classes, and repeated names"}[metric]
    return ("<form method='post' action='/download/feature-list' style='display:inline;margin:0' "
            "onsubmit=\"this.matrix_overrides.value=(document.getElementById('matrix-overrides-field')||{}).value||'{}'\">"
            f"<input type='hidden' name='study_id' value='{_e(str(study_id or '').strip().upper())}'><input type='hidden' name='metric' value='{metric}'>"
            f"<input type='hidden' name='matrix_overrides' value='{{}}'>{hidden}"
            f"<button type='submit' class='v9-feature-list' title='JSON with the {what}, for every analysis and endpoint; built when clicked, with the settings and sample edits in force' "
            "style='background:none;border:none;padding:0;color:#0d6e6e;text-decoration:underline;cursor:pointer;font:inherit'>"
            "download the full list (JSON)</button></form>")


def _low_information_cell(ep: dict) -> str:
    if "rec" not in ep:
        text = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
        return f"<td style='{_TD};color:#51656a;font-style:italic'>{_e(text)}</td>"
    r = ep["rec"]
    if r["score"] is None:
        return (f"<td class='v9-lowinfo-cell' data-score='' style='{_TD}'><div style='font-size:.84rem;color:#51656a'>"
                f"Not assessed: {_e(r['not_assessed'])}</div></td>")
    head = f"<div style='font-size:.84rem'><b>Low-information features: {_floor1(r['score'])}</b></div>"
    body = details = ""
    if r["flagged"]:
        groups = " · ".join(f"“{_e(g['value'])}” ({g['features']:,})" for g in r["repeated_values"][:2])
        more = f" · {len(r['repeated_values']) - 2} other values" if len(r["repeated_values"]) > 2 else ""
        body = (f"<div style='font-size:.82rem;margin-top:2px'>{r['flagged']:,} of {r['judged']:,} features show (almost) no variation</div>"
                f"<div style='font-size:.8rem;color:#51656a;margin-top:1px'>Repeated value: {groups}{more}</div>")
    det = []
    if r["flagged"]:
        det.append(f"Constant: {r['constant']:,} · near-zero variance: {r['near_zero_variance']:,}")
    if r["not_judged"]:
        det.append(f"Not judged (fewer than 3 values; see Missing entries and zeros): {r['not_judged']:,} of {r['features']:,} features")
    cvr = r.get("cv") or {}
    if cvr.get("checked"):
        det.append(f"Relative spread checked: declared units “{_e(cvr.get('units') or '')}” (linear), no negative value; "
                   f"{cvr.get('features_below', 0):,} features below {cvr['cv_pct']:g}% CV (not scored)")
    # when the check cannot run, the amber note below says so in the cell (not repeated in Details)
    det.append(f"Rule in force: one value only, or the most common value more than {r['freq_ratio']:g}× the second and distinct values "
               f"at most {r['unique_pct']:g}% of the {r['ml_eligible_samples']:,} ML-eligible samples")
    details = _details_and_examples(det, [_low_info_example(f) for f in r.get("flagged_features", [])[:5]])
    caution = ""
    if not cvr.get("checked"):
        caution = _note(f"CV not checked: {_e(cvr.get('reason') or 'no units declared')}",
                        "The relative-spread caution needs linear declared units (a concentration, or an intensity, area or count) and no "
                        "negative value: on log-scaled, normalised or centred values a CV does not measure relative spread. Not scored."
                        + (f" Declared units: “{_e(cvr['units'])}”." if cvr.get("units") else ""))
    elif cvr.get("features_below"):
        k = cvr["features_below"]
        caution = _note(f"{k:,} feature{'s vary' if k != 1 else ' varies'} by less than {cvr['cv_pct']:g}% (CV)",
                        "For example internal standards or fixed or rounded values; sometimes tightly regulated metabolites. "
                        "Many such features in one matrix can mean the values are log-scaled although the declared units are not. "
                        "Not scored; check before use. Names in the download.")
    return (f"<td class='v9-lowinfo-cell' data-score='{r['score']:.4f}' data-flagged='{r['flagged']}' data-judged='{r['judged']}' "
            f"data-cv-below='{cvr.get('features_below', '') if cvr.get('checked') else ''}' style='{_TD}'>{head}{body}{caution}{details}</td>")


def low_information_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    study = _low_information_study(study_id, params, overrides)
    if study is None:
        return ""
    rows = "".join(f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>"
                   + "".join(_low_information_cell(a["endpoints"][k]) for k, _ in SOURCES) + "</tr>" for a in study)
    return (
        "<div class='v9-lowinfo' style='margin:0 0 22px'>"
        "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Low-information features "
        f"<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Scored · 100 = no constant or near-zero-variance feature · <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>higher = better for ML</span> · {_feature_list_form(study_id, 'low_information', params)}</span></h4>"
        "<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>Features whose values hardly vary across the ML-eligible samples. "
        "The rule and its two adjustable values are in the descriptions at the top of this page.</p>"
        f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:900px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
        + "".join(f"<th style='{_TH};white-space:nowrap;min-width:220px'>{lab}</th>" for _, lab in SOURCES)
        + f"</tr>{rows}</table></div></div>")


def export_low_information_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    study = _low_information_study(study_id, params, overrides)
    if study is None:
        return None
    r, u = li._params(params)
    return {"schema": "merit.low_information_analysis_endpoint.pilot.v2", "scored": True,
            "note": "constant, or near-zero variance (most common value > R x the second and distinct values <= U% of ML-eligible samples; "
                    "caret nearZeroVar defaults R = 19, U = 10) among features with >= 3 values; score = 100 x (1 - flagged / judged)",
            "caution_not_scored": "cv: features not flagged with CV below low_information_cv_pct, only for linear declared units without negative values",
            "parameters": {"low_information_freq_ratio": r, "low_information_unique_pct": u, "low_information_cv_pct": li.cv_pct(params)},
            "sample_edits": overrides or {},
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints": {k: ({"availability": "valid", **a["endpoints"][k]["rec"]} if "rec" in a["endpoints"][k]
                                                                              else {"availability": a["endpoints"][k]["availability"]}) for k, _ in SOURCES}} for a in study]}


# ---------------------------------------------------------------------------
# Analytical QC: redundant features (multicollinearity), one score per valid matrix; scored (Reviewer 1, comment 3, metric 2)
# ---------------------------------------------------------------------------
_KUHN_DOI = ("<a href='https://doi.org/10.18637/jss.v028.i05' target='_blank' rel='noopener noreferrer' style='color:#0d6e6e'>"
             "doi:10.18637/jss.v028.i05</a>")
_SPEARMAN_WHY = ("Spearman correlation (on ranks) is used because any transformation that keeps the order of a feature's values "
                 "(a log transform, a change of units or scale) leaves it unchanged; a few very large values cannot drive it; and it captures "
                 "any monotonic relation, not only a straight line. Normalisation that differs between samples (for example to each "
                 "sample's total signal) changes the order within a feature and can change the correlations.")


def _redundancy_description(params: dict | None = None) -> str:
    c = cr.cutoff(params)
    return (
        f"<p style='margin:12px 0 8px'><b>Redundant features (multicollinearity).</b> Scored, {SPAN_DIR}higher = fewer features flagged as redundant</span>: 100 × "
        "(1 − redundant ÷ evaluated features), on the ML-eligible samples. <b>Evaluated</b>: features with at least 3 values, <b>except those "
        "flagged in Low-information features</b> (a column that hardly varies gives meaningless correlations and is scored there). A pair is "
        f"<b>redundant</b> when its Spearman |ρ|, over at least 10 shared samples, is above {c:g}; of each pair, the feature with the larger mean "
        "|ρ| to all others counts as redundant (caret findCorrelation, fast version: each feature's mean |ρ| is computed once and not recalculated "
        "as features are flagged, so it can flag more features than caret's iterative version), so that no pair of the remaining features is above "
        "the cut-off (adjustable 0.80–0.99). Each feature is ranked once over its own values, and a pair's correlation is computed on the samples "
        "both features have values for; this equals Spearman's ρ when no value is missing.</p>"
        f"<p style='margin:8px 0'>Why Spearman: {_SPEARMAN_WHY}</p>"
        "<p style='margin:8px 0'>Why it is reported: correlated features do not by themselves prevent good prediction, and their effect depends "
        "on the model and the goal. They make the coefficients of unregularised linear models unstable, and make feature importance and biomarker "
        "choice ambiguous, because one of several correlated features is picked arbitrarily; in metabolomics they often come from isotopes, adducts "
        "or fragments of one metabolite, or a metabolite reported twice. Source of the rule and the 0.90 default: Kuhn M. Building Predictive "
        f"Models in R Using the caret Package. <i>Journal of Statistical Software</i> 2008;28(5):1–26, Section 3 (Data preparation), pp. 4–5, {_KUHN_DOI}. "
        "Details gives the pairs above the cut-off and the largest linked group; the download lists every redundant feature with its partner.</p>")


@_memo
def _redundancy_study(study_id: str, params: dict | None = None, overrides: dict | None = None, all_pairs: bool = False) -> list[dict] | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    li_default = all(float((params or {}).get(k, v)) == float(v) for k, v in li.DEFAULT_PARAMS.items() if k != "low_information_cv_pct")
    out = []
    for a in raw["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            st = ((ep.get("sections") or {}).get("analytical") or {}).get("redundancy")
            if "roster" not in ep or not st:
                endpoints[key] = {"availability": ep.get("availability", "")}
                continue
            z = np.load(V9_ROOT / st["file"])
            names_all = [str(x) for x in z["names"]]
            note = ""
            # recompute only when the samples kept or the Low-information settings differ from the default: a label or subject edit
            # changes neither, so the stored result stands (2026-11-01; a recompute from the stored values could differ slightly)
            kept = lambda ov: np.array([p["eligible"] and not p["excluded"] and not p["row"][sv.I_ALIGN] for p in sv.effective_positions(ep["roster"], ov)])
            changed = not li_default or (bool(overrides) and not np.array_equal(kept(overrides), kept(None)))
            if changed and "values" in z:
                keep = kept(overrides)
                lst = ep["sections"]["analytical"]["low_information"]; lz = np.load(V9_ROOT / lst["file"])
                flagged = set(li.compute(lst, lz, [str(x) for x in lz["names"]], ep["roster"], overrides, params)["flagged_index"])
                rec = cr.compute_from_values(z["values"][keep].astype(np.float64), names_all, flagged, params, all_pairs)
            else:
                ev = z["evaluated"]
                rec = cr.summarise(st["default"]["ml_eligible_samples"], int(ev.size), z["sums"], z["cnt"], z["pairs"], z["rho"],
                                   cr.cutoff(params), [names_all[j] for j in ev], all_pairs)
                if changed:
                    note = ("Not recomputed for the sample edits or Low-information settings in force: this matrix has more than 1,000,000 "
                            "cells; shown for the deposited ML-eligible samples and default Low-information settings.")
            rec["features"] = st["features"]; rec["note"] = note
            endpoints[key] = {"availability": "valid", "rec": rec}
        out.append({"analysis_id": a["analysis_id"], "endpoints": endpoints})
    return out


def _redundancy_cell(ep: dict) -> str:
    if "rec" not in ep:
        text = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
        return f"<td style='{_TD};color:#51656a;font-style:italic'>{_e(text)}</td>"
    r = ep["rec"]
    if r["score"] is None:
        return (f"<td class='v9-redundancy-cell' data-score='' style='{_TD}'><div style='font-size:.84rem;color:#51656a'>"
                f"Not assessed: {_e(r['not_assessed'])}</div></td>")
    head = f"<div style='font-size:.84rem'><b>Redundant features: {_floor1(r['score'])}</b></div>"
    body = (f"<div style='font-size:.82rem;margin-top:2px'>{r['redundant']:,} of {r['features_evaluated']:,} features are redundant "
            f"(|ρ| &gt; {r['cutoff']:g})</div>") if r["redundant"] else ""
    det = []
    if r["redundant"]:
        det.append(f"Pairs above {r['cutoff']:g}: {r['pairs_above']:,} · largest group of features linked by such pairs: {r['largest_group']:,}")
    det.append(f"Features evaluated: {r['features_evaluated']:,} of {r['features']:,} (at least 3 values; constant and near-zero-variance "
               "features, flagged in Low-information features, are not evaluated)")
    det.append(f"Rule in force: Spearman |ρ| &gt; {r['cutoff']:g} over the samples both features have (at least 10) among the "
               f"{r['ml_eligible_samples']:,} ML-eligible samples; of each pair, the feature with the larger mean |ρ| counts as redundant")
    if r.get("note"):
        det.append(_e(r["note"]))
    details = _details_and_examples(det, [f"“{_e(p['feature_a'])}” and “{_e(p['feature_b'])}”: ρ = {p['rho']:.3f}" for p in r.get("top_pairs", [])])
    small = (_note(f"Fewer than 20 ML-eligible samples ({r['ml_eligible_samples']:,})",
                   "With few samples, high correlations also arise by chance, so this score tends to be lower: across the cohort the median "
                   "score is 62.5 with 10–14 samples and 92.5 with 200 or more (datatable and mwTab, cut-off 0.90). Not a correction; read "
                   "the score with this in mind.") if r["ml_eligible_samples"] < 20 else "")
    return (f"<td class='v9-redundancy-cell' data-score='{r['score']:.4f}' data-redundant='{r['redundant']}' data-evaluated='{r['features_evaluated']}' "
            f"style='{_TD}'>{head}{body}{small}{details}</td>")


def redundancy_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    study = _redundancy_study(study_id, params, overrides)
    if study is None:
        return ""
    rows = "".join(f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>"
                   + "".join(_redundancy_cell(a["endpoints"][k]) for k, _ in SOURCES) + "</tr>" for a in study)
    return (
        "<div class='v9-redundancy' style='margin:0 0 22px'>"
        "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Redundant features (multicollinearity) "
        f"<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Scored · 100 = no pair of features above the cut-off · <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>higher = fewer features flagged as redundant</span> · {_feature_list_form(study_id, 'redundancy', params)}</span></h4>"
        f"<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>Features that are highly correlated with another feature. {_SPEARMAN_WHY} "
        "<b>Features flagged as constant or near-zero variance in Low-information features are not evaluated here.</b> "
        "The rule is in the descriptions at the top of this page.</p>"
        f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:900px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
        + "".join(f"<th style='{_TH};white-space:nowrap;min-width:220px'>{lab}</th>" for _, lab in SOURCES)
        + f"</tr>{rows}</table></div></div>")


def export_redundancy_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    study = _redundancy_study(study_id, params, overrides)
    if study is None:
        return None
    return {"schema": "merit.redundant_features_analysis_endpoint.pilot.v1", "scored": True,
            "note": "Spearman |rho| > cutoff (default 0.90) over samples both features have (>= 10); caret findCorrelation (fast); features with "
                    ">= 3 values not flagged by Low-information features; score = 100 x (1 - redundant / evaluated)",
            "parameters": {"redundancy_cutoff": cr.cutoff(params)}, "sample_edits": overrides or {},
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints": {k: ({"availability": "valid", **a["endpoints"][k]["rec"]} if "rec" in a["endpoints"][k]
                                                                              else {"availability": a["endpoints"][k]["availability"]}) for k, _ in SOURCES}} for a in study]}


# lists left out of the page's embedded data (they can be large); the full lists come from the on-demand download
_LIST_KEYS = ("flagged_features", "flagged_index", "redundant_features", "top_pairs", "pairs_above_cutoff", "outlier_samples")


def _light(block: dict | None) -> dict | None:
    if not block:
        return block
    for a in block.get("analyses", []):
        for ep in a.get("endpoints", {}).values():
            for k in _LIST_KEYS:
                ep.pop(k, None)
            if isinstance(ep.get("cv"), dict):
                ep["cv"].pop("features", None)
    block["full_lists"] = "not in the page; use 'download the full list (JSON)' beside the metric heading (built on request, not stored)"
    return block


def feature_list_payload(study_id: str, metric: str, params: dict | None = None, overrides: dict | None = None) -> dict:
    """Full feature list of one study for one metric, built on request (nothing stored): every analysis x endpoint."""
    import datetime
    sid = str(study_id or "").strip().upper()
    if metric == "low_information":
        study = _low_information_study(sid, params, overrides)
        keep = ("score", "ml_eligible_samples", "features", "judged", "constant", "near_zero_variance", "flagged", "freq_ratio", "unique_pct")
        def ep_out(r):
            cvr = r.get("cv") or {}
            return {**{k: r.get(k) for k in keep}, "flagged_features": r.get("flagged_features", []),
                    "cv_caution_not_scored": {"checked": cvr.get("checked"), "reason": cvr.get("reason") or None, "cv_pct": cvr.get("cv_pct"),
                                              "features": cvr.get("features", [])}}
        what = ("Low-information features: every feature flagged as constant or near-zero variance (caret nearZeroVar rule in force), "
                "with the reason; and the features below the CV caution (not scored). Higher score = better for ML.")
    elif metric == "redundancy":
        study = _redundancy_study(sid, params, overrides, all_pairs=True)
        keep = ("score", "ml_eligible_samples", "features", "features_evaluated", "redundant", "pairs_above", "largest_group", "cutoff", "note")
        def ep_out(r):
            return {**{k: r.get(k) for k in keep}, "redundant_features": r.get("redundant_features", []),
                    "pairs_above_cutoff": r.get("pairs_above_cutoff", [])}
        what = ("Redundant features (multicollinearity): every redundant feature with its most correlated partner, and every pair of "
                "features with Spearman |rho| above the cut-off. Higher score = better for ML.")
    elif metric == "outliers":
        study = _outlier_study(sid, params, overrides)
        keep = ("score", "ml_eligible_samples", "samples_evaluated", "without_numeric_value", "outliers", "k", "q1", "q3", "iqr",
                "lower_fence", "upper_fence", "collapsed")
        def ep_out(r):
            return {**{k: r.get(k) for k in keep}, "outlier_samples": r.get("outlier_samples", [])}
        what = ("Outlier samples: every ML-eligible sample whose median value (over all features, as deposited) lies outside the Tukey "
                "fences of the samples' medians, with its median, the fence it crosses and its distance in IQRs. Higher score = better for ML.")
    elif metric == "qc_reproducibility":
        raw = _raw_study(sid)
        if raw is None:
            raise ValueError(f"no v9 record for {sid}")
        study = [{"analysis_id": a["analysis_id"], "endpoints": {k: ({"rec": _qc_repro(sid, a["endpoints"][k], overrides, params, features=True)}
                                                                     if (a["endpoints"].get(k) or {}).get("roster") else {"availability": (a["endpoints"].get(k) or {}).get("availability", "")})
                                                                 for k, _ in SOURCES}} for a in raw["analyses"]]
        def ep_out(r):
            return r or {"shown": False, "reason": "not available"}
        what = ("QC reproducibility (informational, not scored): per feature, the detection rate and RSD across the injections of the "
                "largest QC-like material, and whether it passes (detection rate > 70%, RSD below the cut-off; Broadhurst et al. 2018, "
                "Metabolomics 14(6):72). Shown only when there are >= 3 injections of one QC material and the declared units are a concentration, an "
                "intensity, area or count, or normalised, relative or ratio units, with no negative value. More passing features = better measurement reproducibility.")
    elif metric == "controls":
        raw = _raw_study(sid)
        if raw is None:
            raise ValueError(f"no v9 record for {sid}")
        def controls(ep):
            lists = qb.listed(ep["roster"], overrides, sid)
            q = qb.compute(ep["roster"], overrides, sid)
            return {"rec": {**{k: q[k] for k in ("measured_positions", "qc_like_positions", "blank_like_positions", "standard_like_positions", "presence")},
                            "positions": [{"sample_id": x["sample_id"], "kind": name, "found_in": x["found_in"],
                                           "keywords": [{"place": p, "keywords": w} for p, w in qb.keywords_found(kind, x)]}
                                          for kind, name in _CONTROL_NAMES for x in lists[kind]]}}
        study = [{"analysis_id": a["analysis_id"], "endpoints": {k: (controls(a["endpoints"][k]) if (a["endpoints"].get(k) or {}).get("roster")
                                                                     else {"availability": (a["endpoints"].get(k) or {}).get("availability", "")}) for k, _ in SOURCES}}
                 for a in raw["analyses"]]
        def ep_out(r):
            return r
        what = ("Sample eligibility & analytical controls (informational, not scored): every QC-like, blank-like and standard-like position of every "
                "analysis and endpoint, with what made it a control (the keyword and where it was found, or the curated decision). These positions "
                "are not ML-eligible. No better or worse direction.")
    elif metric == "annotation":
        study = annotation_lists(sid)
        def ep_out(r):
            return r
        what = ("Annotation / Interoperability: per matrix, the named-feature coverage and its label classes (Text S3), every named feature "
                "without a RefMet name with the reason, every mapped feature with its RefMet name and RefMet classes, and every group of "
                "repeated feature names. Feature labels only: sample edits and parameters do not apply.")
    else:
        raise ValueError("metric must be low_information, redundancy, outliers, qc_reproducibility, controls or annotation")
    if study is None:
        raise ValueError(f"no v9 record for {sid}")
    return {"study_id": sid, "metric": metric, "description": what,
            "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "parameters": {k: v for k, v in (params or {}).items() if k in li.DEFAULT_PARAMS or k in cr.DEFAULT_PARAMS or k in ov.DEFAULT_PARAMS or k in qr.DEFAULT_PARAMS},
            "sample_edits": overrides or {},
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints": {k: ({"availability": "valid", **ep_out(a["endpoints"][k]["rec"])}
                                                                              if "rec" in a["endpoints"][k] else {"availability": a["endpoints"][k]["availability"]})
                                                                          for k, _ in SOURCES}} for a in study]}


# ---------------------------------------------------------------------------
# Analytical QC: outlier samples, one score per valid matrix; scored (Reviewer 1, comment 3)
# ---------------------------------------------------------------------------
def _outliers_description(params: dict | None = None) -> str:
    k = ov.k_of(params)
    return (
        f"<p style='margin:12px 0 8px'><b>Outlier samples.</b> Scored, {SPAN_DIR}higher = fewer sample medians outside the fences</span>: 100 × (1 − outlier ÷ evaluated "
        "samples), on the ML-eligible samples. Each sample is summarised by the <b>median of its deposited values</b> (zeros included, nothing "
        f"transformed); it is an <b>outlier</b> when that median lies below Q1 − {k:g} × IQR or above Q3 + {k:g} × IQR of all samples' medians "
        "(Tukey's fences; k adjustable 1.0–3.0). Not assessed with fewer than 4 samples; when most medians are equal (usually 0) the fences "
        "collapse, and Details says so.</p>"
        "<p style='margin:8px 0'>Why it matters: one extreme sample can dominate scaling, principal components and small-sample models, and often "
        "signals a failed injection, a dilution or loading error, or a mislabelled sample. It can also be real biology, so it is reported, not "
        "removed; Examples and the download give each outlier's median and the fence it crosses.</p>")


@_memo
def _outlier_study(study_id: str, params: dict | None = None, overrides: dict | None = None) -> list[dict] | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    out = []
    for a in raw["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            if "roster" not in ep or "sample_medians_all" not in ep:
                endpoints[key] = {"availability": ep.get("availability", "")}
                continue
            endpoints[key] = {"availability": "valid", "rec": ov.compute(ep["roster"], ep["sample_medians_all"], overrides, params)}
        out.append({"analysis_id": a["analysis_id"], "endpoints": endpoints})
    return out


def _outlier_cell(ep: dict) -> str:
    if "rec" not in ep:
        text = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " "))
        return f"<td style='{_TD};color:#51656a;font-style:italic'>{_e(text)}</td>"
    r = ep["rec"]
    if r["score"] is None:
        return (f"<td class='v9-outlier-cell' data-score='' style='{_TD}'><div style='font-size:.84rem;color:#51656a'>"
                f"Not assessed: {_e(r['not_assessed'])}</div></td>")
    head = f"<div style='font-size:.84rem'><b>Outlier samples: {_floor1(r['score'])}</b></div>"
    body = (f"<div style='font-size:.82rem;margin-top:2px'>{r['outliers']:,} of {r['samples_evaluated']:,} sample positions are outliers "
            "(median outside the fences)</div>") if r["outliers"] else ""
    det = [f"Samples' medians: Q1 {_num(r['q1'])}, Q3 {_num(r['q3'])}, IQR {_num(r['iqr'])}; fences {_num(r['lower_fence'])} to {_num(r['upper_fence'])} "
           f"(k = {r['k']:g})"]
    if r["collapsed"]:
        det.append("More than half of the samples' medians are equal, so the IQR is 0 and the fences collapse onto that value: any sample "
                   "with another median is flagged")
    det.append(f"Sample positions evaluated: {r['samples_evaluated']:,} of {r['ml_eligible_samples']:,} ML-eligible (a position is one sample row "
               "or column of the file; a sample ID deposited twice has two)"
               + (f" ({r['without_numeric_value']:,} without any numeric value)" if r["without_numeric_value"] else ""))
    det.append("Rule in force: a sample's median over all its values as deposited (zeros included), outside Q1 − k·IQR or Q3 + k·IQR")
    ex = [f"“{_e(o['sample_id'])}”: median {_num(o['median'])}, {o['side']} the {'upper' if o['side'] == 'above' else 'lower'} fence {_num(o['fence'])}"
          for o in r["outlier_samples"][:ov.EXAMPLES]]
    return (f"<td class='v9-outlier-cell' data-score='{r['score']:.4f}' data-outliers='{r['outliers']}' data-evaluated='{r['samples_evaluated']}' "
            f"style='{_TD}'>{head}{body}{_details_and_examples(det, ex)}</td>")


def outlier_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    study = _outlier_study(study_id, params, overrides)
    if study is None:
        return ""
    rows = "".join(f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>"
                   + "".join(_outlier_cell(a["endpoints"][k]) for k, _ in SOURCES) + "</tr>" for a in study)
    return (
        "<div class='v9-outliers' style='margin:0 0 22px'>"
        "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Outlier samples "
        f"<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Scored · 100 = no sample median outside the selected fences · {SPAN_DIR}higher = fewer sample medians outside the fences</span> · "
        f"{_feature_list_form(study_id, 'outliers', params)}</span></h4>"
        "<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>ML-eligible samples whose median value lies far from the other samples' "
        "medians (Tukey's fences, on the values as deposited). Reported, not removed. The rule is in the descriptions at "
        "the top of this page.</p>"
        f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:900px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
        + "".join(f"<th style='{_TH};white-space:nowrap;min-width:220px'>{lab}</th>" for _, lab in SOURCES)
        + f"</tr>{rows}</table></div></div>")


def export_outlier_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    study = _outlier_study(study_id, params, overrides)
    if study is None:
        return None
    return {"schema": "merit.outlier_samples_analysis_endpoint.pilot.v1", "scored": True, "higher_is_better": True,
            "note": "per-sample median over all features as deposited; outlier outside Q1 - k IQR / Q3 + k IQR (k default 1.5, Tukey's fences); "
                    "score = 100 x (1 - outliers / samples evaluated)",
            "parameters": {"outlier_iqr_k": ov.k_of(params)}, "sample_edits": overrides or {},
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints": {k: ({"availability": "valid", **a["endpoints"][k]["rec"]} if "rec" in a["endpoints"][k]
                                                                              else {"availability": a["endpoints"][k]["availability"]}) for k, _ in SOURCES}} for a in study]}



# ---------------------------------------------------------------------------
# Annotation / Interoperability: one record per valid matrix (analysis x endpoint); feature labels only, so sample edits do not apply
# ---------------------------------------------------------------------------
# what a higher value means (2026-10-26): names help interpretation and combining studies; a model can be trained without them
_DIR_ANN = (f"{SPAN_DIR}higher = more features carry a metabolite name</span> (helps interpretation and combining studies; "
            "names are not needed to train a model)")
_DIR_REFMET = (f"{SPAN_DIR}higher = more named features have a RefMet name</span> (helps interpretation and combining studies; "
               "not needed to train a model)")
_NO_SAMPLES = "Feature labels do not depend on samples: sample edits and the parameter panel do not change this tab."


def annotation_description() -> str:
    return _descriptions(
        "<p style='margin:8px 0'><b>Blocks on this page, in order</b></p><ul style='margin:0 0 8px 18px;padding:0'>"
        f"<li>Named-feature coverage: <b>scored</b>, {_DIR_ANN}</li>"
        f"<li>RefMet mapping: <b>scored</b>, {_DIR_REFMET}</li>"
        f"<li>Metabolite classes: <i>informational, not scored</i> ({SPAN_DIR}no better or worse direction</span>)</li>"
        f"<li>Repeated feature names: <i>informational, not scored</i> ({SPAN_DIR}no better or worse direction</span>)</li></ul>"
        "<p style='margin:8px 0'>Computed per valid matrix, because the endpoints of an analysis can label their features differently; endpoints "
        f"are never averaged. {_NO_SAMPLES}</p>"
        "<p style='margin:8px 0'><b>Named-feature coverage.</b> Scored: 100 × named ÷ eligible features, with the manuscript's label classifier "
        "(Text S3, Table S1) unchanged. <i>Named</i>: a specific metabolite (a chemical name or lipid species, also with adduct, isotope or "
        "duplicate suffixes), or an identifier, formula or coordinate label resolved by the repository's record of the analysis. <i>Without a "
        "name</i>: class-level names, tentative annotations, formulas, m/z–RT or bare m/z values, NMR bins, feature IDs and placeholders such as "
        "“Unknown”. Empty labels, sample-metadata words and protein entries are not features; a matrix whose labels are mostly sample IDs is not "
        "assessed. It records whether a name was reported, not whether the identification is correct. Why it matters: named features can be "
        "interpreted, linked to pathways and matched across studies for pooling or external validation; it does not change how well a model "
        "trained on one study performs. Pooled shares match Table S1c (datatable 99.2%, mwTab 62.1%, untarg_data 0.4%).</p>"
        "<p style='margin:8px 0'><b>RefMet mapping.</b> Scored: 100 × named features with a RefMet name ÷ named features (not assessed without a "
        "named feature, so an unnamed feature counts once, in coverage). Each named label is looked up in the repository's metabolite records of "
        "the analysis, both written in one form (trimmed, lower case, “,” read as “_” and “'” as “^”, as in Text S10); it is mapped when every "
        "matching record gives the same valid RefMet name (not blank, “-”, Unknown, Standard or Protein). The names are read as assigned by "
        "Metabolomics Workbench. Why it matters: one standard name per metabolite lets features of different studies be aligned. Unlike RefMet "
        "mapping in Metadata &amp; FAIR, this counts the features of the matrix, not repository records. The cell also gives the number of "
        "distinct RefMet names; features sharing one are listed under Repeated feature names.</p>"
        "<p style='margin:8px 0'><b>Metabolite classes.</b> Informational: the share of each RefMet super class among the mapped features, one "
        "bar per matrix (one colour per class across the study; the seven largest classes named, the rest Other), from the RefMet reference table "
        "of Metabolomics Workbench. It shows what the panel covers, for example mostly lipids; counts are in Details, main and sub classes in the "
        "download.</p>"
        "<p style='margin:8px 0'><b>Repeated feature names.</b> Informational. First, labels that occur more than once in the matrix, compared "
        "as whole deposited strings in the same written form (for named features a “.1” or “(2)” suffix is ignored); labels that differ otherwise, "
        "such as by an RT tag (“@10.47”), an ion or an isotope label, are different strings. Second, different labels that share one RefMet name "
        "(for example “Oxymesterone@10.47” and “Oxymesterone@10.62”), with a visible line of the largest groups. Why it matters: repeated names "
        "make feature identity ambiguous (tools rename such columns silently, for example “name.1”, and they collide when matrices are joined), "
        "and features sharing a name must be combined or one chosen before cross-study alignment. Both can be genuine separate measurements "
        "(isomers, ions); check before merging.</p>"
        "<p style='margin:8px 0 0'>Per-feature lists (unmapped names with the reason, mapped names with their classes, every repeated group) are "
        "in the download beside each block heading.</p>")


@_memo
def _annotation_study(study_id: str) -> list[dict] | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    out = []
    for a in raw["analyses"]:
        endpoints = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            rec = (ep.get("sections") or {}).get("annotation")
            endpoints[key] = {"availability": "valid", "rec": rec} if "roster" in ep and rec else {"availability": ep.get("availability", "")}
        out.append({"analysis_id": a["analysis_id"], "endpoints": endpoints})
    return out


def study_card_annotations(study_id: str) -> str:
    """Study card, Annotations row (user, 2026-11-01: the card used the old per-analysis share and colour thresholds): named-feature
    coverage per analysis and data file, as in Annotation and the readiness profile; one value when the data files agree."""
    rows = []
    for a in _annotation_study(study_id) or []:
        vals = [(lab, a["endpoints"][k]["rec"]["coverage"]["score"]) for k, lab in SOURCES
                if a["endpoints"][k].get("rec") and a["endpoints"][k]["rec"].get("coverage", {}).get("score") is not None]
        if not vals:
            continue
        txt = (f"{_floor1(vals[0][1])}%" if len({_floor1(x) for _, x in vals}) == 1
               else ", ".join(f"{_floor1(x)}% ({lab})" for lab, x in vals))
        rows.append(f"<span class='v9-card-pill' style='background:rgba(17,62,82,.07);color:#113e52;padding:2px 9px;border-radius:999px;font-size:.78rem;margin:2px;font-weight:600'>{_e(a['analysis_id'])}: {_e(txt)} named</span>")
    return "".join(rows)


def study_card_classes(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    """Study card, Class labels row: per analysis, the classes in force (the deposited whole factor string unless chosen in Label
    Structure), counted on ML-eligible sample IDs: number of classes and smallest / largest class. No balance threshold."""
    d = _labels_study(study_id, params, overrides)
    if d is None:
        return ""
    out = []
    for a in d["analyses"]:
        recs = [(dict(SOURCES)[k], a["endpoints"][k]["rec"]) for k in a["valid"]]
        if not recs:
            continue
        one = lambda r: (f"{r['n_classes']:,} class{'es' if r['n_classes'] != 1 else ''}"
                         + (f", smallest {r['smallest']:,}, largest {r['largest']:,}" if r["n_classes"] else ""))
        txt = one(recs[0][1]) if len({one(r) for _, r in recs}) == 1 else "; ".join(f"{lab} {one(r)}" for lab, r in recs)
        out.append(f"<span class='v9-card-pill' style='background:rgba(17,62,82,.07);color:#113e52;padding:2px 9px;border-radius:999px;font-size:.78rem;margin:2px;font-weight:600'>{_e(a['analysis_id'])}: {_e(txt)}</span>")
    what = "the deposited labels (whole factor string)" if d.get("choice") is None else "the chosen classes"
    return ("".join(out) + f"<span style='font-size:.76rem;color:#51656a;margin-left:6px'>{_e(what)}, ML-eligible sample IDs · "
            "<a href='#' class='v9-ovg-goto' data-goto='cohort'>Label Structure ›</a></span>")


def _ann_absent(ep: dict) -> str:
    text = {"no_file": "no file"}.get(ep.get("availability", ""), str(ep.get("availability", "")).replace("_", " ") or "not available")
    return f"<td style='{_TD};color:#51656a;font-style:italic'>{_e(text)}</td>"


def _ann_na(cls: str, why: str) -> str:
    return f"<td class='{cls}' data-score='' style='{_TD}'><div style='font-size:.84rem;color:#51656a'>Not assessed: {_e(why)}</div></td>"


def _coverage_cell(ep: dict) -> str:
    if "rec" not in ep:
        return _ann_absent(ep)
    r = ep["rec"]; c = r["coverage"]
    if c["score"] is None:
        return _ann_na("v9-anncov-cell", c["not_assessed"])
    order = mfl.NAMED + mfl.WITHOUT + mfl.EXCLUDED
    det = [f"{_e(mfl.CLASS_LABELS.get(k, k))}: {n:,}" for k, n in sorted(r["class_counts"].items(), key=lambda kv: order.index(kv[0]))]
    det.append(f"Named {r['named']:,} · without a metabolite name {r['without_name']:,} · not features {r['excluded']:,} · labels {r['labels']:,}")
    ex = [f"{_e(mfl.CLASS_LABELS.get(k, k))}: " + ", ".join(f"“{_e(x)}”" for x in v[:3])
          for k, v in sorted(r["class_examples"].items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)]
    return (f"<td class='v9-anncov-cell' data-score='{c['score']:.4f}' data-named='{r['named']}' data-eligible='{r['eligible']}' style='{_TD}'>"
            f"<div style='font-size:.84rem'><b>Named-feature coverage: {_floor1(c['score'])}</b></div>"
            f"<div style='font-size:.82rem;margin-top:2px'>{r['named']:,} of {r['eligible']:,} features named</div>"
            + _details_and_examples(det, ex) + "</td>")


def _refmet_cell(ep: dict) -> str:
    if "rec" not in ep:
        return _ann_absent(ep)
    r = ep["rec"]; m = r["refmet"]
    if m["score"] is None:
        return _ann_na("v9-annrefmet-cell", m["not_assessed"])
    det = [f"{_e(k)}: {n:,}" for k, n in sorted(m["status"].items(), key=lambda kv: (kv[0] != "mapped", kv[0]))]
    if m["shared_names"]:
        det.append(f"RefMet names shared by several features: {m['shared_names']:,} ({m['features_sharing']:,} features with different labels)")
    det.append("Looked up in the repository's metabolite records of this analysis; RefMet names as assigned by Metabolomics Workbench")
    ex = ([f"No RefMet name: “{_e(x)}” ({_e(why)})" for x, why in m["unmapped_examples"]]
          or [f"“{_e(x)}” → {_e(n)}" for x, n in m["mapped_examples"]])
    shared = (f"<div style='font-size:.8rem;color:#51656a;margin-top:1px'>{m['distinct_refmet_names']:,} distinct RefMet names: "
              f"{m['shared_names']:,} of them shared by {m['features_sharing']:,} features (see Repeated feature names)</div>" if m["shared_names"] else
              f"<div style='font-size:.8rem;color:#51656a;margin-top:1px'>{m['distinct_refmet_names']:,} distinct RefMet names (one per feature)</div>")
    return (f"<td class='v9-annrefmet-cell' data-score='{m['score']:.4f}' data-mapped='{m['mapped']}' data-named='{r['named']}' "
            f"data-distinct='{m['distinct_refmet_names']}' data-shared='{m['shared_names']}' style='{_TD}'>"
            f"<div style='font-size:.84rem'><b>RefMet mapping: {_floor1(m['score'])}</b></div>"
            f"<div style='font-size:.82rem;margin-top:2px'>{m['mapped']:,} of {r['named']:,} named features have a RefMet name</div>{shared}"
            + _details_and_examples(det, ex) + "</td>")


# categorical slots 1-7 (validated: lightness, chroma, adjacent CVD and normal-vision separation all pass on the light surface;
# three slots are below 3:1 contrast, so the legend names every class and Details lists the counts); grey = Other, never a hue
_CLASS_COLOURS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
_CLASS_OTHER = "#9aa3a6"


def _class_colours(study: list[dict]) -> dict:
    """One colour per RefMet super class for the whole study (the 7 largest classes over all its matrices; the rest is Other)."""
    total = collections.Counter()
    for a in study:
        for ep in a["endpoints"].values():
            if "rec" in ep:
                total.update(ep["rec"]["classes"]["super"])
    return {n: _CLASS_COLOURS[i] for i, (n, _) in enumerate(total.most_common(len(_CLASS_COLOURS)))}


def _class_bar(sup: dict, colours: dict, n: int) -> str:
    parts = [(c, k) for c, k in sup.items() if c in colours]
    other = sum(k for c, k in sup.items() if c not in colours)
    if other:
        parts.append(("Other", other))
    segs = "".join(f"<span class='v9-cls-seg' data-class='{_e(c)}' data-n='{k}' title='{_e(c)}: {k:,} of {n:,} features ({100 * k / n:.0f}%)' "
                   f"style='flex:{k} 1 0;background:{colours.get(c, _CLASS_OTHER)}'></span>" for c, k in parts)
    return (f"<div class='v9-cls-bar' role='img' aria-label='{_e('; '.join(f'{c} {k}' for c, k in parts))}' "
            f"style='display:flex;gap:2px;height:14px;margin:4px 0 2px;border-radius:4px;overflow:hidden;background:#fff'>{segs}</div>")


def _classes_cell(ep: dict, colours: dict | None = None) -> str:
    if "rec" not in ep:
        return _ann_absent(ep)
    k = ep["rec"]["classes"]
    if not k["features"]:
        return f"<td class='v9-annclass-cell' data-classes='0' style='{_TD};color:#51656a;font-size:.84rem'>No feature with a RefMet name</td>"
    sup = list(k["super"].items())
    sw = lambda n: (f"<span style='display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;"
                    f"background:{(colours or {}).get(n, _CLASS_OTHER)}'></span>")
    det = [f"{sw(n)}{_e(n)}: {c:,} ({100 * c / k['features']:.0f}%)" for n, c in sup]
    det.append(f"Main classes: {len(k['main']):,} · sub classes: {k['sub_count']:,} (in the download)")
    if k["without_class"]:
        det.append(f"RefMet name not found in the RefMet table: {k['without_class']:,}")
    return (f"<td class='v9-annclass-cell' data-classes='{len(sup)}' style='{_TD}'>"
            f"<div style='font-size:.82rem'><b>{len(sup):,} super class{'es' if len(sup) != 1 else ''}</b> · {k['features']:,} features with a RefMet name</div>"
            + _class_bar(k["super"], colours or {}, sum(k["super"].values())) + _details_and_examples(det, []) + "</td>")


def _repeats_cell(ep: dict) -> str:
    if "rec" not in ep:
        return _ann_absent(ep)
    q = ep["rec"]["repeats"]; m = ep["rec"]["refmet"]; sh = m.get("shared_names") or 0
    if not q["names"] and not sh:
        return (f"<td class='v9-annrep-cell' data-repeats='0' data-shared='0' style='{_TD};font-size:.84rem;color:#51656a'>No repeated label "
                "(deposited strings compared as a whole) and no RefMet name shared by several features</td>")
    head = (f"<div style='font-size:.84rem'><b>{q['names']:,} label{'s' if q['names'] != 1 else ''} repeated</b> ({q['features']:,} features; whole strings)</div>"
            if q["names"] else "<div style='font-size:.82rem;color:#51656a'>No repeated label (deposited strings compared as a whole)</div>")
    if sh:
        eg = " · ".join(f"<span title='{_e(', '.join(g['labels']) + (' …' if g['n'] > len(g['labels']) else ''))}'>{_e(g['refmet_name'])} ×{g['n']}</span>"
                        for g in m["shared_examples"][:3])
        head += (f"<div style='font-size:.84rem;margin-top:3px'><b>{sh:,} RefMet name{'s' if sh != 1 else ''} shared</b> by {m['features_sharing']:,} "
                 f"features with different labels</div><div class='v9-shared-eg' style='font-size:.78rem;color:#132327;margin-top:1px'>e.g. {eg}</div>")
    ex = ([f"Same RefMet name “{_e(g['refmet_name'])}” ← {g['n']} features: " + ", ".join(f"“{_e(x)}”" for x in g["labels"][:4]) + (" …" if g["n"] > 4 else "")
           for g in m.get("shared_examples", [])[:3]]
          + [" = ".join(f"“{_e(x)}”" for x in g["labels"]) + (f" ({g['n']} features)" if g["n"] > len(g["labels"]) else "") for g in q["examples"][:3]])
    det = ([f"Repeated labels: {q['names']:,} ({q['with_identical_spelling']:,} with an identical spelling)"] if q["names"] else []) + (
           [f"RefMet names shared by several features: {sh:,} ({m['features_sharing']:,} features; {m['distinct_refmet_names']:,} distinct RefMet names "
            f"among {m['mapped']:,} mapped features)"] if sh else []) + [
           "Repeated labels: deposited strings compared as a whole, after trimming, lower case and “,” read as “_”; for named features a “.1” or "
           "“(2)” suffix is ignored. Labels that differ in any other way (an RT tag such as “@10.47”, an ion or an isotope label) are different "
           "strings; when they share a RefMet name they are counted on the second line."]
    return (f"<td class='v9-annrep-cell' data-repeats='{q['names']}' data-shared='{sh}' style='{_TD}'>{head}"
            + _details_and_examples(det, ex) + "</td>")


def _class_legend(colours: dict, study: list[dict]) -> str:
    other = any(c not in colours for a in study for ep in a["endpoints"].values() if "rec" in ep for c in ep["rec"]["classes"]["super"])
    items = list(colours.items()) + ([("Other", _CLASS_OTHER)] if other else [])
    return ("<div class='v9-cls-legend' style='display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.76rem;color:#132327;margin:0 0 8px'>"
            + "".join(f"<span style='display:inline-flex;align-items:center;gap:5px'><span style='width:11px;height:11px;border-radius:3px;background:{c};display:inline-block'></span>{_e(n)}</span>"
                      for n, c in items) + "</div>")


def _annotation_block(study_id: str, cls: str, title: str, sub: str, lede: str, cell) -> str:
    study = _annotation_study(study_id)
    if study is None:
        return ""
    legend = ""
    if cell is _classes_cell:
        colours = _class_colours(study)
        cell = functools.partial(_classes_cell, colours=colours)
        legend = _class_legend(colours, study) if colours else ""
    rows = "".join(f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td>"
                   + "".join(cell(a["endpoints"][k]) for k, _ in SOURCES) + "</tr>" for a in study)
    return (f"<div class='{cls}' style='margin:0 0 22px'>"
            f"<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>{title} "
            f"<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>{sub} · {_feature_list_form(study_id, 'annotation', None)}</span></h4>"
            f"<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>{lede}</p>{legend}"
            f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:900px'><tr><th style='{_TH};white-space:nowrap'>Analysis</th>"
            + "".join(f"<th style='{_TH};white-space:nowrap;min-width:220px'>{lab}</th>" for _, lab in SOURCES)
            + f"</tr>{rows}</table></div></div>")


def annotation_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    """The Annotation / Interoperability tab (params and overrides are accepted for the common signature; nothing here depends on them)."""
    if _annotation_study(study_id) is None:
        return f"<div style='{_DASHED}'>No valid matrix in this study, so there is no feature label to assess.</div>"
    return (annotation_description()
            + f"<p style='font-size:.84rem;color:#51656a;margin:0 0 14px'>{_NO_SAMPLES} One row per analysis, one column per endpoint. "
            "Annotation is reported on its own and does not enter the readiness profile (cross-validation sample counts and analytical "
            "review alerts); its values are shown beside the profile on the Overview, under interpretation and reuse.</p>"
            + _annotation_block(study_id, "v9-anncov", "Named-feature coverage", f"Scored · 100 = every eligible feature named · {_DIR_ANN}",
                                "Features that report a specific metabolite name, out of all features (manuscript classifier, Text S3). Details "
                                "gives the label classes, Examples a few labels of each class.", _coverage_cell)
            + _annotation_block(study_id, "v9-annrefmet", "RefMet mapping", f"Scored · 100 = every named feature has a RefMet name · {_DIR_REFMET}",
                                "Named features of this matrix whose name has a RefMet name in the repository's records of the analysis. Named "
                                "features and RefMet names differ for two reasons: some features have no RefMet name, and several features can share "
                                "one (listed under Repeated feature names). Examples lists names without a RefMet name.", _refmet_cell)
            + _annotation_block(study_id, "v9-annclass", "Metabolite classes", f"Informational · {SPAN_DIR}no better or worse direction</span>",
                                "Share of each RefMet super class among the features with a RefMet name; one colour per class for the whole study, "
                                "the seven largest classes named and the rest grouped as Other. Hover a segment for its count; Details lists every class "
                                "(main and sub classes in the download).", _classes_cell)
            + _annotation_block(study_id, "v9-annrep", "Repeated feature names", f"Informational · {SPAN_DIR}no better or worse direction</span>",
                                "Two kinds of repetition: deposited labels that occur more than once, compared as whole strings; and different labels "
                                "that share one RefMet name (for example one metabolite at several retention times, “Oxymesterone@10.47” and "
                                "“@10.62”). Each example shows the labels of one group.", _repeats_cell))


def export_annotation_block(study_id: str) -> dict | None:
    study = _annotation_study(study_id)
    if study is None:
        return None
    return {"schema": "merit.annotation_analysis_endpoint.pilot.v1", "scored": ["coverage", "refmet"], "informational": ["classes", "repeats"],
            "note": "per matrix: named-feature coverage = named / eligible features (Text S3 classifier); RefMet mapping = named features with a "
                    "RefMet name / named features (repository records of the analysis, Text S10 name form); classes from the RefMet reference table of Metabolomics Workbench; "
                    "repeated names among eligible features. Independent of samples. Per-feature lists: 'download the full list' in the tab.",
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints": {k: ({"availability": "valid", **{x: y for x, y in a["endpoints"][k]["rec"].items() if x != "file"}}
                                                                             if "rec" in a["endpoints"][k] else {"availability": a["endpoints"][k]["availability"]})
                                                                         for k, _ in SOURCES}} for a in study]}


def annotation_lists(study_id: str) -> list[dict] | None:
    """Per-feature lists of every matrix (stored by scripts/add_annotation_from_deposit.py) for the on-demand download."""
    import gzip
    study = _annotation_study(study_id)
    if study is None:
        return None
    out = []
    for a in study:
        eps = {}
        for k, _ in SOURCES:
            ep = a["endpoints"][k]
            if "rec" not in ep:
                eps[k] = ep; continue
            with gzip.open(V9_ROOT / ep["rec"]["file"], "rt", encoding="utf-8") as fh:
                lists = json.load(fh)
            r = ep["rec"]
            eps[k] = {"rec": {"named_feature_coverage": r["coverage"]["score"], "named": r["named"], "eligible": r["eligible"],
                              "class_counts": r["class_counts"], "refmet_mapping": r["refmet"]["score"], "refmet_status": r["refmet"]["status"],
                              "distinct_refmet_names": r["refmet"]["distinct_refmet_names"],
                              "refmet_names_shared_by_several_features": [{"refmet_name": n, "features": v} for n, v in lists["shared_refmet"]],
                              "unmapped_named_features": [{"feature": x, "reason": why} for x, why in lists["unmapped"]],
                              "mapped_features": [{"feature": x, "refmet_name": n, "super_class": s1, "main_class": s2, "sub_class": s3}
                                                  for x, n, s1, s2, s3 in lists["mapped"]],
                              "super_classes": r["classes"]["super"], "main_classes": r["classes"]["main"], "sub_classes": lists["sub"],
                              "repeated_name_groups": lists["repeats"]}}
        out.append({"analysis_id": a["analysis_id"], "endpoints": eps})
    return out



# ---------------------------------------------------------------------------
# Label Structure and Class Support: class support (balance = entropy of the smallest vs largest class, x share of the fold target met) and cross-validation feasible per analysis x endpoint, one row per
# analysis; the class definition is chosen once per study (default: the whole factor string, as deposited; or values of one factor /
# deposited value combinations of several picked factors)
# ---------------------------------------------------------------------------
_LB_METRICS = (("class_support", "Class support"), ("cv_feasible", "Cross-validation feasible"))


@_memo
def _labels_study(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    items = lb.factor_items(raw, overrides)
    choice, message = lb.parse_choice(params, items)
    analyses = []
    for a in raw["analyses"]:
        eps = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            eps[key] = ({"availability": "valid", "rec": lb.compute(ep["roster"], sid, overrides, choice, params, ep.get("sample_table_conflicts"))} if "roster" in ep
                        else {"availability": ep.get("availability", "")})
        valid = [k for k, _ in SOURCES if "rec" in eps[k]]
        same = len({json.dumps(eps[k]["rec"], sort_keys=True) for k in valid}) <= 1     # every displayed count, class, score and factor
        analyses.append({"analysis_id": a["analysis_id"], "endpoints": eps, "valid": valid, "endpoints_agree": same})
    return {"study_id": sid, "items": items, "choice": choice, "message": message, "analyses": analyses}


def _lb_choice_text(choice: dict | None) -> str:
    return lb.choice_text(choice)


def _lb_example(largest: int, smallest: int, k: int) -> str:
    """A worked class-support value for the description, with the k in force."""
    return _floor1(100.0 * lb.pair_entropy(smallest, largest) * min(smallest, k) / k)


def labels_description(params: dict | None = None) -> str:
    k = lb.folds(params)
    return _descriptions(
        "<p style='margin:8px 0'><b>Blocks on this page, in order</b></p><ul style='margin:0 0 8px 18px;padding:0'>"
        + "".join(f"<li>{n}: <b>scored</b>, {SPAN_DIR}higher = better for ML</span></li>" for _, n in _LB_METRICS)
        + f"<li>Factors, their values and joint classes: <i>informational, not scored</i> ({SPAN_DIR}no better or worse direction</span>)</li></ul>"
        "<p style='margin:8px 0'><b>Labels.</b> A sample's label is the factor string of its sample-table line (SUBJECT_SAMPLE_FACTORS), exactly "
        "as deposited, or the label on its position when it has no line; an edit in Adjust Matrix Properties replaces it. ML-eligible samples only, "
        "each sample ID once; an empty label has no class and is counted apart, while words such as “None” or “NA” are deposited values. A "
        "sample ID whose sample-table lines give different factor strings has no single deposited label: it is left out of the classes and "
        "counted, unless its label is edited. Computed "
        "per matrix: a row shows one value when the endpoints agree, and one line per endpoint otherwise.</p>"
        "<p style='margin:8px 0'><b>Classes.</b> By default each distinct whole factor string is a class, as deposited (the manuscript's rule); "
        "several factors give many small classes. You can instead pick one or more varying factors (two or more values among the study's "
        "ML-eligible samples; a constant factor such as one sample source is not offered) and two or more classes. With one factor, each class "
        "is one of its values (for example “Hours: 0”, “Hours: 12”, “Hours: 72”); with several, each class is one combination of their values "
        "that occurs in the sample table (for example “FCS: NO_FCS | Hours: 72” against “FCS: FCS | Hours: 72”). Values are never pooled into "
        "one class; a sample has one value per factor, so it belongs to at most one class, and a combination that does not occur cannot be "
        "chosen. Samples with another value or combination are left out (counted). A string is split into factors only when that is certain "
        "(at “ | ”, so “Ju|Hoan” stays whole; every part “Name:value”; no repeated name); otherwise its samples are left out of a chosen-class "
        "definition. The choice applies to the whole study and the Overview.</p>"
        "<p style='margin:8px 0'><b>Factors (informational).</b> The deposited factors, how many vary (more than one value among the "
        "ML-eligible samples) and how many are constant, and the joint classes (the distinct whole factor strings) with the samples per joint "
        "class. Not scored: more factors describe the design more fully, and their effect on class sizes is already in the scores of the "
        "classes in force.</p>"
        f"<p style='margin:8px 0'><b>Class support</b> = 100 × H(m ÷ (m + M)) × min(1, m ÷ k), for a smallest class of m, a largest of M "
        f"and k = {k} folds. The first factor is the class balance: the binary entropy H of the smallest against the largest class (1 for equal "
        "classes). It falls slowly for mild imbalance and steeply for severe imbalance (1:2 = 91.8, 1:4 = 72.2, 1:9 = 46.9, 1:19 = 28.6), "
        "and because only the most extreme pair is used, one small class among many large ones still decides (nothing is averaged over "
        "classes): uneven classes make accuracy misleading. The "
f"second is the share of the fold target met: the smallest class's size m relative to the {k} folds selected, min(m, {k}) ÷ {k}. In "
        f"stratified {k}-fold cross-validation (scikit-learn), a smallest class of m &lt; {k} samples triggers a warning and is absent from "
        f"some test folds; when every class has fewer than {k} samples, the split is refused. Worked examples with k = {k}: "
        f"3 vs 3: {_lb_example(3, 3, k)}; 1 vs 1: {_lb_example(1, 1, k)}; 33 vs 6: {_lb_example(33, 6, k)}; 200 vs 100: {_lb_example(200, 100, k)}. Once the smallest class has at least "
        f"{k} samples, class support equals class balance: larger sample sizes are not scored further (the class sizes are shown in the "
        "Classes column). One class: 0 (nothing to separate). Class support is MERIT-ML's own combination of class balance and the fold "
        "target, not a published metric and not a validated predictor of accuracy; cross-validation feasible is the literal check. <b>Cross-validation feasible</b> = 100 (✓) "
        f"when there are at least two classes and the smallest has at least {k} samples, else 0 (✕): stratified {k}-fold cross-validation needs "
        "a sample of every class in every fold. Five folds is the default (the manuscript's per-class support rule: fewer than 5 makes 5-fold "
        "cross-validation not valid); the number of folds, set in Show tools under Cross-validation folds, changes both scores. The two are shown side by side and "
        "never averaged. Not assessed without a labelled sample, or when a chosen class has no sample in the matrix.</p>")


def _lb_selector(study_id: str, d: dict, inline: bool = False) -> str:
    """Class definition: the whole factor string (default), or picked factors (one or more) and two or more of their deposited values or
    value combinations, each picked from a list into a box. The combinations of any picked factors are counted in the page from the
    study's splittable labels (lb.combinations does the same on the server). Every copy on the page (Label Structure, ML task readiness,
    the readiness profile: inline=True) shows the same picks while they are made, before Apply."""
    # only varying factors (two or more values among the study's ML-eligible samples) can be picked; constant ones are named, not offered
    facs = {f: v for f, v in d["items"]["factors"].items() if len(v) >= 2}
    const = [(f, next(iter(v))) for f, v in d["items"]["factors"].items() if len(v) == 1]
    const_txt = ("Constant factors (one value in this study) are not offered: " + "; ".join(f"{_e(f)} ({_e(v)})" for f, v in const) + ". "
                 if const else "")
    if not facs:
        return (f"<div class='v9-lb-select' style='{_CARD};margin:0 0 12px;font-size:.84rem'><b>Class definition:</b> {_e(lb.choice_text(None))}. "
                f"Classes cannot be chosen: no factor has two or more values that can be read with certainty. {const_txt}</div>")
    ch = d["choice"] or {}
    chosen = {"factors": list(ch.get("factors", [])), "classes": [list(v) for v in ch.get("classes", [])]}
    fopts = "<option value=''>add a factor …</option>" + "".join(
        f"<option value='{_e(json.dumps(f))}'>{_e(f)} ({len(v):,} value{'s' if len(v) != 1 else ''})</option>" for f, v in facs.items())
    box = ("display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:32px;min-width:240px;flex:1 1 320px;padding:4px 6px;"
           "border:1px solid rgba(19,35,39,.25);border-radius:10px;background:#fff")
    return (
        f"<div class='v9-lb-select' style='{_CARD};margin:0 0 12px;font-size:.84rem;text-align:left' data-chosen='{_e(json.dumps(chosen))}' data-study='{_e(str(study_id or ''))}' "
        f"data-joint='{_e(json.dumps(d['items']['joint'], separators=(',', ':')))}'>"
        + ("<div style='font-weight:700;color:#0d6e6e;margin-bottom:3px'>Choose the classes for the intended task "
           "<span style='font-weight:400;color:#51656a'>(the same choice as in Label Structure: Apply recalculates the readiness profile and "
           "every class-based score)</span></div>" if inline else
           "<div style='font-weight:700;color:#0d6e6e;margin-bottom:3px'>Adjust classes and labels here for the intended task "
           "<span style='font-weight:400;color:#51656a'>(the readiness profile and every class-based score follow; individual sample labels "
           "can also be edited in Adjust Matrix Properties)</span></div>")
        + f"<div><b>Class definition in force:</b> {_e(lb.choice_text(d['choice']))}</div>"
        + (f"<div style='color:{_RED};margin-top:4px'>{_e(d['message'])}</div>" if d["message"] else "")
        + "<div style='display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px'><b style='min-width:70px'>1. Factors</b>"
        "<select class='v9-lb-fadd' style='font:inherit;max-width:340px'>" + fopts + "</select>"
        f"<div class='v9-lb-fbox' style='{box}'></div></div>"
        "<div style='display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px'><b style='min-width:70px'>2. Classes</b>"
        "<select class='v9-lb-add' style='font:inherit;max-width:340px'></select>"
        f"<div class='v9-lb-box' style='{box}'></div></div>"
        "<div style='display:flex;gap:10px;flex-wrap:wrap;margin-top:8px'>"
        "<button type='button' class='v2-apply v9-lb-apply' style='padding:6px 12px;border-radius:10px'>Apply classes</button>"
        "<button type='button' class='v2-reset v9-lb-reset' style='padding:6px 12px;border-radius:10px'>Use the whole factor string as deposited</button>"
        "</div><div class='v9-lb-msg' style='color:#9b2335;margin-top:4px'></div>"
        "<div style='color:#51656a;font-size:.78rem;margin-top:6px'>Pick one factor to use its values as classes, or several to use the "
        "combinations of their values that occur in the sample table. Then pick two or more classes; the counts shown here are the "
        "ML-eligible sample IDs across the study. Readiness counts are calculated separately for each analysis and endpoint. Each sample "
        "has one value per factor, so it belongs to at most one class; samples with another value or "
        "combination are left out (counted). Remove a factor or class with ×; changing the factors clears the classes. "
        f"{const_txt}{d['items']['not_split']:,} ML-eligible samples have a factor string that does not split with certainty; they are left out of a "
        "chosen-class definition.</div></div>"
        "<script>(function(){var box=document.currentScript.previousElementSibling;var fsel=box.querySelector('.v9-lb-fadd'),fbox=box.querySelector('.v9-lb-fbox'),"
        "sel=box.querySelector('.v9-lb-add'),area=box.querySelector('.v9-lb-box'),msg=box.querySelector('.v9-lb-msg');"
        "var st=JSON.parse(box.getAttribute('data-chosen')||'{}');var factors=st.factors||[],classes=st.classes||[];"
        "var joint=JSON.parse(box.getAttribute('data-joint')||'{}');var labels=(joint.labels||[]).map(function(l){var d={};l.forEach(function(p){d[p[0]]=p[1];});return d;});"
        "function key(c){return JSON.stringify(c);}function name(vals){return vals.map(function(v,i){return factors[i]+': '+v;}).join(' | ');}"
        "function combos(){if(!factors.length)return [];var n={};(joint.groups||[]).forEach(function(g){var seen={};g[0].forEach(function(i){var vals=factors.map(function(f){return labels[i][f];});"
        "if(vals.some(function(v){return v===undefined;}))return;var k=key(vals);if(!seen[k]){seen[k]=1;n[k]=(n[k]||0)+g[1];}});});"
        "return Object.keys(n).map(function(k){return [JSON.parse(k),n[k]];}).sort(function(a,b){return b[1]-a[1]||(key(a[0])<key(b[0])?-1:1);});}"
        "function chip(text,label,cls,onx){var t=document.createElement('span');t.className=cls;"
        "t.style.cssText='display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border-radius:999px;background:rgba(13,110,110,.1);color:#132327';"
        "t.textContent=text;var x=document.createElement('button');x.type='button';x.textContent='×';x.setAttribute('aria-label','Remove '+label);"
        "x.style.cssText='border:none;background:none;cursor:pointer;font-weight:700;color:#9b2335;padding:0 2px';x.addEventListener('click',onx);t.appendChild(x);return t;}"
        "function hint(el,text){var h=document.createElement('span');h.style.color='#51656a';h.textContent=text;el.appendChild(h);}"
        "function sync(){document.dispatchEvent(new CustomEvent('v9-lb-sync',{detail:{src:box,factors:factors.slice(),classes:classes.map(function(c){return c.slice();}),msg:msg.textContent}}));}"
        "document.addEventListener('v9-lb-sync',function(e){var o=e.detail.src;if(o===box||o.getAttribute('data-study')!==box.getAttribute('data-study'))return;factors=e.detail.factors.slice();"
        "classes=e.detail.classes.map(function(c){return c.slice();});msg.textContent=e.detail.msg;draw();});"
        "function draw(){fbox.innerHTML='';factors.forEach(function(f,i){fbox.appendChild(chip(f,f,'v9-lb-fchip',function(){factors.splice(i,1);"
        "if(classes.length){classes=[];msg.textContent='Classes cleared: the factors changed.';}draw();sync();}));});"
        "if(!factors.length)hint(fbox,'No factor picked: the whole factor string is used');"
        "fsel.querySelectorAll('option').forEach(function(o){if(o.value)o.disabled=factors.indexOf(JSON.parse(o.value))>=0;});"
        "var used=classes.map(key);sel.innerHTML='';var o0=document.createElement('option');o0.value='';"
        "o0.textContent=factors.length?'add a class …':'pick a factor first';sel.appendChild(o0);sel.disabled=!factors.length;"
        "combos().forEach(function(c){var o=document.createElement('option');o.value=key(c[0]);o.textContent=name(c[0])+' ('+c[1].toLocaleString('en-US')+')';"
        "o.disabled=used.indexOf(key(c[0]))>=0;sel.appendChild(o);});"
        "area.innerHTML='';classes.forEach(function(c,i){var nm=name(c);area.appendChild(chip(nm,nm,'v9-lb-chip',function(){classes.splice(i,1);msg.textContent='';draw();sync();}));});"
        "if(!classes.length)hint(area,factors.length?'No class chosen yet':'No class chosen: the whole factor string is used');}"
        "fsel.addEventListener('change',function(){if(!fsel.value)return;var f=JSON.parse(fsel.value);fsel.value='';if(factors.indexOf(f)>=0)return;"
        "factors.push(f);if(classes.length){classes=[];msg.textContent='Classes cleared: the factors changed.';}else msg.textContent='';draw();sync();});"
        "sel.addEventListener('change',function(){if(!sel.value)return;var c=JSON.parse(sel.value);sel.value='';if(classes.map(key).indexOf(key(c))<0)classes.push(c);"
        "msg.textContent='';draw();sync();});"
        "function send(v){var f=document.getElementById('run-form');if(!f)return;var h=document.getElementById('label-classes-field');if(!h){h=document.createElement('input');"
        "h.type='hidden';h.name='label_classes';h.id='label-classes-field';h.setAttribute('form','run-form');document.body.appendChild(h);}h.value=v;"
        "if(f.requestSubmit)f.requestSubmit();else f.submit();}"
        "box.querySelector('.v9-lb-apply').addEventListener('click',function(){if(!factors.length){msg.textContent='Pick a factor first.';return;}"
        "if(classes.length<2){msg.textContent='Choose at least two classes.';return;}send(JSON.stringify({factors:factors,classes:classes}));});"
        "box.querySelector('.v9-lb-reset').addEventListener('click',function(){send('');});draw();})();</script>")


def _lb_value(r: dict, key: str) -> str:
    return "–" if r.get(key) is None else _floor1(r[key])


def _lb_metric_html(r: dict, key: str) -> str:
    """The value as the Overview shows it; class support with its two factors, the cross-validation check with its reason."""
    v = _lb_value(r, key)
    if key == "class_support" and r.get(key) is not None:
        why = ("one class: nothing to separate" if r.get("balance") is None else
               f"balance {_floor1(r['balance'])} ({r['smallest']:,} vs {r['largest']:,}) × fold target met {r['folds_covered']} of {r['cv_folds']}")
        return f"<b>{v}</b><div style='font-size:.74rem;color:#51656a;white-space:normal'>{_e(why)}</div>"
    if key != "cv_feasible" or r.get(key) is None:
        return f"<b>{v}</b>"
    ok = r[key] >= 100
    why = (f"smallest class {r['smallest']:,} {'≥' if ok else '<'} {r['cv_folds']} folds" if r["n_classes"] >= 2 else "fewer than two classes")
    return (f"<b>{v}</b> <span style='font-weight:700;color:{'#0d6e6e' if ok else '#9b2335'}'>{'✓' if ok else '✕'}</span>"
            f"<div style='font-size:.74rem;color:#51656a;white-space:normal'>{_e(why)}</div>")


def _lb_used(r: dict) -> str:
    """'18 of 39 ML-eligible sample IDs in 2 classes': the sample IDs the scores use (those in the classes in force) out of the pool."""
    used = sum(r["classes"].values())
    return (f"{used:,} of {r['ml_eligible_samples']:,} ML-eligible sample IDs in {r['n_classes']:,} class{'es' if r['n_classes'] != 1 else ''}")


def _lb_classes_html(r: dict, brief: bool = False) -> str:
    if r.get("not_assessed") and not r["classes"]:
        return f"<span style='color:#51656a'>{_e(r['not_assessed'])}</span>"
    if brief:                  # another endpoint of the same analysis: its counts only (the class names are in the first line)
        out_n = r["left_out"] + r["not_split"] + r.get("without_picked_factor", 0) + r.get("conflicting_table_lines", 0)
        extra = [x for x in (f"{r['without_label']:,} without a label" if r["without_label"] else "",
                             f"{out_n:,} left out" if out_n else "") if x]
        return (f"<b>{_lb_used(r)}</b>; smallest {r['smallest']:,}, largest {r['largest']:,}"
                + (f" <span style='font-size:.76rem;color:#51656a'>({_e(' · '.join(extra))})</span>" if extra else "") + _lb_other_html(r.get("other_factors")))
    items = list(r["classes"].items())
    shown = " · ".join(f"“{_e(c)}” {n:,}" for c, n in items[:4]) + (f" · +{len(items) - 4} more" if len(items) > 4 else "")
    if len(items) > r["n_classes"]:          # a chosen class without a sample in this matrix: not assessed, said once here
        return (f"<b>{r['n_classes']:,} of {len(items):,} chosen classes</b> have samples here: {sum(r['classes'].values()):,} of {r['ml_eligible_samples']:,} ML-eligible sample IDs"
                f"<div style='font-size:.78rem;color:#51656a;margin-top:2px'>{shown}</div>"
                f"<div style='font-size:.78rem;color:#9b2335;margin-top:2px'>Not assessed: {_e(r['not_assessed'] or '')}</div>")
    extra = []
    if r["without_label"]: extra.append(f"{r['without_label']:,} without a label")
    if r.get("conflicting_table_lines"): extra.append(f"{r['conflicting_table_lines']:,} with conflicting sample-table lines, left out")
    if r["left_out"]: extra.append(f"{r['left_out']:,} with another value or combination, left out")
    if r["not_split"]: extra.append(f"{r['not_split']:,} with a string not split, left out")
    if r.get("without_picked_factor"): extra.append(f"{r['without_picked_factor']:,} without a picked factor, left out")
    return (f"<b>{_lb_used(r)}</b>; smallest {r['smallest']:,}, largest {r['largest']:,}<div style='font-size:.78rem;color:#51656a;margin-top:2px'>{shown}</div>"
            + (f"<div style='font-size:.76rem;color:#51656a'>{_e(' · '.join(extra))}</div>" if extra else "") + _lb_other_html(r.get("other_factors")))


# plain words for the one line (user, 2026-10-31: "state it clearly … fewer words … just main observations"); the popup has the detail
_LB_OTHER_POP = {"values absent": "differs by class (some values are not in every class)", "different": "differs by class (proportions)",
                 "same": "same in every class (same proportions)", "one level": "not compared (one value only)",
                 "one per sample": "not compared (one value per sample ID)", "not assessable": "not compared"}


def _lb_other_html(o: dict | None, compact: bool = False) -> str:
    """Informational line under the classes: do the factors not picked also differ between the chosen classes (labels_v9.other_factors)?
    'differ by class' = some value not in every class, or different proportions (with the largest gap); 'same in every class' = the same
    proportions; 'not compared' = one value, one value per sample ID, or not reported for every selected sample ID. The counts are in the
    (i) popup. compact (the readiness count cell): numbers of factors instead of names. Nothing here enters a score."""
    if not o:
        return ""
    head = "<span style='font-weight:600;color:#3f565b'>Other factors</span> — "
    wrap = lambda body, info="": f"<div class='v9-lb-other' style='{_RD_SMALL};margin-top:2px'>{head}{body}{info}</div>"
    if not o["factors"]:
        return wrap("none (every factor of the factor string is picked)")
    pp = lambda g: f"{g:.1f}".rstrip("0").rstrip(".")
    F = o["factors"]
    differ = [f for f, x in F.items() if x["state"] in ("values absent", "different")]
    same = [f for f, x in F.items() if x["state"] == "same"]
    other = [f for f, x in F.items() if x["state"] not in ("values absent", "different", "same")]
    c = o.get("combined")
    mixed = bool(same) and not differ and c and c["state"] != "same"     # each the same alone, but their combinations differ

    def names(fs):
        shown = [_e(f) + (f" (up to {pp(F[f]['gap'])} points)" if F[f]["state"] == "different" else "") for f in fs[:3]]
        return ", ".join(shown) + (f" and {len(fs) - 3} more" if len(fs) > 3 else "")
    parts = []
    if differ:
        parts.append(f"{len(differ)} differ{'s' if len(differ) == 1 else ''} by class" if compact else f"differ by class: {names(differ)}")
    if same:
        parts.append((f"{len(same)} same in every class" if compact else f"same in every class: {names(same)}")
                     + (" (their combinations differ)" if mixed else ""))
    if other:
        parts.append(f"{len(other)} not compared" if compact else f"not compared: {names(other)}")

    def detail(name, x):
        if x["state"] == "not assessable":
            return (f"<div style='margin-top:3px'><b>{_e(name)}</b>: not compared (reported for {x['reported']:,} of {o['selected']:,} "
                    "selected sample IDs)</div>")
        extra = (f", largest gap {pp(x['gap'])} percentage points" if x["state"] == "different" else
                 ": " + ", ".join(f"“{_e(v)}” in {k} of {o['classes']} classes" for v, k in x["absent"][:6])
                 + (" …" if len(x["absent"]) > 6 else "") if x["state"] == "values absent" else "")
        cnt = ""
        if x.get("counts") and x["state"] != "one level":
            cnt = "<div style='color:#51656a'>" + "; ".join(
                f"{_e(cl)}: " + ", ".join(f"{_e(v)} {n:,}" for v, n in list(vals.items())[:8]) + (" …" if len(vals) > 8 else "")
                for cl, vals in list(x["counts"].items())[:6]) + (" …" if len(x["counts"]) > 6 else "") + "</div>"
        return f"<div style='margin-top:3px'><b>{_e(name)}</b>: {_LB_OTHER_POP[x['state']]}{extra}{cnt}</div>"
    pop = (f"<div><b>Do the other factors also differ between your classes?</b> Where they do, a difference between the classes may come "
           f"from them as well. Compared on the {o['selected']:,} sample IDs in the chosen classes (this matrix).</div>"
           + "".join(detail(f, x) for f, x in F.items())
           + (detail("All together (" + ", ".join(c["factors"]) + ")", c) if c else "")
           + "<div style='margin-top:4px;color:#51656a'>Only factors in the factor string are seen, not subject or batch. Counts only: no "
             "threshold, no score.</div>")
    info = (" <span class='minfo' tabindex='0' role='button' aria-label='How the other factors are spread over the chosen classes'>"
            "<span class='minfo-icon' aria-hidden='true'>i</span><span class='minfo-popup' style='text-align:left;white-space:normal;"
            "text-transform:none;letter-spacing:0;font-weight:400;min-width:300px'>" + pop + "</span></span>")
    return wrap(" · ".join(parts), info)

def _lb_num(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def _lb_factors_html(r: dict) -> str:
    """Informational: the deposited factors (varying / constant), each with its values, and the joint classes with samples per class."""
    j = r.get("joint_class_samples")
    joint = ("" if not j else
             f"<div style='font-size:.78rem;color:#51656a;margin-top:2px'>{r['joint_classes']:,} joint class{'es' if r['joint_classes'] != 1 else ''} "
             f"(distinct whole factor strings); samples per joint class: median {_lb_num(j['median'])} (IQR {_lb_num(j['q1'])}–{_lb_num(j['q3'])}), "
             f"smallest {j['smallest']:,}, largest {j['largest']:,}</div>")
    if not r["factors"]:
        return "<span style='color:#51656a;font-size:.8rem'>No factor string split with certainty</span>" + joint
    det = [f"<b>{_e(f)}</b> ({x['n_values']} value{'s' if x['n_values'] != 1 else ''}, smallest {x['smallest']:,}): "
           + ", ".join(f"“{_e(v)}” {n:,}" for v, n in list(x["values"].items())[:8]) + (" …" if x["n_values"] > 8 else "") for f, x in r["factors"].items()]
    if r["labels_not_split"]:
        det.append(f"Factor strings not split with certainty: {r['labels_not_split']:,} samples")
    nf = len(r["factors"])
    head = (f"{nf:,} factor{'s' if nf != 1 else ''}: " + ", ".join(_e(f) for f in r["factors"])
            + f"<div style='font-size:.78rem;color:#51656a;margin-top:2px'>{r.get('factors_varying', 0):,} vary, {r.get('factors_constant', 0):,} "
            "constant (one value) among these samples</div>")
    return f"<div style='font-size:.8rem'>{head}{joint}</div>" + _details_and_examples(det, [])


def labels_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    d = _labels_study(study_id, params, overrides)
    if d is None:
        return f"<div style='{_DASHED}'>No valid matrix in this study, so there is no labelled sample to assess.</div>"
    rows = ""
    for a in d["analyses"]:
        if not a["valid"]:
            rows += (f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td><td colspan='{len(_LB_METRICS) + 2}' style='{_TD};color:#51656a;font-style:italic'>"
                     "no valid matrix on any endpoint</td></tr>")
            continue
        groups = [("all endpoints" if len(a["valid"]) > 1 else dict(SOURCES)[a["valid"][0]], a["endpoints"][a["valid"][0]]["rec"])] if a["endpoints_agree"] \
            else [(dict(SOURCES)[k], a["endpoints"][k]["rec"]) for k in a["valid"]]
        def cell(fn, key=None):
            parts = []
            for i, (lab, r) in enumerate(groups):
                tag = "" if a["endpoints_agree"] else f"<span style='font-size:.72rem;color:#51656a'>{_e(lab)}: </span>"
                parts.append(tag + ((fn(r, brief=i > 0) if fn is _lb_classes_html else fn(r)) if key is None else _lb_metric_html(r, key)))
            return "<br>".join(parts)
        attrs = " ".join(f"data-{k.replace('_', '-')}='{'' if groups[0][1].get(k) is None else f'{groups[0][1][k]:.4f}'}'" for k, _ in _LB_METRICS)
        rows += (f"<tr class='v9-lb-row' data-analysis='{_e(a['analysis_id'])}' data-agree='{int(a['endpoints_agree'])}' {attrs}>"
                 f"<td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b><div style='font-size:.74rem;color:#51656a'>"
                 f"{_e('all endpoints agree' if a['endpoints_agree'] else 'endpoints differ')}</div></td>"
                 f"<td style='{_TD}'>{cell(_lb_classes_html)}</td>"
                 + "".join(f"<td class='v9-lb-{k}' style='{_TD};white-space:nowrap'>{cell(None, k)}</td>" for k, _ in _LB_METRICS)
                 + f"<td style='{_TD}'>{cell(_lb_factors_html)}</td></tr>")   # one breakdown per endpoint when they differ
    return (labels_description(params) + _lb_selector(study_id, d)
            + "<div class='v9-labels' style='margin:0 0 22px'>"
            "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Label structure and class support "
            f"<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Scored · {SPAN_DIR}higher = better for ML</span> · "
            f"classes: {_e(_lb_choice_text(d['choice']))}</span></h4>"
            "<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>One row per analysis, on its ML-eligible samples: the first line counts the sample IDs in the classes in force (the ones the scores use) out of all ML-eligible sample IDs, each sample ID once. The factors column is "
            "informational: the deposited factors (varying or constant) with their values, read from the factor strings that split with "
            "certainty, and the joint classes (distinct whole factor strings) with the samples per joint class.</p>"
            f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:1000px'><tr>"
            f"<th style='{_TH}'>Analysis</th><th style='{_TH};min-width:280px'>Classes</th>"
            + "".join(f"<th style='{_TH};white-space:nowrap'>{n}</th>" for _, n in _LB_METRICS)
            + f"<th style='{_TH}'>Factors (informational)</th></tr>{rows}</table></div></div>")


def export_labels_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    d = _labels_study(study_id, params, overrides)
    if d is None:
        return None
    return {"schema": "merit.labels_analysis_endpoint.pilot.v1", "scored": [k for k, _ in _LB_METRICS], "higher_is_better": True,
            "class_definition": _lb_choice_text(d["choice"]), "class_choice": d["choice"], "class_choice_message": d["message"] or None,
            "sample_edits": overrides or {}, "factors_in_study": d["items"],
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints_agree": a["endpoints_agree"],
                          "endpoints": {k: ({"availability": "valid", **a["endpoints"][k]["rec"]} if "rec" in a["endpoints"][k]
                                            else {"availability": a["endpoints"][k]["availability"]}) for k, _ in SOURCES}} for a in d["analyses"]]}



# ---------------------------------------------------------------------------
# What leaves the server (page data, downloads): no internal names, server paths, dates of the local copy or storage remarks
# ---------------------------------------------------------------------------
_PUBLIC_KEYS = {"pilot_note": "note", "v8_study_metric_scores": "study_metric_scores", "v8_study_reusability_score": "study_reusability_score"}
_SERVER_PATH = re.compile(r"^(?:/home/|/tmp/|/mnt/|[A-Za-z]:\\)")
_PUBLIC_TEXT = ((re.compile(r"\.pilot(?=\.v\d)"), ""), (re.compile(r"\s*\(built on request, not stored\)"), " (built on request)"),
                (re.compile(r"(?:frozen )?Metabolomics Workbench (?:deposit|files)(?: as downloaded)? (?:of|on) 18 September 2026"), "Metabolomics Workbench deposit"))


# ---------------------------------------------------------------------------
# ML Task Readiness: class separability (R-value, Oh 2011) and informational rows, per analysis x endpoint, on the classes chosen in
# Label structure (one choice for the study)
# ---------------------------------------------------------------------------
@_memo
def _ml_study(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    sid = str(study_id or "").strip().upper()
    raw = _raw_study(sid) if sid else None
    if raw is None:
        return None
    lbd = _labels_study(sid, params, overrides) or {"choice": None}
    choice = lbd.get("choice")
    stored = not overrides and choice is None and sp.settings(params) == sp.settings(None) and not sp.subjects_confirmed(params)
    analyses = []
    for a in raw["analyses"]:
        eps = {}
        for key, _ in SOURCES:
            ep = a["endpoints"].get(key) or {}
            sec = ep.get("sections") or {}
            ml = sec.get("ml_next") or sec.get("ml")          # 'ml_next' exists only while a rebuild waits to be swapped in
            if "roster" not in ep:
                eps[key] = {"availability": ep.get("availability", "")}
            elif not ml:
                eps[key] = {"availability": "valid", "rec": None}
            else:
                rec = dict(ml["default"]) if stored else sp.compute(ep["roster"], sid, V9_ROOT / ml["store"], ml["n_features"], overrides, choice, params,
                                                              ep.get("sample_table_conflicts"))
                if rec.get("chance") is not None and "chance_per_class" not in rec:
                    rec["chance_per_class"] = sp.chance_per_class(rec["classes"], rec["k"], rec["theta"])
                eps[key] = {"availability": "valid", "rec": rec}
        analyses.append({"analysis_id": a["analysis_id"], "endpoints": eps})
    return {"study_id": sid, "analyses": analyses, "labels": lbd, "choice": choice}


def ml_description(params: dict | None = None) -> str:
    k, theta, q = sp.settings(params)
    return _descriptions(
        "<p style='margin:8px 0'><b>Blocks on this page, in order</b></p><ul style='margin:0 0 8px 18px;padding:0'>"
        f"<li>Class separability: <b>scored</b>, {SPAN_DIR}higher = stronger local separation of the chosen classes</span> (not an accuracy estimate)</li>"
        f"<li>Features associated with the selected classes, p/n ratio, subjects: "
        f"<i>informational, not scored</i> ({SPAN_DIR}no better or worse direction</span>)</li></ul>"
        "<p style='margin:8px 0'><b>What it measures.</b> Local class separation under the selected labels and this distance "
        "representation: do samples sit among their own class? It is not a measure of data quality and does not predict attainable "
        "accuracy: a batch effect aligned with the classes can make separation look excellent, a real but subtle biological difference "
        "can show little, and samples from the same subject can sit next to each other.</p>"
        "<p style='margin:8px 0'><b>Samples and classes.</b> The ML-eligible sample IDs in the classes chosen in Label Structure and Class "
        "Support (one choice for the study; the selector below is the same). A chosen class with no sample in a matrix makes it not "
        "assessed, as in Label Structure. Each sample ID is one profile: the mean of its positions (repeated injections are averaged, not "
        "dropped). Computed per matrix: an analysis read from one endpoint has its own features.</p>"
        "<p style='margin:8px 0'><b>Distances.</b> Every feature is ranked across the measured samples of the matrix, the ranks are "
        "standardised, and samples are compared by Euclidean distance. Units, scale and any monotone transform such as log do not change "
        "the result; per-sample normalisation in the deposit is used as deposited. A missing value takes the feature's mean rank: this "
        "imputation pulls the sample towards the middle on that feature, so it still affects distances.</p>"
        f"<p style='margin:8px 0'><b>Class separability</b> = 100 × the mean over the classes of (1 − R(C)), where R(C) is the R-value of a "
        f"class (Oh, Comput Biol Med 2011, eq. 16): the share of its samples with more than θ = {theta} of their k = {k} nearest neighbours "
        "in another class. Averaging over classes keeps imbalance from inflating the score (with the share over all samples, random labels "
        "with a 95:5 split score about 95; class-averaged, about 50). When samples tie at the k-th distance, the tie is not broken by row "
        "order: each sample's chance of being in an overlap area is computed exactly over the tied samples. <b>Chance level</b>, shown "
        "beside it: the expected class separability with random labels of the same class sizes (exact, hypergeometric); a score near it "
        f"means no local separation. Not assessed when a class has fewer than k − θ + 1 = {k - theta + 1} samples (such a sample cannot have "
        "enough neighbours of its own class), or with fewer than two classes. k and θ (0 ≤ θ ≤ k/2) are set in the parameter panel. Oh "
        "found 1 − R correlated with the accuracy of k-NN, neural-network and SVM classifiers on ten datasets (0.93, 0.87, 0.91).</p>"
        "<p style='margin:8px 0'><b>Not shown, in the download.</b> The share of each class's samples outside an overlap area, 1 − R(C), "
        "with its own chance level; the most overlapping pair of classes (Oh, eqs. 14–15); samples surrounded by another class (all k "
        "neighbours in other classes: a possible label issue or sample swap). They are computed for every matrix and kept in the download, "
        "not shown here, to keep the table readable.</p>"
        "<p style='margin:8px 0'><b>p/n ratio</b> (informational): the number of features divided by the number of samples in the chosen "
        "classes, as in Figure S7. Above 1 there are more features than samples. <b>Subjects</b> (informational): subject IDs as deposited or "
        "edited, how many are distinct, the most shared one, and how many carry several sample IDs or span several classes. When subject IDs "
        "carry several sample IDs, a collapsed red note explains the risk of subject leakage in train/test splits (scikit-learn user guide, "
        "cross-validation for grouped data).</p>"
        "<p style='margin:8px 0'><b>Features associated with the selected classes (%)</b> (informational; the toolkit's feature-relevance "
        "idea): the share of the tested features whose values differ between the selected classes, with the number tested out of all features. "
        "It measures association with the chosen target, so it changes with the class choice (a condition, a batch or a combination of factors); "
        "it gives no effect size or predictive importance, and with several classes a significant feature differs somewhere among them. "
        f"Under the share, the 3 significant features with the smallest p are named with the class where they are higher (the highest median "
        f"of the deposited values; with confirmed paired subjects, the class that is higher within subjects) and, for two classes, both medians. "
        f"“Details” lists the 10 strongest with the median of every class, the p-value and the Benjamini–Hochberg adjusted p over all tested "
        f"features, so each can be checked against the deposit. Benjamini–Hochberg false discovery rate q = {q:g}. Large samples make small differences significant, and a batch effect "
        "aligned with the classes produces strong associations. <b>Without confirmed subjects</b>: Kruskal–Wallis on one value per sample ID "
        "(its positions' ranks averaged), missing values left out, a feature tested only when every class has at least 5 values (SciPy's rule "
        "for this test). This assumes every sample ID is independent; where sample IDs share a subject ID the cell says “subject dependence not "
        "adjusted”. <b>With subjects confirmed</b> in Adjust Matrix Properties (the deposited subject IDs, or your edits): when no subject has "
        "sample IDs in different classes, each subject's values are averaged and Kruskal–Wallis is run on subjects; when subjects have sample "
        "IDs in both of two classes, the exact Wilcoxon signed-rank test on those subjects (their mean in each class; zero differences dropped; "
        f"a feature tested with at least {sp.min_pairs(q)} non-zero pairs, the fewest for which an exact two-sided p can reach q); with three or "
        "more classes, the Friedman test on subjects present in every class, only with more than 10 such subjects and more than 6 classes "
        "(SciPy: below that its p-value is not reliable), otherwise not assessable (choose two classes for the exact paired test); when some "
        "subjects span classes while others repeat within one class, not assessable. Missing pairs are never filled. Confirmed subjects do "
        "not change any score; cross-validation that should generalise to new subjects must also keep each subject's samples in one fold.</p>")


def _ml_num(x: float) -> str:
    return f"{x:,.0f}" if abs(x - round(x)) < 1e-9 else f"{x:,.1f}"


def _ml_cell_sep(r: dict) -> str:
    if r.get("score") is None:
        return f"<span style='color:#51656a'>{_e(r.get('not_assessed') or 'not assessed')}</span>"
    tied = f"; ties at the {r['k']}th neighbour in {r['tied_samples']:,} samples, counted exactly" if r.get("tied_samples") else ""
    return (f"<b>{_floor1(r['score'])}</b> <span style='font-size:.78rem;color:#51656a'>· chance level {_floor1(r['chance'])}</span>"
            f"<div style='font-size:.74rem;color:#51656a;white-space:normal'>class-averaged; {_ml_num(r['in_overlap'])} of {r['samples']:,} "
            f"samples in an overlap area (k = {r['k']}, θ = {r['theta']}){tied}</div>")


def _ml_cell_overlap(r: dict) -> str:
    if r.get("score") is None:
        return "–"
    per = sorted(r["per_class"].items(), key=lambda kv: -kv[1])
    ch = r.get("chance_per_class") or {}
    shown = " · ".join(f"“{_e(c)}” {100 * (1 - v):.0f}% outside overlap" + (f" (chance {ch[c]:.0f}%)" if c in ch else "")
                       for c, v in per[:4]) + (f" · +{len(per) - 4} more" if len(per) > 4 else "")
    w = r.get("worst_pair")
    return (f"<div style='font-size:.8rem'>{shown}</div>"
            + f"<div style='font-size:.74rem;color:#51656a'>all samples: {_floor1(r['samples_outside_overlap'])}% outside an overlap area</div>"
            + (f"<div style='font-size:.74rem;color:#51656a'>most overlapping pair: “{_e(w['classes'][0])}” and “{_e(w['classes'][1])}” "
               f"({100 * w['r']:.0f}%)</div>" if w else ""))


def _ml_cell_disc(r: dict) -> str:
    """Features associated with the selected classes: the share of the tested features, how many were tested, and how subjects were handled."""
    d = r.get("discriminative") or {}
    small = "font-size:.74rem;color:#51656a"
    n_f = int(r.get("n_features") or 0)
    if d.get("adjusted"):
        how = {"between": f"subject-adjusted: one value per subject, {d['subjects']:,} subjects from {d['sample_ids']:,} sample IDs",
               "paired": f"subject-adjusted, paired: {d.get('complete_subjects', 0):,} subjects with a sample ID in both classes"
                         + (f"; {d['left_out_subjects']:,} left out (not in both)" if d.get("left_out_subjects") else ""),
               "blocked": f"subject-adjusted: {d.get('complete_subjects', 0):,} subjects with a sample ID in every class"
                          + (f"; {d['left_out_subjects']:,} left out" if d.get("left_out_subjects") else ""),
               "mixed": "subject-adjusted"}.get(d.get("design"), "subject-adjusted")
        if d.get("not_assessable"):
            return (f"<span style='font-size:.78rem;color:#8a5a00'>not assessable with subject adjustment</span>"
                    f"<div style='{small}'>{_e(d['not_assessable'])}</div>")
        head = f"<div style='{small};color:#0d6e6e'>{_e(how)}; {_e(d.get('test', ''))}</div>"
    else:
        head = (f"<div style='font-size:.74rem;color:#8a5a00'>subject dependence not adjusted: {r['subjects_with_several_samples']:,} subject IDs "
                "carry several sample IDs (confirm subjects in Adjust Matrix Properties)</div>" if r.get("subjects_with_several_samples") else "")
    if not d.get("judged"):
        why = ("a class has fewer than 5 values" if not d.get("adjusted") or d.get("design") == "between"
               else f"fewer than {d.get('min_pairs', 6)} non-zero pairs" if d.get("design") == "paired" else "too few complete subjects")
        return (f"<span style='font-size:.78rem;color:#51656a'>no feature tested ({why})</span>{head}" if d.get("not_judged") else "–")
    top = d.get("top") or []
    short = _short_classes(list(r.get("classes") or {}))
    cls_order = list(r.get("classes") or {})
    def word(t):
        two = len(cls_order) == 2
        if t.get("by") == "within subjects":
            return "higher within subjects in" if two else "highest within subjects in"
        return "higher in" if two else "highest median in"
    def inline(t):
        med = t.get("medians") or {}
        vs = (f" (median {_num_short(med[t['highest']])} vs {_num_short(next(v for c, v in med.items() if c != t['highest']))})"
              if len(cls_order) == 2 and med and all(v is not None for v in med.values()) else "")
        return (f"<div style='font-size:.76rem;line-height:1.35' title='{_e(t['name'])}'>{_e(_trim(t['name'], 48))} "
                f"<span style='color:#51656a'>· {word(t)} {_e(short.get(t['highest'], t['highest']))}{vs}</span></div>")
    tops = "".join(inline(t) for t in top[:3])
    table = ""
    if top:
        th = ("padding:4px 8px;border-bottom:1px solid rgba(19,35,39,.15);text-align:left;font-size:.7rem;color:#51656a;font-weight:700;"
              "white-space:nowrap;word-break:normal;overflow-wrap:normal")
        td = "padding:4px 8px;border-bottom:1px solid rgba(19,35,39,.08);font-size:.74rem;vertical-align:top;word-break:normal;overflow-wrap:normal"
        num = td + ";text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums"
        heads = "".join(f"<th style='{th};text-align:right' title='{_e(c)}'>Median, {_e(short.get(c, c))}</th>" for c in cls_order)
        body = "".join(
            f"<tr><td style='{td};min-width:150px;max-width:260px'>{_e(t['name'])}</td>"
            f"<td style='{td};white-space:nowrap' title='{_e(t['highest'])}'>{_e(short.get(t['highest'], t['highest']))}</td>"
            + "".join(f"<td style='{num}'>{'–' if not t.get('medians') or t['medians'].get(c) is None else _num_short(t['medians'][c])}"
                      f"{_n_html(t, c)}</td>" for c in cls_order)
            + f"<td style='{num}'>{_sci(t['p'])}</td><td style='{num}'>{_sci(t.get('q'))}</td></tr>"
            for t in top)
        note = ("Medians of the deposited values, per class, over the units the test used (sample IDs; subjects when they are confirmed). "
                "“Higher in”: the class with the highest median; with confirmed paired or blocked subjects, the class that is higher within subjects. "
                f"p: the test's p-value; BH-adjusted p: Benjamini–Hochberg over all {d['judged']:,} tested features (significant when at most q = {r['fdr']:g}).")
        if any(t.get("medians") is None for t in top):
            note += " Medians are not shown for this matrix: its values are not kept with the page data."
        table = (f"<details style='margin-top:3px'><summary style='cursor:pointer;font-size:.74rem;color:#0d6e6e'>details: the {len(top)} strongest features</summary>"
                 f"<div style='overflow-x:auto;max-width:100%;margin-top:4px'><table style='border-collapse:collapse;width:auto'><tr><th style='{th}'>Feature</th>"
                 f"<th style='{th}'>Higher in</th>{heads}<th style='{th};text-align:right'>p</th><th style='{th};text-align:right'>BH-adjusted p</th></tr>"
                 f"{body}</table></div><div style='font-size:.72rem;color:#51656a;margin-top:3px;max-width:520px'>{note}</div></details>")
    return (f"<b>{d['share']:.1f}%</b><div style='{small}'>{d['significant']:,} of {d['judged']:,} tested features (q = {r['fdr']:g})"
            + (f"; {n_f - d['judged']:,} of {n_f:,} features not testable" if n_f > d["judged"] else "") + "</div>"
            + (f"<div style='{small};margin-top:3px'>Strongest (smallest p):</div>{tops}{table}" if tops else "") + head)


def _num_short(x: float) -> str:
    """A deposited value with 4 significant digits, thousands separated."""
    if x is None:
        return "–"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.4g}"


def _trim(text: str, n: int) -> str:
    text = str(text)
    return text if len(text) <= n else text[:n - 1] + "…"


def _short_classes(names: list) -> dict:
    """Class names without the factor parts shared by every class ('Sample type:Retina | Type:Control' -> 'Type:Control'), and without
    the factor name when every class is left with one value of the same factor ('Type:Control' -> 'Control'); the full name is on hover."""
    parts = {c: [x.strip() for x in str(c).split(" | ")] for c in names}
    common = set.intersection(*(set(v) for v in parts.values())) if len(parts) > 1 else set()
    left = {c: [x for x in v if x not in common] for c, v in parts.items()}
    factor = {x.split(":", 1)[0].strip() for v in left.values() for x in v if ":" in x}
    one = all(len(v) == 1 and ":" in v[0] for v in left.values()) and len(factor) == 1
    return {c: _trim((v[0].split(":", 1)[1].strip() if one else " | ".join(v)) or c, 40) for c, v in left.items()}


def _n_html(t: dict, c: str) -> str:
    return "" if not t.get("n") else f" <span style='color:#51656a'>(n {t['n'].get(c, 0):,})</span>"


def _sci(x) -> str:
    """A p-value: 0.0123 as 0.012; small values as 2.1×10<sup>−30</sup>."""
    if x is None or x != x:
        return "–"
    if x >= 0.001:
        return f"{x:.2g}"
    m, e = f"{x:.1e}".split("e")
    return f"{m}×10<sup>−{int(e[1:])}</sup>" if e.startswith("-") else f"{m}×10<sup>{int(e)}</sup>"


def _ml_cell_subjects(r: dict) -> str:
    if not r.get("subjects"):
        return "<span style='color:#51656a'>no subject IDs deposited</span>"
    top = r.get("most_shared_subject") or {}
    return (f"{r['subjects']:,} distinct subject IDs for {r['sample_ids_with_subject']:,} sample IDs"
            + (f"<div style='font-size:.74rem;color:#51656a'>most shared: “{_e(top['subject'][:60])}” on {top['sample_ids']:,}</div>" if top.get("sample_ids", 0) > 1 else "")
            + (f"<div style='font-size:.74rem;color:#51656a'>{r['subjects_with_several_samples']:,} with several sample IDs · "
               f"{r['subjects_in_several_classes']:,} in several classes</div>" if r.get("subjects_with_several_samples") else "")
            + (_red_note("⚠ Risk of subject leakage in train/test splits",
                         f"{r['subjects_with_several_samples']:,} subject IDs carry several sample IDs. If they are real subjects (people, animals or "
                         "sources), a random split can put samples of the same subject in both the training and the test set: the model is then "
                         "tested on subjects it has already seen, and accuracy is overestimated. scikit-learn's user guide (cross-validation for "
                         "grouped data): to know whether a model generalises to unseen subjects, “all the samples in the validation fold [must] come "
                         "from groups that are not represented at all in the paired training fold”: use grouped cross-validation (GroupKFold, "
                         "StratifiedGroupKFold) with the subject as the group. If the field holds a group code or a sample type instead (for "
                         "example one value on most samples), this does not apply: correct it in Adjust Matrix Properties.", "font-size:.76rem;margin-top:4px")
               if r.get("subjects_with_several_samples") else ""))


def ml_panel(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    d = _ml_study(study_id, params, overrides)
    if d is None:
        return f"<div style='{_DASHED}'>No valid matrix in this study.</div>"
    rows = ""
    for a in d["analyses"]:
        valid = [(k, lab) for k, lab in SOURCES if a["endpoints"][k].get("availability") == "valid"]
        if not valid:
            rows += (f"<tr><td style='{_TD};white-space:nowrap'><b>{_e(a['analysis_id'])}</b></td><td colspan='6' style='{_TD};color:#51656a;"
                     "font-style:italic'>no valid matrix on any endpoint</td></tr>")
            continue
        for i, (k, lab) in enumerate(valid):
            r = a["endpoints"][k].get("rec")
            first = f"<b>{_e(a['analysis_id'])}</b>" if i == 0 else ""
            if not r:
                rows += (f"<tr><td style='{_TD};white-space:nowrap'>{first}</td><td style='{_TD}'>{_e(lab)}</td><td colspan='5' style='{_TD};color:#51656a'>"
                         "not computed for this matrix</td></tr>")
                continue
            pn = "–" if r.get("features_per_sample") is None else f"{r['features_per_sample']:,.2f}"
            rows += (f"<tr class='v9-ml-row' data-analysis='{_e(a['analysis_id'])}' data-ep='{k}' "
                     f"data-class-separability='{'' if r.get('score') is None else format(r['score'], '.4f')}' "
                     f"data-chance='{'' if r.get('chance') is None else format(r['chance'], '.4f')}'>"
                     f"<td style='{_TD};white-space:nowrap'>{first}</td><td style='{_TD};white-space:nowrap'>{_e(lab)}</td>"
                     f"<td style='{_TD}'>{r['samples']:,} in {len(r['classes']):,} class{'es' if len(r['classes']) != 1 else ''}</td>"
                     f"<td class='v9-ml-sep' style='{_TD};min-width:170px'>{_ml_cell_sep(r)}</td>"
                     f"<td style='{_TD};min-width:220px'>{_ml_cell_disc(r)}</td>"
                     f"<td style='{_TD}'>{pn}<div style='font-size:.74rem;color:#51656a'>{r['n_features']:,} features ÷ {r['samples']:,} samples</div></td>"
                     f"<td style='{_TD};min-width:160px'>{_ml_cell_subjects(r)}</td></tr>")
    lab = d["labels"]
    selector = _lb_selector(study_id, lab) if lab.get("analyses") is not None and "items" in lab else ""
    return (ml_description(params) + selector + "<div class='v9-ml' style='margin:0 0 22px'>"
            "<h4 style='margin:0 0 4px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Class separability "
            f"<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Scored · {SPAN_DIR}higher = stronger local separation of the chosen classes</span> · "
            f"classes: {_e(_lb_choice_text(d['choice']))}</span></h4>"
            "<p style='font-size:.81rem;color:#51656a;margin:0 0 10px'>One row per matrix (analysis × endpoint), on the ML-eligible sample IDs "
            "in the classes in force. Only class separability is scored; the other columns are informational. Not an accuracy estimate: a batch effect aligned with the classes can raise it, and a low value does not mean a model cannot learn the classes.</p>"
            f"<div style='overflow-x:auto;contain:inline-size'><table style='border-collapse:collapse;width:100%;min-width:980px'><tr>"
            f"<th style='{_TH}'>Analysis</th><th style='{_TH}'>Endpoint</th><th style='{_TH}'>Samples</th><th style='{_TH}'>Class separability</th>"
            f"<th style='{_TH}'>Features associated with the selected classes (%)</th><th style='{_TH}'>p/n ratio (features ÷ samples)</th><th style='{_TH}'>Subjects</th></tr>"
            f"{rows}</table></div></div>")


def export_ml_block(study_id: str, params: dict | None = None, overrides: dict | None = None) -> dict | None:
    d = _ml_study(study_id, params, overrides)
    if d is None:
        return None
    k, theta, q = sp.settings(params)
    return {"schema": "merit.ml_task_readiness_analysis_endpoint.v1", "scored": ["class_separability"], "higher_is_better": True,
            "class_definition": _lb_choice_text(d["choice"]), "k": k, "theta": theta, "fdr": q, "sample_edits": overrides or {},
            "subjects_confirmed": sp.subjects_confirmed(params),
            "analyses": [{"analysis_id": a["analysis_id"], "endpoints": {k_: ({"availability": "valid", **{x: y for x, y in (e.get("rec") or {}).items()}}
                                                                              if e.get("availability") == "valid" else {"availability": e.get("availability")})
                                                                         for k_, e in a["endpoints"].items()}} for a in d["analyses"]]}


# The earlier assessment kept in the page data scored labels differently (class balance, group-size support, label entropy, with pass
# thresholds); 'label_structure' replaces them (2026-10-23), so these entries are dropped and cannot contradict the tab.
_SUPERSEDED_METRICS = {"class_balance", "group_size_support", "label_entropy"}
_SUPERSEDED_PARAMS = {"class_balance_pass", "group_support_strong", "group_support_moderate", "group_support_weak", "label_entropy_pass"}


_PREVIOUS_SCHEME_KEYS = ("readiness_score", "gate_feasibility", "section_metric_scores")


def drop_superseded(payload):
    """The page data and download carry only the current results: the previous scheme's overall readiness score, band and gates, and
    its per-metric scores, are removed (removed 2026-10-25 after a review; the v9 blocks hold every current result)."""
    if not isinstance(payload, dict):
        return payload
    for k in _PREVIOUS_SCHEME_KEYS:
        payload.pop(k, None)
    for item in (payload.get("source_assessments") or {}).values():
        if isinstance(item, dict):
            for k in ("readiness_score", "gate_feasibility", "metric_scores"):
                item.pop(k, None)
    keep = lambda rows: [m for m in rows or [] if not (isinstance(m, dict) and m.get("metric_id") in _SUPERSEDED_METRICS)]
    if isinstance(payload.get("section_metric_scores"), list):
        payload["section_metric_scores"] = keep(payload["section_metric_scores"])
    for item in (payload.get("source_assessments") or {}).values():
        if isinstance(item, dict) and isinstance(item.get("metric_scores"), list):
            item["metric_scores"] = keep(item["metric_scores"])
    params = (payload.get("assessment_settings") or {}).get("scoring_params")
    if isinstance(params, dict):
        for k in _SUPERSEDED_PARAMS:
            params.pop(k, None)
    return payload


def public(obj):
    """A copy for users: '_pilot' / 'v8_' removed from keys, server paths dropped, internal wording replaced."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and _SERVER_PATH.match(v):
                continue
            key = _PUBLIC_KEYS.get(k, k[:-len("_pilot")] if isinstance(k, str) and k.endswith("_pilot") else k)
            if key in ("frozen_source_dump",):
                continue
            out[key] = public(v)
        return out
    if isinstance(obj, list):
        return [public(v) for v in obj]
    if isinstance(obj, str):
        for rx, sub in _PUBLIC_TEXT:
            obj = rx.sub(sub, obj)
    return obj
