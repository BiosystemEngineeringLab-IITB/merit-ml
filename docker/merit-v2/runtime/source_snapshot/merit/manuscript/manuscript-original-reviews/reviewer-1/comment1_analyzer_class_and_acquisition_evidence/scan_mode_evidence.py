#!/usr/bin/python3
"""Reported scan-mode evidence (block I of build_analysis_evidence.py): scan-mode terms found in an mwTab record.

This module is the only place where the scan-mode terms are defined. build_analysis_evidence.py imports it to fill the
workbook columns and to print section I of RULES.md; explore_scan_mode_terms.py imports the same definitions.

What it does  For one MS record, each term is looked for inside one mwTab field at a time (a field = one key with its
              continuation lines joined), in the fields of the analysis sections, in file order. The first field that
              matches is kept, with an excerpt. The record then gets one of four descriptions: selected-mode terms
              only, full-scan/DDA/DIA terms only, both kinds, or no scan-mode term detected.
What it does not do  It does not assign targeted or untargeted acquisition. It does not read the analyser class, the
              targeted/untargeted wording or the repository markers, and the two instrument fields are not searched.
Patterns      Regular expressions, run without flags. `(?i:...)` marks a part that ignores capitalisation; anything
              outside it is matched exactly as written.
"""
from __future__ import annotations

import re

RM = r"(?:multiple|selected|selective)[- ]reaction[- ]monitoring"
# "SRM" followed by a 3- or 4-digit number is a NIST Standard Reference Material (SRM 1950), not selected reaction monitoring.
# The plural counts only as "MRMs" with a lower-case s: "MRMS" is magnetic resonance mass spectrometry (Bruker scimaX MRMS).
MRM = rf"(?i:\b[ds]?mrm\b|\bsrm\b(?!\s*\d{{3,4}}\b)|{RM})|\b[ds]?MRMs\b"
PRM = r"(?i:parallel[- ]reaction[- ]monitoring|\bprm\b)"
# Named SIM forms that are not a selected-ion acquisition of the study samples. They are looked for first, in this order,
# and blanked out of the field before SIM is looked for.
SIM_FORMS = [
    ("dd-SIM (data-dependent)", r"(?i:dd-?\s?sim\b)"),
    ("Full MS-SIM (full-scan mode name)", r"(?i:full\s?ms[-/ ]sim\b)"),
    ("SIM windows / SIM-stitching (wide-range)",
     r"(?i:sim[- ]stitch\w*|sim\s+windows?|overlapping\s+selected\s+ion\s+monitoring(?:\s*\(sim\))?\s+windows?)"),
]
# The abbreviation is matched in upper case only (SIM, tSIM, t-SIM); the long form ignores capitalisation.
SIM = r"(?i:(?:selected|single|selective|select)[- ]ion[- ]monitoring)|\bt-?SIM\b|\bSIM\b"
FULL_SCAN = r"(?i:full[- ]?scan|full[- ]?ms\b|\bms1\b|ms\s*1\s+scan)"
# "IDA" is matched in upper case only (an operator is called "Ida").
DDA = r"(?i:data[- ]dependent|\bdda\b|dd-?ms2|dd-?ms/?ms|information[- ]dependent)|\bIDA\b"
# "top N" counts as DDA only when precursor-selection or fragmentation wording occurs within TOP_N_WINDOW characters.
TOP_N = r"(?i:\btop[- ]?\d+\b)"
TOP_N_NEAR = (r"(?i:precursor|parent ion|\bions?\b|fragment|ms/?ms|ms2|ms\^?2|ms²|dd-?ms|\bdda\b|data[- ]dependent|\bida\b|"
              r"top ?n\b|method was (?:applied|used))")
TOP_N_WINDOW = 80
# "DIA", "AIF" and "MSE" / "MSe" / "Mse" are matched as written, so "dia." (diameter) and lower-case words do not count.
DIA = r"(?i:data[- ]independent|\bswath\b|all[- ]ion[- ]fragmentation)|\bDIA\b|\bAIF\b|\bM[Ss]\^?[Ee]\b"

