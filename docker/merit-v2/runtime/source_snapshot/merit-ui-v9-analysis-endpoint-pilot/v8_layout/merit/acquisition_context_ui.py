"""Unscored per-analysis presentation of the five independent evidence tracks."""
from __future__ import annotations

import html
from collections import Counter
from typing import Any

from .acquisition_context import TRACK_TIPS, field_label, field_tip


HEADLINE_FIELDS = {
    "Mass-analyser capability": "resolution_class",
    "Acquisition wording": "acquisition_wording_any_section",
    "Scan-mode terms": "reported_scan_mode_evidence",
    "Repository marker": "repository_marker",
    "MS2 in linked raw files": "panredu_ms2_analysis_shortlist",
}
# The remaining reviewed columns stay in the downloadable evidence table.
EXPLANATION_FIELDS = (
    "resolution_class_basis", "analyser_family", "acquisition_wording_level",
    "untargeted_stated_in_sections", "targeted_stated_in_sections",
    "scan_mode_terms_detected", "scan_mode_matched_fields", "scan_mode_excerpts",
    "panredu_ms2_link_scope", "panredu_ms2_raw_file_coverage_pct",
)

OVERVIEW_WORDING_TIP = (
    "Flags come from acquisition-related mentions in the mwTab text file. "
    "Untargeted includes untargeted/non-targeted wording; targeted requires a phrase related to analysis "
    "or a study/project type declaration. The wording is read from deposited text "
    "and is not converted into an acquisition type. It is not independently verified ground truth "
    "or a confidence score. A mention may describe the broader study/project rather than this analysis; "
    "negation is not resolved. Both means both wordings were detected; it does not establish that both "
    "strategies were used. Counts refer to MS analysis records and may repeat shared study descriptions. "
    "No flag is assigned without detected wording. Analytical QC shows the wording scope and source sections."
)


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def info(text: str) -> str:
    return (
        "<span class='minfo' tabindex='0' role='note' aria-label='" + escape(text) + "'>"
        "<span class='minfo-icon' aria-hidden='true'>i</span>"
        "<span class='minfo-popup' style='text-align:left;white-space:normal;text-transform:none;"
        "font-weight:400;letter-spacing:normal'>" + escape(text) + "</span></span>"
    )


def field_details(row: dict[str, str]) -> str:
    items = []
    for field in EXPLANATION_FIELDS:
        value = row.get(field, "")
        rendered = escape(value) if value != "" else "<span style='color:#6f7e83'>Not recorded</span>"
        items.append(
            "<div style='padding:7px 0;border-bottom:1px solid rgba(19,35,39,.08)'>"
            "<dt data-context-field='" + escape(field) + "' style='font-size:.77rem;font-weight:600;color:#42585f'>"
            + escape(field_label(field)) + " " + info(field_tip(field))
            + "</dt>"
            + "<dd style='margin:3px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8rem'>"
            + rendered + "</dd></div>"
        )
    return (
        "<details class='acquisition-explanation' style='margin-top:12px'><summary style='cursor:pointer;font-size:.8rem;color:#225e73'>"
        "How this was read</summary><dl style='margin:6px 0 0'>"
        + "".join(items) + "</dl></details>"
    )


def track_value(row: dict[str, str], track: str) -> str:
    is_ms = row.get("modality") == "MS"
    if track == "Mass-analyser capability":
        return row.get("resolution_class") or ("Not applicable (NMR)" if row.get("modality") == "NMR" else "Not established")
    if track == "Repository marker":
        return {
            "valid untarg_data matrix and results-file declaration": "Valid untarg_data matrix + results-file declaration",
            "valid untarg_data matrix only": "Valid untarg_data matrix only",
            "results-file declaration only": "Results-file declaration only",
            "none": "No repository marker detected",
        }.get(row.get("repository_marker", ""), "Not recorded")
    if not is_ms:
        return "Not assessed (non-MS or modality not established)"
    if track == "Acquisition wording":
        return {"untargeted": "Untargeted wording only", "targeted": "Targeted wording only",
                "both": "Both wordings detected", "": "No targeted/untargeted wording detected"}.get(row.get("acquisition_wording_any_section", ""), "Not recorded")
    if track == "Scan-mode terms":
        return row.get("reported_scan_mode_evidence") or "Not recorded"
    return row.get("panredu_ms2_analysis_shortlist") or "Not established"


