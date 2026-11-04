"""Overview "Data at a glance" (decided by the author on 2026-11-01): a plain-language summary per analysis that names the matrix to
download and says what to expect in it, built only from the records the page already shows (no new score, no threshold of its own).

Per analysis, two sections: 1. mwTab (the values as deposited), else the datatable when mwTab has no valid matrix; 2. untarg_data when
the analysis has a valid one. Each section states, for the matrix it names: ML-eligible sample IDs (and positions when an ID is
deposited more than once) and features; named-feature coverage, RefMet mapping and the other feature-label classes (Annotation tab);
the count requirement under the classes in force, how many sample IDs the chosen classes hold, a class choice that was not applied,
and the factors that vary in this matrix (study-wide factors constant here named); empty and zero cells; the low-information,
redundancy (caret flags: the features whose removal leaves no pair above the cut-off) and outlier counts (sample IDs or positions);
the checks not assessed and why, and the note of a large matrix not recomputed; the Structural items below 100 or not assessed;
class separability (Oh 2011); the features that differ between the classes with the test actually used (or why none was run); notes
(subject IDs: one value for every sample / confirmed / unconfirmed, p/n of the chosen classes, QC-like positions apart from positions
set aside by edits, units not declared, unequal classes, other factors, a batch-like field lined up with the classes); and a button that
builds the matrix with the classes in force from the deposit files when clicked (v9_matrix_export.ml_ready_zip). Revised after the
independent audit of 2026-11-01 (outputs/data_at_a_glance_independent_20261101); closed when a study is opened.
"""
from __future__ import annotations

from merit import pilot_analysis_endpoint as pe

_e = pe._e
OVERLAP_REF = "https://doi.org/10.1016/j.compbiomed.2010.12.006"


_pct = pe._share_pct        # one formatter for the summary and the readiness profile (recheck R06, 2 Nov 2026)


def _rec(block: list | None, aid: str, key: str) -> dict | None:
    for a in block or []:
        if a["analysis_id"] == aid:
            return ((a["endpoints"].get(key) or {}).get("rec")) or None
    return None


