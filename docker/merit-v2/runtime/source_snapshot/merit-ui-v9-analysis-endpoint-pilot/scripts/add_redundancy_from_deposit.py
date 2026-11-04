"""Fill the metric "Redundant features (multicollinearity)" of the Analytical QC section of the v9 cache (Reviewer 1, comment 3).

Per valid matrix (analysis x endpoint): values read with the manuscript's Table 4 readers, positions checked against the roster;
ML-eligible samples; features with >= 3 values not flagged by Low-information features (defaults); exact Spearman (merit/correlation_v9.py).
Phase 1: 60 processes, one BLAS thread each, every matrix read once and computed, except matrices with more than BIG features.
Phase 2: each deferred matrix alone, its feature blocks spread over 60 processes (ranks memory-mapped from a temporary file).
Phase 3: results written into the study JSON (sections.analytical.redundancy) and the manifest; per-matrix sensitivity table.
Stored per matrix, <v9 root>/correlation/<ST>/<AN>_<endpoint>.npz: evaluated (feature positions), names, sums and counts of |rho|,
pairs and rho (|rho| >= 0.80), and the values of every position x feature (float32; float64 where float32 would merge distinct
values of a feature, since 1 Nov 2026) for matrices up to 1,000,000 cells (sample edits).
Progress: <v9 root>/correlation_progress.json.   usage: add_redundancy_from_deposit.py [v9 root] [workers]
"""
import collections, datetime, json, os, shutil, sys, time
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1'); os.environ.setdefault('OMP_NUM_THREADS', '1')
from multiprocessing import Pool
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/table4_descriptors_20260927'))
from merit import correlation_v9 as cv, low_information_v9 as li, structural_v9 as sv
import table4_descriptors as t4
t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_feats': list(feats), '_mat': mat}
BIG = 20_000                     # features: larger matrices are computed in phase 2
RES = V9 / 'correlation' / '_results'; TMP = V9 / 'correlation' / '_tmp'
SENS = (0.80, 0.90, 0.95, 0.99)
PROGRESS = V9 / 'correlation_progress.json'
T0 = time.time()


def progress(**kw):
    kw.update(elapsed_min=round((time.time() - T0) / 60, 1), updated=datetime.datetime.now().isoformat(timespec='seconds'))
    PROGRESS.write_text(json.dumps(kw))


def finish(key, n, ev, names_all, sums, cnt, pairs, rho, values):
    sid, aid, src = key
    rel = Path('correlation') / sid / f'{aid}_{src}.npz'
    (V9 / rel).parent.mkdir(parents=True, exist_ok=True)
    extra = {'values': values} if values is not None else {}
    np.savez_compressed(V9 / rel, evaluated=ev.astype(np.int64), names=np.array(names_all, dtype=str), sums=sums, cnt=cnt,
                        pairs=pairs, rho=rho, **extra)
    res = {'key': key, 'file': str(rel), 'features': len(names_all), 'evaluated': int(ev.size), 'values_stored': values is not None,
           'pairs_stored': int(len(rho))}
    for c in SENS:
        s = cv.summarise(n, int(ev.size), sums, cnt, pairs, rho, c)
        res[f'{c:.2f}'] = {k: s[k] for k in ('score', 'redundant', 'pairs_above', 'largest_group', 'not_assessed', 'ml_eligible_samples',
                                             'features_evaluated', 'cutoff')}
    (RES / f'{sid}__{aid}__{src}.json').write_text(json.dumps(res))


def phase1(path):
    from threadpoolctl import threadpool_limits
    threadpool_limits(1)
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']; deferred, problems = [], []
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            key = (sid, a['analysis_id'], src)
            r = t4.one(key)
            if r.get('error') or r['_sids'] != [row[0] for row in ep['roster']]:
                problems.append((key, r.get('error') or 'positions differ from the roster')); continue
            mat = np.asarray(r['_mat'], dtype=float)
            if not mat.size:
                mat = np.zeros((len(ep['roster']), 0))
            keep = np.array([p['eligible'] and not p['excluded'] and not p['row'][sv.I_ALIGN] for p in sv.effective_positions(ep['roster'], None)])
            sub = mat[keep]
            judged, const, nzv = li.flags(sub, *[li.DEFAULT_PARAMS[k] for k in ('low_information_freq_ratio', 'low_information_unique_pct')])
            ev = np.where(judged & ~(const | nzv))[0]
            values = None
            if mat.size <= li.STORE_ALL_CELLS:                       # float32, or float64 when a value is beyond float32's range or when
                # float32 would merge two distinct values of a feature (ties change the ranks; 1 Nov 2026, redundancy_values_full_precision.py)
                f32 = mat.astype(np.float32)
                merged = any(np.unique(c[np.isfinite(c)]).size != np.unique(c[np.isfinite(c)].astype(np.float32)).size for c in mat.T)
                values = mat.astype(np.float64) if np.nanmax(np.abs(mat), initial=0) > np.finfo(np.float32).max or merged else f32
            n = int(keep.sum())
            if n < cv.MIN_SAMPLES or ev.size < 2:
                finish(key, n, ev, r['_feats'], np.zeros(ev.size), np.zeros(ev.size, np.int64), np.zeros((0, 2), np.int32), np.zeros(0, np.float32), values)
            elif ev.size > BIG:
                TMP.mkdir(parents=True, exist_ok=True)
                prep = cv.prepare(sub[:, ev]); stem = TMP / f'{sid}__{a["analysis_id"]}__{src}'
                for k_, v_ in prep.items():
                    if isinstance(v_, np.ndarray):
                        np.save(f'{stem}__{k_}.npy', v_)
                meta = {'key': key, 'n': n, 'complete': prep['complete'], 'ev': ev.tolist(), 'names': r['_feats'], 'stem': str(stem)}
                Path(f'{stem}__meta.json').write_text(json.dumps(meta)); deferred.append(str(stem))
            else:
                finish(key, n, ev, r['_feats'], *cv.spearman_blocks(sub[:, ev]), values)
    return deferred, problems


