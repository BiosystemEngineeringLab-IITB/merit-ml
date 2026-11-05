"""Fill the two informational blocks of the 'analytical' (Analytical QC) section of the v9 cache from the v8 results.
Nothing is recomputed and nothing here is scored.

  acquisition_evidence  the record of the analysis in v8's 'analytical_acquisition_context' (84 fields), copied unchanged.
                        v8 holds one record per analysis of the study, also for analyses without a valid matrix.
  data_summary          the fields behind the first nine columns of v8's "Per-Analysis Data Summary" (analysis type, MS type,
                        ion mode, chromatography, instrument, units) and behind v8's "NMR Acquisition Details", copied unchanged
                        from v8's per-analysis ingestion record. v8 holds such a record for every endpoint view in which the
                        analysis has a valid matrix; the script stops if a copied field differs between two views of one
                        analysis, so one record per analysis is exact.
                        An analysis without a valid matrix has no such record in v8. Its row is filled from the analysis
                        metadata v8 stored when it read the mwTab file of the analysis
                        (merit-cache-workbench-full-v8/_derived/<study>/__merit_combined.json, 'analyses'), with the mapping
                        v8 uses for its own rows (row_from_analysis_metadata). The script applies that mapping to every
                        analysis and stops unless it reproduces every existing v8 row exactly, so the filled rows follow the
                        same rule as the copied ones. A filled row is marked data_summary_source = 'analysis metadata'.

  data_summary_provenance   where the displayed analysis type and units were deposited, found by looking in the mwTab JSON and
                        text file of the analysis in the frozen deposit (no value is changed by this):
                        analysis_type_source  mwtab_analysis_type_field | mwtab_json_only | mwtab_section_present |
                                              study_level_no_mwtab_file | none
                        units_source          the <BLOCK>:UNITS declaration of the mwTab data block that holds the displayed units
                        units_read_from, units_is_placeholder (the manuscript's placeholder rule), metadata_read_from (mwTab JSON, or
                        the text file when the JSON is not valid JSON), results_file_declarations (every results-file line of the
                        text file, as deposited), json_text_differences (displayed fields that the two formats state differently)
                        The script stops if a displayed unit is not found in such a declaration, or a type source does not hold.

Both are analysis level: one record per analysis, none per endpoint.
usage: add_analytical_section_from_v8.py [v9 root]
"""
import json, re, sys, datetime, collections
from pathlib import Path
from multiprocessing import Pool
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
V8 = B / 'merit-cache-workbench-full-v8'
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
# every field the nine kept columns of the table read
SUMMARY_FIELDS = ('analysis_id', 'analysis_type', 'platform', 'ms_type', 'polarity', 'chromatography', 'chromatography_system', 'chromatography_column',
                  'ms_instrument_type', 'ms_instrument_name', 'nmr_instrument_type', 'nmr_experiment_type', 'units',
                  # NMR Acquisition Details
                  'nmr_data_block', 'nmr_spectrometer_frequency', 'nmr_solvent', 'nmr_pulse_sequence', 'nmr_water_suppression', 'nmr_reference_compound', 'nmr_temperature')


def row_from_analysis_metadata(info, study_type):
    """The summary row v8 builds from the stored metadata of one analysis (merit/connectors/workbench.py, merit/assessment.py)."""
    analysis_type = str(info.get('analysis_type') or study_type or '').strip()
    ion = (info.get('ion_mode') or '').strip().upper()
    polarity = 'positive' if ion in {'POSITIVE', 'POS', 'P'} else 'negative' if ion in {'NEGATIVE', 'NEG', 'N'} else ion
    platform = analysis_type or study_type or ''
    row = {'analysis_id': info.get('analysis_id'), 'analysis_type': analysis_type or platform, 'platform': platform, 'ms_type': info.get('ms_type', '') or '',
           'polarity': polarity, 'chromatography': info.get('chromatography_type', '') or '', 'units': (info.get('units') or '').strip()}
    for k in SUMMARY_FIELDS:
        if k not in row: row[k] = info.get(k, '') or ''
    return {k: row[k] for k in SUMMARY_FIELDS}
