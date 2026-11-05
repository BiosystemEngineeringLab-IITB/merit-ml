"""Build the v9 cache: one file per study, one record per analysis x endpoint, each section filled as it is finished.

Now filled: Structural. Every valid matrix carries its position roster (sample ID, labels, QC/blank flag, Tier 1 sample
alignment flag, subject ID, and the number of numeric / missing-entry / text value cells of that position), so the four
Structural items can be recomputed for any parameter value or sample edit without reading the deposit again.

Inputs (read only): frozen dump of 18 September 2026; the manuscript validity table; the Structural items table
outputs/structural_items_v9_claude_20261011/structural_items_per_matrix.jsonl (evidence fields and the cross-check).
Output: merit-cache-workbench-full-v9/ (manifest.json, index.json, json/<study>_v9.json, logs/).
usage: build_v9_cache.py [output root]
"""
import csv, sys, re, json, collections, datetime
from pathlib import Path
from multiprocessing import Pool
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'v8_layout')); sys.path.insert(0, str(B / 'outputs/structural_items_v9_claude_20261011/scripts'))
from merit import structural_v9 as sv
from merit.manuscript_sample_rules import is_qc_or_blank, tier1_alignment
from merit.curated_corrections import correction_for, addition_for, describe     # curated decisions (curated_control_corrections.tsv)
from merit.manuscript_matrix_readers import _lines, _mwtab_layout
from value_cell_classes import klass
from matrix_paths import matrix_path, ROOT
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
VALID = B / 'merit/manuscript/figures/Figure-2/Used-in-manuscript/figure2b_source_availability_per_analysis.tsv'
ITEMS = B / 'outputs/structural_items_v9_claude_20261011/structural_items_per_matrix.jsonl'
SOURCES = ('datatable', 'mwtab', 'untarg_data')
DEPENDS_ON_QC_FLAG = ('declared_non_qc_samples', 'declared_samples_measured', 'eligible_with_factor', 'n_with_factor_in_sample_table', 'n_with_factor_on_position',
                      'qc_blank_positions_deposited', 'word_values', 'text_cells_by_class', 'text_examples', 'malformed_exponent_cells', 'malformed_exponent_examples',
                      'ids_agree_with_other_endpoints')
N = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
NUM = re.compile(rf'^{N}$'); ROW = re.compile(rf'^(?: *{N} *\t)* *{N} *$'); REPAIRED = re.compile(r'^[+-]?\d+\.\d+\.[eE][+-]?\d+$')
_class = {}
def cell_class(t):
    """0 number, 1 missing entry, 2 text (same rules as the Structural items table)."""
    if NUM.match(t) or REPAIRED.match(t): return 0
    c = _class.get(t)
    if c is None: c = _class[t] = 1 if klass(t) == 'missing entry' else 2
    return c

def positions_of(path, src):
    """[(sample ID, label on the position, [numeric, missing, text])] in file order, same positions as the manuscript readers."""
    out = []
    if src == 'mwtab':
        columns, samples, labels, frows = _mwtab_layout(path)
        for k, s, l in zip(columns, samples, labels):
            n = [0, 0, 0]
            for r in frows: n[cell_class(r[k + 1].strip() if k + 1 < len(r) else '')] += 1
            out.append((str(s).strip(), str(l), n))
        return out, len(frows)
    lines = _lines(path)
    while lines and not lines[0].strip(): lines.pop(0)
    if not lines or lines[0].count('\t') < 1: return out, 0
    header = lines[0].split('\t'); pos = [k for k in range(2, len(header)) if header[k].strip()]
    contiguous = pos == list(range(2, len(header))); width = len(header)
    for line in lines[1:]:
        if not line.strip(): continue
        m = line.count('\t') + 1
        if m > width: continue
        head = line.split('\t', 2)
        if not head[0].strip(): continue
        if contiguous and m == width and len(head) == 3 and ROW.match(head[2]):
            n = [len(pos), 0, 0]
        else:
            c = line.split('\t'); n = [0, 0, 0]
            for k in pos: n[cell_class(c[k].strip() if k < m else '')] += 1
        out.append((head[0].strip(), head[1] if len(head) > 1 else '', n))
    return out, len(pos)

