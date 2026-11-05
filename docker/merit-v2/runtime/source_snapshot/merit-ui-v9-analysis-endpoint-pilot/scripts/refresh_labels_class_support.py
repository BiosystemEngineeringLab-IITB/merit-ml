"""Recompute the stored Label Structure defaults (endpoints[*].sections.labels) with the current merit/labels_v9.py, from the roster and
the stored sample-table conflicts (the sample tables are not read again). Written 2026-10-24 for the class-balance term change (entropy of
the smallest vs the largest class, was smallest / largest): every field other than class_support and balance must come out unchanged,
and any other difference is reported as a problem and the manifest is not updated.
Progress: <v9 root>/labels_refresh_progress.json, rewritten after every study.   usage: refresh_labels_class_support.py [v9 root] [workers]
"""
import collections, datetime, json, sys
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-cache-workbench-full-v9')
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 40
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
from merit import labels_v9 as lb
CHANGING = {'class_support', 'balance'}


def one(path):
    v = json.loads(Path(path).read_text()); c = collections.Counter(); problems = []
    for a in v['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            old = (ep.get('sections') or {}).get('labels') or {}
            new = lb.compute(ep['roster'], v['study_id'], conflicts=ep.get('sample_table_conflicts'))
            other = sorted(k for k in set(old) | set(new) if k not in CHANGING and old.get(k) != new.get(k))
            if other:
                problems.append((v['study_id'], a['analysis_id'], src, other)); continue
            ep['sections']['labels'] = new
            c[f'{src}: matrices'] += 1; c[f'{src}: scored'] += new['class_support'] is not None
            c[f'{src}: class support changed'] += old.get('class_support') != new['class_support']
    if not problems:
        Path(path).write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':')))
    return c, problems


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    files = sorted((V9 / 'json').glob('*_v9.json')); total = collections.Counter(); problems = []; done = 0
    with Pool(WORKERS) as pool:
        for c, p in pool.imap_unordered(one, files, chunksize=4):
            total.update(c); problems += p; done += 1
            if done % 50 == 0 or done == len(files):
                (V9 / 'labels_refresh_progress.json').write_text(json.dumps({'studies_done': done, 'studies': len(files), 'problems': len(problems),
                    'matrices_done': sum(x for k, x in total.items() if k.endswith(': matrices')), 'updated': datetime.datetime.now().isoformat(timespec='seconds')}, indent=1))
    for p in problems[:20]: print('PROBLEM', p)
    if problems:
        sys.exit(f'{len(problems)} problems (study files with a problem were left unchanged); manifest not updated')
    m = json.loads((V9 / 'manifest.json').read_text())
    m['sections']['labels'].update({'rules': 'class support = 100 x H(smallest vs largest class) x min(1, smallest / k), H the binary entropy (balance term since 2026-10-24; was smallest / largest), one class = 0; cross-validation feasible = smallest class >= k; k = 5 folds (manuscript G4 pass rule); label entropy removed 2026-10-23',
                                    'refreshed_at': started, 'refreshed_by': str(Path(__file__).resolve())})
    (V9 / 'manifest.json').write_text(json.dumps(m, indent=1))
    for k, x in sorted(total.items()): print(f'{x:8,}  {k}')
    import subprocess                              # keep manifest.json and index.json equal to the records (manifest_sync.py)
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / 'manifest_sync.py'), '--write'], check=True)
