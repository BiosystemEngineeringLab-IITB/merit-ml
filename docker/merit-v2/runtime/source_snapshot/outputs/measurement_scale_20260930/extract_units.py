#!/usr/bin/python3
"""Declared measurement units per analysis, from the mwTab data-block UNITS line. 18 Sep 2026 frozen cache.
Revision 3 (2026-09-30): two independent axes, measurement type and processing state, instead of one category.

Extraction (unchanged from revision 2). For every study-analysis record (Figure 2B, 6,696), the analysis's mwTab file is
read (split on '\\n' only). For each non-EXTENDED data block '<BLOCK>_START', the units value is the text after the first
tab of the line starting with '<BLOCK>:UNITS' (e.g. 'MS_METABOLITE_DATA:UNITS<spaces><tab>Peak height'), stripped. If the
line is absent or the value is empty or an undeclared token ('-', 'NA', 'Not applicable', 'N/A (...)', ...), units are
BLANK. The declared units describe the analysis's mwTab matrix (the datatable is derived from it); they are NOT assumed to
describe untarg_data.

Classification of one declared value (all steps are regular expressions listed in TYPE_RULES / FLAG_RULES below):
  0. clean: strip; mis-encoded micro sign 'Âµ'/'Î¼' -> 'µ'; full-width parentheses -> ASCII; a number alone ('1', '13')
     -> blank; declarations listed in OVERRIDES take their recorded type (reason recorded).
  1. whole-value pre-emption: a value that is only m/z, m/Δm, Da, dalton, amu, ppm, ppb, that consists only of m/z and
     retention-time words ('m/z ratio', 'mz_rt', 'time_m/z'), or that states presence/absence -> other.
  2. 'm/z' and 'm/Δm' are removed from the text (so 'Peak area (m/z)' is an area, not molar).
  3. the text is split into parts at ' and ', ' or ', ' & ', ', ', ' except ' and at ') ' followed by more text.
     Each part is given the measurement type of the FIRST matching TYPE_RULE (priority order, T1 first):
        T1 other (rate)  >  T2a isotope labelling  >  T2-T4 relative/ratio  >  T5 amount/content (molar symbol per mass)
        >  T6-T7 concentration  >  T8 amount/content  >  T9 concentration (the word only, no unit)
        >  T10 intensity/area/counts
     A part that matches no rule is untyped (a modifier such as 'normalized' or 'blank subtracted').
  4. measurement type of the declaration: the single type of its typed parts; if the typed parts carry two or more
     different types -> mixed; if none is typed -> not stated (e.g. 'log transformed data', 'normalized').
     The word 'concentration' without a unit counts as relative/ratio when the value is also normalised or scaled.
     A bare 'relative <signal>' label without a normalised/scaled flag -> relative (unspecified) (BARE_RELATIVE).
  5. processing flags (independent, on the whole text): log-transformed, normalised, scaled, corrected, imputed,
     blank-subtracted.
  6. display category (Figure S7F); processing state takes precedence over measurement type in this order:
        mixed -> 'other/mixed'; log-transformed flag -> 'log-transformed'; relative (unspecified) -> 'relative (unspecified)';
        relative/ratio or isotope labelling -> 'normalised/relative';   (these fine categories are kept in the table)
        any measurement type with a normalised or scaled flag -> 'normalised/relative';
        intensity/area/counts (raw, corrected, imputed or blank-subtracted) -> 'intensity/area/counts';
        concentration -> 'concentration'; amount/content -> 'amount/content'; other or not stated -> 'other/mixed'.
  7. UNITS declaration family (Figure S7F): roll-up of the display category (FAMILY): intensity, area or counts;
     concentration or amount (concentration + amount/content); normalised, relative or ratio (normalised/relative +
     relative (unspecified)); log indicated; other or mixed; not declared. Families describe the declared text; they
     are not used to infer the numerical scale.
  Interpretation notes record every non-literal reading (e.g. lowercase 'um' read as µM, 'µMol' read as µmol).
Concentration = molarity, or amount/mass per volume. Amount/content = absolute amount, or amount/mass per mass, tissue,
protein, DNA, cells, vial, plate, sample or organ. Relative/ratio = dimensionless quantities (ratios, fractions,
percentages, fold changes, amount per amount of a reference compound such as creatinine or PC) and signal normalised to
an amount of material (e.g. 'area/ug protein'). Isotope labelling = enrichment, labelling fractions, isotopologue
distributions, light/heavy ratios. Relative (unspecified) = a bare 'relative abundance/intensity/area/units' label.
Mixed = two or more types in one declaration. Other = non-abundance declarations (m/z, Da, ppm, presence/absence) and
rates. Not stated = only processing words, or no recognisable quantity.
Outputs (this folder): units_per_analysis.tsv, units_raw_by_category.tsv (every distinct raw value with its revision-2
category, revision-3 type, flags, display category and note), units_summary.txt
"""
from __future__ import annotations

