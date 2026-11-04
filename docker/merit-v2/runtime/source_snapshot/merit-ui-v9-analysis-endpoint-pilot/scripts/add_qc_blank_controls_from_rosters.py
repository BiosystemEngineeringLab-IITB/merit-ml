"""Fill the metric "QC / blank controls" of the Analytical QC section of the v9 cache: counts and a presence label, nothing scored.

Per valid matrix (analysis x endpoint): endpoints[*].sections.analytical.qc_blank_controls = merit/qc_blank_v9.py::compute
  (QC-like and blank-like positions from the position roster of the cache, with sample IDs and where the keyword was found,
  presence label), plus how many of the sample table's QC-like and blank-like sample IDs occur among the matrix positions.
Per analysis: sections.analytical.additional_sample_data (merit/additional_sample_data.py: every field of the additional
  sample data of the sample table, from the mwTab text file, else the JSON; fields already shown elsewhere listed with the
  reason; replaces run_order_fields on 15 Oct 2026) and sections.analytical.sample_table_controls (QC-like and blank-like records of the
  sample table of the mwTab text file; records, not unique samples).
Only the rosters of the cache and the mwTab files of the frozen deposit are read; no measured value is read.
Run after add_analytical_section_from_v8.py (it extends manifest.sections.analytical). Under a minute.
usage: add_qc_blank_controls_from_rosters.py [v9 root]
"""
import json, re, sys, datetime, collections
from pathlib import Path
from multiprocessing import Pool
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
DUMP = B / 'mw-dump-latest-18thSep-2026-version'
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
from merit import qc_blank_v9 as qb
from merit import additional_sample_data as asd


def mwtab_text_lines(study_id, analysis_id):
    path = DUMP / study_id / analysis_id / 'mwtab' / f'{analysis_id}_mwtab.txt'
    return path.read_text(encoding='utf-8', errors='replace').split('\n') if path.is_file() else []


def one(path):
    v9 = json.loads(path.read_text()); sid = v9['study_id']; counts = collections.Counter()
    for a in v9['analyses']:
        aid = a['analysis_id']
        lines = mwtab_text_lines(sid, aid)
        table = qb.sample_table_controls(lines, sid) if lines else None
        section = a.setdefault('sections', {}).setdefault('analytical', {})
        section.pop('run_order_fields', None)          # replaced on 15 Oct 2026 by the additional sample data
        extra = asd.summarise(*asd.read(DUMP / sid / aid / 'mwtab', aid))
        section['additional_sample_data'] = extra
        section['sample_table_controls'] = table or {'note': 'no mwTab text file'}
        has_qc = False
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep: continue
            rec = qb.compute(ep['roster'], study_id=sid)
            ids = {row[0] for row in ep['roster']}
            if table:
                rec['sample_table_qc_like_ids_in_matrix'] = sum(1 for i in table['qc_like_sample_ids'] if i in ids)
                rec['sample_table_blank_like_ids_in_matrix'] = sum(1 for i in table['blank_like_sample_ids'] if i in ids)
            if ep.get('curated_corrections'): rec['curated_corrections'] = ep['curated_corrections']
            ep.setdefault('sections', {}).setdefault('analytical', {})['qc_blank_controls'] = rec
            has_qc = has_qc or bool(rec['qc_like_positions'])
            counts['matrices'] += 1; counts[f"presence: {rec['presence']}"] += 1
            counts['matrices with QC-like positions'] += bool(rec['qc_like_positions']); counts['matrices with blank-like positions'] += bool(rec['blank_like_positions'])
            counts['matrices with standard-like positions'] += bool(rec['standard_like_positions']); counts['standard-like positions'] += rec['standard_like_positions']
            if table and table['qc_like_records'] and not rec['qc_like_positions']: counts['matrices without QC-like positions whose sample table lists QC-like records'] += 1
            if table and table['blank_like_records'] and not rec['blank_like_positions']: counts['matrices without blank-like positions whose sample table lists blank-like records'] += 1
        counts['analyses'] += 1
        counts['analyses with QC-like positions in a valid matrix'] += has_qc
        counts[f"additional sample data read from: {extra['read_from']}"] += 1
        counts['analyses with additional sample data reported'] += bool(extra['fields']); counts['additional sample data fields reported'] += len(extra['fields'])
        counts['analyses with QC-like positions and additional sample data reported'] += has_qc and bool(extra['fields'])
        for x in extra['not_repeated']: counts['fields not repeated: ' + re.sub(r'the factor .*', 'a factor', x['reason'])] += 1
        if table:
            counts['sample-table records'] += table['records']; counts['sample-table QC-like records'] += table['qc_like_records']; counts['sample-table blank-like records'] += table['blank_like_records']
            counts['sample-table standard-like records'] += table['standard_like_records']
    path.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return counts


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    total = collections.Counter()
    with Pool(40) as pool:
        for c in pool.map(one, sorted((V9 / 'json').glob('*_v9.json')), chunksize=8): total.update(c)
    manifest = json.loads((V9 / 'manifest.json').read_text())
    section = manifest['sections'].setdefault('analytical', {})
    section['status'] = 'partly filled: two informational blocks and QC / blank controls; the other metrics are not filled yet'
    section['qc_blank_controls'] = {
        'display_name': 'Sample eligibility & analytical controls (formerly QC / blank controls; renamed 15 Oct 2026)',
        'status': 'filled', 'scope': 'analysis x endpoint: one record per valid matrix; additional sample data and sample-table counts per analysis',
        'informational': 'counts and a presence label only; no value and no score',
        'computed_by': 'merit/qc_blank_v9.py::compute, sample_table_controls; merit/additional_sample_data.py::read, summarise',
        'computed_from': 'position rosters of this cache; mwTab text files of the frozen deposit (sample tables), the mwTab JSON only where no text file was deposited; no measured value is read',
        'computed_at': started, 'script': str(Path(__file__).resolve()),
        'presence_labels': list(qb.PRESENCE.values()), 'keywords': {k: list(v) for k, v in qb.KEYWORDS.items()}, 'counts': dict(sorted(total.items()))}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k, v in sorted(total.items()): print(f'{v:9,}  {k}')
