"""Value cells of ML-ELIGIBLE samples only (measured and not QC/blank): number or not; every non-number recorded verbatim.

Same cells and readers as enumerate_value_cell_tokens.py, restricted to the positions that are not datatable Tier 1 sample
alignment rows and not QC/blank (manuscript 16-keyword rule on sample ID and label). No cap.
Output: value_cell_tokens_per_matrix_eligible.jsonl
"""
import csv, sys, re, json, collections
from pathlib import Path
from multiprocessing import Pool
sys.path.insert(0, '/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-ui-v8'); sys.path.insert(0, str(Path(__file__).resolve().parent))
from matrix_paths import matrix_path
from merit.manuscript_matrix_readers import _lines, _mwtab_layout
from merit.manuscript_sample_rules import is_qc_or_blank, tier1_alignment
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot'); ROOT = B / 'mw-dump-latest-18thSep-2026-version'
VALID = B / 'merit/manuscript/figures/Figure-2/Used-in-manuscript/figure2b_source_availability_per_analysis.tsv'
OUT = B / 'outputs/structural_items_v9_claude_20261011'
N = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
NUM = re.compile(rf'^{N}$')
# the manuscript's value parser (table4_descriptors.to_float) reads a malformed exponent such as '1.80.E-04' as the number 1.80E-04
REPAIRED = re.compile(r'^[+-]?\d+\.\d+\.[eE][+-]?\d+$'); ROW = re.compile(rf'^(?: *{N} *\t)* *{N} *$')

def one(row):
    sid, aid = row['study_id'], row['analysis_id']; base = ROOT / sid / aid; mw = base / 'mwtab' / f'{aid}_mwtab.txt'; out = []
    for src in ('datatable', 'mwtab', 'untarg_data'):
        if row[src] != 'valid': continue
        p, _folder = matrix_path(sid, aid, src)
        if p is None: continue
        cells = numeric = npos = 0; tok = collections.Counter(); repaired = collections.Counter()
        if src == 'mwtab':
            columns, samples, labels, frows = _mwtab_layout(p)
            keep = [k for k, s, l in zip(columns, samples, labels) if not is_qc_or_blank(str(s).strip(), str(l))]
            npos = len(keep)
            for r in frows:
                for k in keep:
                    t = r[k + 1].strip() if k + 1 < len(r) else ''
                    cells += 1
                    if NUM.match(t): numeric += 1
                    elif REPAIRED.match(t): numeric += 1; repaired[t] += 1
                    else: tok[t] += 1
        else:
            removed = set()
            if src == 'datatable':
                al = tier1_alignment(p, mw)
                if al: removed = set(al['all_zero_absent_sample_ids'])
            lines = _lines(p)
            while lines and not lines[0].strip(): lines.pop(0)
            header = lines[0].split('\t'); pos = [k for k in range(2, len(header)) if header[k].strip()]
            contiguous = pos == list(range(2, len(header))); width = len(header)
            for line in lines[1:]:
                if not line.strip(): continue
                n = line.count('\t') + 1
                if n > width: continue
                head = line.split('\t', 2)
                sample = head[0].strip()
                if not sample or sample in removed or is_qc_or_blank(sample, head[1] if len(head) > 1 else ''): continue
                npos += 1
                if contiguous and n == width and len(head) == 3 and ROW.match(head[2]):
                    cells += len(pos); numeric += len(pos); continue
                c = line.split('\t')
                for k in pos:
                    t = c[k].strip() if k < n else ''
                    cells += 1
                    if NUM.match(t): numeric += 1
                    elif REPAIRED.match(t): numeric += 1; repaired[t] += 1
                    else: tok[t] += 1
        out.append(dict(study_id=sid, analysis_id=aid, source=src, eligible_positions=npos, value_cells=cells, numeric_cells=numeric, malformed_exponent_cells=sum(repaired.values()), malformed_exponent_examples=[t for t, _ in repaired.most_common(4)], tokens=dict(tok)))
    return out

if __name__ == '__main__':
    rows = list(csv.DictReader(open(VALID), delimiter='\t')); n = 0; tot = collections.Counter(); vocab = set()
    with Pool(52) as pool, open(OUT / 'value_cell_tokens_per_matrix_eligible.jsonl', 'w') as h:
        for part in pool.imap_unordered(one, rows, chunksize=4):
            for r in part:
                n += 1; h.write(json.dumps(r, ensure_ascii=False) + '\n'); tot['cells'] += r['value_cells']; tot['numeric'] += r['numeric_cells']; vocab.update(r['tokens'])
    print(n, 'matrices |', tot['cells'], 'value cells of ML-eligible samples |', tot['numeric'], 'numbers |', len(vocab), 'distinct other entries')
    known = {r['token'] for r in csv.DictReader(open(OUT / 'value_cell_token_vocabulary.tsv'), delimiter='\t')}
    print('entries not in the full vocabulary:', len(vocab - known))
