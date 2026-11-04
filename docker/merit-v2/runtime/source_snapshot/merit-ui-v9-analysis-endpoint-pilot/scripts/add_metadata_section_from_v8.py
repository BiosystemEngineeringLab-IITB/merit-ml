"""Fill the 'metadata' (Metadata and FAIR reusability) section of the v9 cache from the v8 results. Nothing is recomputed.

The five FAIR metrics of v8 are copied as they are stored in merit-cache-workbench-full-v8 and arranged per analysis:
  study level     study-metadata FAIR compliance (7 checks)
  analysis level  RefMet mapping of deposited metabolites, external cross-references via RefMet, mass/RT-like metadata
                  presence (the per-analysis presence v8 stores; v8 scored it once per study as "any analysis"),
                  basic LC-method context
These metrics are read from the study record, the repository's metabolite records and the mwTab file of the analysis,
never from the measurement values of a matrix, so there is ONE record per analysis and none per endpoint (in v8 every
value is identical in all endpoint reports of an analysis). FAIR score of an analysis = mean of its assessable metrics (the v8 rule: a metric that is not assessable is
left out of the mean).
usage: add_metadata_section_from_v8.py [v9 root]
"""
import json, sys, datetime, collections
from pathlib import Path
from multiprocessing import Pool
B = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot')
V8 = B / 'merit-cache-workbench-full-v8'
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
ORDER = ('fair_study_metadata_compliance', 'fair_metabolite_identifier_resolvability', 'external_cross_references_via_refmet',
         'mass_rt_like_metadata_presence', 'lc_method_context_presence')
SCOPE = {'fair_study_metadata_compliance': 'study', 'mass_rt_like_metadata_presence': 'analysis', 'fair_metabolite_identifier_resolvability': 'analysis',
         'external_cross_references_via_refmet': 'analysis', 'lc_method_context_presence': 'analysis'}

def mass_metric(m):
    """Per-analysis presence as stored in v8: 1 when the metabolite annotation table of this analysis's mwTab file has a populated mass or
    m/z-like, or RT or RI-like, field; 0 when it has none; not assessed when no annotation source of the analysis could be read."""
    p = (m or {}).get('presence') or {}
    readable = bool(p.get('blocks_scanned')) or bool(p.get('json_available'))
    if not m or not readable:
        return {'scope': 'analysis', 'score': None, 'assessable': False, 'reason': 'no mwTab annotation table could be read for this analysis'}
    return {'scope': 'analysis', 'score': 1.0 if p.get('present') else 0.0, 'assessable': True, 'present': bool(p.get('present')), 'category': p.get('category'),
            'fields': sorted((p.get('fields') or {}).keys())[:8] if isinstance(p.get('fields'), dict) else [], 'annotation_blocks_read': p.get('blocks_scanned'),
            'coverage': m.get('coverage'), 'rt_unit_category': (m.get('rt_units') or {}).get('unit_category'), 'adducts_declared': (m.get('reported_adducts') or {}).get('declared')}


