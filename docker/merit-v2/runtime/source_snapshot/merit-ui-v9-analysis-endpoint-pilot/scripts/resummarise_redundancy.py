"""Recompute the stored results of "Redundant features" from the saved pairs (correlation/<ST>/<AN>_<endpoint>.npz) after a change
of the counting rule (merit/correlation_v9.py::summarise), without recomputing any correlation: sections.analytical.redundancy.default
(cut-off 0.90) of every study JSON and the sensitivity table outputs/redundant_features_v9_claude_20261018/per_matrix.tsv.
With --tsv-only the study files are not written (the stored results are only read again): used after single-study rebuilds,
whose raw results in correlation/_results predate the counting-rule change of 2026-10-18 and must not feed the table.
usage: resummarise_redundancy.py [v9 root] [--tsv-only]
"""
import json, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
TSV_ONLY = '--tsv-only' in sys.argv
args = [a for a in sys.argv[1:] if a != '--tsv-only']
V9 = Path(args[0]) if args else B / 'merit-cache-workbench-full-v9'
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
from merit import correlation_v9 as cv
SENS = (0.80, 0.90, 0.95, 0.99)
KEYS = ('score', 'redundant', 'pairs_above', 'largest_group', 'not_assessed', 'ml_eligible_samples', 'features_evaluated', 'cutoff')


def one(path):
    v9 = json.loads(Path(path).read_text()); rows = []
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            st = ((ep.get('sections') or {}).get('analytical') or {}).get('redundancy')
            if not st:
                continue
            z = np.load(V9 / st['file']); n = st['default']['ml_eligible_samples']
            res = {c: cv.summarise(n, int(z['evaluated'].size), z['sums'], z['cnt'], z['pairs'], z['rho'], c) for c in SENS}
            st['default'] = {k: res[0.90][k] for k in KEYS}
            rows.append([v9['study_id'], a['analysis_id'], src, n, st['features'], st['evaluated'], st['pairs_stored']] +
                        ['' if res[c][k] is None else res[c][k] for c in SENS for k in ('score', 'redundant', 'pairs_above', 'largest_group')])
    if not TSV_ONLY:
        Path(path).write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return rows


if __name__ == '__main__':
    with Pool(60) as pool:
        rows = [r for res in pool.map(one, sorted((V9 / 'json').glob('*_v9.json')), chunksize=8) for r in res]
    out = B / 'outputs/redundant_features_v9_claude_20261018'; out.mkdir(parents=True, exist_ok=True)
    with open(out / 'per_matrix.tsv', 'w') as fh:
        fh.write('study_id\tanalysis_id\tendpoint\tml_eligible_samples\tfeatures\tevaluated\tpairs_stored\t' +
                 '\t'.join(f'{k}_{c:.2f}' for c in SENS for k in ('score', 'redundant', 'pairs_above', 'largest_group')) + '\n')
        for r in sorted(rows):
            fh.write('\t'.join(map(str, r)) + '\n')
    print('matrices', len(rows))
    if not TSV_ONLY:
        import subprocess                          # keep manifest.json and index.json equal to the records (manifest_sync.py)
        subprocess.run([sys.executable, str(HERE / 'manifest_sync.py'), '--write'], check=True)
