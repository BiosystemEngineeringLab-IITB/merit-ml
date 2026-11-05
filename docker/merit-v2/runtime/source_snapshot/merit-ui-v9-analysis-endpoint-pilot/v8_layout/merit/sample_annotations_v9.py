"""Sample annotations, MetaBatch-style filters, of one matrix (one analysis from one endpoint), Analytical QC. INFORMATIONAL ONLY:
nothing here is a score, and nothing enters the readiness profile or the FAIR score.

Samples: the ML-eligible positions after the sample edits in force (not QC-like, blank-like or standard-like, not a Tier 1
sample alignment row). A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.

Two deposited sources, both from the 18 September dump:
  factors                 <ST>/factors.json (the Workbench all-factors record that MetaBatch/StdMW reads). Joined by sample ID to
                          local_sample_id, then to mb_sample_id, then the position's own deposited label (as v8). The mwTab text is
                          not used for factors: its factor names differ from factors.json in 911 of 6,687 analyses (for example
                          Factor1 / Cancer status, <enter factor name> / Treatment).
  additional sample data  the fifth field of the SUBJECT_SAMPLE_FACTORS lines of the mwTab text file (JSON if no text file),
                          joined by sample ID; RAW_FILE keys are skipped (FAIR raw-data check).
MetaBatch/StdMW filters (Matrix.java removeNonBatches): a field passes when it has at least two distinct values, a value for at
least 60% of the samples and no more distinct values than 90% of the samples; '' and '-' are missing. Values are compared as written.
Technical field: decided from the field NAME (batch, plate, run order, run day / run; plurals such as Batches included), or, for a
field whose name is not technical, from its VALUES only when every value among these samples is a whole batch / plate / run label
("batch1", "Batch_2", "Batch6_7", "Plate 3", "Run A"); shown as "from values", with its family (batch / plate / run; mixed families are "mixed labels"). A word inside a longer value never counts, so
"STZ injection: no", "same batch medium" or a raw-file name containing Batch_2 cannot make a field technical. A run order is unique per sample by design and
never passes the 90% rule, so it is reported by the samples that have it. A batch-type field that passes is checked against
every non-technical factor that has at least two values among these samples: when each batch level holds one value of that
factor (a factor value may span several batches), batch and factor are confounded. Every technical assignment is an inference and is shown as "-like" (batch-like, ...).
A sample with two different values for one additional-sample-data field (a sample ID listed twice, or a key repeated in one record,
for example the NEG and POS injection orders of ST004440) is not resolved: the value is left out and the sample counted as conflicting.
Batch-like label: pre-calculated by MERIT from the batch-, plate- or run-like field that passes the filters (batch-like first, then
plate-like, mixed labels, run-like; ties in deposit order); a label typed in
Adjust Matrix Properties (sample edit key "batch") replaces it for that sample, and '-' leaves the sample without one.
"""
from __future__ import annotations

import collections
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from merit import additional_sample_data as asd
from merit import structural_v9 as sv

DUMP = Path(os.environ.get("MERIT_MW_DUMP_ROOT", "/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/mw-dump-latest-18thSep-2026-version"))
MISSING = {"", "-"}
MIN_COVERAGE, MAX_DISTINCT = 0.6, 0.9
BATCH_KINDS = ("batch", "plate", "run day or run", "mixed")
# every technical assignment is an inference from the deposit, so it is shown as "-like"
KIND_LABEL = {"batch": "batch-like", "plate": "plate-like", "run day or run": "run-like (run day / run)", "run order": "run order-like",
              "mixed": "batch/plate/run-like (mixed labels)"}
VALUE_LABEL = re.compile(r"(batch|plate|run)[\s_\-#.]*(?:no\.?|nr\.?|number)?[\s_\-#.]*(?:[0-9]{1,3}(?:[_\-/&][0-9]{1,3})*[a-z]?|[a-z])", re.I)
_RUN = {"run", "injection", "inj", "analysis", "analytical", "acquisition", "loading", "measurement"}


def _norm(name: str) -> str:
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name).strip().strip('"'))
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def technical_kind(name: str) -> str | None:
    """'batch', 'plate', 'run order' or 'run day or run' from the field name alone; None otherwise."""
    n = _norm(name); t = {{"batches": "batch", "plates": "plate"}.get(w, w) for w in n.split()}
    if n == "platenum":
        return "plate"
    if n in {"runorder", "injectionorder", "injectionnumber"}:
        return "run order"
    if "batch" in t:
        return "run order" if t & {"order", "position"} else "batch"
    if "plate" in t and not t & {"media", "with", "weight", "tare"}:
        return "plate"
    if (t & {"order", "position"} and t & _RUN) or re.fullmatch(r"(?:injection|run) (?:number|no|nr|id)|injection order \w+|run position", n):
        return "run order"
    if re.fullmatch(r"(?:run|analysis|acquisition|injection|measurement|ms) (?:day|date)|date (?:analy[sz]ed|of (?:analysis|acquisition|run|injection|measurement))"
                    r"|(?:analy[sz]ed|acquired|run) (?:date|on)|run|run day|run a or run b|ms run|lc ms run|lcms run", n):
        return "run day or run"
    return None


