#!/usr/bin/python3
"""Reviewer 1, Comment 1: per-analysis evidence on mass-analyser class and acquisition type. 18 Sep 2026 frozen cache.

One row per study-analysis record (6,696; Figure 2B). Every item that can be read for an analysis is recorded, with
its source, so that coverage can be reported honestly. Nothing is inferred by hand.

Inputs (read only)
  mw-dump-latest-18thSep-2026-version/<ST>/<AN>/mwtab/<AN>_mwtab.txt      instrument, analysis type, free text, raw files
  merit/manuscript/figures/Figure-2/Used-in-manuscript/figure2b_source_availability_per_analysis.tsv   source validity
  outputs/annotation_coverage_20260928/class_counts_per_analysis.tsv       named / without-name feature labels
  all_sampleinformation.tsv (Pan-ReDU export)                               raw-file instrument, MS2 count, acquisition

Blocks of columns (rules in RULES.md, generated from the constants below)
  A  analyser class       from MS:INSTRUMENT_TYPE and MS:INSTRUMENT_NAME, classified separately and then combined:
                          HRMS-capable / Nominal/unit-resolution / Indeterminate; NMR and 'no mwTab file' kept apart
  B  declared acquisition 'untargeted' / 'targeted' wording, by mwTab section; MRM/SRM and DDA/DIA wording
  C  resolution statement MS:RESOLUTION_SETTING, MS:MASS_ACCURACY, free-text resolution with a number
  D  repository markers   results-file declaration in the mwTab file, source validity, share of named labels
  E  Pan-ReDU             study level, and analysis level through raw-file names listed in the mwTab file
  G  MS2 shortlist        linked-file evidence, shared-file flags, and a conservative analysis-level shortlist
  I  scan-mode terms      MRM/SRM, PRM, SIM, full scan/MS1, DDA, DIA terms in the AN, CH and MS fields, with the matched
                          field and an excerpt (scan_mode_evidence.py); a description, not an acquisition type
Outputs (this folder): Reviewer1_Comment1_analysis_evidence.xlsx, per_analysis_evidence.tsv, coverage.tsv,
instrument_strings.tsv, RULES.md, panredu_ms2_distribution.tsv, panredu_ms2_per_analysis.tsv,
panredu_ms2_analysis_shortlist.tsv
"""
from __future__ import annotations

import collections
import hashlib
import os
import re
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_mode_evidence as SM  # noqa: E402  (block I: reported scan-mode evidence)

ROOT = Path("/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot")
CACHE = ROOT / "mw-dump-latest-18thSep-2026-version"
VALIDITY = ROOT / "merit/manuscript/figures/Figure-2/Used-in-manuscript/figure2b_source_availability_per_analysis.tsv"
LABELS = ROOT / "outputs/annotation_coverage_20260928/class_counts_per_analysis.tsv"
REDU = ROOT / "all_sampleinformation.tsv"
OUT = Path(__file__).resolve().parent
HR, NOM, IND = "HRMS-capable", "Nominal/unit-resolution", "Indeterminate"

# ---------------------------------------------------------------- A. analyser class
# Instrument TYPE (MS:INSTRUMENT_TYPE), lower case. First match wins, in this order.
TYPE_HYBRID_HR = r"orb\w*ap.*ion trap|ion trap.*orb\w*ap"          # one hybrid instrument (ion trap + Orbitrap)
TYPE_HR = (r"orbitrap|orbritrap|exactive|exploris|\bidx\b|ft-?icr|\bftms\b|ic-ftms|ltq-ft|q-?\s?tof|qtof|triple tof|"
           r"\bttof\b|tims|trapped ion mobility|it-tof")
TYPE_TOF = r"tof|time of flight"                    # plain TOF, GC-TOF, MALDI-TOF, CE-TOF, PTR-TOF: capability depends on the model
TYPE_NOM = (r"triple quad|\bqqq\b|7000d|single quad|q-?trap|ion trap|gc-itq|gc-q-ms|qp2020|\bmsd\b")
TYPE_FAMILY = [("Orbitrap", r"orbitrap|orbritrap|exactive|exploris|\bidx\b"), ("FT-ICR / FT-MS", r"ft-?icr|\bftms\b|ic-ftms|ltq-ft"),
               ("IT-TOF", r"it-tof"), ("Q-TOF / TripleTOF / timsTOF", r"q-?\s?tof|qtof|triple tof|\bttof\b|tims|trapped ion mobility"),
               ("GC-TOF", r"gc\s*x\s*gc|gc-?tof"), ("MALDI-TOF", r"maldi"), ("TOF", r"tof|time of flight"),
               ("Triple quadrupole", r"triple quad|\bqqq\b|7000d"), ("QTRAP", r"q-?trap|linear ion trap"),
               ("Single quadrupole", r"single quad|gc-q-ms|qp2020|\bmsd\b"), ("Ion trap", r"ion trap|gc-itq")]
# Instrument NAME (MS:INSTRUMENT_NAME), lower case, tested per instrument named in the field.
NAME_HR = (r"orbitrap|exactive|exploris|lumos|\bfusion\b|id-?x\b|ft-?icr|solarix|ltq[- ]?ft|\bftms\b|"
           r"q-?\s?tof|qtof|triple\s?tof|\b[56]600\+?|tims\s?tof|synapt|xevo[- ]?g2|\bvion\b|\bimpact\b|maxis|micr?otof|"
           r"\b65[1-6]\d\b|\b72[05]0\b|x500|zeno\s?tof|it-?tof|"
           r"\b62[1-3]\d\b|g1969|g3250|\blct\b|accutof|pegasus.*hrt|gct premier")
NAME_TOF = r"tof|time of flight"                    # a TOF is named but the model does not settle its resolution
NAME_UNIT_GCTOF = r"pegasus|leco gc-?tof"            # LECO Pegasus III / IV / HT / 4D / BT: unit-mass TOF (HRT is caught by NAME_HR first)
NAME_NOM = [("QTRAP", r"q-?\s?trap"),
            ("Triple quadrupole", r"\btsq\b|xevo[- ]?tq|\btq-?(?:s|d|xs)\b|qqq|triple\s?quad|\b64[1-9]\d\b|\b70[01]0[a-d]?\b|"
                                  r"api\s?[2-5]000|quattro|altis|quantiva|vantage|endura|lcms-?80[3-6]0"),
            ("Triple quadrupole or QTRAP (Sciex model number)", r"sciex.*\b[3-7]500\+?"),
            ("Single quadrupole", r"\b597[357][a-c]?\b|ms_5975|\bmsd\b(?!\s*tof)|\bisq\b|\bdsq\b|qp-?20[125]0|single\s?quad"),
            ("Ion trap", r"\bltq\b|\blcq\b|\bitq\b|ion\s?trap|amazon|\bhct\b")]
NAME_SPLIT = r"\s*;\s*|\s+and\s+|\s*&\s*|,\s+"       # several instruments in one field
UNDECLARED = {"", "-", "other", "several", "multiple", "unspecified", "not supplied", "none", "new", "n/a", "na"}


def type_class(itype: str) -> tuple[str, str]:
    """(class, family) of an instrument TYPE: HR / NOM / TOF (unresolved) / MIXED / IND."""
    t = itype.strip().lower()
    if t in UNDECLARED:
        return "IND", ""
    fam = next((f for f, rx in TYPE_FAMILY if re.search(rx, t)), "")
    if re.search(TYPE_HYBRID_HR, t):
        return "HR", "Orbitrap"
    hr, nom = bool(re.search(TYPE_HR, t)), bool(re.search(TYPE_NOM, t))
    if hr and nom:
        return "MIXED", fam
    if hr:
        return "HR", fam
    if re.search(TYPE_TOF, t):
        return "TOF", fam
    return ("NOM", fam) if nom else ("IND", "")


def one_name_class(part: str) -> tuple[str, str]:
    p = part.strip().lower()
    if re.search(NAME_HR, p):
        return "HR", "high-resolution model"
    if re.search(NAME_UNIT_GCTOF, p):
        return "NOM", "GC-TOF (unit mass)"
    for fam, rx in NAME_NOM:
        if re.search(rx, p):
            return "NOM", fam
    if re.search(NAME_TOF, p):
        return "TOF", "TOF (model not informative)"
    return "IND", ""


def name_class(iname: str) -> tuple[str, str]:
    """(class, family) of an instrument NAME: HR / NOM / TOF (unresolved) / MIXED / IND."""
    hits = [one_name_class(p) for p in re.split(NAME_SPLIT, iname) if p.strip()]
    firm = [h for h in hits if h[0] in ("HR", "NOM")]
    if len({h[0] for h in firm}) > 1:
        return "MIXED", "; ".join(dict.fromkeys(h[1] for h in firm))
    if firm:
        return firm[0][0], "; ".join(dict.fromkeys(h[1] for h in firm))
    tof = [h for h in hits if h[0] == "TOF"]
    return ("TOF", tof[0][1]) if tof else ("IND", "")


def combine(itype: str, iname: str) -> tuple[str, str, str]:
    """(resolution class, basis, analyser family) from the TYPE and NAME classes. A class is given only when a
    high-resolution or a nominal analyser is positively named and the other field does not contradict it."""
    (tc, tf), (nc, nf) = type_class(itype), name_class(iname)
    fam = tf or nf
    if "MIXED" in (tc, nc):
        return IND, "more than one instrument class named", fam
    if tc == "HR":
        return (IND, "type and name disagree", fam) if nc == "NOM" else (HR, "type and name agree" if nc == "HR" else "type only", fam)
    if tc == "NOM":
        return (IND, "type and name disagree", fam) if nc in ("HR", "TOF") else (NOM, "type and name agree" if nc == "NOM" else "type only", fam)
    if tc == "TOF":
        if nc == "HR":
            return HR, "TOF type, high-resolution model named", fam
        if nc == "NOM":
            return (NOM, "TOF type, unit-mass GC-TOF model named", "GC-TOF (unit mass)") if nf == "GC-TOF (unit mass)" \
                else (IND, "type and name disagree", fam)
        return IND, "TOF type, model not informative", fam
    if nc == "HR":
        return HR, "name only", nf
    if nc == "NOM":
        return NOM, "name only", nf
    if nc == "TOF":
        return IND, "TOF named, model not informative", nf
    return IND, "no analyser stated", ""


SELF_TESTS = {
    ("Orbitrap", "Thermo Q Exactive Orbitrap"): HR, ("QTOF", "Agilent 6545 QTOF"): HR, ("Triple TOF", "ABI Sciex 5600 TripleTOF"): HR,
    ("FT-ICR", "Bruker Solarix FT-ICR-MS"): HR, ("LTQ-FT", "Thermo LTQ-FT Ultra"): HR, ("IT-TOF", "Shimadzu LCMS-IT-TOF"): HR,
    ("orbitrap and ion trap", "Thermo Orbitrap ID-X tribrid"): HR, ("Orbritrap/ion trap", "Thermo Orbitrap ID-X Tribrid"): HR, ("TOF", "Agilent 6230 TOF"): HR, ("Other", "Agilent CE-TOFMS"): IND, ("TOF", "Agilent CE-TOFMS"): IND,
    ("CE-TOF", "Agilent 6210 TOF"): HR, ("QTOF", "Bruker maXis Impact qTOF"): HR, ("Single quadrupole", "Bruker Autoflex TOF"): IND,
    ("GC-TOF", "Agilent 7200 Q-TOF"): HR, ("TOF", "Leco Pegasus HRT+"): HR, ("", "Thermo Q Exactive Focus"): HR,
    ("Triple quadrupole", "Agilent 6490 QQQ"): NOM, ("Single quadrupole", "Agilent 5975C"): NOM, ("QTRAP", "ABI Sciex 5500 QTrap"): NOM,
    ("Ion trap", "Thermo LTQ XL"): NOM, ("GC-ITQ", "Thermo ITQ"): NOM, ("GC-TOF", "Leco Pegasus IV TOF"): NOM,
    ("GC x GC-TOF", "Leco Pegasus 4D GCxGC TOF"): NOM, ("Single quadrupole", "Thermo LTQ XL"): NOM, ("Other", "Waters Xevo-TQ-S"): NOM,
    ("QTRAP", "ABI Sciex 6500+"): NOM, ("hybrid triple quadrupole/linear ion trap", ""): NOM,
    ("TOF", "Bruker Autoflex speed TOF/TOF"): IND, ("GC-TOF", "Agilent 7890A"): IND, ("Single quadrupole", "Thermo Q Exactive Orbitrap"): IND,
    ("QTRAP", "Waters Xevo-G2-S"): IND, ("GC-TOF", "Agilent 5977B"): IND, ("Triple quadrupole/Orbitrap", ""): IND,
    ("Other", "Thermo Velos LTQ Orbitrap; Thermo Trace DSQ"): IND, ("", ""): IND, ("-", "-"): IND, ("GC-MS", ""): IND,
    ("Ion trap", "Thermo Orbitrap Exploris 480"): IND, ("Other", "Agilent G3250AA LC/MSD TOF system"): HR,
}

# ---------------------------------------------------------------- B. declared acquisition (free text)
UNTARGETED = r"\b(?:un[- ]?targeted|non[- ]?targeted)\b"
TARGETED_STRICT = (r"(?<![\w-])targeted\s+(?:\w+[\s-]+){0,2}(?:metabol\w*|lipid\w*|analys\w*|assay\w*|quanti\w*|profil\w*|method\w*|"
                   r"approach\w*|panel\w*|ms\b|lc\b|gc\b|mass spec\w*|measure\w*|detect\w*|mrm|srm)|"
                   r"(?:metabol\w+|lipid\w+|analys\w+|assay|quantif\w+|profiling)\s+(?:was|were|is|are)?\s*(?<![\w-])targeted\b")
