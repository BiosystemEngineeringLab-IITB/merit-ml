"""Sample rules of the manuscript, used by the UI so that both count samples the same way.

Three rules, ported without change from the scripts behind Table 4 of the manuscript:

1. QC/blank filter (Text S2, Table S14): 16 keywords, matched in the sample ID and in the VALUES of the class label
   (not in the factor names), ignoring capitalisation. ``qc``, ``nist``, ``ltr`` and ``sst`` must not be preceded by a
   letter; the other twelve match anywhere.
   Source: outputs/table4_descriptors_20260927/table4_descriptors.py (BOUNDARY, SUBSTR, label_values, is_qc).

2. Tier 1 sample alignment (Text S14, Table S19): a datatable row is set aside when its sample ID is absent from the
   header of the paired mwTab quantitative block AND every value cell of the row is the number zero.
   Source: outputs/datatable_padding_verification_20260928/verify_padding.py (mwtab_ids, datatable_rows).

3. Feature-to-sample ratio after alignment (the manuscript's p/n): all deposited features divided by the sample rows that
   remain after rules 1 and 2. A matrix with no remaining sample has no ratio.
   Source: outputs/pn_recompute_20260930/pn_recompute.py (definition P2).

Alignment rows are REMOVED (final decision of 2026-10-06, following the manuscript): the workflow drops the rows of
rule 2 from every datatable matrix before assessment, so no sample count, gate, metric score or ratio uses them. The
deposited row count and the removed rows stay in the per-analysis record for display.

The SCORED feature-to-sample ratio counts DISTINCT sample IDs: features / distinct sample IDs with at least one
position that is not QC/blank (a position is one sample row of a datatable or untarg_data file, or one sample column
of an mwTab data block). Repeated positions of one sample ID are not independent samples. The manuscript's ratio by
matrix position is reported beside it; where no sample ID is repeated the two are equal, which is the case for every
datatable matrix.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 1. QC/blank filter (16 keywords)
BOUNDARY_KEYWORDS = ("qc", "nist", "ltr", "sst")
SUBSTRING_KEYWORDS = ("pool", "reference", "quality control", "pooled qc", "system suitability", "calibration standard", "drift",
                      "blank", "solvent", "process blank", "method blank", "reagent blank")
BLANK_KEYWORDS = ("blank", "solvent", "process blank", "method blank", "reagent blank")     # the blank-type subset of the 16
QC_KEYWORDS = tuple(k for k in BOUNDARY_KEYWORDS + SUBSTRING_KEYWORDS if k not in BLANK_KEYWORDS)
QC_BLANK_RE = re.compile("|".join([rf"(?<![a-z]){k}" for k in BOUNDARY_KEYWORDS] + [re.escape(k) for k in SUBSTRING_KEYWORDS]), re.I)
BLANK_RE = re.compile("|".join(re.escape(k) for k in BLANK_KEYWORDS), re.I)
assert len(BOUNDARY_KEYWORDS + SUBSTRING_KEYWORDS) == 16


def label_values(label: str) -> str:
    """Values of a 'Factor:Value | Factor:Value' label; a label without ':' is its own value."""
    parts = [p.strip() for p in str(label).split("|")]
    return " | ".join(p.split(":", 1)[1] if ":" in p else p for p in parts)


def is_qc_or_blank(sample_id: str, label: str) -> bool:
    return bool(QC_BLANK_RE.search(sample_id or "") or QC_BLANK_RE.search(label_values(label or "")))


def is_blank(sample_id: str, label: str) -> bool:
    """True when a row flagged by the 16-keyword filter matches one of its five blank-type keywords."""
    return bool(BLANK_RE.search(sample_id or "") or BLANK_RE.search(label_values(label or "")))


# ---------------------------------------------------------------- 2. Tier 1 sample alignment
def _number(token: str) -> float | None:
    t = token.strip()
    if not t:
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _lines(path: Path) -> list[str]:
    # split on "\n" only: a stray control character inside a feature name must not break a line
    return Path(path).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")


def mwtab_block_sample_ids(path: Path) -> list[str]:
    """Sample IDs in the header of every non-EXTENDED quantitative block of an mwTab file, in order, without repeats."""
    block: list[str] = []
    lines = _lines(path)
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.endswith("_DATA_START") and not s.startswith("EXTENDED") and i + 1 < len(lines):
            block += [x for x in (c.strip() for c in lines[i + 1].split("\t")[1:]) if x and x not in block]
    return block


def datatable_rows(path: Path) -> list[tuple[str, str, str]]:
    """(sample ID, class label, row class) of every datatable row; row class = all_zero / zero_empty / has_value."""
    lines = _lines(path)
    width = len(lines[0].split("\t"))
    out = []
    for ln in lines[1:]:
        if not ln.strip():
            continue
        p = ln.split("\t")
        vals = (p[2:] + [""] * width)[: max(width - 2, 0)]
        parsed = [_number(v) for v in vals]
        if any(v not in (None, 0.0) for v in parsed):
            cls = "has_value"
        elif all(v == 0.0 for v in parsed):
            cls = "all_zero"
        elif all(v == 0.0 or vals[k].strip() == "" for k, v in enumerate(parsed)):
            cls = "zero_empty"
        else:
            cls = "has_value"                      # text tokens without a number: not a zero row
        out.append((p[0].strip(), p[1] if len(p) > 1 else "", cls))
    return out


def tier1_alignment(datatable_path: Path, mwtab_path: Path) -> dict[str, Any] | None:
    """Datatable rows that are all zero and absent from the paired mwTab block. None when either file is unusable."""
    try:
        if not Path(datatable_path).is_file() or not Path(mwtab_path).is_file() or Path(datatable_path).stat().st_size == 0:
            return None
        block = mwtab_block_sample_ids(mwtab_path)
        rows = datatable_rows(datatable_path)
    except (OSError, IndexError):
        return None
    if not block or not rows:
        return None
    bset = set(block)
    absent_zero = [(sid, label) for sid, label, cls in rows if sid not in bset and cls == "all_zero"]
    return {
        "datatable_rows": len(rows),
        "mwtab_block_samples": len(block),
        "rows_absent_from_mwtab_block": sum(1 for sid, _, _ in rows if sid not in bset),
        "all_zero_rows_absent_from_mwtab_block": len(absent_zero),
        "all_zero_absent_rows_not_qc_or_blank": sum(1 for sid, label in absent_zero if not is_qc_or_blank(sid, label)),
        "all_zero_absent_sample_ids": [sid for sid, _ in absent_zero],
        "all_zero_absent_sample_ids_not_qc_or_blank": [sid for sid, label in absent_zero if not is_qc_or_blank(sid, label)],
    }


# ---------------------------------------------------------------- 3. per-matrix QC/blank rows and feature-to-sample ratio
def matrix_qc_flags(matrix: Any) -> list[bool]:
    """QC/blank flag of every row of a matrix, from the row's sample ID and the class label deposited in that matrix."""
    ids = [str(x).strip() for x in (getattr(matrix, "sample_ids", None) or [])]
    labels = list(getattr(matrix, "sample_class_labels", None) or [])
    if len(labels) != len(ids):                       # matrix built without per-row labels: use its label map
        by_id = getattr(matrix, "labels", None) or {}
        labels = [str(by_id.get(sid, "") or "") for sid in ids]
    return [is_qc_or_blank(sid, str(label or "")) for sid, label in zip(ids, labels)]


