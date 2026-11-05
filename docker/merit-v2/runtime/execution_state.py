"""Shared provenance and merge rules for successful, failed and browser-limited inputs."""
from __future__ import annotations
import copy
import datetime
import json
import re
from pathlib import Path

SOURCES=('datatable','mwtab','untarg_data')
STAGES=('controls','missingness','scale','low_information','redundancy','outliers','qc_reproducibility','annotation','refmet_lookup','labels','separability')


def input_failures(run):
    p=Path(run)/'inputs/fetch_manifest.json'
    if not p.exists():return []
    return [x for x in json.loads(p.read_text()).get('requests',[]) if x.get('error') or x.get('resource_refusal')]


def input_block(run,sid,aid,source):
    for r in input_failures(run):
        name=r.get('file','')
        if not name.startswith(f'{sid}/{aid}/'):continue
        direct=(source=='datatable' and name.endswith('_datatable_slash.txt') or
                source=='mwtab' and name.endswith('_mwtab.txt') or
                source=='untarg_data' and name.endswith('_untarg_data.tsv'))
        dependency=name.endswith('_mwtab.txt') and not r.get('metadata_only_view')
        if direct or dependency:
            return {'state':'not_assessed','code':'input_fetch_failed','reason':
                f"Required input {name.rsplit('/',1)[-1]} could not be retrieved: {r.get('error') or r.get('resource_refusal')}",
                'failed_input':name,'http_status':r.get('http_status'),'source_present':None}
    return None


def shared_metadata_block(run,sid):
    names={f'{sid}/{k}.json' for k in ('summary','factors','metabolites')}
    failed=[r for r in input_failures(run) if r.get('file') in names or r.get('file','').startswith(sid+'/') and r.get('file','').endswith('_mwtab.json')]
    return ('Metadata checks could not be completed because these inputs were not retrieved: '+', '.join(r['file'] for r in failed)) if failed else None


def unavailable(reason, *, availability='not_assessed', **evidence):
    return {'availability':availability,'note':reason,'execution_status':{'state':'not_assessed','reason':reason,**evidence}}


def empty_study(sid,aids):
    return {'schema':'merit.cache.v9.study','study_id':sid,'analyses':[
        {'analysis_id':aid,'endpoints':{src:{'availability':'no_file'} for src in SOURCES},
         'sections':{'analytical':{'score':None}}} for aid in aids]}


def scoped(full,aid,source,stage):
    value=copy.deepcopy(full)
    for a in value['analyses']:
        for src,ep in a['endpoints'].items():
            keep=(a['analysis_id'],src)==(aid,source)
            if stage in ('missingness','scale') and source=='datatable' and (a['analysis_id'],src)==(aid,'mwtab'):keep=True
            if not keep:ep.pop('roster',None)
    return value


def merge(full,after,aid=None,source=None, *, shared=False):
    """Preserve every sibling endpoint and shared per-analysis evidence written by a stage."""
    if shared:return after
    result=copy.deepcopy(full);byid={x['analysis_id']:x for x in result['analyses']}
    if 'roster_fields' in after:result['roster_fields']=after['roster_fields']
    for row in after['analyses']:
        old=byid[row['analysis_id']]
        for key,value in row.items():
            if key!='endpoints':old[key]=value
        if row['analysis_id']==aid and source in row['endpoints']:old['endpoints'][source]=row['endpoints'][source]
    return result


def report_event(sid,aid,source,stage,**result):
    return {'study_id':sid,'analysis_id':aid,'endpoint':source,'stage':stage,
            'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),**result}


def dependency_reason(full,aid,source,stage):
    if not aid or not source:return None
    ep=next(a for a in full['analyses'] if a['analysis_id']==aid)['endpoints'][source]
    if stage=='redundancy' and not ep.get('sections',{}).get('analytical',{}).get('low_information'):
        return 'Low-information features could not be assessed; redundancy needs that check to exclude constant and near-constant features'
    if stage=='qc_reproducibility' and not ep.get('sections',{}).get('analytical',{}).get('low_information'):
        return 'QC reproducibility could not be assessed because its required numerical-scale/CV-gate evidence was not computed'
    return None