SCOPE = 'analysis: one record per analysis, not per endpoint'
DUMP = B / 'mw-dump-latest-18thSep-2026-version'
UNIT_BLOCKS = ('MS_METABOLITE_DATA', 'NMR_METABOLITE_DATA', 'NMR_BINNED_DATA', 'DIRECT_INFUSION_METABOLITE_DATA', 'METABOLITE_DATA')   # order in which v8 looks
UNITS_LINE = re.compile(r'^([A-Z_]*DATA):UNITS\s*\t(.*)$')
TYPE_LINE = re.compile(r'^AN:ANALYSIS_TYPE\s+(\S.*)$')
TYPE_SOURCES = {
    'mwtab_analysis_type_field': 'ANALYSIS_TYPE field of the mwTab file of the analysis',
    'mwtab_json_only': 'ANALYSIS_TYPE in the mwTab JSON of the analysis; the analysis has no mwTab text file',
    'mwtab_section_present': 'no ANALYSIS_TYPE field in the mwTab file; named after the MS or NMR section present in that file',
    'study_level_no_mwtab_file': 'the analysis has no mwTab file; analysis type declared by the other analyses of the study',
    'none': 'no analysis type deposited or shown',
}


# UNITS values that are not a unit: the rule of the manuscript's units analysis, copied from
# merit/manuscript/manuscript-original-reviews/reviewer-1/comment1_analyzer_class_and_acquisition_evidence/build_analysis_evidence.py
UNITS_PLACEHOLDER = re.compile(r"^(?:|-|--|na|n/a|none|not applicable|not available|unknown|unspecified|\.|\d+(?:\.\d+)?|"
                               r"(?:n/a|na|not applicable)\s*\(.*|no[tn]?\s+applicable(?:\s+unit\w*)?)$", re.I)
RESULTS_LINE = re.compile(r"^(?:#\S*\s+)?(MS:MS|NM:NMR)_RESULTS_FILE\b")
# displayed field -> (JSON block, JSON key, key of the mwTab text file)
FORMAT_FIELDS = {'analysis_type': ('ANALYSIS', 'ANALYSIS_TYPE', 'AN:ANALYSIS_TYPE'), 'ms_type': ('MS', 'MS_TYPE', 'MS:MS_TYPE'), 'polarity': ('MS', 'ION_MODE', 'MS:ION_MODE'),
                 'chromatography': ('CHROMATOGRAPHY', 'CHROMATOGRAPHY_TYPE', 'CH:CHROMATOGRAPHY_TYPE'), 'chromatography_system': ('CHROMATOGRAPHY', 'INSTRUMENT_NAME', 'CH:INSTRUMENT_NAME'),
                 'chromatography_column': ('CHROMATOGRAPHY', 'COLUMN_NAME', 'CH:COLUMN_NAME'), 'ms_instrument_type': ('MS', 'INSTRUMENT_TYPE', 'MS:INSTRUMENT_TYPE'),
                 'ms_instrument_name': ('MS', 'INSTRUMENT_NAME', 'MS:INSTRUMENT_NAME')}
_one_space = lambda s: re.sub(r'\s+', ' ', str(s or '')).strip()


def results_file_declarations(text_lines):
    """Every results-file line of the mwTab text file (MS:MS_RESULTS_FILE / NM:NMR_RESULTS_FILE), as deposited.
    A line is: key <tab> file name <tab> attributes such as UNITS:..., Has m/z:..., Has RT:... . Identical lines are kept once
    with their count. Nothing is taken apart beyond the tabs: when UNITS is written inside the file-name cell, the cell is kept whole."""
    seen = {}
    for number, line in enumerate(text_lines, 1):
        if 'RESULTS_FILE' not in line: continue
        m = RESULTS_LINE.match(line)
        if not m: continue
        cells = line.split('\t')
        attributes = {}
        for cell in cells[2:]:
            if ':' in cell:
                k, v = cell.split(':', 1); attributes[k.strip()] = v.strip()
        units = next((v for k, v in attributes.items() if k.lower() == 'units'), '')
        file_cell = cells[1].strip() if len(cells) > 1 else ''
        key = (m.group(1) + '_RESULTS_FILE', file_cell, tuple(sorted(attributes.items())))
        if key in seen:
            seen[key]['times_in_file'] += 1; continue
        seen[key] = {'line_key': m.group(1) + '_RESULTS_FILE', 'file_name_cell': file_cell, 'units': units,
                     'units_is_placeholder': bool(units) and bool(UNITS_PLACEHOLDER.match(units.strip())),
                     'units_written_inside_file_name_cell': (not units) and bool(re.search(r'\bUNITS\s*:', file_cell, re.I)),
                     'attributes': attributes, 'first_line_number': number, 'times_in_file': 1}
    return list(seen.values())


