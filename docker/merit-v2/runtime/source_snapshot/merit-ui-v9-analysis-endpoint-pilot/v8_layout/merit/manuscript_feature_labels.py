"""Feature-label rules of the manuscript (Text S3, Table S1a-c), used by the UI so that both classify labels the same way.

Ported unchanged from the scripts behind Table S1 and Figure 4C:
  outputs/annotation_coverage_20260928/feature_classifier.py   label rules, applied in priority order (Table S1a)
  outputs/annotation_coverage_20260928/collect_labels.py       which labels of a deposited file are feature labels
  outputs/annotation_coverage_20260928/classify_all.py         context rules within one matrix (Table S1b), groups, bounds

The unit is one feature label in one matrix. Every label ends in one final class, and every class in one group:
  NAMED     named, named_by_refmet, resolved_identifier                      a specific metabolite name is reported
  WITHOUT   unresolved_identifier, class_level, tentative, formula, mz_rt,   no specific metabolite name is reported
            mz_only, nmr_bin, feature_id, unknown
  EXCLUDED  excluded_empty, sample_metadata, non_metabolite, malformed_matrix   not a metabolite feature
Eligible features = NAMED + WITHOUT. Coverage = NAMED / eligible. The classification says whether a name was reported;
it does not assess whether the identification is correct.

Label classes returned by classify():
  named              chemical name (incl. adduct / isotope / polarity / RT / duplicate suffixes, lipid species, named
                     internal standards)
  database_id        HMDB / ChEBI / KEGG compound / LIPID MAPS / PubChem CID / InChIKey syntax      (resolved in context)
  catalog_id         MDL (MFCD) / NP- / RTECS / Cayman (CAY) / MMV / NCI (NSC) registry or catalog syntax (resolved)
  protein_accession  UniProt accession syntax, alone or ';'-separated                              (resolved in context)
  short_code         1-4 letters + >= 3 digits ('VD2716', 'SB 203580'): generic-ID series or compound code (context)
  class_level        a compound class or lipid class without a species ('Hexose', 'PC', 'Hexitols')
  tentative          explicitly tentative annotation ('POSSIBLY C10H13NO2', '[Similar to: ...]')
  formula            elemental formula only (after removing adduct notation)
  mz_rt / mz_only / nmr_bin / feature_id / unknown   coordinate-only, identifier-only or placeholder labels
  sample_metadata    reserved sample-metadata word used as a label ('Days', 'Phenotype')
  excluded_empty     empty label
"""
from __future__ import annotations

import collections
import functools
import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------------------------------------------------
# Label rules (feature_classifier.py, unchanged)
# ---------------------------------------------------------------------------------------------------------------------
NUM = r"\d+(?:\.\d*)?|\.\d+"
# helper patterns used inside classify() (named here so RULES.md quotes exactly what runs)
TRAILING_PAREN = r"\(\d+\)$"                                  # trailing '(6)'
DUP_SUFFIX = r"(?<=[A-Za-z])\.\d+$|\s*\(\d+\)$"               # '.1' after a letter, or trailing '(2)'
COMPACT_SEP = r"(?<=[A-Za-z])[\s_-]+(?=\d)"                    # separators between a letter and a following digit
NULL_RUN = r"(?i)(?:null)+"                                     # 'nullnullnull' stands where a polarity tag would be
MZ_WORD = r"(?i)m/z"

# R1 -- sample-metadata words (whole label, case-insensitive)
METADATA = re.compile(r"^(?:class|classes|sample|samples|factor|factors|group|label|sample[ _]?id|samples?[ _]?name|"
                      r"phenotype|genotype|condition|treatment|time|time[ _]?point|day|days|sex|gender|age|batch|"
                      r"replicate)$", re.I)
# R2 -- placeholders for 'no identification'
UNKNOWN_A = re.compile(r"^(?:<\s*none\s*>|>\s*limit)")
UNKNOWN_B = re.compile(r"^(?:no\s*name|noname|no\s*id|noid)(?![a-z])", re.I)
UNKNOWN_C = re.compile(r"^(?:unknown|unkn|unk|unidentified|unassigned|not[\s_-]?identified|unannotated|unnamed)"
                       r"(?:[\s_#:.,()\[\]-].*|\d.*)?$", re.I)