def one(path):
    v9 = json.loads(path.read_text()); sid = v9['study_id']
    s = json.loads((V8 / 'json' / f'{sid.lower()}_workflow_state.json').read_text())
    reports = [s.get('final_report')] + [(a or {}).get('report') for a in (s.get('source_assessments') or {}).values()]
    reports = [r for r in reports if r]
    final = {m['name']: m for m in reports[0]['metadata_readiness']}
    per = {k: {} for k in ORDER}                                  # analysis-level details, from every report of the study
    mass = {}
    # endpoint-specific, from the annotation section of each source report: named features of the matrix that carry a RefMet name
    named_refmet = {}; label_classes = {}
    for src, a in (s.get('source_assessments') or {}).items():
        for m in ((a or {}).get('report') or {}).get('annotation_readiness') or []:
            if m.get('name') == 'named_feature_coverage':             # what kind of label each feature of the matrix carries (manuscript label classes)
                for x in (m.get('details') or {}).get('per_analysis') or []:
                    label_classes[(str(x['analysis_id']).upper(), src)] = {'class_counts': x.get('class_counts') or {}, 'n_excluded': x.get('n_excluded'),
                                                                          'class_examples': {k: list(v)[:3] for k, v in (x.get('class_examples') or {}).items()}}
            if m.get('name') == 'refmet_standardized_named_features':
                for x in (m.get('details') or {}).get('per_analysis') or []:
                    named_refmet[(str(x['analysis_id']).upper(), src)] = {'named_features': x.get('n_named'), 'named_features_with_refmet_name': x.get('n_named_with_refmet'), 'share': x.get('share')}
    for rep in reports:
        M = {m['name']: m for m in rep['metadata_readiness']}
        for k in ('fair_metabolite_identifier_resolvability', 'external_cross_references_via_refmet', 'lc_method_context_presence'):
            for x in (M[k]['details'].get('per_analysis') or []):
                per[k].setdefault(str(x['analysis_id']).upper(), x)
        for x in (M['mass_rt_like_metadata_presence']['details'].get('analyses') or []):
            mass.setdefault(str(x['analysis_id']).upper(), x)
    study_meta = final['fair_study_metadata_compliance']; study_mass = final['mass_rt_like_metadata_presence']
    study_block = {
        'fair_study_metadata_compliance': {'score': study_meta['score'], 'status': study_meta['status'], 'passed': study_meta['details']['passed'], 'total': study_meta['details']['total'],
                                           'checks': study_meta['details']['checks'], 'field_evidence': study_meta['details']['field_evidence']},
        'mass_rt_like_metadata_presence': {'score': study_mass['score'], 'status': study_mass['status'], 'present': study_mass['details'].get('present'),
                                           'category': study_mass['details'].get('category'), 'not_assessable': bool(study_mass['details'].get('not_assessable'))},
        'v8_study_reusability_score': (s.get('readiness_score') or {}).get('reusability_score'),
        'v8_study_metric_scores': {k: {'score': final[k]['score'], 'status': final[k]['status']} for k in ORDER},
    }
    n_scored = 0
    for a in v9['analyses']:
        aid = a['analysis_id']
        r = per['fair_metabolite_identifier_resolvability'].get(aid); e = per['external_cross_references_via_refmet'].get(aid); l = per['lc_method_context_presence'].get(aid); m = mass.get(aid) or {}
        metrics = {
            'fair_study_metadata_compliance': {'scope': 'study', 'score': study_meta['score'], 'assessable': True},
            'fair_metabolite_identifier_resolvability': (
                {'scope': 'analysis', 'score': None, 'assessable': False, 'reason': 'this analysis has no valid matrix, so its metabolite records were not assessed'} if r is None else
                {'scope': 'analysis', 'score': r.get('share'), 'assessable': r.get('share') is not None, 'n_records': r.get('n_records'), 'n_valid': r.get('n_valid'),
                 **({} if r.get('share') is not None else {'reason': 'no metabolite record deposited for this analysis'})}),
            'external_cross_references_via_refmet': (
                {'scope': 'analysis', 'score': None, 'assessable': False, 'reason': 'this analysis has no valid matrix, so its metabolite records were not assessed'} if e is None else
                {'scope': 'analysis', 'score': e.get('share'), 'assessable': e.get('share') is not None, 'n_valid_records': e.get('n_valid_records'), 'n_with_any': e.get('n_with_any'),
                 'n_with_all_six': e.get('n_with_all_six'), **({} if e.get('share') is not None else {'reason': 'no record with a valid RefMet name'})}),
            'mass_rt_like_metadata_presence': mass_metric(m),
            'lc_method_context_presence': (
                {'scope': 'analysis', 'score': None, 'assessable': False, 'reason': 'not an LC analysis'} if l is None else
                {'scope': 'analysis', 'score': l.get('context_score'), 'assessable': l.get('context_score') is not None, 'chromatography_type': l.get('chromatography_type'),
                 'column': l.get('column'), 'mobile_phase': l.get('mobile_phase'), 'gradient': l.get('gradient'), 'rt_ri_reported_for_named_metabolites': l.get('rt_ri_reported_for_named_metabolites')}),
        }
        vals = [metrics[k]['score'] for k in ORDER if metrics[k]['assessable'] and metrics[k]['score'] is not None]
        a.setdefault('sections', {})['metadata'] = {'scope': 'analysis: one record per analysis, not per endpoint', 'score': sum(vals) / len(vals) if vals else None,
                                                    'n_assessable_metrics': len(vals), 'metrics': metrics}
        n_scored += bool(vals)
        for src, ep in a['endpoints'].items():                 # not part of this section: per-matrix annotation figures, kept for the Annotation section
            if 'roster' in ep:
                (ep.get('sections') or {}).pop('metadata', None)
                ep.setdefault('annotation_evidence', {}).update({'refmet_names_among_named_features': named_refmet.get((aid, src)),
                                                                 'feature_label_classes': label_classes.get((aid, src))})
    v9.setdefault('study_sections', {})['metadata'] = study_block
    path.write_text(json.dumps(v9, ensure_ascii=False, separators=(',', ':')))
    return sid, len(v9['analyses']), n_scored, sum(1 for a in v9['analyses'] if 'reason' in a['sections']['metadata']['metrics']['fair_metabolite_identifier_resolvability']
                                                  and 'no valid matrix' in a['sections']['metadata']['metrics']['fair_metabolite_identifier_resolvability']['reason'])

if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    files = sorted((V9 / 'json').glob('*_v9.json'))
    with Pool(40) as pool: res = pool.map(one, files, chunksize=8)
    manifest = json.loads((V9 / 'manifest.json').read_text())
    v8_index = json.loads((V8 / 'index.json').read_text())
    manifest['sections']['metadata'] = {
        'status': 'filled', 'how': 'copied from the v8 results, not recomputed', 'copied_from': str(V8), 'v8_results_generated_at_utc': v8_index.get('generated_at_utc'),
        'copied_at': started, 'script': str(Path(__file__).resolve()), 'metrics': {k: SCOPE[k] + ' level' for k in ORDER},
        'analysis_score': 'mean of the assessable metrics of the analysis; one score per analysis, none per endpoint',
        'kept_for_the_annotation_section': 'endpoints[*].annotation_evidence: per-matrix named-feature and RefMet-lookup figures (not part of this section)',
        'counts': {'analyses': sum(r[1] for r in res), 'analyses_with_a_fair_score': sum(r[2] for r in res), 'analyses_not_in_v8_results_for_the_record_based_metrics': sum(r[3] for r in res)}}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(manifest['sections']['metadata']['counts'])
