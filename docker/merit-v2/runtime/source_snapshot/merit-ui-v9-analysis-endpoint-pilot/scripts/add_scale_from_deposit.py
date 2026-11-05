"""Fill the metric "Measurement scale" of the Analytical QC section of the v9 cache (one scored item: units declared).

Per valid matrix (analysis x endpoint), endpoints[*].sections.analytical.measurement_scale:
  units            the declaration that applies (mwTab and datatable: the mwTab quantitative data block; untarg_data: the
                   results-file line), its family and whether it is valid; score 100 / 0
  deposited scale  median and Q1-Q3 of all non-zero values of the matrix (every position, as Table 4), share negative,
                   5th-95th percentile of the positive values; read with the manuscript's Table 4 readers
  datatable        paired non-zero mwTab numbers shown unchanged / as another number / as 0 (manuscript pairing code)
endpoints[*].sample_medians: per position, the median of its non-zero values (None if it has none), for the sample spread and
sample edits (merit/scale_v9.py::samples).
Declarations and families are the manuscript's per-analysis tables (outputs/measurement_scale_20260930/units_per_analysis.tsv,
untarg_units_per_analysis.tsv; Tables S20b-S20D). Run after add_missingness_from_deposit.py. About 10 minutes.
usage: add_scale_from_deposit.py [v9 root]
"""
import collections, csv, datetime, importlib.util, json, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
MS = B / 'outputs/measurement_scale_20260930'
ZO = B / 'outputs/table4_descriptors_20260927/zero_origin_per_analysis.tsv'
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/table4_descriptors_20260927'))
from merit import scale_v9 as sc
import table4_descriptors as t4
t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_mat': mat}       # hand back the value matrix
_sp = importlib.util.spec_from_file_location('dvm', B / 'outputs/datatable_vs_mwtab_values_20260925/datatable_vs_mwtab_values.py')
dvm = importlib.util.module_from_spec(_sp); _sp.loader.exec_module(dvm)
NOT_DECLARED = 'Not declared'
import re
UNITS_LINE = re.compile(r'^(?!EXTENDED)([A-Z_]+_DATA):UNITS\b')


def data_block_units_text(sid, aid):
    """The UNITS text of every non-EXTENDED quantitative data block of the mwTab text file, exactly as deposited (after the first tab),
    or None when the file has no UNITS line (extract_units.py reads the same line)."""
    path = B / 'mw-dump-latest-18thSep-2026-version' / sid / aid / 'mwtab' / f'{aid}_mwtab.txt'
    if not path.is_file():
        return None
    out = []
    for line in path.read_text(encoding='utf-8', errors='replace').split('\n'):
        if UNITS_LINE.match(line):
            out.append(line.split('\t', 1)[1].strip() if '\t' in line else '')
    return list(dict.fromkeys(out)) or None          # a block repeated byte-identically is one block (the manuscript skips the copy)
_tables = None


def tables():
    global _tables
    if _tables is None:
        U = {(r['study_id'], r['analysis_id']): r for r in csv.DictReader(open(MS / 'units_per_analysis.tsv'), delimiter='\t')}
        W = {(r['study_id'], r['analysis_id']): r for r in csv.DictReader(open(MS / 'untarg_units_per_analysis.tsv'), delimiter='\t')}
        Z = {(r['study_id'], r['analysis_id']) for r in csv.DictReader(open(ZO), delimiter='\t')}
        _tables = (U, W, Z)
    return _tables


