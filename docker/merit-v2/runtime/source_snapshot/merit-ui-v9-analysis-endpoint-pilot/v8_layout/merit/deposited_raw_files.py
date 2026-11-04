"""Deposited raw-data declarations of a Metabolomics Workbench study, read from its mwTab text files.

Used by the FAIR raw-data check. Two declarations count, and either one is enough:
  - AN:DATA_FORMAT with a value;
  - a raw file named for a sample: any key that starts with RAW_FILE in the additional sample data of a
    SUBJECT_SAMPLE_FACTORS line (RAW_FILE_NAME, RAW_FILE_NAME(Raw file name), raw_file, ...), with a value.
A declaration documents the raw data; it does not show that the files can be downloaded. Nothing is inferred.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_BLANK = {"", "-", "na", "n/a", "nan", "null", "none", "raw data file"}
_STOP = re.compile(r"^(?:[A-Z_]+_DATA_START|METABOLITES_START)\b")
_RAW_KEY = re.compile(r"^\s*RAW_FILE", re.I)
_EXTENSION = re.compile(r"(\.[A-Za-z0-9]{1,8})$")


def declarations_of_file(path: Path) -> dict[str, Any]:
    data_format: list[str] = []
    sample_lines = named = 0
    extensions: set[str] = set()
    with Path(path).open("rt", encoding="utf-8", errors="replace", newline="") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if _STOP.match(line):
                break
            cells = line.split("\t")
            tag = cells[0].strip()
            if tag == "AN:DATA_FORMAT" and len(cells) > 1 and cells[1].strip():
                data_format.append(cells[1].strip())
            elif tag == "SUBJECT_SAMPLE_FACTORS" and len(cells) > 2 and cells[2].strip():
                sample_lines += 1
                values = [pair.split("=", 1)[1].strip() for pair in (cells[4] if len(cells) > 4 else "").split(";")
                          if "=" in pair and _RAW_KEY.match(pair.split("=", 1)[0])]
                values = [value for value in values if value.casefold() not in _BLANK]
                if values:
                    named += 1
                    for value in values:
                        found = _EXTENSION.search(value)
                        if found:
                            extensions.add(found.group(1).lower())
    text = " ".join(data_format)
    return {
        "analysis_id": Path(path).name.split("_")[0],
        "data_format": "" if text.casefold() in _BLANK else text,
        "sample_lines": sample_lines,
        "samples_with_raw_file": named,
        "raw_file_extensions": sorted(extensions),
    }


@lru_cache(maxsize=64)
def declarations_of_study_root(study_root: str) -> list[dict[str, Any]] | None:
    """One record per mwTab text file of the study; None when the study has no such file."""
    files = sorted(Path(study_root).glob("AN*/mwtab/AN*_mwtab.txt"))
    if not files:
        return None
    out = []
    for path in files:
        try:
            out.append(declarations_of_file(path))
        except OSError:
            continue
    return out


def of_study(study: Any) -> list[dict[str, Any]] | None:
    study_id = str(getattr(study.study, "study_id", "") or "").strip().upper()
    source_root = str(getattr(study.provenance, "source_root", "") or "").strip()
    if not study_id or not source_root:
        return None
    return declarations_of_study_root(str(Path(source_root).expanduser() / study_id))