import collections
import csv
import re
from pathlib import Path

CACHE = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/mw-dump-latest-18thSep-2026-version")
VALIDITY = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit/manuscript/figures/Figure-2/Used-in-manuscript/"
                "figure2b_source_availability_per_analysis.tsv")
OUT = Path(__file__).resolve().parent
NOT_DECLARED = {"", "-", "--", "na", "n/a", "none", "not applicable", "not available", "unknown", "unspecified", "."}
NUMERIC_ONLY = re.compile(r"^\d+(?:\.\d+)?$")                        # '1', '13': a number, not a unit -> blank
NOT_DECLARED_PREFIX = re.compile(r"^(?:n/a|na|not applicable)\s*\(", re.I)   # 'N/A (structure elucidation)' -> blank
NOT_APPLICABLE = re.compile(r"^no[tn]?\s+applicable(?:\s+unit\w*)?\s*$", re.I)   # 'no applicable unites' -> blank

PREEMPT_OTHER = re.compile(
    r"^\s*(?:m/z|m/[δΔ]m|da|daltons?|amu|ppm|ppb)\s*$|presence|absence|"
    # only m/z, retention-time and filler words ('m/z ratio', 'm/z values', 'mz_rt', 'time_m/z', 'm/z-retention time features')
    r"^(?=.*(?<![a-z])m\s*/?\s*z(?![a-z]))\s*(?:(?:m\s*/?\s*z|rt|retention\s*time|time|underscore|features?|values?|ratios?)"
    r"[\s_\-]*)+$", re.I)
MZ = re.compile(r"m\s*/\s*(?:z|[δΔ]m)", re.I)
SPLIT = re.compile(r"\s+and\s+|\s+or\s+|\s*&\s*|,\s+|\s+except\s+|\)\s+(?=\S)", re.I)

