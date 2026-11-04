"""Additional sample data of an analysis: the fifth field of the active SUBJECT_SAMPLE_FACTORS lines of its mwTab text
file ("key=value; key=value"), or the "Additional sample data" of the mwTab JSON when no text file was deposited.

Every field is reported with the number of sample-table records that give it a value, its number of distinct values and
its first two distinct values, in the order the fields first appear. Nothing is inferred from a field's name or values.
A field is not repeated when the page already shows it, and the reason is given:
  raw file name           a key starting with RAW_FILE (FAIR raw-data check, Metadata)
  no value                every record leaves it empty
  same name as a factor   the factors are the class labels of the study
  same values as a factor in every record with a value, the value of one factor of the same record
  same as the sample ID / same as the subject ID (the subject IDs are counted in Structural)
Records, not unique samples: a sample listed twice counts twice.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SSF_LINE = re.compile(r"^SUBJECT_SAMPLE_FACTORS\b")
RAW_FILE_KEY = re.compile(r"^\s*RAW_FILE", re.I)
# a ';' separates two fields only when the text after it holds '=' before the next ';' (a raw-file value can list files
# separated by ';') and it does not end an HTML entity of the deposit (for example "Days to &gt;1000 Copies")
FIELD_SPLIT = re.compile(r"(?<!&gt)(?<!&lt)(?<!&amp)(?<!&quot)(?<!&apos)(?<!&nbsp);(?=[^;=]*=)")
EXAMPLES = 2

NOT_REPEATED = {"raw": "raw file name, shown in the FAIR raw-data check (Metadata)", "empty": "no value in any record",
                "factor_name": "same name as a factor", "factor_values": "same values as the factor {}",
                "sample": "same as the sample ID", "subject": "same as the subject ID (counted in Structural)"}


def fields_of(cell: str) -> list[tuple[str, str]]:
    out = []
    for part in FIELD_SPLIT.split(cell or ""):
        if "=" in part:
            key, value = part.split("=", 1)
            if key.strip():
                out.append((key.strip(), value.strip()))
    return out


def records_from_text(lines: list[str]) -> list[dict[str, Any]]:
    out = []
    for line in lines:
        if not SSF_LINE.match(line):
            continue
        cells = line.rstrip("\r").split("\t")
        factors = {}
        for part in (cells[3].split("|") if len(cells) > 3 else []):
            if ":" in part:
                name, value = part.split(":", 1)
                factors[name.strip()] = value.strip()
        out.append({"subject": cells[1].strip() if len(cells) > 1 else "", "sample": cells[2].strip() if len(cells) > 2 else "",
                    "factors": factors, "fields": fields_of(cells[4] if len(cells) > 4 else "")})
    return out


def records_from_json(entries: list[Any]) -> list[dict[str, Any]]:
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        f, x = e.get("Factors"), e.get("Additional sample data")
        out.append({"subject": str(e.get("Subject ID") or "").strip(), "sample": str(e.get("Sample ID") or "").strip(),
                    "factors": {str(k).strip(): str(v).strip() for k, v in (f.items() if isinstance(f, dict) else [])},
                    "fields": [(str(k).strip(), str(v).strip()) for k, v in (x.items() if isinstance(x, dict) else []) if str(k).strip()]})
    return out


def read(folder: Path, analysis_id: str) -> tuple[str, list[dict[str, Any]]]:
    """(where read, records) for the mwTab files of one analysis: the text file, else the JSON, else nothing."""
    text, js = Path(folder) / f"{analysis_id}_mwtab.txt", Path(folder) / f"{analysis_id}_mwtab.json"
    if text.is_file():
        return "mwTab text file", records_from_text(text.read_text(encoding="utf-8", errors="replace").split("\n"))
    if js.is_file():
        try:
            return "mwTab JSON (no text file deposited)", records_from_json(json.loads(js.read_text()).get("SUBJECT_SAMPLE_FACTORS"))
        except (json.JSONDecodeError, AttributeError):
            return "mwTab JSON could not be read", []
    return "no mwTab file", []


def _why_not(key: str, pairs: list[tuple[dict, str]]) -> str | None:
    given = [(r, v) for r, v in pairs if v != ""]
    if RAW_FILE_KEY.match(key):
        return NOT_REPEATED["raw"]
    if not given:
        return NOT_REPEATED["empty"]
    names = {n.casefold() for r, _ in pairs for n in r["factors"]}
    if key.casefold() in names:
        return NOT_REPEATED["factor_name"]
    for name in sorted({n for r, _ in given for n in r["factors"]}):
        if all(r["factors"].get(name) == v for r, v in given):
            return NOT_REPEATED["factor_values"].format(name)
    if all(v == r["sample"] for r, v in given):
        return NOT_REPEATED["sample"]
    if all(v == r["subject"] for r, v in given):
        return NOT_REPEATED["subject"]
    return None


def summarise(read_from: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[str, list[tuple[dict, str]]] = {}
    for r in records:
        for key, value in r["fields"]:
            by_key.setdefault(key, []).append((r, value))
    fields, not_repeated = [], []
    for key, pairs in by_key.items():
        values = [v for _, v in pairs if v != ""]
        why = _why_not(key, pairs)
        entry = {"name": key, "records_with_value": len(values)}
        if why:
            not_repeated.append({**entry, "reason": why})
        else:
            fields.append({**entry, "distinct_values": len(set(values)), "examples": list(dict.fromkeys(values))[:EXAMPLES]})
    return {"read_from": read_from, "records": len(records), "fields": fields, "not_repeated": not_repeated}
