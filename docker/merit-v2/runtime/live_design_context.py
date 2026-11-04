"""Execute the original programmatic design rules on fresh MW evidence.

No reviewed labels, spreadsheets, historical raw bundles or prior model answers
are loaded. The original independent language-model reading is a separate step;
an unavailable reading is not represented as agreement or low confidence.
"""
import collections
import hashlib
import json
from pathlib import Path


def compute(run,study_id,raw_study):
    import online_assess as oa
    from fetch_online import records
    run=Path(run);live=run/'materialized';out=run/'assessment'
    base=oa.BASE/'outputs/study_design_sanity_check/auditor_v1_2026-05-30'
    auditor=oa.load('live_design_auditor',base/'auditor_v11_2026-06-01.py',out)
    resolver=oa.load('live_design_resolver',base/'resolve_v11_2026-06-01.py',out)
    extractor=oa.load('live_design_extractor',base/'extract_mwtab_features_2026-06-01.py',out)
    # Preserve the original harvester's expected layout using links to this
    # job's freshly materialized responses; no historical path is read.
    for a in raw_study['analyses']:
        p=live/study_id/a['analysis_id'];alias=p/'json'
        if not alias.exists():alias.symlink_to('mwtab',target_is_directory=True)
    mw=extractor.harvest_one(live/study_id)
    summary_rows=records(json.loads((live/study_id/'summary.json').read_text()))
    summary=summary_rows[0] if summary_rows else {}
    factors=json.loads((live/study_id/'factors.json').read_text())
    signals=auditor.extract_signals({'raw_endpoints':{'summary':summary,'factors':factors}})
    combined=json.loads((live/study_id/'__merit_combined_online.json').read_text())
    measured=set();eligible={};matrices=0
    for a in raw_study['analyses']:
        for e in a['endpoints'].values():
            if 'roster' not in e:continue
            matrices+=1
            for r in e['roster']:
                if r[3]:continue
                measured.add(r[0])
                if not r[2]:eligible.setdefault(r[0],r[5] if r[4] else r[1])
    classes=collections.Counter(v for v in eligible.values() if v and v!='-')
    row={'study_id':study_id,'title':summary.get('study_title',''),'description':combined.get('project_summary') or combined.get('collection_summary') or '',
         'organism':summary.get('species',''),'n_total_samples':len(measured),'n_ml_eligible_samples':len(eligible),
         'n_classes':len(classes),'class_counts':json.dumps(dict(classes)),'n_feature_matrices':matrices,
         'factor_variables':' | '.join(signals['factor_names']),'sample_sources_top':' | '.join(signals['sample_sources']),
         'tissues_or_sample_matrices':' | '.join(signals['sample_sources']),
         'n_distinct_factor_combinations':signals['n_distinct_factor_combinations'],
         'n_unique_subjects_inferred':signals['n_unique_subjects_inferred'],
         'samples_per_subject_median_inferred':signals['samples_per_subject_median'],**mw}
    result=auditor.classify(signals,row,mw)
    resolver_input={**row,'v11_auditor_label':result['auditor_label'],
                    'v11_auditor_confidence':result['auditor_confidence'],'v11_auditor_evidence':result['auditor_evidence'],
                    'llm_primary_design_context':'','llm_confidence':'','v11_consensus_label':'',
                    'v11_agreement_with_llm':'not_assessed'}
    provisional=resolver.resolve(resolver_input)
    return {'study_id':study_id,'label':result['auditor_label'],'confidence':result['auditor_confidence'],
            'decided_by':'live_programmatic_rules','reason':result['auditor_reason'],
            'rule_based_label':result['auditor_label'],'rule_evidence':result['auditor_evidence'].split(' ; '),
            'rule_evidence_inputs':row,'evidence_types':[],
            'language_model_label':None,'language_model_status':'not_run',
            'consensus_status':'not_assessed: independent model reading unavailable',
            'resolver_without_model':provisional,'scope':'rule-based reading only, not the reviewed historical two-reader consensus',
            'source':'original programmatic classifier and field harvester executed on fresh MW responses',
            'source_code_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                base/'auditor_v11_2026-06-01.py',base/'resolve_v11_2026-06-01.py',base/'extract_mwtab_features_2026-06-01.py')},
            'historical_results_used':False}
