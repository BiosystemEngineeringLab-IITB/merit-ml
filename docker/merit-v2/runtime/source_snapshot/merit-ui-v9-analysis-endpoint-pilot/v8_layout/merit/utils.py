from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


def ensure_path(path: str | Path) -> Path:
    return path if isinstance(path, Path) else Path(path)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    path = ensure_path(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)


def load_json(path: str | Path) -> Any:
    path = ensure_path(path)
    return json.loads(path.read_text())


def write_json(path: str | Path, payload: Any) -> None:
    path = ensure_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(payload))


def read_tsv(path: str | Path) -> list[dict[str, str]]:
    path = ensure_path(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader]


def read_tsv_gz(path: str | Path) -> list[dict[str, str]]:
    path = ensure_path(path)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader]


def read_delimited_table(path: str | Path) -> list[dict[str, str]]:
    path = ensure_path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = "\t"
        name = path.name.lower()
        force_tsv = (
            name.endswith(".tsv")
            or name.endswith(".tsv.gz")
            or "_untarg_data.tsv" in name
            or "_datatable.tsv" in name
            or ".datatable.tsv" in name
            or name.endswith("_datatable_slash.txt")
        )
        first_line = sample.splitlines()[0] if sample else ""
        tab_count = first_line.count("\t")
        comma_count = first_line.count(",")
        if sample and not force_tsv:
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters="\t,")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = "\t"
        # Guard against comma mis-sniff when tabs are the dominant structure.
        if delimiter == "," and tab_count >= 2 and tab_count > comma_count:
            delimiter = "\t"
        # Workbench TSV/TXT occasionally contains unmatched quotes inside
        # metabolite names; treat quotes as literal text so one bad name cannot
        # collapse many rows/columns into a single giant field.
        reader = csv.DictReader(handle, delimiter=delimiter, quoting=csv.QUOTE_NONE)
        rows = [dict(row) for row in reader]
        if rows:
            return rows
    return []


def normalize_label(value: str | None) -> str:
    if value is None:
        return "unknown"
    label = value.strip().lower()
    label = re.sub(r"[^a-z0-9]+", "_", label).strip("_")
    return label or "unknown"


_NON_USABLE_CLASS_LABELS = {
    "",
    "unknown",
    "none",
    "null",
    "na",
    "n_a",
    "n/a",
    "missing",
    "not_available",
    "not_collected",
    "not_provided",
    "not_applicable",
    "not_reported",
    "nd",
    "n_d",
    "-",
}


