#!/usr/bin/env python3
"""
Collect per-analysis stats independently for each source.
No pairing — each source is processed across all analyses where it is valid.

Outputs:
  /tmp/dt_independent.tsv   — all datatable-valid analyses (n≈4872)
  /tmp/mw_independent.tsv   — all mwtab-valid analyses    (n≈4990)
  /tmp/ut_independent.tsv   — all untarg-valid analyses   (n≈1887)

Columns per file: an, st, n_feats, n_samples, miss_pct, median_intensity
"""
import csv, re, math
from pathlib import Path
from statistics import median as pymedian

DUMP = Path('/home/shayantan/metabolomics/ML-ready/mw-dump-latest-confirmation-latest-version')
SRC  = Path('/home/shayantan/metabolomics/ML-ready/outputs/diagnostics/mw_6696_source_presence.tsv')

BLOCK_START = re.compile(r'^(\w+)_METABOLITE_DATA_START')
BLOCK_END   = re.compile(r'^\w+_METABOLITE_DATA_END')

_MISSING_TOKENS = {
    '', 'na', 'n/a', 'nan', 'null', 'none', 'nd', 'bdl', 'bql',
    'lod', '<lod', 'llod', '<llod', 'lloq', '<lloq', 'bloq',
    'nq', 'loq', 'missing', 'not detected',
}

def is_missing(val: str, count_zero: bool) -> bool:
    v = val.strip().lower()
    if v in _MISSING_TOKENS: return True
    if v.startswith('<'): return True
    try:
        f = float(v)
        if f < 0: return True
        if f == 0.0 and count_zero: return True
    except ValueError:
        pass
    return False

def _median_of(vals):
    if not vals: return None
    s = sorted(vals); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2

FIELDS = ['an', 'st', 'n_feats', 'n_samples', 'miss_pct', 'median_intensity']

src_rows = list(csv.DictReader(open(SRC), delimiter='\t'))

# ── Datatable ─────────────────────────────────────────────────────────────────
dt_target = [r for r in src_rows if r['datatable_valid_present'] == '1']
print(f"Datatable: {len(dt_target)} analyses", flush=True)
dt_results = []
dt_errors  = 0
for i, r in enumerate(dt_target):
    an, st = r['analysis_id'], r['study_id']
    f_path = DUMP / st / an / 'tabular' / f'{an}_datatable.tsv'
    if not f_path.exists(): dt_errors += 1; continue
    try:
        with open(f_path, errors='replace') as fh:
            hdr   = fh.readline().rstrip('\r\n').split('\t')
            n_feats = len([h.strip() for h in hdr[2:] if h.strip()])
            n_samp = 0; n_miss = 0; n_tot = 0; intensities = []
            for line in fh:
                parts = line.rstrip('\r\n').split('\t')
                if len(parts) < 3: continue
                n_samp += 1
                vals = parts[2:2 + n_feats]
                for v in vals:
                    n_tot += 1
                    if is_missing(v, count_zero=False):
                        n_miss += 1
                    else:
                        try:
                            fv = float(v.strip())
                            if fv > 0: intensities.append(fv)
                        except ValueError: pass
        med_int = _median_of(intensities)
        dt_results.append({
            'an': an, 'st': st, 'n_feats': n_feats, 'n_samples': n_samp,
            'miss_pct': round(n_miss / n_tot * 100, 3) if n_tot > 0 else None,
            'median_intensity': round(med_int, 4) if med_int else None,
        })
    except Exception as e:
        dt_errors += 1
    if (i+1) % 500 == 0: print(f"  dt {i+1}/{len(dt_target)}", flush=True)

