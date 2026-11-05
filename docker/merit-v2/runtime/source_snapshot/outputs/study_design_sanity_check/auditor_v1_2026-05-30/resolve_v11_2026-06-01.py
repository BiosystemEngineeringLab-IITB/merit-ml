"""
v11 tie-breaker resolver. Inputs:
  - v9 LLM labels (mw_study_design_type_review_sheet_v9_2026-05-27.xlsx)
  - v11 auditor verdicts (programmatic_design_audit_v11_2026-06-01.tsv)
  - mwtab features (study_mwtab_features_2026-06-01.tsv)

For each row not pre-resolved (LLM == auditor), apply an expanded cascade that
uses every available evidence source:

STEP A — DECISIVE LABEL SIGNALS (high-precision; force the call)
  A1  Cell-culture / in-vitro sample source             → experimental
  A2  Multi-value Time/Hours/Days factor (>=3)          → experimental
  A3  Plant or microbe sample source                    → experimental
  A4  Multi-value treatment + model-animal species      → experimental
  A5  Genotype factor + model system                    → experimental
  A6  Stable-isotope tracer language                    → experimental
  A7  >=20 human subjects, ~1 sample/subj, clinical     → cohort  (uses real OR inferred subj structure)
  A8  Cohort / case-control language + clinical tissue  → cohort
  A9  Newborn / pregnancy / twin / family               → cohort
  A10 Diagnosis factor >=2 states + clinical            → cohort
  --- NEW IN v11 ---
  A11 SUBJECT_TYPE = Cultured cells / Cells / Bacteria /
        Yeast / Bacterial cells                          → experimental
  A12 SUBJECT_TYPE = Plant                              → experimental
  A13 SUBJECT.GENOTYPE_STRAIN populated                 → experimental
  A14 SUBJECT.ANIMAL_ANIMAL_SUPPLIER populated          → experimental
  A15 SUBJECT_TYPE = Human + AGE wide-range (X-Y digits)
        + clinical tissue                                → cohort
  A16 SUBJECT_TYPE = Mammal/Animal/Mouse/Rat/Insect/Fish
        + treatment factor in factor_variables           → experimental
  A17 Real subject_id structure: subj_id_populated +
        n_unique_subj_real >= 20 + spp_mean <= 1.5      → cohort
  A18 Real subject_id structure: subj_id_populated +
        n_unique_subj_real < 5 + spp_mean >= 3          → experimental

STEP B — confidence cascade (same as v10)
STEP C — definitional metadata_only case (same as v10)
STEP D — one_unclear asymmetry (same as v10)
STEP E — fall through → unclear
"""
from __future__ import annotations
import re
import pandas as pd
from pathlib import Path

BASE = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/outputs/study_design_sanity_check')
V9_XLSX  = BASE / 'mw_study_design_type_review_sheet_v9_2026-05-27.xlsx'
V11_TSV  = BASE / 'auditor_v1_2026-05-30' / 'programmatic_design_audit_v11_2026-06-01.tsv'
MWTAB    = BASE / 'auditor_v1_2026-05-30' / 'study_mwtab_features_2026-06-01.tsv'
OUT_TSV  = BASE / 'mw_study_design_type_review_sheet_v11_resolved_2026-06-01.tsv'
OUT_XLSX = BASE / 'mw_study_design_type_review_sheet_v11_resolved_2026-06-01.xlsx'

LABEL_EXP  = 'experimental_or_replicate_based'
LABEL_COH  = 'cohort_or_subject_based'
LABEL_META = 'metadata_only_or_no_usable_matrix'
LABEL_UNC  = 'unclear'