def is_usable_class_label(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = normalize_label(text)
    return normalized not in _NON_USABLE_CLASS_LABELS


# QC/blank filter of the manuscript (Text S2, Table S14): 16 keywords; qc, nist, ltr and sst must not follow a letter.
# The rule itself lives in merit/manuscript_sample_rules.py; this tuple is kept for code that lists the keywords.
from merit.manuscript_sample_rules import (  # noqa: E402
    BOUNDARY_KEYWORDS as _QC_BOUNDARY_KEYWORDS,
    QC_BLANK_RE as _QC_BLANK_RE,
    SUBSTRING_KEYWORDS as _QC_SUBSTRING_KEYWORDS,
    is_qc_or_blank as _manuscript_is_qc_or_blank,
)

_QC_BLANK_KEYWORDS: tuple[str, ...] = _QC_BOUNDARY_KEYWORDS + _QC_SUBSTRING_KEYWORDS

_CONTROL_CONTEXT_KEYS = {
    "type",
    "sample_type",
    "sample_source",
    "sample_source_type",
    "source",
    "class",
    "classification",
}

_CONTROL_VALUE_EXPANSIONS = {
    "blk": "field blank",
    "blank": "blank",
    "fieldblank": "field blank",
    "field_blank": "field blank",
    "pblk": "process blank",
    "processblank": "process blank",
    "process_blank": "process blank",
    "mblk": "method blank",
    "methodblank": "method blank",
    "method_blank": "method blank",
    "rblk": "reagent blank",
    "reagentblank": "reagent blank",
    "reagent_blank": "reagent blank",
    "sblk": "solvent blank",
    "solventblank": "solvent blank",
    "solvent_blank": "solvent blank",
    "qc": "quality control",
    "qcs": "quality control",
    "pooledqc": "pooled qc",
    "pooled_qc": "pooled qc",
    "poolqc": "pooled qc",
    "pool_qc": "pooled qc",
    "nist": "reference material",
    "sst": "system suitability",
    "ltr": "long-term reference",
}


def is_qc_like_text(text: str | None) -> bool:
    """True when the text holds one of the 16 QC/blank keywords (boundary rule for qc, nist, ltr, sst)."""
    return bool(_QC_BLANK_RE.search(str(text or "")))


def _norm_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _compact_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _control_expansion_for_value(value: str) -> str:
    normalized = _norm_token(value)
    compact = _compact_token(value)
    if normalized in _CONTROL_VALUE_EXPANSIONS:
        return _CONTROL_VALUE_EXPANSIONS[normalized]
    if compact in _CONTROL_VALUE_EXPANSIONS:
        return _CONTROL_VALUE_EXPANSIONS[compact]

    tokens = [t for t in re.split(r"[^a-z0-9]+", str(value or "").lower()) if t]
    for token in tokens:
        stripped = re.sub(r"\d+$", "", token)
        if stripped in _CONTROL_VALUE_EXPANSIONS:
            return _CONTROL_VALUE_EXPANSIONS[stripped]
    return ""


def _iter_factor_pairs(factor_string: str):
    for part in str(factor_string or "").split("|"):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            key, value = item.split(":", 1)
            yield key.strip(), value.strip()
        else:
            yield "", item


def control_context_expansion(
    *,
    sample_id: str = "",
    label: str = "",
    sample_type: str = "",
    class_string: str = "",
    factor_string: str = "",
) -> str:
    """Recognize narrow analytical-control abbreviations in sample context."""
    for value in (sample_type, class_string, label):
        expansion = _control_expansion_for_value(value)
        if expansion:
            return expansion

    for key, value in _iter_factor_pairs(factor_string):
        key_norm = _norm_token(key)
        expansion = _control_expansion_for_value(value)
        if expansion and (not key_norm or key_norm in _CONTROL_CONTEXT_KEYS):
            return expansion

    return _control_expansion_for_value(sample_id)


def sample_is_qc_like(
    sample_id: str = "",
    label: str = "",
    sample_type: str = "",
    class_string: str = "",
    factor_string: str = "",
    attributes: dict[str, Any] | None = None,
) -> bool:
    attrs = attributes if isinstance(attributes, dict) else {}
    explicit = attrs.get("is_qc_like")
    if isinstance(explicit, bool):
        return explicit
    if isinstance(explicit, str):
        token = explicit.strip().lower()
        if token in {"true", "1", "yes"}:
            return True
        if token in {"false", "0", "no"}:
            return False

    # Manuscript rule (2026-10-06): the 16 keywords are looked for in the sample ID and in the VALUES of the class
    # label of the source matrix. Factor names, the sample-type field and abbreviation expansions are not used.
    # The class label is the raw label of the matrix (class_string); the factors text, then the composed label, are
    # used only when the matrix gives no class text.
    label_text = str(class_string or "").strip() or str(factor_string or "").strip() or str(label or "").strip()
    return _manuscript_is_qc_or_blank(str(sample_id or ""), label_text)


def sample_object_is_qc_like(sample: object) -> bool:
    attrs = getattr(sample, "attributes", {})
    if not isinstance(attrs, dict):
        attrs = {}
    return sample_is_qc_like(
        sample_id=str(getattr(sample, "sample_id", "") or ""),
        label=str(getattr(sample, "label", "") or ""),
        sample_type=str(getattr(sample, "sample_type", "") or ""),
        class_string=str(attrs.get("class_string", "") or ""),
        factor_string=str(attrs.get("factor_string", "") or ""),
        attributes=attrs,
    )


def sample_object_is_biological(sample: object) -> bool:
    return not sample_object_is_qc_like(sample)


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Workbench contains scientific notation such as 8.12.E-04: the extra
    # dot before E is a formatting artifact, with an unambiguous numeric value.
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?\.[Ee][+-]?\d+", text):
        text = re.sub(r"\.([Ee])", r"\1", text)
    try:
        return float(text)
    except ValueError:
        return None


def mean(values: Iterable[float]) -> float | None:
    items = [value for value in values]
    if not items:
        return None
    return sum(items) / len(items)


def stdev(values: Iterable[float]) -> float | None:
    items = [value for value in values]
    if len(items) < 2:
        return None
    avg = sum(items) / len(items)
    variance = sum((value - avg) ** 2 for value in items) / (len(items) - 1)
    return variance ** 0.5


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if q <= 0:
        return min(values)
    if q >= 1:
        return max(values)
    items = sorted(values)
    index = (len(items) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(items) - 1)
    fraction = index - lower
    return items[lower] * (1 - fraction) + items[upper] * fraction


def slugify(value: str) -> str:
    return normalize_label(value)


def short_text(value: str | None, limit: int = 160) -> str:
    if not value:
        return ""
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
