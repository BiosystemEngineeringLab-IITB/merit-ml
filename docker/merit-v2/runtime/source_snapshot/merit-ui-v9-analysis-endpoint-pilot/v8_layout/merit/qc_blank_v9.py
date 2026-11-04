"""Sample eligibility & analytical controls (formerly "QC / blank controls") of one matrix (one analysis from one endpoint): counts and a presence label, nothing scored.

One set of functions serves the v9 cache builder, the page and the download:
    position_kinds(roster, overrides)      what each position of the matrix is
    compute(roster, overrides)             QC-like and blank-like positions, where the keyword was found, presence label
    sample_table_controls(text_lines)      QC-like and blank-like records of the sample table of the mwTab text file
With no sample edits, `compute` returns exactly what the cache stores.

A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
One kind per SAMPLE ID (decided 2026-10-21; not matrix-specific): sample_kind() reads the sample ID and its line in the sample
table of the analysis (SUBJECT_SAMPLE_FACTORS of the mwTab text file; the label on the position only for a sample ID that has no
line there) with the 16 keywords and the curated table:
  a keyword in the sample ID decides alone   blank-like if one of the five blank keywords is in it, QC-like otherwise
  else a keyword in the factor values        blank-like if a blank keyword is among them, QC-like otherwise
  else a curated addition                    blank-like, standard-like or QC-like; a curated "not a control" row overrides a keyword
Every position of that sample ID has that kind, in every endpoint; Tier 1 sample alignment rows hold no measurement and are
never counted among the measured positions. The roster field qc_blank (ML eligibility) is this rule; its few differences from the
manuscript rule (label on the position) are listed per matrix in sample_table_rule_changes.
A position that a sample edit marks not ML-eligible, without being a control, is set aside: neither a sample nor a control.
"QC-like" and "blank-like" are keyword matches in deposited names, not verified analytical controls.
"""
from __future__ import annotations

import re
from typing import Any

from merit import structural_v9 as sv
from merit.curated_corrections import addition_for, correction_for, describe
from merit.manuscript_sample_rules import BLANK_KEYWORDS, BLANK_RE, QC_BLANK_RE, QC_KEYWORDS, label_values

PARAMETERS: dict[str, tuple[float, float, float, float]] = {}      # nothing to adjust: counts and a label only
DEFAULT_PARAMS: dict[str, float] = {}
KEYWORDS = {"qc": tuple(QC_KEYWORDS), "blank": tuple(BLANK_KEYWORDS)}
PRESENCE = {(True, True): "QC-like and blank-like", (True, False): "QC-like only", (False, True): "Blank-like only", (False, False): "Neither identified"}
MAX_IDS = 200        # sample IDs listed per matrix and kind; the counts are always complete

SSF_LINE = re.compile(r"^SUBJECT_SAMPLE_FACTORS\b")


def is_blank(sample_id: Any, label_on_position: Any) -> bool:
    """One of the five blank keywords in the sample ID or in the values of the label on the position."""
    return bool(BLANK_RE.search(str(sample_id or "")) or BLANK_RE.search(label_values(str(label_on_position or ""))))


def rule_factors(row: list) -> str:
    """The factors the rule reads for a position: the sample's line in the sample table, else the label on the position."""
    return row[sv.I_TLABEL] if row[sv.I_TABLE] else row[sv.I_LABEL]


def _decision(study_id: Any, sample_id: Any, factors: Any) -> tuple[str, dict | None]:
    sid = str(sample_id or "").strip()
    if not (QC_BLANK_RE.search(sid) or QC_BLANK_RE.search(label_values(str(factors or "")))):
        add = addition_for(study_id, sid, factors)
        return (add["decision"], add) if add else ("sample", None)
    removed = correction_for(study_id, sid, factors)
    if removed:
        return "sample", removed
    if QC_BLANK_RE.search(sid):
        return ("blank" if BLANK_RE.search(sid) else "qc"), None
    return ("blank" if BLANK_RE.search(label_values(str(factors or ""))) else "qc"), None


def sample_kind(study_id: Any, sample_id: Any, factors: Any) -> str:
    """'qc', 'blank', 'standard' or 'sample' for one sample ID from its factors (see the module text)."""
    return _decision(study_id, sample_id, factors)[0]


def found_where(study_id: Any, sample_id: Any, factors: Any) -> str:
    """What decided a control: "sample ID", the factor(s) whose value holds the keyword, or the curated row."""
    kind, row = _decision(study_id, sample_id, factors)
    if row is not None and kind != "sample":
        return f"curated: {describe(row)}"
    if QC_BLANK_RE.search(str(sample_id or "")):
        return "sample ID"
    return where_found("", factors)


def where_found(sample_id: Any, label_on_position: Any) -> str:
    """Where the keyword is: the sample ID, or the factor (name: value) of the label whose value holds it."""
    places = []
    if QC_BLANK_RE.search(str(sample_id or "")):
        places.append("sample ID")
    for part in str(label_on_position or "").split("|"):
        name, value = (part.split(":", 1) if ":" in part else ("label", part))
        if QC_BLANK_RE.search(value.strip()):
            places.append(f"{name.strip() or 'label'}: {value.strip()}")
    return "; ".join(places)


