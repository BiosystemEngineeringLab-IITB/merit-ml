"""Pure manuscript mass/RT field and populated-value rules; no batch-script imports."""
import re
from typing import Any

_MASS_RT_EMPTY_VALUES = {
    "",
    "-",
    "na",
    "n/a",
    "none",
    "null",
    "unknown",
}

def _normalise_mwtab_field_name(field_name: str) -> tuple[str, str]:
    raw = str(field_name or "").strip().lower()
    spaced = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    return spaced, compact

def _classify_mass_rt_like_field(field_name: str) -> str:
    """Conservative classifier for source-deposited mass, m/z, RT, or RI fields.

    The metric is intentionally named "mass/RT-like" rather than "m/z available"
    because MW users deposit a broad mix of field labels, including m/z-like
    coordinates, neutral/exact mass fields, retention time, and retention index.
    """
    spaced, compact = _normalise_mwtab_field_name(field_name)
    raw = str(field_name or "").strip().lower()
    if not spaced and not compact:
        return ""
    if spaced in {"ri type", "rt type", "moverz type", "mz type", "m z type", "formula type"}:
        return ""

    # m/z-like fields.  MW often serializes "m/z" as "moverz" or
    # "moverz_quant"; those are treated as m/z-like coordinate metadata.
    if compact in {"mz", "moverz", "moverzquant", "moverzratio", "masstocharge", "masstochargequant", "mrm"}:
        return "mz-like"
    if compact.endswith("mz") and "mzcloud" not in compact:
        return "mz-like"
    if re.search(r"(^|\b)m\s*/\s*z($|\b)", raw):
        return "mz-like"
    if re.search(r"\b(mz|m z)\b", spaced) or "mass to charge" in spaced:
        return "mz-like"
    if re.search(r"\b(precursor|fragment|product|parent|quantified|quant|target|observed|average)\s+(mz|m z|m/z)\b", spaced):
        return "mz-like"
    if re.search(r"\b(mz|m z|m/z)\s+(quant|ratio|value|observed|measured)\b", spaced):
        return "mz-like"
    if "precursor ion" in spaced or "product ion" in spaced or "fragment ion" in spaced or "parent ion" in spaced:
        return "mz-like"

    # Retention-time / retention-index fields.  Retention index is included
    # because it is the GC-MS analogue of RT-like chromatographic position.
    if compact in {
        "rt",
        "ri",
        "retentiontime",
        "retentionindex",
        "rettime",
        "rettimes",
        "rindex",
        "retindex",
        "medrt",
        "basert",
        "rtimes",
    }:
        return "rt-like"
    if re.search(r"(^|\b)r\.?t\.?($|\b)", raw):
        return "rt-like"
    if re.search(r"\b(rt|ri)\b", spaced):
        return "rt-like"
    if "retention time" in spaced or "retention index" in spaced or "ret time" in spaced:
        return "rt-like"
    if re.search(r"\b(base|median|med|mean|average)\s*rt\b", spaced):
        return "rt-like"

    invalid_tokens = {
        "spectrometry",
        "instrument",
        "platform",
        "method",
        "protocol",
        "ion mode",
        "polarity",
        "adduct",
        "formula",
        "smiles",
        "inchi",
        "inchikey",
        "kegg",
        "hmdb",
        "pubchem",
        "chebi",
        "compound name",
        "metabolite name",
        "metabolite",
        "name",
        "description",
        "pathway",
        "class",
        "cas",
        "ri type",
        "rt type",
        "moverz type",
        "formula type",
    }
    if any(token in spaced for token in invalid_tokens):
        return ""

    # Mass-like fields are not equivalent to an m/z coordinate, but they still
    # help reuse and reannotation, so they are included under the deliberately
    # cautious "mass-like" label.
    if compact in {
        "mass",
        "exactmass",
        "neutralmass",
        "molecularmass",
        "molecularweight",
        "monoisotopicmass",
        "averagemass",
        "formulaweight",
        "neutralmasses",
        "molecularweights",
        "calcmw",
        "calculatedmw",
    }:
        return "mass-like"
    if re.search(
        r"\b(exact|neutral|molecular|monoisotopic|average|calc(?:ulated)?|target|observed|measured|nominal|parent)\s+mass\b",
        spaced,
    ):
        return "mass-like"
    if re.search(r"\bmass\b", spaced) or "molecular weight" in spaced or "formula weight" in spaced:
        return "mass-like"
    if re.search(r"\b(m|mass)\s+meas", spaced) or re.search(r"\bmw\b", spaced):
        return "mass-like"

    # Narrow ambiguous coordinate-like labels retained from the manual audit.
    if compact in {"q1", "q3", "precursor", "product", "fragment", "parent", "transition", "compound", "identifier"}:
        return "ambiguous"
    return ""

def _mass_rt_value_is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _MASS_RT_EMPTY_VALUES
    return True