AMT = r"(?:[mnupfaµμ]?mol(?:e|es|s)?|nnol|(?:milli|micro|nano|pico|femto|atto)mol(?:e|es|s)?)"   # amount (moles)
MASS = r"(?:[mnupfµμ]?g|(?:milli|micro|nano|pico|femto)?grams?)"                                 # mass
VOL = r"(?:[mnuµμd]?l|liters?|litres?)"                                                            # volume
# (measurement type, rule id, regex, case-sensitive?)
TYPE_RULES = [
    ("other", "T1 rate (per hour, minute or day; lowercase)", r"(?:/|\bper)\s*(?:hr|h|min|day)s?\b", True),
    ("isotope labelling", "T2a isotope labelling (enrichment, labelling fraction, isotopologue distribution)",
     r"enrichment|labell?ing|isotopo(?:mer|logue)|\bmpe\b|\bL/H\b", False),
    ("relative/ratio", "T2 relative terms",
     r"relative|\brel\b|\bratios?\b|\bfold|(?:\b|(?<=\d))fc\b|percent|%|\bpct\b|proportion|fraction|z-?score|\bmol ?%|mol percent", False),
    ("relative/ratio", "T3 amount per amount of a reference compound",
     rf"{AMT}(?:\s+\w{{1,3}})?\s*(?:/|per)\s*{AMT}|(?:/|per)\s*(?:\d+\s*)?{MASS}\s+creatinine", False),
    ("relative/ratio", "T4 signal per amount of material",
     r"(?:area|height|intensit|count|\bcpm|\bcps|\bauc|signal|abundan|arbitrary(?:\s+units?)?|\ba\.?u\.?)\w*\s*(?:/|per)\s*"
     r"(?:\d|[mnupfµμ]?g|ml|l\b|cells?|cfu|protein|tissue|dna|wet)", False),
    ("amount/content", "T5 molar symbol per mass (read as amount per mass)",
     r"(?<![A-Za-z])[munpfµμ]?M\s*/\s*(?:\d+\s*)?(?:[munpfµμ]?g|gm)(?![A-Za-z])", True),
    ("concentration", "T6 molarity",
     r"(?<![A-Za-z])[munpfµμ]M(?![A-Za-z])|molars?\b|^\s*um\s*$", True),
    ("concentration", "T7 amount or mass per volume",
     rf"(?i:(?:{AMT}|{MASS})(?:\s+\w{{1,3}})?\s*(?:/|per)\s*(?:\d[\d,.^*eE]*\s*)?{VOL}\b|/\s*I\b|"
     r"parts per (?:million|billion|trillion)|\bppbv\b)", True),
    ("amount/content", "T8 amount or mass (absolute, or per mass/tissue/protein/cells/vial/sample)",
     rf"(?<![A-Za-z]){AMT}(?![A-Za-z])|{AMT}\b|\b{MASS}\b|\b{MASS}(?=[/\s(]|$)", False),
    ("concentration", "T9 the word 'concentration' without a unit", r"concentration", False),
    ("intensity/area/counts", "T10 intensity, area, counts",
     r"area|height|hieght|intensit|intersity|intensiy|intesity|internsity|intentsity|abundan|abudan|abundace|absorbance|count|\bau\b|a\.u\.|\barb\w*|response|signal|\bion|"
     r"\bauc\b|^\s*peaks?\s*$|\bpeak values?\b|\bsi\b|\bcps\b|\bcpm\b|\bua\b|mv\s*\*\s*(?:min|s)\b|original\s*scale|\breading\b|^\s*peak results\s*$|^\s*quant\s*$", False),
]
TYPE_COMPILED = [(t, rid, re.compile(p) if cs else re.compile(p, re.I)) for t, rid, p, cs in TYPE_RULES]
FLAG_RULES = [
    ("log-transformed", r"\blog|\bln\b"),
    ("normalised", r"normali[sz]|normalisd|nomali[sz]|\bnorm\b|per (?:tic|total)|\btic-|\btss\b|tsp_normal|/\s*(?:is|istd|internal)\b|"
                   r"volnorm"),
    ("scaled", r"scaled|autoscal|pareto|z-?score"),
    ("corrected", r"correct"),
    ("imputed", r"imput"),
    ("blank-subtracted", r"blank.?sub|subs?tract"),
]
FLAG_COMPILED = [(f, re.compile(p, re.I)) for f, p in FLAG_RULES]
# Declarations that the rules cannot read correctly; each is listed with its reason (Table SX, 'Overrides').
OVERRIDES = {
    "Signal intensity normalized by IS for the metabolite signal/c13 carbon enrichment %": ("mixed",
        "names two reported quantities, a normalised metabolite signal and a 13C enrichment percentage"),
    "abundance & normalized peak area": ("mixed", "names two quantities, raw abundance and normalised peak area"),
    "uL(plasma), pmole/mg (muscle)": ("mixed", "plasma and muscle values are declared in different units"),
    "PI3P / 36:1PS": ("relative/ratio", "ratio of two lipid species"),
}
# A bare 'relative <signal>' label ('Relative abundance', 'relative intensity', 'Rel Abundance', 'relative units') does not say
# whether values were normalised: in MS usage 'relative' often means relative (non-absolute) quantification of raw signal.
BARE_RELATIVE = re.compile(r"^\s*rel(?:ative)?\.?\s+(?:abundances?|intensit(?:y|ies)|(?:peak\s+)?areas?|(?:ion\s+)?counts|units?|"
                           r"values?|peak\s+intensit(?:y|ies)|peak\s+heights?|signal\s+intensit(?:y|ies))\.?\s*$", re.I)
