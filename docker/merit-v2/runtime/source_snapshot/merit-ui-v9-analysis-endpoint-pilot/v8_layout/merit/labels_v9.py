"""Label Structure and Class Support (v9 tab), per analysis x endpoint, on the ML-eligible samples. Decided by the author on 2026-10-22.

Label of a sample: the factor string of its line in the sample table of the analysis (SUBJECT_SAMPLE_FACTORS of the mwTab text
file), the label on the position only when the sample ID has no line there - the same reading as sample eligibility; a factor label
edited in Adjust Matrix Properties replaces it. Each ML-eligible sample ID counts once. A sample with an empty label has no class and
is counted apart; word values such as 'None' or 'NA' are deposited values and are classes. A sample ID whose lines in the sample table
give different factor strings (3 studies, 12 sample IDs, e.g. ST000355 PN00502 Stage:3 and Stage:4) has no single deposited label: it
is left out of the classes and counted apart, unless its label is edited (decided by the author on 2026-10-23).

Class definition (one choice per study, made on the page; parameter 'label_classes'):
  * default: every distinct whole factor string is a class, exactly as deposited (the manuscript's rule);
  * chosen classes: one or more picked factors and two or more of their deposited values or value combinations, each one class
    (values are never pooled into one class). With one factor a class is one value (for example the time points of Hours); with
    several factors a class is one combination of their values that occurs in the study's sample table (for example FCS:NO_FCS with
    Hours:72). Every sample has exactly one value per factor, so it belongs to at most one class and no class is invented; only
    combinations that occur can be chosen. Samples with another value or combination, or without one of the picked factors, are left
    out (counted). A factor string is split into factors only when that is certain: split on ' | ' (with the spaces, so a value such
    as 'Ju|Hoan' stays whole), every part 'Name:value' with a non-empty name, no name repeated; samples whose string does not split are
    left out of a chosen-class definition (counted).

Informational (not scored): the factors of the splittable strings with their values (varying = more than one value among the
samples, constant = one value), and the joint classes (the distinct whole factor strings) with the samples per joint class.

Scores (0-100, higher = better for ML; decided by the author on 2026-10-23, replacing class balance and label entropy; balance term
revised 2026-10-24):
  class support             = 100 x H(m / (m + M)) x min(1, m / k) for a smallest class of m and a largest of M, with H the binary
                              (base-2) entropy, H(1/2) = 1; one class -> 0; no labelled sample -> not assessed. The first factor is the
                              class balance: the entropy of the smallest against the largest class only, so the most extreme pair decides
                              and nothing is averaged over classes (100 vs 200 -> 91.8; 90 vs 10 -> 46.9; 95 vs 5 -> 28.6; nine classes of
                              100 and one of 3 -> 19.0 before the fold factor, 11.4 with it). Until 2026-10-24 it was m / M, which fell as fast from 1:1
                              to 1:2 (50) as from 1:10 to 1:20. The second factor is the share of the fold target met, min(m, k) / k. In
                              stratified k-fold cross-validation (scikit-learn) a smallest class of m < k triggers "The least populated
                              class in y has only m members, which is less than n_splits=k" and is absent from some test folds; when every
                              class has fewer than k the split is refused ("n_splits=k cannot be greater than the number of members in each
                              class"). Equals class balance once the smallest class has k samples (larger sizes are not scored). A MERIT-ML
                              heuristic, not a published metric and not a validated predictor of accuracy. With k = 5: 3 vs 3 -> 60;
                              1 vs 1 -> 20; 10 vs 10 -> 100; 33 vs 6 -> 61.9.
  cross-validation feasible = 100 when there are at least 2 classes and the smallest class has at least k samples, else 0: stratified
                              k-fold cross-validation needs a sample of every class in every fold. k = 5 by default (the manuscript's
                              G4 pass rule and its Figure S14 "5-fold not valid" below 5), adjustable as the number of folds (2-10).
The two are shown side by side and never combined. Label entropy was removed on 2026-10-23: it ranks matrices almost exactly as
class balance (Spearman 0.944; 1.000 with two classes) and is >= 95 in 81% of matrices.
With a chosen-class definition, a matrix in which a chosen class has no sample is not assessed (that class is absent there).

Other factors across the chosen classes (informational, decided by the author on 2026-10-30; with a chosen-class definition only):
each factor not picked is compared across the chosen classes on the sample IDs in those classes, per matrix, from its reported values,
and gets the first of these that applies:
  not assessable        - the factor is reported for only some of the selected sample IDs (coverage shown);
  one reported level    - one value among the selected sample IDs;
  one value per sample  - every value is carried by exactly one selected sample ID (no value shared);
  values absent         - a value reported among the selected sample IDs has no sample ID in another chosen class;
  same proportions      - every value has exactly the same share in every class (compared on counts, not rounded shares);
  different proportions - every value occurs in every class, with different shares (the largest gap in percentage points is shown).
The factors with two or more values, reported for every selected sample ID and shared between samples, are also compared together
(their value combinations) when there are at least two. A zero count is an observed absence, not missing metadata; the same shares
mean no observed imbalance for that factor, not that the comparison is free of confounding (factors not in the factor string, such
as subject or batch, are not seen). No threshold and no score.
"""
from __future__ import annotations