UNKNOWN_D = {"NA", "N/A", "na", "n/a", "nan", "NaN", "NAN", "none", "None", "NONE", "null", "NULL"}
UNKNOWN_E = re.compile(r"^(?:-+|\?+)$")
UNKNOWN_F = re.compile(r"^x\s*-\s*\d+(?:\s*[:;,(].*)?$", re.I)          # Metabolon unknown, optional suffix
# R3 -- tentative annotations
TENTATIVE = re.compile(r"^\[?\s*(?:possibly|putative(?:ly)?|tentative(?:ly)?|similar\s+to|likely)\b", re.I)
# adduct notation (used by R4 and R14)
AG = r"(?:H|Na|K|Li|NH4|NH3|Cl|Br|HCOO|HCOOH|FA|CH3COO|OAc|Ac|HAc|H2O|ACN|CH3CN|MeOH|H2CO2|e)"
ADDUCT = re.compile(rf"(?<![A-Za-z0-9])[\[(]?\s*\d*M\s*(?:[+-]\s*\d*{AG})+\s*[\])]?\s*\d*[+-]?(?![A-Za-z0-9])"
                    rf"|\[\s*\d*M\s*\]\s*\d*[+-]")
ADDUCT_BRACKETED = re.compile(rf"[\[(]\s*\d*M\s*(?:[+-]\s*\d*{AG})*\s*[\])]\s*\d*[+-]?")   # no boundary requirement
# R4 -- formula only
ELEM = r"(?:Cl|Br|Na|Si|Se|C|H|N|O|S|P|F|I|K)"
FORMULA = re.compile(rf"^C\d*\s*(?:{ELEM}\d*\s*)+(?:[+-]\d*)?(?:\s*(?i:esi|pos|neg)\s*[+-]?\s*(?:{NUM})|\s*[+-]\s*(?:{NUM}))?"
                     rf"(?:\s*:\s*\d+)?$")
# R5-R8 -- instrument / software feature codes
CSH = re.compile(r"^CSH_(?:pos|neg)ESI(?![A-Za-z])", re.I)                 # 'CSH_negESI109' and 'CSH_posESI #285' 
CODED_MZRT = re.compile(r"^M\d+\s*\([^)]*\)\s*,\s*BP\s*(?:\d+(?:\.\d*)?|\.\d+)\s*,\s*RT\s*(?:\d+(?:\.\d*)?|\.\d+)$", re.I)
XCMS = re.compile(r"^M\d+(?:\.\d+)?T\d+(?:\.\d+)?(?:_\d+)?$")
IMF = re.compile(r"^IMF\d+$", re.I)
# R9 -- identifiers, tested on the compact label (separators between a letter and the following digit removed)
PCB = re.compile(r"^PCB\d{1,3}$", re.I)                                     # PCB congener = chemical name
DATABASE_ID = re.compile(r"^(?:HMDB\d{4,}|CHEBI:\s*\d+|C\d{5}|LM[A-Z]{2}\d{4,}|(?:pubchem)?CID:?\d+|"
                         r"[A-Z]{14}-[A-Z]{10}-[A-Z])$", re.I)
