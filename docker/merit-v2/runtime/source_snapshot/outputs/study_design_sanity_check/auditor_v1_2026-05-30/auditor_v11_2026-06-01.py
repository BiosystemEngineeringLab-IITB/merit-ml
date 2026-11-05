"""
Auditor v11 (2026-06-01) — uses ALL available evidence:
  v10 inputs (v9 sheet + MW REST factors/summary)
  + new mwtab-derived features (study_summary, SUBJECT block, real subject IDs,
    TREATMENT/COLLECTION/SAMPLEPREP narratives)

Adds the following NEW signals to the 8-signal cascade from v10:

  Signal 9  — STUDY_SUMMARY narrative keyword scan (additive to PROJECT_SUMMARY)
  Signal 10 — SUBJECT_TYPE categorical mapping
                Human / HUMAN                          → cohort lean (+2)
                Mammal / Animal / Mouse / Rat / Insect /
                  Fish / Invertebrate                  → experimental lean (+2)
                Cultured cells / Cells                 → experimental (+3)
                Plant / Bacteria / Yeast               → experimental (+3)
  Signal 11 — SUBJECT.GENOTYPE_STRAIN present          → experimental (+2)
              SUBJECT.ANIMAL_ANIMAL_SUPPLIER present   → experimental (+2)
              SUBJECT.AGE_OR_AGE_RANGE wide-range +
                  Human SUBJECT_TYPE                   → cohort (+2)
  Signal 12 — Real per-sample SUBJECT_SAMPLE_FACTORS structure
                (n_unique_subject_ids, samples_per_subject_mean)
                ≥20 subjects + ratio ~1                → cohort (+3)
                <5 subjects + ratio ≥3                  → experimental (+2)
  Signal 13 — TREATMENT_SUMMARY / COLLECTION_SUMMARY / SAMPLEPREP_SUMMARY
                keyword scan (same 3 regex families)

Tie-breaker cascade is extended with new decisive rules:
  A11  Cell / cultured cells / bacteria / yeast SUBJECT_TYPE → experimental
  A12  Plant SUBJECT_TYPE → experimental
  A13  GENOTYPE_STRAIN populated → experimental
  A14  ANIMAL_ANIMAL_SUPPLIER populated → experimental
  A15  ≥20 real subjects + ratio ≤1.5 + Human SUBJECT_TYPE → cohort
  A16  <5 real subjects + ratio ≥3 → experimental (replicate-based)

Outputs:
  programmatic_design_audit_v11_2026-06-01.tsv
"""
from __future__ import annotations
import json
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
REVIEW_XLSX = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/outputs/study_design_sanity_check/"
                   "mw_study_design_type_review_sheet_v9_2026-05-27.xlsx")
LOCAL_RAW   = Path("/home/shayantan/metabolomics/metabolomics_pipeline/"
                   "manuscript/data_used_for_harmony/MW_raw_data_rest")
MWTAB_FEATS = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/outputs/study_design_sanity_check/"
                   "auditor_v1_2026-05-30/study_mwtab_features_2026-06-01.tsv")
OUT_DIR     = Path("/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/outputs/study_design_sanity_check/"
                   "auditor_v1_2026-05-30")
OUT_TSV     = OUT_DIR / "programmatic_design_audit_v11_2026-06-01.tsv"
OUT_JSON    = OUT_DIR / "programmatic_design_audit_v11_summary_2026-06-01.json"

REST_BASE = "https://www.metabolomicsworkbench.org/rest/study/study_id"