def one(job):
    sid, arows, items = job
    study = {'schema': 'merit.cache.v9.study', 'study_id': sid, 'roster_fields': list(sv.ROSTER_FIELDS), 'analyses': []}
    problems = []; n_valid = n_scored = 0
    for row in arows:
        aid = row['analysis_id']; mw = ROOT / sid / aid / 'mwtab' / f'{aid}_mwtab.txt'
        ssf = {}
        if mw.is_file():
            for line in _lines(mw):
                if line.startswith('SUBJECT_SAMPLE_FACTORS'):
                    c = line.split('\t')
                    if len(c) > 2 and c[2].strip(): ssf.setdefault(c[2].strip(), (c[1].strip(), c[3].strip() if len(c) > 3 else ''))
        an = {'analysis_id': aid, 'endpoints': {}}
        for src in SOURCES:
            ep = {'availability': row[src]}
            if row[src] == 'valid':
                path, folder = matrix_path(sid, aid, src)
                stored = items.get((aid, src))
                if path is None or stored is None:
                    problems.append((sid, aid, src, 'valid in the manuscript table but no file or no items')); an['endpoints'][src] = ep; continue
                n_valid += 1
                pos, n_features = positions_of(path, src)
                removed = set()
                if src == 'datatable':
                    al = tier1_alignment(path, mw)
                    if al: removed = set(al['all_zero_absent_sample_ids'])
                roster = [[i, l, int(is_qc_or_blank(i, l)), int(i in removed), int(i in ssf), ssf[i][1] if i in ssf else '', ssf[i][0] if i in ssf else '', n[0], n[1], n[2]]
                          for i, l, n in pos]
                # the roster as the manuscript rule gives it must reproduce the items table; curated corrections are applied after that check
                rec = sv.compute(roster, sample_table_lines=len(ssf))
                curated = []
                for r in roster:
                    c = correction_for(sid, r[0], r[1]) if r[2] else addition_for(sid, r[0], r[1], r[5])   # r[5]: the sample's line in the sample table
                    if c:
                        r[2] = 1 if c['decision'] in ('blank', 'standard') else 0
                        curated.append({'sample_id': r[0], 'label_on_position': r[1], 'matched': describe(c), 'decision': c['decision'],
                                        'reason': c['reason'], 'decided_by': c['decided_by'], 'decided_on': c['decided_on']})
                # the roster must reproduce the items table exactly
                for k in ('positions', 'tier1_alignment_rows', 'measured_positions', 'qc_blank_positions', 'eligible_positions', 'distinct_eligible_ids', 'repeated_positions',
                          'positions_with_repeated_id', 'sample_ids_repeated', 'n_with_factor', 'value_cells', 'numeric_cells', 'missing_entry_cells', 'text_cells',
                          'subject_ids_given_for', 'distinct_subject_ids', 'subjects_with_several_sample_ids', 'sample_ids_sharing_a_subject', 'n_scored_items',
                          'repeated_id_examples', 'samples_without_factor_examples', 'subject_examples'):
                    if rec[k] != stored[k]: problems.append((sid, aid, src, k, rec[k], stored[k]))
                for k in sv.ITEM_KEYS + ('structural', 'every_factor_value_filled', 'sample_ids_in_sample_table'):
                    a, b = rec[k], stored[k]
                    if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-12): problems.append((sid, aid, src, k, a, b))
                if n_features != stored['n_features']: problems.append((sid, aid, src, 'n_features', n_features, stored['n_features']))
                evidence = {k: v for k, v in stored.items() if k not in ('study_id', 'analysis_id', 'source')}
                if curated:
                    rec = sv.compute(roster, sample_table_lines=len(ssf))
                    # items-table evidence that depends on which positions are QC/blank is left out for a corrected matrix
                    for k in DEPENDS_ON_QC_FLAG: evidence.pop(k, None)
                    evidence['curated_correction_note'] = (f'{len(curated)} positions changed QC/blank status after a curated correction; evidence that depends on '
                                                           'which positions are QC/blank was recomputed from the roster or left out')
                n_scored += rec['structural'] is not None
                ep.update(file=str(path.relative_to(ROOT)), file_in_study_folder=folder, n_features=n_features, sample_table_lines=len(ssf), roster=roster,
                          sections={'structural': {'score': rec['structural'], 'items': {k: rec[k] for k in sv.ITEM_KEYS},
                                                   'parameters': dict(sv.DEFAULT_PARAMS), 'evidence': evidence}})
                if curated: ep['curated_corrections'] = curated
            an['endpoints'][src] = ep
        study['analyses'].append(an)
    (OUT / 'json' / f'{sid.lower()}_v9.json').write_text(json.dumps(study, ensure_ascii=False, separators=(',', ':')))
    return sid, len(arows), n_valid, n_scored, problems