def one(path):
    U, W, Z = tables()
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']; c = collections.Counter(); problems = []
    for a in v9['analyses']:
        aid = a['analysis_id']
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep: continue
            r = t4.one((sid, aid, src)); mat = r['_mat']
            if r['_sids'] != [row[0] for row in ep['roster']]:
                problems.append((sid, aid, src, 'positions differ from the roster')); continue
            vals = mat[np.isfinite(mat)] if mat.size else np.array([])
            nz = vals[vals != 0]; posv = vals[vals > 0]
            med_rows = []
            for row in (mat if mat.size else []):
                x = row[np.isfinite(row) & (row != 0)]
                med_rows.append(float(np.median(x)) if x.size else None)
            if not mat.size: med_rows = [None] * len(ep['roster'])
            if src == 'untarg_data':
                w = W.get((sid, aid), {}); raw, fam, where = w.get('units_as_written', ''), w.get('units_family', NOT_DECLARED) or NOT_DECLARED, 'results-file line (MS:MS_RESULTS_FILE / NM:NMR_RESULTS_FILE)'
                deposited = {'text': w.get('units_as_written', ''), 'state': w.get('source_of_declaration', '')}
                other = (U.get((sid, aid), {}).get('units_family') or NOT_DECLARED)
            else:
                u = U.get((sid, aid), {}); raw, fam, where = u.get('units_raw', ''), u.get('units_family', NOT_DECLARED) or NOT_DECLARED, 'mwTab quantitative data block (UNITS)'
                texts = data_block_units_text(sid, aid)
                deposited = {'text': ' | '.join(texts) if texts else '', 'state': 'no UNITS line' if texts is None else ('empty UNITS field' if not any(texts) else 'UNITS line')}
                other = None
            declared = fam != NOT_DECLARED
            rec = {'score': sc.score(declared),
                   'units': {'declared': declared, 'family': fam, 'raw': raw, 'read_from': where, 'deposited': deposited,
                             **({'data_block_family': other} if other is not None else {})},
                   'deposited_scale': {'non_zero_values': int(nz.size),
                                       'median': float(np.median(nz)) if nz.size else None,
                                       'q1': float(np.percentile(nz, 25)) if nz.size else None, 'q3': float(np.percentile(nz, 75)) if nz.size else None,
                                       'pct_negative_of_non_zero': 100 * float((nz < 0).sum()) / nz.size if nz.size else None,
                                       'p05_positive': float(np.percentile(posv, 5)) if posv.size else None,
                                       'p95_positive': float(np.percentile(posv, 95)) if posv.size else None}}
            if src == 'datatable' and (sid, aid) in Z:
                d = dvm.analyse((sid, aid)); g = lambda k: int(d.get(k, 0) or 0)
                rec['datatable_vs_mwtab'] = {'unchanged': g('number->same_as_printed') + g('number->rounded_to_printed_precision'),
                                             'another_number': g('number->different_number'),
                                             'zero': g('number->rounds_to_zero') + g('number->negative_to_zero') + g('number->positive_to_zero')}
            rec['samples'] = sc.samples(ep['roster'], med_rows)
            ep['sample_medians'] = med_rows
            ep.setdefault('sections', {}).setdefault('analytical', {})['measurement_scale'] = rec
            c[f'{src}: matrices'] += 1; c[f'{src}: units declared'] += declared
    path_out = Path(path); path_out.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return c, problems


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    total = collections.Counter(); problems = []
    with Pool(40) as pool:
        for c, p in pool.map(one, sorted((V9 / 'json').glob('*_v9.json')), chunksize=4):
            total.update(c); problems += p
    if problems:
        for p in problems[:20]: print('PROBLEM', p)
        sys.exit(f'{len(problems)} problems; manifest not updated')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    section = manifest['sections'].setdefault('analytical', {})
    section['status'] = 'partly filled: two informational blocks, sample eligibility & analytical controls, sample-level missingness, measurement scale; other metrics not filled yet'
    section['measurement_scale'] = {
        'status': 'filled', 'scored': 'units declared only (100 when a valid UNITS declaration exists, else 0); deposited scale and the rest are information',
        'units_rule': 'valid = non-empty, non-placeholder declaration (Text S15); mwTab and datatable: the mwTab data-block UNITS; untarg_data: the results-file UNITS',
        'computed_by': 'merit/scale_v9.py', 'declarations_from': [str(MS / 'units_per_analysis.tsv'), str(MS / 'untarg_units_per_analysis.tsv')],
        'computed_at': started, 'script': str(Path(__file__).resolve()), 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k, v in sorted(total.items()): print(f'{v:9,}  {k}')
