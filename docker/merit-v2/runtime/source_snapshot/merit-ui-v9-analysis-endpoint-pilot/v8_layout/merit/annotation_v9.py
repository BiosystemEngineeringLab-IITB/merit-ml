"""Annotation (v9 tab "Annotation / Interoperability"): one record per valid matrix (one analysis read from one endpoint).

Feature labels do not depend on samples, so sample edits do not change anything here. Decided by the author on 2026-10-22.

1. Named-feature coverage (scored, higher = better for ML): named / eligible features of the matrix, with the manuscript's
   classifier unchanged (Text S3, Table S1a-c; merit/manuscript_feature_labels.py). Named = named, named via RefMet, resolved
   identifier; eligible = named + the classes without a metabolite name (unresolved identifier, class level, tentative, formula,
   m/z-RT, bare m/z, NMR bin, generic feature ID, unknown); empty labels, sample-metadata words, protein entries and corrupted
   matrices are not features. Not assessed when the matrix has no eligible feature. Scored on every endpoint (untarg_data too).
2. RefMet mapping (scored, higher = better for ML): named features of the matrix that have a RefMet name / named features. The
   feature label is looked up in the repository's metabolite records of the same analysis (metabolites.json of the frozen dump),
   both sides normalised as in Text S10 (trimmed, lower-cased, spaces collapsed, ',' read as '_' and "'" as '^'). A feature is
   mapped when every record found gives the same valid RefMet name (not blank, '-', Unknown, Standard or Protein); records
   giving different valid names leave it unmapped ("conflicting"). The RefMet name is the one Metabolomics Workbench assigned;
   MERIT-ML does not map names itself. Not assessed when the matrix has no named feature. Shown beside the score (not scored; added
   2026-10-22): the number of distinct RefMet names among the mapped features, and the RefMet names that several features share (for
   example one metabolite reported at several retention times, 'Oxymesterone@10.47' and 'Oxymesterone@10.62', or as several ions or
   isotopologues). So named features and RefMet entries differ for two reasons: features without a RefMet name, and features sharing one.
3. Metabolite classes (informational): the RefMet super class, main class and sub class of every mapped feature, looked up by
   RefMet name in refmet_db.csv of the frozen dump.
4. Repeated feature names (informational): eligible features whose label occurs more than once in the matrix. The deposited strings
   are compared as a whole, after the Text S10 normalisation; for named features also a duplicate suffix ('.1' after a letter, or a
   trailing '(2)') is ignored. Labels that differ in any other way (an RT tag such as '@10.47', an ion or an isotope label) are different
   strings; when they share a RefMet name, the Repeated feature names cell counts them on a second line with a visible line of the
   largest groups (decided 2026-10-22: the shared groups belong here; RefMet mapping only states the distinct RefMet names).
"""
from __future__ import annotations

import collections
import csv
import functools
import re
from pathlib import Path

from merit import manuscript_feature_labels as fl
from merit import manuscript_refmet as mr

DUMP = Path("/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/mw-dump-latest-18thSep-2026-version")
NAMED, WITHOUT, EXCLUDED = fl.NAMED, fl.WITHOUT, fl.EXCLUDED
DUP_SUFFIX = re.compile(fl.DUP_SUFFIX)
EXAMPLES = 5


def norm(s: str) -> str:
    """Text S10 name normalisation (trim, lower case, collapse spaces, ',' as '_', "'" as '^')."""
    return re.sub(r"\s+", " ", str(s).strip().lower()).replace(",", "_").replace("'", "^")


def file_of(study_id: str, analysis_id: str, source: str) -> Path:
    """The deposited file the manuscript read (collect_labels.py)."""
    base = DUMP / study_id / analysis_id
    if source == "datatable":
        return base / "tabular" / f"{analysis_id}_datatable_slash.txt"
    if source == "mwtab":
        return base / "mwtab" / f"{analysis_id}_mwtab.txt"
    p = base / "tabular" / f"{analysis_id}_untarg_data.tsv"
    return p if p.is_file() else next(DUMP.glob(f"ST*/{analysis_id}/tabular/{analysis_id}_untarg_data.tsv"))


def refmet_lookup(study_id: str) -> dict:
    """{(analysis_id, normalised metabolite name): set of RefMet names as deposited} from the study's metabolite records."""
    out = collections.defaultdict(set)
    for r in fl.load_records(DUMP / study_id / "metabolites.json"):
        out[(str(r.get("analysis_id", "")), norm(r.get("metabolite_name", "")))].add(str(r.get("refmet_name") or "").strip())
    return out


@functools.lru_cache(maxsize=1)
def refmet_classes() -> dict:
    """{RefMet name: (super class, main class, sub class)} from refmet_db.csv of the frozen dump; a lower-cased key as fallback."""
    out = {}
    with (DUMP / "refmet_db.csv").open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            row = {k.strip(): (v or "").strip() for k, v in r.items()}
            cls = (row.get("super_class", ""), row.get("main_class", ""), row.get("sub_class", ""))
            out.setdefault(row["refmet_name"], cls)
            out.setdefault("\0" + row["refmet_name"].lower(), cls)
    return out


