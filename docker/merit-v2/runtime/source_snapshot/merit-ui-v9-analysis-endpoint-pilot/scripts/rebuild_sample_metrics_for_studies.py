"""Rebuild the sample-level metrics of a few studies after their ML-eligible positions changed (e.g. apply_sample_table_rule.py).

Calls each builder's own per-study function, in build order, for the listed studies only: sample-level missingness, measurement
scale, outlier samples and redundant features (phase 1 and the write of the study file, then outputs/redundant_features_v9_claude_20261018/
per_matrix.tsv rewritten by resummarise_redundancy.py --tsv-only from the saved pairs). Low-information features need the project venv: run with --low-information under
merit-ui-v8/.venv/bin/python. Ends with manifest_sync.py --write, so the manifest counts and index.json follow the rebuilt records.
usage: rebuild_sample_metrics_for_studies.py ST001095 ST002402 ... [--low-information]
"""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
V9 = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/merit-cache-workbench-full-v9')
studies = [a.upper() for a in sys.argv[1:] if a.startswith('ST')]
files = [V9 / 'json' / f'{s.lower()}_v9.json' for s in studies]
assert all(f.is_file() for f in files), files
sys.argv = [sys.argv[0]]          # the builders read their own arguments

if '--low-information' in __import__('sys').orig_argv:
    import add_low_information_from_deposit as L
    for f in files:
        print('low-information', f.name, L.one(f))
    import subprocess                              # keep manifest.json and index.json equal to the records (manifest_sync.py)
    subprocess.run(['/usr/bin/python3', str(HERE / 'manifest_sync.py'), '--write'], check=True)
    raise SystemExit

import add_missingness_from_deposit as M
import add_scale_from_deposit as SC
import add_outliers_from_deposit as O
import add_redundancy_from_deposit as R
for name, mod in (('missingness', M), ('scale', SC), ('outliers', O)):
    for f in files:
        print(name, f.name, mod.one(f))
for f in files:
    deferred, problems = R.phase1(f)
    assert not deferred and not problems, (f, deferred, problems)
results = {tuple(r['key']): r for r in (json.loads(p.read_text()) for p in R.RES.glob('*.json'))}
for f in files:
    v9 = json.loads(f.read_text()); sid = v9['study_id']
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' in ep:
                r = results[(sid, a['analysis_id'], src)]
                ep['sections']['analytical']['redundancy'] = {k: r[k] for k in ('file', 'features', 'evaluated', 'values_stored', 'pairs_stored')} | {'default': r['0.90']}
    f.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    print('redundancy', f.name, 'written')
import subprocess                                  # sensitivity table from the saved pairs of every matrix with the current counting rule
subprocess.run(['/usr/bin/python3', str(HERE / 'resummarise_redundancy.py'), '--tsv-only'], check=True)   # (not from correlation/_results, which predate 2026-10-18)
import subprocess                                  # keep manifest.json and index.json equal to the records (manifest_sync.py)
subprocess.run(['/usr/bin/python3', str(HERE / 'manifest_sync.py'), '--write'], check=True)