def _pairs(text: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(text or "").split("|"):
        if ":" in part:
            key, _, value = part.strip().partition(":")
            if key.strip():
                out[key.strip()] = value.strip()
    return out


@lru_cache(maxsize=64)
def _factors(study_id: str) -> tuple[dict, dict]:
    path = DUMP / study_id / "factors.json"
    by_local: dict[str, dict] = {}; by_mb: dict[str, dict] = {}
    if not path.is_file():
        return by_local, by_mb
    data = json.loads(path.read_text())
    for row in (data.values() if isinstance(data, dict) else data):
        if not isinstance(row, dict):
            continue
        f = _pairs(row.get("factors") or row.get("Factors") or "")
        local = str(row.get("local_sample_id") or row.get("sample_id") or "").strip()
        mb = str(row.get("mb_sample_id") or row.get("Subject ID") or "").strip()
        if local and f:
            by_local[local] = f
        if mb and f:
            by_mb[mb] = f
    return by_local, by_mb


def value_family(values: list[str]) -> str | None:
    """'batch', 'plate', 'run day or run' or 'mixed' when every value is a whole batch / plate / run label; else None."""
    given = [v.strip() for v in values if v not in MISSING]
    fams = set()
    for v in given:
        m = VALUE_LABEL.fullmatch(v)
        if not m:
            return None
        fams.add({"batch": "batch", "plate": "plate", "run": "run day or run"}[m.group(1).lower()])
    return None if not given else (fams.pop() if len(fams) == 1 else "mixed")


@lru_cache(maxsize=256)
def _additional(study_id: str, analysis_id: str) -> tuple[str, dict]:
    """Per sample ID: every value of every field over all its records (a key repeated in one record or a sample ID listed twice
    can give two values; they are kept, not resolved)."""
    where, records = asd.read(DUMP / study_id / analysis_id / "mwtab", analysis_id)
    by: dict[str, dict] = {}
    for r in records:
        d = by.setdefault(r["sample"], {})
        for k, v in r["fields"]:
            if not asd.RAW_FILE_KEY.match(k):
                d.setdefault(k, [])
                if v not in d[k]:
                    d[k].append(v)
    return where, by


def _filters(values: list[str], n: int) -> tuple[bool, int, int, str]:
    present = [v for v in values if v not in MISSING]
    distinct = len(set(present))
    if distinct < 2:
        why = "one value" if distinct == 1 else "no value"
    elif len(present) < MIN_COVERAGE * n:
        why = "values for fewer than 60% of samples"
    elif distinct > MAX_DISTINCT * n:
        why = "more distinct values than 90% of samples"
    else:
        why = ""
    return not why, len(present), distinct, why


def confounded(levels_of: list[str], factors: dict[str, list[str]]) -> list[dict]:
    """Factors confounded with a batch-like field or label: every level (batch) holds a single value of the factor, and the factor has
    at least two values among the samples. Not necessarily one-to-one: one factor value can span several batches. Per factor, the
    batches of each factor value are kept for display."""
    out = []
    for fk, fv in factors.items():
        levels = collections.defaultdict(set)
        for x, y in zip(levels_of, fv):
            if x not in MISSING and y not in MISSING:
                levels[x].add(y)
        values = {y for s in levels.values() for y in s}
        if len(values) >= 2 and all(len(s) == 1 for s in levels.values()):
            by_value = collections.defaultdict(list)
            for x, s in levels.items():
                by_value[next(iter(s))].append(x)
            out.append({"factor": fk, "one_to_one": all(len(v) == 1 for v in by_value.values()),
                        "batches_per_value": {y: sorted(v) for y, v in sorted(by_value.items())}})
    return out


def compute(study_id: str, analysis_id: str, roster: list[list], overrides: dict | None = None) -> dict[str, Any]:
    pos = [p for p in sv.effective_positions(roster, overrides) if p["eligible"] and not p["excluded"] and not p["row"][sv.I_ALIGN]]
    n = len(pos); ids = [str(p["sample_id"]).strip() for p in pos]
    by_local, by_mb = _factors(study_id)
    where, extra = _additional(study_id, analysis_id)
    cols: dict[tuple[str, str], dict[int, str]] = {}
    join = collections.Counter()
    for i, p in enumerate(pos):
        f, how = by_local.get(ids[i]), "sample ID in factors.json"
        if f is None:
            f, how = by_mb.get(ids[i]), "Workbench sample accession in factors.json"
        if f is None:
            f, how = _pairs(p["row"][sv.I_LABEL]) or _pairs(p["row"][sv.I_TLABEL]), "deposited label of the position"
        join[how if f else "no factor"] += 1
        for k, v in f.items():
            cols.setdefault(("factor", k), {})[i] = v
    matched = 0
    conflicts = collections.Counter()
    for i, sid in enumerate(ids):
        r = extra.get(sid)
        if not r:
            continue
        matched += 1
        for k, vs in r.items():
            given = [v for v in vs if v not in MISSING]
            if len(given) > 1:                    # two different values for one sample: not resolved, left out and counted
                conflicts[k] += 1
                cols.setdefault(("additional", k), {})[i] = ""
            else:
                cols.setdefault(("additional", k), {})[i] = given[0] if given else (vs[0] if vs else "")
    values = {key: [d.get(i, "") for i in range(n)] for key, d in cols.items()}
    kinds = {key: (technical_kind(key[1]), "name") for key in values}
    for key, vals in values.items():
        fam = None if kinds[key][0] else value_family(vals)
        if fam:
            kinds[key] = (fam, "values")
    biological = {k: v for (w, k), v in values.items() if w == "factor" and not kinds[(w, k)][0]}
    factors, technical = [], []
    for (w, k), vals in values.items():
        ok, present, distinct, why = _filters(vals, n)
        kind, by = kinds[(w, k)]
        if w == "factor":
            factors.append({"name": k, "with_value": present, "distinct": distinct, "passes": ok, "why_not": why, "technical": kind})
        if not kind or not present:
            continue
        rec = {"name": k, "where": w, "kind": kind, "identified_by": by, "with_value": present, "distinct": distinct, "passes": ok, "why_not": why,
               "conflicting_samples": conflicts.get(k, 0) if w == "additional" else 0,
               "examples": list(dict.fromkeys(v for v in vals if v not in MISSING))[:3]}
        if kind == "run order":
            rec["usable"] = present >= MIN_COVERAGE * n
        else:
            rec["usable"] = ok
            if ok:
                rec["confounded"] = confounded(vals, biological)
                rec["separates"] = [c["factor"] for c in rec["confounded"]]
        technical.append(rec)
    seen, unique = set(), []
    for t in technical:                       # the same name in factors and additional sample data is listed once (factors first)
        if t["name"].casefold() not in seen:
            seen.add(t["name"].casefold()); unique.append(t)
    # the batch-like label: pre-calculated by MERIT from the batch-, plate- or run-like field that passes the filters, batch-like first,
    # then plate-like, mixed labels, run-like; ties in deposit order (factors first, then additional sample data); a user label from Adjust Matrix Properties replaces it per sample
    rank = {"batch": 0, "plate": 1, "mixed": 2, "run day or run": 3}      # a field read as a batch first, then plate, mixed labels, run day / run
    source = min((t for t in unique if t["kind"] in BATCH_KINDS and t["usable"]), key=lambda t: rank[t["kind"]], default=None)
    pre = values[(source["where"], source["name"])] if source else [""] * n
    user = [str(((overrides or {}).get(sid) or {}).get("batch", "") or "").strip() for sid in ids]
    label = [u or m for u, m in zip(user, pre)]
    rec_label = None
    if source or any(user):
        ok, present, distinct, why = _filters(label, n)
        conf = confounded(label, biological) if ok else []
        sep = [c["factor"] for c in conf]
        rec_label = {"pre_calculated_from": source["name"] if source else None, "pre_calculated_kind": source["kind"] if source else None,
                     "samples_edited": sum(1 for u in user if u), "with_value": present, "distinct": distinct, "passes": ok, "why_not": why,
                     "separates": sep, "confounded": conf, "examples": list(dict.fromkeys(v for v in label if v not in MISSING))[:3]}
    return {"informational": True, "ml_eligible_samples": n, "factor_join": dict(join),
            "additional_sample_data": {"read_from": where, "samples_matched": matched},
            "usable_factors": sum(1 for f in factors if f["passes"]), "factors": factors, "technical_fields": unique,
            "batch_like_label": rec_label, "_pre": dict(zip(ids, pre)) if source else {}}


def precalculated_labels(study_id: str, analyses: list[dict]) -> dict[str, tuple[str, str]]:
    """Per sample ID of the study: (MERIT's pre-calculated batch-like label, field it was read from), from the first matrix that has one."""
    out: dict[str, tuple[str, str]] = {}
    for a in analyses:
        for ep in a["endpoints"].values():
            if "roster" not in ep:
                continue
            r = compute(study_id, a["analysis_id"], ep["roster"])
            src = (r["batch_like_label"] or {}).get("pre_calculated_from")
            for sid, v in r["_pre"].items():
                if v not in MISSING and sid not in out:
                    out[sid] = (v, src)
    return out


def technical_additional_names(study_id: str, analysis_id: str, rosters: list[list[list]], overrides: dict | None = None) -> set[str]:
    """Additional-sample-data fields shown as technical for any matrix of the analysis (Additional sample data does not repeat them)."""
    return {t["name"] for roster in rosters for t in compute(study_id, analysis_id, roster, overrides)["technical_fields"] if t["where"] == "additional"}
