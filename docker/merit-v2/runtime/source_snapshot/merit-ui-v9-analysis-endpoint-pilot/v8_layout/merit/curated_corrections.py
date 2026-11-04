"""Curated corrections of the 16-keyword QC/blank rule, one row per decision in curated_control_corrections.tsv.

Three kinds of decision, each with its reason, who decided and when:
  'not a QC or blank sample'  a keyword match that names something else (e.g. a tube type). Matched by a factor:value of
                              the label on the position (the position is corrected only when EVERY keyword match it holds
                              is a listed factor:value of that study, and a keyword in the sample ID is then not
                              overridden), or by an explicit whole-sample-ID pattern, the only way a keyword in a sample ID
                              is overridden.
  'blank'                     a blank that the five blank keywords miss (e.g. Blk, BK, PBS Control).
  'standard'                  an analytical standard (calibration or external standard, standard mix): standard-like, not
                              an ML-eligible sample.
  'qc'                        a QC sample that the 16 keywords miss because the keyword is only in a field the rule does not
                              read (e.g. Sample type=CHEAR reference in the additional sample data): QC-like.
                              The three additions are matched by a factor:value of the label on the position OR of the sample's
                              line in the sample table of the mwTab file, or by a whole-sample-ID pattern, and are applied
                              only to positions the keyword rule does not flag.
Nothing else about the rule changes. Every corrected position keeps its correction in the cache and on the page.
The MERIT UI therefore differs from the manuscript's counts for the studies listed in the table.
"""
from __future__ import annotations

import csv
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from merit.manuscript_sample_rules import QC_BLANK_RE

TABLE = Path(os.environ.get("MERIT_CURATED_CORRECTIONS", Path(__file__).resolve().parent / "curated_control_corrections.tsv"))   # the environment variable is for comparison builds only
NOT_A_CONTROL = "not a QC or blank sample"
BLANK = "blank"
STANDARD = "standard"
QC = "qc"
ADDITIONS = (BLANK, STANDARD, QC)


@lru_cache(maxsize=1)
def corrections() -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    with open(TABLE, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            assert row["decision"] in (NOT_A_CONTROL,) + ADDITIONS and row["match_on"] in ("factor_value", "sample_id_pattern"), row
            out.setdefault(row["study_id"].strip().upper(), []).append(row)
    return out


def _pairs(label: Any) -> list[tuple[str, str]]:
    out = []
    for part in str(label or "").split("|"):
        name, value = (part.split(":", 1) if ":" in part else ("", part))
        out.append((name.strip().casefold(), value.strip().casefold()))
    return out


def _factor_row(rows: list[dict[str, str]], decision: str, name: str, value: str) -> dict[str, str] | None:
    return next((r for r in rows if r["decision"] == decision and r["match_on"] == "factor_value"
                 and r["factor"].strip().casefold() == name and r["value"].strip().casefold() == value), None)


def correction_for(study_id: Any, sample_id: Any, label: Any) -> dict[str, str] | None:
    """For a keyword-flagged position or record: the 'not a QC or blank sample' row that removes every keyword match, or None."""
    rows = corrections().get(str(study_id or "").strip().upper())
    if not rows:
        return None
    sid = str(sample_id or "").strip()
    for r in rows:          # an explicit decision for one sample ID (whole-ID match) overrides a keyword in that ID
        if r["decision"] == NOT_A_CONTROL and r["match_on"] == "sample_id_pattern" and re.fullmatch(r["sample_id_pattern"], sid):
            return r
    if QC_BLANK_RE.search(sid):
        return None
    hits = [(n, v) for n, v in _pairs(label) if QC_BLANK_RE.search(v)]
    if not hits:
        return None
    matched = None
    for name, value in hits:
        row = _factor_row(rows, NOT_A_CONTROL, name, value)
        if row is None:
            return None
        matched = row
    return matched


def addition_for(study_id: Any, sample_id: Any, label: Any, table_label: Any = "") -> dict[str, str] | None:
    """For a position or record the rule does not flag: the 'blank', 'standard' or 'qc' row that names it, or None.
    label = label on the position; table_label = the sample's line in the sample table (factors), when known."""
    rows = corrections().get(str(study_id or "").strip().upper())
    if not rows:
        return None
    sid = str(sample_id or "").strip()
    pairs = _pairs(label) + _pairs(table_label)
    for r in rows:
        if r["decision"] not in ADDITIONS:
            continue
        if r["match_on"] == "sample_id_pattern" and re.fullmatch(r["sample_id_pattern"], sid):
            return r
        if r["match_on"] == "factor_value" and (r["factor"].strip().casefold(), r["value"].strip().casefold()) in pairs:
            return r
    return None


def blank_for(study_id: Any, sample_id: Any, label: Any, table_label: Any = "") -> dict[str, str] | None:
    """Kept for callers of the earlier version: an addition whose decision is 'blank'."""
    row = addition_for(study_id, sample_id, label, table_label)
    return row if row and row["decision"] == BLANK else None


def describe(row: dict[str, str]) -> str:
    return f"{row['factor']}: {row['value']}" if row["match_on"] == "factor_value" else f"sample ID {row['sample_id_pattern']}"
