from __future__ import annotations

import re
from typing import Any

from merit.utils import normalize_label

_UNKNOWN_TERMS = {
    "unknown",
    "na",
    "n/a",
    "",
    "unidentified",
    "unnamed",
    "not identified",
    "unknown metabolite",
    "unknown compound",
    "unassigned",
    "unannotated",
    "noname",
    "no name",
}

_NUM_RE_FRAGMENT = r"(?:\d+(?:\.\d*)?|\.\d+)"

_MZ_RT_PAIR_RE = re.compile(
    rf"^(?P<a>{_NUM_RE_FRAGMENT})[_/](?P<b>{_NUM_RE_FRAGMENT})(?:[_/](?P<c>{_NUM_RE_FRAGMENT}))?(?P<suffix>m/?z|mz|[np])?$",
    re.IGNORECASE,
)
_MZT_RE = re.compile(
    rf"^[Mm]\s*(?P<mz>{_NUM_RE_FRAGMENT})[TtRr]\s*(?P<rt>{_NUM_RE_FRAGMENT})(?P<suffix>[np])?$",
    re.IGNORECASE,
)
_MZ_ONLY_RE = re.compile(
    rf"^(?:m/?z\s*[:=_-]?\s*)?(?P<mz>{_NUM_RE_FRAGMENT})\s*(?:m/?z)?$",
    re.IGNORECASE,
)
_RT_MZ_RE = re.compile(
    rf"^(?:rt|t)\s*[:=_-]?\s*(?P<rt>{_NUM_RE_FRAGMENT})[_/\- ]+(?:m/?z|mz)\s*[:=_-]?\s*(?P<mz>{_NUM_RE_FRAGMENT})(?P<suffix>[np])?$",
    re.IGNORECASE,
)
_MZ_RT_RE = re.compile(
    rf"^(?:m/?z|mz)\s*[:=_-]?\s*(?P<mz>{_NUM_RE_FRAGMENT})[_/\- ]+(?:rt|t)\s*[:=_-]?\s*(?P<rt>{_NUM_RE_FRAGMENT})(?P<suffix>[np])?$",
    re.IGNORECASE,
)
_NMR_BIN_RE = re.compile(r"^\d+\.?\d*\s*(ppm|hz)$", re.IGNORECASE)
_NMR_BIN_RANGE_RE = re.compile(
    r"^\d+\.?\d*\s*(?:\.{2,}|…|-|to)\s*\d+\.?\d*(?:\s*(ppm|hz))?$",
    re.IGNORECASE,
)
_FEATURE_ID_RE = re.compile(
    r"^(feature|peak|bin|ion|mz|rt|metabolite|compound)?[_-]?\d+(?:[_-]\d+)*(?:[np])?$",
    re.IGNORECASE,
)
_LIPID_LIKE_SLASH_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.+\-\s]*\(([^()]*/[^()]*)\)$"
)
_ARTIFACT_KEYWORDS = {
    "classification",
    "class",
    "factor",
    "factors",
    "sample",
    "samples",
    "metabolite_names",
    "metabolite names",
}


def looks_like_lipid_structural_name(value: Any) -> bool:
    """Return True for lipid-like names with slash-delimited acyl chains.

    Examples:
    - LysoPA(0:0/16:0)
    - PC(16:0/18:1)
    - TG(16:0/18:1/18:2)
    """
    text = str(value or "").strip()
    if "/" not in text:
        return False
    match = _LIPID_LIKE_SLASH_RE.match(text)
    if not match:
        return False
    inner = match.group(1)
    parts = [part.strip() for part in inner.split("/") if part.strip()]
    if len(parts) < 2:
        return False
    # Loose chain token check: require at least one digit and one ":" per part.
    # This avoids flagging generic "A/B" slash names as lipid structural notation.
    for part in parts:
        compact = part.replace(" ", "")
        if ":" not in compact:
            return False
        if not re.search(r"\d", compact):
            return False
    return True