def _class_of(name: str):
    t = refmet_classes()
    return t.get(name) or t.get("\0" + name.lower())


def repeat_key(label: str, named: bool) -> str:
    k = norm(label)
    return DUP_SUFFIX.sub("", k).strip() if named else k


def analyse(study_id: str, analysis_id: str, source: str, ctx: tuple | None = None, lookup: dict | None = None) -> dict:
    """Everything the tab shows for one matrix, plus the per-feature lists for the download ('lists')."""
    roster, rmap = ctx if ctx is not None else fl.study_context(DUMP / study_id)
    lookup = lookup if lookup is not None else refmet_lookup(study_id)
    labels, fnames = fl.labels_of_file(file_of(study_id, analysis_id, source), source)
    c = fl.classify_matrix(labels, analysis_id, fnames, roster, rmap, with_final=True)
    final = c.pop("final")
    cls_of = [final[x] for x in labels]
    named = [x for x, k in zip(labels, cls_of) if k in NAMED]
    eligible = [(x, k) for x, k in zip(labels, cls_of) if k in NAMED or k in WITHOUT]
    # 2. RefMet mapping of the named features
    status = collections.Counter(); mapped, unmapped = [], []
    for x in named:
        names = lookup.get((analysis_id, norm(x)))
        if not names:
            status["not in the repository's records"] += 1; unmapped.append((x, "not in the repository's records")); continue
        valid = {n for n in names if mr.category(n) == "valid"}
        if len(valid) == 1:
            status["mapped"] += 1; mapped.append((x, next(iter(valid))))
        elif len(valid) > 1:
            status["records give different RefMet names"] += 1; unmapped.append((x, "records give different RefMet names: " + "; ".join(sorted(valid))))
        else:
            status["records without a valid RefMet name"] += 1
            unmapped.append((x, "records without a valid RefMet name (" + "; ".join(sorted(n or "blank" for n in names)) + ")"))
    by_name = collections.defaultdict(list)
    for x, rn in mapped:
        by_name[rn].append(x)
    shared = sorted(((n, v) for n, v in by_name.items() if len(v) > 1), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    # 3. classes of the mapped features
    sup, main, sub = collections.Counter(), collections.Counter(), collections.Counter(); per_feature = []; no_class = 0
    for x, rn in mapped:
        cl = _class_of(rn)
        if cl is None:
            no_class += 1; per_feature.append((x, rn, None, None, None)); continue
        sup[cl[0] or "(no super class)"] += 1; main[cl[1] or "(no main class)"] += 1; sub[cl[2] or "(no sub class)"] += 1
        per_feature.append((x, rn) + cl)
    # 4. repeated names among the eligible features
    groups = collections.defaultdict(list)
    for x, k in eligible:
        groups[repeat_key(x, k in NAMED)].append(x)
    rep = {g: v for g, v in groups.items() if len(v) > 1}
    exact = sum(1 for v in rep.values() if len(set(v)) < len(v))
    rep_sorted = sorted(rep.values(), key=lambda v: (-len(v), v[0].lower()))
    n_named, n_elig = len(named), len(eligible)
    return {
        "labels": len(labels), "malformed_matrix": c["malformed_matrix"], "class_counts": c["class_counts"],
        "class_examples": c["class_examples"], "named": n_named, "without_name": c["n_without_name"], "eligible": n_elig,
        "excluded": c["n_excluded"], "named_lower_manuscript": c["n_named_lower"],
        "coverage": {"score": 100.0 * n_named / n_elig if n_elig else None,
                     "not_assessed": None if n_elig else ("corrupted matrix: most labels are sample IDs" if c["malformed_matrix"] else "no eligible feature")},
        "refmet": {"score": 100.0 * status["mapped"] / n_named if n_named else None, "mapped": status["mapped"],
                   "distinct_refmet_names": len(by_name), "shared_names": len(shared), "features_sharing": sum(len(v) for _, v in shared),
                   "shared_examples": [{"refmet_name": n, "labels": v[:6], "n": len(v)} for n, v in shared[:EXAMPLES]],
                   "status": dict(status), "unmapped_examples": unmapped[:EXAMPLES], "mapped_examples": mapped[:EXAMPLES],
                   "not_assessed": None if n_named else "no named feature"},
        "classes": {"features": len(mapped), "without_class": no_class, "super": dict(sup.most_common()),
                    "main": dict(main.most_common()), "sub_count": len(sub)},
        "repeats": {"names": len(rep), "features": sum(len(v) for v in rep.values()), "with_identical_spelling": exact,
                    "examples": [{"labels": v[:6], "n": len(v)} for v in rep_sorted[:EXAMPLES]]},
        "lists": {"shared_refmet": [[n, v] for n, v in shared], "unmapped": unmapped, "mapped": per_feature, "sub": dict(sub.most_common()), "repeats": [v for v in rep_sorted]},
    }