SELECTED = ("MRM / SRM", "PRM", "SIM")                      # selected-reaction / selected-ion terms
FULL = ("full scan / MS1", "DDA", "DIA")                    # full-scan / data-dependent / data-independent terms
TERM_ORDER = SELECTED + FULL
NOT_SEARCHED = ("MS:INSTRUMENT_TYPE", "MS:INSTRUMENT_NAME")  # the two fields that define the analyser class
EXCERPT = 60                                                 # characters kept on each side of a match
CONTROL = r"[\x00-\x1f\x7f]"

# EVIDENCE is the only place where the four descriptions are defined: (selected-mode term found, full-scan/DDA/DIA term found).
EVIDENCE = [
    (True, False, "Selected-mode terms only (MRM/SRM, PRM, SIM)"),
    (False, True, "Full-scan, DDA or DIA terms only"),
    (True, True, "Both kinds of term"),
    (False, False, "No scan-mode term detected"),
]
EVIDENCE_LOOKUP = {(s, f): lab for s, f, lab in EVIDENCE}
EVIDENCE_ORDER = [lab for _, _, lab in EVIDENCE]
NONE_DETECTED = EVIDENCE_LOOKUP[(False, False)]
COLUMNS = ("reported_scan_mode_evidence", "scan_mode_terms_detected", "scan_mode_matched_fields", "scan_mode_excerpts")


def scan_mode_evidence(selected: bool, full: bool) -> str:
    return EVIDENCE_LOOKUP[(bool(selected), bool(full))]


def find_plain(pattern: str, text: str):
    m = re.search(pattern, text)
    return (m.start(), m.end()) if m else None


def sim_forms(text: str) -> tuple[list[str], str]:
    """Named SIM forms present in a field, and the field with those forms blanked out."""
    forms = []
    for form, pat in SIM_FORMS:
        if re.search(pat, text):
            forms.append(form)
            text = re.sub(pat, lambda m: " " * len(m.group()), text)
    return forms, text


def find_sim(text: str):
    return find_plain(SIM, sim_forms(text)[1])


def find_dda(text: str):
    hit = find_plain(DDA, text)
    if hit:
        return hit
    for t in re.finditer(TOP_N, text):
        if re.search(TOP_N_NEAR, text[max(0, t.start() - TOP_N_WINDOW):t.end() + TOP_N_WINDOW]):
            return t.start(), t.end()
    return None


FINDERS = {
    "MRM / SRM": lambda t: find_plain(MRM, t), "PRM": lambda t: find_plain(PRM, t), "SIM": find_sim,
    "full scan / MS1": lambda t: find_plain(FULL_SCAN, t), "DDA": find_dda, "DIA": lambda t: find_plain(DIA, t),
}
assert tuple(FINDERS) == TERM_ORDER


def excerpt(text: str, start: int, end: int) -> str:
    return re.sub(CONTROL, " ", "…" + text[max(0, start - EXCERPT):end + EXCERPT] + "…")


def first_matches(fields: dict, sections: tuple[str, ...]) -> dict:
    """term -> (field key, field text, (start, end)) for the first searched field in which the term is found."""
    searched = [(k, v) for k, v in fields.items() if k[:2] in sections and k not in NOT_SEARCHED]
    out = {}
    for term, find in FINDERS.items():
        hit = next(((k, v, span) for k, v in searched if (span := find(v))), None)
        if hit:
            out[term] = hit
    return out


def scan_mode_columns(fields: dict, sections: tuple[str, ...]) -> dict:
    """The four workbook columns of one MS record."""
    hits = first_matches(fields, sections)
    return {
        "reported_scan_mode_evidence": scan_mode_evidence(any(t in hits for t in SELECTED), any(t in hits for t in FULL)),
        "scan_mode_terms_detected": "; ".join(hits),
        "scan_mode_matched_fields": "; ".join(f"{t}: {k}" for t, (k, _, _) in hits.items()),
        "scan_mode_excerpts": " || ".join(f"{t}: {excerpt(v, *span)}" for t, (_, v, span) in hits.items()),
    }


