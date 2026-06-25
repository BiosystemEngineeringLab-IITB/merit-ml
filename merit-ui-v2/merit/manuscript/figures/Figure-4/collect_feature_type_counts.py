"""
Collect per-analysis named-metabolite vs mz/RT feature counts
for all three sources (datatable, mwtab, untarg_data).
Output: /tmp/feature_type_counts.json
"""
import json, os, re, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, '/home/shayantan/metabolomics/ML-ready')
from merit.feature_names import classify_feature_name

DUMP = Path('/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version')
SRC  = Path('/home/shayantan/metabolomics/ML-ready/outputs/diagnostics/mw_6696_source_presence.tsv')
OUT  = Path('/tmp/feature_type_counts.json')

BLOCK_START = re.compile(
    r'(MS_METABOLITE_DATA_START|NMR_METABOLITE_DATA_START|'
    r'EXTENDED_MS_METABOLITE_DATA_START|EXTENDED_NMR_METABOLITE_DATA_START)')
BLOCK_END = re.compile(
    r'(MS_METABOLITE_DATA_END|NMR_METABOLITE_DATA_END|'
    r'EXTENDED_MS_METABOLITE_DATA_END|EXTENDED_NMR_METABOLITE_DATA_END)')


def classify_features(names):
    named = mzrt = other = 0
    for n in names:
        r = classify_feature_name(n)
        if r['is_named_metabolite']:
            named += 1
        elif r['is_mz_rt']:
            mzrt += 1
        else:
            other += 1
    return named, mzrt, other


def get_dt_features(path):
    with open(path) as f:
        header = f.readline().strip().split('\t')
    return [h.strip() for h in header[2:] if h.strip()]


def get_mw_features(path):
    feats = []
    in_block = False; rows_seen = 0
    with open(path, errors='replace') as f:
        for line in f:
            if BLOCK_START.search(line.strip()):
                in_block = True; rows_seen = 0; continue
            if BLOCK_END.search(line.strip()):
                in_block = False; continue
            if not in_block: continue
            rows_seen += 1
            cols = line.strip().split('\t')
            label = cols[0].strip()
            if rows_seen <= 2: continue
            if label:
                feats.append(label)
    return feats


src = pd.read_csv(SRC, sep='\t')
results = {'dt': [], 'mw': [], 'ut': []}
errors = 0

total = len(src)
for i, (_, row) in enumerate(src.iterrows()):
    if i % 500 == 0:
        print(f"  {i}/{total}...")
    st, an = row['study_id'], row['analysis_id']

    # datatable
    if row['datatable_valid_present']:
        dt_f = DUMP / st / an / 'tabular' / f'{an}_datatable.tsv'
        if dt_f.exists():
            try:
                feats = get_dt_features(dt_f)
                named, mzrt, other = classify_features(feats)
                results['dt'].append({'an': an, 'st': st,
                                      'named': named, 'mzrt': mzrt, 'other': other,
                                      'total': len(feats)})
            except Exception as e:
                errors += 1

    # mwtab
    if row['mwtab_valid_present']:
        mw_f = DUMP / st / an / 'json' / f'{an}_mwtab.txt'
        if mw_f.exists():
            try:
                feats = get_mw_features(mw_f)
                named, mzrt, other = classify_features(feats)
                results['mw'].append({'an': an, 'st': st,
                                      'named': named, 'mzrt': mzrt, 'other': other,
                                      'total': len(feats)})
            except Exception as e:
                errors += 1

    # untarg_data
    if row['untarg_valid_present']:
        ut_f = DUMP / st / an / 'tabular' / f'{an}_untarg_data.tsv'
        if ut_f.exists():
            try:
                feats = get_dt_features(ut_f)
                named, mzrt, other = classify_features(feats)
                results['ut'].append({'an': an, 'st': st,
                                      'named': named, 'mzrt': mzrt, 'other': other,
                                      'total': len(feats)})
            except Exception as e:
                errors += 1

with open(OUT, 'w') as f:
    json.dump(results, f)

print(f"\nDone. dt={len(results['dt'])}, mw={len(results['mw'])}, ut={len(results['ut'])}, errors={errors}")
print(f"Saved to {OUT}")