NOTES = [  # (regex on the cleaned value, note)
    (re.compile(r"^\s*um\s*$"), "lowercase 'um' read as µM (the same values are declared 'uM' in ST000544)"),
    (re.compile(r"/\s*I\b"), "capital 'I' read as litre ('pmoles/I')"),
    (re.compile(r"(?<![A-Za-z])[munpfµμ]?M\s*/\s*(?:\d+\s*)?(?:[munpfµμ]?g|gm)(?![A-Za-z])"),
     "molar symbol per mass (e.g. µM/mg) read as amount per mass"),
    (re.compile(r"(?<![A-Za-z])[munpµμ]Mol\b"), "'µMol'-type capitalisation read as an amount (µmol), not molarity"),
    (re.compile(r"^\s*concentrations?\s*$", re.I), "concentration declared without a unit"),
    (re.compile(r"extraction|nmr sample|in extraction vial", re.I), "concentration in the extract or NMR sample"),
    (re.compile(r"original\s*scale", re.I), "Metabolon 'original scale' = raw area counts"),
    (re.compile(r"^\s*N/L\s*$"), "unit not interpretable"),
    (BARE_RELATIVE, "bare 'relative' label: normalisation not stated (may denote relative quantification of raw signal)"),
    (re.compile(r"^\s*SI\s*$"), "ambiguous abbreviation: 'SI' read as signal intensity"),
    (re.compile(r"^\s*ua\s*$", re.I), "ambiguous abbreviation: 'ua' read as arbitrary units (unités arbitraires)"),
    (re.compile(r"^\s*(?:ABS|IU|SUC|MS|molp|TIC|s|A)\s*$"), "ambiguous abbreviation: not interpreted"),
    (re.compile(r"\branked\b", re.I), "'ranked' not interpreted; read as the underlying signal"),
    (re.compile(r"^\s*unitless\s*$", re.I), "declared as unitless"),
]
# UNITS declaration families (Figure S7F): a roll-up of the display categories. They describe the declared text only.
FAMILY = {"intensity/area/counts": "Intensity, area or counts", "concentration": "Concentration or amount",
          "amount/content": "Concentration or amount", "normalised/relative": "Normalised, relative or ratio",
          "relative (unspecified)": "Normalised, relative or ratio", "log-transformed": "Log indicated",
          "other/mixed": "Other or mixed", "": "Not declared"}


def clean(raw: str) -> str:
    t = raw.strip().replace("Âµ", "µ").replace("Î¼", "µ").replace("（", " (").replace("）", ")")
    undeclared = t.lower() in NOT_DECLARED or NOT_DECLARED_PREFIX.match(t) or NOT_APPLICABLE.match(t) or NUMERIC_ONLY.match(t)
    return "" if undeclared else t


def part_type(part: str) -> tuple[str, str] | None:
    for t, rid, rx in TYPE_COMPILED:
        if rx.search(part):
            return t, rid
    return None


def classify(raw: str) -> dict:
    """Measurement type, processing flags, display category, rules fired and notes for one declared value."""
    v = clean(raw)
    res = {"measurement_type": "", "processing_flags": "", "display_category": "", "rules": "", "note": ""}
    if not v:
        return res
    flags = [f for f, rx in FLAG_COMPILED if rx.search(v)]
    notes = [n for rx, n in NOTES if rx.search(v)]
    if v in OVERRIDES:
        mtype, why = OVERRIDES[v]
        rules = ["override"]
        notes.append(f"override: {why}")
    elif PREEMPT_OTHER.search(v):
        mtype, rules = "other", ["P1 non-abundance declaration"]
    else:
        text = MZ.sub(" ", v)
        typed = [pt for pt in (part_type(p) for p in SPLIT.split(text) if p.strip()) if pt]
        kinds = list(dict.fromkeys(t for t, _ in typed))
        rules = list(dict.fromkeys(r for _, r in typed))
        processed = bool({"normalised", "scaled"} & set(flags))
        if len(kinds) > 1:
            mtype = "mixed"
        elif kinds:
            mtype = kinds[0]
            if mtype == "relative/ratio" and BARE_RELATIVE.match(text) and not processed:
                mtype = "relative (unspecified)"
            if rules == ["T9 the word 'concentration' without a unit"] and processed:
                mtype = "relative/ratio"
                notes.append("'concentration' without a unit, normalised or scaled -> relative")
            if len(typed) > 1 and mtype in ("concentration", "amount/content"):
                notes.append("several units of the same type (unit varies across features or sample types)")
        else:
            mtype, rules = "not stated", ["no type rule (quantity not stated)"]
    if mtype == "mixed":
        disp = "other/mixed"
    elif "log-transformed" in flags:
        disp = "log-transformed"
    elif mtype == "relative (unspecified)":
        disp = "relative (unspecified)"
    elif mtype in ("relative/ratio", "isotope labelling") or {"normalised", "scaled"} & set(flags):
        disp = "normalised/relative"      # a stated normalisation or scaling takes precedence over the underlying quantity
    elif mtype in ("other", "not stated"):
        disp = "other/mixed"
    else:
        disp = mtype
    res.update(measurement_type=mtype, processing_flags="; ".join(flags), display_category=disp,
               rules=" + ".join(rules), note="; ".join(dict.fromkeys(notes)))
    return res


