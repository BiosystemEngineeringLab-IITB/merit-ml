"""Structural items per valid matrix (analysis x endpoint) for the 8782 pilot. Frozen dump only; nothing inferred.

Scored (each 0-1):
  sample_support        min(1, distinct ML-eligible sample IDs / 20)
  samples_with_factor   ML-eligible positions that have a non-empty factor label / ML-eligible positions
  numeric_values        1 - text cells / (value cells - missing entries), value cells of ML-eligible positions only
  unique_sample_ids     distinct sample IDs / ML-eligible positions   (percent duplicate = 100 - score)
A position = one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block.
Measured positions = all positions except datatable Tier 1 sample alignment rows. ML-eligible = measured and not QC/blank.
Every sample-related item uses ML-eligible positions only (user decision 2026-10-11).
"""
import csv, sys, json, collections
from pathlib import Path
from multiprocessing import Pool
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, '/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-ui-v8')
from value_cell_classes import klass, TEXT_CLASSES
from matrix_paths import matrix_path
from merit.manuscript_sample_rules import is_qc_or_blank, tier1_alignment
from merit.manuscript_matrix_readers import sample_rows_of_sample_by_feature, sample_rows_of_mwtab, _lines, _mwtab_layout
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot'); ROOT = B / 'mw-dump-latest-18thSep-2026-version'; OUT = HERE.parent
VALID = B / 'merit/manuscript/figures/Figure-2/Used-in-manuscript/figure2b_source_availability_per_analysis.tsv'
OLD = B / 'outputs/structural_candidates_claude_20261011'
BLANK = {'', '-', 'na', 'n/a', 'nan', 'null', 'none'}
SOURCES = ('datatable', 'mwtab', 'untarg_data')

EMPTY_VALUE = {'', '-'}      # an empty field, or the repository's '-' for "nothing entered". Words such as None, NA or Unknown are deposited values.

def label_values(text):
    return [(part.split(':', 1)[1] if ':' in part else part).strip() for part in str(text).split('|')]

def nonempty_label(text):
    """A factor label counts when at least one of its values is not empty ('Factor:Value | Factor:Value' or plain text)."""
    return any(v not in EMPTY_VALUE for v in label_values(text))

def all_values_filled(text):
    values = label_values(text)
    return bool(values) and all(v not in EMPTY_VALUE for v in values)

def one(row):
    sid, aid = row['study_id'], row['analysis_id']; base = ROOT / sid / aid; mw = base / 'mwtab' / f'{aid}_mwtab.txt'
    ssf = {}
    if mw.is_file():
        for line in _lines(mw):
            if line.startswith('SUBJECT_SAMPLE_FACTORS'):
                c = line.split('\t')
                if len(c) > 2 and c[2].strip():
                    ssf.setdefault(c[2].strip(), (c[1].strip(), c[3].strip() if len(c) > 3 else ''))
    out = []
    paths = {}; folders = {}
    for src in SOURCES:
        paths[src], folders[src] = matrix_path(sid, aid, src) if row[src] == 'valid' else (None, None)
    rosters = {}
    for src in SOURCES:
        if paths[src] is None: continue
        rows_ = sample_rows_of_mwtab(paths[src]) if src == 'mwtab' else sample_rows_of_sample_by_feature(paths[src])
        removed_ = []
        if src == 'datatable':
            al = tier1_alignment(paths[src], mw)
            if al: removed_ = list(al['all_zero_absent_sample_ids'])
        rosters[src] = (rows_, removed_)
    declared = {i for i, (_, fac) in ssf.items() if not is_qc_or_blank(i, fac)}      # non-QC samples listed in the sample table
    for src in SOURCES:
        if src not in rosters: continue
        rows, removed = rosters[src]
        rset = set(removed)
        measured = [(i, l) for i, l in rows if i not in rset]
        elig = [(i, l) for i, l in measured if not is_qc_or_blank(i, l)]
        ids = [i for i, _ in elig]; dist = list(dict.fromkeys(ids)); cnt = collections.Counter(ids)
        rec = dict(study_id=sid, analysis_id=aid, source=src, positions=len(rows), tier1_alignment_rows=len(rows) - len(measured),
                   tier1_alignment_examples=removed[:8], measured_positions=len(measured), qc_blank_positions=len(measured) - len(elig),
                   eligible_positions=len(ids), distinct_eligible_ids=len(dist), sample_table_lines=len(ssf),
                   qc_blank_positions_deposited=sum(1 for i, l in rows if is_qc_or_blank(i, l)), distinct_ids_all_positions=len({i for i, _ in rows}),
                   file_in_study_folder=folders[src])
        rec['sample_support'] = min(1.0, len(dist) / 20) if ids else None
        rec['unique_sample_ids'] = len(dist) / len(ids) if ids else None
        rec['repeated_positions'] = len(ids) - len(dist)
        rec['positions_with_repeated_id'] = sum(c for c in cnt.values() if c > 1)      # every position whose sample ID occurs more than once
        rec['sample_ids_repeated'] = sum(1 for c in cnt.values() if c > 1)
        rec['repeated_id_examples'] = [f'{i} ×{c}' for i, c in cnt.most_common(5) if c > 1]
        # factor label per measured position: on the position itself, or on the sample-table line of the same ID
        in_table = [nonempty_label(ssf[i][1]) if i in ssf else False for i, _ in elig]
        own = [nonempty_label(l) for _, l in elig]
        either = [a or b for a, b in zip(in_table, own)]
        rec['samples_with_factor'] = sum(either) / len(elig) if elig else None
        rec['n_with_factor'] = sum(either); rec['n_with_factor_in_sample_table'] = sum(in_table); rec['n_with_factor_on_position'] = sum(own)
        rec['samples_without_factor_examples'] = [i for (i, _), ok in zip(elig, either) if not ok][:8]
        e_either = [(nonempty_label(ssf[i][1]) if i in ssf else False) or nonempty_label(l) for i, l in elig]
        rec['eligible_with_factor'] = sum(e_either)
        best = [l if nonempty_label(l) else (ssf[i][1] if i in ssf else '') for i, l in elig]
        rec['every_factor_value_filled'] = sum(all_values_filled(b) for b in best) / len(elig) if elig else None
        rec['word_values'] = dict(collections.Counter(v.casefold() for b in best for v in label_values(b) if v.casefold() in {'none', 'na', 'n/a', 'nan', 'null', 'unknown'}))
        # subject IDs (sample table, first field) of the ML-eligible sample IDs: information only
        subj = collections.defaultdict(list); given = 0
        for i in dist:
            s = ssf.get(i, ('', ''))[0]
            if s.casefold() not in BLANK | {'unknown', 'not applicable'}:
                given += 1; subj[s].append(i)
        multi = {s: v for s, v in subj.items() if len(v) > 1}
        rec['subject_ids_given_for'] = given; rec['distinct_subject_ids'] = len(subj); rec['subjects_with_several_sample_ids'] = len(multi)
        rec['sample_ids_sharing_a_subject'] = sum(len(v) for v in multi.values())
        rec['subject_examples'] = [{'subject_id': s, 'n_sample_ids': len(v), 'sample_ids': v[:5]} for s, v in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:3]]
        # shown, not scored
        rec['sample_ids_in_sample_table'] = sum(1 for i in dist if i in ssf) / len(dist) if dist and ssf else None
        peers = []
        for other, (orows, oremoved) in rosters.items():
            if other != src and dist:
                oids = {i for i, _ in orows if i not in set(oremoved)}
                peers.append(sum(1 for i in dist if i in oids) / len(dist))
        rec['ids_agree_with_other_endpoints'] = sum(peers) / len(peers) if peers else None
        mset = {i for i, _ in measured}
        rec['declared_non_qc_samples'] = len(declared)
        rec['declared_samples_measured'] = sum(1 for i in declared if i in mset) / len(declared) if declared else None
        if src == 'mwtab':
            rec['n_features'] = len(_mwtab_layout(paths[src])[3])
        else:
            lines = _lines(paths[src])
            while lines and not lines[0].strip(): lines.pop(0)
            rec['n_features'] = sum(1 for h in lines[0].split('\t')[2:] if h.strip()) if lines else 0
        out.append(rec)
    return out