def _n(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _facts(sid: str, aid: str, key: str, ov_a: dict, ctx: dict) -> dict:
    """Every number the section states for one matrix, read from the page's own records."""
    ep = ov_a["endpoints"][key]; items = ep["items"]; prof = ep["profile"]
    lab = _rec(ctx["labels"], aid, key) or {}; ml = _rec(ctx["ml"], aid, key) or {}
    miss = _rec(ctx["missingness"], aid, key) or {}; li = _rec(ctx["low_information"], aid, key) or {}
    red = _rec(ctx["redundancy"], aid, key) or {}; out = _rec(ctx["outliers"], aid, key) or {}
    ann = _rec(ctx["annotation"], aid, key) or {}
    mi = miss.get("items") or {}
    zero = sum((mi.get(k) or {}).get("share", 0.0) for k in ("unknown_zero", "hidden", "replaced"))
    # positions by kind after the sample edits (the same function as Analytical QC and Adjust Matrix Properties)
    raw_ep = next(((a["endpoints"].get(key) or {}) for a in (ctx["raw"] or {}).get("analyses", []) if a["analysis_id"] == aid), {})
    kinds = pe.qb.position_kinds(raw_ep["roster"], ctx["overrides"], sid) if "roster" in raw_ep else []
    ann_row = next((r for r in ctx["annotations"] or [] if r["analysis_id"] == aid), {})
    return {"key": key, "label": dict(pe.SOURCES)[key], "eligible": lab.get("ml_eligible_samples") or 0, "features": prof.get("features") or 0,
            "named": (items.get("named_coverage") or {}).get("value"), "refmet": (items.get("refmet_mapping") or {}).get("value"),
            "label_classes": ann.get("class_counts") or {},
            "lab": lab, "task": ep["readiness"]["task"], "data": ep["readiness"]["data"],
            "alerts": {x["key"]: x["tier"] for x in ep["readiness"]["data"]["alerts"]},
            "empty": 100.0 * (mi.get("invalid") or {}).get("share", 0.0), "zero": 100.0 * zero,
            "hidden": (mi.get("hidden") or {}).get("cells", 0), "replaced": (mi.get("replaced") or {}).get("cells", 0),
            "li": li, "red": red, "out": out, "ml": ml, "prof": prof, "items": items,
            "controls": sum(kinds.count(k) for k in ("qc", "blank", "standard")), "edited_out": kinds.count("set_aside") + kinds.count("excluded"),
            "eligible_positions": kinds.count("sample"),
            "batch": ((ann_row.get("rec") or {}).get("batch_like_label") or {}),
            "struct": {k: (items.get(k) or {}).get("value") for k, _, _ in pe._OV_STRUCT}}


def _goto(text: str) -> str:
    return f"<a href='#' class='v9-ovg-goto v9-rec-goto' title='Open the class, fold and alert settings of the readiness profile'>{_e(text)}</a>"


def _labels_text(f: dict) -> str:
    """Named-feature coverage, then what the other feature labels are (the Annotation tab's label classes)."""
    others = sorted(((c, n) for c, n in f["label_classes"].items() if c not in pe._NAMED_CLASSES and n), key=lambda x: -x[1])
    kinds = ", ".join(f"{pe._LABEL_CLASS_WORDS.get(c, c.replace('_', ' '))} ({n:,})" for c, n in others[:3]) + (", …" if len(others) > 3 else "")
    if f["named"] is None:
        return "Named-feature coverage could not be assessed."
    if f["named"] == 0:
        return "None of the features carries a metabolite name" + (f"; their labels are: {kinds}." if kinds else ".")
    return (f"{pe._floor1(f['named'])}% of the features carry a metabolite name"
            + (f", and {pe._floor1(f['refmet'])}% of those named features have a RefMet name." if f["refmet"] is not None
               else "; RefMet mapping could not be assessed.")
            + (f" The other labels are: {kinds}." if kinds and f["named"] < 100 else ""))


def _section(f: dict, ctx: dict, params: dict | None, why: str, same_as: str | None = None) -> str:
    k = pe.lb.folds(params); rho, kf = pe._rd_cut_settings(params)
    lab, t, ml, prof = f["lab"], f["task"], f["ml"], f["prof"]
    P = []
    head = f"<p style='margin:2px 0 6px;font-size:.78rem;color:#51656a'>{_e(why)}</p>"
    # 0. nothing to assess: no ML-eligible sample (every claim below would describe zero cells)
    if not f["eligible"]:
        parts = ([_n(f["controls"], "QC-, blank- or standard-like position")] if f["controls"] else []) + \
                ([_n(f["edited_out"], "position") + " set aside or left out by your edits"] if f["edited_out"] else [])
        return head + "<p style='margin:0 0 6px'>" + _e(
            f"No ML-eligible sample in this matrix ({f['features']:,} features" + ("; " + "; ".join(parts) if parts else "") + "): "
            "nothing in it can be assessed, and there is nothing to download.") + "</p>"
    # 1. the matrix
    ids = f"{f['eligible']:,} ML-eligible sample IDs"
    if f["eligible_positions"] and f["eligible_positions"] != f["eligible"]:
        ids += f" ({f['eligible_positions']:,} positions in the file: some sample IDs are deposited more than once)"
    P.append(_e(f"{ids} and {f['features']:,} features. {_labels_text(f)}"))
    # 2. classes and factors (the factors are those of this matrix; a factor can vary in the study and be constant here)
    msg = ctx.get("message")
    under = ("Under the deposited labels (the whole factor string" + (f", {lab['joint_classes']:,} class{'' if lab['joint_classes'] == 1 else 'es'})" if lab.get("joint_classes") else ")")
             if ctx["choice"] is None else f"Under the chosen classes ({pe.lb.choice_text(ctx['choice'])})")
    labelled = sum((lab.get("classes") or {}).values())
    if t["result"] == "met":
        cnt = (f"the count requirement for stratified {k}-fold cross-validation is met: the smallest class has "
               f"{t['smallest']:,} ML-eligible sample IDs.")
    elif t["result"] == "not met" and (t.get("n_classes") or 0) < 2:
        cnt = "there is only one class, so there is nothing to separate."
    elif t["result"] == "not met":
        cnt = (f"the count requirement is not met: the smallest class has {_n(t['smallest'], 'ML-eligible sample ID')}, and stratified "
               f"{k}-fold cross-validation needs at least {k}.")
    else:
        cnt = f"the class counts cannot be assessed ({t.get('reason') or 'no labelled ML-eligible sample'})."
    member = ""
    if labelled < f["eligible"] and (labelled or ctx["choice"] is not None):     # 0 of n is stated too (recheck R01)
        outside = []
        if lab.get("left_out"):
            outside.append(f"{lab['left_out']:,} in other classes")
        if lab.get("without_label"):
            outside.append(f"{lab['without_label']:,} without a label")
        if lab.get("without_picked_factor"):
            outside.append(f"{lab['without_picked_factor']:,} without the chosen factor")
        member = (f" {labelled:,} of the {f['eligible']:,} ML-eligible sample IDs are in these classes"
                  + (f" ({'; '.join(outside)})" if outside else "") + ".")
    local = {x: v for x, v in (lab.get("factors") or {}).items()}
    vary = [x for x, v in local.items() if (v.get("n_values") or len(v.get("values") or {})) >= 2]
    study_vary = [x for x, v in (ctx["items"].get("factors") or {}).items() if len(v) >= 2]
    const_here = [x for x in study_vary if x not in vary]
    fac = (f" In this matrix the factor strings hold {len(vary)} varying factor{'s' if len(vary) != 1 else ''}: {', '.join(vary)}. You may "
           "choose one of them or a combination; this summary and the readiness profile then follow your choice."
           if vary else " No factor varies across the samples of this matrix, so no other classes can be chosen here.")
    if const_here:
        fac += f" {', '.join(const_here)} {'varies' if len(const_here) == 1 else 'vary'} in the study but {'has' if len(const_here) == 1 else 'have'} one value here."
    note = (f"<b style='color:{pe._RD_AMBER}'>{_e('Your class choice was not applied: ' + msg + '.')}</b> " if msg else "")
    if same_as:      # the second section of an analysis: the same classes with the same counts as the first
        P.append(note + _e(f"Classes: as for {same_as} ({cnt[0].lower() + cnt[1:].rstrip('.')}).") + " " + _goto("Adjust classes or folds"))
    else:
        P.append(note + _e(f"{under}, {cnt}{member}") + " " + _goto("Adjust classes or folds") + _e(fac) + (" " + _goto("Choose here") if vary else ""))
    # 3. analytical checks: each one stated, or why it was not assessed
    tag = lambda key: (f" <b style='color:{pe._RED if f['alerts'][key] == 'extreme' else pe._RD_AMBER}'>({f['alerts'][key]} alert)</b>"
                       if key in f["alerts"] else "")
    said = lambda text, key: _e(text.rstrip(".")) + tag(key) + "."      # the alert inside the sentence, before its full stop
    A = []
    na = [(key, name) for key, name in pe.rd.ALERT_METRICS if (f["items"].get(key) or {}).get("value") is None]
    if "missingness" not in dict(na):
        if f["empty"] or f["zero"]:
            parts = ([f"{_pct(f['empty'])} of the cells are empty or non-numeric"] if f["empty"] else []) + ([f"{_pct(f['zero'])} are zeros"] if f["zero"] else [])
            A.append(said(" and ".join(parts) + ".", "missingness"))
            if f["zero"]:
                extra = ""
                if f["hidden"] or f["replaced"]:
                    extra = (f" In this datatable, {f['hidden']:,} zeros stand where the paired mwTab has no value and {f['replaced']:,} where it "
                             "has another value.")
                A.append(_e("Zeros are reported apart from empty cells because a zero in a metabolomics table can mean a true absence, a value "
                            "below the detection limit, a peak that was not detected or integrated, or a missing value written as 0; the file "
                            "does not say which." + extra))
        else:
            A.append(said("No cell of the ML-eligible samples is empty, non-numeric or zero.", "missingness"))
    li = f["li"]
    if li.get("flagged"):
        A.append(said(f"{_pct(100 * li['flagged'] / li['judged'])} of the features ({li['flagged']:,} of {li['judged']:,}) are constant or "
                      "nearly constant; they carry little information for a model and are often removed, inside the training folds.", "low_information"))
    red = f["red"]
    if red.get("redundant"):
        A.append(said(f"{_pct(100 * red['redundant'] / red['features_evaluated'])} of the evaluated features are redundant (Spearman |ρ| > {rho:.2f}). "
                      "Strongly correlated features "
                      "carry overlapping information, which can make models less stable and feature importance harder to read.", "redundancy"))
    out = f["out"]
    if out.get("outliers"):
        unit = "sample IDs" if out["samples_evaluated"] == f["eligible"] else "sample positions"
        A.append(said(f"{out['outliers']:,} of {out['samples_evaluated']:,} {unit} ({_pct(100 * out['outliers'] / out['samples_evaluated'])}) {'is an outlier' if out['outliers'] == 1 else 'are outliers'} "
                      f"(median value beyond {kf:g} × IQR); worth checking for handling or preparation issues before modelling, though they may "
                      "also be real biology.", "outliers"))
    if na:
        A.append(_e(f"{len(pe.rd.ALERT_METRICS) - len(na)} of the {len(pe.rd.ALERT_METRICS)} analytical checks could be assessed; not assessed: "
                    + "; ".join(f"{name.lower()} ({(f['items'].get(key) or {}).get('reason') or 'not assessable'})" for key, name in na) + "."))
    for r in (li, red, out):
        if r.get("note"):
            A.append(_e(r["note"]))
    P.append(" ".join(A))
    # 4. structure: an item is complete only when assessed at 100
    s = f["struct"]; below, unassessed = [], []
    for key, _, name in pe._OV_STRUCT:
        if s.get(key) is None:
            unassessed.append(f"{name.lower()} ({(f['items'].get(key) or {}).get('reason') or 'not assessable'})")
    if s.get("sample_support") is not None and s["sample_support"] < 100:
        below.append(f"fewer than {int(float((params or {}).get('structural_sample_support_n', 20)))} ML-eligible samples")
    if s.get("samples_with_factor") is not None and s["samples_with_factor"] < 100:
        below.append(f"{pe._floor1(s['samples_with_factor'])}% of the ML-eligible samples have a factor label")
    if s.get("numeric_values") is not None and s["numeric_values"] < 100:
        below.append("some value cells hold text instead of numbers")
    if s.get("unique_sample_ids") is not None and s["unique_sample_ids"] < 100:
        below.append("some sample IDs are repeated")
    support = int(float((params or {}).get("structural_sample_support_n", 20)))
    if not below and not unassessed:
        st = (f"All four Structural items reach 100: every ML-eligible sample has a factor label, no value cell holds text (empty cells are "
              f"counted under missing entries), no sample ID is repeated, and there are at least {support} ML-eligible samples (a reference "
              "count, not a measure of statistical power).")
    else:
        st = ("Structural items below 100: " + "; ".join(below) + "." if below else "") + \
             ((" " if below else "") + "Not assessed: " + "; ".join(unassessed) + "." if unassessed else "")
    P.append(_e(st))
    # 5. separability and associated features (the test and its reasons as the ML Task Readiness tab gives them)
    if ml.get("score") is not None:
        sep = (f"Class separability is {pe._floor1(ml['score'])} (chance level {pe._floor1(ml['chance'])}): it measures how much the classes "
               "overlap among each sample's nearest neighbours")
        P.append(_e(sep) + f" (<a href='{OVERLAP_REF}' target='_blank' rel='noopener'>Oh 2011</a>)" +
                 _e("; higher means less overlap. It is not an estimate of predictive accuracy."))
    else:
        P.append(_e(f"Class separability is not assessed: {ml.get('not_assessed') or 'not available for these classes'}."))
    disc = ml.get("discriminative") or {}
    q = ml.get("fdr") or 0.05
    test = disc.get("test") or "Kruskal–Wallis test"
    if disc.get("judged") and not disc.get("significant"):
        P.append(_e(f"No tested feature differs clearly between the classes ({disc['judged']:,} tested; {test}, Benjamini–Hochberg q < {q:g})."))
    elif disc.get("judged"):
        txt = (f"{disc['significant']:,} of the {disc['judged']:,} tested features ({pe._floor1(disc['share'] or 0)}%) differ between the classes "
               f"({test}, Benjamini–Hochberg q < {q:g}). A difference does not show that a feature predicts the class in new samples, or causes it.")
        top = (disc.get("top") or [])[:5]
        if top:
            txt += " Top " + str(len(top)) + " (smallest q): " + "; ".join(f"{x.get('name')} (highest in {x.get('highest')}, q = {x['q']:.2g})" for x in top) + "."
        P.append(_e(txt))
    elif ml.get("score") is not None or disc:
        why = (disc.get("not_assessable")
               or (f"no feature has at least {disc.get('min_pairs', 6)} subjects whose two samples differ (non-zero pairs, {disc.get('test') or 'exact paired test'})"
                   if disc.get("adjusted") and disc.get("design") == "paired"
                   else "too few subjects have a sample ID in every class" if disc.get("adjusted") and disc.get("design") == "blocked"
                   else "no feature has at least 5 values in every class with values that differ"))      # as the detailed tab (recheck R02)
        P.append(_e("Features that differ between the classes are not tested here: " + why + "."))
    # 6. notes
    N = []
    subj, shared, top_s = ml.get("subjects"), prof.get("subjects_with_several_samples") or 0, prof.get("most_shared_subject") or {}
    if shared and subj == 1 and prof.get("subjects_confirmed"):
        N.append(f"Every sample comes from one confirmed subject ('{top_s.get('subject', '')}'), so a model cannot be tested on subjects it has "
                 "not seen; cross-validation here only tests new samples from the same subject.")
    elif shared and subj == 1:
        N.append(f"Every sample with a subject ID has the same one ('{top_s.get('subject', '')}'): this usually names a species, cell line or "
                 "group, not a subject. Check it in Adjust Matrix Properties before grouping samples by it.")
    elif shared and prof.get("subjects_confirmed"):
        N.append(f"{shared:,} confirmed subjects have several samples: keep each subject's samples in the same fold (grouped cross-validation); "
                 "otherwise performance can be overestimated.")
    elif shared:
        N.append(f"{shared:,} subject IDs are shared by several samples in the deposit. If they are real subjects (confirm them in Adjust Matrix "
                 "Properties), keep each subject's samples in the same fold (grouped cross-validation); otherwise performance can be overestimated.")
    n_task = labelled          # the sample IDs in the classes in force; 0 = no task here, so no p/n advice (recheck R01)
    if f["features"] > n_task > 0:
        N.append(f"There are {f['features']:,} features for the {n_task:,} sample IDs " + ("in these classes " if labelled < f["eligible"] else "")
                 + f"(p/n {f['features'] / n_task:.1f}): use regularisation, or select features inside each cross-validation fold.")
    if f["controls"]:
        N.append(f"{_n(f['controls'], 'QC-, blank- or standard-like position')} {'is' if f['controls'] == 1 else 'are'} set aside from the ML-eligible "
                 "samples; they can be used to check drift when their run order is known.")
    if f["edited_out"]:
        N.append(f"{_n(f['edited_out'], 'position')} {'is' if f['edited_out'] == 1 else 'are'} set aside or left out by your sample edits.")
    u = f["items"].get("units") or {}
    if u.get("value") is not None and not u.get("flag"):
        N.append("The units of the values are not declared in the deposit; check them before combining this matrix with others.")
    if (lab.get("n_classes") or 0) >= 2 and lab.get("smallest") != lab.get("largest"):
        N.append(f"The classes differ in size (smallest {lab['smallest']:,}, largest {lab['largest']:,}): use stratified folds and a "
                 "balanced metric such as balanced accuracy.")
    of = lab.get("other_factors") or {}
    diff = [x for x, v in (of.get("factors") or {}).items() if v["state"] in ("values absent", "different")]
    if diff:
        N.append(f"Other factors also differ between your classes ({', '.join(diff)}); a difference between the classes may partly come from them.")
    in_force = set((ctx["choice"] or {}).get("factors") or vary)
    conf = [c["factor"] for c in (f["batch"].get("confounded") or []) if c["factor"] in in_force]
    if conf and f["batch"].get("pre_calculated_from"):
        N.append(f"The batch-like field '{f['batch']['pre_calculated_from']}' lines up with {', '.join(conf)}: each batch holds one value, so a "
                 "difference between these classes cannot be told apart from a batch difference.")
    notes = ("<ul style='margin:4px 0 0 18px;padding:0'>" + "".join(f"<li>{_e(x)}</li>" for x in N) + "</ul>") if N else ""
    rows_note = ("one row per ML-eligible position (its place in the file is in the position column)"
                 + ("; a sample ID deposited more than once gives several rows" if f["eligible_positions"] != f["eligible"] else "")
                 + ("; rows outside the chosen classes have an empty class" if labelled < f["eligible"] else ""))
    dl = (f"<button type='button' class='v9-rec-dl' data-analysis='{_e(ctx['aid'])}' data-endpoint='{f['key']}' "
          "style='font:inherit;font-size:.8rem;font-weight:700;padding:4px 12px;border-radius:10px;border:1px solid rgba(13,110,110,.4);"
          f"background:rgba(13,110,110,.08);color:#0d6e6e;cursor:pointer'>Download the {_e(f['label'])} matrix (CSV, classes in force)</button>"
          "<span style='font-size:.74rem;color:#51656a;margin-left:6px'>" + _e(f"Sample ID, position, subject ID, class in force and factor string, "
          f"then the features as deposited: {rows_note}. Built from the deposit files when you click, with your class choice and sample edits.") + "</span>")
    return (head + "".join(f"<p style='margin:0 0 6px'>{x}</p>" for x in P)
            + (f"<div style='margin:0 0 8px'><b style='font-size:.8rem'>Notes</b>{notes}</div>" if notes else "") + f"<div>{dl}</div>")


def recommendation_html(study_id: str, params: dict | None = None, overrides: dict | None = None) -> str:
    sid = str(study_id or "").strip().upper()
    ov = pe._overview_data(sid, params, overrides)
    if ov is None:
        return ""
    lbd = pe._labels_study(sid, params, overrides) or {}
    ctx = {"labels": lbd.get("analyses"), "items": lbd.get("items") or {}, "choice": lbd.get("choice"), "message": lbd.get("message") or "",
           "raw": pe._raw_study(sid), "overrides": overrides, "annotation": pe._annotation_study(sid), "annotations": pe._annotations_study(sid, overrides),
           "ml": (pe._ml_study(sid, params, overrides) or {}).get("analyses"),
           "missingness": pe._missingness_study(sid, overrides), "low_information": pe._low_information_study(sid, params, overrides),
           "redundancy": pe._redundancy_study(sid, params, overrides), "outliers": pe._outlier_study(sid, params, overrides)}
    cards = []
    for i, a in enumerate(ov["analyses"]):
        aid = a["analysis_id"]; ctx["aid"] = aid
        valid = {k for k, _ in pe.SOURCES if (a["endpoints"].get(k) or {}).get("readiness")}
        t1 = "mwtab" if "mwtab" in valid else "datatable" if "datatable" in valid else None
        if t1 is None and "untarg_data" not in valid:
            cards.append(f"<details class='v9-rec-card'><summary><b>{_e(aid)}</b>: no valid matrix to recommend</summary></details>")
            continue
        why1 = ("mwTab holds the values as deposited; the datatable is derived from it, and its zeros can hide a missing value or replace a "
                "real one." if t1 == "mwtab" else "mwTab has no valid matrix for this analysis, so the datatable is recommended.")
        sec1 = (f"<h5 class='v9-rec-h'>1. {_e(dict(pe.SOURCES)[t1])}</h5>" + _section(_facts(sid, aid, t1, a, ctx), ctx, params, why1)
                if t1 else "<h5 class='v9-rec-h'>1. mwTab or datatable</h5><p>No valid mwTab or datatable matrix for this analysis.</p>")
        f2 = _facts(sid, aid, "untarg_data", a, ctx) if "untarg_data" in valid else None
        # "as for mwTab" only when the classes and every class count are the same (not only the smallest class and the result)
        same = (dict(pe.SOURCES)[t1] if t1 and f2 and a["endpoints"][t1]["readiness"]["task"]["result"] == f2["task"]["result"]
                and (_rec(ctx["labels"], aid, t1) or {}).get("classes") == f2["lab"].get("classes") else None)
        sec2 = (f"<h5 class='v9-rec-h'>2. untarg_data</h5>"
                + _section(f2, ctx, params, "The untargeted feature table deposited for the same analysis.", same)
                if "untarg_data" in valid else "<h5 class='v9-rec-h'>2. untarg_data</h5><p style='margin:0'>No untargeted (untarg_data) matrix for this analysis.</p>")
        head = f"<b>{_e(aid)}</b>: download " + (f"{_e(dict(pe.SOURCES)[t1])}" if t1 else "") + (" and untarg_data" if "untarg_data" in valid else "")
        cards.append(f"<details class='v9-rec-card'{' open' if i == 0 else ''}><summary>{head}</summary><div class='v9-rec-body'>{sec1}{sec2}</div></details>")
    css = ("<style>.v9-rec-sum{cursor:pointer}.v9-rec{margin:0 0 16px;padding:12px 14px;border:1px solid rgba(13,110,110,.25);border-radius:14px;background:rgba(13,110,110,.04)}"
           ".v9-rec-card{margin:6px 0;border:1px solid rgba(19,35,39,.12);border-radius:10px;background:#fff}.v9-rec-card>summary{cursor:pointer;padding:7px 10px;font-size:.88rem}"
           ".v9-rec-body{padding:2px 12px 10px;font-size:.84rem;line-height:1.5;color:#132327}.v9-rec-h{margin:10px 0 2px;font-size:.84rem;color:#113e52}</style>")
    script = ("<script>(function(){var box=document.currentScript.parentElement;"
              "box.addEventListener('click',function(e){var g=e.target.closest('.v9-rec-goto');if(g){e.preventDefault();"
              "var panel=box.closest('.tab-panel')||document;var r=[].slice.call(panel.querySelectorAll('.v9-rd')).filter(function(x){return x.offsetParent!==null;})[0];"
              "if(!r)return;['.v9-rd-open','.v9-rd-cut-open'].forEach(function(s){var o=r.querySelector(s);if(o&&o.textContent.trim()==='Adjust here')o.click();});"
              "var c=r.querySelector('.v9-rd-chips');(c||r).scrollIntoView({block:'start',behavior:'smooth'});return;}"
              "var b=e.target.closest('.v9-rec-dl');if(!b)return;var src=document.getElementById('run-form');var f=document.createElement('form');"
              "f.method='post';f.action='/download/ml-ready-matrix';f.style.display='none';"
              "function add(n,v){var i=document.createElement('input');i.type='hidden';i.name=n;i.value=v;f.appendChild(i);}"
              "if(src){[].slice.call(src.elements).concat([].slice.call(document.querySelectorAll('[form=run-form]'))).forEach(function(el){"
              "if(el.name&&!el.disabled&&(el.type!=='checkbox'||el.checked))add(el.name,el.value);});}"
              f"add('study_id','{sid}');add('analysis_id',b.getAttribute('data-analysis'));add('endpoint',b.getAttribute('data-endpoint'));"
              "document.body.appendChild(f);f.submit();setTimeout(function(){f.remove();},1000);});})();</script>")
    lead = ("For each analysis: the matrix to download (mwTab, else the datatable) and, when deposited, the untarg_data matrix, with what to "
            "expect in each. Only these matrices are described here; the readiness profile below covers every endpoint. Everything follows "
            "the classes, folds, sample edits and alert cut-offs in force.")
    # closed when a study is opened (author, 2026-11-01); kept open across Apply for the same study only (this browser tab)
    keep = ("<script>(function(){var d=document.currentScript.parentElement,K='merit-glance-open',s='" + sid + "';"
            "try{if(sessionStorage.getItem(K)===s)d.open=true;}catch(e){}"
            "d.addEventListener('toggle',function(){try{if(d.open)sessionStorage.setItem(K,s);else sessionStorage.removeItem(K);}catch(e){}});})();</script>")
    return (f"<details class='v9-rec'>{css}<summary class='v9-rec-sum'><h4 style='display:inline;margin:0;font-size:.95rem;text-transform:uppercase;letter-spacing:.06em'>Data at a glance <span style='font-size:.78rem;font-weight:500;text-transform:none;letter-spacing:0;color:#51656a'>(a plain-language summary of the readiness profile)</span></h4></summary>"
            f"<p style='margin:6px 0 6px;font-size:.82rem;color:#51656a'>{_e(lead)}</p>" + "".join(cards) + script + keep + "</details>")
