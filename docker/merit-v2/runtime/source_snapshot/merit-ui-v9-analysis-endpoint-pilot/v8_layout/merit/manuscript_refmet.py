"""RefMet rules of the manuscript (Text S3 "RefMet Mappings" and "External identifiers of RefMet-mapped records";
Table S1e; Figure S7), used by the UI so that both count the same way.

Ported from outputs/refmet_mapping_20260929/refmet_mapping.py, table_S1e.py and refmet_external_ids.py.

* A metabolite record is one row of the study's cached metabolites.json (REST /study/study_id/<ST>/metabolites): one
  record per deposited metabolite per analysis, with the repository's own ``refmet_name``. No new mapping is attempted.
* A RefMet name is VALID unless, after stripping, it is blank, '-', 'Unknown', 'Standard' or 'Protein' (any case).
* External identifiers are looked up by RefMet name in two frozen reference tables stored with the code (merit/data/):
  the RefMet identifier table (/rest/refmet/all_ids, downloaded 2026-09-29) and the full RefMet name list
  (/rest/refmet/all, downloaded 2026-09-27). An identifier counts as present only when its field is non-empty. The
  identifiers come from RefMet; they were not necessarily deposited with the feature.
"""
from __future__ import annotations

import collections
import functools
import gzip
import json
from pathlib import Path

from merit.manuscript_feature_labels import classify_cached, load_records

DATA = Path(__file__).resolve().parent / "data"
ID_TABLE_DATE = "2026-09-29"
NAME_LIST_DATE = "2026-09-27"
FIELDS = ("pubchem_cid", "inchi_key", "chebi_id", "kegg_id", "hmdb_id", "lm_id")
FIELD_LABELS = {"pubchem_cid": "PubChem CID", "inchi_key": "InChIKey", "chebi_id": "ChEBI", "kegg_id": "KEGG",
                "hmdb_id": "HMDB", "lm_id": "LIPID MAPS"}
CATEGORIES = ("valid", "blank", "dash", "unknown", "standard", "protein")
# Table S1e: wording of the categories of records without a valid RefMet name
S1E_LABELS = {
    "blank_named": "Empty RefMet name: chemical name without a RefMet entry",
    "blank_formula": "Empty RefMet name: formula only",
    "blank_code": "Empty RefMet name: catalog, database or short code",
    "blank_other": "Empty RefMet name: m/z–RT, class-level, tentative or other",
    "standard": "RefMet name \"Standard\" (internal standard)",
    "unknown": "RefMet name \"Unknown\"",
    "protein": "RefMet name \"Protein\"",
    "dash": "RefMet name \"-\"",
}


def category(value) -> str:
    """Category of a deposited refmet_name: valid, blank, dash, unknown, standard or protein."""
    low = str(value if value is not None else "").strip().lower()
    return {"": "blank", "-": "dash", "unknown": "unknown", "standard": "standard", "protein": "protein"}.get(low, "valid")


def records_by_analysis(study_dir: Path) -> dict[str, list[dict]]:
    """Metabolite records of the study's cached metabolites.json, grouped by analysis ID."""
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for r in load_records(Path(study_dir) / "metabolites.json"):
        out[str(r.get("analysis_id", "") or "")].append(r)
    return out


def mapping_summary(records: list[dict]) -> dict:
    """Counts of one analysis: records per RefMet category, and the Table S1e split of the records without a valid name."""
    cats = collections.Counter(category(r.get("refmet_name")) for r in records)
    blank = collections.Counter(classify_cached(str(r.get("metabolite_name", ""))) for r in records
                                if category(r.get("refmet_name")) == "blank")
    code = blank["catalog_id"] + blank["database_id"] + blank["short_code"]
    not_valid = {
        "blank_named": blank["named"], "blank_formula": blank["formula"], "blank_code": code,
        "blank_other": cats["blank"] - blank["named"] - blank["formula"] - code,
        "standard": cats["standard"], "unknown": cats["unknown"], "protein": cats["protein"], "dash": cats["dash"],
    }
    return {"n_records": len(records), "n_valid": cats["valid"], "not_valid": {k: v for k, v in not_valid.items() if v}}


@functools.lru_cache(maxsize=1)
def _reference() -> tuple[dict, dict, set, set]:
    with gzip.open(DATA / "refmet_external_id_flags_20260929.json.gz", "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert tuple(payload["fields"]) == FIELDS
    ids = payload["flags"]
    with gzip.open(DATA / "refmet_names_20260927.txt.gz", "rt", encoding="utf-8") as fh:
        full = set(fh.read().split("\n"))
    return ids, {k.lower(): v for k, v in ids.items()}, full, {k.lower() for k in full}


def external_id_summary(records: list[dict]) -> dict:
    """External identifiers, via RefMet, of the records that have a valid RefMet name."""
    ids, ids_ci, full, full_ci = _reference()
    n = 0
    present = collections.Counter()
    n_ids = collections.Counter()
    match = collections.Counter()
    for r in records:
        if category(r.get("refmet_name")) != "valid":       # checked on the deposited value (a JSON null is blank)
            continue
        rn = str(r.get("refmet_name")).strip()
        n += 1
        flags = ids.get(rn)
        if flags is not None:
            match["in_id_table"] += 1
        elif rn.lower() in ids_ci:
            flags = ids_ci[rn.lower()]
            match["in_id_table"] += 1
        elif rn in full or rn.lower() in full_ci:
            match["in_refmet_without_external_id"] += 1
        else:
            match["not_in_current_refmet"] += 1
        k = 0
        for f, flag in zip(FIELDS, flags or "000000"):
            if flag == "1":
                present[f] += 1
                k += 1
        n_ids[k] += 1
    return {"n_valid_records": n, "n_with_any": n - n_ids[0], "n_with_all_six": n_ids[6],
            "per_identifier": {f: present[f] for f in FIELDS},
            "n_refmet_entry_without_external_id": match["in_refmet_without_external_id"],
            "n_name_not_in_current_refmet": match["not_in_current_refmet"]}


def self_test() -> None:
    assert [category(x) for x in ("Alanine", "", " - ", "unknown", "STANDARD", "Protein", None)] == \
        ["valid", "blank", "dash", "unknown", "standard", "protein", "blank"]
    recs = [{"metabolite_name": "x", "refmet_name": "Alanine"}, {"metabolite_name": "C6H12O6", "refmet_name": ""},
            {"metabolite_name": "IS", "refmet_name": "Standard"}]
    s = mapping_summary(recs)
    assert (s["n_records"], s["n_valid"], s["not_valid"]) == (3, 1, {"blank_formula": 1, "standard": 1}), s
    assert external_id_summary([{"metabolite_name": "x", "refmet_name": None}])["n_valid_records"] == 0


self_test()
