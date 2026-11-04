"""Fill the metric "Low-information features" of the Analytical QC section of the v9 cache (Reviewer 1, comment 3, metric 1).

Per valid matrix (analysis x endpoint): the values are read with the manuscript's Table 4 readers
(outputs/table4_descriptors_20260927/table4_descriptors.py) and their positions checked against the roster. Every feature that
is constant or near-zero variance under the loosest adjustable values (merit/low_information_v9.py::LOOSEST) on the ML-eligible
samples or on all measured positions is stored as codes per position, and every other feature with a missing value as presence
bits per position; for a matrix with linear declared units and no negative value, the values of every feature (matrices up to
1,000,000 cells) or of every feature with a CV below 5% (larger matrices), for the relative-spread caution; in
  <v9 root>/low_information/<ST>/<AN>_<endpoint>.npz   (codes, top_values, names, present_bits, index, cv_values, cv_index, cv_names)
and endpoints[*].sections.analytical.low_information holds the counts needed to recompute (features, other features without a
missing value, stored features, file) and the result with the default values. About 15 minutes with 40 workers.
usage: add_low_information_from_deposit.py [v9 root] [workers]
"""
import collections, datetime, json, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 40
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/table4_descriptors_20260927'))
from merit import low_information_v9 as li
from merit import structural_v9 as sv
import table4_descriptors as t4
t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_feats': list(feats), '_mat': mat}


