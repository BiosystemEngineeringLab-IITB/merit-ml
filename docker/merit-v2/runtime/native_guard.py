"""Isolate interactive recalculations by matrix without changing MERIT formulas."""
from __future__ import annotations
import copy
import functools
import hashlib
import html
import inspect
import json
import os
import sys
from pathlib import Path
from native_resources import atomic,run_isolated,json_hook,memory_available

FUNCTIONS=('_missingness_study','_scale_study','_low_information_study','_redundancy_study',
           '_outlier_study','_qc_blank_study','_ml_study','_annotation_study','annotation_lists','feature_list_payload')
HOME=Path(__file__).resolve().parent


def install(ui,pilot,run,live,out):
    from merit import low_information_v9 as li,separability_v9 as sp
    li.STORE_ALL_CELLS=sys.maxsize;sp.FULL_LIMIT=sys.maxsize
    if os.environ.get('MERIT_V2_METRIC_WORKER')=='1':return
    failures={}
    originals={name:getattr(pilot,name) for name in FUNCTIONS if hasattr(pilot,name)}
    seed=hashlib.sha256(b''.join((HOME/x).read_bytes() for x in ('native_guard.py','metric_worker.py','source_snapshot_manifest.json'))).hexdigest()

    def guarded(name):
        original=originals[name];signature=inspect.signature(original)
        @functools.wraps(original)
        def invoke(*args,**kwargs):
            bound=signature.bind(*args,**kwargs);bound.apply_defaults();arguments=dict(bound.arguments)
            sid=arguments['study_id'];raw=pilot._raw_study(sid)
            if raw is None:return None
            rows=[];whole=None;retained_bytes=0
            for a in raw['analyses']:
                eps={};row_extra={}
                if name=='_qc_blank_study':
                    analytical=pilot._analytical(a)
                    row_extra={'sample_table':analytical.get('sample_table_controls') or {},
                               'additional':analytical.get('additional_sample_data') or {},
                               'processing':analytical.get('processing_evidence') or {},'technical':set()}
                for src,_ in pilot.SOURCES:
                    ep=a['endpoints'].get(src,{})
                    if 'roster' not in ep:
                        eps[src]={'availability':ep.get('availability','')};continue
                    key=(a['analysis_id'],src,name)
                    token=json.dumps([seed,name,arguments,a['analysis_id'],src,(out/'json'/f'{str(sid).lower()}_v9.json').stat().st_mtime_ns],sort_keys=True,default=str)
                    digest=hashlib.sha256(token.encode()).hexdigest()
                    folder=out/'interactive'/digest
                    result_path=folder/'result.json'
                    if not result_path.exists():
                        folder.mkdir(parents=True,exist_ok=True)
                        request={'run':str(run),'function':name,'arguments':arguments,'analysis':a['analysis_id'],'endpoint':src,'output':str(result_path)}
                        request_path=folder/'request.json';atomic(request_path,request)
                        result=run_isolated([sys.executable,str(HOME/'metric_worker.py'),str(request_path)],folder/'worker.log')
                        if result['state']!='complete':
                            failures[key]={**result,'analysis_id':a['analysis_id'],'endpoint':src,'stage':name}
                            eps[src]={'availability':'not assessed: '+result['reason'],'not_assessed':result['reason']}
                            continue
                    available=memory_available();size=result_path.stat().st_size
                    # Include Python-object and final JSON/base64 expansion before loading a very large export.
                    # This is a memory estimate, not a matrix-size or scientific exclusion rule.
                    if available is not None and size*12+retained_bytes*4>available*.65:
                        reason='Estimated report/export memory exceeds available memory; the calculated matrix is retained'
                        failures[key]={'state':'not_assessed','code':'report_memory_estimate','reason':reason,
                                       'analysis_id':a['analysis_id'],'endpoint':src,'stage':name}
                        eps[src]={'availability':'not assessed: '+reason,'not_assessed':reason};continue
                    failures.pop(key,None);retained_bytes+=size
                    value=json.loads(result_path.read_text(),object_hook=json_hook);whole=value
                    entries=value['analyses'] if isinstance(value,dict) else value
                    row=next(x for x in entries if x['analysis_id']==a['analysis_id'])
                    row_extra.update({k:v for k,v in row.items() if k not in ('analysis_id','endpoints')})
                    eps[src]=row['endpoints'][src]
                rows.append({**row_extra,'analysis_id':a['analysis_id'],'endpoints':eps})
            if name=='_ml_study':
                labels=pilot._labels_study(sid,arguments.get('params'),arguments.get('overrides'))
                return {'study_id':sid,'analyses':rows,'labels':labels,'choice':(labels or {}).get('choice')}
            if name=='feature_list_payload':
                header={k:v for k,v in (whole or {}).items() if k!='analyses'}
                header.update(study_id=sid,metric=arguments.get('metric'),analyses=rows)
                header['execution_failures']=list(failures.values())
                return header
            return rows
        return invoke
    for name in originals:setattr(pilot,name,guarded(name))
    original_overview=pilot._overview_data
    def overview(*args,**kwargs):
        data=original_overview(*args,**kwargs)
        if data:
            mapping={'missingness':'missingness','scale':'units','separability':'class_separability',
                     '_missingness_study':'missingness','_scale_study':'units','_low_information_study':'low_information',
                     '_redundancy_study':'redundancy','_outlier_study':'outliers','_ml_study':'class_separability'}
            records=list(failures.values())
            status=run/'matrix_execution.json'
            if status.exists():records+=json.loads(status.read_text()).get('checks',[])
            for a in data['analyses']:
                for src,ep in a['endpoints'].items():
                    for failed in records:
                        if failed.get('state')=='complete' or (failed.get('analysis_id'),failed.get('endpoint'))!=(a['analysis_id'],src):continue
                        metric=mapping.get(failed['stage'],failed['stage']);item=ep.get('items',{}).get(metric)
                        if item and item.get('value') is None:item['reason']=failed.get('reason') or 'not assessed'
            data['execution_failures']=[x for x in records if x.get('state')!='complete']
        return data
    pilot._overview_data=overview
    page=ui._page
    def guarded_page(*args,**kwargs):
        result=page(*args,**kwargs)
        records=list(failures.values());status=run/'matrix_execution.json'
        if status.exists():records+=json.loads(status.read_text()).get('checks',[])
        records=[x for x in records if x.get('state')!='complete']
        if records:
            rows=''.join('<tr>'+''.join('<td style="padding:6px">'+html.escape(str(x.get(k) or 'shared'))+'</td>' for k in ('analysis_id','endpoint','stage','reason'))+'</tr>' for x in records)
            note=("<section id='merit-v2-execution-notes' style='margin:16px;padding:14px;border:1px solid #c48b25;background:#fff8e7'>"
                  "<b>Some checks could not be assessed</b><p>Completed results are retained. A blank result is not a score of zero. "
                  "Other matrices and checks were still calculated. Retry with more available memory, a longer check time limit, "
                  "or after resolving the logged error.</p><table><thead><tr><th>Analysis</th><th>Data file</th><th>Check</th><th>Reason</th></tr></thead><tbody>"+rows+"</tbody></table></section>")
            import re;result=re.sub(r'(<body[^>]*>)',lambda m:m[0]+note,result,count=1)
        return result
    ui._page=guarded_page
