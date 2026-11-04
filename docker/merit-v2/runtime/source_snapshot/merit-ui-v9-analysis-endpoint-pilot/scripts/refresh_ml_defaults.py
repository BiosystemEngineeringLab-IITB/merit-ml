"""Recompute the stored ML Task Readiness defaults (endpoints[*].sections.ml.default) with the current merit/separability_v9.py, from the
stored distances and ranks (nothing read from the deposit). Written 2026-10-24 to add the named top features of "Features associated
with the selected classes" (discriminative.top), the subject fields (subjects_confirmed, discriminative.adjusted) and, since
2026-10-25, usable_features (the readiness task gate). Every other field
must come out unchanged; any other difference is a problem, that study file is left unchanged, and the manifest is not touched.
Run with --dry-run first: everything is compared and nothing is written (MERIT_CODE can point at another copy of the code).
Progress: <v9 root>/ml_refresh_progress.json, rewritten every 50 studies.   usage: refresh_ml_defaults.py [--dry-run] [v9 root] [workers]
"""
import collections, datetime, json, os, sys
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1'); os.environ.setdefault('OMP_NUM_THREADS', '1')
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
DRY = '--dry-run' in sys.argv; args = [a for a in sys.argv[1:] if a != '--dry-run']
V9 = Path(args[0]) if args else Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/merit-cache-workbench-full-v9')
WORKERS = int(args[1]) if len(args) > 1 else 32
sys.path.insert(0, os.environ.get('MERIT_CODE', str(HERE.parent / 'v8_layout')))
from merit import separability_v9 as sp
NEW = {'subjects_confirmed', 'usable_features'}; NEW_DISC = {'adjusted', 'top'}


def one(path):
    v = json.loads(Path(path).read_text()); c = collections.Counter(); problems = []
    for a in v['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            ml = ep['sections']['ml']; old = ml['default']
            new = sp.compute(ep['roster'], v['study_id'], V9 / ml['store'], ml['n_features'], conflicts=ep.get('sample_table_conflicts'))
            other = sorted(k for k in set(old) | set(new) if k not in NEW | {'discriminative'} and old.get(k) != new.get(k))
            od, nd = old.get('discriminative'), new.get('discriminative')
            if (od is None) != (nd is None) or (od and any(od.get(k) != nd.get(k) for k in set(od) | set(nd) if k not in NEW_DISC)):
                other.append('discriminative')
            if other:
                problems.append((v['study_id'], a['analysis_id'], src, other)); continue
            ml['default'] = new
            c[f'{src}: matrices'] += 1; c[f'{src}: with named top features'] += bool((nd or {}).get('top'))
    if not problems and not DRY:
        Path(path).write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':')))
    return c, problems


if __name__ == '__main__':
    files = sorted((V9 / 'json').glob('*_v9.json')); total = collections.Counter(); problems = []; done = 0
    with Pool(WORKERS) as pool:
        for c, p in pool.imap_unordered(one, files, chunksize=2):
            total.update(c); problems += p; done += 1
            if done % 50 == 0 or done == len(files):
                (V9 / ('ml_refresh_dryrun_progress.json' if DRY else 'ml_refresh_progress.json')).write_text(json.dumps({'studies_done': done, 'studies': len(files), 'problems': len(problems),
                    'matrices_done': sum(x for k, x in total.items() if k.endswith(': matrices')), 'updated': datetime.datetime.now().isoformat(timespec='seconds')}, indent=1))
    for p in problems[:20]: print('PROBLEM', p)
    for k, x in sorted(total.items()): print(f'{x:8,}  {k}')
    if problems:
        sys.exit(f'{len(problems)} problems (those study files were left unchanged); manifest not updated')
    if DRY:
        sys.exit('dry run: no difference other than the added fields; nothing written')
    m = json.loads((V9 / 'manifest.json').read_text())
    m['sections']['ml'].update({'refreshed_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'refreshed_by': str(Path(__file__).resolve()),
                                'refresh_note': 'defaults recomputed from the stored distances and ranks: named top features (at most 3 significant, smallest p) and subject fields added; every other field unchanged'})
    (V9 / 'manifest.json').write_text(json.dumps(m, indent=1))
    import subprocess                              # keep manifest.json and index.json equal to the records (manifest_sync.py)
    subprocess.run([sys.executable, str(HERE / 'manifest_sync.py'), '--write'], check=True)