def one(path):
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']; c = collections.Counter(); problems = []
    for a in v9['analyses']:
        aid = a['analysis_id']
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            r = t4.one((sid, aid, src))
            if r.get('error') or r['_sids'] != [row[0] for row in ep['roster']]:
                problems.append((sid, aid, src, r.get('error') or 'positions differ from the roster')); continue
            mat = np.asarray(r['_mat'], dtype=float)
            if not mat.size:
                mat = np.zeros((len(ep['roster']), 0))
            pos = sv.effective_positions(ep['roster'], None)
            measured = np.array([not p['row'][sv.I_ALIGN] for p in pos], dtype=bool)
            elig = np.array([p['eligible'] and not p['excluded'] and not p['row'][sv.I_ALIGN] for p in pos], dtype=bool)
            j_e, c_e, z_e = li.flags(mat[elig], *li.LOOSEST)
            _, c_m, z_m = li.flags(mat[measured], *li.LOOSEST)
            # every feature of a matrix up to li.STORE_ALL_CELLS cells (sample edits then exact); otherwise those that can be flagged
            keep = np.arange(mat.shape[1]) if mat.size <= li.STORE_ALL_CELLS else np.where(c_e | z_e | c_m | z_m)[0]
            codes = np.zeros((len(keep), mat.shape[0]), dtype=np.uint8); tops = np.full((len(keep), li.TOP_CODES), np.nan)
            for i, j in enumerate(keep):
                codes[i], tv = li.encode(mat[:, j], measured)
                tops[i, :len(tv)] = tv
            names = [str(r['_feats'][j]) for j in keep]
            rest = np.setdiff1d(np.arange(mat.shape[1]), keep)
            gaps = np.isnan(mat[measured][:, rest]).any(axis=0) if rest.size else np.zeros(0, dtype=bool)
            bits = np.packbits(~np.isnan(mat[:, rest[gaps]]).T, axis=1) if gaps.any() else np.zeros((0, 0), dtype=np.uint8)
            # relative spread (caution, not scored): linear declared units (measurement scale) and no negative value
            units = ((ep.get('sections') or {}).get('analytical') or {}).get('measurement_scale', {}).get('units', {})
            fam = units.get('family', 'Not declared')
            negative = bool(np.isfinite(mat[measured]).any() and np.nanmin(mat[measured]) < 0)
            reason = ('' if fam in li.LINEAR_FAMILIES and not negative else 'negative values are present' if fam in li.LINEAR_FAMILIES else
                      {'Log indicated': 'declared units are log-scaled', 'Normalised, relative or ratio': 'declared units are normalised, relative or a ratio',
                       'Other or mixed': 'declared units are other or mixed'}.get(fam, 'no units declared'))
            gate = {'checked': not reason, 'reason': reason, 'family': fam, 'units': (units.get('deposited') or {}).get('text') or units.get('raw', '')}
            cv_idx = np.zeros(0, dtype=np.int64)
            if gate['checked'] and mat.shape[1]:          # every feature up to li.STORE_ALL_CELLS cells (sample edits then exact)
                cv_idx = (np.arange(mat.shape[1]) if mat.size <= li.STORE_ALL_CELLS else
                          np.where((li.cv(mat[elig]) < li.CV_MAX) | (li.cv(mat[measured]) < li.CV_MAX))[0])
            cv_vals = mat[:, cv_idx].T.astype(np.float64) if cv_idx.size else np.zeros((0, mat.shape[0]))
            rel = Path('low_information') / sid / f'{aid}_{src}.npz'
            (V9 / rel).parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(V9 / rel, codes=codes, top_values=tops, names=np.array(names, dtype=str), present_bits=bits, index=keep,
                                cv_values=cv_vals, cv_index=cv_idx, cv_names=np.array([str(r['_feats'][j]) for j in cv_idx], dtype=str))
            stored = {'features': int(mat.shape[1]), 'stored_features': int(len(keep)), 'with_presence_bits': int(gaps.sum()),
                      'complete_outside': int(rest.size - gaps.sum()), 'file': str(rel), 'cv_gate': gate, 'cv_stored': int(cv_idx.size)}
            data = {'codes': codes, 'top_values': tops, 'present_bits': bits, 'index': keep, 'cv_values': cv_vals, 'cv_index': cv_idx,
                    'cv_names': np.array([str(r['_feats'][j]) for j in cv_idx], dtype=str)}
            d = li.compute(stored, data, names, ep['roster'])
            d['cv'] = {k: v for k, v in d['cv'].items() if k != 'features'}
            stored['default'] = {k: v for k, v in d.items() if k != 'flagged_features'}
            ep.setdefault('sections', {}).setdefault('analytical', {})['low_information'] = stored
            c[f'{src}: matrices'] += 1; c[f'{src}: stored features'] += len(keep)
            c[f'{src}: scored'] += stored['default']['score'] is not None
            c[f'{src}: relative spread checked'] += gate['checked']
            c[f'{src}: matrices with a feature below CV 1%'] += bool(stored['default']['cv'].get('features_below'))
    Path(path).write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return c, problems


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    total = collections.Counter(); problems = []; files = sorted((V9 / 'json').glob('*_v9.json')); done = 0
    with Pool(WORKERS) as pool:
        for c, p in pool.imap_unordered(one, files, chunksize=2):
            total.update(c); problems += p; done += 1
            if done % 50 == 0 or done == len(files):
                (V9 / 'low_information_progress.json').write_text(json.dumps({'studies_done': done, 'studies': len(files),
                                                                              'updated': datetime.datetime.now().isoformat(timespec='seconds')}))
    if problems:
        for p in problems[:20]: print('PROBLEM', p)
        sys.exit(f'{len(problems)} problems; manifest not updated')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    section = manifest['sections'].setdefault('analytical', {})
    section['status'] = ('partly filled: two informational blocks, sample eligibility & analytical controls, sample-level missingness, '
                         'low-information features, measurement scale, sample annotations; other metrics not filled yet')
    section['low_information'] = {
        'status': 'filled', 'scored': 'low-information features: 100 x (1 - (constant + near-zero variance) / features with >= 3 values)',
        'rule': 'caret nearZeroVar on ML-eligible samples: constant, or freqRatio > 19 and percentUnique <= 10 (adjustable 5-99, 1-25)',
        'caution': 'not scored: features not flagged with CV < 1% (adjustable 0.1-5), only for linear declared units and no negative value',
        'computed_by': 'merit/low_information_v9.py', 'codes': 'low_information/<ST>/<AN>_<endpoint>.npz',
        'computed_at': started, 'script': str(Path(__file__).resolve()), 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k, v in sorted(total.items()): print(f'{v:12,}  {k}')