TERM_TESTS = {  # (term, text) -> is the term found?
    ("MRM / SRM", "NIST SRM 1950 serum sample"): False,
    ("MRM / SRM", "dwell time was 3 ms per SRM transition"): True,
    ("MRM / SRM", "Scheduled MRMs were based on retention time"): True,
    ("MRM / SRM", "dynamic multiple reaction monitoring (dMRM)"): True,
    ("MRM / SRM", "analysed with MRMPROBS"): False,
    ("MRM / SRM", "Bruker scimaX MRMS"): False,
    ("MRM / SRM", "dMRMs were divided into three methods"): True,
    ("PRM", "parallel reaction monitoring (PRM) mode"): True,
    ("SIM", "SIM; peak integration, concentrations calculated"): True,
    ("SIM", "Selected ion monitoring was used in the negative ion mode"): True,
    ("SIM", "A targeted selected ion monitoring (tSIM) scan was also included"): True,
    ("SIM", "a data dependent (dd-MS2/dd-SIM) manner"): False,
    ("SIM", "acquired in full MS-SIM mode at 140,000 resolution"): False,
    ("SIM", "SIM windows were assembled into single spectra (SIM-Stitching)"): False,
    ("SIM", "processed with SIMCA"): False,
    ("full scan / MS1", "acquired in full MS-SIM mode at 140,000 resolution"): True,
    ("full scan / MS1", "FullMS data of each sample"): True,
    ("DDA", "the top 60 important signals (based on feature importance)"): False,
    ("DDA", "The top 5 precursor ions were subsequently fragmented"): True,
    ("DDA", "a data dependent (dd-MS2/dd-SIM) manner"): True,
    ("DDA", "Ida Erngren"): False,
    ("DDA", "acquired in IDA mode"): True,
    ("DDA", "Auto MS/MS"): False,
    ("DIA", "2.1 mm dia. column"): False,
    ("DIA", "in MSE mode"): True,
    ("DIA", "CID, Mse"): True,
    ("DIA", "SWATH acquisition"): True,
}
EVIDENCE_TESTS = {  # independent statement of the expected result for the four combinations
    (True, False): "Selected-mode terms only (MRM/SRM, PRM, SIM)",
    (False, True): "Full-scan, DDA or DIA terms only",
    (True, True): "Both kinds of term",
    (False, False): "No scan-mode term detected",
}


def self_test() -> None:
    import inspect
    bad = [(k, t) for (k, t), want in TERM_TESTS.items() if bool(FINDERS[k](t)) != want]
    assert not bad, bad
    assert set(EVIDENCE_LOOKUP) == set(EVIDENCE_TESTS) and len(EVIDENCE) == 4, "the four combinations must each be defined once"
    assert all(scan_mode_evidence(*k) == lab for k, lab in EVIDENCE_TESTS.items())
    assert list(inspect.signature(scan_mode_evidence).parameters) == ["selected", "full"]   # the description: two flags only
    # One field at a time: a "top N" in one field is not completed by precursor wording in another field.
    sec = ("AN", "CH", "MS")
    assert scan_mode_columns({"MS:MS_COMMENTS": "the top 60 important signals", "CH:CHROMATOGRAPHY_SUMMARY": "precursor ions"}, sec)[
        "reported_scan_mode_evidence"] == NONE_DETECTED
    # The instrument fields are not searched, a term outside the analysis sections does not count, and the same text gives
    # the same result whatever instrument is named.
    assert scan_mode_columns({"MS:INSTRUMENT_NAME": "MRM instrument", "ST:STUDY_SUMMARY": "MRM and full scan"}, sec)[
        "reported_scan_mode_evidence"] == NONE_DETECTED
    text = {"MS:MS_COMMENTS": "Data were acquired in MRM mode."}
    a = scan_mode_columns({"MS:INSTRUMENT_TYPE": "Orbitrap", "MS:INSTRUMENT_NAME": "Thermo Q Exactive Orbitrap", **text}, sec)
    b = scan_mode_columns({"MS:INSTRUMENT_TYPE": "Triple quadrupole", "MS:INSTRUMENT_NAME": "Agilent 6490 QQQ", **text}, sec)
    assert a == b and a["reported_scan_mode_evidence"] == EVIDENCE_LOOKUP[(True, False)] and a["scan_mode_matched_fields"] == "MRM / SRM: MS:MS_COMMENTS"
    c = scan_mode_columns({"MS:MS_COMMENTS": "Full scan with data-dependent MS2; PRM for selected lipids."}, sec)
    assert c["reported_scan_mode_evidence"] == EVIDENCE_LOOKUP[(True, True)] and c["scan_mode_terms_detected"] == "PRM; full scan / MS1; DDA"


if __name__ == "__main__":
    self_test()
    print(f"{len(TERM_TESTS)} term tests and {len(EVIDENCE_TESTS)} combination tests passed")