CELL_CULTURE_RE = re.compile(r"\b(cell|cells|cell\s+line|macrophage|hepatocyt|astrocyt|fibroblast|culture|cultured|organoid|spheroid|hek|hela|jurkat|raw\s*264|3t3|caco|huvec|primary\s+cells?)\b", re.I)
PLANT_MICROBE_RE = re.compile(r"\b(plant|leaf|leaves|root|stem|seed|seedling|bacteria|bacterium|microbe|microbial|yeast|fungus|fungal|algae|coral|biofilm)\b", re.I)
CLINICAL_TISSUE_RE = re.compile(r"\b(blood|plasma|serum|urine|saliva|csf|cerebrospinal|stool|feces|fecal|breath|sweat|biopsy|tumor|tumour|adipose|breast\s+tissue|liver\s+biopsy|placenta)\b", re.I)
HUMAN_RE = re.compile(r"homo\s+sapiens|human", re.I)
MODEL_ANIMAL_RE = re.compile(r"mus\s+musculus|mouse|mice|rat\b|rats\b|rattus|drosophila|zebrafish|danio|c\.?\s*elegans|caenorhabditis|guinea\s+pig|pig\b|porcine|rabbit|sheep|bovine|canine|monkey|macaque", re.I)
TRACER_RE = re.compile(r"\b(13C|U-13C|15N|2H[\s\-]?label|stable[\s\-]?isotope|tracer|isotopolog|flux|fluxomic)\b", re.I)
TIMECOURSE_FACTOR_RE = re.compile(r"\b(time|timepoint|hour|hours|hr|hrs|day|days|min|minute|week|weeks|month|months)\b", re.I)
TREATMENT_FACTOR_RE = re.compile(r"\b(treatment|compound|drug|dose|concentration|conc|stimul|exposure|perturbation|diet|condition|inhibitor)\b", re.I)
GENOTYPE_FACTOR_RE = re.compile(r"\b(genotype|strain|mutation|knockout|knockdown|wild[\s\-]?type|transgenic|crispr|sirna)\b", re.I)
DIAGNOSIS_FACTOR_RE = re.compile(r"\b(diagnos|case|control|disease[\s\-]?status|healthy|patient[\s\-]?status|clinical[\s\-]?status|prognosis|stage|grade)\b", re.I)
DEMOGRAPHIC_FACTOR_RE = re.compile(r"\b(age|sex|gender|ethnicity|race|bmi|smoking)\b", re.I)
COHORT_LANG_RE = re.compile(r"\b(cohort|case[\s\-]?control|case[\s\-]?study|patients\s+with|healthy\s+controls?|healthy\s+volunteers?|epidemiolog|observational|cross[\s\-]?sectional|biobank|registry|nested\s+case|matched\s+pair|clinical\s+samples?|patient\s+samples?)\b", re.I)
LIFESTAGE_RE = re.compile(r"\b(newborn|neonate|neonatal|infants?|pregnan|pregnancy|twins?\s+study|family\s+study|adolescents?)\b", re.I)
AGE_RANGE_RE = re.compile(r"\d{1,3}\s*[-–]\s*\d{1,3}")

SUBJ_TYPE_CELLS_MICROBE = {"cultured cells","cells","cell culture","cell line","cell",
                            "bacteria","bacterial cells","yeast","fungus","fungi","fungal",
                            "algae","archaea","microbe","microbial","virus","bacteriophage"}
SUBJ_TYPE_PLANT = {"plant","plants","moss","arabidopsis"}
SUBJ_TYPE_ANIMAL = {"animal","mammal","mouse","mice","rat","rats","rodent","insect","fish",
                    "invertebrate","vertebrate","bird","reptile","amphibian","primate",
                    "monkey","pig","porcine","bovine","canine","rabbit","sheep","guinea pig"}
SUBJ_TYPE_HUMAN = {"human","humans","homo sapiens"}


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


def count_signal_types(ev: str) -> int:
    if not isinstance(ev, str): return 0
    tags = ('title_exp_kw','title_cohort_kw','title_invivo_kw',
            'description_exp_kw','description_cohort_kw','description_invivo_kw',
            'study_summary_exp_kw','study_summary_cohort_kw','study_summary_invivo_kw',
            'protocol_exp_kw','protocol_cohort_kw','protocol_invivo_kw',
            'factor_exp_kw','factor_cohort_kw','multi_timepoints',
            'dose_or_treatment_factor','diagnostic_factor','demographic_factor',
            'sample_source_cell_culture','sample_source_plant_or_microbe',
            'clinical_tissue_human','inferred_many_subjects',
            'inferred_replicate_struct','real_many_subjects','real_replicate_struct',
            'model_animal_species_prior','subject_type','genotype_strain',
            'animal_supplier','human_age_range','human_mixed_gender')
    return sum(1 for t in tags if t in ev)