TARGETED_LOOSE = r"(?<![\w-])targeted\b"
TYPE_FIELDS = ("ST:STUDY_TYPE", "PR:PROJECT_TYPE")     # dedicated type fields: any "targeted" there is an acquisition statement
UN_BRACKET = r"\(un\)\s?targeted"                    # "(un)targeted MS" = untargeted and targeted
MRM = r"\b(?:mrm|srm|multiple reaction monitoring|selected reaction monitoring|parallel reaction monitoring|prm)\b"
DDA_DIA = r"\b(?:dda|dia|data[- ]dependent|data[- ]independent|swath|ms/ms|ms2|msms|tandem)\b"
ANALYSIS_SECTIONS, STUDY_SECTIONS = ("AN", "CH", "MS"), ("PR", "ST", "SU", "CO", "TR", "SP")
RESOLUTION = (r"(?:resolution|resolving power)[^.;\n]{0,60}?(?:\d[\d,. ]*\d|\d)\s*(?:k\b|,000|000)?|"
              r"(?:\d[\d,. ]*\d|\d)\s*(?:k\b)?[^.;\n]{0,25}?(?:resolution|resolving power)")
RAW_FILE = r"RAW_FILE_NAME[^=]*=\s*([^;|\t]+)"
# UNITS values that are not a declaration (same rule as the units analysis): placeholder words, a number alone,
# 'N/A (...)' and misspelt 'not applicable'.
UNITS_PLACEHOLDER = (r"^(?:|-|--|na|n/a|none|not applicable|not available|unknown|unspecified|\.|\d+(?:\.\d+)?|"
                     r"(?:n/a|na|not applicable)\s*\(.*|no[tn]?\s+applicable(?:\s+unit\w*)?)$")
# The mwTab line naming the untargeted results file; a '#...' prefix before the key occurs in a few files.
RESULTS_LINE = r"^(?:#\S*\s+)?(MS:MS|NM:NMR)_RESULTS_FILE\b"

# ---------------------------------------------------------------- F. acquisition evidence profile (MS analyses only)
# The profile DESCRIBES two inputs; it does not assign an acquisition type. Analyser class is not used.
#   wording : acquisition-related wording of block B ("untargeted", "targeted", "both", "" = none)
#   marker  : repository marker of block D = a results-file declaration in the mwTab file and/or a retrieved valid
#             untarg_data matrix (True / False)
# PROFILE is the only place where the profiles are defined: the lookup, the reporting order, the coverage rows, the
# workbook sheet and the table in RULES.md are all generated from it.
PROFILE = [  # (wording, marker present, profile label), in reporting order
    ("untargeted", True, "Untargeted wording; repository marker present"),
    ("", True, "No targeted/untargeted wording; repository marker present"),
    ("untargeted", False, "Untargeted wording; repository marker absent"),
    ("targeted", False, "Targeted wording; repository marker absent"),
    ("targeted", True, "Targeted wording; repository marker present"),
    ("both", True, "Both targeted and untargeted wording detected"),
    ("both", False, "Both targeted and untargeted wording detected"),
    ("", False, "Neither wording nor repository marker detected"),
]
PROFILE_LOOKUP = {(w, m): lab for w, m, lab in PROFILE}
PROFILE_ORDER = list(dict.fromkeys(lab for _, _, lab in PROFILE))
WORDING_TEXT = {"untargeted": "untargeted only", "targeted": "targeted only", "both": "untargeted and targeted", "": "none"}
# Independent statement of the expected result for all eight (wording, marker) combinations.
PROFILE_TESTS = {
    ("untargeted", True): "Untargeted wording; repository marker present",
    ("untargeted", False): "Untargeted wording; repository marker absent",
    ("targeted", True): "Targeted wording; repository marker present",
    ("targeted", False): "Targeted wording; repository marker absent",
    ("both", True): "Both targeted and untargeted wording detected",
    ("both", False): "Both targeted and untargeted wording detected",
    ("", True): "No targeted/untargeted wording; repository marker present",
    ("", False): "Neither wording nor repository marker detected",
}


def evidence_profile(wording: str, marker: bool) -> str:
    return PROFILE_LOOKUP[(wording, bool(marker))]


def no_cross_inference_test() -> None:
    """The analyser class and the acquisition columns must not feed each other."""
    import inspect
    assert list(inspect.signature(combine).parameters) == ["itype", "iname"]            # class: instrument fields only
    assert list(inspect.signature(evidence_profile).parameters) == ["wording", "marker"]  # profile: wording + marker only
    assert list(inspect.signature(SM.scan_mode_evidence).parameters) == ["selected", "full"]  # scan-mode: two term flags only
    assert set(SM.NOT_SEARCHED) == {"MS:INSTRUMENT_TYPE", "MS:INSTRUMENT_NAME"}       # instrument fields are not searched
    def record(itype: str, iname: str, study_text: str) -> dict:
        f = {"MS:INSTRUMENT_TYPE": itype, "MS:INSTRUMENT_NAME": iname, "ST:STUDY_SUMMARY": study_text}
        sec = collections.defaultdict(str, {"MS": f"{itype} {iname}", "ST": study_text})
        return {"fields": f, "sections": sec, "results": {}}
    keys = ("acquisition_wording_any_section", "acquisition_wording_analysis_sections", "untargeted_stated_in_sections",
            "targeted_stated_in_sections")
    for text in ("Untargeted metabolomics of plasma.", "Targeted metabolomics of bile acids.", "Plasma samples were analysed."):
        a = text_block(record("Orbitrap", "Thermo Q Exactive Orbitrap", text))      # HRMS-capable instrument
        b = text_block(record("Triple quadrupole", "Agilent 6490 QQQ", text))       # nominal instrument
        assert all(a[k] == b[k] for k in keys), (text, a, b)                         # same text -> same wording
    # an HRMS-capable instrument with no wording must give no wording, and the class must not depend on the text
    assert text_block(record("Orbitrap", "Thermo Q Exactive Orbitrap", "Plasma samples were analysed."))["acquisition_wording_any_section"] == ""
    assert combine("Orbitrap", "Thermo Q Exactive Orbitrap")[0] == HR and combine("Triple quadrupole", "Agilent 6490 QQQ")[0] == NOM


def profile_self_test() -> None:
    combos = {(w, m) for w in ("untargeted", "targeted", "both", "") for m in (True, False)}
    assert set(PROFILE_LOOKUP) == combos == set(PROFILE_TESTS), "the eight (wording, marker) combinations must each be defined once"
    assert len(PROFILE) == 8, "a combination is defined twice"
    bad = {k: evidence_profile(*k) for k, exp in PROFILE_TESTS.items() if evidence_profile(*k) != exp}
    assert not bad, bad


# ---------------------------------------------------------------- E. Pan-ReDU
REDU_MISSING = {"missing value", "", "not specified", "not applicable"}
REDU_COLUMNS = ["ATTRIBUTE_DatasetAccession", "filename", "MassSpectrometer", "IonizationSourceAndPolarity", "ChromatographyAndPhase",
                "classification", "MS2spectra_count"]
RAW_EXTENSION = r"\.(raw|mzml|mzxml|d|wiff|cdf|zip|gz|lcd|qgd)$"   # the one extension removed from a raw-file name
REDU_FILE_EXTENSION = r"\.([a-z0-9]+)$"                             # extension of a Pan-ReDU file name (lower case)
DATA_BLOCK_START = ("MS_METABOLITE_DATA", "NMR_METABOLITE_DATA", "NMR_BINNED_DATA")   # first line of the quantitative block
MS2_CALLS = {"DDA", "DIA-SWATH", "DIA OR TARGETED", "inconclusive (limited MS2)"}


def norm_raw(name: str) -> str:
    # A trailing slash is removed first: vendor raw data can be a directory ("folder/49.D/"), and its name is the last part.
    b = os.path.basename(name.strip().replace("\\", "/").rstrip("/")).lower()
    if b.startswith("f."):
        b = b[2:]
    return re.sub(RAW_EXTENSION, "", b)


def ms2_status(states: collections.Counter, lenient: bool) -> str:
    present = states["present"]
    absent = states["absent"] + (states["zero_unclassified_open"] if lenient else 0)
    return "both" if present and absent else "present" if present else "absent" if absent else ""


MS2_EVIDENCE_ORDER = ["MS2 detected", "MS2 detected + no-MS2 files", "No MS2 reported in all matched files",
                      "No-MS2 calls; other files unresolved", "Matched files; MS2 status unresolved", "No matching files"]
MS2_SHORTLIST_ORDER = ["MS2 detected", "No MS2 reported; complete file-roster match",
                       "MS2 absence assessed in a subset of files", "Shared-file evidence only",
                       "MS2 status unresolved", "No matching files"]


def ms2_shortlist_labels(present: int, absent: int, unresolved: int, matched: int, declared: int, shared: int) -> tuple[str, str, str]:
    """MS2 evidence describes files; the shortlist withholds analysis assignment when file links are shared."""
    assert min(present, absent, unresolved, matched, declared, shared) >= 0
    assert shared <= matched <= declared
    if not matched:
        assert present + absent + unresolved == 0
        return "No matching files", "No matching files", "No matching files"
    assert present + absent + unresolved > 0
    evidence = ("MS2 detected + no-MS2 files" if present and absent else "MS2 detected" if present else
                "No-MS2 calls; other files unresolved" if absent and unresolved else
                "No MS2 reported in all matched files" if absent else "Matched files; MS2 status unresolved")
    scope = "Shared across analyses" if shared else "Analysis-specific filename match"
    shortlist = ("Shared-file evidence only" if shared else "MS2 detected" if present else
                 "No MS2 reported; complete file-roster match" if absent and not unresolved and matched == declared else
                 "MS2 absence assessed in a subset of files" if absent and not unresolved else "MS2 status unresolved")
    return evidence, scope, shortlist


# Each tuple checks a materially different conclusion, including asymmetric presence/absence evidence.
# Inputs: (present, absent, unresolved, matched, declared, shared) -> (evidence, scope, shortlist)
MS2_TESTS = [
    ((0, 0, 0, 0, 3, 0), ("No matching files", "No matching files", "No matching files")),
    ((1, 0, 2, 3, 4, 0), ("MS2 detected", "Analysis-specific filename match", "MS2 detected")),
    ((1, 2, 0, 3, 3, 0), ("MS2 detected + no-MS2 files", "Analysis-specific filename match", "MS2 detected")),
    ((0, 3, 0, 3, 3, 0), ("No MS2 reported in all matched files", "Analysis-specific filename match", "No MS2 reported; complete file-roster match")),
    ((0, 2, 0, 2, 3, 0), ("No MS2 reported in all matched files", "Analysis-specific filename match", "MS2 absence assessed in a subset of files")),
    ((0, 2, 1, 3, 3, 0), ("No-MS2 calls; other files unresolved", "Analysis-specific filename match", "MS2 status unresolved")),
    ((0, 0, 3, 3, 3, 0), ("Matched files; MS2 status unresolved", "Analysis-specific filename match", "MS2 status unresolved")),
    ((1, 0, 0, 1, 1, 1), ("MS2 detected", "Shared across analyses", "Shared-file evidence only")),
    ((0, 2, 0, 2, 2, 1), ("No MS2 reported in all matched files", "Shared across analyses", "Shared-file evidence only")),
]
NORM_RAW_TESTS = {"Folder/49.D/": "49", "Folder\\Sample_01.raw": "sample_01", "f.QC1.mzML": "qc1", ".D": "", "a.b.wiff": "a.b",
                  "Sample 3": "sample 3", "run7.d": "run7", "": ""}


def ms2_shortlist_self_test() -> None:
    bad = {k: norm_raw(k) for k, want in NORM_RAW_TESTS.items() if norm_raw(k) != want}
    assert not bad, bad
    for inputs, expected in MS2_TESTS:
        assert ms2_shortlist_labels(*inputs) == expected, (inputs, expected)


def add_ms2_shortlist(d: pd.DataFrame, redu: pd.DataFrame, raw_sets: dict) -> pd.DataFrame:
    # Count owners of every filename, including partial roster overlap; study identifiers remain part of the key.
    owners: dict = collections.defaultdict(set)
    for (study, analysis), names in raw_sets.items():
        for name in names:
            owners[(study, name)].add(analysis)
    available: dict = collections.defaultdict(set)
    for study, name in zip(redu.ATTRIBUTE_DatasetAccession, redu.base):
        available[study].add(name)
    added = []
    for row in d.itertuples():
        if row.modality != "MS":
            added.append({c: "" for c in ("panredu_ms2_evidence_status", "panredu_ms2_link_scope", "panredu_ms2_raw_file_coverage_pct",
                                          "panredu_ms2_shared_raw_files", "panredu_ms2_analysis_shortlist")})
            continue
        names = raw_sets.get((row.study_id, row.analysis_id), set())
        matched = names & available[row.study_id]
        shared = sum(len(owners[(row.study_id, name)]) > 1 for name in matched)
        count = lambda c: int(float(getattr(row, c) or 0))
        present, absent = count("redu_analysis_files_ms2_present"), count("redu_analysis_files_no_ms2")
        unresolved = count("redu_analysis_files_zero_count_unclassified") + count("redu_analysis_files_not_determined")
        evidence, scope, shortlist = ms2_shortlist_labels(present, absent, unresolved, len(matched), len(names), shared)
        added.append({"panredu_ms2_evidence_status": evidence, "panredu_ms2_link_scope": scope,
                      "panredu_ms2_raw_file_coverage_pct": round(100 * len(matched) / len(names), 1) if names else "",
                      "panredu_ms2_shared_raw_files": shared, "panredu_ms2_analysis_shortlist": shortlist})
    return pd.concat([d, pd.DataFrame(added, index=d.index)], axis=1)


