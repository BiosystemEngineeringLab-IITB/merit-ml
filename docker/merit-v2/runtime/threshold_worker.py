"""Fetch one public study and retain only the four default analytical scores."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

HOME=Path(__file__).resolve().parent
SOURCES=('datatable','mwtab','untarg_data')
STAGES=('controls','missingness','scale','low_information','redundancy','outliers')

def assess(run,sid):
    from native_resources import atomic,run_isolated
    from execution_state import empty_study,scoped,merge,unavailable,input_block,dependency_reason
    registry=json.loads((run/'live_registry.json').read_text());aids=registry[sid]
    path=run/'assessment/json'/f'{sid.lower()}_v9.json';report=[]
    def call(name,aid='',src='',blocked=None):
        atomic(run/'threshold_progress.json',{'stage':name,'analysis_id':aid,'endpoint':src})
        cmd=[sys.executable,str(HOME/'matrix_worker.py'),'--run-root',str(run),'--study',sid,'--stage',name]
        if aid:cmd+=['--analysis',aid,'--endpoint',src]
        result={'stage':name,'analysis_id':aid,'endpoint':src,**({'state':'not_assessed','reason':blocked} if blocked else run_isolated(cmd,run/'checks'/f'{aid or "shared"}-{src}-{name}.log'))}
        report.append(result);return result
    if call('materialize')['state']!='complete':raise RuntimeError('Could not prepare live inputs')
    full=empty_study(sid,aids)
    for a in full['analyses']:
        for src in SOURCES:
            block=input_block(run,sid,a['analysis_id'],src)
            r=call('bootstrap',a['analysis_id'],src,block['reason'] if block else None)
            if r['state']=='complete':
                v=json.loads(path.read_text());full['roster_fields']=v.get('roster_fields',[]);a['endpoints'][src]=v['analyses'][0]['endpoints'][src]
            else:a['endpoints'][src]={**unavailable(r['reason']),'execution_failure':r}
            atomic(path,full)
    for a in full['analyses']:
        aid=a['analysis_id']
        for src,ep in a['endpoints'].items():
            if 'roster' not in ep:continue
            for name in STAGES:
                atomic(path,scoped(full,aid,src,name))
                paired=next(x for x in full['analyses'] if x['analysis_id']==aid)['endpoints'].get('mwtab',{})
                reason=('Paired mwTab input could not be assessed; no zero provenance inferred' if src=='datatable' and name=='missingness' and paired.get('execution_failure') else None)
                reason=reason or dependency_reason(full,aid,src,name)
                result=call(name,aid,src,reason)
                if result['state']=='complete':full=merge(full,json.loads(path.read_text()),aid,src)
                else:
                    next(x for x in full['analyses'] if x['analysis_id']==aid)['endpoints'][src].setdefault('execution_failures',{})[name]=result
                atomic(path,full)
    return full,report

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-root',type=Path,required=True);p.add_argument('--study',required=True);p.add_argument('--release-date',required=True);args=p.parse_args()
    run=args.run_root.resolve();sid=args.study
    if not run.is_relative_to(HOME/'runs'):raise ValueError('Reference work must remain inside the local run directory')
    from native_resources import atomic
    from fetch_online import Fetcher,link_pinned_references,records
    from threshold_update import rows_from_record,utc
    atomic(run/'threshold_progress.json',{'stage':'fetching','study_id':sid})
    f=Fetcher(run/'inputs',allow_partial=True);link_pinned_references(f)
    aids=f.study(sid);atomic(run/'live_registry.json',{sid:aids});f.save()
    f.require_success([f'{sid}/summary.json',f'{sid}/analysis.json'])
    ds={str(x.get('release_date') or '') for x in records(json.loads((run/'inputs'/sid/'summary.json').read_text()))}
    if ds!={args.release_date} or args.release_date>utc().split('T')[0]:raise ValueError('Public release date changed or cannot be confirmed; study excluded')
    full,checks=assess(run,sid)
    errors=[{'stage':x['stage'],'analysis_id':x['analysis_id'],'endpoint':x['endpoint'],'reason':x['reason']} for x in checks if x['state']!='complete']
    errors.extend({'url':e['url'],'reason':e['error']} for e in f.entries if e.get('error'))
    # Compact derived results, input digests and failure reasons only; no rosters or measurements.
    result={'study_id':sid,'release_date':args.release_date,'analysis_ids':aids,'rows':rows_from_record(full),'errors':errors,
            'fetched_at':utc(),'fetch_provenance':[{'url':x['url'],'sha256':x.get('sha256'),'http_status':x.get('http_status'),'bytes':x.get('bytes'),'started_utc':x.get('started_utc'),'finished_utc':x.get('finished_utc'),'error':x.get('error')} for x in f.entries],
            'matrix_dimensions':[{'analysis_id':a['analysis_id'],'endpoint':src,'features':ep.get('n_features'),'positions':len(ep.get('roster',[]))} for a in full['analyses'] for src,ep in a['endpoints'].items() if 'roster' in ep]}
    atomic(run/'compact_result.json',result)
if __name__=='__main__':main()