def position_kinds(roster: list[list], overrides: dict[str, dict[str, Any]] | None = None, study_id: str = "") -> list[str]:
    """Per position of the roster: 'alignment', 'excluded', 'qc', 'blank', 'standard', 'sample' or 'set_aside'.
    'standard' and curated 'blank' come from the curated table (decision of a flagged position the keyword rule would leave a sample)."""
    kinds = []
    for p in sv.effective_positions(roster, overrides):
        row = p["row"]
        if row[sv.I_ALIGN]:
            kinds.append("alignment")
        elif p["excluded"]:
            kinds.append("excluded")
        elif p["eligible"]:
            kinds.append("sample")
        else:
            k = sample_kind(study_id, row[sv.I_ID], rule_factors(row))
            kinds.append("set_aside" if k == "sample" else k)
    return kinds


def _keyword(row: list) -> bool:
    return bool(QC_BLANK_RE.search(str(row[sv.I_ID] or "")) or QC_BLANK_RE.search(label_values(str(row[sv.I_LABEL] or ""))))


def listed(roster: list[list], overrides: dict[str, dict[str, Any]] | None = None, study_id: str = "",
           kinds: list[str] | None = None) -> dict[str, list[dict[str, str]]]:
    """Every QC-like, blank-like and standard-like position (no cap), with its sample ID and where the keyword was found."""
    kinds = position_kinds(roster, overrides, study_id) if kinds is None else kinds
    return {kind: [{"sample_id": row[sv.I_ID], "found_in": found_where(study_id, row[sv.I_ID], rule_factors(row))}
                   for row, k in zip(roster, kinds) if k == kind] for kind in ("qc", "blank", "standard")}


def keywords_found(kind: str, item: dict[str, str]) -> list[tuple[str, list[str]]]:
    """(place, keywords) for each place of item["found_in"]: the keywords of the rule found there (the five blank keywords for a
    blank-like position, the 16 QC/blank keywords otherwise). Empty for a curated position."""
    if str(item.get("found_in", "")).startswith("curated:"):
        return []
    rx = BLANK_RE if kind == "blank" else QC_BLANK_RE
    out = []
    for place in str(item.get("found_in", "")).split("; "):
        text = item["sample_id"] if place == "sample ID" else (place.split(": ", 1)[1] if ": " in place else place)
        words = list(dict.fromkeys(m.group(0) for m in rx.finditer(str(text))))
        if words:
            out.append((place, words))
    return out


def compute(roster: list[list], overrides: dict[str, dict[str, Any]] | None = None, study_id: str = "") -> dict[str, Any]:
    """QC-like and blank-like positions of the matrix, with their sample IDs and where the keyword was found."""
    kinds = position_kinds(roster, overrides, study_id)
    lists = listed(roster, overrides, study_id, kinds)
    qc, blank, standard = lists["qc"], lists["blank"], lists["standard"]
    aligned = [row for row in roster if row[sv.I_ALIGN]]
    return {
        "positions": len(roster),
        "tier1_alignment_rows": len(aligned),
        "tier1_alignment_rows_named_qc_or_blank": sum(1 for row in aligned if row[sv.I_QC]),
        "excluded_positions": kinds.count("excluded"),
        "measured_positions": len(roster) - len(aligned) - kinds.count("excluded"),
        "qc_like_positions": len(qc),
        "blank_like_positions": len(blank),
        "standard_like_positions": len(standard),
        "sample_positions": kinds.count("sample"),
        "set_aside_positions": kinds.count("set_aside"),
        "keyword_positions_marked_eligible": sum(1 for row, k in zip(roster, kinds) if k == "sample" and row[sv.I_QC]),
        "presence": PRESENCE[(bool(qc), bool(blank))],
        "qc_like": qc[:MAX_IDS],
        "blank_like": blank[:MAX_IDS],
        "standard_like": standard[:MAX_IDS],
        "edited_positions": sum(1 for p in sv.effective_positions(roster, overrides) if p["edited"]),
    }


def sample_table_controls(text_lines: list[str], study_id: str = "") -> dict[str, Any]:
    """Controls of the sample table (active SUBJECT_SAMPLE_FACTORS lines) of an mwTab text file, with sample_kind(): records
    (a sample listed twice counts twice) and the distinct sample IDs of each kind."""
    rows = 0
    records = {"qc": 0, "blank": 0, "standard": 0}
    ids: dict[str, dict[str, None]] = {"qc": {}, "blank": {}, "standard": {}}
    curated = added = 0
    for line in text_lines:
        if not SSF_LINE.match(line):
            continue
        cells = line.split("\t")
        rows += 1
        sample_id = cells[2].strip() if len(cells) > 2 else ""
        factors = cells[3] if len(cells) > 3 else ""
        kind, row = _decision(study_id, sample_id, factors)
        if row is not None:
            curated += kind == "sample"
            added += kind == "blank"
        if kind != "sample":
            records[kind] += 1
            ids[kind][sample_id] = None
    return {"records": rows, "qc_like_records": records["qc"], "blank_like_records": records["blank"], "standard_like_records": records["standard"],
            "records_not_controls_by_curated_correction": curated, "records_blank_by_curated_correction": added,
            "qc_like_sample_ids": list(ids["qc"]), "blank_like_sample_ids": list(ids["blank"]), "standard_like_sample_ids": list(ids["standard"])}