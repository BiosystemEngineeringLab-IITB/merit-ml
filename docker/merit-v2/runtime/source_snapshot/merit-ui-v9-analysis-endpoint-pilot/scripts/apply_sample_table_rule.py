"""Apply the per-sample-ID rule of merit/qc_blank_v9.sample_kind (decided 2026-10-21) to the v9 cache rosters.

One kind per sample ID from its line in the sample table of the analysis (SUBJECT_SAMPLE_FACTORS of the mwTab text file; the label
on the position only for a sample ID without a line there), the 16 keywords with the sample ID deciding first, and the curated table.
For every position of every valid matrix: roster field qc_blank = (kind is not 'sample'). Where this differs from the stored flag
(the manuscript rule on the label on the position, plus the curated table), the change is listed in
endpoints[*].sample_table_rule_changes with the label on the position and the sample-table factors; the curated list is rebuilt
with the same factors; a matrix whose measured flags change gets its Structural record recomputed from the roster.
Expected (measured positions): exactly the 14 changes listed below; anything else stops the run before anything is written.
usage: apply_sample_table_rule.py [--write] [v9 root]
"""
import collections, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
WRITE = '--write' in sys.argv
args = [a for a in sys.argv[1:] if a != '--write']
V9 = Path(args[0]) if args else B / 'merit-cache-workbench-full-v9'
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
sys.path.insert(0, str(HERE))
from merit import qc_blank_v9 as qb, structural_v9 as sv
from merit.curated_corrections import describe
from build_v9_cache import DEPENDS_ON_QC_FLAG

EXPECTED = {('ST001095', 'AN001781', 'untarg_data', '12C-AT-LeafAndStem'), ('ST001095', 'AN001781', 'untarg_data', '12C-AT-Root'),
            ('ST001095', 'AN001782', 'untarg_data', '12C-AT-LeafAndStem'), ('ST001095', 'AN001782', 'untarg_data', '12C-AT-Root'),
            ('ST002402', 'AN003915', 'untarg_data', '220826_pos PLs ctrl_4')} | {('ST003477', 'AN005712', 'datatable', f'plate_{i}') for i in range(1, 10)}


def curated_list(study_id, roster):
    out = []
    for r in roster:
        kind, row = qb._decision(study_id, r[0], qb.rule_factors(r))
        if row is not None:
            out.append({'sample_id': r[0], 'label_on_position': r[1], 'matched': describe(row), 'decision': row['decision'],
                        'reason': row['reason'], 'decided_by': row['decided_by'], 'decided_on': row['decided_on']})
    return out


def one(path):
    v = json.loads(Path(path).read_text()); st = v['study_id']; changes = []; tier1 = 0; curated_changed = []; touched = False
    for a in v['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            listed = []
            for r in ep['roster']:
                new = int(qb.sample_kind(st, r[0], qb.rule_factors(r)) != 'sample')
                if new != r[2]:
                    if r[3]:
                        tier1 += 1
                    else:
                        changes.append((st, a['analysis_id'], src, r[0]))
                        listed.append({'sample_id': r[0], 'label_on_position': r[1], 'sample_table_factors': r[5] if r[4] else None,
                                       'qc_blank_before': r[2], 'qc_blank_now': new, 'kind_now': qb.sample_kind(st, r[0], qb.rule_factors(r))})
                    r[2] = new; touched = True
            cur = curated_list(st, ep['roster'])
            if cur != (ep.get('curated_corrections') or []):
                curated_changed.append((st, a['analysis_id'], src)); touched = True
                if cur: ep['curated_corrections'] = cur
                else: ep.pop('curated_corrections', None)
            if listed:
                ep['sample_table_rule_changes'] = listed
                rec = sv.compute(ep['roster'], sample_table_lines=ep.get('sample_table_lines'))
                sec = ep['sections']['structural']
                sec['score'] = rec['structural']; sec['items'] = {k: rec[k] for k in sv.ITEM_KEYS}
                ev = sec['evidence']
                for k in DEPENDS_ON_QC_FLAG:
                    ev.pop(k, None)
                for k in list(ev):
                    if k in rec and not isinstance(rec[k], (list, dict)):
                        ev[k] = rec[k]
                ev['sample_table_rule_note'] = (f'{len(listed)} positions changed ML eligibility under the sample-table rule (2026-10-21); '
                                                'counts recomputed from the roster, evidence that depends on the QC/blank flag left out')
    if touched and WRITE:
        Path(path).write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':')))
    return changes, tier1, curated_changed


if __name__ == '__main__':
    from multiprocessing import Pool
    files = sorted((V9 / 'json').glob('*_v9.json'))
    if WRITE:     # a dry run must have passed: compute first without writing, then write
        WRITE = False
        with Pool(40) as pool: res = pool.map(one, files, chunksize=8)
        if {c for ch, _, _ in res for c in ch} != EXPECTED:
            sys.exit('measured-position changes differ from the expected 14; nothing written')
        WRITE = True
    with Pool(40) as pool: res = pool.map(one, files, chunksize=8)
    ch = {c for x, _, _ in res for c in x}; t1 = sum(t for _, t, _ in res); cur = [c for _, _, x in res for c in x]
    print(f'measured positions changed: {len(ch)} (expected 14: {ch == EXPECTED}); Tier 1 rows whose flag changed: {t1}; matrices whose curated list changed: {len(cur)} {cur[:6]}')
    print('studies:', sorted({c[0] for c in ch}))
    print('written' if WRITE else 'dry run: nothing written')
    if WRITE:
        import subprocess                          # keep manifest.json and index.json equal to the records (manifest_sync.py)
        subprocess.run([sys.executable, str(HERE / 'manifest_sync.py'), '--write'], check=True)