print(f"  Done: {len(dt_results)} ok, {dt_errors} errors", flush=True)
with open('/tmp/dt_independent.tsv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=FIELDS, delimiter='\t')
    w.writeheader(); w.writerows(dt_results)
print("  Saved: /tmp/dt_independent.tsv", flush=True)


# ── mwTab ─────────────────────────────────────────────────────────────────────
mw_target = [r for r in src_rows if r['mwtab_valid_present'] == '1']
print(f"\nmwTab: {len(mw_target)} analyses", flush=True)
mw_results = []
mw_errors  = 0
for i, r in enumerate(mw_target):
    an, st = r['analysis_id'], r['study_id']
    f_path = DUMP / st / an / 'json' / f'{an}_mwtab.txt'
    if not f_path.exists(): mw_errors += 1; continue
    try:
        n_feats = 0; n_samples = 0; n_miss = 0; n_tot = 0
        rows_seen = 0; in_block = False; intensities = []
        with open(f_path, errors='replace') as fh:
            for line in fh:
                ls = line.strip()
                if BLOCK_START.match(ls):
                    in_block = True; rows_seen = 0; continue
                if BLOCK_END.match(ls):
                    in_block = False; continue
                if not in_block: continue
                rows_seen += 1
                cols = line.rstrip('\r\n').split('\t')
                label = cols[0].strip()
                if rows_seen == 1:
                    n_samples = len([c.strip() for c in cols[1:] if c.strip()])
                    continue
                if rows_seen == 2 and label == 'Factors':
                    continue
                if label:
                    n_feats += 1
                    vals = cols[1:n_samples + 1]
                    for v in vals:
                        n_tot += 1
                        if is_missing(v, count_zero=True):
                            n_miss += 1
                        else:
                            try:
                                fv = float(v.strip())
                                if fv > 0: intensities.append(fv)
                            except ValueError: pass
        med_int = _median_of(intensities)
        mw_results.append({
            'an': an, 'st': st, 'n_feats': n_feats, 'n_samples': n_samples,
            'miss_pct': round(n_miss / n_tot * 100, 3) if n_tot > 0 else None,
            'median_intensity': round(med_int, 4) if med_int else None,
        })
    except Exception as e:
        mw_errors += 1
    if (i+1) % 500 == 0: print(f"  mw {i+1}/{len(mw_target)}", flush=True)

print(f"  Done: {len(mw_results)} ok, {mw_errors} errors", flush=True)
with open('/tmp/mw_independent.tsv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=FIELDS, delimiter='\t')
    w.writeheader(); w.writerows(mw_results)
print("  Saved: /tmp/mw_independent.tsv", flush=True)


# ── Untarg ────────────────────────────────────────────────────────────────────
ut_target = [r for r in src_rows if r['untarg_valid_present'] == '1']
print(f"\nUntarg: {len(ut_target)} analyses", flush=True)
ut_results = []
ut_errors  = 0
for i, r in enumerate(ut_target):
    an, st = r['analysis_id'], r['study_id']
    f_path = DUMP / st / an / 'tabular' / f'{an}_untarg_data.tsv'
    if not f_path.exists(): ut_errors += 1; continue
    try:
        with open(f_path, errors='replace') as fh:
            hdr    = fh.readline().rstrip('\r\n').split('\t')
            n_feats = len([h.strip() for h in hdr[2:] if h.strip()])
            n_samp = 0; n_miss = 0; n_tot = 0; intensities = []
            for line in fh:
                parts = line.rstrip('\r\n').split('\t')
                if len(parts) < 3: continue
                n_samp += 1
                vals = parts[2:2 + n_feats]
                for v in vals:
                    n_tot += 1
                    if is_missing(v, count_zero=True):
                        n_miss += 1
                    else:
                        try:
                            fv = float(v.strip())
                            if fv > 0: intensities.append(fv)
                        except ValueError: pass
        med_int = _median_of(intensities)
        ut_results.append({
            'an': an, 'st': st, 'n_feats': n_feats, 'n_samples': n_samp,
            'miss_pct': round(n_miss / n_tot * 100, 3) if n_tot > 0 else None,
            'median_intensity': round(med_int, 4) if med_int else None,
        })
    except Exception as e:
        ut_errors += 1
    if (i+1) % 200 == 0: print(f"  ut {i+1}/{len(ut_target)}", flush=True)

print(f"  Done: {len(ut_results)} ok, {ut_errors} errors", flush=True)
with open('/tmp/ut_independent.tsv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=FIELDS, delimiter='\t')
    w.writeheader(); w.writerows(ut_results)
print("  Saved: /tmp/ut_independent.tsv", flush=True)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n=== Summary ===")
for label, results in [('Datatable', dt_results), ('mwTab', mw_results), ('Untarg', ut_results)]:
    mp = [r['miss_pct'] for r in results if r['miss_pct'] is not None]
    mi = [math.log10(r['median_intensity']) for r in results if r['median_intensity']]
    pn = [r['n_feats']/r['n_samples'] for r in results if r['n_samples'] > 0]
    print(f"{label} (n={len(results)}):")
    print(f"  miss%   median={pymedian(mp):.1f}%")
    print(f"  log10(intensity) median={pymedian(mi):.2f}" if mi else "  no intensity")
    print(f"  p/n     median={pymedian(pn):.2f}")
