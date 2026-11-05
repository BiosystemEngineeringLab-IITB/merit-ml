"""Fill "Processing evidence (as deposited)" of every analysis in the v9 cache (Analytical QC, Sample eligibility & analytical
controls; informational, not scored). Source: the accepted Reviewer 1 comment 5 audit of the 18 September dump
(merit/manuscript/manuscript-original-reviews/reviewer-1/comment5_preprocessing_parameters_and_software_evidence/):
  every_dedicated_field_declaration.tsv     values of the dedicated fields (text file first, else JSON; placeholders left out)
  per_analysis_processing_evidence.tsv      software or services named, processing steps named, source-header identity status
  raw_minfrac_independent_contexts.json     the six minFraction / minfrac mentions with their wording
Recount check (outputs/preprocessing_evidence_recount_claude_20261018/recount.py, own code): dedicated-field counts equal the
audit's for 10 of 11 fields (ANALYSIS_DETAILS 26 vs 24); software names equal or within a few analyses; minfrac 6 confirmed.
analyses[*].sections.analytical.processing_evidence = {dedicated: [{field, label, value}], software: [...], steps: [...],
minfrac: [quote], header_status, header_note}. Analysis level (the same for every endpoint).
usage: add_processing_evidence_from_audit.py [v9 root]
"""
import collections, csv, datetime, json, re, sys
from pathlib import Path
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
A = B / 'merit/manuscript/manuscript-original-reviews/reviewer-1/comment5_preprocessing_parameters_and_software_evidence'
csv.field_size_limit(10 ** 9)
PLACEHOLDER = {'', '-', '--', 'na', 'n/a', 'n.a.', 'none', 'null', 'not applicable', 'not available', 'not provided', 'unknown', 'no', 'n.a'}
# dedicated fields shown, in this order (SAMPLEPREP:PROCESSING_METHOD is physical sample preparation and is not shown here)
FIELDS = [('AN:SOFTWARE_VERSION', 'Software version'), ('AN:PROCESSING_PARAMETERS_FILE', 'Processing parameters file (named; contents not read)'),
          ('AN:ANALYSIS_DETAILS', 'Analysis details'), ('NM:BASELINE_CORRECTION_METHOD', 'Baseline correction'), ('NM:APODIZATION', 'Apodization'),
          ('NM:LINE_BROADENING', 'Line broadening'), ('NM:ZERO_FILLING', 'Zero filling'), ('NM:BINNED_INCREMENT', 'Binned increment'),
          ('NM:BINNED_DATA_EXCLUDED_RANGE', 'Binned data excluded range'), ('NM:BINNED_DATA_NORMALIZATION_METHOD', 'Binned data normalization')]
CANON = {'ANALYSIS': 'AN', 'SAMPLEPREP': 'SP'}


def canon(field):
    sec, _, name = field.partition(':')
    return f'{CANON.get(sec, sec)}:{name}'


def main():
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    vals = collections.defaultdict(lambda: collections.defaultdict(lambda: {'txt': [], 'json': []}))
    for r in csv.DictReader(open(A / 'every_dedicated_field_declaration.tsv'), delimiter='\t'):
        v = re.sub(r'\s+', ' ', r['value'] or '').strip()
        if v.casefold() in PLACEHOLDER:
            continue
        lst = vals[(r['study_id'], r['analysis_id'])][canon(r['field'])][r['format'] if r['format'] in ('txt', 'json') else 'json']
        if v not in lst:
            lst.append(v)
    per = {(r['study_id'], r['analysis_id']): r for r in csv.DictReader(open(A / 'per_analysis_processing_evidence.tsv'), delimiter='\t')}
    minfrac = collections.defaultdict(list)
    for r in json.load(open(A / 'raw_minfrac_independent_contexts.json')):
        ctx = re.sub(r'\n[A-Z]{2}:[A-Z_]+\s*\t', ' ', r['literal_context']); ctx = re.sub(r'\s+', ' ', ctx)
        i = ctx.find(r['exact_match'])
        quote = ctx[max(0, i - 50):i + 70].strip()
        minfrac[(r['study_id'], r['analysis_id'])].append(quote)
    c = collections.Counter(); missing = []
    for path in sorted((V9 / 'json').glob('*_v9.json')):
        v9 = json.loads(path.read_text()); sid = v9['study_id']
        for a in v9['analyses']:
            key = (sid, a['analysis_id']); p = per.get(key)
            if p is None:
                missing.append(key); continue
            ded = []
            for f, label in FIELDS:
                x = vals[key].get(f)
                if x and (x['txt'] or x['json']):
                    ded.append({'field': f, 'label': label, 'value': ' | '.join(x['txt'] or x['json']),
                                'read_from': 'mwTab text file' if x['txt'] else 'mwTab JSON'})
            status = p['source_header_identity_status']
            rec = {'dedicated': ded, 'software': json.loads(p['software_resource_service_mentions'] or '[]'),
                   'steps': json.loads(p['stage_mentions'] or '[]'), 'minfrac': minfrac.get(key, []), 'header_status': status,
                   'files': 'none' if p['txt_present'] != 'True' and p['json_present'] != 'True' else
                            ('mwTab JSON only' if p['txt_present'] != 'True' else 'mwTab text and JSON')}
            a.setdefault('sections', {}).setdefault('analytical', {})['processing_evidence'] = rec
            c['analyses'] += 1; c['with a dedicated field'] += bool(ded); c['with software named'] += bool(rec['software'])
            c['software version'] += any(d['field'] == 'AN:SOFTWARE_VERSION' for d in ded)
            c['processing parameters file'] += any(d['field'] == 'AN:PROCESSING_PARAMETERS_FILE' for d in ded)
            c['minfrac'] += bool(rec['minfrac']); c['header conflict'] += status.startswith('conflict')
        path.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    if missing:
        sys.exit(f'{len(missing)} analyses not in the audit, e.g. {missing[:5]}')
    manifest = json.loads((V9 / 'manifest.json').read_text())
    manifest['sections'].setdefault('analytical', {})['processing_evidence'] = {
        'status': 'filled', 'scored': False, 'source': str(A), 'computed_at': started, 'script': str(Path(__file__).resolve()),
        'counts': dict(c)}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    for k, n in c.items(): print(f'{n:6,}  {k}')


if __name__ == '__main__':
    main()