# Self-tests (value -> (measurement type, display category)); run on every execution, the script stops on a failure.
SELF_TESTS = {
    "Peak area": ("intensity/area/counts", "intensity/area/counts"), "Peak area (m/z)": ("intensity/area/counts", "intensity/area/counts"),
    "mV*min": ("intensity/area/counts", "intensity/area/counts"), "Metabolon original scale": ("intensity/area/counts", "intensity/area/counts"),
    "Corrected areas": ("intensity/area/counts", "intensity/area/counts"), "blank-subtracted peak area": ("intensity/area/counts", "intensity/area/counts"),
    "chromatographic areas with no units": ("intensity/area/counts", "intensity/area/counts"),
    "LC/MS Peak intensity": ("intensity/area/counts", "intensity/area/counts"),
    "Rel Abundance": ("relative (unspecified)", "relative (unspecified)"),
    "Relative abundance": ("relative (unspecified)", "relative (unspecified)"), "relative units": ("relative (unspecified)", "relative (unspecified)"),
    "relative abundance/mg tissue": ("relative/ratio", "normalised/relative"), "Relative Area": ("relative (unspecified)", "relative (unspecified)"),
    "normalized relative abundance": ("relative/ratio", "normalised/relative"), "log(relative intensity)": ("relative/ratio", "log-transformed"),
    "mole percent enrichment": ("isotope labelling", "normalised/relative"), "13C labeling fraction": ("isotope labelling", "normalised/relative"),
    "Mass isotopomer distributions": ("isotope labelling", "normalised/relative"), "unitless L/H ratio": ("isotope labelling", "normalised/relative"),
    "Signal intensity normalized by IS for the metabolite signal/c13 carbon enrichment %": ("mixed", "other/mixed"),
    "MS reading": ("intensity/area/counts", "intensity/area/counts"), "quant m/z": ("intensity/area/counts", "intensity/area/counts"),
    "13": ("", ""), "1": ("", ""), "Area Pct": ("relative/ratio", "normalised/relative"),
    "VolNormImp Area counts": ("intensity/area/counts", "normalised/relative"),
    "TIC-Corrected Peak Area": ("intensity/area/counts", "normalised/relative"),
    "Arbitrary unit/ug protein": ("relative/ratio", "normalised/relative"), "AUC/ngDNA": ("relative/ratio", "normalised/relative"),
    "pmol/umol PC": ("relative/ratio", "normalised/relative"),
    "umol/mg creatinine": ("relative/ratio", "normalised/relative"), "PI3P / 36:1PS": ("relative/ratio", "normalised/relative"),
    "mg/kg (relative quantification, NOT ABSOLUTE)": ("relative/ratio", "normalised/relative"),
    "Normalized Concentrations": ("relative/ratio", "normalised/relative"), "normalized": ("not stated", "normalised/relative"),
    "uM": ("concentration", "concentration"), "um": ("concentration", "concentration"), "ng/mL": ("concentration", "concentration"),
    "pmoles/I": ("concentration", "concentration"), "micro molar": ("concentration", "concentration"),
    "Estimated metabolite carbon concentration (nmol C per L)": ("concentration", "concentration"),
    "parts per billion by volume": ("concentration", "concentration"), "concentration": ("concentration", "concentration"),
    "nM (norm to 100ug/ml cell protein)": ("concentration", "normalised/relative"),
    "normalized conc. (uM/mg)": ("amount/content", "normalised/relative"),
    "nmol/g": ("amount/content", "amount/content"), "nmol/mg protein": ("amount/content", "amount/content"),
    "nano moles per vial": ("amount/content", "amount/content"), "nmol": ("amount/content", "amount/content"),
    "µMol": ("amount/content", "amount/content"), "µM/mg": ("amount/content", "amount/content"),
    "Concentration pmol/g": ("amount/content", "amount/content"), "umol g FW-1": ("amount/content", "amount/content"),
    "mg/g (fecal sample)": ("amount/content", "amount/content"),
    "Total nmoles (with relative protein adjustment)": ("relative/ratio", "normalised/relative"),
    "log2(area)": ("intensity/area/counts", "log-transformed"), "log2FC(peak area)": ("relative/ratio", "log-transformed"),
    "log10(nmol/ml)": ("concentration", "log-transformed"), "log transformed data": ("not stated", "log-transformed"),
    "urine uM and Tissue nnol/vial": ("mixed", "other/mixed"), "nM (STDs) pmol/g (analyte)": ("mixed", "other/mixed"),
    "umol/gDCW and Height ratio/gDCW": ("mixed", "other/mixed"), "pmol per mg (tissues) pmol per ml (plasma)": ("mixed", "other/mixed"),
    "ng/mL of plasma, except estradiol (in peak area) and testosterone (in peak area ratio)": ("mixed", "other/mixed"),
    "ng/ml or umol/L": ("concentration", "concentration"), "pg/hr/ug and g/hr for media": ("other", "other/mixed"),
    "ppm": ("other", "other/mixed"), "m/z": ("other", "other/mixed"), "Presence/Absence": ("other", "other/mixed"),
    "N/L": ("not stated", "other/mixed"), "N/A (structure elucidation)": ("", ""),
    "m/z ratio": ("other", "other/mixed"), "m/z values": ("other", "other/mixed"), "mz_rt": ("other", "other/mixed"),
    "time_m/z": ("other", "other/mixed"), "m/z-retention time features": ("other", "other/mixed"), "dalton": ("other", "other/mixed"),
    "retention time underscore m/z": ("other", "other/mixed"), "amu": ("other", "other/mixed"), "ratio": ("relative/ratio", "normalised/relative"),
    "abundance of specific m/z (amu)": ("intensity/area/counts", "intensity/area/counts"), "quant m/z": ("intensity/area/counts", "intensity/area/counts"),
    "no applicable unites": ("", ""), "Peak Intensiy": ("intensity/area/counts", "intensity/area/counts"),
    "intesity": ("intensity/area/counts", "intensity/area/counts"), "Peak hieght": ("intensity/area/counts", "intensity/area/counts"),
    "Nomalized Peak Intensities": ("intensity/area/counts", "normalised/relative"), "absorbance": ("intensity/area/counts", "intensity/area/counts"),
    "Peak Area/mL": ("relative/ratio", "normalised/relative"), "C12:C13 Ratio": ("relative/ratio", "normalised/relative"),
    "MS count (log2)": ("intensity/area/counts", "log-transformed"), "unitless": ("not stated", "other/mixed"),
}