import collections
import json
import math
import statistics

from merit import qc_blank_v9 as qb
from merit import structural_v9 as sv

SEP = " | "
# the number of cross-validation folds (the only adjustable value); the class choice is a text parameter ('label_classes')
PARAMETERS: dict = {"cv_folds": (5.0, 2.0, 10.0, 1.0)}
DEFAULT_PARAMS = {k: v[0] for k, v in PARAMETERS.items()}
SCORES = ("class_support", "cv_feasible")


def folds(params: dict | None) -> int:
    lo, hi = PARAMETERS["cv_folds"][1], PARAMETERS["cv_folds"][2]
    try:
        k = float((params or {}).get("cv_folds", DEFAULT_PARAMS["cv_folds"]))
    except (TypeError, ValueError):
        k = DEFAULT_PARAMS["cv_folds"]
    return int(round(max(lo, min(hi, k))))


def sample_label(row: list, overrides: dict | None) -> str:
    edit = (overrides or {}).get(row[sv.I_ID]) or {}
    if str(edit.get("label", "") or "").strip():
        return str(edit["label"]).strip()
    text = row[sv.I_TLABEL] if row[sv.I_TABLE] else row[sv.I_LABEL]
    return str(text or "").strip()


def split(label: str) -> list[tuple[str, str]] | None:
    """[(factor, value)] when the string certainly reads 'Name:value | Name:value'; otherwise None (not split)."""
    if not label:
        return None
    out, names = [], set()
    for part in label.split(SEP):
        name, colon, value = part.partition(":")
        name, value = name.strip(), value.strip()
        if not colon or not name or name.lower() in names:
            return None
        names.add(name.lower()); out.append((name, value))
    return out


def sample_table_conflicts(lines) -> dict[str, list[str]]:
    """{sample ID: its distinct factor strings, in file order} for the sample IDs whose SUBJECT_SAMPLE_FACTORS lines give different
    factor strings (read as the cache build reads the sample table: tab-separated, sample ID in field 3, factor string in field 4)."""
    seen: dict[str, list[str]] = {}
    for line in lines:
        if line.startswith("SUBJECT_SAMPLE_FACTORS"):
            c = line.rstrip("\r\n").split("\t")
            if len(c) > 2 and c[2].strip():
                f = c[3].strip() if len(c) > 3 else ""
                vals = seen.setdefault(c[2].strip(), [])
                if f not in vals:
                    vals.append(f)
    return {k: v for k, v in seen.items() if len(v) > 1}


def _unsettled(labels: dict, overrides: dict | None, conflicts: dict | None) -> set:
    """The sample IDs of labels with conflicting sample-table lines and no edited label."""
    return {i for i in labels if i in (conflicts or {}) and not str(((overrides or {}).get(i) or {}).get("label", "") or "").strip()}


def eligible_labels(roster: list, study_id: str, overrides: dict | None) -> dict[str, str]:
    """{sample ID: label} of the ML-eligible positions (each sample ID once; the first position's label)."""
    out: dict[str, str] = {}
    for row, kind in zip(roster, qb.position_kinds(roster, overrides, study_id)):
        if kind == "sample" and not row[sv.I_ALIGN] and row[sv.I_ID] not in out:
            out[row[sv.I_ID]] = sample_label(row, overrides)
    return out


