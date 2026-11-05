"""Per-position cell counts of a deposited matrix, in the position order of the v9 roster (merit-ui-v9 build_v9_cache.py).

A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
For every position, the counts in FIELDS:
  numeric                a number as the manuscript reads it (also '1.80.E-04'); zeros are numeric
  the six classes of Table S2C for an entry that is not a number, assigned by the manuscript's own classifier
  (outputs/table4_descriptors_20260927/missing_token_scan.py::klass): empty (also a cell missing at the end of a short row),
  missing-value token, detection-limit entry, non-finite number, malformed number, other text
  zero                   numeric cells equal to 0
The six classes together are the manuscript's "empty or non-numeric entries" (Table 4); zero is "zero entries".
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from merit.manuscript_matrix_readers import _lines, _mwtab_layout

sys.path.insert(0, "/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/outputs/table4_descriptors_20260927")
from missing_token_scan import ORDER, klass  # noqa: E402  (Table S2C classes, in the manuscript's order)

N = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
NUM = re.compile(rf"^{N}$")
ROW = re.compile(rf"^(?: *{N} *\t)* *{N} *$")
REPAIRED = re.compile(r"^([+-]?\d+\.\d+)\.([eE][+-]?\d+)$")
FIELDS = ("numeric", "empty", "missing-value token", "detection-limit entry", "non-finite number", "malformed number", "other text", "zero")
assert tuple(ORDER) == FIELDS[1:7], ORDER
ZERO = len(FIELDS) - 1
_kind: dict[str, int] = {}


def _count(t: str, n: list[int]) -> None:
    if NUM.match(t):
        n[0] += 1
        n[ZERO] += float(t) == 0
        return
    m = REPAIRED.match(t)
    if m:
        n[0] += 1
        n[ZERO] += float(m.group(1) + m.group(2)) == 0
        return
    k = _kind.get(t)
    if k is None:
        k = _kind[t] = 1 + ORDER.index(klass(t))
    n[k] += 1


def counts_of(path: Path, src: str) -> tuple[list[tuple[str, list[int]]], int]:
    """[(sample ID, counts in FIELDS order)] in file order, and the number of features."""
    out = []
    if src == "mwtab":
        columns, samples, labels, frows = _mwtab_layout(path)
        for k, s in zip(columns, samples):
            n = [0] * len(FIELDS)
            for r in frows:
                _count(r[k + 1].strip() if k + 1 < len(r) else "", n)
            out.append((str(s).strip(), n))
        return out, len(frows)
    lines = _lines(path)
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].count("\t") < 1:
        return out, 0
    header = lines[0].split("\t")
    pos = [k for k in range(2, len(header)) if header[k].strip()]
    contiguous = pos == list(range(2, len(header)))
    width = len(header)
    for line in lines[1:]:
        if not line.strip():
            continue
        m = line.count("\t") + 1
        if m > width:
            continue
        head = line.split("\t", 2)
        if not head[0].strip():
            continue
        n = [0] * len(FIELDS)
        if contiguous and m == width and len(head) == 3 and ROW.match(head[2]):
            vals = head[2].split("\t")
            n[0] = len(vals)
            n[ZERO] = sum(1 for v in vals if float(v) == 0)
        else:
            c = line.split("\t")
            for k in pos:
                _count(c[k].strip() if k < m else "", n)
        out.append((head[0].strip(), n))
    return out, len(pos)