def self_test() -> None:
    bad = {v: (classify(v)["measurement_type"], classify(v)["display_category"], exp) for v, exp in SELF_TESTS.items()
           if (classify(v)["measurement_type"], classify(v)["display_category"]) != exp}
    assert not bad, bad


def units_of(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "", ""
    lines = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")
    decl = {}
    for ln in lines:
        m = re.match(r"^([A-Z_]+_DATA):UNITS\b[^\t]*\t(.*)$", ln)
        if m:
            decl.setdefault(m.group(1), m.group(2).strip().strip("\r").strip())
    blocks = [ln.strip()[:-len("_START")] for ln in lines
              if ln.strip().endswith("_DATA_START") and not ln.strip().startswith("EXTENDED")]
    vals = [clean(decl.get(b, "")) for b in dict.fromkeys(blocks)]
    vals = [v for v in dict.fromkeys(vals) if v]
    return " | ".join(vals), "|".join(dict.fromkeys(blocks))


def main() -> None:
    self_test()
    recs = list(csv.DictReader(VALIDITY.open(), delimiter="\t"))
    assert len(recs) == 6696
    old = {(r["study_id"], r["analysis_id"]): r["units_category"]
           for r in csv.DictReader((OUT / "units_per_analysis_v2.tsv").open(), delimiter="\t")}
    rows = []
    for r in recs:
        st, an = r["study_id"], r["analysis_id"]
        raw, blocks = units_of(CACHE / st / an / "mwtab" / f"{an}_mwtab.txt")
        assert " | " not in raw                   # one declared value per analysis in this cache
        c = classify(raw)
        rows.append({"study_id": st, "analysis_id": an, "mwtab_blocks": blocks, "units_raw": raw,
                     "measurement_type": c["measurement_type"], "processing_flags": c["processing_flags"],
                     "units_category": c["display_category"], "units_family": FAMILY[c["display_category"]],
                     "units_category_v2": old.get((st, an), ""),
                     "rules_fired": c["rules"], "interpretation_note": c["note"]})
    with (OUT / "units_per_analysis.tsv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    distinct = collections.Counter(r["units_raw"] for r in rows if r["units_raw"])
    first = {}
    for r in rows:
        first.setdefault(r["units_raw"], r)
    keys = ["units_category_v2", "measurement_type", "processing_flags", "units_category", "rules_fired", "interpretation_note"]
    with (OUT / "units_raw_by_category.tsv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["units_raw", "analyses", "category_v2", "measurement_type", "processing_flags", "display_category",
                    "changed", "rules_fired", "interpretation_note", "declaration_family"])
        for v, n in sorted(distinct.items(), key=lambda x: (first[x[0]]["units_category"], -x[1], x[0])):
            f = first[v]
            w.writerow([v, n, f["units_category_v2"], f["measurement_type"], f["processing_flags"], f["units_category"],
                        "yes" if f["units_category_v2"] != f["units_category"] else "", f["rules_fired"],
                        f["interpretation_note"], f["units_family"]])
    cat = collections.Counter(r["units_category"] or "(blank)" for r in rows)
    typ = collections.Counter(r["measurement_type"] or "(blank)" for r in rows)
    flg = collections.Counter(f for r in rows for f in r["processing_flags"].split("; ") if f)
    moved = collections.Counter((r["units_category_v2"], r["units_category"]) for r in rows
                                if r["units_raw"] and r["units_category_v2"] != r["units_category"])
    txt = [f"analyses: {len(rows):,}; with a units value: {sum(1 for r in rows if r['units_raw']):,}; blank: {cat['(blank)']:,} "
           f"(of which no mwTab data block: {sum(1 for r in rows if not r['mwtab_blocks']):,}); distinct values: {len(distinct):,}",
           "", "UNITS declaration family (Figure S7F):"]
    famc = collections.Counter(r["units_family"] for r in rows)
    famd = collections.Counter(FAMILY[first[v]["units_category"]] for v in distinct)
    txt += [f"  {k:32s} {v:>6,} analyses  {famd.get(k, 0):>4,} distinct declarations" for k, v in famc.most_common()]
    txt += ["", "display category (fine):"] + [f"  {k:28s} {v:>6,}" for k, v in cat.most_common()]
    txt += ["", "measurement type:"] + [f"  {k:28s} {v:>6,}" for k, v in typ.most_common()]
    txt += ["", "processing flags (analyses):"] + [f"  {k:28s} {v:>6,}" for k, v in flg.most_common()]
    txt += ["", f"analyses whose display category changed from revision 2: {sum(moved.values()):,}"]
    txt += [f"  {a:24s} -> {b:24s} {n:>5,}" for (a, b), n in moved.most_common()]
    txt += ["", "TYPE RULES as run (priority order):"] + [f"  {rid} [{t}]{' (case-sensitive)' if cs else ''}: {p}"
                                                        for t, rid, p, cs in TYPE_RULES]
    txt += ["", "FLAG RULES:"] + [f"  {f}: {p}" for f, p in FLAG_RULES]
    txt += ["", "OVERRIDES:"] + [f"  {k!r} -> {t}: {why}" for k, (t, why) in OVERRIDES.items()]
    (OUT / "units_summary.txt").write_text("\n".join(txt) + "\n")
    print("\n".join(txt[:60]))


if __name__ == "__main__":
    main()
