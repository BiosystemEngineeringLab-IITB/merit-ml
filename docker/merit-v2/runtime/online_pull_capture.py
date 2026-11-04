"""Inventory and retain EVERY output of a newly calculated cohort study.

This module cannot access historical deposits or result caches. It does not
calculate expected values from the baseline. The comparison is a separate phase.
"""
import gzip
import json
import os
import shutil
from pathlib import Path
from online_pull_common import atomic,digest

UI_KEYS=('metadata_fair_per_analysis','analytical_informational_per_analysis','analytical_measurement_scale',
         'analytical_low_information','analytical_redundancy','overview_scores','analytical_outliers',
         'analytical_sample_annotations','analytical_sample_missingness','label_structure','ml_task_readiness',
         'annotation_per_matrix','analytical_qc_blank_controls','structural_analysis_endpoint')
ANALYTICAL=('qc_blank_controls','sample_missingness','measurement_scale','low_information','redundancy','outliers','qc_reproducibility')
FAIR=('fair_study_metadata_compliance','fair_metabolite_identifier_resolvability','external_cross_references_via_refmet',
      'mass_rt_like_metadata_presence','lc_method_context_presence')
GLOBAL_FILES=('index.json','citation_index.json','study_metadata_index.json')


def check_complete(job_root,sid,public):
    job_root=Path(job_root);out=job_root/'assessment';errors=[];refs=[]
    audit=json.loads((job_root/'isolation_audit.json').read_text())
    if audit['failures'] or audit['denied_attempts']:errors.append('calculation stage failure or forbidden read')
    v=json.loads((out/'json'/f'{sid.lower()}_v9.json').read_text())
    if v.get('study_id')!=sid:errors.append('study identity mismatch')
    matrices=0;by_endpoint={k:0 for k in ('datatable','mwtab','untarg_data')}
    for k in UI_KEYS:
        if k not in public:errors.append('UI block absent: '+k)
    for k in ('metadata','analytical'):
        if k not in v.get('study_sections',{}):errors.append('study section absent: '+k)
    for a in v['analyses']:
        aid=a['analysis_id'];asec=a.get('sections',{})
        if 'metadata' not in asec:errors.append(aid+': missing analysis FAIR')
        else:
            for k in FAIR:
                if k not in asec['metadata'].get('metrics',{}):errors.append(aid+': FAIR item absent: '+k)
        for k in ('data_summary','additional_sample_data','sample_table_controls','processing_evidence','acquisition_evidence'):
            if k not in asec.get('analytical',{}):errors.append(aid+': analysis evidence absent: '+k)
        for src,e in a['endpoints'].items():
            if 'roster' not in e:continue
            matrices+=1;by_endpoint[src]+=1;sec=e.get('sections',{})
            for k in ('structural','analytical','annotation','labels','ml'):
                if k not in sec:errors.append(aid+'/'+src+': missing section '+k)
            for k in ANALYTICAL:
                if k not in sec.get('analytical',{}):errors.append(aid+'/'+src+': missing Analytical QC item '+k)
            if src=='datatable' and 'datatable_zero_provenance' not in sec.get('analytical',{}):errors.append(aid+': missing datatable zero provenance')
            for k in ('sample_support','samples_with_factor','numeric_values','unique_sample_ids'):
                if k not in sec.get('structural',{}).get('items',{}):errors.append(aid+'/'+src+': Structural item absent '+k)
            source=job_root/'materialized'/e['file']
            if not source.is_file():errors.append('source table missing: '+str(source))
    def scan(obj,pointer=''):
        if isinstance(obj,dict):
            for k,value in obj.items():
                p=pointer+'/'+k
                if isinstance(value,str) and k in ('file','store','values_file','ranks_file') and value.endswith(('.npz','.npy','.gz')):
                    path=Path(value) if Path(value).is_absolute() else out/value
                    if not path.is_file():errors.append('required numerical store missing: '+p+' -> '+value)
                    else:
                        refs.append(value)
                        if k=='store' and value.endswith('.npz') and '/ml/' in p:
                            rank=Path(str(path).replace('.npz','_ranks.npy'))
                            if not rank.exists():errors.append('separability ranks missing: '+str(rank))
                scan(value,p)
        elif isinstance(obj,list):
            for i,value in enumerate(obj):scan(value,pointer+'/'+str(i))
    scan(v)
    for folder in ('fair_evidence','readiness','acquisition_evidence','design_context','external_dependencies','ui_summaries'):
        suffix='.tsv' if folder=='acquisition_evidence' else '.json'
        if not (out/folder/f'{sid.lower()}{suffix}').is_file():errors.append('required evidence absent: '+folder)
    if errors:raise ValueError('\n'.join(errors))
    return {'study_id':sid,'analyses':len(v['analyses']),'valid_matrices':matrices,'by_endpoint':by_endpoint,
            'ui_blocks':len(UI_KEYS),'required_store_references':len(refs),'complete':True,
            'explicitly_unassessed_ui_blocks':[k for k in UI_KEYS if public[k] is None],
            'language_model_design_reading':'not_run; live programmatic rules only, never a historical copied label'}


