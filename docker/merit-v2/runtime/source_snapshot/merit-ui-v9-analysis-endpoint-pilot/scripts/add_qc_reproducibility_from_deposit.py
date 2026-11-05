"""Fill "QC reproducibility" (Sample eligibility & analytical controls, informational; Reviewer 1, comment 3) in the v9 cache.

Per valid matrix: the QC-like positions as deposited (merit/qc_blank_v9.position_kinds, curated corrections included); the units
gate of merit/qc_reproducibility_v9.gate (declared units family: concentration or amount; intensity, area or counts; normalised,
relative or ratio; and no negative measured value, read from the matrix; since 2026-10-21, before: the Low-information CV gate,
which leaves out normalised units). For the two linear families the negative-value test must equal the CV gate's. When the largest QC material has
>= 3 injections and the gate passes, the values of every QC-like position (float32) and the feature names are stored in
<v9 root>/qc_reproducibility/<ST>/<AN>_<endpoint>.npz; endpoints[*].sections.analytical.qc_reproducibility = {gate, gate_reason, file,
default}. Sample edits can only remove QC-like positions (a QC made ML-eligible), so the stored positions cover every edit.
usage: add_qc_reproducibility_from_deposit.py [v9 root] [workers]
"""
import collections, datetime, json, os, sys
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
from multiprocessing import Pool
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/table4_descriptors_20260927'))
from merit import qc_reproducibility_v9 as qr, qc_blank_v9 as qb, structural_v9 as sv
import table4_descriptors as t4
t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_feats': list(feats), '_mat': mat}


def one(path):
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']; c = collections.Counter(); problems = []
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            an = ep.setdefault('sections', {}).setdefault('analytical', {})
            cvg = (an.get('low_information') or {}).get('cv_gate') or {}
            fam = (an.get('measurement_scale') or {}).get('units', {}).get('family', 'Not declared')
            name, pos, n_qc = qr.qc_group(ep['roster'], None, sid)
            data = None; r = None
            linear = fam in qr.QC_FAMILIES and fam != 'Normalised, relative or ratio'
            if linear and len(pos) < qr.MIN_INJECTIONS:      # as before: the CV gate holds the negative-value test of these
                ok, why = qr.gate(fam, cvg.get('reason') == 'negative values are present')
            elif fam in qr.QC_FAMILIES:                      # read the matrix: negative values among the measured positions
                r = t4.one((sid, a['analysis_id'], src))
                if r.get('error') or r['_sids'] != [row[0] for row in ep['roster']]:
                    problems.append(((sid, a['analysis_id'], src), r.get('error') or 'positions differ from the roster')); continue
                mat = np.asarray(r['_mat'], dtype=float)
                measured = np.array([not row[sv.I_ALIGN] for row in ep['roster']], dtype=bool)
                ok, why = qr.gate(fam, bool(np.isfinite(mat[measured]).any() and np.nanmin(mat[measured]) < 0))
            else:
                ok, why = qr.gate(fam, False)
            if linear and (ok, why) != (bool(cvg.get('checked')), cvg.get('reason', '')):
                problems.append(((sid, a['analysis_id'], src), f'gate differs from the CV gate: {(ok, why)} vs {cvg}')); continue
            st = {'gate': ok, 'gate_reason': why}
            if st['gate'] and len(pos) >= qr.MIN_INJECTIONS:
                kinds = qb.position_kinds(ep['roster'], None, sid)
                allqc = np.array([i for i, k in enumerate(kinds) if k == 'qc'], dtype=np.int64)
                data = {'positions': allqc, 'values': mat[allqc].astype(np.float32), 'names': np.array([str(f) for f in r['_feats']], dtype=str)}
                rel = Path('qc_reproducibility') / sid / f'{a["analysis_id"]}_{src}.npz'
                (V9 / rel).parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(V9 / rel, **data); st['file'] = str(rel)
            st['default'] = qr.compute(ep['roster'], st, data, sid)
            an['qc_reproducibility'] = st
            c[f'{src}: matrices'] += 1; c[f'{src}: shown'] += st['default']['shown']
            c[f'{src}: >= 3 injections of one QC material'] += len(pos) >= qr.MIN_INJECTIONS
    Path(path).write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return c, problems


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    files = sorted((V9 / 'json').glob('*_v9.json')); total = collections.Counter(); problems = []; done = 0
    with Pool(WORKERS) as pool:
        for c, p in pool.imap_unordered(one, files, chunksize=2):
            total.update(c); problems += p; done += 1
            if done % 50 == 0 or done == len(files):
                (V9 / 'qc_reproducibility_progress.json').write_text(json.dumps({'studies_done': done, 'studies': len(files),
                                                                                 'updated': datetime.datetime.now().isoformat(timespec='seconds')}))
    if problems:
        for p in problems[:20]: print('PROBLEM', p)
        sys.exit(f'{len(problems)} problems; manifest not updated')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    manifest['sections'].setdefault('analytical', {})['qc_reproducibility'] = {
        'status': 'filled', 'scored': False, 'rule': 'Broadhurst et al. 2018: detection rate > 70% and RSD < 20% (adjustable 10-40%) across the largest QC-like material (>= 3 injections); declared units concentration, intensity/area/counts or normalised/relative/ratio (since 2026-10-21), no negative values',
        'computed_by': 'merit/qc_reproducibility_v9.py', 'computed_at': started, 'script': str(Path(__file__).resolve()), 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k, v in sorted(total.items()): print(f'{v:8,}  {k}')
