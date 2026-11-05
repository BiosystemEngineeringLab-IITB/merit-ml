"""Keep manifest.json of the v9 cache equal to the stored records (decided by the author on 2026-10-24: "everything should match").

Every count in the manifest is recounted here from the study files (json/*_v9.json) and the side folders, with the definition the
builder that wrote it used (the builder and line are named beside each count below). Also checked:
  - a section whose records exist for every valid matrix (or analysis) has status 'filled';
  - every script / refreshed_by path named in the manifest exists and is not a temporary or scratch path;
  - index.json lists every study file with its analyses, valid matrices and Structural-scored matrices as stored;
  - outputs/redundant_features_v9_claude_20261018/per_matrix.tsv (kept by the rebuild scripts) has the stored score of every matrix;
  - the readiness reference in the manifest equals the version in force (readiness_reference/current.json and <version>.json), and the
    current missingness, low-information, redundancy and outlier scores of that version's own matrices reproduce its cut-offs at every
    kept percentile (scripts/add_readiness_reference.py); matrices outside the reference are judged against it, not added to it.
usage: manifest_sync.py            check only: one line per difference, exit 1 if any (validation step 26)
       manifest_sync.py --write    set every count to the recounted value, record recounted_at, then check again
       --root PATH                 another cache folder (a portable rebuild); default: the cache the interface reads
       --redundancy-tsv PATH       the redundancy sensitivity table kept for that cache; without it, a cache other than the default
                                   skips that one comparison and says so
Run --write at the end of every script that changes stored records (builders, refreshes, partial rebuilds, curated changes).
"""
import collections, datetime, json, re, sys, zipfile
import numpy as np
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
DEFAULT_V9 = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-cache-workbench-full-v9')


