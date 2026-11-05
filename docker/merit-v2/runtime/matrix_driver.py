"""Sequential matrix/check supervision with durable successful-result checkpoints."""
from __future__ import annotations
import argparse
import copy
import datetime
import json
import os
import sys
from pathlib import Path
from native_resources import atomic,run_isolated
from execution_state import input_block,shared_metadata_block,empty_study,scoped,merge,unavailable,dependency_reason

HOME=Path(__file__).resolve().parent
SOURCES=('datatable','mwtab','untarg_data')
STAGES=('controls','missingness','scale','low_information','redundancy','outliers','qc_reproducibility',
        'annotation','refmet_lookup','labels','separability')


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-root',required=True,type=Path)
    args=p.parse_args();run=args.run_root.resolve()
    if not run.is_relative_to(HOME/'runs'):raise ValueError('Assessment must be inside runtime/runs')
    registry=json.loads((run/'live_registry.json').read_text());out=run/'assessment';out.mkdir(exist_ok=True)
    from versioned_context import ensure
    for sid in registry:
        context=ensure(HOME/'assets/reference',sid)
        atomic(run/f'{sid}_context_references.json',context)
    report={'version':'v2-live-20261105-r4','checks':[],'scope':'per analysis x endpoint; shared metadata identified separately'}
    # Fetch errors are not silently interpreted as missing deposits.
    manifest=json.loads((run/'inputs/fetch_manifest.json').read_text())

    def call(sid,name,aid='',src='',blocked=None):
        cmd=[sys.executable,str(HOME/'matrix_worker.py'),'--run-root',str(run),'--study',sid,'--stage',name]
        if aid:cmd+=['--analysis',aid,'--endpoint',src]
        base={'study_id':sid,'analysis_id':aid or None,'endpoint':src or None,'stage':name}
        def event(state):
            line={**base,'state':state,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
            with (run/'assessment_progress.jsonl').open('a') as f:f.write(json.dumps(line)+'\n')
            print(json.dumps(line),flush=True)
        event('started')
        log=run/'check_logs'/f'{sid}__{aid or "shared"}__{src or "shared"}__{name}.log'
        result={**base,**({'state':'not_assessed','reason':blocked,'code':'dependency_failed',
                          'returncode':None,'elapsed_seconds':0,'peak_rss_bytes':0,'memory_budget_bytes':None,'log':None}
                         if blocked else run_isolated(cmd,log))}
        report['checks'].append(result);report['partial']=any(x['state']!='complete' for x in report['checks'])
        atomic(run/'matrix_execution.json',report);event('done' if result['state']=='complete' else 'not_assessed')
        return result

    if call(next(iter(registry)),'materialize')['state']!='complete':raise RuntimeError('Could not prepare downloaded inputs; see matrix_execution.json')
    for sid,aids in registry.items():
        path=out/'json'/f'{sid.lower()}_v9.json'
        full=empty_study(sid,aids)
        def checkpoint():
            atomic(path,full);atomic(out/'checkpoints'/path.name,full)
        for analysis in full['analyses']:
            aid=analysis['analysis_id']
            for src in SOURCES:
                block=input_block(run,sid,aid,src)
                result=call(sid,'bootstrap',aid,src,blocked=block['reason'] if block else None)
                if result['state']=='complete':
                    value=json.loads(path.read_text());full['roster_fields']=value.get('roster_fields',[])
                    analysis['endpoints'][src]=value['analyses'][0]['endpoints'][src]
                else:
                    analysis['endpoints'][src]={**unavailable(result['reason']),'execution_failure':result}
                checkpoint()
        for shared in ('cross_endpoint','metadata'):
            result=call(sid,shared,blocked=shared_metadata_block(run,sid) if shared=='metadata' else None)
            if result['state']=='complete':full=json.loads(path.read_text())
            checkpoint()
        targets=[(a['analysis_id'],src) for a in full['analyses'] for src,ep in a['endpoints'].items() if 'roster' in ep]
        for aid,src in targets:
            for name in STAGES:
                atomic(path,scoped(full,aid,src,name))
                paired=next(a for a in full['analyses'] if a['analysis_id']==aid)['endpoints'].get('mwtab',{})
                blocked=('Paired mwTab matrix could not be assessed; datatable zero provenance cannot be assumed unpaired'
                         if src=='datatable' and name=='missingness' and paired.get('execution_failure') else None)
                blocked=blocked or dependency_reason(full,aid,src,name)
                result=call(sid,name,aid,src,blocked=blocked)
                target=next(a for a in full['analyses'] if a['analysis_id']==aid)['endpoints'][src]
                if result['state']=='complete':
                    value=json.loads(path.read_text())
                    full=merge(full,value,aid,src)
                else:target.setdefault('execution_failures',{})[name]=result
                checkpoint()
        # Shared informational evidence must not erase successful individual metrics.
        for name in ('extra_evidence_and_readiness','live_context_and_indexes'):
            result=call(sid,name)
            if result['state']=='complete':full=json.loads(path.read_text())
            checkpoint()
    denied=[]
    for p in (run/'worker_audits').glob('*.json'):denied+=json.loads(p.read_text()).get('denied_attempts',[])
    atomic(run/'isolation_audit.json',{'historical_result_or_deposit_reads_forbidden':True,'denied_attempts':denied,
        'failures':[],'partial_results':report['partial'],'execution_report':'matrix_execution.json',
        'note':'Individual failed checks are reported explicitly and do not invalidate successful matrices.'})
    print(json.dumps({'state':'complete','partial':report['partial'],'checks':len(report['checks'])}),flush=True)


if __name__=='__main__':main()