def link_file(source,target,replace=False):
    source=Path(source);target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if source.stat().st_ino==target.stat().st_ino or digest(source)==digest(target):return
        if not replace:raise ValueError('Conflicting aggregate output path: '+str(target))
        tmp=target.with_name(target.name+f'.{os.getpid()}.tmp')
        if tmp.exists():tmp.unlink()
        try:os.link(source,tmp)
        except OSError:shutil.copy2(source,tmp)
        tmp.replace(target);return
    try:os.link(source,target)
    except OSError:shutil.copy2(source,target)


def merge_global(source,target):
    new=json.loads(source.read_text());old=json.loads(target.read_text()) if target.exists() else {}
    if source.name=='study_metadata_index.json':
        rows={x['study_id']:x for x in old.get('studies',[])};rows.update({x['study_id']:x for x in new['studies']})
        old.update(new);old['studies']=[rows[k] for k in sorted(rows)]
    else:
        rows={**old.get('studies',{}),**new.get('studies',{})};old.update(new);old['studies']=rows
    atomic(target,old)


def capture(cohort,job_root,sid,public):
    cohort=Path(cohort);job_root=Path(job_root);check=check_complete(job_root,sid,public)
    cache=cohort/'online_pull_v9_compatible_cache';out=job_root/'assessment';inventory=[]
    # Every file is retained; no scored/informational folder whitelist.
    for p in sorted(out.rglob('*')):
        if not p.is_file():continue
        rel=p.relative_to(out);target=cache/rel
        if str(rel) in GLOBAL_FILES:merge_global(p,target)
        else:link_file(p,target,replace=sid.lower() in str(rel).lower())
        inventory.append({'file':str(rel),'bytes':p.stat().st_size,'sha256':digest(p)})
    ui_path=cohort/'online_pull_ui_exports'/f'{sid.lower()}_ui_state.json.gz';ui_path.parent.mkdir(exist_ok=True)
    with ui_path.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as f:f.write(json.dumps(public,ensure_ascii=False,separators=(',',':')).encode())
    for folder,target in (('inputs','online_pull_downloads'),('materialized','online_pull_materialized')):
        dest=cohort/target/sid;dest.parent.mkdir(exist_ok=True)
        link=os.path.relpath(job_root/folder/sid,dest.parent)
        if dest.is_symlink() and os.readlink(dest)!=link:dest.unlink()
        if not dest.exists():dest.symlink_to(link,target_is_directory=True)
    meta={**check,'job_root':str(job_root),'ui_export':str(ui_path.relative_to(cohort)),
          'assessment_files':inventory,'calculation_inputs':'fresh online HTTP responses only',
          'same_cohort_shared_reference':True,'defaults_only':True}
    atomic(cohort/'online_pull_study_manifests'/f'{sid.lower()}.json',meta)
    return meta