def decisive_signals(row) -> dict:
    title = safe_str(row.get('title'))
    desc  = safe_str(row.get('description'))
    organism = safe_str(row.get('organism')).lower()
    sources_top = safe_str(row.get('sample_sources_top')).lower()
    tissues = safe_str(row.get('tissues_or_sample_matrices')).lower()
    sources_blob = sources_top + " " + tissues
    factor_vars = safe_str(row.get('factor_variables')).lower()

    # NEW mwtab fields
    study_summary = safe_str(row.get('study_summary_mwtab'))
    subject_type  = safe_str(row.get('subject_type')).lower().strip()
    genotype      = safe_str(row.get('genotype_strain'))
    supplier      = safe_str(row.get('animal_supplier'))
    age_range     = safe_str(row.get('age_or_age_range'))
    gender        = safe_str(row.get('gender'))
    treatment_summ  = safe_str(row.get('treatment_summary'))
    collection_summ = safe_str(row.get('collection_summary'))
    sampleprep_summ = safe_str(row.get('sampleprep_summary'))
    n_subj_real = safe_int(row.get('n_unique_subject_ids'))
    spp_real    = safe_float(row.get('samples_per_subject_mean'))
    subj_pop    = str(row.get('subject_id_populated','')).lower() == 'true'

    # Combined text for any keyword scan
    text_blob = "\n".join([title, desc, study_summary, treatment_summ,
                            collection_summ, sampleprep_summ]).strip()

    n_subj_inf = safe_int(row.get('v10_n_unique_subjects_inferred'))
    if n_subj_inf == 0:
        n_subj_inf = safe_int(row.get('n_unique_subjects_inferred'))
    n_combos = safe_int(row.get('v10_n_factor_combinations'))
    if n_combos == 0:
        n_combos = safe_int(row.get('n_distinct_factor_combinations'))
    spp_med = safe_float(row.get('v10_samples_per_subject_median'))
    if spp_med == 0.0:
        spp_med = safe_float(row.get('samples_per_subject_median_inferred'))

    is_human = bool(HUMAN_RE.search(organism))
    is_model_animal = bool(MODEL_ANIMAL_RE.search(organism))
    is_cell_culture = bool(CELL_CULTURE_RE.search(sources_blob))
    is_plant_microbe = bool(PLANT_MICROBE_RE.search(sources_blob))
    is_clinical = bool(CLINICAL_TISSUE_RE.search(sources_blob))

    is_subj_cells_microbe = subject_type in SUBJ_TYPE_CELLS_MICROBE
    is_subj_plant = subject_type in SUBJ_TYPE_PLANT
    is_subj_animal = subject_type in SUBJ_TYPE_ANIMAL
    is_subj_human = subject_type in SUBJ_TYPE_HUMAN

    age_is_range = bool(AGE_RANGE_RE.search(age_range))

    has_time_factor = bool(TIMECOURSE_FACTOR_RE.search(factor_vars))
    has_treatment_factor = bool(TREATMENT_FACTOR_RE.search(factor_vars))
    has_genotype_factor = bool(GENOTYPE_FACTOR_RE.search(factor_vars))
    has_diagnosis_factor = bool(DIAGNOSIS_FACTOR_RE.search(factor_vars))
    has_demographic_factor = bool(DEMOGRAPHIC_FACTOR_RE.search(factor_vars))

    class_counts_str = safe_str(row.get('class_counts'))
    n_distinct_classes = max(0, class_counts_str.count('|') + (1 if class_counts_str else 0))

    multi_time = has_time_factor and (n_combos >= 3 or n_distinct_classes >= 3)
    multi_treatment = has_treatment_factor and (n_combos >= 2 or n_distinct_classes >= 2)
    multi_diagnosis = has_diagnosis_factor and (n_combos >= 2 or n_distinct_classes >= 2)

    tracer_hit = bool(TRACER_RE.search(text_blob))
    cohort_lang = bool(COHORT_LANG_RE.search(text_blob))
    lifestage = bool(LIFESTAGE_RE.search(text_blob))

    # Combined subject structure (use real if available, else inferred)
    if subj_pop and n_subj_real > 0:
        many_subjects_struct = (n_subj_real >= 20 and spp_real <= 1.5
                                and (is_human or is_subj_human))
        replicate_struct = (n_subj_real < 5 and spp_real >= 3.0)
    else:
        many_subjects_struct = (n_subj_inf >= 20 and spp_med <= 1.5
                                and (is_human or is_subj_human))
        replicate_struct = (n_subj_inf > 0 and n_subj_inf < 5 and spp_med >= 3.0)

    return locals()