def _overview_parts(context: dict[str, Any] | None, tab_suffix: str) -> tuple[str, str] | None:
    """The mass-analyser line (with its link to Analytical QC) and the wording line, or None without MS records."""
    if not isinstance(context, dict):
        return None
    records = context.get("records") or []
    ms_records = [r for r in records if r.get("modality") == "MS"]
    classes = Counter(r.get("resolution_class") for r in ms_records)
    if not classes:
        return None
    labels = "; ".join(f"{n} {label}" for label, n in sorted(classes.items()))
    wording = Counter(r.get("acquisition_wording_any_section") for r in ms_records)
    flags = []
    for status, label in (("untargeted", "Untargeted wording only"),
                          ("targeted", "Targeted wording only"), ("both", "Both wordings detected")):
        if wording[status]:
            flags.append("<span class='acquisition-wording-flag' data-wording-status='" + status
                         + "' data-analysis-count='" + str(wording[status]) + "'>"
                         + escape(f"{wording[status]} {label}") + "</span>")
    wording_summary = ""
    if flags:
        detected = sum(wording[status] for status in ("untargeted", "targeted", "both"))
        wording_summary = (
            "<div class='acquisition-wording-summary' style='margin-top:7px'>"
            "<strong>Targeted/untargeted wording (mwTab text):</strong> " + "; ".join(flags) + " "
            + info(f"Wording detected in {detected} of {len(ms_records)} MS analysis records in this study. "
                   + OVERVIEW_WORDING_TIP) + "</div>"
        )
    mass_line = (
        "<strong>Mass-analyser context:</strong> " + escape(labels) + " "
        + info("Counts of MS study–analysis records across the entire study, including analyses without a usable matrix. Descriptive capability only; no result is changed.")
        + " <button type='button' class='acquisition-overview-link' style='border:0;background:none;color:#225e73;cursor:pointer;font:inherit;text-decoration:underline'"
        + " onclick='switchTab(&quot;analytical-" + escape(tab_suffix) + "&quot;,&quot;" + escape(tab_suffix)
        + "&quot;);var b=document.querySelector(&quot;#tab-analytical-" + escape(tab_suffix)
        + " .analytical-acquisition-context&quot;);if(b){b.scrollIntoView({block:&quot;start&quot;});window.scrollBy(0,-90)}'>View acquisition evidence in Analytical QC</button>"
    )
    return mass_line, wording_summary


def overview_html(context: dict[str, Any] | None, tab_suffix: str) -> str:
    parts = _overview_parts(context, tab_suffix)
    if parts is None:
        return ""
    mass_line, wording_summary = parts
    return (
        "<div class='acquisition-overview-context' style='margin:10px 0;padding:10px 12px;background:rgba(47,111,143,.06);"
        "border:1px solid rgba(47,111,143,.2);border-radius:12px;font-size:.82rem;"
        "width:min(100%,1100px,calc(100vw - 64px));box-sizing:border-box'>"
        + mass_line + wording_summary + "</div>"
    )


# ---------------------------------------------------------------------------------------------------------------------------------
# Derived context (Overview): labels MERIT-ML assigns from deposited text and fields, kept apart from the deposited study record
# (requested by the author on 2026-10-28). Study-design context comes from design_context/<study>.json (frozen labels of 1 June 2026).
# ---------------------------------------------------------------------------------------------------------------------------------
DESIGN_LABELS = {
    "experimental_or_replicate_based": "Experimental / replicate-based",
    "cohort_or_subject_based": "Cohort / subject-based",
    "metadata_only_or_no_usable_matrix": "Metadata only / no usable matrix",
    "unclear": "Unclear",
}
DERIVED_TIP = (
    "These labels are assigned by MERIT-ML from the deposited text and fields. They are not deposited by the study authors, are not "
    "checked against the authors' intent, and are not used by any score, alert or class-count condition."
)
DESIGN_TIP = (
    "One label per study, read from the title, summaries, subject block, sample sources, factors and sample-table structure. "
    "Two independent readings were made: a rule-based score of keywords and metadata fields, and a language-model reading of the "
    "same record. Where they agree, that label is kept; where they disagree, a fixed sequence of rules decides, or the study is "
    "left unclear. Cohort / subject-based: rows mostly represent different people or naturally occurring groups. Experimental / "
    "replicate-based: rows mostly represent controlled conditions, perturbations, time points or replicates of the same material."
)
_DECIDED = {
    "consensus_v9_v11": "the rule-based and language-model readings agreed",
    "B11_aud_high_over_llm_low": "the readings disagreed; the rule-based reading was confident and the language-model reading was not",
    "B11_llm_high_over_aud_low": "the readings disagreed; the language-model reading was confident and the rule-based reading was not",
    "B12_collision_high_high": "both readings were confident and disagreed, so the study is left unclear",
    "B13_aud_multi_signal": "the readings disagreed; the rule-based reading was supported by several kinds of evidence",
    "B14_llm_high_aud_weak": "the readings disagreed; the language-model reading was confident and the rule-based reading had at most one kind of evidence",
    "C_definitional_metadata_to_design": "the language-model reading found no usable design and the rule-based reading found one",
    "C_definitional_keep_metadata": "no usable design structure was found",
    "D_one_unclear_take_auditor": "the language-model reading was unclear, so the rule-based reading was used",
    "D_one_unclear_take_llm": "the rule-based reading was unclear, so the language-model reading was used",
    "E_fall_through": "no deciding evidence was found",
}
_REVIEW = {
    "isotope": "the stable-isotope tracer rule, which here matched isotope-labelled internal standards in the sample-preparation text "
               "rather than a tracer experiment",
    "genotype": "the filled GENOTYPE_STRAIN rule, which here matched a placeholder or an observed human genotype",
    "supplier": "the filled ANIMAL_ANIMAL_SUPPLIER rule, which here matched a placeholder value",
}


