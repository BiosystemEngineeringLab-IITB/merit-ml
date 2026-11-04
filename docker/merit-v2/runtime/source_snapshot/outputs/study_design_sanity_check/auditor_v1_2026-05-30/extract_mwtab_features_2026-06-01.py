"""
Per-study feature extractor that walks every analysis dir for every study and
harvests the rich mwtab fields that v9/v10 did NOT use:

  * STUDY.STUDY_SUMMARY              (long-form study description; distinct from PROJECT_SUMMARY)
  * SUBJECT.SUBJECT_TYPE             (categorical: Human / Animal / Mammal / Plant / Cultured cells / ...)
  * SUBJECT.SUBJECT_SPECIES          (canonical species string)
  * SUBJECT.GENOTYPE_STRAIN          (strong experimental marker if non-empty)
  * SUBJECT.ANIMAL_ANIMAL_SUPPLIER   (definitive in-vivo experimental marker)
  * SUBJECT.AGE_OR_AGE_RANGE         (cohort signal when broad range)
  * SUBJECT.GENDER                   (cohort signal when "M/F" or mixed)
  * SUBJECT_SAMPLE_FACTORS Subject IDs:
        - n_total_samples_mwtab
        - n_unique_subject_ids       (explicit count, not inferred!)
        - subject_id_populated       (bool: any non-"-" Subject ID present)
        - samples_per_subject_mean
  * TREATMENT.TREATMENT_SUMMARY      (treatment narrative)
  * COLLECTION.COLLECTION_SUMMARY    (collection narrative)
  * SAMPLEPREP.SAMPLEPREP_SUMMARY    (sample prep narrative)

If multiple analyses exist per study, we take the first non-empty value
for each text field and aggregate Subject IDs across all analyses.

Output: study_mwtab_features_2026-06-01.tsv (4,121 rows)
"""
from __future__ import annotations
import json
import os
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

DUMP_ROOT = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/mw-dump-latest-confirmation-latest-version')
OUT_TSV   = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/outputs/study_design_sanity_check/'
                 'auditor_v1_2026-05-30/study_mwtab_features_2026-06-01.tsv')
V10_TSV   = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/outputs/study_design_sanity_check/'
                 'mw_study_design_type_review_sheet_v10_resolved_2026-06-01.tsv')


