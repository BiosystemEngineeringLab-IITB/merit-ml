#!/usr/bin/env python3
"""
Collect median intensity value per analysis for data scale plot.
Samples up to MAX_AN analyses per source to keep runtime reasonable.
Output: /tmp/intensity_scale.json
"""
import csv, json, random
from pathlib import Path

DUMP    = Path('/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version')
SRC     = Path('/home/shayantan/metabolomics/ML-ready/outputs/diagnostics/mw_6696_source_presence.tsv')

_MISSING_TOKENS = {
    '', 'na', 'n/a', 'nan', 'null', 'none', 'nd', 'bdl', 'bql',
    'lod', '<lod', 'llod', '<llod', 'lloq', '<lloq', 'bloq',
    'nq', 'loq', 'missing', 'not detected',
}

def is_missing(val: str) -> bool:
    v = val.strip().lower()
    if v in _MISSING_TOKENS: return True
    if v.startswith('<'): return True
    return False

def median_of(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0: return None
    return (s[n//2] if n % 2 else (s[n//2 - 1] + s[n//2]) / 2)

def sample_median_intensity(path, feat_start_col, cap_cols=None):
    """Read file, collect all non-missing numeric values, return median."""
    values = []
    try:
        with open(path, errors='replace') as f:
            f.readline()  # skip header
            for line in f:
                parts = line.rstrip('\r\n').split('\t')
                cols = parts[feat_start_col:]
                if cap_cols:
                    cols = cols[:cap_cols]
                for v in cols:
                    if is_missing(v): continue
                    try:
                        f_val = float(v.strip())
                        if f_val > 0:
                            values.append(f_val)
                    except ValueError:
                        pass
    except Exception:
        return None
    return median_of(values)

results = {'dt': [], 'mw': [], 'ut': []}

# ── Datatable & mwTab (all combo-110 analyses) ────────────────────────────────
import pandas as pd
mw_df = pd.read_csv('/tmp/dt_mw_metrics.tsv', sep='\t')

print(f"Collecting datatable intensities ({len(mw_df)} analyses)...", flush=True)
for i, (_, row) in enumerate(mw_df.iterrows()):
    an, st = row['an'], row['st']
    dt_f = DUMP / st / an / 'tabular' / f'{an}_datatable.tsv'
    if not dt_f.exists(): continue
    med = sample_median_intensity(dt_f, feat_start_col=2)
    if med and med > 0:
        results['dt'].append(med)
    if (i+1) % 500 == 0: print(f"  dt {i+1}/{len(mw_df)}", flush=True)

print(f"Collecting mwTab intensities ({len(mw_df)} analyses)...", flush=True)

import re
BLOCK_START = re.compile(r'^(\w+)_METABOLITE_DATA_START')
BLOCK_END   = re.compile(r'^\w+_METABOLITE_DATA_END')

for i, (_, row) in enumerate(dt_sample.iterrows()):
    an, st = row['an'], row['st']
    mw_f = DUMP / st / an / 'json' / f'{an}_mwtab.txt'
    if not mw_f.exists(): continue
    mw_n_samples = int(row['n_mw_samples'])
    values = []
    try:
        with open(mw_f, errors='replace') as f:
            in_block = False; rows_seen = 0
            for line in f:
                ls = line.strip()
                if BLOCK_START.match(ls):
                    in_block = True; rows_seen = 0; continue
                if BLOCK_END.match(ls):
                    in_block = False; continue
                if not in_block: continue
                rows_seen += 1
                cols = line.rstrip('\r\n').split('\t')
                if rows_seen == 1: continue  # sample names row
                if rows_seen == 2 and cols[0].strip() == 'Factors': continue
                if cols[0].strip():
                    for v in cols[1:mw_n_samples+1]:
                        if is_missing(v): continue
                        try:
                            f_val = float(v.strip())
                            if f_val > 0: values.append(f_val)
                        except ValueError: pass
    except Exception: pass
    med = median_of(values)
    if med and med > 0:
        results['mw'].append(med)
    if (i+1) % 100 == 0: print(f"  mw {i+1}/{len(dt_sample)}", flush=True)

# ── Untarg ────────────────────────────────────────────────────────────────────
ut_df = pd.read_csv('/tmp/untarg_missingness.tsv', sep='\t')
ut_sample = ut_df.sample(min(MAX_AN, len(ut_df)), random_state=RANDOM_SEED)

print(f"Collecting untarg intensities ({len(ut_sample)} analyses)...", flush=True)
for i, (_, row) in enumerate(ut_sample.iterrows()):
    an, st = row['an'], row['st']
    ut_f = DUMP / st / an / 'tabular' / f'{an}_untarg_data.tsv'
    if not ut_f.exists(): continue
    n_feats = int(row['n_feats'])
    med = sample_median_intensity(ut_f, feat_start_col=2, cap_cols=n_feats)
    if med and med > 0:
        results['ut'].append(med)
    if (i+1) % 100 == 0: print(f"  ut {i+1}/{len(ut_sample)}", flush=True)

print(f"\nDone: dt={len(results['dt'])}, mw={len(results['mw'])}, ut={len(results['ut'])}", flush=True)

with open('/tmp/intensity_scale.json', 'w') as f:
    json.dump(results, f)
print("Saved: /tmp/intensity_scale.json", flush=True)

import math
for src, vals in results.items():
    if vals:
        log_vals = [math.log10(v) for v in vals]
        med_log = median_of(log_vals)
        print(f"  {src}: n={len(vals)}, log10 median={med_log:.2f} (raw median={10**med_log:.1e})")
