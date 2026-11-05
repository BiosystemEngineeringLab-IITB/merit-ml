"""Every value cell of every valid matrix: is it a number? Every cell that is not a number is recorded verbatim.

No cap: all cells of all 11,747 scanned matrices (frozen dump only). Value cells are the ones the manuscript readers use:
datatable / untarg_data = columns 3.. under a non-empty header, rows with a sample ID and not wider than the header
(a row shorter than the header has empty trailing cells); mwTab = the value columns of the first distinct data block.
Output: value_cell_tokens_per_matrix.jsonl (counts per matrix) and value_cell_token_vocabulary.tsv (all tokens).
"""
import csv, sys, re, json, collections
from pathlib import Path
from multiprocessing import Pool
sys.path.insert(0, '/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-ui-v8'); sys.path.insert(0, str(Path(__file__).resolve().parent))
from matrix_paths import matrix_path
from merit.manuscript_matrix_readers import _lines, _mwtab_layout
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot'); ROOT = B / 'mw-dump-latest-18thSep-2026-version'
VALID = B / 'merit/manuscript/figures/Figure-2/Used-in-manuscript/figure2b_source_availability_per_analysis.tsv'
OUT = B / 'outputs/structural_items_v9_claude_20261011'
N = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
NUM = re.compile(rf'^{N}$')
# the manuscript's value parser (table4_descriptors.to_float) reads a malformed exponent such as '1.80.E-04' as the number 1.80E-04
REPAIRED = re.compile(r'^[+-]?\d+\.\d+\.[eE][+-]?\d+$')
ROW = re.compile(rf'^(?: *{N} *\t)* *{N} *$')            # a whole row of numbers, tab separated

def one(row):
    sid, aid = row['study_id'], row['analysis_id']; base = ROOT / sid / aid; out = []
    for src in ('datatable', 'mwtab', 'untarg_data'):
        if row[src] != 'valid': continue
        p, _folder = matrix_path(sid, aid, src)
        if p is None: continue
        cells = numeric = 0; tok = collections.Counter(); repaired = collections.Counter()
        if src == 'mwtab':
            columns, samples, labels, frows = _mwtab_layout(p)
            for r in frows:
                for k in columns:
                    t = r[k + 1].strip() if k + 1 < len(r) else ''
                    cells += 1
                    if NUM.match(t): numeric += 1
                    elif REPAIRED.match(t): numeric += 1; repaired[t] += 1
                    else: tok[t] += 1
        else:
            lines = _lines(p)
            while lines and not lines[0].strip(): lines.pop(0)
            header = lines[0].split('\t'); pos = [k for k in range(2, len(header)) if header[k].strip()]
            contiguous = pos == list(range(2, len(header))); width = len(header)
            for line in lines[1:]:
                if not line.strip(): continue
                n = line.count('\t') + 1
                if n > width: continue
                head = line.split('\t', 2)
                if not head[0].strip(): continue
                if contiguous and n == width and len(head) == 3 and ROW.match(head[2]):
                    cells += len(pos); numeric += len(pos); continue
                c = line.split('\t')
                for k in pos:
                    t = c[k].strip() if k < n else ''
                    cells += 1
                    if NUM.match(t): numeric += 1
                    elif REPAIRED.match(t): numeric += 1; repaired[t] += 1
                    else: tok[t] += 1
        out.append(dict(study_id=sid, analysis_id=aid, source=src, value_cells=cells, numeric_cells=numeric, malformed_exponent_cells=sum(repaired.values()), malformed_exponent_examples=[t for t, _ in repaired.most_common(4)], tokens=dict(tok)))
    return out

if __name__ == '__main__':
    rows = list(csv.DictReader(open(VALID), delimiter='\t'))
    vocab = collections.Counter(); mats = collections.Counter(); by = collections.defaultdict(collections.Counter); tot = collections.Counter(); n = 0
    with Pool(52) as pool, open(OUT / 'value_cell_tokens_per_matrix.jsonl', 'w') as h:
        for part in pool.imap_unordered(one, rows, chunksize=4):
            for r in part:
                n += 1; h.write(json.dumps(r, ensure_ascii=False) + '\n')
                tot['cells'] += r['value_cells']; tot['numeric'] += r['numeric_cells']
                for t, c in r['tokens'].items():
                    vocab[t] += c; mats[t] += 1; by[t][r['source']] += c
    with open(OUT / 'value_cell_token_vocabulary.tsv', 'w', newline='') as h:
        w = csv.writer(h, delimiter='\t'); w.writerow(['token', 'cells', 'matrices', 'cells_datatable', 'cells_mwtab', 'cells_untarg_data'])
        for t, c in vocab.most_common(): w.writerow([t, c, mats[t], by[t]['datatable'], by[t]['mwtab'], by[t]['untarg_data']])
    print(n, 'matrices |', tot['cells'], 'value cells |', tot['numeric'], 'numbers |', len(vocab), 'distinct other tokens |', sum(vocab.values()), 'other cells')