if __name__ == '__main__':
    (OUT / 'json').mkdir(parents=True, exist_ok=True); (OUT / 'logs').mkdir(exist_ok=True)
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    by_study = collections.OrderedDict()
    for r in csv.DictReader(open(VALID), delimiter='\t'): by_study.setdefault(r['study_id'], []).append(r)
    items = collections.defaultdict(dict)
    for line in open(ITEMS):
        r = json.loads(line); items[r['study_id']][(r['analysis_id'], r['source'])] = r
    jobs = [(sid, arows, items.get(sid, {})) for sid, arows in by_study.items()]
    done = 0; index = {}; problems = []
    with Pool(48) as pool, open(OUT / 'logs' / 'status.tsv', 'w') as status:
        status.write('study_id\tanalyses\tvalid_matrices\tscored_matrices\tproblems\n')
        for sid, n_an, n_valid, n_scored, prob in pool.imap_unordered(one, jobs, chunksize=4):
            done += 1; problems += prob
            index[sid] = {'path': f'json/{sid.lower()}_v9.json', 'analyses': n_an, 'valid_matrices': n_valid, 'structural_scored_matrices': n_scored}
            status.write(f'{sid}\t{n_an}\t{n_valid}\t{n_scored}\t{len(prob)}\n'); status.flush()
            if done % 200 == 0 or done == len(jobs):
                (OUT / 'checkpoint.json').write_text(json.dumps({'done': done, 'total': len(jobs), 'problems': len(problems), 'started': started}))
    with open(OUT / 'logs' / 'failures.tsv', 'w') as h:
        h.write('study_id\tanalysis_id\tendpoint\tfield\tfrom_roster\tin_items_table\n')
        for p in problems: h.write('\t'.join(str(x) for x in p) + '\n')
    finished = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    index = dict(sorted(index.items()))
    (OUT / 'index.json').write_text(json.dumps({'schema': 'merit.cache.v9.index', 'studies': index}, indent=1))
    manifest = {
        'schema': 'merit.cache.v9', 'built': {'started': started, 'finished': finished, 'script': str(Path(__file__).resolve()), 'roster_vs_items_problems': len(problems)},
        'source_dump': str(ROOT), 'scoring_unit': 'one valid matrix = one analysis from one endpoint; endpoints are not averaged and have no order of preference',
        'counts': {'studies': len(index), 'analyses': sum(v['analyses'] for v in index.values()), 'valid_matrices': sum(v['valid_matrices'] for v in index.values()),
                   'structural_scored_matrices': sum(v['structural_scored_matrices'] for v in index.values())},
        'sections': {'structural': {'status': 'filled', 'items': list(sv.ITEM_KEYS), 'parameters': {k: {'default': v[0], 'lowest': v[1], 'highest': v[2], 'step': v[3]} for k, v in sv.PARAMETERS.items()},
                                    'computed_by': 'merit/structural_v9.py::compute', 'items_table': str(ITEMS)},
                     **{k: {'status': 'not filled yet'} for k in ('metadata', 'analytical', 'annotation', 'labels', 'ml')}},
        'roster_fields': list(sv.ROSTER_FIELDS),
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest['counts']), '| problems', len(problems), problems[:5])
