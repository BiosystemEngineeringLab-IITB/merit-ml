"""Fill the Annotation tab (named-feature coverage, RefMet mapping, metabolite classes, repeated feature names) in the v9 cache.

Per valid matrix (analysis x endpoint): merit/annotation_v9.analyse on the deposited file the manuscript read (frozen dump), with
the manuscript's classifier unchanged. Writes endpoints[*].sections.annotation (counts, scores, up to 5 examples) and the
per-feature lists for the download in <v9 root>/annotation/<ST>/<AN>_<endpoint>.json.gz. Nothing depends on samples.
usage: add_annotation_from_deposit.py [v9 root] [workers]
"""
import collections, datetime, gzip, json, sys
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
from merit import annotation_v9 as an, manuscript_feature_labels as fl


def one(path):
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']; c = collections.Counter(); problems = []
    ctx = fl.study_context(an.DUMP / sid); lookup = an.refmet_lookup(sid)
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            try:
                r = an.analyse(sid, a['analysis_id'], src, ctx, lookup)
            except Exception as e:                       # noqa: BLE001 - reported, nothing written for the study
                problems.append(((sid, a['analysis_id'], src), repr(e))); continue
            lists = r.pop('lists')
            rel = Path('annotation') / sid / f'{a["analysis_id"]}_{src}.json.gz'
            (V9 / rel).parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(V9 / rel, 'wt', encoding='utf-8') as fh:
                json.dump(lists, fh, ensure_ascii=False)
            r['file'] = str(rel)
            ep.setdefault('sections', {})['annotation'] = r
            c[f'{src}: matrices'] += 1
            c[f'{src}: coverage scored'] += r['coverage']['score'] is not None
            c[f'{src}: RefMet mapping scored'] += r['refmet']['score'] is not None
            c[f'{src}: with repeated names'] += r['repeats']['names'] > 0
    if not problems:
        Path(path).write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return c, problems


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    an.refmet_classes()
    files = sorted((V9 / 'json').glob('*_v9.json')); total = collections.Counter(); problems = []; done = 0
    with Pool(WORKERS) as pool:
        for c, p in pool.imap_unordered(one, files, chunksize=2):
            total.update(c); problems += p; done += 1
            if done % 100 == 0 or done == len(files):
                (V9 / 'annotation_progress.json').write_text(json.dumps({'studies_done': done, 'studies': len(files),
                                                                         'updated': datetime.datetime.now().isoformat(timespec='seconds')}))
    if problems:
        for p in problems[:20]: print('PROBLEM', p)
        sys.exit(f'{len(problems)} problems; those studies and the manifest were not updated')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    manifest['sections']['annotation'] = {
        'status': 'filled', 'scope': 'analysis x endpoint', 'depends_on_samples': False,
        'scored': ['named-feature coverage', 'RefMet mapping'], 'informational': ['metabolite classes', 'repeated feature names'],
        'computed_by': 'merit/annotation_v9.py (classifier: merit/manuscript_feature_labels.py, Text S3)', 'computed_at': started,
        'script': str(Path(__file__).resolve()), 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k, v in sorted(total.items()): print(f'{v:8,}  {k}')
