"""Where the file of one matrix (study, analysis, endpoint) is in the frozen dump.

Normally <study>/<analysis>/... . An analysis ID that belongs to two studies has its files under one of them only:
when the manuscript's validity table says the matrix is valid and the study's own folder has no such file, the file
of the same analysis ID under the other study folder is used, and that folder is reported.
"""
from pathlib import Path
ROOT = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/mw-dump-latest-18thSep-2026-version')
NAMES = {'datatable': 'tabular/{a}_datatable_slash.txt', 'mwtab': 'mwtab/{a}_mwtab.txt', 'untarg_data': 'tabular/{a}_untarg_data.tsv'}

def matrix_path(study_id, analysis_id, source):
    """(path, study folder the file is in) or (None, None)."""
    rel = NAMES[source].format(a=analysis_id)
    p = ROOT / study_id / analysis_id / rel
    if p.is_file():
        return p, study_id
    others = sorted(q for q in ROOT.glob(f'ST*/{analysis_id}/{rel}') if q.is_file())
    if len(others) == 1:
        return others[0], others[0].parts[-4]
    return None, None