def factor_items(raw: dict, overrides: dict | None = None) -> dict:
    """Study-wide choices: {factor: {value: ML-eligible sample IDs of the study}} from the splittable labels of every analysis (a
    sample ID counts once per value), and the number of ML-eligible sample IDs with a label that cannot be split."""
    sid = raw["study_id"]; per: dict[str, set] = collections.defaultdict(set)
    for a in raw["analyses"]:                      # every analysis: a sample ID can carry other factor values in a later analysis
        for ep in a["endpoints"].values():
            if "roster" in ep:
                labs = eligible_labels(ep["roster"], sid, overrides)
                skip = _unsettled(labs, overrides, ep.get("sample_table_conflicts"))
                for s, lab in labs.items():
                    if s not in skip:
                        per[s].add(lab)
    items: dict = collections.defaultdict(lambda: collections.defaultdict(set)); unsplit = set()
    index: dict[str, int] = {}; groups: collections.Counter = collections.Counter()
    for s, labs in per.items():
        idx = []
        for lab in labs:
            parts = split(lab)
            if parts is None:
                if lab:
                    unsplit.add(s)
                continue
            for f, v in parts:
                items[f][v].add(s)                 # each sample ID counted once per value
            idx.append(index.setdefault(lab, len(index)))
        if idx:
            groups[tuple(sorted(idx))] += 1        # samples with the same splittable labels (one label for almost every sample)
    items = {f: collections.Counter({v: len(ids) for v, ids in vals.items()}) for f, vals in items.items()}
    return {"factors": {f: dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))) for f, c in sorted(items.items())},
            "not_split": len(unsplit), "samples": len(per),
            # for value combinations of any picked factors: the distinct splittable labels, and how many sample IDs carry each set of them
            "joint": {"labels": [split(lab) for lab in index], "groups": [[list(k), n] for k, n in sorted(groups.items())]}}


def combinations(items: dict, factors: list[str]) -> dict[tuple, int]:
    """{(value of each picked factor): ML-eligible sample IDs of the study carrying that combination}; a sample ID counts once per
    combination; samples without one of the factors carry no combination."""
    labels = [dict(p) for p in items["joint"]["labels"]]
    out: collections.Counter = collections.Counter()
    for idx, n in items["joint"]["groups"]:
        combos = {tuple(labels[i].get(f) for f in factors) for i in idx}
        for c in combos:
            if None not in c:
                out[c] += n
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def class_name(factors: list[str], values) -> str:
    return " | ".join(f"{f}: {v}" for f, v in zip(factors, values))


def parse_choice(params: dict | None, items: dict) -> tuple[dict | None, str]:
    """(choice or None for the default, message). Form: {"factors": [picked factors], "classes": [[a value of each factor], ...]}. The
    choice is applied only when every check passes: picked factors of this study, none twice; at least two classes, none twice; every
    class a value (one factor) or value combination (several) that occurs in the study. The earlier forms {"classes": [[factor, value],
    ...]} and {factor1, value1, factor2, value2} are read when all their classes come from one factor."""
    raw = (params or {}).get("label_classes") or ""
    if not raw:
        return None, ""
    try:
        c = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if "factors" in c:
            factors = [str(f) for f in c["factors"]]
            chosen = [tuple(str(v) for v in vals) for vals in c["classes"]]
        else:
            pairs = ([(str(f), str(v)) for f, v in c["classes"]] if "classes" in c else
                     [x for x in ((str(c.get("factor1", "")), str(c.get("value1", ""))), (str(c.get("factor2", "")), str(c.get("value2", "")))) if x[0]])
            if len({f for f, _ in pairs}) > 1:
                return None, "classes from different factors cannot be mixed; pick the factors to use their value combinations; the whole factor string is used"
            factors = [pairs[0][0]] if pairs else []
            chosen = [(v,) for _, v in pairs]
    except (ValueError, TypeError, AttributeError, KeyError, IndexError):
        return None, "the class choice could not be read; the whole factor string is used"
    if not chosen:
        return None, ""
    if not factors or len(set(factors)) != len(factors):
        return None, "pick each factor once; the whole factor string is used"
    unknown = [f for f in factors if f not in items["factors"]]
    if unknown:
        return None, f"not a factor of this study: {', '.join(unknown)}; the whole factor string is used"
    constant = [f for f in factors if len(items["factors"][f]) < 2]   # one value among the ML-eligible samples: nothing to separate
    if constant:
        return None, (f"{', '.join(constant)} has one value in this study (constant); only factors with two or more values can be "
                      "picked; the whole factor string is used")
    if any(len(v) != len(factors) for v in chosen):
        return None, "each class needs one value of every picked factor; the whole factor string is used"
    if len(set(chosen)) != len(chosen):
        return None, "a class was chosen twice; the whole factor string is used"
    if len(chosen) < 2:
        return None, "choose at least two classes; the whole factor string is used"
    occurring = combinations(items, factors)
    missing = [class_name(factors, v) for v in chosen if v not in occurring]
    if missing:
        return None, f"not in this study's labels: {', '.join(missing)}; the whole factor string is used"
    return {"factors": factors, "classes": chosen}, ""


