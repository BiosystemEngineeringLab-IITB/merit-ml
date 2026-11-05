"""Fill the metric "Sample-level missingness" of the Analytical QC section of the v9 cache (the first scored metric).

Per valid matrix (analysis x endpoint):
  endpoints[*].cell_counts   per position, in roster order: the six Table S2C classes of non-numeric entries and the zero
                             cells (merit/cell_counts_v9.py: the manuscript's reading and classifier); the numeric cells stay in the roster
  endpoints[*].sections.analytical.sample_missingness = merit/missingness_v9.py::compute (no sample edits): four items, each a share of all
                             cells of the ML-eligible samples; score = the lowest item
  datatable only: sections.analytical.datatable_zero_provenance = the matrix's row of the manuscript's zero-origin table
                             (outputs/table4_descriptors_20260927/zero_origin_per_analysis.tsv; Text SX, Table SX), copied
Checks while building (any difference stops the run): the positions and sample IDs equal the roster, the numeric cells of every
position equal the roster, and the non-numeric cells of every position equal the roster's missing-entry + text cells.
Reads the frozen deposit again (the rosters do not hold zeros). Run after add_qc_blank_controls_from_rosters.py. About 2 minutes.
usage: add_missingness_from_deposit.py [v9 root]
"""
import collections, csv, datetime, json, sys
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
ZO = B / 'outputs/table4_descriptors_20260927/zero_origin_per_analysis.tsv'
PROV = B / 'merit/manuscript/supplementary/sample_missingness_scoring/datatable_zero_provenance_per_sample.tsv.gz'
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/structural_items_v9_claude_20261011/scripts'))
from merit import cell_counts_v9 as cc, missingness_v9 as mv, structural_v9 as sv
from matrix_paths import matrix_path
ZERO_FIELDS = ('cells', 'datatable_zero_cells', 'padding_rows', 'padding', 'reported_zero', 'value_to_zero', 'filled_missing', 'filled_empty',
               'filled_token', 'filled_other_text', 'unattributed', 'filled_missing_nonzero', 'sample_relation')
_zo = None
_prov = None


def provenance():
    """{(study, analysis): {sample ID: [counts in mv.PROVENANCE order]}} for every paired datatable (an empty dict = paired, nothing traced)."""
    global _prov
    if _prov is None:
        import gzip
        _prov = {k: {} for k in zero_origin()}
        with gzip.open(PROV, 'rt', newline='') as fh:
            for r in csv.DictReader(fh, delimiter='\t'):
                _prov[(r['study_id'], r['analysis_id'])][r['sample_id']] = [int(r[k]) for k in mv.PROVENANCE]
    return _prov


def zero_origin():
    global _zo
    if _zo is None:
        _zo = {(r['study_id'], r['analysis_id']): r for r in csv.DictReader(open(ZO, newline=''), delimiter='\t')}
    return _zo


def one(path):
    v9 = json.loads(path.read_text()); sid = v9['study_id']; c = collections.Counter(); problems = []
    for a in v9['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep: continue
            file, _ = matrix_path(sid, a['analysis_id'], src)
            pos, n_features = cc.counts_of(file, src)
            ro = ep['roster']
            if [p[0] for p in pos] != [r[sv.I_ID] for r in ro] or n_features != ep['n_features']:
                problems.append((sid, a['analysis_id'], src, 'positions or features differ from the roster')); continue
            for (i, n), r in zip(pos, ro):
                if n[0] != r[sv.I_NUM] or sum(n[1:7]) != r[sv.I_MISS] + r[sv.I_TEXT] or n[0] + sum(n[1:7]) != n_features:
                    problems.append((sid, a['analysis_id'], src, i, 'cell counts differ from the roster', n, r[7:])); break
            ep['cell_counts'] = [n[1:] for _, n in pos]
            pv = None
            if src == 'datatable' and (sid, a['analysis_id']) in provenance():
                per = provenance()[(sid, a['analysis_id'])]
                dup = collections.Counter(r[sv.I_ID] for r in ro)
                # a traced zero belongs to a unique sample ID (the pairing skips repeated IDs)
                pv = [per.get(r[sv.I_ID], [0] * len(mv.PROVENANCE)) if dup[r[sv.I_ID]] == 1 else [0] * len(mv.PROVENANCE) for r in ro]
                if sum(map(sum, pv)) != sum(map(sum, per.values())):   # every traced zero (one-to-one and resolved) placed
                    problems.append((sid, a['analysis_id'], src, 'traced zeros not all placed on a roster position')); continue
                ep['zero_provenance'] = pv
            else:
                ep.pop('zero_provenance', None)
            rec = mv.compute(ro, ep['cell_counts'], n_features, None, pv)
            an = ep.setdefault('sections', {}).setdefault('analytical', {})
            an['sample_missingness'] = rec
            if src == 'datatable':
                z = zero_origin().get((sid, a['analysis_id']))
                an['datatable_zero_provenance'] = {k: (z[k] if k == 'sample_relation' else int(z[k] or 0)) for k in ZERO_FIELDS} if z else None
            c['matrices'] += 1; c[f'{src}: matrices'] += 1; c[f'{src}: scored'] += rec['score'] is not None
            c[f'{src}: paired with mwTab (items I3, I4)'] += pv is not None
            c[f'{src}: datatable zero provenance'] += bool(src == 'datatable' and an.get('datatable_zero_provenance'))
    path.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return c, problems


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    total = collections.Counter(); problems = []
    with Pool(40) as pool:
        for c, p in pool.map(one, sorted((V9 / 'json').glob('*_v9.json')), chunksize=8):
            total.update(c); problems += p
    if problems:
        for p in problems[:20]: print('PROBLEM', p)
        sys.exit(f'{len(problems)} problems; manifest not updated')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    section = manifest['sections'].setdefault('analytical', {})
    section['status'] = 'partly filled: two informational blocks, sample eligibility & analytical controls, sample-level missingness; other metrics not filled yet'
    section['sample_missingness'] = {
        'status': 'filled', 'scored': True, 'scope': 'analysis x endpoint: one record per valid matrix',
        'definition': 'four items on the ML-eligible samples, each 100 x (1 - share), share = the item cells / all cells of the ML-eligible samples: '
                      'I1 invalid entries (empty or non-numeric), I2 zeros of unknown meaning, I3 datatable zeros hiding a missing value and '
                      'I4 datatable zeros replacing a value (I3, I4: datatables paired with mwTab); score = the lowest item. Zeros are never counted as '
                      'invalid entries. The median sample (items 1-2) is kept as supporting information. Samples with no numeric value, or whose numeric '
                      'values are all 0 (other cells may be missing), are counted.',
        'reasoning': 'merit/manuscript/supplementary/sample_missingness_scoring/sample_missingness_scoring.md',
        'datatable_zero_provenance_per_sample': str(PROV),
        'cell_count_fields': list(cc.FIELDS[1:]), 'computed_by': 'merit/cell_counts_v9.py::counts_of, merit/missingness_v9.py::compute',
        'datatable_zero_provenance_from': str(ZO), 'computed_at': started, 'script': str(Path(__file__).resolve()), 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k, v in sorted(total.items()): print(f'{v:9,}  {k}')
