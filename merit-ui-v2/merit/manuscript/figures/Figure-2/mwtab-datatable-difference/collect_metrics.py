#!/usr/bin/env python3
"""
Collect per-analysis metrics for datatable vs mwtab comparison.
Covers all 5 investigation axes:
  1. Feature count discrepancy
  2. Feature name Jaccard similarity
  3. Adduct inflation in mwtab
  4. Missingness structure
  5. NMR-exclusive content
Output: /tmp/dt_mw_metrics.tsv
"""
import csv, re, json
from pathlib import Path
from statistics import median

DUMP = Path('/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version')
SRC  = Path('/home/shayantan/metabolomics/ML-ready/outputs/diagnostics/mw_6696_source_presence.tsv')

BLOCK_START  = re.compile(r'^(\w+)_METABOLITE_DATA_START')
BLOCK_END    = re.compile(r'^\w+_METABOLITE_DATA_END')
ADDUCT_PAT   = re.compile(r'[+-]\d+\.\d+$')
NMR_BLOCKS   = {'NMR_METABOLITE_DATA_START', 'NMR_BINNED_DATA_START',
                'EXTENDED_NMR_METABOLITE_DATA_START'}

# Universal missing tokens (case-insensitive)
_MISSING_TOKENS = {
    '', 'na', 'n/a', 'nan', 'null', 'none', 'nd', 'bdl', 'bql',
    'lod', '<lod', 'llod', '<llod', 'lloq', '<lloq', 'bloq',
    'nq', 'loq', 'missing', 'not detected',
}

def is_missing(val: str, count_zero: bool) -> bool:
    """Return True if val should be treated as a missing observation."""
    v = val.strip().lower()
    if v in _MISSING_TOKENS:
        return True
    if v.startswith('<'):          # <0.01, <LOD variants not already listed
        return True
    try:
        f = float(v)
        if f < 0:                  # negative sentinel values
            return True
        if f == 0.0 and count_zero:
            return True
    except ValueError:
        pass
    return False

rows = list(csv.DictReader(open(SRC), delimiter='\t'))

# combo 110 only: mwtab + datatable valid, no untarg
target = [r for r in rows
          if r['mwtab_valid_present'] == '1'
          and r['datatable_valid_present'] == '1'
          and r['untarg_valid_present'] == '0']

print(f"Target analyses (combo 110): {len(target)}", flush=True)

results = []
errors  = 0