def choice_text(choice: dict | None) -> str:
    if choice is None:
        return "whole factor string, as deposited (default)"
    names = ", ".join(f"“{class_name(choice['factors'], v)}”" for v in choice["classes"])
    of = (f"values of {choice['factors'][0]}" if len(choice["factors"]) == 1
          else f"combinations of {', '.join(choice['factors'][:-1])} and {choice['factors'][-1]}")
    return f"{len(choice['classes'])} chosen classes ({of}): {names}"


def _balance(classes: list[str], rows: list[tuple[str, object]]) -> dict:
    """The comparison of one factor (or one combination of factors) across the chosen classes: rows = (class, value) for every selected
    sample ID that reports it. Counts are compared exactly (cross-multiplied); the gap is in percentage points."""
    t = {c: collections.Counter() for c in classes}
    for c, v in rows:
        t[c][v] += 1
    n = {c: sum(t[c].values()) for c in classes}
    values = sorted({v for _, v in rows}, key=str)
    name = lambda v: " / ".join(v) if isinstance(v, tuple) else str(v)
    # the counts per class (for the hover), kept only for up to 20 values so a record stays small
    counts = {c: {name(v): t[c][v] for v in values} for c in classes} if len(values) <= 20 else None
    if len(values) == 1:
        return {"state": "one level", "values": 1, "counts": counts}
    if all(sum(t[c][v] for c in classes) == 1 for v in values):
        return {"state": "one per sample", "values": len(values), "counts": None}
    absent = [(name(v), sum(1 for c in classes if t[c][v])) for v in values if any(not t[c][v] for c in classes)]
    if absent:
        return {"state": "values absent", "values": len(values), "absent": absent, "counts": counts}
    c0 = classes[0]
    if all(t[c][v] * n[c0] == t[c0][v] * n[c] for c in classes[1:] for v in values):
        return {"state": "same", "values": len(values), "counts": counts}
    gap = max(max(t[c][v] / n[c] for c in classes) - min(t[c][v] / n[c] for c in classes) for v in values)
    return {"state": "different", "values": len(values), "gap": 100.0 * gap, "counts": counts}


def other_factors(selected: list[tuple[str, dict]], classes: list[str], picked: list[str]) -> dict:
    """Informational: each factor not picked, across the chosen classes, on the selected sample IDs ((class, factors of its string)).
    The states are exclusive and applied in the order of the module notes; the factors that are compared together are listed."""
    names = sorted({f for _, d in selected for f in d if f not in picked})
    out = {"selected": len(selected), "classes": len(classes), "factors": {}, "combined": None}
    for f in names:
        rows = [(c, d[f]) for c, d in selected if f in d]
        if len(rows) < len(selected):
            out["factors"][f] = {"state": "not assessable", "reported": len(rows)}
        else:
            out["factors"][f] = {"reported": len(rows), **_balance(classes, rows)}
    joint = [f for f in names if out["factors"][f]["state"] in ("values absent", "same", "different")]
    if len(joint) >= 2:
        out["combined"] = {"factors": joint, **_balance(classes, [(c, tuple(d[f] for f in joint)) for c, d in selected])}
    return out


def pair_entropy(lo: int, hi: int) -> float:
    """Binary (base-2) entropy of a smallest class of lo against a largest of hi: 1 when equal, falling slowly for mild imbalance."""
    if lo == hi:
        return 1.0
    p = lo / (lo + hi)
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def _scores(counts: dict, params: dict | None = None) -> dict:
    k = len(counts); n = sum(counts.values()); kf = folds(params)
    if not n:
        return {"class_support": None, "cv_feasible": None, "balance": None, "folds_covered": None, "not_assessed": "no ML-eligible sample with a label"}
    lo, hi = min(counts.values()), max(counts.values())
    if k == 1:                       # no minority class: nothing to separate, class balance is undefined
        return {"class_support": 0.0, "cv_feasible": 0.0, "balance": None, "folds_covered": None, "not_assessed": None}
    balance = 100.0 * pair_entropy(lo, hi)   # entropy of the smallest vs the largest class (2026-10-24; was lo / hi)
    covered = min(lo, kf)            # test folds that can hold a sample of the smallest class
    return {"class_support": balance * covered / kf, "cv_feasible": 100.0 if lo >= kf else 0.0, "balance": balance, "folds_covered": covered,
            "not_assessed": None}