def _design_line(design: dict[str, Any]) -> str:
    label = DESIGN_LABELS.get(design.get("label", ""), "Unclear")
    confidence = str(design.get("confidence") or "").strip()
    decided_by = str(design.get("decided_by") or "")
    if decided_by in _DECIDED:
        how = _DECIDED[decided_by]
    else:                                                     # a deciding rule: its own reason, read from deposited fields
        how = "decided by a fixed rule: " + str(design.get("reason") or "").strip()
    types = [t.replace("_", " ") for t in design.get("evidence_types") or [] if t]
    types_html = (
        "<div class='derived-design-types' style='margin-top:4px;color:#51656a;font-size:.77rem'>Evidence types named by the "
        "language-model reading: " + escape(", ".join(types)) + "</div>" if types else ""
    )
    review = design.get("rule_review") or {}
    review_html = ""
    if review:
        review_html = (
            "<details class='derived-design-review' style='margin-top:5px;font-size:.77rem;color:#8a1c1c'>"
            "<summary style='cursor:pointer'>The rule that decided this label may not apply to this study</summary>"
            "<div style='margin:4px 0 0;line-height:1.45'>This label was decided by " + escape(_REVIEW.get(review.get("rule"), "a fixed rule"))
            + ". Without that rule, the label would be " + escape(DESIGN_LABELS.get(review.get("label_without_rule", ""), "Unclear"))
            + ". The label is kept as assigned so that it matches the counts reported in the article.</div></details>"
        )
    return (
        "<div class='derived-design' data-design-context='" + escape(design.get("label", "")) + "'>"
        "<strong>Study-design context:</strong> " + escape(label)
        + (" · " + escape(confidence) + " confidence" if confidence else "") + " " + info(DESIGN_TIP)
        + "<div style='margin-top:3px;color:#51656a;font-size:.77rem'>How it was assigned: " + escape(how) + ".</div>"
        + types_html + review_html + "</div>"
    )


def derived_context_html(design: dict[str, Any] | None, context: dict[str, Any] | None, tab_suffix: str) -> str:
    """One box with every MERIT-ML-assigned label of the study: study-design context, mass-analyser context, acquisition wording."""
    parts = _overview_parts(context, tab_suffix)
    rows = []
    if isinstance(design, dict) and design.get("label"):
        rows.append(_design_line(design))
    if parts is not None:
        mass_line, wording_summary = parts
        rows.append("<div class='derived-acquisition'>" + mass_line + wording_summary + "</div>")
    if not rows:
        return ""
    return (
        "<section class='derived-context' data-informational='true' style='margin:10px 0;padding:10px 12px;"
        "background:rgba(47,111,143,.06);border:1px solid rgba(47,111,143,.2);border-radius:12px;font-size:.82rem;"
        "width:min(100%,1100px,calc(100vw - 64px));box-sizing:border-box'>"
        "<div style='display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:0 0 8px'>"
        "<h4 style='margin:0;font-size:.85rem;text-transform:uppercase;letter-spacing:.05em'>Derived context</h4>"
        "<span style='font-size:.72rem;color:#51656a'>Labels assigned by MERIT-ML, not deposited fields · informational · not used by any score</span> "
        + info(DERIVED_TIP) + "</div>"
        "<div style='display:grid;gap:9px'>" + "".join(rows) + "</div></section>"
    )