def safe_str(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s in ("-", "—", "n/a", "N/A", "none", "None", "nan"):
        return ""
    return s


def harvest_one(study_dir: Path) -> dict:
    sid = study_dir.name
    out: dict = {
        "study_id": sid,
        "study_summary_mwtab": "",
        "subject_type": "",
        "subject_species_mwtab": "",
        "genotype_strain": "",
        "animal_supplier": "",
        "age_or_age_range": "",
        "gender": "",
        "treatment_summary": "",
        "collection_summary": "",
        "sampleprep_summary": "",
        "n_total_samples_mwtab": 0,
        "n_unique_subject_ids": 0,
        "subject_id_populated": False,
        "samples_per_subject_mean": 0.0,
        "n_analyses_mwtab": 0,
        "mwtab_files_found": 0,
    }

    # Walk all AN dirs
    subject_id_counter: Counter = Counter()
    seen_sample_ids: set = set()

    an_dirs = [d for d in study_dir.iterdir() if d.is_dir() and d.name.startswith('AN')]
    out["n_analyses_mwtab"] = len(an_dirs)

    for an in an_dirs:
        jdir = an / "json"
        if not jdir.exists():
            continue
        mwtab_files = list(jdir.glob('*_mwtab.json'))
        if not mwtab_files:
            continue
        try:
            d = json.load(mwtab_files[0].open())
        except Exception:
            continue
        out["mwtab_files_found"] += 1

        st = d.get("STUDY", {}) or {}
        sub = d.get("SUBJECT", {}) or {}
        ssf = d.get("SUBJECT_SAMPLE_FACTORS", []) or []
        tr = d.get("TREATMENT", {}) or {}
        co = d.get("COLLECTION", {}) or {}
        sp = d.get("SAMPLEPREP", {}) or {}

        # Take first non-empty value per text field
        for field, target_key in [
            (st.get("STUDY_SUMMARY"),  "study_summary_mwtab"),
            (sub.get("SUBJECT_TYPE"),  "subject_type"),
            (sub.get("SUBJECT_SPECIES"), "subject_species_mwtab"),
            (sub.get("GENOTYPE_STRAIN"), "genotype_strain"),
            (sub.get("ANIMAL_ANIMAL_SUPPLIER"), "animal_supplier"),
            (sub.get("AGE_OR_AGE_RANGE"), "age_or_age_range"),
            (sub.get("GENDER"), "gender"),
            (tr.get("TREATMENT_SUMMARY"), "treatment_summary"),
            (co.get("COLLECTION_SUMMARY"), "collection_summary"),
            (sp.get("SAMPLEPREP_SUMMARY"), "sampleprep_summary"),
        ]:
            cleaned = safe_str(field)
            if cleaned and not out[target_key]:
                out[target_key] = cleaned

        # Aggregate SUBJECT_SAMPLE_FACTORS across analyses
        if isinstance(ssf, list):
            for record in ssf:
                if not isinstance(record, dict):
                    continue
                sid_field = safe_str(record.get("Subject ID"))
                sample_id = safe_str(record.get("Sample ID"))
                if sample_id and sample_id not in seen_sample_ids:
                    seen_sample_ids.add(sample_id)
                    if sid_field:
                        subject_id_counter[sid_field] += 1

    out["n_total_samples_mwtab"] = len(seen_sample_ids)
    out["n_unique_subject_ids"] = len(subject_id_counter)
    out["subject_id_populated"] = bool(subject_id_counter)
    if subject_id_counter:
        out["samples_per_subject_mean"] = sum(subject_id_counter.values()) / len(subject_id_counter)

    return out


def main(limit: int | None = None):
    # Pull list of study IDs from v10 to keep ordering aligned
    v10 = pd.read_csv(V10_TSV, sep='\t', low_memory=False, keep_default_na=False)
    study_ids = v10['study_id'].astype(str).tolist()
    print(f"Will process {len(study_ids)} studies", flush=True)

    rows = []
    for i, sid in enumerate(study_ids):
        if limit is not None and i >= limit:
            break
        study_dir = DUMP_ROOT / sid
        if not study_dir.exists():
            rows.append({
                "study_id": sid,
                "study_summary_mwtab": "", "subject_type": "",
                "subject_species_mwtab": "", "genotype_strain": "",
                "animal_supplier": "", "age_or_age_range": "", "gender": "",
                "treatment_summary": "", "collection_summary": "",
                "sampleprep_summary": "",
                "n_total_samples_mwtab": 0, "n_unique_subject_ids": 0,
                "subject_id_populated": False, "samples_per_subject_mean": 0.0,
                "n_analyses_mwtab": 0, "mwtab_files_found": 0,
            })
            continue
        rows.append(harvest_one(study_dir))
        if (i + 1) % 500 == 0:
            print(f"  processed {i+1}/{len(study_ids)} ...", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(OUT_TSV, sep='\t', index=False)
    print(f"\nWrote: {OUT_TSV}", flush=True)
    print(f"\n=== Coverage stats ===")
    for col in ["study_summary_mwtab", "subject_type", "genotype_strain",
                "animal_supplier", "treatment_summary", "collection_summary",
                "sampleprep_summary"]:
        n = (df[col].astype(str).str.len() > 0).sum()
        print(f"  {col}: {n} ({100*n/len(df):.1f}%) populated")
    print(f"  subject_id_populated: {df['subject_id_populated'].sum()} ({100*df['subject_id_populated'].sum()/len(df):.1f}%)")
    print(f"\nsubject_type distribution:")
    print(df['subject_type'].value_counts().head(20))


if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit=limit)
