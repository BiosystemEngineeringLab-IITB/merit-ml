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


_QC_BLANK_KEYWORDS: tuple[str, ...] = (
    "qc", "blank", "pool", "nist", "reference", "solvent",
    "quality control", "pooled qc", "ltr", "sst",
    "calibration standard", "system suitability", "process blank",
    "method blank", "reagent blank", "drift",
    "standard mixture", "external standard", "empty run",
    "equilibration", "conditioning", "wash",
)


def is_qc_like_text(text: str | None) -> bool:
    haystack = str(text or "").lower()
    if not haystack:
        return False
    return any(kw in haystack for kw in _QC_BLANK_KEYWORDS)


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

    primary_text = " ".join(
        [
            str(sample_id or ""),
            str(label or ""),
            str(class_string or ""),
            str(factor_string or ""),
        ]
    )
    if is_qc_like_text(primary_text):
        return True

    # MW often reports "Pooled Sample" in sample_type for genuine biological
    # rows; only use sample_type as a QC signal when no usable class/factor
    # label context exists.
    has_class_context = (
        is_usable_class_label(class_string)
        or is_usable_class_label(label)
        or bool(str(factor_string or "").strip())
    )
    if has_class_context:
        return False
    return is_qc_like_text(sample_type)


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