def context_html(context: dict[str, Any] | None, source_key: str | None = None) -> str:
    """One table: a row per analysis, a column per evidence track, the reading details and the mwTab link at the end of the row."""
    if not isinstance(context, dict) or not context.get("records"):
        return ""
    records = context["records"]
    study_id = context.get("study_id", "")
    th = ("padding:9px 9px;font-size:.71rem;text-transform:uppercase;letter-spacing:.06em;color:#3f565b;border-bottom:2px solid rgba(19,35,39,.12);"
          "white-space:normal;vertical-align:bottom;background:rgba(47,111,143,.07);text-align:left")
    td = "padding:9px 9px;border-bottom:1px solid rgba(19,35,39,.06);font-size:.82rem;line-height:1.4;vertical-align:top;overflow-wrap:normal;word-break:normal"
    head = "<th style='" + th + "'>Analysis</th>" + "".join(
        "<th style='" + th + "' data-context-field='" + escape(HEADLINE_FIELDS[track]) + "'>" + escape(track) + " " + info(
            ("Primary classification. " if track == "Mass-analyser capability" else "Supporting evidence. ") + tip
            + (" Source: Pan-ReDU (redu.gnps2.org)." if track == "MS2 in linked raw files" else "")) + "</th>"
        for track, tip in TRACK_TIPS.items()) + "<th style='" + th + "'>How this was read</th><th style='" + th + ";white-space:nowrap'>Verify</th>"
    rows = ""
    for i, row in enumerate(records):
        aid = row["analysis_id"]
        bg = "rgba(245,241,232,.4)" if i % 2 == 0 else "transparent"
        metadata_url = "https://www.metabolomicsworkbench.org/rest/study/analysis_id/" + escape(aid) + "/mwtab/txt"
        rows += (
            "<tr class='acquisition-analysis' data-study-id='" + escape(study_id) + "' data-analysis-id='" + escape(aid) + "' style='background:" + bg + "'>"
            "<td style='" + td + ";font-weight:600;white-space:nowrap'>" + escape(aid) + "</td>"
            + "".join("<td class='acquisition-track' data-context-field='" + escape(HEADLINE_FIELDS[track]) + "' style='" + td + "'>"
                      + escape(track_value(row, track)) + "</td>" for track in TRACK_TIPS)
            + "<td style='" + td + ";min-width:150px'>" + field_details(row).replace("margin-top:12px", "margin-top:0").replace(">How this was read</summary>", ">details</summary>") + "</td>"
            "<td style='" + td + ";white-space:nowrap'><a href='" + metadata_url + "' target='_blank' rel='noopener noreferrer' "
            "title='Open the mwTab text file of this analysis on Metabolomics Workbench'>mwTab</a></td></tr>"
        )
    ms_count = sum(r.get("modality") == "MS" for r in records)
    return (
        "<section class='analytical-acquisition-context' data-informational='true' data-study-id='" + escape(study_id) + "' style='margin-bottom:24px;max-width:100%;box-sizing:border-box'>"
        "<h4 style='margin:0 0 8px;font-size:.9rem;text-transform:uppercase;letter-spacing:.05em'>Analytical platform and acquisition evidence "
        "<span style='font-size:.69rem;font-weight:500;text-transform:none;letter-spacing:0'>Informational · unscored · <span class='v9-direction' style='color:#1f5fbf;font-weight:600'>no better or worse direction</span></span></h4>"
        "<p style='font-size:.81rem;color:#51656a;line-height:1.5;margin:0 0 8px'>"
        + str(len(records)) + " study–analysis records (" + str(ms_count) + " MS). One record per analysis, the same on every endpoint; analyses without a valid matrix are included.</p>"
        "<p style='font-size:.8rem;color:#214e62;line-height:1.5;margin:0 0 12px'>"
        "No evidence is used to set another. The supporting evidence does not assign an acquisition type. "
        "HRMS-capable is not read as untargeted; nominal/unit-resolution is not read as targeted."
        "</p>"
        "<div style='overflow-x:auto;contain:inline-size;border:1px solid rgba(47,111,143,.2);border-radius:14px;background:rgba(255,255,255,.92)'>"
        "<table class='acquisition-table' style='width:100%;min-width:900px;border-collapse:collapse'><thead><tr>" + head + "</tr></thead><tbody>" + rows + "</tbody></table></div>"
        "</section>"
    )
