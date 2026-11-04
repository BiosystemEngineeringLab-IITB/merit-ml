"""Matrix readers of the manuscript, used by the UI so that both count sample rows and features the same way.

Ported from outputs/table4_descriptors_20260927/table4_descriptors.py (read_sample_by_feature, read_mwtab_matrix), the
script behind Table 4 of the manuscript. Every source is read POSITIONALLY:

* datatable and untarg_data: the first line is the header; column 1 is the sample ID, column 2 the class label, and
  every later column with a non-empty header is one feature. A header name that occurs more than once keeps each of
  its columns. A row is a sample row when its first cell is not empty. A row with more cells than the header is not
  read (it cannot be assigned to columns).
* mwTab: the non-EXTENDED ``*_DATA_START`` block. The header gives the sample IDs; every non-empty header position is
  one sample column, so an ID that occurs more than once keeps each of its columns (exception: when the value rows hold
  exactly one value per unique ID, the columns are the unique IDs in order). Every row with a non-empty name other than
  ``Factors`` is one feature, including a name that occurs more than once. The class label of a column is its cell in
  the ``Factors`` row, else the SUBJECT_SAMPLE_FACTORS text of that sample ID.

Both readers return rows in the samples x features layout the connector already uses: a list of dicts whose first key
holds the sample ID, whose second key holds the class label, and whose remaining keys are the features. A repeated
feature name gets a key made unique with ``DUP`` + its position; ``display_name`` gives back the deposited name.
"""
from __future__ import annotations

import gzip
from pathlib import Path

DUP = "\x1f"                      # unit separator: never occurs in a deposited name (asserted when reading)
SAMPLE_KEY = "_sample_id"
CLASS_KEY = "_class"


def display_name(key: object) -> str:
    """Deposited name of a feature key (the key without the position suffix of a repeated name)."""
    return str(key if key is not None else "").split(DUP, 1)[0]


def _unique_keys(names: list[str], taken: set[str]) -> list[str]:
    out = []
    for k, name in enumerate(names):
        key = name.replace(DUP, " ")
        if key in taken:
            key = f"{key}{DUP}{k}"
        taken.add(key)
        out.append(key)
    return out


def _lines(path: Path) -> list[str]:
    # split on "\n" only: a stray control character inside a feature name must not break a line
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
        return handle.read().replace("\r\n", "\n").split("\n")


def read_sample_by_feature(path: Path) -> list[dict[str, str]]:
    """datatable / untarg_data: rows = samples, columns 3.. = features, read by position."""
    lines = _lines(Path(path))
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return []
    header = lines[0].split("\t")
    if len(header) < 2:
        return []
    positions = [k for k in range(2, len(header)) if header[k].strip()]
    taken: set[str] = set()
    first_key, class_key = _unique_keys([header[0], header[1]], taken)
    feature_keys = _unique_keys([header[k] for k in positions], taken)
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) > len(header) or not cells[0].strip():
            continue
        row = {first_key: cells[0], class_key: cells[1] if len(cells) > 1 else ""}
        n = len(cells)
        for key, k in zip(feature_keys, positions):
            row[key] = cells[k] if k < n else ""
        rows.append(row)
    return rows


def mwtab_blocks(lines: list[str]) -> list[list[str]]:
    """Non-EXTENDED quantitative blocks of an mwTab file; a block identical to an earlier one is listed once."""
    blocks, seen, i = [], set(), 0
    while i < len(lines):
        start = lines[i].strip()
        if start.endswith("_DATA_START") and not start.startswith("EXTENDED"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().endswith("_DATA_END"):
                j += 1
            body = "\n".join(lines[i + 1:j])
            if body not in seen:
                seen.add(body)
                blocks.append(lines[i + 1:j])
            i = j
        i += 1
    return blocks


def _mwtab_layout(path: Path) -> tuple[list[int], list[str], list[str], list[list[str]]]:
    """(value-column positions, sample IDs, class labels, feature rows) of the first distinct quantitative block."""
    lines = _lines(Path(path))
    ssf: dict[str, str] = {}
    for line in lines:
        if line.startswith("SUBJECT_SAMPLE_FACTORS"):
            cells = line.split("\t")
            if len(cells) > 3:
                ssf.setdefault(cells[2].strip(), cells[3])
    blocks = mwtab_blocks(lines)
    if not blocks or not blocks[0]:
        return [], [], [], []
    block = blocks[0]
    header = [x.strip() for x in block[0].split("\t")[1:]]
    positions = [k for k, x in enumerate(header) if x]
    body = [line.split("\t") for line in block[1:]]
    factors = next((cells for cells in body if cells[0].strip().lower() == "factors"), None)
    feature_rows = [cells for cells in body if cells[0].strip() and cells[0].strip().lower() != "factors"]
    unique_ids = list(dict.fromkeys(header[k] for k in positions))
    width = max((len(cells) - 1 for cells in feature_rows), default=len(header))
    if len(unique_ids) < len(positions) and width == len(unique_ids):
        columns, samples = list(range(len(unique_ids))), unique_ids      # repeated header, one value column per unique ID
    else:
        columns, samples = positions, [header[k] for k in positions]
    labels = [factors[k + 1] if factors is not None and k + 1 < len(factors) else ssf.get(sample_id, "")
              for k, sample_id in zip(columns, samples)]
    return columns, samples, labels, feature_rows


def read_mwtab_block(path: Path) -> list[dict[str, str]]:
    """mwTab quantitative block as samples x features rows, read by position (first distinct block)."""
    columns, samples, labels, feature_rows = _mwtab_layout(path)
    feature_keys = _unique_keys([cells[0] for cells in feature_rows], {SAMPLE_KEY, CLASS_KEY})
    rows: list[dict[str, str]] = []
    for k, sample_id, label in zip(columns, samples, labels):
        row = {SAMPLE_KEY: sample_id, CLASS_KEY: label}
        for key, cells in zip(feature_keys, feature_rows):
            row[key] = cells[k + 1].strip() if k + 1 < len(cells) else ""
        rows.append(row)
    return rows


def sample_rows_of_sample_by_feature(path: Path) -> list[tuple[str, str]]:
    """(sample ID, class label) of every sample row of a datatable / untarg_data file, without reading the values."""
    lines = _lines(Path(path))
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or lines[0].count("\t") < 1:
        return []
    width = lines[0].count("\t") + 1
    out = []
    for line in lines[1:]:
        if not line.strip() or line.count("\t") + 1 > width:
            continue
        cells = line.split("\t", 2)
        if cells[0].strip():
            out.append((cells[0].strip(), cells[1] if len(cells) > 1 else ""))
    return out


def sample_rows_of_mwtab(path: Path) -> list[tuple[str, str]]:
    """(sample ID, class label) of every sample column of the mwTab quantitative block."""
    _, samples, labels, _ = _mwtab_layout(path)
    return [(str(sample_id).strip(), str(label)) for sample_id, label in zip(samples, labels)]


def self_test() -> None:
    assert display_name("glucose" + DUP + "7") == "glucose" and display_name("glucose") == "glucose"
    assert _unique_keys(["a", "b", "a", "a"], set()) == ["a", "b", "a" + DUP + "2", "a" + DUP + "3"]
    lines = ["MS_METABOLITE_DATA_START", "Samples\tS1\tS1\tS2", "Factors\tg:a\tg:a\tg:b", "x\t1\t2\t3", "x\t4\t5\t6",
             "MS_METABOLITE_DATA_END"]
    assert mwtab_blocks(lines + lines) == [lines[1:5]]


self_test()
