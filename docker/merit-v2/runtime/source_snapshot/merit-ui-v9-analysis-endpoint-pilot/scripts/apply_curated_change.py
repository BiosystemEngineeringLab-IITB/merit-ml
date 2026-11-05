"""Apply a new or changed row of merit/curated_control_corrections.tsv to the v9 cache of the named studies.

For every position of every valid matrix of each study: roster field qc_blank = (merit/qc_blank_v9.sample_kind is not 'sample').
Every flag that changes must be decided by a curated row (otherwise nothing is written); the curated list of the matrix is rebuilt
and a matrix whose measured flags change gets its Structural record recomputed from the roster, as build_v9_cache.py does for a
curated correction. Then run add_qc_blank_controls_from_rosters.py, rebuild_sample_metrics_for_studies.py (both modes) and
add_qc_reproducibility_from_deposit.py.
usage: apply_curated_change.py ST000825 [...] [--write]
"""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(HERE))
from merit import qc_blank_v9 as qb, structural_v9 as sv
from build_v9_cache import DEPENDS_ON_QC_FLAG
from apply_sample_table_rule import curated_list
V9 = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-cache-workbench-full-v9')
WRITE = '--write' in sys.argv
studies = [a.upper() for a in sys.argv[1:] if a.upper().startswith('ST')]
for st in studies:
    path = V9 / 'json' / f'{st.lower()}_v9.json'; v = json.loads(path.read_text()); report = []
    for a in v['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            changed = []
            for r in ep['roster']:
                kind, row = qb._decision(st, r[0], qb.rule_factors(r))
                new = int(kind != 'sample')
                if new != r[2]:
                    if row is None:
                        sys.exit(f'{st} {a["analysis_id"]} {src} {r[0]}: flag changes without a curated row; nothing written')
                    changed.append((r[0], r[2], new, row['decision'])); r[2] = new
            cur = curated_list(st, ep['roster'])
            if cur: ep['curated_corrections'] = cur
            else: ep.pop('curated_corrections', None)
            measured = [c for c in changed if not next(r for r in ep['roster'] if r[0] == c[0])[sv.I_ALIGN]]
            if measured:
                rec = sv.compute(ep['roster'], sample_table_lines=ep.get('sample_table_lines'))
                sec = ep['sections']['structural']; sec['score'] = rec['structural']; sec['items'] = {k: rec[k] for k in sv.ITEM_KEYS}
                ev = sec['evidence']
                for k in DEPENDS_ON_QC_FLAG: ev.pop(k, None)
                for k in list(ev):
                    if k in rec and not isinstance(rec[k], (list, dict)): ev[k] = rec[k]
                ev['curated_correction_note'] = (f'{len(cur)} positions changed QC/blank status after a curated correction; evidence that depends on '
                                                 'which positions are QC/blank was recomputed from the roster or left out')
            if changed:
                report.append((a['analysis_id'], src, len(changed), len(measured), sorted({c[3] for c in changed})))
    for x in report: print(st, *x)
    if WRITE and report:
        path.write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':')))
print('written' if WRITE else 'dry run: nothing written')
if WRITE:
    import subprocess                              # keep manifest.json and index.json equal to the records (manifest_sync.py)
    subprocess.run([sys.executable, str(HERE / 'manifest_sync.py'), '--write'], check=True)
