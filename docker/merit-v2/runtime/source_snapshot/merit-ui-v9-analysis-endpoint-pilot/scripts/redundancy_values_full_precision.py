"""Redundancy store: keep the values of a matrix at full precision where single precision changes them (1 Nov 2026, approved by the author).

Why: add_redundancy_from_deposit.py computed every stored result from the deposit values at full precision (float64), but saved the
values used for sample edits as float32. Where float32 merges two distinct deposited values of a feature (e.g. ST001777 AN002884
mwTab: 90.05498 and 90.05499), the ranks change, so a result recomputed after a sample edit could differ from the stored one for
reasons that have nothing to do with the edit (independent audit of Data at a glance, 1 Nov 2026).

What: for every correlation/<ST>/<AN>_<endpoint>.npz that holds values, read the matrix again with the same reader as the builder
(manuscript Table 4 readers), check that the positions match the roster and that the stored float32 values equal the deposit cast to
float32; then, when any feature has fewer distinct values in float32 than in float64 (finite values only), rewrite the file with the
values in float64. Every other array is copied unchanged, so no stored result, record or score changes. The original file is copied
to <v9 root>/correlation/_backup_float32_20261101/ first.

Check: for every rewritten matrix, recomputing from the new values with the deposited samples and default settings gives the stored
result (flagged count and pairs above 0.90). Report: <v9 root>/correlation/full_precision_20261101.tsv and .json.
usage: redundancy_values_full_precision.py [v9 root] [workers] [--write]   (without --write: report only)
"""
import json, os, shutil, sys
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1'); os.environ.setdefault('OMP_NUM_THREADS', '1')
from multiprocessing import Pool
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot')
args = [a for a in sys.argv[1:] if not a.startswith('--')]
V9 = Path(args[0]) if args else B / 'merit-cache-workbench-full-v9'
WORKERS = int(args[1]) if len(args) > 1 else 40
WRITE = '--write' in sys.argv
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/table4_descriptors_20260927'))
from merit import correlation_v9 as cv, low_information_v9 as li, structural_v9 as sv
import table4_descriptors as t4
t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_feats': list(feats), '_mat': mat}
BACKUP = V9 / 'correlation' / '_backup_float32_20261101'


def merges(m64: np.ndarray) -> int:
    """Features in which float32 has fewer distinct finite values than float64."""
    n = 0
    for j in range(m64.shape[1]):
        c = m64[:, j]; c = c[np.isfinite(c)]
        if c.size and np.unique(c).size != np.unique(c.astype(np.float32)).size:
            n += 1
    return n


def one(path):
    from threadpoolctl import threadpool_limits
    threadpool_limits(1)
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']; out = []
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            st = ((ep.get('sections') or {}).get('analytical') or {}).get('redundancy')
            if 'roster' not in ep or not st or not st.get('values_stored'):
                continue
            f = V9 / st['file']; z = dict(np.load(f))
            if 'values' not in z or z['values'].dtype != np.float32:
                continue
            key = (sid, a['analysis_id'], src)
            r = t4.one(key)
            if r.get('error') or r['_sids'] != [row[0] for row in ep['roster']]:
                out.append({'key': key, 'status': 'problem', 'detail': r.get('error') or 'positions differ from the roster'}); continue
            m64 = np.asarray(r['_mat'], dtype=float)
            if m64.shape != z['values'].shape or not np.array_equal(m64.astype(np.float32), z['values'], equal_nan=True):
                out.append({'key': key, 'status': 'problem', 'detail': 'stored values differ from the deposit'}); continue
            n = merges(m64)
            if not n:
                continue
            rec = {'key': key, 'status': 'rewrite', 'features_merged_in_float32': n, 'features': int(m64.shape[1]), 'file': st['file']}
            # the stored default result, recomputed from float64 and from float32 (deposited samples, default settings)
            keep = np.array([p['eligible'] and not p['excluded'] and not p['row'][sv.I_ALIGN] for p in sv.effective_positions(ep['roster'], None)])
            ev = z['evaluated']
            if keep.sum() >= cv.MIN_SAMPLES and ev.size >= 2 and m64.size <= li.STORE_ALL_CELLS:
                names = [str(x) for x in z['names']]
                for tag, vals in (('float64', m64), ('float32', z['values'].astype(np.float64))):
                    s = cv.summarise(int(keep.sum()), int(ev.size), *cv.spearman_blocks(vals[keep][:, ev]), 0.90)
                    rec[f'recomputed_{tag}'] = [s['redundant'], s['pairs_above']]
                s = cv.summarise(int(keep.sum()), int(ev.size), z['sums'], z['cnt'], z['pairs'], z['rho'], 0.90)
                rec['stored'] = [s['redundant'], s['pairs_above']]
            if WRITE:
                b = BACKUP / st['file'].split('correlation/', 1)[1]; b.parent.mkdir(parents=True, exist_ok=True)
                if not b.exists():
                    shutil.copy2(f, b)
                z['values'] = m64
                tmp = f.with_name(f.stem + '.tmp.npz')
                np.savez_compressed(tmp, **z); os.replace(tmp, f)
                rec['written'] = True
            out.append(rec)
    return out


def main():
    files = sorted((V9 / 'json').glob('*_v9.json'))
    with Pool(WORKERS) as pool:
        rows = [r for o in pool.imap_unordered(one, files, chunksize=4) for r in o]
    rw = [r for r in rows if r['status'] == 'rewrite']; pr = [r for r in rows if r['status'] == 'problem']
    agree64 = sum(1 for r in rw if r.get('recomputed_float64') == r.get('stored'))
    agree32 = sum(1 for r in rw if r.get('recomputed_float32') == r.get('stored'))
    checked = sum(1 for r in rw if 'stored' in r)
    summary = {'write': WRITE, 'matrices_rewritten' if WRITE else 'matrices_to_rewrite': len(rw), 'studies': len({r['key'][0] for r in rw}),
               'problems': len(pr), 'checked_against_stored_result': checked,
               'recomputed_float64_equals_stored': agree64, 'recomputed_float32_equals_stored': agree32,
               'backup': str(BACKUP) if WRITE else None}
    (V9 / 'correlation' / 'full_precision_20261101.json').write_text(json.dumps({'summary': summary, 'problems': pr, 'rows': rw}, indent=1, default=list))
    with open(V9 / 'correlation' / 'full_precision_20261101.tsv', 'w') as fh:
        fh.write('study_id\tanalysis_id\tendpoint\tfeatures\tfeatures_merged_in_float32\tstored\trecomputed_float64\trecomputed_float32\n')
        for r in sorted(rw, key=lambda r: r['key']):
            fh.write('\t'.join(map(str, list(r['key']) + [r['features'], r['features_merged_in_float32'], r.get('stored', ''),
                                                          r.get('recomputed_float64', ''), r.get('recomputed_float32', '')])) + '\n')
    print(json.dumps(summary, indent=1))
    for p in pr[:10]:
        print('PROBLEM', p)


if __name__ == '__main__':
    main()
