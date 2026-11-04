"""Fill Label Structure and Class Support (default class definition: the whole factor string, as deposited) in the v9 cache.

Per valid matrix: merit/labels_v9.compute(roster, study, no sample edits, default classes, conflicts) -> endpoints[*].sections.labels.
The label of each sample is already in the roster (its first sample-table line, else the label on the position). The sample table of
each analysis (mwTab text file of the frozen deposit) is read again for one thing only: the sample IDs whose lines give different factor
strings -> endpoints[*].sample_table_conflicts (written only where there are some; since 2026-10-23). The page recomputes from the
roster for sample edits and for a class choice.
usage: add_labels_from_rosters.py [v9 root]
"""
import collections, datetime, json, sys
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/merit-cache-workbench-full-v9')
DUMP = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/mw-dump-latest-18thSep-2026-version')
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
from merit import labels_v9 as lb
from merit.manuscript_matrix_readers import _lines


def one(path):
    v = json.loads(Path(path).read_text()); c = collections.Counter()
    for a in v['analyses']:
        mw = DUMP / v['study_id'] / a['analysis_id'] / 'mwtab' / f"{a['analysis_id']}_mwtab.txt"
        conflicts = lb.sample_table_conflicts(_lines(mw)) if mw.is_file() else {}
        for src, ep in a['endpoints'].items():
            if 'roster' in ep:
                ids = {row[0] for row in ep['roster']}
                mine = {k: x for k, x in conflicts.items() if k in ids}
                if mine:
                    ep['sample_table_conflicts'] = mine
                else:
                    ep.pop('sample_table_conflicts', None)
                r = lb.compute(ep['roster'], v['study_id'], conflicts=mine or None)
                c[f'{src}: sample IDs with conflicting sample-table lines'] += len(mine)
                ep.setdefault('sections', {})['labels'] = r
                c[f'{src}: matrices'] += 1; c[f'{src}: scored'] += r['class_support'] is not None
                c[f'{src}: cross-validation feasible (5 folds)'] += r['cv_feasible'] == 100.0
    Path(path).write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':')))
    return c


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    files = sorted((V9 / 'json').glob('*_v9.json')); total = collections.Counter()
    with Pool(48) as pool:
        for c in pool.imap_unordered(one, files, chunksize=8):
            total.update(c)
    m = json.loads((V9 / 'manifest.json').read_text())
    m['sections']['labels'] = {'status': 'filled', 'scope': 'analysis x endpoint (shown one row per analysis)', 'default_classes': 'whole factor string, as deposited',
                               'scored': ['class_support', 'cv_feasible'], 'rules': 'class support = 100 x H(smallest vs largest class) x min(1, smallest / k), H the binary entropy (since 2026-10-24; was smallest / largest), one class = 0; cross-validation feasible = smallest class >= k; k = 5 folds (manuscript G4 pass rule); label entropy removed 2026-10-23',
                               'computed_by': 'merit/labels_v9.py', 'computed_at': started, 'script': str(Path(__file__).resolve()), 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(m, indent=1))
    for k, v in sorted(total.items()): print(f'{v:8,}  {k}')