def resolve(row) -> dict:
    """Apply tie-breaker cascade."""
    consensus = safe_str(row.get('v11_consensus_label'))
    if consensus:
        return {
            'v11_final_label': consensus,
            'v11_final_confidence': row.get('v11_auditor_confidence', ''),
            'v11_final_reason': 'both labelers agreed',
            'v11_final_resolver': 'consensus_v9_v11',
        }

    llm = safe_str(row.get('llm_primary_design_context'))
    llm_conf = safe_str(row.get('llm_confidence'))
    aud = safe_str(row.get('v11_auditor_label'))
    aud_conf = safe_str(row.get('v11_auditor_confidence'))
    n_signals = count_signal_types(safe_str(row.get('v11_auditor_evidence')))
    s = decisive_signals(row)

    # ── STEP A — DECISIVE ──
    if s['is_cell_culture']:
        return _r(LABEL_EXP, 'high', 'cell-culture sample source', 'A1_cell_culture')
    if s['multi_time']:
        return _r(LABEL_EXP, 'high',
                  f"time-course factor with {s['n_distinct_classes']} levels", 'A2_multi_time')
    if s['is_plant_microbe']:
        return _r(LABEL_EXP, 'high', 'plant / microbe sample source', 'A3_plant_microbe')
    if s['tracer_hit']:
        return _r(LABEL_EXP, 'high', 'stable-isotope tracer language', 'A6_tracer')
    if s['multi_treatment'] and s['is_model_animal']:
        return _r(LABEL_EXP, 'high',
                  'multi-value treatment in model animal', 'A4_multi_treatment_animal')
    if s['has_genotype_factor'] and (s['is_model_animal'] or s['is_cell_culture']):
        return _r(LABEL_EXP, 'high',
                  'genotype factor + model system', 'A5_genotype_model_system')

    # NEW A11–A16
    if s['is_subj_cells_microbe']:
        return _r(LABEL_EXP, 'high',
                  f"SUBJECT_TYPE = {s['subject_type']}", 'A11_subject_type_cells_microbe')
    if s['is_subj_plant']:
        return _r(LABEL_EXP, 'high',
                  f"SUBJECT_TYPE = {s['subject_type']}", 'A12_subject_type_plant')
    if s['genotype']:
        return _r(LABEL_EXP, 'high',
                  f"GENOTYPE_STRAIN populated ({s['genotype'][:30]})", 'A13_genotype_strain')
    if s['supplier']:
        return _r(LABEL_EXP, 'high',
                  f"ANIMAL_SUPPLIER populated ({s['supplier'][:30]})", 'A14_animal_supplier')
    if (s['is_subj_animal'] and s['has_treatment_factor']
        and (s['multi_treatment'] or s['n_combos'] >= 2)):
        return _r(LABEL_EXP, 'high',
                  f"SUBJECT_TYPE animal + treatment factor", 'A16_animal_subject_treatment')

    # Cohort decisives
    if s['cohort_lang'] and s['is_clinical'] and (s['is_human'] or s['is_subj_human']):
        return _r(LABEL_COH, 'high',
                  'cohort/case-control language + clinical human tissue',
                  'A8_cohort_lang_clinical')
    if s['lifestage'] and (s['is_human'] or s['is_subj_human']):
        return _r(LABEL_COH, 'high',
                  'newborn / pregnancy / twin / family-study language', 'A9_lifestage')
    if (s['many_subjects_struct'] and s['is_clinical']
        and (s['has_diagnosis_factor'] or s['has_demographic_factor'])):
        n_used = s['n_subj_real'] if s['subj_pop'] else s['n_subj_inf']
        return _r(LABEL_COH, 'high',
                  f"≥{n_used} human subjects, ~1 sample/subj, clinical tissue",
                  'A7_many_subjects_clinical')
    if s['multi_diagnosis'] and s['is_clinical'] and (s['is_human'] or s['is_subj_human']):
        return _r(LABEL_COH, 'high',
                  f"diagnosis factor with {s['n_distinct_classes']} states + clinical",
                  'A10_multi_diagnosis_clinical')
    if s['age_is_range'] and s['is_subj_human'] and s['is_clinical']:
        return _r(LABEL_COH, 'high',
                  f"SUBJECT human + wide AGE range ({s['age_range']}) + clinical",
                  'A15_human_age_range_clinical')

    # NEW A17 / A18 — real subject_id structure decisive
    if s['subj_pop'] and s['n_subj_real'] >= 20 and s['spp_real'] <= 1.5 and s['is_subj_human']:
        return _r(LABEL_COH, 'high',
                  f"real {s['n_subj_real']} unique subjects, ratio {s['spp_real']:.1f}",
                  'A17_real_subjects_cohort')
    if s['subj_pop'] and s['n_subj_real'] > 0 and s['n_subj_real'] < 5 and s['spp_real'] >= 3.0:
        return _r(LABEL_EXP, 'high',
                  f"real {s['n_subj_real']} subjects with {s['spp_real']:.1f} samples each",
                  'A18_real_subjects_replicate')

    # ── STEP C — definitional metadata_only ──
    if llm == LABEL_META and aud in (LABEL_EXP, LABEL_COH):
        if s['n_combos'] >= 2 or n_signals >= 2:
            return _r(aud, 'moderate',
                      'LLM marked metadata_only; auditor found real design',
                      'C_definitional_metadata_to_design')
        return _r(LABEL_META, 'moderate',
                  'no usable design structure',
                  'C_definitional_keep_metadata')

    # ── STEP B — confidence ──
    if aud_conf == 'high' and llm_conf == 'low' and aud != LABEL_UNC:
        return _r(aud, 'moderate',
                  'auditor high vs LLM low', 'B11_aud_high_over_llm_low')
    if llm_conf == 'high' and aud_conf == 'low' and llm != LABEL_UNC:
        return _r(llm, 'moderate',
                  'LLM high vs auditor low', 'B11_llm_high_over_aud_low')
    if aud_conf == 'high' and llm_conf == 'high' and aud != llm and aud != LABEL_UNC and llm != LABEL_UNC:
        return _r(LABEL_UNC, 'low',
                  'both high-conf and disagree', 'B12_collision_high_high')
    if aud not in ('', LABEL_UNC) and n_signals >= 3:
        return _r(aud, 'moderate',
                  f"auditor backed by {n_signals} distinct signal types",
                  'B13_aud_multi_signal')

    # ── STEP D — one_unclear asymmetry ──
    if row.get('v11_agreement_with_llm') == 'one_unclear':
        if llm == LABEL_UNC and aud not in ('', LABEL_UNC):
            if aud_conf in ('high','moderate') or n_signals >= 2:
                return _r(aud, aud_conf or 'low',
                          'LLM unclear; auditor supported', 'D_one_unclear_take_auditor')
        if aud in ('', LABEL_UNC) and llm not in ('', LABEL_UNC):
            if llm_conf in ('high','moderate'):
                return _r(llm, llm_conf,
                          'auditor unclear; LLM confident', 'D_one_unclear_take_llm')

    if llm_conf == 'high' and llm not in ('', LABEL_UNC) and n_signals <= 1:
        return _r(llm, 'moderate',
                  'LLM high-conf; auditor weak', 'B14_llm_high_aud_weak')

    return _r(LABEL_UNC, 'low',
              f"no decisive signal; LLM={llm}({llm_conf}); aud={aud}({aud_conf}, {n_signals} sigs)",
              'E_fall_through')


