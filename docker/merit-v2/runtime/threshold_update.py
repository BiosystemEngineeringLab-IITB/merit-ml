"""Optional local reference builder. Never mutates the distributed R1 or scientific rules."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

HOME=Path(__file__).resolve().parent
ROOT=Path(os.environ.get('MERIT_REFERENCE_STORE',str(HOME/'runs/threshold_updates'))).resolve()
if not ROOT.is_relative_to(HOME/'runs'):raise ValueError('Reference storage must be inside the application runs directory')
KEYS=('missingness','low_information','redundancy','outliers')
PERCENTILES=(2.5,5,7.5,10)
ID=re.compile(r'ref-[0-9TZA-Fa-f-]+')
_LAUNCH_LOCK=threading.RLock()

def utc():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    p=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');p.write_text(json.dumps(value,indent=2,allow_nan=False));p.replace(path)
def read(path):return json.loads(Path(path).read_text())
def jobpath(ident,root=ROOT):
    if not ID.fullmatch(ident):raise ValueError('Invalid reference run ID')
    return Path(root)/ident
def baseline():return read(HOME/'assets/alert_reference_R1.json')
def validate_reference(r):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,90}',r.get('reference_version','')):raise ValueError('Invalid reference identifier')
    for p in PERCENTILES:
        c=r.get('sensitivity',{}).get(f'{p:g}',{}).get('cutoffs',{})
        if set(c)!=set(KEYS) or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=100 for v in c.values()):raise ValueError('Invalid reference cut-offs')
    for k in KEYS:
        if any(r['sensitivity'][f'{a:g}']['cutoffs'][k]>r['sensitivity'][f'{b:g}']['cutoffs'][k] for a,b in zip(PERCENTILES,PERCENTILES[1:])):raise ValueError('Non-monotone reference percentiles')
    return r
def active(root=ROOT):
    p=Path(root)/'active.json'
    if not p.exists():return baseline()
    v=read(p)
    if digest(v['reference'])!=v['sha256']:raise ValueError('Active reference checksum does not match; restore R1 in Update thresholds')
    return validate_reference(v['reference'])
def dates(start,end):
    a=dt.date.fromisoformat(start);b=dt.date.fromisoformat(end)
    if a>b or b>dt.datetime.now(dt.timezone.utc).date():raise ValueError('Choose an ordered date range ending no later than today (UTC)')
    return a.isoformat(),b.isoformat()
def baseline_rows():
    b=read(HOME/'assets/alert_reference_R1_scores.json')
    if b['baseline_reference_sha256']!=hashlib.sha256((HOME/'assets/alert_reference_R1.json').read_bytes()).hexdigest():raise ValueError('Baseline scores do not match the distributed R1')
    return b['matrices']
def rows_from_record(value):
    rows=[]
    for a in value['analyses']:
        for src,ep in a['endpoints'].items():
            if 'roster' not in ep:continue
            an=ep.get('sections',{}).get('analytical',{})
            scores=[an.get('sample_missingness',{}).get('score')]+[an.get(k,{}).get('default',{}).get('score') for k in KEYS[1:]]
            scores=[float(x) if isinstance(x,(int,float)) and math.isfinite(x) else None for x in scores]
            if any(x is not None and not 0<=x<=100 for x in scores):raise ValueError('Score outside 0–100')
            rows.append([value['study_id'],a['analysis_id'],src,*scores])
    return rows
def build_reference(rows,version,provenance):
    import numpy as np
    unique={tuple(r[:3]):r for r in rows}
    if len(unique)!=len(rows):raise ValueError('Duplicate study/analysis/endpoint in reference')
    arrays={k:np.array([r[i+3] for r in rows if r[i+3] is not None],dtype=float) for i,k in enumerate(KEYS)}
    if any(not len(a) for a in arrays.values()):raise ValueError('At least one assessable score is required for each of the four checks')
    sensitivity={}
    for p in PERCENTILES:
        c={k:float(np.percentile(a,p,method='linear')) for k,a in arrays.items()}
        sensitivity[f'{p:g}']={'cutoffs':c,'matrices':len(rows),'matrices_with_an_alert':sum(any(r[i+3] is not None and r[i+3]<c[k] for i,k in enumerate(KEYS)) for r in rows)}
    stats={k:{'n':len(a),'median':float(np.median(a)),'q1':float(np.percentile(a,25)),'q3':float(np.percentile(a,75))} for k,a in arrays.items()}
    r={'reference_version':version,'reference_cohort':provenance['description'],'reference_matrices':len(rows),
       'reference_studies':len({r[0] for r in rows}),'reference_analyses':len({tuple(r[:2]) for r in rows}),
       'matrices_by_endpoint':{s:sum(r[2]==s for r in rows) for s in ('datatable','mwtab','untarg_data')},
       'reference_cohort_sha256':digest(sorted(rows,key=lambda r:r[:3])),'comparison':'strictly below the cut-off',
       'created_at':utc(),'n_matrices_with_a_value':{k:len(a) for k,a in arrays.items()},'sensitivity':sensitivity,
       'statistics':stats,'update_provenance':provenance}
    return validate_reference(r)

def get_json(url):
    last=None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'MERIT-ML-reference-update/1.0'}),timeout=120) as r:raw=r.read()
            return json.loads(raw),{'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'retrieved_utc':utc(),'bytes':len(raw)}
        except Exception as e:last=e;time.sleep(attempt+1)
    raise RuntimeError(f'Repository request failed: {url}: {type(last).__name__}')

def select_cohort(public_rows,summary_rows,start,end,today=None):
    """Release date only, inclusive. Unknown/conflicting/future dates never enter the cohort."""
    today=today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    public={}
    for r in public_rows:
        sid=str(r.get('study_id','')).upper();aid=str(r.get('analysis_id','')).upper()
        if re.fullmatch(r'ST\d{6}',sid) and re.fullmatch(r'AN\d{6}',aid):public.setdefault(sid,set()).add(aid)
    grouped={}
    for r in summary_rows:
        sid=str(r.get('study_id','')).upper()
        if sid in public:grouped.setdefault(sid,[]).append(r)
    selected=[];excluded=[]
    for sid,aids in sorted(public.items()):
        rs=grouped.get(sid,[]);ds={str(r.get('release_date') or '').strip() for r in rs}
        try:
            if len(ds)!=1:raise ValueError()
            release=dt.date.fromisoformat(next(iter(ds))).isoformat()
        except (ValueError,TypeError):excluded.append({'study_id':sid,'reason':'release date missing or conflicting'});continue
        if release>today:excluded.append({'study_id':sid,'reason':'future release date; excluded'});continue
        if start<=release<=end:selected.append({'study_id':sid,'release_date':release,'analysis_ids':sorted(aids),'title':str(rs[0].get('study_title') or '')})
    return selected,excluded,len(public)

def prepare(start,end,mode='extend_r1',root=ROOT):
    start,end=dates(start,end)
    if mode not in ('extend_r1','selected_range'):raise ValueError('Unknown reference mode')
    ident='ref-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    p=jobpath(ident,root);p.mkdir(parents=True)
    atomic(p/'status.json',{'id':ident,'state':'discovering','start_date':start,'end_date':end,'mode':mode,'created_at':utc(),'message':'Reading the current public registry and release dates'})
    return ident

def discover(ident,root=ROOT):
    from fetch_online import API,records
    from live_inventory import LIST_URL
    p=jobpath(ident,root);s=read(p/'status.json')
    try:
        registry,audit=get_json(LIST_URL);public=records(registry)
        if not public:raise ValueError('Public analysis registry is empty; no cohort inferred')
        requests=[audit];summaries=[];errors=[]
        try:
            value,audit=get_json(API+'/study/study_id/ST/summary');summaries=records(value);requests.append(audit)
        except RuntimeError:pass
        available={str(x.get('study_id','')).upper() for x in summaries}
        missing={str(x.get('study_id','')).upper() for x in public}-available
        # Bound requests to accession groups missing from the global response.
        for prefix in sorted({x[:6] for x in missing if re.fullmatch(r'ST\d{6}',x)}):
            s.update(message='Reading release dates: '+prefix);atomic(p/'status.json',s)
            try:
                value,audit=get_json(API+f'/study/study_id/{prefix}/summary');summaries.extend(records(value));requests.append(audit)
            except RuntimeError as e:errors.append(str(e))
        chosen,excluded,n=select_cohort(public,summaries,s['start_date'],s['end_date'])
        plan={'studies':chosen,'excluded':excluded,'public_studies_seen':n,'requests':requests,'discovery_errors':errors,'date_field':'release_date','inclusive':True,'discovered_at':utc()}
        atomic(p/'cohort.json',plan)
        s.update(state='ready',selected_studies=len(chosen),registered_analyses=sum(len(x['analysis_ids']) for x in chosen),
                 endpoints_to_check=sum(3*len(x['analysis_ids']) for x in chosen),unknown_dates=sum(x['reason'].startswith('release date missing') for x in excluded),
                 message='Review the cohort and start calculation. Valid matrix counts become available after retrieval.')
    except Exception as e:s.update(state='failed',error=str(e),message='Discovery failed; no reference changed')
    atomic(p/'status.json',s)

def update_status(p,**kw):
    s=read(p/'status.json');s.update(kw,updated_at=utc());atomic(p/'status.json',s);return s

def owned_workers(scratch):
    """Identify only our disposable workers with this exact owned work path."""
    for proc in Path('/proc').glob('[0-9]*'):
        try:args=(proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
        except (OSError,PermissionError):continue
        if str(scratch.resolve()) in args and any(Path(a).name in ('threshold_worker.py','matrix_worker.py') for a in args):yield int(proc.name)
def work_in_use(scratch):return any(owned_workers(scratch))
def stop_owned_work(scratch):
    for pid in owned_workers(scratch):
        try:os.kill(pid,15)
        except ProcessLookupError:pass

def run(ident,root=ROOT):
    import fcntl
    from native_resources import run_isolated
    p=jobpath(ident,root);Path(root).mkdir(parents=True,exist_ok=True)
    with (Path(root)/'calculation.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            update_status(p,state='failed',pid=None,error='Another reference calculation is running',message='Wait for the other reference run, then resume this one');return
        s=read(p/'status.json')
        if s['state'] not in ('ready','paused','interrupted','starting','running','failed'):raise ValueError('This reference cannot be started')
        plan=read(p/'cohort.json');completed={x.stem:read(x) for x in (p/'records').glob('ST*.json')}
        code=runtime_digest()
        if s.get('runtime_sha256') and s['runtime_sha256']!=code:
            update_status(p,state='failed',pid=None,error='Application code changed since this run began. Start a new date-range run to avoid mixing calculation versions.',message='Previous compact results are retained, but cannot be mixed with changed calculation code.');return
        if not plan['studies']:raise ValueError('No public studies in this date range; no new reference calculated')
        if (p/'pause').exists():(p/'pause').unlink()
        update_status(p,state='running',pid=os.getpid(),runtime_sha256=code,calculation_started_at=s.get('calculation_started_at') or utc(),message='Fetching and assessing one study at a time')
        try:
            for study in plan['studies']:
                sid=study['study_id']
                if sid in completed:continue
                if runtime_digest()!=code:raise ValueError('Application code changed during calculation. Start a new run; completed records are retained.')
                if (p/'pause').exists():update_status(p,state='paused',pid=None,message='Paused after the last study; resume to continue');return
                scratch=p/'work'/sid
                if scratch.exists():
                    if scratch.is_symlink():raise ValueError('Refusing a symlink scratch directory')
                    if work_in_use(scratch):raise ValueError('An interrupted study worker is still finishing. Resume after it stops, or restart the local application/container.')
                    shutil.rmtree(scratch)
                scratch.mkdir(parents=True)
                update_status(p,current_study=sid,completed_studies=len(completed),total_studies=len(plan['studies']))
                result=None
                try:
                    cmd=[sys.executable,str(HOME/'threshold_worker.py'),'--run-root',str(scratch),'--study',sid,'--release-date',study['release_date']]
                    # Each numerical stage has its normal isolated native limit; allow a long fetch/study wrapper.
                    report=run_isolated(cmd,p/'current-study.log',timeout=float(os.environ.get('MERIT_REFERENCE_STUDY_TIMEOUT_SECONDS','21600')))
                    if report['state']!='complete':raise RuntimeError(report['reason'])
                    result=read(scratch/'compact_result.json')
                except Exception as e:result={'study_id':sid,'rows':[],'errors':[str(e)],'failed':True}
                finally:
                    if result is None or result.get('failed'):stop_owned_work(scratch)
                    if result is not None:result['runtime_sha256']=code;completed[sid]=result;atomic(p/'records'/(sid+'.json'),result)
                    # Owned scratch directory only. No matrix/intermediate is kept between studies.
                    if scratch.is_symlink():raise ValueError('Refusing a symlink scratch directory')
                    shutil.rmtree(scratch)
                update_status(p,completed_studies=len(completed),valid_matrices=sum(len(x['rows']) for x in completed.values()),
                              failed_studies=sum(bool(x.get('failed')) for x in completed.values()))
            if runtime_digest()!=code:raise ValueError('Application code changed during calculation. Start a new run; completed records are retained.')
            atomic(p/'scores.json',completed)
            rows=[];baseline_keys=set()
            if s['mode']=='extend_r1':rows=baseline_rows();baseline_keys={tuple(x[:3]) for x in rows}
            fresh=[r for result in completed.values() for r in result['rows']]
            rows.extend(r for r in fresh if tuple(r[:3]) not in baseline_keys)
            warnings=[x['study_id'] for x in completed.values() if x.get('errors') or x.get('failed')]
            prov={'mode':s['mode'],'start_date':s['start_date'],'end_date':s['end_date'],'date_field':'public release_date (inclusive)',
                  'retrieval_started_at':read(p/'status.json')['calculation_started_at'],'retrieval_finished_at':utc(),
                  'selected_studies':len(plan['studies']),'completed_studies':len(completed),'failed_or_partial_studies':warnings,
                  'discovery_errors':plan['discovery_errors'],'unknown_date_studies':s.get('unknown_dates',0),
                  'new_valid_matrices':len(fresh),'baseline_matrices':len(baseline_keys),'duplicate_baseline_matrices_not_added':sum(tuple(r[:3]) in baseline_keys for r in fresh),
                  'runtime_sha256':code,'settings':'Default analytical settings, deposited sample eligibility, no user edits',
                  'description':('Original R1 plus public studies released ' if baseline_keys else 'Public studies released ')+s['start_date']+' to '+s['end_date']+'; fetched and calculated '+utc().split('T')[0],
                  'selection_sha256':digest(plan),'requires_partial_acknowledgement':bool(warnings or plan['discovery_errors'] or s.get('unknown_dates'))}
            r=build_reference(rows,ident,prov)
            atomic(p/'candidate.json',{'reference':r,'sha256':digest(r),'rows':rows})
            update_status(p,state='review',pid=None,candidate_sha256=digest(r),message='Calculation complete. Review the reference, then approve activation.',current_study=None)
        except Exception as e:update_status(p,state='failed',pid=None,error=str(e),message='Reference build failed. The active reference is unchanged.')

def runtime_digest():
    h=hashlib.sha256()
    for n in ('source_snapshot_manifest.json','matrix_worker.py','online_assess.py','threshold_worker.py','threshold_update.py'):
        h.update((HOME/n).read_bytes())
    return h.hexdigest()

@contextmanager
def reference_lock(root):
    import fcntl
    Path(root).mkdir(parents=True,exist_ok=True)
    with (Path(root)/'activation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield
def approve(ident,sha,ack=False,root=ROOT):
    with reference_lock(root):return _approve(ident,sha,ack,root)
def _approve(ident,sha,ack=False,root=ROOT):
    p=jobpath(ident,root);c=read(p/'candidate.json');r=validate_reference(c['reference'])
    if read(p/'status.json')['state'] not in ('review','approved'):raise ValueError('Calculation is not ready for approval')
    if sha!=c['sha256'] or digest(r)!=sha:raise ValueError('The reviewed candidate has changed; reload before approving')
    if r['update_provenance']['requires_partial_acknowledgement'] and not ack:raise ValueError('Acknowledge incomplete coverage before approving this partial reference')
    if digest(build_reference(c['rows'],r['reference_version'],r['update_provenance'])['sensitivity'])!=digest(r['sensitivity']):raise ValueError('Reference scores do not reproduce the candidate cut-offs')
    old=active(root);event={'reference':r,'sha256':sha,'approved_at':utc(),'previous_reference':old['reference_version'],'partial_coverage_acknowledged':bool(ack)}
    atomic(Path(root)/'history'/(uuid.uuid4().hex+'.json'),event)
    atomic(Path(root)/'active.json',event);update_status(p,state='approved',approved_at=event['approved_at'],message='Approved reference is active. Existing custom score cut-offs still take precedence.')
    return r
def restore(root=ROOT):
    with reference_lock(root):return _restore(root)
def _restore(root=ROOT):
    try:old=active(root)['reference_version']
    except (ValueError,KeyError):old='unreadable reference'
    event={'reference':baseline(),'sha256':digest(baseline()),'approved_at':utc(),'previous_reference':old,'action':'restore_original_R1'}
    atomic(Path(root)/'history'/(uuid.uuid4().hex+'.json'),event);atomic(Path(root)/'active.json',event);return baseline()
def status(ident,root=ROOT):
    p=jobpath(ident,root);s=read(p/'status.json')
    if s['state'] in ('running','starting'):
        try:
            pid=s.get('pid') or -999999;os.kill(pid,0)
            args=(Path('/proc')/str(pid)/'cmdline').read_bytes().decode(errors='replace').split('\0')
            if ident not in args or not any(Path(a).name=='threshold_update.py' for a in args):raise ProcessLookupError()
        except (ProcessLookupError,FileNotFoundError):s=update_status(p,state='interrupted',message='Calculation process stopped; resume from completed studies')
        progress=p/'work'/str(s.get('current_study') or '')/'threshold_progress.json'
        if progress.exists():s['current_check']=read(progress)
    if (p/'candidate.json').exists():
        c=read(p/'candidate.json');s['candidate']={'reference':c['reference'],'sha256':c['sha256']}
    return s
def launch(ident,action,root=ROOT):
    with _LAUNCH_LOCK:return _launch(ident,action,root)
def _launch(ident,action,root=ROOT):
    p=jobpath(ident,root)
    if action=='run':
        if (p/'pause').exists():(p/'pause').unlink()
        if status(ident,root)['state'] in ('running','starting'):raise ValueError('Already running')
        update_status(p,state='starting',message='Starting the resumable calculation process')
    with (p/(action+'.log')).open('a') as log:
        proc=subprocess.Popen([sys.executable,str(Path(__file__)),action,'--id',ident,'--store',str(root)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        if action=='run':update_status(p,pid=proc.pid)

def install_routes(app):
    from flask import request,jsonify,Response,send_file
    from urllib.parse import urlparse
    def same_origin():
        if request.mimetype!='application/json':raise ValueError('JSON is required')
        origin=request.headers.get('Origin')
        if origin and urlparse(origin).netloc!=request.host:raise ValueError('Cross-origin changes are not permitted')
        if request.headers.get('Sec-Fetch-Site')=='cross-site':raise ValueError('Cross-site changes are not permitted')
        return request.get_json()
    @app.get('/thresholds')
    def threshold_page():
        from threshold_ui import page
        return Response(page(),mimetype='text/html',headers={'Cache-Control':'no-store'})
    @app.get('/api/thresholds')
    def threshold_overview():
        values=[]
        if ROOT.exists():
            for p in sorted(ROOT.glob('ref-*'),reverse=True)[:30]:
                try:values.append(status(p.name))
                except (OSError,ValueError):pass
        return jsonify(active=active(),baseline=baseline(),runs=values)
    @app.post('/api/thresholds/prepare')
    def threshold_prepare():
        v=same_origin();ident=prepare(v['start_date'],v['end_date'],v.get('mode','extend_r1'));launch(ident,'discover');return jsonify(id=ident)
    @app.get('/api/thresholds/<ident>')
    def threshold_status(ident):return jsonify(status(ident))
    @app.post('/api/thresholds/<ident>/<action>')
    def threshold_action(ident,action):
        v=same_origin();p=jobpath(ident)
        if action=='start':launch(ident,'run')
        elif action=='pause':atomic(p/'pause',{'requested_at':utc()})
        elif action=='approve':approve(ident,v['sha256'],v.get('accept_partial') is True)
        else:raise ValueError('Unknown reference action')
        return jsonify(status(ident))
    @app.post('/api/thresholds/restore')
    def threshold_restore():same_origin();return jsonify(reference=restore())
    @app.get('/api/thresholds/<ident>/download')
    def threshold_download(ident):
        p=jobpath(ident);name=request.args.get('file','candidate.json')
        if name not in ('candidate.json','scores.json','cohort.json','status.json'):raise ValueError('Unknown provenance file')
        return send_file(p/name,as_attachment=True,download_name=ident+'_'+name)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','discover','run','status','approve','restore']);p.add_argument('--id');p.add_argument('--start-date');p.add_argument('--end-date');p.add_argument('--mode',default='extend_r1');p.add_argument('--sha256');p.add_argument('--accept-partial',action='store_true');p.add_argument('--store',type=Path,default=ROOT);a=p.parse_args()
    if a.action=='prepare':
        ident=prepare(a.start_date,a.end_date,a.mode,a.store);discover(ident,a.store);print(json.dumps(status(ident,a.store)))
    elif a.action=='discover':discover(a.id,a.store)
    elif a.action=='run':run(a.id,a.store);print(json.dumps(status(a.id,a.store)))
    elif a.action=='status':print(json.dumps(status(a.id,a.store)))
    elif a.action=='approve':print(json.dumps(approve(a.id,a.sha256,a.accept_partial,a.store)))
    else:print(json.dumps(restore(a.store)))
if __name__=='__main__':main()