def read_mwtab(path: Path) -> dict:
    out = {"has_mwtab": path.is_file()}
    if not out["has_mwtab"]:
        return out
    sec: dict = collections.defaultdict(str)
    field: dict = collections.defaultdict(str)
    raw: set = set()
    results: dict = {}
    in_data = False
    for ln in path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n"):
        # Stop at the quantitative block. A '#END' line is NOT a stop: some files begin with an empty template that
        # ends in '#END', followed by the real record.
        # (in two files the results-file line follows the data block, so that line is looked for in the whole file).
        if ln.startswith(DATA_BLOCK_START):
            in_data = True
        rl = re.match(RESULTS_LINE, ln) if "RESULTS_FILE" in ln else None
        if rl and not results:
            results = {"kind": "NMR results file" if rl.group(1) == "NM:NMR" else "MS results file"}
            for a in ln.split("\t")[2:]:
                if ":" in a:
                    k, val = a.split(":", 1)
                    results[k.strip().lower()] = val.strip()
        if in_data:
            continue
        if ln.startswith("SUBJECT_SAMPLE_FACTORS"):
            raw.update(norm_raw(m) for m in re.findall(RAW_FILE, ln, flags=re.I))
            continue
        if len(ln) > 3 and ln[2] == ":" and "\t" in ln:
            k, v = ln.split("\t", 1)
            k = k.strip()
            field[k] = (field[k] + " " + v.strip()).strip()
            sec[k[:2]] += " " + v.strip()
    out.update(fields=field, sections=sec, raw_files=raw - {""}, results=results)
    return out


def text_block(m: dict) -> dict:
    f = m["fields"]
    expand = lambda t: re.sub(UN_BRACKET, "untargeted and targeted", t, flags=re.I)
    sec = {k: expand(v) for k, v in m["sections"].items()}
    where_u = [s for s in sec if re.search(UNTARGETED, sec[s], re.I)]
    stripped = {s: re.sub(UNTARGETED, " ", sec[s], flags=re.I) for s in sec}
    where_ts = [s for s in sec if re.search(TARGETED_STRICT, stripped[s], re.I)]
    type_targeted = [k[:2] for k in TYPE_FIELDS
                     if re.search(TARGETED_LOOSE, re.sub(UNTARGETED, " ", expand(f.get(k, "")), flags=re.I), re.I)]
    where_ts = sorted(set(where_ts) | set(type_targeted))
    where_tl = [s for s in sec if re.search(TARGETED_LOOSE, stripped[s], re.I)]
    u, t = bool(where_u), bool(where_ts)
    au, at = any(s in ANALYSIS_SECTIONS for s in where_u), any(s in ANALYSIS_SECTIONS for s in where_ts)
    stat = lambda a, b: "both" if a and b else "untargeted" if a else "targeted" if b else ""
    alltext = " ".join(sec.values())
    anatext = " ".join(sec.get(s, "") for s in ANALYSIS_SECTIONS)
    res = re.search(RESOLUTION, " ".join(sec.get(s, "") for s in ("MS", "AN")), re.I)
    return {
        "study_type_field": f.get("ST:STUDY_TYPE", ""), "project_type_field": f.get("PR:PROJECT_TYPE", ""),
        "untargeted_stated_in_sections": ";".join(sorted(where_u)), "targeted_stated_in_sections": ";".join(sorted(where_ts)),
        "targeted_word_any_sense_in_sections": ";".join(sorted(where_tl)),
        "acquisition_wording_any_section": stat(u, t), "acquisition_wording_analysis_sections": stat(au, at),
        "acquisition_wording_level": ("" if not (u or t) else "analysis sections (AN, CH, MS)" if (au or at) else "other sections only"),
        "targeted_wording_in_type_field": "yes" if type_targeted else "",
        "mrm_srm_prm_mentioned_analysis_sections": "yes" if re.search(MRM, anatext, re.I) else "",
        "mrm_srm_prm_mentioned_any_section": "yes" if re.search(MRM, alltext, re.I) else "",
        "ms2_or_dda_dia_mentioned_analysis_sections": "yes" if re.search(DDA_DIA, anatext, re.I) else "",
        "resolution_setting_field": f.get("MS:RESOLUTION_SETTING", ""), "mass_accuracy_field": f.get("MS:MASS_ACCURACY", ""),
        "resolution_statement_in_text": (res.group(0).strip()[:160] if res else ""),
        "results_file_line": m["results"].get("kind", ""),
        "results_file_has_mz": (m["results"].get("has m/z", "") or "not stated") if m["results"] else "",
        "results_file_has_rt": (m["results"].get("has rt", "") or "not stated") if m["results"] else "",
        "results_file_rt_units": m["results"].get("rt units", ""),
        "results_file_units": m["results"].get("units", ""),
        "results_file_units_declared": ("" if re.match(UNITS_PLACEHOLDER, m["results"].get("units", "").strip(), re.I)
                                        else m["results"].get("units", "").strip()),
    }


