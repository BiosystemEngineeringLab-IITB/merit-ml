"""Study-design context for the Overview "Derived context" box (display only; requested by the author on 2026-10-28).

Writes merit-cache-workbench-full-v9/design_context/<study>.json from the frozen v11-resolved labels of 1 June 2026, the labels used
by Figure S13, Table S4 and the design-context results. No label is changed here. Nothing in this folder feeds a score, an alert or
the class-count condition.

Per study: the label and its confidence, how it was decided (both readings agreed, or which tie-break rule), the deciding reason, the
evidence types of the language-model reading (llm_specific_design_tags, as Figure S13B), and, for the 38 studies found in the review
of 2026-10-28, which rule misfired and the label the cascade gives without it (resolve_v11_2026-06-01.py rerun with the tracer rule
restricted to title / description / study summary minus internal-standard sentences, and placeholder or human GENOTYPE_STRAIN /
ANIMAL_ANIMAL_SUPPLIER values ignored). The rerun without the patch must reproduce every stored label, or nothing is written.

usage: add_design_context_from_v11.py [--root CACHE]
"""
import importlib.util, json, re, sys
from pathlib import Path

import pandas as pd

B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
ROOT = Path(sys.argv[sys.argv.index('--root') + 1]) if '--root' in sys.argv[:-1] else B / 'merit-cache-workbench-full-v9'
AUDIT = B / 'outputs/study_design_sanity_check'
SHEET = AUDIT / 'mw_study_design_type_review_sheet_v11_resolved_2026-06-01.tsv'
RESOLVER = AUDIT / 'auditor_v1_2026-05-30/resolve_v11_2026-06-01.py'
OUT = ROOT / 'design_context'

spec = importlib.util.spec_from_file_location('resolve_v11', RESOLVER)
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)

PLACEHOLDER = {'', 'na', 'n/a', 'n.a.', 'not applicable', 'none', '-', 'unknown', 'not available', '9606', 'nan'}
INTERNAL_STANDARD = re.compile(r"[^.;\n]*(internal\s+standard|spiked?|dilution|labell?ed\s+standard|labell?ed\s+chemicals|\bIS\b)[^.;\n]*", re.I)
_signals = R.decisive_signals


def patched_signals(row):
    s = _signals(row)
    text = "\n".join(R.safe_str(row.get(c)) for c in ('title', 'description', 'study_summary_mwtab'))
    s['tracer_hit'] = bool(R.TRACER_RE.search(INTERNAL_STANDARD.sub(' ', text)))
    for k in ('genotype', 'supplier'):
        if s[k].strip().lower() in PLACEHOLDER:
            s[k] = ''
    if s['is_subj_human'] or (s['is_human'] and not s['is_model_animal']):
        s['genotype'] = ''                                   # an observed human genotype is not an experimental manipulation
    return s


MISFIRE = {'A6_tracer': 'isotope', 'A13_genotype_strain': 'genotype', 'A14_animal_supplier': 'supplier'}


def main():
    d = pd.read_csv(SHEET, sep='\t', low_memory=False, keep_default_na=False)
    again = d.apply(R.resolve, axis=1, result_type='expand')
    if not (again.v11_final_label == d.v11_final_label).all():
        sys.exit('the resolver no longer reproduces the stored labels: nothing written')
    R.decisive_signals = patched_signals
    patched = d.apply(R.resolve, axis=1, result_type='expand')
    OUT.mkdir(parents=True, exist_ok=True)
    flagged = 0
    for i, r in d.iterrows():
        tags = [t.strip() for t in re.split(r'[;|,]', r.llm_specific_design_tags) if t.strip()]
        rec = {'study_id': r.study_id, 'label': r.v11_final_label, 'confidence': r.v11_final_confidence,
               'decided_by': r.v11_final_resolver, 'reason': r.v11_final_reason,
               'language_model_label': r.llm_primary_design_context, 'rule_based_label': r.v11_auditor_label,
               'evidence_types': tags, 'source': 'v11 resolved labels, 1 June 2026 (frozen)'}
        new = patched.v11_final_label[i]
        if new != r.v11_final_label:
            flagged += 1
            rec['rule_review'] = {'rule': MISFIRE.get(r.v11_final_resolver, r.v11_final_resolver), 'label_without_rule': new}
        (OUT / f'{r.study_id.lower()}.json').write_text(json.dumps(rec, ensure_ascii=False))
    print(f'{len(d)} studies written to {OUT}; {flagged} carry a rule-review note')


if __name__ == '__main__':
    main()