def _r(label, conf, reason, resolver):
    return {
        'v11_final_label': label,
        'v11_final_confidence': conf,
        'v11_final_reason': reason,
        'v11_final_resolver': resolver,
    }


def main():
    v9 = pd.read_excel(V9_XLSX)
    v11 = pd.read_csv(V11_TSV, sep='\t', low_memory=False, keep_default_na=False)
    mw = pd.read_csv(MWTAB, sep='\t', low_memory=False, keep_default_na=False)

    # Merge v9 (preserve all cols) with v11 auditor outputs + mwtab features
    v11_keep = ['study_id','auditor_label','auditor_confidence','auditor_reason',
                'auditor_evidence','agreement_with_llm']
    v11_subset = v11[v11_keep].rename(columns={
        'auditor_label':       'v11_auditor_label',
        'auditor_confidence':  'v11_auditor_confidence',
        'auditor_reason':      'v11_auditor_reason',
        'auditor_evidence':    'v11_auditor_evidence',
        'agreement_with_llm':  'v11_agreement_with_llm',
    })
    df = v9.merge(v11_subset, on='study_id', how='left')
    df = df.merge(mw, on='study_id', how='left')

    # Consensus label
    df['v11_consensus_label'] = df.apply(
        lambda r: r['v11_auditor_label']
        if r['v11_agreement_with_llm'] == 'agree' else '', axis=1)

    print(f"Loaded {len(df)} rows; pre-resolved (consensus): {(df['v11_consensus_label']!='').sum()}")

    # Apply resolver
    results = df.apply(resolve, axis=1, result_type='expand')
    for col in results.columns:
        df[col] = results[col]

    # Stats
    print(f"\nFinal label distribution:\n{df['v11_final_label'].value_counts()}")
    print(f"\nFinal confidence:\n{df['v11_final_confidence'].value_counts()}")
    print(f"\nResolver tag distribution:\n{df['v11_final_resolver'].value_counts()}")
    resolved = (df['v11_final_label'] != LABEL_UNC).sum()
    print(f"\nResolved: {resolved} ({100*resolved/len(df):.1f}%) | Unclear: {(df['v11_final_label']==LABEL_UNC).sum()}")

    df.to_csv(OUT_TSV, sep='\t', index=False)
    print(f"\nWrote: {OUT_TSV}")


if __name__ == '__main__':
    main()