_P = {}


def phase2_block(job):
    from threadpoolctl import threadpool_limits
    threadpool_limits(1)
    stem, complete, n, a, b = job
    if stem not in _P:
        _P.clear()
        ks = ('z',) if complete else ('x0', 'm', 'x2')
        _P[stem] = {'complete': complete, 'n': n, **{k: np.load(f'{stem}__{k}.npy', mmap_mode='r') for k in ks}}
    return a, cv.compute_block(_P[stem], a, b)


def main():
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    for d in (RES, TMP):
        shutil.rmtree(d, ignore_errors=True); d.mkdir(parents=True, exist_ok=True)
    files = sorted((V9 / 'json').glob('*_v9.json')); deferred, problems, done = [], [], 0
    progress(phase='1 of 3: reading and computing every matrix', studies_done=0, studies=len(files))
    with Pool(WORKERS) as pool:
        for d, p in pool.imap_unordered(phase1, files, chunksize=1):
            deferred += d; problems += p; done += 1
            if done % 20 == 0 or done == len(files):
                progress(phase='1 of 3: reading and computing every matrix', studies_done=done, studies=len(files),
                         matrices_done=len(list(RES.glob('*.json'))), large_matrices_deferred=len(deferred))
    if problems:
        for p in problems[:20]: print('PROBLEM', p)
        sys.exit(f'{len(problems)} problems')
    for i, stem in enumerate(sorted(deferred), 1):
        meta = json.loads(Path(f'{stem}__meta.json').read_text()); f = len(meta['ev']); bs = cv.block_size(f)
        jobs = [(stem, meta['complete'], meta['n'], a, min(f, a + bs)) for a in range(0, f, bs)]
        sums = np.zeros(f); cnt = np.zeros(f, dtype=np.int64); I, J, V = [], [], []; k = 0
        with Pool(WORKERS) as pool:
            for a, (s_, c_, i_, j_, v_) in pool.imap_unordered(phase2_block, jobs, chunksize=1):
                sums[a:a + len(s_)] = s_; cnt[a:a + len(c_)] = c_; I.append(i_); J.append(j_); V.append(v_); k += 1
                if k % 25 == 0 or k == len(jobs):
                    progress(phase='2 of 3: large matrices, blocks in parallel', large_matrix=f'{i} of {len(deferred)}',
                             matrix=' '.join(meta['key']), features=f, blocks_done=k, blocks=len(jobs))
        _, _, pairs, rho = cv.merge_blocks(sums, cnt, I, J, V)
        finish(tuple(meta['key']), meta['n'], np.array(meta['ev']), meta['names'], sums, cnt, pairs, rho, None)
        for x in TMP.glob(Path(stem).name + '__*'):
            x.unlink()
    progress(phase='3 of 3: writing the study files')
    results = {tuple(r['key']): r for r in (json.loads(p.read_text()) for p in RES.glob('*.json'))}
    total = collections.Counter(); rows = []
    for path in files:
        v9 = json.loads(path.read_text()); sid = v9['study_id']
        for a in v9['analyses']:
            for src, ep in a['endpoints'].items():
                if 'roster' not in ep:
                    continue
                r = results[(sid, a['analysis_id'], src)]
                ep['sections']['analytical']['redundancy'] = {k: r[k] for k in ('file', 'features', 'evaluated', 'values_stored', 'pairs_stored')} | {'default': r['0.90']}
                total[f'{src}: matrices'] += 1; total[f'{src}: scored'] += r['0.90']['score'] is not None
                rows.append(r)
        path.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    out = B / 'outputs/redundant_features_v9_claude_20261018'; out.mkdir(parents=True, exist_ok=True)
    with open(out / 'per_matrix.tsv', 'w') as fh:
        fh.write('study_id\tanalysis_id\tendpoint\tml_eligible_samples\tfeatures\tevaluated\tpairs_stored\t' +
                 '\t'.join(f'{k}_{c:.2f}' for c in SENS for k in ('score', 'redundant', 'pairs_above', 'largest_group')) + '\n')
        for r in sorted(rows, key=lambda r: r['key']):
            fh.write('\t'.join(map(str, list(r['key']) + [r['0.90']['ml_eligible_samples'], r['features'], r['evaluated'], r['pairs_stored']] +
                                   ['' if r[f'{c:.2f}'][k] is None else r[f'{c:.2f}'][k] for c in SENS for k in ('score', 'redundant', 'pairs_above', 'largest_group')])) + '\n')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    manifest['sections'].setdefault('analytical', {})['redundancy'] = {
        'status': 'filled', 'scored': 'redundant features: 100 x (1 - redundant / evaluated); Spearman, caret findCorrelation (fast), |rho| > 0.90 (adjustable 0.80-0.99)',
        'computed_by': 'merit/correlation_v9.py', 'store': 'correlation/<ST>/<AN>_<endpoint>.npz', 'computed_at': started,
        'script': str(Path(__file__).resolve()), 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    shutil.rmtree(TMP, ignore_errors=True)
    progress(phase='done', matrices=len(rows))
    for k, v in sorted(total.items()): print(f'{v:8,}  {k}')


if __name__ == '__main__':
    main()
