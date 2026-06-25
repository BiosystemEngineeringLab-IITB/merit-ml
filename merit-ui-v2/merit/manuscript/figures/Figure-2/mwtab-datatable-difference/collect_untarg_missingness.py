#!/usr/bin/env python3
"""
Compute per-analysis missingness for untarg_data source.
Output: /tmp/untarg_missingness.tsv
"""
import csv
from pathlib import Path

DUMP = Path('/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version')
SRC  = Path('/home/shayantan/metabolomics/ML-ready/outputs/diagnostics/mw_6696_source_presence.tsv')

# Same missing token set as collect_metrics.py
_MISSING_TOKENS = {
    '', 'na', 'n/a', 'nan', 'null', 'none', 'nd', 'bdl', 'bql',
    'lod', '<lod', 'llod', '<llod', 'lloq', '<lloq', 'bloq',
    'nq', 'loq', 'missing', 'not detected',
}

def is_missing(val: str) -> bool:
    """Return True if val should be treated as missing.
    Zeros ARE missing for untarg (feature not detected in run)."""
    v = val.strip().lower()
    if v in _MISSING_TOKENS:
        return True
    if v.startswith('<'):
        return True
    try:
        f = float(v)
        if f < 0 or f == 0.0:
            return True
    except ValueError:
        pass
    return False

rows = list(csv.DictReader(open(SRC), delimiter='\t'))
target = [r for r in rows if r['untarg_valid_present'] == '1']
print(f"Untarg valid analyses: {len(target)}", flush=True)

results = []
errors  = 0

for idx, r in enumerate(target):
    an, st = r['analysis_id'], r['study_id']
    ut_f = DUMP / st / an / 'tabular' / f'{an}_untarg_data.tsv'

    if not ut_f.exists():
        errors += 1; continue

    try:
        with open(ut_f, errors='replace') as f:
            hdr = f.readline().rstrip('\r\n').split('\t')
            # Features start at col index 2 (after Samples, group)
            n_feats = len([h for h in hdr[2:] if h.strip()])

            n_samples = 0
            n_missing = 0
            n_total   = 0

            for line in f:
                parts = line.rstrip('\r\n').split('\t')
                if len(parts) < 3:
                    continue
                n_samples += 1
                vals = parts[2:2 + n_feats]
                for v in vals:
                    n_total += 1
                    if is_missing(v):
                        n_missing += 1

        miss_pct = round(n_missing / n_total * 100, 3) if n_total > 0 else None
        results.append({
            'an': an, 'st': st,
            'n_feats': n_feats,
            'n_samples': n_samples,
            'n_missing': n_missing,
            'n_total': n_total,
            'miss_pct': miss_pct,
        })

    except Exception as e:
        print(f"  ERROR {an}: {e}")
        errors += 1

    if (idx + 1) % 200 == 0:
        print(f"  {idx+1}/{len(target)} done", flush=True)

print(f"\nDone: {len(results)} analysed, {errors} errors", flush=True)

fields = ['an', 'st', 'n_feats', 'n_samples', 'n_missing', 'n_total', 'miss_pct']
with open('/tmp/untarg_missingness.tsv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields, delimiter='\t')
    w.writeheader()
    w.writerows(results)
print("Saved: /tmp/untarg_missingness.tsv", flush=True)

# Summary
from statistics import median
mp = [r['miss_pct'] for r in results if r['miss_pct'] is not None]
print(f"\nSummary ({len(mp)} analyses):")
print(f"  median miss%:  {median(mp):.1f}%")
print(f"  mean miss%:    {sum(mp)/len(mp):.1f}%")
print(f"  == 0%:         {sum(1 for x in mp if x == 0)}")
print(f"  > 0%:          {sum(1 for x in mp if x > 0)}")
print(f"  >= 50%:        {sum(1 for x in mp if x >= 50)}")
print(f"  >= 80%:        {sum(1 for x in mp if x >= 80)}")
print(f"  == 100%:       {sum(1 for x in mp if x == 100)}")