CATALOG_ID = re.compile(r"^(?:MFCD\d{8}|NP\d{6}|[A-Z]{2}\d{7}|CAY\d{5}|MMV\d{6}|NSC\d+)$", re.I)
UNIPROT_ONE = r"(?:[OPQ]\d[A-Z0-9]{3}\d|[A-NR-Z]\d(?:[A-Z][A-Z0-9]{2}\d){1,2})(?:-\d+)?"
UNIPROT = re.compile(rf"^{UNIPROT_ONE}(?:\s*;\s*{UNIPROT_ONE})*$")           # case-sensitive (upper case)
ALL_ELEMENTS = re.compile(rf"^(?:{ELEM}\d*){{2,}}$")                           # 'H2S2O3' is a formula, not an accession
# R10 -- NMR bin range
RANGE = re.compile(rf"^\s*[Bb]?(?:in)?[\s_]*(?:{NUM})\s*(?:ppm)?\s*(?:-|–|to|\.{{2,3}}|_)\s*(?:{NUM})\s*(?:ppm)?\s*$")
# R11 -- class-level terms (whole label after removing duplicate suffixes and adduct notation, case-insensitive)
CLASS_TERMS = re.compile(
    r"^(?:PC|PE|PI|PS|PG|PA|TG|TAG|DG|DAG|MG|MAG|SM|Cer|HexCer|CE|LPC|LPE|LPI|LPS|LPG|LPA|FA|FFA|CL|CAR|AC|"
    r"hexoses?|hexitols?|pentoses?|pentitols?|tetroses?|heptoses?|sugars?|lipids?|total lipids?|acylcarnitines?|"
    r"(?:free )?fatty acids?|amino acids?|bile acids?|carbohydrates?|phospholipids?|sphingolipids?|"
    r"glycerophospholipids?|triglycerides?|triacylglycerols?|diglycerides?|diacylglycerols?|cholesteryl esters?|"
    r"ceramides?|sphingomyelins?|phosphatidylcholines?|phosphatidylethanolamines?|lysophosphatidylcholines?|"
    r"plasmalogens?)$", re.I)
# R12 -- lipid / fatty-acyl species shorthand
LIPID_SHORTHAND = re.compile(r"(?<![\d.])\d{1,2}:\d{1,2}(?![\d.])")
LIPID_CONDENSED = re.compile(r"^(?:LPC|LPE|LPG|LPI|LPS|LPA|PC|PE|PG|PI|PS|PA|FA|FFA|Cer|SM|CE|TG|TAG|DG|DAG|MG|MAG|CAR|AC|"
                             r"HexCer)(?:[OP]-?)?\d{3,4}$", re.I)          # 'LPC140' = LPC 14:0, 'LPE161', 'cer180' 
# R13 -- generic tokens dropped in the numeric test (case-insensitive)
GENERIC = {"mz", "rt", "ri", "min", "mins", "sec", "s", "n", "pos", "neg", "positive", "negative", "esi",
           "mode", "feature", "features", "peak", "peaks", "bin", "bucket", "cmpd", "compound", "id", "cluster", "ion",
           "mass", "time", "retention", "index", "ft", "var", "variable", "metabolite", "mw", "da", "unk"}
PPM_UNIT = re.compile(r"(?<=\d)\s*ppm\b", re.I)                            # 'ppm' only as a unit after a number
# R14 -- short code: 1-4 letters, >= 3 digits (tested on the compact label). Returned as 'short_code'; classify_all.py
#        decides per analysis: a prefix shared by >= 10 labels of the analysis is a generic ID series ('feature_id'),
#        otherwise the code is treated as an identifier (resolved via RefMet or unresolved).
SHORT_ID = re.compile(r"^[A-Za-z]{1,4}\d{3,}(?:[-_]\d+)?$")

NAMED_CLASSES = ("named",)
IDENTIFIER_CLASSES = ("database_id", "catalog_id", "protein_accession", "short_code")
WITHOUT_NAME_CLASSES = ("class_level", "tentative", "formula", "mz_rt", "mz_only", "nmr_bin", "feature_id", "unknown")
EXCLUDED_CLASSES = ("sample_metadata", "excluded_empty")


def compact(t: str) -> str:
    """Remove spaces, hyphens and underscores that sit between a letter and the following digit ('VD 2716')."""
    return re.sub(COMPACT_SEP, "", t)