def _opt(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv[:-1] else None


V9 = Path(_opt('--root')).resolve() if _opt('--root') else DEFAULT_V9           # never the default cache when another root is given
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
SRC = ('datatable', 'mwtab', 'untarg_data')
DEFAULT_REDUNDANCY_TSV = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/outputs/redundant_features_v9_claude_20261018/per_matrix.tsv')
REDUNDANCY_TSV = Path(_opt('--redundancy-tsv')) if _opt('--redundancy-tsv') else (DEFAULT_REDUNDANCY_TSV if V9 == DEFAULT_V9 else None)


def one(path):
    from merit import qc_reproducibility_v9 as qr
    v = json.loads(Path(path).read_text()); sid = v['study_id']; C = collections.defaultdict(collections.Counter)
    top, md, fe, an, dsp, qcb, mis, sca, low, pe, red, out, qrp, ann, ml, lab = (C[k] for k in (
        'top', 'metadata', 'metadata.fair_evidence', 'analytical', 'analytical.data_summary_provenance', 'analytical.qc_blank_controls',
        'analytical.sample_missingness', 'analytical.measurement_scale', 'analytical.low_information', 'analytical.processing_evidence',
        'analytical.redundancy', 'analytical.outliers', 'analytical.qc_reproducibility', 'annotation', 'ml', 'labels'))
    top['studies'] += 1; idx = {'analyses': len(v['analyses']), 'valid_matrices': 0, 'structural_scored_matrices': 0}
    fair = json.loads((V9 / 'fair_evidence' / f'{sid.lower()}.json').read_text()) if (V9 / 'fair_evidence' / f'{sid.lower()}.json').is_file() else {}
    for aid, ev in fair.items():                                   # add_fair_evidence_from_deposit.py 134-156
        fe['analyses'] += 1; fe['external IDs not assessed (no valid matrix)'] += ev['external_ids'] is None
        fe['mass/RT: more than 8 fields (stored list truncated, all shown)'] += len((ev['mass_rt'] or {}).get('fields', [])) > 8
    for a in v['analyses']:
        top['analyses'] += 1; s = a['sections']
        m = s['metadata']                                          # add_metadata_section_from_v8.py 104-108
        md['analyses'] += 1; md['analyses_with_a_fair_score'] += m['n_assessable_metrics'] > 0
        r = m['metrics']['fair_metabolite_identifier_resolvability']
        md['analyses_not_in_v8_results_for_the_record_based_metrics'] += 'no valid matrix' in (r.get('reason') or '')
        x = s['analytical']                                        # add_analytical_section_from_v8.py 213-249
        an['analyses'] += 1; an['analyses_with_acquisition_evidence'] += bool(x.get('acquisition_evidence'))
        an['ms_analyses'] += (x.get('acquisition_evidence') or {}).get('modality') == 'MS'
        an['data_summaries_copied_from_a_v8_summary_row'] += x['data_summary_source'] == 'per-analysis summary row'
        an['data_summaries_filled_from_v8_analysis_metadata_no_valid_matrix'] += x['data_summary_source'] != 'per-analysis summary row'
        p = x['data_summary_provenance']; decl = p['results_file_declarations']
        dsp['type: ' + p['analysis_type_source']] += 1; dsp['units: ' + (p['units_source'] or 'none declared')] += 1
        dsp['read from: ' + p['metadata_read_from']] += 1; dsp['units that are a placeholder'] += p['units_is_placeholder']
        dsp['units read from the JSON, no text file'] += bool(p['units_read_from'] == 'mwTab JSON' and not p['mwtab_text_file'])
        dsp['analyses with a results-file line'] += bool(decl); dsp['analyses with more than one distinct results-file line'] += len(decl) > 1
        dsp['analyses with results-file UNITS (tab attribute)'] += any(d['units'] for d in decl)
        dsp['analyses with results-file UNITS that are a placeholder'] += any(d['units_is_placeholder'] for d in decl)
        dsp['analyses with UNITS written inside the file-name cell only'] += (not any(d['units'] for d in decl)) and any(d['units_written_inside_file_name_cell'] for d in decl)
        dsp['analyses with a JSON/text difference in a displayed field'] += bool(p['json_text_differences'])
        for field, dd in p['json_text_differences'].items(): dsp[f'JSON/text difference: {field}, {dd["kind"]}'] += 1
        pr = x['processing_evidence']; ded = pr['dedicated']        # add_processing_evidence_from_audit.py 69-72
        pe['analyses'] += 1; pe['with a dedicated field'] += bool(ded); pe['with software named'] += bool(pr['software'])
        pe['software version'] += any(d['field'] == 'AN:SOFTWARE_VERSION' for d in ded)
        pe['processing parameters file'] += any(d['field'] == 'AN:PROCESSING_PARAMETERS_FILE' for d in ded)
        pe['minfrac'] += bool(pr['minfrac']); pe['header conflict'] += str(pr['header_status']).startswith('conflict')
        table = x.get('sample_table_controls'); table = None if 'note' in (table or {}) else table      # no mwTab text file: None in the builder
        extra = x['additional_sample_data']; has_qc = False   # add_qc_blank_controls_from_rosters.py 52-66
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            top['valid_matrices'] += 1; sec = ep['sections']; idx['valid_matrices'] += 1
            top['structural_scored_matrices'] += sec['structural']['score'] is not None; idx['structural_scored_matrices'] += sec['structural']['score'] is not None
            q = sec['analytical']['qc_blank_controls']; has_qc = has_qc or bool(q['qc_like_positions'])
            qcb['matrices'] += 1; qcb[f"presence: {q['presence']}"] += 1
            qcb['matrices with QC-like positions'] += bool(q['qc_like_positions']); qcb['matrices with blank-like positions'] += bool(q['blank_like_positions'])
            qcb['matrices with standard-like positions'] += bool(q['standard_like_positions']); qcb['standard-like positions'] += q['standard_like_positions']
            if table and table['qc_like_records'] and not q['qc_like_positions']: qcb['matrices without QC-like positions whose sample table lists QC-like records'] += 1
            if table and table['blank_like_records'] and not q['blank_like_positions']: qcb['matrices without blank-like positions whose sample table lists blank-like records'] += 1
            r = sec['analytical']['sample_missingness']           # add_missingness_from_deposit.py 82-84
            mis['matrices'] += 1; mis[f'{src}: matrices'] += 1; mis[f'{src}: scored'] += r['score'] is not None
            mis[f'{src}: paired with mwTab (items I3, I4)'] += 'zero_provenance' in ep
            mis[f'{src}: datatable zero provenance'] += bool(src == 'datatable' and sec['analytical'].get('datatable_zero_provenance'))
            r = sec['analytical']['measurement_scale']            # add_scale_from_deposit.py 85, 103
            sca[f'{src}: matrices'] += 1; sca[f'{src}: units declared'] += bool(r['units']['declared'])
            r = sec['analytical']['low_information']              # add_low_information_from_deposit.py 82-85
            low[f'{src}: matrices'] += 1; low[f'{src}: stored features'] += r['stored_features']
            low[f'{src}: scored'] += r['default']['score'] is not None; low[f'{src}: relative spread checked'] += bool(r['cv_gate']['checked'])
            low[f'{src}: matrices with a feature below CV 1%'] += bool(r['default']['cv'].get('features_below'))
            r = sec['analytical']['redundancy']['default']         # add_redundancy_from_deposit.py 144-145 (default = cut-off 0.90)
            C['_redundancy_scores'][(sid, a['analysis_id'], src)] = r['score']
            C['_readiness_values'][(sid, a['analysis_id'], src)] = (sec['analytical']['sample_missingness'].get('score'),
                                                                    sec['analytical']['low_information']['default'].get('score'),
                                                                    sec['analytical']['redundancy']['default'].get('score'),
                                                                    sec['analytical']['outliers']['default'].get('score'))
            red[f'{src}: matrices'] += 1; red[f'{src}: scored'] += r['score'] is not None
            r = sec['analytical']['outliers']['default']           # add_outliers_from_deposit.py 40
            out[f'{src}: matrices'] += 1; out[f'{src}: scored'] += r['score'] is not None; out[f'{src}: outlier samples'] += r['outliers']
            r = sec['analytical']['qc_reproducibility']            # add_qc_reproducibility_from_deposit.py 36, 62-63
            qrp[f'{src}: matrices'] += 1; qrp[f'{src}: shown'] += bool(r['default']['shown'])
            qrp[f'{src}: >= 3 injections of one QC material'] += len(qr.qc_group(ep['roster'], None, sid)[1]) >= qr.MIN_INJECTIONS
            r = sec['annotation']                                  # add_annotation_from_deposit.py 37-40
            ann[f'{src}: matrices'] += 1; ann[f'{src}: coverage scored'] += r['coverage']['score'] is not None
            ann[f'{src}: RefMet mapping scored'] += r['refmet']['score'] is not None; ann[f'{src}: with repeated names'] += r['repeats']['names'] > 0
            r = sec['ml']                                          # add_separability_from_deposit.py 74-75 (store type read from the store)
            ml[f'{src}: matrices'] += 1; ml[f'{src}: scored'] += r['default']['score'] is not None
            with zipfile.ZipFile(V9 / r['store']) as z:
                ml[f'{src}: full distances' if 'dist.npy' in z.namelist() else f'{src}: nearest-neighbour lists'] += 1
            r = sec['labels']                                      # add_labels_from_rosters.py 35-38
            lab[f'{src}: matrices'] += 1; lab[f'{src}: scored'] += r['class_support'] is not None
            lab[f'{src}: cross-validation feasible (5 folds)'] += r['cv_feasible'] == 100.0
            lab[f'{src}: sample IDs with conflicting sample-table lines'] += len(ep.get('sample_table_conflicts') or {})
        qcb['analyses'] += 1; qcb['analyses with QC-like positions in a valid matrix'] += has_qc
        qcb[f"additional sample data read from: {extra['read_from']}"] += 1
        qcb['analyses with additional sample data reported'] += bool(extra['fields']); qcb['additional sample data fields reported'] += len(extra['fields'])
        qcb['analyses with QC-like positions and additional sample data reported'] += has_qc and bool(extra['fields'])
        for y in extra['not_repeated']: qcb['fields not repeated: ' + re.sub(r'the factor .*', 'a factor', y['reason'])] += 1
        if table:
            qcb['sample-table records'] += table['records']; qcb['sample-table QC-like records'] += table['qc_like_records']
            qcb['sample-table blank-like records'] += table['blank_like_records']; qcb['sample-table standard-like records'] += table['standard_like_records']
    return {k: dict(c) for k, c in C.items()}, (sid, idx)


def recount():
    total = collections.defaultdict(collections.Counter); index = {}
    with Pool(40) as pool:
        for part, (sid, idx) in pool.imap_unordered(one, sorted((V9 / 'json').glob('*_v9.json')), chunksize=8):
            index[sid] = idx
            for k, c in part.items():
                dict.update(total[k], c) if k.startswith('_') else total[k].update(c)   # '_' blocks are values per matrix, not counts
    return total, index


def node(manifest, key):
    if key == 'top':
        return manifest
    d = manifest['sections']
    for part in key.split('.'):
        d = d[part]
    return d


def check(manifest, total, index, stored_index):
    diffs = []
    if sorted(stored_index) != sorted(index):
        diffs.append(f'DIFF index.json: {len(stored_index)} studies listed vs {len(index)} study files')
    for sid, idx in sorted(index.items()):
        got = {k: stored_index.get(sid, {}).get(k) for k in idx}
        if got != idx:
            diffs.append(f'DIFF index.json {sid}: {got} vs records {idx}')
    import csv                                                 # the sensitivity table that rebuilds maintain = the stored defaults
    if REDUNDANCY_TSV is None:
        print(f'NOTE redundancy sensitivity table not compared: none given for {V9} (use --redundancy-tsv)')
        tsv = None
    else:
        tsv = {(r['study_id'], r['analysis_id'], r['endpoint']): r['score_0.90'] for r in
               csv.DictReader(open(REDUNDANCY_TSV), delimiter='\t')}
    rec = total['_redundancy_scores']
    if tsv is not None:
        bad = [k for k in set(tsv) | set(rec) if k not in tsv or k not in rec or (tsv[k] == '') != (rec[k] is None)
               or (rec[k] is not None and abs(float(tsv[k]) - rec[k]) > 1e-9)]
        if bad:
            diffs.append(f'DIFF {REDUNDANCY_TSV.name} (redundant features, cut-off 0.90): {len(bad)} matrices differ from the stored records, e.g. {sorted(bad)[:3]}')
    ready = manifest['sections'].get('readiness')
    if ready:                                                  # readiness reference: the manifest = the version file in force, and the
        ref_dir = V9 / 'readiness_reference'                    # current records of the version's own matrices reproduce its cut-offs
        cur = json.loads((ref_dir / 'current.json').read_text())['version'] if (ref_dir / 'current.json').exists() else None
        if cur is None or ready.get('reference_version') != cur:
            diffs.append(f"DIFF readiness reference: manifest version {ready.get('reference_version')!r} vs version in force {cur!r}")
        else:
            ver = json.loads((ref_dir / f'{cur}.json').read_text())
            for k, v in ver.items():
                if k != 'matrices' and ready.get(k) != v:
                    diffs.append(f"DIFF readiness reference {k}: manifest {str(ready.get(k))[:80]} vs {cur}.json {str(v)[:80]}")
            mine = {tuple(m[:3]): m[3:] for m in ver['matrices']}
            rec = total['_readiness_values']
            missing = [k for k in mine if k not in rec]
            if missing:
                diffs.append(f"DIFF readiness reference {cur}: {len(missing):,} of its matrices are not in the records, e.g. {missing[:3]}")
            vals = [rec[k] for k in mine if k in rec]
            for i, k in enumerate(('missingness', 'low_information', 'redundancy', 'outliers')):
                x = np.array([v[i] for v in vals if v[i] is not None], dtype=float)
                if ver['n_matrices_with_a_value'].get(k) != x.size:
                    diffs.append(f"DIFF readiness reference {cur} {k}: {ver['n_matrices_with_a_value'].get(k)} values in the version vs {x.size} in the records")
                for p, block in (ver.get('sensitivity') or {}).items():         # the cut-offs at every Show tools percentile
                    got = (block.get('cutoffs') or {}).get(k)
                    if got is None or abs(float(np.percentile(x, float(p))) - got) > 1e-9:
                        diffs.append(f"DIFF readiness cut-off {k} at the {p}th percentile: {cur} {got} vs records {float(np.percentile(x, float(p)))}")
    for key, c in sorted(total.items()):
        if key.startswith('_'):
            continue
        have = node(manifest, key).get('counts', {})
        for k in sorted(set(have) | set(c)):
            want = c.get(k, 0)
            if k not in have and want == 0:
                continue                                           # the builders leave out counts that never occurred
            if have.get(k) != want:
                diffs.append(f'DIFF counts {key}: {k!r} manifest {have.get(k)} vs records {want}')
    n = total['top']['valid_matrices']
    per_matrix = {'annotation': 'annotation', 'ml': 'ml', 'labels': 'labels'}
    for sec in per_matrix:
        if total[sec].get('datatable: matrices', 0) + total[sec].get('mwtab: matrices', 0) + total[sec].get('untarg_data: matrices', 0) == n \
                and manifest['sections'][sec].get('status') != 'filled':
            diffs.append(f'DIFF status {sec}: {manifest["sections"][sec].get("status")!r} but every valid matrix has a record')
    a = manifest['sections']['analytical']
    for k in ('qc_blank_controls', 'sample_missingness', 'measurement_scale', 'low_information', 'redundancy', 'outliers', 'qc_reproducibility', 'processing_evidence'):
        if (a.get(k) or {}).get('status') != 'filled':
            diffs.append(f'DIFF status analytical.{k}: {(a.get(k) or {}).get("status")!r}')
    if 'not filled' in str(a.get('status')):
        diffs.append(f'DIFF status analytical: {a.get("status")!r} but every analytical metric is filled')
    for sec, d in manifest['sections'].items():
        if d.get('status') == 'not filled yet':
            diffs.append(f'DIFF status {sec}: "not filled yet" for a section that no record uses')

    def paths(d, where):
        for k, x in d.items():
            if isinstance(x, dict):
                yield from paths(x, f'{where}.{k}')
            elif k in ('script', 'refreshed_by') and isinstance(x, str):
                yield f'{where}.{k}', x
    for where, p in paths(manifest, 'manifest'):
        if p.startswith('/tmp') or 'scratchpad' in p or not Path(p).is_file():
            diffs.append(f'DIFF path {where}: {p} (missing, temporary or scratch)')
    return diffs


if __name__ == '__main__':
    manifest = json.loads((V9 / 'manifest.json').read_text()); stored_index = json.loads((V9 / 'index.json').read_text())
    total, index = recount()
    if '--write' in sys.argv:
        for key, c in total.items():
            if key.startswith('_'):
                continue
            d = node(manifest, key)
            d['counts'] = dict(sorted((k, x) for k, x in c.items() if x or k in d.get('counts', {})))
        for sid, idx in index.items():
            stored_index['studies'].setdefault(sid, {'path': f'json/{sid.lower()}_v9.json'}).update(idx)
        stored_index['studies'] = dict(sorted(stored_index['studies'].items()))
        (V9 / 'index.json').write_text(json.dumps(stored_index, indent=1))
        manifest['counts_recounted'] = {'at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'by': str(Path(__file__).resolve()),
                                        'rule': 'every count recounted from the stored records with the definition of the builder that wrote it'}
        (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    diffs = check(manifest, total, index, stored_index['studies'])
    for d in diffs:
        print(d)
    print(f"RESULT: {'manifest = records' if not diffs else f'{len(diffs)} differences'} "
          f"({sum(len(c) for k, c in total.items() if not k.startswith('_'))} counts in {sum(1 for k in total if not k.startswith('_'))} manifest blocks recounted from {total['top']['studies']:,} study files)")
    sys.exit(1 if diffs else 0)
