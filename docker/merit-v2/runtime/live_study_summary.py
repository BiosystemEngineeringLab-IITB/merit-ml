"""Rebuild the current UI's descriptive study headers using its original code.

Only freshly fetched source tables and metadata are normalized. No old report,
source preference, assessment score or study-level band is loaded or computed.
"""
import json
from pathlib import Path


def compute(run,study_id,raw_study):
    from merit.connectors.workbench import MetabolomicsWorkbenchConnector
    from merit.assessment import summarize_study
    from merit.workflow import _tier1_alignment_for_bundle,_remove_alignment_rows,_has_numeric_value
    run=Path(run);live=run/'materialized';combined=live/study_id/'__merit_combined_online.json'
    files=[str(combined)];resolution=[]
    for a in raw_study['analyses']:
        aid=a['analysis_id']
        for src,e in a['endpoints'].items():
            if 'roster' not in e:continue
            path=live/e['file'];kind='untarg' if src=='untarg_data' else src
            files.append(str(path));resolution.append({'analysis_id':aid,'selected_path':str(path),'selected_name':path.name,'selected_kind':kind})
    bundle={'study_id':study_id,'connector':'metabolomics_workbench','json_path':str(combined),
            'file_manifest':files,'tabular_resolution':resolution,'source_root':str(live)}
    connector=MetabolomicsWorkbenchConnector();summaries={}
    for src in ('datatable','mwtab','untarg_data'):
        kind='untarg' if src=='untarg_data' else src
        if not any(r['selected_kind']==kind for r in resolution):continue
        canonical=connector.normalize_bundle({**bundle,'_source_filter':{kind}})
        canonical.feature_matrices=[m for m in canonical.feature_matrices if _has_numeric_value(m)]
        if src=='datatable':
            alignment=_tier1_alignment_for_bundle(bundle,canonical)
            if alignment:_remove_alignment_rows(canonical,alignment)
        ids={str(s).strip() for m in canonical.feature_matrices for s in m.sample_ids}
        canonical.samples=[s for s in canonical.samples if str(s.sample_id).strip() in ids]
        summaries[src]=summarize_study(canonical)
    return summaries