def matrix_distinct_sample_ids(matrix: Any) -> tuple[int, int]:
    """(distinct sample IDs, distinct sample IDs with at least one position that is not QC/blank) of a matrix.

    A sample ID can occupy several positions (rows or columns) of one deposited matrix. Positions are what the
    manuscript counts; distinct IDs are what the scored feature-to-sample ratio counts (decision of 2026-10-06).
    """
    ids = [str(x).strip() for x in (getattr(matrix, "sample_ids", None) or [])]
    flags = matrix_qc_flags(matrix)
    return len(set(ids)), len({sid for sid, is_qc in zip(ids, flags) if not is_qc})


def ratio_samples(n_sample_rows: int, n_qc_blank_rows: int, n_aligned_rows_not_qc: int = 0) -> int:
    """Samples in the denominator of the ratio: sample rows - QC/blank rows - Tier 1 aligned rows that are not QC/blank."""
    return max(int(n_sample_rows) - int(n_qc_blank_rows) - int(n_aligned_rows_not_qc), 0)


def feature_to_sample_ratio(n_features: int, n_sample_rows: int, n_qc_blank_rows: int, n_aligned_rows_not_qc: int = 0) -> float | None:
    """All deposited features / remaining samples; None when no sample remains (the matrix has no ratio)."""
    n = ratio_samples(n_sample_rows, n_qc_blank_rows, n_aligned_rows_not_qc)
    return (n_features / n) if n > 0 else None


def alignment_for_matrix(study: Any, matrix: Any) -> dict[str, Any]:
    """Tier 1 sample alignment record stored for this matrix by the workflow ({} when there is none)."""
    if str(getattr(matrix, "source_kind", "") or "").lower() != "datatable":
        return {}
    store = (getattr(study, "score_defaults", None) or {}).get(ALIGNMENT_KEY) or {}
    record = store.get(str(getattr(matrix, "assay_id", "") or "").strip().upper())
    return record if isinstance(record, dict) else {}


ALIGNMENT_KEY = "tier1_sample_alignment"


# ---------------------------------------------------------------- self-tests (the assertions of the manuscript scripts)
def self_test() -> None:
    assert is_qc_or_blank("PooledQC_01", "") and is_qc_or_blank("S1", "Type:Blank")
    assert not is_qc_or_blank("S1", "Treatment:oral administration")
    assert is_qc_or_blank("QC12", "") and not is_qc_or_blank("ASST_3", "Group:case")          # 'sst' after a letter does not count
    assert is_qc_or_blank("S1", "Type:NIST 1950") and not is_qc_or_blank("S1", "Pool:no")      # factor NAMES are not searched
    assert is_qc_or_blank("S1", "Sample:pooled plasma") and is_blank("S2", "Type:solvent blank") and not is_blank("QC3", "")
    assert label_values("A:1 | B:x:y | plain") == "1 | x:y | plain"
    assert feature_to_sample_ratio(300, 12, 2, 0) == 30 and feature_to_sample_ratio(300, 12, 2, 4) == 50
    assert feature_to_sample_ratio(300, 5, 5) is None and ratio_samples(10, 4, 9) == 0


self_test()