if __name__ == '__main__':
    rows = list(csv.DictReader(open(VALID), delimiter='\t'))
    with Pool(48) as pool: res = {(r['study_id'], r['analysis_id'], r['source']): r for part in pool.imap_unordered(one, rows, chunksize=20) for r in part}
    # value cells (full enumeration)
    for line in open(OUT / 'value_cell_tokens_per_matrix_eligible.jsonl'):          # value cells of ML-eligible samples only
        t = json.loads(line); r = res[(t['study_id'], t['analysis_id'], t['source'])]
        by = collections.Counter(); ex = collections.Counter()
        for tok, c in t['tokens'].items():
            k = klass(tok); by[k] += c
            if k != 'missing entry': ex[tok] += c
        text = sum(by[k] for k in TEXT_CLASSES); denom = t['value_cells'] - by['missing entry']
        assert t['eligible_positions'] == r['eligible_positions'], (t['study_id'], t['analysis_id'], t['source'])
        r.update(value_cells=t['value_cells'], numeric_cells=t['numeric_cells'], missing_entry_cells=by['missing entry'], text_cells=text,
                 text_cells_by_class={k: by[k] for k in TEXT_CLASSES if by[k]}, text_examples=[f'{tok} ×{c}' for tok, c in ex.most_common(6)],
                 numeric_values=(1 - text / denom) if denom else None,
                 malformed_exponent_cells=t.get('malformed_exponent_cells', 0), malformed_exponent_examples=t.get('malformed_exponent_examples', []))
    ITEMS = ('sample_support', 'samples_with_factor', 'numeric_values', 'unique_sample_ids')
    for r in res.values():
        v = [r.get(k) for k in ITEMS if r.get(k) is not None]
        r['structural'] = sum(v) / len(v) if v else None; r['n_scored_items'] = len(v)
    out = sorted(res.values(), key=lambda r: (r['study_id'], r['analysis_id'], r['source']))
    with open(OUT / 'structural_items_per_matrix.jsonl', 'w') as h:
        for r in out: h.write(json.dumps(r, ensure_ascii=False) + '\n')
    flat = [k for k in out[0] if not isinstance(out[0][k], (list, dict))]
    with open(OUT / 'structural_items_per_matrix.tsv', 'w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=flat, delimiter='\t', extrasaction='ignore'); w.writeheader(); w.writerows(out)
    print(len(out), 'matrices')