def provenance(sid, aid, summary, own_type, study_types):
    """Where the displayed values were deposited: looked up in the frozen mwTab JSON and text file of the analysis."""
    folder = DUMP / sid / aid / 'mwtab'
    json_path, text_path = folder / f'{aid}_mwtab.json', folder / f'{aid}_mwtab.txt'
    payload = None
    if json_path.is_file():
        try: payload = json.loads(json_path.read_text())
        except json.JSONDecodeError: payload = None
    payload_ok = isinstance(payload, dict)
    json_type = ''
    if payload_ok and isinstance(payload.get('ANALYSIS'), dict):
        json_type = str(payload['ANALYSIS'].get('ANALYSIS_TYPE', '') or '').strip()
    text_lines = text_path.read_text(errors='replace').replace('\r\n', '\n').split('\n') if text_path.is_file() else []
    text_type = ''; text_units = {}; text_fields = collections.defaultdict(list)
    for line in text_lines:
        m = TYPE_LINE.match(line)
        if m and not text_type: text_type = m.group(1).strip()
        m = UNITS_LINE.match(line.strip('\n'))
        if m and m.group(2).strip(): text_units.setdefault(m.group(1), m.group(2).strip())
        if len(line) > 3 and line[2] == ':' and '\t' in line:
            k, v = line.split('\t', 1); text_fields[k.strip()].append(_one_space(v))
    # the format v8 read the analysis metadata from: the JSON when it is valid JSON, else the text file
    read_from = ('mwTab JSON' if payload_ok else 'mwTab text file (the deposited JSON is not valid JSON)' if json_path.is_file() and text_lines
                 else 'mwTab text file' if text_lines else 'no mwTab file')
    shown_type = summary['analysis_type']
    if text_type: source = 'mwtab_analysis_type_field'
    elif json_type:
        assert not text_path.is_file(), f'{sid} {aid}: ANALYSIS_TYPE in the JSON but not in the text file'
        source = 'mwtab_json_only'
    elif own_type:
        sections = [name for name, keys in (('MS', ('MS', 'MS_METABOLITE_DATA', 'DIRECT_INFUSION_METABOLITE_DATA')), ('NMR', ('NM', 'NMR', 'NMR_METABOLITE_DATA', 'NMR_BINNED_DATA')))
                    if payload_ok and any(k in payload for k in keys)]
        assert '; '.join(sections) == shown_type, f'{sid} {aid}: type {shown_type!r} is not the section present {sections}'
        source = 'mwtab_section_present'
    elif shown_type:
        assert not json_path.is_file() and not text_path.is_file(), f'{sid} {aid}: study-level type although an mwTab file exists'
        assert shown_type in study_types, f'{sid} {aid}: study-level type {shown_type!r} is not declared by another analysis'
        source = 'study_level_no_mwtab_file'
    else: source = 'none'
    units_source = units_read_from = None
    if summary['units']:
        json_blocks = [b for b in UNIT_BLOCKS if payload_ok and isinstance(payload.get(b), dict) and str(payload[b].get('Units', '') or '').strip() == summary['units']]
        text_blocks = [b for b in UNIT_BLOCKS if text_units.get(b) == summary['units']]
        assert json_blocks or text_blocks, f'{sid} {aid}: units {summary["units"]!r} not found in a UNITS declaration of an mwTab data block'
        units_source = (json_blocks or text_blocks)[0] + ':UNITS'
        units_read_from = 'mwTab JSON' if json_blocks else 'mwTab text file'
    # values that the two formats of the same mwTab file state differently (only when the JSON was the format read and a text file exists).
    # Not listed: the text file repeating the same line, and differences of spacing or letter case only.
    differences = {}
    if payload_ok and text_lines:
        for field, (block, key, text_key) in FORMAT_FIELDS.items():
            json_value = _one_space(payload[block].get(key, '') if isinstance(payload.get(block), dict) else '')
            lines = text_fields.get(text_key, [])
            joined = ' '.join(lines).strip()
            if json_value == joined or (lines and all(x == lines[0] for x in lines) and json_value == lines[0]): continue
            if json_value.replace(' ', '').casefold() == joined.replace(' ', '').casefold(): continue
            differences[field] = {'json': json_value, 'text_lines': lines,
                                  'kind': 'in the JSON only' if not joined else 'in the text file only' if not json_value else 'different'}
    return {'analysis_type_source': source, 'analysis_type_source_text': TYPE_SOURCES[source], 'units_source': units_source,
            'units_source_text': (f'{units_source} of the mwTab file (units declared for the mwTab data block)' if units_source else 'no units declared for an mwTab data block'),
            'units_read_from': units_read_from, 'units_is_placeholder': bool(summary['units']) and bool(UNITS_PLACEHOLDER.match(summary['units'].strip())),
            'metadata_read_from': read_from, 'mwtab_json_file': json_path.is_file(), 'mwtab_json_valid': payload_ok, 'mwtab_text_file': text_path.is_file(),
            'results_file_declarations': results_file_declarations(text_lines), 'json_text_differences': differences}