def classify(label: str) -> str:
    t = label.strip()
    # R0 / R1
    if not t:
        return "excluded_empty"
    if METADATA.match(t):
        return "sample_metadata"
    # R2 placeholders
    if (UNKNOWN_A.match(t) or UNKNOWN_B.match(t) or UNKNOWN_C.match(t) or t in UNKNOWN_D or UNKNOWN_E.match(t)
            or UNKNOWN_F.match(t)):
        return "unknown"
    # R3 tentative
    if TENTATIVE.match(t):
        return "tentative"
    # R4 formula only (trailing '(digits)' removed, 'null' runs read as a polarity tag, adduct notation removed)
    tf = re.sub(TRAILING_PAREN, "", t)
    tf = re.sub(NULL_RUN, " esi ", tf)
    tf = ADDUCT.sub(" ", ADDUCT_BRACKETED.sub(" ", tf)).strip()
    if FORMULA.match(tf) and re.search(r"\d", tf):
        return "formula"
    # R5-R8 feature codes
    if CSH.match(t) or IMF.match(t):
        return "feature_id"
    if CODED_MZRT.match(t) or XCMS.match(t):
        return "mz_rt"
    # R9 identifiers
    c = compact(t)
    if PCB.match(c):
        return "named"
    if DATABASE_ID.match(c):
        return "database_id"
    if CATALOG_ID.match(c):
        return "catalog_id"
    if UNIPROT.match(t) and not ALL_ELEMENTS.match(t):
        return "protein_accession"
    # R10 NMR bin range
    if RANGE.match(t) and all(float(x) <= 15 for x in re.findall(NUM, t) if x.strip(".")):
        return "nmr_bin"
    # R11 class level
    core = re.sub(DUP_SUFFIX, "", t)
    if CLASS_TERMS.match(ADDUCT.sub(" ", core).strip()):
        return "class_level"
    # R12 lipid species shorthand, with or without the colon
    if LIPID_SHORTHAND.search(t) or LIPID_CONDENSED.match(compact(t)):
        return "named"
    # R13 numeric test
    core = ADDUCT.sub(" ", core)
    core = re.sub(MZ_WORD, " mz ", core)
    ppm_unit = bool(PPM_UNIT.search(core))
    core = PPM_UNIT.sub(" ", core)
    toks = re.findall(rf"{NUM}|[A-Za-z]+", core)
    words = [w for w in toks if not re.fullmatch(NUM, w) and w.lower() not in GENERIC]
    nums = [w for w in toks if re.fullmatch(NUM, w)]
    if words and all(len(w) == 1 for w in words) and any("." in x for x in nums):
        words = []
    if not words:
        if not nums:
            return "unknown"
        dec = [x for x in nums if "." in x]
        if ppm_unit or (len(nums) == 1 and dec and float(nums[0]) <= 15):
            return "nmr_bin"
        if len(nums) >= 2 and dec:
            return "mz_rt"
        if len(nums) == 1 and dec:
            return "mz_only"
        return "feature_id"
    # R14 short code
    if SHORT_ID.match(c):
        return "short_code"
    # R15
    return "named"


