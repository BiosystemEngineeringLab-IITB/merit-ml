"""One-off (2026-10-23): rewrite the subject counts of every stored ML default record with separability_v9.subjects (plain counts:
distinct subject IDs, the most shared one). Nothing else in the record changes."""
import json, sys
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
V9 = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/merit-cache-workbench-full-v9')
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
from merit import separability_v9 as sp, labels_v9 as lb
KEYS = ('subjects', 'sample_ids_with_subject', 'subjects_with_several_samples', 'subjects_in_several_classes', 'most_shared_subject')


def one(path):
    v = json.loads(Path(path).read_text()); n = 0
    for a in v['analyses']:
        for ep in a['endpoints'].values():
            ml = (ep.get('sections') or {}).get('ml')
            if not ml:
                continue
            assigned = lb.sample_classes(ep['roster'], v['study_id'], None, None, ep.get('sample_table_conflicts'))
            ids = [s for s, (_, i) in assigned.items() if not ep['roster'][i][3]]
            d = ml['default']
            for k in KEYS: d.pop(k, None)
            d.update(sp.subjects(ep['roster'], ids, [assigned[s][1] for s in ids], [assigned[s][0] for s in ids]))
            n += 1
    Path(path).write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':')))
    return n


if __name__ == '__main__':
    with Pool(40) as pool:
        print('records refreshed:', sum(pool.map(one, sorted((V9 / 'json').glob('*_v9.json')), chunksize=8)))