for idx, r in enumerate(target):
    an, st = r['analysis_id'], r['study_id']
    dt_f = DUMP / st / an / 'tabular' / f'{an}_datatable.tsv'
    mw_f = DUMP / st / an / 'json'    / f'{an}_mwtab.txt'

    if not dt_f.exists() or not mw_f.exists():
        errors += 1; continue

    rec = {'an': an, 'st': st}

    # ── 1 & 2: datatable features (header, col 2+) ──────────────────────────
    try:
        with open(dt_f, errors='replace') as f:
            hdr = f.readline().rstrip('\r\n').split('\t')
            dt_feats = set(h.strip() for h in hdr[2:] if h.strip())

            # Count non-header rows (samples) + missingness
            # datatable: zeros are imputed fill values — do NOT count as missing
            dt_sample_rows = 0; dt_missing = 0; dt_total_vals = 0
            for line in f:
                parts = line.rstrip('\r\n').split('\t')
                if len(parts) < 3: continue
                dt_sample_rows += 1
                vals = parts[2:]
                for v in vals:
                    dt_total_vals += 1
                    if is_missing(v, count_zero=False):
                        dt_missing += 1

        rec['n_dt_feats']   = len(dt_feats)
        rec['n_dt_samples'] = dt_sample_rows
        rec['dt_miss_pct']  = round(dt_missing / dt_total_vals * 100, 3) if dt_total_vals > 0 else None
        rec['dt_n_adduct']  = sum(1 for f in dt_feats if ADDUCT_PAT.search(f))

    except Exception as e:
        errors += 1; continue

    # ── 1, 2, 3, 5: mwtab features (block rows 2+) ──────────────────────────
    try:
        mw_feats    = set()
        mw_n_samples = 0
        in_block    = False; rows_seen = 0
        block_type  = ''
        is_nmr      = False
        mw_missing  = 0; mw_total_vals = 0
        mw_samples  = []   # sample IDs from row 0

        with open(mw_f, errors='replace') as f:
            for line in f:
                ls = line.strip()
                m = BLOCK_START.match(ls)
                if m:
                    in_block = True; rows_seen = 0
                    block_type = ls
                    if ls in NMR_BLOCKS: is_nmr = True
                    continue
                if BLOCK_END.match(ls):
                    in_block = False; continue
                if not in_block: continue

                rows_seen += 1
                cols = line.rstrip('\r\n').split('\t')
                label = cols[0].strip()

                if rows_seen == 1:   # Samples row
                    mw_samples = [c.strip() for c in cols[1:] if c.strip()]
                    mw_n_samples = len(mw_samples)
                    continue
                if rows_seen == 2 and label == 'Factors':
                    continue         # skip Factors row
                # Feature row
                # Cap at mw_n_samples to avoid counting trailing empty tab columns
                # mwtab: zeros are true "not detected" — count as missing
                if label:
                    mw_feats.add(label)
                    vals = cols[1:mw_n_samples + 1]
                    for v in vals:
                        mw_total_vals += 1
                        if is_missing(v, count_zero=True):
                            mw_missing += 1

        rec['n_mw_feats']    = len(mw_feats)
        rec['n_mw_samples']  = mw_n_samples
        rec['mw_miss_pct']   = round(mw_missing / mw_total_vals * 100, 3) if mw_total_vals > 0 else None
        rec['mw_n_adduct']   = sum(1 for f in mw_feats if ADDUCT_PAT.search(f))
        rec['mw_adduct_pct'] = round(rec['mw_n_adduct'] / len(mw_feats) * 100, 2) if mw_feats else 0
        rec['is_nmr']        = int(is_nmr)
        rec['block_type']    = block_type

    except Exception as e:
        errors += 1; continue

    # ── 2: Jaccard(dt_features, mw_features) ──────────────────────────────
    n_inter = len(dt_feats & mw_feats)
    n_union = len(dt_feats | mw_feats)
    rec['jaccard_dt_mw'] = round(n_inter / n_union, 6) if n_union > 0 else 0.0
    rec['n_shared']      = n_inter
    rec['feat_diff']     = rec['n_mw_feats'] - rec['n_dt_feats']

    results.append(rec)

    if (idx + 1) % 500 == 0:
        print(f"  {idx+1}/{len(target)} done", flush=True)

print(f"\nDone: {len(results)} analysed, {errors} errors", flush=True)

# Save
fields = ['an','st','n_dt_feats','n_dt_samples','dt_miss_pct','dt_n_adduct',
          'n_mw_feats','n_mw_samples','mw_miss_pct','mw_n_adduct','mw_adduct_pct',
          'is_nmr','block_type','jaccard_dt_mw','n_shared','feat_diff']
with open('/tmp/dt_mw_metrics.tsv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields, delimiter='\t', extrasaction='ignore')
    w.writeheader(); w.writerows(results)
print("Saved: /tmp/dt_mw_metrics.tsv", flush=True)

# Quick summary
j = [r['jaccard_dt_mw'] for r in results]
fd = [r['feat_diff'] for r in results]
ap = [r['mw_adduct_pct'] for r in results]
nm = sum(r['is_nmr'] for r in results)
print(f"\nSummary:")
print(f"  Jaccard(dt,mw): median={median(j):.3f}, zero={sum(1 for x in j if x==0)}, >0.9={sum(1 for x in j if x>0.9)}")
print(f"  feat_diff (mw-dt): median={median(fd):.0f}, mean={sum(fd)/len(fd):.1f}")
print(f"  mw adduct%: median={median(ap):.1f}%")
print(f"  NMR analyses: {nm}")