def sample_classes(roster: list, study_id: str, overrides: dict | None = None, choice: dict | None = None,
                   conflicts: dict | None = None) -> dict[str, tuple[str, int]]:
    """{sample ID: (class, index of its first ML-eligible position in the roster)} for the sample IDs in the classes in force: the same
    assignment as compute() (ML Task Readiness uses it, so both tabs work on the same samples and classes)."""
    first: dict[str, tuple[str, int]] = {}
    for i, (row, kind) in enumerate(zip(roster, qb.position_kinds(roster, overrides, study_id))):
        if kind == "sample" and not row[sv.I_ALIGN] and row[sv.I_ID] not in first:
            first[row[sv.I_ID]] = (sample_label(row, overrides), i)
    unsettled = _unsettled({k: v[0] for k, v in first.items()}, overrides, conflicts)
    out = {}
    for sid, (lab, i) in first.items():
        if sid in unsettled or not lab:
            continue
        if choice is None:
            out[sid] = (lab, i); continue
        parts = split(lab)
        if parts is None:
            continue
        d = dict(parts); key = tuple(d.get(f) for f in choice["factors"])
        if None not in key and key in {tuple(v) for v in choice["classes"]}:
            out[sid] = (class_name(choice["factors"], key), i)
    return out


def compute(roster: list, study_id: str, overrides: dict | None = None, choice: dict | None = None, params: dict | None = None,
            conflicts: dict | None = None) -> dict:
    labels = eligible_labels(roster, study_id, overrides)
    n_eligible = len(labels)
    unsettled = _unsettled(labels, overrides, conflicts)       # conflicting sample-table lines: no single deposited label
    for i in unsettled:
        labels.pop(i)
    no_label = sum(1 for lab in labels.values() if not lab)
    left_out = not_split = no_factor = 0
    joint = collections.Counter(lab for lab in labels.values() if lab)
    if choice is None:
        counts = joint
    else:
        names = {tuple(v): class_name(choice["factors"], v) for v in choice["classes"]}
        counts = collections.Counter({n: 0 for n in names.values()}); selected = []
        for lab in labels.values():
            parts = split(lab)
            if parts is None:
                not_split += bool(lab); continue
            d = dict(parts)
            key = tuple(d.get(f) for f in choice["factors"])
            if None in key:
                no_factor += 1                     # the string has no value for a picked factor
            elif key in names:
                counts[names[key]] += 1            # one value per factor, so at most one class
                selected.append((names[key], d))
            else:
                left_out += 1
    sizes = sorted(joint.values())
    q = statistics.quantiles(sizes, n=4, method="inclusive") if len(sizes) > 1 else sizes * 3
    out = {"ml_eligible_samples": n_eligible, "without_label": no_label, "conflicting_table_lines": len(unsettled), "left_out": left_out, "not_split": not_split,
           "without_picked_factor": no_factor, "cv_folds": folds(params),
           # informational: the distinct whole factor strings (joint classes) and the samples per joint class
           "joint_classes": len(sizes), "joint_class_samples": ({"median": q[1], "q1": q[0], "q3": q[2], "smallest": sizes[0], "largest": sizes[-1]}
                                                               if sizes else None),
           "classes": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))), "n_classes": sum(1 for c in counts.values() if c),
           "smallest": min(counts.values()) if counts else 0, "largest": max(counts.values()) if counts else 0}
    if choice is not None and any(c == 0 for c in counts.values()):
        empty = [k for k, c in counts.items() if c == 0]
        out.update({"class_support": None, "cv_feasible": None, "balance": None, "folds_covered": None,
                    "not_assessed": f"no ML-eligible sample of {', '.join(empty)} in this matrix"})
    else:
        out.update(_scores({k: c for k, c in counts.items() if c}, params))
    # per-factor breakdown (informational): the factors of the splittable labels of these samples
    fac: dict = collections.defaultdict(collections.Counter); unsplit = 0
    for lab in labels.values():
        parts = split(lab)
        if parts is None:
            unsplit += bool(lab); continue
        for f, v in parts:
            fac[f][v] += 1
    out["factors"] = {f: {"values": dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))), "smallest": min(c.values()), "n_values": len(c)}
                      for f, c in sorted(fac.items())}
    out["factors_varying"] = sum(1 for c in fac.values() if len(c) > 1)
    out["factors_constant"] = sum(1 for c in fac.values() if len(c) == 1)
    out["labels_not_split"] = unsplit
    if choice is not None and out.get("not_assessed") is None:
        out["other_factors"] = other_factors(selected, [c for c, _ in out["classes"].items()], choice["factors"])
    return out