def write_xlsx(path: Path, sheets: list[tuple[str, pd.DataFrame]], wrap: tuple[str, ...]) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets:
            df.to_excel(xw, sheet_name=name, index=False)
    wb = load_workbook(path)
    for ws in wb.worksheets:
        ws.freeze_panes = "C2" if ws.title == "Per analysis" else "A2"
        big = ws.max_row > 2000
        for j, col in enumerate(ws.iter_cols(min_row=1, max_row=min(ws.max_row, 300)), start=1):
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(70 if ws.title in wrap else 42, max(11, width + 2))
        for c in ws[1]:
            c.font = Font(name="Arial", size=10, bold=True)
            c.alignment = Alignment(wrap_text=True, vertical="top")
        if not big:
            for row in ws.iter_rows(min_row=2):
                for c in row:
                    c.font = Font(name="Arial", size=10)
                    if ws.title in wrap:
                        c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def main() -> None:
    ms2_shortlist_self_test()
    bad = {k: combine(*k)[0] for k, exp in SELF_TESTS.items() if combine(*k)[0] != exp}
    assert not bad, bad
    profile_self_test()
    SM.self_test()
    no_cross_inference_test()
    v = pd.read_csv(VALIDITY, sep="\t", keep_default_na=False)
    assert len(v) == 6696
    lab = pd.read_csv(LABELS, sep="\t")
    lab["pct_named"] = (100 * lab.n_named / (lab.n_named + lab.n_without_name)).round(1)
    named = lab.pivot_table(index=["study_id", "analysis_id"], columns="source", values="pct_named", aggfunc="first")

    # ---- Pan-ReDU, Workbench rows of our studies
    r = pd.read_csv(REDU, sep="\t", usecols=REDU_COLUMNS, dtype=str, keep_default_na=False)
    n_redu_all = len(r)
    r = r[r.ATTRIBUTE_DatasetAccession.isin(set(v.study_id))].copy()
    r["base"] = r.filename.map(norm_raw)
    r["ext"] = r.filename.str.lower().str.extract(REDU_FILE_EXTENSION, expand=False).fillna("")
    cnt = pd.to_numeric(r.MS2spectra_count, errors="coerce")
    r["ms2"] = "not_determined"
    r.loc[(cnt == 0) & r.ext.isin(["mzml", "mzxml"]) & (r.classification == "Unclassified"), "ms2"] = "zero_unclassified_open"
    r.loc[r.classification == "NO_MS2", "ms2"] = "absent"
    r.loc[cnt > 0, "ms2"] = "present"
    r["model"] = r.MassSpectrometer.str.split("|").str[0].str.strip()
    by_study = {st: g for st, g in r.groupby("ATTRIBUTE_DatasetAccession")}

    def redu_summary(g: pd.DataFrame, prefix: str) -> dict:
        models = [m for m in dict.fromkeys(g.model) if m.lower() not in REDU_MISSING]
        mc = {one_name_class(m)[0] for m in models} - {"IND", "TOF"}   # a TOF without an informative model has no class
        calls = collections.Counter(c for c in g.classification if c != "Unclassified")
        st = collections.Counter(g.ms2)
        keep = lambda s: "; ".join(x for x in dict.fromkeys(s) if x.lower() not in REDU_MISSING)
        return {f"{prefix}_files": len(g), f"{prefix}_instrument_models": "; ".join(models),
                f"{prefix}_resolution_class": ("" if not mc else HR if mc == {"HR"} else NOM if mc == {"NOM"} else "both classes"),
                f"{prefix}_chromatography": keep(g.ChromatographyAndPhase), f"{prefix}_ionization": keep(g.IonizationSourceAndPolarity),
                f"{prefix}_acquisition_calls": "; ".join(f"{k} ({n})" for k, n in calls.most_common()),
                f"{prefix}_files_ms2_present": st["present"], f"{prefix}_files_no_ms2": st["absent"],
                f"{prefix}_files_zero_count_unclassified": st["zero_unclassified_open"], f"{prefix}_files_not_determined": st["not_determined"],
                f"{prefix}_ms2_status_strict": ms2_status(st, False), f"{prefix}_ms2_status_lenient": ms2_status(st, True)}

    # ---- per analysis
    rows, raw_sets, sections_seen = [], {}, set()
    for st, an, s_mw, s_dt, s_un in zip(v.study_id, v.analysis_id, v.mwtab, v.datatable, v.untarg_data):
        m = read_mwtab(CACHE / st / an / "mwtab" / f"{an}_mwtab.txt")
        row = {"study_id": st, "analysis_id": an, "mwtab_source": s_mw, "datatable_source": s_dt, "untarg_data_source": s_un}
        if not m["has_mwtab"]:
            row.update(modality="No mwTab file", resolution_class="", resolution_class_basis="no mwTab file")
            rows.append(row)
            continue
        f = m["fields"]
        sections_seen.update(m["sections"])
        atype, itype, iname = f.get("AN:ANALYSIS_TYPE", ""), f.get("MS:INSTRUMENT_TYPE", ""), f.get("MS:INSTRUMENT_NAME", "")
        has_ms, has_nmr = any(k.startswith("MS:") for k in f), any(k.startswith("NM:") for k in f)
        modality = "NMR" if ("NMR" in atype.upper() or (has_nmr and not has_ms)) else "MS" if (has_ms or "MS" in atype.upper()) else "Not stated"
        row.update(modality=modality, analysis_type_field=atype, instrument_type_field=itype, instrument_name_field=iname,
                   ms_type_field=f.get("MS:MS_TYPE", ""), ion_mode_field=f.get("MS:ION_MODE", ""),
                   chromatography_type_field=f.get("CH:CHROMATOGRAPHY_TYPE", ""))
        if modality == "MS":
            cls, basis, fam = combine(itype, iname)
            row.update(resolution_class=cls, resolution_class_basis=basis, analyser_family=fam,
                       class_from_type=type_class(itype)[0], class_from_name=name_class(iname)[0])
        else:
            row.update(resolution_class="", resolution_class_basis="not an MS analysis" if modality == "NMR" else "modality not stated")
        row.update(text_block(m))
        if modality == "MS":
            row.update(SM.scan_mode_columns(f, ANALYSIS_SECTIONS))
        for src in ("datatable", "mwtab", "untarg_data"):
            val = named.get(src, pd.Series(dtype=float)).get((st, an))
            row[f"{src}_pct_named_labels"] = "" if val is None or pd.isna(val) else val
        raw_sets[(st, an)] = m["raw_files"]
        row["raw_file_names_in_mwtab"] = len(m["raw_files"])
        g = by_study.get(st)
        row["redu_study_listed"] = "yes" if g is not None else ""
        if g is not None:
            row.update(redu_summary(g, "redu_study"))
            hit = g[g.base.isin(m["raw_files"])] if m["raw_files"] else g.iloc[0:0]
            row["redu_raw_files_matched"] = len(hit)
            if len(hit):
                row.update(redu_summary(hit, "redu_analysis"))
        rows.append(row)
    d = pd.DataFrame(rows).fillna("")
    # raw-file sets shared by two or more analyses of the same study: the ReDU link is then not analysis-specific
    sets_by_study: dict = collections.defaultdict(collections.Counter)
    for (st, an), s in raw_sets.items():
        if s:
            sets_by_study[st][frozenset(s)] += 1
    d["raw_file_set_shared_with_another_analysis"] = [
        ("yes" if raw_sets.get((st, an)) and sets_by_study[st][frozenset(raw_sets[(st, an)])] > 1 else "")
        for st, an in zip(d.study_id, d.analysis_id)]
    npct = pd.to_numeric(d.untarg_data_pct_named_labels, errors="coerce")
    d["untarg_data_label_type"] = ["" if pd.isna(x) else "mostly without a metabolite name (<=10% named)" if x <= 10
                                   else "mostly named (>=90% named)" if x >= 90 else "mixed" for x in npct]
    ms2p = d.get("redu_analysis_ms2_status_strict", pd.Series("", index=d.index)).isin(["present", "both"])
    insec = lambda sections, inside: any((x in ANALYSIS_SECTIONS) == inside for x in sections.split(";") if x)
    d["items_detected"] = ["; ".join(k for k, ok in (
        ("wording: untargeted (AN/CH/MS sections)", insec(r.untargeted_stated_in_sections, True)),
        ("wording: untargeted (other sections)", insec(r.untargeted_stated_in_sections, False)),
        ("wording: targeted (AN/CH/MS sections)", insec(r.targeted_stated_in_sections, True)),
        ("wording: targeted (other sections)", insec(r.targeted_stated_in_sections, False)),
        ("wording: MRM/SRM/PRM (AN/CH/MS sections)", r.mrm_srm_prm_mentioned_analysis_sections != ""),
        ("repository: valid untarg_data matrix", r.untarg_data_source == "valid"),
        ("repository: results-file declaration", r.results_file_line != ""),
        ("repository: results file declares m/z and RT", r.results_file_has_mz.lower() in ("yes", "neutral masses") and r.results_file_has_rt.lower() == "yes"),
        ("labels: untarg_data mostly without a metabolite name", r.untarg_data_label_type.startswith("mostly without")),
        ("Pan-ReDU: MS2 spectra in matched files", p2)) if ok)
        for r, p2 in zip(d.itertuples(), ms2p)]
    ut, rf = d.untarg_data_source == "valid", d.results_file_line != ""
    d["repository_marker"] = ["valid untarg_data matrix and results-file declaration" if a and b else "valid untarg_data matrix only" if a
                              else "results-file declaration only" if b else "none" for a, b in zip(ut, rf)]
    d["acquisition_evidence_profile"] = [evidence_profile(w, a or b) if mod == "MS" else ""
                                         for w, a, b, mod in zip(d.acquisition_wording_any_section, ut, rf, d.modality)]
    is_ms = d.modality == "MS"
    assert (d.acquisition_evidence_profile[is_ms].isin(PROFILE_ORDER)).all() and (d.acquisition_evidence_profile[~is_ms] == "").all()
    # Each derived column must be fully determined by its stated inputs (so nothing else can have influenced it).
    m_ = d[is_ms]
    assert m_.groupby(["instrument_type_field", "instrument_name_field"]).resolution_class.nunique().max() == 1
    assert m_.groupby(["acquisition_wording_any_section", m_.repository_marker != "none"]).acquisition_evidence_profile.nunique().max() == 1
    assert m_.groupby(["untargeted_stated_in_sections", "targeted_stated_in_sections"]).acquisition_wording_any_section.nunique().max() == 1
    # Block I: every MS record has one of the four descriptions, non-MS records are blank, and the description is fully
    # determined by the terms detected (so neither the analyser class nor the wording can have influenced it).
    assert d.reported_scan_mode_evidence[is_ms].isin(SM.EVIDENCE_ORDER).all() and (d[list(SM.COLUMNS)][~is_ms] == "").all().all()
    assert m_.groupby("scan_mode_terms_detected").reported_scan_mode_evidence.nunique().max() == 1
    has_term = lambda t: d.scan_mode_terms_detected.map(lambda x: t in x.split("; "))
    sel_, full_ = [pd.concat([has_term(t) for t in grp], axis=1).any(axis=1) for grp in (SM.SELECTED, SM.FULL)]
    assert (d.reported_scan_mode_evidence[is_ms] == [SM.scan_mode_evidence(a, b) for a, b in zip(sel_[is_ms], full_[is_ms])]).all()
    assert not d.scan_mode_matched_fields.str.contains("|".join(SM.NOT_SEARCHED)).any()
    agree = lambda a, b: "" if not a or not b or a == IND else ("agree" if a == b else "ReDU has both classes" if b == "both classes" else "disagree")
    d["analyser_class_vs_redu_study"] = [agree(a, b) for a, b in zip(d.resolution_class, d.get("redu_study_resolution_class", ""))]
    d["analyser_class_vs_redu_analysis"] = [agree(a, b) for a, b in zip(d.resolution_class, d.get("redu_analysis_resolution_class", ""))]
    d = add_ms2_shortlist(d, r, raw_sets)
    lead = ["study_id", "analysis_id", "modality", "resolution_class", "resolution_class_basis", "analyser_family", "class_from_type",
            "class_from_name", "instrument_type_field", "instrument_name_field"]
    d = d[lead + [c for c in d.columns if c not in lead and c not in SM.COLUMNS] + list(SM.COLUMNS)]
    d.to_csv(OUT / "per_analysis_evidence.tsv", sep="\t", index=False)

    # ---- instrument strings
    ms = d[d.modality == "MS"]
    ins = ms.groupby(["instrument_type_field", "instrument_name_field", "class_from_type", "class_from_name", "resolution_class",
                      "resolution_class_basis", "analyser_family"]).size().reset_index(name="analyses")
    ins = ins.sort_values(["resolution_class", "analyses"], ascending=[True, False])
    ins.to_csv(OUT / "instrument_strings.tsv", sep="\t", index=False)

    # ---- coverage
    n_all, n_ms = len(d), len(ms)
    def cov(label: str, mask: pd.Series, note: str = "") -> dict:
        m_ = mask.reindex(d.index, fill_value=False)
        k, kms = int(m_.sum()), int((m_ & (d.modality == "MS")).sum())
        return {"Item": label, "Analyses with the item": k, "% of all 6,696 analyses": round(100 * k / n_all, 1),
                "MS analyses with the item": kms, f"% of {n_ms:,} MS analyses": round(100 * kms / n_ms, 1), "Note": note}
    has = lambda c: d[c].astype(str) != ""
    pos = lambda c: (pd.to_numeric(d[c], errors="coerce").fillna(0) > 0) if c in d else pd.Series(False, index=d.index)
    covr = [
        cov("mwTab file present", d.modality != "No mwTab file"),
        cov("Modality: MS", d.modality == "MS"), cov("Modality: NMR", d.modality == "NMR"),
        cov("Instrument type field filled (not blank, '-', Other, Unspecified ...)", d.instrument_type_field.str.strip().str.lower().map(lambda x: x not in UNDECLARED)),
        cov("Instrument name field filled", d.instrument_name_field.str.strip().str.lower().map(lambda x: x not in UNDECLARED)),
        cov("A. Analyser class assigned: HRMS-capable", d.resolution_class == HR),
        cov("A. Analyser class assigned: Nominal/unit-resolution", d.resolution_class == NOM),
        cov("A. Analyser class: Indeterminate", d.resolution_class == IND, "MS analyses whose analyser class cannot be inferred"),
        cov("A. Analyser class: HRMS-capable or Nominal/unit-resolution", d.resolution_class.isin([HR, NOM])),
        cov("B. STUDY_TYPE field filled", has("study_type_field")), cov("B. PROJECT_TYPE field filled", has("project_type_field")),
        cov("B. 'untargeted' / 'non-targeted' stated, any section", has("untargeted_stated_in_sections")),
        cov("B. 'targeted' + analysis term, or 'targeted' in a type field (strict), any section", has("targeted_stated_in_sections")),
        cov("B. Acquisition-related wording detected, any section: untargeted only", d.acquisition_wording_any_section == "untargeted"),
        cov("B. Acquisition-related wording detected, any section: targeted only", d.acquisition_wording_any_section == "targeted"),
        cov("B. Acquisition-related wording detected, any section: both", d.acquisition_wording_any_section == "both"),
        cov("B. Acquisition-related wording detected, any section", has("acquisition_wording_any_section")),
        cov("B. Acquisition-related wording detected in analysis sections (AN, CH, MS)", has("acquisition_wording_analysis_sections")),
        cov("B. Acquisition-related wording outside AN, CH and MS only", d.acquisition_wording_level == "other sections only", "applicability to the individual analysis not established"),
        cov("B. MRM / SRM / PRM mentioned, analysis sections", has("mrm_srm_prm_mentioned_analysis_sections")),
        cov("B. MS/MS, DDA or DIA mentioned, analysis sections", has("ms2_or_dda_dia_mentioned_analysis_sections")),
        cov("C. RESOLUTION_SETTING field filled", has("resolution_setting_field")),
        cov("C. MASS_ACCURACY field filled", has("mass_accuracy_field")),
        cov("C. Text fragment with 'resolution' and a number in MS/AN text", has("resolution_statement_in_text"), "keyword match; may refer to MS2 scans, unit resolution or chromatography"),
        cov("D. Results-file declaration in the mwTab file", has("results_file_line")),
        cov("D. Results file declares m/z (Yes or Neutral masses)", d.results_file_has_mz.str.lower().isin(["yes", "neutral masses"])),
        cov("D. Results file declares RT (Yes)", d.results_file_has_rt.str.lower() == "yes"),
        cov("D. Results file declares both m/z and RT", d.results_file_has_mz.str.lower().isin(["yes", "neutral masses"]) & (d.results_file_has_rt.str.lower() == "yes")),
        cov("D. Results file declares UNITS (placeholders excluded)", has("results_file_units_declared"), "placeholders: NA, N/A, none, unknown, a number alone, 'no applicable units'"),
        cov("D. Non-empty UNITS attribute, including placeholders", has("results_file_units")),
        cov("D. Valid untarg_data table with a results-file line", (d.untarg_data_source == "valid") & has("results_file_line")),
        cov("D. untarg_data labels mostly without a metabolite name (<=10% named)", d.untarg_data_label_type.str.startswith("mostly without")),
        *[cov(f"F. Evidence profile: {k}", d.acquisition_evidence_profile == k) for k in PROFILE_ORDER],
        cov("F. Repository marker present (results-file declaration and/or valid untarg_data matrix)", d.repository_marker != "none"),
        cov("F. ...of which results-file declaration without a valid untarg_data matrix", d.repository_marker == "results-file declaration only"),
        cov("F. Either wording or repository marker detected", is_ms & (d.acquisition_evidence_profile != PROFILE_LOOKUP[("", False)])),
        cov("Any item detected (wording, repository, labels or Pan-ReDU)", has("items_detected"), "see column items_detected; an inventory, not a classification"),
        cov("E. Study listed in Pan-ReDU", has("redu_study_listed")),
        cov("E. Pan-ReDU instrument model, study level", has("redu_study_instrument_models") if "redu_study_instrument_models" in d else d.study_id == ""),
        cov("E. Pan-ReDU MS2 status, study level, strict", has("redu_study_ms2_status_strict"), "MS2 count > 0, or classified NO_MS2"),
        cov("E. Pan-ReDU MS2 status, study level, lenient", has("redu_study_ms2_status_lenient"), "also zero-count unclassified mzML/mzXML files"),
        cov("E. Raw-file names listed in the mwTab file", pos("raw_file_names_in_mwtab")),
        cov("E. Raw-file names matched to Pan-ReDU files", pos("redu_raw_files_matched")),
        cov("E. ...of which raw-file set shared with another analysis of the study", (d.raw_file_set_shared_with_another_analysis == "yes") & pos("redu_raw_files_matched"), "link is not analysis-specific"),
        cov("E. Pan-ReDU instrument model, analysis level", has("redu_analysis_instrument_models") if "redu_analysis_instrument_models" in d else d.study_id == ""),
        cov("E. Pan-ReDU MS2 status, analysis level, strict", has("redu_analysis_ms2_status_strict") if "redu_analysis_ms2_status_strict" in d else d.study_id == ""),
        cov("E. Pan-ReDU MS2 status, analysis level, lenient", has("redu_analysis_ms2_status_lenient") if "redu_analysis_ms2_status_lenient" in d else d.study_id == ""),
        *[cov(f"G. Pan-ReDU MS2 linked-file evidence: {k}", d.panredu_ms2_evidence_status == k) for k in MS2_EVIDENCE_ORDER],
        *[cov(f"G. Pan-ReDU MS2 analysis shortlist: {k}", d.panredu_ms2_analysis_shortlist == k) for k in MS2_SHORTLIST_ORDER],
        *[cov(f"I. Reported scan-mode evidence: {k}", d.reported_scan_mode_evidence == k) for k in SM.EVIDENCE_ORDER],
        cov("I. Any scan-mode term detected (AN, CH and MS fields)", is_ms & (d.reported_scan_mode_evidence != SM.NONE_DETECTED),
            "keyword match in deposited text; not an acquisition type"),
        *[cov(f"I. Scan-mode term detected: {t}", has_term(t), "a record can carry several terms") for t in SM.TERM_ORDER],
        cov("F + I. Wording, repository marker or scan-mode term detected",
            is_ms & ((d.acquisition_evidence_profile != PROFILE_LOOKUP[("", False)]) | (d.reported_scan_mode_evidence != SM.NONE_DETECTED))),
    ]
    coverage = pd.DataFrame(covr)
    coverage.to_csv(OUT / "coverage.tsv", sep="\t", index=False)

    # ---- cross-tabulations
    order = [HR, NOM, IND]
    lab_cls = d.resolution_class.where(d.modality == "MS", d.modality)
    def xt(col: pd.Series, name: str) -> pd.DataFrame:
        t = pd.crosstab(lab_cls, col.replace("", "(none)"), margins=True, margins_name="All").reset_index().rename(columns={"row_0": "Class", "resolution_class": "Class"})
        t.insert(0, "Table", name)
        return t
    src = pd.DataFrame({"Class": lab_cls, "datatable": d.datatable_source == "valid", "mwTab": d.mwtab_source == "valid",
                        "untarg_data": d.untarg_data_source == "valid"})
    by_src = src.groupby("Class").agg(analyses=("datatable", "size"), datatable_valid=("datatable", "sum"), mwtab_valid=("mwTab", "sum"),
                                      untarg_data_valid=("untarg_data", "sum")).reset_index()
    for c in ("datatable_valid", "mwtab_valid", "untarg_data_valid"):
        by_src[c.replace("_valid", "_pct")] = (100 * by_src[c] / by_src.analyses).round(1)
    basis = ms.groupby(["resolution_class", "resolution_class_basis"]).size().reset_index(name="analyses")
    fam = ms.groupby(["resolution_class", "analyser_family"]).size().reset_index(name="analyses").sort_values(["resolution_class", "analyses"], ascending=[True, False])
    x_text = xt(d.acquisition_wording_any_section, "Analyser class x acquisition-related wording (any section)")
    x_res = xt(d.results_file_line, "Analyser class x Workbench results-file line")
    prof = d.acquisition_evidence_profile[is_ms]
    st_tab = pd.crosstab(pd.Categorical(prof, PROFILE_ORDER), d.resolution_class[is_ms], margins=True, margins_name="All MS records")
    st_tab = st_tab.reset_index().rename(columns={"row_0": "Acquisition evidence profile"})
    st_tab["% of MS records"] = (100 * st_tab["All MS records"] / int(is_ms.sum())).round(1)
    # the sheet must account for every MS record
    assert int(st_tab.loc[st_tab.iloc[:, 0] == "All MS records", "All MS records"].iat[0]) == int(is_ms.sum())
    assert int(st_tab.loc[st_tab.iloc[:, 0] != "All MS records", "All MS records"].sum()) == int(is_ms.sum())
    assert set(st_tab.iloc[:-1, 0]) == set(PROFILE_ORDER)
    x_ms2 = xt(d.get("redu_study_ms2_status_strict", pd.Series("", index=d.index)), "Analyser class x Pan-ReDU MS2 status (study level, strict)")
    x_redu = xt(d.analyser_class_vs_redu_study, "Analyser class x Pan-ReDU instrument class (study level)")
    ms2_tabs = []
    for col, labels, section in (("panredu_ms2_evidence_status", MS2_EVIDENCE_ORDER, "Linked-file evidence"),
                                  ("panredu_ms2_analysis_shortlist", MS2_SHORTLIST_ORDER, "Analysis shortlist")):
        tab = pd.crosstab(pd.Categorical(d.loc[is_ms, col], labels), d.loc[is_ms, "resolution_class"],
                          margins=True, margins_name="All MS records").reset_index().rename(columns={"row_0": "MS2 evidence / shortlist"})
        tab.insert(0, "Section", section)
        tab["% of MS records"] = (100 * tab["All MS records"] / n_ms).round(1)
        assert int(tab.loc[tab.iloc[:, 1] == "All MS records", "All MS records"].iat[0]) == n_ms
        assert int(tab.loc[tab.iloc[:, 1] != "All MS records", "All MS records"].sum()) == n_ms
        ms2_tabs.append(tab)
    ms2_tab = pd.concat(ms2_tabs, ignore_index=True)
    ms2_tab.to_csv(OUT / "panredu_ms2_distribution.tsv", sep="\t", index=False)
    d[["study_id", "analysis_id", "modality", "resolution_class", "panredu_ms2_evidence_status", "panredu_ms2_link_scope",
       "panredu_ms2_raw_file_coverage_pct", "panredu_ms2_shared_raw_files", "panredu_ms2_analysis_shortlist"]].to_csv(
        OUT / "panredu_ms2_per_analysis.tsv", sep="\t", index=False)
    shortlist_mask = is_ms & d.panredu_ms2_analysis_shortlist.isin(["MS2 detected", "No MS2 reported; complete file-roster match"])
    d.loc[shortlist_mask, ["study_id", "analysis_id", "resolution_class", "panredu_ms2_analysis_shortlist", "panredu_ms2_evidence_status",
                           "panredu_ms2_link_scope", "panredu_ms2_raw_file_coverage_pct", "panredu_ms2_shared_raw_files"]].to_csv(
        OUT / "panredu_ms2_analysis_shortlist.tsv", sep="\t", index=False)

    # ---- block I sheet: the four descriptions, then the six terms, by analyser class
    sm_ev = pd.crosstab(pd.Categorical(d.loc[is_ms, "reported_scan_mode_evidence"], SM.EVIDENCE_ORDER), d.loc[is_ms, "resolution_class"],
                        margins=True, margins_name="All MS records").reset_index().rename(columns={"row_0": "Description / term"})
    assert int(sm_ev.loc[sm_ev.iloc[:, 0] == "All MS records", "All MS records"].iat[0]) == n_ms
    assert int(sm_ev.loc[sm_ev.iloc[:, 0] != "All MS records", "All MS records"].sum()) == n_ms
    assert set(sm_ev.iloc[:-1, 0]) == set(SM.EVIDENCE_ORDER)
    sm_ev.insert(0, "Section", "Reported scan-mode evidence (one per MS record)")
    sm_terms = pd.DataFrame([{"Section": "Terms detected (a record can carry several)", "Description / term": t,
                              **{c: int((has_term(t) & is_ms & (d.resolution_class == c)).sum()) for c in sorted(order)},
                              "All MS records": int((has_term(t) & is_ms).sum())} for t in SM.TERM_ORDER])
    sm_tab = pd.concat([sm_ev, sm_terms], ignore_index=True)
    sm_tab["% of MS records"] = (100 * sm_tab["All MS records"] / n_ms).round(1)

    guide = pd.DataFrame([
        ("study_id, analysis_id", "Identifiers", "Figure 2B source-availability table (6,696 records)"),
        ("modality", "A", "MS, NMR, Not stated, or No mwTab file; from AN:ANALYSIS_TYPE and the presence of MS: / NM: fields"),
        ("resolution_class", "A", f"{HR}, {NOM} or {IND}; MS analyses only (blank otherwise)"),
        ("resolution_class_basis", "A", "How the class was reached: type and name agree; type only; name only; TOF type resolved by the model; type and name disagree; TOF type, model not informative; no analyser stated; more than one instrument class named"),
        ("analyser_family", "A", "Orbitrap, FT-ICR / FT-MS, Q-TOF / TripleTOF / timsTOF, IT-TOF, TOF, GC-TOF, GC-TOF (unit mass), MALDI-TOF, Triple quadrupole, QTRAP, Single quadrupole, Ion trap"),
        ("class_from_type, class_from_name", "A", "HR / NOM / TOF (type only) / MIXED / IND: the class read separately from MS:INSTRUMENT_TYPE and MS:INSTRUMENT_NAME"),
        ("instrument_type_field, instrument_name_field, ms_type_field, ion_mode_field, chromatography_type_field, analysis_type_field", "A", "mwTab fields, as deposited"),
        ("mwtab_source, datatable_source, untarg_data_source", "Inputs", "Validity of each source for the record, copied from the Figure 2B table: valid, invalid_or_empty or no_file"),
        ("datatable_pct_named_labels, mwtab_pct_named_labels, untarg_data_pct_named_labels", "D", "Share of feature labels that are metabolite names in each source: 100 x n_named / (n_named + n_without_name), one decimal, from the Text S3 classifier; blank when the source has no classified labels"),
        ("study_type_field, project_type_field", "B", "ST:STUDY_TYPE and PR:PROJECT_TYPE, as deposited (free text)"),
        ("untargeted_stated_in_sections", "B", "mwTab sections (two-letter prefixes) whose text contains 'untargeted' or 'non-targeted'"),
        ("targeted_stated_in_sections", "B", "Sections containing 'targeted' next to an analysis term, or 'targeted' in ST:STUDY_TYPE / PR:PROJECT_TYPE (strict rule), after removing 'untargeted'"),
        ("targeted_wording_in_type_field", "B", "yes = the word 'targeted' occurs in ST:STUDY_TYPE or PR:PROJECT_TYPE"),
        ("targeted_word_any_sense_in_sections", "B", "Sections containing the word 'targeted' in any sense (loose rule), after removing 'untargeted'"),
        ("acquisition_wording_any_section", "B", "Acquisition-related wording detected: untargeted / targeted / both / blank (strict rule). It describes deposited wording, not a verified acquisition type"),
        ("acquisition_wording_analysis_sections", "B", "Same, restricted to the analysis-specific sections AN, CH and MS"),
        ("acquisition_wording_level", "B", "Where the wording occurs: analysis sections (AN, CH, MS), or other sections only. Wording outside AN/CH/MS is reported separately; its applicability to the individual analysis was not established"),
        ("mrm_srm_prm_mentioned_*, ms2_or_dda_dia_mentioned_analysis_sections", "B", "Keyword present in the text"),
        ("resolution_setting_field, mass_accuracy_field", "C", "MS:RESOLUTION_SETTING and MS:MASS_ACCURACY, as deposited"),
        ("resolution_statement_in_text", "C", "First text fragment in the MS/AN sections where 'resolution' or 'resolving power' occurs with a number (not validated)"),
        ("results_file_line", "D", "'MS results file' or 'NMR results file' when the mwTab file names an untargeted results file (MS:MS_RESULTS_FILE / NM:NMR_RESULTS_FILE); blank otherwise"),
        ("results_file_has_mz, results_file_has_rt, results_file_rt_units, results_file_units", "D", "Attributes on that line ('Has m/z', 'Has RT', 'RT units', 'UNITS'), as deposited; 'not stated' when the line exists without the attribute"),
        ("results_file_units_declared", "D", "results_file_units with placeholder entries (NA, N/A, none, unknown, a number alone, 'no applicable units') left blank; the raw string stays in results_file_units"),
        ("untarg_data_label_type", "D", "From untarg_data_pct_named_labels: mostly without a metabolite name (<=10% named), mixed, or mostly named (>=90%); data-derived"),
        ("repository_marker", "F", "valid untarg_data matrix and results-file declaration / valid untarg_data matrix only / results-file declaration only / none. A results-file declaration is a line in the mwTab file; a valid untarg_data matrix is a table that was retrieved and parsed. Either is a repository marker; neither is an acquisition statement"),
        ("acquisition_evidence_profile", "F", "MS records only. A description of two inputs: acquisition_wording_any_section and repository_marker (RULES.md, section F). Analyser class is not used to assign it. It is not an acquisition type"),
        ("items_detected", "B, D, E", "An inventory of the items found for the record, each prefixed by its source (wording, repository, labels, Pan-ReDU). It names what was detected; it does not label any item as evidence for an acquisition type"),
        ("raw_file_names_in_mwtab", "E", "Number of distinct RAW_FILE_NAME entries in the SUBJECT_SAMPLE_FACTORS section"),
        ("redu_study_*", "E", "Pan-ReDU rows of the study: files, instrument models, class of those models, chromatography, ionisation, acquisition calls, MS2 file counts and status"),
        ("redu_raw_files_matched, redu_analysis_*", "E", "The same items for the Pan-ReDU files whose name matches a raw-file name of this analysis"),
        ("raw_file_set_shared_with_another_analysis", "E", "yes = another analysis of the study lists the same raw files, so the analysis-level link is not specific"),
        ("panredu_ms2_evidence_status", "G", "MS records only. Evidence in matched Pan-ReDU files: positive spectrum count, NO_MS2 calls, both, unresolved files, or no match. Unlike the older strict summary, no-MS2 calls alongside unresolved files are identified separately"),
        ("panredu_ms2_link_scope", "G", "Analysis-specific filename match / Shared across analyses / No matching files. Shared means at least one matched filename also occurs in another analysis of the same study, including partial roster overlap"),
        ("panredu_ms2_raw_file_coverage_pct", "G", "100 x distinct matched normalized raw filenames / distinct raw filenames declared in the mwTab factor block; blank when no filenames are declared. This measures link coverage, not MS2 coverage"),
        ("panredu_ms2_shared_raw_files", "G", "Number of distinct matched normalized filenames also listed by another analysis of this study"),
        ("panredu_ms2_analysis_shortlist", "G", "Shared links receive Shared-file evidence only. For unshared links, any positive MS2 count establishes detected MS2; no MS2 reported requires every declared raw filename to match and every linked file record to be NO_MS2. Partial coverage and unresolved records are reported separately. It does not assign targeted/untargeted acquisition"),
        ("reported_scan_mode_evidence", "I", "MS records only. Which kinds of scan-mode term were found in the AN, CH and MS fields: " + "; ".join(SM.EVIDENCE_ORDER) + ". Selected-mode terms are MRM/SRM, PRM and SIM. It describes deposited wording; it is not an acquisition type, and it is not translated into targeted or untargeted. Analyser class, targeted/untargeted wording and repository markers are not used to assign it"),
        ("scan_mode_terms_detected", "I", "The terms found, in the fixed order " + "; ".join(SM.TERM_ORDER) + " (RULES.md, section I)"),
        ("scan_mode_matched_fields", "I", "For each term found, the first mwTab field in which it was matched ('term: field'). MS:INSTRUMENT_TYPE and MS:INSTRUMENT_NAME are not searched"),
        ("scan_mode_excerpts", "I", f"For each term found, the matched text with {SM.EXCERPT} characters on each side ('term: ...text...'), separated by ' || '. Given for review; the matches were not validated by reading every record"),
        ("*_ms2_status_strict / _lenient", "E", "strict: 'present' = MS2 spectrum count > 0, 'absent' = classified NO_MS2. lenient: zero-count unclassified mzML/mzXML files also count as absent. Vendor-format files and count -1 are not determined"),
        ("analyser_class_vs_redu_study / _analysis", "A vs E", "agree / disagree / ReDU has both classes; blank when either side has no class or the analyser class is Indeterminate"),
    ], columns=["Column(s)", "Block", "Definition and source"])
    # every column of the per-analysis table must have an entry in the column guide ('*' stands for any text)
    guide_names = [n.strip() for cell in guide["Column(s)"] for n in re.split(r",|/", cell) if n.strip()]
    guide_rx = [re.compile("^" + ".*".join(re.escape(x) for x in n.split("*")) + "$") for n in guide_names]
    covered = lambda c: any(rx.match(c) or (n.startswith("_") and c.endswith(n)) for n, rx in zip(guide_names, guide_rx))
    assert all(covered(c) for c in d.columns), [c for c in d.columns if not covered(c)]

    readme = pd.DataFrame([
        ("Purpose", "Reviewer 1, Comment 1: evidence available per analysis for stratifying by mass-analyser class (first line) and by acquisition type (second line)."),
        ("Unit", f"One row per study-analysis record: {n_all:,}. MS analyses: {n_ms:,}; NMR: {int((d.modality == 'NMR').sum())}; modality not stated: {int((d.modality == 'Not stated').sum())}; no mwTab file: {int((d.modality == 'No mwTab file').sum())}."),
        ("Data", "Metabolomics Workbench frozen cache of 18 September 2026 (read only). Pan-ReDU export all_sampleinformation.tsv "
                 f"({n_redu_all:,} rows; {len(r):,} rows belong to studies of this cohort)."),
        ("Analyser class", f"{HR}: Orbitrap, FT-ICR / FT-MS, Q-TOF and related TOF hybrids, IT-TOF, and standalone TOF where the named model is a high-resolution TOF. "
                           f"{NOM}: single and triple quadrupole, QTRAP, linear and 3D ion trap, and unit-mass GC-TOF (LECO Pegasus, not HRT). "
                           f"{IND}: analyser not stated, TOF type without an informative model, type and name in different classes, or two instrument classes named."),
        ("What the class means", "Analyser capability, read from the declared instrument. It is not the resolving power or mass accuracy achieved in the run, and not the acquisition mode."),
        ("Acquisition-related wording", "These text-derived categories describe deposited wording and were not treated as verified analysis-level acquisition types. 'Targeted' is counted next to an analysis term, or anywhere in the two type fields (strict rule); the loose count is given for comparison. Most wording is outside the AN, CH and MS sections. Wording outside AN/CH/MS is reported separately; its applicability to the individual analysis was not established."),
        ("Acquisition evidence profile", "One profile per MS record, describing two inputs: the acquisition-related wording and the repository marker (a results-file declaration and/or a valid untarg_data matrix). Analyser class is not used to assign this evidence profile. A repository marker reports what was deposited; it is not an acquisition statement, and MRM/SRM/PRM wording is recorded in separate columns and is not an input."),
        ("Do not stratify sources by the profile", "The profile depends on the untarg_data endpoint: 'Targeted wording; repository marker absent' excludes every valid untarg_data matrix by definition. Comparing source availability across profiles would be circular. Analyser capability is the grouping for stratification."),
        ("Recommended use", "Analyser capability (block A) for the main stratification. Acquisition-related wording and the other items (blocks B to E) as supplementary evidence only."),
        ("Pan-ReDU", "Organised by raw file, without analysis identifiers. Study-level items describe the raw files of the study as a whole; their applicability to the individual analysis was not established. Analysis-level items use raw-file names listed in the mwTab file and are specific only where the raw-file set is not shared with another analysis."),
        ("MS2 shortlist", "Pan-ReDU MS2 evidence is linked by study accession and normalized RAW_FILE_NAME. Unknown counts are not absence. Shared filenames are flagged, including partial roster overlap; the conservative shortlist withholds an analysis assignment for these links. Absence requires complete filename-roster coverage and NO_MS2 in every linked file record. MS2 status does not establish targeted or untargeted acquisition."),
        ("Reported scan-mode evidence", "One description per MS record of the scan-mode terms found in the AN, CH and MS fields: selected-mode terms (MRM/SRM, PRM, SIM), full-scan, DDA or DIA terms, both kinds, or none detected, with the matched field and an excerpt. It is a description of deposited wording and is not translated into targeted or untargeted acquisition. Analyser class, targeted/untargeted wording and repository markers are not used to assign it, and the acquisition evidence profile is unchanged."),
        ("Sheets", "Coverage; Per analysis; Column guide; Class by source; Class basis; Analyser families; Acquisition evidence profile; Reported scan-mode evidence; Pan-ReDU MS2; Instrument strings (every distinct type and name with its class); Cross-tabulations."),
        ("Reproduce", "build_analysis_evidence.py in this folder (self-tests run on every execution); rules in RULES.md, generated from the script."),
    ], columns=["Item", "Detail"])
    cross = pd.concat([x_text, x_res, x_ms2, x_redu], ignore_index=True).fillna("")
    workbook_sheets = [("README", readme), ("Coverage", coverage), ("Per analysis", d), ("Column guide", guide), ("Class by source", by_src),
                       ("Class basis", basis), ("Analyser families", fam), ("Acquisition evidence profile", st_tab),
                       ("Reported scan-mode evidence", sm_tab), ("Pan-ReDU MS2", ms2_tab), ("Instrument strings", ins), ("Cross-tabulations", cross)]
    write_xlsx(OUT / "Reviewer1_Comment1_analysis_evidence.xlsx", workbook_sheets, wrap=("README", "Column guide", "Coverage"))

    # ---- RULES.md
    pc = d[is_ms].groupby([d.acquisition_wording_any_section[is_ms], (d.repository_marker[is_ms] != "none")]).size()
    profile_rows = "".join(f"| {WORDING_TEXT[w]} | {'present' if m else 'absent'} | {lab} | {int(pc.get((w, m), 0)):,} |\n" for w, m, lab in PROFILE)
    profile_total = int(is_ms.sum())
    assert int(pc.sum()) == profile_total
    sc = d.reported_scan_mode_evidence[is_ms].value_counts()
    scan_rows = "".join(f"| {'yes' if a else 'no'} | {'yes' if b else 'no'} | {lab} | {int(sc.get(lab, 0)):,} |\n" for a, b, lab in SM.EVIDENCE)
    assert int(sc.sum()) == profile_total
    md = lambda x: str(x).replace("|", "\\|")
    redu_sha = hashlib.sha256()
    with open(REDU, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            redu_sha.update(chunk)
    n_valid = {c: int((v[c] == "valid").sum()) for c in ("mwtab", "datatable", "untarg_data")}
    mod_n = d.modality.value_counts()
    backslash = "\\"
    rules_front = f"""# Rules: analyser class and acquisition evidence (Reviewer 1, Comment 1)

Generated by `build_analysis_evidence.py` on every build. Every pattern below is the one the script ran, and the build
stops if a pattern used by the code is missing from this file.

How to read the patterns
- Patterns are regular expressions.
- Sections A to G: matching ignores capitalisation. The one exception is the results-file line (section D), which is
  matched as written.
- Section I: patterns are run without flags, and capitalisation is stated for each pattern.
- "MS record" means a record whose modality is MS (see "Modality").

Contents: Inputs; Reading an mwTab file; Modality; sections A to I; Outputs; Checks run on every build; Appendix 1
(columns); Appendix 2 (test cases); Appendix 3 (exploratory patterns that are not in the workbook).

## Inputs

All inputs are read only. Nothing is downloaded and nothing is curated by hand.

| # | Source | File | What is taken from it |
|---|---|---|---|
| 1 | Metabolomics Workbench, frozen cache of 18 September 2026 | `{CACHE.relative_to(ROOT)}/<study>/<analysis>/mwtab/<analysis>_mwtab.txt` | Analysis type, instrument type and name, the text of every section, the results-file line and the raw-file names. {int((d.modality != "No mwTab file").sum()):,} of the {n_all:,} records have this file. |
| 2 | Figure 2B source-availability table | `{VALIDITY.relative_to(ROOT)}` | The list of {n_all:,} study-analysis records, and the validity of each source (`valid`, `invalid_or_empty`, `no_file`), copied to `mwtab_source`, `datatable_source` and `untarg_data_source`. Valid: mwTab {n_valid["mwtab"]:,}, datatable {n_valid["datatable"]:,}, untarg_data {n_valid["untarg_data"]:,}. |
| 3 | Feature-label classes (Text S3 classifier) | `{LABELS.relative_to(ROOT)}` | `n_named` and `n_without_name` for each record and source ({len(lab):,} rows). Share named = 100 x n_named / (n_named + n_without_name), one decimal, written to `datatable_pct_named_labels`, `mwtab_pct_named_labels` and `untarg_data_pct_named_labels`. The classifier has its own rules file in the same folder. |
| 4 | Pan-ReDU export | `{REDU.relative_to(ROOT)}` ({n_redu_all:,} rows; SHA-256 `{redu_sha.hexdigest()}`) | Columns {", ".join(f"`{c}`" for c in REDU_COLUMNS)}. Only the {len(r):,} rows whose `ATTRIBUTE_DatasetAccession` is a study of this cohort are used. |

## Reading an mwTab file

The steps below are the steps of `read_mwtab`, in order.

1. The file is read as UTF-8 (bytes that cannot be decoded are replaced). `{backslash}r{backslash}n` is turned into `{backslash}n`, and the text is split
   into lines at `{backslash}n` only.
2. The quantitative block starts at the first line that begins with one of {", ".join(f"`{x}`" for x in DATA_BLOCK_START)}.
   From that line onwards no text is read. A `#END` line does not stop the reading: a few files begin with an empty
   template that ends in `#END`, followed by the real record.
3. The results-file line (section D) is looked for in the whole file, including after the quantitative block. The first
   one found is kept.
4. On lines that begin with `SUBJECT_SAMPLE_FACTORS`, only the raw-file names are taken (section E).
5. A field is a line whose third character is `:` and which contains a tab. The key is the text before the first tab; the
   value is the rest. Lines with the same key are joined with a space (these are continuation lines).
6. A section is the two-letter prefix of the key. The sections present in this cohort are
   {", ".join(sorted({k for k in sections_seen}))}. The text of a section is all its values joined with spaces.

## Modality

Decided in this order (column `modality`):

1. No mwTab file → "No mwTab file" ({int(mod_n.get("No mwTab file", 0)):,} records).
2. `AN:ANALYSIS_TYPE` contains "NMR", or the file has `NM:` fields and no `MS:` field → "NMR" ({int(mod_n.get("NMR", 0)):,}).
3. The file has an `MS:` field, or `AN:ANALYSIS_TYPE` contains "MS" → "MS" ({int(mod_n.get("MS", 0)):,}).
4. Otherwise → "Not stated" ({int(mod_n.get("Not stated", 0)):,}).

Sections A, F, G and I are filled for MS records only. For the other records `resolution_class` is blank and
`resolution_class_basis` is "not an MS analysis" (NMR), "modality not stated" or "no mwTab file".

"""
    rules = rules_front + f"""## A. Analyser class (MS analyses only)

The class is read twice, from `MS:INSTRUMENT_TYPE` and from `MS:INSTRUMENT_NAME`, and the two are combined. Both values
are trimmed and put in lower case before matching.

**Instrument type** (checked in this order; the first that applies decides)
1. Undeclared (`{sorted(UNDECLARED)}`) → no class.
2. Ion trap + Orbitrap hybrid → HR: `{TYPE_HYBRID_HR}`
3. The type matches BOTH the high-resolution pattern (4) and the nominal pattern (6) → MIXED. This conflict is checked before 4.
4. High-resolution type → HR: `{TYPE_HR}`
5. TOF-type without a hybrid designation → TOF (unresolved): `{TYPE_TOF}`
6. Nominal/unit-resolution type → NOM: `{TYPE_NOM}`
7. Anything else → no class.

**Instrument name** (each instrument named in the field, split at `{NAME_SPLIT}`)
1. High-resolution model → HR: `{NAME_HR}`
2. Unit-mass GC-TOF → NOM: `{NAME_UNIT_GCTOF}`
3. Nominal/unit-resolution model → NOM:
""" + "".join(f"   - {fam}: `{rx}`\n" for fam, rx in NAME_NOM) + f"""4. A TOF named without an informative model (`{NAME_TOF}`), e.g. "Agilent CE-TOFMS" or "Bruker Autoflex TOF/TOF" → TOF (unresolved).
5. Otherwise no class.
6. When the field names several instruments: one high-resolution and one nominal → MIXED; otherwise the class of the
   instruments that have one (HR or NOM); a TOF without a model counts only when no named instrument has a class.

**Combination**

| Type | Name | Class | Basis |
|---|---|---|---|
| HR | HR | {HR} | type and name agree |
| HR | none | {HR} | type only |
| HR | NOM | {IND} | type and name disagree |
| NOM | NOM | {NOM} | type and name agree |
| NOM | none | {NOM} | type only |
| NOM | HR | {IND} | type and name disagree |
| TOF | HR | {HR} | TOF type, high-resolution model named |
| TOF | unit-mass GC-TOF | {NOM} | TOF type, unit-mass GC-TOF model named |
| TOF | other NOM | {IND} | type and name disagree |
| TOF | none, or TOF without a model | {IND} | TOF type, model not informative |
| HR | TOF without a model | {HR} | type only |
| NOM | TOF without a model | {IND} | type and name disagree |
| none | TOF without a model | {IND} | TOF named, model not informative |
| none | HR | {HR} | name only |
| none | NOM | {NOM} | name only |
| none | none | {IND} | no analyser stated |
| MIXED (either) | any | {IND} | more than one instrument class named |

**Analyser family** (column `analyser_family`; a label for the tables, not an input to the class)
- From the type, the first of these that matches, in this order. The ion trap + Orbitrap hybrid of step 2 is "Orbitrap".
""" + "".join(f"  - {fam}: `{rx}`\n" for fam, rx in TYPE_FAMILY) + f"""- If the type gives no family, from the name: "high-resolution model", "GC-TOF (unit mass)", the family of the nominal
  pattern that matched (name step 3), or "TOF (model not informative)".
- A TOF type with a unit-mass GC-TOF model named is "GC-TOF (unit mass)".

Self-tests: {len(SELF_TESTS)} (type, name) pairs with the expected class (Appendix 2); the script stops if one fails.

## B. Acquisition-related wording detected (free text)

These text-derived categories describe deposited wording and were not treated as verified analysis-level acquisition types.

The text of a section is every field of that section joined with spaces. Every section of the file is searched. The
steps are those of `text_block`, in order.

1. `(un)targeted` (`{UN_BRACKET}`) is replaced by "untargeted and targeted" in every section.
2. Untargeted: a section is listed in `untargeted_stated_in_sections` when its text matches
   `{UNTARGETED}`
3. The untargeted words are then removed from every section, so that "untargeted" is never counted as "targeted".
4. Targeted, strict: a section is listed in `targeted_stated_in_sections` when the remaining text matches
   `{TARGETED_STRICT}`
   That is "targeted" followed within two words by an analysis term, or an analysis term followed by "targeted".
5. Type-field rule: in {TYPE_FIELDS} the word "targeted" alone (`{TARGETED_LOOSE}`, after steps 1 and 3) counts as
   targeted wording. The section (ST or PR) is added to the list of step 4, and `targeted_wording_in_type_field` is "yes".
6. Targeted, loose (given for comparison only): sections whose remaining text matches `{TARGETED_LOOSE}` are listed in
   `targeted_word_any_sense_in_sections`. This list is not used for the wording status.
7. Wording status, `acquisition_wording_any_section`: "both" when step 2 and steps 4-5 each list a section;
   "untargeted" when only step 2 does; "targeted" when only steps 4-5 do; blank otherwise.
8. `acquisition_wording_analysis_sections`: the same, counting only the analysis sections {ANALYSIS_SECTIONS}.
9. `acquisition_wording_level`: "analysis sections (AN, CH, MS)" when step 8 is not blank; "other sections only" when
   step 7 is not blank and step 8 is blank. Wording outside AN/CH/MS is reported separately; its applicability to the
   individual analysis was not established. The other sections are the study-level sections {STUDY_SECTIONS} and NM.
10. Keyword columns. They are not inputs to any other column.
    - `mrm_srm_prm_mentioned_analysis_sections`: `{MRM}` in the AN, CH and MS text joined together;
      `mrm_srm_prm_mentioned_any_section`: the same pattern in all sections joined together.
    - `ms2_or_dda_dia_mentioned_analysis_sections`: `{DDA_DIA}` in the AN, CH and MS text joined together.

## C. Resolution statements

- Fields `MS:RESOLUTION_SETTING` and `MS:MASS_ACCURACY`, as deposited.
- Text: `{RESOLUTION}` in the MS text followed by the AN text; the first match is kept, cut to 160 characters, and not validated.

## D. Repository markers

- Results-file line: the first line of the file that matches `{RESULTS_LINE}` (matched as written; a `#...` prefix
  before the key occurs in a few files). `results_file_line` is "MS results file" or "NMR results file".
- Attributes of that line: the line is split at tabs; from the third item onwards, each item that contains `:` is split
  at its first colon into a name (put in lower case) and a value. `Has m/z`, `Has RT`, `RT units` and `UNITS` are kept
  as deposited in `results_file_has_mz`, `results_file_has_rt`, `results_file_rt_units` and `results_file_units`;
  "not stated" means that the line exists without the attribute.
- "Declares m/z" = the value is `Yes` or `Neutral masses`; "declares RT" = the value is `Yes`.
- UNITS placeholders: a value matching `{UNITS_PLACEHOLDER}` is a placeholder. It stays in `results_file_units` and is
  blank in `results_file_units_declared`.
- Source validity (`mwtab_source`, `datatable_source`, `untarg_data_source`): copied from the Figure 2B table (Inputs, 2).
- Share of named labels (`*_pct_named_labels`): from the Text S3 classifier (Inputs, 3).
- untarg_data label type (`untarg_data_label_type`), from `untarg_data_pct_named_labels`: at most 10% named =
  "mostly without a metabolite name (<=10% named)"; at least 90% = "mostly named (>=90% named)"; otherwise "mixed";
  blank when there is no value.
- `repository_marker`: "valid untarg_data matrix and results-file declaration", "valid untarg_data matrix only",
  "results-file declaration only" or "none", from `untarg_data_source` = valid and `results_file_line` not blank.
- `items_detected`: an inventory of what was found, each item prefixed by its source. No item is labelled as evidence for
  an acquisition type. The ten items and their conditions:
  1. "wording: untargeted (AN/CH/MS sections)": `untargeted_stated_in_sections` lists AN, CH or MS.
  2. "wording: untargeted (other sections)": it lists any other section.
  3. "wording: targeted (AN/CH/MS sections)": `targeted_stated_in_sections` lists AN, CH or MS.
  4. "wording: targeted (other sections)": it lists any other section.
  5. "wording: MRM/SRM/PRM (AN/CH/MS sections)": `mrm_srm_prm_mentioned_analysis_sections` is "yes".
  6. "repository: valid untarg_data matrix": `untarg_data_source` is valid.
  7. "repository: results-file declaration": `results_file_line` is not blank.
  8. "repository: results file declares m/z and RT": m/z is `Yes` or `Neutral masses`, and RT is `Yes`.
  9. "labels: untarg_data mostly without a metabolite name": the label type above.
  10. "Pan-ReDU: MS2 spectra in matched files": `redu_analysis_ms2_status_strict` is "present" or "both".

## E. Pan-ReDU

- Rows used: those whose `ATTRIBUTE_DatasetAccession` is a study of this cohort ({len(r):,} of {n_redu_all:,}).
- Declared raw files of an analysis: `{RAW_FILE}` on the SUBJECT_SAMPLE_FACTORS lines of the mwTab file.
  `raw_file_names_in_mwtab` is the number of distinct names after the normalisation below.
- File-name normalisation, applied to the declared names and to the Pan-ReDU `filename`, in this order: surrounding
  spaces removed; `{backslash}` read as `/`; a trailing slash removed; the folder removed (the text after the last `/` is kept); lower
  case; a leading `f.` removed; one extension removed, `{RAW_EXTENSION}`. A raw-data directory (`folder/49.D/`) is
  therefore read as `49`. A value that is empty after these steps (for example `.D`) is not counted as a declared name.
- Extension of a Pan-ReDU file: `{REDU_FILE_EXTENSION}` on the lower-case file name.
- MS2 state of a Pan-ReDU file, assigned in this order (a later rule replaces an earlier one):
  1. not determined (the default; vendor formats, count -1 or no count);
  2. zero-count unclassified: `MS2spectra_count` = 0, extension `mzml` or `mzxml`, and `classification` = `Unclassified`;
  3. absent: `classification` = `NO_MS2`;
  4. present: `MS2spectra_count` > 0.
- Instrument model: the text before `|` in `MassSpectrometer`; the values `{sorted(REDU_MISSING)}` are treated as
  missing. Each model is classified with the instrument-name rules of section A. `redu_*_resolution_class` is
  "{HR}" when every classified model is high-resolution, "{NOM}" when every classified model is nominal,
  "both classes" when both occur, and blank when no model has a class (a TOF without an informative model has none).
- `redu_*_chromatography` and `redu_*_ionization`: the distinct values of `ChromatographyAndPhase` and
  `IonizationSourceAndPolarity`, missing values removed.
- `redu_*_acquisition_calls`: the values of `classification` other than `Unclassified`, each with its number of files.
- `redu_*_files`, `redu_*_files_ms2_present`, `redu_*_files_no_ms2`, `redu_*_files_zero_count_unclassified`,
  `redu_*_files_not_determined`: numbers of Pan-ReDU file records, in total and by MS2 state.
- `redu_*_ms2_status_strict`: "both" when present and absent files occur, "present", "absent", or blank.
  `redu_*_ms2_status_lenient`: the same, with zero-count unclassified files also counted as absent.
- Study level (`redu_study_*`): all Pan-ReDU rows of the study; `redu_study_listed` is "yes" when the study has rows.
  These items describe the study as a whole; their applicability to the individual analysis was not established.
- Analysis level (`redu_analysis_*`): the Pan-ReDU rows of the study whose normalised name is one of the declared names.
  `redu_raw_files_matched` is the number of such rows.
- `raw_file_set_shared_with_another_analysis`: "yes" when another record of the same study declares exactly the same set
  of names.
- `analyser_class_vs_redu_study` and `analyser_class_vs_redu_analysis`: blank when either side has no class or the
  analyser class is {IND}; "agree" when the two are equal; "ReDU has both classes"; otherwise "disagree".

## F. Acquisition evidence profile (MS records only)

The profile describes two inputs. It does not assign an acquisition type.

- **Wording** = `acquisition_wording_any_section` (section B).
- **Repository marker** = a results-file declaration in the mwTab file and/or a retrieved valid untarg_data matrix
  (column `repository_marker`, which keeps the two apart). A marker reports what was deposited in the repository; it is
  not an acquisition statement.
- Analyser class is not used to assign this evidence profile.

| Wording | Repository marker | Evidence profile | MS records |
|---|---|---|---:|
{profile_rows}| | | **Total** | **{profile_total:,}** |

Notes
- MRM / SRM / PRM wording and the Pan-ReDU items are recorded in their own columns and are not inputs to the profile.
- "Both targeted and untargeted wording detected" reports wording only; it does not mean that the analysis used both
  acquisition modes. Most of this wording is outside the AN, CH and MS sections.
- A repository marker can be a declaration without a retrieved valid matrix (`results-file declaration only`).
- Absence of a marker is not evidence of targeted acquisition.
- The profile depends on the untarg_data endpoint: "Targeted wording; repository marker absent" excludes every valid
  untarg_data matrix by definition. It must not be used to compare source availability; analyser capability is the
  grouping for stratification.
- Non-MS records have a blank profile.
- Tests: all eight (wording, marker) combinations are checked on every run, and the profile sheet is asserted to account
  for every MS record.

## G. Pan-ReDU MS2 evidence and analysis shortlist (MS records only)

- Input: `all_sampleinformation.tsv`, joined by study accession and normalized raw filename, using section E's matching rule.
- Linked-file evidence: MS2 is detected when `MS2spectra_count` > 0. A no-MS2 call requires `classification` = `NO_MS2`.
  A zero count on an unclassified file, an unavailable count or a vendor-format file without an explicit call remains unresolved.
- `panredu_ms2_evidence_status` distinguishes positive evidence, positive plus no-MS2 files, no-MS2 in every matched file,
  no-MS2 calls plus unresolved files, unresolved matched files, and no matching files. The older strict/lenient columns are retained.
- `panredu_ms2_link_scope` flags any matched raw filename also declared by another analysis of the same study, including
  partial overlap between their file rosters. Such links receive `Shared-file evidence only` in the analysis shortlist.
- For links without shared filenames, detected MS2 requires at least one positive count. No MS2 reported requires a complete
  match of every declared filename and NO_MS2 in every matched file record. Partial coverage and unresolved records stay separate.
- `panredu_ms2_raw_file_coverage_pct` = 100 x distinct matched filenames / distinct declared filenames; blank if no filenames
  are declared. This describes link coverage, not the fraction of samples with MS2.
- Non-MS records have blank G columns. MS2 evidence does not establish targeted/untargeted acquisition, spectrum quality,
  identification confidence, or the absence of MS2 in files not available in this export.
- Nine decision tests run on every build (Appendix 2). Both sections of the Pan-ReDU MS2 sheet must sum to all MS records.

Decision order (function `ms2_shortlist_labels`). The inputs are the numbers of matched Pan-ReDU file records that are
present, absent (NO_MS2) and unresolved (zero-count unclassified plus not determined, section E), and the numbers of
matched, declared and shared names. A name is shared when another record of the same study, of any modality, also
declares it.
1. No declared name matches a Pan-ReDU file of the study → all three columns are "No matching files".
2. `panredu_ms2_evidence_status`, the first that applies: present and absent → "MS2 detected + no-MS2 files"; present →
   "MS2 detected"; absent and unresolved → "No-MS2 calls; other files unresolved"; absent → "No MS2 reported in all
   matched files"; otherwise "Matched files; MS2 status unresolved".
3. `panredu_ms2_link_scope`: at least one shared name → "Shared across analyses"; otherwise "Analysis-specific filename
   match".
4. `panredu_ms2_analysis_shortlist`, the first that applies: a shared name → "Shared-file evidence only"; present →
   "MS2 detected"; absent, nothing unresolved and every declared name matched → "No MS2 reported; complete file-roster
   match"; absent and nothing unresolved → "MS2 absence assessed in a subset of files"; otherwise "MS2 status unresolved".

Scope of the evidence
- Section G recounts what Pan-ReDU reports for raw files (`MS2spectra_count`, `classification`) and attaches it to analyses
  through raw-file names. It does not re-read raw data and makes no statement about acquisition type or analyser class.
- A call for an analysis rests on a file-name match, not on an identifier. "No MS2 reported; complete file-roster match"
  holds only as far as Pan-ReDU's NO_MS2 label is correct and the mwTab file declares all raw files of the analysis;
  "complete" refers to the declared raw-file names, not to the samples.
- File names are compared without their folder, so one declared name can match files in more than one Pan-ReDU folder.
  Among the analyses with firm or partial evidence this occurs once: in ST003120/AN005114 the 16 matched names each occur
  in two folders (negative and positive mode). Both copies report MS2 spectra, so "MS2 detected" holds for either folder,
  but which copy belongs to the analysis is not established.
- The shared-file rule withholds a call whenever a matched name is also declared by another analysis of the study.
- In this cohort, all 148 analyses with firm or partial evidence belonged to studies containing only one MS analysis. This
  is an observed property, not a requirement of the shared-file rule.

Independent checks (separate scripts in this folder; neither is part of the build)
- `audit_panredu_ms2_independent.py`: a second implementation of the G columns, compared with `per_analysis_evidence.tsv`
  (the table the workbook is written from) for every record. It does not read the workbook itself.
- `validate_panredu_ms2_claude.py`: a third implementation that imports nothing from the build or from the other audit. It
  recounts every MS record from the mwTab files and the Pan-ReDU export, and stops unless all of the following hold:
  the record keys are unique in every table and join one to one; the evidence status, link scope, shortlist label,
  shared-file count and raw-file coverage (blank cells included) are identical to the workbook for all 6,696 records; the
  workbook's G columns equal `per_analysis_evidence.tsv`; the "Pan-ReDU MS2" sheet equals the recount by analyser class,
  with its totals and percentages; and the shortlist file has no duplicate record, the same records and the same labels.
  Its report, `panredu_ms2_claude_validation.txt`, also lists the shortlisted analyses that match several folders, with
  the call obtained from each folder.

## H. What the rules do not infer

- **Analyser class** (A) is assigned from the instrument type and name only. Acquisition wording, repository markers,
  feature labels and Pan-ReDU items are not inputs. A class is given only when a high-resolution or a nominal analyser is
  positively named and the other field does not contradict it; every other case is Indeterminate.
- **HRMS-capable does not mean untargeted, and nominal/unit-resolution does not mean targeted.** No rule and no column
  derives an acquisition type from the analyser class, or the analyser class from acquisition information.
- **Acquisition-related wording** (B) reports words found in the deposited text. It does not handle negation
  ("not targeted"), and the wording may describe another part of the study.
- **Resolution statements** (C) are text fragments; no class is derived from them.
- **Repository markers** (D) report what was deposited. A marker is not an acquisition statement.
- **Pan-ReDU items** (E, G) describe raw files. They do not establish an acquisition type, and study-level items were not
  established for the individual analysis.
- **The evidence profile** (F) describes its two inputs; it is not an acquisition type.
- **Reported scan-mode evidence** (I) reports scan-mode terms found in the deposited text. Selected-mode terms are not read
  as targeted, and full-scan, DDA or DIA terms are not read as untargeted. Analyser class, targeted/untargeted wording and
  repository markers are not inputs, and the two instrument fields are not searched.
- `items_detected` is an inventory; no item is labelled as evidence for an acquisition type.
- Enforced on every run: the class function accepts only the type and the name; the profile function accepts only the wording
  and the marker; identical text with an HRMS-capable and with a nominal instrument must give identical wording; and each
  derived column is asserted to be fully determined by its stated inputs.
- Also enforced on every run for section I: the description function accepts only the two term flags; identical text with an
  HRMS-capable and with a nominal instrument must give identical scan-mode columns; and the description is asserted to be
  fully determined by the terms detected.

## I. Reported scan-mode evidence (MS records only)

The column describes which scan-mode terms occur in the deposited text. It does not assign targeted or untargeted
acquisition. The rules are defined in `scan_mode_evidence.py`; the steps below are the steps the code runs, in the same order.

In this section a pattern is run without flags: `(?i:...)` marks a part that ignores capitalisation, and anything outside
it is matched exactly as written.

**Step 1. Fields searched**
- The fields of sections {ANALYSIS_SECTIONS}, in the order of the mwTab file. A field is one key with its continuation
  lines joined.
- `{SM.NOT_SEARCHED[0]}` and `{SM.NOT_SEARCHED[1]}` are not searched (they define the analyser class).
- Each term is looked for inside one field at a time, never across two fields. The first field that matches is kept.

**Step 2. Selected-mode terms**
- MRM / SRM: `{SM.MRM}`. "SRM" followed by a 3- or 4-digit number (NIST SRM 1950, a reference material) does not count.
  The plural counts only as "MRMs"; "MRMS" (magnetic resonance mass spectrometry) does not count.
- PRM: `{SM.PRM}`
- SIM, in two passes over the field:
  1. These named forms are looked for in this order, and each one found is blanked out of the field:
""" + "".join(f"     - {form}: `{rx}`\n" for form, rx in SM.SIM_FORMS) + f"""  2. SIM is then looked for in what remains: `{SM.SIM}`. The abbreviation counts in upper case only (SIM, tSIM, t-SIM).

**Step 3. Full-scan, DDA and DIA terms**
- full scan / MS1: `{SM.FULL_SCAN}`
- DDA, in two passes over the field:
  1. `{SM.DDA}`. "IDA" counts in upper case only.
  2. If pass 1 finds nothing, each "top N" (`{SM.TOP_N}`) is checked in turn. It counts only when
     `{SM.TOP_N_NEAR}` occurs within {SM.TOP_N_WINDOW} characters before or after it, in the same field.
- DIA: `{SM.DIA}`. "DIA", "AIF" and "MSE" / "MSe" / "Mse" count only as written.

**Step 4. Description of the record**

| A selected-mode term found (step 2) | A full-scan, DDA or DIA term found (step 3) | `reported_scan_mode_evidence` | MS records |
|---|---|---|---:|
{scan_rows}| | | **Total** | **{profile_total:,}** |

**Step 5. Columns written**
- `reported_scan_mode_evidence`: the description from step 4.
- `scan_mode_terms_detected`: the terms found, in the fixed order {"; ".join(SM.TERM_ORDER)}.
- `scan_mode_matched_fields`: for each term found, the first field in which it matched.
- `scan_mode_excerpts`: for each term found, the matched text with {SM.EXCERPT} characters on each side; control characters
  are replaced by a space.
- Non-MS records have blank I columns.

Notes
- A term is a keyword match in deposited text. The matches were not validated by reading every record, negation is not
  handled, and a term may describe only part of the acquisition (for example annotation or quality-control runs).
- Not inputs to the description: "transitions", generic "MS/MS" or "tandem", "Auto MS/MS", the three named SIM forms of
  step 2, calibration and quantification wording, and provider or kit names.
- The keyword columns of section B (`mrm_srm_prm_mentioned_*`, `ms2_or_dda_dia_mentioned_analysis_sections`) are unchanged;
  they use the broader patterns of section B and are not inputs here.
- The acquisition evidence profile (F) is unchanged; scan-mode terms are not inputs to it.
- Tests: {len(SM.TERM_TESTS)} term tests and the four combinations of step 4 are checked on every run, and the sheet
  "Reported scan-mode evidence" is asserted to account for every MS record.
"""
    import explore_scan_mode_terms as X   # exploratory terms: printed in Appendix 3 only; they fill no workbook column
    pat = lambda v: v.pattern if hasattr(v, "pattern") else v[0]
    explore_rows = "".join(f"- {k}: `{pat(v)}`\n" for k, v in X.TERMS.items() if v is not None and k not in SM.TERM_ORDER)
    sheet_notes = [
        ("README", "purpose, unit, data and how to read the workbook"),
        ("Coverage", "for each item, the number of records and of MS records that have it, with percentages. A type or name field counts as filled when its value is not in the undeclared list of section A"),
        ("Per analysis", f"one row per record, {len(d.columns)} columns (Appendix 1)"),
        ("Column guide", "the definitions of Appendix 1"),
        ("Class by source", "for each analyser class (and for NMR, not stated, no mwTab file): the number of records and how many have a valid datatable, mwTab and untarg_data source"),
        ("Class basis", "MS records by analyser class and by how the class was reached"),
        ("Analyser families", "MS records by analyser class and analyser family"),
        ("Acquisition evidence profile", "the profiles of section F by analyser class"),
        ("Reported scan-mode evidence", "the descriptions and terms of section I by analyser class"),
        ("Pan-ReDU MS2", "the evidence and shortlist of section G by analyser class"),
        ("Instrument strings", "every distinct pair of instrument type and name, with its class, basis and family"),
        ("Cross-tabulations", "analyser class against: wording (any section); results-file line; Pan-ReDU MS2 status (study level, strict); agreement with the Pan-ReDU instrument class (study level)"),
    ]
    assert [n for n, _ in sheet_notes] == [n for n, _ in workbook_sheets], "a workbook sheet is not described in RULES.md"
    rules_back = f"""
## Outputs

All outputs are written to this folder by `build_analysis_evidence.py`.

Workbook `Reviewer1_Comment1_analysis_evidence.xlsx`, sheets:
""" + "".join(f"- **{n}**: {t}.\n" for n, t in sheet_notes) + f"""
Tables
- `per_analysis_evidence.tsv`: the "Per analysis" sheet.
- `coverage.tsv`: the "Coverage" sheet.
- `instrument_strings.tsv`: the "Instrument strings" sheet.
- `panredu_ms2_distribution.tsv`: the "Pan-ReDU MS2" sheet.
- `panredu_ms2_per_analysis.tsv`: the section G columns of every record.
- `panredu_ms2_analysis_shortlist.tsv`: the MS records with "MS2 detected" or "No MS2 reported; complete file-roster match".
- `RULES.md`: this file.

Code
- `build_analysis_evidence.py`: sections A to H, and the workbook.
- `scan_mode_evidence.py`: the terms and the description of section I; imported by the build.
- `explore_scan_mode_terms.py`: exploratory counts (Appendix 3); it changes no workbook column.
- `build_terms_table.py`: writes `TERMS_TABLE.xlsx` and `TERMS_TABLE.tsv`, one table with one row per term or condition
  used to derive every category (each pattern of this file split into its alternatives), with the number of MS records
  each term matches, the text it most often matched, and the decision behind it. It is generated from the same constants.
- `validate_panredu_ms2_claude.py`, `audit_panredu_ms2_independent.py`, `audit_ms2_artifacts.py`: independent checks
  of section G (see section G).

## Checks run on every build

The build stops if one fails.

- Test cases (Appendix 2): {len(SELF_TESTS)} for the analyser class, {len(PROFILE_TESTS)} for the evidence profile, {len(MS2_TESTS)} for the
  MS2 decision, {len(NORM_RAW_TESTS)} for the file-name normalisation, {len(SM.TERM_TESTS)} for the scan-mode terms and {len(SM.EVIDENCE_TESTS)} for the scan-mode
  description.
- No cross-inference (section H): the class function accepts only the instrument type and name; the profile function
  accepts only the wording and the marker; the scan-mode description function accepts only the two term flags; the two
  instrument fields are not searched for scan-mode terms; identical text with an HRMS-capable and with a nominal
  instrument gives identical wording and identical scan-mode columns.
- Record list: {n_all:,} records.
- Determined by their stated inputs, checked on the data: the analyser class by (type, name); the evidence profile by
  (wording, marker present); the wording status by (sections with untargeted wording, sections with targeted wording); the
  scan-mode description by the terms detected.
- MS records all have an evidence profile and a scan-mode description; all other records are blank in those columns.
- The sheets "Acquisition evidence profile", "Reported scan-mode evidence" and "Pan-ReDU MS2" each account for every
  MS record.
- Every column of the per-analysis table has an entry in the column guide, and every sheet is described under "Outputs".
- Every pattern used by the code appears in this file.

## Appendix 1. Columns of the per-analysis table

{len(d.columns)} columns. `*` stands for any text. The block is the section of this file that defines the column.

| Column(s) | Block | Definition and source |
|---|---|---|
""" + "".join(f"| {md(a)} | {md(b)} | {md(c)} |\n" for a, b, c in guide.itertuples(index=False)) + f"""
## Appendix 2. Test cases

**A. Analyser class** ({len(SELF_TESTS)} cases)

| Instrument type | Instrument name | Expected class |
|---|---|---|
""" + "".join(f"| {md(t) or '(blank)'} | {md(n) or '(blank)'} | {c} |\n" for (t, n), c in SELF_TESTS.items()) + f"""
**E. File-name normalisation** ({len(NORM_RAW_TESTS)} cases)

| Value | Read as |
|---|---|
""" + "".join(f"| {('`' + k + '`') if k else '(blank)'} | {('`' + w + '`') if w else '(nothing; not a name)'} |\n" for k, w in NORM_RAW_TESTS.items()) + f"""
**F. Evidence profile** ({len(PROFILE_TESTS)} cases)

| Wording | Repository marker | Expected profile |
|---|---|---|
""" + "".join(f"| {WORDING_TEXT[w]} | {'present' if m else 'absent'} | {lab} |\n" for (w, m), lab in PROFILE_TESTS.items()) + f"""
**G. MS2 decision** ({len(MS2_TESTS)} cases; the six numbers are file records present, absent and unresolved, then names matched, declared and shared)

| Present | Absent | Unresolved | Matched | Declared | Shared | Evidence | Scope | Shortlist |
|---:|---:|---:|---:|---:|---:|---|---|---|
""" + "".join("| " + " | ".join(map(str, i)) + " | " + " | ".join(o) + " |\n" for i, o in MS2_TESTS) + f"""
**I. Scan-mode terms** ({len(SM.TERM_TESTS)} cases)

| Term | Text | Found? |
|---|---|---|
""" + "".join(f"| {k} | {md(t)} | {'yes' if w else 'no'} |\n" for (k, t), w in SM.TERM_TESTS.items()) + f"""
**I. Scan-mode description** ({len(SM.EVIDENCE_TESTS)} cases)

| Selected-mode term found | Full-scan, DDA or DIA term found | Expected description |
|---|---|---|
""" + "".join(f"| {'yes' if a else 'no'} | {'yes' if b else 'no'} | {lab} |\n" for (a, b), lab in SM.EVIDENCE_TESTS.items()) + f"""
## Appendix 3. Exploratory patterns (not in the workbook)

`explore_scan_mode_terms.py` measured how often further wording occurs in the MS records, to decide what was worth adding.
Its output is `scan_mode_terms_exploration.tsv`. The patterns below fill no workbook column and are not inputs to any
rule above. They ignore capitalisation, and each is matched inside one field at a time, as in section I.

{explore_rows}- absolute quantification: `{X.ABSOLUTE.pattern}`. It is counted as negated when the 100 characters before it match
  `{X.NEGATION.pattern}`
- provider or kit named:
""" + "".join(f"  - {name}: `{rx}`\n" for name, rx in X.PROVIDERS) + f"""- structured MS fields counted as filled: {", ".join(f"`{k}`" for k in X.FIELDS)}; a value matching
  `{X.PLACEHOLDER.pattern}` is a placeholder and is not counted.
"""
    rules += rules_back
    # every pattern the code runs must be printed in RULES.md
    used = {"TYPE_HYBRID_HR": TYPE_HYBRID_HR, "TYPE_HR": TYPE_HR, "TYPE_TOF": TYPE_TOF, "TYPE_NOM": TYPE_NOM, "NAME_HR": NAME_HR,
            "NAME_TOF": NAME_TOF, "NAME_UNIT_GCTOF": NAME_UNIT_GCTOF, "NAME_SPLIT": NAME_SPLIT, "UNTARGETED": UNTARGETED,
            "TARGETED_STRICT": TARGETED_STRICT, "TARGETED_LOOSE": TARGETED_LOOSE, "UN_BRACKET": UN_BRACKET, "MRM": MRM, "DDA_DIA": DDA_DIA,
            "RESOLUTION": RESOLUTION, "RAW_FILE": RAW_FILE, "UNITS_PLACEHOLDER": UNITS_PLACEHOLDER, "RESULTS_LINE": RESULTS_LINE,
            "RAW_EXTENSION": RAW_EXTENSION, "REDU_FILE_EXTENSION": REDU_FILE_EXTENSION,
            **{f"TYPE_FAMILY {k}": rx for k, rx in TYPE_FAMILY}, **{f"NAME_NOM {k}": rx for k, rx in NAME_NOM},
            "SM.MRM": SM.MRM, "SM.PRM": SM.PRM, "SM.SIM": SM.SIM, "SM.FULL_SCAN": SM.FULL_SCAN, "SM.DDA": SM.DDA, "SM.TOP_N": SM.TOP_N,
            "SM.TOP_N_NEAR": SM.TOP_N_NEAR, "SM.DIA": SM.DIA, **{f"SM.SIM_FORMS {k}": rx for k, rx in SM.SIM_FORMS}}
    missing = [k for k, rx in used.items() if f"`{rx}`" not in rules]
    assert not missing, f"patterns not printed in RULES.md: {missing}"
    (OUT / "RULES.md").write_text(rules)
    print(coverage.to_string(index=False))
    print()
    print(by_src.to_string(index=False))
    print()
    print(basis.to_string(index=False))


if __name__ == "__main__":
    main()