def _format_num(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _decimal_comma_candidate(compact: str) -> str:
    """Convert decimal commas in numeric feature coordinates to decimal points.

    Some Workbench untarg_data files use headers such as ``174,1123_10,84``.
    Those are m/z/RT-like coordinates, not metabolite names.  Keep the raw
    header unchanged elsewhere, but normalize this representation for pattern
    matching so the annotation tier is not inflated to "named".
    """
    if "," not in compact or not re.search(r"\d,\d", compact):
        return compact
    return re.sub(r"(?<=\d),(?=\d)", ".", compact)


def _canonical_mzrt_from_compact(compact: str) -> str:
    # Some untargeted exports append a replicate/index suffix to RT, e.g.
    # "116.0707_0.7.1". Treat this as mz/RT with an extra trailing index.
    rt_rep_suffix = re.match(
        r"^(?P<mz>\d+\.?\d*)[_/](?P<rt>\d+\.\d+)\.(?P<idx>\d+)$",
        compact,
        re.IGNORECASE,
    )
    if rt_rep_suffix:
        compact = f"{rt_rep_suffix.group('mz')}_{rt_rep_suffix.group('rt')}"

    match = _MZT_RE.match(compact)
    if match:
        mz = float(match.group("mz"))
        rt = float(match.group("rt"))
        suffix = (match.group("suffix") or "").lower()
        suffix_part = f":{suffix}" if suffix else ""
        return f"mz{_format_num(mz)}_rt{_format_num(rt)}{suffix_part}"

    match = _RT_MZ_RE.match(compact)
    if match:
        mz = float(match.group("mz"))
        rt = float(match.group("rt"))
        suffix = (match.group("suffix") or "").lower()
        suffix_part = f":{suffix}" if suffix else ""
        return f"mz{_format_num(mz)}_rt{_format_num(rt)}{suffix_part}"

    match = _MZ_RT_RE.match(compact)
    if match:
        mz = float(match.group("mz"))
        rt = float(match.group("rt"))
        suffix = (match.group("suffix") or "").lower()
        suffix_part = f":{suffix}" if suffix else ""
        return f"mz{_format_num(mz)}_rt{_format_num(rt)}{suffix_part}"

    match = _MZ_RT_PAIR_RE.match(compact)
    if match:
        a = float(match.group("a"))
        b = float(match.group("b"))
        suffix = (match.group("suffix") or "").lower()
        # Heuristic: one low number (RT) + one high number (m/z).
        if a <= 20 and b >= 30:
            rt, mz = a, b
        elif b <= 20 and a >= 30:
            rt, mz = b, a
        else:
            mz, rt = a, b
        suffix_part = f":{suffix}" if suffix else ""
        return f"mz{_format_num(mz)}_rt{_format_num(rt)}{suffix_part}"

    return ""


def canonical_mzrt(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text)

    canonical = _canonical_mzrt_from_compact(compact)
    if canonical:
        return canonical

    decimal_candidate = _decimal_comma_candidate(compact)
    if decimal_candidate != compact:
        return _canonical_mzrt_from_compact(decimal_candidate)

    return ""


def mzrt_lookup_key(value: Any) -> str:
    canonical = canonical_mzrt(value)
    return f"mzrt::{canonical}" if canonical else ""


def classify_feature_name(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    lowered = text.lower()
    compact = re.sub(r"\s+", "", lowered)

    if not text:
        return {
            "kind": "empty",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": False,
            "canonical_mzrt": "",
        }

    if lowered in _UNKNOWN_TERMS:
        return {
            "kind": "unknown",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": False,
            "canonical_mzrt": "",
        }

    canonical = canonical_mzrt(text)
    if canonical:
        return {
            "kind": "mz_rt",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": True,
            "canonical_mzrt": canonical,
        }

    if _NMR_BIN_RE.match(compact):
        return {
            "kind": "nmr_bin",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": True,
            "canonical_mzrt": "",
        }

    if _NMR_BIN_RANGE_RE.match(compact):
        return {
            "kind": "nmr_bin_range",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": True,
            "canonical_mzrt": "",
        }

    if compact in _ARTIFACT_KEYWORDS or any(word in compact for word in ("samplepool", "chearpool")):
        return {
            "kind": "artifact_token",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": False,
            "canonical_mzrt": "",
        }

    if _FEATURE_ID_RE.match(compact):
        return {
            "kind": "feature_id",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": False,
            "canonical_mzrt": "",
        }

    mz_only_compact = _decimal_comma_candidate(compact)
    if _MZ_ONLY_RE.match(mz_only_compact):
        return {
            "kind": "mz_only",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": True,
            "canonical_mzrt": "",
        }

    normalized = normalize_label(text)
    if normalized in _UNKNOWN_TERMS:
        return {
            "kind": "unknown",
            "is_named_metabolite": False,
            "is_non_metabolite": True,
            "is_mz_rt": False,
            "canonical_mzrt": "",
        }

    return {
        "kind": "named_metabolite",
        "is_named_metabolite": True,
        "is_non_metabolite": False,
        "is_mz_rt": False,
        "canonical_mzrt": "",
    }
