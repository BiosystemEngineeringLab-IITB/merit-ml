"""Add to every valid matrix of the v9 cache how many of its feature names are found in the repository's metabolite
records of the same analysis: (a) under exactly the same spelling (lower case, single spaces), and (b) also when a
name that is not found is tried again with '_' read as ','. Kept as evidence for the Annotation section (endpoints[*].annotation_evidence); not scored.

Some mwTab data blocks write '_' where the repository's records (and the METABOLITES block of the same file) write ','.
A record counts when it gives exactly one RefMet name that is not blank, '-', 'Unknown', 'Standard' or 'Protein'.
Reads the frozen dump only. usage: add_refmet_lookup_diagnostic.py [v9 root]
"""
import sys, json, collections
from pathlib import Path
from multiprocessing import Pool
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v8_layout'))
from merit.manuscript_matrix_readers import _lines, _mwtab_layout
from merit import manuscript_feature_labels as ml
DUMP = B / 'mw-dump-latest-18thSep-2026-version'
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
NOT_A_NAME = {'', '-', 'unknown', 'standard', 'protein'}

def one(path):
    v9 = json.loads(path.read_text()); sid = v9['study_id']
    exact = collections.defaultdict(set); relaxed = collections.defaultdict(set)
    rec = DUMP / sid / 'metabolites.json'
    if rec.is_file():
        for r in ml.load_records(rec):
            name = ml.key(str(r.get('metabolite_name', ''))); refmet = str(r.get('refmet_name', '')).strip()
            exact[(r.get('analysis_id', ''), name)].add(refmet); relaxed[(r.get('analysis_id', ''), name.replace('_', ','))].add(refmet)
    ok = lambda m: bool(m) and len(m) == 1 and next(iter(m)).lower() not in NOT_A_NAME
    n = gained = 0
    for a in v9['analyses']:
        aid = a['analysis_id']
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep: continue
            f = DUMP / ep['file']
            if src == 'mwtab':
                names = [x[0].strip() for x in _mwtab_layout(f)[3]]
            else:
                lines = _lines(f)
                while lines and not lines[0].strip(): lines.pop(0)
                names = [h.strip() for h in lines[0].split('\t')[2:] if h.strip()]
            e = l = 0
            for x in names:
                k = ml.key(x)
                if ok(exact.get((aid, k))): e += 1; l += 1
                elif ok(relaxed.get((aid, k.replace('_', ',')))): l += 1
            ep.setdefault('annotation_evidence', {})['feature_name_lookup_in_repository_records'] = {
                'feature_names': len(names), 'found_with_the_same_spelling': e, 'found_when_underscore_is_read_as_comma': l}
            n += 1; gained += l > e
    path.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return n, gained

if __name__ == '__main__':
    with Pool(40) as pool: res = pool.map(one, sorted((V9 / 'json').glob('*_v9.json')), chunksize=8)
    print('matrices', sum(r[0] for r in res), '| matrices where more names are found when "_" is read as ",":', sum(r[1] for r in res))