def one(path):
    v9 = json.loads(path.read_text()); sid = v9['study_id']
    s = json.loads((V8 / 'json' / f'{sid.lower()}_workflow_state.json').read_text())
    context = s.get('analytical_acquisition_context') or {}
    records = {str(r['analysis_id']).upper(): r for r in context.get('records') or []}
    assert len(records) == len(context.get('records') or []), f'{sid}: repeated analysis in the v8 acquisition context'
    views = {}                                                   # endpoint view -> analysis -> v8 per-analysis ingestion record
    for src, a in (s.get('source_assessments') or {}).items():
        for r in ((a or {}).get('ingestion_summary') or {}).get('per_analysis') or []:
            views.setdefault(str(r.get('analysis_id', '')).upper(), {})[src] = r
    derived = json.loads((V8 / '_derived' / sid / '__merit_combined.json').read_text())
    info = {str(x.get('analysis_id', '')).upper(): x for x in (derived.get('analyses') or {}).values() if isinstance(x, dict)}
    study_type = (derived.get('summary') or {}).get('analysis_type', '')
    n_summary = n_filled = 0
    prov_counts = collections.Counter()
    for a in v9['analyses']:
        aid = a['analysis_id']
        assert aid in records, f'{sid} {aid}: no acquisition record in v8'
        valid = sorted(k for k, ep in a['endpoints'].items() if ep.get('availability') == 'valid')
        have = views.get(aid) or {}
        assert sorted(have) == valid, f'{sid} {aid}: v8 summary views {sorted(have)} but valid endpoints {valid}'
        summary = None
        if have:
            picked = [{k: r[k] for k in SUMMARY_FIELDS if k in r} for r in have.values()]
            assert all(p == picked[0] for p in picked), f'{sid} {aid}: a summary field differs between endpoint views'
            summary = picked[0]; n_summary += 1
            # the mapping used for the filled rows must reproduce this v8 row exactly
            assert aid in info and row_from_analysis_metadata(info[aid], study_type) == summary, f'{sid} {aid}: analysis metadata does not reproduce the v8 row'
        else:
            own = info.get(aid)
            assert own is not None and str(own.get('analysis_type') or '').strip(), f'{sid} {aid}: no stored analysis metadata with its own analysis type'
            summary = row_from_analysis_metadata(own, study_type); n_filled += 1
        # keys written by later steps (for example additional_sample_data of the QC / blank controls step) are kept
        a.setdefault('sections', {}).setdefault('analytical', {}).update({
            'scope': SCOPE, 'score': None,
            'acquisition_evidence': records[aid],
            'data_summary': summary,
            'data_summary_source': 'per-analysis summary row' if have else 'analysis metadata (the analysis has no valid matrix on any endpoint)',
            'data_summary_provenance': provenance(sid, aid, summary, str((info.get(aid) or {}).get('analysis_type') or '').strip(),
                                                  {str(x.get('analysis_type') or '').strip() for k, x in info.items() if k != aid} - {''}),
        })
        prov = a['sections']['analytical']['data_summary_provenance']
        prov_counts['type: ' + prov['analysis_type_source']] += 1
        prov_counts['units: ' + (prov['units_source'] or 'none declared')] += 1
        prov_counts['read from: ' + prov['metadata_read_from']] += 1
        prov_counts['units that are a placeholder'] += prov['units_is_placeholder']
        prov_counts['units read from the JSON, no text file'] += bool(prov['units_read_from'] == 'mwTab JSON' and not prov['mwtab_text_file'])
        decl = prov['results_file_declarations']
        prov_counts['analyses with a results-file line'] += bool(decl)
        prov_counts['analyses with more than one distinct results-file line'] += len(decl) > 1
        prov_counts['analyses with results-file UNITS (tab attribute)'] += any(d['units'] for d in decl)
        prov_counts['analyses with results-file UNITS that are a placeholder'] += any(d['units_is_placeholder'] for d in decl)
        prov_counts['analyses with UNITS written inside the file-name cell only'] += (not any(d['units'] for d in decl)) and any(d['units_written_inside_file_name_cell'] for d in decl)
        prov_counts['analyses with a JSON/text difference in a displayed field'] += bool(prov['json_text_differences'])
        for field, dd in prov['json_text_differences'].items(): prov_counts[f'JSON/text difference: {field}, {dd["kind"]}'] += 1
    assert set(records) == {a['analysis_id'] for a in v9['analyses']}, f'{sid}: acquisition records and analyses differ'
    v9.setdefault('study_sections', {})['analytical'] = {'acquisition_context': {k: v for k, v in context.items() if k not in ('records', 'study_id')}}
    path.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return sid, len(v9['analyses']), n_summary, sum(1 for r in records.values() if r.get('modality') == 'MS'), n_filled, prov_counts


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    files = sorted((V9 / 'json').glob('*_v9.json'))
    with Pool(40) as pool: res = pool.map(one, files, chunksize=8)
    prov_total = collections.Counter()
    for r in res: prov_total.update(r[5])
    manifest = json.loads((V9 / 'manifest.json').read_text())
    v8_index = json.loads((V8 / 'index.json').read_text())
    kept = {k: v for k, v in (manifest['sections'].get('analytical') or {}).items() if k in ('qc_blank_controls',)}     # entries of later steps
    manifest['sections']['analytical'] = {
        'status': 'partly filled: two informational blocks; the scored metrics are not filled yet',
        'how': 'copied from the v8 results, not recomputed', 'copied_from': str(V8), 'v8_results_generated_at_utc': v8_index.get('generated_at_utc'),
        'copied_at': started, 'script': str(Path(__file__).resolve()),
        'blocks': {'acquisition_evidence': 'analysis level, informational, unscored (v8 analytical_acquisition_context record, unchanged)',
                   'data_summary': 'analysis level, informational, unscored (v8 per-analysis ingestion record: ' + ', '.join(SUMMARY_FIELDS) + ')'},
        'counts': {'analyses': sum(r[1] for r in res), 'analyses_with_acquisition_evidence': sum(r[1] for r in res), 'ms_analyses': sum(r[3] for r in res),
                   'data_summaries_copied_from_a_v8_summary_row': sum(r[2] for r in res),
                   'data_summaries_filled_from_v8_analysis_metadata_no_valid_matrix': sum(r[4] for r in res)},
        'data_summary_provenance': {'what': 'where the displayed analysis type and units were deposited (mwTab JSON and text file of the analysis in the frozen deposit)',
                                    'analysis_type_sources': TYPE_SOURCES, 'counts': dict(sorted(prov_total.items()))},
        'filled_rows': 'analyses without a valid matrix: fields from ' + str(V8 / '_derived') + '/<study>/__merit_combined.json with the v8 row mapping; '
                       'the mapping reproduces every copied v8 row exactly (asserted at build time)'}
    manifest['sections']['analytical'].update(kept)
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(manifest['sections']['analytical']['counts'])
