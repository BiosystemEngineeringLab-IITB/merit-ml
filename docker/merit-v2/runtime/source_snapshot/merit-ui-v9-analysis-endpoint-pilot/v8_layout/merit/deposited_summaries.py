"""Deposited PROJECT_SUMMARY and STUDY_SUMMARY of a Metabolomics Workbench study, read from its mwTab text files.

Used by the FAIR description check: the check passes when either summary has at least 20 words.
A summary that continues over several lines of the same tag is joined with single spaces. Words are
whitespace-separated tokens. Nothing is inferred: only the text of these two fields is counted, and where
the mwTab files of a study differ, the longest deposited text of each field is taken.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

FIELDS = {"PR:PROJECT_SUMMARY": "project_summary", "ST:STUDY_SUMMARY": "study_summary"}
_TAG = re.compile(r"^(PR:PROJECT_SUMMARY|ST:STUDY_SUMMARY)(?:\s+(.*))?$")
# the header ends where the first data block starts; an empty template at the top of a file does not end it
_STOP = re.compile(r"^(?:[A-Z_]+_DATA_START|METABOLITES_START)\b")


def summaries_of_file(path: Path) -> dict[str, str]:
    parts: dict[str, list[str]] = {name: [] for name in FIELDS.values()}
    with Path(path).open("rt", encoding="utf-8", errors="replace", newline="") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if _STOP.match(line):
                break
            found = _TAG.match(line)
            if found and (found.group(2) or "").strip():
                parts[FIELDS[found.group(1)]].append(found.group(2).strip())
    return {name: " ".join(texts) for name, texts in parts.items()}


@lru_cache(maxsize=64)
def summaries_of_study_root(study_root: str) -> dict[str, dict[str, Any]] | None:
    """Longest deposited text of each field over the study's mwTab files; None when the study has no mwTab file."""
    files = sorted(Path(study_root).glob("AN*/mwtab/AN*_mwtab.txt"))
    if not files:
        return None
    best = {name: {"words": 0, "analysis_id": "", "text": ""} for name in FIELDS.values()}
    for path in files:
        try:
            texts = summaries_of_file(path)
        except OSError:
            continue
        for name, text in texts.items():
            words = len(text.split())
            if words > best[name]["words"]:
                best[name] = {"words": words, "analysis_id": path.name.split("_")[0], "text": text}
    return best


def of_study(study: Any) -> dict[str, dict[str, Any]] | None:
    """Summaries of a canonical study; None when it is not a local Metabolomics Workbench study."""
    study_id = str(getattr(study.study, "study_id", "") or "").strip().upper()
    source_root = str(getattr(study.provenance, "source_root", "") or "").strip()
    if not study_id or not source_root:
        return None
    return summaries_of_study_root(str(Path(source_root).expanduser() / study_id))
