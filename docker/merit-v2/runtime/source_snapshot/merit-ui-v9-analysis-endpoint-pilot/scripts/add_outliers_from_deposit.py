"""Fill the metric "Outlier samples" of the Analytical QC section of the v9 cache (Reviewer 1, comment 3).

Per valid matrix (analysis x endpoint): values read with the manuscript's Table 4 readers, positions checked against the roster;
endpoints[*].sample_medians_all = per position, the median of its numeric values over all features as deposited (zeros included,
missing and invalid entries left out; None when the position has no numeric value; Tier 1 alignment rows hold none);
endpoints[*].sections.analytical.outliers = {"default": the result with k = 1.5 on the ML-eligible samples} (merit/outliers_v9.py).
About 10 minutes with 60 processes. Progress: <v9 root>/outliers_progress.json.   usage: add_outliers_from_deposit.py [v9 root] [workers]
"""
import collections, datetime, json, os, sys, warnings
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
from multiprocessing import Pool
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/table4_descriptors_20260927'))
from merit import outliers_v9 as ov
import table4_descriptors as t4
t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_feats': list(feats), '_mat': mat}


def one(path):
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']; c = collections.Counter(); problems = []
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            r = t4.one((sid, a['analysis_id'], src))
            if r.get('error') or r['_sids'] != [row[0] for row in ep['roster']]:
                problems.append(((sid, a['analysis_id'], src), r.get('error') or 'positions differ from the roster')); continue
            mat = np.asarray(r['_mat'], dtype=float)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', RuntimeWarning)
                med = np.nanmedian(mat, axis=1) if mat.size else np.full(len(ep['roster']), np.nan)
            ep['sample_medians_all'] = [None if not np.isfinite(x) else float(x) for x in med]
            d = ov.compute(ep['roster'], ep['sample_medians_all'])
            ep.setdefault('sections', {}).setdefault('analytical', {})['outliers'] = {'default': {k: v for k, v in d.items() if k != 'outlier_samples'}}
            c[f'{src}: matrices'] += 1; c[f'{src}: scored'] += d['score'] is not None; c[f'{src}: outlier samples'] += d['outliers']
    Path(path).write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return c, problems


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    files = sorted((V9 / 'json').glob('*_v9.json')); total = collections.Counter(); problems = []; done = 0
    with Pool(WORKERS) as pool:
        for c, p in pool.imap_unordered(one, files, chunksize=2):
            total.update(c); problems += p; done += 1
            if done % 50 == 0 or done == len(files):
                (V9 / 'outliers_progress.json').write_text(json.dumps({'studies_done': done, 'studies': len(files),
                                                                       'updated': datetime.datetime.now().isoformat(timespec='seconds')}))
    if problems:
        for p in problems[:20]: print('PROBLEM', p)
        sys.exit(f'{len(problems)} problems; manifest not updated')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    manifest['sections'].setdefault('analytical', {})['outliers'] = {
        'status': 'filled', 'scored': 'outlier samples: 100 x (1 - outliers / samples evaluated); Tukey fences k = 1.5 (adjustable 1.0-3.0) on per-sample medians as deposited',
        'computed_by': 'merit/outliers_v9.py', 'computed_at': started, 'script': str(Path(__file__).resolve()), 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k_, v in sorted(total.items()): print(f'{v:8,}  {k_}')
