"""Fill ML Task Readiness (class separability and its informational rows) in the v9 cache, for every valid matrix.

Since 2026-10-24 one profile per sample ID (float64 throughout, distances kept as float32) (the mean of its positions' standardised-rank profiles), written to <v9 root>/separability_next
and endpoints[*].sections.ml_next until swapped in (the live tab keeps reading 'ml' meanwhile).
Per valid matrix (analysis x endpoint): values read with the manuscript's Table 4 readers from the frozen 18 September 2026 deposit,
positions checked against the roster; the measured positions (every position except Tier 1 sample alignment rows, QC-like and
blank-like included, so that any sample edit is covered) are ranked per feature and standardised (merit/separability_v9.py), and
<v9 root>/separability/<ST>/<AN>_<endpoint>.npz holds:
  pos          roster indices of the measured positions (the rows of everything below)
  dist         condensed Euclidean distances (computed in float64, kept as float32), when there are at most FULL_LIMIT positions; otherwise
  nn_idx, nn_dist  each position's NN_KEEP nearest neighbours
  informative  number of features that vary across the measured positions
and <AN>_<endpoint>_ranks.npy the dense ranks (uint16, 0 = missing) for the discriminative-features test.
endpoints[*].sections.ml = {store, n_features, default: separability_v9.compute(default classes, no edits, default k / theta / q)},
the default computed on the full distances.
Progress: <v9 root>/separability_progress.json, rewritten after every study and listing each matrix being computed right now
(study, analysis, endpoint, positions x features, started).   usage: add_separability_from_deposit.py [v9 root] [workers]
"""
import collections, datetime, json, os, sys, traceback
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1'); os.environ.setdefault('OMP_NUM_THREADS', '1')
from multiprocessing import Pool
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 48
RUNNING = V9 / 'separability_running'           # one small file per busy process: the matrix it is computing
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/table4_descriptors_20260927'))
from merit import separability_v9 as sp, structural_v9 as sv
import table4_descriptors as t4
t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_feats': list(feats), '_mat': mat}


def one(path):
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']; c = collections.Counter(); problems = []
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            beat = RUNNING / f'{os.getpid()}.json'
            beat.write_text(json.dumps({'matrix': f"{sid} {a['analysis_id']} {src}", 'positions': sum(1 for row in ep['roster'] if not row[3]),
                                        'features': ep.get('n_features'), 'started': datetime.datetime.now().isoformat(timespec='seconds')}))
            try:
                r = t4.one((sid, a['analysis_id'], src))
                if r.get('error') or r['_sids'] != [row[0] for row in ep['roster']]:
                    problems.append(((sid, a['analysis_id'], src), r.get('error') or 'positions differ from the roster')); continue
                mat = np.asarray(r['_mat'], dtype=float)
                pos = np.array([i for i, row in enumerate(ep['roster']) if not row[sv.I_ALIGN]], dtype=np.int32)
                m = mat[pos] if mat.size else np.zeros((len(pos), 0))
                z = sp.standardised_ranks(m)
                informative = int((np.abs(z).sum(axis=0) > 0).sum()) if z.size else 0
                ids = list(dict.fromkeys(str(ep['roster'][i][0]) for i in pos))            # sample IDs, first appearance order
                index = {u: j for j, u in enumerate(ids)}
                unit_of_row = np.array([index[str(ep['roster'][i][0])] for i in pos], dtype=np.int64)
                order_rows = np.argsort(unit_of_row, kind='stable')
                unit_ptr = np.concatenate([[0], np.cumsum(np.bincount(unit_of_row, minlength=len(ids)))]).astype(np.int64)
                prof = sp.unit_profiles(z, unit_of_row, len(ids)) if len(pos) else np.zeros((0, 0), np.float32)
                square = sp.distances(prof) if len(ids) else np.zeros((0, 0), np.float32)
                rel = Path('separability_next') / sid / f"{a['analysis_id']}_{src}.npz"
                out = V9 / rel; out.parent.mkdir(parents=True, exist_ok=True)
                common = dict(pos=pos, ids=np.array(ids, dtype=str), unit_ptr=unit_ptr, unit_rows=order_rows.astype(np.int32), informative=informative)
                if len(ids) <= sp.FULL_LIMIT:
                    np.savez(out, dist=sp.condensed(square), **common)
                else:
                    sq = square.astype(np.float64); np.fill_diagonal(sq, np.inf)
                    order = np.argsort(sq, axis=1, kind='stable')[:, :sp.NN_KEEP]
                    np.savez(out, nn_idx=order.astype(np.int32), nn_dist=np.take_along_axis(square, order, axis=1), **common)
                np.save(str(out).replace('.npz', '_ranks.npy'), sp.dense_ranks(m))
                n_features = int(mat.shape[1]) if mat.ndim == 2 else 0
                d = sp.compute(ep['roster'], sid, out, n_features, conflicts=ep.get('sample_table_conflicts'),
                               store={'square': square, **common})
                ep.setdefault('sections', {})['ml_next'] = {'store': str(rel), 'n_features': n_features, 'default': d}
                c[f'{src}: matrices'] += 1; c[f'{src}: scored'] += d['score'] is not None
                c[f'{src}: full distances' if len(ids) <= sp.FULL_LIMIT else f'{src}: nearest-neighbour lists'] += 1
            except Exception:
                problems.append(((sid, a['analysis_id'], src), traceback.format_exc(limit=3)))
    Path(path).write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    (RUNNING / f'{os.getpid()}.json').unlink(missing_ok=True)
    return c, problems


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    files = sorted((V9 / 'json').glob('*_v9.json'), key=lambda p: -p.stat().st_size)      # large studies first
    total = collections.Counter(); problems = []; done = 0
    RUNNING.mkdir(exist_ok=True)
    for f in RUNNING.glob('*.json'): f.unlink()
    with Pool(WORKERS, maxtasksperchild=20) as pool:
        for c, p in pool.imap_unordered(one, files, chunksize=1):
            total.update(c); problems += p; done += 1
            running = []
            for f in RUNNING.glob('*.json'):          # a worker may remove its file between listing and reading: skip it
                try:
                    running.append(json.loads(f.read_text()))
                except (FileNotFoundError, ValueError):
                    pass
            running.sort(key=lambda x: x['started'])
            (V9 / 'separability_progress.json').write_text(json.dumps({'studies_done': done, 'studies': len(files), 'problems': len(problems),
                                                                       'matrices_done': sum(v for k, v in total.items() if k.endswith(': matrices')),
                                                                       'running_now': running,
                                                                       'updated': datetime.datetime.now().isoformat(timespec='seconds')}, indent=1))
    for p in problems[:20]: print('PROBLEM', p)
    if problems:
        sys.exit(f'{len(problems)} problems; manifest not updated')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    manifest['sections']['ml_next'] = {'status': 'filled', 'scored': ['class_separability'],
                                  'rules': 'class separability = 100 x mean over classes of (1 - R(Ci)), Oh 2011 eq. 16, class-averaged, exact order-independent ties, one profile per sample ID (mean of its positions), k = 7, theta = 3, features ranked per matrix; chance level shown; informational: R per class and pair, samples surrounded by another class, discriminative features (Kruskal-Wallis, BH q = 0.05), p/n, subjects',
                                  'computed_by': 'merit/separability_v9.py', 'computed_at': started, 'script': str(Path(__file__).resolve()),
                                  'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k_, v in sorted(total.items()): print(f'{v:8,}  {k_}')
