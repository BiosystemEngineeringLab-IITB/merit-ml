"""Write value_cell_token_vocabulary_classified.tsv: every distinct non-number entry of the value cells with its class."""
import csv, sys, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent; OUT = HERE.parent
sys.path.insert(0, str(HERE))
from value_cell_classes import klass
rows = list(csv.DictReader(open(OUT / 'value_cell_token_vocabulary.tsv'), delimiter='\t'))
cells = collections.Counter(); n = collections.Counter()
with open(OUT / 'value_cell_token_vocabulary_classified.tsv', 'w', newline='') as h:
    w = csv.writer(h, delimiter='\t'); w.writerow(['token', 'class', 'cells', 'matrices', 'cells_datatable', 'cells_mwtab', 'cells_untarg_data'])
    for x in rows:
        k = klass(x['token']); cells[k] += int(x['cells']); n[k] += 1
        w.writerow([x['token'], k, x['cells'], x['matrices'], x['cells_datatable'], x['cells_mwtab'], x['cells_untarg_data']])
for k in cells: print(f'{k:18s} {n[k]:6d} distinct {cells[k]:12d} cells')