TESTS = {
    # named
    "adenosine": "named", "alanine [M+H]+": "named", "CE(10:0)-13C0[M+Na]": "named", "DG 24:0; [M+NH4]+@5.36": "named",
    "CYSTINE_1": "named", "LTB4": "named", "PC(36:2)": "named", "TG 12:0_12:0_12:0": "named", "Coenzyme Q10": "named",
    "CerP 32:1; [M-H]-@5.12": "named", "PC(38:6)>PC(16:0_22:6)_M+H2CO2_feature1": "named", "Glucose_180.06_3.2": "named",
    "Na": "named", "K": "named", "SM-C18:0": "named", "R5P": "named", "S1P": "named", "E4P.1": "named", "S7P": "named",
    "18:3 (n-3)": "named", "IC202C": "named", "C16": "named", "Adenosine Esi+3.147999": "named",
    "TAG(46:4) A [M+Na]+": "named", "PPM-18": "named", "PCB-153": "named", "PCB 180": "named", "LPE(18:1(d7))": "named",
    "PC 36:2": "named", "Hexose-6-phosphate": "named", "H2SO4": "named", "H2S2O3": "named", "LPC140": "named", "LPE161": "named", "cer180": "named", "FA120": "named",
    "LPG201": "named", "PC4959775": "catalog_id", "Dienogest": "named",
    # identifiers (resolved in context)
    "HMDB0000122": "database_id", "C00031": "database_id", "CID 12910457": "database_id", "LMFA08020333": "database_id",
    "CHEBI:69439": "database_id", "MFCD01182773": "catalog_id", "NP-011548": "catalog_id", "AL9450000": "catalog_id",
    "CAY10621": "catalog_id", "NSC23766": "catalog_id", "NSC 23766": "catalog_id", "CAY-10487": "catalog_id", "MMV688955": "catalog_id", "P11717": "protein_accession",
    "A0A0C4DH29": "protein_accession", "A6NCE7;Q9GZQ8;Q9H492": "protein_accession",
    # without a metabolite name
    "Hexose": "class_level", "hexitol": "class_level", "Hexitols": "class_level", "PC": "class_level", "PE": "class_level",
    "POSSIBLY C10H13NO2": "tentative", "[Similar to: Microcystin RR; ΔMass 1.2]": "tentative",
    "C11 H25 N5 O4": "formula", "C4H6N6O4": "formula", "C24 H30 O6 Esi+1.1370002": "formula",
    "C14 H N S2 Esi-2.8139994 :1": "formula", "C30 H26": "formula", "C6 H9 O5 nullnullnull1.2390001": "formula",
    "C27H46O3(6)": "formula", "C6H12O6[M+H]+": "formula",
    "197.060476_8.162866667": "mz_rt", "120.0436@0.67199874": "mz_rt", "0.82_130.0515m/z": "mz_rt",
    "256.092m/z_0.46ri": "mz_rt", "1.13_152.0027n": "mz_rt", "M60T343": "mz_rt", "M70.0132T60": "mz_rt",
    "RT11.82_729.66": "mz_rt", "218.9831_11.9.1": "mz_rt", "610.53943398141 _ 15.7673166666667": "mz_rt",
    "M845T308_1": "mz_rt", "641.5104_6": "mz_rt", "100.9995_R1": "mz_rt", "MW_228.00319@RT_8.794": "mz_rt",
    "M00034 (M+H), BP 86.09733, RT 1.382": "mz_rt", "M03758 (M+H), BP 118.21805, RT .71": "mz_rt",
    "101.060501": "mz_only", "m/z 123.45": "mz_only",
    "0.4...0.46": "nmr_bin", "9.3995..9.3985": "nmr_bin", "3.40-3.42": "nmr_bin", "3.41 ppm": "nmr_bin", "3.405": "nmr_bin",
    "279797": "feature_id", "127_15": "feature_id", "Feature 12": "feature_id", "VD2716": "short_code",
    "VD 2716": "short_code", "IMF3758": "feature_id", "IMF7": "feature_id", "V16082": "short_code", "SB 203580": "short_code", "GW501516": "short_code", "ATP 232": "short_code",
    "CSH_posESI #285": "feature_id", "CSH_negESI #12a": "feature_id", "CSH_negESI109": "feature_id", "CSH_posESI_12": "feature_id",
    "Unknown 12": "unknown", "UNKNOWN_3": "unknown", "UNASSIGNED 4": "unknown", "<none>": "unknown",
    "> limit Esi+ 5.1510005": "unknown", "UNK_100.0512_7.8": "unknown", "X-12345": "unknown", "X - 17612": "unknown",
    "X-23314: ret for 64131": "unknown", "NA": "unknown", "---": "unknown",
    # excluded
    "": "excluded_empty", "Samples": "sample_metadata", "Days": "sample_metadata", "Phenotype": "sample_metadata",
    "Genotype": "sample_metadata", "Condition": "sample_metadata",
}


def self_test() -> None:
    bad = {k: (classify(k), v) for k, v in TESTS.items() if classify(k) != v}
    assert not bad, bad


classify_cached = functools.lru_cache(maxsize=2_000_000)(classify)


# ---------------------------------------------------------------------------------------------------------------------
# Feature labels of a deposited file (collect_labels.py, unchanged)
# ---------------------------------------------------------------------------------------------------------------------
def _read_lines(path: Path) -> list[str]:
    # split on "\n" only: a stray control character inside a label must not break a line
    return Path(path).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").split("\n")


def factor_names(values: list[str]) -> list[str]:
    """Factor names (the text before ':' in each 'Factor:Value' part, lower-cased)."""
    keys = set()
    for v in values:
        for part in v.split("|"):
            if ":" in part:
                k = part.split(":", 1)[0].strip().lower()
                if k:
                    keys.add(k)
    return sorted(keys)


