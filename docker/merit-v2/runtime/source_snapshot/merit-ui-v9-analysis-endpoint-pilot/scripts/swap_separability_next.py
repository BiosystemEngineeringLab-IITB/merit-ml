"""Swap the rebuilt ML Task Readiness records in (2026-10-24): <v9>/separability -> separability_prev_<BACKUP> (kept for rollback; BACKUP = argument 1, default 20261023),
separability_next -> separability, and in every study file and the manifest sections.ml_next -> sections.ml (store paths rewritten).
Run with the interface stopped."""
import json, sys
from multiprocessing import Pool
from pathlib import Path
V9 = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot/merit-cache-workbench-full-v9')


def one(path):
    v = json.loads(Path(path).read_text()); n = 0
    for a in v['analyses']:
        for ep in a['endpoints'].values():
            sec = ep.get('sections') or {}
            if 'ml_next' in sec:
                nxt = sec.pop('ml_next'); nxt['store'] = nxt['store'].replace('separability_next/', 'separability/', 1)
                sec['ml'] = nxt; n += 1
    Path(path).write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':')))
    return n


if __name__ == '__main__':
    prev = V9 / f"separability_prev_{sys.argv[1] if len(sys.argv) > 1 else '20261023'}"
    assert (V9 / 'separability_next').is_dir() and not prev.exists()
    if (V9 / 'separability').is_dir():           # absent on a fresh cache
        (V9 / 'separability').rename(prev)
    (V9 / 'separability_next').rename(V9 / 'separability')
    with Pool(40) as pool:
        n = sum(pool.map(one, sorted((V9 / 'json').glob('*_v9.json')), chunksize=8))
    m = json.loads((V9 / 'manifest.json').read_text()); m['sections']['ml'] = m['sections'].pop('ml_next'); (V9 / 'manifest.json').write_text(json.dumps(m, indent=1))
    print('records swapped:', n)
    import subprocess                              # keep manifest.json and index.json equal to the records (manifest_sync.py)
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / 'manifest_sync.py'), '--write'], check=True)