# ── Keyword families (re-used across all narrative scans) ────────────────────
EXP_TITLE_RE = re.compile(
    r"\b(time[\s\-]?course|time[\s\-]?series|kinetic|dose[\s\-]?response|"
    r"dose[\s\-]?dependent|knockout|knock[\s\-]?down|knockin|transgenic|"
    r"in[\s\-]?vitro|cell[\s\-]?line|cell[\s\-]?culture|primary[\s\-]?culture|"
    r"tracer|stable[\s\-]?isotope|13C|15N|2H[\s\-]?labelled|2H[\s\-]?labeled|"
    r"flux|U-13C|fluxomic|isotopolog|treated\s+with|exposure|exposed\s+to|"
    r"perturbation|stimulated|stimulation|induction|induced|silenc(ed|ing)|"
    r"crispr|sirna|mutant|reporter|chemostat|fermentation|bioreactor|"
    r"feeding\s+study|feed\s+experiment|dietary\s+intervention|"
    r"drug\s+screen|compound\s+screen)\b",
    re.I,
)
COHORT_TITLE_RE = re.compile(
    r"\b(cohort|case[\s\-]?control|case[\s\-]?study|patients?|individuals?|"
    r"healthy\s+controls?|healthy\s+volunteers?|epidemiolog|observational|"
    r"cross[\s\-]?sectional|longitudinal\s+cohort|biobank|registry|"
    r"discovery\s+cohort|validation\s+cohort|nested\s+case|matched\s+pair|"
    r"diagnosis|diagnostic|prognost|biomarker\s+discovery|"
    r"clinical\s+cohort|clinical\s+samples?|patient\s+samples?|"
    r"newborn|infants?|pregnancy|pregnant|adults?|adolescents?|"
    r"twins?\s+study|family\s+study)\b",
    re.I,
)
INVIVO_EXP_RE = re.compile(
    r"\b(mouse\s+model|murine\s+model|rat\s+model|animal\s+model|"
    r"transgenic\s+(mouse|mice|rat|rats)|knockout\s+(mouse|mice)|"
    r"chow\s+diet|high[\s\-]?fat\s+diet|hfd|standard\s+diet|"
    r"vehicle\s+control|sham|sham[\s\-]?operated|placebo[\s\-]?controlled)\b",
    re.I,
)
EXP_FACTOR_RE = re.compile(
    r"\b(time|timepoint|time[\s\-]?point|hour|hours|hr|hrs|day|days|min|minute|"
    r"week|weeks|month|months|dose|concentration|conc\.?|treatment|"
    r"compound|drug|stimulus|stimulation|condition|exposure|13c|15n|"
    r"genotype|strain|mutation|knockout|knockdown|wild[\s\-]?type|"
    r"medium|media|temperature|ph|oxygen|hypoxia|culture|passage|"
    r"replicate|technical[\s\-]?replicate|biological[\s\-]?replicate)\b",
    re.I,
)
COHORT_FACTOR_RE = re.compile(
    r"\b(diagnosis|disease[\s\-]?status|case[\s\-]?control|case[\/\\]control|"
    r"healthy|control[\s\-]?subject|patient[\s\-]?status|"
    r"clinical[\s\-]?status|prognosis|stage|grade|"
    r"smoking|smoker|alcohol|bmi|age|sex|gender|ethnicity|race|"
    r"diabetes|cancer\s+stage|tumor\s+stage|hypertension)\b",
    re.I,
)

# Subject-type lexicons
SUBJ_TYPE_HUMAN   = {"human", "humans", "homo sapiens"}
SUBJ_TYPE_ANIMAL  = {"animal", "mammal", "mouse", "mice", "rat", "rats",
                     "rodent", "insect", "fish", "invertebrate", "vertebrate",
                     "bird", "reptile", "amphibian", "primate", "monkey",
                     "pig", "porcine", "bovine", "canine", "rabbit", "sheep",
                     "guinea pig"}
SUBJ_TYPE_CELLS   = {"cultured cells", "cells", "cell culture", "cell line",
                     "hek", "hela", "cell"}
SUBJ_TYPE_MICROBE = {"bacteria", "bacterial cells", "yeast", "fungus", "fungi",
                     "fungal", "algae", "archaea", "microbe", "microbial",
                     "virus", "bacteriophage", "phage"}
SUBJ_TYPE_PLANT   = {"plant", "plants", "moss", "arabidopsis"}


def _flatten_hits(hits):
    out = []
    for h in hits:
        if isinstance(h, tuple):
            out.extend([x for x in h if x])
        elif h:
            out.append(h)
    return [x.strip() for x in out if x.strip()]


