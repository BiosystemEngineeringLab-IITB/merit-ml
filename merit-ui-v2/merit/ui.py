from __future__ import annotations

import copy
import csv
import hashlib
import html
import io
import json
import mimetypes
import os
import re
import zipfile
from collections import Counter
from dataclasses import is_dataclass
from datetime import datetime, timezone
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import log
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse
from urllib.request import Request, urlopen

from merit.models import dataclass_to_dict
from merit.readiness_score import compute_readiness_score
from merit.serialization import assessment_report_from_dict, load_assessment_report, read_json
from merit.utils import is_usable_class_label, normalize_label, sample_is_qc_like
from merit.version import __version__ as MERIT_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _e(text: str) -> str:
    return html.escape(str(text))


def _repository_display_label(source: Any) -> str:
    normalized = normalize_label(str(source or "")).lower()
    if normalized in {"workbench", "metabolomics workbench", "metabolomics_workbench"}:
        return "Metabolomics Workbench"
    text = str(source or "").strip()
    return text


_V2_BAND_LABELS: dict[str, str] = {
    "Ready": "ML-ready",
    "Conditional": "ML-ready with caveats",
    "Fragile": "Exploratory ML use",
    "Not Ready": "Class-support limited",
    "No Data": "Metadata-only record",
}

_V2_BAND_ORDER: dict[str, int] = {
    "No Data": 0,
    "Not Ready": 1,
    "Fragile": 2,
    "Conditional": 3,
    "Ready": 4,
}

_SCOPE_NOTE_TEXT = (
    "MERIT-ML is an independent, source-aware ML-readiness assessment of public metabolomics "
    "tabular data. This report is derived from publicly available Metabolomics Workbench "
    "records and does not evaluate the scientific validity, analytical quality, or "
    "biological importance of the original study. Users should verify the current source "
    "record on Metabolomics Workbench and cite the original Project ID, Project DOI where "
    "available, Study ID/accession, and associated publication(s)."
)

_INDEPENDENCE_NOTE_TEXT = (
    "MERIT-ML is an independent research tool developed for source-aware assessment of "
    "supervised ML-readiness in public metabolomics tabular data. MERIT-ML is not affiliated "
    "with, endorsed by, or maintained by Metabolomics Workbench, NMDR, NIH, or the original "
    "data submitters. Original data and metadata remain attributable to their source "
    "repositories and study authors. We gratefully acknowledge the "
    "Metabolomics Workbench/NMDR team for maintaining the public repository infrastructure that makes "
    "this type of secondary assessment possible."
)

_MERIT_PARSING_ISSUE_FORM_URL = "https://forms.gle/devGeKVKQTxJRceH7"

_UMAMI_WEBSITE_ID = "e9fa298c-3199-4358-a0ec-8fa401f3eb10"
_ANALYTICS_CONSENT_STORAGE_KEY = "merit-ml-umami-consent"


def _merit_analytics_head_script() -> str:
    return f"""<script>
(function(){{
  var KEY = "{_ANALYTICS_CONSENT_STORAGE_KEY}";
  var WEBSITE_ID = "{_UMAMI_WEBSITE_ID}";
  function loadUmami() {{
    if (document.querySelector('script[data-website-id="' + WEBSITE_ID + '"]')) return;
    var s = document.createElement("script");
    s.defer = true;
    s.src = "https://cloud.umami.is/script.js";
    s.setAttribute("data-website-id", WEBSITE_ID);
    document.head.appendChild(s);
  }}
  window.__meritAnalytics = {{
    accept: function() {{
      try {{ localStorage.setItem(KEY, "accepted"); }} catch (e) {{}}
      loadUmami();
      var banner = document.getElementById("merit-analytics-consent");
      if (banner) banner.style.display = "none";
      document.body.classList.remove("merit-analytics-banner-visible");
    }},
    decline: function() {{
      try {{ localStorage.setItem(KEY, "declined"); }} catch (e) {{}}
      var banner = document.getElementById("merit-analytics-consent");
      if (banner) banner.style.display = "none";
      document.body.classList.remove("merit-analytics-banner-visible");
    }},
    getConsent: function() {{
      try {{ return localStorage.getItem(KEY); }} catch (e) {{ return null; }}
    }}
  }};
  if (window.__meritAnalytics.getConsent() === "accepted") loadUmami();
}})();
</script>"""


def _merit_analytics_consent_css() -> str:
    return """
#merit-analytics-consent{display:none;position:fixed;top:0;left:0;right:0;z-index:100000;
  background:#4a90c4;color:#fff;font-size:.92rem;line-height:1.45;
  box-shadow:0 2px 12px rgba(19,35,39,.18)}
body.merit-analytics-banner-visible #merit-analytics-consent{display:block}
body.merit-analytics-banner-visible{padding-top:92px}
#merit-analytics-consent .merit-analytics-inner{max-width:min(1800px,calc(100vw - 28px));margin:0 auto;
  padding:14px 18px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
#merit-analytics-consent .merit-analytics-text{flex:1 1 320px;margin:0}
#merit-analytics-consent .merit-analytics-actions{display:flex;gap:10px;flex:0 0 auto}
#merit-analytics-consent .merit-analytics-actions button{border:0;border-radius:4px;padding:10px 22px;
  font:inherit;font-size:.82rem;font-weight:800;letter-spacing:.04em;color:#fff;cursor:pointer}
#merit-analytics-consent .merit-analytics-accept{background:#3d9e52}
#merit-analytics-consent .merit-analytics-accept:hover{background:#349447}
#merit-analytics-consent .merit-analytics-decline{background:#d94b4b}
#merit-analytics-consent .merit-analytics-decline:hover{background:#c43f3f}
@media(max-width:700px){#merit-analytics-consent .merit-analytics-actions{width:100%;justify-content:flex-end}}
"""


def _merit_analytics_consent_banner() -> str:
    return """<div id="merit-analytics-consent" role="dialog" aria-label="Analytics consent">
  <div class="merit-analytics-inner">
    <p class="merit-analytics-text">This website uses Umami Analytics to collect anonymous usage statistics. The data is processed via Umami Cloud and is not shared with any third parties or external services beyond what is required to operate the analytics service. These statistics are invaluable to us, as they enable us to focus future developments on the features of <strong>MERIT-ML</strong> that are most used, or on the contrary, to pinpoint the features that are of least interest to users.</p>
    <div class="merit-analytics-actions">
      <button type="button" class="merit-analytics-accept" onclick="window.__meritAnalytics.accept()">ACCEPT</button>
      <button type="button" class="merit-analytics-decline" onclick="window.__meritAnalytics.decline()">DECLINE</button>
    </div>
  </div>
</div>
<script>
(function(){
  var consent = window.__meritAnalytics && window.__meritAnalytics.getConsent();
  if (!consent) document.body.classList.add("merit-analytics-banner-visible");
})();
</script>"""


_V2_DEFAULT_PARAMS: dict[str, float] = {
    # Band cutoffs
    "band_ready_min": 0.85,
    "band_conditional_min": 0.70,
    "band_exploratory_min": 0.50,
    # Feasibility gates
    "g2_sample_pass": 20.0,
    "g2_sample_fail_below": 10.0,
    "g4_class_pass": 5.0,
    "g4_class_warn_min": 3.0,
    "g5_missing_pass_pct": 50.0,
    "g5_missing_fail_pct": 80.0,
    # Cohort metrics
    "class_balance_pass": 0.40,
    "group_support_strong": 20.0,
    "group_support_moderate": 10.0,
    "group_support_weak": 5.0,
    "label_entropy_pass": 0.70,
    # ML task metrics
    "pn_low": 10.0,
    "pn_moderate": 50.0,
    "pn_high": 200.0,
    "pn_tail": 1000.0,
    # Analytical QC metrics/guidance
    "sample_missingness_score_pass": 0.85,
    "class_missingness_gap_warn_pct": 10.0,
    "sample_outlier_score_pass": 0.90,
    "correlation_score_pass": 0.85,
    "feature_missingness_threshold_pct": 30.0,
    "feature_missingness_burden_warn_pct": 10.0,
    # Annotation/interoperability status cutoffs
    "annotation_general_pass": 0.70,
    "annotation_redundancy_pass": 0.85,
    "unknown_feature_max_pct": 20.0,
}

_V2_PARAM_BOUNDS: dict[str, tuple[float, float, float]] = {
    "band_ready_min": (0.50, 0.99, 0.01),
    "band_conditional_min": (0.30, 0.95, 0.01),
    "band_exploratory_min": (0.10, 0.90, 0.01),
    "g2_sample_pass": (5, 100, 1),
    "g2_sample_fail_below": (1, 50, 1),
    "g4_class_pass": (2, 30, 1),
    "g4_class_warn_min": (1, 20, 1),
    "g5_missing_pass_pct": (0, 90, 1),
    "g5_missing_fail_pct": (10, 100, 1),
    "class_balance_pass": (0.05, 1.00, 0.01),
    "group_support_strong": (5, 100, 1),
    "group_support_moderate": (3, 50, 1),
    "group_support_weak": (1, 30, 1),
    "label_entropy_pass": (0.10, 1.00, 0.01),
    "pn_low": (1, 100, 1),
    "pn_moderate": (5, 300, 1),
    "pn_high": (20, 1000, 5),
    "pn_tail": (200, 5000, 50),
    "sample_missingness_score_pass": (0.10, 1.00, 0.01),
    "class_missingness_gap_warn_pct": (0, 50, 1),
    "sample_outlier_score_pass": (0.10, 1.00, 0.01),
    "correlation_score_pass": (0.10, 1.00, 0.01),
    "feature_missingness_threshold_pct": (0, 100, 1),
    "feature_missingness_burden_warn_pct": (0, 100, 1),
    "annotation_general_pass": (0.10, 1.00, 0.01),
    "annotation_redundancy_pass": (0.10, 1.00, 0.01),
    "unknown_feature_max_pct": (0, 100, 1),
}

_V2_SCORE_PARAM_KEYS = {
    "band_ready_min",
    "band_conditional_min",
    "band_exploratory_min",
    "class_balance_pass",
    "label_entropy_pass",
    "sample_missingness_score_pass",
    "sample_outlier_score_pass",
    "correlation_score_pass",
    "annotation_general_pass",
    "annotation_redundancy_pass",
}

_V2_HIDDEN_LEGACY_METRICS = {
    "age_biological_sex_metadata",
}

_V2_DEFAULT_MW_DUMP_ROOT = "/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version"
_WB_STUDY_PAGE_BASE = "https://www.metabolomicsworkbench.org/data/DRCCMetadata.php?Mode=Study&StudyID="
_WB_REST_BASE = "https://www.metabolomicsworkbench.org/rest"

_EMBARGOED_STUDIES: dict[str, str] = {
    "ST002866": "2026-08-29",
    "ST003408": "2027-03-31",
    "ST003494": "2026-09-30",
    "ST003594": "2026-12-31",
}


def _study_id_key(study_id: Any) -> str:
    return str(study_id or "").strip().upper()


def _workbench_study_url(study_id: Any) -> str:
    sid = _study_id_key(study_id)
    return f"{_WB_STUDY_PAGE_BASE}{sid}" if sid else "https://www.metabolomicsworkbench.org/"


def _workbench_rest_url(*parts: Any) -> str:
    cleaned = [quote(str(part).strip("/")) for part in parts if str(part or "").strip("/")]
    return f"{_WB_REST_BASE}/{'/'.join(cleaned)}" if cleaned else _WB_REST_BASE


def _workbench_study_rest_url(study_id: Any, endpoint: str) -> str:
    sid = _study_id_key(study_id)
    endpoint = str(endpoint or "").strip("/") or "summary"
    return _workbench_rest_url("study", "study_id", sid, endpoint)


def _workbench_analysis_rest_url(analysis_id: Any, endpoint: str = "mwtab/txt") -> str:
    aid = _analysis_id_label(str(analysis_id or "")).strip().upper()
    endpoint = str(endpoint or "").strip("/") or "mwtab/txt"
    return _workbench_rest_url("study", "analysis_id", aid, *endpoint.split("/"))


def _source_matrix_rest_endpoint(source_key: Any) -> str:
    source = str(source_key or "").strip().lower()
    if source == "datatable":
        return "datatable/file"
    if source == "untarg_data":
        return "untarg_data/file"
    return "mwtab/txt"


def _source_matrix_rest_label(source_key: Any) -> str:
    source = str(source_key or "").strip().lower()
    if source == "datatable":
        return "datatable/file"
    if source == "untarg_data":
        return "untarg_data/file"
    return "mwtab/txt"


def _verify_rest_chip(
    url: str,
    *,
    label: str = "Verify from source",
    endpoint_label: str | None = None,
    compact: bool = True,
    title: str | None = None,
) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    size = ".67rem" if compact else ".74rem"
    padding = "2px 7px" if compact else "4px 9px"
    margin = "margin-left:6px;" if compact else "margin-left:8px;"
    tooltip = title or (
        f"Verify parsed source field in Metabolomics Workbench REST endpoint: {endpoint_label or url}"
    )
    return (
        f"<a href='{_e(url)}' target='_blank' rel='noopener noreferrer' "
        f"title='{_e(tooltip)}' aria-label='{_e(label)}' "
        "style='display:inline-flex;align-items:center;gap:4px;"
        f"{margin}padding:{padding};border-radius:999px;"
        "border:1px solid rgba(13,110,110,.24);background:rgba(13,110,110,.06);"
        f"color:#0d6e6e;font-size:{size};font-weight:800;text-decoration:none;"
        "white-space:nowrap;line-height:1.2'>"
        f"{_e(label)}</a>"
    )


def _overview_rest_source(study_id: Any, field_key: str) -> tuple[str, str, str]:
    """Return (url, endpoint label, short explanation) for overview-field verification."""
    sid = _study_id_key(study_id)
    field = str(field_key or "").strip().lower()
    endpoint = "summary"
    note = "Study-level metadata endpoint"
    if field in {"disease", "disease_condition"}:
        endpoint = "disease"
        note = "Study disease/condition metadata endpoint"
    elif field in {"sample_matrices", "factor_variables", "factor_examples", "class_labels"}:
        endpoint = "factors"
        note = "Sample-level factors endpoint"
    elif field in {"annotations", "ion_mode", "polarities"}:
        endpoint = "analysis"
        note = "Study analysis list; exact assay metadata is linked in Analytical QC"
    elif field == "repository":
        return _workbench_study_url(sid), "Metabolomics Workbench study page", "Repository source page"
    label = f"/rest/study/study_id/{sid}/{endpoint}" if sid else f"/rest/study/study_id/<study>/{endpoint}"
    return _workbench_study_rest_url(sid, endpoint), label, note


def _overview_verify_chip(study_id: Any, field_key: str) -> str:
    url, endpoint_label, note = _overview_rest_source(study_id, field_key)
    return _verify_rest_chip(
        url,
        label="Verify from source",
        endpoint_label=endpoint_label,
        compact=True,
        title=f"{note}. MERIT-ML parsed or derived this displayed field from public Metabolomics Workbench source metadata. Endpoint: {endpoint_label}",
    )


def _verify_workbench_button(
    study_id: Any,
    label: str = "Verify from source",
    *,
    compact: bool = False,
) -> str:
    sid = _study_id_key(study_id)
    if not sid:
        return ""
    if compact:
        style = (
            "display:inline-flex;align-items:center;gap:4px;margin-left:6px;padding:2px 7px;"
            "border-radius:999px;border:1px solid rgba(13,110,110,.28);"
            "background:rgba(13,110,110,.07);color:#0d6e6e;font-size:.68rem;"
            "font-weight:800;text-decoration:none;white-space:nowrap"
        )
    else:
        style = (
            "display:inline-flex;align-items:center;gap:5px;margin-left:8px;padding:4px 9px;"
            "border-radius:999px;border:1px solid rgba(13,110,110,.28);"
            "background:rgba(13,110,110,.07);color:#0d6e6e;font-size:.75rem;"
            "font-weight:800;text-decoration:none;white-space:nowrap"
        )
    return (
        f"<a href='{_e(_workbench_study_url(sid))}' target='_blank' "
        f"rel='noopener noreferrer' style='{style}'>{_e(label)}</a>"
    )


def _report_merit_parsing_issue_card(summary: dict[str, Any]) -> str:
    study_id = _study_id_key(summary.get("study_id", ""))
    source = str(summary.get("source") or "").strip()
    form_url = _MERIT_PARSING_ISSUE_FORM_URL.strip()
    if form_url:
        action_html = (
            f"<a href='{_e(form_url)}' target='_blank' rel='noopener noreferrer' "
            "style='display:inline-flex;align-items:center;justify-content:center;border-radius:12px;"
            "border:1px solid rgba(13,110,110,.32);background:#0d6e6e;color:white;"
            "padding:8px 12px;font-weight:800;text-decoration:none;font-size:.8rem'>"
            "Report a MERIT-ML parsing issue</a>"
        )
    else:
        action_html = (
            "<span aria-disabled='true' title='Report form link not configured yet' "
            "style='display:inline-flex;align-items:center;justify-content:center;border-radius:12px;"
            "border:1px solid rgba(13,110,110,.22);background:rgba(13,110,110,.08);"
            "color:#0d6e6e;padding:8px 12px;font-weight:800;font-size:.8rem'>"
            "Report a MERIT-ML parsing issue</span>"
        )
    chips = []
    if study_id:
        chips.append(f"Study ID: <strong>{_e(study_id)}</strong>")
    if source:
        chips.append(f"Source matrix: <strong>{_e(source)}</strong>")
    chip_html = (
        "<div style='display:flex;flex-wrap:wrap;gap:7px;margin-top:8px'>"
        + "".join(
            "<span style='display:inline-flex;border-radius:999px;padding:3px 8px;"
            "background:rgba(255,255,255,.82);border:1px solid rgba(13,110,110,.16);"
            f"color:#51656a;font-size:.74rem'>{chip}</span>"
            for chip in chips
        )
        + "</div>"
        if chips
        else ""
    )
    return (
        "<div style='margin:0 0 16px;padding:13px 15px;border-radius:16px;"
        "background:rgba(255,255,255,.72);border:1px solid rgba(13,110,110,.18);"
        "display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:flex-start;"
        "box-shadow:0 10px 26px rgba(19,35,39,.05)'>"
        "<div style='min-width:0;color:#2e474d;font-size:.86rem;line-height:1.5'>"
        "<strong style='display:block;color:#132327;margin-bottom:3px'>Report a MERIT-ML parsing issue</strong>"
        "If you believe MERIT-ML has mis-parsed a Metabolomics Workbench record, please report the issue "
        "with the Study ID, source matrix, and expected correction. MERIT-ML does not modify "
        "original Metabolomics Workbench records."
        "<div style='margin-top:5px;color:#51656a;font-size:.8rem'>"
        "Reports help distinguish MERIT-ML parsing errors, missing or ambiguous source metadata, "
        "and possible repository/API inconsistencies.</div>"
        f"{chip_html}"
        "</div>"
        f"<div style='flex:0 0 auto'>{action_html}</div>"
        "</div>"
    )


def _embargoed_study_message(study_id: Any) -> str:
    sid = _study_id_key(study_id)
    release_date = _EMBARGOED_STUDIES.get(sid)
    if not release_date:
        return ""
    return (
        f"{sid} is currently under embargo in Metabolomics Workbench and is not available through MERIT-ML "
        f"until {release_date}. MERIT-ML does not display, export, or include embargoed studies."
    )


def _is_embargoed_study(study_id: Any) -> bool:
    return bool(_embargoed_study_message(study_id))


def _v2_band_label(band: Any) -> str:
    raw = str(band or "").strip()
    if not raw:
        return ""
    normalized = {
        "ready": "Ready",
        "conditional": "Conditional",
        "fragile": "Fragile",
        "not ready": "Not Ready",
        "not_ready": "Not Ready",
        "no data": "No Data",
        "no_data": "No Data",
    }.get(raw.casefold(), raw)
    return _V2_BAND_LABELS.get(normalized, raw)


def _v2_is_default_params(params: dict[str, float] | None) -> bool:
    params = params or _V2_DEFAULT_PARAMS
    for key, default in _V2_DEFAULT_PARAMS.items():
        try:
            if abs(float(params.get(key, default)) - float(default)) > 1e-9:
                return False
        except Exception:
            return False
    return True


def _coerce_v2_scoring_params(values: dict[str, Any] | None) -> dict[str, float]:
    values = values or {}
    params: dict[str, float] = {}
    for key, default in _V2_DEFAULT_PARAMS.items():
        raw = values.get(key, default)
        try:
            val = float(raw)
        except Exception:
            val = float(default)
        if key in _V2_SCORE_PARAM_KEYS and val > 1.0:
            val = val / 100.0
        lo, hi, _step = _V2_PARAM_BOUNDS.get(key, (-1e12, 1e12, 1.0))
        params[key] = max(float(lo), min(float(hi), val))

    # Keep dependent cutoffs monotonic even if a user enters conflicting values.
    params["band_conditional_min"] = min(params["band_conditional_min"], params["band_ready_min"])
    params["band_exploratory_min"] = min(params["band_exploratory_min"], params["band_conditional_min"])
    params["g2_sample_fail_below"] = min(params["g2_sample_fail_below"], params["g2_sample_pass"])
    params["g4_class_warn_min"] = min(params["g4_class_warn_min"], params["g4_class_pass"])
    params["g5_missing_pass_pct"] = min(params["g5_missing_pass_pct"], params["g5_missing_fail_pct"])
    params["group_support_moderate"] = min(params["group_support_moderate"], params["group_support_strong"])
    params["group_support_weak"] = min(params["group_support_weak"], params["group_support_moderate"])
    params["pn_moderate"] = max(params["pn_moderate"], params["pn_low"])
    params["pn_high"] = max(params["pn_high"], params["pn_moderate"])
    params["pn_tail"] = max(params["pn_tail"], params["pn_high"] + 1.0)
    return params


def _coerce_v2_matrix_overrides(values: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not values:
        return {}
    raw = str(values.get("matrix_overrides", "") or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    def _as_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    for sample_id, item in payload.items():
        sid = str(sample_id or "").strip()
        if not sid or not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        if "eligible" in item:
            entry["eligible"] = _as_bool(item.get("eligible"))
        if "excluded" in item:
            entry["excluded"] = _as_bool(item.get("excluded"))
        if "label" in item:
            entry["label"] = str(item.get("label", "") or "").strip()
        if entry:
            cleaned[sid] = entry
    return cleaned


def _v2_fmt_param(value: float, *, pct: bool = False) -> str:
    if pct:
        return f"{float(value):.0f}%"
    if abs(float(value) - round(float(value))) < 1e-9:
        return str(int(round(float(value))))
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def _v2_fmt_score(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _v2_param_display_value(key: str, value: float) -> float:
    return float(value) * 100.0 if key in _V2_SCORE_PARAM_KEYS else float(value)


def _v2_param_display_bounds(key: str) -> tuple[float, float, float]:
    lo, hi, step = _V2_PARAM_BOUNDS[key]
    if key in _V2_SCORE_PARAM_KEYS:
        return lo * 100.0, hi * 100.0, 1.0
    return lo, hi, step


def _v2_param_display_text(key: str, value: float) -> str:
    if key in _V2_SCORE_PARAM_KEYS:
        return _v2_fmt_score(value, digits=0)
    return _v2_fmt_param(value, pct=key.endswith("_pct"))


def _logo_asset_path() -> Path | None:
    """Locate the lightweight sidebar logo without inlining it into HTML."""
    candidates = [
        Path(__file__).resolve().parents[1] / "static" / "merit-logo-sidebar.png",
        Path(__file__).resolve().parents[1] / "Logo.png",
        Path.cwd() / "Logo.png",
        Path("/home/shayantan/metabolomics/ML-ready/Logo.png"),
    ]
    for path in candidates:
        try:
            if path.exists() and path.is_file() and path.stat().st_size > 0:
                return path
        except Exception:
            continue
    return None


def _logo_asset_bytes() -> tuple[bytes, str] | None:
    path = _logo_asset_path()
    if not path:
        return None
    try:
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        return path.read_bytes(), mime
    except Exception:
        return None


def _logo_asset_url() -> str:
    return "/assets/logo.png" if _logo_asset_path() else ""


def _json_safe_state_payload(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a derived-only public MERIT-ML assessment JSON payload."""
    if not state:
        return None

    def _redact_local_path_text(text: str) -> str:
        if not text:
            return text
        lower = text.lower()
        if lower.startswith(("http://", "https://")):
            return text
        looks_local = (
            "/home/" in text
            or "/users/" in lower
            or "\\users\\" in lower
            or "metabolomics/ml-ready" in lower
            or "mw-dump-latest-confirmation" in lower
            or "merit-cache-workbench-full-v" in lower
            or "merit-ui-v2" in lower
            or lower == "cache_embedded"
        )
        if not looks_local:
            return text
        return "[internal artifact omitted]"

    def _strip_local_paths(value: Any) -> Any:
        if isinstance(value, str):
            return _redact_local_path_text(value)
        if isinstance(value, list):
            return [_strip_local_paths(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _strip_local_paths(v) for k, v in value.items()}
        return value

    def _jsonable(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, list):
            return [_jsonable(v) for v in value]
        if isinstance(value, tuple):
            return [_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if is_dataclass(value):
            return dataclass_to_dict(value)
        return str(value)

    def _report_field(report: Any, key: str, default: Any = None) -> Any:
        if isinstance(report, dict):
            return report.get(key, default)
        return getattr(report, key, default)

    def _score_payload(score: Any) -> dict[str, Any]:
        if not isinstance(score, dict):
            return {}
        keep = (
            "score",
            "core_ml_readiness_score",
            "reusability_score",
            "section_scores",
            "core_section_scores",
            "reusability_section_scores",
            "band",
            "band_label",
            "final_band",
            "final_band_label",
            "provisional_band",
            "provisional_band_label",
            "gate_ceiling",
            "gate_ceiling_label",
            "gate_summary",
            "recommendation",
            "status_note",
            "source_tier",
        )
        return {key: _jsonable(score.get(key)) for key in keep if key in score}

    def _gate_payload(gates: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(gates, list):
            return out
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            out.append(
                {
                    "id": gate.get("id", ""),
                    "name": gate.get("name", ""),
                    "status": gate.get("status", ""),
                    "rule": gate.get("rule", ""),
                    "value": _jsonable(gate.get("value")),
                }
            )
        return out

    def _metric_summaries(report: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        sections = (
            "metadata_readiness",
            "analytical_readiness",
            "annotation_readiness",
            "cohort_bias",
            "ml_readiness",
            "class_separability",
            "cross_study_harmonization",
        )
        for section in sections:
            metrics = _report_field(report, section, []) or []
            if not isinstance(metrics, list):
                continue
            for metric in metrics:
                metric_dict = metric if isinstance(metric, dict) else dataclass_to_dict(metric)
                if not isinstance(metric_dict, dict):
                    continue
                # Informational analytical-QC entries may encode source-deposited metadata
                # such as platform/instrument descriptors. Public JSON keeps scored MERIT-ML
                # outputs only.
                if bool(metric_dict.get("informational", False)):
                    continue
                score = metric_dict.get("score")
                score_0_100 = None
                if isinstance(score, (int, float)) and not isinstance(score, bool):
                    score_0_100 = round(float(score) * 100.0, 3)
                rows.append(
                    {
                        "section": section,
                        "family": metric_dict.get("family", ""),
                        "metric_id": metric_dict.get("name", ""),
                        "status": metric_dict.get("status", ""),
                        "score": score,
                        "score_0_100": score_0_100,
                    }
                )
        return rows

    def _source_counts(source_availability: Any) -> dict[str, Any]:
        if not isinstance(source_availability, dict):
            return {}
        counts = {
            "datatable": int(source_availability.get("datatable_count") or 0),
            "mwtab": int(source_availability.get("mwtab_count") or 0),
            "untarg_data": int(source_availability.get("untarg_data_count") or 0),
        }
        return {
            "available_sources": [src for src, count in counts.items() if count > 0],
            "source_counts": counts,
            "priority_tier": source_availability.get("priority_tier", ""),
        }

    readiness = state.get("readiness_score") or {}
    final_report = state.get("final_report")
    study_id = str(state.get("study_id") or "").strip().upper()
    source_assessments: dict[str, Any] = {}
    for src, item in (state.get("source_assessments") or {}).items():
        if not isinstance(item, dict) or not item:
            source_assessments[str(src)] = None
            continue
        source_score = item.get("readiness_score") or {}
        source_assessments[str(src)] = {
            "source": item.get("source", src),
            "source_tier": item.get("source_tier", ""),
            "readiness_score": _score_payload(source_score),
            "gate_feasibility": _gate_payload(source_score.get("gates")),
            "metric_scores": _metric_summaries(item.get("_report") or item.get("report")),
        }

    payload: dict[str, Any] = {
        "schema": "merit_assessment_derived_v1",
        "study_id": study_id,
        "repository": "Metabolomics Workbench",
        "merit_version": MERIT_VERSION,
        "generated_at_utc": state.get("generated_at_utc", ""),
        "primary_source": state.get("primary_source", ""),
        "source_tier": state.get("source_tier", ""),
        "source_availability_summary": _source_counts(state.get("source_availability")),
        "readiness_score": _score_payload(readiness),
        "gate_feasibility": _gate_payload(readiness.get("gates")),
        "section_metric_scores": _metric_summaries(final_report),
        "source_assessments": source_assessments,
        "assessment_settings": {
            "scoring_params": _jsonable(state.get("v2_scoring_params", {})),
            "matrix_properties_adjusted": bool(state.get("v2_matrix_overrides")),
        },
        "provenance_note": (
            "This JSON is a MERIT-ML-derived assessment summary. It intentionally excludes "
            "source-deposited metadata fields, per-sample labels, and tabular matrix values."
        ),
    }
    return _strip_local_paths(payload)


def _default_precomputed_root() -> str:
    configured = (
        os.getenv("MERIT_UI_PRECOMPUTED_ROOT")
        or os.getenv("MERIT_PRECOMPUTED_ROOT")
        or os.getenv("MERIT_CACHE_BASE_URL")
    )
    if configured:
        return configured
    for candidate in (
        Path.cwd() / "merit-cache-workbench-full-v7",
        Path.cwd().parent / "merit-cache-workbench-full-v7",
        Path("/home/shayantan/metabolomics/ML-ready/merit-cache-workbench-full-v7"),
    ):
        try:
            if candidate.exists():
                return str(candidate)
        except Exception:
            continue
    return "merit-cache-workbench-full-v7"


def _study_metadata_index_path(precomputed_root: str | Path) -> str:
    root_raw = str(precomputed_root).strip()
    return (
        urljoin(root_raw.rstrip("/") + "/", "study_metadata_index.json")
        if _is_http_location(root_raw)
        else str((Path(root_raw).expanduser() / "study_metadata_index.json"))
    )


def _citation_index_path(precomputed_root: str | Path) -> str:
    root_raw = str(precomputed_root).strip()
    return (
        urljoin(root_raw.rstrip("/") + "/", "citation_index.json")
        if _is_http_location(root_raw)
        else str((Path(root_raw).expanduser() / "citation_index.json"))
    )


@lru_cache(maxsize=8)
def _load_citation_index(precomputed_root: str | Path) -> dict[str, Any]:
    try:
        payload = _read_json_from_location(_citation_index_path(precomputed_root))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    studies = payload.get("studies")
    return studies if isinstance(studies, dict) else {}


def _related_publications_html(
    publications: Any,
    study_id: str,
) -> str:
    workbench_url = _workbench_study_url(study_id)
    verify_button = _verify_workbench_button(study_id)
    if isinstance(publications, list) and publications:
        items: list[str] = []
        for publication in publications[:8]:
            if not isinstance(publication, dict):
                continue
            citation = str(publication.get("citation") or "").strip()
            if not citation:
                continue
            doi = str(publication.get("doi") or "").strip()
            doi_url = str(publication.get("doi_url") or "").strip() or (
                f"https://doi.org/{doi}" if doi else ""
            )
            pubmed_id = str(publication.get("pubmed_id") or "").strip()
            pubmed_url = str(publication.get("pubmed_url") or "").strip() or (
                f"https://pubmed.ncbi.nlm.nih.gov/{pubmed_id}/" if pubmed_id else ""
            )
            links: list[str] = []
            if doi:
                links.append(
                    f"<a href='{_e(doi_url)}' target='_blank' rel='noopener noreferrer' "
                    "style='color:#0d6e6e;font-weight:800;text-decoration:none'>"
                    f"DOI: {_e(doi)}</a>"
                )
            if pubmed_id:
                links.append(
                    f"<a href='{_e(pubmed_url)}' target='_blank' rel='noopener noreferrer' "
                    "style='color:#0d6e6e;font-weight:800;text-decoration:none'>"
                    f"PubMed: {_e(pubmed_id)}</a>"
                )
            link_html = (
                "<div style='margin-top:3px;font-size:.78rem;color:#51656a'>"
                + " · ".join(links)
                + "</div>"
                if links
                else ""
            )
            items.append(
                "<li style='margin:0 0 7px;padding-left:2px'>"
                f"<span>{_e(citation)}</span>{link_html}</li>"
            )
        if items:
            return (
                "<div style='margin-top:10px;padding-top:10px;border-top:1px solid rgba(13,110,110,.16)'>"
                "<h5 style='margin:0 0 5px;font-size:.82rem;text-transform:uppercase;"
                "letter-spacing:.06em;color:#123135'>Related publications from Metabolomics Workbench</h5>"
                "<p style='margin:0 0 6px;color:#51656a;font-size:.82rem'>"
                "Associated publication(s) detected from source metadata.</p>"
                "<ol style='margin:0 0 0 18px;padding:0;color:#263a3f;font-size:.82rem'>"
                + "".join(items)
                + "</ol>"
                "<p style='margin:8px 0 0;color:#51656a;font-size:.82rem'>"
                "Users should cite both the Metabolomics Workbench accession/project and the associated "
                "publication where applicable.</p>"
                "</div>"
            )
    return (
        "<div style='margin-top:10px;padding-top:10px;border-top:1px solid rgba(13,110,110,.16)'>"
        "<h5 style='margin:0 0 5px;font-size:.82rem;text-transform:uppercase;"
        "letter-spacing:.06em;color:#123135'>Related publications from Metabolomics Workbench</h5>"
        "<p style='margin:0;color:#51656a;font-size:.82rem'>"
        "Associated publication(s): none detected in parsed Metabolomics Workbench metadata. Users should "
        "verify on the "
        f"<a href='{_e(workbench_url)}' target='_blank' rel='noopener noreferrer' "
        "style='color:#0d6e6e;font-weight:800;text-decoration:none'>"
        "original Metabolomics Workbench study page</a> before publication."
        f"{verify_button}</p>"
        "</div>"
    )


def _citation_card_html(summary: dict[str, Any], precomputed_root: str | Path) -> str:
    study_id = str(summary.get("study_id") or "").strip().upper()
    citation_index = _load_citation_index(str(precomputed_root))
    citation = citation_index.get(study_id, {}) if isinstance(citation_index, dict) else {}
    if not isinstance(citation, dict):
        citation = {}
    project_id = str(citation.get("project_id") or "").strip()
    project_doi = str(citation.get("project_doi") or "").strip()
    doi_url = str(citation.get("doi_url") or "").strip()
    related_publications = citation.get("related_publications")
    verify_button = _verify_workbench_button(study_id)
    project_id_html = (
        f"<strong>{_e(project_id)}</strong>"
        if project_id
        else "<strong>NA</strong>"
    )
    if project_doi:
        doi_href = doi_url or f"https://doi.org/{project_doi}"
        doi_html = (
            f"<a href='{_e(doi_href)}' target='_blank' rel='noopener noreferrer' "
            "style='color:#0d6e6e;font-weight:800;text-decoration:none'>"
            f"{_e(project_doi)}</a>"
        )
        doi_sentence = f"The data can be accessed directly via its Project DOI: {doi_html}. "
    else:
        doi_sentence = (
            "Project DOI was not detected in the parsed source metadata at the time of "
            "MERIT-ML access. This limits automated citation completeness for ML-reuse "
            "reporting. Please verify the current Project DOI status on the original "
            f"Metabolomics Workbench project page. {verify_button} "
        )
    related_publications_block = _related_publications_html(related_publications, study_id)
    return (
        "<section style='margin-top:14px;padding:13px 14px;border-radius:16px;"
        "background:rgba(13,110,110,.055);border:1px solid rgba(13,110,110,.18);"
        "color:#132327;line-height:1.55'>"
        "<h4 style='margin:0 0 7px;font-size:.88rem;text-transform:uppercase;"
        "letter-spacing:.07em;color:#123135'>Citation</h4>"
        "<p style='margin:0;color:#51656a;font-size:.82rem'>"
        "This data is available at the NIH Common Fund's National Metabolomics Data Repository "
        "(NMDR) website, the Metabolomics Workbench, "
        "<a href='https://www.metabolomicsworkbench.org' target='_blank' rel='noopener noreferrer' "
        "style='color:#0d6e6e;font-weight:800;text-decoration:none'>https://www.metabolomicsworkbench.org</a> "
        f"where it has been assigned Project ID {project_id_html}. "
        f"{doi_sentence}"
        "This work is supported by Metabolomics Workbench/National Metabolomics Data Repository "
        "(NMDR), Common Fund Data Ecosystem (CFDE), and Metabolomics Consortium "
        "Coordinating Center (M3C)."
        "</p>"
        "<p style='margin:8px 0 0;color:#51656a;font-size:.82rem'>"
        "Please cite the Metabolomics Workbench as: "
        "<strong>The Metabolomics Workbench, "
        "<a href='https://www.metabolomicsworkbench.org/' target='_blank' rel='noopener noreferrer' "
        "style='color:#0d6e6e;font-weight:800;text-decoration:none'>"
        "https://www.metabolomicsworkbench.org/</a></strong>"
        "</p>"
        f"{related_publications_block}"
        "</section>"
    )


def _normalize_platform_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    upper = raw.upper()
    if "NMR" in upper:
        return "NMR"
    if "MS" in upper:
        return "MS"
    return "Other"


def _normalize_analysis_type_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    tokens = [tok for tok in re.split(r"[\s;/,+]+", raw.upper()) if tok]
    if tokens and all(tok == "MS" for tok in tokens):
        return "MS"
    if tokens and all(tok == "NMR" for tok in tokens):
        return "NMR"
    parts = [part.strip() for part in re.split(r"\s*;\s*", raw) if part.strip()]
    return " + ".join(parts) if len(parts) > 1 else raw


@lru_cache(maxsize=8)
def _load_study_browser_data(precomputed_root: str | Path) -> list[dict[str, Any]]:
    try:
        payload = _read_json_from_location(_study_metadata_index_path(precomputed_root))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    studies = payload.get("studies")
    if not isinstance(studies, list):
        return []
    cleaned: list[dict[str, Any]] = []
    def _clean_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        text = str(value or "").strip()
        return [text] if text else []

    for row in studies:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("study_id", "")).strip().upper()
        if not (sid.startswith("ST") and len(sid) == 8 and sid[2:].isdigit()):
            continue
        if _is_embargoed_study(sid):
            continue
        score_raw = row.get("score")
        score = float(score_raw) if isinstance(score_raw, (int, float)) else None
        platform_raw = str(row.get("platform", "")).strip()
        analysis_type_raw = str(row.get("analysis_type", "")).strip()
        analysis_types: list[str] = []
        for value in _clean_list(row.get("analysis_types")):
            normalized = _normalize_analysis_type_label(value)
            if normalized and normalized not in analysis_types:
                analysis_types.append(normalized)
        if not analysis_types:
            for value in re.split(r"[;,]+", analysis_type_raw):
                normalized = _normalize_analysis_type_label(value)
                if normalized and normalized not in analysis_types:
                    analysis_types.append(normalized)
        analysis_type = _normalize_analysis_type_label(analysis_type_raw)
        if not analysis_type and analysis_types:
            analysis_type = " + ".join(analysis_types)
        mass_rt_present = bool(row.get("mass_rt_like_metadata_present"))
        mass_rt_score_raw = row.get("mass_rt_like_metadata_score")
        mass_rt_score = (
            float(mass_rt_score_raw)
            if isinstance(mass_rt_score_raw, (int, float))
            else (1.0 if mass_rt_present else 0.0)
        )
        raw_band = str(row.get("band", "")).strip()
        cleaned.append(
            {
                "study_id": sid,
                "score": score,
                "band": _v2_band_label(raw_band),
                "band_label": _v2_band_label(raw_band),
                "title": str(row.get("title", "")).strip(),
                "organism": str(row.get("organism", "")).strip(),
                "disease": str(row.get("disease", "")).strip(),
                "platform": _normalize_platform_label(platform_raw),
                "platform_raw": platform_raw,
                "analysis_type": analysis_type,
                "analysis_type_raw": analysis_type_raw,
                "analysis_types": analysis_types,
                "ion_modes": _clean_list(row.get("ion_modes")),
                "chromatography_types": _clean_list(row.get("chromatography_types")),
                "instruments": _clean_list(row.get("instruments")),
                "sample_types": _clean_list(row.get("sample_types")),
                "project_type": str(row.get("project_type", "")).strip(),
                "institute": str(row.get("institute", "")).strip(),
                "mass_rt_like_metadata_present": mass_rt_present,
                "mass_rt_like_metadata_score": mass_rt_score,
                "mzrt_metadata_status": "present" if mass_rt_present else "absent",
                "search_text": str(row.get("search_text", "")).strip(),
            }
        )
    return cleaned


def _study_browser_sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            1 if not isinstance(r.get("score"), float) else 0,
            -(r.get("score") or 0.0),
            r.get("study_id", ""),
        ),
    )


def _study_browser_facet_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    vals: dict[str, str] = {}
    for r in rows:
        value = r.get(key, "")
        raw_values = value if isinstance(value, list) else [value]
        for raw in raw_values:
            text = re.sub(r"\s+", " ", str(raw or "").strip())
            if text:
                vals.setdefault(text.casefold(), text)
    return [vals[k] for k in sorted(vals)]


def _study_browser_facets(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    labels = {
        "absent": "Mass/RT-like metadata absent",
        "present": "Mass/RT-like metadata present",
    }
    statuses = {str(r.get("mzrt_metadata_status", "")).strip().lower() for r in rows}
    return {
        "organism": _study_browser_facet_values(rows, "organism"),
        "disease": _study_browser_facet_values(rows, "disease"),
        "analysis_type": _study_browser_facet_values(rows, "analysis_type"),
        "ion_modes": _study_browser_facet_values(rows, "ion_modes"),
        "chromatography_types": _study_browser_facet_values(rows, "chromatography_types"),
        "instruments": _study_browser_facet_values(rows, "instruments"),
        "sample_types": _study_browser_facet_values(rows, "sample_types"),
        "project_type": _study_browser_facet_values(rows, "project_type"),
        "institute": _study_browser_facet_values(rows, "institute"),
        "mzrt_metadata_status": [labels[s] for s in ("absent", "present") if s in statuses],
        "band": sorted({str(r.get("band_label", "")).strip() for r in rows if str(r.get("band_label", "")).strip()}),
    }


def _study_browser_norm(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _study_browser_split_quoted(text: str, sep: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in str(text or ""):
        if ch in {"'", '"'} and not quote:
            quote = ch
            continue
        if quote and ch == quote:
            quote = ""
            continue
        if not quote and ch == sep:
            item = "".join(buf).strip()
            if item:
                out.append(item)
            buf = []
        else:
            buf.append(ch)
    item = "".join(buf).strip()
    if item:
        out.append(item)
    return out


def _study_browser_expand_term(term: str) -> list[str]:
    normalized = _study_browser_norm(term)
    if not normalized:
        return []
    synonyms = {
        "human": ["homo sapiens", "human", "humans"],
        "humans": ["homo sapiens", "human", "humans"],
        "homo sapiens": ["homo sapiens", "human", "humans"],
        "mouse": ["mus musculus", "mouse", "mice"],
        "mice": ["mus musculus", "mouse", "mice"],
        "mus musculus": ["mus musculus", "mouse", "mice"],
        "rat": ["rattus norvegicus", "rat", "rats"],
        "rats": ["rattus norvegicus", "rat", "rats"],
        "rattus norvegicus": ["rattus norvegicus", "rat", "rats"],
        "alzheimer": ["alzheimer", "alzheimers", "alzheimer disease", "alzheimers disease"],
        "alzheimers": ["alzheimer", "alzheimers", "alzheimer disease", "alzheimers disease"],
        "alzheimer disease": ["alzheimer", "alzheimers", "alzheimer disease", "alzheimers disease"],
        "alzheimers disease": ["alzheimer", "alzheimers", "alzheimer disease", "alzheimers disease"],
        "cancer": ["cancer", "tumor", "tumour", "carcinoma", "neoplasm", "malignancy", "adenocarcinoma"],
        "tumor": ["cancer", "tumor", "tumour", "carcinoma", "neoplasm", "malignancy", "adenocarcinoma"],
        "tumour": ["cancer", "tumor", "tumour", "carcinoma", "neoplasm", "malignancy", "adenocarcinoma"],
        "neoplasm": ["cancer", "tumor", "tumour", "carcinoma", "neoplasm", "malignancy", "adenocarcinoma"],
        "diabetes": ["diabetes", "diabetic", "t2d", "type 2 diabetes", "type ii diabetes"],
        "ms": ["ms", "mass spectrometry"],
        "mass spec": ["ms", "mass spectrometry"],
        "mass spectrometry": ["ms", "mass spectrometry"],
        "nmr": ["nmr", "nuclear magnetic resonance"],
        "lc": ["lc", "liquid chromatography"],
        "gc": ["gc", "gas chromatography"],
        "hilic": ["hilic"],
        "rp": ["reversed phase", "reverse phase", "rp"],
        "reversed phase": ["reversed phase", "reverse phase", "rp"],
        "positive": ["positive", "pos"],
        "negative": ["negative", "neg"],
    }
    seen: set[str] = set()
    variants: list[str] = []
    for value in synonyms.get(normalized, [normalized]):
        v = _study_browser_norm(value)
        if v and v not in seen:
            variants.append(v)
            seen.add(v)
    return variants


_STUDY_BROWSER_FIELD_ALIASES = {
    "study": "id",
    "id": "id",
    "accession": "id",
    "title": "title",
    "disease": "disease",
    "condition": "disease",
    "phenotype": "disease",
    "diagnosis": "disease",
    "organism": "organism",
    "species": "organism",
    "taxon": "organism",
    "analysis": "analysis",
    "assay": "analysis",
    "platform": "analysis",
    "method": "method",
    "instrument": "instrument",
    "msinstrument": "instrument",
    "chromatography": "chromatography",
    "chrom": "chromatography",
    "column": "chromatography",
    "sample": "sample",
    "tissue": "sample",
    "matrix": "sample",
    "project": "project",
    "design": "project",
    "institute": "institute",
    "center": "institute",
}


def _study_browser_infer_field(term: str) -> str:
    t = _study_browser_norm(term)
    if t in {"human", "humans", "homo sapiens", "mouse", "mice", "mus musculus", "rat", "rats", "rattus norvegicus"}:
        return "organism"
    if t in {"ms", "mass spec", "mass spectrometry", "nmr", "lc", "gc", "hilic", "rp", "reversed phase", "reverse phase", "positive", "negative"}:
        return "method"
    return ""


def _study_browser_parse_search(raw: str) -> list[list[dict[str, Any]]]:
    text = str(raw or "").strip()
    if not text:
        return []
    concept_parts = _study_browser_split_quoted(text, ",")
    if len(concept_parts) == 1 and "|" not in text and ":" not in text:
        simple = [tok for tok in _study_browser_norm(text).split(" ") if tok]
        if 1 < len(simple) <= 5:
            concept_parts = simple
    groups: list[list[dict[str, Any]]] = []
    for concept in concept_parts:
        alts: list[dict[str, Any]] = []
        for raw_alt in _study_browser_split_quoted(concept, "|"):
            alt = str(raw_alt or "").strip()
            field = ""
            term = alt
            match = re.match(r"^([a-zA-Z_ -]+)\s*:\s*(.+)$", alt)
            if match:
                alias = _study_browser_norm(match.group(1)).replace(" ", "")
                field = _STUDY_BROWSER_FIELD_ALIASES.get(alias, "")
                term = match.group(2)
            if not field:
                field = _study_browser_infer_field(term)
            variants = _study_browser_expand_term(term)
            if variants:
                alts.append({"field": field, "term": _study_browser_norm(term), "variants": variants})
        if alts:
            groups.append(alts)
    return groups


def _study_browser_row_fields(row: dict[str, Any]) -> dict[str, str]:
    analysis_parts: list[Any] = []
    for key in ("analysis_type", "analysis_types", "analysis_type_raw", "platform", "platform_raw", "ion_modes"):
        value = row.get(key)
        if isinstance(value, list):
            analysis_parts.extend(value)
        else:
            analysis_parts.append(value)
    fields = {
        "id": _study_browser_norm(row.get("study_id")),
        "title": _study_browser_norm(row.get("title")),
        "disease": _study_browser_norm(row.get("disease")),
        "organism": _study_browser_norm(row.get("organism")),
        "analysis": _study_browser_norm(" ".join(str(v or "") for v in analysis_parts)),
        "instrument": _study_browser_norm(" ".join(str(v or "") for v in row.get("instruments", []))),
        "chromatography": _study_browser_norm(" ".join(str(v or "") for v in row.get("chromatography_types", []))),
        "sample": _study_browser_norm(" ".join(str(v or "") for v in row.get("sample_types", []))),
        "project": _study_browser_norm(row.get("project_type")),
        "institute": _study_browser_norm(row.get("institute")),
        "all": _study_browser_norm(row.get("search_text")),
    }
    fields["method"] = _study_browser_norm(" ".join([fields["analysis"], fields["instrument"], fields["chromatography"]]))
    return fields


def _study_browser_contains(hay: str, needle: str) -> bool:
    hay_norm = _study_browser_norm(hay)
    needle_norm = _study_browser_norm(needle)
    if not hay_norm or not needle_norm:
        return False
    if len(needle_norm) <= 3 and " " not in needle_norm:
        return f" {needle_norm} " in f" {hay_norm} "
    return needle_norm in hay_norm


def _study_browser_match_search(row: dict[str, Any], raw_search: str) -> tuple[bool, int, list[str]]:
    groups = _study_browser_parse_search(raw_search)
    if not groups:
        return True, 0, []
    fields = _study_browser_row_fields(row)
    labels: list[str] = []
    total_score = 0
    bases = {
        "disease": 100,
        "organism": 95,
        "analysis": 90,
        "method": 90,
        "title": 80,
        "sample": 70,
        "project": 65,
        "instrument": 62,
        "chromatography": 62,
        "institute": 35,
        "all": 20,
    }
    ordered_fields = ["disease", "organism", "analysis", "title", "sample", "project", "instrument", "chromatography", "institute", "all"]
    for group in groups:
        best: tuple[int, str, str] | None = None
        for alt in group:
            field = str(alt.get("field") or "")
            candidates = [field] if field and field in fields else ordered_fields
            for variant in alt.get("variants", []):
                for key in candidates:
                    if not _study_browser_contains(fields.get(key, ""), str(variant)):
                        continue
                    score = bases.get(key, 25)
                    if _study_browser_norm(fields.get(key, "")) == _study_browser_norm(variant):
                        score += 8
                    if best is None or score > best[0]:
                        best = (score, key, str(alt.get("term") or variant))
        if best is None:
            return False, 0, []
        total_score += best[0]
        labels.append(f"{best[1]}:{best[2]}")
    return True, total_score, labels


def _study_browser_value_matches(row_value: Any, selected: str) -> bool:
    selected_norm = _study_browser_norm(selected)
    if not selected_norm:
        return True
    raw_values = row_value if isinstance(row_value, list) else [row_value]
    return any(_study_browser_norm(value) == selected_norm for value in raw_values)


def _study_browser_compact_row(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "study_id",
        "score",
        "band",
        "band_label",
        "title",
        "organism",
        "disease",
        "platform",
        "platform_raw",
        "analysis_type",
        "analysis_type_raw",
        "analysis_types",
        "ion_modes",
        "chromatography_types",
        "instruments",
        "sample_types",
        "project_type",
        "institute",
        "mass_rt_like_metadata_present",
        "mass_rt_like_metadata_score",
        "mzrt_metadata_status",
        "_match_score",
        "_match_labels",
    )
    return {key: row.get(key) for key in keys if key in row}


def _study_browser_data_payload(precomputed_root: str | Path, query: dict[str, str] | None = None) -> dict[str, Any]:
    query = query or {}
    rows = [
        row for row in _study_browser_sorted_rows(_load_study_browser_data(precomputed_root))
        if not _is_embargoed_study(row.get("study_id"))
    ]
    include_facets = str(query.get("facets", "1") or "1").strip().lower() not in {"0", "false", "no"}
    facets = _study_browser_facets(rows) if include_facets else {}
    search = str(query.get("q") or query.get("search") or "").strip()
    facet_keys = {
        "organism": "organism",
        "disease": "disease",
        "analysis_type": "analysis_type",
        "ion_modes": "ion_modes",
        "chromatography_types": "chromatography_types",
        "instruments": "instruments",
        "sample_types": "sample_types",
        "project_type": "project_type",
        "institute": "institute",
        "band": "band_label",
    }
    selected = {key: str(query.get(key, "") or "").strip() for key in list(facet_keys) + ["mzrt_metadata_status"]}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        ok, score, labels = _study_browser_match_search(row, search)
        if not ok:
            continue
        keep = True
        for query_key, row_key in facet_keys.items():
            if selected.get(query_key) and not _study_browser_value_matches(row.get(row_key), selected[query_key]):
                keep = False
                break
        if not keep:
            continue
        mzrt_selected = _study_browser_norm(selected.get("mzrt_metadata_status"))
        if mzrt_selected:
            if "present" in mzrt_selected:
                mzrt_selected = "present"
            elif "absent" in mzrt_selected:
                mzrt_selected = "absent"
            if _study_browser_norm(row.get("mzrt_metadata_status")) != mzrt_selected:
                continue
        item = dict(row)
        item["_match_score"] = score
        item["_match_labels"] = labels
        filtered.append(item)
    if search:
        filtered.sort(
            key=lambda r: (
                -int(r.get("_match_score") or 0),
                1 if not isinstance(r.get("score"), float) else 0,
                -(r.get("score") or 0.0),
                r.get("study_id", ""),
            )
        )
    try:
        limit = int(query.get("limit", 80) or 80)
    except ValueError:
        limit = 80
    try:
        offset = int(query.get("offset", 0) or 0)
    except ValueError:
        offset = 0
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    try:
        bulk_limit = int(query.get("bulk_limit", 500) or 500)
    except ValueError:
        bulk_limit = 500
    try:
        bulk_offset = int(query.get("bulk_offset", 0) or 0)
    except ValueError:
        bulk_offset = 0
    # Keep each Bulk MERIT-ML run bounded, but expose all matched studies as
    # navigable batches so broad filters can be processed 500 at a time.
    bulk_limit = max(1, min(bulk_limit, 500))
    bulk_batch_count = ((len(filtered) + bulk_limit - 1) // bulk_limit) if filtered else 0
    if filtered:
        last_batch_start = (bulk_batch_count - 1) * bulk_limit
        bulk_offset = max(0, min(last_batch_start, bulk_offset))
    else:
        bulk_offset = 0
    bulk_end = min(bulk_offset + bulk_limit, len(filtered))
    bulk_batch_index = (bulk_offset // bulk_limit) + 1 if filtered else 0
    page_rows = filtered[offset : offset + limit]
    bulk_rows = filtered[bulk_offset:bulk_end]
    next_offset = offset + limit if offset + limit < len(filtered) else None
    return {
        "rows": [_study_browser_compact_row(r) for r in page_rows],
        "bulk_rows": [_study_browser_compact_row(r) for r in bulk_rows],
        "total": len(filtered),
        "total_rows": len(rows),
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
        "bulk_offset": bulk_offset,
        "bulk_limit": bulk_limit,
        "bulk_end": bulk_end,
        "bulk_batch_index": bulk_batch_index,
        "bulk_batch_count": bulk_batch_count,
        "bulk_has_prev": bulk_offset > 0,
        "bulk_has_next": bulk_end < len(filtered),
        "facets": facets,
    }


def _study_browser_html(precomputed_root: str | Path, limit: int = 500) -> str:
    return (
        "<section id='study-browser-card' data-endpoint='/study-browser-data' style='margin-top:18px;padding:12px;border:1px solid var(--line);border-radius:14px;"
        "background:rgba(255,255,255,.65)'>"
        "<h3 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.07em;color:#51656a'>"
        "Find Similar Studies</h3>"
	        "<p style='margin:0 0 10px;color:#51656a;font-size:.82rem;line-height:1.45'>"
	        "Search by robust cohort logic. Comma means AND, pipe means OR, and field tags are supported."
	        "</p>"
	        "<input id='study-search-text' placeholder='Example: disease:alzheimers, organism:human, analysis:ms' style='margin-bottom:8px'>"
        "<div id='study-browser-filters' style='display:grid;grid-template-columns:1fr;gap:8px'>"
        "<select id='facet-organism'><option value=''>All organisms</option></select>"
        "<select id='facet-disease'><option value=''>All diseases</option></select>"
        "<select id='facet-analysis-type'><option value=''>All analysis types</option></select>"
        "<select id='facet-ion-mode'><option value=''>All ion modes</option></select>"
        "<select id='facet-chromatography'><option value=''>All chromatography</option></select>"
        "<select id='facet-instrument'><option value=''>All instruments</option></select>"
        "<select id='facet-sample-type'><option value=''>All sample types</option></select>"
        "<select id='facet-project-type'><option value=''>All project types</option></select>"
        "<select id='facet-institute'><option value=''>All institutes</option></select>"
        "<select id='facet-mzrt-status'><option value=''>All mass/RT-like metadata</option></select>"
        "<select id='facet-band'><option value=''>All bands</option></select>"
	        "</div>"
	        "<div id='study-browser-meta' style='margin:10px 0 6px;color:#51656a;font-size:.8rem'></div>"
	        "<div id='bulk-batch-controls' style='display:grid;grid-template-columns:1fr;gap:6px;margin:0 0 8px;"
	        "padding:8px;border:1px solid rgba(13,110,110,.16);border-radius:11px;background:rgba(13,110,110,.05)'>"
	        "<div id='bulk-batch-meta' style='color:#51656a;font-size:.74rem;line-height:1.35'>Bulk batch: loading...</div>"
	        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:7px'>"
	        "<button id='bulk-prev-batch' type='button' disabled "
	        "style='border:1px solid rgba(19,35,39,.13);border-radius:10px;background:rgba(255,255,255,.82);"
	        "color:#51656a;padding:6px 7px;font:inherit;font-size:.72rem;font-weight:800;cursor:pointer'>"
	        "Previous batch</button>"
	        "<button id='bulk-next-batch' type='button' disabled "
	        "style='border:1px solid rgba(13,110,110,.26);border-radius:10px;background:rgba(13,110,110,.08);"
	        "color:#0d6e6e;padding:6px 7px;font:inherit;font-size:.72rem;font-weight:900;cursor:pointer'>"
	        "Next batch</button>"
	        "</div></div>"
	        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:7px;margin:0 0 8px'>"
	        "<button id='bulk-add-filtered' type='button' disabled "
	        "style='border:1px solid rgba(13,110,110,.26);border-radius:11px;background:rgba(13,110,110,.08);"
	        "color:#0d6e6e;padding:8px 8px;font:inherit;font-size:.74rem;font-weight:900;cursor:pointer'>"
	        "Add filtered set</button>"
	        "<button id='study-filter-reset' type='button' "
	        "style='border:1px solid rgba(19,35,39,.13);border-radius:11px;background:rgba(255,255,255,.82);"
	        "color:#51656a;padding:8px 8px;font:inherit;font-size:.74rem;font-weight:800;cursor:pointer'>"
	        "Reset filters</button>"
	        "</div>"
	        "<p style='margin:0 0 7px;color:#6d7f84;font-size:.7rem;line-height:1.35'>"
	        "Query grammar: comma = AND; pipe = OR; tags include organism:, disease:, analysis:, title:, instrument:, chromatography:, sample:, project:. "
	        "Examples: <code>alzheimers, human, ms</code> or <code>disease:cancer|tumor|carcinoma, organism:human, analysis:ms</code>. "
	        "Bulk MERIT-ML runs are capped at 500 studies per run; use batch controls for studies 1-500, 501-1000, etc."
	        "</p>"
	        "<div id='study-browser-list' style='max-height:min(56vh,620px);overflow:auto;border:1px solid var(--line);"
        "border-radius:10px;padding:6px;background:rgba(255,255,255,.84)'>"
        "<div style='padding:8px;color:#51656a;font-size:.82rem'>Loading study browser in batches...</div></div>"
        "<button id='study-load-more' type='button' style='display:none;margin-top:8px;width:100%;"
        "border:1px solid rgba(13,110,110,.24);background:rgba(13,110,110,.07);color:#0d6e6e;"
        "padding:8px;border-radius:10px;font:inherit;font-size:.76rem;font-weight:900;cursor:pointer'>Load more studies</button>"
        "<script id='study-browser-data' type='application/json'>{\"rows\":[],\"lazy\":true}</script>"
        "</section>"
    )


def _bulk_workspace_html(precomputed_root: str | Path) -> str:
    """Render the local-only bulk analysis workspace.

    The selected-study session is intentionally kept in browser localStorage
    until the user explicitly submits it. That keeps the cache immutable and
    avoids creating server-side artifacts for exploratory bulk edits.
    """
    return (
        "<section id='bulk-merit-card' style='margin-top:18px;padding:12px;border:1px solid rgba(13,110,110,.22);"
        "border-radius:14px;background:linear-gradient(180deg,rgba(13,110,110,.09),rgba(255,255,255,.72))'>"
        "<div style='display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px'>"
        "<h3 style='margin:0;font-size:.88rem;text-transform:uppercase;letter-spacing:.07em;color:#123135'>"
        "Bulk MERIT-ML Analysis</h3>"
        "</div>"
        "<p style='margin:0 0 10px;color:#51656a;font-size:.78rem;line-height:1.45'>"
        "Build a study set from Find Similar Studies, save per-study matrix/threshold edits, then run one sortable report."
        "</p>"
        "<div style='display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;margin-bottom:8px'>"
        "<div style='font-size:.78rem;color:#51656a'>Selected studies: "
        "<strong id='bulk-study-count' style='color:#0d6e6e'>0</strong></div>"
        "<select id='bulk-sort' style='padding:6px 8px;border-radius:10px;font-size:.74rem'>"
        "<option value='added'>Sort: added</option>"
        "<option value='study_id'>Study ID</option>"
        "<option value='score'>Score</option>"
        "<option value='organism'>Organism</option>"
        "<option value='samples'>Samples</option>"
        "</select>"
        "</div>"
        "<div id='bulk-study-list' style='max-height:190px;overflow:auto;border:1px solid rgba(19,35,39,.1);"
        "border-radius:12px;background:rgba(255,255,255,.84);padding:6px;margin-bottom:10px'>"
        "<div style='padding:8px;color:#7b8b90;font-size:.78rem;line-height:1.35'>"
        "No studies selected yet. Use <strong>Add to bulk</strong> in Find Similar Studies.</div>"
        "</div>"
        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px'>"
        "<button id='bulk-save-current' type='button' style='border:1px solid rgba(13,110,110,.28);"
        "border-radius:12px;background:rgba(13,110,110,.08);color:#0d6e6e;padding:8px 9px;"
        "font:inherit;font-size:.76rem;font-weight:800;cursor:pointer'>Save current edits</button>"
        "<button id='bulk-clear' type='button' style='border:1px solid rgba(143,45,45,.24);"
        "border-radius:12px;background:rgba(143,45,45,.06);color:#8f2d2d;padding:8px 9px;"
        "font:inherit;font-size:.76rem;font-weight:800;cursor:pointer'>Clear</button>"
        "</div>"
        "<form id='bulk-run-form' method='post' action='/bulk/run' style='margin:0'>"
        "<input type='hidden' id='bulk-session-field' name='bulk_session' value=''>"
        "<button id='bulk-run-button' type='submit' style='width:100%;border:0;border-radius:13px;"
        "background:#0d6e6e;color:white;padding:10px 12px;font:inherit;font-size:.82rem;"
        "font-weight:900;cursor:pointer;box-shadow:0 10px 20px rgba(13,110,110,.18)'>"
        "Run Bulk MERIT-ML</button>"
        "</form>"
        "<button id='bulk-download-session' type='button' style='width:100%;margin-top:8px;border:1px solid rgba(19,35,39,.14);"
        "border-radius:12px;background:rgba(255,255,255,.82);color:#51656a;padding:8px 9px;"
        "font:inherit;font-size:.75rem;font-weight:800;cursor:pointer'>Download bulk session JSON</button>"
        "<p id='bulk-status' style='margin:8px 0 0;color:#51656a;font-size:.72rem;line-height:1.35'>"
        "Tip: open a study, edit matrix labels or thresholds, then click Save current edits before moving to another study."
        "</p>"
        "</section>"
    )


def _sidebar_top_worst_html(precomputed_root: str | Path = "merit-cache-workbench-full-v7", limit: int = 5) -> str:
    """Render quick reference lists for best/worst study scores."""
    root_raw = str(precomputed_root).strip()
    try:
        index_loc = (
            urljoin(root_raw.rstrip("/") + "/", "index.json")
            if _is_http_location(root_raw)
            else str((Path(root_raw).expanduser() / "index.json"))
        )
        payload = _read_json_from_location(index_loc)
        studies = (payload or {}).get("studies", {})
        scored: list[tuple[str, float, str]] = []
        valid_ids: set[str] | None = None

        # For local cache roots, cross-check real files so sidebar does not show stale index-only IDs.
        if not _is_http_location(root_raw):
            json_dir = Path(root_raw).expanduser() / "json"
            if json_dir.exists():
                readiness_ids = {
                    p.name.split("_", 1)[0].upper()
                    for p in json_dir.glob("st*_readiness_score.json")
                }
                workflow_ids = {
                    p.name.split("_", 1)[0].upper()
                    for p in json_dir.glob("st*_workflow_state.json")
                }
                valid_ids = readiness_ids & workflow_ids

        if isinstance(studies, dict):
            for sid, entry in studies.items():
                if not isinstance(entry, dict):
                    continue
                sid_norm = str(sid).upper()
                if valid_ids is not None and sid_norm not in valid_ids:
                    continue
                score = entry.get("score")
                if isinstance(score, (int, float)):
                    scored.append((sid_norm, float(score), str(entry.get("band", ""))))
        if not scored:
            raise ValueError("No scored studies in index.")

        top = sorted(scored, key=lambda x: x[1], reverse=True)[:limit]
        worst = sorted(scored, key=lambda x: x[1])[:limit]

        def _render(rows: list[tuple[str, float, str]]) -> str:
            return "".join(
                f"<li style='display:flex;justify-content:space-between;gap:8px;padding:4px 0;'>"
                f"<span style='font-family:IBM Plex Mono,monospace;font-size:.83rem'>{_e(sid)}</span>"
                f"<span style='font-size:.82rem;font-weight:700;color:{_score_color(score)}'>{_score_100_text(score)}</span>"
                f"</li>"
                for sid, score, _band in rows
            )

        return (
            "<section style='margin-top:18px;padding:12px;border:1px solid var(--line);border-radius:14px;"
            "background:rgba(255,255,255,.65)'>"
            "<h3 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.07em;color:#51656a'>"
            "Study Score Reference</h3>"
            "<p style='margin:0 0 10px;color:#51656a;font-size:.82rem;line-height:1.45'>"
            "Reference only. Scores are shown on the 0–100 display scale. Enter any ST accession on the right.</p>"
            "<div style='display:grid;grid-template-columns:1fr;gap:8px'>"
            "<div><div style='font-size:.78rem;font-weight:700;color:#196b4a;margin-bottom:2px'>Top scored</div>"
            f"<ul style='list-style:none;padding:0;margin:0'>{_render(top)}</ul></div>"
            "<div><div style='font-size:.78rem;font-weight:700;color:#8f2d2d;margin:4px 0 2px'>Worst scored</div>"
            f"<ul style='list-style:none;padding:0;margin:0'>{_render(worst)}</ul></div>"
            "</div>"
            "</section>"
        )
    except Exception:
        return (
            "<section style='margin-top:18px;padding:12px;border:1px solid var(--line);border-radius:14px;"
            "background:rgba(255,255,255,.65)'>"
            "<h3 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.07em;color:#51656a'>"
            "Study Score Reference</h3>"
            "<p style='margin:0;color:#7a8d92;font-size:.82rem'>Reference index unavailable right now.</p>"
            "</section>"
        )


def _score_color(score: float) -> str:
    if score >= 0.8:
        return "#196b4a"
    if score >= 0.5:
        return "#995b00"
    return "#8f2d2d"


def _score_100_text(score: Any, digits: int = 1) -> str:
    try:
        return f"{float(score) * 100:.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _status_display_label(status: str) -> str:
    return {
        "pass": "pass",
        "warn": "warn",
        "fail": "fail",
    }.get(str(status or "").strip().lower(), str(status or "").strip() or "not assessed")


def _status_badge(status: str) -> str:
    colors = {"pass": ("#196b4a", "#e6f4ed"), "warn": ("#995b00", "#fdf3e3"), "fail": ("#8f2d2d", "#fdeaea")}
    fg, bg = colors.get(status.lower(), ("#51656a", "#f0f0f0"))
    label = _status_display_label(status)
    return (
        f"<span style='display:inline-flex;align-items:center;justify-content:center;"
        f"min-width:74px;white-space:nowrap;line-height:1;background:{bg};color:{fg};"
        f"padding:3px 9px;border-radius:999px;font-size:0.75rem;font-weight:700;"
        f"letter-spacing:.02em'>{_e(label)}</span>"
    )


def _score_bar(score: float) -> str:
    pct = max(0, min(100, int(float(score) * 100)))
    color = _score_color(score)
    return (
        f"<div style='display:flex;align-items:center;gap:8px'>"
        f"<div style='flex:1;height:6px;border-radius:3px;background:#e2e8e9;overflow:hidden'>"
        f"<div style='width:{pct}%;height:100%;background:{color};border-radius:3px'></div></div>"
        f"<span style='font-variant-numeric:tabular-nums;font-size:.85rem;font-weight:700;color:{color}'>{_score_100_text(score)}</span>"
        f"</div>"
    )


def _pill(text: str, color: str = "#0d6e6e") -> str:
    return f"<span style='background:{color}18;color:{color};padding:2px 8px;border-radius:999px;font-size:0.78rem;margin:2px'>{_e(text)}</span>"


def _safe_missing_rate(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _source_aware_missingness_rate(report: Any) -> float | None:
    """Return source-aware median sample missingness from missingness_structure."""
    analytical = getattr(report, "analytical_readiness", []) or []
    for metric in analytical:
        if getattr(metric, "name", "") != "missingness_structure":
            continue
        details = getattr(metric, "details", {}) or {}
        for key in (
            "global_median_sample_missingness_rate",
            "median_sample_missingness_rate",
            "global_mean_sample_missingness_rate",
            "mean_sample_missingness_rate",
        ):
            value = details.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _analysis_id_label(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"an\d+", text, flags=re.IGNORECASE):
        return text.upper()
    return text


def _normalize_analysis_tokens(text: Any) -> str:
    raw = str(text or "")
    return re.sub(r"\ban(\d{6})\b", lambda match: f"AN{match.group(1)}", raw, flags=re.IGNORECASE)


def _mini_info_icon(text: str, *, size: int = 13) -> str:
    """Compact tooltip icon for inline metric/context help."""
    if not str(text).strip():
        return ""
    return (
        f"<span class='minfo' tabindex='0'>"
        f"<span class='minfo-icon' style='margin-left:0;width:{size}px;height:{size}px;font-size:.62rem'>i</span>"
        f"<div class='minfo-popup' style='max-width:320px'>{_e(text)}</div>"
        f"</span>"
    )


_OVERVIEW_STAT_HELP: dict[str, str] = {
    "Samples": "All sample rows detected in the loaded matrices, including QC, pool, blank, and reference controls.",
    "ML-eligible": "Samples retained for ML assessment after excluding QC/pool/blank/reference/system-suitability controls.",
    "Features": "Total machine-readable feature columns (metabolites or peak IDs) across all loaded matrices.",
    "Matrices": "Total number of non-empty assay matrices across all available data sources (datatable + mwTab + untarg_data) for this study.",
    "Matrices (this source)": "Number of non-empty assay matrices in the currently selected source tab only.",
    "Missingness": "Source-aware median sample-level missingness from the readiness metric when available; otherwise weighted ingestion missingness.",
}


# ---------------------------------------------------------------------------
# Study header card
# ---------------------------------------------------------------------------

def _analysis_type_text(summary: dict[str, Any]) -> str:
    raw = str(summary.get("analysis_type") or "").strip()
    if raw:
        parts: list[str] = []
        for token in raw.replace("/", ";").split(";"):
            normalized = token.strip()
            if normalized and normalized not in parts:
                parts.append(normalized)
        return "; ".join(parts)

    items = summary.get("analysis_types") or []
    parsed: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in parsed:
            parsed.append(text)
    if parsed:
        return "; ".join(parsed)

    per_analysis = summary.get("per_analysis") or []
    fallback: list[str] = []
    for row in per_analysis:
        platform = str((row or {}).get("platform") or "").strip()
        if platform and platform not in fallback:
            fallback.append(platform)
    return "; ".join(fallback)


def _design_summary_sentence(summary: dict[str, Any]) -> str:
    """Auto-generate a one-line study design description."""
    n_bio = summary.get("n_biological_samples") or summary.get("n_samples") or 0
    organism = str(summary.get("organism") or "").strip()
    tissues = summary.get("tissues") or []
    tissue_str = ", ".join(str(t).lower() for t in tissues[:2]) if tissues else ""
    platform = _analysis_type_text(summary) or str(summary.get("platform") or "").strip()
    disease = str(summary.get("disease") or "").strip()
    has_disease = summary.get("has_disease_endpoint", False)
    n_classes = summary.get("n_classes") or len(summary.get("class_counts") or {})
    n_labeled = summary.get("n_labeled_samples") or 0

    has_label_endpoint = False
    if n_bio:
        try:
            label_coverage = float(n_labeled) / float(n_bio)
        except Exception:
            label_coverage = 0.0
        has_label_endpoint = (n_classes >= 2) and (label_coverage >= 0.5)

    parts: list[str] = []
    if n_bio:
        parts.append(f"<strong>{n_bio}</strong>")
    if organism:
        parts.append(f"<em>{_e(organism)}</em>")
    if tissue_str:
        parts.append(_e(tissue_str))
    parts.append("samples")
    if platform:
        parts.append(f"profiled by <strong>{_e(platform)}</strong>")
    if disease and has_disease:
        parts.append(f"· disease context: <strong>{_e(disease)}</strong>")
        if n_classes >= 2:
            parts.append(f"({n_classes} classes)")
    elif not has_disease:
        if has_label_endpoint:
            parts.append(f"· no study-level disease metadata (label endpoint available: <strong>{n_classes} classes</strong>)")
        else:
            parts.append("· <span style='color:#8f2d2d'>no disease endpoint</span>")

    if not parts:
        return ""
    sentence = " ".join(parts)
    return (
        f"<p style='font-size:.9rem;color:#132327;line-height:1.65;margin:0 0 14px;"
        f"padding-bottom:12px;border-bottom:1px solid rgba(19,35,39,.08)'>{sentence}</p>"
    )


def _study_design_context_notice(
    study_design_context: dict[str, Any] | None,
    summary: dict[str, Any],
    readiness_score: dict[str, Any],
) -> str:
    """Render a non-scoring study-design context label in Overview."""
    if not isinstance(study_design_context, dict) or not study_design_context:
        return ""

    primary = str(study_design_context.get("primary_design_context") or "unclear").strip()
    sample_context = study_design_context.get("sample_context") if isinstance(study_design_context.get("sample_context"), dict) else {}
    n_ml = sample_context.get("n_ml_eligible_samples")
    if n_ml is None:
        n_ml = summary.get("n_biological_samples", summary.get("n_samples"))
    try:
        n_ml_int = int(n_ml)
    except Exception:
        n_ml_int = None

    g2_status = str(sample_context.get("g2_sample_gate_status_at_assignment") or "").strip().lower()
    for gate in readiness_score.get("gates", []) or []:
        if isinstance(gate, dict) and str(gate.get("id", "")).upper() == "G2":
            g2_status = str(gate.get("status") or g2_status).strip().lower()
            break

    small_n = bool(study_design_context.get("small_n_flag")) or (n_ml_int is not None and n_ml_int < 20)
    sample_gate_limited = g2_status in {"warn", "fail"}
    feasibility_limited = small_n or sample_gate_limited

    label_map = {
        "experimental_or_replicate_based": "Experimental / replicate-based",
        "cohort_or_subject_based": "Cohort / subject-based",
        "metadata_only_or_no_usable_matrix": "Metadata-only / no usable matrix",
        "unclear": "Unclear study design",
    }
    color_map = {
        "experimental_or_replicate_based": ("#0d6e6e", "rgba(13,110,110,.08)", "rgba(13,110,110,.22)"),
        "cohort_or_subject_based": ("#995b00", "rgba(210,125,45,.09)", "rgba(210,125,45,.26)"),
        "metadata_only_or_no_usable_matrix": ("#51656a", "rgba(81,101,106,.08)", "rgba(81,101,106,.22)"),
        "unclear": ("#6f5a00", "rgba(246,199,68,.10)", "rgba(246,199,68,.30)"),
    }
    label = label_map.get(primary, "Unclear study design")
    ink, bg, border = color_map.get(primary, color_map["unclear"])

    if primary == "experimental_or_replicate_based":
        if feasibility_limited:
            body = (
                "Small replicate numbers may be appropriate for the original experimental objective. "
                "MERIT-ML still applies supervised-classification feasibility rules; the readiness band describes "
                "reuse for classifier training, validation, and feature selection."
            )
        else:
            body = (
                "The metadata suggests an experimental or replicate-based design. This context helps interpret "
                "whether n primarily represents biological replicates, engineered perturbations, time points, "
                "or related experimental units; it does not change MERIT-ML scores or bands."
            )
    elif primary == "cohort_or_subject_based":
        if feasibility_limited:
            body = (
                "This appears to be subject/specimen-based. Small sample count or limited class support can "
                "restrict classifier training, validation, and feature-selection stability."
            )
        else:
            body = (
                "The metadata suggests a subject/specimen-based design. This context helps interpret n as "
                "samples, subjects, or specimens for supervised-classification reuse; it does not change "
                "MERIT-ML scores or bands."
            )
    elif primary == "metadata_only_or_no_usable_matrix":
        body = (
            "The available record does not provide enough usable supervised-learning matrix/class context "
            "to distinguish design-specific sample-size expectations."
        )
    else:
        if feasibility_limited:
            body = (
                "The available metadata was not explicit enough to confidently distinguish cohort/specimen-based "
                "from experimental/replicate-based design. Interpret small-n feasibility gates with that context."
            )
        else:
            body = (
                "The available metadata was not explicit enough to confidently distinguish cohort/specimen-based "
                "from experimental/replicate-based design. This context is shown for transparency and does not "
                "change MERIT-ML scores or bands."
            )

    confidence = str(study_design_context.get("confidence") or "low").strip().lower()
    confidence_label = {"high": "High confidence", "moderate": "Moderate confidence", "low": "Low confidence"}.get(confidence, "Low confidence")
    n_term = str(study_design_context.get("n_term") or "unclear").replace("_", " ").strip()
    reason = str(study_design_context.get("reason_short") or "").strip()
    tags = study_design_context.get("specific_design_tags")
    tag_labels: list[str] = []
    if isinstance(tags, list):
        for item in tags[:5]:
            text = str(item or "").replace("_", " ").strip()
            if text:
                tag_labels.append(text)
    tag_html = "".join(
        f"<span style='font-size:.7rem;font-weight:700;color:{ink};background:rgba(255,255,255,.72);"
        f"border:1px solid {border};border-radius:999px;padding:2px 7px'>{_e(tag)}</span>"
        for tag in tag_labels
    )
    n_text = f"{n_ml_int} ML-eligible samples" if n_ml_int is not None else "ML-eligible sample count unavailable"
    if n_term:
        n_text += f" · n interpreted as {_e(n_term)}"

    tooltip = (
        "Study-design context is a MERIT-ML-derived label, not a native Metabolomics Workbench field. It was inferred "
        "from multiple Metabolomics Workbench metadata fields including title, summary, project type, organism, "
        "disease field, sample source, tissue or sample matrix, factors, class labels, and design terms "
        "such as time-course, cell culture, in vitro, treatment, dose response, and isotope tracing. "
        "It does not change MERIT-ML scores, gates, or bands."
    )
    disclaimer = (
        "Note: this label is inferred from multiple metadata fields and is not directly provided by "
        "Metabolomics Workbench; use it as contextual guidance."
    )

    reason_html = (
        f"<div style='margin-top:6px;color:#51656a;font-size:.76rem;line-height:1.45'>Evidence note: {_e(reason)}</div>"
        if reason
        else ""
    )

    return (
        f"<div style='margin:0 0 14px;padding:13px 15px;border-radius:14px;background:{bg};"
        f"border:1px solid {border};color:#132327'>"
        f"<div style='display:flex;align-items:center;gap:8px;justify-content:space-between;flex-wrap:wrap;margin-bottom:7px'>"
        f"<div style='display:flex;align-items:center;gap:7px;flex-wrap:wrap'>"
        f"<strong style='color:{ink};font-size:.9rem'>Study-design context: {_e(label)}</strong>"
        f"{_mini_info_icon(tooltip, size=12)}"
        f"<span style='font-size:.7rem;font-weight:800;color:{ink};background:rgba(255,255,255,.78);"
        f"border:1px solid {border};border-radius:999px;padding:2px 8px'>{_e(confidence_label)}</span>"
        f"</div>"
        f"<span style='font-size:.74rem;font-weight:700;color:#51656a'>{n_text}</span>"
        f"</div>"
        f"<div style='font-size:.84rem;line-height:1.55;color:#132327'>{_e(body)}</div>"
        f"<div style='margin-top:7px;color:#51656a;font-size:.76rem;line-height:1.45'>{_e(disclaimer)}</div>"
        f"{reason_html}"
        f"<div style='display:flex;gap:6px;flex-wrap:wrap;margin-top:8px'>{tag_html}</div>"
        f"</div>"
    )


@lru_cache(maxsize=1)
def _source_sample_count_notes() -> dict[str, dict[str, str]]:
    """Load non-scoring source sample-count discrepancy notes for affected studies."""
    path = Path(__file__).with_name("source_sample_count_notes.json")
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    notes: dict[str, dict[str, str]] = {}
    for study_id, note in payload.items():
        if isinstance(note, dict):
            notes[str(study_id).strip().upper()] = {str(k): str(v) for k, v in note.items()}
    return notes


def _source_sample_count_notice(study_id: str) -> str:
    """Render a non-scoring note when deposited sources expose different sample counts."""
    sid = str(study_id or "").strip().upper()
    if not sid:
        return ""
    note = _source_sample_count_notes().get(sid)
    if not note:
        return ""

    label_map = {
        "datatable": "Datatable",
        "mwtab": "mwTab",
        "untarg_data": "Untarg Data",
    }
    counts = []
    for part in str(note.get("counts", "")).split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        counts.append(f"{label_map.get(key, key)}: {value}")
    count_text = " · ".join(counts) if counts else "Sample counts differ across deposited sources."

    reason = str(note.get("possible_reason", "")).strip()
    user_note = str(note.get("user_note", "")).strip()
    tooltip = (
        "This is a display-only source-comparison note. MERIT-ML scores each deposited source independently "
        "using the usable samples present in that source's feature matrix. The note does not change any "
        "score, gate, band, or downloaded result JSON."
    )
    return (
        "<div style='margin:12px 0 0;padding:12px 14px;border-radius:14px;"
        "background:rgba(246,199,68,.12);border:1px solid rgba(153,91,0,.24);"
        "color:#132327;line-height:1.45'>"
        "<div style='display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:5px'>"
        "<strong style='color:#995b00;font-size:.86rem'>Source sample-count difference detected</strong>"
        f"{_mini_info_icon(tooltip, size=11)}"
        f"<span style='font-size:.74rem;color:#51656a;font-weight:700'>{_e(count_text)}</span>"
        "</div>"
        f"<div style='font-size:.82rem;color:#132327'>Possible reason: {_e(reason or 'Deposited sources may expose different sample subsets or matrix layouts.')} "
        f"{_e(user_note or 'Review source tabs before choosing a matrix for reuse.')}</div>"
        "</div>"
    )


def _study_header(summary: dict[str, Any]) -> str:
    disease_flag = ""
    study_id = summary.get("study_id")
    verify_button = _verify_workbench_button(study_id)
    n_bio = int(summary.get("n_biological_samples") or summary.get("n_samples") or 0)
    n_classes = int(summary.get("n_classes") or len(summary.get("class_counts") or {}))
    n_labeled = int(summary.get("n_labeled_samples") or 0)
    label_coverage = (float(n_labeled) / float(n_bio)) if n_bio > 0 else 0.0
    has_label_endpoint = (n_classes >= 2) and (label_coverage >= 0.5)

    if not summary.get("has_disease_endpoint") and not has_label_endpoint:
        disease_flag = (
            "<div style='background:#fdeaea;border:1px solid #f5b5b5;border-radius:12px;"
            "padding:12px 16px;margin-bottom:16px;color:#8f2d2d;font-weight:600'>"
            "&#9888; No disease endpoint detected in metadata or sample labels — the dataset may not support supervised classification. "
            f"The report is generated, but interpret readiness scores with caution.{verify_button}"
            "</div>"
        )
    elif not summary.get("has_disease_endpoint") and has_label_endpoint:
        disease_flag = (
            "<div style='background:#fdf3e3;border:1px solid #f6d19b;border-radius:12px;"
            "padding:12px 16px;margin-bottom:16px;color:#995b00;font-weight:600'>"
            f"&#9432; No study-level disease metadata detected. Label-based endpoint is inferred from sample labels.{verify_button}"
            "</div>"
        )

    def row(label: str, value: Any, field_key: str | None = None) -> str:
        val_str = str(value).strip()
        if not val_str:
            return ""
        verify = _overview_verify_chip(study_id, field_key) if field_key else ""
        return (
            f"<div style='display:flex;align-items:flex-start;gap:12px;padding:6px 0;border-bottom:1px solid rgba(19,35,39,.06)'>"
            f"<span style='min-width:160px;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a'>{label}</span>"
            f"<span style='font-size:.9rem;flex:1;min-width:0'>{_e(val_str)}</span>{verify}</div>"
        )

    def pills_row(label: str, items: list, field_key: str | None = None) -> str:
        if not items:
            return ""
        pills = "".join(_pill(str(i)) for i in items)
        verify = _overview_verify_chip(study_id, field_key) if field_key else ""
        return (
            f"<div style='display:flex;align-items:flex-start;gap:12px;padding:6px 0;border-bottom:1px solid rgba(19,35,39,.06)'>"
            f"<span style='min-width:160px;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a'>{label}</span>"
            f"<span style='flex:1;min-width:0'>{pills}</span>{verify}</div>"
        )

    # Annotation coverage row — pill per analysis showing tier
    _tier_colors = {
        "named": ("#196b4a", "Named ✓"),
        "mixed": ("#995b00", "Mixed"),
        "unannotated": ("#8f2d2d", "mz/RT only"),
        "no_annotations": ("#51656a", "None"),
    }
    per_analysis = summary.get("per_analysis", [])
    ann_pills_html = ""
    if per_analysis:
        for a in per_analysis:
            tier = a.get("annotation_tier", "no_annotations")
            color, label_text = _tier_colors.get(tier, ("#51656a", tier))
            aid = _analysis_id_label(a.get("analysis_id", ""))
            ann_pills_html += (
                f"<span title='{_e(aid)}' style='background:{color}18;color:{color};"
                f"padding:2px 9px;border-radius:999px;font-size:0.78rem;margin:2px;font-weight:600'>"
                f"{_e(aid[:18] + ('…' if len(aid) > 18 else ''))}: {label_text}</span>"
            )
    named_count = sum(1 for a in per_analysis if a.get("annotation_tier") == "named")
    mixed_count = sum(1 for a in per_analysis if a.get("annotation_tier") == "mixed")
    ann_note = ""
    if per_analysis:
        total_a = len(per_analysis)
        has_named = named_count + mixed_count
        if has_named == 0:
            ann_note = (
                "<span style='font-size:.78rem;color:#8f2d2d;margin-left:6px'>"
                "⚠ No named metabolite annotations on any analysis — mz/RT only.</span>"
            )
        elif has_named < total_a:
            ann_note = f"<span style='font-size:.78rem;color:#995b00;margin-left:6px'>Named annotations available on {has_named}/{total_a} analyses.</span>"
        else:
            ann_note = f"<span style='font-size:.78rem;color:#196b4a;margin-left:6px'>Named annotations available on all {total_a} analyses.</span>"
    ann_row = (
        f"<div style='display:flex;flex-wrap:wrap;gap:4px;align-items:center;padding:6px 0;border-bottom:1px solid rgba(19,35,39,.06)'>"
        f"<span style='min-width:160px;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a;flex-shrink:0'>Annotations</span>"
        f"<span style='display:flex;flex-wrap:wrap;gap:2px;align-items:center;flex:1;min-width:0'>{ann_pills_html}{ann_note}</span>"
        f"{_overview_verify_chip(study_id, 'annotations')}"
        f"</div>"
    ) if per_analysis else ""

    # Class distribution row — pills for each class + imbalance badge
    class_counts: dict[str, int] = summary.get("class_counts", {})
    class_row = ""
    if class_counts:
        counts = list(class_counts.items())
        total_labeled = sum(c for _, c in counts)
        min_c = min(c for _, c in counts)
        max_c = max(c for _, c in counts)
        imbalance_ratio = min_c / max_c if max_c > 0 else 1.0
        class_pill_colors = ["#0d6e6e", "#113e52", "#d27d2d", "#6a3d99", "#2d7d8f", "#7d5a2d", "#2d8f52"]
        class_pills_html = ""
        for idx, (lbl, cnt) in enumerate(counts[:20]):
            color = class_pill_colors[idx % len(class_pill_colors)]
            display_lbl = lbl[:30] + ("…" if len(lbl) > 30 else "")
            class_pills_html += (
                f"<span style='background:{color}18;color:{color};padding:2px 9px;"
                f"border-radius:999px;font-size:0.78rem;margin:2px;font-weight:600'>"
                f"{_e(display_lbl)} ({cnt})</span>"
            )
        if len(counts) > 20:
            class_pills_html += f"<span style='font-size:.78rem;color:#51656a;margin:2px'>+{len(counts)-20} more</span>"
        # Labeled sample count vs ML-eligible sample count
        n_labeled = summary.get("n_labeled_samples", total_labeled)
        n_bio = summary.get("n_biological_samples", 0)
        discrepancy_note = ""
        if n_bio > 0 and n_labeled != n_bio:
            discrepancy_note = (
                f"<span style='background:#fdf3e3;color:#995b00;padding:2px 8px;border-radius:8px;"
                f"font-size:.75rem;font-weight:700;margin-left:6px'>"
                f"⚠ {n_labeled} labeled / {n_bio} ML-eligible samples</span>"
            )
        imbalance_badge = ""
        if len(counts) > 1:
            if imbalance_ratio < 0.3:
                imbalance_badge = "<span style='background:#fdeaea;color:#8f2d2d;padding:2px 8px;border-radius:8px;font-size:.75rem;font-weight:700;margin-left:6px'>Severe imbalance</span>"
            elif imbalance_ratio < 0.5:
                imbalance_badge = "<span style='background:#fdf3e3;color:#995b00;padding:2px 8px;border-radius:8px;font-size:.75rem;font-weight:700;margin-left:6px'>Moderate imbalance</span>"
            elif imbalance_ratio < 0.7:
                imbalance_badge = "<span style='background:#fffbe6;color:#7d6200;padding:2px 8px;border-radius:8px;font-size:.75rem;font-weight:700;margin-left:6px'>Mild imbalance</span>"
            else:
                imbalance_badge = "<span style='background:#e6f4ed;color:#196b4a;padding:2px 8px;border-radius:8px;font-size:.75rem;font-weight:700;margin-left:6px'>Balanced</span>"
        class_row = (
            f"<div style='display:flex;flex-wrap:wrap;gap:4px;align-items:center;padding:6px 0;border-bottom:1px solid rgba(19,35,39,.06)'>"
            f"<span style='min-width:160px;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a;flex-shrink:0'>Class Labels</span>"
            f"<span style='display:flex;flex-wrap:wrap;gap:2px;align-items:center;flex:1;min-width:0'>{class_pills_html}{imbalance_badge}{discrepancy_note}</span>"
            f"{_overview_verify_chip(study_id, 'class_labels')}"
            f"</div>"
        )
    elif summary.get("n_classes", 0) == 0:
        class_row = (
            f"<div style='display:flex;gap:12px;padding:6px 0;border-bottom:1px solid rgba(19,35,39,.06)'>"
            f"<span style='min-width:160px;font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a'>Class Labels</span>"
            f"<span style='font-size:.85rem;color:#8f2d2d;font-style:italic;flex:1;min-width:0'>No class labels detected</span>"
            f"{_overview_verify_chip(study_id, 'class_labels')}"
            f"</div>"
        )

    # Prefer source-aware missingness from the scored metric. The ingestion
    # summary can undercount mwTab/untarg zeros, which MERIT-ML treats as missing.
    _source_aware_missing = summary.get("_overview_missingness_rate")
    if _source_aware_missing is not None:
        _miss_pct = _safe_missing_rate(_source_aware_missing) * 100
        _miss_str = f"{_miss_pct:.1f}%"
        _miss_color = "#196b4a" if _miss_pct < 5 else ("#995b00" if _miss_pct < 25 else "#8f2d2d")
    else:
        _pa = summary.get("per_analysis") or []
        _total_cells = sum(a.get("n_samples", 0) * a.get("n_features", 0) for a in _pa)
        if _total_cells > 0:
            _missing_cells = sum(
                _safe_missing_rate(a.get("missing_rate", 0)) * a.get("n_samples", 0) * a.get("n_features", 0)
                for a in _pa
            )
            _miss_pct = _missing_cells / _total_cells * 100
            _miss_str = f"{_miss_pct:.1f}%"
            _miss_color = "#196b4a" if _miss_pct < 5 else ("#995b00" if _miss_pct < 25 else "#8f2d2d")
        else:
            _miss_str = "—"
            _miss_color = "#51656a"

    stat_items: list[tuple[str, Any, str]] = [
        ("Samples", summary.get("n_samples", "—"), "#0d6e6e"),
        ("ML-eligible", summary.get("n_biological_samples", "—"), "#0d6e6e"),
        ("Features", summary.get("n_features", "—"), "#0d6e6e"),
        ("Matrices", summary.get("n_feature_matrices", "—"), "#0d6e6e"),
    ]
    try:
        source_matrix_count = int(summary.get("n_feature_matrices_this_source", 0) or 0)
        total_matrix_count = int(summary.get("n_feature_matrices", 0) or 0)
    except Exception:
        source_matrix_count = 0
        total_matrix_count = 0
    if source_matrix_count > 0 and total_matrix_count > 0 and source_matrix_count != total_matrix_count:
        stat_items.append(("Matrices (this source)", source_matrix_count, "#0d6e6e"))
    stat_items.append(("Missingness", _miss_str, _miss_color))

    stats = (
        f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:12px;margin-top:16px'>"
        + "".join(
            f"<div style='padding:12px;border-radius:14px;background:rgba(13,110,110,.06);text-align:center'>"
            f"<div style='display:flex;justify-content:center;min-height:14px;margin-bottom:2px'>"
            f"{_mini_info_icon(_OVERVIEW_STAT_HELP.get(k, ''), size=12)}"
            f"</div>"
            f"<div style='font-size:1.4rem;font-weight:700;color:{c}'>{v}</div>"
            f"<div style='font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a;margin-top:4px'>{k}</div>"
            f"</div>"
            for k, v, c in stat_items
        )
        + "</div>"
    )

    # Provenance block — dates + content hash
    sub_date = summary.get("submission_date", "") or ""
    rel_date = summary.get("release_date", "") or ""
    acc_date = summary.get("accessed_date", "") or ""
    content_hash = summary.get("content_hash", "") or ""
    hash_short = content_hash[:16] if content_hash else ""
    prov_items = []
    if sub_date:
        prov_items.append(f"<span style='font-size:.78rem;color:#51656a'><strong style='color:#132327'>Submitted:</strong> {_e(sub_date)}</span>")
    if rel_date:
        prov_items.append(f"<span style='font-size:.78rem;color:#51656a'><strong style='color:#132327'>Released:</strong> {_e(rel_date)}</span>")
    if acc_date:
        prov_items.append(f"<span style='font-size:.78rem;color:#51656a'><strong style='color:#132327'>Accessed:</strong> {_e(acc_date)}</span>")
    if hash_short:
        prov_items.append(
            f"<span title='{_e(content_hash)}' style='font-size:.78rem;color:#51656a;cursor:help'>"
            f"<strong style='color:#132327'>Hash:</strong> "
            f"<code style='font-size:.76rem;background:rgba(19,35,39,.06);padding:1px 5px;border-radius:4px'>{_e(hash_short)}…</code>"
            f"</span>"
        )
    prov_html = ""
    if prov_items:
        prov_html = (
            f"<div style='display:flex;flex-wrap:wrap;gap:14px;padding:10px 0 4px;margin-top:4px;border-top:1px solid rgba(19,35,39,.06)'>"
            + "".join(prov_items)
            + "</div>"
        )

    study_id_text = str(summary.get("study_id", "") or "").strip().upper()
    study_link_html = ""
    if study_id_text.startswith("ST") and len(study_id_text) == 8 and study_id_text[2:].isdigit():
        wb_url = (
            "https://www.metabolomicsworkbench.org/data/DRCCMetadata.php?"
            f"Mode=Study&StudyID={study_id_text}"
        )
        study_link_html = (
            f"<div style='padding:8px 0 10px;border-bottom:1px solid rgba(19,35,39,.06)'>"
            f"<a class='study-link-chip' href='{_e(wb_url)}' target='_blank' rel='noopener noreferrer'>"
            f"Open Study in Metabolomics Workbench"
            f"<span class='study-link-chip__meta'>DRCCMetadata.php?Mode=Study&StudyID={_e(study_id_text)}</span>"
            f"</a>"
            f"</div>"
        )

    return (
        f"<section style='margin-bottom:22px'>"
        f"{disease_flag}"
        f"<div style='background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.1);border-radius:20px;padding:20px'>"
        f"{_design_summary_sentence(summary)}"
        f"{row('Study ID', summary.get('study_id', ''), 'study_id')}"
        f"{study_link_html}"
        f"{row('Repository', _repository_display_label(summary.get('source')), 'repository')}"
        f"{row('Title', summary.get('title', ''), 'title')}"
        f"{row('Disease / Condition', summary.get('disease', ''), 'disease')}"
        f"{row('Organism', summary.get('organism', ''), 'organism')}"
        f"{row('Analysis Type', _analysis_type_text(summary), 'analysis_type')}"
        f"{row('Ion Mode', summary.get('polarity_label', ''), 'ion_mode')}"
        f"{''.join([pills_row('Polarities (per assay)', summary.get('polarities', []), 'polarities')] if len(set(str(p).upper() for p in (summary.get('polarities') or []) if p)) > 1 else [])}"
        f"{pills_row('Sample Matrices', summary.get('tissues', []), 'sample_matrices')}"
        f"{pills_row('Factor Variables', summary.get('factor_variables', []), 'factor_variables')}"
        f"{row('Factor Examples', ' ; '.join(summary.get('factor_examples', [])), 'factor_examples')}"
        f"{ann_row}"
        f"{class_row}"
        f"{stats}"
        f"{prov_html}"
        f"</div>"
        f"</section>"
    )


# ---------------------------------------------------------------------------
# Per-analysis summary table
# ---------------------------------------------------------------------------

def _nmr_analysis_table(per_analysis: list[Any]) -> str:
    if not per_analysis:
        return ""

    nmr_rows: list[dict[str, Any]] = []
    for row in per_analysis:
        analysis_type = str(row.get("analysis_type", "") or row.get("platform", "") or "").upper()
        data_block = str(row.get("nmr_data_block", "") or "").upper()
        if "NMR" in analysis_type or data_block.startswith("NMR_") or str(row.get("nmr_experiment_type", "")).strip():
            nmr_rows.append(row)
    if not nmr_rows:
        return ""

    def _text(value: Any) -> str:
        text = str(value or "").strip()
        return _e(text) if text else "—"

    _th = "padding:8px;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:#51656a;border-bottom:2px solid rgba(19,35,39,.1);white-space:nowrap"
    _td = "padding:9px 8px;border-bottom:1px solid rgba(19,35,39,.06);font-size:.84rem;vertical-align:top"
    rows_html = ""
    for i, row in enumerate(nmr_rows):
        row_bg = "rgba(245,241,232,.4)" if i % 2 == 0 else "transparent"
        rows_html += (
            "<tr style='background:" + row_bg + "'>"
            + "<td style='" + _td + ";font-weight:600;white-space:nowrap'>" + _text(_analysis_id_label(row.get("analysis_id", ""))) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_data_block", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_experiment_type", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_instrument_type", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_spectrometer_frequency", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_solvent", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_pulse_sequence", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_water_suppression", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_reference_compound", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("nmr_temperature", "")) + "</td>"
            + "<td style='" + _td + "'>" + _text(row.get("units", "")) + "</td>"
            + "</tr>"
        )

    header_html = (
        "<th style='" + _th + ";text-align:left'>Analysis ID</th>"
        + "<th style='" + _th + ";text-align:left'>NMR Data Block</th>"
        + "<th style='" + _th + ";text-align:left'>Experiment Type</th>"
        + "<th style='" + _th + ";text-align:left'>Instrument</th>"
        + "<th style='" + _th + ";text-align:left'>Spectrometer</th>"
        + "<th style='" + _th + ";text-align:left'>Solvent</th>"
        + "<th style='" + _th + ";text-align:left'>Pulse Sequence</th>"
        + "<th style='" + _th + ";text-align:left'>Water Suppression</th>"
        + "<th style='" + _th + ";text-align:left'>Reference</th>"
        + "<th style='" + _th + ";text-align:left'>Temperature</th>"
        + "<th style='" + _th + ";text-align:left'>Units</th>"
    )
    return (
        "<div style='margin-bottom:22px'>"
        "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>NMR Acquisition Details</h4>"
        "<p style='margin:0 0 10px;color:#51656a;font-size:.82rem'>Shown only for NMR analyses. Ion mode and chromatography are often not applicable for NMR workflows.</p>"
        "<div style='overflow-x:auto'>"
        "<table style='width:100%;border-collapse:collapse;font-size:.88rem'>"
        "<thead><tr style='background:rgba(13,110,110,.06)'>" + header_html + "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table></div></div>"
    )


def _per_analysis_table(
    per_analysis: list[Any],
    study_id: Any | None = None,
    source_key: str | None = None,
) -> str:
    if not per_analysis:
        return ""

    def _miss_bar(rate: float | None) -> str:
        if rate is None:
            return "<span style='color:#999;font-size:.8rem'>—</span>"
        rate = _safe_missing_rate(rate)
        pct = int(rate * 100)
        color = "#196b4a" if rate < 0.1 else ("#995b00" if rate < 0.3 else "#8f2d2d")
        return (
            f"<div style='display:flex;align-items:center;gap:6px'>"
            f"<div style='width:60px;height:5px;border-radius:3px;background:#e2e8e9;overflow:hidden'>"
            f"<div style='width:{pct}%;height:100%;background:{color};border-radius:3px'></div></div>"
            f"<span style='font-size:.8rem;color:{color};font-weight:700'>{rate:.1%}</span>"
            f"</div>"
        )

    def _analysis_mode_text(row: dict[str, Any]) -> str:
        analysis_type = str(row.get("analysis_type") or row.get("platform") or "").strip()
        ms_type = str(row.get("ms_type") or "").strip()
        if analysis_type and ms_type:
            if ms_type.lower() in analysis_type.lower():
                return analysis_type
            return f"{analysis_type} / {ms_type}"
        return analysis_type or ms_type or "—"

    def _analysis_verify_links(row: dict[str, Any]) -> str:
        aid = _analysis_id_label(row.get("analysis_id", ""))
        if not aid:
            return ""
        metadata_url = _workbench_analysis_rest_url(aid, "mwtab/txt")
        matrix_endpoint = _source_matrix_rest_endpoint(source_key)
        matrix_url = _workbench_analysis_rest_url(aid, matrix_endpoint)
        metadata_label = f"/rest/study/analysis_id/{aid}/mwtab/txt"
        matrix_label = f"/rest/study/analysis_id/{aid}/{_source_matrix_rest_label(source_key)}"
        if matrix_url == metadata_url:
            return _verify_rest_chip(
                metadata_url,
                label="mwTab REST",
                endpoint_label=metadata_label,
                compact=True,
                title=(
                    "Verify analysis-level Metabolomics Workbench REST source for analytical metadata, "
                    f"declared units, and mwTab-derived matrix fields. Endpoint: {metadata_label}"
                ),
            )
        return (
            _verify_rest_chip(
                metadata_url,
                label="Metadata",
                endpoint_label=metadata_label,
                compact=True,
                title=(
                    "Verify analysis/MS type, ion mode, chromatography, instrument, and declared units "
                    f"in the Metabolomics Workbench mwTab REST endpoint: {metadata_label}"
                ),
            )
            + _verify_rest_chip(
                matrix_url,
                label="Matrix",
                endpoint_label=matrix_label,
                compact=True,
                title=(
                    "Verify the source-specific table that MERIT-ML parsed for samples, features, "
                    f"and missingness. Endpoint: {matrix_label}"
                ),
            )
        )

    rows_html = ""
    _td = "padding:10px 9px;border-bottom:1px solid rgba(19,35,39,.06);font-size:.82rem;line-height:1.35;vertical-align:top"
    _no_data = "<span style='color:#999'>&#8212;</span>"
    for i, a in enumerate(per_analysis):
        n_features_val = a.get("n_features", "")
        actual = str(n_features_val) if n_features_val != "" else ""
        analysis_type = str(a.get("analysis_type", "") or a.get("platform", "") or "").upper()
        is_nmr = "NMR" in analysis_type or str(a.get("nmr_experiment_type", "")).strip() != ""
        row_bg = "rgba(245,241,232,.4)" if i % 2 == 0 else "transparent"
        polarity_html = (
            _pill(_e(a.get("polarity", "")), "#113e52")
            if a.get("polarity")
            else (_pill("N/A (NMR)", "#51656a") if is_nmr else _no_data)
        )
        units_html = (
            _pill(_e(a.get("units", "")), "#0d6e6e")
            if a.get("units")
            else (
                _pill("N/A", "#51656a")
                if is_nmr
                else f"{_no_data}{_verify_workbench_button(study_id, compact=True)}"
            )
        )
        chromatography_text = a.get("chromatography", "") or ("N/A (NMR)" if is_nmr else "—")
        chromatography_system = a.get("chromatography_system", "") or ("N/A (NMR)" if is_nmr else "—")
        chromatography_column = a.get("chromatography_column", "") or ("N/A (NMR)" if is_nmr else "—")
        ms_instrument_type = (
            a.get("ms_instrument_type", "")
            or (a.get("nmr_instrument_type", "") if is_nmr else "")
            or ("N/A (NMR)" if is_nmr else "—")
        )
        ms_instrument_name = (
            a.get("ms_instrument_name", "")
            or (a.get("nmr_instrument_type", "") if is_nmr else "")
            or ("N/A (NMR)" if is_nmr else "—")
        )
        analysis_mode = _analysis_mode_text(a)
        rows_html += (
            "<tr style='background:" + row_bg + "'>"
            + "<td style='" + _td + ";font-weight:600;white-space:nowrap'>" + _e(_analysis_id_label(a.get("analysis_id", ""))) + "</td>"
            + "<td style='" + _td + ";max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' title='" + _e(analysis_mode) + "'>" + _e(analysis_mode) + "</td>"
            + "<td style='" + _td + "'>" + polarity_html + "</td>"
            + "<td style='" + _td + "'>" + _e(chromatography_text) + "</td>"
            + "<td style='" + _td + "'>" + _e(chromatography_system) + "</td>"
            + "<td style='" + _td + "'>" + _e(chromatography_column) + "</td>"
            + "<td style='" + _td + "'>" + _e(str(ms_instrument_type)) + "</td>"
            + "<td style='" + _td + "'>" + _e(str(ms_instrument_name)) + "</td>"
            + "<td style='" + _td + "'>" + units_html + "</td>"
            + "<td style='" + _td + ";text-align:right'>" + _e(str(a.get("n_samples", "—"))) + "</td>"
            + "<td style='" + _td + ";text-align:right'>" + (actual or "—") + "</td>"
            + "<td style='" + _td + "'>" + _miss_bar(a.get("missing_rate", 0.0)) + "</td>"
            + "<td style='" + _td + ";white-space:nowrap'>" + _analysis_verify_links(a) + "</td>"
            + "</tr>"
        )

    _th = (
        "padding:9px 9px;font-size:.71rem;text-transform:uppercase;letter-spacing:.08em;color:#3f565b;"
        "border-bottom:2px solid rgba(19,35,39,.12);white-space:nowrap;background:rgba(13,110,110,.07)"
    )
    def _hdr(label: str, tip: str = "", align: str = "left") -> str:
        icon = (
            f"<span class='minfo' tabindex='0'><span class='minfo-icon'>i</span>"
            f"<div class='minfo-popup' style='text-align:left;white-space:normal'>{_e(tip)}</div></span>"
        ) if tip else ""
        return f"<th style='{_th};text-align:{align}'>{label} {icon}</th>"

    headers_html = (
        _hdr("Analysis ID", "Unique analysis accession (e.g. AN000001). Use this row's Metabolomics Workbench REST link to verify the exact analysis endpoint.")
        + _hdr("Analysis / MS Type", "Analytical platform as declared in Metabolomics Workbench metadata. Verify via the row Metadata link: /rest/study/analysis_id/<AN>/mwtab/txt.")
        + _hdr("Ion Mode", "Electrospray ion polarity parsed from analysis metadata. Verify via the row Metadata link: /rest/study/analysis_id/<AN>/mwtab/txt.")
        + _hdr("Chromatography Type", "Separation technique parsed from analysis metadata. Verify via the row Metadata link: /rest/study/analysis_id/<AN>/mwtab/txt.")
        + _hdr("Chromatography System", "LC/GC system or instrument metadata. Verify via the row Metadata link: /rest/study/analysis_id/<AN>/mwtab/txt.")
        + _hdr("Column", "Analytical column metadata. Verify via the row Metadata link: /rest/study/analysis_id/<AN>/mwtab/txt.")
        + _hdr("MS Instrument Type", "Mass spectrometer type parsed from analysis metadata. Verify via the row Metadata link: /rest/study/analysis_id/<AN>/mwtab/txt.")
        + _hdr("MS Instrument Name", "Specific instrument model name parsed from analysis metadata. Verify via the row Metadata link: /rest/study/analysis_id/<AN>/mwtab/txt.")
        + _hdr("Units", "Abundance unit declared in source metadata/table. Verify via the row Metadata link, and where needed the row Matrix link.")
        + _hdr("Samples", "Total sample rows parsed from this source matrix. Verify via the row Matrix link to the active source-specific REST endpoint.", "right")
        + _hdr("Features", "Total number of features parsed from this source matrix, excluding sample-ID, class, and metadata columns. Verify via the row Matrix link.", "right")
        + _hdr("Missingness", "Fraction of abundance cells MERIT-ML parsed as missing in this source matrix. Verify the underlying source table via the row Matrix link.")
        + _hdr("Verify source", "Metadata opens /rest/study/analysis_id/<AN>/mwtab/txt. Matrix opens the active source-specific endpoint used for samples, features, and missingness.")
    )
    return (
        "<div style='margin-bottom:22px'>"
        "<h4 style='margin:0 0 10px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>Per-Analysis Data Summary</h4>"
        "<div style='overflow-x:auto;border:1px solid rgba(19,35,39,.1);border-radius:14px;background:rgba(255,255,255,.92)'>"
        "<table style='width:100%;min-width:1940px;border-collapse:collapse;font-size:.86rem'>"
        "<thead><tr>" + headers_html + "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table></div></div>"
    )


# ---------------------------------------------------------------------------
# RefMet chemical class distribution chart
# ---------------------------------------------------------------------------

def _refmet_class_charts(
    per_analysis: list[Any],
    chart_suffix: str = "",
    study_id: Any | None = None,
) -> str:
    """Render a RefMet super-class distribution dropdown + Plotly donut pie per analysis."""
    analyses_with_data = [
        a for a in per_analysis
        if a.get("class_distribution")
        and any(v > 0 for v in a["class_distribution"].values())
    ]
    if not analyses_with_data:
        return ""

    all_unclassified = True
    for a in analyses_with_data:
        for k, v in a.get("class_distribution", {}).items():
            if v > 0 and str(k).strip().lower() not in {"unclassified", "unknown", ""}:
                all_unclassified = False
                break
        if not all_unclassified:
            break

    chart_data = {
        _analysis_id_label(a["analysis_id"]): dict(sorted(a["class_distribution"].items(), key=lambda x: -x[1]))
        for a in analyses_with_data
    }
    # Scope IDs and function name so multiple source panels don't collide
    sfx = chart_suffix.lstrip("_") or "main"
    select_id = f"class-chart-select-{sfx}"
    chart_id = f"class-pie-chart-{sfx}"
    fn_name = f"renderClassPie_{sfx}"
    options_html = "".join(
        "<option value='" + _e(_analysis_id_label(a['analysis_id'])) + "'>" + _e(_analysis_id_label(a["analysis_id"])) + "</option>"
        for a in analyses_with_data
    )
    return (
        "<div style='margin-bottom:24px;padding:16px 20px;background:rgba(13,110,110,.04);"
        "border-radius:14px;border:1px solid rgba(13,110,110,.12)'>"
        "<h4 style='margin:0 0 12px;font-size:.88rem;text-transform:uppercase;"
        "letter-spacing:.06em;color:#132327'>RefMet Chemical Class Distribution</h4>"
        + (
            "<div style='font-size:.78rem;color:#995b00;margin:-4px 0 10px'>"
            "All detected entries are currently Unclassified. "
            "No explicit RefMet super_class values were found in the Metabolomics Workbench metabolite metadata for this study/analysis."
            f"{_verify_workbench_button(study_id, compact=True)}"
            "</div>"
            if all_unclassified else ""
        )
        + "<div style='display:flex;align-items:center;gap:10px;margin-bottom:14px;min-width:0'>"
        "<label style='font-size:.85rem;color:#51656a;font-weight:600;white-space:nowrap;flex:0 0 auto'>Analysis:</label>"
        f"<select id='{select_id}' onchange='{fn_name}()' "
        "style='padding:5px 10px;border-radius:8px;border:1px solid rgba(19,35,39,.2);"
        "font-size:.85rem;background:white;min-width:0;width:100%'>"
        + options_html
        + "</select>"
        "</div>"
        f"<div id='{chart_id}' style='min-height:380px'></div>"
        "<script>"
        f"var _classData_{sfx} = " + json.dumps(chart_data) + ";"
        f"function {fn_name}() {{"
        f"  var sel = document.getElementById('{select_id}');"
        f"  var chart = document.getElementById('{chart_id}');"
        "  if (!sel || !chart) return;"
        "  function esc(v) { return String(v).replace(/[&<>\"']/g, function(ch) { return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[ch]; }); }"
        f"  var dist = _classData_{sfx}[sel.value] || {{}};"
        "  var entries = Object.keys(dist).map(function(label) { return [label, Number(dist[label]) || 0]; }).filter(function(x) { return x[1] > 0; });"
        "  var total = entries.reduce(function(acc, x) { return acc + x[1]; }, 0);"
        "  if (!total) { chart.innerHTML = \"<div style='padding:18px;border:1px dashed rgba(19,35,39,.16);border-radius:14px;color:#51656a;background:rgba(255,255,255,.72)'>No RefMet class counts available for this analysis.</div>\"; return; }"
        "  var colors = ['#0d6e6e','#d27d2d','#113e52','#8f2d2d','#6a994e','#c9a227','#7b5ea7','#2f7f9f','#9c6644','#5f6f52','#bc4749','#577590'];"
        "  var r = 82, c = 2 * Math.PI * r, offset = 0;"
        "  var circles = entries.map(function(x, i) {"
        "    var frac = x[1] / total;"
        "    var dash = Math.max(0.0001, frac * c);"
        "    var circle = \"<circle cx='125' cy='125' r='\" + r + \"' fill='transparent' stroke='\" + colors[i % colors.length] + \"' stroke-width='48' stroke-dasharray='\" + dash + \" \" + Math.max(0, c - dash) + \"' stroke-dashoffset='\" + (-offset) + \"' transform='rotate(-90 125 125)'/>\";"
        "    offset += dash;"
        "    return circle;"
        "  }).join('');"
        "  var legend = entries.map(function(x, i) {"
        "    var pct = ((x[1] / total) * 100).toFixed(1);"
        "    return \"<div style='display:grid;grid-template-columns:13px minmax(0,1fr) auto;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid rgba(19,35,39,.06)'>\" +"
        "      \"<span style='width:13px;height:13px;border-radius:4px;background:\" + colors[i % colors.length] + \"'></span>\" +"
        "      \"<span style='min-width:0;overflow-wrap:anywhere;color:#132327;font-size:.86rem'>\" + esc(x[0]) + \"</span>\" +"
        "      \"<span style='font-variant-numeric:tabular-nums;color:#51656a;font-size:.82rem'>\" + x[1] + \" (\" + pct + \"%)</span></div>\";"
        "  }).join('');"
        "  chart.innerHTML = \"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:18px;align-items:center'>\" +"
        "    \"<svg viewBox='0 0 250 250' role='img' aria-label='RefMet chemical class distribution' style='width:min(100%,330px);height:auto;display:block;margin:auto'>\" +"
        "    \"<circle cx='125' cy='125' r='106' fill='rgba(13,110,110,.04)'/>\" + circles +"
        "    \"<circle cx='125' cy='125' r='56' fill='rgba(255,255,255,.96)' stroke='rgba(19,35,39,.08)'/>\" +"
        "    \"<text x='125' y='121' text-anchor='middle' style='font-size:24px;font-weight:800;fill:#132327'>\" + total + \"</text>\" +"
        "    \"<text x='125' y='142' text-anchor='middle' style='font-size:11px;fill:#51656a;text-transform:uppercase;letter-spacing:.08em'>features</text>\" +"
        "    \"</svg><div style='max-height:330px;overflow:auto;padding-right:4px'>\" + legend + \"</div></div>\";"
        "}"
        "if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', function() { setTimeout(" + fn_name + ", 0); }); }"
        "else { setTimeout(" + fn_name + ", 0); }"
        "</script>"
        "</div>"
    )


def _class_separability_panel(metrics: list[Any], chart_suffix: str = "") -> str:
    """Render class separability summary + analysis-selectable PCA class-overlay scatter."""
    metric = next((m for m in metrics if getattr(m, "name", "") == "class_separability"), None)
    if metric is None:
        return ""
    details = metric.details or {}
    per_analysis = details.get("per_analysis")
    if not isinstance(per_analysis, list):
        per_analysis = []

    valid = []
    for item in per_analysis:
        if not isinstance(item, dict):
            continue
        pca = item.get("pca_projection")
        if not isinstance(pca, dict):
            continue
        labels = pca.get("labels", [])
        pc1 = pca.get("pc1", [])
        pc2 = pca.get("pc2", [])
        if (
            isinstance(labels, list)
            and isinstance(pc1, list)
            and isinstance(pc2, list)
            and len(labels) >= 2
            and len(pc1) == len(pc2) == len(labels)
            and len(set(labels)) >= 2
        ):
            valid.append(item)

    max_features = details.get("max_features_evaluated")
    imputation = str(details.get("imputation", "") or "").strip() or "median_per_feature"
    scaling = str(details.get("scaling", "") or "").strip() or "zscore_per_feature"
    cv_repeats = details.get("cv_repeats")
    cv_test_size = details.get("cv_test_size")

    method_bits = [
        "Evaluates how well class labels can be separated using logistic regression under repeated stratified cross-validation.",
        "Uses cleaned numeric abundance features after imputation/scaling; includes labeled ML-eligible samples only.",
    ]
    if max_features is not None:
        method_bits.append(
            f"Feature handling: uses all cleaned features up to top-variance cap = {max_features} features per analysis."
        )
    method_bits.append(
        f"Preprocessing: imputation = {imputation}; scaling = {scaling}."
    )
    if cv_repeats is not None and cv_test_size is not None:
        method_bits.append(
            f"Per-analysis score: 100 × mean CV AUROC across {cv_repeats} stratified repeats at test_size={cv_test_size}."
        )
    method_bits.append(
        "PCA uses the same processed matrix; for very large cohorts only the visualization points may be downsampled."
    )

    n_total = details.get("n_analyses_total")
    n_eligible = details.get("n_analyses_eligible")
    coverage = details.get("eligible_coverage")
    mean_auc = details.get("mean_cv_auroc_eligible")
    median_auc = details.get("median_cv_auroc_eligible")
    iqr_auc = details.get("iqr_cv_auroc_eligible")
    ci95 = details.get("ci95_cv_auroc_eligible")
    if isinstance(ci95, list) and len(ci95) == 2:
        ci_low, ci_high = ci95[0], ci95[1]
    else:
        ci_low, ci_high = None, None

    coverage_html = ""
    if n_total is not None and n_eligible is not None:
        coverage_html = (
            "<div style='font-size:.8rem;color:#132327;margin:0 0 10px;padding:8px 10px;border-radius:10px;"
            "background:rgba(255,255,255,.7);border:1px solid rgba(19,35,39,.1)'>"
            f"Eligible analyses for AUROC: <strong>{_e(str(n_eligible))}/{_e(str(n_total))}</strong>"
            + (
                f" (<strong>{_e(_fmt_pct(coverage))}</strong>)"
                if coverage is not None
                else ""
            )
            + (
                f" · Mean AUROC: <strong>{_e(_fmt_num(mean_auc, 3))}</strong>"
                if mean_auc is not None
                else ""
            )
            + (
                f" · Median AUROC: <strong>{_e(_fmt_num(median_auc, 3))}</strong>"
                if median_auc is not None
                else ""
            )
            + (
                f" · IQR: <strong>{_e(_fmt_num(iqr_auc, 3))}</strong>"
                if iqr_auc is not None
                else ""
            )
            + (
                f" · 95% CI: <strong>[{_e(_fmt_num(ci_low, 3))}, {_e(_fmt_num(ci_high, 3))}]</strong>"
                if ci_low is not None and ci_high is not None
                else ""
            )
            + "</div>"
        )

    method_html = (
        "<div style='font-size:.8rem;color:#51656a;margin-bottom:10px;line-height:1.45'>"
        + " ".join(_e(bit) for bit in method_bits)
        + "</div>"
    )

    summary_rows: list[str] = []
    for item in per_analysis:
        if not isinstance(item, dict):
            continue
        aid = _analysis_id_label(item.get("analysis_id", "") or "—")
        eligible_for_auroc = bool(item.get("eligible_for_auroc", False))
        score = _score_100_text(item.get("score"), 1) if eligible_for_auroc else "N/A"
        cv_auroc = _fmt_num(item.get("cv_linear_auroc_mean"), 3)
        cv_auroc_std = _fmt_num(item.get("cv_linear_auroc_std"), 3)
        cv_cell = f"{_e(cv_auroc)} ± {_e(cv_auroc_std)}" if eligible_for_auroc else "N/A"
        pca = item.get("pca_projection") if isinstance(item.get("pca_projection"), dict) else {}
        n_points = pca.get("n_points", "—")
        n_total = pca.get("n_total", "—")
        downsampled = bool(pca.get("downsampled", False))
        points_text = f"{n_points}/{n_total}" if (eligible_for_auroc and n_points != "—" and n_total != "—") else "—"
        points_html = f"{_e(points_text)}{' (downsampled)' if downsampled else ''}"
        n_samples = str(item.get("n_samples_labeled", "—"))
        n_classes = str(item.get("n_classes", "—"))
        n_features = str(item.get("n_features_used", "—")) if eligible_for_auroc else "—"
        reason = str(item.get("skipped_reason", "") or "").strip()
        reason_html = f"<div style='color:#995b00;font-size:.76rem;margin-top:3px'>{_e(reason)}</div>" if reason else ""
        summary_rows.append(
            "<tr>"
            f"<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>{_e(aid)}{reason_html}</td>"
            f"<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>{cv_cell}</td>"
            f"<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>{_e(score)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>{points_html}</td>"
            f"<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>{_e(n_samples)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>{_e(n_classes)}</td>"
            f"<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>{_e(n_features)}</td>"
            "</tr>"
        )
    header_specs = [
        (
            "Analysis ID",
            "Metabolomics Workbench analysis accession (ANxxxxxx).",
        ),
        (
            "CV linear AUROC",
            "Repeated stratified holdout AUROC from logistic regression; binary AUROC or macro one-vs-rest AUROC for multiclass. Ineligible analyses are shown as N/A.",
        ),
        (
            "Score (0-100)",
            "Per-analysis class separability score = 100 × mean CV AUROC across repeats for eligible analyses. Ineligible analyses are shown as N/A and excluded from study-level aggregation.",
        ),
        (
            "PCA points",
            "Displayed PCA sample count / total eligible labeled samples. Downsample note affects visualization only.",
        ),
        (
            "Labeled samples",
            "Number of ML-eligible samples with usable class labels used for this analysis.",
        ),
        (
            "Classes",
            "Number of class groups retained after minimum per-class sample filtering.",
        ),
        (
            "Features used",
            "Numeric feature count after cleanup, imputation, constant-feature removal, scaling, and variance-cap filtering.",
        ),
    ]
    th_base = (
        "padding:7px 8px;text-align:left;font-size:.7rem;text-transform:uppercase;"
        "letter-spacing:.06em;color:#51656a;border-bottom:2px solid rgba(19,35,39,.1);white-space:nowrap"
    )
    header_html = "".join(
        f"<th style='{th_base}'>{_e(label)} {_mini_info_icon(help_text, size=12)}</th>"
        for label, help_text in header_specs
    )

    table_html = (
        "<div style='overflow-x:auto;margin-top:10px'>"
        "<table style='width:100%;border-collapse:collapse;font-size:.84rem'>"
        "<thead><tr style='background:rgba(13,110,110,.06)'>"
        + header_html +
        "</tr></thead>"
        f"<tbody>{''.join(summary_rows)}</tbody>"
        "</table>"
        "</div>"
    ) if summary_rows else ""

    if not valid:
        return (
            "<div style='margin-bottom:16px;padding:14px 16px;background:rgba(13,110,110,.04);"
            "border-radius:14px;border:1px solid rgba(13,110,110,.12)'>"
            "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em;color:#132327'>Class Separability</h4>"
            f"<div style='font-size:.85rem;color:#51656a'>{_e(metric.summary)}</div>"
            + method_html
            + coverage_html
            + table_html
            + "</div>"
        )

    chart_data: dict[str, dict[str, Any]] = {}
    for item in valid:
        aid = _analysis_id_label(item.get("analysis_id", "") or "—")
        pca = item.get("pca_projection", {}) if isinstance(item.get("pca_projection"), dict) else {}
        chart_data[aid] = {
            "score": float(item.get("score", 0.0) or 0.0),
            "cv_linear_auroc_mean": item.get("cv_linear_auroc_mean"),
            "cv_linear_auroc_std": item.get("cv_linear_auroc_std"),
            "pca": {
                "labels": pca.get("labels", []),
                "pc1": pca.get("pc1", []),
                "pc2": pca.get("pc2", []),
                "sample_ids": pca.get("sample_ids", []),
                "explained_variance_ratio": pca.get("explained_variance_ratio", [0.0, 0.0]),
                "n_points": pca.get("n_points", 0),
                "n_total": pca.get("n_total", 0),
                "downsampled": bool(pca.get("downsampled", False)),
            },
            "n_samples_labeled": int(item.get("n_samples_labeled", 0) or 0),
            "n_classes": int(item.get("n_classes", 0) or 0),
            "n_features_used": int(item.get("n_features_used", 0) or 0),
        }

    # Scope IDs and function name so multiple source panels don't collide
    _sfx = chart_suffix.lstrip("_") or "main"
    _select_id = f"separability-select-{_sfx}"
    _meta_id = f"separability-meta-{_sfx}"
    _pca_id = f"separability-pca-{_sfx}"
    _data_var = f"_separabilityData_{_sfx}"
    _fn_name = f"renderSeparabilityPCA_{_sfx}"

    options_html = "".join(
        f"<option value='{_e(aid)}'>{_e(aid)}</option>"
        for aid in chart_data.keys()
    )
    return (
        "<div style='margin-bottom:24px;padding:16px 20px;background:rgba(13,110,110,.04);"
        "border-radius:14px;border:1px solid rgba(13,110,110,.12)'>"
        "<h4 style='margin:0 0 6px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em;color:#132327'>Class Separability PCA</h4>"
        "<div style='font-size:.8rem;color:#51656a;margin-bottom:10px'>"
        "Evaluates separability using mean CV linear AUROC from logistic regression across repeated stratified splits (eligible analyses only). "
        "Plot shows PCA (PC1 vs PC2) with class overlays and class centroids."
        "</div>"
        + method_html
        + coverage_html
        +
        "<div style='display:flex;align-items:center;gap:10px;margin-bottom:10px;min-width:0'>"
        "<label style='font-size:.85rem;color:#51656a;font-weight:600;white-space:nowrap;flex:0 0 auto'>Analysis:</label>"
        f"<select id='{_select_id}' onchange='{_fn_name}()' "
        "style='padding:5px 10px;border-radius:8px;border:1px solid rgba(19,35,39,.2);font-size:.85rem;background:white;min-width:0;width:100%'>"
        + options_html
        + "</select>"
        "</div>"
        f"<div id='{_meta_id}' style='font-size:.8rem;color:#132327;margin-bottom:8px'></div>"
        f"<div id='{_pca_id}' style='height:440px'></div>"
        + table_html
        + "<script>"
        f"var {_data_var} = " + json.dumps(chart_data) + ";"
        f"var _separabilityRetry_{_sfx} = 0;"
        f"function {_fn_name}() {{"
        f"  var sel = document.getElementById('{_select_id}');"
        "  if (!sel) return;"
        "  if (!window.Plotly) {"
        f"    if (_separabilityRetry_{_sfx} < 20) {{"
        f"      _separabilityRetry_{_sfx} += 1;"
        f"      setTimeout({_fn_name}, 100);"
        "    }"
        "    return;"
        "  }"
        f"  _separabilityRetry_{_sfx} = 0;"
        f"  var rec = {_data_var}[sel.value];"
        "  if (!rec) return;"
        "  var pca = rec.pca || {};"
        "  var labels = pca.labels || [];"
        "  var xs = pca.pc1 || [];"
        "  var ys = pca.pc2 || [];"
        "  var ids = pca.sample_ids || [];"
        "  var evr = pca.explained_variance_ratio || [0,0];"
        "  var cvMean = rec.cv_linear_auroc_mean;"
        "  var cvStd = rec.cv_linear_auroc_std;"
        "  var cvText = (cvMean === null || cvMean === undefined) ? 'n/a' : Number(cvMean).toFixed(3) + ' ± ' + Number(cvStd || 0).toFixed(3);"
        "  var p1 = ((Number(evr[0] || 0) * 100)).toFixed(1);"
        "  var p2 = ((Number(evr[1] || 0) * 100)).toFixed(1);"
        f"  var meta = document.getElementById('{_meta_id}');"
        "  if (meta) {"
        "    var pointsTxt = String(pca.n_points || 0) + '/' + String(pca.n_total || 0);"
        "    if (pca.downsampled) pointsTxt += ' (downsampled)';"
        "    meta.innerHTML = 'Score (0-100; mean CV AUROC, eligible-only): <strong>' + (Number(rec.score||0) * 100).toFixed(1)"
        "      + '</strong> · CV linear AUROC: <strong>' + cvText"
        "      + '</strong> · PCA points: <strong>' + pointsTxt"
        "      + '</strong> · Labeled samples: <strong>' + rec.n_samples_labeled + '</strong> · Classes: <strong>' + rec.n_classes"
        "      + '</strong> · Features used: <strong>' + rec.n_features_used + '</strong>';"
        "  }"
        "  if (!labels.length || labels.length !== xs.length || labels.length !== ys.length) {"
        f"    Plotly.purge('{_pca_id}');"
        "    return;"
        "  }"
        "  var palette = ['#1f77b4','#d62728','#2ca02c','#9467bd','#ff7f0e','#17becf','#e377c2','#8c564b','#bcbd22','#7f7f7f','#1b9e77','#e41a1c','#377eb8','#4daf4a','#984ea3','#a65628','#f781bf','#999999'];"
        "  var symbols = ['circle','square','diamond','triangle-up','triangle-down','cross','x','triangle-left','triangle-right'];"
        "  var classes = [];"
        "  var seen = {};"
        "  for (var i=0;i<labels.length;i++){ var c = String(labels[i]); if(!seen[c]){ seen[c]=true; classes.push(c);} }"
        "  var traces = [];"
        "  for (var ci=0; ci<classes.length; ci++) {"
        "    var cls = classes[ci];"
        "    var tx = [], ty = [], td = [];"
        "    for (var j=0; j<labels.length; j++) {"
        "      if (String(labels[j]) === cls) {"
        "        tx.push(Number(xs[j])); ty.push(Number(ys[j])); td.push(String(ids[j] || ('sample_' + (j+1))));"
        "      }"
        "    }"
        "    var col = palette[ci % palette.length];"
        "    traces.push({"
        "      type: 'scattergl', mode: 'markers', name: cls, x: tx, y: ty, customdata: td,"
        "      marker: {size: 9, opacity: 0.9, color: col, symbol: symbols[ci % symbols.length], line: {width: 0.8, color: '#132327'}},"
        "      hovertemplate: '<b>%{customdata}</b><br>Class: %{fullData.name}<br>PC1: %{x:.3f}<br>PC2: %{y:.3f}<extra></extra>'"
        "    });"
        "    if (tx.length) {"
        "      var sx = 0, sy = 0;"
        "      for (var k=0; k<tx.length; k++) { sx += tx[k]; sy += ty[k]; }"
        "      var cx = sx / tx.length; var cy = sy / ty.length;"
        "      traces.push({"
        "        type: 'scatter', mode: 'markers+text', showlegend: false,"
        "        x: [cx], y: [cy], text: [cls], textposition: 'top center', textfont: {size: 11, color: '#132327'},"
        "        marker: {symbol: 'diamond', size: 12, color: col, line: {width: 1.4, color: '#132327'}},"
        "        hovertemplate: 'Centroid: %{text}<br>PC1: %{x:.3f}<br>PC2: %{y:.3f}<extra></extra>'"
        "      });"
        "    }"
        "  }"
        f"  Plotly.newPlot('{_pca_id}', traces, {{"
        "    paper_bgcolor: 'transparent',"
        "    plot_bgcolor: 'rgba(255,255,255,0.55)',"
        "    margin: {t: 24, b: 56, l: 64, r: 24},"
        "    legend: {orientation:'h', y:1.14, x:0, bgcolor:'rgba(255,255,255,0.75)', bordercolor:'rgba(19,35,39,.12)', borderwidth:1},"
        "    xaxis: {title: 'PC1 (' + p1 + '%)', zeroline: true, zerolinecolor:'rgba(19,35,39,.14)', gridcolor:'rgba(19,35,39,.08)'},"
        "    yaxis: {title: 'PC2 (' + p2 + '%)', zeroline: true, zerolinecolor:'rgba(19,35,39,.14)', gridcolor:'rgba(19,35,39,.08)'}"
        "  }, { responsive: true, displayModeBar: false });"
        "}"
        "// Render on demand after Plotly is available."
        "</script>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Metric descriptors and info tooltips
# ---------------------------------------------------------------------------

_METRIC_DESCRIPTORS: dict[str, str] = {
    # Structural
    "schema_integrity": "Checks that the five required top-level study components are present and non-empty.",
    "tabular_data_availability": "Verifies that each assay matrix contains actual sample IDs, feature IDs, and abundance values. At least one populated matrix is required for ML.",
    "required_field_completeness": "Tallies six study-level descriptors (Title, Description, Organism, Disease, Analysis Type, Platform) plus per-sample Label, Sample Type, and Organism.",
    "duplicate_entities": "Detects repeated Sample IDs across the sample list and repeated Feature IDs within each assay matrix. Duplicates inflate counts and introduce train/test leakage.",
    "minimum_sample_count": "Counts ML-eligible samples after excluding QC pools, blanks, NIST references, and other non-biological rows. Minimum threshold: 20.",
    # Metadata / FAIR
    "fair_study_metadata_compliance": (
        "Displayed score = 100 × passed / 7 binary checks drawn from the study's mwtab metadata. "
        "Inspects: DOI (F1 — persistent identifier), linked publication (R1.2 — methodology provenance), "
        "funding source and contributors (R1 — attribution), study type (R1 — reuse context), "
        "substantive description ≥20 words (F2 — not just the title), "
        "and raw data format in the ANALYSIS block (A1.1 — raw files documented)."
    ),
    "fair_metabolite_identifier_resolvability": "Displayed score = 100 × metabolites with a RefMet match / total named metabolites, sourced from the study metabolites endpoint.",
    "mass_rt_like_metadata_presence": (
        "Binary reuse signal: 1 if populated mass-, m/z-, retention-time-, or retention-index-like metabolite metadata "
        "fields are present in the mwTab Metabolites block; otherwise 0. This is deliberately labeled mass/RT-like "
        "because Metabolomics Workbench deposits use heterogeneous field names such as m/z, moverz, RI, RT, retention time, "
        "or exact mass. Repository-wide prevalence: 1,833/4,121 studies (44.49%)."
    ),
    # Backward compatibility for older report artifacts
    "fair_metadata_coverage": "Legacy FAIR coverage metric kept for older reports.",
    "factor_label_harmonizability": (
        "Displayed score = 100 × (0.5 × label_quality + 0.5 × simplicity). "
        "label_quality = fraction of ML-eligible samples with a valid non-unknown label. "
        "simplicity = step function on the average number of pipe-separated factor dimensions per sample: "
        "1 dimension → 100/100 (e.g. 'Group:NAFLD'), "
        "2 → 70/100, 3 → 40/100, ≥4 → 10/100. "
        "Studies with a single factor variable are directly ML-ready; "
        "compound factor strings (e.g. ST000010: FCS | Hours | REFED | TGF) require "
        "a target variable to be explicitly defined before model training."
    ),
    "disease_endpoint_extractability": (
        "Checks whether sample labels define a usable supervised endpoint "
        "(≥2 distinct groups with high label coverage). "
        "Study-level disease metadata is reported separately in Overview."
    ),
    "factor_variable_richness": "Counts distinct factor variable types (e.g. diagnosis, sex, treatment) from structured attributes and pipe-delimited factor strings.",
    # Analytical QC
    "qc_blank_presence": (
        "Detects QC/pool/reference/system-suitability samples separately from blank samples using keyword rules. "
        "QC samples support system suitability checks, drift tracking, and QC-based normalization workflows. "
        "Blank samples support background/contaminant assessment and blank subtraction. "
        "If QC is absent, QC-based normalization may not be feasible; if blanks are absent, blank subtraction may not be feasible."
    ),
    "missingness_structure": "Sample-level missingness (ML-eligible samples only; QC/blank/pool/reference excluded): per-analysis score = 100 × [1 − median(per-sample missingness rates)]; aggregate = mean of per-analysis scores. Class-dependent gap reported separately as a diagnostic warning.",
    "missingness": "Legacy overall missing-value metric kept for older report artifacts.",
    "scale_diagnostics": "Summarizes observed value-scale and distribution characteristics (e.g., raw-like vs compressed/likely_transformed) from min/median/p90/max over ML-eligible samples. Declared units (value scale) are reported separately from mwTab JSON and are not used directly in inference. P50/P90 is used as a skewness-like distribution-shape proxy: lower ratios indicate stronger right-skew (median far below upper-tail), while higher ratios indicate a more compressed/less-skewed distribution. Near-zero variance (NZV) and low-signal features are diagnostics only. This metric is informational and does not affect the ML readiness score.",
    "metabatch_batch_annotation_compatibility": (
        "Reports whether Metabolomics Workbench factor annotations can be converted into MetaBatch-style batch/covariate tables for the active matrix samples. "
        "A factor is considered usable if it has at least two non-empty levels, covers at least 60% of samples, and is not nearly one unique value per sample (>90% distinct). "
        "Technical-like keys are a conservative MERIT-ML add-on based on explicit batch/run/order/plate/injection/acquisition-like text in factor names or values; "
        "generic biological covariates are not treated as proven technical batch metadata. Informational only."
    ),
    "assay_platform_comparability": (
        "This metric compares analyses using the spread of their median log10 abundance values after excluding "
        "missing, non-finite, and non-positive entries. Here, spread is defined as the difference between the "
        "highest and lowest per-analysis log10 median value (spread = max(log10 median) − min(log10 median)). "
        "Score uses a smooth rule displayed as 100/(1 + spread). Larger spread yields lower score."
    ),
    "assay_comparability": "Legacy assay comparability metric label kept for older report artifacts.",
    "feature_correlation_burden": "Analysis-wise sampled pairwise Pearson redundancy burden (|r| >= 0.95). High burden indicates duplicated signal blocks.",
    "outlier_burden": (
        "Sample-level IQR outlier check. Each sample is summarized by its median intensity across features. "
        "Samples outside Tukey fences [Q1 - 1.5×IQR, Q3 + 1.5×IQR] are counted as outliers. "
        "Score = 100 × [1 - (sample_outliers / sample_total)]."
    ),
    # Backward compatibility for older report artifacts
    "feature_correlation": "Flags redundant features using sampled pairwise Pearson correlation (|r| >= 0.95).",
    "outlier_samples": "IQR-based outlier burden across feature values.",
    "feature_level_missingness": "Full profile only. Feature-level missingness = missing values per feature divided by sample count in each analysis. Features above 30% are flagged; score is 1 minus mean feature-level missingness.",
    # Annotation
    "identifier_coverage": "Fraction of annotated features carrying external identifiers or non-lexical reference mappings (e.g., RefMet-backed). Lexical name normalization alone is not counted.",
    "feature_annotation_type": "Classifies features into named metabolites, mz/RT-style tokens, NMR bins, and unknown/non-metabolite placeholders. Tiered scoring is displayed on a 0-100 scale and reflects annotation usability for interpretation and reuse.",
    "annotation_ambiguity_burden": "Fraction of features with ambiguity flags (multi-candidate names, unknown IDs, or other unresolved mapping flags). Lower burden improves interpretability and transfer.",
    "annotation_ambiguity": "Detects features with multi-candidate annotations (names containing '/' or ';'). Ambiguous features cannot be reliably mapped to pathways.",
    "unknown_feature_fraction": "Fraction of features whose raw feature name is a placeholder (unknown, NA, unidentified, unassigned, etc.). High values indicate poor annotation quality.",
    "feature_redundancy": "Detects repeated raw feature names within each assay. Duplicate names within a matrix should be reviewed/collapsed before ML.",
    # Label structure and support
    "class_balance": (
        "Computed on ML-eligible samples with usable class labels only. "
        "If no usable classes: score=0/100. If exactly 1 class: score=25/100. "
        "Otherwise score = 100 × min(class_count) / max(class_count). "
        "Status: pass if score >= 40/100, else warn."
    ),
    "group_size_support": (
        "Computed on ML-eligible samples with usable class labels only. Let m = smallest class size. "
        "If fewer than 2 classes: score=0/100 (warn). "
        "If m >= 20 -> score=100/100; 10<=m<=19 -> 70/100; 5<=m<=9 -> 40/100; m<5 -> 10/100. "
        "Status: pass if score >= 70/100, else warn."
    ),
    "label_entropy": (
        "Computed on ML-eligible samples with usable class labels only. "
        "For class proportions p_i and K classes: H = -sum(p_i * ln(p_i)), "
        "H_max = ln(K), score = 100 × H / H_max (clipped to [0,100]). "
        "If K<2: score=0/100 (warn). Status: pass if score >= 70/100, else warn. "
        "Interpretation: score near 100 means classes are evenly distributed; "
        "score near 0 means class dominance. Purpose: this captures class-distribution fairness, "
        "which affects model bias risk and split reliability."
    ),
    "sample_type_confounding_risk": "Checks class-vs-matrix/source association (Cramer's V). If entangled, models can learn matrix effects rather than disease biology. Single-matrix studies are not penalized.",
    "biological_sex_distribution": "Parses sex from structured attributes and factor strings. Single-sex datasets cannot support sex-stratified analyses.",
    "sample_matrix_homogeneity": "Counts distinct organism_part / sample_type values. Mixed matrices introduce technical variation; displayed score is multiplied by 70% per extra matrix type.",
    # ML Feasibility
    "label_suitability": "Checks that every class has at least 5 samples. Classes below this threshold cannot be reliably included in cross-validated splits.",
    "recommended_ml_task": "Classifies the ML task from label count: 0=undetermined, 1=single-class, 2=binary, 3–10=multi-class, >10=too many classes.",
    "feature_to_sample_ratio": "Ratio of total features to ML-eligible samples. Ratios >50 require regularisation; >200 require dimensionality reduction.",
    "stratified_split_feasibility": "Simulates a 75/25 stratified split and checks both partitions have ≥5 samples per class. Lists infeasible classes.",
    "benchmark_split_leakage_risk": "Detects duplicate sample IDs within each analysis matrix. Duplicate rows can leak information and inflate apparent model stability.",
    # Class Separability
    "class_separability": "Evaluates how well class labels can be separated using logistic regression AUROC under repeated stratified validation; study-level score aggregates eligible analyses only, and ineligible analyses are reported separately. Includes per-analysis PCA class-overlay plots.",
}

_METRIC_DISPLAY_NAMES: dict[str, str] = {
    "missingness_structure": "sample-level missingness",
    "feature_level_missingness": "feature-level missingness",
    "scale_diagnostics": "scale diagnostics",
    "qc_blank_presence": "QC / blank controls",
    "disease_endpoint_extractability": "label endpoint extractability",
    "mass_rt_like_metadata_presence": "mass/RT-like metadata presence",
    "metabatch_batch_annotation_compatibility": "MetaBatch annotation compatibility",
}


def _metric_display_name(metric_name: str) -> str:
    return _METRIC_DISPLAY_NAMES.get(metric_name, metric_name.replace("_", " "))


def _metric_descriptor(metric_name: str, params: dict[str, float] | None = None) -> str:
    params = params or _V2_DEFAULT_PARAMS
    if metric_name == "minimum_sample_count":
        return (
            "Counts ML-eligible samples after excluding QC pools, blanks, NIST references, and other non-biological rows. "
            "This criterion is calibrated for supervised classification and feature-selection reuse, not for judging "
            "whether a small triplicate time-course, isotope-tracing, or mechanistic experiment was adequate for its original purpose. "
            f"Current pass threshold: {_v2_fmt_param(params['g2_sample_pass'])}; fail below "
            f"{_v2_fmt_param(params['g2_sample_fail_below'])}."
        )
    if metric_name == "missingness_structure":
        return (
            "Sample-level missingness (ML-eligible samples only; QC/blank/pool/reference excluded): score = 100 × [1 - median per-sample missingness]. "
            f"Current G5 gate: pass <= {_v2_fmt_param(params['g5_missing_pass_pct'], pct=True)}, "
            f"fail > {_v2_fmt_param(params['g5_missing_fail_pct'], pct=True)}; "
            f"class-dependent gap warn >= {_v2_fmt_param(params['class_missingness_gap_warn_pct'], pct=True)}."
        )
    if metric_name == "class_balance":
        return (
            "Computed on ML-eligible samples with usable class labels only. "
            "Score = 100 × min(class_count) / max(class_count) when >=2 classes. "
            f"Current pass threshold: score >= {_v2_fmt_score(params['class_balance_pass'], digits=0)}."
        )
    if metric_name == "group_size_support":
        return (
            "Computed on ML-eligible samples with usable class labels only. Let m = smallest class size. "
            f"Current tiers: m >= {_v2_fmt_param(params['group_support_strong'])} -> 100; "
            f">= {_v2_fmt_param(params['group_support_moderate'])} -> 70; "
            f">= {_v2_fmt_param(params['group_support_weak'])} -> 40; below that -> 10."
        )
    if metric_name == "label_entropy":
        return (
            "Normalized class-label entropy; score near 100 means classes are evenly distributed. "
            f"Current pass threshold: score >= {_v2_fmt_score(params['label_entropy_pass'], digits=0)}."
        )
    if metric_name == "label_suitability":
        return (
            "Checks whether every class has enough samples for supervised ML. "
            f"Current minimum class target: {_v2_fmt_param(params['g4_class_pass'])}; "
            f"warn floor: {_v2_fmt_param(params['g4_class_warn_min'])}."
        )
    if metric_name == "feature_to_sample_ratio":
        return (
            "Sample-weighted feature-to-sample ratio score. "
            f"Current p/n score tiers: <= {_v2_fmt_param(params['pn_low'])} -> 100; "
            f"<= {_v2_fmt_param(params['pn_moderate'])} -> 80; "
            f"<= {_v2_fmt_param(params['pn_high'])} -> 50; above that uses "
            f"100 × max(0.1, 1 - ratio/{_v2_fmt_param(params['pn_tail'])})."
        )
    if metric_name == "feature_level_missingness":
        return (
            "Feature-level missingness = missing values per feature divided by sample count in each analysis. "
            f"Current status burden cutoff: warn if >= {_v2_fmt_param(params['feature_missingness_burden_warn_pct'], pct=True)} "
            "of features exceed the high-missingness threshold."
        )
    if metric_name == "feature_correlation_burden":
        return (
            "Analysis-wise sampled pairwise Pearson redundancy burden. "
            f"Current pass threshold: score >= {_v2_fmt_score(params['correlation_score_pass'], digits=0)}."
        )
    if metric_name == "outlier_burden":
        return (
            "Sample-level IQR outlier check; score = 100 × [1 - sample outlier rate]. "
            f"Current pass threshold: score >= {_v2_fmt_score(params['sample_outlier_score_pass'], digits=0)}."
        )
    if metric_name in {"identifier_coverage", "annotation_ambiguity_burden", "feature_annotation_type"}:
        return (
            _METRIC_DESCRIPTORS.get(metric_name, metric_name.replace("_", " "))
            + f" Current pass threshold: score >= {_v2_fmt_score(params['annotation_general_pass'], digits=0)}."
        )
    if metric_name == "feature_redundancy":
        return (
            _METRIC_DESCRIPTORS.get(metric_name, metric_name.replace("_", " "))
            + f" Current pass threshold: score >= {_v2_fmt_score(params['annotation_redundancy_pass'], digits=0)}."
        )
    if metric_name == "unknown_feature_fraction":
        return (
            _METRIC_DESCRIPTORS.get(metric_name, metric_name.replace("_", " "))
            + f" Current pass rule: unknown fraction <= {_v2_fmt_param(params['unknown_feature_max_pct'], pct=True)}."
        )
    return _METRIC_DESCRIPTORS.get(metric_name, "")


def _drop_legacy_batch_info_metric(report: Any) -> Any:
    """Hide the retired batch_info_availability metric from cached reports."""
    if report is None:
        return report
    metrics = getattr(report, "analytical_readiness", None)
    if isinstance(metrics, list):
        report.analytical_readiness = [
            m for m in metrics
            if getattr(m, "name", "") != "batch_info_availability"
        ]
    return report


_V2_SECTION_METRIC_COUNTS: dict[str, int] = {
    "structural": 5,
    "metadata": 3,
    "analytical": 5,
    "annotation": 4,
    "cohort": 3,
    "ml_feasibility": 4,
}

_V2_CORE_SECTION_KEYS = ("structural", "analytical", "annotation", "cohort", "ml_feasibility")
_V2_ML_SCORING_METRICS = {
    "disease_endpoint_extractability",
    "factor_label_harmonizability",
    "label_suitability",
    "feature_to_sample_ratio",
}


def _v2_metric_by_name(metrics: list[Any] | None, name: str) -> Any | None:
    for metric in metrics or []:
        if getattr(metric, "name", "") == name:
            return metric
    return None


def _v2_display_text(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    for old, new in (
        ("Cohort Structure and Class Balance", "Label Structure and Class Support"),
        ("sufficient biological sample count", "sufficient ML-eligible sample count"),
        ("biological sample count", "ML-eligible sample count"),
        ("Biological sample count", "ML-eligible sample count"),
        ("biological samples detected", "ML-eligible samples detected"),
        ("labeled biological samples", "labeled ML-eligible samples"),
        ("biological samples", "ML-eligible samples"),
    ):
        text = text.replace(old, new)

    for old, new in (
        (
            "Add a DOI to the PROJECT block (PROJECT.DOI in mwtab). Metabolomics Workbench assigns dataset DOIs under the 10.21228/ prefix at submission; contact Metabolomics Workbench support if the field is missing. A linked publication DOI is also accepted.",
            "Project DOI was not detected in the parsed source metadata at the time of MERIT-ML access. This limits automated citation completeness for ML-reuse reporting. Please verify the current Project DOI status on the original Metabolomics Workbench project page.",
        ),
        (
            "Link a publication (PUBLICATIONS field) so the methodology is traceable to peer-reviewed documentation.",
            "Associated publication metadata was not detected in the parsed source record. MERIT-ML flags methodology provenance as limited for automated reuse; users should verify publications on the original Metabolomics Workbench study page.",
        ),
        (
            "Declare the funding source (FUNDING_SOURCE) to meet standard RDM requirements and enable cross-study funding-body filtering.",
            "Funding-source metadata was not detected in the parsed source record. This may limit automated metadata reuse and filtering; users should verify the original Metabolomics Workbench record.",
        ),
        (
            "Add populated m/z, retention time/index, or mass-like metabolite metadata fields to the mwTab Metabolites block when available. This improves reuse and independent reannotation.",
            "Mass/RT-like metabolite metadata was not detected in the parsed source fields. This may limit independent reannotation or harmonization; users should verify whether such metadata is available in the source record.",
        ),
        (
            "Fill missing disease, sample type, and descriptive fields to improve comparability.",
            "Some disease, sample-type, or descriptive metadata fields were not detected in parsed metadata. This may limit automated cohort filtering or reuse; users should verify the original Metabolomics Workbench record.",
        ),
        (
            "Filter or impute high-missingness features before modeling.",
            "High-missingness patterns may require preprocessing or sensitivity analysis before supervised ML reuse.",
        ),
        (
            "Class-dependent missingness gap >= 10%; audit acquisition/preprocessing artifacts before ML.",
            "Class-dependent missingness differs across labels. Users should check whether acquisition or preprocessing artifacts could influence supervised ML reuse.",
        ),
        (
            "Collapse redundant features before benchmarking to reduce leakage and instability.",
            "Highly redundant features may affect model stability or feature-selection interpretation; users may consider collapsing, filtering, or regularized modeling.",
        ),
        (
            "Inspect outlier samples separately before training and verify no acquisition artifacts dominate.",
            "Outlier samples were detected by MERIT-ML diagnostics. Users should inspect whether these reflect biological signal, acquisition artifacts, or preprocessing effects before supervised ML reuse.",
        ),
        (
            "Rebalance or stratify the cohort before training predictive models.",
            "Class imbalance may affect supervised-classification reuse. Users should consider stratified evaluation, class weighting, label merging, or additional validation.",
        ),
        (
            "At least 2 labeled classes are required to assess class-size support.",
            "At least two labeled groups are needed for MERIT-ML to assess supervised-classification class support.",
        ),
        (
            "Use labels with >=2 classes and enough samples per class.",
            "Supervised-classification reuse requires at least two usable label groups with sufficient per-group support.",
        ),
        (
            "Only one distinct label group found — binary or multi-class classification is not possible.",
            "Only one label group was detected in the parsed ML-eligible samples. Supervised classification reuse is not supported unless an alternative valid label definition is provided.",
        ),
        (
            "Increase minority-class size or merge labels before benchmarking.",
            "The smallest parsed label group has limited support for benchmarking. Users may consider label merging, exploratory-only use, or additional validation data.",
        ),
        (
            "Simplify or curate label strings before building ML models.",
            "Parsed label strings may require curation before supervised ML reuse so that class definitions are unambiguous.",
        ),
        (
            "Named metabolites are preferred for ML interpretability and cross-study reuse. Consider studies with higher annotation coverage.",
            "Named metabolite annotations were limited in the parsed source. This may constrain biological interpretation and cross-study reuse.",
        ),
        (
            "Flag unresolved isomers and unknowns before interpretation-heavy analyses.",
            "Unresolved isomers or unknown feature labels may limit interpretation-heavy analyses; users should verify annotations before biological interpretation.",
        ),
        (
            "Improve annotation quality for better interpretability and transfer.",
            "Annotation coverage may limit interpretability and cross-study reuse.",
        ),
        (
            "Proceed to external validation and holdout analysis.",
            "Proceed with external validation, holdout analysis, and appropriate sensitivity checks.",
        ),
    ):
        text = text.replace(old, new)

    text = re.sub(
        r"Only\s+(\d+/\d+)\s+metabolites have a RefMet match\. Submit missing metabolites to RefMet for standardised annotation\.",
        r"Only \1 named metabolites were matched to RefMet in MERIT-ML parsing. Identifier-level reuse may therefore be limited; users should verify metabolite identifiers and mappings at the source.",
        text,
    )
    text = re.sub(
        r"Drop or impute\s+([0-9,]+)\s+features with >30% missingness before training\.",
        r"\1 features exceed the high-missingness threshold used by MERIT-ML. Downstream users may need imputation, filtering, or sensitivity analysis before supervised ML reuse.",
        text,
    )
    text = re.sub(
        r"Increase samples in the smallest class \(target >=10, ideally >=20\) for reliable modeling\.",
        "The smallest parsed class has limited sample support for supervised classification. Consider whether labels should be merged, restricted to exploratory use, or validated with additional data.",
        text,
    )
    text = re.sub(
        r"Fewer than\s+(\d+)\s+ML-eligible samples detected\. ML models may be unreliable at this scale\.",
        r"Fewer than \1 ML-eligible samples were detected. MERIT-ML treats this as limited support for reliable supervised-classification training, validation, or feature selection.",
        text,
    )
    text = re.sub(
        r"High feature-to-sample ratio \(worst ([^)]+)\)\. Use regularised models \(LASSO, Ridge, Elastic Net\) or apply feature selection before training\.",
        r"High feature-to-sample ratio was detected (worst \1). Supervised ML reuse may require regularized models, feature selection, or sensitivity analysis.",
        text,
    )

    # Cached summaries often contain normalized score phrases such as
    # "score is 0.700"; v2 displays user-facing scores on a 0-100 scale.
    def _score_phrase_repl(match: re.Match[str]) -> str:
        prefix = match.group(1)
        value = float(match.group(2))
        return f"{prefix}{value * 100:.1f}"

    text = re.sub(
        r"(?i)(score\s+(?:is|was)\s+)(0(?:\.\d+)?|1(?:\.0+)?)\b",
        _score_phrase_repl,
        text,
    )
    text = re.sub(
        r"(?i)(aggregate score\s+)(0(?:\.\d+)?|1(?:\.0+)?)\b",
        _score_phrase_repl,
        text,
    )

    def _score_fraction_repl(match: re.Match[str]) -> str:
        prefix = match.group(1)
        fraction = match.group(2)
        value = float(match.group(3))
        return f"{prefix}100 × {fraction}{value * 100:.1f}/100"

    text = re.sub(
        r"(?i)(score\s*=\s*)(\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*=\s*)(0(?:\.\d+)?|1(?:\.0+)?)\b",
        _score_fraction_repl,
        text,
    )
    return text


def _v2_state_study_id(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    report = state.get("final_report")
    summary = getattr(report, "ingestion_summary", None)
    if isinstance(summary, dict) and summary.get("study_id"):
        return str(summary.get("study_id", "")).strip().upper()
    bundle = state.get("bundle") if isinstance(state.get("bundle"), dict) else {}
    if bundle.get("study_id"):
        return str(bundle.get("study_id", "")).strip().upper()
    return str(state.get("study_id", "") or "").strip().upper()


def _v2_dump_root() -> Path:
    return Path(os.environ.get("MERIT_MW_DUMP_ROOT", _V2_DEFAULT_MW_DUMP_ROOT)).expanduser()


def _v2_default_sample_eligible(sample_id: str, label: str) -> bool:
    return not sample_is_qc_like(
        sample_id=sample_id,
        label=label,
        class_string=label,
        factor_string=label,
    )


def _v2_read_row_oriented_sample_table(path: Path) -> list[dict[str, str]]:
    """Read sample rows from datatable/untarg_data style matrices."""
    rows: list[dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            header_line = handle.readline()
            if not header_line:
                return rows
            header = header_line.rstrip("\n\r").split("\t")
            sample_idx = 0
            for i, col in enumerate(header):
                if str(col).strip().casefold() in {"samples", "sample", "sample_id", "sample id"}:
                    sample_idx = i
                    break
            class_idx = None
            for i, col in enumerate(header):
                if str(col).strip().casefold() in {"class", "label", "group", "factors", "factor"}:
                    class_idx = i
                    break
            for line in handle:
                cols = line.rstrip("\n\r").split("\t")
                if sample_idx >= len(cols):
                    continue
                sid = str(cols[sample_idx] or "").strip()
                if not sid or sid.casefold() in {"samples", "sample", "sample id", "sample_id"}:
                    continue
                label = str(cols[class_idx] or "").strip() if class_idx is not None and class_idx < len(cols) else ""
                rows.append({"sample_id": sid, "label": label})
    except Exception:
        return []
    return rows


def _v2_read_mwtab_samples(path: Path) -> list[dict[str, str]]:
    """Read sample IDs and factor labels from an mwTab text file."""
    labels: dict[str, str] = {}
    matrix_samples: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    for line in lines:
        if not line.startswith("SUBJECT_SAMPLE_FACTORS"):
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            sid = str(parts[2] or "").strip()
            label = str(parts[3] or "").strip()
            if sid and sid not in labels:
                labels[sid] = label
    for i, line in enumerate(lines[:-1]):
        if not line.strip().endswith("_DATA_START"):
            continue
        header = lines[i + 1].rstrip("\n\r").split("\t")
        if header and header[0].strip().casefold() == "samples":
            for sid in header[1:]:
                sid = str(sid or "").strip()
                if sid:
                    matrix_samples.add(sid)
    sample_ids = matrix_samples or set(labels)
    return [{"sample_id": sid, "label": labels.get(sid, "")} for sid in sorted(sample_ids)]


def _v2_read_study_factor_labels(study_dir: Path) -> dict[str, dict[str, str]]:
    """Read REST/factors-endpoint sample labels from the local confirmation dump."""
    path = study_dir / "factors.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        rows = list(payload.values())
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    lookup: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sample_id = str(
            row.get("local_sample_id")
            or row.get("local_sample_id".upper())
            or row.get("sample_id")
            or row.get("Sample ID")
            or row.get("mb_sample_id")
            or ""
        ).strip()
        if not sample_id:
            continue
        factors = row.get("factors")
        if isinstance(factors, dict):
            parts = [
                f"{str(k).strip()}:{str(v).strip()}"
                for k, v in factors.items()
                if str(k).strip() and str(v).strip()
            ]
            factors_text = " | ".join(parts)
        else:
            factors_text = str(factors or row.get("Factors") or "").strip()
        sample_source = str(
            row.get("sample_source")
            or row.get("Sample source")
            or row.get("sample_type")
            or ""
        ).strip()
        lookup[sample_id] = {
            "native_label": factors_text,
            "sample_source": sample_source,
            "mb_sample_id": str(row.get("mb_sample_id") or row.get("MB Sample ID") or "").strip(),
        }
    return lookup


def _v2_build_sample_matrix_model(state: dict[str, Any] | None) -> dict[str, Any]:
    """Build a local, UI-only sample table from the confirmation dump."""
    study_id = _v2_state_study_id(state)
    model: dict[str, Any] = {
        "study_id": study_id,
        "dump_root": str(_v2_dump_root()),
        "dump_available": False,
        "samples": [],
        "class_labels": [],
        "source_sample_ids": {},
        "analysis_samples": {},
        "message": "",
    }
    if not study_id:
        model["message"] = "Study ID unavailable."
        return model
    study_dir = _v2_dump_root() / study_id
    if not study_dir.exists():
        model["message"] = f"Parsed sample metadata is unavailable for {study_id}."
        return model
    model["dump_available"] = True
    factors_lookup = _v2_read_study_factor_labels(study_dir)

    sample_map: dict[str, dict[str, Any]] = {}
    source_sample_ids: dict[str, set[str]] = {"datatable": set(), "mwtab": set(), "untarg_data": set()}
    analysis_samples: dict[str, dict[str, set[str]]] = {"datatable": {}, "mwtab": {}, "untarg_data": {}}

    def add_rows(source_key: str, analysis_id: str, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        analysis_samples.setdefault(source_key, {}).setdefault(analysis_id, set())
        for row in rows:
            sid = str(row.get("sample_id", "") or "").strip()
            if not sid:
                continue
            label = str(row.get("label", "") or "").strip()
            rec = sample_map.setdefault(
                sid,
                {
                    "sample_id": sid,
                    "default_label": "",
                    "native_label": "",
                    "native_sample_source": "",
                    "native_mb_sample_id": "",
                    "sources": set(),
                    "analyses_by_source": {},
                },
            )
            factor_item = factors_lookup.get(sid, {})
            if factor_item:
                if factor_item.get("native_label") and not rec.get("native_label"):
                    rec["native_label"] = factor_item.get("native_label", "")
                if factor_item.get("sample_source") and not rec.get("native_sample_source"):
                    rec["native_sample_source"] = factor_item.get("sample_source", "")
                if factor_item.get("mb_sample_id") and not rec.get("native_mb_sample_id"):
                    rec["native_mb_sample_id"] = factor_item.get("mb_sample_id", "")
            if label and not rec.get("default_label"):
                rec["default_label"] = label
            rec["sources"].add(source_key)
            rec.setdefault("analyses_by_source", {}).setdefault(source_key, set()).add(analysis_id)
            source_sample_ids.setdefault(source_key, set()).add(sid)
            analysis_samples[source_key][analysis_id].add(sid)

    for an_dir in sorted(study_dir.glob("AN*")):
        if not an_dir.is_dir():
            continue
        analysis_id = an_dir.name.upper()
        for path in sorted((an_dir / "tabular").glob("*_datatable.tsv")):
            add_rows("datatable", analysis_id, _v2_read_row_oriented_sample_table(path))
        for path in sorted((an_dir / "tabular").glob("*_untarg_data.tsv")):
            add_rows("untarg_data", analysis_id, _v2_read_row_oriented_sample_table(path))
        for path in sorted((an_dir / "json").glob("*_mwtab.txt")):
            add_rows("mwtab", analysis_id, _v2_read_mwtab_samples(path))

    samples: list[dict[str, Any]] = []
    labels: set[str] = set()
    for sid, rec in sorted(sample_map.items()):
        label = str(rec.get("default_label", "") or "").strip()
        native_label = str(rec.get("native_label", "") or "").strip()
        if label:
            labels.add(label)
        if native_label:
            labels.add(native_label)
        sources = sorted(str(s) for s in rec.get("sources", set()))
        analyses_by_source = {
            source: sorted(str(a) for a in analyses)
            for source, analyses in (rec.get("analyses_by_source", {}) or {}).items()
        }
        samples.append({
            "sample_id": sid,
            "default_label": label,
            "native_label": native_label or label,
            "native_sample_source": str(rec.get("native_sample_source", "") or ""),
            "native_mb_sample_id": str(rec.get("native_mb_sample_id", "") or ""),
            "default_eligible": _v2_default_sample_eligible(sid, label),
            "sources": sources,
            "analyses_by_source": analyses_by_source,
        })
    model["samples"] = samples
    model["class_labels"] = sorted(labels)
    model["source_sample_ids"] = {k: sorted(v) for k, v in source_sample_ids.items()}
    model["analysis_samples"] = {
        source: {aid: sorted(ids) for aid, ids in per_source.items()}
        for source, per_source in analysis_samples.items()
    }
    if not samples:
        model["message"] = "No sample rows could be read for this study."
    return model


def _v2_effective_sample_rows(
    model: dict[str, Any],
    overrides: dict[str, dict[str, Any]] | None,
    source_key: str | None = None,
) -> list[dict[str, Any]]:
    overrides = overrides or {}
    source_ids = set((model.get("source_sample_ids") or {}).get(source_key or "", []) or [])
    rows: list[dict[str, Any]] = []
    for row in model.get("samples", []) or []:
        sid = str(row.get("sample_id", "") or "").strip()
        if source_key and source_ids and sid not in source_ids:
            continue
        item = overrides.get(sid, {})
        label = str(item.get("label", row.get("default_label", "")) or "").strip()
        excluded = bool(item.get("excluded", False))
        eligible = bool(item.get("eligible", row.get("default_eligible", False))) and not excluded
        rows.append({
            **row,
            "label": label,
            "eligible": eligible,
            "excluded": excluded,
            "normalized_label": normalize_label(label) if is_usable_class_label(label) else "",
        })
    return rows


def _v2_label_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_rows = [row for row in rows if not row.get("excluded")]
    eligible = [row for row in active_rows if row.get("eligible")]
    usable = [row for row in eligible if is_usable_class_label(str(row.get("label", "") or ""))]
    raw_counts = Counter(str(row.get("label", "") or "").strip() for row in usable)
    norm_counts = Counter(normalize_label(str(row.get("label", "") or "")) for row in usable)
    non_usable = Counter(
        str(row.get("label", "") or "").strip() or "<empty>"
        for row in eligible
        if not is_usable_class_label(str(row.get("label", "") or ""))
    )
    n_classes = len(norm_counts)
    total_labeled = sum(norm_counts.values())
    if n_classes < 2 or total_labeled <= 0:
        entropy = entropy_max = entropy_norm = 0.0
    else:
        probs = [count / total_labeled for count in norm_counts.values() if count > 0]
        entropy = -sum(p * log(p) for p in probs)
        entropy_max = log(n_classes)
        entropy_norm = max(0.0, min(1.0, entropy / entropy_max if entropy_max > 0 else 0.0))
    balance_score = 0.0
    if n_classes == 1:
        balance_score = 0.25
    elif n_classes >= 2:
        balance_score = min(norm_counts.values()) / max(norm_counts.values())
    return {
        "n_total": len(active_rows),
        "n_detected": len(rows),
        "n_excluded": len(rows) - len(active_rows),
        "n_eligible": len(eligible),
        "n_labeled": total_labeled,
        "n_classes": n_classes,
        "raw_counts": dict(raw_counts),
        "norm_counts": dict(norm_counts),
        "non_usable_counts": dict(non_usable),
        "label_coverage": (total_labeled / len(eligible)) if eligible else 0.0,
        "min_group_size": min(norm_counts.values()) if norm_counts else 0,
        "balance_score": balance_score,
        "entropy": entropy,
        "entropy_max": entropy_max,
        "entropy_norm": entropy_norm,
        "raw_unique_labels": sorted(raw_counts),
        "norm_unique_labels": sorted(norm_counts),
    }


def _v2_update_metric(metric: Any, *, score: float | None = None, status: str | None = None,
                      summary: str | None = None, details: dict[str, Any] | None = None) -> None:
    if metric is None:
        return
    if score is not None:
        metric.score = score
    if status is not None:
        metric.status = status
    if summary is not None:
        metric.summary = summary
    if details is not None:
        existing = dict(getattr(metric, "details", {}) or {})
        existing.update(details)
        metric.details = existing


def _v2_apply_matrix_overrides_to_report(
    report: Any,
    model: dict[str, Any],
    overrides: dict[str, dict[str, Any]],
    source_key: str | None,
) -> Any:
    if report is None or not model.get("samples") or not overrides:
        return report
    rows = _v2_effective_sample_rows(model, overrides, source_key)
    stats = _v2_label_stats(rows)

    summary = dict(getattr(report, "ingestion_summary", {}) or {})
    summary["n_samples"] = stats["n_total"]
    summary["n_biological_samples"] = stats["n_eligible"]
    summary["n_labeled_samples"] = stats["n_labeled"]
    summary["n_classes"] = stats["n_classes"]
    summary["class_counts"] = stats["raw_counts"]
    summary["v2_matrix_overrides_applied"] = True
    report.ingestion_summary = summary

    class_balance = _v2_metric_by_name(getattr(report, "cohort_bias", []), "class_balance")
    _v2_update_metric(
        class_balance,
        score=float(stats["balance_score"]),
        status="pass" if float(stats["balance_score"]) >= 0.4 else "warn",
        summary=f"Class balance score is {_v2_fmt_score(stats['balance_score'])}/100 across {stats['n_classes']} labeled groups.",
        details={"counts": stats["norm_counts"], "v2_matrix_overrides_applied": True},
    )

    group_support = _v2_metric_by_name(getattr(report, "cohort_bias", []), "group_size_support")
    _v2_update_metric(
        group_support,
        details={
            "counts": stats["norm_counts"],
            "n_classes": stats["n_classes"],
            "min_group_size": stats["min_group_size"],
            "v2_matrix_overrides_applied": True,
        },
    )

    entropy = _v2_metric_by_name(getattr(report, "cohort_bias", []), "label_entropy")
    _v2_update_metric(
        entropy,
        score=float(stats["entropy_norm"]),
        status="pass" if float(stats["entropy_norm"]) >= 0.7 else "warn",
        summary=(
            f"Normalized label entropy is {_v2_fmt_score(stats['entropy_norm'])}/100 across "
            f"{stats['n_classes']} classes ({stats['n_labeled']} labeled ML-eligible samples)."
        ),
        details={
            "counts": stats["norm_counts"],
            "n_classes": stats["n_classes"],
            "total_samples": stats["n_labeled"],
            "entropy": stats["entropy"],
            "entropy_max": stats["entropy_max"],
            "entropy_norm": stats["entropy_norm"],
            "v2_matrix_overrides_applied": True,
        },
    )

    min_sample = _v2_metric_by_name(getattr(report, "schema_validation", []), "minimum_sample_count")
    _v2_update_metric(
        min_sample,
        details={
            "n_biological_samples": stats["n_eligible"],
            "n_total_samples": stats["n_total"],
            "v2_matrix_overrides_applied": True,
        },
    )

    endpoint = _v2_metric_by_name(getattr(report, "ml_readiness", []), "disease_endpoint_extractability")
    if endpoint is not None:
        disease_field = str((getattr(endpoint, "details", {}) or {}).get("study_disease_field", "") or "")
        coverage = float(stats["label_coverage"])
        groups = int(stats["n_classes"])
        if not disease_field and coverage < 0.5:
            score, status = 0.0, "fail"
        elif groups >= 2 and coverage >= 0.8:
            score, status = 1.0, "pass"
        elif groups >= 2 and coverage >= 0.5:
            score, status = 0.7, "warn"
        else:
            score, status = 0.3, "warn"
        _v2_update_metric(
            endpoint,
            score=score,
            status=status,
            summary=(
                f"Study disease metadata {'present' if disease_field else 'absent'}. "
                f"Label endpoint extractability: {groups} group(s) across {coverage:.0%} of ML-eligible samples "
                "after UI matrix-property overrides."
            ),
            details={
                "distinct_label_groups": groups,
                "label_counts": stats["norm_counts"],
                "label_coverage": coverage,
                "non_usable_label_counts": stats["non_usable_counts"],
                "v2_matrix_overrides_applied": True,
            },
        )

    factor = _v2_metric_by_name(getattr(report, "ml_readiness", []), "factor_label_harmonizability")
    if factor is not None:
        label_quality = float(stats["label_coverage"])
        raw_labels = stats["raw_unique_labels"]
        avg_pipe_count = (
            sum(str(lbl).count("|") for lbl in raw_labels) / len(raw_labels)
            if raw_labels else 0.0
        )
        n_dims = round(avg_pipe_count) + 1 if raw_labels else 0
        if not raw_labels:
            simplicity = 0.0
        elif any(str(lbl).strip().replace(".", "", 1).isdigit() for lbl in raw_labels):
            simplicity = 0.0
        elif n_dims <= 1:
            simplicity = 1.0
        elif n_dims == 2:
            simplicity = 0.7
        elif n_dims == 3:
            simplicity = 0.4
        else:
            simplicity = 0.1
        score = 0.5 * label_quality + 0.5 * simplicity
        _v2_update_metric(
            factor,
            score=score,
            status="pass" if score >= 0.75 else "warn",
            summary=(
                f"{len(raw_labels)} unique label(s) across {max(n_dims, 0)} factor dimension(s). "
                f"Label quality: {label_quality:.0%}, simplicity: {simplicity:.1f}. "
                "UI matrix-property overrides applied."
            ),
            details={
                "raw_unique_labels": raw_labels[:20],
                "normalized_unique_labels": stats["norm_unique_labels"][:20],
                "label_quality": round(label_quality, 4),
                "simplicity": simplicity,
                "avg_pipe_count": round(avg_pipe_count, 2),
                "n_factor_dimensions": max(n_dims, 0),
                "v2_matrix_overrides_applied": True,
            },
        )

    suitability = _v2_metric_by_name(getattr(report, "ml_readiness", []), "label_suitability")
    _v2_update_metric(
        suitability,
        details={
            "counts": stats["norm_counts"],
            "v2_matrix_overrides_applied": True,
        },
    )

    ratio = _v2_metric_by_name(getattr(report, "ml_readiness", []), "feature_to_sample_ratio")
    if ratio is not None:
        details = dict(getattr(ratio, "details", {}) or {})
        analysis_samples = (model.get("analysis_samples") or {}).get(source_key or "", {}) or {}
        per_analysis = []
        for item in details.get("per_analysis", []) or []:
            if not isinstance(item, dict):
                continue
            item = dict(item)
            aid = str(item.get("analysis_id", "") or "").strip().upper()
            sample_ids = set(analysis_samples.get(aid, []))
            if sample_ids:
                per_rows = [row for row in _v2_effective_sample_rows(model, overrides, source_key) if row.get("sample_id") in sample_ids]
                n_matrix_eligible = sum(1 for row in per_rows if row.get("eligible"))
            else:
                n_matrix_eligible = stats["n_eligible"]
            n_features = int(item.get("n_features_in_matrix", item.get("n_features", 0)) or 0)
            item["n_samples_in_matrix"] = n_matrix_eligible
            item["ratio"] = round((n_features / n_matrix_eligible), 4) if n_matrix_eligible else float(n_features or 0)
            per_analysis.append(item)
        details["per_analysis"] = per_analysis
        details["n_biological_samples"] = stats["n_eligible"]
        details["v2_matrix_overrides_applied"] = True
        ratio.details = details

    return report


def _v2_apply_display_terminology_to_report(report: Any) -> Any:
    """Apply v2 display-only terminology without changing metric keys/details."""
    for family_name in (
        "schema_validation",
        "metadata_readiness",
        "analytical_readiness",
        "annotation_readiness",
        "cohort_bias",
        "ml_readiness",
        "separability",
    ):
        for metric in getattr(report, family_name, []) or []:
            if hasattr(metric, "family"):
                metric.family = _v2_display_text(getattr(metric, "family", ""))
            if hasattr(metric, "summary"):
                metric.summary = _v2_display_text(getattr(metric, "summary", ""))
            recs = getattr(metric, "recommendations", None)
            if isinstance(recs, list):
                metric.recommendations = [_v2_display_text(item) for item in recs]
    return report


def _v2_mean_score(metrics: list[Any] | None, fixed_count: int) -> float:
    items = [
        m for m in (metrics or [])
        if not getattr(m, "informational", False)
        and getattr(m, "name", "") not in _V2_HIDDEN_LEGACY_METRICS
    ]
    if not items:
        return 0.0
    denominator = max(len(items), fixed_count)
    return sum(float(getattr(metric, "score", 0.0) or 0.0) for metric in items) / denominator


def _v2_status_badge_for_score(score: float, pass_min: float) -> str:
    return "pass" if float(score) >= float(pass_min) else "warn"


def _v2_ratio_score(ratio: float, params: dict[str, float]) -> float:
    low = params["pn_low"]
    moderate = params["pn_moderate"]
    high = params["pn_high"]
    tail = max(params["pn_tail"], high + 1.0)
    if ratio <= low:
        return 1.0
    if ratio <= moderate:
        return 0.8
    if ratio <= high:
        return 0.5
    return max(0.1, 1.0 - ratio / tail)


def _v2_band_from_score(score: float, params: dict[str, float]) -> str:
    if score >= params["band_ready_min"]:
        return "Ready"
    if score >= params["band_conditional_min"]:
        return "Conditional"
    if score >= params["band_exploratory_min"]:
        return "Fragile"
    return "Not Ready"


def _v2_cap_band(provisional_band: str, ceiling_band: str | None) -> str:
    if not ceiling_band:
        return provisional_band
    current_rank = _V2_BAND_ORDER.get(provisional_band, 1)
    ceiling_rank = _V2_BAND_ORDER.get(ceiling_band, current_rank)
    capped_rank = min(current_rank, ceiling_rank)
    for name, rank in _V2_BAND_ORDER.items():
        if rank == capped_rank:
            return name
    return provisional_band


def _v2_adjust_report_metrics(report: Any, params: dict[str, float]) -> Any:
    """Apply experimental scoring parameters to a copied AssessmentReport.

    This v2 path is intentionally presentation/local only: cached JSON is not
    edited, and metrics that need raw feature distributions unavailable in the
    cache are left as cached scores with only status/tooltips updated.
    """
    if report is None:
        return report
    _v2_apply_display_terminology_to_report(report)

    # Structural: minimum ML-eligible samples.
    min_sample = _v2_metric_by_name(getattr(report, "schema_validation", []), "minimum_sample_count")
    if min_sample is not None:
        details = dict(getattr(min_sample, "details", {}) or {})
        n_bio = int(details.get("n_biological_samples", 0) or 0)
        threshold = max(1.0, params["g2_sample_pass"])
        min_sample.score = min(1.0, n_bio / threshold)
        min_sample.status = "pass" if n_bio >= threshold else "warn"
        details["threshold"] = int(round(threshold))
        min_sample.details = details
        min_sample.thresholds = {"minimum_biological_samples": int(round(threshold))}
        min_sample.summary = f"{n_bio} ML-eligible samples detected (threshold: {int(round(threshold))})."

    # Analytical: status cutoffs and summaries where cache has sufficient evidence.
    missingness = _v2_metric_by_name(getattr(report, "analytical_readiness", []), "missingness_structure")
    if missingness is not None:
        details = dict(getattr(missingness, "details", {}) or {})
        gap = float(details.get("class_dependent_gap_weighted", details.get("class_dependent_gap", 0.0)) or 0.0)
        pass_min = params["sample_missingness_score_pass"]
        gap_warn = params["class_missingness_gap_warn_pct"] / 100.0
        missingness.status = "pass" if float(missingness.score or 0.0) >= pass_min and gap < gap_warn else "warn"
        details["recommended_minimum"] = pass_min
        details["class_dependent_gap_warn"] = gap_warn
        missingness.details = details
        missingness.thresholds = {"recommended_minimum": pass_min, "class_dependent_gap_warn": gap_warn}

    outlier = _v2_metric_by_name(getattr(report, "analytical_readiness", []), "outlier_burden")
    if outlier is not None:
        pass_min = params["sample_outlier_score_pass"]
        outlier.status = _v2_status_badge_for_score(float(outlier.score or 0.0), pass_min)
        thresholds = dict(getattr(outlier, "thresholds", {}) or {})
        thresholds["recommended_minimum"] = pass_min
        outlier.thresholds = thresholds

    corr = _v2_metric_by_name(getattr(report, "analytical_readiness", []), "feature_correlation_burden")
    if corr is not None:
        pass_min = params["correlation_score_pass"]
        corr.status = _v2_status_badge_for_score(float(corr.score or 0.0), pass_min)
        thresholds = dict(getattr(corr, "thresholds", {}) or {})
        thresholds["recommended_minimum"] = pass_min
        corr.thresholds = thresholds

    flm = _v2_metric_by_name(getattr(report, "analytical_readiness", []), "feature_level_missingness")
    if flm is not None:
        details = dict(getattr(flm, "details", {}) or {})
        burden = float(details.get("pct_features_over_threshold", 0.0) or 0.0)
        warn_cutoff = params["feature_missingness_burden_warn_pct"] / 100.0
        flm.status = "pass" if burden < warn_cutoff else "warn"
        thresholds = dict(getattr(flm, "thresholds", {}) or {})
        thresholds["high_missing_burden_warn_fraction"] = warn_cutoff
        thresholds["display_high_missing_threshold"] = params["feature_missingness_threshold_pct"] / 100.0
        flm.thresholds = thresholds

    # Annotation: score fractions are intrinsic, but status cutoffs are tunable.
    for metric in getattr(report, "annotation_readiness", []) or []:
        name = getattr(metric, "name", "")
        score = float(getattr(metric, "score", 0.0) or 0.0)
        if name in {"identifier_coverage", "annotation_ambiguity_burden", "feature_annotation_type"}:
            metric.status = _v2_status_badge_for_score(score, params["annotation_general_pass"])
            metric.thresholds = {"recommended_minimum": params["annotation_general_pass"]}
        elif name == "feature_redundancy":
            metric.status = _v2_status_badge_for_score(score, params["annotation_redundancy_pass"])
            metric.thresholds = {"recommended_minimum": params["annotation_redundancy_pass"]}
        elif name == "unknown_feature_fraction":
            max_unknown = params["unknown_feature_max_pct"] / 100.0
            metric.status = "pass" if (1.0 - score) <= max_unknown else "warn"
            metric.thresholds = {"recommended_maximum_unknown_fraction": max_unknown}

    # Label structure: class balance, group-size support, and entropy cutoffs.
    class_balance = _v2_metric_by_name(getattr(report, "cohort_bias", []), "class_balance")
    if class_balance is not None:
        class_balance.status = _v2_status_badge_for_score(float(class_balance.score or 0.0), params["class_balance_pass"])
        class_balance.thresholds = {"recommended_minimum": params["class_balance_pass"]}

    group_support = _v2_metric_by_name(getattr(report, "cohort_bias", []), "group_size_support")
    if group_support is not None:
        details = dict(getattr(group_support, "details", {}) or {})
        counts = details.get("counts", {}) or {}
        n_classes = int(details.get("n_classes", len(counts) if isinstance(counts, dict) else 0) or 0)
        min_n = int(details.get("min_group_size", 0) or 0)
        strong = params["group_support_strong"]
        moderate = params["group_support_moderate"]
        weak = params["group_support_weak"]
        if n_classes < 2:
            group_support.score = 0.0
            group_support.status = "warn"
            group_support.summary = "Group-size support unavailable (fewer than 2 labeled classes)."
        elif min_n >= strong:
            group_support.score = 1.0
            group_support.status = "pass"
        elif min_n >= moderate:
            group_support.score = 0.7
            group_support.status = "pass"
        elif min_n >= weak:
            group_support.score = 0.4
            group_support.status = "warn"
        else:
            group_support.score = 0.1
            group_support.status = "warn"
        if n_classes >= 2:
            group_support.summary = f"Smallest class has {min_n} samples across {n_classes} labeled groups."
        group_support.thresholds = {
            "strong_support_min_n": int(round(strong)),
            "moderate_support_min_n": int(round(moderate)),
            "weak_support_min_n": int(round(weak)),
        }

    entropy = _v2_metric_by_name(getattr(report, "cohort_bias", []), "label_entropy")
    if entropy is not None:
        entropy.status = _v2_status_badge_for_score(float(entropy.score or 0.0), params["label_entropy_pass"])
        entropy.thresholds = {"recommended_minimum": params["label_entropy_pass"]}

    # ML task readiness: label suitability and feature/sample ratio scores.
    label_suitability = _v2_metric_by_name(getattr(report, "ml_readiness", []), "label_suitability")
    if label_suitability is not None:
        details = dict(getattr(label_suitability, "details", {}) or {})
        counts = details.get("counts", {}) or {}
        min_class = int(min(counts.values())) if isinstance(counts, dict) and counts else 0
        min_required = max(1.0, params["g4_class_pass"])
        if not isinstance(counts, dict) or len(counts) < 2:
            label_suitability.score = 0.0
        else:
            label_suitability.score = min(1.0, min_class / min_required)
        label_suitability.status = "pass" if float(label_suitability.score or 0.0) >= 1.0 else "warn"
        details["minimum_class_count"] = int(round(min_required))
        label_suitability.details = details
        label_suitability.thresholds = {"minimum_class_count": int(round(min_required))}
        label_suitability.summary = f"Label suitability score is {_v2_fmt_score(label_suitability.score)}/100 with class counts {counts}."

    ratio_metric = _v2_metric_by_name(getattr(report, "ml_readiness", []), "feature_to_sample_ratio")
    if ratio_metric is not None:
        details = dict(getattr(ratio_metric, "details", {}) or {})
        per_analysis = details.get("per_analysis", [])
        weighted_sum = 0.0
        weight_sum = 0.0
        worst_ratio = 0.0
        if isinstance(per_analysis, list):
            updated = []
            for item in per_analysis:
                if not isinstance(item, dict):
                    continue
                item = dict(item)
                try:
                    ratio = float(item.get("ratio", 0.0) or 0.0)
                except Exception:
                    ratio = 0.0
                try:
                    weight = float(item.get("n_samples_in_matrix", 0.0) or 0.0)
                except Exception:
                    weight = 0.0
                item_score = _v2_ratio_score(ratio, params)
                item["score"] = round(item_score, 3)
                updated.append(item)
                weighted_sum += item_score * weight
                weight_sum += weight
                worst_ratio = max(worst_ratio, ratio)
            details["per_analysis"] = updated
        ratio_metric.score = (weighted_sum / weight_sum) if weight_sum > 0 else float(ratio_metric.score or 0.0)
        ratio_metric.status = "pass" if float(ratio_metric.score or 0.0) >= 0.8 else "warn"
        details["composite_score"] = round(float(ratio_metric.score or 0.0), 3)
        details["worst_ratio"] = round(worst_ratio or float(details.get("worst_ratio", 0.0) or 0.0), 2)
        ratio_metric.details = details
        ratio_metric.thresholds = {
            "low_risk": params["pn_low"],
            "moderate_risk": params["pn_moderate"],
            "high_risk": params["pn_high"],
            "tail_denominator": params["pn_tail"],
        }
        ratio_metric.summary = (
            f"Feature-to-sample ratio score recalculated using the current p/n thresholds "
            f"({params['pn_low']:.0f}/{params['pn_moderate']:.0f}/{params['pn_high']:.0f}); "
            f"composite score {_v2_fmt_score(ratio_metric.score)}/100."
        )

    return report


def _v2_compute_readiness_score(report: Any, params: dict[str, float], source_availability: dict[str, object] | None = None) -> dict[str, Any]:
    tabular = _v2_metric_by_name(getattr(report, "schema_validation", []), "tabular_data_availability")
    if source_availability:
        n_with_data = int(
            (source_availability.get("datatable_count", 0) or 0)
            + (source_availability.get("mwtab_count", 0) or 0)
            + (source_availability.get("untarg_data_count", 0) or 0)
        )
    else:
        n_with_data = int((getattr(tabular, "details", {}) or {}).get("n_with_data", 0)) if tabular else 0
    g1 = {
        "id": "G1",
        "name": "tabular_data_availability",
        "status": "pass" if n_with_data > 0 else "fail",
        "value": n_with_data,
        "rule": ">= 1 usable assay matrix",
        "summary": f"{n_with_data} usable matrix/matrices found.",
    }

    min_sample = _v2_metric_by_name(getattr(report, "schema_validation", []), "minimum_sample_count")
    n_bio = int(((getattr(min_sample, "details", {}) or {}).get("n_biological_samples", 0)) if min_sample else 0)
    g2_pass = int(round(params["g2_sample_pass"]))
    g2_fail_below = int(round(params["g2_sample_fail_below"]))
    if n_bio >= g2_pass:
        g2_status = "pass"
    elif n_bio >= g2_fail_below:
        g2_status = "warn"
    else:
        g2_status = "fail"
    g2 = {
        "id": "G2",
        "name": "sufficient_biological_sample_count",
        "status": g2_status,
        "value": n_bio,
        "rule": f">= {g2_pass} pass; {g2_fail_below}-{g2_pass - 1} warn; < {g2_fail_below} fail",
        "summary": f"{n_bio} ML-eligible samples.",
    }

    endpoint = _v2_metric_by_name(getattr(report, "ml_readiness", []), "disease_endpoint_extractability")
    endpoint_details = getattr(endpoint, "details", {}) or {}
    n_groups = int(endpoint_details.get("distinct_label_groups", 0) or 0)
    g3 = {
        "id": "G3",
        "name": "deposited_groups",
        "status": "pass" if n_groups >= 2 else "fail",
        "value": n_groups,
        "rule": ">= 2 groups",
        "summary": f"{n_groups} distinct deposited groups.",
    }

    group_support = _v2_metric_by_name(getattr(report, "cohort_bias", []), "group_size_support")
    suitability = _v2_metric_by_name(getattr(report, "ml_readiness", []), "label_suitability")
    group_details = getattr(group_support, "details", {}) or {}
    suitability_details = getattr(suitability, "details", {}) or {}
    class_counts = group_details.get("counts") or suitability_details.get("counts", {}) or {}
    counts_dict = dict(class_counts) if hasattr(class_counts, "items") else {}
    min_class_n = int(group_details.get("min_group_size", 0) or 0) if group_details else 0
    if not min_class_n:
        min_class_n = int(min(counts_dict.values())) if counts_dict else 0
    class_pass = int(round(params["g4_class_pass"]))
    class_warn = int(round(params["g4_class_warn_min"]))
    if min_class_n >= class_pass and len(counts_dict) >= 2:
        g4_status = "pass"
    elif min_class_n >= class_warn and len(counts_dict) >= 2:
        g4_status = "warn"
    else:
        g4_status = "fail"
    g4 = {
        "id": "G4",
        "name": "minimum_per_group_support",
        "status": g4_status,
        "value": min_class_n,
        "rule": f"min class >= {class_pass} pass; {class_warn}-{class_pass - 1} warn; < {class_warn} fail",
        "summary": f"Smallest class has {min_class_n} samples across {len(counts_dict)} labeled groups.",
    }

    missingness = _v2_metric_by_name(getattr(report, "analytical_readiness", []), "missingness_structure")
    missing_details = getattr(missingness, "details", {}) or {}
    median_missing = missing_details.get("global_median_sample_missingness_rate")
    if median_missing is None:
        median_missing = 1.0
        g5_status = "warn"
    else:
        median_missing = float(median_missing)
        if median_missing <= params["g5_missing_pass_pct"] / 100.0:
            g5_status = "pass"
        elif median_missing <= params["g5_missing_fail_pct"] / 100.0:
            g5_status = "warn"
        else:
            g5_status = "fail"
    g5 = {
        "id": "G5",
        "name": "missingness_within_reuse_range",
        "status": g5_status,
        "value": float(median_missing),
        "rule": f"median sample missingness <= {params['g5_missing_pass_pct']:.0f}% pass; <={params['g5_missing_fail_pct']:.0f}% warn; >{params['g5_missing_fail_pct']:.0f}% fail",
        "summary": f"Median sample missingness {float(median_missing):.1%}.",
    }
    gates = [g1, g2, g3, g4, g5]
    gate_counts = {"pass": 0, "warn": 0, "fail": 0}
    for gate in gates:
        gate_counts[str(gate["status"])] += 1

    sections = {
        "structural": _v2_mean_score(getattr(report, "schema_validation", []), _V2_SECTION_METRIC_COUNTS["structural"]),
        "metadata": _v2_mean_score(getattr(report, "metadata_readiness", []), _V2_SECTION_METRIC_COUNTS["metadata"]),
        "analytical": _v2_mean_score(getattr(report, "analytical_readiness", []), _V2_SECTION_METRIC_COUNTS["analytical"]),
        "annotation": _v2_mean_score(getattr(report, "annotation_readiness", []), _V2_SECTION_METRIC_COUNTS["annotation"]),
        "cohort": _v2_mean_score(getattr(report, "cohort_bias", []), _V2_SECTION_METRIC_COUNTS["cohort"]),
        "ml_feasibility": _v2_mean_score(
            [m for m in (getattr(report, "ml_readiness", []) or []) if getattr(m, "name", "") in _V2_ML_SCORING_METRICS],
            _V2_SECTION_METRIC_COUNTS["ml_feasibility"],
        ),
    }
    core_score = sum(sections[key] for key in _V2_CORE_SECTION_KEYS) / len(_V2_CORE_SECTION_KEYS)
    reusability_score = sections["metadata"]
    provisional_band = _v2_band_from_score(core_score, params)
    if g1["status"] == "fail":
        gate_ceiling = "No Data"
        final_band = "No Data"
        reported_core = 0.0
    elif gate_counts["fail"] > 0:
        gate_ceiling = "Not Ready"
        final_band = _v2_cap_band(provisional_band, gate_ceiling)
        reported_core = core_score
    elif gate_counts["warn"] > 0:
        gate_ceiling = "Conditional"
        final_band = _v2_cap_band(provisional_band, gate_ceiling)
        reported_core = core_score
    else:
        gate_ceiling = None
        final_band = provisional_band
        reported_core = core_score

    if final_band == "No Data":
        recommendation = "Metadata is available, but no usable feature matrix is available for core ML readiness."
    elif final_band == "Ready":
        recommendation = "This deposited source is ML-ready under the current parameter profile."
    elif final_band == "Conditional":
        recommendation = "This deposited source is ML-ready with caveats under the current parameter profile."
    elif final_band == "Fragile":
        recommendation = "This deposited source is best treated as exploratory ML use under the current parameter profile."
    else:
        recommendation = "This deposited source is class-support limited under the current parameter profile."

    weak_sections = [name for name, value in sections.items() if value < params["band_conditional_min"]]
    actions: list[str] = []
    if gate_counts["fail"] > 0:
        actions.append("Review feasibility gates that limit the final band before relying on automated supervised-ML reuse.")
    elif gate_counts["warn"] > 0:
        actions.append("Review gate warnings; they can limit the final band even when section scores are strong.")
    if "cohort" in weak_sections or "ml_feasibility" in weak_sections:
        actions.append("For supervised-classification reuse, consider whether class support, label balance, and train/test split feasibility are sufficient.")
    if "analytical" in weak_sections:
        actions.append("Review missingness, outlier burden, redundancy, and assay comparability before supervised ML reuse.")
    if "annotation" in weak_sections:
        actions.append("Annotation coverage may limit interpretability and cross-study reuse.")
    if not actions:
        actions.append("Proceed to external validation and holdout analysis.")

    return {
        "score": round(reported_core, 3),
        "band": final_band,
        "core_ml_readiness_score": round(reported_core, 3),
        "raw_core_ml_readiness_score": round(core_score, 3),
        "reusability_score": round(reusability_score, 3),
        "section_scores": {k: round(v, 3) for k, v in sections.items()},
        "final_band": final_band,
        "final_band_label": _v2_band_label(final_band),
        "provisional_band": provisional_band,
        "provisional_band_label": _v2_band_label(provisional_band),
        "gate_ceiling": gate_ceiling,
        "gate_ceiling_label": _v2_band_label(gate_ceiling) if gate_ceiling else None,
        "gate_summary": gate_counts,
        "gates": gates,
        "recommendation": recommendation,
        "actions": actions,
        "status_note": "Scoring thresholds were adjusted for this browser session; source parsing is unchanged.",
        "v2_scoring_params": dict(params),
    }


def _v2_apply_scoring_profile(
    state: dict[str, Any] | None,
    params: dict[str, float],
    matrix_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if state is None:
        return None
    new_state = copy.deepcopy(state)
    source_availability = new_state.get("source_availability") or {}
    matrix_overrides = matrix_overrides or {}
    embedded_matrix_model = (
        new_state.get("v2_sample_matrix_model")
        if isinstance(new_state.get("v2_sample_matrix_model"), dict)
        else {}
    )
    matrix_model = _v2_build_sample_matrix_model(new_state)
    if not matrix_model.get("dump_available") and embedded_matrix_model.get("samples"):
        # Vercel does not have the local confirmation dump, so use the
        # cache-embedded sample model when present.
        matrix_model = copy.deepcopy(embedded_matrix_model)
    new_state["v2_sample_matrix_model"] = matrix_model
    new_state["v2_matrix_overrides"] = matrix_overrides

    final_report = new_state.get("final_report")
    if final_report is not None:
        if matrix_overrides:
            final_report = _v2_apply_matrix_overrides_to_report(
                final_report,
                matrix_model,
                matrix_overrides,
                str(new_state.get("primary_source") or ""),
            )
        final_report = _v2_adjust_report_metrics(final_report, params)
        new_state["final_report"] = final_report
        new_state["readiness_score"] = _v2_compute_readiness_score(final_report, params, source_availability=source_availability)

    initial_report = new_state.get("initial_report")
    if initial_report is not None:
        if matrix_overrides:
            initial_report = _v2_apply_matrix_overrides_to_report(
                initial_report,
                matrix_model,
                matrix_overrides,
                str(new_state.get("primary_source") or ""),
            )
        new_state["initial_report"] = _v2_adjust_report_metrics(initial_report, params)

    source_assessments = new_state.get("source_assessments") or {}
    for _source_name, item in source_assessments.items():
        if not item or not item.get("_report"):
            continue
        report = item["_report"]
        if matrix_overrides:
            report = _v2_apply_matrix_overrides_to_report(report, matrix_model, matrix_overrides, str(_source_name))
        report = _v2_adjust_report_metrics(report, params)
        item["_report"] = report
        item["readiness_score"] = _v2_compute_readiness_score(report, params, source_availability=source_availability)

    new_state["v2_scoring_params"] = dict(params)
    # Force downloads from v2 to reflect the rendered experimental profile
    # rather than the untouched cached payload.
    new_state.pop("__raw_state_payload", None)
    return new_state


def _checks_list_html(checks: dict[str, bool]) -> str:
    """Render a dict of {label: bool} as a green-tick / red-cross checklist."""
    items = []
    for field, ok in checks.items():
        mark = "✓" if ok else "✗"
        fg = "#196b4a" if ok else "#8f2d2d"
        label_text = field.replace("_", " ").title()
        items.append(
            f"<div style='display:flex;align-items:center;gap:6px;padding:2px 0'>"
            f"<span style='font-weight:700;color:{fg};min-width:14px;flex-shrink:0'>{mark}</span>"
            f"<span style='color:{fg}'>{_e(label_text)}</span>"
            f"</div>"
        )
    return "".join(items)


def _metric_info_tooltip(
    m: Any,
    params: dict[str, float] | None = None,
    study_id: Any | None = None,
) -> str:
    """Return the inner HTML for the metric's ⓘ hover tooltip, or '' if not applicable."""
    params = params or _V2_DEFAULT_PARAMS
    details = m.details or {}
    verify_button_compact = _verify_workbench_button(study_id, compact=True)

    # schema_integrity + FAIR study checklist metrics: generic "checks" dict
    if m.name in ("schema_integrity", "fair_metadata_coverage", "fair_study_metadata_compliance"):
        checks = details.get("checks")
        if checks and isinstance(checks, dict):
            if m.name != "fair_study_metadata_compliance":
                html = (
                    "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Sub-checks</div>"
                    + _checks_list_html(checks)
                )
            if m.name == "fair_study_metadata_compliance":
                evidence = details.get("field_evidence", {}) if isinstance(details, dict) else {}
                checks_dict = checks if isinstance(checks, dict) else {}
                if isinstance(evidence, dict):
                    desc_words = evidence.get("description_words", 0)
                    desc_threshold = evidence.get("description_threshold", 20)
                    raw_formats = evidence.get("raw_data_formats", [])

                    # Each row: (check_key, label, value_html)
                    def _val(v: str, truncate: int = 110, verify_missing: bool = False) -> str:
                        v = v.strip()
                        if not v:
                            suffix = verify_button_compact if verify_missing else ""
                            return f"<span style='color:#c0392b;font-style:italic'>missing</span>{suffix}"
                        display = _e(v[:truncate] + ("…" if len(v) > truncate else ""))
                        return f"<span style='color:#1a7a4a;overflow-wrap:anywhere'>{display}</span>"

                    rows = [
                        ("f1_doi_registered",            "DOI",               _val(str(evidence.get("doi", "") or ""), verify_missing=True)),
                        ("r1_2_linked_publication",      "Publication",       _val(str(evidence.get("publications", "") or ""), verify_missing=True)),
                        ("r1_funding_source_declared",   "Funding source",    _val(str(evidence.get("funding_source", "") or ""))),
                        ("r1_contributors_listed",       "Contributors",      _val(str(evidence.get("contributors", "") or ""))),
                        ("r1_study_type_declared",       "Project type / experimental design", _val(str(evidence.get("project_type", "") or ""))),
                        ("f2_substantive_description",   "Description",
                            (f"<span style='color:#1a7a4a'>{desc_words} words</span>"
                             if desc_words >= desc_threshold
                             else f"<span style='color:#c0392b;font-style:italic'>{desc_words} words (need ≥{desc_threshold})</span>")),
                        ("a1_1_raw_data_format_recorded", "Raw data format",
                            (_val(", ".join(
                                "raw files linked (no extension)" if f == "raw_files_linked" else f
                                for f in raw_formats
                            ))
                             if raw_formats
                             else "<span style='color:#c0392b;font-style:italic'>missing</span>")),
                    ]

                    check_rows = ""
                    for check_key, label, val_html in rows:
                        passed = bool(checks_dict.get(check_key, False))
                        icon = "✓" if passed else "✗"
                        icon_color = "#1a7a4a" if passed else "#c0392b"
                        check_rows += (
                            "<div class='fair-check-row'>"
                            f"<span style='color:{icon_color};font-weight:800'>{icon}</span>"
                            f"<span class='fair-check-label'>{_e(label)}</span>"
                            f"<span class='fair-check-value'>{val_html}</span>"
                            "</div>"
                        )
                    html = (
                        "<div style='font-weight:600;margin-bottom:6px;color:#132327'>"
                        f"FAIR checks: {details.get('passed', 0)}/{details.get('total', 7)} passed"
                        "</div>"
                        f"<div class='fair-check-grid'>{check_rows}</div>"
                    )
                    return html
            elif m.name == "fair_metadata_coverage":
                evidence = details.get("field_evidence", {}) if isinstance(details, dict) else {}
                if isinstance(evidence, dict):
                    submit_date = str(evidence.get("submission_date", "") or "").strip() or "—"
                    release_date = str(evidence.get("release_date", "") or "").strip() or "—"
                    publication_date = str(evidence.get("publication_date", "") or "").strip() or "—"
                    temporal_scored = bool(evidence.get("temporal_metadata_scored", False))
                    html += (
                        "<div style='font-weight:600;margin:8px 0 4px;color:#132327'>Temporal Metadata</div>"
                        "<div style='font-size:.78rem;color:#132327;line-height:1.45'>"
                        f"Submission date: <strong>{_e(submit_date)}</strong><br>"
                        f"Release date: <strong>{_e(release_date)}</strong><br>"
                        f"Publication date: <strong>{_e(publication_date)}</strong><br>"
                        f"Used in FAIR score: <strong>{'Yes' if temporal_scored else 'No (informational only)'}</strong>"
                        "</div>"
                    )
            return html

    if m.name == "mass_rt_like_metadata_presence":
        present = bool(details.get("present", False)) if isinstance(details, dict) else False
        fields = details.get("fields", []) if isinstance(details, dict) else []
        field_classes = details.get("field_classes", {}) if isinstance(details, dict) else {}
        examples = details.get("examples", []) if isinstance(details, dict) else []
        prevalence = details.get("repository_prevalence", {}) if isinstance(details, dict) else {}
        present_studies = int(prevalence.get("present_studies", 1833) or 1833)
        total_studies = int(prevalence.get("total_studies", 4121) or 4121)
        present_percent = float(prevalence.get("present_percent", 44.49) or 44.49)
        status_color = "#1a7a4a" if present else "#c0392b"
        status_label = "present" if present else "not detected"

        field_bits = ""
        if isinstance(fields, list) and fields:
            field_bits = "".join(
                "<span style='display:inline-block;margin:2px 4px 2px 0;padding:2px 6px;border-radius:999px;"
                "background:rgba(19,35,39,.07);color:#132327'>"
                f"{_e(str(field))}"
                f"<span style='color:#51656a'> ({_e(str(field_classes.get(field, 'like')) if isinstance(field_classes, dict) else 'like')})</span>"
                "</span>"
                for field in fields[:10]
            )
            if len(fields) > 10:
                field_bits += f"<span style='color:#51656a'>+{len(fields) - 10} more</span>"

        example_rows = ""
        if isinstance(examples, list) and examples:
            for item in examples[:4]:
                if not isinstance(item, dict):
                    continue
                example_rows += (
                    "<tr>"
                    "<td style='padding:2px 8px 2px 0;color:#132327;white-space:nowrap'>"
                    f"{_e(str(item.get('field_name', '')))}</td>"
                    "<td style='padding:2px 8px 2px 0;color:#51656a;white-space:nowrap'>"
                    f"{_e(str(item.get('field_class', '')))}</td>"
                    "<td style='padding:2px 0;color:#132327;word-break:break-word'>"
                    f"{_e(str(item.get('example_value', '')))}</td>"
                    "</tr>"
                )

        source_text = str(details.get("source", "mwtab Metabolites metadata") if isinstance(details, dict) else "mwtab Metabolites metadata")
        files_scanned = details.get("files_scanned", 0) if isinstance(details, dict) else 0
        blocks_scanned = details.get("blocks_scanned", 0) if isinstance(details, dict) else 0
        rt_meta = details.get("rt_units_ms_results_file_metadata", {}) if isinstance(details, dict) else {}
        rt_values = rt_meta.get("rt_units_values", []) if isinstance(rt_meta, dict) else []
        rt_values_text = ", ".join(str(v) for v in rt_values if str(v).strip())
        if rt_values_text:
            rt_units_html = (
                "<div style='font-weight:600;margin:8px 0 4px;color:#132327'>RT unit metadata</div>"
                f"<div style='color:#132327;font-size:.76rem;line-height:1.45'>"
                f"{_e(str(rt_meta.get('label', 'RT units reported in mwTab MS_RESULTS_FILE metadata')))}: "
                f"<strong>{_e(rt_values_text)}</strong></div>"
            )
        else:
            rt_units_html = (
                "<div style='font-weight:600;margin:8px 0 4px;color:#132327'>RT unit metadata</div>"
                "<div style='color:#51656a;font-size:.76rem;line-height:1.45'>"
                f"RT units not available in mwTab MS_RESULTS_FILE metadata.{verify_button_compact}</div>"
            )
        html = (
            "<div style='font-weight:600;margin-bottom:6px;color:#132327'>"
            "Mass/RT-like metabolite metadata"
            "</div>"
            f"<div style='color:#132327;padding:2px 0'>Status: "
            f"<strong style='color:{status_color}'>{_e(status_label)}</strong>"
            f"{verify_button_compact if not present else ''}</div>"
            f"<div style='color:#51656a;font-size:.78rem;line-height:1.45;padding-top:4px'>"
            f"Source: {_e(source_text)}. Scanned {_e(str(files_scanned))} mwTab JSON file(s) "
            f"and {_e(str(blocks_scanned))} Metabolites block(s). "
            f"Repository prevalence: {present_studies:,}/{total_studies:,} studies ({present_percent:.2f}%)."
            "</div>"
            f"{rt_units_html}"
        )
        if field_bits:
            html += (
                "<div style='font-weight:600;margin:8px 0 4px;color:#132327'>Detected field names</div>"
                f"<div style='font-size:.76rem;line-height:1.5'>{field_bits}</div>"
            )
        if example_rows:
            html += (
                "<div style='font-weight:600;margin:8px 0 4px;color:#132327'>Examples</div>"
                f"<table style='font-size:.76rem;border-collapse:collapse;width:100%'>{example_rows}</table>"
            )
        return html

    if m.name == "metabatch_batch_annotation_compatibility":
        rules = details.get("rules", {}) if isinstance(details, dict) else {}
        source_rule = str(rules.get("source", "StdMW/MetaBatch-style Metabolomics Workbench allfactors to batches.tsv filtering") or "")
        return (
            "<div style='font-weight:600;margin-bottom:6px;color:#132327'>"
            "MetaBatch annotation compatibility"
            "</div>"
            "<div style='color:#132327;font-size:.78rem;line-height:1.45'>"
            "This metric asks whether Metabolomics Workbench factor annotations can be converted into a "
            "MetaBatch-style batch/covariate table for the samples in the active feature matrix. "
            "It uses the StdMW/MetaBatch filtering rules for factor usability: at least two "
            "non-empty levels, at least 60% sample coverage, and not more than 90% distinct values."
            "</div>"
            "<div style='color:#51656a;font-size:.78rem;line-height:1.45;margin-top:6px'>"
            f"Rule source: {_e(source_rule)}. "
            "The separate <strong>technical-like key</strong> flag is MERIT-ML-specific: it scans factor "
            "names and values for explicit batch/run/order/plate/injection/acquisition-like text, so "
            "generic biological factors are not overinterpreted as technical batch metadata."
            "</div>"
            "<div style='margin-top:6px;font-size:.78rem'>"
            "<a href='https://bioinformatics.mdanderson.org/public-software/metabatch/' "
            "target='_blank' rel='noopener noreferrer' style='color:#0d6e6e;font-weight:700'>"
            "Original MetaBatch tool</a>"
            "</div>"
        )

    if m.name == "factor_label_harmonizability":
        lq = details.get("label_quality")
        simp = details.get("simplicity")
        avg_pipes = details.get("avg_pipe_count", 0)
        n_dims = details.get("n_factor_dimensions", 1)
        examples = details.get("example_factor_strings", [])
        discrepancies = details.get("endpoint_discrepancy_count", 0)
        if lq is not None and simp is not None:
            try:
                lq_pct = f"{float(lq) * 100:.1f}%"
            except Exception:
                lq_pct = "—"
            try:
                simp_val = _score_100_text(simp, 1)
            except Exception:
                simp_val = "—"

            _td = "padding:3px 10px 3px 0;vertical-align:top;white-space:nowrap;color:#132327;font-size:.78rem;font-weight:600"
            _td_val = "padding:3px 0;vertical-align:top;color:#132327;font-size:.78rem"

            def _score_color(val: float) -> str:
                return "#1a7a4a" if val >= 0.75 else ("#c07000" if val >= 0.4 else "#c0392b")

            lq_f = float(lq)
            simp_f = float(simp)

            # 1-2 example factor strings shown verbatim with header
            ex_html = ""
            if examples:
                n_ex = min(len(examples), 2)
                ex_html = (
                    f"<div style='margin-top:5px;font-size:.74rem;font-weight:600;color:#132327'>"
                    f"Example factor variable{'s' if n_ex > 1 else ''} ({n_ex}):</div>"
                )
                for ex in examples[:2]:
                    truncated = ex[:80] + ("…" if len(ex) > 80 else "")
                    ex_html += (
                        f"<div style='color:#51656a;font-size:.74rem;font-family:monospace;"
                        f"margin-top:2px;padding-left:8px;word-break:break-all'>"
                        f"• {_e(truncated)}</div>"
                    )

            rows_html = (
                f"<tr><td style='{_td}'>Label quality</td>"
                f"<td style='{_td_val}'><span style='color:{_score_color(lq_f)};font-weight:700'>{lq_pct}</span>"
                f"<span style='color:#51656a;font-weight:400'> fraction of samples with valid label</span></td></tr>"
                f"<tr><td style='{_td}'>Simplicity</td>"
                f"<td style='{_td_val}'>"
                f"<span style='color:{_score_color(simp_f)};font-weight:700'>{simp_val}</span>"
                f"<span style='color:#51656a;font-weight:400'> — {n_dims} dimension(s), avg {avg_pipes:.1f} pipe(s)"
                f" &nbsp;[1→100 · 2→70 · 3→40 · ≥4→10]</span>"
                f"{ex_html}</td></tr>"
            )
            if discrepancies:
                rows_html += (
                    f"<tr><td style='{_td}'>Discrepancies</td>"
                    f"<td style='{_td_val}'><span style='color:#c0392b'>{discrepancies} sample(s) differ between tabular and factors endpoint</span></td></tr>"
                )
            return (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>"
                "Score (0-100) = 0.5 × label_quality% + 0.5 × simplicity score"
                "</div>"
                f"<table style='font-size:.78rem;border-collapse:collapse;width:100%'>{rows_html}</table>"
            )

    if m.name == "label_entropy":
        counts = details.get("counts", {}) if isinstance(details, dict) else {}
        n_classes = details.get("n_classes")
        entropy = details.get("entropy")
        entropy_max = details.get("entropy_max")
        entropy_norm = details.get("entropy_norm")
        try:
            entropy_text = f"{float(entropy):.4f}"
        except Exception:
            entropy_text = "—"
        try:
            entropy_max_text = f"{float(entropy_max):.4f}"
        except Exception:
            entropy_max_text = "—"
        try:
            entropy_norm_text = _score_100_text(entropy_norm, 1)
        except Exception:
            entropy_norm_text = "—"
        n_classes_text = str(n_classes) if isinstance(n_classes, int) else "—"
        top_counts = ""
        if isinstance(counts, dict) and counts:
            sorted_counts = sorted(
                ((str(k), int(v)) for k, v in counts.items()),
                key=lambda item: (-item[1], item[0]),
            )[:6]
            rows = "".join(
                "<div style='display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 0'>"
                f"<span style='color:#132327'>{_e(k)}</span>"
                f"<span style='font-weight:700;color:#113e52'>{_e(str(v))}</span>"
                "</div>"
                for k, v in sorted_counts
            )
            top_counts = (
                "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Class Counts (Top)</div>"
                + rows
            )
        return (
            "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Entropy Calculation</div>"
            "<div style='color:#132327;padding:2px 0'><strong>Formula:</strong> "
            "H = -sum(p<sub>i</sub> * ln(p<sub>i</sub>)), H<sub>max</sub> = ln(K), "
            "score = 100 × H / H<sub>max</sub></div>"
            f"<div style='color:#132327;padding:2px 0'>K (classes): <strong>{_e(n_classes_text)}</strong>; "
            f"H: <strong>{_e(entropy_text)}</strong>; H_max: <strong>{_e(entropy_max_text)}</strong>; "
            f"normalized entropy score: <strong>{_e(entropy_norm_text)}</strong></div>"
            "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
            "Label-structure section score is shown on the 0-100 display scale."
            "</div>"
            f"{top_counts}"
        )

    if m.name == "feature_correlation_burden":
        high_corr = details.get("high_correlation_pairs")
        sampled = details.get("sampled_pairs")
        if isinstance(high_corr, int) and isinstance(sampled, int):
            pct = (high_corr / sampled * 100.0) if sampled > 0 else 0.0
            return (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Quick Details</div>"
                f"<div style='color:#132327;padding:2px 0'>Highly correlated pairs (|r| >= 0.95): "
                f"<strong>{_e(str(high_corr))}</strong> / {_e(str(sampled))} ({pct:.1f}%)</div>"
                "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
                "High values suggest redundant feature blocks that can inflate model confidence."
                "</div>"
            )

    if m.name == "assay_platform_comparability":
        spread = details.get("spread_log10_median")
        n_usable = details.get("n_usable_analyses")
        try:
            spread_text = f"{float(spread):.4f}"
        except Exception:
            spread_text = "—"
        try:
            score_text = _score_100_text(m.score, 1)
        except Exception:
            score_text = "—"
        return (
            "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Cross-Analysis Value-Scale Check</div>"
            "<div style='color:#132327;padding:2px 0'>This metric compares analyses using the spread of median "
            "log10 abundance values after excluding missing, non-finite, and non-positive entries.</div>"
            "<div style='color:#132327;padding:2px 0'><strong>Spread definition:</strong> "
            "spread = max(per-analysis log10 median) − min(per-analysis log10 median).</div>"
            f"<div style='color:#132327;padding:2px 0'>Usable analyses: <strong>{_e(str(n_usable if n_usable is not None else '—'))}</strong>; "
            f"spread: <strong>{_e(spread_text)}</strong> log10 units.</div>"
            f"<div style='color:#132327;padding:2px 0'><strong>Scoring formula:</strong> "
            f"score = 100 / (1 + spread), giving <strong>{_e(score_text)}</strong> for this metric.</div>"
            "<div style='color:#132327;padding:2px 0'><strong>Status rule:</strong> pass if score ≥ 50, else warn.</div>"
            "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
            "<strong>Positive values</strong> = matrix cells used for this metric after filtering to values that are "
            "(1) not missing or non-finite and (2) strictly greater than 0."
            "</div>"
        )

    if m.name == "missingness_structure":
        n_total_samples = details.get("n_total_samples")
        mean_sample_rate = details.get("mean_sample_missingness_rate")
        median_sample_rate = details.get("median_sample_missingness_rate")
        class_gap = details.get("class_dependent_gap_weighted")
        semantics = str(details.get("missing_semantics", "") or "").strip()
        parser_note = str(details.get("parser_note", "") or "").strip()
        token_hints = details.get("parser_token_hints", [])
        sample_top = details.get("sample_missing_top10_global", [])
        if n_total_samples is not None:
            try:
                sample_text = f"{float(mean_sample_rate) * 100:.1f}%"
            except Exception:
                sample_text = "—"
            try:
                sample_median_text = f"{float(median_sample_rate) * 100:.1f}%"
            except Exception:
                sample_median_text = "—"
            try:
                gap_text = f"{float(class_gap) * 100:.1f}%"
            except Exception:
                gap_text = "—"
            hint_text = ", ".join(str(token).strip() for token in token_hints if str(token).strip()) or "—"
            gap_warn = ""
            try:
                if float(class_gap) >= 0.1:
                    gap_warn = " <span style='color:#c0392b;font-weight:700'>warning</span>"
            except Exception:
                pass
            html = (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Sample-Level Missingness</div>"
                f"<div style='color:#132327;padding:2px 0'>Total samples: <strong>{_e(str(n_total_samples))}</strong></div>"
                f"<div style='color:#132327;padding:2px 0'>Median sample-level missingness: <strong>{_e(sample_median_text)}</strong>  (per-analysis score = 100 × [1 − this value])</div>"
                f"<div style='color:#132327;padding:2px 0'>Mean sample-level missingness: <strong>{_e(sample_text)}</strong></div>"
                f"<div style='color:#132327;padding:2px 0'>Class-dependent missingness gap: <strong>{_e(gap_text)}</strong>{gap_warn}</div>"
                "<div style='color:#51656a;font-size:.78rem;padding:4px 0 2px'>"
                "<strong style='color:#132327'>How the score is calculated:</strong> "
                "For each ML-eligible sample (QC/blank/pool/reference excluded), compute the fraction of features that are missing. "
                "Per-analysis score = 100 × [1 − median(per-sample missingness rates)]. "
                "Aggregate score = mean of per-analysis scores."
                "</div>"
                f"<div style='color:#51656a;font-size:.78rem;padding:2px 0'>{_e(semantics or 'Missing = empty/non-numeric or non-finite values after ingestion cleanup.')}</div>"
                f"<div style='color:#51656a;font-size:.78rem;padding:2px 0'>{_e(parser_note or 'Ingestion cleanup treats blank/non-numeric abundance tokens as missing before metrics run.')}</div>"
                f"<div style='color:#51656a;font-size:.78rem;padding-top:4px'>Typical raw tokens mapped to missing: {_e(hint_text)}</div>"
                "<div style='font-size:.76rem;color:#51656a;background:rgba(13,110,110,.06);border-radius:8px;"
                "padding:5px 8px;margin-top:6px;line-height:1.4'>"
                "<strong>Source-aware zero handling:</strong> "
                "datatable zeros are treated as valid (curated structural fill); "
                "mwTab/untarg_data zeros are treated as missing (below detection). "
                "Empirical basis: 73.9% of retained explicit mwTab missing tokens map to datatable zero."
                "</div>"
            )
            if isinstance(sample_top, list) and sample_top:
                sample_rows = []
                for rec in sample_top[:10]:
                    if not isinstance(rec, dict):
                        continue
                    try:
                        missing_rate_value = float(rec.get("missing_rate", 0.0))
                    except Exception:
                        missing_rate_value = 0.0
                    if missing_rate_value <= 0.0:
                        continue
                    aid = _analysis_id_label(rec.get("analysis_id", ""))
                    sid = str(rec.get("sample_id", "") or "").strip() or "—"
                    try:
                        rate = f"{missing_rate_value * 100:.1f}%"
                    except Exception:
                        rate = "—"
                    sample_rows.append(
                        "<div style='display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 0'>"
                        f"<span style='color:#132327;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' title='{_e(sid)}'>{_e(aid)} :: {_e(sid)}</span>"
                        f"<span style='font-weight:700;color:#113e52'>{_e(rate)}</span>"
                        "</div>"
                    )
                if sample_rows:
                    html += (
                        "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Highest-Missing Samples (Top 10)</div>"
                        + "".join(sample_rows)
                    )
            return html

    if m.name == "scale_diagnostics":
        median_v = details.get("median")
        p90_v = details.get("p90")
        min_v = details.get("min")
        max_v = details.get("max")
        ratio = details.get("median_to_p90_ratio")
        log10_med = details.get("log10_median_intensity")
        status_label = str(details.get("status", "") or "").strip() or "unknown"
        median_text = _fmt_sci(median_v, 3)
        p90_text = _fmt_sci(p90_v, 3)
        min_text = _fmt_sci(min_v, 3)
        max_text = _fmt_sci(max_v, 3)
        try:
            ratio_text = f"{float(ratio):.4f}"
        except Exception:
            ratio_text = "—"
        try:
            log10_med_text = f"{float(log10_med):.3f}"
        except Exception:
            log10_med_text = "—"
        per_analysis = details.get("per_analysis")
        declared_units: list[str] = []
        if isinstance(per_analysis, list):
            seen_units: set[str] = set()
            for item in per_analysis:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("declared_units", "") or "").strip() or "unknown"
                if text not in seen_units:
                    seen_units.add(text)
                    declared_units.append(text)
        units_text = ", ".join(declared_units) if declared_units else "unknown"
        units_verify = verify_button_compact if "unknown" in {u.casefold() for u in declared_units} or units_text == "unknown" else ""
        return (
            "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Global Value Distribution</div>"
            f"<div style='color:#132327;padding:2px 0'>Status: <strong>{_e(status_label)}</strong></div>"
            f"<div style='color:#132327;padding:2px 0'>Declared value scale (mwTab JSON units): <strong>{_e(units_text)}</strong>{units_verify}</div>"
            f"<div style='color:#132327;padding:2px 0'>min={_e(min_text)}, median={_e(median_text)}, p90={_e(p90_text)}, max={_e(max_text)}</div>"
            f"<div style='color:#132327;padding:2px 0'>median/p90 ratio: <strong>{_e(ratio_text)}</strong></div>"
            f"<div style='color:#132327;padding:2px 0'>log&#8321;&#8320;(global median intensity): <strong>{_e(log10_med_text)}</strong></div>"
            "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
            "Scale diagnostics are inferred from numeric distribution (min/median/p90/max), not directly from the declared units text. "
            "Low median relative to p90 suggests many values near LOD/LOQ; interpret weak features cautiously. "
            "This is informational only and does not contribute to the readiness score."
            "</div>"
            "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
            "P50/P90 heuristic bands (interpretive only): <= 0.05 very low, 0.05-0.15 low, 0.15-0.35 medium, > 0.35 high. "
            "Low ratios indicate stronger right-skew; high ratios indicate a more compressed distribution."
            "</div>"
        )

    if m.name == "outlier_burden":
        def _as_int(value: Any) -> int | None:
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            return None

        per_analysis = details.get("per_analysis", [])
        sample_outliers = _as_int(details.get("sample_outliers"))
        sample_total = _as_int(details.get("sample_total"))
        if (sample_outliers is None or sample_total is None) and isinstance(per_analysis, list):
            sample_outliers = sum(_as_int(item.get("sample_outliers")) or 0 for item in per_analysis if isinstance(item, dict))
            sample_total = sum(_as_int(item.get("sample_total")) or 0 for item in per_analysis if isinstance(item, dict))
        formula = str(details.get("formula", "") or "").strip()
        outlier_samples_top = details.get("outlier_samples_top50", [])
        if isinstance(sample_outliers, int) and isinstance(sample_total, int):
            sample_pct = (sample_outliers / sample_total * 100.0) if sample_total > 0 else 0.0
            sample_component = 1.0 - (sample_outliers / sample_total) if sample_total > 0 else 1.0
            sample_component_display = sample_component * 100.0
            html = (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Outlier Formula</div>"
                f"<div style='color:#132327;padding:2px 0'>{_e(formula or 'Sample-level only: per-sample median intensity -> Tukey 1.5×IQR fences across samples; score = 100 × [1 - sample_outlier_rate].')}</div>"
                f"<div style='color:#132327;padding:2px 0'>Sample-level outliers: <strong>{_e(str(sample_outliers))}</strong> / {_e(str(sample_total))} ({sample_pct:.1f}%)</div>"
                f"<div style='color:#132327;padding:2px 0'>Sample component: <strong>100 × [1 - ({_e(str(sample_outliers))}/{_e(str(sample_total))})] = {sample_component_display:.1f}</strong></div>"
                "<div style='color:#51656a;font-size:.78rem;padding:2px 0'>"
                "How to read this: "
                "<strong>IQR</strong> is computed as <strong>Q3 - Q1</strong>; outliers are values below <strong>Q1 - 1.5×IQR</strong> or above <strong>Q3 + 1.5×IQR</strong>. "
                "<strong>Sample-level</strong> uses one value per sample (its median intensity) with these limits. "
                "Higher sample-outlier burden indicates worse analytical quality and therefore a lower outlier score."
                "</div>"
            )
            if isinstance(outlier_samples_top, list) and outlier_samples_top:
                sample_rows = []
                for rec in outlier_samples_top[:8]:
                    if not isinstance(rec, dict):
                        continue
                    analysis_id = _analysis_id_label(str(rec.get("analysis_id", "") or "").strip()) or "—"
                    sample_id = str(rec.get("sample_id", "") or "").strip() or "—"
                    sample_rows.append(
                        "<div style='display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 0'>"
                        f"<span style='color:#132327'>{_e(analysis_id)} :: {_e(sample_id)}</span>"
                        "<span style='font-weight:700;color:#113e52'>sample</span>"
                        "</div>"
                    )
                if sample_rows:
                    html += (
                        "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Outlier Samples (Top)</div>"
                        + "".join(sample_rows)
                    )
            return html

    if m.name == "feature_level_missingness":
        threshold = details.get("threshold")
        total_features = details.get("n_total_features")
        high_missing = details.get("n_high_missing_features")
        mean_rate = details.get("mean_missingness_rate")
        median_rate = details.get("median_missingness_rate")
        semantics = str(details.get("missing_semantics", "") or "").strip()
        parser_note = str(details.get("parser_note", "") or "").strip()
        token_hints = details.get("parser_token_hints", [])
        if isinstance(total_features, int) and isinstance(high_missing, int):
            try:
                threshold_text = f"{float(threshold) * 100:.0f}%"
            except Exception:
                threshold_text = "30%"
            try:
                mean_text = f"{float(mean_rate) * 100:.1f}%"
            except Exception:
                mean_text = "—"
            try:
                median_text = f"{float(median_rate) * 100:.1f}%"
            except Exception:
                median_text = "—"
            hint_text = ", ".join(str(token).strip() for token in token_hints if str(token).strip()) or "—"
            return (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Feature-Level Missingness</div>"
                f"<div style='color:#132327;padding:2px 0'>Flag threshold: <strong>{_e(threshold_text)}</strong> missing per feature.</div>"
                f"<div style='color:#132327;padding:2px 0'>Flagged features: <strong>{_e(str(high_missing))}</strong> / {_e(str(total_features))}</div>"
                f"<div style='color:#132327;padding:2px 0'>Mean per-feature missingness: <strong>{_e(mean_text)}</strong> (score = 100 × [1 − mean rate])</div>"
                f"<div style='color:#132327;padding:2px 0'>Median per-feature missingness: <strong>{_e(median_text)}</strong> (diagnostic only)</div>"
                f"<div style='color:#51656a;font-size:.78rem;padding:2px 0'>{_e(semantics or 'Missing = empty/non-numeric or non-finite values after ingestion cleanup.')}</div>"
                f"<div style='color:#51656a;font-size:.78rem;padding:2px 0'>{_e(parser_note or 'Ingestion cleanup treats blank/non-numeric abundance tokens as missing before metrics run.')}</div>"
                f"<div style='color:#51656a;font-size:.78rem;padding-top:4px'>Typical raw tokens mapped to missing: {_e(hint_text)}</div>"
            )

    if m.name == "sample_type_confounding_risk":
        cramers_v = details.get("cramers_v")
        unique_labels = details.get("unique_labels")
        unique_markers = details.get("unique_markers")
        dominance = details.get("dominant_marker_fraction_by_label", {})
        rows = []
        if isinstance(dominance, dict) and dominance:
            for label, frac in sorted(dominance.items(), key=lambda item: str(item[0])):
                try:
                    pct = f"{float(frac) * 100:.1f}%"
                except Exception:
                    pct = str(frac)
                rows.append(
                    "<div style='display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 0'>"
                    f"<span style='color:#132327'>{_e(str(label))}</span>"
                    f"<span style='font-weight:700;color:#113e52'>{_e(pct)}</span>"
                    "</div>"
                )
        if rows or cramers_v is not None:
            crv_text = "—"
            try:
                crv_text = f"{float(cramers_v):.3f}"
            except Exception:
                if cramers_v is not None:
                    crv_text = str(cramers_v)
            ul = str(unique_labels) if isinstance(unique_labels, int) else "—"
            um = str(unique_markers) if isinstance(unique_markers, int) else "—"
            return (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Quick Details</div>"
                f"<div style='color:#132327;padding:2px 0'>Cramer's V (class vs matrix/source): <strong>{_e(crv_text)}</strong></div>"
                f"<div style='color:#51656a;font-size:.78rem;padding:2px 0'>Labels: {ul}, matrix/source markers: {um}</div>"
                + (
                    "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Dominant Marker Fraction by Label</div>"
                    + "".join(rows)
                    if rows else ""
                )
            )

    if m.name == "benchmark_split_leakage_risk":
        total_dup = details.get("duplicate_occurrences_within_assays")
        total_seen = details.get("total_sample_appearances")
        per_analysis = details.get("per_analysis_duplicate_summary", [])
        try:
            ratio_text = f"{(float(total_dup) / max(1.0, float(total_seen))) * 100:.1f}%"
        except Exception:
            ratio_text = "—"
        html = (
            "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Within-Analysis Duplicate IDs</div>"
            f"<div style='color:#132327;padding:2px 0'>Duplicate sample-ID occurrences: "
            f"<strong>{_e(str(total_dup if total_dup is not None else '—'))}</strong> / "
            f"{_e(str(total_seen if total_seen is not None else '—'))} ({_e(ratio_text)})</div>"
            "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
            "Only duplicate sample IDs within each assay are counted here."
            "</div>"
        )
        if isinstance(per_analysis, list) and per_analysis:
            rows: list[str] = []
            for item in per_analysis[:12]:
                if not isinstance(item, dict):
                    continue
                aid = _analysis_id_label(item.get("analysis_id", "")) or "—"
                dup_ids = item.get("n_duplicated_ids", 0)
                dup_occ = item.get("duplicate_occurrences", 0)
                n_rows = item.get("n_rows", 0)
                n_unique = item.get("n_unique_sample_ids", 0)
                rows.append(
                    "<div style='display:flex;align-items:flex-start;justify-content:space-between;gap:8px;padding:2px 0'>"
                    "<div style='min-width:0'>"
                    f"<div style='color:#132327'>{_e(str(aid))}</div>"
                    f"<div style='color:#51656a;font-size:.74rem'>rows: {_e(str(n_rows))}, unique IDs: {_e(str(n_unique))}</div>"
                    "</div>"
                    f"<span style='font-weight:700;color:#113e52'>dup IDs: {_e(str(dup_ids))} · dup occ: {_e(str(dup_occ))}</span>"
                    "</div>"
                )
            if rows:
                html += (
                    "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Per-Analysis Breakdown</div>"
                    + "".join(rows)
                )
        return html

    if m.name == "class_separability":
        method = str(details.get("method", "") or "").strip() or "cv_linear_auroc_diagnostic"
        formula = str(details.get("formula", "") or "").strip()
        aggregation = str(details.get("aggregation", "") or "").strip()
        max_features = details.get("max_features_evaluated")
        cv_repeats = details.get("cv_repeats")
        cv_test_size = details.get("cv_test_size")
        n_total = details.get("n_analyses_total")
        n_eligible = details.get("n_analyses_eligible")
        coverage = details.get("eligible_coverage")
        mean_auc = details.get("mean_cv_auroc_eligible")
        median_auc = details.get("median_cv_auroc_eligible")
        iqr_auc = details.get("iqr_cv_auroc_eligible")
        ci95 = details.get("ci95_cv_auroc_eligible")
        if isinstance(ci95, list) and len(ci95) == 2:
            ci_low, ci_high = ci95[0], ci95[1]
        else:
            ci_low, ci_high = None, None
        method_label = (
            "Repeated stratified CV linear-AUROC (logistic regression)"
            if method == "cv_linear_auroc_diagnostic"
            else method
        )
        aggregation_label = (
            "Mean of eligible per-analysis scores (unweighted)"
            if aggregation in {"unweighted_mean_per_analysis", "unweighted_mean_eligible_analyses"}
            else (aggregation or "Mean of eligible per-analysis scores (unweighted)")
        )
        eligible_line = ""
        if n_total is not None and n_eligible is not None:
            eligible_line = (
                f"<div style='color:#132327;padding:2px 0'>Eligible analyses: <strong>{_e(str(n_eligible))}/{_e(str(n_total))}</strong>"
                + (f" ({_e(_fmt_pct(coverage))})" if coverage is not None else "")
                + "</div>"
            )
        stats_line = ""
        if mean_auc is not None:
            stats_line = (
                f"<div style='color:#132327;padding:2px 0'>Eligible-only AUROC: mean <strong>{_e(_fmt_num(mean_auc, 3))}</strong>"
                + (f", median <strong>{_e(_fmt_num(median_auc, 3))}</strong>" if median_auc is not None else "")
                + (f", IQR <strong>{_e(_fmt_num(iqr_auc, 3))}</strong>" if iqr_auc is not None else "")
                + (
                    f", 95% CI <strong>[{_e(_fmt_num(ci_low, 3))}, {_e(_fmt_num(ci_high, 3))}]</strong>"
                    if ci_low is not None and ci_high is not None
                    else ""
                )
                + "</div>"
            )
        return (
            "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Separability Method</div>"
            f"<div style='color:#132327;padding:2px 0'>Method: <strong>{_e(method_label)}</strong></div>"
            f"<div style='color:#132327;padding:2px 0'>Formula: <code>{_e(formula or 'analysis_score = 100 × mean(cv_auroc); study_score = mean(analysis_score)')}</code></div>"
            f"<div style='color:#132327;padding:2px 0'>Aggregation: <strong>{_e(aggregation_label)}</strong></div>"
            f"{eligible_line}"
            f"{stats_line}"
            f"<div style='color:#51656a;font-size:.78rem;padding-top:4px'>Max features evaluated per analysis: {_e(str(max_features if max_features is not None else '—'))}. CV repeats: {_e(str(cv_repeats if cv_repeats is not None else '—'))}, test size: {_e(str(cv_test_size if cv_test_size is not None else '—'))}.</div>"
        )

    if m.name == "feature_annotation_type":
        named = details.get("named")
        mz_rt = details.get("mz_rt")
        nmr_bin = details.get("nmr_bin")
        unannotated = details.get("unannotated")
        non_metabolite = details.get("non_metabolite")
        total = details.get("total")
        tier = str(details.get("tier", "") or "").strip() or "unknown"
        if isinstance(total, int) and total >= 0:
            named_f = float(named) if isinstance(named, (int, float)) else 0.0
            mz_rt_f = float(mz_rt) if isinstance(mz_rt, (int, float)) else 0.0
            nmr_f = float(nmr_bin) if isinstance(nmr_bin, (int, float)) else 0.0
            unann_f = float(unannotated) if isinstance(unannotated, (int, float)) else 0.0
            non_meta_f = float(non_metabolite) if isinstance(non_metabolite, (int, float)) else 0.0
            total_f = float(total) if total > 0 else 0.0
            named_pct = (named_f / total_f * 100.0) if total_f > 0 else 0.0
            mzrt_pct = (mz_rt_f / total_f * 100.0) if total_f > 0 else 0.0
            nmr_pct = (nmr_f / total_f * 100.0) if total_f > 0 else 0.0
            unann_pct = (unann_f / total_f * 100.0) if total_f > 0 else 0.0
            return (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Annotation Type Tiering</div>"
                f"<div style='color:#132327;padding:2px 0'>Current tier: <strong>{_e(tier)}</strong></div>"
                f"<div style='color:#132327;padding:2px 0'>Named metabolites: <strong>{_e(str(int(named_f)))}</strong> / {_e(str(total))} ({named_pct:.1f}%)</div>"
                f"<div style='color:#132327;padding:2px 0'>mz/RT-style tokens: <strong>{_e(str(int(mz_rt_f)))}</strong> / {_e(str(total))} ({mzrt_pct:.1f}%)</div>"
                f"<div style='color:#132327;padding:2px 0'>NMR bins: <strong>{_e(str(int(nmr_f)))}</strong> / {_e(str(total))} ({nmr_pct:.1f}%)</div>"
                f"<div style='color:#132327;padding:2px 0'>Unknown/non-metabolite: <strong>{_e(str(int(unann_f)))}</strong> / {_e(str(total))} ({unann_pct:.1f}%)</div>"
                f"<div style='color:#51656a;font-size:.78rem;padding:2px 0'>Non-metabolite token count (subset of unknown/non-metabolite): {_e(str(int(non_meta_f)))}</div>"
                "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
                "<strong>Tier rules:</strong> nmr_bin fraction >= 50% => score 65/100; "
                "else named fraction >= 70% => score 100/100; "
                "else named > 0 and (named + mz/RT) >= 70% => score 50/100; "
                "otherwise score 20/100."
                "</div>"
            )

    if m.name == "unknown_feature_fraction":
        unknown = details.get("unknown_features")
        total = details.get("total_features")
        nmr_bins = details.get("nmr_bin_features")
        if isinstance(unknown, int) and isinstance(total, int):
            unk_pct = (unknown / total * 100.0) if total > 0 else 0.0
            return (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Unknown Feature Fraction</div>"
                f"<div style='color:#132327;padding:2px 0'>Unknown placeholders: <strong>{_e(str(unknown))}</strong> / {_e(str(total))} ({unk_pct:.1f}%)</div>"
                f"<div style='color:#132327;padding:2px 0'>NMR bin features treated as identified: <strong>{_e(str(nmr_bins if isinstance(nmr_bins, int) else 0))}</strong></div>"
                "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
                "<strong>Formula:</strong> score = 100 × [1 − (unknown_features / total_features)]. "
                "Pass threshold: score >= 80 (equivalently unknown fraction <= 20%)."
                "</div>"
            )

    if m.name in ("annotation_ambiguity_burden", "annotation_ambiguity"):
        ambiguous = details.get("ambiguous")
        total = details.get("total")
        flag_counts = details.get("ambiguity_flag_counts", {})
        mixed_examples = details.get("multi_candidate_examples_mixed_top10", [])
        if isinstance(ambiguous, int) and isinstance(total, int):
            pct = (ambiguous / total * 100.0) if total > 0 else 0.0
            html = (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Quick Details</div>"
                f"<div style='color:#132327;padding:2px 0'>Ambiguous annotations: "
                f"<strong>{_e(str(ambiguous))}</strong> / {_e(str(total))} ({pct:.1f}%)</div>"
                "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
                "Any non-empty ambiguity flag marks a feature as ambiguous."
                "</div>"
            )
            if isinstance(flag_counts, dict) and flag_counts:
                top_items = sorted(
                    ((str(k), int(v)) for k, v in flag_counts.items()),
                    key=lambda item: (-item[1], item[0]),
                )[:6]
                rows = "".join(
                    "<div style='display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 0'>"
                    f"<span style='color:#132327'>{_e(k)}</span>"
                    f"<span style='font-weight:700;color:#113e52'>{_e(str(v))}</span>"
                    "</div>"
                    for k, v in top_items
                )
                html += (
                    "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Top Ambiguity Flags</div>"
                    + rows
                )
            if isinstance(mixed_examples, list) and mixed_examples:
                reason_labels = {
                    "semicolon_delimited": "A;B",
                    "slash_delimited": "A/B",
                    "refmet_match_count": "RefMet>1",
                }
                rows = []
                for item in mixed_examples[:10]:
                    if not isinstance(item, dict):
                        continue
                    reason = reason_labels.get(str(item.get("reason", "")), str(item.get("reason", "") or "other"))
                    name = str(item.get("name", "") or "").strip()
                    if not name:
                        continue
                    rows.append(
                        "<div style='display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 0'>"
                        f"<span style='color:#132327;max-width:290px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' title='{_e(name)}'>{_e(name)}</span>"
                        f"<span style='font-size:.72rem;font-weight:700;color:#995b00;background:#fdf3e3;border-radius:8px;padding:1px 6px'>{_e(reason)}</span>"
                        "</div>"
                    )
                if rows:
                    html += (
                        "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Multi-Candidate Examples (Top 10)</div>"
                        + "".join(rows)
                    )
            return html

    if m.name == "feature_redundancy":
        redundant = details.get("redundant")
        top_redundant = details.get("top_redundant", {})
        analysis_map = details.get("repeated_feature_analysis_ids", {})
        duplicate_groups = details.get("duplicate_name_groups")
        total = None
        if isinstance(redundant, int):
            try:
                summary = str(m.summary or "")
                # Summary format: "Detected X redundant ... across Y annotations."
                import re as _re
                match = _re.search(r"across\\s+(\\d+)\\s+annotations", summary)
                if match:
                    total = int(match.group(1))
            except Exception:
                total = None
            pct = (redundant / total * 100.0) if (isinstance(total, int) and total > 0) else None
            html = (
                "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Quick Details</div>"
                f"<div style='color:#132327;padding:2px 0'>Redundant raw names: "
                f"<strong>{_e(str(redundant))}</strong>"
                + (f" / {_e(str(total))} ({pct:.1f}%)" if pct is not None else "")
                + "</div>"
                + (
                    f"<div style='color:#132327;padding:2px 0'>Repeated-name groups: <strong>{_e(str(duplicate_groups))}</strong></div>"
                    if isinstance(duplicate_groups, int) else ""
                )
                +
                "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
                "Redundant = sum(count-1) for raw feature names appearing more than once within the same assay."
                "</div>"
            )
            if isinstance(top_redundant, dict) and top_redundant:
                top_items = sorted(
                    ((str(k), int(v)) for k, v in top_redundant.items()),
                    key=lambda item: (-item[1], item[0]),
                )
                rows = "".join(
                    "<div style='display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 0'>"
                    f"<span style='color:#132327'>{_e(k)}</span>"
                    f"<span style='font-weight:700;color:#113e52'>{_e(str(v))}</span>"
                    "</div>"
                    for k, v in top_items[:6]
                )
                html += (
                    "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Top Repeated Names</div>"
                    + rows
                )
                if isinstance(analysis_map, dict) and analysis_map:
                    rows_with_analysis: list[str] = []
                    for name, count in top_items[:10]:
                        analyses_raw = analysis_map.get(name, [])
                        analyses: list[str] = []
                        if isinstance(analyses_raw, list):
                            for item in analyses_raw:
                                text = _analysis_id_label(item)
                                if text and text not in analyses:
                                    analyses.append(text)
                        analyses_display = "; ".join(analyses[:4]) if analyses else "—"
                        if len(analyses) > 4:
                            analyses_display += f"; +{len(analyses) - 4} more"
                        rows_with_analysis.append(
                            "<div style='display:flex;align-items:flex-start;justify-content:space-between;gap:8px;padding:3px 0'>"
                            "<div style='min-width:0'>"
                            f"<div style='color:#132327;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' title='{_e(name)}'>{_e(name)}</div>"
                            f"<div style='color:#51656a;font-size:.74rem;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' title='{_e(analyses_display)}'>analysis IDs: {_e(analyses_display)}</div>"
                            "</div>"
                            f"<span style='font-weight:700;color:#113e52'>{_e(str(count))}</span>"
                            "</div>"
                        )
                    if rows_with_analysis:
                        html += (
                            "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Repeated Names by Analysis ID</div>"
                            + "".join(rows_with_analysis)
                            + "<div style='color:#51656a;font-size:.74rem;padding-top:4px'>All repeated names and analysis-ID mappings are stored in report JSON details.</div>"
                        )
            return html

    # required_field_completeness: study field checks + sample field coverage
    if m.name == "required_field_completeness":
        study_checks = details.get("study_field_checks")
        sample_cov = details.get("sample_field_coverage", {})
        html_parts = []
        if study_checks and isinstance(study_checks, dict):
            html_parts.append("<div style='font-weight:600;margin-bottom:6px;color:#132327'>Study-level fields</div>")
            html_parts.append(_checks_list_html(study_checks))
        if sample_cov:
            html_parts.append("<div style='font-weight:600;margin:8px 0 4px;color:#132327'>Per-sample field coverage</div>")
            for field, coverage in sample_cov.items():
                try:
                    num, denom = (int(x) for x in coverage.split("/"))
                    ok = num == denom
                except Exception:
                    ok = True
                mark = "✓" if ok else "⚠"
                color = "#196b4a" if ok else "#995b00"
                html_parts.append(
                    f"<div style='display:flex;align-items:center;gap:6px;padding:2px 0'>"
                    f"<span style='font-weight:700;color:{color};min-width:14px;flex-shrink:0'>{mark}</span>"
                    f"<span style='color:#132327'>{_e(field.title())}:</span>"
                    f"<span style='color:{color};font-weight:600'>{_e(str(coverage))}</span>"
                    f"</div>"
                )
        return "".join(html_parts) if html_parts else ""

    # duplicate_entities: split into samples vs features
    if m.name == "duplicate_entities":
        dup_s = details.get("duplicate_samples", {})
        dup_f = details.get("duplicate_features", {})
        ok_s = len(dup_s) == 0
        ok_f = len(dup_f) == 0
        rows = [
            (ok_s, f"Duplicate sample IDs: {len(dup_s)}"),
            (ok_f, f"Duplicate feature IDs: {len(dup_f)}"),
        ]
        items = []
        for ok, text in rows:
            mark = "✓" if ok else "✗"
            fg = "#196b4a" if ok else "#8f2d2d"
            items.append(
                f"<div style='display:flex;align-items:center;gap:6px;padding:2px 0'>"
                f"<span style='font-weight:700;color:{fg};min-width:14px'>{mark}</span>"
                f"<span style='color:{fg}'>{_e(text)}</span>"
                f"</div>"
            )
        return "<div style='font-weight:600;margin-bottom:6px;color:#132327'>By entity type</div>" + "".join(items)

    # minimum_sample_count
    if m.name == "minimum_sample_count":
        n = details.get("n_biological_samples", 0)
        n_total = details.get("n_total_samples", 0)
        threshold = details.get("threshold", 20)
        ok = n >= threshold
        color = "#196b4a" if ok else "#995b00"
        mark = "✓" if ok else "⚠"
        return (
            f"<div style='color:{color};font-weight:600'>{mark} {n} ML-eligible samples (min: {threshold})</div>"
            f"<div style='color:#51656a;font-size:.79rem;margin-top:4px'>"
            f"Total samples (incl. QC/blank/pool): {n_total}"
            f"<br>Rows excluded before ML assessment: {n_total - n}</div>"
        )

    if m.name == "feature_to_sample_ratio":
        total_features = details.get("total_features")
        n_bio = details.get("n_biological_samples")
        ratio_val = details.get("ratio")
        median_pn = details.get("median_pn_ratio")
        pct_pn_gt1 = details.get("pct_analyses_pn_gt1")
        composite_score = details.get("composite_score")
        per_analysis = details.get("per_analysis", [])
        try:
            ratio_text = f"{float(ratio_val):.1f}"
        except Exception:
            ratio_text = "—"
        try:
            pct_pn_text = f"{float(pct_pn_gt1):.1f}%"
        except Exception:
            pct_pn_text = "—"
        try:
            composite_text = _score_100_text(composite_score, 1)
        except Exception:
            composite_text = "—"

        ratio_calc_text = "—"
        if total_features is not None and n_bio not in (None, 0):
            try:
                ratio_calc_text = f"{int(total_features)}/{int(n_bio)}"
            except Exception:
                ratio_calc_text = f"{total_features}/{n_bio}"

        ratio_list: list[float] = []
        if isinstance(per_analysis, list):
            for item in per_analysis:
                if not isinstance(item, dict):
                    continue
                try:
                    ratio_list.append(float(item.get("ratio")))
                except Exception:
                    continue
        if ratio_list:
            mean_pn = sum(ratio_list) / len(ratio_list)
        else:
            # Backward-compatible fallback if per-analysis ratios are absent.
            try:
                mean_pn = float(median_pn)
            except Exception:
                mean_pn = None
        mean_pn_text = f"{mean_pn:.2f}" if mean_pn is not None else "—"

        html = (
            "<div style='font-weight:600;margin-bottom:6px;color:#132327'>Feature-to-Sample Ratio</div>"
            f"<div style='color:#132327;padding:2px 0'>Total features (calculated from all analyses): "
            f"<strong>{_e(str(total_features if total_features is not None else '—'))}</strong></div>"
            f"<div style='color:#132327;padding:2px 0'>ML-eligible samples: <strong>{_e(str(n_bio if n_bio is not None else '—'))}</strong></div>"
            f"<div style='color:#132327;padding:2px 0'>Global F:S ratio: <strong>{_e(ratio_text)}</strong> "
            f"(calculation: <code>{_e(ratio_calc_text)}</code>)</div>"
        )
        if isinstance(per_analysis, list) and per_analysis:
            rows = []
            for item in per_analysis[:10]:
                if not isinstance(item, dict):
                    continue
                aid = _analysis_id_label(item.get("analysis_id", "") or "—")
                n_f = item.get("n_features_in_matrix", "—")
                n_s = item.get("n_samples_in_matrix", "—")
                r = item.get("ratio")
                try:
                    r_text = f"{float(r):.1f}"
                except Exception:
                    r_text = "—"
                rows.append(
                    "<div class='fsr-analysis-row'>"
                    f"<span class='fsr-analysis-id'>{_e(str(aid))}</span>"
                    f"<span class='fsr-analysis-detail'>{_e(str(n_f))} feat / {_e(str(n_s))} samp (matrix)</span>"
                    f"<span class='fsr-analysis-ratio'>{_e(r_text)}</span>"
                    "</div>"
                )
            if rows:
                html += (
                    "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Per-Analysis Ratios</div>"
                    + "".join(rows)
                )
        html += (
            f"<div style='color:#132327;padding:2px 0'>Mean P:N ratio (features:samples, per analysis): "
            f"<strong>{_e(mean_pn_text)}</strong></div>"
            f"<div style='color:#132327;padding:2px 0'>% analyses where features &gt; samples (P:N &gt; 1): "
            f"<strong>{_e(pct_pn_text)}</strong></div>"
            "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Score Mapping (0-100, per analysis)</div>"
            "<div style='color:#132327;padding:2px 0'><code>ratio_i = n_features_in_matrix / n_samples_in_matrix</code></div>"
            f"<div style='color:#132327;padding:2px 0'><code>ratio_i ≤ {_v2_fmt_param(params['pn_low'])} → 100</code></div>"
            f"<div style='color:#132327;padding:2px 0'><code>{_v2_fmt_param(params['pn_low'])} &lt; ratio_i ≤ {_v2_fmt_param(params['pn_moderate'])} → 80</code></div>"
            f"<div style='color:#132327;padding:2px 0'><code>{_v2_fmt_param(params['pn_moderate'])} &lt; ratio_i ≤ {_v2_fmt_param(params['pn_high'])} → 50</code></div>"
            f"<div style='color:#132327;padding:2px 0'><code>ratio_i &gt; {_v2_fmt_param(params['pn_high'])} → 100 × max(0.1, 1 - ratio_i/{_v2_fmt_param(params['pn_tail'])})</code></div>"
            "<div style='font-weight:600;margin:8px 0 6px;color:#132327'>Composite Score (0-100)</div>"
            f"<div style='color:#132327;padding:2px 0'><code>final_score = sample-weighted mean(per-analysis scores on 0-100 scale)</code> "
            f"= <strong>{_e(composite_text)}</strong></div>"
            "<div style='color:#51656a;font-size:.78rem;padding-top:4px'>"
            "Interpretation: F:S &gt; 50 generally needs regularization (e.g., LASSO/Ridge); "
            "F:S &gt; 200 generally needs dimensionality reduction. "
            "P:N &gt; 1 means more features than samples, so unregularized models can be unstable."
            "</div>"
        )
        return html

    return ""


# ---------------------------------------------------------------------------
# Metric table per section
# ---------------------------------------------------------------------------

def _metric_rows(
    metrics: list[Any],
    params: dict[str, float] | None = None,
    study_id: Any | None = None,
) -> str:
    params = params or _V2_DEFAULT_PARAMS
    metrics = [
        m for m in metrics
        if getattr(m, "name", "") != "batch_info_availability"
        and getattr(m, "name", "") not in _V2_HIDDEN_LEGACY_METRICS
    ]
    if not metrics:
        return "<p style='color:#51656a;font-style:italic;padding:8px'>No metrics in this section.</p>"
    _td = "padding:10px 8px;border-bottom:1px solid rgba(19,35,39,.07);vertical-align:top"
    _th = "padding:8px;text-align:left;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:#51656a;border-bottom:2px solid rgba(19,35,39,.1)"
    _status_col = "min-width:78px;width:78px;white-space:nowrap;word-break:normal;text-align:left"
    rows = []
    for m in metrics:
        recs_html = ""
        if m.recommendations:
            items = "".join(f"<li style='margin:4px 0'>{_e(r)}</li>" for r in m.recommendations)
            recs_html = f"<ul style='margin:8px 0 0 16px;padding:0;color:#995b00;font-size:.85rem'>{items}</ul>"

        descriptor = _metric_descriptor(m.name, params)
        desc_html = (
            f"<div style='font-size:.76rem;color:#51656a;font-weight:400;margin-top:3px;line-height:1.4'>"
            f"{_e(descriptor)}</div>"
        ) if descriptor else ""

        tooltip_content = _metric_info_tooltip(m, params, study_id=study_id)
        popup_class = (
            "minfo-popup fair-metadata-popup" if m.name == "fair_study_metadata_compliance"
            else "minfo-popup feature-sample-popup" if m.name == "feature_to_sample_ratio"
            else "minfo-popup"
        )
        info_icon = (
            f"<span class='minfo' tabindex='0'>"
            f"<span class='minfo-icon'>i</span>"
            f"<div class='{popup_class}'>{tooltip_content}</div>"
            f"</span>"
        ) if tooltip_content else ""

        keyword_html = ""
        if m.name == "qc_blank_presence":
            details = m.details or {}
            qc_keywords = [str(k).strip() for k in details.get("qc_keywords", []) if str(k).strip()]
            blank_keywords = [str(k).strip() for k in details.get("blank_keywords", []) if str(k).strip()]
            if qc_keywords or blank_keywords:
                qc_text = ", ".join(qc_keywords) if qc_keywords else "none"
                blank_text = ", ".join(blank_keywords) if blank_keywords else "none"
                keyword_html = (
                    f"<div style='font-size:.67rem;color:#51656a;line-height:1.35;margin:0 0 4px'>"
                    f"<strong style='color:#113e52'>QC keywords:</strong> {_e(qc_text)}<br>"
                    f"<strong style='color:#113e52'>Blank keywords:</strong> {_e(blank_text)}"
                    f"</div>"
                )

        name_cell = (
            f"{keyword_html}"
            f"<div style='display:flex;align-items:flex-start;gap:2px'>"
            f"<span style='font-weight:600'>{_e(_metric_display_name(m.name))}</span>{info_icon}"
            f"</div>"
            f"{desc_html}"
        )

        if getattr(m, "informational", False):
            score_cell = "<span style='font-size:.75rem;font-weight:600;color:#c0392b'>informational</span>"
        else:
            score_cell = _score_bar(m.score)
        rows.append(
            f"<tr>"
            f"<td style='{_td};min-width:200px'>{name_cell}</td>"
            f"<td style='{_td};min-width:140px'>{score_cell}</td>"
            f"<td style='{_td};{_status_col}'>{_status_badge(m.status)}</td>"
            f"<td style='{_td};font-size:.88rem'>{_e(m.summary)}{recs_html}</td>"
            f"</tr>"
        )
    return (
        "<table style='width:100%;border-collapse:collapse;font-size:.9rem'>"
        "<thead><tr>"
        f"<th style='{_th}'>Metric</th>"
        f"<th style='{_th}'>Score (0-100)</th>"
        f"<th style='{_th};{_status_col}'>Status</th>"
        f"<th style='{_th}'>Summary / Recommendations</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _fmt_num(value: Any, digits: int = 3) -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):.{digits}f}"
    except Exception:
        text = str(value or "").strip()
        return _e(text) if text else "—"


def _fmt_sci(value: Any, digits: int = 3) -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value):.{digits}e}"
    except Exception:
        text = str(value or "").strip()
        return _e(text) if text else "—"


def _fmt_pct(value: Any, digits: int = 1) -> str:
    try:
        if value is None:
            return "—"
        return f"{float(value) * 100:.{digits}f}%"
    except Exception:
        return "—"


def _analysis_table(headers: list[str], rows: list[list[str]], *, allow_header_html: bool = False) -> str:
    if not rows:
        return "<p style='margin:6px 0 0;color:#51656a;font-size:.82rem;font-style:italic'>No analysis-wise values available.</p>"
    _th = (
        "padding:7px 8px;text-align:left;font-size:.7rem;text-transform:uppercase;"
        "letter-spacing:.06em;color:#51656a;border-bottom:2px solid rgba(19,35,39,.1);white-space:nowrap"
    )
    _td = "padding:8px;border-bottom:1px solid rgba(19,35,39,.07);font-size:.83rem;vertical-align:top"
    head_html = "".join(
        f"<th style='{_th}'>{header if allow_header_html else _e(header)}</th>"
        for header in headers
    )
    body_html = "".join(
        "<tr>" + "".join(f"<td style='{_td}'>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        "<div style='overflow-x:auto;margin-top:8px'>"
        "<table style='width:100%;border-collapse:collapse'>"
        f"<thead><tr style='background:rgba(13,110,110,.06)'>{head_html}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>"
    )


def _analytical_breakdown_html(m: Any) -> str:
    details = m.details or {}
    per_analysis = details.get("per_analysis")
    if not isinstance(per_analysis, list):
        per_analysis = []

    rows: list[list[str]] = []

    if m.name == "qc_blank_presence":
        qc_count = details.get("qc_count")
        blank_count = details.get("blank_count")
        qc_ratio = details.get("qc_ratio")
        blank_ratio = details.get("blank_ratio")
        qc_missing = not qc_count
        blank_missing = not blank_count
        feasibility_notes: list[str] = []
        if qc_missing:
            feasibility_notes.append(
                "QC-based normalization or drift correction may not be feasible because no QC/pool/reference samples were detected."
            )
        if blank_missing:
            feasibility_notes.append(
                "Blank subtraction or contaminant-screening workflows may not be feasible because no blank samples were detected."
            )
        feasibility_html = ""
        if feasibility_notes:
            feasibility_html = (
                "<div style='color:#995b00;margin-top:6px;line-height:1.45'>"
                + " ".join(_e(note) for note in feasibility_notes)
                + "</div>"
            )
        return (
            "<div style='margin-top:8px;padding:10px 12px;border-radius:12px;"
            "background:rgba(13,110,110,.06);border:1px solid rgba(13,110,110,.14);font-size:.84rem'>"
            f"<div style='color:#132327'><strong>Study-level QC / blank coverage:</strong> QC/pool/reference = {_e(str(qc_count))}, blanks = {_e(str(blank_count))}</div>"
            f"<div style='color:#51656a;margin-top:4px'>QC ratio: <strong>{_e(_fmt_pct(qc_ratio))}</strong>, blank ratio: <strong>{_e(_fmt_pct(blank_ratio))}</strong></div>"
            "<div style='color:#51656a;margin-top:6px;line-height:1.45'>"
            "<strong style='color:#132327'>How this is calculated:</strong> "
            "Each sample's ID, label, and sample type are scanned for known QC keywords "
            "(qc, pool, nist, reference, ltr, sst, system suitability, etc.) and blank keywords "
            "(blank, solvent, process blank, method blank, reagent blank, wash, empty run, etc.). "
            "QC samples and blanks are treated as different control types: QC/pool/reference samples support reproducibility and system-suitability assessment, "
            "whereas blanks capture chemical/background contamination and support blank subtraction. "
            "Displayed score = 50/100 if at least one QC/pool/reference sample is found + 50/100 if at least one blank is found. "
            "QC and blank ratios are the fraction of total samples matched by each keyword set."
            "</div>"
            f"{feasibility_html}"
            "<div style='color:#51656a;margin-top:4px'>No per-analysis split shown because this metric is interpreted at sample-study level.</div>"
            "<div style='margin-top:6px;font-size:.78rem;font-weight:700;color:#c0392b'>"
            "This metric is informational only and is not included in the readiness score."
            "</div>"
            "</div>"
        )

    if m.name == "metabatch_batch_annotation_compatibility":
        compatible = bool(details.get("metabatch_compatible"))
        technical = bool(details.get("technical_batch_like_present"))
        rows = []
        for item in per_analysis:
            usable_keys = item.get("usable_factor_keys", [])
            technical_keys = item.get("technical_batch_like_keys", [])
            rows.append(
                [
                    _e(_analysis_id_label(item.get("analysis_id", "") or "—")),
                    _e(str(item.get("n_samples", "—"))),
                    _fmt_pct(item.get("sample_factor_coverage")),
                    _e(", ".join(str(k) for k in usable_keys[:5]) if usable_keys else "—"),
                    _e(", ".join(str(k) for k in technical_keys[:5]) if technical_keys else "—"),
                ]
            )
        table = _analysis_table(
            [
                "Analysis ID",
                "Samples",
                "Factor coverage",
                "MetaBatch-usable factors",
                "Technical-like keys",
            ],
            rows,
        )
        return (
            "<div style='margin-top:8px;padding:10px 12px;border-radius:12px;"
            "background:rgba(13,110,110,.06);border:1px solid rgba(13,110,110,.14);font-size:.84rem'>"
            f"<div style='color:#132327'><strong>MetaBatch-style factor table possible:</strong> {'Yes' if compatible else 'No'}</div>"
            f"<div style='color:#51656a;margin-top:4px'><strong>Explicit technical batch-like keys:</strong> {'Yes' if technical else 'No'}</div>"
            "<div style='color:#51656a;margin-top:6px;line-height:1.45'>"
            "This ports the StdMW/MetaBatch idea of converting Metabolomics Workbench allfactors into a batches/covariates table. "
            "MERIT-ML keeps the interpretation conservative: generic factors may be useful covariates, but only factor names or values containing explicit batch/run/order/plate/injection/acquisition-like text are treated as technical-like metadata. "
            "<a href='https://bioinformatics.mdanderson.org/public-software/metabatch/' target='_blank' rel='noopener noreferrer' style='color:#0d6e6e;font-weight:700'>Original MetaBatch tool</a>."
            "</div>"
            f"{table}"
            "<div style='margin-top:6px;font-size:.78rem;font-weight:700;color:#c0392b'>"
            "This metric is informational only and is not included in the readiness score."
            "</div>"
            "</div>"
        )

    if not per_analysis:
        return "<p style='margin:6px 0 0;color:#51656a;font-size:.82rem;font-style:italic'>No analysis-wise values available.</p>"

    if m.name == "missingness_structure":
        sample_notes: list[str] = []
        for item in per_analysis:
            sample_top = item.get("sample_missing_top10", [])
            sample_tokens: list[str] = []
            if isinstance(sample_top, list):
                for rec in sample_top[:10]:
                    if not isinstance(rec, dict):
                        continue
                    sid = str(rec.get("sample_id", "") or "").strip()
                    if not sid:
                        continue
                    try:
                        missing_rate_value = float(rec.get("missing_rate", 0.0))
                    except Exception:
                        missing_rate_value = 0.0
                    if missing_rate_value <= 0.0:
                        continue
                    rate = f"{missing_rate_value * 100:.1f}%"
                    sample_tokens.append(f"{sid} ({rate})")
            rows.append(
                [
                    _e(_analysis_id_label(item.get("analysis_id", "") or "—")),
                    f"{_e(str(item.get('n_biological_samples', item.get('n_samples', '—'))))} / {_e(str(item.get('n_features', '—')))}",
                    _fmt_pct(item.get("median_sample_missingness_rate")),
                    _fmt_pct(item.get("mean_sample_missingness_rate")),
                    _fmt_pct(item.get("class_dependent_gap")),
                    _score_100_text(item.get("score"), 1),
                ]
            )
            if sample_tokens:
                sample_notes.append(
                    f"<li style='margin:3px 0'><code>{_e(_analysis_id_label(item.get('analysis_id', '') or '—'))}</code>: {_e(', '.join(sample_tokens))}</li>"
                )
        missingness_guide = (
            "<div style='margin-top:8px;padding:10px 12px;border-radius:12px;"
            "background:rgba(13,110,110,.06);border:1px solid rgba(13,110,110,.14);font-size:.8rem;color:#51656a'>"
            "<div style='font-weight:700;color:#132327;margin-bottom:6px'>Column Guide</div>"
            "<div><strong style='color:#132327'>Samples / Features:</strong> Number of ML-eligible samples (QC/blank/pool/reference excluded) and features in this analysis matrix.</div>"
            "<div><strong style='color:#132327'>Median sample missing %:</strong> Median of per-sample missingness rates — the typical sample's fraction of missing features. This drives the score.</div>"
            "<div><strong style='color:#132327'>Mean sample missing %:</strong> Mean of per-sample missingness rates (sensitive to outlier samples).</div>"
            "<div><strong style='color:#132327'>Class-dependent gap %:</strong> Difference between highest and lowest class-wise missingness rates. Reported as a diagnostic warning (>= 10% triggers warn status) but not mixed into the score.</div>"
            "<div><strong style='color:#132327'>Score:</strong> 1 − median(per-sample missingness rates). Measures how complete the typical sample is.</div>"
            "<div style='margin-top:6px'><strong style='color:#132327'>Why class-dependent missingness is reported separately:</strong> "
            "if one class is systematically missing more values, models can learn technical artifacts instead of disease biology. "
            "This is a bias concern (not a data quantity concern), so it is flagged as a separate warning rather than blended into the completeness score.</div>"
            "<div style='margin-top:8px;padding:6px 8px;background:rgba(13,110,110,.06);border-radius:8px;color:#51656a;font-size:.77rem;line-height:1.4'>"
            "<strong>Source-aware zero handling:</strong> "
            "datatable zeros are treated as valid (curated structural fill); "
            "mwTab/untarg_data zeros are treated as missing (below detection)."
            "</div>"
            "</div>"
        )
        table = _analysis_table(
            [
                "Analysis ID",
                "Samples / Features",
                "Median sample missing %",
                "Mean sample missing %",
                "Class-dependent gap %",
                "Score (0-100)",
            ],
            rows,
        )
        table = missingness_guide + table
        if sample_notes:
            table += "<div style='margin-top:8px;font-size:.8rem;color:#51656a'>"
            if sample_notes:
                table += "<strong style='color:#132327'>Highest-missing samples (top 10 per analysis):</strong>"
                table += f"<ul style='margin:6px 0 8px 16px;padding:0'>{''.join(sample_notes)}</ul>"
            table += "</div>"
        return table

    if m.name == "scale_diagnostics":
        low_signal_notes: list[str] = []
        nzv_notes: list[str] = []
        for item in per_analysis:
            rows.append(
                [
                    _e(_analysis_id_label(item.get("analysis_id", "") or "—")),
                    _e(str(item.get("declared_units", "") or "unknown")),
                    _e(str(item.get("status", "") or "—")),
                    _fmt_sci(item.get("min"), 3),
                    _fmt_sci(item.get("median"), 3),
                    _fmt_num(item.get("log10_median_intensity"), 3),
                    _fmt_sci(item.get("p90"), 3),
                    _fmt_sci(item.get("max"), 3),
                    _fmt_num(item.get("median_to_p90_ratio"), 4),
                    _e(str(item.get("low_signal_feature_count", "0"))),
                    _e(str(item.get("near_zero_variance_feature_count", "0"))),
                ]
            )
            examples = item.get("low_signal_features_top20", [])
            if isinstance(examples, list) and examples:
                names = []
                for rec in examples[:10]:
                    if not isinstance(rec, dict):
                        continue
                    feature_name = str(rec.get("feature_name", "") or "").strip()
                    feature_id = _normalize_analysis_tokens(str(rec.get("feature_id", "") or "").strip())
                    display = feature_name if feature_name else feature_id
                    if display:
                        names.append(display)
                if names:
                    low_signal_notes.append(
                        f"<li style='margin:3px 0'><code>{_e(_analysis_id_label(item.get('analysis_id', '')))}</code>: {_e(', '.join(names))}</li>"
                    )
            nzv_examples = item.get("near_zero_variance_features_top20", [])
            if isinstance(nzv_examples, list) and nzv_examples:
                names = []
                for rec in nzv_examples[:10]:
                    if not isinstance(rec, dict):
                        continue
                    feature_name = str(rec.get("feature_name", "") or "").strip()
                    feature_id = _normalize_analysis_tokens(str(rec.get("feature_id", "") or "").strip())
                    display = feature_name if feature_name else feature_id
                    if display:
                        names.append(display)
                if names:
                    nzv_notes.append(
                        f"<li style='margin:3px 0'><code>{_e(_analysis_id_label(item.get('analysis_id', '')))}</code>: {_e(', '.join(names))}</li>"
                    )

        low_signal_header = (
            "Low-signal features "
            f"{_mini_info_icon('Formula per analysis: for each feature f, compute s_f = P90(feature values). Let T = P10 of all s_f in that analysis. Feature is low-signal if s_f <= T (bottom decile of feature P90).')}"
        )
        declared_scale_header = (
            "Declared value scale "
            f"{_mini_info_icon('Pulled directly from mwTab JSON units (MS_METABOLITE_DATA.Units / NMR_METABOLITE_DATA.Units). If missing, shown as unknown. This is metadata, not inferred.')}"
        )
        nzv_header = (
            "Near-zero variance features "
            f"{_mini_info_icon('Near-zero variance (NZV) is detected with a robust MAD-based variability measure and an IQR check. For each feature: median = median(values), MAD = median(|values - median|), scale = median(|values|) + 1e-8, mad_relative_variability = MAD/scale, and IQR = P75 - P25. Feature is NZV if mad_relative_variability < 1e-3 or IQR = 0. Low MAD means minimal variation relative to signal; IQR = 0 means the middle 50% of values are identical (collapsed distribution). Reported as diagnostic; scale diagnostics are informational and not included in the readiness score.')}"
        )

        log10_med_header = (
            "log\u2081\u2080(median) "
            f"{_mini_info_icon('log10 of the median intensity across non-missing biological values in this analysis. Values in the 3–7 range often indicate raw-scale LC-MS peak areas (median ≈ 10³–10⁷). Transformed/normalized scales usually sit lower (often < 1–2), but this is a heuristic, not a strict rule.')}"
        )
        p50_p90_header = (
            "P50/P90 "
            f"{_mini_info_icon('Why this matters: Scale diagnostics summarizes whether the observed value distribution looks raw-like or compressed/transformed for preprocessing decisions. P50/P90 compares central signal (median, P50) against the upper-tail signal (P90). Very low values suggest many measurements clustered near low-intensity range, while higher values indicate broader dynamic spread. This is interpretive guidance only and does not affect readiness score.')}"
        )
        table = _analysis_table(
            [
                "Analysis ID",
                declared_scale_header,
                "Inferred status",
                "Min",
                "Median",
                log10_med_header,
                "P90",
                "Max",
                p50_p90_header,
                low_signal_header,
                nzv_header,
            ],
            rows,
            allow_header_html=True,
        )
        if low_signal_notes or nzv_notes:
            table += "<div style='margin-top:8px;font-size:.8rem;color:#51656a'>"
            if low_signal_notes:
                table += (
                    "<strong style='color:#132327'>Low-signal feature flags (top 10 each analysis):</strong>"
                    f"<ul style='margin:6px 0 8px 16px;padding:0'>{''.join(low_signal_notes)}</ul>"
                )
            if nzv_notes:
                table += (
                    "<strong style='color:#132327'>Near-zero variance features (top 10 each analysis):</strong>"
                    f"<ul style='margin:6px 0 0 16px;padding:0'>{''.join(nzv_notes)}</ul>"
                )
            table += "</div>"
        table += (
            "<div style='margin-top:6px;font-size:.78rem;font-weight:700;color:#c0392b'>"
            "This metric is informational only and is not included in the readiness score."
            "</div>"
        )
        return table

    if m.name == "assay_platform_comparability":
        for item in per_analysis:
            rows.append(
                [
                    _e(_analysis_id_label(item.get("analysis_id", "") or "—")),
                    _e(str(item.get("positive_values", item.get("n_positive_values", "—")))),
                    _fmt_num(item.get("log10_median"), 4),
                ]
            )
        positive_values_tip = (
            "Positive values = number of matrix entries used for this analysis after filtering to values that are:<br><br>"
            "1. not missing or non-finite<br>"
            "2. strictly greater than 0<br><br>"
            "This reflects how many data points contribute to the log10 median. "
            "Larger counts indicate a more stable estimate of the analysis scale."
        )
        positive_values_header = (
            "Positive values "
            "<span class='minfo' tabindex='0'>"
            "<span class='minfo-icon'>i</span>"
            f"<div class='minfo-popup' style='text-align:left;white-space:normal'>{positive_values_tip}</div>"
            "</span>"
        )
        return _analysis_table(
            ["Analysis ID", positive_values_header, "log10 median"],
            rows,
            allow_header_html=True,
        )

    if m.name == "feature_correlation_burden":
        corr_pair_notes: list[str] = []
        for item in per_analysis:
            rows.append(
                [
                    _e(_analysis_id_label(item.get("analysis_id", "") or "—")),
                    _e(str(item.get("high_correlation_pairs", "—"))),
                    _e(str(item.get("sampled_pairs", "—"))),
                    _fmt_pct(item.get("high_correlation_rate")),
                    _score_100_text(item.get("score"), 1),
                ]
            )
            top_pairs = item.get("top_correlated_pairs", [])
            tokens: list[str] = []
            if isinstance(top_pairs, list):
                for rec in top_pairs[:5]:
                    if not isinstance(rec, dict):
                        continue
                    feature_a_name = str(rec.get("feature_a_name", "") or "").strip()
                    feature_b_name = str(rec.get("feature_b_name", "") or "").strip()
                    feature_a_id = _normalize_analysis_tokens(str(rec.get("feature_a", "") or "").strip())
                    feature_b_id = _normalize_analysis_tokens(str(rec.get("feature_b", "") or "").strip())
                    display_a = feature_a_name or feature_a_id
                    display_b = feature_b_name or feature_b_id
                    if not display_a or not display_b:
                        continue
                    try:
                        abs_r_text = f"{float(rec.get('abs_r', 0.0)):.3f}"
                    except Exception:
                        abs_r_text = "—"
                    tokens.append(f"{display_a} ~ {display_b} (|r|={abs_r_text})")
            if tokens:
                corr_pair_notes.append(
                    f"<li style='margin:3px 0'><code>{_e(_analysis_id_label(item.get('analysis_id', '') or '—'))}</code>: {_e(', '.join(tokens))}</li>"
                )
        table = _analysis_table(
            ["Analysis ID", "High-corr pairs", "Sampled pairs", "High-corr %", "Score (0-100)"],
            rows,
        )
        if corr_pair_notes:
            table += (
                "<div style='margin-top:8px;font-size:.8rem;color:#51656a'>"
                "<strong style='color:#132327'>Top correlated feature pairs (top 5 each analysis):</strong>"
                f"<ul style='margin:6px 0 0 16px;padding:0'>{''.join(corr_pair_notes)}</ul>"
                "</div>"
            )
        return table

    if m.name == "outlier_burden":
        sample_notes: list[str] = []
        for item in per_analysis:
            analysis_id = _analysis_id_label(item.get("analysis_id", "") or "—")
            rows.append(
                [
                    _e(analysis_id),
                    f"{_e(str(item.get('sample_outliers', '—')))} / {_e(str(item.get('sample_total', '—')))}",
                    _fmt_pct(item.get("sample_outlier_rate")),
                    _score_100_text(item.get("score"), 1),
                ]
            )
            out_samples = item.get("outlier_samples", [])
            if isinstance(out_samples, list) and out_samples:
                sample_notes.append(
                    f"<li style='margin:3px 0'><code>{_e(analysis_id)}</code>: {_e(', '.join(str(s) for s in out_samples[:10]))}</li>"
                )
        table = _analysis_table(
            [
                "Analysis ID",
                "Sample outliers",
                "Sample outlier %",
                "Score (0-100)",
            ],
            rows,
        )
        if sample_notes:
            table += "<div style='margin-top:8px;font-size:.8rem;color:#51656a'>"
            table += "<strong style='color:#132327'>Outlier samples (top 10 per analysis):</strong>"
            table += f"<ul style='margin:6px 0 8px 16px;padding:0'>{''.join(sample_notes)}</ul>"
            table += "</div>"
        return table

    if m.name == "feature_level_missingness":
        top_missing_notes: list[str] = []
        for item in per_analysis:
            rows.append(
                [
                    _e(_analysis_id_label(item.get("analysis_id", "") or "—")),
                    _e(str(item.get("n_total_features", "—"))),
                    _e(str(item.get("n_high_missing_features", "—"))),
                    _fmt_pct(item.get("mean_missingness_rate")),
                    _fmt_pct(item.get("median_missingness_rate")),
                    _score_100_text(item.get("score"), 1),
                ]
            )
            examples = item.get("top_missing_features_top20", [])
            if isinstance(examples, list) and examples:
                names = []
                for rec in examples[:10]:
                    if not isinstance(rec, dict):
                        continue
                    fid = _normalize_analysis_tokens(str(rec.get("feature_id", "") or "").strip())
                    if not fid:
                        continue
                    feature_name = str(rec.get("feature_name", "") or "").strip()
                    feature_name = _normalize_analysis_tokens(feature_name)
                    if feature_name and feature_name.lower() in {"unknown", "na", "n/a", "none", "null"}:
                        feature_name = ""
                    try:
                        missing_rate_value = float(rec.get("missing_rate", 0.0))
                    except Exception:
                        missing_rate_value = 0.0
                    if missing_rate_value <= 0.0:
                        continue
                    rate = f"{missing_rate_value * 100:.1f}%"
                    display = feature_name if feature_name else fid
                    names.append(f"{display} ({rate})")
                if names:
                    top_missing_notes.append(
                        f"<li style='margin:3px 0'><code>{_e(_analysis_id_label(item.get('analysis_id', '') or '—'))}</code>: {_e(', '.join(names))}</li>"
                    )
        table = _analysis_table(
            ["Analysis ID", "Total features", "Features >30% missing", "Mean feature missing %", "Median feature missing %", "Score (0-100)"],
            rows,
        )
        if top_missing_notes:
            table += (
                "<div style='margin-top:8px;font-size:.8rem;color:#51656a'>"
                "<strong style='color:#132327'>Highest-missing features (top 10 per analysis):</strong>"
                f"<ul style='margin:6px 0 0 16px;padding:0'>{''.join(top_missing_notes)}</ul>"
                "</div>"
            )
        return table

    for item in per_analysis:
        rows.append(
            [
                _e(_analysis_id_label(item.get("analysis_id", "") or "—")),
                _score_100_text(item.get("score"), 1),
            ]
        )
    return _analysis_table(["Analysis ID", "Score (0-100)"], rows)


def _analytical_metric_rows(
    metrics: list[Any],
    params: dict[str, float] | None = None,
    study_id: Any | None = None,
) -> str:
    params = params or _V2_DEFAULT_PARAMS
    if not metrics:
        return "<p style='color:#51656a;font-style:italic;padding:8px'>No metrics in this section.</p>"

    cards = []
    for m in metrics:
        descriptor = _metric_descriptor(m.name, params)
        desc_html = (
            f"<div style='font-size:.78rem;color:#51656a;line-height:1.45;margin-top:2px'>{_e(descriptor)}</div>"
            if descriptor
            else ""
        )
        tooltip_content = _metric_info_tooltip(m, params, study_id=study_id)
        popup_class = (
            "minfo-popup fair-metadata-popup" if m.name == "fair_study_metadata_compliance"
            else "minfo-popup feature-sample-popup" if m.name == "feature_to_sample_ratio"
            else "minfo-popup"
        )
        info_icon = (
            f"<span class='minfo' tabindex='0'>"
            f"<span class='minfo-icon'>i</span>"
            f"<div class='{popup_class}'>{tooltip_content}</div>"
            f"</span>"
        ) if tooltip_content else ""

        recs_html = ""
        if m.recommendations:
            rec_items = "".join(f"<li style='margin:3px 0'>{_e(rec)}</li>" for rec in m.recommendations)
            recs_html = (
                "<div style='margin-top:8px'>"
                "<div style='font-size:.74rem;text-transform:uppercase;letter-spacing:.06em;color:#995b00'>Recommendations</div>"
                f"<ul style='margin:4px 0 0 16px;padding:0;color:#995b00;font-size:.83rem'>{rec_items}</ul>"
                "</div>"
            )

        if getattr(m, "informational", False):
            score_chip = (
                "<span style='font-size:.75rem;font-weight:700;padding:2px 8px;border-radius:999px;background:rgba(192,57,43,.08);color:#c0392b'>"
                "informational only</span>"
            )
        else:
            score_chip = (
                f"<span style='font-size:.75rem;font-weight:700;padding:2px 8px;border-radius:999px;background:rgba(13,110,110,.08);color:#0d6e6e'>"
                f"aggregate score {_score_100_text(m.score)}/100</span>"
            )
        header_html = (
            "<div style='display:flex;align-items:flex-start;justify-content:space-between;gap:10px'>"
            "<div style='min-width:0'>"
            f"<div style='display:flex;align-items:flex-start;gap:2px'><span style='font-weight:700'>{_e(_metric_display_name(m.name))}</span>{info_icon}</div>"
            f"{desc_html}"
            "</div>"
            f"<div style='display:flex;align-items:center;gap:8px;flex-shrink:0'>{score_chip}{_status_badge(m.status)}</div>"
            "</div>"
        )

        breakdown_html = _analytical_breakdown_html(m)
        cards.append(
            "<div style='padding:14px 14px 10px;border-radius:14px;border:1px solid rgba(19,35,39,.1);"
            "background:rgba(255,255,255,.74);margin-bottom:10px'>"
            f"{header_html}"
            f"<div style='margin-top:8px;font-size:.86rem;color:#132327'>{_e(m.summary)}</div>"
            f"{breakdown_html}"
            f"{recs_html}"
            "</div>"
        )
    return "".join(cards)


# ---------------------------------------------------------------------------
# Overview helpers: ML difficulty, confidence, risks panel
# ---------------------------------------------------------------------------

def _ml_difficulty(summary: dict[str, Any]) -> tuple[str, str, str]:
    """Return (level, hex_color, reason_text) for estimated ML task difficulty.

    Difficulty is an a-priori assessment of how hard it will be to build a
    useful ML model from this study, based on ML-eligible sample-set size, class balance,
    feature-to-sample ratio, missingness, annotation quality, and number of
    classes.  It is independent of the ReadinessScore.
    """
    n_bio = summary.get("n_biological_samples") or 0
    n_features = summary.get("n_features") or 0
    n_matrices = summary.get("n_feature_matrices") or 0
    n_classes = summary.get("n_classes") or 0
    class_counts = summary.get("class_counts") or {}
    per_analysis = summary.get("per_analysis") or []

    if n_matrices == 0 or n_bio == 0:
        return ("Unknown", "#51656a", "No tabular data available")

    counts = list(class_counts.values())
    balance = (min(counts) / max(counts)) if len(counts) >= 2 and max(counts) > 0 else None
    fsr = n_features / n_bio if n_bio > 0 else 0

    source_aware_missing = summary.get("_overview_missingness_rate")
    if source_aware_missing is not None:
        miss_pct = _safe_missing_rate(source_aware_missing) * 100
    else:
        total_cells = sum(a.get("n_samples", 0) * a.get("n_features", 0) for a in per_analysis)
        miss_pct = 0.0
        if total_cells > 0:
            miss_pct = sum(
                _safe_missing_rate(a.get("missing_rate", 0)) * a.get("n_samples", 0) * a.get("n_features", 0)
                for a in per_analysis
            ) / total_cells * 100

    has_named = any(a.get("annotation_tier") in ("named", "mixed") for a in per_analysis)

    hard: list[str] = []
    easy: list[str] = []

    # Cohort size
    if n_bio < 30:
        hard.append(f"very small ML-eligible sample set ({n_bio} samples)")
    elif n_bio >= 100:
        easy.append(f"adequate ML-eligible sample set ({n_bio} samples)")

    # Class balance
    if n_classes < 2:
        hard.append("fewer than 2 classes — classification not possible")
    elif balance is not None:
        if balance < 0.2:
            hard.append(f"strong class imbalance ({balance:.2f})")
        elif balance < 0.4:
            hard.append(f"moderate class imbalance ({balance:.2f})")
        elif balance >= 0.5:
            easy.append("balanced classes")

    # Feature-to-sample ratio
    if fsr > 200:
        hard.append(f"very high feature-to-sample ratio ({fsr:.0f}:1)")
    elif fsr > 50:
        hard.append(f"high feature-to-sample ratio ({fsr:.0f}:1)")
    elif fsr <= 10 and fsr > 0:
        easy.append(f"low F:S ratio ({fsr:.1f}:1)")

    # Missingness
    if miss_pct > 30:
        hard.append(f"high missingness ({miss_pct:.0f}%)")
    elif miss_pct > 15:
        hard.append(f"moderate missingness ({miss_pct:.0f}%)")
    elif miss_pct < 5:
        easy.append("low missingness")

    # Annotation quality
    if not has_named:
        hard.append("mz/RT-only features (limited interpretability)")
    else:
        easy.append("named metabolite annotations")

    # Class cardinality
    if n_classes > 20:
        hard.append(f"excessive classes ({n_classes}) — likely label parsing issue")
    elif n_classes > 10:
        hard.append(f"high class cardinality ({n_classes} classes)")

    if len(hard) >= 3:
        return ("Hard", "#8f2d2d", "; ".join(hard[:3]))
    if len(hard) >= 1:
        mixed = hard + [e for e in easy if e not in hard]
        return ("Moderate", "#995b00", "; ".join(mixed[:3]) if mixed else "mixed indicators")
    if len(easy) >= 2:
        return ("Easy", "#196b4a", "; ".join(easy[:3]))
    return ("Moderate", "#995b00", "mixed indicators")


def _readiness_confidence(summary: dict[str, Any], section_scores: dict[str, float]) -> tuple[str, str, str]:
    """Return (level, hex_color, reason_text) confidence indicator for the ReadinessScore.

    Confidence reflects how trustworthy the composite score is — not the score
    itself.  A study can score high but have low confidence if the score is
    derived from very few signals (e.g. metadata-only, tiny ML-eligible sample set, or most
    sections falling back to neutral defaults).
    """
    n_matrices = summary.get("n_feature_matrices") or 0
    n_bio = summary.get("n_biological_samples") or 0

    # Hard floors: no feature data or very small ML-eligible sample set → low confidence
    if n_matrices == 0:
        return (
            "Low",
            "#8f2d2d",
            "no usable feature matrix (n_feature_matrices = 0); score is metadata-driven",
        )
    if n_bio < 10:
        return (
            "Low",
            "#8f2d2d",
            f"very small ML-eligible sample set: {n_bio} ML-eligible samples (< 10 hard floor)",
        )

    # Count how many of the 6 scored dimensions produced a meaningful signal
    # (i.e. are not sitting at a neutral default).  Sections near 0.5 are
    # likely running on absent-data neutral defaults.
    reasons: list[str] = []
    n_informative = 0
    for key in ("structural", "metadata", "analytical", "annotation", "cohort", "ml_feasibility"):
        if key == "ml_feasibility":
            s = section_scores.get("ml_feasibility", section_scores.get("ml", 0))
        else:
            s = section_scores.get(key, 0)
        if s > 0.55 or s < 0.45:
            n_informative += 1

    if n_informative <= 2:
        reasons.append(f"only {n_informative}/6 dimensions have meaningful signal")
    if n_bio < 30:
        reasons.append(f"small ML-eligible sample set ({n_bio} samples)")

    meta_score = section_scores.get("metadata", 0)
    analytical_score = section_scores.get("analytical", 0)
    if meta_score < 0.5:
        reasons.append("sparse metadata")
    if analytical_score < 0.5:
        reasons.append("weak analytical QC signal")

    if len(reasons) >= 2:
        return ("Low", "#8f2d2d", "; ".join(reasons[:2]))
    if n_informative >= 5 and n_bio >= 50 and meta_score >= 0.65:
        return (
            "High",
            "#196b4a",
            f"meets high-confidence thresholds: informative dimensions {n_informative}/6 (>= 5), "
            f"ML-eligible samples = {n_bio} (>= 50), metadata = {_score_100_text(meta_score)} (>= 65)",
        )
    if reasons:
        reason_text = reasons[0]
    elif n_informative < 5:
        reason_text = f"{n_informative}/6 dimensions informative (target >= 5 for high confidence)"
    elif n_bio < 50:
        reason_text = f"ML-eligible sample-set size {n_bio} below high-confidence target (>= 50)"
    elif meta_score < 0.65:
        reason_text = f"metadata score {_score_100_text(meta_score)} below high-confidence target (>= 65)"
    else:
        reason_text = "does not meet all high-confidence criteria"
    return ("Moderate", "#995b00", reason_text)


def _risks_panel(report: Any) -> str:
    """Collect reuse caveats across all sections into a compact, source-aware list."""
    risks: list[dict[str, str]] = []
    all_sections: list[tuple[str, Any]] = [
        ("Structural", report.schema_validation),
        ("Metadata and FAIR Reusability", report.metadata_readiness),
        ("Analytical QC", report.analytical_readiness),
        ("Annotation / Interoperability", report.annotation_readiness),
        ("Label Structure and Class Support", report.cohort_bias),
        ("ML Task Readiness", report.ml_readiness),
    ]
    for section_name, metrics in all_sections:
        for m in (metrics or []):
            if getattr(m, "informational", False):
                continue
            if m.status in ("fail", "warn") and m.recommendations:
                risks.append(
                    {
                        "status": m.status,
                        "section": section_name,
                        "metric": _metric_display_name(m.name),
                        "metric_name": str(getattr(m, "name", "")),
                        "recommendation": str(m.recommendations[0]),
                    }
                )

    # De-duplicate repeated recommendation text; keep fail items over warn items.
    dedup: dict[str, dict[str, str]] = {}
    for item in risks:
        key = item["recommendation"].strip().lower()
        current = dedup.get(key)
        if current is None:
            dedup[key] = item
            continue
        if current["status"] != "fail" and item["status"] == "fail":
            dedup[key] = item

    risks_unique = list(dedup.values())
    status_rank = {"fail": 0, "warn": 1}
    risks_unique.sort(key=lambda item: (status_rank.get(item["status"], 2), item["section"], item["metric"]))

    if not risks_unique:
        return (
            "<div style='background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.1);"
            "border-radius:20px;padding:18px;margin-top:14px'>"
            "<h4 style='margin:0 0 8px;font-size:.9rem;text-transform:uppercase;letter-spacing:.06em;color:#196b4a'>"
            "ML-reuse caveats</h4>"
            "<p style='font-size:.85rem;color:#196b4a;margin:0'>"
            "No major ML-reuse caveats detected under the current MERIT-ML profile.</p>"
            "</div>"
        )

    def _caveat_label(item: dict[str, str]) -> tuple[str, str, str]:
        status = str(item.get("status", "")).strip().lower()
        section = str(item.get("section", ""))
        metric_name = str(item.get("metric_name", ""))
        parsed_source_metrics = {
            "fair_study_metadata_compliance",
            "fair_metabolite_identifier_resolvability",
            "mass_rt_like_metadata_presence",
            "required_field_completeness",
            "feature_annotation_type",
            "annotation_ambiguity_burden",
            "unknown_feature_fraction",
            "redundancy",
        }
        if status == "fail":
            if section in {"Metadata and FAIR Reusability", "Annotation / Interoperability"} or metric_name in parsed_source_metrics:
                return ("Not detected in parsed source", "#995b00", "#fdf3e3")
            if section in {"Structural", "Label Structure and Class Support", "ML Task Readiness"}:
                return ("ML-reuse blocker", "#8f2d2d", "#fdeaea")
            return ("Required for selected MERIT-ML profile", "#8f2d2d", "#fdeaea")
        if section in {"Metadata and FAIR Reusability", "Annotation / Interoperability"} or metric_name in parsed_source_metrics:
            return ("Verification recommended", "#995b00", "#fdf3e3")
        return ("ML-reuse caveat", "#995b00", "#fdf3e3")

    items = "".join(
        (
            lambda label, fg, bg: (
                f"<li style='display:flex;flex-wrap:wrap;gap:8px 9px;align-items:flex-start;padding:8px 0;"
                f"border-bottom:1px solid rgba(19,35,39,.06)'>"
                f"<span style='font-size:.68rem;font-weight:800;padding:3px 7px;border-radius:999px;"
                f"flex-shrink:0;margin-top:1px;background:{bg};color:{fg};border:1px solid {fg}22;"
                f"white-space:nowrap;max-width:100%;overflow-wrap:anywhere'>{_e(label)}</span>"
                f"<span style='font-size:.82rem;line-height:1.48;color:#132327;flex:1 1 260px;min-width:0'>"
                f"<strong style='color:#263a3e'>{_e(item['section'])} · {_e(item['metric'])}</strong><br>"
                f"{_e(item['recommendation'])}</span>"
                f"</li>"
            )
        )(*_caveat_label(item))
        for item in risks_unique[:6]
    )
    more = ""
    if len(risks_unique) > 6:
        more = (
            f"<p style='font-size:.78rem;color:#51656a;margin:8px 0 0'>"
            f"+{len(risks_unique) - 6} additional caveats — see section tabs for parsed evidence and verification links.</p>"
        )

    return (
        "<div style='background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.1);"
        "border-radius:20px;padding:18px;margin-top:14px'>"
        "<h4 style='margin:0 0 8px;font-size:.9rem;text-transform:uppercase;letter-spacing:.06em;"
        "color:#995b00'>ML-reuse caveats</h4>"
        "<p style='margin:0 0 10px;color:#51656a;font-size:.82rem;line-height:1.5'>"
        "These caveats are derived from MERIT-ML parsing of public source metadata and tabular matrices. "
        "They are not judgments of the original study purpose, scientific value, or repository quality. "
        "Source-sensitive items should be verified on the original Metabolomics Workbench record.</p>"
        f"<ul style='list-style:none;margin:0;padding:0'>{items}</ul>{more}"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Tabbed report
# ---------------------------------------------------------------------------

def _source_availability_panel(source_avail: dict[str, Any], source_tier: str) -> str:
    """Render a compact source-availability card for the overview tab."""
    if not source_avail:
        return ""
    dt = source_avail.get("datatable_count", 0)
    mw = source_avail.get("mwtab_count", 0)
    ut = source_avail.get("untarg_data_count", 0)
    tier_label = "Tier 1 (named metabolites)" if source_tier == "tier1" else "Tier 2 (mz/RT peak table)"
    tier_color = "#196b4a" if source_tier == "tier1" else "#1565C0"

    def _src_badge(label: str, count: int, active: bool) -> str:
        fg = "#132327" if active else "#9aafb2"
        bg = "rgba(13,110,110,.10)" if active else "rgba(19,35,39,.04)"
        border = "rgba(13,110,110,.3)" if active else "rgba(19,35,39,.1)"
        indicator = f"<span style='display:inline-block;width:7px;height:7px;border-radius:50%;background:{'#196b4a' if active else '#b0bec5'};margin-right:5px;vertical-align:middle'></span>"
        return (
            f"<div style='padding:8px 12px;border-radius:10px;background:{bg};"
            f"border:1px solid {border};display:flex;align-items:center;gap:6px'>"
            f"{indicator}"
            f"<span style='font-weight:700;font-size:.82rem;color:{fg}'>{_e(label)}</span>"
            f"<span style='margin-left:auto;font-size:.78rem;color:{fg};font-variant-numeric:tabular-nums'>"
            f"{'%d analysis' % count if count == 1 else '%d analyses' % count}</span>"
            f"</div>"
        )

    return (
        f"<div style='background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.1);"
        f"border-radius:16px;padding:16px;margin-bottom:14px'>"
        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:10px'>"
        f"<h4 style='margin:0;font-size:.85rem;text-transform:uppercase;letter-spacing:.06em'>Data Sources</h4>"
        f"<span style='font-size:.72rem;font-weight:700;padding:2px 8px;border-radius:20px;"
        f"background:{tier_color}18;color:{tier_color};border:1px solid {tier_color}44'>"
        f"Scored as {_e(tier_label)}</span>"
        f"</div>"
        f"<div style='display:flex;flex-direction:column;gap:6px'>"
        f"{_src_badge('Datatable (Tier 1)', dt, dt > 0)}"
        f"{_src_badge('mwTab text (Tier 1)', mw, mw > 0)}"
        f"{_src_badge('Untarg data (Tier 2)', ut, ut > 0)}"
        f"</div>"
        f"<p style='margin:10px 0 0;font-size:.75rem;color:#51656a;line-height:1.5'>"
        f"<strong style='color:#132327'>Tier 1</strong> (datatable / mwTab) — metabolites are named and annotated; annotation quality metrics contribute fully to the score. "
        f"<strong style='color:#132327'>Tier 2</strong> (untarg_data) — features are identified by m/z and retention time only, not by name; interpret annotation-oriented metrics in this context because metabolite identity is absent. "
        f"Where multiple sources exist, each is scored independently — use the source selector above to compare.</p>"
        f"<p style='margin:6px 0 0;font-size:.74rem;color:#51656a;background:rgba(13,110,110,.06);border-radius:8px;"
        f"padding:5px 8px;line-height:1.4'>"
        f"<strong>Source-aware zero handling:</strong> "
        f"datatable zeros = valid (curated fill); mwTab/untarg zeros = missing (below detection).</p>"
        f"</div>"
    )


def _matrix_adjust_tab_html(
    model: dict[str, Any] | None,
    overrides: dict[str, dict[str, Any]] | None,
    source_key: str | None,
    tab_sfx: str,
) -> str:
    model = model or {}
    overrides = overrides or {}
    panel_id = f"matrix-adjust-{tab_sfx}"
    if not model.get("samples"):
        msg = model.get("message") or "Full sample list is unavailable for this study in the current browser session."
        return (
            "<div style='padding:18px;border:1px dashed rgba(19,35,39,.16);border-radius:16px;"
            "background:rgba(255,255,255,.66)'>"
            "<h4 style='margin:0 0 8px;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em'>"
            "Adjust Matrix Properties</h4>"
            f"<p style='margin:0;color:#51656a;line-height:1.55'>{_e(msg)} "
            "This view uses parsed sample rows when available and does not edit source files.</p>"
            "</div>"
        )

    rows = _v2_effective_sample_rows(model, overrides, source_key)
    stats = _v2_label_stats(rows)
    source_label = {
        "datatable": "Datatable",
        "mwtab": "mwTab",
        "untarg_data": "Untarg data",
    }.get(str(source_key or ""), str(source_key or "active source"))
    base_label_options = sorted(set(model.get("class_labels", []) or []))
    override_label_options = {
        str(item.get("label", "") or "").strip()
        for item in overrides.values()
        if str(item.get("label", "") or "").strip()
    }
    label_options = sorted(set(base_label_options) | override_label_options)
    custom_label_options = sorted(label for label in override_label_options if label not in set(base_label_options))
    if not label_options:
        label_options = [""]
    label_options_json = json.dumps(label_options).replace("</", "<\\/")
    custom_labels_json = json.dumps(custom_label_options).replace("</", "<\\/")
    row_payload: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row.get("sample_id", "") or "").strip()
        default_label = str(row.get("default_label", "") or "")
        native_label = str(row.get("native_label", "") or default_label or "")
        current_label = str(row.get("label", "") or "")
        default_eligible = bool(row.get("default_eligible", False))
        current_eligible = bool(row.get("eligible", False))
        current_excluded = bool(row.get("excluded", False))
        current_status = "exclude" if current_excluded else ("1" if current_eligible else "0")
        sources = ", ".join(str(x) for x in row.get("sources", []) or [])
        analyses = row.get("analyses_by_source", {}) or {}
        analysis_text = ", ".join(str(x) for x in analyses.get(source_key or "", []) or [])
        row_payload.append({
            "sample_id": sid,
            "default_label": default_label,
            "native_label": native_label,
            "label": current_label,
            "default_eligible": default_eligible,
            "eligible": current_eligible,
            "excluded": current_excluded,
            "status": current_status,
            "sources": sources,
            "analysis_text": analysis_text,
        })
    row_payload_json = json.dumps(row_payload, ensure_ascii=False).replace("</", "<\\/")
    active_override_count = len(overrides)
    return (
        f"<div class='matrix-adjust-panel' id='{_e(panel_id)}' data-source='{_e(str(source_key or ''))}' "
        f"data-label-options='{_e(label_options_json)}'>"
        "<div style='display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:14px'>"
        "<div>"
        "<h4 style='margin:0 0 6px;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em'>"
        "Adjust Matrix Properties</h4>"
        f"<p style='margin:0;color:#51656a;line-height:1.55;max-width:860px'>Showing all samples detected for "
        f"<strong>{_e(source_label)}</strong>. Change class labels, mark samples as ML-eligible / not ML-eligible, "
        "or exclude samples from this analysis, then apply.</p>"
        "</div>"
        f"<div style='display:flex;gap:8px;flex-wrap:wrap'>"
        f"<span style='padding:7px 10px;border-radius:999px;background:rgba(13,110,110,.08);color:#0d6e6e;font-weight:800;font-size:.78rem'>{stats['n_eligible']} ML-eligible</span>"
        f"<span style='padding:7px 10px;border-radius:999px;background:rgba(17,62,82,.08);color:#113e52;font-weight:800;font-size:.78rem'>{stats['n_classes']} classes</span>"
        f"<span style='padding:7px 10px;border-radius:999px;background:rgba(210,125,45,.12);color:#995b00;font-weight:800;font-size:.78rem'>{active_override_count} override(s)</span>"
        "</div>"
        "</div>"
        "<div style='display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,320px) auto auto;gap:10px;align-items:end;margin-bottom:12px'>"
        "<label style='margin:0;font-size:.72rem;color:#51656a'>Search samples/classes"
        f"<input type='search' class='matrix-search' data-panel='{_e(panel_id)}' placeholder='Type sample ID or class label...' "
        "style='margin-top:5px'></label>"
        "<label style='margin:0;font-size:.72rem;color:#51656a'>Add new class group"
        f"<input type='text' class='matrix-new-class' data-panel='{_e(panel_id)}' placeholder='e.g. Treatment:High dose' style='margin-top:5px'></label>"
        f"<button type='button' class='v2-reset matrix-add-class' data-panel='{_e(panel_id)}'>Add class group</button>"
        "<button type='submit' form='run-form' class='v2-apply matrix-apply'>Apply matrix changes</button>"
        "</div>"
        f"<div class='matrix-custom-class-list' data-panel='{_e(panel_id)}' data-custom-labels='{_e(custom_labels_json)}' "
        "style='display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:-2px 0 12px;color:#51656a;font-size:.76rem'>"
        "</div>"
        f"<script type='application/json' class='matrix-row-data'>{row_payload_json}</script>"
        "<div class='matrix-lazy-note' style='margin:0 0 10px;padding:9px 11px;border-radius:12px;"
        "background:rgba(13,110,110,.06);border:1px solid rgba(13,110,110,.14);color:#51656a;font-size:.8rem;line-height:1.45'>"
        "Sample rows are loaded when this tab is opened, keeping large reports responsive.</div>"
        "<div style='max-height:520px;overflow:auto;border:1px solid rgba(19,35,39,.1);border-radius:14px;background:rgba(255,255,255,.72)'>"
        "<table style='width:100%;border-collapse:collapse;font-size:.84rem'>"
        "<thead style='position:sticky;top:0;background:#f7f3ea;z-index:1'>"
        "<tr>"
        "<th style='padding:8px;text-align:left;border-bottom:1px solid rgba(19,35,39,.12)'>Sample ID</th>"
        "<th style='padding:8px;text-align:left;border-bottom:1px solid rgba(19,35,39,.12)'>Source / analysis</th>"
        "<th style='padding:8px;text-align:left;border-bottom:1px solid rgba(19,35,39,.12)'>Native label</th>"
        "<th style='padding:8px;text-align:left;border-bottom:1px solid rgba(19,35,39,.12)'>Class label</th>"
        "<th style='padding:8px;text-align:left;border-bottom:1px solid rgba(19,35,39,.12)'>ML category</th>"
        "</tr></thead>"
        "<tbody class='matrix-sample-body'></tbody>"
        "</table>"
        "</div>"
        "<div style='display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-top:12px'>"
        "<p style='margin:0;color:#7b8b90;font-size:.78rem;line-height:1.45'>Default values come from MERIT-ML's parsed sample metadata and current QC/blank filtering. "
        "Use Reset to discard all manual matrix-property overrides.</p>"
        "<button type='submit' form='run-form' class='v2-reset matrix-reset-overrides'>Reset sample overrides</button>"
        "</div>"
        "</div>"
    )


def _download_tabular_data_tab_html(
    study_id: str,
    source_key: str | None,
    tab_sfx: str,
    analysis_ids: list[str] | None = None,
) -> str:
    source_label = {
        "datatable": "Datatable",
        "mwtab": "mwTab",
        "untarg_data": "Untarg data",
        "summary": "available sources",
    }.get(str(source_key or ""), "available sources")
    analysis_ids = sorted({str(aid or "").strip().upper() for aid in (analysis_ids or []) if str(aid or "").strip()})
    if analysis_ids:
        analysis_list_html = (
            "<div style='margin:0 0 12px;padding:10px 12px;border-radius:13px;background:rgba(255,255,255,.72);"
            "border:1px solid rgba(19,35,39,.08)'>"
            "<strong style='display:block;color:#132327;margin-bottom:6px;font-size:.86rem'>Analyses detected in this MERIT-ML report</strong>"
            "<div style='display:flex;gap:6px;flex-wrap:wrap'>"
            + "".join(
                f"<span style='padding:3px 8px;border-radius:999px;background:rgba(13,110,110,.08);color:#0d6e6e;font-weight:800;font-size:.74rem'>{_e(aid)}</span>"
                for aid in analysis_ids
            )
            + "</div>"
            "</div>"
        )
    else:
        analysis_list_html = (
            "<div style='margin:0 0 12px;padding:10px 12px;border-radius:13px;background:rgba(255,255,255,.72);"
            "border:1px solid rgba(19,35,39,.08);color:#51656a;font-size:.84rem'>"
            "Analyses will be discovered from the Metabolomics Workbench REST API when the ZIP is generated.</div>"
        )
    return (
        "<div class='ml-data-download-panel' "
        f"data-study-id='{_e(study_id)}' data-source='{_e(str(source_key or ''))}'>"
        "<div style='display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:14px'>"
        "<div>"
        "<h4 style='margin:0 0 6px;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em'>"
        "DOWNLOAD MERIT-ML-DERIVED TABULAR EXPORT</h4>"
        "<p style='margin:0;color:#51656a;line-height:1.55;max-width:920px'>"
        "MERIT-ML uses the Metabolomics Workbench REST API at download time to generate source-specific tabular "
        "exports for reproducibility of this assessment. Each parseable source table is used to create a "
        "MERIT-ML-derived, machine-learning-compatible TSV with rows as samples and columns as metabolite features. "
        "Source matrix measurement values are preserved, while MERIT-ML may add aligned class labels, sample "
        "inclusion/exclusion indicators, and source manifests."
        "</p>"
        "<p style='margin:8px 0 0;color:#51656a;line-height:1.55;max-width:920px'>"
        "Only ML-eligible samples under the current assessment settings are exported. Any active Adjust Matrix "
        "Properties settings are applied before TSV files are generated. The exported files should be interpreted "
        "together with the downloaded MERIT-ML Assessment JSON, which records the source, settings, scoring profile, "
        "and citation/provenance summary."
        "</p>"
        "</div>"
        "</div>"
        "<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-bottom:14px'>"
        "<div style='padding:13px;border:1px solid rgba(13,110,110,.16);border-radius:14px;background:rgba(13,110,110,.06)'>"
        "<strong style='display:block;color:#0d6e6e;margin-bottom:5px'>What is exported</strong>"
        "<ul style='margin:0;padding-left:18px;color:#51656a;line-height:1.5;font-size:.86rem'>"
        "<li>Parseable source-specific tables accessible through the Metabolomics Workbench REST response at download time.</li>"
        "<li>One TSV per source and analysis when a parseable matrix is returned.</li>"
        "<li>Source and citation manifests including Study ID, Project ID, Project DOI where detected, REST source URL, access date, MERIT-ML version, and matrix dimensions.</li>"
        "<li>Skipped-sample and label-alignment manifests for reproducibility.</li>"
        "</ul>"
        "</div>"
        "<div style='padding:13px;border:1px solid rgba(210,125,45,.18);border-radius:14px;background:rgba(210,125,45,.08)'>"
        "<strong style='display:block;color:#995b00;margin-bottom:5px'>What is not changed</strong>"
        "<ul style='margin:0;padding-left:18px;color:#51656a;line-height:1.5;font-size:.86rem'>"
        "<li>Source matrix measurement values are not transformed, normalized, scaled, imputed, or feature-remediated.</li>"
        "<li>No Metabolomics Workbench repository files are modified.</li>"
        "<li>MERIT-ML does not maintain a persistent server-side mirror of generated exports.</li>"
        "<li>Exported files are MERIT-ML-derived assessment inputs, not official Metabolomics Workbench files.</li>"
        "</ul>"
        "</div>"
        "<div style='padding:13px;border:1px solid rgba(143,45,45,.16);border-radius:14px;background:rgba(143,45,45,.055)'>"
        "<strong style='display:block;color:#8f2d2d;margin-bottom:5px'>Citation and reuse</strong>"
        "<p style='margin:0;color:#51656a;line-height:1.5;font-size:.86rem'>"
        "This export is generated from public Metabolomics Workbench/NMDR source records. Users should cite the "
        "original Metabolomics Workbench study/project, including the Study ID/accession, Project ID, Project DOI where available, "
        "and associated publication(s) where applicable. MERIT-ML-derived assessment scores and exports should be cited "
        "separately from the original source data."
        "</p>"
        "</div>"
        "</div>"
        "<div style='margin:0 0 12px;padding:10px 12px;border-radius:13px;background:rgba(245,241,232,.74);"
        "border:1px solid rgba(19,35,39,.08)'>"
        "<strong style='display:block;color:#132327;margin-bottom:6px;font-size:.86rem'>Sources attempted at download time</strong>"
        "<div style='display:flex;gap:7px;flex-wrap:wrap'>"
        "<span style='padding:4px 9px;border-radius:999px;background:rgba(13,110,110,.10);color:#0d6e6e;font-weight:900;font-size:.76rem'>Datatable</span>"
        "<span style='padding:4px 9px;border-radius:999px;background:rgba(13,110,110,.10);color:#0d6e6e;font-weight:900;font-size:.76rem'>mwTab</span>"
        "<span style='padding:4px 9px;border-radius:999px;background:rgba(17,62,82,.10);color:#113e52;font-weight:900;font-size:.76rem'>Untarg data</span>"
        "</div>"
        "</div>"
        f"{analysis_list_html}"
        "<form class='ml-data-download-form' method='post' action='/download/ml-ready-data' style='margin:0'>"
        f"<input type='hidden' name='study_id' value='{_e(study_id)}'>"
        f"<input type='hidden' name='analysis_ids' value='{_e(json.dumps(analysis_ids))}'>"
        "<input type='hidden' name='matrix_overrides' class='ml-download-overrides' value='{}'>"
        "<button type='submit' class='v2-apply' style='padding:10px 14px;border-radius:13px'>"
        "Generate MERIT-ML Export ZIP</button>"
        "<span style='display:inline-block;margin-left:10px;color:#7b8b90;font-size:.78rem;line-height:1.4'>"
        "Generated on demand from Metabolomics Workbench REST; large studies may take a moment."
        "</span>"
        "</form>"
        "</div>"
    )


def _tabbed_report(report: Any, readiness_score: dict[str, Any],
                   source_avail: dict[str, Any] | None = None,
                   source_tier: str = "tier1",
                   chart_suffix: str = "",
                   scoring_params: dict[str, float] | None = None,
                   matrix_model: dict[str, Any] | None = None,
                   matrix_overrides: dict[str, dict[str, Any]] | None = None,
                   source_key: str | None = None,
                   precomputed_root: str | Path | None = None,
                   study_design_context: dict[str, Any] | None = None) -> str:
    """Render the full tabbed assessment panel for one source.

    chart_suffix scopes DOM IDs when multiple source panels coexist
    (e.g. ``chart_suffix="_datatable"`` → ``id='radar-chart_datatable'``).
    """
    radar_id = f"radar-chart{chart_suffix}"
    scoring_params = scoring_params or _V2_DEFAULT_PARAMS
    precomputed_root = precomputed_root or _default_precomputed_root()
    summary = dict(report.ingestion_summary or {})
    study_id = summary.get("study_id")
    source_aware_missingness = _source_aware_missingness_rate(report)
    if source_aware_missingness is not None:
        summary["_overview_missingness_rate"] = source_aware_missingness
    tab_sfx  = chart_suffix.lstrip("_") or "main"  # unique tab namespace per source

    sections = [
        ("Adjust Matrix Properties", "matrix", None),
        ("Overview", "overview", None),
        ("Structural", "structural", report.schema_validation),
        ("Metadata and FAIR Reusability", "metadata", report.metadata_readiness),
        ("Analytical QC", "analytical", report.analytical_readiness),
        ("Annotation / Interoperability", "annotation", report.annotation_readiness),
        ("Label Structure and Class Support", "cohort", report.cohort_bias),
        ("ML Task Readiness", "ml", report.ml_readiness),
        ("Derived ML Assessment Inputs", "download", None),
    ]

    tab_buttons = "".join(
        f"<button class='tab-btn tab-btn-{tab_sfx}' data-tab='{tid}-{tab_sfx}' "
        f"onclick='switchTab(\"{tid}-{tab_sfx}\",\"{tab_sfx}\")'>{_e(label)}</button>"
        for label, tid, _ in sections
    )

    # Radar chart data for Plotly (section diagnostics only)
    dim_defs = [
        ("structural", "Structural"),
        ("metadata", "Metadata"),
        ("analytical", "Analytical QC"),
        ("annotation", "Annotation / Interoperability"),
        ("cohort", "Label Structure and Class Support"),
        ("ml_feasibility", "ML Task Readiness"),
    ]
    dim_keys = [k for k, _ in dim_defs]
    dim_labels = [lbl for _, lbl in dim_defs]
    radar_labels = [
        "Structural",
        "Metadata",
        "Analytical<br>QC",
        "Annotation /<br>Interoperability",
        "Label Structure<br>and Class Support",
        "ML Task<br>Readiness",
    ]
    _ss = readiness_score.get("section_scores", {}) or {}
    dim_scores_norm = []
    for key in dim_keys:
        if key == "ml_feasibility":
            dim_scores_norm.append(round(float(_ss.get("ml_feasibility", _ss.get("ml", 0.0))), 3))
        else:
            dim_scores_norm.append(round(float(_ss.get(key, 0.0)), 3))
    dim_scores = [round(score * 100.0, 1) for score in dim_scores_norm]
    radar_data = json.dumps({
        "r": dim_scores + [dim_scores[0]],
        "theta": radar_labels + [radar_labels[0]],
        "score": readiness_score.get("core_ml_readiness_score", readiness_score["score"]),
        "band": _v2_band_label(readiness_score.get("final_band", readiness_score["band"])),
    })

    # Overview tab
    core_sections = {"structural", "analytical", "annotation", "cohort", "ml_feasibility"}
    section_score_cards = "".join(
        f"<div style='padding:12px 14px;border-radius:14px;background:rgba(255,255,255,.8);border:1px solid rgba(19,35,39,.1)'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;gap:8px'>"
        f"<span style='font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a'>{_e(dim_labels[i])}</span>"
        f"<span style='display:inline-flex;align-items:center;justify-content:center;"
        f"white-space:nowrap;line-height:1;min-width:42px;font-size:.66rem;font-weight:700;"
        f"padding:2px 7px;border-radius:999px;"
        f"background:{'rgba(25,107,74,.14)' if dim_keys[i] in core_sections else 'rgba(21,101,192,.12)'};"
        f"color:{'#196b4a' if dim_keys[i] in core_sections else '#1565C0'}'>"
        f"{'core' if dim_keys[i] in core_sections else 'reuse'}</span>"
        f"</div>"
        f"{_score_bar(dim_scores_norm[i])}"
        f"</div>"
        for i in range(len(dim_keys))
    )

    action_items = "".join(
        f"<li style='padding:8px 12px;border-left:3px solid #d27d2d;background:rgba(245,241,232,.76);border-radius:0 10px 10px 0;margin-bottom:6px;font-size:.88rem'>{_e(a)}</li>"
        for a in readiness_score["actions"]
    )

    final_band = readiness_score.get("final_band", readiness_score.get("band", "Not Ready"))
    provisional_band = readiness_score.get("provisional_band", final_band)
    final_band_label = _v2_band_label(final_band)
    provisional_band_label = _v2_band_label(provisional_band)
    core_score = float(readiness_score.get("core_ml_readiness_score", readiness_score.get("score", 0.0)))
    reusability_score = float(readiness_score.get("reusability_score", 0.0))
    core_score_display = _score_100_text(core_score)
    gate_summary = readiness_score.get("gate_summary", {}) or {}
    gates = readiness_score.get("gates", []) or []
    gate_ceiling = readiness_score.get("gate_ceiling")
    gate_ceiling_label = _v2_band_label(gate_ceiling) if gate_ceiling else ""

    band_color = {
        "Ready": "#196b4a",
        "Conditional": "#995b00",
        "Fragile": "#8f2d2d",
        "Not Ready": "#8f2d2d",
        "No Data": "#51656a",
    }.get(final_band, "#51656a")
    tier_label = "Core ML readiness (annotation included; FAIR reported separately)"

    reusability_tip = _mini_info_icon(
        "Reusability score summarizes FAIR-style reuse signals only: study metadata completeness/documentation "
        "metabolite identifier resolvability, and mass/RT-like metabolite metadata presence. "
        "It is reported separately from core ML readiness and is displayed on the same 0–100 scale.",
        size=11,
    )
    core_score_tip = _mini_info_icon(
        "Displayed on a 0–100 scale. Band cutoffs are shown in the sidebar on the same 0–100 display scale.",
        size=11,
    )
    provisional_band_tip = _mini_info_icon(
        "Provisional band is derived from the core ML readiness score before feasibility-gate ceilings are applied. "
        "Final band may be limited by warn or fail gate outcomes.",
        size=11,
    )
    gate_summary_tip = _mini_info_icon(
        "Gate summary counts feasibility-gate outcomes. Warn or fail gates can limit the final "
        "readiness band even when section scores are strong.",
        size=11,
    )
    recommendation_tip = _mini_info_icon(
        "This recommendation is generated from weak section scores and gate outcomes. "
        f"Sections scoring below {_v2_fmt_score(scoring_params['band_conditional_min'])} and any warn/fail gates "
        "are translated into priority actions.",
        size=11,
    )
    gate_display_names = {
        "G1": "tabular data availability",
        "G2": "sufficient ML-eligible sample count",
        "G3": "deposited groups",
        "G4": "minimum per group support",
        "G5": "missingness within reuse range",
    }
    gate_tips = {
        "G1": "Passes when at least one usable tabular feature matrix is present.",
        "G2": (
            f"Passes when ML-eligible sample count is at least {_v2_fmt_param(scoring_params['g2_sample_pass'])}; "
            f"warns down to {_v2_fmt_param(scoring_params['g2_sample_fail_below'])}; "
            f"fails below {_v2_fmt_param(scoring_params['g2_sample_fail_below'])}. "
            "This is a supervised ML feasibility gate: triplicate mechanistic designs can be scientifically valid, "
            "but n=3 does not support reliable classifier training, validation, or feature selection."
        ),
        "G3": "Passes when at least two deposited label groups define a supervised endpoint.",
        "G4": (
            f"Passes when the smallest class has at least {_v2_fmt_param(scoring_params['g4_class_pass'])} samples "
            f"and there are at least 2 classes; warns down to {_v2_fmt_param(scoring_params['g4_class_warn_min'])}; "
            "fails below that range."
        ),
        "G5": (
            f"Passes when median sample-level missingness is <= {_v2_fmt_param(scoring_params['g5_missing_pass_pct'], pct=True)}; "
            f"warns through {_v2_fmt_param(scoring_params['g5_missing_fail_pct'], pct=True)}; "
            f"fails above {_v2_fmt_param(scoring_params['g5_missing_fail_pct'], pct=True)}."
        ),
    }
    gate_status_labels = {
        "pass": "pass",
        "warn": "warn",
        "fail": "fail",
    }

    gate_rows = "".join(
        f"<li style='display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px solid rgba(19,35,39,.05)'>"
        f"<span style='font-size:.78rem;color:#51656a;display:block;flex:1;min-width:0;line-height:1.35;word-break:break-word'>"
        f"<span style='display:flex;align-items:flex-start;gap:4px'>"
        f"<span>{_e(str(g.get('id','')))} {_e(gate_display_names.get(str(g.get('id', '')), str(g.get('name','')).replace('_', ' ')))}</span>"
        f"{_mini_info_icon(gate_tips.get(str(g.get('id', '')), str(g.get('rule', '') or '')), size=10)}"
        f"</span>"
        f"<span style='display:block;font-size:.72rem;color:#7b8b90;margin-top:2px'>{_e(str(g.get('summary', '') or g.get('rule', '') or ''))}</span>"
        f"</span>"
        f"<span style='font-size:.74rem;font-weight:700;padding:1px 8px;border-radius:999px;"
        f"background:{'#e8f4ee' if g.get('status') == 'pass' else '#fdf3e3' if g.get('status') == 'warn' else '#fdeaea'};"
        f"color:{'#196b4a' if g.get('status') == 'pass' else '#995b00' if g.get('status') == 'warn' else '#8f2d2d'}'>"
        f"{_e(gate_status_labels.get(str(g.get('status', '')).lower(), str(g.get('status', ''))))}</span>"
        f"</li>"
        for g in gates
    )
    gate_ceiling_note = (
        f"<span style='display:block;font-size:.73rem;color:#51656a;margin-top:6px'>Gate ceiling applied: {_e(gate_ceiling_label)}</span>"
        if gate_ceiling
        else "<span style='display:block;font-size:.73rem;color:#196b4a;margin-top:6px'>No gate ceiling applied.</span>"
    )

    conf_level, conf_color, conf_reason = _readiness_confidence(summary, readiness_score.get("section_scores", {}))
    diff_level, diff_color, diff_reason = _ml_difficulty(summary)

    confidence_html = (
        f"<div style='margin-top:10px;padding:9px 10px;border-radius:11px;"
        f"background:rgba(245,241,232,.72);border:1px solid rgba(19,35,39,.08);text-align:left'>"
        f"<div style='display:flex;align-items:center;justify-content:center;gap:6px;flex-wrap:wrap'>"
        f"<span style='font-size:.72rem;color:#51656a;text-transform:uppercase;letter-spacing:.05em'>"
        f"MERIT-ML extraction confidence</span>"
        f"<span style='font-size:.75rem;font-weight:700;padding:2px 9px;border-radius:20px;"
        f"background:{conf_color}22;color:{conf_color};border:1px solid {conf_color}55'>{_e(conf_level)}</span>"
        f"</div>"
        f"<div style='font-size:.72rem;color:#51656a;line-height:1.45;margin-top:6px;text-align:center'>"
        "Confidence reflects how completely MERIT-ML could parse the public source metadata and matrix structure. "
        "It does not represent confidence in the biological conclusions of the original study."
        f"</div>"
        f"<div style='font-size:.7rem;color:#7b8b90;line-height:1.4;margin-top:4px;text-align:center'>"
        f"Reason: {_e(conf_reason)}</div>"
        f"</div>"
    )

    difficulty_tip = _mini_info_icon(
        "Estimated ML difficulty is an a-priori assessment of how challenging it will be to build a useful "
        "supervised model from this study, based on ML-eligible sample-set size, class balance, feature-to-sample ratio, "
        "missingness, annotation quality, and class cardinality. It is independent of the ReadinessScore. "
        "Reason: " + diff_reason,
        size=11,
    )
    difficulty_html = (
        f"<div style='display:flex;align-items:center;justify-content:center;gap:6px;margin-top:6px;flex-wrap:wrap'>"
        f"<span style='font-size:.75rem;color:#51656a;text-transform:uppercase;letter-spacing:.05em'>Est. ML difficulty</span>"
        f"{difficulty_tip}"
        f"<span style='font-size:.75rem;font-weight:700;padding:2px 9px;border-radius:20px;"
        f"background:{diff_color}22;color:{diff_color};border:1px solid {diff_color}55' "
        f"title='{_e(diff_reason)}'>{_e(diff_level)}</span>"
        f"</div>"
    )

    src_avail_html = _source_availability_panel(source_avail or {}, source_tier)

    # MATRICES stat: show total across all sources, not just the source-filtered count
    _sa = source_avail or {}
    _total_matrices = (
        _sa.get("datatable_count", 0)
        + _sa.get("mwtab_count", 0)
        + _sa.get("untarg_data_count", 0)
    )
    _source_matrices = summary.get("n_feature_matrices", 0)
    if _total_matrices and _total_matrices != summary.get("n_feature_matrices", 0):
        summary = dict(summary)
        summary["n_feature_matrices_this_source"] = _source_matrices
        summary["n_feature_matrices"] = _total_matrices

    section_scores_tip = _mini_info_icon(
        "Section cards show the average of scored metrics in that section on the 0–100 display scale. "
        "Core ML readiness combines Structural, Analytical QC, Annotation / Interoperability, "
        "Label Structure and Class Support, and ML Task Readiness. "
        "Metadata and FAIR Reusability is reported separately as a reusability score "
        "over study metadata, metabolite identifier resolvability, and mass/RT-like metadata presence.",
        size=12,
    )
    radar_tip = _mini_info_icon(
        "Radar chart shows section-level diagnostics across six dimensions. "
        "Each axis ranges from 0 to 100, where higher values indicate stronger readiness signals. "
        "Core ML readiness is computed from five core sections (excluding Metadata and FAIR Reusability), "
        "while Metadata and FAIR Reusability is reported separately as reusability. "
        "The headline core readiness score is also displayed on a 0–100 scale. "
        f"Bands: ML-ready \u2265 {_v2_fmt_score(scoring_params['band_ready_min'])} \u00b7 "
        f"ML-ready with caveats \u2265 {_v2_fmt_score(scoring_params['band_conditional_min'])} \u00b7 "
        f"Exploratory ML use \u2265 {_v2_fmt_score(scoring_params['band_exploratory_min'])} \u00b7 "
        f"Class-support limited below {_v2_fmt_score(scoring_params['band_exploratory_min'])}.",
        size=12,
    )
    study_design_notice = _study_design_context_notice(study_design_context, summary, readiness_score)
    overview_content = (
        f"<div class='report-overview-grid' style='display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,420px);gap:20px;align-items:start'>"
        # left: study header + source availability + section scores
        f"<div style='min-width:0'>"
        f"{_study_header(summary)}"
        f"{study_design_notice}"
        f"{src_avail_html}"
        f"<div style='display:flex;align-items:center;gap:6px;margin:0 0 12px'>"
        f"<h4 style='margin:0;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em'>Section Scores</h4>"
        f"{section_scores_tip}"
        f"</div>"
        f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px'>{section_score_cards}</div>"
        f"{_citation_card_html(summary, precomputed_root)}"
        f"</div>"
        # right: radar + score + confidence + difficulty + recommendation + risks
        f"<div style='min-width:0'>"
        f"<div style='background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.1);border-radius:20px;padding:20px;margin-bottom:16px'>"
        f"<div style='display:flex;justify-content:flex-end;margin-bottom:-4px'>{radar_tip}</div>"
        f"<div id='{radar_id}' style='height:320px;max-width:440px;margin:0 auto;overflow:visible'></div>"
        f"<div style='text-align:center;margin-top:8px;border-top:1px solid rgba(19,35,39,.08);padding-top:10px'>"
        f"<span style='display:flex;align-items:center;justify-content:center;gap:4px;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:#51656a'>"
        f"Core ML Readiness Score (0–100) {core_score_tip}</span>"
        f"<span style='display:block;font-size:2rem;font-weight:700;color:{band_color}'>{core_score_display}</span>"
        f"<span style='display:block;font-size:.8rem;text-transform:uppercase;letter-spacing:.08em;color:{band_color}'>{_e(final_band_label)}</span>"
        f"<span style='display:block;font-size:.72rem;color:#9aafb2;margin-top:3px'>{_e(tier_label)}</span>"
        f"<div style='margin-top:9px;display:grid;grid-template-columns:1fr 1fr;gap:8px;text-align:left'>"
        f"<div style='padding:7px 9px;border-radius:10px;background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.08)'>"
        f"<span style='display:flex;align-items:center;gap:4px;font-size:.65rem;color:#51656a;text-transform:uppercase;letter-spacing:.06em'>"
        f"Reusability score (0–100) {reusability_tip}</span>"
        f"<strong style='font-size:.95rem'>{_score_100_text(reusability_score)}</strong></div>"
        f"<div style='padding:7px 9px;border-radius:10px;background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.08)'>"
        f"<span style='display:flex;align-items:center;gap:4px;font-size:.65rem;color:#51656a;text-transform:uppercase;letter-spacing:.06em'>"
        f"Provisional band {provisional_band_tip}</span>"
        f"<strong style='font-size:.95rem'>{_e(provisional_band_label)}</strong></div>"
        f"</div>"
        f"<div style='margin-top:10px;text-align:left;padding:9px 10px;border-radius:11px;background:rgba(245,241,232,.72);"
        f"border:1px solid rgba(19,35,39,.08)'>"
        f"<div style='font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a;margin-bottom:4px;display:flex;align-items:center;gap:4px'>"
        f"Gate summary {gate_summary_tip}</div>"
        f"<div style='font-size:.78rem;color:#132327'>Pass {int(gate_summary.get('pass', 0))} \u00b7 "
        f"Warn {int(gate_summary.get('warn', 0))} \u00b7 Fail {int(gate_summary.get('fail', 0))}</div>"
        f"{gate_ceiling_note}"
        f"</div>"
        f"<ul style='list-style:none;margin:8px 0 0;padding:0;text-align:left'>{gate_rows}</ul>"
        f"</div>"
        f"{confidence_html}"
        f"{difficulty_html}"
        f"</div>"
        f"<div style='background:rgba(255,255,255,.82);border:1px solid rgba(19,35,39,.1);border-radius:20px;padding:20px'>"
        f"<h4 style='margin:0 0 10px;font-size:.9rem;text-transform:uppercase;letter-spacing:.06em;display:flex;align-items:center;gap:6px'>"
        f"Recommendation "
        f"{recommendation_tip}"
        f"</h4>"
        f"<p style='font-size:.9rem;line-height:1.6;color:#132327;margin:0 0 12px'>{_e(readiness_score['recommendation'])}</p>"
        f"<ul style='list-style:none;margin:0;padding:0'>{action_items}</ul>"
        f"</div>"
        f"{_risks_panel(report)}"
        f"</div>"
        f"</div>"
    )

    # Build all tab panels (scoped with tab_sfx to avoid ID collisions across sources)
    matrix_adjust_html = _matrix_adjust_tab_html(matrix_model, matrix_overrides, source_key, tab_sfx)
    download_analysis_ids: list[str] = []
    analyses_by_source = (source_avail or {}).get("analyses_by_source") if isinstance(source_avail, dict) else {}
    if isinstance(analyses_by_source, dict):
        for ids in analyses_by_source.values():
            if isinstance(ids, (list, tuple, set)):
                download_analysis_ids.extend(_analysis_id_label(aid) for aid in ids if str(aid or "").strip())
    if not download_analysis_ids:
        download_analysis_ids = [
            _analysis_id_label((item or {}).get("analysis_id", ""))
            for item in (summary.get("per_analysis", []) if isinstance(summary.get("per_analysis", []), list) else [])
            if isinstance(item, dict) and str((item or {}).get("analysis_id", "") or "").strip()
        ]
    download_tab_html = _download_tabular_data_tab_html(
        str(summary.get("study_id", "") or ""),
        source_key,
        tab_sfx,
        download_analysis_ids,
    )
    tab_panels = (
        f"<div id='tab-matrix-{tab_sfx}' class='tab-panel tab-panel-{tab_sfx}' style='display:none'>{matrix_adjust_html}</div>"
        f"<div id='tab-download-{tab_sfx}' class='tab-panel tab-panel-{tab_sfx}' style='display:none'>{download_tab_html}</div>"
        f"<div id='tab-overview-{tab_sfx}' class='tab-panel tab-panel-{tab_sfx}'>{overview_content}</div>"
    )
    # Source-aware per-analysis list for UI tables/charts:
    # ingestion_summary.per_analysis may include analyses from other sources
    # in cache payloads; filter it using analysis IDs actually assessed in the
    # current source report.
    per_analysis = summary.get("per_analysis", [])
    analytical_ids: set[str] = set()
    for metric in report.analytical_readiness:
        details = getattr(metric, "details", {}) or {}
        per_metric = details.get("per_analysis")
        if not isinstance(per_metric, list):
            continue
        for item in per_metric:
            if not isinstance(item, dict):
                continue
            an = str(item.get("analysis_id", "")).strip().upper()
            if an:
                analytical_ids.add(an)
    if analytical_ids and isinstance(per_analysis, list):
        per_analysis = [
            item for item in per_analysis
            if str((item or {}).get("analysis_id", "")).strip().upper() in analytical_ids
        ]
    section_data = [
        ("structural", report.schema_validation, None),
        ("metadata", report.metadata_readiness, None),
        ("analytical", report.analytical_readiness, per_analysis),
        ("annotation", report.annotation_readiness, None),
        ("cohort", report.cohort_bias, None),
        ("ml", report.ml_readiness, None),
    ]
    _section_labels = {
        "structural": "Section 2: Structural",
        "metadata": "Section 3: Metadata and FAIR Reusability",
        "analytical": "Section 4: Analytical QC",
        "annotation": "Section 5: Annotation / Interoperability",
        "cohort": "Section 6: Label Structure and Class Support",
        "ml": "Section 7: ML Task Readiness",
    }
    _section_intros: dict[str, str] = {
        "structural": (
            "Checks the fundamental structure of the ingested study: required top-level components, "
            "presence of non-empty sample and feature matrices, completeness of mandatory descriptors, "
            "and absence of duplicate sample or feature IDs. Structural failures prevent downstream "
            "metrics from producing meaningful results."
        ),
        "metadata": (
            "Evaluates study- and metabolite-level reusability signals only: "
            "(1) study metadata completeness/documentation (FAIR-style evidence fields), and "
            "(2) metabolite identifier interoperability (RefMet-backed resolvability), and "
            "(3) mass/RT-like metabolite metadata presence in mwTab. "
            "These metrics improve reproducibility and cross-study reuse, but are not used to infer whether "
            "deposited labels define a valid supervised ML target."
        ),
        "analytical": (
            "Assesses the analytical quality of the raw feature matrices: missingness patterns, "
            "presence of QC/blank controls, scale diagnostics, outlier burden, "
            "feature–feature correlation redundancy, and between-analysis scale comparability. "
            "These dimensions directly determine whether the data is fit for supervised learning "
            "without additional preprocessing. Each metric shows a per-analysis breakdown — expand "
            "the card to inspect individual assay matrices."
        ),
        "annotation": (
            "Reviews annotation quality and interpretability: classification of features into named metabolites "
            "vs mz/RT tokens/NMR bins, ambiguity burden (multi-candidate or unresolved mapping flags), "
            "unknown placeholder burden, and within-assay raw feature-name redundancy. "
            "Poorly annotated studies are harder to interpret, transfer, or harmonize with other datasets. "
            "This section is included in the core ML readiness score because interpretability is a core "
            "readiness requirement for metabolomics ML."
        ),
        "cohort": (
            "Examines label structure signals that directly affect supervised ML reliability: "
            "class balance (min/max ratio), group-size support (smallest class size), and "
            "label entropy (how evenly samples are distributed across classes). "
            "These metrics focus on whether label groups are sufficiently supported and not overly dominance-skewed. "
            "Label-structure section score = mean(class_balance, group_size_support, label_entropy), displayed on 0-100 scale."
        ),
        "ml": (
            "Checks concrete feasibility requirements for supervised ML: minimum samples per class, "
            "feature-to-sample ratio (drives regularization needs), label suitability, and whether deposited labels are "
            "structurally usable targets (label endpoint extractability and factor label harmonizability). "
            "These metrics translate directly into recommended preprocessing steps and classifier choices. "
            "Label usability metrics are grouped explicitly in this tab."
        ),
        "separability": (
            "Measures how well class labels are intrinsically separable in the data, "
            "using a simple linear model (logistic regression) under repeated stratified validation. "
            "The score is the mean CV AUROC per analysis, then averaged across analyses. "
            "A low separability score does not necessarily mean the dataset is unusable — "
            "it means class signal may be subtle and harder to recover robustly. "
            "PCA plots are provided per analysis for visual inspection."
        ),
    }
    for tid, metrics, extra in section_data:
        extra_html = _per_analysis_table(extra, study_id=study_id, source_key=source_key) if extra is not None else ""
        if tid == "analytical":
            extra_html = _nmr_analysis_table(per_analysis) + extra_html
        if tid == "annotation":
            extra_html = _refmet_class_charts(
                per_analysis,
                chart_suffix=chart_suffix,
                study_id=study_id,
            ) + extra_html
        if tid == "separability":
            extra_html = _class_separability_panel(metrics, chart_suffix=chart_suffix) + extra_html
        print_title = f"<span class='print-section-title' style='display:none'>{_e(_section_labels.get(tid, tid))}</span>"
        intro_text = _section_intros.get(tid, "")
        intro_html = (
            f"<p style='font-size:.85rem;color:#51656a;line-height:1.65;margin:0 0 16px;"
            f"padding-bottom:14px;border-bottom:1px solid rgba(19,35,39,.08)'>{_e(intro_text)}</p>"
        ) if intro_text else ""
        if tid == "analytical":
            metric_html = _analytical_metric_rows(metrics, scoring_params, study_id=study_id)
        elif tid == "ml":
            allowed_metric_names = {
                "disease_endpoint_extractability",
                "factor_label_harmonizability",
                "label_suitability",
                "feature_to_sample_ratio",
            }
            ml_metrics = [m for m in metrics if m.name in allowed_metric_names]
            label_metric_names = {
                "disease_endpoint_extractability",
                "factor_label_harmonizability",
            }
            label_metrics = [m for m in ml_metrics if m.name in label_metric_names]
            core_metric_names = {
                "label_suitability",
                "feature_to_sample_ratio",
            }
            core_ml_metrics = [m for m in ml_metrics if m.name in core_metric_names]

            blocks: list[str] = []
            if label_metrics:
                blocks.append(
                    "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>"
                    "Label Usability</h4>"
                )
                blocks.append(_metric_rows(label_metrics, scoring_params, study_id=study_id))
            if core_ml_metrics:
                if label_metrics:
                    blocks.append("<div style='height:14px'></div>")
                blocks.append(
                    "<h4 style='margin:0 0 8px;font-size:.88rem;text-transform:uppercase;letter-spacing:.06em'>"
                    "Core ML Task Readiness</h4>"
                )
                blocks.append(_metric_rows(core_ml_metrics, scoring_params, study_id=study_id))

            metric_html = "".join(blocks) if blocks else _metric_rows(ml_metrics, scoring_params, study_id=study_id)
        else:
            metric_html = _metric_rows(metrics, scoring_params, study_id=study_id)
        panel_content = f"{print_title}{intro_html}{extra_html}{metric_html}"
        if tid == "analytical":
            panel_content = (
                "<div class='analytical-scroll-shell' data-analytical-scroll>"
                "<div class='analytical-scroll-control analytical-scroll-top' "
                "aria-label='Scroll Analytical QC horizontally from the top'>"
                "<div class='analytical-scroll-spacer'></div></div>"
                "<div class='analytical-scroll-body'>"
                f"{panel_content}"
                "</div>"
                "<div class='analytical-scroll-control analytical-scroll-bottom' "
                "aria-label='Scroll Analytical QC horizontally from the bottom'>"
                "<div class='analytical-scroll-spacer'></div></div>"
                "</div>"
            )
        tab_panels += (
            f"<div id='tab-{tid}-{tab_sfx}' class='tab-panel tab-panel-{tab_sfx}' style='display:none'>"
            f"{panel_content}</div>"
        )

    # Store radar data in a source-scoped JS variable so each source panel
    # has its own chart data independent of the others.
    radar_var = f"_radarData_{chart_suffix.lstrip('_') or 'main'}"

    return f"""
<section style='margin-top:26px'>
  <div style='display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px'>
    {tab_buttons}
  </div>
  <div style='background:rgba(255,255,255,.78);border:1px solid rgba(19,35,39,.1);border-radius:20px;padding:20px'>
    {tab_panels}
  </div>
</section>
<script>
var {radar_var} = {radar_data};
function switchTab(tid, sfx, deferPlots) {{
  if (!sfx || sfx === 'main') {{
    var dash = tid.indexOf('-');
    if (dash > 0 && dash < tid.length - 1) sfx = tid.substring(dash + 1);
  }}
  sfx = sfx || 'main';
  deferPlots = !!deferPlots;
  document.querySelectorAll('.tab-panel-' + sfx).forEach(function(p) {{ p.style.display = 'none'; }});
  var panel = document.getElementById('tab-' + tid);
  if (panel) panel.style.display = 'block';
  document.querySelectorAll('.tab-btn-' + sfx).forEach(function(b) {{ b.classList.remove('active'); }});
  var btn = document.querySelector('[data-tab="' + tid + '"]');
  if (btn) btn.classList.add('active');
  var base = tid.replace('-' + sfx, '');
  document.body.classList.toggle('report-analytical-active', base === 'analytical');
  if (window.refreshAnalyticalScrollbars) requestAnimationFrame(window.refreshAnalyticalScrollbars);
  if (base === 'matrix' && window.renderMatrixPanelRows) {{
    window.renderMatrixPanelRows(panel);
  }}
  if (base === 'overview' && !deferPlots) {{
    var renderRadar = window['renderRadar_' + sfx];
    if (window.loadPlotlyOnce) {{
      window.loadPlotlyOnce().then(function() {{ if (typeof renderRadar === 'function') renderRadar(); }}).catch(function() {{}});
    }} else if (window.Plotly && typeof renderRadar === 'function') {{
      renderRadar();
    }}
  }}
  if (base === 'annotation') {{
    var renderAnnotation = function() {{
      var fn = window['renderClassPie_' + sfx];
      try {{ if (typeof fn === 'function') fn(); }} catch(e) {{}}
    }};
    renderAnnotation();
  }}
  if (base === 'separability') {{
    var renderSep = function() {{
      var fn = window['renderSeparabilityPCA_' + sfx];
      try {{ if (typeof fn === 'function') fn(); }} catch(e) {{}}
    }};
    if (window.loadPlotlyOnce) window.loadPlotlyOnce().then(renderSep).catch(function() {{}});
    else if (window.Plotly) renderSep();
  }}
}}
function renderRadar_{tab_sfx.lstrip('_') or 'main'}() {{
  if (!window.Plotly) return;
  var d = {radar_var};
  if (!document.getElementById('{radar_id}')) return;
  Plotly.newPlot('{radar_id}', [{{
    type: 'scatterpolar',
    r: d.r,
    theta: d.theta,
    fill: 'toself',
    fillcolor: 'rgba(13,110,110,0.15)',
    line: {{ color: '#0d6e6e', width: 2 }},
    name: 'Readiness Score'
  }}], {{
    polar: {{ radialaxis: {{ visible: true, range: [0,100], tickvals: [0,20,40,60,80,100], tickfont: {{ size: 9 }} }},
             angularaxis: {{ tickfont: {{ size: 10 }} }} }},
    showlegend: false,
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    margin: {{ t: 24, b: 44, l: 74, r: 74 }}
  }}, {{ responsive: true, displayModeBar: false }});
}}
function _positionTooltip(host) {{
  if (!host) return;
  var popup = host.querySelector('.minfo-popup');
  if (!popup) return;
  popup.classList.remove('up', 'align-right');
  popup.style.maxHeight = Math.floor(window.innerHeight * 0.65) + 'px';
  var rect = popup.getBoundingClientRect();
  if (rect.bottom > window.innerHeight - 8) {{
    popup.classList.add('up');
    rect = popup.getBoundingClientRect();
  }}
  var boundRight = window.innerWidth - 8;
  var panel = host.closest ? host.closest('.content .panel') : null;
  if (panel) {{
    var panelRect = panel.getBoundingClientRect();
    boundRight = Math.min(boundRight, panelRect.right - 8);
  }}
  if (rect.right > boundRight) {{
    popup.classList.add('align-right');
  }}
}}
function _bindTooltipPositioning() {{
  document.querySelectorAll('.minfo').forEach(function(host) {{
    if (host.dataset.tooltipBound === '1') return;
    host.dataset.tooltipBound = '1';
    host.addEventListener('mouseenter', function() {{
      requestAnimationFrame(function() {{ _positionTooltip(host); }});
    }});
    host.addEventListener('focusin', function() {{
      requestAnimationFrame(function() {{ _positionTooltip(host); }});
    }});
  }});
}}
document.addEventListener('DOMContentLoaded', function() {{
  switchTab('overview-{tab_sfx}', '{tab_sfx}', true);
  var idleRender = function() {{
    var panel = document.getElementById('tab-overview-{tab_sfx}');
    var sourcePanel = panel && panel.closest ? panel.closest('.src-panel') : null;
    if (sourcePanel && getComputedStyle(sourcePanel).display === 'none') return;
    switchTab('overview-{tab_sfx}', '{tab_sfx}');
  }};
  if (window.requestIdleCallback) window.requestIdleCallback(idleRender, {{timeout: 2500}});
  else window.setTimeout(idleRender, 1600);
  _bindTooltipPositioning();
}});
window.addEventListener('resize', function() {{
  document.querySelectorAll('.minfo:hover, .minfo:focus-within').forEach(function(host) {{
    _positionTooltip(host);
  }});
}});
</script>
"""


# ---------------------------------------------------------------------------
# Full result panel
# ---------------------------------------------------------------------------

def _result_panel(
    state: dict[str, Any] | None,
    scoring_params: dict[str, float] | None = None,
    matrix_overrides: dict[str, dict[str, Any]] | None = None,
    precomputed_root: str | Path | None = None,
) -> str:
    scoring_params = scoring_params or _V2_DEFAULT_PARAMS
    if not state:
        return (
            "<section style='margin-top:24px;padding:24px;border-radius:22px;"
            "border:1px dashed rgba(19,35,39,.16);background:rgba(255,255,255,.6)'>"
            "<h3 style='margin:0 0 8px'>Workflow Ready</h3>"
            "<p style='margin:0;color:#51656a;line-height:1.6'>"
            "Enter a Metabolomics Workbench accession ID and run the pipeline. "
            "The report will display all readiness dimensions, a Readiness Score radar chart, "
            "per-source data availability, and per-metric recommendations."
            "</p></section>"
        )
    state = _v2_apply_scoring_profile(state, scoring_params, matrix_overrides=matrix_overrides) or state

    source_avail = state.get("source_availability") or {}
    source_assessments = state.get("source_assessments") or {}
    matrix_model = state.get("v2_sample_matrix_model") if isinstance(state.get("v2_sample_matrix_model"), dict) else {}
    matrix_overrides_state = state.get("v2_matrix_overrides") if isinstance(state.get("v2_matrix_overrides"), dict) else {}
    matrix_overrides_json = json.dumps(matrix_overrides_state, sort_keys=True)
    primary_src = state.get("primary_source") or "datatable"
    remediations = state.get("remediations") or []
    bundle = state.get("bundle", {}) or {}
    acquisition_note = bundle.get("tabular_message", "")
    requested_fetch_mode = state.get("requested_fetch_mode", "auto")
    acquisition_source = bundle.get("acquisition_source", "")
    acquisition_label = {
        "latest_dump": "Local latest dump",
        "managed_archive": "Local managed archive",
        "legacy_dump": "Local legacy dump",
        "remote_fetch": "Remote fetch",
    }.get(acquisition_source, "Unknown")

    artifact_rows = ""
    for label, path in [
        ("Bundle", state.get("bundle_path")),
        ("Canonical", state.get("canonical_path")),
        ("Assessment", state.get("assessment_path")),
        ("Report MD", state.get("report_md_path")),
        ("Remediated Canonical", state.get("remediated_canonical_path")),
        ("Remediated Assessment", state.get("remediated_assessment_path")),
    ]:
        if path:
            artifact_rows += (
                f"<div style='padding:8px 10px;border-radius:10px;background:rgba(245,241,232,.8);"
                f"border:1px solid rgba(19,35,39,.08);margin-bottom:6px'>"
                f"<div style='font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a'>{_e(label)}</div>"
                f"<code style='font-size:.78rem;word-break:break-all'>{_e(path)}</code>"
                f"</div>"
            )

    rem_list = ""
    if remediations:
        for item in remediations:
            rem_list += f"<li style='margin-bottom:4px;font-size:.88rem'><strong>{_e(item.get('action',''))}</strong> — {_e(str({k: v for k, v in item.items() if k != 'action'}))}</li>"
        rem_list = f"<ul style='margin:0;padding-left:18px'>{rem_list}</ul>"
    else:
        rem_list = "<p style='color:#51656a;font-style:italic;font-size:.88rem'>No remediation applied.</p>"

    # Build per-source tabbed reports — one hidden panel per source
    src_labels = {
        "datatable":   ("Datatable", "tier1"),
        "mwtab":       ("mwTab",     "tier1"),
        "untarg_data": ("Untarg Data","tier2"),
    }
    src_selector_btns = ""
    src_panels = ""
    first_active = None

    for src_key, (src_label, src_tier) in src_labels.items():
        sa = source_assessments.get(src_key)
        report_obj = (sa or {}).get("_report") if sa else None
        available = report_obj is not None

        if available and first_active is None:
            first_active = src_key

        score_val = (sa or {}).get("readiness_score", {}).get("score") if sa else None
        band_val  = (sa or {}).get("readiness_score", {}).get("band", "") if sa else ""
        band_label = _v2_band_label(band_val)
        band_color_map = {"Ready": "#196b4a", "Conditional": "#995b00",
                          "Fragile": "#8f2d2d", "Not Ready": "#8f2d2d", "No Data": "#51656a"}

        if available:
            _bc = band_color_map.get(band_val, "#51656a")
            score_badge = (
                f"<span style='font-size:.7rem;font-weight:800;padding:2px 7px;"
                f"border-radius:10px;background:rgba(255,255,255,.82);"
                f"color:#123135;border:1px solid rgba(18,49,53,.16);margin-left:7px;"
                f"font-variant-numeric:tabular-nums'>"
                f"{_score_100_text(score_val)} {_e(band_label)}</span>"
            ) if score_val is not None else ""
        else:
            score_badge = (
                "<span style='font-size:.7rem;color:#b0bec5;margin-left:5px'>N/A</span>"
            )

        click_attr = f"onclick=\"switchSource('{src_key}')\"" if available else "disabled aria-disabled='true'"
        disabled_style = "" if available else "opacity:.45;cursor:not-allowed;"
        src_selector_btns += (
            f"<button id='srcbtn-{src_key}' {click_attr} "
            f"style='border:none;border-radius:12px;padding:8px 16px;font:inherit;"
            f"font-size:.85rem;font-weight:700;cursor:{'pointer' if available else 'not-allowed'};"
            f"background:rgba(19,35,39,.07);color:#51656a;{disabled_style}transition:.15s'>"
            f"{_e(src_label)}{score_badge}</button>"
        )

        if available:
            rs = sa["readiness_score"]
            tabbed_html = _tabbed_report(
                report_obj, rs,
                source_avail=source_avail,
                source_tier=src_tier,
                chart_suffix=f"_{src_key}",
                scoring_params=scoring_params,
                matrix_model=matrix_model,
                matrix_overrides=matrix_overrides_state,
                source_key=src_key,
                precomputed_root=precomputed_root,
                study_design_context=state.get("study_design_context") if isinstance(state.get("study_design_context"), dict) else None,
            )
            src_panels += (
                f"<div id='srcpanel-{src_key}' class='src-panel' style='display:none'>"
                f"{tabbed_html}"
                f"</div>"
            )
        else:
            src_panels += (
                f"<div id='srcpanel-{src_key}' class='src-panel' style='display:none'>"
                f"<p style='color:#9aafb2;padding:24px;text-align:center'>"
                f"No {_e(src_label)} data available for this study.</p>"
                f"</div>"
            )

    if first_active is None:
        final_report = state.get("final_report")
        readiness_score = state.get("readiness_score") if isinstance(state.get("readiness_score"), dict) else {}
        if final_report is not None and readiness_score:
            first_active = "summary"
            band_val = readiness_score.get("final_band") or readiness_score.get("band") or "No Data"
            band_label = _v2_band_label(band_val)
            score_val = readiness_score.get("core_ml_readiness_score", readiness_score.get("score"))
            _bc = band_color_map.get(str(band_val), "#51656a")
            try:
                score_badge = (
                    f"<span style='font-size:.7rem;font-weight:800;padding:2px 7px;"
                    f"border-radius:10px;background:rgba(255,255,255,.82);"
                    f"color:#123135;border:1px solid rgba(18,49,53,.16);margin-left:7px;"
                    f"font-variant-numeric:tabular-nums'>"
                    f"{_score_100_text(score_val)} {_e(str(band_label))}</span>"
                )
            except Exception:
                score_badge = f"<span style='font-size:.7rem;color:{_bc};margin-left:5px'>{_e(str(band_label))}</span>"
            src_selector_btns += (
                "<button id='srcbtn-summary' onclick=\"switchSource('summary')\" "
                "style='border:none;border-radius:12px;padding:8px 16px;font:inherit;"
                "font-size:.85rem;font-weight:700;cursor:pointer;"
                "background:rgba(19,35,39,.07);color:#51656a;transition:.15s'>"
                f"Study Metadata Summary{score_badge}</button>"
            )
            tabbed_html = _tabbed_report(
                final_report,
                readiness_score,
                source_avail=source_avail,
                source_tier=state.get("source_tier", "tier1"),
                chart_suffix="_summary",
                scoring_params=scoring_params,
                matrix_model=matrix_model,
                matrix_overrides=matrix_overrides_state,
                source_key=str(state.get("primary_source") or ""),
                precomputed_root=precomputed_root,
                study_design_context=state.get("study_design_context") if isinstance(state.get("study_design_context"), dict) else None,
            )
            src_panels += (
                "<div id='srcpanel-summary' class='src-panel' style='display:none'>"
                "<div style='margin:2px 0 12px;padding:10px 12px;border-radius:12px;"
                "background:rgba(13,110,110,.07);border:1px solid rgba(13,110,110,.16);"
                "color:#51656a;font-size:.82rem;line-height:1.45'>"
                "No usable tabular source matrix was found. Showing the study-level metadata, "
                "FAIR reusability, and feasibility diagnostics that remain available for metadata-only records. "
                "By policy, non-reuse metric scores are set to 0 for metadata-only records; Metadata/FAIR "
                "reuse scores are retained when metadata evidence is available."
                "</div>"
                f"{tabbed_html}"
                "</div>"
            )

    first_active = first_active or "datatable"
    _report_for_note = state.get("final_report")
    _summary_for_note = getattr(_report_for_note, "ingestion_summary", {}) if _report_for_note else {}
    source_sample_count_notice = _source_sample_count_notice(
        str(state.get("study_id") or _summary_for_note.get("study_id") or "")
    )
    tabbed = (
        f"<div style='margin-bottom:14px'>"
        f"<div style='font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;"
        f"color:#51656a;margin-bottom:8px'>Data source — select to view independent assessment</div>"
        f"<div style='display:flex;gap:8px;flex-wrap:wrap'>{src_selector_btns}</div>"
        f"{source_sample_count_notice}"
        f"<input type='hidden' id='matrix-overrides-field' name='matrix_overrides' form='run-form' value='{_e(matrix_overrides_json)}'>"
        f"</div>"
        f"{src_panels}"
        f"<script>"
        f"(function(){{"
        f"var _activeSrc = '{first_active}';"
        f"window.switchSource = function(key) {{"
        f"  document.querySelectorAll('.src-panel').forEach(function(p){{p.style.display='none';}});"
        f"  var panel = document.getElementById('srcpanel-' + key);"
        f"  if (panel) panel.style.display = 'block';"
        f"  document.querySelectorAll('[id^=\"srcbtn-\"]').forEach(function(b){{"
        f"    b.style.background = 'rgba(19,35,39,.07)'; b.style.color = '#51656a';"
        f"  }});"
        f"  var btn = document.getElementById('srcbtn-' + key);"
	        f"  if (btn) {{ btn.style.background = '#0d6e6e'; btn.style.color = '#fff'; }}"
	        f"  _activeSrc = key;"
	        f"  window.__MERIT_ACTIVE_SOURCE = key;"
	        f"  if (window.Plotly) {{"
        f"    var rd = window['_radarData_' + key];"
        f"    if (rd) {{"
        f"      setTimeout(function() {{"
        f"        try {{ Plotly.react('radar-chart_' + key, [{{type:'scatterpolar',"
        f"          r:rd.r, theta:rd.theta, fill:'toself',"
        f"          fillcolor:'rgba(13,110,110,0.15)', line:{{color:'#0d6e6e',width:2}},"
        f"          name:'Readiness Score'}}],"
        f"          {{polar:{{radialaxis:{{visible:true,range:[0,100],tickvals:[0,20,40,60,80,100],tickfont:{{size:9}}}},"
        f"                    angularaxis:{{tickfont:{{size:10}}}}}},"
        f"           showlegend:false, paper_bgcolor:'transparent', plot_bgcolor:'transparent',"
        f"           margin:{{t:24,b:44,l:74,r:74}}}}, {{responsive:true,displayModeBar:false}});"
        f"          Plotly.Plots.resize('radar-chart_' + key); }}"
        f"        catch(e) {{}}"
        f"      }}, 50);"
        f"    }}"
        f"  }}"
        f"}};"
        f"switchSource('{first_active}');"
        f"}})();"
        f"</script>"
    )
    _report_for_summary = state.get("final_report")
    summary = _report_for_summary.ingestion_summary if _report_for_summary else {}
    state_payload = _json_safe_state_payload(state)
    state_json_js = json.dumps(state_payload).replace("</", "<\\/") if isinstance(state_payload, dict) else "null"
    study_label = str(summary.get("study_id", "") or state.get("study_id", "study")).strip() or "study"
    state_filename = f"{study_label.lower()}_merit_assessment.json"
    pdf_header = (
        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:16px'>"
        f"<div>"
        f"<h2 style='margin:0;font-family:\"Iowan Old Style\",Georgia,serif;font-size:1.5rem'>"
        f"MERIT-ML Assessment Report — {_e(summary.get('study_id',''))}</h2>"
        f"<p style='margin:4px 0 0;color:#51656a;font-size:.88rem'>"
        f"{_e(summary.get('title','')[:100])} &bull; {_e(_repository_display_label(summary.get('source')))}</p>"
        f"</div>"
        f"<div style='display:flex;gap:8px;align-items:center'>"
        f"<button onclick='downloadRenderedState()' style='border:1px solid rgba(13,110,110,.35);border-radius:12px;"
        f"padding:9px 14px;background:rgba(13,110,110,.08);color:#0d6e6e;font:inherit;font-size:.82rem;"
        f"font-weight:700;cursor:pointer;white-space:nowrap' title='Download the compact public MERIT-ML assessment JSON'>"
        f"Download MERIT-ML Assessment JSON</button>"
        f"</div>"
        f"</div>"
    )
    scope_note_html = (
        "<div style='margin:0 0 16px;padding:13px 15px;border-radius:16px;"
        "background:rgba(13,110,110,.07);border:1px solid rgba(13,110,110,.22);"
        "color:#2e474d;font-size:.88rem;line-height:1.55'>"
        f"{_e(_SCOPE_NOTE_TEXT)}"
        "</div>"
    )
    parsing_issue_html = _report_merit_parsing_issue_card(summary)

    return (
        f"<div style='margin-top:26px'>"
        f"<div style='margin:0 0 10px;color:#51656a;font-size:.86rem'>"
        f"Please download and keep this MERIT-ML Assessment JSON for reproducibility.</div>"
        f"{pdf_header}"
        f"{scope_note_html}"
        f"{parsing_issue_html}"
        f"<div style='display:grid;grid-template-columns:1fr;gap:18px;align-items:start'>"
        f"<div>{tabbed}</div>"
        f"</div>"
        f"<script>"
        f"window.__MERIT_STATE_JSON = {state_json_js};"
        f"window.downloadRenderedState = function() {{"
        f"  if (!window.__MERIT_STATE_JSON) {{ alert('No MERIT-ML Assessment JSON available to download.'); return; }}"
        f"  var blob = new Blob([JSON.stringify(window.__MERIT_STATE_JSON, null, 2)], {{type:'application/json'}});"
        f"  var a = document.createElement('a');"
        f"  a.href = URL.createObjectURL(blob);"
        f"  a.download = '{_e(state_filename)}';"
        f"  document.body.appendChild(a);"
        f"  a.click();"
        f"  document.body.removeChild(a);"
        f"  setTimeout(function(){{ URL.revokeObjectURL(a.href); }}, 300);"
        f"}};"
        f"</script>"
        f"</div>"
    )


def _bulk_clean_matrix_overrides(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return {}
    return _coerce_v2_matrix_overrides({"matrix_overrides": json.dumps(raw)})


def _ml_export_clean_analysis_ids(raw: Any) -> list[str]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raw = []
        else:
            try:
                raw = json.loads(text)
            except Exception:
                raw = re.split(r"[\s,;|]+", text)
    if isinstance(raw, dict):
        iterable = raw.values()
    elif isinstance(raw, (list, tuple, set)):
        iterable = raw
    else:
        iterable = []
    out: list[str] = []
    seen: set[str] = set()
    for item in iterable:
        aid = _analysis_id_label(str(item or ""))
        if not aid or aid in seen:
            continue
        if aid.startswith("AN") and aid[2:].isdigit():
            seen.add(aid)
            out.append(aid)
    return out


def _bulk_clean_session(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
    except Exception as exc:
        raise ValueError("Bulk MERIT-ML session JSON could not be parsed.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Bulk MERIT-ML session must be a JSON object.")
    studies_raw = payload.get("studies")
    if isinstance(studies_raw, dict):
        iterable = studies_raw.values()
    elif isinstance(studies_raw, list):
        iterable = studies_raw
    else:
        iterable = []

    studies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in iterable:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("study_id", "") or "").strip().upper()
        if not (sid.startswith("ST") and len(sid) == 8 and sid[2:].isdigit()):
            continue
        if sid in seen:
            continue
        seen.add(sid)
        studies.append(
            {
                "study_id": sid,
                "title": str(item.get("title", "") or "").strip(),
                "organism": str(item.get("organism", "") or "").strip(),
                "selected_source": str(item.get("selected_source", "") or "").strip(),
                "analysis_ids": _ml_export_clean_analysis_ids(item.get("analysis_ids", [])),
                "matrix_overrides": _bulk_clean_matrix_overrides(item.get("matrix_overrides", {})),
                "scoring_params": _coerce_v2_scoring_params(item.get("scoring_params") if isinstance(item.get("scoring_params"), dict) else {}),
                "saved_at": str(item.get("saved_at", "") or "").strip(),
            }
        )
    if not studies:
        raise ValueError("Bulk MERIT-ML session contains no valid ST study IDs.")
    return {
        "version": payload.get("version", 1),
        "created_at": payload.get("created_at", ""),
        "updated_at": payload.get("updated_at", ""),
        "studies": studies[:500],
        "n_submitted": len(studies),
        "n_used": min(len(studies), 500),
    }


def _bulk_session_embargoed_ids(raw: str | dict[str, Any]) -> list[str]:
    try:
        if isinstance(raw, dict):
            session = raw
        else:
            session = _bulk_clean_session(raw)
    except Exception:
        return []
    ids: list[str] = []
    for item in session.get("studies", []) if isinstance(session, dict) else []:
        sid = _study_id_key(item.get("study_id") if isinstance(item, dict) else item)
        if sid and _is_embargoed_study(sid) and sid not in ids:
            ids.append(sid)
    return ids


def _raise_if_embargoed_bulk_session(raw: str | dict[str, Any]) -> None:
    ids = _bulk_session_embargoed_ids(raw)
    if ids:
        messages = [_embargoed_study_message(sid) for sid in ids]
        raise ValueError(" ".join(messages))


_ML_EXPORT_SAMPLE_ALIASES = {"samples", "sample", "sample_id", "sample id", "local_sample_id", "local sample id"}
_ML_EXPORT_LABEL_ALIASES = {"class", "label", "group", "groups", "factor", "factors", "class label", "class_label"}
_ML_EXPORT_SOURCE_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "datatable": ("datatable/file",),
    "mwtab": ("mwtab/txt",),
    "untarg_data": ("untarg_data/file", "untarg_data", "untarg_data/txt"),
}


def _ml_export_source_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _ml_export_citation_metadata(study_id: str) -> dict[str, Any]:
    study_id = str(study_id or "").strip().upper()
    citation_index = _load_citation_index(_default_precomputed_root())
    citation = citation_index.get(study_id, {}) if isinstance(citation_index, dict) else {}
    if not isinstance(citation, dict):
        citation = {}
    return {
        "project_id": str(citation.get("project_id") or "") or None,
        "project_doi": str(citation.get("project_doi") or "") or None,
        "project_doi_url": str(citation.get("doi_url") or "") or None,
        "related_publications": citation.get("related_publications") if isinstance(citation.get("related_publications"), list) else [],
    }


def _ml_export_derivation_metadata(
    *,
    study_id: str,
    generated_at: str,
    source_matrix: str | None = None,
    analysis_id: str | None = None,
    rest_url: str | None = None,
    source_hash: str | None = None,
    matrix_file: str | None = None,
    n_exported_samples: int | None = None,
    n_features: int | None = None,
) -> dict[str, Any]:
    study_id = str(study_id or "").strip().upper()
    citation = _ml_export_citation_metadata(study_id)
    metadata: dict[str, Any] = {
        "source_repository": "Metabolomics Workbench",
        "study_id": study_id,
        "project_id": citation.get("project_id"),
        "project_doi": citation.get("project_doi"),
        "project_doi_url": citation.get("project_doi_url"),
        "source_url": f"{_WB_STUDY_PAGE_BASE}{study_id}" if study_id else None,
        "accessed_on": generated_at.split("T", 1)[0],
        "generated_at_utc": generated_at,
        "merit_version": MERIT_VERSION,
        "derivation_note": (
            "This file was generated by MERIT-ML from public Metabolomics Workbench tabular data "
            "for ML-readiness assessment. It is a MERIT-ML-derived representation and does not "
            "replace the original Metabolomics Workbench record."
        ),
        "citation_note": (
            "Users should cite the original Metabolomics Workbench Project ID, Project DOI where available, "
            "Study ID/accession, and associated publication(s) where applicable."
        ),
        "related_publications": citation.get("related_publications") or [],
    }
    if source_matrix is not None:
        metadata["source_matrix"] = source_matrix
    if analysis_id is not None:
        metadata["analysis_id"] = analysis_id
    if rest_url is not None:
        metadata["rest_url"] = rest_url
    if source_hash is not None:
        metadata["source_hash"] = source_hash
    if matrix_file is not None:
        metadata["matrix_file"] = matrix_file
    if n_exported_samples is not None:
        metadata["n_exported_samples"] = n_exported_samples
    if n_features is not None:
        metadata["n_features"] = n_features
    return metadata


def _ml_export_slug(value: Any, fallback: str = "value") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return text or fallback


def _ml_export_fetch_text(url: str, timeout: int = 35) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "MERIT-ML/1.0 (+https://merit-ml.in)",
            "Accept": "text/plain,application/json,*/*",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    return payload.decode("utf-8", errors="replace")


def _ml_export_fetch_json(url: str) -> Any:
    text = _ml_export_fetch_text(url)
    stripped = text.strip()
    if not stripped or stripped.lower().startswith(("no ", "error", "null")):
        return None
    return json.loads(stripped)


def _ml_export_rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if payload and all(isinstance(value, dict) for value in payload.values()):
            return [value for value in payload.values() if isinstance(value, dict)]
        return [payload]
    return []


def _ml_export_analysis_id(row: dict[str, Any]) -> str:
    for key in ("analysis_id", "ANALYSIS_ID", "Analysis_ID", "Analysis ID"):
        value = row.get(key)
        if value:
            return str(value).strip().upper()
    return ""


def _ml_export_study_id(row: dict[str, Any]) -> str:
    for key in ("study_id", "STUDY_ID", "Study_ID", "Study ID"):
        value = row.get(key)
        if value:
            return str(value).strip().upper()
    return ""


def _ml_export_untarg_registry_analyses(study_id: str) -> tuple[list[str], list[str]]:
    """Recover untarg-only analysis IDs when the regular study endpoint is empty."""
    url = f"{_WB_REST_BASE}/study/study_id/{study_id}/untarg_studies"
    try:
        payload = _ml_export_fetch_json(url)
    except Exception as exc:
        return [], [f"Could not fetch Metabolomics Workbench untarg_data registry from {url}: {exc}"]
    analysis_ids = [
        _ml_export_analysis_id(row)
        for row in _ml_export_rows_from_payload(payload)
        if _ml_export_study_id(row) == study_id
    ]
    analysis_ids = sorted({aid for aid in analysis_ids if aid})
    if not analysis_ids:
        return [], [f"No untarg_data analysis IDs for {study_id} were found in the Metabolomics Workbench untarg_data registry."]
    return analysis_ids, [
        f"Regular study analysis endpoint returned no IDs; recovered {len(analysis_ids)} untarg_data analysis ID(s) from the Metabolomics Workbench untarg_data registry."
    ]


def _ml_export_cached_analysis_ids(study_id: str) -> tuple[list[str], list[str]]:
    """Fallback to MERIT-ML assessment metadata for IDs only; matrix values still use REST."""
    try:
        state = _load_precomputed_state(
            study_id=study_id,
            precomputed_root=_default_precomputed_root(),
            requested_profile="full",
        )
    except Exception:
        state = None
    if not isinstance(state, dict):
        return [], []
    source_avail = state.get("source_availability") if isinstance(state.get("source_availability"), dict) else {}
    analyses_by_source = source_avail.get("analyses_by_source") if isinstance(source_avail, dict) else {}
    analysis_ids: list[str] = []
    if isinstance(analyses_by_source, dict):
        for ids in analyses_by_source.values():
            if isinstance(ids, (list, tuple, set)):
                analysis_ids.extend(_ml_export_clean_analysis_ids(list(ids)))
    if not analysis_ids:
        report = state.get("final_report") if isinstance(state.get("final_report"), dict) else {}
        summary = report.get("ingestion_summary") if isinstance(report.get("ingestion_summary"), dict) else {}
        per_analysis = summary.get("per_analysis") if isinstance(summary.get("per_analysis"), list) else []
        analysis_ids = _ml_export_clean_analysis_ids(
            [(item or {}).get("analysis_id", "") for item in per_analysis if isinstance(item, dict)]
        )
    if not analysis_ids:
        return [], []
    return analysis_ids, [
        "Recovered analysis IDs from the current MERIT-ML assessment metadata; exported matrix values were fetched live from Metabolomics Workbench REST."
    ]


def _ml_export_study_analyses(study_id: str, analysis_ids: Any = None) -> tuple[list[str], list[str]]:
    explicit_ids = _ml_export_clean_analysis_ids(analysis_ids)
    if explicit_ids:
        return explicit_ids, ["Using analysis IDs from the current MERIT-ML report."]
    url = f"{_WB_REST_BASE}/study/study_id/{study_id}/analysis"
    warnings: list[str] = []
    try:
        payload = _ml_export_fetch_json(url)
    except Exception as exc:
        return [], [f"Could not fetch Metabolomics Workbench analysis list from {url}: {exc}"]
    analysis_ids = [
        _ml_export_analysis_id(row)
        for row in _ml_export_rows_from_payload(payload)
    ]
    analysis_ids = sorted({aid for aid in analysis_ids if aid})
    if not analysis_ids:
        fallback_ids, fallback_warnings = _ml_export_untarg_registry_analyses(study_id)
        warnings.extend(fallback_warnings)
        if fallback_ids:
            return fallback_ids, warnings
        cached_ids, cached_warnings = _ml_export_cached_analysis_ids(study_id)
        warnings.extend(cached_warnings)
        if cached_ids:
            return cached_ids, warnings
        warnings.append(f"No analysis IDs were returned by {url}.")
    return analysis_ids, warnings


def _ml_export_factor_labels(study_id: str) -> tuple[dict[str, str], list[dict[str, str]], list[str]]:
    url = f"{_WB_REST_BASE}/study/study_id/{study_id}/factors"
    warnings: list[str] = []
    labels: dict[str, str] = {}
    rows_out: list[dict[str, str]] = []
    try:
        payload = _ml_export_fetch_json(url)
    except Exception as exc:
        return labels, rows_out, [f"Could not fetch Metabolomics Workbench factor labels from {url}: {exc}"]
    for row in _ml_export_rows_from_payload(payload):
        sample_id = str(
            row.get("local_sample_id")
            or row.get("LOCAL_SAMPLE_ID")
            or row.get("sample_id")
            or row.get("Sample ID")
            or row.get("mb_sample_id")
            or ""
        ).strip()
        if not sample_id:
            continue
        factors = row.get("factors")
        if isinstance(factors, dict):
            label = " | ".join(
                f"{str(k).strip()}:{str(v).strip()}"
                for k, v in factors.items()
                if str(k).strip() and str(v).strip()
            )
        else:
            label = str(factors or row.get("Factors") or row.get("factor") or row.get("Factor") or "").strip()
        labels[sample_id] = label
        rows_out.append(
            {
                "sample_id": sample_id,
                "native_label": label,
                "mb_sample_id": str(row.get("mb_sample_id") or row.get("MB Sample ID") or "").strip(),
                "sample_source": str(row.get("sample_source") or row.get("Sample source") or row.get("sample_type") or "").strip(),
            }
        )
    if not labels:
        warnings.append(f"No sample labels were returned by {url}.")
    return labels, rows_out, warnings


def _ml_export_parse_table(text: str, *, force_tsv: bool = False) -> tuple[list[str], list[list[str]]]:
    stripped = text.strip("\ufeff\r\n ")
    if not stripped:
        return [], []
    sample = stripped[:4096]
    first_line = sample.splitlines()[0] if sample else ""
    delimiter = "\t"
    if not force_tsv:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters="\t,")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "\t"
    if delimiter == "," and first_line.count("\t") >= 2 and first_line.count("\t") > first_line.count(","):
        delimiter = "\t"
    reader = csv.reader(io.StringIO(stripped), delimiter=delimiter)
    raw_rows = [[str(cell or "").strip() for cell in row] for row in reader]
    raw_rows = [row for row in raw_rows if any(cell for cell in row)]
    if not raw_rows:
        return [], []
    width = max(len(row) for row in raw_rows)
    raw_rows = [row + [""] * (width - len(row)) for row in raw_rows]
    return raw_rows[0], raw_rows[1:]


def _ml_export_mwtab_table(text: str) -> tuple[list[str], list[list[str]], dict[str, str]]:
    labels: dict[str, str] = {}
    lines = text.splitlines()
    for line in lines:
        if not line.startswith("SUBJECT_SAMPLE_FACTORS"):
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            sid = str(parts[2] or "").strip()
            label = str(parts[3] or "").strip()
            if sid and sid not in labels:
                labels[sid] = label
    in_section = False
    data_lines: list[str] = []
    for line in lines:
        token = line.strip()
        upper = token.upper()
        if not in_section:
            if upper.endswith("_DATA_START") and "METABOLITE" in upper:
                in_section = True
            continue
        if upper.endswith("_END"):
            break
        if token:
            data_lines.append(line)
    header, rows = _ml_export_parse_table("\n".join(data_lines))
    return header, rows, labels


def _ml_export_mwtab_matrix_rows(text: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Parse mwTab with the same row-oriented conversion used by MERIT-ML ingestion."""
    labels: dict[str, str] = {}
    lines = text.splitlines()
    for line in lines:
        if not line.startswith("SUBJECT_SAMPLE_FACTORS"):
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            sid = str(parts[2] or "").strip()
            label = str(parts[3] or "").strip()
            if sid and sid not in labels:
                labels[sid] = label

    in_section = False
    data_lines: list[str] = []
    for line in lines:
        token = line.strip()
        upper = token.upper()
        if not in_section:
            if upper in {
                "MS_METABOLITE_DATA_START",
                "NMR_BINNED_DATA_START",
                "NMR_METABOLITE_DATA_START",
                "EXTENDED_MS_METABOLITE_DATA_START",
                "EXTENDED_NMR_METABOLITE_DATA_START",
                "DIRECT_INFUSION_METABOLITE_DATA_START",
                "METABOLITE_DATA_START",
            }:
                in_section = True
            continue
        if upper.endswith("_END"):
            break
        if token:
            data_lines.append(line)
    if not data_lines:
        return [], [], ["No mwTab metabolite data section was found."]

    # Match the cache pipeline: DictReader over the mwTab data block followed
    # by MetabolomicsWorkbenchConnector._convert_row_oriented_rows().
    try:
        from merit.connectors.workbench import MetabolomicsWorkbenchConnector

        reader = csv.DictReader(io.StringIO("\n".join(data_lines)), delimiter="\t")
        raw_rows = [dict(row) for row in reader]
        sample_rows = MetabolomicsWorkbenchConnector._convert_row_oriented_rows(raw_rows)
    except Exception as exc:
        return [], [], [f"mwTab cache-style conversion failed: {exc}"]

    if not sample_rows:
        return [], [], ["No sample rows were recovered from the mwTab data block."]
    feature_names = _ml_export_unique_features(
        [
            str(key)
            for key in sample_rows[0].keys()
            if str(key) not in {"_sample_id", "_class"}
        ]
    )
    raw_feature_keys = [
        str(key)
        for key in sample_rows[0].keys()
        if str(key) not in {"_sample_id", "_class"}
    ]
    matrix_rows: list[dict[str, Any]] = []
    for row in sample_rows:
        sid = str(row.get("_sample_id", "") or "").strip()
        if not sid:
            continue
        native_label = str(row.get("_class", "") or "").strip() or labels.get(sid, "")
        values = {
            feature_names[pos]: str(row.get(feature_key, "") or "").strip()
            for pos, feature_key in enumerate(raw_feature_keys)
        }
        matrix_rows.append({"sample_id": sid, "native_label": native_label, "values": values})
    return matrix_rows, feature_names, []


def _ml_export_unique_features(names: list[str]) -> list[str]:
    counts: Counter[str] = Counter()
    out: list[str] = []
    for idx, raw in enumerate(names, start=1):
        name = str(raw or "").strip() or f"feature_{idx}"
        counts[name] += 1
        out.append(name if counts[name] == 1 else f"{name}__dup{counts[name]}")
    return out


def _ml_export_matrix_from_table(
    header: list[str],
    data_rows: list[list[str]],
    *,
    source_key: str,
    mwtab_labels: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    warnings: list[str] = []
    mwtab_labels = mwtab_labels or {}
    if not header or not data_rows:
        return [], [], ["No tabular rows were returned."]
    lower = [str(col or "").strip().casefold() for col in header]
    sample_idx = next((i for i, col in enumerate(lower) if col in _ML_EXPORT_SAMPLE_ALIASES), None)
    label_idx = next((i for i, col in enumerate(lower) if col in _ML_EXPORT_LABEL_ALIASES), None)
    first_data_keys = {
        str(row[0] if row else "").strip().casefold()
        for row in data_rows[:5]
        if row
    }
    sample_header_is_transposed = (
        sample_idx == 0
        and label_idx is None
        and (
            source_key == "mwtab"
            or bool(first_data_keys & _ML_EXPORT_LABEL_ALIASES)
        )
    )

    if sample_idx is not None and not sample_header_is_transposed:
        feature_indices = [
            i for i, col in enumerate(header)
            if i != sample_idx and i != label_idx and str(col or "").strip()
        ]
        feature_names = _ml_export_unique_features([header[i] for i in feature_indices])
        matrices: list[dict[str, Any]] = []
        for row in data_rows:
            sid = str(row[sample_idx] if sample_idx < len(row) else "").strip()
            if not sid or sid.casefold() in _ML_EXPORT_SAMPLE_ALIASES:
                continue
            row_label = str(row[label_idx] if label_idx is not None and label_idx < len(row) else "").strip()
            values = {
                feature_names[pos]: (row[idx] if idx < len(row) else "")
                for pos, idx in enumerate(feature_indices)
            }
            matrices.append({"sample_id": sid, "native_label": row_label or mwtab_labels.get(sid, ""), "values": values})
        return matrices, feature_names, warnings

    # Feature-by-sample orientation, common in mwTab: first column is a feature,
    # all remaining columns are sample IDs.
    feature_col = 0
    sample_cols = [(idx, str(col or "").strip()) for idx, col in enumerate(header[1:], start=1) if str(col or "").strip()]
    if not sample_cols:
        return [], [], ["Could not identify sample columns in the downloaded table."]

    label_by_sample: dict[str, str] = {}
    feature_names_raw: list[str] = []
    feature_value_rows: list[tuple[str, list[str]]] = []
    metadata_row_names = _ML_EXPORT_LABEL_ALIASES | {"samples", "sample", "sample id", "sample_id"}
    for row in data_rows:
        feature = str(row[feature_col] if feature_col < len(row) else "").strip()
        if not feature:
            continue
        key = feature.casefold()
        if key in metadata_row_names:
            for idx, sample_id in sample_cols:
                val = str(row[idx] if idx < len(row) else "").strip()
                if val and sample_id not in label_by_sample:
                    label_by_sample[sample_id] = val
            continue
        feature_names_raw.append(feature)
        feature_value_rows.append((feature, row))

    feature_names = _ml_export_unique_features(feature_names_raw)
    matrices = []
    for idx, sample_id in sample_cols:
        values: dict[str, str] = {}
        for pos, (_feature, row) in enumerate(feature_value_rows):
            values[feature_names[pos]] = str(row[idx] if idx < len(row) else "").strip()
        matrices.append(
            {
                "sample_id": sample_id,
                "native_label": label_by_sample.get(sample_id, "") or mwtab_labels.get(sample_id, ""),
                "values": values,
            }
        )
    return matrices, feature_names, warnings


def _ml_export_fetch_matrix(study_id: str, analysis_id: str, source_key: str) -> dict[str, Any]:
    endpoints = _ML_EXPORT_SOURCE_ENDPOINTS[source_key]
    errors: list[str] = []
    for output_item in endpoints:
        url = f"{_WB_REST_BASE}/study/analysis_id/{analysis_id}/{output_item}"
        try:
            text = _ml_export_fetch_text(url)
        except Exception as exc:
            errors.append(f"{output_item}: {exc}")
            continue
        if not text.strip() or text.strip().lower().startswith(("no ", "error", "null")):
            errors.append(f"{output_item}: empty response")
            continue
        if source_key == "mwtab":
            matrix_rows, feature_names, warnings = _ml_export_mwtab_matrix_rows(text)
            if matrix_rows and feature_names:
                return {
                    "ok": True,
                    "source_key": source_key,
                    "analysis_id": analysis_id,
                    "url": url,
                    "source_hash": _ml_export_source_hash(text),
                    "rows": matrix_rows,
                    "features": feature_names,
                    "warnings": warnings,
                }
            errors.extend(warnings or [f"{output_item}: no sample-by-feature matrix could be parsed"])
            continue
        else:
            header, data_rows = _ml_export_parse_table(
                text,
                force_tsv=source_key in {"datatable", "untarg_data"},
            )
            mwtab_labels = {}
        matrix_rows, feature_names, warnings = _ml_export_matrix_from_table(
            header,
            data_rows,
            source_key=source_key,
            mwtab_labels=mwtab_labels,
        )
        if matrix_rows and feature_names:
            return {
                "ok": True,
                "source_key": source_key,
                "analysis_id": analysis_id,
                "url": url,
                "source_hash": _ml_export_source_hash(text),
                "rows": matrix_rows,
                "features": feature_names,
                "warnings": warnings,
            }
        errors.extend(warnings or [f"{output_item}: no sample-by-feature matrix could be parsed"])
    return {
        "ok": False,
        "source_key": source_key,
        "analysis_id": analysis_id,
        "errors": errors or ["Source not available from Metabolomics Workbench REST."],
    }


def _ml_export_effective_row(
    sample_id: str,
    native_label: str,
    overrides: dict[str, dict[str, Any]],
) -> tuple[bool, str, bool, str]:
    override = overrides.get(sample_id) or overrides.get(sample_id.strip()) or {}
    override_label = str(override.get("label", "") or "").strip()
    label = override_label if override_label else str(native_label or "").strip()
    excluded = bool(override.get("excluded", False))
    if "eligible" in override:
        eligible = bool(override.get("eligible"))
    else:
        eligible = _v2_default_sample_eligible(sample_id, label)
    usable_label = is_usable_class_label(label)
    return bool(eligible and not excluded and usable_label), label, excluded, "manual" if override else "default"


def _ml_export_write_tsv(zf: zipfile.ZipFile, path: str, header: list[str], rows: list[list[Any]]) -> None:
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    zf.writestr(path, stream.getvalue())


def _ml_export_build_zip(studies: list[dict[str, Any]], *, bulk: bool = False) -> tuple[bytes, str]:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest: dict[str, Any] = {
        "export_type": "MERIT-ML-derived ML assessment inputs",
        "generated_at_utc": generated_at,
        "rest_api_base": _WB_REST_BASE,
        "source_repository": "Metabolomics Workbench",
        "merit_version": MERIT_VERSION,
        "derivation_note": (
            "This ZIP contains MERIT-ML-derived representations of public Metabolomics Workbench tabular data "
            "for reproducibility of MERIT-ML readiness assessment. It does not replace the original Metabolomics Workbench record."
        ),
        "citation_note": (
            "Users should cite the original Metabolomics Workbench Project ID, Project DOI where available, "
            "Study ID/accession, and associated publication(s) where applicable."
        ),
        "policy": {
            "source": "Metabolomics Workbench REST API only",
            "storage": "No server-side copy is saved for this export.",
            "matrix_orientation": "Derived TSV files use rows as samples and columns as metabolite features.",
            "sample_filter": "Only samples marked ML-eligible and carrying usable class labels are exported.",
            "data_values": "Original matrix values are preserved; MERIT-ML does not impute, normalize, or remediate values in this ZIP.",
            "citation": "The export is not a Metabolomics Workbench mirror; users must consult and cite the original Metabolomics Workbench record.",
        },
        "studies": [],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        readme = (
            "MERIT-ML-derived ML assessment inputs\n"
            f"Generated UTC: {generated_at}\n\n"
            "This ZIP was built dynamically from the Metabolomics Workbench REST API and contains MERIT-ML-derived\n"
            "representations of public Metabolomics Workbench source data for reproducibility of the MERIT-ML assessment.\n"
            "It does not replace the original Metabolomics Workbench record, and MERIT-ML does not save a server-side copy of this ZIP.\n\n"
            "For each available source and analysis, MERIT-ML fetches the source-specific tabular dataset from Metabolomics Workbench REST,\n"
            "then converts it on the fly into an assessment-ready TSV with rows as samples and columns as metabolite features.\n"
            "The first two columns are Sample ID and Class Label. Class labels are aligned to MERIT-ML sample names and reflect\n"
            "the default Metabolomics Workbench factor labels plus any current UI-session edits made in Adjust Matrix Properties.\n"
            "Only ML-eligible samples with usable class labels are included. Original matrix values are preserved; no imputation, scaling, normalization, or feature remediation is applied.\n\n"
            "Citation responsibility: users must cite the original Metabolomics Workbench Project ID, Project DOI where available,\n"
            "Study ID/accession, and associated publication(s) where applicable.\n"
        )
        zf.writestr("README.txt", readme)
        for item in studies:
            study_id = str(item.get("study_id", "") or "").strip().upper()
            if not study_id:
                continue
            overrides = _bulk_clean_matrix_overrides(item.get("matrix_overrides", {}))
            study_folder = _ml_export_slug(study_id)
            study_source_metadata = _ml_export_derivation_metadata(
                study_id=study_id,
                generated_at=generated_at,
            )
            study_manifest: dict[str, Any] = {
                "study_id": study_id,
                "source_metadata": study_source_metadata,
                "matrix_override_count": len(overrides),
                "warnings": [],
                "analyses": [],
            }
            zf.writestr(
                f"{study_folder}/source_metadata.json",
                json.dumps(study_source_metadata, indent=2, ensure_ascii=False),
            )
            analysis_ids, analysis_warnings = _ml_export_study_analyses(
                study_id,
                item.get("analysis_ids", []),
            )
            study_manifest["warnings"].extend(analysis_warnings)
            factor_labels, factor_rows, factor_warnings = _ml_export_factor_labels(study_id)
            study_manifest["warnings"].extend(factor_warnings)
            sample_manifest_rows: dict[str, list[Any]] = {}
            for analysis_id in analysis_ids:
                analysis_entry: dict[str, Any] = {"analysis_id": analysis_id, "sources": []}
                for source_key in ("datatable", "mwtab", "untarg_data"):
                    fetched = _ml_export_fetch_matrix(study_id, analysis_id, source_key)
                    if not fetched.get("ok"):
                        analysis_entry["sources"].append(
                            {
                                "source": source_key,
                                "available": False,
                                "errors": fetched.get("errors", []),
                            }
                        )
                        continue
                    exported_rows: list[list[Any]] = []
                    skipped = Counter()
                    feature_names = list(fetched.get("features", []) or [])
                    for row in fetched.get("rows", []) or []:
                        sid = str(row.get("sample_id", "") or "").strip()
                        if not sid:
                            skipped["missing_sample_id"] += 1
                            continue
                        native_label = factor_labels.get(sid) or str(row.get("native_label", "") or "").strip()
                        include, label, excluded, label_source = _ml_export_effective_row(sid, native_label, overrides)
                        if not include:
                            if excluded:
                                skipped["excluded_by_ui"] += 1
                            elif not is_usable_class_label(label):
                                skipped["missing_or_unusable_label"] += 1
                            else:
                                skipped["not_ml_eligible"] += 1
                            sample_manifest_rows.setdefault(
                                sid,
                                [study_id, sid, native_label, label, "no", "yes" if excluded else "no", label_source],
                            )
                            continue
                        values = row.get("values", {}) if isinstance(row.get("values"), dict) else {}
                        exported_rows.append([sid, label] + [values.get(feature, "") for feature in feature_names])
                        sample_manifest_rows[sid] = [study_id, sid, native_label, label, "yes", "no", label_source]
                    source_dir = f"{study_folder}/{source_key}"
                    source_slug = _ml_export_slug(source_key)
                    analysis_slug = _ml_export_slug(analysis_id)
                    matrix_path = f"{source_dir}/{analysis_slug}_{source_slug}_merit_derived.tsv"
                    source_metadata_path = f"{source_dir}/{analysis_slug}_{source_slug}_source_metadata.json"
                    source_metadata = _ml_export_derivation_metadata(
                        study_id=study_id,
                        generated_at=generated_at,
                        source_matrix=source_key,
                        analysis_id=analysis_id,
                        rest_url=str(fetched.get("url", "") or ""),
                        source_hash=str(fetched.get("source_hash", "") or ""),
                        matrix_file=matrix_path if feature_names else "",
                        n_exported_samples=len(exported_rows),
                        n_features=len(feature_names),
                    )
                    if feature_names:
                        _ml_export_write_tsv(
                            zf,
                            matrix_path,
                            ["Sample ID", "Class Label"] + feature_names,
                            exported_rows,
                        )
                        zf.writestr(
                            source_metadata_path,
                            json.dumps(source_metadata, indent=2, ensure_ascii=False),
                        )
                    analysis_entry["sources"].append(
                        {
                            "source": source_key,
                            "available": True,
                            "rest_url": fetched.get("url", ""),
                            "source_hash": fetched.get("source_hash", ""),
                            "matrix_file": matrix_path if feature_names else "",
                            "source_metadata_file": source_metadata_path if feature_names else "",
                            "n_exported_samples": len(exported_rows),
                            "n_features": len(feature_names),
                            "n_downloaded_samples": len(fetched.get("rows", []) or []),
                            "skipped_samples": dict(skipped),
                            "warnings": fetched.get("warnings", []),
                        }
                    )
                study_manifest["analyses"].append(analysis_entry)
            factor_rows_for_tsv = [
                [study_id, row.get("sample_id", ""), row.get("native_label", ""), row.get("mb_sample_id", ""), row.get("sample_source", "")]
                for row in factor_rows
            ]
            _ml_export_write_tsv(
                zf,
                f"{study_folder}/workbench_factor_labels.tsv",
                ["Study ID", "Sample ID", "Native label", "MB sample ID", "Sample source"],
                factor_rows_for_tsv,
            )
            _ml_export_write_tsv(
                zf,
                f"{study_folder}/sample_manifest.tsv",
                ["Study ID", "Sample ID", "Native label", "Exported Class Label", "ML-eligible in export", "Excluded by UI", "Label source"],
                list(sample_manifest_rows.values()),
            )
            zf.writestr(
                f"{study_folder}/manifest.json",
                json.dumps(study_manifest, indent=2, ensure_ascii=False),
            )
            manifest["studies"].append(study_manifest)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    if bulk:
        filename = f"merit_bulk_derived_assessment_inputs_{generated_at.replace(':', '').replace('-', '')}.zip"
    else:
        sid = _ml_export_slug(studies[0].get("study_id", "study") if studies else "study")
        filename = f"{sid}_merit_derived_assessment_inputs.zip"
    return buffer.getvalue(), filename


def _ml_ready_data_zip_payload(
    study_id: str,
    matrix_overrides: dict[str, Any] | str | None = None,
    analysis_ids: Any = None,
) -> tuple[bytes, str]:
    sid = str(study_id or "").strip().upper()
    if not (sid.startswith("ST") and len(sid) == 8 and sid[2:].isdigit()):
        raise ValueError("A valid Metabolomics Workbench study ID is required for the derived assessment-input export.")
    embargo_message = _embargoed_study_message(sid)
    if embargo_message:
        raise ValueError(embargo_message)
    overrides = _bulk_clean_matrix_overrides(matrix_overrides or {})
    return _ml_export_build_zip(
        [{"study_id": sid, "matrix_overrides": overrides, "analysis_ids": _ml_export_clean_analysis_ids(analysis_ids)}],
        bulk=False,
    )


def _bulk_ml_ready_data_zip_payload(raw_session: str) -> tuple[bytes, str]:
    session = _bulk_clean_session(raw_session)
    _raise_if_embargoed_bulk_session(session)
    return _ml_export_build_zip(session.get("studies", []), bulk=True)


def _bulk_report_sections(report: Any) -> list[tuple[str, list[Any]]]:
    return [
        ("Structural", list(getattr(report, "schema_validation", []) or [])),
        ("Metadata and FAIR Reusability", list(getattr(report, "metadata_readiness", []) or [])),
        ("Analytical QC", list(getattr(report, "analytical_readiness", []) or [])),
        ("Annotation / Interoperability", list(getattr(report, "annotation_readiness", []) or [])),
        ("Label Structure and Class Support", list(getattr(report, "cohort_bias", []) or [])),
        ("ML Task Readiness", list(getattr(report, "ml_readiness", []) or [])),
    ]


def _bulk_pick_source(state: dict[str, Any], preferred_source: str = "") -> tuple[str, Any | None, dict[str, Any]]:
    source_assessments = state.get("source_assessments") or {}
    preferred = str(preferred_source or "").strip()
    candidates = [
        preferred,
        str(state.get("primary_source") or "").strip(),
        "datatable",
        "mwtab",
        "untarg_data",
    ]
    for src in candidates:
        if not src or src == "summary":
            continue
        item = source_assessments.get(src)
        if isinstance(item, dict) and item.get("_report") is not None:
            score = item.get("readiness_score") if isinstance(item.get("readiness_score"), dict) else {}
            return src, item["_report"], score
    final_report = state.get("final_report")
    final_score = state.get("readiness_score") if isinstance(state.get("readiness_score"), dict) else {}
    if final_report is not None:
        return "summary", final_report, final_score
    return preferred or "unavailable", None, {}


def _bulk_get_metric(report: Any, name: str) -> Any | None:
    for _section, metrics in _bulk_report_sections(report):
        for metric in metrics:
            if getattr(metric, "name", "") == name:
                return metric
    return None


def _bulk_metric_value(report: Any, metric_name: str, detail_keys: tuple[str, ...] = ()) -> Any:
    metric = _bulk_get_metric(report, metric_name)
    details = getattr(metric, "details", {}) or {} if metric is not None else {}
    for key in detail_keys:
        if key in details:
            return details.get(key)
    return None


def _bulk_score100(score: Any) -> str:
    try:
        return f"{float(score) * 100:.1f}"
    except Exception:
        return ""


def _bulk_fmt_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if 0 <= value <= 1:
            return f"{value:.3f}"
        return f"{value:.3g}"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _bulk_text_cell(value: Any) -> str:
    text = _bulk_fmt_value(value)
    text = re.sub(r"[\t\r\n]+", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _bulk_rows_to_tsv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append("\t".join(_bulk_text_cell(row.get(col, "")) for col in columns))
    return "\n".join(lines) + "\n"


_BULK_EXPORT_PROVENANCE_COLUMNS = [
    "repository",
    "study_id",
    "project_id",
    "project_doi",
    "original_study_url",
    "associated_publication_doi",
    "associated_publication_pmid",
    "accessed_on",
    "source_matrix_type",
    "source_hash",
    "MERIT_version",
    "citation_text",
]


def _bulk_export_columns(columns: list[str]) -> list[str]:
    merged: list[str] = []
    for col in [*_BULK_EXPORT_PROVENANCE_COLUMNS, *columns]:
        if col not in merged:
            merged.append(col)
    return merged


def _bulk_export_readme_text() -> str:
    return (
        "This bulk export contains MERIT-ML-derived assessment metrics for public repository records. "
        "It does not replace the original repository records. Users must cite the original "
        "Metabolomics Workbench/NMDR Project ID, Project DOI where available, Study ID/accession, "
        "and associated publication(s) where applicable."
    )


def _bulk_publication_identifiers(publications: Any) -> tuple[str, str]:
    dois: list[str] = []
    pmids: list[str] = []
    if isinstance(publications, list):
        for publication in publications:
            if not isinstance(publication, dict):
                continue
            doi = str(publication.get("doi") or "").strip()
            pmid = str(publication.get("pubmed_id") or publication.get("pmid") or "").strip()
            if doi and doi not in dois:
                dois.append(doi)
            if pmid and pmid not in pmids:
                pmids.append(pmid)
    return "; ".join(dois), "; ".join(pmids)


def _bulk_citation_text(study_id: str, project_id: str, project_doi: str, publications: Any) -> str:
    project_display = project_id or "NA"
    doi_display = project_doi or "NA"
    pub_doi, pub_pmid = _bulk_publication_identifiers(publications)
    pub_note = ""
    if pub_doi or pub_pmid:
        pub_bits = []
        if pub_doi:
            pub_bits.append(f"publication DOI(s): {pub_doi}")
        if pub_pmid:
            pub_bits.append(f"PubMed ID(s): {pub_pmid}")
        pub_note = " Associated publication metadata detected: " + "; ".join(pub_bits) + "."
    else:
        pub_note = " No associated publication was detected in the parsed Metabolomics Workbench metadata; users should verify the original Metabolomics Workbench study page before publication."
    return (
        "This data is available at the NIH Common Fund's National Metabolomics Data Repository "
        "(NMDR) website, the Metabolomics Workbench, https://www.metabolomicsworkbench.org "
        f"where it has been assigned Study ID {study_id or 'NA'} and Project ID {project_display}. "
        f"The Project DOI parsed by MERIT-ML is {doi_display}. "
        "Please cite the Metabolomics Workbench as: "
        "\"The Metabolomics Workbench, https://www.metabolomicsworkbench.org/\"."
        f"{pub_note}"
    )


def _bulk_provenance_fields(
    *,
    study_id: str,
    source_matrix_type: str = "",
    accessed_on: Any = "",
    source_hash: Any = "",
    precomputed_root: str | Path | None = None,
) -> dict[str, Any]:
    sid = str(study_id or "").strip().upper()
    citation_root = precomputed_root if precomputed_root is not None else _default_precomputed_root()
    citation_index = _load_citation_index(str(citation_root))
    citation = citation_index.get(sid, {}) if isinstance(citation_index, dict) else {}
    if not isinstance(citation, dict):
        citation = {}
    publications = citation.get("related_publications") if isinstance(citation.get("related_publications"), list) else []
    pub_doi, pub_pmid = _bulk_publication_identifiers(publications)
    project_id = str(citation.get("project_id") or "").strip()
    project_doi = str(citation.get("project_doi") or "").strip()
    return {
        "repository": "Metabolomics Workbench",
        "study_id": sid,
        "project_id": project_id,
        "project_doi": project_doi,
        "original_study_url": _workbench_study_url(sid) if sid else "",
        "associated_publication_doi": pub_doi,
        "associated_publication_pmid": pub_pmid,
        "accessed_on": accessed_on or "",
        "source_matrix_type": source_matrix_type or "",
        "source_hash": source_hash or "",
        "MERIT_version": MERIT_VERSION,
        "citation_text": _bulk_citation_text(sid, project_id, project_doi, publications),
    }


def _bulk_export_session_payload(session: dict[str, Any], summary_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload = json.loads(json.dumps(session, default=str))
    payload["bulk_export_notice"] = _bulk_export_readme_text()
    payload["provenance_columns_added_to_tsv_exports"] = list(_BULK_EXPORT_PROVENANCE_COLUMNS)
    payload["citation_responsibility"] = (
        "Users must cite the original Metabolomics Workbench/NMDR Project ID, Project DOI where "
        "available, Study ID/accession, and associated publication(s) where applicable."
    )
    if summary_rows:
        payload["study_provenance"] = [
            {col: row.get(col, "") for col in _BULK_EXPORT_PROVENANCE_COLUMNS}
            for row in summary_rows
        ]
    return payload


def _bulk_first_recommendation(metric: Any) -> str:
    recs = getattr(metric, "recommendations", None)
    if isinstance(recs, list) and recs:
        return str(recs[0])
    return ""


def _bulk_lowest_section(score_payload: dict[str, Any]) -> tuple[str, float | None]:
    section_scores = score_payload.get("section_scores", {}) if isinstance(score_payload, dict) else {}
    if not isinstance(section_scores, dict) or not section_scores:
        return "", None
    valid = []
    for key, value in section_scores.items():
        try:
            valid.append((str(key), float(value)))
        except Exception:
            continue
    if not valid:
        return "", None
    return min(valid, key=lambda item: item[1])


def _bulk_lowest_metric(report: Any) -> tuple[str, str, float | None, str]:
    candidates: list[tuple[str, str, float, str]] = []
    for section, metrics in _bulk_report_sections(report):
        for metric in metrics:
            name = str(getattr(metric, "name", "") or "")
            if name in _V2_HIDDEN_LEGACY_METRICS:
                continue
            try:
                score = float(getattr(metric, "score", 0.0))
            except Exception:
                continue
            status = str(getattr(metric, "status", "") or "")
            candidates.append((section, name, score, status))
    if not candidates:
        return "", "", None, ""
    return min(candidates, key=lambda item: item[2])


def _bulk_gate_summary(score_payload: dict[str, Any]) -> tuple[str, str, str]:
    gates = score_payload.get("gates", []) if isinstance(score_payload, dict) else []
    gate_summary = score_payload.get("gate_summary", {}) if isinstance(score_payload, dict) else {}
    parts = []
    if isinstance(gate_summary, dict):
        parts = [f"pass {int(gate_summary.get('pass', 0) or 0)}", f"warn {int(gate_summary.get('warn', 0) or 0)}", f"fail {int(gate_summary.get('fail', 0) or 0)}"]
    worst = ""
    worst_detail = ""
    if isinstance(gates, list):
        for desired in ("fail", "warn"):
            for gate in gates:
                if isinstance(gate, dict) and str(gate.get("status", "")).lower() == desired:
                    worst = f"{gate.get('id', '')} {gate.get('name', '')}".strip()
                    worst_detail = str(gate.get("summary", "") or gate.get("rule", "") or "")
                    return "; ".join(parts), worst, worst_detail
    return "; ".join(parts), worst, worst_detail


def _bulk_metric_long_rows(report: Any, score_payload: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    status_rank = {"fail": 1, "warn": 2, "pass": 3}
    for section, metrics in _bulk_report_sections(report):
        for metric in metrics:
            name = str(getattr(metric, "name", "") or "")
            if name in _V2_HIDDEN_LEGACY_METRICS:
                continue
            status = str(getattr(metric, "status", "") or "")
            score = getattr(metric, "score", None)
            try:
                score_float = float(score)
            except Exception:
                score_float = 0.0
            details = getattr(metric, "details", {}) or {}
            rows.append(
                {
                    **base,
                    "section": section,
                    "metric_name": name,
                    "metric_score_0_100": round(score_float * 100.0, 1),
                    "metric_status": status,
                    "metric_priority_group": status_rank.get(status.lower(), 4),
                    "metric_recommendation": _bulk_first_recommendation(metric),
                    "metric_details_compact": {
                        k: v
                        for k, v in details.items()
                        if k in {
                            "n_biological_samples",
                            "n_total_samples",
                            "n_classes",
                            "min_group_size",
                            "minimum_class_count",
                            "global_median_sample_missingness_rate",
                            "median_pn_ratio",
                            "ratio",
                            "total_features",
                            "unknown_features",
                            "unknown_fraction",
                            "counts",
                        }
                    },
                }
            )
    rows.sort(key=lambda r: (r.get("study_priority_rank", 999999), r.get("metric_priority_group", 4), float(r.get("metric_score_0_100", 0) or 0), str(r.get("metric_name", ""))))
    return rows


def _bulk_refmet_fields(report: Any) -> dict[str, Any]:
    metric = _bulk_get_metric(report, "fair_metabolite_identifier_resolvability")
    details = getattr(metric, "details", {}) or {} if metric is not None else {}
    try:
        named = int(details.get("named_metabolites", 0) or 0)
    except Exception:
        named = 0
    try:
        matched = int(details.get("refmet_matched", 0) or 0)
    except Exception:
        matched = 0
    pct = (matched / named * 100.0) if named > 0 else None
    if named > 0:
        display = f"{'Yes' if matched > 0 else 'No'}: {pct:.1f}% ({matched}/{named})"
    else:
        display = "No named features"
    return {
        "has_refmet_annotations": "yes" if matched > 0 else "no",
        "n_named_metabolites": named,
        "refmet_matched_features": matched,
        "refmet_coverage_pct": round(pct, 1) if pct is not None else "",
        "refmet_summary": display,
    }


def _bulk_run_from_session(session: dict[str, Any], precomputed_root: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    summary_rows: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for item in session.get("studies", []):
        sid = str(item.get("study_id", "")).strip().upper()
        params = item.get("scoring_params") if isinstance(item.get("scoring_params"), dict) else _V2_DEFAULT_PARAMS
        matrix_overrides = item.get("matrix_overrides") if isinstance(item.get("matrix_overrides"), dict) else {}
        try:
            state = _load_precomputed_state(
                study_id=sid,
                precomputed_root=precomputed_root,
                requested_profile="full",
            )
            if state is None:
                raise ValueError("study not found in the selected MERIT-ML assessment set")
            adjusted = _v2_apply_scoring_profile(state, params, matrix_overrides=matrix_overrides) or state
            selected_source, report, score_payload = _bulk_pick_source(adjusted, str(item.get("selected_source", "") or ""))
            if report is None:
                raise ValueError("no report object available after loading cached state")
            summary = dict(getattr(report, "ingestion_summary", {}) or {})
            score_payload = score_payload if isinstance(score_payload, dict) else {}
            score = float(score_payload.get("core_ml_readiness_score", score_payload.get("score", 0.0)) or 0.0)
            final_band_raw = str(score_payload.get("final_band", score_payload.get("band", "")) or "")
            final_band_label = _v2_band_label(final_band_raw)
            band_rank = _V2_BAND_ORDER.get(final_band_raw, -1)
            gate_summary, worst_gate, worst_gate_detail = _bulk_gate_summary(score_payload)
            low_sec, low_sec_score = _bulk_lowest_section(score_payload)
            low_metric_section, low_metric, low_metric_score, low_metric_status = _bulk_lowest_metric(report)
            min_sample = _bulk_get_metric(report, "minimum_sample_count")
            min_sample_details = getattr(min_sample, "details", {}) or {} if min_sample is not None else {}
            group_support = _bulk_get_metric(report, "group_size_support")
            group_details = getattr(group_support, "details", {}) or {} if group_support is not None else {}
            label_suit = _bulk_get_metric(report, "label_suitability")
            label_details = getattr(label_suit, "details", {}) or {} if label_suit is not None else {}
            missing = _bulk_get_metric(report, "missingness_structure")
            missing_details = getattr(missing, "details", {}) or {} if missing is not None else {}
            fsr = _bulk_get_metric(report, "feature_to_sample_ratio")
            fsr_details = getattr(fsr, "details", {}) or {} if fsr is not None else {}
            refmet_fields = _bulk_refmet_fields(report)
            counts = group_details.get("counts") or label_details.get("counts") or summary.get("class_counts") or {}
            n_classes = summary.get("n_classes") or len(counts if isinstance(counts, dict) else {})
            n_labeled_samples = summary.get("n_labeled_samples")
            if n_labeled_samples in (None, "") and isinstance(counts, dict) and counts:
                try:
                    n_labeled_samples = sum(int(v) for v in counts.values())
                except Exception:
                    n_labeled_samples = ""
            min_class = group_details.get("min_group_size") if "min_group_size" in group_details else None
            if min_class is None:
                min_class = label_details.get("minimum_class_count")
            if min_class is None and isinstance(counts, dict) and counts:
                try:
                    min_class = min(int(v) for v in counts.values())
                except Exception:
                    min_class = None
            median_missing = missing_details.get("global_median_sample_missingness_rate")
            pn_ratio = fsr_details.get("median_pn_ratio", fsr_details.get("ratio"))
            if pn_ratio is None:
                n_ml = summary.get("n_biological_samples") or summary.get("n_samples") or 0
                n_features = summary.get("n_features") or 0
                pn_ratio = (float(n_features) / float(n_ml)) if n_ml else None
            custom_thresholds = not _v2_is_default_params(params)
            fail_count = int((score_payload.get("gate_summary", {}) or {}).get("fail", 0) or 0)
            warn_count = int((score_payload.get("gate_summary", {}) or {}).get("warn", 0) or 0)
            severity_bucket = 0 if fail_count else (1 if warn_count else 2)
            priority_tuple = (severity_bucket, band_rank if band_rank >= 0 else 99, score)
            row = {
                "study_id": sid,
                "title": summary.get("title") or item.get("title") or "",
                "organism": summary.get("organism") or item.get("organism") or "",
                "source": selected_source,
                "analysis_type": summary.get("analysis_type", ""),
                "n_samples_total": summary.get("n_samples", ""),
                "n_ml_eligible_samples": summary.get("n_biological_samples", ""),
                "n_labeled_samples": n_labeled_samples,
                "n_classes": n_classes,
                "min_class_size": min_class,
                "n_features": summary.get("n_features", ""),
                **refmet_fields,
                "feature_to_sample_ratio": pn_ratio,
                "median_sample_missingness_pct": (float(median_missing) * 100.0) if median_missing is not None else "",
                "core_score_0_100": round(score * 100.0, 1),
                "final_band": final_band_label,
                "legacy_final_band": final_band_raw,
                "gate_summary": gate_summary,
                "worst_gate": worst_gate,
                "worst_gate_detail": worst_gate_detail,
                "lowest_section": low_sec,
                "lowest_section_score_0_100": round(float(low_sec_score) * 100.0, 1) if low_sec_score is not None else "",
                "lowest_metric_section": low_metric_section,
                "lowest_metric": low_metric,
                "lowest_metric_score_0_100": round(float(low_metric_score) * 100.0, 1) if low_metric_score is not None else "",
                "lowest_metric_status": low_metric_status,
                "matrix_override_count": len(matrix_overrides),
                "custom_thresholds_applied": "yes" if custom_thresholds else "no",
                "recommendation": score_payload.get("recommendation", ""),
                "_priority_tuple": priority_tuple,
            }
            row.update(
                _bulk_provenance_fields(
                    study_id=sid,
                    source_matrix_type=selected_source,
                    accessed_on=summary.get("accessed_date", ""),
                    source_hash=summary.get("content_hash", ""),
                    precomputed_root=precomputed_root,
                )
            )
            summary_rows.append(row)
            metric_rows.extend(_bulk_metric_long_rows(report, score_payload, row))
        except Exception as exc:
            errors.append(f"{sid}: {exc}")
            error_source = str(item.get("selected_source", "") or "")
            error_row = {
                "study_id": sid,
                "title": item.get("title", ""),
                "organism": item.get("organism", ""),
                "source": error_source,
                "analysis_type": "",
                "n_samples_total": "",
                "n_ml_eligible_samples": "",
                "n_labeled_samples": "",
                "n_classes": "",
                "min_class_size": "",
                "n_features": "",
                "has_refmet_annotations": "",
                "n_named_metabolites": "",
                "refmet_matched_features": "",
                "refmet_coverage_pct": "",
                "refmet_summary": "",
                "feature_to_sample_ratio": "",
                "median_sample_missingness_pct": "",
                "core_score_0_100": "",
                "final_band": "Error",
                "legacy_final_band": "Error",
                "gate_summary": "",
                "worst_gate": "load_error",
                "worst_gate_detail": str(exc),
                "lowest_section": "",
                "lowest_section_score_0_100": "",
                "lowest_metric_section": "",
                "lowest_metric": "",
                "lowest_metric_score_0_100": "",
                "lowest_metric_status": "",
                "matrix_override_count": len(matrix_overrides),
                "custom_thresholds_applied": "yes" if not _v2_is_default_params(item.get("scoring_params", {})) else "no",
                "recommendation": "Check whether this study exists in the selected MERIT-ML assessment set.",
                "_priority_tuple": (-1, -1, 0),
            }
            error_row.update(
                _bulk_provenance_fields(
                    study_id=sid,
                    source_matrix_type=error_source,
                    precomputed_root=precomputed_root,
                )
            )
            summary_rows.append(error_row)

    summary_rows.sort(key=lambda r: r.get("_priority_tuple", (99, 99, 99)))
    for idx, row in enumerate(summary_rows, start=1):
        row["study_priority_rank"] = idx
        row.pop("_priority_tuple", None)
    for row in metric_rows:
        sid = row.get("study_id")
        rank = next((r.get("study_priority_rank") for r in summary_rows if r.get("study_id") == sid), 999999)
        row["study_priority_rank"] = rank
    metric_rows.sort(key=lambda r: (r.get("study_priority_rank", 999999), r.get("metric_priority_group", 4), float(r.get("metric_score_0_100", 0) or 0), str(r.get("metric_name", ""))))
    return summary_rows, metric_rows, errors


def _bulk_chunk_payload(raw_session: str, precomputed_root: str | Path) -> dict[str, Any]:
    session = _bulk_clean_session(raw_session)
    _raise_if_embargoed_bulk_session(session)
    summary_rows, metric_rows, errors = _bulk_run_from_session(session, precomputed_root)
    return {
        "ok": True,
        "n_used": session.get("n_used", len(session.get("studies", []))),
        "summary_rows": summary_rows,
        "metric_rows": metric_rows,
        "errors": errors,
    }


def _bulk_runner_page(session: dict[str, Any], precomputed_root: str | Path) -> str:
    """Return an immediate page that computes Bulk MERIT-ML in small API chunks.

    Vercel kills a single long-running request after the configured function
    limit. The runner keeps each request bounded while preserving a 500-study
    per-run user workflow.
    """
    _raise_if_embargoed_bulk_session(session)
    header_help = {
        "priority": "Action order: fail gates first, then warn gates, then lower readiness scores.",
        "study": "Metabolomics Workbench study accession.",
        "title": "Study title from the parsed Metabolomics Workbench metadata. Long titles are clipped to two lines in this compact table.",
        "organism": "Primary organism parsed from the cached study metadata.",
        "source": "Cached source used for the displayed assessment, usually datatable, mwTab, or untarg_data.",
        "ml_samples": "ML-eligible sample count after excluding QC, blanks, pools, references, standards, and similar non-training samples.",
        "unique_classes": "Number of distinct usable class labels after class-label normalization.",
        "min_class": "Smallest deposited label group after ML-eligible filtering; low values limit supervised validation.",
        "features": "Number of machine-readable feature columns in the selected source matrix.",
        "refmet": "RefMet-resolved named metabolites from the FAIR metabolite identifier metric: matched/named and percent matched.",
        "pn": "Feature-to-sample ratio for the selected source. Higher values require stronger regularization or feature selection.",
        "missing": "Median sample-level missingness percentage across the selected source matrix.",
        "score": "Core ML readiness score after any local threshold or matrix-property adjustments, shown on a 0-100 scale.",
        "band": "Final readiness band after applying feasibility-gate ceilings.",
        "gate": "First failed or warning gate driving the action priority.",
        "lowest_metric": "Lowest-scoring active MERIT-ML metric for this study/source.",
    }

    def _th(label: str, key: str, sort: str = "") -> str:
        sort_attr = " data-sort='num'" if sort == "num" else ""
        help_text = header_help.get(key, "")
        return (
            f"<th{sort_attr} title='{_e(help_text)}'>"
            f"<span>{_e(label)}</span>"
            f"<span class='th-tip' aria-label='{_e(help_text)}' title='{_e(help_text)}'>i</span>"
            "</th>"
        )

    session_json = json.dumps(session, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    bulk_readme_json = json.dumps(_bulk_export_readme_text(), ensure_ascii=False).replace("</", "<\\/")
    provenance_cols_json = json.dumps(_BULK_EXPORT_PROVENANCE_COLUMNS, ensure_ascii=False).replace("</", "<\\/")
    total = len(session.get("studies", []))
    return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Bulk MERIT-ML Analysis</title>
{_merit_analytics_head_script()}
<style>
:root{{--ink:#132327;--muted:#51656a;--paper:#f5f1e8;--line:rgba(19,35,39,.12);--accent:#0d6e6e;--accent2:#d27d2d}}
*{{box-sizing:border-box}}
body{{margin:0;color:var(--ink);font-family:"IBM Plex Sans","Avenir Next","Segoe UI",sans-serif;
  background:radial-gradient(circle at top right,rgba(13,110,110,.15),transparent 36%),linear-gradient(180deg,#f7f3ea,#eaf0f1)}}
main{{width:min(1420px,calc(100vw - 32px));margin:24px auto 40px}}
.panel{{background:rgba(255,255,255,.84);border:1px solid var(--line);border-radius:22px;padding:24px;box-shadow:0 24px 60px rgba(19,35,39,.10)}}
h1{{margin:0;font-family:"Iowan Old Style",Georgia,serif;font-size:2.1rem}}
.sub{{color:var(--muted);line-height:1.55;margin:8px 0 0;max-width:82ch}}
.actions{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}
button,a.btn{{border:1px solid rgba(13,110,110,.28);border-radius:13px;background:rgba(13,110,110,.08);color:#0d6e6e;
  padding:10px 13px;font:inherit;font-weight:800;text-decoration:none;cursor:pointer}}
button:disabled{{opacity:.45;cursor:not-allowed}}
.progress-shell{{height:14px;border-radius:999px;background:rgba(19,35,39,.10);overflow:hidden;margin:18px 0 8px}}
#progress-bar{{height:100%;width:0%;background:linear-gradient(90deg,#0d6e6e,#d27d2d);transition:width .25s ease}}
.status-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:14px 0}}
.stat{{border:1px solid var(--line);background:rgba(255,255,255,.70);border-radius:14px;padding:10px}}
.stat b{{display:block;color:#0d6e6e;font-size:1.2rem}}
.stat span{{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:#51656a;font-weight:800}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;background:rgba(255,255,255,.72);border-radius:14px;overflow:hidden}}
th{{position:sticky;top:0;background:#0d6e6e;color:white;text-align:left;padding:9px;font-size:.72rem;letter-spacing:.04em;text-transform:uppercase;cursor:pointer}}
th .th-tip{{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;margin-left:5px;border-radius:50%;
  background:rgba(255,255,255,.24);color:#fff;font-size:.62rem;font-weight:900;text-transform:none;letter-spacing:0;vertical-align:middle}}
td{{vertical-align:top;padding:8px 9px;border-bottom:1px solid rgba(19,35,39,.08)}}
.table-wrap{{display:none;max-height:68vh;overflow:auto;border:1px solid var(--line);border-radius:14px;margin-top:14px}}
.table-wrap table{{min-width:1680px}}
.pill{{display:inline-flex;border-radius:999px;padding:3px 9px;background:rgba(13,110,110,.09);color:#0d6e6e;font-weight:800;font-size:.75rem}}
#bulk-errors{{display:none;margin:14px 0;padding:12px 14px;border-radius:14px;background:#fdeaea;color:#8f2d2d;border:1px solid rgba(143,45,45,.22);font-size:.86rem;line-height:1.45}}
code{{background:rgba(19,35,39,.08);border-radius:6px;padding:1px 4px}}
{_merit_analytics_consent_css()}
</style>
</head>
<body>
<main>
  <section class='panel'>
    <h1>Bulk MERIT-ML Analysis</h1>
    <p class='sub'>Running MERIT-ML for {total} selected studies. Keep this tab open until the run completes; downloadable tables will appear here.</p>
    <div class='actions'>
      <a class='btn' href='/'>Back to MERIT-ML UI</a>
      <button id='download-summary' type='button' disabled>Download summary TSV</button>
      <button id='download-metrics' type='button' disabled>Download metrics-long TSV</button>
      <button id='download-session' type='button'>Download session JSON</button>
      <button id='download-readme' type='button'>Download README</button>
      <span class='pill'>{total} studies selected</span>
    </div>
    <div class='progress-shell'><div id='progress-bar'></div></div>
    <p id='bulk-status' class='sub'>Starting Bulk MERIT-ML run...</p>
    <div class='status-grid'>
      <div class='stat'><b id='stat-done'>0</b><span>Studies processed</span></div>
      <div class='stat'><b id='stat-total'>{total}</b><span>Total studies</span></div>
      <div class='stat'><b id='stat-chunks'>0</b><span>Chunks finished</span></div>
      <div class='stat'><b id='stat-errors'>0</b><span>Errors</span></div>
    </div>
    <div id='bulk-errors'></div>
    <div id='bulk-table-wrap' class='table-wrap'>
      <table id='bulk-table'>
        <thead>
          <tr>
            {_th("Priority", "priority", "num")}
            {_th("Study", "study")}
            {_th("Title", "title")}
            {_th("Organism", "organism")}
            {_th("Source", "source")}
            {_th("ML samples", "ml_samples", "num")}
            {_th("Number of unique classes", "unique_classes", "num")}
            {_th("Min class", "min_class", "num")}
            {_th("Features", "features", "num")}
            {_th("RefMet", "refmet", "num")}
            {_th("p/n", "pn", "num")}
            {_th("Missing %", "missing", "num")}
            {_th("Score", "score", "num")}
            {_th("Band", "band")}
            {_th("Gate driver", "gate")}
            {_th("Lowest metric", "lowest_metric")}
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
    <p class='sub' style='margin-top:14px'>The summary table is compact. The metrics-long TSV contains every displayed MERIT-ML metric per study/source, sorted by fail, warn, and pass status followed by score.</p>
  </section>
</main>
<script id='bulk-session-json' type='application/json'>{session_json}</script>
<script>
(function(){{
  var session = JSON.parse(document.getElementById('bulk-session-json').textContent || '{{}}');
  var bulkExportReadme = {bulk_readme_json};
  var provenanceCols = {provenance_cols_json};
  var allStudies = Array.isArray(session.studies) ? session.studies : [];
  var total = allStudies.length;
  var chunkSize = 20;
  var maxConcurrent = 3;
  var nextOffset = 0;
  var active = 0;
  var completedStudies = 0;
  var completedChunks = 0;
  var summaryRows = [];
  var metricRows = [];
  var errors = [];
  var finalized = false;
  function uniqueCols(cols) {{
    var seen = Object.create(null);
    return cols.filter(function(col) {{
      if (seen[col]) return false;
      seen[col] = true;
      return true;
    }});
  }}
  var summaryCols = uniqueCols(provenanceCols.concat([
    'study_priority_rank','study_id','title','organism','source','analysis_type','n_samples_total',
    'n_ml_eligible_samples','n_classes','min_class_size','n_features','has_refmet_annotations',
    'n_named_metabolites','refmet_matched_features','refmet_coverage_pct','feature_to_sample_ratio',
    'median_sample_missingness_pct','core_score_0_100','final_band','gate_summary','worst_gate',
    'worst_gate_detail','lowest_section','lowest_section_score_0_100','lowest_metric',
    'lowest_metric_score_0_100','matrix_override_count','custom_thresholds_applied','recommendation'
  ]));
  var metricCols = uniqueCols(provenanceCols.concat([
    'study_priority_rank','study_id','source','final_band','core_score_0_100','section','metric_name',
    'metric_score_0_100','metric_status','metric_recommendation','metric_details_compact'
  ]));
  function esc(text) {{
    return String(text == null ? '' : text)
      .replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
  }}
  function cell(value) {{
    if (value == null) return '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value).replace(/[\\t\\r\\n]+/g, ' ').replace(/\\s{{2,}}/g, ' ').trim();
  }}
  function toTsv(rows, cols) {{
    return cols.join('\\t') + '\\n' + rows.map(function(row) {{
      return cols.map(function(col) {{
        var text = cell(row[col]).replace(/"/g, '""');
        return /[\\t\\n"]/.test(text) ? '"' + text + '"' : text;
      }}).join('\\t');
    }}).join('\\n') + '\\n';
  }}
  function download(name, text, type) {{
    var blob = new Blob([text], {{type: type || 'text/plain'}});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function(){{ URL.revokeObjectURL(a.href); }}, 300);
  }}
  function countGate(row, key) {{
    var m = String(row.gate_summary || '').match(new RegExp(key + '\\\\s+(\\\\d+)', 'i'));
    return m ? Number(m[1] || 0) : 0;
  }}
  function bandRank(row) {{
    var band = String(row.legacy_final_band || row.final_band || '');
    var map = {{
      'Error': -1,
      'No Data': 0,
      'Metadata-only record': 0,
      'Not Ready': 1,
      'Class-support limited': 1,
      'Fragile': 2,
      'Exploratory ML use': 2,
      'Conditional': 3,
      'ML-ready with caveats': 3,
      'Ready': 4,
      'ML-ready': 4
    }};
    return Object.prototype.hasOwnProperty.call(map, band) ? map[band] : 99;
  }}
  function severity(row) {{
    if (String(row.final_band || '') === 'Error') return -1;
    if (countGate(row, 'fail') > 0) return 0;
    if (countGate(row, 'warn') > 0) return 1;
    return 2;
  }}
  function sortRows() {{
    summaryRows.sort(function(a,b) {{
      return (severity(a) - severity(b))
        || (bandRank(a) - bandRank(b))
        || ((parseFloat(a.core_score_0_100) || 0) - (parseFloat(b.core_score_0_100) || 0))
        || String(a.study_id || '').localeCompare(String(b.study_id || ''));
    }});
    var rank = {{}};
    summaryRows.forEach(function(row, idx) {{
      row.study_priority_rank = idx + 1;
      rank[String(row.study_id || '')] = idx + 1;
    }});
    metricRows.forEach(function(row) {{
      row.study_priority_rank = rank[String(row.study_id || '')] || 999999;
    }});
    metricRows.sort(function(a,b) {{
      return ((Number(a.study_priority_rank) || 999999) - (Number(b.study_priority_rank) || 999999))
        || ((Number(a.metric_priority_group) || 4) - (Number(b.metric_priority_group) || 4))
        || ((parseFloat(a.metric_score_0_100) || 0) - (parseFloat(b.metric_score_0_100) || 0))
        || String(a.metric_name || '').localeCompare(String(b.metric_name || ''));
    }});
  }}
  function renderTable() {{
    var tbody = document.querySelector('#bulk-table tbody');
    if (!tbody) return;
    tbody.innerHTML = summaryRows.map(function(row) {{
      var sid = esc(row.study_id || '');
      var href = sid ? '/?study_id=' + encodeURIComponent(sid) + '&profile=full' : '#';
      return '<tr>'
        + '<td style="text-align:right">' + esc(row.study_priority_rank) + '</td>'
        + '<td>' + sid + '</td>'
        + '<td title="' + esc(row.title || '') + '" style="max-width:260px;min-width:210px"><div style="line-height:1.28;color:#51656a;font-size:.78rem;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden"><a href="' + href + '" target="_blank" rel="noopener noreferrer" style="color:#0d6e6e;text-decoration:none;font-weight:700">' + esc(row.title || 'Open MERIT-ML report') + '</a></div></td>'
        + '<td>' + esc(row.organism || '') + '</td>'
        + '<td>' + esc(row.source || '') + '</td>'
        + '<td style="text-align:right">' + esc(row.n_ml_eligible_samples || '') + '</td>'
        + '<td style="text-align:right">' + esc(row.n_classes || '') + '</td>'
        + '<td style="text-align:right">' + esc(row.min_class_size || '') + '</td>'
        + '<td style="text-align:right">' + esc(row.n_features || '') + '</td>'
        + '<td style="text-align:right">' + esc(row.refmet_summary || '') + '</td>'
        + '<td style="text-align:right">' + esc(row.feature_to_sample_ratio || '') + '</td>'
        + '<td style="text-align:right">' + esc(row.median_sample_missingness_pct || '') + '</td>'
        + '<td style="text-align:right">' + esc(row.core_score_0_100 || '') + '</td>'
        + '<td>' + esc(row.final_band || '') + '</td>'
        + '<td>' + esc(row.worst_gate || '') + '</td>'
        + '<td>' + esc(row.lowest_metric || '') + '</td>'
        + '</tr>';
    }}).join('');
    document.getElementById('bulk-table-wrap').style.display = 'block';
  }}
  function updateStatus(text) {{
    var pct = total ? Math.min(100, Math.round((completedStudies / total) * 100)) : 100;
    document.getElementById('progress-bar').style.width = pct + '%';
    document.getElementById('stat-done').textContent = String(completedStudies);
    document.getElementById('stat-chunks').textContent = String(completedChunks);
    document.getElementById('stat-errors').textContent = String(errors.length);
    document.getElementById('bulk-status').textContent = text || ('Processed ' + completedStudies + ' of ' + total + ' studies...');
    var errBox = document.getElementById('bulk-errors');
    if (errors.length) {{
      errBox.style.display = 'block';
      errBox.innerHTML = '<strong>Warnings/errors:</strong><br>' + errors.slice(0, 30).map(esc).join('<br>') + (errors.length > 30 ? '<br>...' : '');
    }}
  }}
  function chunkSession(start) {{
    var chunk = allStudies.slice(start, start + chunkSize);
    return {{
      version: session.version || 1,
      created_at: session.created_at || '',
      updated_at: session.updated_at || '',
      studies: chunk
    }};
  }}
  function postChunk(start, attempt) {{
    var chunk = allStudies.slice(start, start + chunkSize);
    var body = new URLSearchParams();
    body.set('bulk_session', JSON.stringify(chunkSession(start)));
    body.set('chunk_start', String(start));
    body.set('chunk_size', String(chunk.length));
    return fetch('/bulk/chunk', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'}},
      body: body.toString(),
      cache: 'no-store'
    }}).then(function(resp) {{
      if (!resp.ok) return resp.text().then(function(text) {{ throw new Error(text || ('HTTP ' + resp.status)); }});
      return resp.json();
    }}).then(function(payload) {{
      if (!payload || !payload.ok) throw new Error((payload && payload.error) || 'Chunk failed');
      summaryRows = summaryRows.concat(payload.summary_rows || []);
      metricRows = metricRows.concat(payload.metric_rows || []);
      (payload.errors || []).forEach(function(err) {{ errors.push(err); }});
      completedStudies += chunk.length;
      completedChunks += 1;
      updateStatus('Processed ' + completedStudies + ' of ' + total + ' studies...');
    }}).catch(function(err) {{
      if ((attempt || 0) < 2) {{
        updateStatus('Retrying studies ' + (start + 1) + '-' + (start + chunk.length) + ' after a transient error...');
        return new Promise(function(resolve) {{ setTimeout(resolve, 1200 * ((attempt || 0) + 1)); }})
          .then(function() {{ return postChunk(start, (attempt || 0) + 1); }});
      }}
      errors.push('Studies ' + (start + 1) + '-' + (start + chunk.length) + ': ' + (err && err.message ? err.message : err));
      completedStudies += chunk.length;
      completedChunks += 1;
      updateStatus('Continuing after an error in studies ' + (start + 1) + '-' + (start + chunk.length) + '...');
    }});
  }}
  function finalize() {{
    if (finalized) return;
    finalized = true;
    sortRows();
    renderTable();
    updateStatus('Bulk MERIT-ML complete: ' + summaryRows.length + ' studies summarized, ' + metricRows.length + ' metric rows generated.');
    document.getElementById('download-summary').disabled = false;
    document.getElementById('download-metrics').disabled = false;
  }}
  function pump() {{
    while (active < maxConcurrent && nextOffset < total) {{
      var start = nextOffset;
      nextOffset += chunkSize;
      active += 1;
      updateStatus('Processing studies ' + (start + 1) + '-' + Math.min(start + chunkSize, total) + '...');
      postChunk(start, 0).finally(function() {{
        active -= 1;
        if (nextOffset >= total && active === 0) finalize();
        else pump();
      }});
    }}
    if (total === 0) finalize();
  }}
  document.getElementById('download-summary').addEventListener('click', function() {{
    download('bulk_merit_summary.tsv', toTsv(summaryRows, summaryCols), 'text/tab-separated-values');
  }});
  document.getElementById('download-metrics').addEventListener('click', function() {{
    download('bulk_merit_metrics_long.tsv', toTsv(metricRows, metricCols), 'text/tab-separated-values');
  }});
  document.getElementById('download-session').addEventListener('click', function() {{
    var exportedSession = JSON.parse(JSON.stringify(session || {{}}));
    exportedSession.bulk_export_notice = bulkExportReadme;
    exportedSession.provenance_columns_added_to_tsv_exports = provenanceCols;
    exportedSession.citation_responsibility = 'Users must cite the original Metabolomics Workbench/NMDR Project ID, Project DOI where available, Study ID/accession, and associated publication(s) where applicable.';
    exportedSession.study_provenance = summaryRows.map(function(row) {{
      var out = {{}};
      provenanceCols.forEach(function(col) {{ out[col] = row[col] == null ? '' : row[col]; }});
      return out;
    }});
    download('bulk_merit_session.json', JSON.stringify(exportedSession, null, 2), 'application/json');
  }});
  document.getElementById('download-readme').addEventListener('click', function() {{
    download('bulk_merit_README.txt', bulkExportReadme + '\\n', 'text/plain');
  }});
  document.querySelectorAll('#bulk-table th').forEach(function(th, idx) {{
    th.addEventListener('click', function() {{
      var numeric = th.getAttribute('data-sort') === 'num';
      var tbody = document.querySelector('#bulk-table tbody');
      var rows = Array.prototype.slice.call(tbody.rows);
      var dir = th.getAttribute('data-dir') === 'asc' ? -1 : 1;
      document.querySelectorAll('#bulk-table th').forEach(function(h) {{ h.removeAttribute('data-dir'); }});
      th.setAttribute('data-dir', dir === 1 ? 'asc' : 'desc');
      rows.sort(function(a,b) {{
        var av = a.cells[idx] ? a.cells[idx].innerText.trim() : '';
        var bv = b.cells[idx] ? b.cells[idx].innerText.trim() : '';
        if (numeric) {{
          var an = parseFloat(av); var bn = parseFloat(bv);
          if (isNaN(an)) an = 1e99;
          if (isNaN(bn)) bn = 1e99;
          return (an - bn) * dir;
        }}
        return av.localeCompare(bv) * dir;
      }});
      rows.forEach(function(row) {{ tbody.appendChild(row); }});
    }});
  }});
  updateStatus('Queued ' + total + ' studies in chunks of ' + chunkSize + ' with up to ' + maxConcurrent + ' concurrent requests.');
  pump();
}})();
</script>
{_merit_analytics_consent_banner()}
</body>
</html>"""


def _bulk_results_page(
    summary_rows: list[dict[str, Any]],
    metric_rows: list[dict[str, Any]],
    session: dict[str, Any],
    precomputed_root: str | Path,
    errors: list[str] | None = None,
) -> str:
    errors = errors or []
    summary_cols = _bulk_export_columns([
        "study_priority_rank",
        "study_id",
        "title",
        "organism",
        "source",
        "analysis_type",
        "n_samples_total",
        "n_ml_eligible_samples",
        "n_classes",
        "min_class_size",
        "n_features",
        "has_refmet_annotations",
        "n_named_metabolites",
        "refmet_matched_features",
        "refmet_coverage_pct",
        "feature_to_sample_ratio",
        "median_sample_missingness_pct",
        "core_score_0_100",
        "final_band",
        "gate_summary",
        "worst_gate",
        "worst_gate_detail",
        "lowest_section",
        "lowest_section_score_0_100",
        "lowest_metric",
        "lowest_metric_score_0_100",
        "matrix_override_count",
        "custom_thresholds_applied",
        "recommendation",
    ])
    metric_cols = _bulk_export_columns([
        "study_priority_rank",
        "study_id",
        "source",
        "final_band",
        "core_score_0_100",
        "section",
        "metric_name",
        "metric_score_0_100",
        "metric_status",
        "metric_recommendation",
        "metric_details_compact",
    ])
    summary_tsv = _bulk_rows_to_tsv(summary_rows, summary_cols)
    metric_tsv = _bulk_rows_to_tsv(metric_rows, metric_cols)
    bulk_readme = _bulk_export_readme_text()
    session_json = json.dumps(_bulk_export_session_payload(session, summary_rows), indent=2, sort_keys=True)
    downloads_json = json.dumps(
        {
            "bulk_merit_summary.tsv": summary_tsv,
            "bulk_merit_metrics_long.tsv": metric_tsv,
            "bulk_merit_session.json": session_json,
            "bulk_merit_README.txt": bulk_readme + "\n",
        }
    ).replace("</", "<\\/")

    header_help = {
        "priority": "Action order: fail gates first, then warn gates, then lower readiness scores.",
        "study": "Metabolomics Workbench study accession.",
        "title": "Study title from the parsed Metabolomics Workbench metadata. Long titles are clipped to two lines in this compact table.",
        "organism": "Primary organism parsed from the cached study metadata.",
        "source": "Cached source used for the displayed assessment, usually datatable, mwTab, or untarg_data.",
        "ml_samples": "ML-eligible sample count after excluding QC, blanks, pools, references, standards, and similar non-training samples.",
        "unique_classes": "Number of distinct usable class labels after class-label normalization.",
        "min_class": "Smallest deposited label group after ML-eligible filtering; low values limit supervised validation.",
        "features": "Number of machine-readable feature columns in the selected source matrix.",
        "refmet": "RefMet-resolved named metabolites from the FAIR metabolite identifier metric: matched/named and percent matched.",
        "pn": "Feature-to-sample ratio for the selected source. Higher values require stronger regularization or feature selection.",
        "missing": "Median sample-level missingness percentage across the selected source matrix.",
        "score": "Core ML readiness score after any local threshold or matrix-property adjustments, shown on a 0-100 scale.",
        "band": "Final readiness band after applying feasibility-gate ceilings.",
        "gate": "First failed or warning gate driving the action priority.",
        "lowest_metric": "Lowest-scoring active MERIT-ML metric for this study/source.",
    }

    def _th(label: str, key: str, sort: str = "") -> str:
        sort_attr = " data-sort='num'" if sort == "num" else ""
        help_text = header_help.get(key, "")
        return (
            f"<th{sort_attr} title='{_e(help_text)}'>"
            f"<span>{_e(label)}</span>"
            f"<span class='th-tip' aria-label='{_e(help_text)}' title='{_e(help_text)}'>i</span>"
            "</th>"
        )

    def _td(value: Any, align: str = "left", sort_value: Any | None = None) -> str:
        sort_attr = ""
        if sort_value is not None:
            sort_attr = f" data-sort-value='{_e(_bulk_text_cell(sort_value))}'"
        return f"<td{sort_attr} style='padding:8px 9px;border-bottom:1px solid rgba(19,35,39,.08);text-align:{align}'>{_e(_bulk_text_cell(value))}</td>"

    def _title_td(value: Any, study_id: Any) -> str:
        text = _bulk_text_cell(value)
        sid = _bulk_text_cell(study_id).upper()
        href = f"/?study_id={_e(sid)}&profile=full" if sid else "#"
        link_text = text or "Open MERIT-ML report"
        return (
            f"<td title='{_e(text)}' style='padding:8px 9px;border-bottom:1px solid rgba(19,35,39,.08);"
            "max-width:260px;min-width:210px'>"
            "<div style='line-height:1.28;color:#51656a;font-size:.78rem;display:-webkit-box;"
            "-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden'>"
            f"<a href='{href}' target='_blank' rel='noopener noreferrer' "
            "style='color:#0d6e6e;text-decoration:none;font-weight:700'>"
            f"{_e(link_text)}</a>"
            "</div></td>"
        )

    table_rows = "".join(
        "<tr>"
        + _td(row.get("study_priority_rank"), "right")
        + _td(row.get("study_id"))
        + _title_td(row.get("title"), row.get("study_id"))
        + _td(row.get("organism"))
        + _td(row.get("source"))
        + _td(row.get("n_ml_eligible_samples"), "right")
        + _td(row.get("n_classes"), "right")
        + _td(row.get("min_class_size"), "right")
        + _td(row.get("n_features"), "right")
        + _td(row.get("refmet_summary"), "right", row.get("refmet_coverage_pct") if row.get("refmet_coverage_pct") != "" else -1)
        + _td(row.get("feature_to_sample_ratio"), "right")
        + _td(row.get("median_sample_missingness_pct"), "right")
        + _td(row.get("core_score_0_100"), "right")
        + _td(row.get("final_band"))
        + _td(row.get("worst_gate"))
        + _td(row.get("lowest_metric"))
        + "</tr>"
        for row in summary_rows
    )
    error_html = (
        "<div style='margin:14px 0;padding:12px 14px;border-radius:14px;background:#fdeaea;color:#8f2d2d;"
        "border:1px solid rgba(143,45,45,.22);font-size:.88rem;line-height:1.45'>"
        "<strong>Some studies could not be loaded:</strong><br>"
        + "<br>".join(_e(err) for err in errors[:20])
        + ("<br>..." if len(errors) > 20 else "")
        + "</div>"
        if errors
        else ""
    )
    return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Bulk MERIT-ML Analysis</title>
{_merit_analytics_head_script()}
<style>
:root{{--ink:#132327;--muted:#51656a;--paper:#f5f1e8;--line:rgba(19,35,39,.12);--accent:#0d6e6e;--accent2:#d27d2d}}
*{{box-sizing:border-box}}
body{{margin:0;color:var(--ink);font-family:"IBM Plex Sans","Avenir Next","Segoe UI",sans-serif;
  background:radial-gradient(circle at top right,rgba(13,110,110,.15),transparent 36%),linear-gradient(180deg,#f7f3ea,#eaf0f1)}}
main{{width:min(1420px,calc(100vw - 32px));margin:24px auto 40px}}
.panel{{background:rgba(255,255,255,.82);border:1px solid var(--line);border-radius:22px;padding:24px;box-shadow:0 24px 60px rgba(19,35,39,.10)}}
h1{{margin:0;font-family:"Iowan Old Style",Georgia,serif;font-size:2.1rem}}
.sub{{color:var(--muted);line-height:1.55;margin:8px 0 0;max-width:78ch}}
.actions{{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}}
button,a.btn{{border:1px solid rgba(13,110,110,.28);border-radius:13px;background:rgba(13,110,110,.08);color:#0d6e6e;
  padding:10px 13px;font:inherit;font-weight:800;text-decoration:none;cursor:pointer}}
table{{width:100%;border-collapse:collapse;font-size:.82rem;background:rgba(255,255,255,.72);border-radius:14px;overflow:hidden}}
th{{position:sticky;top:0;background:#0d6e6e;color:white;text-align:left;padding:9px;font-size:.72rem;letter-spacing:.04em;text-transform:uppercase;cursor:pointer}}
th .th-tip{{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;margin-left:5px;border-radius:50%;
  background:rgba(255,255,255,.24);color:#fff;font-size:.62rem;font-weight:900;text-transform:none;letter-spacing:0;vertical-align:middle}}
td{{vertical-align:top}}
.table-wrap{{max-height:68vh;overflow:auto;border:1px solid var(--line);border-radius:14px}}
.table-wrap table{{min-width:1680px}}
.pill{{display:inline-flex;border-radius:999px;padding:3px 9px;background:rgba(13,110,110,.09);color:#0d6e6e;font-weight:800;font-size:.75rem}}
{_merit_analytics_consent_css()}
</style>
</head>
<body>
<main>
  <section class='panel'>
    <h1>Bulk MERIT-ML Analysis</h1>
    <div class='actions'>
      <a class='btn' href='/'>Back to MERIT-ML UI</a>
      <button type='button' onclick="downloadBulk('bulk_merit_summary.tsv')">Download summary TSV</button>
      <button type='button' onclick="downloadBulk('bulk_merit_metrics_long.tsv')">Download metrics-long TSV</button>
      <button type='button' onclick="downloadBulk('bulk_merit_session.json')">Download session JSON</button>
      <button type='button' onclick="downloadBulk('bulk_merit_README.txt')">Download README</button>
      <span class='pill'>{len(summary_rows)} studies</span>
      <span class='pill'>{len(metric_rows)} metric rows</span>
    </div>
    {error_html}
    <div class='table-wrap'>
      <table id='bulk-table'>
        <thead>
          <tr>
            {_th("Priority", "priority", "num")}
            {_th("Study", "study")}
            {_th("Title", "title")}
            {_th("Organism", "organism")}
            {_th("Source", "source")}
            {_th("ML samples", "ml_samples", "num")}
            {_th("Number of unique classes", "unique_classes", "num")}
            {_th("Min class", "min_class", "num")}
            {_th("Features", "features", "num")}
            {_th("RefMet", "refmet", "num")}
            {_th("p/n", "pn", "num")}
            {_th("Missing %", "missing", "num")}
            {_th("Score", "score", "num")}
            {_th("Band", "band")}
            {_th("Gate driver", "gate")}
            {_th("Lowest metric", "lowest_metric")}
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
    <p class='sub' style='margin-top:14px'>The summary table is intentionally compact. The metrics-long TSV contains every displayed MERIT-ML metric per study/source, sorted by fail, warn, and pass status followed by score.</p>
  </section>
</main>
<script>
window.__MERIT_BULK_DOWNLOADS = {downloads_json};
function downloadBulk(name) {{
  var text = window.__MERIT_BULK_DOWNLOADS[name];
  if (typeof text !== 'string') return;
  var mime = name.endsWith('.json') ? 'application/json' : (name.endsWith('.txt') ? 'text/plain' : 'text/tab-separated-values');
  var blob = new Blob([text], {{type: mime}});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function(){{ URL.revokeObjectURL(a.href); }}, 300);
}}
(function(){{
  var table = document.getElementById('bulk-table');
  if (!table) return;
  table.querySelectorAll('th').forEach(function(th, idx){{
    th.addEventListener('click', function(){{
      var numeric = th.getAttribute('data-sort') === 'num';
      var rows = Array.prototype.slice.call(table.tBodies[0].rows);
      var dir = th.getAttribute('data-dir') === 'asc' ? -1 : 1;
      table.querySelectorAll('th').forEach(function(h){{ h.removeAttribute('data-dir'); }});
      th.setAttribute('data-dir', dir === 1 ? 'asc' : 'desc');
      rows.sort(function(a,b){{
        var av = a.cells[idx] ? (a.cells[idx].getAttribute('data-sort-value') || a.cells[idx].innerText.trim()) : '';
        var bv = b.cells[idx] ? (b.cells[idx].getAttribute('data-sort-value') || b.cells[idx].innerText.trim()) : '';
        if (numeric) {{
          var an = parseFloat(av); var bn = parseFloat(bv);
          if (isNaN(an)) an = 1e99;
          if (isNaN(bn)) bn = 1e99;
          return (an - bn) * dir;
        }}
        return av.localeCompare(bv) * dir;
      }});
      rows.forEach(function(r){{ table.tBodies[0].appendChild(r); }});
    }});
  }});
}})();
</script>
{_merit_analytics_consent_banner()}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------

def _v2_scoring_control(key: str, label: str, params: dict[str, float], help_text: str = "") -> str:
    lo, hi, step = _v2_param_display_bounds(key)
    value = _v2_param_display_value(key, params.get(key, _V2_DEFAULT_PARAMS[key]))
    default = _v2_param_display_value(key, _V2_DEFAULT_PARAMS[key])
    tooltip = (
        f"<button class='v2-param-help-toggle' type='button' aria-expanded='false' "
        f"aria-controls='v2-help-{_e(key)}'>"
        f"<span class='v2-param-help-icon'>i</span><span class='v2-param-help-word'>Help</span>"
        f"</button>"
        if help_text else ""
    )
    help_panel = (
        f"<div class='v2-param-help-text' id='v2-help-{_e(key)}'>{_e(help_text)}</div>"
        if help_text else ""
    )
    return (
        "<div class='v2-param'>"
        f"<div class='v2-param-head'><label for='v2-{_e(key)}'>{_e(label)}</label>{tooltip}"
        f"<span>default {_e(_v2_param_display_text(key, _V2_DEFAULT_PARAMS[key]))}</span>{help_panel}</div>"
        f"<div class='v2-param-inputs'>"
        f"<input class='v2-slider' id='v2-{_e(key)}' name='{_e(key)}' form='run-form' "
        f"type='range' min='{lo:g}' max='{hi:g}' step='{step:g}' value='{value:g}' data-v2-param='{_e(key)}'>"
        f"<input class='v2-number' name='{_e(key)}' form='run-form' type='number' "
        f"min='{lo:g}' max='{hi:g}' step='{step:g}' value='{value:g}' data-v2-param='{_e(key)}'>"
        f"</div>"
        "</div>"
    )


def _v2_scoring_controls_html(params: dict[str, float]) -> str:
    groups: list[tuple[str, str, list[tuple[str, str, str]]]] = [
        (
            "Band labels",
            "Diplomatic display names; internal cached labels are not edited.",
            [
                ("band_ready_min", "ML-ready cutoff", "Minimum displayed core score for the strongest band before gate ceilings; higher values make the top label harder to reach."),
                ("band_conditional_min", "Caveat cutoff", "Minimum displayed core score for ML-ready with caveats; raising it moves borderline studies into lower-use bands."),
                ("band_exploratory_min", "Exploratory cutoff", "Minimum displayed core score for exploratory ML use; below this, model training is treated as strongly class-support limited."),
            ],
        ),
        (
            "Structural and gates",
            "Controls the feasibility gates that can cap the final band.",
            [
                ("g2_sample_pass", "Preferred ML-eligible samples", "Minimum ML-eligible sample count for the sample-size gate to pass. MERIT-ML defaults are for supervised classification / feature-selection reuse; triplicate time-course or isotope-tracing designs may be valid experimentally but remain underpowered for reliable ML validation."),
                ("g2_sample_fail_below", "Sample fail-below threshold", "ML-eligible sample count below this is treated as fail for the sample-size gate. This does not judge original-study validity; it flags that very small ML-eligible sample sets cannot support reliable supervised classifier training or feature selection."),
                ("g4_class_pass", "Minimum class target", "Smallest class size required for the class-support gate and label-suitability metric to pass; protects cross-validation from under-filled classes."),
                ("g4_class_warn_min", "Class warning floor", "Smallest class size needed to avoid a fail class-support gate; below this, minority-class learning is treated as infeasible for supervised ML reuse."),
                ("g5_missing_pass_pct", "Missingness pass %", "Median sample-level missingness at or below this passes the missingness gate; lower missingness means fewer imputation-driven signals."),
                ("g5_missing_fail_pct", "Missingness fail %", "Median sample-level missingness above this is treated as fail for the missingness gate; high missingness can dominate model behavior."),
            ],
        ),
        (
            "Label structure",
            "Controls class-balance and group-support status/scoring.",
            [
                ("class_balance_pass", "Class balance pass score", "Minimum smallest/largest class ratio for pass status; stricter balance reduces majority-class dominance during training."),
                ("group_support_strong", "Strong class support", "Smallest class size that receives the full group-support score; supports stable folds and minority-class estimates."),
                ("group_support_moderate", "Moderate class support", "Smallest class size for the intermediate group-support score; indicates training is possible but validation is less stable."),
                ("group_support_weak", "Weak class support", "Smallest class size for the weak group-support score; below this, classes are too sparse for dependable learning."),
                ("label_entropy_pass", "Entropy pass score", "Minimum normalized class-label entropy for pass status; higher entropy means samples are more evenly distributed across labels."),
            ],
        ),
        (
            "ML task readiness",
            "Controls the p/n score mapping for feature-to-sample ratio.",
            [
                ("pn_low", "p/n low-risk", "Feature-to-sample ratio at or below this gets full score; low p/n reduces overfitting pressure."),
                ("pn_moderate", "p/n moderate-risk", "Feature-to-sample ratio at or below this gets an intermediate-high score; regularization is likely needed."),
                ("pn_high", "p/n high-risk", "Feature-to-sample ratio at or below this gets a reduced score; high dimensionality makes model selection fragile."),
                ("pn_tail", "p/n tail denominator", "Controls how sharply extremely high p/n ratios are penalized; larger values make ultra-high-dimensional datasets less harshly penalized."),
            ],
        ),
        (
            "Analytical QC",
            "Controls status thresholds where cached raw distributions support safe v2 recalculation.",
            [
                ("sample_missingness_score_pass", "Missingness score pass", "Minimum sample-missingness score for pass status; higher cutoffs demand more complete samples before training."),
                ("class_missingness_gap_warn_pct", "Class gap warning %", "Marks a warning signal when missingness differs this much between classes; class-specific missingness can leak label information."),
                ("sample_outlier_score_pass", "Outlier score pass", "Minimum outlier-burden score for pass status; stricter values flag studies where unusual samples may drive the model."),
                ("correlation_score_pass", "Correlation score pass", "Minimum redundancy score for pass status; stricter values flag feature blocks that can overweight duplicated signals."),
                ("feature_missingness_burden_warn_pct", "Feature burden warning %", "Marks a warning signal when too many features exceed the high-missingness threshold; high feature dropout increases imputation burden."),
            ],
        ),
        (
            "Annotation",
            "Controls annotation/interoperability status thresholds.",
            [
                ("annotation_general_pass", "General annotation pass", "Minimum annotation/interoperability score for pass status; higher values demand clearer biological interpretation of model features."),
                ("annotation_redundancy_pass", "Redundancy pass", "Minimum feature-redundancy score for pass status; stricter values flag duplicated or repeated feature labels."),
                ("unknown_feature_max_pct", "Unknown feature max %", "Maximum allowed unknown-feature fraction; unknown features can still train models but weaken interpretation and reuse."),
            ],
        ),
    ]
    group_html = []
    for title, note, controls in groups:
        control_html = "".join(_v2_scoring_control(key, label, params, help_text) for key, label, help_text in controls)
        group_html.append(
            "<details class='v2-tune-group'>"
            f"<summary>{_e(title)}</summary>"
            f"<p class='v2-tune-note'>{_e(note)}</p>"
            f"{control_html}"
            "</details>"
        )
    profile_label = "Default MERIT-ML" if _v2_is_default_params(params) else "Custom active"
    return (
        "<div class='v2-tune'>"
        "<div class='v2-tune-title'>"
        "<span>Scoring Parameters</span>"
        f"<strong>{_e(profile_label)}</strong>"
        "</div>"
        "<p class='v2-tune-note'>Change thresholds, then apply to recalculate the same study with updated scores, gates, bands, and tooltip text.</p>"
        "<p class='v2-tune-note' style='background:rgba(13,110,110,.06);border-radius:10px;padding:7px 8px'>"
        "<strong style='color:#132327'>Default scope:</strong> MERIT-ML scores supervised classification and feature-selection readiness. "
        "Small-n designs such as triplicate time-course, cell-culture, or 13C-tracing experiments may be scientifically valid for their original aim, "
        "but they remain limited for reliable supervised ML training, validation, and feature selection.</p>"
        f"{''.join(group_html)}"
        "<div class='v2-tune-actions'>"
        "<button class='v2-reset' type='button' onclick='resetV2Params()'>Defaults</button>"
        "<button class='v2-apply' type='submit' form='run-form'>Apply</button>"
        "</div>"
        "</div>"
    )


def _page(state: dict[str, Any] | None = None, error: str | None = None, defaults: dict[str, str] | None = None) -> str:
    defaults = defaults or {}
    scoring_params = _coerce_v2_scoring_params(defaults)
    matrix_overrides = _coerce_v2_matrix_overrides(defaults)
    precomputed_root = (defaults.get("precomputed_root") or _default_precomputed_root()).strip()
    error_html = (
        f"<div style='background:linear-gradient(120deg,#8f2d2d,#bb4b4b);color:white;"
        f"padding:14px 16px;border-radius:14px;margin-bottom:18px'>{_e(error)}</div>"
    ) if error else ""
    result_html = _result_panel(
        state,
        scoring_params,
        matrix_overrides=matrix_overrides,
        precomputed_root=precomputed_root,
    )
    source = "workbench"
    leaderboard_html = _study_browser_html(precomputed_root, limit=500)
    bulk_workspace_html = _bulk_workspace_html(precomputed_root)
    scoring_controls_html = _v2_scoring_controls_html(scoring_params)
    v2_defaults_js = json.dumps({
        key: _v2_param_display_value(key, value)
        for key, value in _V2_DEFAULT_PARAMS.items()
    })
    logo_uri = _logo_asset_url()
    has_report = state is not None
    body_class = "report-tools-collapsed" if has_report else "home-mode"
    tool_toggle_html = (
        "<button id='tool-rail-toggle' class='tool-rail-toggle' type='button' aria-expanded='false'>"
        "Show tools</button>"
        "<div id='analytical-page-scroll-top' class='analytical-page-scroll analytical-page-scroll-top' "
        "aria-label='Scroll Analytical QC layout horizontally from the top'>"
        "<div class='analytical-page-scroll-spacer'></div></div>"
        "<div id='analytical-page-scroll-bottom' class='analytical-page-scroll analytical-page-scroll-bottom' "
        "aria-label='Scroll Analytical QC layout horizontally from the bottom'>"
        "<div class='analytical-page-scroll-spacer'></div></div>"
        if has_report
        else ""
    )
    about_html = (
        "<button id='merit-about-toggle' class='merit-about-toggle' type='button' "
        "aria-haspopup='dialog' aria-controls='merit-about-modal' aria-expanded='false'>About</button>"
        "<div id='merit-about-modal' class='merit-about-modal' role='dialog' aria-modal='true' "
        "aria-labelledby='merit-about-title' hidden>"
        "<div class='merit-about-backdrop' data-about-close='1'></div>"
        "<section class='merit-about-card'>"
        "<button type='button' class='merit-about-close' data-about-close='1' aria-label='Close About dialog'>&times;</button>"
        "<div class='merit-about-kicker'>About MERIT-ML</div>"
        "<h2 id='merit-about-title'>Metabolomics Evaluation of Readiness and Interoperability of Tabular Data for Machine Learning</h2>"
        "<p>MERIT-ML is a research software framework for assessing the machine-learning readiness of publicly deposited metabolomics tabular data. The current version focuses on studies available through the Metabolomics Workbench and evaluates whether deposited data matrices contain the minimum structural, label, sample-size, missingness, and annotation information needed to attempt supervised classification.</p>"
        "<p>MERIT-ML was developed as part of an academic research project at the Centre for Digital Health, Indian Institute of Technology Bombay. The work was prepared by Shayantan Banerjee under the supervision of Prof. Pramod P. Wangikar, Department of Chemical Engineering and Centre for Digital Health, IIT Bombay.</p>"
        "<p>The tool retrieves and summarizes publicly available repository-hosted data and metadata. It does not replace manual review, analytical validation, or biological interpretation of individual studies. A high MERIT-ML readiness score indicates that a deposited tabular matrix satisfies the framework&rsquo;s operational criteria for supervised-classification reuse; it does not guarantee model performance, biomarker validity, or external generalizability.</p>"
        "<p>MERIT-ML is not affiliated with, endorsed by, or maintained by the Metabolomics Workbench. Users should cite the original deposited studies and the Metabolomics Workbench records when reusing data.</p>"
        "<div class='merit-about-citation'>"
        "<strong>Preprint citation</strong>"
        "<span>Shayantan Banerjee, Pramod P. Wangikar. <em>MERIT-ML: A Machine-Learning-Readiness Framework for Tabular Public Metabolomics Data.</em> ChemRxiv. 10 June 2026.</span>"
        "<a href='https://doi.org/10.26434/chemrxiv.15004429/v2' target='_blank' rel='noopener noreferrer'>https://doi.org/10.26434/chemrxiv.15004429/v2</a>"
        "</div>"
        "</section>"
        "</div>"
    )
    if logo_uri:
        sidebar_brand_html = (
            "<div style='display:flex;align-items:flex-start;justify-content:flex-start;margin:0 0 10px'>"
            f"<img src='{_e(logo_uri)}' alt='MERIT-ML logo' "
            "loading='lazy' decoding='async' "
            "style='width:100%;max-width:285px;height:auto;display:block;object-fit:contain;object-position:left center'/>"
            "</div>"
        )
    else:
        sidebar_brand_html = "<h1>MERIT-ML</h1>"

    return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>MERIT-ML — Metabolomics Workbench Readiness Assessment</title>
{_merit_analytics_head_script()}
<script>
(function(){{
  window.loadPlotlyOnce = function() {{
    if (window.Plotly) return Promise.resolve(window.Plotly);
    if (window.__MERIT_PLOTLY_PROMISE) return window.__MERIT_PLOTLY_PROMISE;
    window.__MERIT_PLOTLY_PROMISE = new Promise(function(resolve, reject) {{
      var s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.32.0/plotly.min.js';
      s.async = true;
      s.onload = function() {{ resolve(window.Plotly); }};
      s.onerror = function() {{ reject(new Error('Plotly failed to load')); }};
      document.head.appendChild(s);
    }});
    return window.__MERIT_PLOTLY_PROMISE;
  }};
}})();
</script>
<style>
:root{{--ink:#132327;--muted:#51656a;--paper:#f5f1e8;--panel:rgba(255,255,255,.78);
  --line:rgba(19,35,39,.12);--accent:#0d6e6e;--accent2:#d27d2d;--accent3:#113e52;
  --shadow:0 24px 60px rgba(19,35,39,.10);--r:20px}}
*{{box-sizing:border-box}}
body{{margin:0;color:var(--ink);font-family:"IBM Plex Sans","Avenir Next","Segoe UI",sans-serif;
  background:radial-gradient(circle at top right,rgba(13,110,110,.18),transparent 36%),
  radial-gradient(circle at 15% 20%,rgba(210,125,45,.18),transparent 28%),
  linear-gradient(180deg,#f7f3ea 0%,#e9efe9 52%,#eaf0f1 100%)}}
.wrap{{width:min(1800px,calc(100vw - 28px));margin:18px auto 36px;
  display:grid;grid-template-columns:minmax(320px,360px) minmax(560px,1fr) minmax(320px,360px);
  grid-template-areas:"left content right";gap:22px;align-items:start}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
  box-shadow:var(--shadow);backdrop-filter:blur(14px);min-width:0}}
.sidebar,.content{{min-width:0}}
.sidebar-left{{grid-area:left}}
.content{{grid-area:content;position:relative;z-index:1}}
.sidebar-right{{grid-area:right;position:relative;z-index:0}}
.sidebar .panel{{padding:18px;position:sticky;top:16px}}
.sidebar-left .panel,.sidebar-right .panel{{min-height:calc(100vh - 54px)}}
.content .panel{{padding:26px;overflow:hidden}}
.tab-panel{{min-width:0;max-width:100%;overflow-wrap:anywhere}}
.report-overview-grid{{min-width:0;max-width:100%}}
h1{{margin:0;font-family:"Iowan Old Style",Georgia,serif;font-size:2rem;line-height:1}}
.brand-sub{{margin:8px 0 0;color:var(--muted);font-size:.95rem;line-height:1.5}}
.ribbon{{display:inline-flex;margin-top:12px;padding:5px 12px;border-radius:999px;
  background:linear-gradient(90deg,rgba(17,62,82,.1),rgba(210,125,45,.16));
  border:1px solid rgba(17,62,82,.1);font-size:.78rem;text-transform:uppercase;letter-spacing:.08em}}
.stepper{{margin:24px 0 0;padding:0;list-style:none}}
.stepper li{{position:relative;padding:0 0 18px 52px}}
.stepper li:last-child{{padding-bottom:0}}
.stepper li::before{{content:attr(data-step);position:absolute;left:0;top:0;width:34px;height:34px;
  border-radius:50%;display:grid;place-items:center;font-weight:700;color:white;font-size:.85rem;
  background:linear-gradient(180deg,var(--accent),var(--accent3))}}
.stepper li::after{{content:"";position:absolute;left:16px;top:40px;width:2px;
  height:calc(100% - 16px);background:linear-gradient(180deg,rgba(13,110,110,.3),rgba(210,125,45,.2))}}
.stepper li:last-child::after{{display:none}}
.stepper h3{{margin:0;font-size:.95rem}}
.stepper p{{margin:5px 0 0;color:var(--muted);font-size:.87rem;line-height:1.45}}
label{{display:block;font-size:.78rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;margin-bottom:6px}}
input,select{{width:100%;padding:10px 13px;border-radius:12px;border:1px solid rgba(19,35,39,.12);
  background:rgba(255,255,255,.94);font:inherit;color:inherit}}
input:focus,select:focus{{outline:2px solid rgba(13,110,110,.2);border-color:rgba(13,110,110,.4)}}
.example-studies{{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin-top:9px;color:#51656a;
  font-size:.78rem;line-height:1.35}}
.example-studies-label{{font-weight:800;letter-spacing:.02em;color:#40565b;margin-right:2px}}
.example-study-chip{{display:inline-flex;align-items:center;gap:5px;border:1px solid rgba(13,110,110,.22);
  border-radius:999px;background:rgba(13,110,110,.07);color:#0d6e6e;padding:5px 9px;font:inherit;
  font-size:.76rem;font-weight:800;line-height:1;cursor:pointer;transition:transform .14s ease,background .14s ease,
  box-shadow .14s ease,border-color .14s ease}}
.example-study-chip small{{font-size:.66rem;font-weight:700;color:#607379;letter-spacing:0}}
.example-study-chip:hover,.example-study-chip:focus{{background:rgba(13,110,110,.13);border-color:rgba(13,110,110,.36);
  box-shadow:0 5px 14px rgba(13,110,110,.13);outline:none;transform:translateY(-1px)}}
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.form-grid .full{{grid-column:1/-1}}
.card{{padding:16px;border:1px solid var(--line);border-radius:16px;background:rgba(255,255,255,.72)}}
.card h3{{margin:0 0 6px;font-size:.95rem}}
.card p{{margin:0 0 12px;color:var(--muted);font-size:.88rem;line-height:1.5}}
.ig{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}}
.toggle-row{{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}}
.toggle{{flex:1 1 200px;display:flex;align-items:center;gap:10px;padding:10px 12px;
  border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.86)}}
.toggle input{{width:auto;margin:0;accent-color:var(--accent)}}
.v2-tune{{margin-top:18px;padding:14px;border-radius:18px;background:rgba(255,255,255,.64);
  border:1px solid rgba(19,35,39,.12)}}
.v2-tune-title{{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;
  font-size:.76rem;text-transform:uppercase;letter-spacing:.07em;font-weight:800;color:#132327}}
.v2-tune-title strong{{font-size:.66rem;padding:3px 8px;border-radius:999px;background:rgba(13,110,110,.1);
  color:#0d6e6e;border:1px solid rgba(13,110,110,.2);white-space:nowrap}}
.v2-tune-note{{margin:0 0 10px;color:#51656a;font-size:.76rem;line-height:1.45}}
.v2-tune-group{{border-top:1px solid rgba(19,35,39,.09);padding:8px 0}}
.v2-tune-group summary{{cursor:pointer;font-weight:800;font-size:.8rem;color:#113e52;letter-spacing:.02em}}
.v2-param{{padding:7px 0;border-top:1px dashed rgba(19,35,39,.08)}}
.v2-param-head{{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:5px 7px;margin-bottom:5px}}
.v2-param-head label{{margin:0;font-size:.67rem;letter-spacing:.04em;line-height:1.2}}
.v2-param-head>span{{font-size:.64rem;color:#7b8b90;white-space:nowrap}}
.v2-param-help-toggle{{display:inline-flex;align-items:center;gap:4px;border:1px solid rgba(13,110,110,.18);
  border-radius:999px;background:rgba(13,110,110,.08);color:#0d6e6e;padding:1px 6px;font:inherit;
  font-size:.62rem;font-weight:800;line-height:1.1;cursor:pointer}}
.v2-param-help-toggle:hover,.v2-param-help-toggle:focus{{background:rgba(13,110,110,.14);outline:none;
  box-shadow:0 0 0 2px rgba(13,110,110,.12)}}
.v2-param-help-icon{{display:inline-flex;align-items:center;justify-content:center;width:12px;height:12px;
  border-radius:50%;background:rgba(13,110,110,.15);color:#0d6e6e;font-size:.62rem;font-weight:800;line-height:1;
  flex-shrink:0}}
.v2-param-help-word{{font-size:.62rem;text-transform:uppercase;letter-spacing:.04em}}
.v2-param-help-text{{display:none;grid-column:1/-1;margin:4px 0 0;padding:7px 8px;border-radius:9px;
  background:rgba(13,110,110,.07);border:1px solid rgba(13,110,110,.14);color:#51656a;
  font-size:.68rem;line-height:1.35;text-transform:none;letter-spacing:0;font-weight:500;white-space:normal}}
.v2-param-head.is-help-open .v2-param-help-text{{display:block}}
.v2-param-inputs{{display:grid;grid-template-columns:minmax(0,1fr) 68px;gap:8px;align-items:center}}
.v2-param input[type=range]{{padding:0;accent-color:#0d6e6e}}
.v2-param input[type=number]{{padding:6px 7px;border-radius:9px;font-size:.76rem;font-variant-numeric:tabular-nums}}
.v2-tune-actions{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}}
.v2-reset,.v2-apply{{border-radius:12px;padding:8px 10px;font:inherit;font-weight:800;font-size:.78rem;cursor:pointer}}
.v2-reset{{border:1px solid rgba(19,35,39,.14);background:rgba(255,255,255,.86);color:#51656a}}
.v2-apply{{border:1px solid rgba(13,110,110,.24);background:#0d6e6e;color:white}}
.actions{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:20px}}
.btn{{border:0;border-radius:16px;padding:13px 20px;font:inherit;font-weight:700;color:white;cursor:pointer;
  background:linear-gradient(135deg,var(--accent3),var(--accent),var(--accent2));
  box-shadow:0 16px 24px rgba(17,62,82,.18)}}
.study-link-chip{{display:inline-flex;flex-direction:column;gap:3px;padding:9px 12px;border-radius:12px;
  border:1px solid rgba(13,110,110,.38);background:rgba(13,110,110,.08);color:#0d6e6e;
  font-size:.84rem;font-weight:700;text-decoration:none;line-height:1.25;transition:all .14s ease}}
.study-link-chip:hover{{background:rgba(13,110,110,.14);border-color:rgba(13,110,110,.55);
  color:#0a5b5b;transform:translateY(-1px)}}
.study-link-chip::after{{content:"↗";font-size:.86rem;font-weight:800;align-self:flex-start}}
.study-link-chip__meta{{font-size:.73rem;font-weight:500;color:#51656a}}
.caption{{color:var(--muted);font-size:.87rem;line-height:1.5;max-width:56ch}}
.tab-btn{{padding:8px 16px;border:1px solid rgba(19,35,39,.12);border-radius:999px;
  background:rgba(255,255,255,.7);font:inherit;font-size:.83rem;cursor:pointer;
  transition:background .15s,color .15s;color:var(--muted)}}
.tab-btn:hover{{background:rgba(13,110,110,.08);color:var(--accent)}}
.tab-btn.active{{background:var(--accent);color:white;border-color:var(--accent)}}
footer{{margin-top:18px;color:var(--muted);font-size:.82rem}}
@media(max-width:1600px){{
  .wrap{{grid-template-columns:minmax(300px,340px) minmax(0,1fr);grid-template-areas:"left content" "right content";gap:16px}}
  .sidebar .panel{{position:static;min-height:0}}
  .report-overview-grid{{grid-template-columns:1fr!important}}
}}
@media(max-width:1100px){{.wrap{{grid-template-columns:1fr;grid-template-areas:"content" "left" "right"}}.sidebar .panel{{position:static}}}}
@media(max-width:760px){{.form-grid,.ig{{grid-template-columns:1fr}}.actions{{flex-direction:column}}}}
@media print{{
  .sidebar,form,.actions,.caption,footer,#tab-bar,.btn{{display:none!important}}
  body{{background:white!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .wrap{{display:block!important;width:100%!important;margin:0!important;padding:0!important}}
  .content{{width:100%!important}}
  .panel{{box-shadow:none!important;border:none!important;backdrop-filter:none!important;
    background:white!important;padding:0!important}}
  .tab-panel{{display:block!important}}
  .tab-panel+.tab-panel{{page-break-before:always;padding-top:18px}}
  section{{page-break-inside:avoid}}
  table{{page-break-inside:auto}}
  tr{{page-break-inside:avoid}}
  .print-section-title{{display:block!important;font-size:1.1rem;font-weight:700;
    text-transform:uppercase;letter-spacing:.08em;color:#0d6e6e;margin:22px 0 10px;
    padding-bottom:6px;border-bottom:2px solid #0d6e6e}}
  #radar-chart,#class-pie-chart{{page-break-inside:avoid}}
  a{{color:inherit;text-decoration:none}}
  code{{background:#f0f0f0!important;border-radius:4px}}
  .minfo-popup{{display:block!important;position:static!important;box-shadow:none!important;border:none!important;padding:0!important}}
  .minfo-icon{{display:none!important}}
}}
.minfo{{position:relative;display:inline-block;cursor:help;vertical-align:middle}}
.minfo-icon{{display:inline-flex;align-items:center;justify-content:center;
  width:15px;height:15px;border-radius:50%;background:rgba(13,110,110,.15);
  color:#0d6e6e;font-size:.68rem;font-weight:800;margin-left:4px;flex-shrink:0;font-style:normal;line-height:1}}
.minfo-popup{{display:none;position:absolute;z-index:300;top:calc(100% + 5px);left:0;
  min-width:280px;max-width:min(72vw,560px);background:#fff;border:1px solid rgba(19,35,39,.14);
  border-radius:12px;box-shadow:0 8px 28px rgba(19,35,39,.16);padding:11px 13px;
  font-size:.81rem;line-height:1.5;white-space:normal;font-weight:400;color:#132327;
  text-transform:none;letter-spacing:0;pointer-events:auto;max-height:min(65vh,460px);overflow-y:auto;
  overscroll-behavior:contain;scrollbar-gutter:stable;-webkit-overflow-scrolling:touch}}
.minfo-popup.up{{top:auto;bottom:calc(100% + 5px)}}
.minfo-popup.align-right{{left:auto;right:0}}
.minfo:hover .minfo-popup,.minfo:focus-within .minfo-popup{{display:block}}
.content .minfo-popup{{max-width:min(420px,calc(100vw - 64px))}}
.minfo-popup.fair-metadata-popup{{min-width:min(620px,calc(100vw - 64px));max-width:min(760px,calc(100vw - 64px))}}
.content .minfo-popup.fair-metadata-popup{{max-width:min(760px,calc(100vw - 64px))}}
.fair-check-grid{{display:grid;gap:5px;font-size:.79rem;line-height:1.35}}
.fair-check-row{{display:grid;grid-template-columns:18px minmax(132px,210px) minmax(180px,1fr);gap:7px;align-items:start}}
.fair-check-label{{color:#132327;font-weight:700;overflow-wrap:normal}}
.fair-check-value{{color:#132327;min-width:0;overflow-wrap:anywhere;word-break:normal}}
@media(max-width:760px){{.fair-check-row{{grid-template-columns:18px 1fr}}.fair-check-value{{grid-column:2 / -1}}}}
.minfo-popup.feature-sample-popup{{min-width:min(620px,calc(100vw - 64px));max-width:min(820px,calc(100vw - 64px))}}
.content .minfo-popup.feature-sample-popup{{max-width:min(820px,calc(100vw - 64px))}}
.feature-sample-popup code{{white-space:normal;overflow-wrap:anywhere;word-break:normal;line-height:1.35}}
.fsr-analysis-row{{display:grid;grid-template-columns:minmax(82px,120px) minmax(190px,1fr) minmax(56px,auto);
  gap:8px;align-items:start;padding:3px 0;border-bottom:1px solid rgba(19,35,39,.06)}}
.fsr-analysis-id{{color:#132327;font-weight:800;overflow-wrap:anywhere}}
.fsr-analysis-detail{{color:#51656a;font-size:.76rem;overflow-wrap:anywhere}}
.fsr-analysis-ratio{{font-weight:800;color:#113e52;text-align:right;font-variant-numeric:tabular-nums}}
@media(max-width:760px){{.fsr-analysis-row{{grid-template-columns:1fr auto}}.fsr-analysis-detail{{grid-column:1 / -1}}}}
.tool-rail-toggle{{display:none;position:fixed;left:18px;bottom:20px;z-index:1200;border:1px solid rgba(13,110,110,.28);
  border-radius:999px;background:linear-gradient(135deg,#113e52,#0d6e6e);color:white;
  box-shadow:0 12px 30px rgba(17,62,82,.24);padding:10px 14px;font:inherit;font-size:.82rem;
  font-weight:900;cursor:pointer}}
.merit-about-toggle{{position:fixed;right:18px;top:18px;z-index:1450;border:1px solid rgba(13,110,110,.28);
  border-radius:999px;background:rgba(255,255,255,.88);backdrop-filter:blur(10px);color:#0d6e6e;
  box-shadow:0 10px 28px rgba(17,62,82,.14);padding:8px 13px;font:inherit;font-size:.8rem;
  font-weight:900;cursor:pointer;letter-spacing:.02em}}
.merit-about-toggle:hover,.merit-about-toggle:focus-visible{{outline:none;background:#0d6e6e;color:white;
  transform:translateY(-1px);box-shadow:0 14px 32px rgba(13,110,110,.22)}}
.merit-about-modal[hidden]{{display:none!important}}
.merit-about-backdrop{{position:fixed;inset:0;background:rgba(19,35,39,.48);z-index:1490;
  backdrop-filter:blur(3px)}}
.merit-about-card{{position:fixed;right:18px;top:58px;z-index:1500;width:min(720px,calc(100vw - 36px));
  max-height:min(82vh,760px);overflow:auto;background:#fff;border:1px solid rgba(19,35,39,.14);
  border-radius:24px;box-shadow:0 28px 90px rgba(19,35,39,.28);padding:24px 26px 22px;color:#132327}}
.merit-about-close{{position:absolute;right:14px;top:12px;border:1px solid rgba(19,35,39,.12);border-radius:999px;
  width:32px;height:32px;background:rgba(245,241,232,.86);color:#51656a;font:inherit;font-size:1.25rem;
  line-height:1;cursor:pointer}}
.merit-about-close:hover,.merit-about-close:focus-visible{{outline:none;background:#0d6e6e;color:white}}
.merit-about-kicker{{font-size:.73rem;text-transform:uppercase;letter-spacing:.12em;font-weight:900;color:#0d6e6e;margin:0 40px 7px 0}}
.merit-about-card h2{{font-family:"Iowan Old Style",Georgia,serif;font-size:1.52rem;line-height:1.12;margin:0 40px 15px 0;color:#132327}}
.merit-about-card p{{margin:0 0 12px;color:#40565b;line-height:1.64;font-size:.92rem}}
.merit-about-citation{{margin-top:16px;padding:14px;border-radius:16px;background:rgba(13,110,110,.07);
  border:1px solid rgba(13,110,110,.18);display:flex;flex-direction:column;gap:6px;color:#2e474d;font-size:.88rem;line-height:1.5}}
.merit-about-citation strong{{color:#0d6e6e;text-transform:uppercase;letter-spacing:.07em;font-size:.72rem}}
.merit-about-citation a{{color:#0d6e6e;font-weight:800;overflow-wrap:anywhere}}
@media(max-width:760px){{.merit-about-toggle{{right:12px;top:12px}}.merit-about-card{{right:10px;left:10px;top:52px;width:auto;padding:20px}}}}
body.report-tools-collapsed .tool-rail-toggle,
body.report-tools-expanded .tool-rail-toggle{{display:inline-flex;align-items:center;gap:6px}}
body.report-tools-collapsed .tool-rail-toggle{{animation:meritToolNudge 2.7s ease-in-out infinite}}
body.report-tools-collapsed .tool-rail-toggle:hover,
body.report-tools-collapsed .tool-rail-toggle:focus-visible{{animation:none;transform:translateY(-2px) scale(1.03)}}
@keyframes meritToolNudge{{
  0%,68%,100%{{transform:translateY(0) scale(1);box-shadow:0 12px 30px rgba(17,62,82,.24)}}
  74%{{transform:translateY(-4px) scale(1.04) rotate(-1deg);box-shadow:0 18px 34px rgba(13,110,110,.34)}}
  80%{{transform:translateY(1px) scale(1.01) rotate(1deg)}}
  86%{{transform:translateY(-3px) scale(1.025) rotate(-.6deg)}}
  92%{{transform:translateY(0) scale(1) rotate(0deg)}}
}}
body.report-tools-collapsed .wrap{{width:min(1320px,calc(100vw - 28px));
  grid-template-columns:minmax(0,1fr);grid-template-areas:"content"}}
body.report-tools-collapsed .sidebar{{display:none}}
body.report-tools-collapsed .content .panel{{overflow:visible}}
body.report-tools-expanded .tool-rail-toggle{{background:linear-gradient(135deg,#8f4f0a,#d27d2d)}}
.analytical-scroll-shell{{width:100%;max-width:100%;min-width:0;overflow:hidden}}
.analytical-scroll-body{{width:100%;max-width:100%;min-width:0;overflow-x:auto;overflow-y:visible;padding-bottom:4px}}
.analytical-scroll-control{{display:none;width:100%;max-width:100%;height:22px;overflow-x:auto;overflow-y:hidden;
  border:1px solid rgba(13,110,110,.18);border-radius:999px;background:rgba(13,110,110,.06);
  scrollbar-gutter:stable;overscroll-behavior-x:contain}}
.analytical-scroll-control::-webkit-scrollbar{{height:12px}}
.analytical-scroll-control::-webkit-scrollbar-track{{background:rgba(13,110,110,.10);border-radius:999px}}
.analytical-scroll-control::-webkit-scrollbar-thumb{{background:#0d6e6e;border-radius:999px;border:2px solid #eaf4f4}}
.analytical-scroll-top{{margin:0 0 10px;position:sticky;top:8px;z-index:80;box-shadow:0 8px 20px rgba(17,62,82,.10)}}
.analytical-scroll-bottom{{margin:10px 0 0}}
.analytical-scroll-spacer{{height:8px;min-width:100%}}
body.report-tools-expanded.report-analytical-active
  .analytical-scroll-shell.has-horizontal-overflow .analytical-scroll-control{{display:block}}
.analytical-page-scroll{{display:none;position:fixed;left:12px;right:12px;height:20px;overflow-x:auto;overflow-y:hidden;
  z-index:1180;border:1px solid rgba(13,110,110,.28);border-radius:999px;background:rgba(255,255,255,.92);
  box-shadow:0 8px 24px rgba(17,62,82,.16);scrollbar-gutter:stable;overscroll-behavior-x:contain}}
.analytical-page-scroll-top{{top:8px}}
.analytical-page-scroll-bottom{{bottom:8px}}
.analytical-page-scroll-spacer{{height:8px;min-width:100%}}
.analytical-page-scroll::-webkit-scrollbar{{height:12px}}
.analytical-page-scroll::-webkit-scrollbar-track{{background:rgba(13,110,110,.10);border-radius:999px}}
.analytical-page-scroll::-webkit-scrollbar-thumb{{background:#0d6e6e;border-radius:999px;border:2px solid #eef7f7}}
body.report-tools-expanded.report-analytical-active .analytical-page-scroll.has-horizontal-overflow{{display:block}}
body.report-tools-expanded.report-analytical-active .tool-rail-toggle{{bottom:38px}}
@media(max-width:1100px){{.tool-rail-toggle{{left:14px;bottom:14px}}}}
@media(prefers-reduced-motion:reduce){{.tool-rail-toggle{{animation:none!important;transition:none!important}}}}
{_merit_analytics_consent_css()}
</style>
</head>
<body class='{body_class}'>
{about_html}
{tool_toggle_html}
<main class='wrap'>
  <aside class='sidebar sidebar-left'>
    <section class='panel'>
      {sidebar_brand_html}
      <p class='brand-sub'>Machine Learning Readiness for Tabular Metabolomics Data focused on Metabolomics Workbench.</p>
      <div class='ribbon'>MERIT-ML web app</div>
      {scoring_controls_html}
      {bulk_workspace_html}
    </section>
  </aside>
  <section class='content'>
    <section class='panel'>
      <h2 style='margin:0 0 4px;font-family:"Iowan Old Style",Georgia,serif;font-size:clamp(1.6rem,2.5vw,2.6rem);line-height:1'>Run a MERIT-ML Assessment</h2>
      <p style='margin:0 0 22px;color:var(--muted);max-width:66ch;line-height:1.55'>Evaluate the machine-learning readiness of tabular metabolomics datasets from Metabolomics Workbench.</p>
      {error_html}
      <form id='run-form' method='post' action='/workflow/run'>
        <section class='card'>
          <h3>Repository &amp; Accession</h3>
          <p>Metabolomics Workbench is active. Paste a study accession ID.</p>
          <input type='hidden' name='source' value='workbench'>
          <input type='hidden' name='profile' value='full'>
          <label>Source</label>
          <input value='Metabolomics Workbench' disabled>
          <label style='margin-top:10px'>Accession ID</label>
          <input id='study-id-input' name='study_id' value='{_e(defaults.get("study_id",""))}' placeholder='e.g. ST000356' list='study-id-suggestions' autocomplete='off' required>
          <datalist id='study-id-suggestions'></datalist>
          <div class='example-studies' aria-label='Representative study examples'>
            <span class='example-studies-label'>Try examples:</span>
            <button type='button' class='example-study-chip' data-study-id='ST000043' title='Small ML-ready demo study'>ST000043 <small>ML-ready</small></button>
            <button type='button' class='example-study-chip' data-study-id='ST000496' title='Study with rich annotation summary'>ST000496 <small>annotation</small></button>
            <button type='button' class='example-study-chip' data-study-id='ST001518' title='Small labelled study example'>ST001518 <small>small n</small></button>
            <button type='button' class='example-study-chip' data-study-id='ST003741' title='Limited-readiness example with source differences'>ST003741 <small>limited</small></button>
          </div>
        </section>
        <div class='actions'>
          <p class='caption'>MERIT-ML pipeline: <strong>acquire → normalize → assess → Readiness Score</strong>. Results are written to the run output directory and shown below.</p>
          <button class='btn' type='submit'>Run Assessment</button>
        </div>
      </form>
      {result_html}
    </section>
  </section>
  <aside class='sidebar sidebar-right'>
    <section class='panel'>
      {leaderboard_html}
    </section>
  </aside>
</main>
<footer style='display:flex;flex-direction:column;align-items:center;gap:9px;padding:8px 12px 18px'>
  <div style='display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border-radius:999px;
    border:1px solid rgba(19,35,39,.14);background:rgba(255,255,255,.72);color:#4e6268;font-size:.81rem'>
    <span>&copy;</span>
    <a href='https://www.wangikarlab-iitb.com/' target='_blank' rel='noopener noreferrer'
      style='color:#0d6e6e;text-decoration:none;font-weight:700'>
      Biosystems Engineering Laboratory
    </a>
    <span style='opacity:.7'>&middot;</span>
    <span style='font-weight:600'>IIT Bombay</span>
  </div>
  <div style='width:min(980px,calc(100vw - 28px));padding:10px 13px;border-radius:14px;
    border:1px solid rgba(19,35,39,.10);background:rgba(255,255,255,.62);color:#51656a;
    font-size:.78rem;line-height:1.5;text-align:center'>
    {_e(_INDEPENDENCE_NOTE_TEXT)}
  </div>
</footer>
<script>
function printReport() {{
  function doPrint() {{
    // Ensure Plotly charts are rendered before printing when Plotly is available.
    if (window.Plotly) {{
      document.querySelectorAll('[id^="radar-chart"]').forEach(function(el) {{
        var sfx = el.id.replace('radar-chart_', '');
        var fn = window['renderRadar_' + sfx];
        try {{ if (typeof fn === 'function') fn(); }} catch(e) {{}}
      }});
    }}
    document.querySelectorAll('[id^="class-pie-chart-"]').forEach(function(el) {{
      var sfx = el.id.replace('class-pie-chart-', '');
      var fn = window['renderClassPie_' + sfx];
      try {{ if (typeof fn === 'function') fn(); }} catch(e) {{}}
    }});
    // Expand all tab panels for print (restored after)
    var panels = document.querySelectorAll('.tab-panel');
    var prev = [];
    panels.forEach(function(p, i) {{
      prev[i] = p.style.display;
      p.style.display = 'block';
    }});
    window.print();
    // Restore original state
    panels.forEach(function(p, i) {{ p.style.display = prev[i]; }});
  }}
  if (!window.Plotly && window.loadPlotlyOnce) {{
    window.loadPlotlyOnce().then(doPrint).catch(doPrint);
  }} else {{
    doPrint();
  }}
}}

window.addEventListener('beforeprint', function() {{
  document.querySelectorAll('.tab-panel').forEach(function(p) {{ p.style.display = 'block'; }});
}});
window.addEventListener('afterprint', function() {{
  // Re-run switchTab to restore active-only state
  var activeBtn = document.querySelector('.tab-btn.active');
  if (activeBtn) {{ var tid = activeBtn.getAttribute('data-tab'); if (tid) switchTab(tid); }}
}});

// Landing-page examples: fill the accession box without auto-submitting.
(function() {{
  document.querySelectorAll('.example-study-chip').forEach(function(btn) {{
    btn.addEventListener('click', function(ev) {{
      ev.preventDefault();
      var input = document.getElementById('study-id-input');
      if (!input) return;
      input.value = btn.getAttribute('data-study-id') || '';
      input.focus();
      try {{
        input.dispatchEvent(new Event('input', {{bubbles:true}}));
        input.dispatchEvent(new Event('change', {{bubbles:true}}));
      }} catch(e) {{}}
    }});
  }});
}})();

// Persistent About dialog.
(function() {{
  var btn = document.getElementById('merit-about-toggle');
  var modal = document.getElementById('merit-about-modal');
  if (!btn || !modal) return;
  function setOpen(open) {{
    if (open) {{
      modal.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      var closeBtn = modal.querySelector('.merit-about-close');
      if (closeBtn) setTimeout(function() {{ closeBtn.focus(); }}, 0);
    }} else {{
      modal.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      btn.focus();
    }}
  }}
  btn.addEventListener('click', function(ev) {{
    ev.preventDefault();
    setOpen(modal.hidden);
  }});
  modal.addEventListener('click', function(ev) {{
    var target = ev.target;
    if (target && target.getAttribute && target.getAttribute('data-about-close') === '1') {{
      ev.preventDefault();
      setOpen(false);
    }}
  }});
  document.addEventListener('keydown', function(ev) {{
    if (ev.key === 'Escape' && !modal.hidden) setOpen(false);
  }});
}})();

// Report focus mode: hide utility rails while reading metric tabs.
(function() {{
  var btn = document.getElementById('tool-rail-toggle');
  if (!btn) return;
	  function setToolsExpanded(expanded) {{
	    document.body.classList.toggle('report-tools-expanded', !!expanded);
	    document.body.classList.toggle('report-tools-collapsed', !expanded);
	    btn.textContent = expanded ? 'Hide tools' : 'Show tools';
	    btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
	    if (window.refreshAnalyticalScrollbars) requestAnimationFrame(window.refreshAnalyticalScrollbars);
	    if (expanded) {{
	      try {{ window.dispatchEvent(new CustomEvent('meritToolsShown')); }} catch(e) {{}}
	    }}
	  }}
  setToolsExpanded(false);
  btn.addEventListener('click', function(ev) {{
    ev.preventDefault();
    setToolsExpanded(!document.body.classList.contains('report-tools-expanded'));
  }});
  document.addEventListener('click', function(ev) {{
    var target = ev.target && ev.target.closest ? ev.target.closest('.tab-btn') : null;
    if (target) setToolsExpanded(false);
  }});
}})();

// Analytical QC can contain very wide per-analysis tables. When the side tools
// are open, expose synchronized top/bottom horizontal scrollbars for that tab.
(function() {{
  function getPageControls() {{
    return Array.prototype.slice.call(document.querySelectorAll('.analytical-page-scroll'));
  }}
  function getActiveAnalyticalShell() {{
    var shells = Array.prototype.slice.call(document.querySelectorAll('[data-analytical-scroll]'));
    return shells.find(function(shell) {{
      var panel = shell.closest ? shell.closest('.tab-panel') : null;
      return panel && getComputedStyle(panel).display !== 'none';
    }}) || null;
  }}
  function constrainShellToVisibleReport(shell) {{
    if (!shell) return;
    var panel = shell.closest ? shell.closest('.tab-panel') : null;
    if (!panel || getComputedStyle(panel).display === 'none') return;
    if (!(document.body.classList.contains('report-tools-expanded') &&
          document.body.classList.contains('report-analytical-active'))) {{
      shell.style.width = '';
      return;
    }}
    var shellRect = shell.getBoundingClientRect();
    var rightLimit = (window.innerWidth || document.documentElement.clientWidth || 0) - 24;
    var rightRail = document.querySelector('.sidebar-right');
    if (rightRail && getComputedStyle(rightRail).display !== 'none') {{
      var railRect = rightRail.getBoundingClientRect();
      if (railRect.left > shellRect.left && railRect.left < rightLimit) {{
        rightLimit = railRect.left - 18;
      }}
    }}
    var width = Math.max(320, Math.floor(rightLimit - shellRect.left));
    shell.style.width = width + 'px';
  }}
  function refreshPageControls() {{
    var controls = getPageControls();
    if (!controls.length) return;
    var activeShell = getActiveAnalyticalShell();
    constrainShellToVisibleReport(activeShell);
    var activeBody = activeShell ? activeShell.querySelector('.analytical-scroll-body') : null;
    var scroller = document.scrollingElement || document.documentElement;
    var maxWidth = Math.max(
      scroller ? scroller.scrollWidth : 0,
      document.body ? document.body.scrollWidth : 0,
      activeBody ? activeBody.scrollWidth : 0,
      window.innerWidth || 0
    );
    var hasOverflow = maxWidth > ((window.innerWidth || 0) + 3);
    controls.forEach(function(control) {{
      control.classList.toggle('has-horizontal-overflow', hasOverflow);
      var spacer = control.querySelector('.analytical-page-scroll-spacer');
      if (spacer) spacer.style.width = maxWidth + 'px';
    }});
  }}
  function getControls(shell) {{
    return Array.prototype.slice.call(shell.querySelectorAll('.analytical-scroll-control'));
  }}
  function getWideTargets(shell) {{
    var body = shell.querySelector('.analytical-scroll-body');
    if (!body) return [];
    var nodes = [body].concat(Array.prototype.slice.call(body.querySelectorAll('div')));
    return nodes.filter(function(el) {{
      return el && el.scrollWidth > el.clientWidth + 3;
    }});
  }}
  window.refreshAnalyticalScrollbars = function() {{
    refreshPageControls();
    document.querySelectorAll('[data-analytical-scroll]').forEach(function(shell) {{
      constrainShellToVisibleReport(shell);
      var targets = getWideTargets(shell);
      var controls = getControls(shell);
      var maxWidth = shell.clientWidth || 0;
      targets.forEach(function(el) {{ maxWidth = Math.max(maxWidth, el.scrollWidth || 0); }});
      shell.classList.toggle('has-horizontal-overflow', maxWidth > (shell.clientWidth + 3));
      controls.forEach(function(control) {{
        var spacer = control.querySelector('.analytical-scroll-spacer');
        if (spacer) spacer.style.width = Math.max(maxWidth, control.clientWidth || 0) + 'px';
      }});
      if (!shell._meritScrollBound) {{
        shell._meritScrollBound = true;
        shell.addEventListener('scroll', function(ev) {{
          var src = ev.target;
          if (!src || src === shell) return;
          var srcIsControl = src.classList && src.classList.contains('analytical-scroll-control');
          var srcIsWide = srcIsControl || (src.scrollWidth > src.clientWidth + 3);
          if (!srcIsWide || shell._meritScrollSync) return;
          shell._meritScrollSync = true;
          var left = src.scrollLeft || 0;
          getControls(shell).concat(getWideTargets(shell)).forEach(function(el) {{
            if (el !== src) el.scrollLeft = left;
          }});
          requestAnimationFrame(function() {{ shell._meritScrollSync = false; }});
        }}, true);
      }}
    }});
  }};
  if (!window._meritAnalyticalPageScrollBound) {{
    window._meritAnalyticalPageScrollBound = true;
    document.addEventListener('scroll', function(ev) {{
      var src = ev.target;
      if (!src || !(src.classList && src.classList.contains('analytical-page-scroll'))) return;
      if (window._meritAnalyticalPageScrollSync) return;
      window._meritAnalyticalPageScrollSync = true;
      var scroller = document.scrollingElement || document.documentElement;
      if (scroller) scroller.scrollLeft = src.scrollLeft || 0;
      var activeShell = getActiveAnalyticalShell();
      if (activeShell) {{
        getWideTargets(activeShell).forEach(function(el) {{ el.scrollLeft = src.scrollLeft || 0; }});
      }}
      getPageControls().forEach(function(control) {{
        if (control !== src) control.scrollLeft = src.scrollLeft || 0;
      }});
      requestAnimationFrame(function() {{ window._meritAnalyticalPageScrollSync = false; }});
    }}, true);
    window.addEventListener('scroll', function() {{
      if (window._meritAnalyticalPageScrollSync) return;
      var scroller = document.scrollingElement || document.documentElement;
      var left = scroller ? (scroller.scrollLeft || 0) : 0;
      getPageControls().forEach(function(control) {{ control.scrollLeft = left; }});
    }});
  }}
  window.addEventListener('resize', function() {{
    clearTimeout(window._meritAnalyticalScrollTimer);
    window._meritAnalyticalScrollTimer = setTimeout(window.refreshAnalyticalScrollbars, 120);
  }});
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', window.refreshAnalyticalScrollbars);
  }} else {{
    requestAnimationFrame(window.refreshAnalyticalScrollbars);
  }}
}})();

// Scoring-parameter controls
(function() {{
  var defaults = {v2_defaults_js};
  function pairInputs(key) {{
    return Array.prototype.slice.call(document.querySelectorAll('[data-v2-param="' + key + '"]'));
  }}
	  Object.keys(defaults).forEach(function(key) {{
	    pairInputs(key).forEach(function(el) {{
	      el.addEventListener('input', function() {{
	        pairInputs(key).forEach(function(peer) {{
	          if (peer !== el) peer.value = el.value;
	        }});
	      }});
	    }});
	  }});
	  window.currentV2ScoringParams = function() {{
	    var out = {{}};
	    document.querySelectorAll('.v2-number[data-v2-param]').forEach(function(el) {{
	      var key = el.getAttribute('data-v2-param') || '';
	      if (key) out[key] = el.value;
	    }});
	    return out;
	  }};
	  window.resetV2Params = function() {{
	    Object.keys(defaults).forEach(function(key) {{
	      pairInputs(key).forEach(function(el) {{ el.value = defaults[key]; }});
	    }});
	  }};
  document.querySelectorAll('.v2-param-help-toggle').forEach(function(btn) {{
    btn.addEventListener('click', function(ev) {{
      ev.preventDefault();
      ev.stopPropagation();
      var head = btn.closest('.v2-param-head');
      if (!head) return;
      var isOpen = head.classList.toggle('is-help-open');
      btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    }});
  }});
}})();

// Matrix-property adjustment controls
(function() {{
  function field() {{
    return document.getElementById('matrix-overrides-field');
  }}
  function cssEscape(value) {{
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value || '').replace(/["\\\\]/g, '\\\\$&');
  }}
  function rowControlsForSample(sampleId) {{
    return Array.prototype.slice.call(document.querySelectorAll('[data-sample-id="' + cssEscape(sampleId) + '"]'));
  }}
  function syncSample(sampleId, sourceEl) {{
    if (!sampleId || !sourceEl) return;
    var isLabel = sourceEl.classList.contains('matrix-label-input');
    var isElig = sourceEl.classList.contains('matrix-eligible-select');
    rowControlsForSample(sampleId).forEach(function(el) {{
      if (el === sourceEl) return;
      if (isLabel && el.classList.contains('matrix-label-input')) {{
        addOptionToSelect(el, sourceEl.value);
        el.value = sourceEl.value;
        el.setAttribute('data-current-label', sourceEl.value);
      }}
      if (isElig && el.classList.contains('matrix-eligible-select')) el.value = sourceEl.value;
    }});
  }}
  function readOverridesFromField() {{
    var f = field();
    try {{
      var parsed = JSON.parse((f && f.value) || '{{}}');
      return (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) ? parsed : {{}};
    }} catch(e) {{
      return {{}};
    }}
  }}
	  function collectOverrides() {{
	    var overrides = readOverridesFromField();
    document.querySelectorAll('.matrix-sample-row').forEach(function(row) {{
      var sid = row.getAttribute('data-sample-id') || '';
      if (!sid) return;
      var labelInput = row.querySelector('.matrix-label-input');
      var eligSelect = row.querySelector('.matrix-eligible-select');
      if (!labelInput || !eligSelect) return;
      var defaultLabel = row.getAttribute('data-default-label') || '';
      var defaultElig = row.getAttribute('data-default-eligible') === '1';
      var defaultExcluded = row.getAttribute('data-default-excluded') === '1';
      var label = String(labelInput.value || '').trim();
      var excluded = eligSelect.value === 'exclude';
      var eligible = eligSelect.value === '1';
      if (label !== defaultLabel || eligible !== defaultElig || excluded !== defaultExcluded) {{
        overrides[sid] = {{ label: label, eligible: eligible, excluded: excluded }};
      }} else if (Object.prototype.hasOwnProperty.call(overrides, sid)) {{
        delete overrides[sid];
      }}
    }});
	    var f = field();
	    if (f) f.value = JSON.stringify(overrides);
	  }}
	  window.currentMatrixOverrides = function() {{
	    collectOverrides();
	    var f = field();
	    try {{ return JSON.parse((f && f.value) || '{{}}') || {{}}; }}
	    catch (e) {{ return {{}}; }}
	  }};
  function optionExists(sel, label) {{
    return Array.prototype.some.call(sel.options, function(opt) {{ return opt.value === label; }});
  }}
  function addOptionToSelect(sel, label) {{
    if (!sel || optionExists(sel, label)) return;
    var opt = document.createElement('option');
    opt.value = label;
    opt.textContent = label;
    sel.appendChild(opt);
  }}
  function panelLabelOptions(panel) {{
    if (!panel) return [];
    try {{
      var labels = JSON.parse(panel.getAttribute('data-label-options') || '[]');
      return Array.isArray(labels) ? labels.map(function(x) {{ return String(x || '').trim(); }}) : [];
    }} catch(e) {{
      return [];
    }}
  }}
  function ensureLabelSelectOptions(sel) {{
    if (!sel || sel.dataset.optionsHydrated === '1') return;
    var panel = sel.closest ? sel.closest('.matrix-adjust-panel') : null;
    var current = String(sel.value || sel.getAttribute('data-current-label') || '').trim();
    var seen = {{}};
    var labels = panelLabelOptions(panel);
    document.querySelectorAll('.matrix-custom-class-list').forEach(function(listEl) {{
      readCustomLabels(listEl).forEach(function(label) {{ labels.push(label); }});
    }});
    if (current) labels.unshift(current);
    labels = labels.filter(function(label) {{
      label = String(label || '').trim();
      var key = label || '__empty__';
      if (seen[key]) return false;
      seen[key] = true;
      return true;
    }});
    if (!labels.length) labels = [''];
    sel.innerHTML = '';
    labels.forEach(function(label) {{
      var opt = document.createElement('option');
      opt.value = label;
      opt.textContent = label || '(empty)';
      sel.appendChild(opt);
    }});
    sel.value = current;
    sel.dataset.optionsHydrated = '1';
  }}
  function addClassLabelToSelects(label) {{
    document.querySelectorAll('select.matrix-label-input').forEach(function(sel) {{
      addOptionToSelect(sel, label);
    }});
  }}
  function readCustomLabels(listEl) {{
    try {{
      var labels = JSON.parse(listEl.getAttribute('data-custom-labels') || '[]');
      return Array.isArray(labels) ? labels.map(function(x) {{ return String(x || '').trim(); }}).filter(Boolean) : [];
    }} catch(e) {{
      return [];
    }}
  }}
  function writeCustomLabels(listEl, labels) {{
    var seen = {{}};
    var clean = labels.map(function(x) {{ return String(x || '').trim(); }}).filter(function(x) {{
      if (!x || seen[x]) return false;
      seen[x] = true;
      return true;
    }});
    listEl.setAttribute('data-custom-labels', JSON.stringify(clean));
    renderCustomLabelList(listEl);
  }}
  function escapeHtml(text) {{
    return String(text || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;');
  }}
  function renderMatrixPanelRows(panel) {{
    if (!panel || panel.dataset.rowsRendered === '1') return;
    var dataEl = panel.querySelector('.matrix-row-data');
    var body = panel.querySelector('.matrix-sample-body');
    if (!dataEl || !body) return;
    var rows = [];
    try {{
      rows = JSON.parse(dataEl.textContent || '[]');
      if (!Array.isArray(rows)) rows = [];
    }} catch(e) {{
      rows = [];
    }}
    var overrides = readOverridesFromField();
    var html = rows.map(function(row) {{
      row = row || {{}};
      var sid = String(row.sample_id || '').trim();
      var defaultLabel = String(row.default_label || '');
      var nativeLabel = String(row.native_label || defaultLabel || '');
      var override = overrides[sid] || null;
      var currentLabel = override ? String(override.label || '') : String(row.label || '');
      var defaultEligible = !!row.default_eligible;
      var eligible = override ? !!override.eligible : !!row.eligible;
      var excluded = override ? !!override.excluded : !!row.excluded;
      var status = excluded ? 'exclude' : (eligible ? '1' : '0');
      var labelText = currentLabel || '(empty)';
      return ""
        + "<tr class='matrix-sample-row' data-sample-id='" + escapeHtml(sid) + "' "
        + "data-default-label='" + escapeHtml(defaultLabel) + "' "
        + "data-native-label='" + escapeHtml(nativeLabel) + "' "
        + "data-default-eligible='" + (defaultEligible ? '1' : '0') + "' data-default-excluded='0'>"
        + "<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07);font-family:IBM Plex Mono,monospace;font-size:.78rem'>" + escapeHtml(sid) + "</td>"
        + "<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07);font-size:.76rem;color:#51656a'>" + escapeHtml(row.sources || '')
        + "<span style='display:block;color:#7b8b90;margin-top:2px'>" + escapeHtml(row.analysis_text || '') + "</span></td>"
        + "<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07);font-size:.76rem;color:#51656a;max-width:310px'>"
        + "<span style='display:block;white-space:normal;line-height:1.35'>" + escapeHtml(nativeLabel || '(not available)') + "</span>"
        + "<span style='display:block;color:#8a999d;margin-top:2px'>REST factors endpoint</span></td>"
        + "<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>"
        + "<select class='matrix-label-input' data-sample-id='" + escapeHtml(sid) + "' data-current-label='" + escapeHtml(currentLabel) + "' "
        + "style='min-width:280px;max-width:420px;padding:7px 9px;border-radius:10px'>"
        + "<option value='" + escapeHtml(currentLabel) + "' selected>" + escapeHtml(labelText) + "</option></select></td>"
        + "<td style='padding:8px;border-bottom:1px solid rgba(19,35,39,.07)'>"
        + "<select class='matrix-eligible-select' data-sample-id='" + escapeHtml(sid) + "' style='min-width:210px;padding:7px 9px;border-radius:10px'>"
        + "<option value='1' " + (status === '1' ? 'selected' : '') + ">ML-eligible</option>"
        + "<option value='0' " + (status === '0' ? 'selected' : '') + ">Not ML-eligible</option>"
        + "<option value='exclude' " + (status === 'exclude' ? 'selected' : '') + ">Exclude from current analysis</option>"
        + "</select></td></tr>";
    }}).join('');
    body.innerHTML = html;
    panel.dataset.rowsRendered = '1';
    var note = panel.querySelector('.matrix-lazy-note');
    if (note) note.style.display = 'none';
  }}
  window.renderMatrixPanelRows = renderMatrixPanelRows;
  function renderCustomLabelList(listEl) {{
    if (!listEl) return;
    var labels = readCustomLabels(listEl);
    if (!labels.length) {{
      listEl.innerHTML = "<span style='color:#7b8b90'>No custom class groups added yet.</span>";
      return;
    }}
    listEl.innerHTML = "<span style='font-weight:800;color:#51656a;margin-right:2px'>Custom class groups:</span>"
      + labels.map(function(label) {{
        return "<span class='matrix-custom-class-pill' data-label='" + escapeHtml(label) + "' "
          + "style='display:inline-flex;align-items:center;gap:5px;border:1px solid rgba(13,110,110,.16);"
          + "background:rgba(13,110,110,.07);color:#0d6e6e;border-radius:999px;padding:3px 7px'>"
          + "<span>" + escapeHtml(label) + "</span>"
          + "<button type='button' class='matrix-edit-class' data-label='" + escapeHtml(label) + "' "
          + "style='border:0;background:rgba(255,255,255,.76);color:#0d6e6e;border-radius:999px;"
          + "font:inherit;font-size:.68rem;font-weight:900;padding:1px 6px;cursor:pointer'>Edit</button>"
          + "</span>";
      }}).join('');
  }}
  function addCustomLabel(label) {{
    var val = String(label || '').trim();
    if (!val) return;
    addClassLabelToSelects(val);
    document.querySelectorAll('.matrix-custom-class-list').forEach(function(listEl) {{
      var labels = readCustomLabels(listEl);
      if (labels.indexOf(val) === -1) labels.push(val);
      writeCustomLabels(listEl, labels);
    }});
  }}
  function renameCustomLabel(oldLabel, newLabel) {{
    oldLabel = String(oldLabel || '').trim();
    newLabel = String(newLabel || '').trim();
    if (!oldLabel || !newLabel || oldLabel === newLabel) return;
    document.querySelectorAll('select.matrix-label-input').forEach(function(sel) {{
      addOptionToSelect(sel, newLabel);
      Array.prototype.slice.call(sel.options).forEach(function(opt) {{
        if (opt.value === oldLabel) {{
          opt.value = newLabel;
          opt.textContent = newLabel;
        }}
      }});
      if (sel.value === oldLabel) sel.value = newLabel;
    }});
    document.querySelectorAll('.matrix-custom-class-list').forEach(function(listEl) {{
      var labels = readCustomLabels(listEl).map(function(label) {{
        return label === oldLabel ? newLabel : label;
      }});
      writeCustomLabels(listEl, labels);
    }});
    collectOverrides();
  }}
  function addClassGroup(panel) {{
    var input = panel.querySelector('.matrix-new-class');
    if (!input) return;
    var val = String(input.value || '').trim();
    if (!val) return;
    addCustomLabel(val);
    input.value = '';
  }}
  function handleMatrixControlChange(el) {{
    if (el && el.classList && (el.classList.contains('matrix-label-input') || el.classList.contains('matrix-eligible-select'))) {{
      if (el.classList.contains('matrix-label-input')) ensureLabelSelectOptions(el);
      syncSample(el.getAttribute('data-sample-id') || '', el);
    }}
  }}
  document.addEventListener('focusin', function(ev) {{
    var el = ev.target;
    if (el && el.classList && el.classList.contains('matrix-label-input')) ensureLabelSelectOptions(el);
  }});
  document.addEventListener('pointerdown', function(ev) {{
    var el = ev.target;
    if (el && el.classList && el.classList.contains('matrix-label-input')) ensureLabelSelectOptions(el);
  }});
  document.addEventListener('input', function(ev) {{
    var el = ev.target;
    if (!el) return;
    handleMatrixControlChange(el);
    if (el.classList && el.classList.contains('matrix-search')) {{
      var panelId = el.getAttribute('data-panel') || '';
      var panel = document.getElementById(panelId);
      var q = String(el.value || '').trim().toLowerCase();
      if (!panel) return;
      renderMatrixPanelRows(panel);
      panel.querySelectorAll('.matrix-sample-row').forEach(function(row) {{
        var hay = row.textContent.toLowerCase();
        row.style.display = (!q || hay.indexOf(q) !== -1) ? '' : 'none';
      }});
    }}
  }});
  document.addEventListener('change', function(ev) {{
    handleMatrixControlChange(ev.target);
  }});
  document.addEventListener('click', function(ev) {{
    var el = ev.target;
    if (!el || !el.classList) return;
    if (el.classList.contains('matrix-add-class')) {{
      ev.preventDefault();
      var panel = document.getElementById(el.getAttribute('data-panel') || '');
      if (panel) addClassGroup(panel);
    }}
    if (el.classList.contains('matrix-edit-class')) {{
      ev.preventDefault();
      var oldLabel = el.getAttribute('data-label') || '';
      var nextLabel = window.prompt('Edit class group label', oldLabel);
      if (nextLabel === null) return;
      nextLabel = String(nextLabel || '').trim();
      if (!nextLabel) return;
      renameCustomLabel(oldLabel, nextLabel);
    }}
    if (el.classList.contains('matrix-reset-overrides')) {{
      var f = field();
      if (f) f.value = '{{}}';
    }}
  }});
  document.querySelectorAll('.matrix-custom-class-list').forEach(renderCustomLabelList);
  var form = document.getElementById('run-form');
  if (form) {{
    form.addEventListener('submit', function(ev) {{
      var submitter = ev.submitter;
      if (!(submitter && submitter.classList && submitter.classList.contains('matrix-reset-overrides'))) {{
        collectOverrides();
      }}
    }});
  }}
  function showDerivedDownloadNotice(dlForm) {{
    var existing = document.getElementById('merit-derived-download-modal');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.id = 'merit-derived-download-modal';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.innerHTML =
      "<div style='position:fixed;inset:0;background:rgba(19,35,39,.46);z-index:9998'></div>" +
      "<div style='position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:9999;" +
      "width:min(620px,calc(100vw - 34px));background:#fff;border-radius:22px;border:1px solid rgba(19,35,39,.14);" +
      "box-shadow:0 28px 80px rgba(19,35,39,.28);padding:22px;color:#132327'>" +
      "<h3 style='margin:0 0 10px;font-family:Georgia,serif;font-size:1.28rem'>Generate MERIT-ML export ZIP?</h3>" +
      "<p style='margin:0 0 12px;color:#51656a;line-height:1.6'>" +
      "This download contains MERIT-ML-derived assessment data generated from public Metabolomics Workbench source records. " +
      "It does not replace the original Metabolomics Workbench record, and source matrix measurement values are preserved.</p>" +
      "<p style='margin:0 0 16px;color:#51656a;line-height:1.6'>" +
      "Please cite the original Metabolomics Workbench/NMDR project and study, including Project ID, Project DOI where available, " +
      "and associated publication(s), and cite MERIT-ML separately if using the assessment scores.</p>" +
      "<div style='display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap'>" +
      "<button type='button' id='merit-derived-download-cancel' style='border:1px solid rgba(19,35,39,.14);border-radius:12px;" +
      "background:white;color:#51656a;padding:9px 13px;font:inherit;font-weight:800;cursor:pointer'>Cancel</button>" +
      "<button type='button' id='merit-derived-download-confirm' style='border:0;border-radius:12px;" +
      "background:#0d6e6e;color:white;padding:9px 13px;font:inherit;font-weight:900;cursor:pointer'>I understand, generate ZIP</button>" +
      "</div></div>";
    document.body.appendChild(modal);
    var cancel = document.getElementById('merit-derived-download-cancel');
    var confirmBtn = document.getElementById('merit-derived-download-confirm');
    function closeModal() {{ modal.remove(); }}
    if (cancel) cancel.addEventListener('click', closeModal);
    modal.addEventListener('click', function(ev) {{
      if (ev.target === modal.firstChild) closeModal();
    }});
    if (confirmBtn) confirmBtn.addEventListener('click', function() {{
      closeModal();
      dlForm.dataset.meritDownloadAcknowledged = '1';
      dlForm.submit();
    }});
  }}
  document.querySelectorAll('.ml-data-download-form').forEach(function(dlForm) {{
    dlForm.addEventListener('submit', function(ev) {{
      collectOverrides();
      var hidden = dlForm.querySelector('.ml-download-overrides');
      var f = field();
      if (hidden) hidden.value = (f && f.value) ? f.value : '{{}}';
      if (dlForm.dataset.meritDownloadAcknowledged === '1') {{
        dlForm.dataset.meritDownloadAcknowledged = '';
        return;
      }}
      ev.preventDefault();
      showDerivedDownloadNotice(dlForm);
    }});
  }});
}})();

// Study browser (lazy batched search)
(function() {{
  var card = document.getElementById('study-browser-card');
  var out = document.getElementById('study-browser-list');
  var meta = document.getElementById('study-browser-meta');
  var loadMoreBtn = document.getElementById('study-load-more');
  if (!card || !out || !meta) return;
  var endpoint = card.getAttribute('data-endpoint') || '/study-browser-data';
  var facetIds = {{
    organism: 'facet-organism',
    disease: 'facet-disease',
    analysis_type: 'facet-analysis-type',
    ion_modes: 'facet-ion-mode',
    chromatography_types: 'facet-chromatography',
    instruments: 'facet-instrument',
    sample_types: 'facet-sample-type',
    project_type: 'facet-project-type',
    institute: 'facet-institute',
    mzrt_metadata_status: 'facet-mzrt-status',
    band: 'facet-band'
  }};
  var firstLabels = {{
    organism: 'All organisms',
    disease: 'All diseases',
    analysis_type: 'All analysis types',
    ion_modes: 'All ion modes',
    chromatography_types: 'All chromatography',
    instruments: 'All instruments',
    sample_types: 'All sample types',
    project_type: 'All project types',
    institute: 'All institutes',
    mzrt_metadata_status: 'All mass/RT-like metadata',
    band: 'All bands'
  }};
  var suggestionList = document.getElementById('study-id-suggestions');
  var searchInput = document.getElementById('study-search-text');
  var addFilteredBtn = document.getElementById('bulk-add-filtered');
  var resetFiltersBtn = document.getElementById('study-filter-reset');
  var bulkBatchMeta = document.getElementById('bulk-batch-meta');
  var bulkPrevBtn = document.getElementById('bulk-prev-batch');
  var bulkNextBtn = document.getElementById('bulk-next-batch');
  var state = {{
    rows: [],
    bulkRows: [],
    total: 0,
    totalRows: 0,
    nextOffset: null,
    loading: false,
    facetsLoaded: false,
	    bulkOffset: 0,
	    bulkLimit: 500,
	    bulkEnd: 0,
	    bulkBatchIndex: 0,
	    bulkBatchCount: 0,
	    bulkHasPrev: false,
	    bulkHasNext: false,
	    started: false
	  }};

  function esc(text) {{
    return String(text || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('\"', '&quot;');
  }}

  function populateFacet(key, options) {{
    var el = document.getElementById(facetIds[key]);
    if (!el) return;
    var current = el.value || '';
    var html = "<option value=''>" + esc(firstLabels[key] || 'All') + "</option>";
    (options || []).forEach(function(value) {{
      html += "<option value='" + esc(value) + "'>" + esc(value) + "</option>";
    }});
    el.innerHTML = html;
    if (current) el.value = current;
  }}

  function populateFacets(facets) {{
    facets = facets || {{}};
    Object.keys(facetIds).forEach(function(key) {{
      populateFacet(key, facets[key] || []);
    }});
  }}

  function queryParams(offset) {{
    var params = new URLSearchParams();
    params.set('offset', String(offset || 0));
    params.set('limit', '80');
    params.set('bulk_offset', String(state.bulkOffset || 0));
    params.set('bulk_limit', String(state.bulkLimit || 500));
    params.set('facets', state.facetsLoaded ? '0' : '1');
    if (searchInput && searchInput.value.trim()) params.set('q', searchInput.value.trim());
    Object.keys(facetIds).forEach(function(key) {{
      var el = document.getElementById(facetIds[key]);
      if (el && el.value) params.set(key, el.value);
    }});
    return params;
  }}

  function publishRows() {{
    window.__MERIT_STUDY_BROWSER_ROWS = state.rows || [];
    window.__MERIT_STUDY_BROWSER_MATCHED = state.bulkRows || [];
    try {{
      window.dispatchEvent(new CustomEvent('meritStudyRowsLoaded', {{
        detail: {{
          rows: state.rows || [],
          bulk_rows: state.bulkRows || [],
          bulk_offset: state.bulkOffset || 0,
          bulk_limit: state.bulkLimit || 500,
          bulk_end: state.bulkEnd || 0,
          bulk_batch_index: state.bulkBatchIndex || 0,
          bulk_batch_count: state.bulkBatchCount || 0
        }}
      }}));
    }} catch(e) {{}}
  }}

  function updateFilteredBulkButton() {{
    var n = state.bulkRows ? state.bulkRows.length : 0;
    var start = n ? ((state.bulkOffset || 0) + 1) : 0;
    var end = n ? (state.bulkEnd || ((state.bulkOffset || 0) + n)) : 0;
    var batchIndex = state.bulkBatchIndex || 0;
    var batchCount = state.bulkBatchCount || 0;
    if (bulkBatchMeta) {{
      bulkBatchMeta.textContent = state.total
        ? ('Bulk batch ' + batchIndex + ' of ' + batchCount + ': studies ' + start + '-' + end + ' of ' + state.total + ' matched')
        : 'Bulk batch: no matched studies';
    }}
    if (bulkPrevBtn) {{
      bulkPrevBtn.disabled = !state.bulkHasPrev || !!state.loading;
      bulkPrevBtn.style.opacity = bulkPrevBtn.disabled ? '.55' : '1';
      bulkPrevBtn.style.cursor = bulkPrevBtn.disabled ? 'not-allowed' : 'pointer';
    }}
    if (bulkNextBtn) {{
      bulkNextBtn.disabled = !state.bulkHasNext || !!state.loading;
      bulkNextBtn.style.opacity = bulkNextBtn.disabled ? '.55' : '1';
      bulkNextBtn.style.cursor = bulkNextBtn.disabled ? 'not-allowed' : 'pointer';
    }}
    if (!addFilteredBtn) return;
    addFilteredBtn.disabled = n < 1;
    addFilteredBtn.style.opacity = n < 1 ? '.55' : '1';
    addFilteredBtn.style.cursor = n < 1 ? 'not-allowed' : 'pointer';
    if (n < 1) {{
      addFilteredBtn.textContent = 'Use batch for bulk';
      addFilteredBtn.title = 'No studies are available in the current batch.';
    }} else if (batchCount > 1) {{
      addFilteredBtn.textContent = 'Use batch ' + batchIndex + ' (' + start + '-' + end + ')';
      addFilteredBtn.title = 'Replace the current Bulk MERIT-ML selection with this 500-study batch, then run or download it.';
    }} else {{
      addFilteredBtn.textContent = 'Use all ' + n + ' filtered';
      addFilteredBtn.title = 'Replace the current Bulk MERIT-ML selection with every currently filtered study.';
    }}
  }}

  function renderRows() {{
    updateFilteredBulkButton();
    var n = state.bulkRows ? state.bulkRows.length : 0;
    var batchText = state.total && state.bulkBatchCount > 1
      ? (' · bulk batch ' + state.bulkBatchIndex + '/' + state.bulkBatchCount + ' (' + ((state.bulkOffset || 0) + 1) + '-' + (state.bulkEnd || ((state.bulkOffset || 0) + n)) + ')')
      : '';
    meta.textContent = state.loading
      ? 'Loading studies...'
      : ('Showing ' + state.rows.length + ' of ' + state.total + ' matched studies' + batchText);
    if (suggestionList) {{
      suggestionList.innerHTML = (state.rows || []).map(function(r) {{
        var sid = esc(r.study_id || '');
        var title = esc(r.title || '');
        return "<option value='" + sid + "'>" + (title ? sid + ' — ' + title : sid) + "</option>";
      }}).join('');
    }}
    if (loadMoreBtn) {{
      loadMoreBtn.style.display = state.nextOffset === null ? 'none' : 'block';
      loadMoreBtn.disabled = !!state.loading;
      loadMoreBtn.textContent = state.loading ? 'Loading...' : 'Load more studies';
    }}
    if (!state.rows.length) {{
      out.innerHTML = state.loading
        ? "<div style='padding:8px;color:#51656a;font-size:.82rem'>Loading study browser in batches...</div>"
        : "<div style='padding:8px;color:#8f2d2d;font-size:.82rem;line-height:1.45'>No studies match current filters.<br><span style='color:#51656a'>Try tagged search such as <code>disease:alzheimers, organism:human, analysis:ms</code>.</span></div>";
      return;
    }}
    out.innerHTML = state.rows.map(function(r) {{
      var score = (typeof r.score === 'number') ? (r.score * 100).toFixed(1) : 'n/a';
      var band = esc(r.band || '');
      var sid = esc(r.study_id || '');
      var title = esc(r.title || '');
      var disease = esc(r.disease || '');
      var organism = esc(r.organism || '');
      var analysisType = esc(r.analysis_type || '');
      var projectType = esc(r.project_type || '');
      var mzrt = esc(r.mzrt_metadata_status || '');
      var labels = r._match_labels || r.__match_labels || [];
      var badges = labels.slice(0, 5).map(function(label) {{
        return "<span style='display:inline-flex;border-radius:999px;background:rgba(13,110,110,.08);"
          + "border:1px solid rgba(13,110,110,.16);color:#0d6e6e;padding:1px 6px;margin:2px 3px 0 0;"
          + "font-size:.66rem;font-weight:800'>" + esc(label) + "</span>";
      }}).join('');
      return (
        "<div class='study-browser-item' data-study-id='" + sid + "' " +
        "style='border:1px solid var(--line);background:#fff;padding:8px;border-radius:10px;margin:5px 0'>" +
        "<div style='display:flex;justify-content:space-between;gap:8px'>" +
        "<span style='font-family:IBM Plex Mono,monospace;font-size:.82rem;font-weight:700'>" + sid + "</span>" +
        "<span style='font-size:.78rem;color:#51656a'>score " + score + "/100 · " + band + "</span></div>" +
        "<div style='font-size:.76rem;color:#51656a;margin-top:2px'>" + organism +
        (disease ? " · " + disease : "") + "</div>" +
        ((analysisType || projectType || mzrt) ? "<div style='font-size:.74rem;color:#6d7f84;margin-top:2px'>" +
          (analysisType || "—") + (projectType ? " · " + projectType : "") +
          (mzrt ? " · mass/RT-like " + mzrt : "") + "</div>" : "") +
        (badges ? "<div style='margin-top:4px'><span style='font-size:.66rem;color:#6d7f84;font-weight:800'>Matched</span> " + badges + "</div>" : "") +
        (title ? "<div style='font-size:.74rem;color:#6d7f84;margin-top:2px;line-height:1.35'>" + title + "</div>" : "") +
        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:7px'>" +
        "<button type='button' class='study-pick' data-study-id='" + sid + "' " +
        "style='border:1px solid rgba(13,110,110,.24);background:rgba(13,110,110,.07);color:#0d6e6e;" +
        "padding:6px 7px;border-radius:9px;font:inherit;font-size:.72rem;font-weight:800;cursor:pointer'>Load study</button>" +
        "<button type='button' class='bulk-add-study' data-study-id='" + sid + "' " +
        "style='border:1px solid rgba(17,62,82,.18);background:rgba(17,62,82,.06);color:#113e52;" +
        "padding:6px 7px;border-radius:9px;font:inherit;font-size:.72rem;font-weight:800;cursor:pointer'>Add to bulk</button>" +
        "</div></div>"
      );
    }}).join('');
    out.querySelectorAll('.study-pick').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        var input = document.getElementById('study-id-input');
        if (input) {{
          input.value = btn.getAttribute('data-study-id') || '';
          input.focus();
          input.scrollIntoView({{behavior:'smooth', block:'center'}});
        }}
      }});
    }});
    out.querySelectorAll('.bulk-add-study').forEach(function(btn) {{
      btn.addEventListener('click', function(ev) {{
        ev.preventDefault();
        ev.stopPropagation();
        if (window.addStudyToBulk) window.addStudyToBulk(btn.getAttribute('data-study-id') || '');
      }});
    }});
  }}

	  function fetchRows(offset, append) {{
	    if (!window.fetch) {{
	      out.innerHTML = "<div style='padding:8px;color:#8f2d2d;font-size:.82rem'>This browser cannot load the lazy study index.</div>";
	      return;
	    }}
	    state.started = true;
	    state.loading = true;
    if (!append) {{
      state.rows = [];
      state.nextOffset = null;
      renderRows();
    }}
    fetch(endpoint + '?' + queryParams(offset || 0).toString(), {{cache: 'no-store'}})
      .then(function(resp) {{
        if (!resp.ok) throw new Error('Study browser request failed');
        return resp.json();
      }})
      .then(function(payload) {{
        state.loading = false;
        state.total = Number(payload.total || 0);
        state.totalRows = Number(payload.total_rows || 0);
        state.nextOffset = payload.next_offset === null || payload.next_offset === undefined ? null : Number(payload.next_offset);
        state.bulkRows = Array.isArray(payload.bulk_rows) ? payload.bulk_rows : [];
        state.bulkOffset = Number(payload.bulk_offset || 0);
        state.bulkLimit = Number(payload.bulk_limit || 500);
        state.bulkEnd = Number(payload.bulk_end || state.bulkRows.length || 0);
        state.bulkBatchIndex = Number(payload.bulk_batch_index || 0);
        state.bulkBatchCount = Number(payload.bulk_batch_count || 0);
        state.bulkHasPrev = !!payload.bulk_has_prev;
        state.bulkHasNext = !!payload.bulk_has_next;
        var nextRows = Array.isArray(payload.rows) ? payload.rows : [];
        state.rows = append ? state.rows.concat(nextRows) : nextRows;
        if (payload.facets && Object.keys(payload.facets).length) {{
          populateFacets(payload.facets || {{}});
          state.facetsLoaded = true;
        }}
        publishRows();
        renderRows();
      }})
      .catch(function(err) {{
        state.loading = false;
        state.rows = [];
        state.bulkRows = [];
        state.bulkOffset = 0;
        state.bulkEnd = 0;
        state.bulkBatchIndex = 0;
        state.bulkBatchCount = 0;
        state.bulkHasPrev = false;
        state.bulkHasNext = false;
        publishRows();
        meta.textContent = 'Study browser could not load.';
        out.innerHTML = "<div style='padding:8px;color:#8f2d2d;font-size:.82rem;line-height:1.45'>Could not load study browser data.<br><span style='color:#51656a'>" + esc(err && err.message ? err.message : err) + "</span></div>";
        updateFilteredBulkButton();
      }});
  }}

	  var timer = null;
	  function ensureStudyBrowserLoaded() {{
	    if (state.started || state.loading) return;
	    fetchRows(0, false);
	  }}
	  function resetBulkBatch() {{
	    state.bulkOffset = 0;
	  }}
	  function scheduleFetch() {{
	    if (timer) window.clearTimeout(timer);
    resetBulkBatch();
    timer = window.setTimeout(function() {{ fetchRows(0, false); }}, 220);
  }}
	  Object.keys(facetIds).forEach(function(k) {{
	    var el = document.getElementById(facetIds[k]);
	    if (el) {{
	      el.addEventListener('focus', ensureStudyBrowserLoaded);
	      el.addEventListener('change', function() {{
	        resetBulkBatch();
	        fetchRows(0, false);
	      }});
	    }}
	  }});
	  if (searchInput) {{
	    searchInput.addEventListener('focus', ensureStudyBrowserLoaded);
	    searchInput.addEventListener('input', scheduleFetch);
	    searchInput.addEventListener('keyup', scheduleFetch);
	    searchInput.addEventListener('search', function() {{ fetchRows(0, false); }});
	  }}
  if (resetFiltersBtn) resetFiltersBtn.addEventListener('click', function(ev) {{
    ev.preventDefault();
    if (searchInput) searchInput.value = '';
    Object.keys(facetIds).forEach(function(k) {{
      var el = document.getElementById(facetIds[k]);
      if (el) el.value = '';
    }});
    resetBulkBatch();
    fetchRows(0, false);
  }});
  if (bulkPrevBtn) bulkPrevBtn.addEventListener('click', function(ev) {{
    ev.preventDefault();
    if (!state.bulkHasPrev || state.loading) return;
    state.bulkOffset = Math.max(0, (state.bulkOffset || 0) - (state.bulkLimit || 500));
    fetchRows(0, false);
  }});
  if (bulkNextBtn) bulkNextBtn.addEventListener('click', function(ev) {{
    ev.preventDefault();
    if (!state.bulkHasNext || state.loading) return;
    state.bulkOffset = (state.bulkOffset || 0) + (state.bulkLimit || 500);
    fetchRows(0, false);
  }});
	  if (loadMoreBtn) loadMoreBtn.addEventListener('click', function(ev) {{
	    ev.preventDefault();
	    if (!state.started) {{ ensureStudyBrowserLoaded(); return; }}
	    if (state.nextOffset !== null) fetchRows(state.nextOffset, true);
	  }});
	  window.addEventListener('meritToolsShown', ensureStudyBrowserLoaded);
	  meta.textContent = 'Study browser loads on demand when you search or open tools.';
	  out.innerHTML = "<div style='padding:8px;color:#51656a;font-size:.82rem;line-height:1.45'>Use search, filters, or Show tools to load matching studies.</div>";
	  updateFilteredBulkButton();
	}})();

	// Bulk MERIT-ML workspace
	(function() {{
	  var KEY = 'merit_bulk_session_v2';
	  var listEl = document.getElementById('bulk-study-list');
	  var countEl = document.getElementById('bulk-study-count');
	  var statusEl = document.getElementById('bulk-status');
	  var sortEl = document.getElementById('bulk-sort');
	  var rows = [];
	  var dataEl = document.getElementById('study-browser-data');
	  try {{
	    var payload = dataEl ? JSON.parse(dataEl.textContent || '{{}}') : {{}};
	    rows = (payload && Array.isArray(payload.rows)) ? payload.rows : [];
	  }} catch(e) {{ rows = []; }}
	  var rowMap = {{}};
	  rows.forEach(function(r) {{
	    if (r && r.study_id) rowMap[String(r.study_id).toUpperCase()] = r;
	  }});
	  function mergeBrowserRows(newRows) {{
	    (Array.isArray(newRows) ? newRows : []).forEach(function(r) {{
	      if (r && r.study_id) {{
	        var sid = String(r.study_id).toUpperCase();
	        rowMap[sid] = Object.assign({{}}, rowMap[sid] || {{}}, r);
	      }}
	    }});
	  }}
	  window.addEventListener('meritStudyRowsLoaded', function(ev) {{
	    var detail = (ev && ev.detail) || {{}};
	    mergeBrowserRows(detail.rows || []);
	    mergeBrowserRows(detail.bulk_rows || []);
	  }});

	  function esc(text) {{
	    return String(text || '')
	      .replaceAll('&', '&amp;')
	      .replaceAll('<', '&lt;')
	      .replaceAll('>', '&gt;')
	      .replaceAll('\"', '&quot;');
	  }}

	  function normalizeSearchText(value) {{
	    var text = String(value || '').toLowerCase();
	    try {{ text = text.normalize('NFKD').replace(/[\\u0300-\\u036f]/g, ''); }} catch(e) {{}}
	    text = text.replace(/[’'`]/g, '');
	    text = text.replace(/[^a-z0-9]+/g, ' ');
	    return text.replace(/\\s+/g, ' ').trim();
	  }}

	  function rowSearchFields(r) {{
	    var analysisParts = []
	      .concat(r.analysis_type || [])
	      .concat(r.analysis_types || [])
	      .concat(r.analysis_type_raw || [])
	      .concat(r.platform || [])
	      .concat(r.platform_raw || [])
	      .concat(r.ion_modes || []);
	    var fields = {{
	      id: normalizeSearchText(r.study_id || ''),
	      title: normalizeSearchText(r.title || ''),
	      disease: normalizeSearchText(r.disease || ''),
	      organism: normalizeSearchText(r.organism || ''),
	      analysis: normalizeSearchText(analysisParts.join(' ')),
	      instrument: normalizeSearchText((r.instruments || []).join(' ')),
	      chromatography: normalizeSearchText((r.chromatography_types || []).join(' ')),
	      sample: normalizeSearchText((r.sample_types || []).join(' ')),
	      project: normalizeSearchText(r.project_type || ''),
	      institute: normalizeSearchText(r.institute || ''),
	      all: normalizeSearchText(r.search_text || '')
	    }};
	    fields.method = normalizeSearchText([fields.analysis, fields.instrument, fields.chromatography].join(' '));
	    return fields;
	  }}

	  var fieldAliases = {{
	    study: 'id', id: 'id', accession: 'id',
	    title: 'title',
	    disease: 'disease', condition: 'disease', phenotype: 'disease', diagnosis: 'disease',
	    organism: 'organism', species: 'organism', taxon: 'organism',
	    analysis: 'analysis', assay: 'analysis', platform: 'analysis', method: 'method',
	    instrument: 'instrument', msinstrument: 'instrument',
	    chromatography: 'chromatography', chrom: 'chromatography', column: 'chromatography',
	    sample: 'sample', tissue: 'sample', matrix: 'sample',
	      project: 'project', design: 'project',
	    institute: 'institute', center: 'institute'
	  }};

	  function splitRespectingQuotes(text, sep) {{
	    var out = [];
	    var buf = '';
	    var quote = '';
	    String(text || '').split('').forEach(function(ch) {{
	      if ((ch === '"' || ch === "'") && !quote) {{ quote = ch; return; }}
	      if (quote && ch === quote) {{ quote = ''; return; }}
	      if (!quote && ch === sep) {{
	        if (buf.trim()) out.push(buf.trim());
	        buf = '';
	      }} else {{
	        buf += ch;
	      }}
	    }});
	    if (buf.trim()) out.push(buf.trim());
	    return out;
	  }}

	  function expandQueryTerm(term) {{
	    var t = normalizeSearchText(term);
	    if (!t) return [];
	    var map = {{
	      human: ['homo sapiens', 'human', 'humans'],
	      humans: ['homo sapiens', 'human', 'humans'],
	      'homo sapiens': ['homo sapiens', 'human', 'humans'],
	      mouse: ['mus musculus', 'mouse', 'mice'],
	      mice: ['mus musculus', 'mouse', 'mice'],
	      'mus musculus': ['mus musculus', 'mouse', 'mice'],
	      rat: ['rattus norvegicus', 'rat', 'rats'],
	      rats: ['rattus norvegicus', 'rat', 'rats'],
	      'rattus norvegicus': ['rattus norvegicus', 'rat', 'rats'],
	      alzheimer: ['alzheimer', 'alzheimers', 'alzheimer disease', 'alzheimers disease'],
	      alzheimers: ['alzheimer', 'alzheimers', 'alzheimer disease', 'alzheimers disease'],
	      'alzheimer disease': ['alzheimer', 'alzheimers', 'alzheimer disease', 'alzheimers disease'],
	      'alzheimers disease': ['alzheimer', 'alzheimers', 'alzheimer disease', 'alzheimers disease'],
	      cancer: ['cancer', 'tumor', 'tumour', 'carcinoma', 'neoplasm', 'malignancy', 'adenocarcinoma'],
	      tumor: ['cancer', 'tumor', 'tumour', 'carcinoma', 'neoplasm', 'malignancy', 'adenocarcinoma'],
	      tumour: ['cancer', 'tumor', 'tumour', 'carcinoma', 'neoplasm', 'malignancy', 'adenocarcinoma'],
	      neoplasm: ['cancer', 'tumor', 'tumour', 'carcinoma', 'neoplasm', 'malignancy', 'adenocarcinoma'],
	      diabetes: ['diabetes', 'diabetic', 't2d', 'type 2 diabetes', 'type ii diabetes'],
	      ms: ['ms', 'mass spectrometry'],
	      'mass spec': ['ms', 'mass spectrometry'],
	      'mass spectrometry': ['ms', 'mass spectrometry'],
	      nmr: ['nmr', 'nuclear magnetic resonance'],
	      lc: ['lc', 'liquid chromatography'],
	      gc: ['gc', 'gas chromatography'],
	      hilic: ['hilic'],
	      rp: ['reversed phase', 'reverse phase', 'rp'],
	      'reversed phase': ['reversed phase', 'reverse phase', 'rp'],
	      positive: ['positive', 'pos'],
	      negative: ['negative', 'neg']
	    }};
	    var vals = map[t] || [t];
	    var seen = {{}};
	    return vals.map(normalizeSearchText).filter(function(v) {{
	      if (!v || seen[v]) return false;
	      seen[v] = true;
	      return true;
	    }});
	  }}

	  function inferFieldForTerm(term) {{
	    var t = normalizeSearchText(term);
	    if (['human','humans','homo sapiens','mouse','mice','mus musculus','rat','rats','rattus norvegicus'].indexOf(t) !== -1) return 'organism';
	    if (['ms','mass spec','mass spectrometry','nmr','lc','gc','hilic','rp','reversed phase','reverse phase','positive','negative'].indexOf(t) !== -1) return 'method';
	    return '';
	  }}

	  function parseSearchQuery(raw) {{
	    var text = String(raw || '').trim();
	    if (!text) return [];
	    var conceptParts = splitRespectingQuotes(text, ',');
	    if (conceptParts.length === 1 && text.indexOf('|') === -1 && text.indexOf(':') === -1) {{
	      var simple = normalizeSearchText(text).split(' ').filter(Boolean);
	      if (simple.length > 1 && simple.length <= 5) conceptParts = simple;
	    }}
	    return conceptParts.map(function(concept) {{
	      var alternatives = splitRespectingQuotes(concept, '|').map(function(rawAlt) {{
	        var alt = String(rawAlt || '').trim();
	        var field = '';
	        var term = alt;
	        var m = alt.match(/^([a-zA-Z_ -]+)\\s*:\\s*(.+)$/);
	        if (m) {{
	          var alias = normalizeSearchText(m[1]).replace(/\\s+/g, '');
	          field = fieldAliases[alias] || '';
	          term = m[2];
	        }}
	        if (!field) field = inferFieldForTerm(term);
	        return {{
	          raw: alt,
	          field: field,
	          term: term,
	          variants: expandQueryTerm(term)
	        }};
	      }}).filter(function(item) {{ return item.variants.length > 0; }});
	      return alternatives;
	    }}).filter(function(group) {{ return group.length > 0; }});
	  }}

	  function containsPhrase(hay, needle) {{
	    hay = normalizeSearchText(hay);
	    needle = normalizeSearchText(needle);
	    if (!hay || !needle) return false;
	    if (needle.length <= 3 && needle.indexOf(' ') === -1) {{
	      return (' ' + hay + ' ').indexOf(' ' + needle + ' ') !== -1;
	    }}
	    return hay.indexOf(needle) !== -1;
	  }}

	  function fieldsForAlternative(fields, alt) {{
	    var key = alt.field || '';
	    if (key && fields[key] !== undefined) return [[key, fields[key]]];
	    return [
	      ['disease', fields.disease],
	      ['organism', fields.organism],
	      ['analysis', fields.analysis],
	      ['title', fields.title],
	      ['sample', fields.sample],
	      ['project', fields.project],
	      ['instrument', fields.instrument],
	      ['chromatography', fields.chromatography],
	      ['institute', fields.institute],
	      ['all', fields.all]
	    ];
	  }}

	  function matchAlternative(fields, alt) {{
	    var candidates = fieldsForAlternative(fields, alt);
	    var best = null;
	    alt.variants.forEach(function(variant) {{
	      candidates.forEach(function(pair) {{
	        if (!containsPhrase(pair[1], variant)) return;
	        var field = pair[0];
	        var base = {{
	          disease: 100,
	          organism: 95,
	          analysis: 90,
	          method: 90,
	          title: 80,
	          sample: 70,
	          project: 65,
	          instrument: 62,
	          chromatography: 62,
	          institute: 35,
	          all: 20
	        }}[field] || 25;
	        var exactBonus = normalizeSearchText(pair[1]) === normalizeSearchText(variant) ? 8 : 0;
	        var score = base + exactBonus;
	        if (!best || score > best.score) {{
	          best = {{score: score, field: field, term: normalizeSearchText(alt.term), variant: variant}};
	        }}
	      }});
	    }});
	    return best;
	  }}

	  function matchSearchQuery(r, rawSearch) {{
	    var groups = parseSearchQuery(rawSearch);
	    if (!groups.length) return {{ok: true, score: 0, labels: []}};
	    var fields = rowSearchFields(r || {{}});
	    var labels = [];
	    var score = 0;
	    for (var i = 0; i < groups.length; i += 1) {{
	      var best = null;
	      groups[i].forEach(function(alt) {{
	        var hit = matchAlternative(fields, alt);
	        if (hit && (!best || hit.score > best.score)) best = hit;
	      }});
	      if (!best) return {{ok: false, score: 0, labels: []}};
	      score += best.score;
	      labels.push(best.field + ':' + best.term);
	    }}
	    return {{ok: true, score: score, labels: labels}};
	  }}
	  function nowIso() {{
	    try {{ return new Date().toISOString(); }} catch(e) {{ return ''; }}
	  }}
	  function readSession() {{
	    var fallback = {{version: 1, created_at: nowIso(), studies: {{}}}};
	    try {{
	      var raw = window.localStorage ? localStorage.getItem(KEY) : '';
	      if (!raw) return fallback;
	      var parsed = JSON.parse(raw);
	      if (!parsed || typeof parsed !== 'object') return fallback;
	      if (!parsed.studies || typeof parsed.studies !== 'object') parsed.studies = {{}};
	      if (!parsed.version) parsed.version = 1;
	      return parsed;
	    }} catch(e) {{
	      return fallback;
	    }}
	  }}
	  function writeSession(session) {{
	    session.updated_at = nowIso();
	    try {{ if (window.localStorage) localStorage.setItem(KEY, JSON.stringify(session)); }} catch(e) {{}}
	    renderBulk();
	  }}
	  function status(text, color) {{
	    if (!statusEl) return;
	    statusEl.textContent = text || '';
	    statusEl.style.color = color || '#51656a';
	  }}
	  function currentStudyId() {{
	    var input = document.getElementById('study-id-input');
	    return input ? String(input.value || '').trim().toUpperCase() : '';
	  }}
		  function currentReportSummary() {{
		    var state = window.__MERIT_STATE_JSON || {{}};
		    var source = window.__MERIT_ACTIVE_SOURCE || state.primary_source || '';
		    var summary = {{}};
		    var rs = null;
		    var analysisIds = [];
		    try {{
		      if (source && state.source_assessments && state.source_assessments[source]) {{
		        var item = state.source_assessments[source];
		        summary = ((item || {{}}).summary || {{}}) || {{}};
		        rs = (item || {{}}).readiness_score || null;
		      }}
		      var bySource = ((state.source_availability || {{}}).analyses_by_source || {{}}) || {{}};
		      Object.keys(bySource).forEach(function(key) {{
		        var ids = bySource[key] || [];
		        if (Array.isArray(ids)) ids.forEach(function(aid) {{
		          aid = String(aid || '').trim().toUpperCase();
		          if (aid && analysisIds.indexOf(aid) === -1) analysisIds.push(aid);
		        }});
		      }});
		      if (!summary || !summary.study_id) {{
		        summary = (state.summary || ((state.final_report || {{}}).ingestion_summary || {{}})) || {{}};
		      }}
		      if (!analysisIds.length && summary && Array.isArray(summary.per_analysis)) {{
		        summary.per_analysis.forEach(function(item) {{
		          var aid = String(((item || {{}}).analysis_id || '')).trim().toUpperCase();
		          if (aid && analysisIds.indexOf(aid) === -1) analysisIds.push(aid);
		        }});
		      }}
		      if (!rs) rs = state.readiness_score || null;
		    }} catch(e) {{
		      summary = {{}};
		      rs = null;
		      analysisIds = [];
		    }}
		    return {{
	      study_id: String(summary.study_id || state.study_id || currentStudyId() || '').toUpperCase(),
	      title: summary.title || '',
	      organism: summary.organism || '',
	      analysis_type: summary.analysis_type || '',
	      n_samples: Number(summary.n_samples || 0) || null,
	      n_ml_eligible_samples: Number(summary.n_biological_samples || 0) || null,
		      n_features: Number(summary.n_features || 0) || null,
		      analysis_ids: analysisIds,
		      source: source || '',
		      score: rs && typeof rs.score === 'number' ? rs.score : null,
	      band: rs ? (rs.final_band_label || rs.final_band || rs.band || '') : ''
	    }};
	  }}
	  function scoringParams() {{
	    if (typeof window.currentV2ScoringParams === 'function') return window.currentV2ScoringParams();
	    var out = {{}};
	    document.querySelectorAll('.v2-number[data-v2-param]').forEach(function(el) {{
	      var key = el.getAttribute('data-v2-param') || '';
	      if (key) out[key] = el.value;
	    }});
	    return out;
	  }}
	  function matrixOverrides() {{
	    if (typeof window.currentMatrixOverrides === 'function') return window.currentMatrixOverrides();
	    var field = document.getElementById('matrix-overrides-field');
	    try {{ return JSON.parse((field && field.value) || '{{}}') || {{}}; }}
	    catch(e) {{ return {{}}; }}
	  }}
	  function buildEntry(studyId, includeCurrentEdits) {{
	    var sid = String(studyId || '').trim().toUpperCase();
	    if (!sid) return null;
	    var row = rowMap[sid] || {{}};
	    var current = includeCurrentEdits ? currentReportSummary() : {{}};
	    var entry = {{
	      study_id: sid,
	      title: current.title || row.title || '',
	      organism: current.organism || row.organism || '',
	      disease: row.disease || '',
	      analysis_type: current.analysis_type || row.analysis_type || '',
	      project_type: row.project_type || '',
	      n_samples: current.n_samples || row.n_samples || null,
	      n_ml_eligible_samples: current.n_ml_eligible_samples || row.n_ml_eligible_samples || null,
	      n_features: current.n_features || row.n_features || null,
	      score: (typeof current.score === 'number') ? current.score : (typeof row.score === 'number' ? row.score : null),
	      band: current.band || row.band || row.band_label || '',
		      selected_source: current.source || window.__MERIT_ACTIVE_SOURCE || '',
		      analysis_ids: current.analysis_ids || row.analysis_ids || [],
		      saved_at: nowIso(),
	      matrix_overrides: includeCurrentEdits ? matrixOverrides() : {{}},
	      scoring_params: includeCurrentEdits ? scoringParams() : {{}}
	    }};
	    return entry;
	  }}
	  function addOrUpdate(studyId, includeCurrentEdits) {{
	    var entry = buildEntry(studyId, includeCurrentEdits);
	    if (!entry) {{
	      status('No study ID available to add.', '#8f2d2d');
	      return;
	    }}
	    var session = readSession();
	    var existing = session.studies[entry.study_id] || {{}};
	    session.studies[entry.study_id] = Object.assign({{}}, existing, entry, {{
	      added_at: existing.added_at || nowIso()
	    }});
	    writeSession(session);
	    status(includeCurrentEdits ? ('Saved edits for ' + entry.study_id + '.') : ('Added ' + entry.study_id + ' to Bulk MERIT-ML.'), '#0d6e6e');
	  }}
	  function addMany(rowsToAdd, replaceExisting) {{
	    var rowsList = Array.isArray(rowsToAdd) ? rowsToAdd : [];
	    if (!rowsList.length) {{
	      status('No filtered studies are available to add.', '#8f2d2d');
	      return;
	    }}
	    var maxRun = 500;
	    if (rowsList.length > maxRun) {{
	      var proceed = confirm('This selected batch contains ' + rowsList.length + ' studies. Bulk MERIT-ML is capped at ' + maxRun + ' studies per run. Use the first ' + maxRun + ' studies from this batch?');
	      if (!proceed) {{
	        status('Choose a smaller batch or narrower filter, then try again.', '#51656a');
	        return;
	      }}
	    }}
	    var selected = rowsList.slice(0, maxRun);
	    var session = readSession();
	    if (replaceExisting) {{
	      session.studies = {{}};
	    }}
	    var added = 0;
	    var updated = 0;
	    selected.forEach(function(row) {{
	      var sid = String((row && row.study_id) || '').trim().toUpperCase();
	      if (!sid) return;
	      rowMap[sid] = Object.assign({{}}, rowMap[sid] || {{}}, row || {{}});
	      var entry = buildEntry(sid, false);
	      if (!entry) return;
	      var existing = session.studies[entry.study_id] || null;
	      session.studies[entry.study_id] = Object.assign({{}}, existing || {{}}, entry, {{
	        added_at: (existing && existing.added_at) || nowIso()
	      }});
	      if (existing) updated += 1; else added += 1;
	    }});
	    writeSession(session);
	    var capped = rowsList.length > maxRun ? (' Capped at ' + maxRun + ' studies for this run.') : '';
	    var prefix = replaceExisting ? 'Loaded current batch into Bulk MERIT-ML, replacing the previous selection. ' : '';
	    status(prefix + 'Added ' + added + ' studies and refreshed ' + updated + ' already-selected studies.' + capped, '#0d6e6e');
	  }}
	  window.addStudyToBulk = function(studyId) {{
	    addOrUpdate(studyId, false);
	  }};
	  window.addFilteredStudiesToBulk = function(rowsToAdd) {{
	    addMany(rowsToAdd, true);
	  }};
	  window.saveCurrentStudyToBulk = function() {{
	    var sid = currentStudyId();
	    if (!sid) {{
	      status('Load or enter a study ID before saving current edits.', '#8f2d2d');
	      return;
	    }}
	    addOrUpdate(sid, true);
	  }};
	  function studyArray(session) {{
	    return Object.keys(session.studies || {{}}).map(function(k) {{ return session.studies[k]; }});
	  }}
	  function renderBulk() {{
	    var session = readSession();
	    var arr = studyArray(session);
	    if (countEl) countEl.textContent = String(arr.length);
	    if (!listEl) return;
	    var sortKey = sortEl ? sortEl.value : 'added';
	    arr.sort(function(a, b) {{
	      if (sortKey === 'score') return (Number(a.score || -1) - Number(b.score || -1));
	      if (sortKey === 'samples') return (Number(a.n_ml_eligible_samples || a.n_samples || 1e12) - Number(b.n_ml_eligible_samples || b.n_samples || 1e12));
	      if (sortKey === 'organism') return String(a.organism || '').localeCompare(String(b.organism || '')) || String(a.study_id || '').localeCompare(String(b.study_id || ''));
	      if (sortKey === 'study_id') return String(a.study_id || '').localeCompare(String(b.study_id || ''));
	      return String(a.added_at || '').localeCompare(String(b.added_at || ''));
	    }});
	    if (!arr.length) {{
	      listEl.innerHTML = "<div style='padding:8px;color:#7b8b90;font-size:.78rem;line-height:1.35'>No studies selected yet. Use <strong>Add to bulk</strong> in Find Similar Studies.</div>";
	      return;
	    }}
	    listEl.innerHTML = arr.map(function(item) {{
	      var sid = esc(item.study_id || '');
	      var sampleText = item.n_ml_eligible_samples ? (item.n_ml_eligible_samples + ' ML samples') : (item.n_samples ? (item.n_samples + ' samples') : 'samples after run');
	      var scoreText = (typeof item.score === 'number') ? ((item.score * 100).toFixed(1) + '/100') : 'score after run';
	      var edited = (item.matrix_overrides && Object.keys(item.matrix_overrides).length) ? ' · matrix edits' : '';
	      var tuned = (item.scoring_params && Object.keys(item.scoring_params).length) ? ' · thresholds saved' : '';
	      return (
	        "<div style='padding:7px 6px;border-bottom:1px solid rgba(19,35,39,.07)'>" +
	        "<div style='display:flex;justify-content:space-between;gap:6px;align-items:center'>" +
	        "<button type='button' class='bulk-load-study' data-study-id='" + sid + "' " +
	        "style='border:0;background:transparent;color:#0d6e6e;font:inherit;font-size:.78rem;font-weight:900;padding:0;cursor:pointer'>" + sid + "</button>" +
	        "<button type='button' class='bulk-remove-study' data-study-id='" + sid + "' " +
	        "style='border:0;background:transparent;color:#8f2d2d;font:inherit;font-size:.72rem;font-weight:800;cursor:pointer'>remove</button></div>" +
	        "<div style='font-size:.72rem;color:#51656a;line-height:1.35'>" + esc(item.organism || 'organism n/a') + " · " + esc(sampleText) + " · " + esc(scoreText) + edited + tuned + "</div>" +
	        (item.title ? "<div style='font-size:.7rem;color:#7b8b90;line-height:1.3;margin-top:2px'>" + esc(item.title).slice(0, 120) + "</div>" : "") +
	        "</div>"
	      );
	    }}).join('');
	    listEl.querySelectorAll('.bulk-remove-study').forEach(function(btn) {{
	      btn.addEventListener('click', function() {{
	        var sid = btn.getAttribute('data-study-id') || '';
	        var session = readSession();
	        delete session.studies[sid];
	        writeSession(session);
	        status('Removed ' + sid + ' from Bulk MERIT-ML.', '#51656a');
	      }});
	    }});
	    listEl.querySelectorAll('.bulk-load-study').forEach(function(btn) {{
	      btn.addEventListener('click', function() {{
	        var sid = btn.getAttribute('data-study-id') || '';
	        var input = document.getElementById('study-id-input');
	        if (input) {{
	          input.value = sid;
	          input.focus();
	          input.scrollIntoView({{behavior:'smooth', block:'center'}});
	        }}
	      }});
	    }});
	  }}
	  var saveBtn = document.getElementById('bulk-save-current');
	  if (saveBtn) saveBtn.addEventListener('click', function(ev) {{
	    ev.preventDefault();
	    window.saveCurrentStudyToBulk();
	  }});
	  var addFilteredBtn = document.getElementById('bulk-add-filtered');
	  if (addFilteredBtn) addFilteredBtn.addEventListener('click', function(ev) {{
	    ev.preventDefault();
	    addMany(window.__MERIT_STUDY_BROWSER_MATCHED || [], true);
	  }});
	  var clearBtn = document.getElementById('bulk-clear');
	  if (clearBtn) clearBtn.addEventListener('click', function(ev) {{
	    ev.preventDefault();
	    if (!confirm('Clear the current Bulk MERIT-ML study set from this browser?')) return;
	    try {{ localStorage.removeItem(KEY); }} catch(e) {{}}
	    renderBulk();
	    status('Bulk MERIT-ML study set cleared.', '#51656a');
	  }});
	  if (sortEl) sortEl.addEventListener('change', renderBulk);
	  var form = document.getElementById('bulk-run-form');
	  if (form) form.addEventListener('submit', function(ev) {{
	    var session = readSession();
	    if (!Object.keys(session.studies || {{}}).length) {{
	      ev.preventDefault();
	      status('Add at least one study before running Bulk MERIT-ML.', '#8f2d2d');
	      return;
	    }}
	    var field = document.getElementById('bulk-session-field');
	    if (field) field.value = JSON.stringify(session);
	    status('Opening Bulk MERIT-ML runner. Keep the next page open until it finishes.', '#0d6e6e');
	  }});
	  var dl = document.getElementById('bulk-download-session');
	  if (dl) dl.addEventListener('click', function(ev) {{
	    ev.preventDefault();
	    var session = readSession();
	    var blob = new Blob([JSON.stringify(session, null, 2)], {{type:'application/json'}});
	    var a = document.createElement('a');
	    a.href = URL.createObjectURL(blob);
	    a.download = 'merit_bulk_session.json';
	    document.body.appendChild(a);
	    a.click();
	    document.body.removeChild(a);
	    setTimeout(function(){{ URL.revokeObjectURL(a.href); }}, 300);
	  }});
	  renderBulk();
	}})();
	</script>
{_merit_analytics_consent_banner()}
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class MetaboUIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # suppress per-request console noise

    def _send_html(self, text: str, status: int = HTTPStatus.OK) -> None:
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, data: dict[str, Any], status: int = HTTPStatus.OK, headers_no_store: bool = False) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store" if headers_no_store else "private, max-age=60")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_asset(self, payload: bytes, mime: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_zip(self, payload: bytes, filename: str, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    _DEFAULT_ROOT = "/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/assets/logo.png":
            asset = _logo_asset_bytes()
            if asset is None:
                self._send_html("Logo not found.", HTTPStatus.NOT_FOUND)
                return
            payload, mime = asset
            self._send_asset(payload, mime)
            return
        if parsed.path == "/study-browser-data":
            query = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
            precomputed_root = query.get("precomputed_root") or _default_precomputed_root()
            self._send_json(_study_browser_data_payload(precomputed_root, query))
            return
        if parsed.path != "/":
            self._send_html(_page(error="Unknown route."), HTTPStatus.NOT_FOUND)
            return
        query = {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        study_id = str(query.get("study_id", "") or "").strip()
        requested_profile = str(query.get("profile", "full") or "full").strip() or "full"
        defaults = {
            "root": self._DEFAULT_ROOT,
            "precomputed_root": _default_precomputed_root(),
            "study_id": study_id,
            "profile": requested_profile,
        }
        if study_id:
            embargo_message = _embargoed_study_message(study_id)
            if embargo_message:
                self._send_html(_page(error=embargo_message, defaults=defaults), HTTPStatus.FORBIDDEN)
                return
            cached_state = _load_precomputed_state(
                study_id=study_id,
                precomputed_root=_default_precomputed_root(),
                requested_profile=requested_profile,
            )
            if cached_state is not None:
                self._send_html(_page(state=cached_state, defaults=defaults))
                return
            self._send_html(
                _page(error=f"The study {study_id} is not available in the current version of MERIT-ML.", defaults=defaults),
                HTTPStatus.NOT_FOUND,
            )
            return
        self._send_html(
            _page(
                defaults=defaults
            )
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/workflow/run", "/workflow/load-state", "/bulk/run", "/bulk/chunk", "/download/ml-ready-data", "/bulk/download-data"}:
            self._send_html(_page(error="Unknown route."), HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        form = {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}
        defaults = dict(form)
        defaults["source"] = "workbench"
        try:
            if self.path == "/download/ml-ready-data":
                payload, filename = _ml_ready_data_zip_payload(
                    form.get("study_id", ""),
                    form.get("matrix_overrides", "{}"),
                    form.get("analysis_ids", ""),
                )
                self._send_zip(payload, filename)
                return

            if self.path == "/bulk/download-data":
                payload, filename = _bulk_ml_ready_data_zip_payload(form.get("bulk_session", ""))
                self._send_zip(payload, filename)
                return

            if self.path == "/bulk/chunk":
                precomputed_root = (
                    form.get("precomputed_root")
                    or form.get("output_root")
                    or _default_precomputed_root()
                )
                payload = _bulk_chunk_payload(form.get("bulk_session", ""), precomputed_root)
                payload["chunk_start"] = int(form.get("chunk_start", 0) or 0)
                payload["chunk_size"] = int(form.get("chunk_size", payload.get("n_used", 0)) or 0)
                self._send_json(payload, headers_no_store=True)
                return

            if self.path == "/bulk/run":
                precomputed_root = (
                    form.get("precomputed_root")
                    or form.get("output_root")
                    or _default_precomputed_root()
                )
                session = _bulk_clean_session(form.get("bulk_session", ""))
                _raise_if_embargoed_bulk_session(session)
                self._send_html(_bulk_runner_page(session, precomputed_root))
                return

            if self.path == "/workflow/load-state":
                state_path = (form.get("state_path") or "").strip()
                if not state_path:
                    raise ValueError("State JSON path is required.")
                state = _load_cached_workflow_state(state_path)
                state_study_id = _v2_state_study_id(state)
                embargo_message = _embargoed_study_message(state_study_id)
                if embargo_message:
                    self._send_html(_page(error=embargo_message, defaults=defaults), HTTPStatus.FORBIDDEN)
                    return
                self._send_html(_page(state=state, defaults=defaults))
                return

            source = "workbench"
            fetch_mode = _requested_fetch_mode(source, allow_remote_fallback=False)
            study_id = form.get("study_id", "").strip()
            embargo_message = _embargoed_study_message(study_id)
            if embargo_message:
                self._send_html(_page(error=embargo_message, defaults=defaults), HTTPStatus.FORBIDDEN)
                return
            requested_profile = form.get("profile", "full")
            precomputed_root = (
                form.get("precomputed_root")
                or form.get("output_root")
                or _default_precomputed_root()
            )
            cached_state = _load_precomputed_state(
                study_id=study_id,
                precomputed_root=precomputed_root,
                requested_profile=requested_profile,
            )
            if cached_state is not None:
                self._send_html(_page(state=cached_state, defaults=defaults))
                return
            # In cached-workbench mode, do not silently fall back to a fresh workflow run.
            # If the study is not present in the current precomputed bundle, return
            # a clear user-facing message.
            raise ValueError("The study is not available in the current version of MERIT-ML.")

            from merit.workflow import run_guided_workflow
            state = run_guided_workflow(
                source=source,
                study_id=study_id,
                profile=requested_profile,
                fetch_mode=fetch_mode,
                root=form.get("root") or None,
                download_root=form.get("download_root") or None,
                output_root=form.get("output_root") or None,
            )
            self._send_html(_page(state=state, defaults=defaults))
        except Exception as exc:  # pragma: no cover
            if self.path == "/bulk/chunk":
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST, headers_no_store=True)
                return
            self._send_html(_page(error=str(exc), defaults=defaults), HTTPStatus.BAD_REQUEST)


def serve_ui(host: str = "0.0.0.0", port: int = 8765) -> None:
    import socket
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), MetaboUIHandler)
    try:
        server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass  # SO_REUSEPORT not available on all platforms
    print(f"MERIT-ML UI → http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _requested_fetch_mode(source: str, allow_remote_fallback: bool) -> str:
    # UI is Workbench-only by default; keep this helper simple and explicit.
    if source != "workbench":
        return "local"
    return "auto" if allow_remote_fallback else "local"


def _is_http_location(value: str) -> bool:
    raw = str(value or "").strip().lower()
    return raw.startswith("http://") or raw.startswith("https://")


def _read_json_from_location(location: str | Path) -> Any:
    loc = str(location).strip()
    if _is_http_location(loc):
        req = Request(
            loc,
            headers={
                # Cloudflare R2 may reject Python-urllib default user-agent (403).
                "User-Agent": "curl/8.0",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=25) as resp:
            payload = resp.read()
        return json.loads(payload.decode("utf-8"))
    return read_json(Path(loc).expanduser())


def _load_precomputed_state(
    *,
    study_id: str,
    precomputed_root: str | Path | None,
    requested_profile: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a cached workflow state by study id from index.json/direct JSON path."""
    if _is_embargoed_study(study_id):
        return None
    root_raw = str(precomputed_root).strip() if precomputed_root else _default_precomputed_root()
    remote_root = _is_http_location(root_raw)
    index_loc = (
        urljoin(root_raw.rstrip("/") + "/", "index.json")
        if remote_root
        else str((Path(root_raw).expanduser() / "index.json"))
    )

    candidate_locs: list[str] = []
    try:
        index_payload = _read_json_from_location(index_loc)
        studies = (index_payload or {}).get("studies", {})
        entry = studies.get(study_id.upper()) if isinstance(studies, dict) else None
        state_path = (entry or {}).get("state_path") if isinstance(entry, dict) else None
        if state_path:
            state_path_str = str(state_path).strip()
            if remote_root:
                if _is_http_location(state_path_str):
                    candidate_locs.append(state_path_str)
            else:
                candidate_locs.append(str(Path(state_path_str).expanduser()))
    except Exception:
        pass

    if remote_root:
        candidate_locs.append(urljoin(root_raw.rstrip("/") + "/", f"json/{study_id.lower()}_workflow_state.json"))
    else:
        candidate_locs.append(str((Path(root_raw).expanduser() / "json" / f"{study_id.lower()}_workflow_state.json")))

    for candidate in candidate_locs:
        try:
            payload = _read_json_from_location(candidate)
            if not isinstance(payload, dict):
                continue
            cached_profile = str((payload or {}).get("profile", "")).strip().lower()
            requested = requested_profile.strip().lower() if requested_profile else ""
            if requested and cached_profile and cached_profile != requested:
                # Allow using full-profile cache when core is requested.
                if not (requested == "core" and cached_profile == "full"):
                    continue
            return _load_cached_workflow_state_payload(payload)
        except Exception:
            continue
    return None


def _load_cached_workflow_state(path_text: str) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"State JSON not found: {path}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("State JSON must be an object.")
    return _load_cached_workflow_state_payload(payload)


def _load_cached_workflow_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = dict(payload)
    final_report = None
    if isinstance(payload.get("final_report"), dict):
        final_report = assessment_report_from_dict(payload["final_report"])
    final_path = (
        payload.get("remediated_assessment_path")
        or payload.get("assessment_path")
        or payload.get("final_report_path")
    )
    if final_report is None and final_path:
        final_path_obj = Path(str(final_path)).expanduser()
        if final_path_obj.exists():
            final_report = load_assessment_report(final_path_obj)

    if final_report is None:
        raise ValueError(
            "Could not resolve final report from state JSON. "
            "Provide remediated_assessment_path/assessment_path or embedded final_report."
        )

    final_report = _drop_legacy_batch_info_metric(final_report)
    state["final_report"] = final_report
    if isinstance(payload.get("initial_report"), dict):
        try:
            state["initial_report"] = _drop_legacy_batch_info_metric(assessment_report_from_dict(payload["initial_report"]))
        except Exception:
            state["initial_report"] = None
    elif payload.get("assessment_path"):
        initial_path = Path(str(payload.get("assessment_path"))).expanduser()
        if initial_path.exists():
            try:
                state["initial_report"] = _drop_legacy_batch_info_metric(load_assessment_report(initial_path))
            except Exception:
                state["initial_report"] = None

    readiness_score = payload.get("readiness_score")
    if not isinstance(readiness_score, dict):
        score_path_str = payload.get("readiness_score_path")
        if score_path_str:
            score_path = Path(str(score_path_str)).expanduser()
            if score_path.exists():
                loaded = read_json(score_path)
                if isinstance(loaded, dict):
                    readiness_score = loaded
    if not isinstance(readiness_score, dict):
        source_tier = payload.get("source_tier", "tier1")
        readiness_score = compute_readiness_score(final_report, source_tier=source_tier)
    state["readiness_score"] = readiness_score
    state["source_tier"] = payload.get("source_tier", "tier1")
    state["source_availability"] = payload.get("source_availability") or {}
    state["primary_source"] = payload.get("primary_source") or ""

    source_assessments_payload = payload.get("source_assessments")
    source_assessments: dict[str, Any] = {}
    if isinstance(source_assessments_payload, dict):
        for source_name, item in source_assessments_payload.items():
            if not item:
                source_assessments[source_name] = None
                continue
            report_obj = None
            report_payload = item.get("report")
            if isinstance(report_payload, dict):
                try:
                    report_obj = _drop_legacy_batch_info_metric(assessment_report_from_dict(report_payload))
                except Exception:
                    report_obj = None
            source_assessments[source_name] = {
                "source": item.get("source", source_name),
                "source_tier": item.get("source_tier", "tier1"),
                "readiness_score": item.get("readiness_score", {}),
                "ingestion_summary": item.get("ingestion_summary", {}),
                "_report": report_obj,
            }
    if not source_assessments:
        fallback_source = str(payload.get("primary_source") or "datatable")
        source_assessments = {
            fallback_source: {
                "source": fallback_source,
                "source_tier": payload.get("source_tier", "tier1"),
                "readiness_score": readiness_score if isinstance(readiness_score, dict) else {},
                "ingestion_summary": final_report.ingestion_summary,
                "_report": final_report,
            }
        }
        state["primary_source"] = fallback_source
    state["source_assessments"] = source_assessments

    if not isinstance(state.get("bundle"), dict):
        state["bundle"] = {}
    if not isinstance(state.get("remediations"), list):
        state["remediations"] = []
    return state
