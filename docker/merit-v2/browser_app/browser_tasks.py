"""Checkpointed browser execution; scientific stages are matrix_worker.execute.

Only execution and availability are browser-specific. Successful values are merged
back after each task; a failed task never turns into a zero or an absent endpoint.
"""
from __future__ import annotations
import copy
import gc
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

from execution_state import SOURCES,STAGES,empty_study,input_block,shared_metadata_block,scoped,merge,unavailable,dependency_reason
from native_resources import atomic


def path_for(run,sid):return Path(run)/'assessment/json'/f'{sid.lower()}_v9.json'


def initial(run):
    import browser_runtime as br
    br._set_aside(run)
    registry=json.loads((Path(run)/'live_registry.json').read_text())
    sid=next(iter(registry));p=path_for(run,sid)
    atomic(p,empty_study(sid,registry[sid]))
    atomic(Path(run)/'browser_checkpoint.json',{'study_id':sid,'completed':[],'failures':[]})
    return {'study_id':sid,'analyses':registry[sid]}


def tasks(run):
    state=json.loads((Path(run)/'browser_checkpoint.json').read_text());sid=state['study_id']
    registry=json.loads((Path(run)/'live_registry.json').read_text())
    bootstrap=[dict(stage='bootstrap',analysis=aid,endpoint=src) for aid in registry[sid] for src in SOURCES]
    pre=[dict(stage='materialize',analysis=None,endpoint=None)]+bootstrap+[dict(stage=s,analysis=None,endpoint=None) for s in ('cross_endpoint','metadata')]
    full=json.loads(path_for(run,sid).read_text())
    calc=[dict(stage=name,analysis=a['analysis_id'],endpoint=src) for a in full['analyses'] for src,ep in a['endpoints'].items() if 'roster' in ep for name in STAGES]
    tail=[dict(stage=s,analysis=None,endpoint=None) for s in ('extra_evidence_and_readiness','live_context_and_indexes')]
    done=set(state['completed'])
    return [x for x in pre+calc+tail if key(x) not in done]


def key(t):return ':'.join(str(t.get(k) or '') for k in ('analysis','endpoint','stage'))


def execute(run,t,forced_reason=None):
    import browser_runtime as br
    from matrix_worker import execute as scientific_stage
    state_path=Path(run)/'browser_checkpoint.json';state=json.loads(state_path.read_text());sid=state['study_id']
    p=path_for(run,sid);full=json.loads(p.read_text());aid=t.get('analysis');src=t.get('endpoint');name=t['stage']
    reason=forced_reason;code='worker_stopped' if reason else None
    plan=json.loads((Path(run)/'browser_resource_plan.json').read_text())
    if name=='bootstrap':
        limit=next((x for x in plan['not_assessed'] if (x['analysis_id'],x['endpoint'])==(aid,src)),None)
        bad=input_block(run,sid,aid,src)
        if limit:reason=br.skip_note(limit,plan['budget_bytes']);code='browser_memory_limit'
        elif bad:reason=bad['reason'];code=bad['code']
    if name=='metadata' and not reason:reason=shared_metadata_block(run,sid);code='metadata_input_unavailable' if reason else None
    if not reason:
        reason=dependency_reason(full,aid,src,name)
        if reason:code='dependency_unavailable'
    if name=='missingness' and src=='datatable' and not reason:
        companion=next(a for a in full['analyses'] if a['analysis_id']==aid)['endpoints'].get('mwtab',{})
        if companion.get('availability') in (br.NOT_ASSESSED,'not_assessed'):
            reason='Paired mwTab could not be assessed; datatable zero provenance was not guessed';code='dependency_unavailable'
    result={'study_id':sid,'analysis_id':aid,'endpoint':src,'stage':name,'state':'complete'}
    br.post('active',task=t)
    br.post('stage',record={**result,'state':'started'})
    try:
        if reason:raise RuntimeError(reason)
        if name in STAGES:atomic(p,scoped(full,aid,src,name))
        scientific_stage(SimpleNamespace(run_root=Path(run),study=sid,analysis=aid,endpoint=src,stage=name))
        if name=='materialize':after=full
        else:after=json.loads(p.read_text())
        if name=='bootstrap':
            full=merge(full,after,aid,src)
        elif name in STAGES:full=merge(full,after,aid,src)
        else:full=after
    except (Exception,MemoryError) as exc:
        result.update(state='not_assessed',reason=reason or str(exc)[-600:] or type(exc).__name__,code=code or ('browser_memory_limit' if isinstance(exc,MemoryError) else 'calculation_error'))
        result['traceback']=traceback.format_exc()[-2500:]
        state['failures'].append(result)
        if name=='bootstrap':
            a=next(a for a in full['analyses'] if a['analysis_id']==aid)
            a['endpoints'][src]=unavailable(result['reason'],availability=br.NOT_ASSESSED if code=='browser_memory_limit' else 'not_assessed',code=result['code'])
        elif aid and src:
            a=next(a for a in full['analyses'] if a['analysis_id']==aid)
            a['endpoints'][src].setdefault('execution_failures',{})[name]=result
    atomic(p,full)
    state['completed'].append(key(t));atomic(state_path,state)
    atomic(Path(run)/'matrix_execution.json',{'checks':state['failures'],'partial':bool(state['failures']),'scope':'per analysis x endpoint; shared metadata identified separately'})
    atomic(Path(run)/'isolation_audit.json',{'historical_result_or_deposit_reads_forbidden':True,'denied_attempts':[],
        'partial_results':bool(state['failures']),'failures':state['failures'],'execution_report':'matrix_execution.json'})
    br.post('stage',record={**result,'state':'done' if result['state']=='complete' else 'not_assessed'})
    gc.collect()
    return result


def finish(run):
    state=json.loads((Path(run)/'browser_checkpoint.json').read_text())
    return {'failures':state['failures'],'not_assessed':[[x.get('analysis_id'),x.get('endpoint')] for x in state['failures'] if x['stage']=='bootstrap'],
            'checkpointed_tasks':len(state['completed'])}