def safe_str(v) -> str:
    if v is None: return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def safe_int(v, default: int = 0) -> int:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)): return default
        return int(float(v))
    except Exception:
        return default


def safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)): return default
        return float(v)
    except Exception:
        return default


# ── Helpers from v10 ─────────────────────────────────────────────────────────
def load_local_raw(study_id: str) -> dict | None:
    fp = LOCAL_RAW / f"{study_id}_raw.json"
    if not fp.exists():
        return None
    try:
        with fp.open() as f:
            return json.load(f)
    except Exception:
        return None


def parse_factors_block(factors_raw: Any) -> list[dict]:
    if factors_raw is None: return []
    if isinstance(factors_raw, list):
        return [x for x in factors_raw if isinstance(x, dict)]
    if isinstance(factors_raw, dict):
        if factors_raw and all(k.isdigit() for k in factors_raw.keys() if isinstance(k, str)):
            return [v for v in factors_raw.values() if isinstance(v, dict)]
        if "factors" in factors_raw:
            return [factors_raw]
    return []


def split_factor_string(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not s or not isinstance(s, str): return out
    for part in s.split("|"):
        part = part.strip()
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def extract_signals(raw: dict | None) -> dict:
    sig: dict[str, Any] = {
        "title": "", "species": "", "analysis_type": "",
        "n_samples_reported": "",
        "sample_sources": [], "factor_names": [],
        "factor_values_by_name": defaultdict(list),
        "n_distinct_factor_combinations": 0, "n_factor_rows": 0,
        "n_unique_subjects_inferred": 0, "samples_per_subject_median": 0.0,
    }
    if not raw: return sig
    re_ = raw.get("raw_endpoints", {})
    summary = re_.get("summary") or {}
    if isinstance(summary, dict):
        sig["title"] = str(summary.get("study_title") or "")
        sig["species"] = str(summary.get("species") or "")
        sig["analysis_type"] = str(summary.get("analysis_type") or "")
        sig["n_samples_reported"] = str(summary.get("number_of_samples") or "")

    factors = parse_factors_block(re_.get("factors"))
    sig["n_factor_rows"] = len(factors)

    src_counter: Counter[str] = Counter()
    combo_counter: Counter[str] = Counter()
    subject_id_counter: Counter[str] = Counter()
    factor_name_order: list[str] = []
    factor_name_seen: set[str] = set()
    factor_values_by_name: dict[str, set[str]] = defaultdict(set)

    for row in factors:
        ss = (row.get("sample_source") or "").strip()
        if ss: src_counter[ss] += 1
        fs = (row.get("factors") or "").strip()
        parsed = split_factor_string(fs)
        for k, v in parsed.items():
            if k not in factor_name_seen:
                factor_name_seen.add(k); factor_name_order.append(k)
            factor_values_by_name[k].add(v)
        if parsed:
            combo_counter[" | ".join(f"{k}={v}" for k, v in sorted(parsed.items()))] += 1
        local_id = (row.get("local_sample_id") or "").strip()
        if local_id:
            stem = re.sub(r"[_\-]?(rep|replicate|R)\s*\d+$", "", local_id, flags=re.I)
            stem = re.sub(r"[_\-]\d+$", "", stem)
            subject_id_counter[stem] += 1

    sig["sample_sources"] = [s for s, _ in src_counter.most_common(5)]
    sig["factor_names"] = factor_name_order
    sig["factor_values_by_name"] = {k: sorted(v)[:20] for k, v in factor_values_by_name.items()}
    sig["n_distinct_factor_combinations"] = len(combo_counter)
    sig["n_unique_subjects_inferred"] = len(subject_id_counter)
    if subject_id_counter:
        counts = sorted(subject_id_counter.values())
        n = len(counts)
        sig["samples_per_subject_median"] = float(
            counts[n//2] if n % 2 else (counts[n//2 - 1] + counts[n//2]) / 2
        )
    return sig


# ── Classifier ───────────────────────────────────────────────────────────────
def classify(sig: dict, v9_row: pd.Series, mwtab_row: pd.Series) -> dict:
    evidence: list[str] = []

    # ── Signal 1 — Metadata gate ──
    n_eligible = safe_int(v9_row.get("n_ml_eligible_samples"))
    n_total    = safe_int(v9_row.get("n_total_samples"))
    n_classes  = safe_int(v9_row.get("n_classes"))
    n_feat_mat = safe_int(v9_row.get("n_feature_matrices"))

    if n_feat_mat == 0:
        return _verdict("metadata_only_or_no_usable_matrix", "high",
            "no feature matrix attached", ["n_feature_matrices=0"])
    if n_total == 0 and n_eligible == 0:
        return _verdict("metadata_only_or_no_usable_matrix", "high",
            "zero samples in any matrix", ["n_total_samples=0"])
    if (n_eligible == 0 and n_total > 0) and n_classes <= 1:
        return _verdict("metadata_only_or_no_usable_matrix", "moderate",
            "samples present but no usable classes/labels",
            [f"n_ml_eligible_samples=0, n_classes={n_classes}"])

    title = sig["title"] or ""
    description = safe_str(v9_row.get("description"))
    species = (sig["species"] or "").lower()
    factor_names = sig["factor_names"]
    factor_values = sig["factor_values_by_name"]
    n_combos = sig["n_distinct_factor_combinations"]
    n_subjects_inf = sig["n_unique_subjects_inferred"]
    spp_median = sig["samples_per_subject_median"]

    # NEW: mwtab features
    study_summary = safe_str(mwtab_row.get("study_summary_mwtab"))
    subject_type  = safe_str(mwtab_row.get("subject_type"))
    genotype      = safe_str(mwtab_row.get("genotype_strain"))
    supplier      = safe_str(mwtab_row.get("animal_supplier"))
    age_range     = safe_str(mwtab_row.get("age_or_age_range"))
    gender        = safe_str(mwtab_row.get("gender"))
    treatment_summ  = safe_str(mwtab_row.get("treatment_summary"))
    collection_summ = safe_str(mwtab_row.get("collection_summary"))
    sampleprep_summ = safe_str(mwtab_row.get("sampleprep_summary"))
    n_subj_real   = safe_int(mwtab_row.get("n_unique_subject_ids"))
    spp_mean      = safe_float(mwtab_row.get("samples_per_subject_mean"))
    subj_id_pop   = str(mwtab_row.get("subject_id_populated","")).lower() == "true"
    subject_type_norm = subject_type.lower().strip()

    # ── Signal 2 — title scan ──
    title_exp_hits    = _flatten_hits(EXP_TITLE_RE.findall(title))
    title_coh_hits    = _flatten_hits(COHORT_TITLE_RE.findall(title))
    title_invivo_hits = _flatten_hits(INVIVO_EXP_RE.findall(title))

    # ── Signal 8 — description (PROJECT_SUMMARY equivalent in v9) scan ──
    desc_exp_hits    = _flatten_hits(EXP_TITLE_RE.findall(description))
    desc_coh_hits    = _flatten_hits(COHORT_TITLE_RE.findall(description))
    desc_invivo_hits = _flatten_hits(INVIVO_EXP_RE.findall(description))
    # Suppress overlaps with title
    t_set_e = {s.lower() for s in title_exp_hits}
    t_set_c = {s.lower() for s in title_coh_hits}
    t_set_i = {s.lower() for s in title_invivo_hits}
    desc_exp_hits    = [s for s in desc_exp_hits    if s.lower() not in t_set_e]
    desc_coh_hits    = [s for s in desc_coh_hits    if s.lower() not in t_set_c]
    desc_invivo_hits = [s for s in desc_invivo_hits if s.lower() not in t_set_i]

    # ── NEW Signal 9 — STUDY_SUMMARY scan (distinct from description) ──
    ss_exp_hits    = _flatten_hits(EXP_TITLE_RE.findall(study_summary))
    ss_coh_hits    = _flatten_hits(COHORT_TITLE_RE.findall(study_summary))
    ss_invivo_hits = _flatten_hits(INVIVO_EXP_RE.findall(study_summary))
    # Suppress overlaps with title and description
    d_set_e = {s.lower() for s in desc_exp_hits}
    d_set_c = {s.lower() for s in desc_coh_hits}
    d_set_i = {s.lower() for s in desc_invivo_hits}
    ss_exp_hits    = [s for s in ss_exp_hits    if s.lower() not in t_set_e | d_set_e]
    ss_coh_hits    = [s for s in ss_coh_hits    if s.lower() not in t_set_c | d_set_c]
    ss_invivo_hits = [s for s in ss_invivo_hits if s.lower() not in t_set_i | d_set_i]

    # ── NEW Signal 13 — TREATMENT/COLLECTION/SAMPLEPREP scan ──
    tcsp_blob = " \n ".join([treatment_summ, collection_summ, sampleprep_summ])
    tcsp_exp_hits    = _flatten_hits(EXP_TITLE_RE.findall(tcsp_blob))
    tcsp_coh_hits    = _flatten_hits(COHORT_TITLE_RE.findall(tcsp_blob))
    tcsp_invivo_hits = _flatten_hits(INVIVO_EXP_RE.findall(tcsp_blob))

    # ── Signal 3 — factor name scan ──
    factor_blob = " | ".join(factor_names)
    factor_exp_hits = _flatten_hits(EXP_FACTOR_RE.findall(factor_blob))
    factor_coh_hits = _flatten_hits(COHORT_FACTOR_RE.findall(factor_blob))

    # ── Signal 4 — sample sources ──
    sources = " ".join(sig["sample_sources"]).lower()
    is_cell_culture = bool(re.search(r"\b(cell|cells|cell\s+line|macrophage|"
                                     r"hepatocyt|astrocyt|fibroblast|culture|"
                                     r"cultured|organoid|spheroid|hek|hela|"
                                     r"jurkat|raw\s*264|3t3|caco|huvec)\b", sources))
    is_plant_microbe = bool(re.search(r"\b(plant|leaf|leaves|root|stem|seed|"
                                      r"seedling|bacteria|bacterium|microbe|"
                                      r"microbial|yeast|fungus|fungal|algae|"
                                      r"coral)\b", sources))
    is_clinical_tissue = bool(re.search(r"\b(blood|plasma|serum|urine|saliva|"
                                        r"csf|cerebrospinal|stool|feces|fecal|"
                                        r"breath|sweat|biopsy|tumor|tumour|"
                                        r"adipose|breast\s+tissue|liver\s+biopsy)\b",
                                        sources))

    # ── Signal 5 — subject structure (inferred from local_sample_id) ──
    inferred_cohort_struct = (n_subjects_inf >= 8 and spp_median <= 2.0)
    inferred_replicate_struct = (n_combos >= 2 and spp_median >= 3.0)

    # ── NEW Signal 12 — REAL subject_id structure (from mwtab SSF) ──
    real_cohort_struct = (subj_id_pop and n_subj_real >= 20 and spp_mean <= 1.5)
    real_replicate_struct = (subj_id_pop and n_subj_real > 0
                              and n_subj_real < 5 and spp_mean >= 3.0)

    # ── Signal 6 — time-course / dose detection ──
    has_time_factor = any(re.search(r"\b(time|hour|hr|day|min|week|month|stage)\b", n, re.I)
                          for n in factor_names)
    has_dose_factor = any(re.search(r"\b(dose|concentration|conc|treatment|"
                                    r"compound|drug|stimul)\b", n, re.I)
                          for n in factor_names)
    time_factor_values: list[str] = []
    for n in factor_names:
        if re.search(r"\b(time|hour|hr|day|min|week|month)\b", n, re.I):
            time_factor_values = factor_values.get(n, []); break
    multi_time = has_time_factor and len(time_factor_values) >= 2

    # ── Signal 6b — diagnostic/demographic factors ──
    has_diag_factor = any(re.search(r"\b(diagnos|case|control|disease[\s\-]?status|"
                                    r"healthy|patient[\s\-]?status|clinical|"
                                    r"smoking|smoker|bmi)\b", n, re.I)
                          for n in factor_names)
    has_demographic_factor = any(re.search(r"\b(age|sex|gender|ethnicity|race)\b", n, re.I)
                                 for n in factor_names)

    # ── Signal 7 — species prior ──
    is_human = "homo sapiens" in species or species == "human" or "human" in species
    is_model_animal = bool(re.search(r"mus musculus|mouse|rat|rattus|drosophila|"
                                     r"zebrafish|danio|c\.\s*elegans|caenorhabditis",
                                     species, re.I))

    # ── NEW Signal 10/11 — SUBJECT block from mwtab ──
    is_subj_human   = subject_type_norm in SUBJ_TYPE_HUMAN
    is_subj_animal  = subject_type_norm in SUBJ_TYPE_ANIMAL
    is_subj_cells   = subject_type_norm in SUBJ_TYPE_CELLS
    is_subj_microbe = subject_type_norm in SUBJ_TYPE_MICROBE
    is_subj_plant   = subject_type_norm in SUBJ_TYPE_PLANT

    # Age range parsing: detect ranges like "55-91", "18-65", or words
    age_is_range = bool(re.search(r"\d{1,3}\s*[-–]\s*\d{1,3}", age_range))
    gender_mixed = "/" in gender or "," in gender or " and " in gender.lower()

    # ────────── SCORING ──────────
    score_exp, score_coh = 0, 0

    # S2 title
    if title_exp_hits:
        score_exp += 3; evidence.append(f"title_exp_kw={','.join(sorted(set(title_exp_hits))[:6])[:120]}")
    if title_invivo_hits:
        score_exp += 2; evidence.append(f"title_invivo_kw={','.join(sorted(set(title_invivo_hits))[:6])[:120]}")
    if title_coh_hits:
        score_coh += 3; evidence.append(f"title_cohort_kw={','.join(sorted(set(title_coh_hits))[:6])[:120]}")

    # S8 description
    if desc_exp_hits:
        score_exp += 2; evidence.append(f"description_exp_kw={','.join(sorted(set(desc_exp_hits))[:6])[:120]}")
    if desc_invivo_hits:
        score_exp += 1; evidence.append(f"description_invivo_kw={','.join(sorted(set(desc_invivo_hits))[:6])[:120]}")
    if desc_coh_hits:
        score_coh += 2; evidence.append(f"description_cohort_kw={','.join(sorted(set(desc_coh_hits))[:6])[:120]}")

    # S9 STUDY_SUMMARY
    if ss_exp_hits:
        score_exp += 2; evidence.append(f"study_summary_exp_kw={','.join(sorted(set(ss_exp_hits))[:6])[:120]}")
    if ss_invivo_hits:
        score_exp += 1; evidence.append(f"study_summary_invivo_kw={','.join(sorted(set(ss_invivo_hits))[:6])[:120]}")
    if ss_coh_hits:
        score_coh += 2; evidence.append(f"study_summary_cohort_kw={','.join(sorted(set(ss_coh_hits))[:6])[:120]}")

    # S13 TREATMENT/COLLECTION/SAMPLEPREP
    if tcsp_exp_hits:
        score_exp += 1; evidence.append(f"protocol_exp_kw={','.join(sorted(set(tcsp_exp_hits))[:6])[:120]}")
    if tcsp_invivo_hits:
        score_exp += 1; evidence.append(f"protocol_invivo_kw={','.join(sorted(set(tcsp_invivo_hits))[:6])[:120]}")
    if tcsp_coh_hits:
        score_coh += 1; evidence.append(f"protocol_cohort_kw={','.join(sorted(set(tcsp_coh_hits))[:6])[:120]}")

    # S3 factor names
    if factor_exp_hits:
        score_exp += 2; evidence.append(f"factor_exp_kw={','.join(sorted(set(factor_exp_hits))[:6])[:120]}")
    if factor_coh_hits:
        score_coh += 2; evidence.append(f"factor_cohort_kw={','.join(sorted(set(factor_coh_hits))[:6])[:120]}")
    if multi_time:
        score_exp += 3; evidence.append(f"multi_timepoints={len(time_factor_values)}_values")
    if has_dose_factor:
        score_exp += 1; evidence.append("dose_or_treatment_factor")
    if has_diag_factor:
        score_coh += 2; evidence.append("diagnostic_factor")
    if has_demographic_factor and (is_human or is_subj_human):
        score_coh += 1; evidence.append("demographic_factor")

    # S4 sample sources
    if is_cell_culture:
        score_exp += 3; evidence.append("sample_source_cell_culture")
    if is_plant_microbe:
        score_exp += 2; evidence.append("sample_source_plant_or_microbe")
    if is_clinical_tissue and (is_human or is_subj_human):
        score_coh += 1; evidence.append("clinical_tissue_human")

    # S5 inferred subject structure
    if inferred_cohort_struct and (is_human or is_subj_human):
        score_coh += 2; evidence.append(f"inferred_many_subjects (n_inf={n_subjects_inf}, spp_med={spp_median:.1f})")
    if inferred_replicate_struct:
        score_exp += 2; evidence.append(f"inferred_replicate_struct (n_combos={n_combos}, spp_med={spp_median:.1f})")

    # NEW S12 REAL subject structure from mwtab SSF
    if real_cohort_struct and is_subj_human:
        score_coh += 3; evidence.append(f"real_many_subjects (n_real={n_subj_real}, spp_mean={spp_mean:.1f})")
    if real_replicate_struct:
        score_exp += 2; evidence.append(f"real_replicate_struct (n_real={n_subj_real}, spp_mean={spp_mean:.1f})")

    # S7 species prior
    if is_model_animal and not title_coh_hits:
        score_exp += 1; evidence.append("model_animal_species_prior")

    # NEW S10 SUBJECT_TYPE
    if is_subj_human:
        score_coh += 2; evidence.append(f"subject_type={subject_type}")
    if is_subj_animal:
        score_exp += 2; evidence.append(f"subject_type={subject_type}")
    if is_subj_cells:
        score_exp += 3; evidence.append(f"subject_type={subject_type}")
    if is_subj_microbe:
        score_exp += 3; evidence.append(f"subject_type={subject_type}")
    if is_subj_plant:
        score_exp += 3; evidence.append(f"subject_type={subject_type}")

    # NEW S11 SUBJECT auxiliary fields
    if genotype:
        score_exp += 2; evidence.append(f"genotype_strain={genotype[:40]}")
    if supplier:
        score_exp += 2; evidence.append(f"animal_supplier={supplier[:40]}")
    if age_is_range and is_subj_human:
        score_coh += 2; evidence.append(f"human_age_range={age_range}")
    if gender_mixed and is_subj_human:
        score_coh += 1; evidence.append(f"human_mixed_gender={gender}")

    # ── Decision ──
    if score_exp == 0 and score_coh == 0:
        if n_combos >= 2 and spp_median >= 2:
            return _verdict("experimental_or_replicate_based", "low",
                "no keyword hits; factor combos + replicate structure",
                [f"n_combos={n_combos}, spp_median={spp_median:.1f}"])
        return _verdict("unclear", "low",
            "no discriminating keywords or factor structure",
            [f"factor_names={factor_names[:5]}, sources={sig['sample_sources'][:3]}"])

    label = ("experimental_or_replicate_based" if score_exp > score_coh else
             "cohort_or_subject_based" if score_coh > score_exp else "unclear")
    margin = abs(score_exp - score_coh)
    if margin >= 5:
        conf = "high"
    elif margin >= 3:
        conf = "moderate"
    else:
        conf = "low"

    reason = (f"exp_score={score_exp}, coh_score={score_coh}; "
              f"primary={'experimental cues' if score_exp > score_coh else 'cohort cues'}")
    return _verdict(label, conf, reason, evidence[:12])


def _verdict(label: str, confidence: str, reason: str, evidence: list[str]) -> dict:
    return {
        "auditor_label": label,
        "auditor_confidence": confidence,
        "auditor_reason": reason,
        "auditor_evidence": " ; ".join(evidence),
    }


# ── Main ─────────────────────────────────────────────────────────────────────
def main(limit: int | None = None):
    review = pd.read_excel(REVIEW_XLSX)
    mwtab  = pd.read_csv(MWTAB_FEATS, sep='\t', low_memory=False, keep_default_na=False)
    mwtab_by_sid = mwtab.set_index('study_id')
    print(f"v9: {len(review)} rows; mwtab feats: {len(mwtab)}", flush=True)

    results = []
    missing_local = 0
    n_iter = len(review) if limit is None else min(limit, len(review))
    for i, (_, row) in enumerate(review.iterrows()):
        if limit is not None and i >= limit:
            break
        sid = str(row["study_id"]).strip()
        raw = load_local_raw(sid)
        source = "local"
        if raw is None:
            missing_local += 1
            source = "missing"
        sig = extract_signals(raw)

        mwtab_row = mwtab_by_sid.loc[sid] if sid in mwtab_by_sid.index else pd.Series()

        verdict = classify(sig, row, mwtab_row)
        out = {
            "study_id": sid,
            "title": sig["title"] or str(row.get("title", "")),
            "description_chars": len(safe_str(row.get("description", ""))),
            "study_summary_mwtab_chars": len(safe_str(mwtab_row.get("study_summary_mwtab"))),
            "subject_type": safe_str(mwtab_row.get("subject_type")),
            "genotype_strain": safe_str(mwtab_row.get("genotype_strain")),
            "animal_supplier": safe_str(mwtab_row.get("animal_supplier")),
            "age_or_age_range": safe_str(mwtab_row.get("age_or_age_range")),
            "gender": safe_str(mwtab_row.get("gender")),
            "n_unique_subject_ids_real": safe_int(mwtab_row.get("n_unique_subject_ids")),
            "samples_per_subject_mean_real": safe_float(mwtab_row.get("samples_per_subject_mean")),
            "subject_id_populated": safe_str(mwtab_row.get("subject_id_populated")),
            "n_distinct_factor_combinations": sig["n_distinct_factor_combinations"],
            "n_unique_subjects_inferred": sig["n_unique_subjects_inferred"],
            "samples_per_subject_median_inferred": sig["samples_per_subject_median"],
            "sample_sources": " | ".join(sig["sample_sources"][:5]),
            "factor_names": " | ".join(sig["factor_names"][:8]),
            "source_of_raw_payload": source,
            "v9_llm_label": row.get("llm_primary_design_context", ""),
            "v9_llm_confidence": row.get("llm_confidence", ""),
            "v9_rule_label": row.get("rule_based_context_suggestion", ""),
            **verdict,
        }
        # Agreement
        ll = str(row.get("llm_primary_design_context", "")).strip()
        if ll == out["auditor_label"]:
            out["agreement_with_llm"] = "agree"
        elif ll == "unclear" or out["auditor_label"] == "unclear":
            out["agreement_with_llm"] = "one_unclear"
        else:
            out["agreement_with_llm"] = "disagree"
        results.append(out)
        if (i + 1) % 500 == 0:
            print(f"  processed {i+1}/{n_iter} ...", flush=True)

    df_out = pd.DataFrame(results)
    df_out.to_csv(OUT_TSV, sep='\t', index=False)
    summary = {
        "n_studies": len(df_out),
        "missing_local_payload": missing_local,
        "auditor_label_counts": df_out["auditor_label"].value_counts().to_dict(),
        "auditor_confidence_counts": df_out["auditor_confidence"].value_counts().to_dict(),
        "agreement_with_llm_counts": df_out["agreement_with_llm"].value_counts().to_dict(),
        "cross_tab_auditor_vs_llm": pd.crosstab(df_out["auditor_label"], df_out["v9_llm_label"]).to_dict(),
    }
    with OUT_JSON.open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nWrote: {OUT_TSV}", flush=True)
    print(f"\nSummary:\n{json.dumps(summary, indent=2)}", flush=True)


if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit=limit)