def sample_by_feature_labels(path: Path) -> tuple[list[str], list[str]]:
    """datatable / untarg_data: (feature labels = header cells 3.., factor names of the class column)."""
    with Path(path).open(encoding="utf-8", errors="replace", newline="") as fh:
        first = fh.readline()
    labels = [c.strip() for c in first.rstrip("\r\n").split("\t")[2:]]
    classes = [x.split("\t", 2)[1] for x in _read_lines(path)[1:] if x.count("\t") >= 1]
    return labels, factor_names(classes)


def mwtab_labels(path: Path) -> tuple[list[str], list[str], int]:
    """mwTab: (first cell of every row of the non-EXTENDED data blocks, factor names, duplicate blocks skipped)."""
    L = _read_lines(path)
    out, seen, skipped, i = [], set(), 0, 0
    ssf = [x.split("\t")[3] for x in L if x.startswith("SUBJECT_SAMPLE_FACTORS") and len(x.split("\t")) > 3]
    while i < len(L):
        s = L[i].strip()
        if s.endswith("_DATA_START") and not s.startswith("EXTENDED"):
            j = i + 1
            while j < len(L) and not L[j].strip().endswith("_DATA_END"):
                j += 1
            body = "\n".join(L[i + 1:j])
            if body in seen:
                skipped += 1                              # identical duplicate block
            else:
                seen.add(body)
                for row in L[i + 2:j]:                    # skip the sample-ID header row
                    name = row.split("\t")[0].strip()
                    if name.lower() != "factors":
                        out.append(name)
            i = j
        i += 1
    return out, factor_names(ssf), skipped


def labels_of_file(path: Path, source: str) -> tuple[list[str], list[str]]:
    """(feature labels, factor names) of one deposited file; source is datatable, mwtab or untarg_data."""
    if source == "mwtab":
        labels, names, _ = mwtab_labels(path)
        return labels, names
    return sample_by_feature_labels(path)


# ---------------------------------------------------------------------------------------------------------------------
# Context rules within one matrix (classify_all.py, unchanged) and the groups
# ---------------------------------------------------------------------------------------------------------------------
NOT_REFMET = {"", "-", "unknown", "standard", "na", "n/a", "none", "protein"}
RESCUABLE = {"feature_id", "formula", "unknown", "mz_rt", "mz_only", "nmr_bin"}
NAMED = ("named", "named_by_refmet", "resolved_identifier")
WITHOUT = ("unresolved_identifier", "class_level", "tentative", "formula", "mz_rt", "mz_only", "nmr_bin", "feature_id",
           "unknown")
EXCLUDED = ("excluded_empty", "sample_metadata", "non_metabolite", "malformed_matrix")
UPPER_EXTRA = ("unresolved_identifier", "class_level", "tentative")
MALFORMED_SHARE = 0.25
SERIES_MIN = 10          # a short-code letter prefix shared by >= 10 labels of one matrix = generic feature-ID series
SERIES_MEMBER = re.compile(r"^[A-Za-z]{1,4}\d{1,2}$")   # 1-2 digit member of such a series ('C1', 'T04')

# wording shown in the UI for each final class (Table S1c)
CLASS_LABELS = {
    "named": "Named metabolite", "named_by_refmet": "Named via RefMet", "resolved_identifier": "Resolved identifier",
    "unresolved_identifier": "Unresolved identifier", "class_level": "Class level", "tentative": "Tentative",
    "formula": "Formula only", "mz_rt": "m/z–RT", "mz_only": "Bare m/z", "nmr_bin": "NMR bin",
    "feature_id": "Generic feature ID", "unknown": "Unknown / placeholder",
    "excluded_empty": "Empty label", "sample_metadata": "Sample metadata", "non_metabolite": "Mapped to protein entry",
    "malformed_matrix": "Corrupted matrix",
}


def prefix(label: str) -> str:
    m = re.match(r"[A-Za-z]+", compact(label.strip()))
    return m.group(0).upper() if m else ""


def key(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def load_records(path: Path) -> list[dict]:
    """Records of a cached factors.json or metabolites.json."""
    try:
        d = json.load(Path(path).open())
    except (OSError, json.JSONDecodeError):
        return []
    recs = [d] if isinstance(d, dict) and ("metabolite_name" in d or "local_sample_id" in d) else (
        list(d.values()) if isinstance(d, dict) else d)
    return [r for r in recs if isinstance(r, dict)]


def study_context(study_dir: Path) -> tuple[set, dict]:
    """(sample IDs of the study incl. line-break fragments, {(analysis, name key): RefMet names}) from the cached files."""
    roster = set()
    for r in load_records(Path(study_dir) / "factors.json"):
        i = str(r.get("local_sample_id", ""))
        roster.add(i.strip())
        roster |= {p.strip() for p in i.split("\n")}
    names = collections.defaultdict(set)
    for r in load_records(Path(study_dir) / "metabolites.json"):
        names[(r.get("analysis_id", ""), key(str(r.get("metabolite_name", ""))))].add(str(r.get("refmet_name", "")).strip())
    return roster - {""}, names


def classify_matrix(labels: list[str], analysis_id: str, matrix_factor_names: list[str], roster: set, rmap: dict,
                    with_final: bool = False) -> dict:
    """Final class of every label of one matrix (label rules, then the context rules K1-K4), with the group counts.
    with_final (added 2026-10-22 for the Annotation tab; the counts are unchanged): also return {label: final class}."""
    rule = {x: classify_cached(x) for x in set(labels)}
    nonempty = [x for x in labels if x.strip()]
    share = sum(x.strip() in roster for x in nonempty) / len(nonempty) if nonempty else 0.0
    malformed = share >= MALFORMED_SHARE
    fnames = set(matrix_factor_names)
    series = collections.Counter(prefix(x) for x in labels if rule[x] == "short_code")
    out = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    lower = 0
    final: dict[str, tuple[str, bool]] = {}
    for x in labels:
        if x not in final:
            k0 = rule[x]
            if k0 == "short_code" and series[prefix(x)] >= SERIES_MIN:
                k0 = "feature_id"                                      # K2: generic feature-ID series
            elif k0 == "named" and SERIES_MEMBER.match(compact(x.strip())) and series[prefix(x)] >= SERIES_MIN:
                k0 = "feature_id"                                      # K2: 1-2 digit member of the same series
            k, resolved = k0, False
            if malformed:
                k = "malformed_matrix"
            elif k0 == "excluded_empty":
                pass
            elif x.strip().lower() in fnames:
                k = "sample_metadata"
            else:
                m = rmap.get((analysis_id, key(x)))
                rn = next(iter(m)) if m and len(m) == 1 else None          # conflicting matches -> no resolution
                if rn is not None and rn.lower() == "protein":
                    k = "non_metabolite"
                elif rn is not None and rn.lower() not in NOT_REFMET:
                    if k0 in IDENTIFIER_CLASSES:
                        k, resolved = "resolved_identifier", True
                    elif k0 in RESCUABLE:
                        k, resolved = "named_by_refmet", True
                    elif k0 == "named":
                        resolved = True
                if k in IDENTIFIER_CLASSES:
                    k = "unresolved_identifier"
            final[x] = (k, resolved)
        k, resolved = final[x]
        out[k] += 1
        lower += int(k in NAMED and resolved)
        if len(examples[k]) < 3 and x not in examples[k]:
            examples[k].append(x)
    n_named = sum(out[k] for k in NAMED)
    n_without = sum(out[k] for k in WITHOUT)
    return {
        "n_labels": len(labels),
        "malformed_matrix": bool(malformed),
        "roster_label_share": round(share, 4),
        "class_counts": {k: out[k] for k in NAMED + WITHOUT + EXCLUDED if out[k]},
        "class_examples": {k: v for k, v in examples.items()},
        "n_named": n_named,
        "n_without_name": n_without,
        "n_eligible": n_named + n_without,
        "n_excluded": sum(out[k] for k in EXCLUDED),
        "n_named_lower": lower,                                              # named with a valid unique same-analysis RefMet name
        "n_named_upper": n_named + sum(out[k] for k in UPPER_EXTRA),
    } | ({"final": {x: k for x, (k, _) in final.items()}} if with_final else {})


self_test()
