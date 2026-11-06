"""On-demand MW assessment server; no dependency on historical MERIT caches.

The public UI is the pinned current MERIT renderer. Fetch/build jobs run in
separate audited processes. The server retains temporary job data for 24 hours
by default; an explicit --retain flag preserves audit pilots. It never deletes
any directory outside its own newly created runs/web/job-* directories.
"""
from __future__ import annotations
import argparse
import base64
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import io
import zipfile
import tempfile
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_file

HOME=Path(__file__).resolve().parent
WEB=HOME/'runs/web'
ENV={**os.environ,'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}
SID=re.compile(r'ST\d{6}')


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(f'.{threading.get_ident()}.tmp');tmp.write_text(json.dumps(value,indent=2));tmp.replace(path)


def code_version():
    """Digest of everything that defines a calculation besides the study inputs: code snapshot, service code, references, R1."""
    h=hashlib.sha256()
    calculation=('live_disease.py','fetch_online.py','online_assess.py','fast_correlation.py','online_evidence.py','mwtab_input.py','live_references.py','live_validity.py',
                 'live_indexes.py','live_design_context.py','live_study_summary.py','execution_state.py','versioned_context.py')
    for p in [HOME/'source_snapshot_manifest.json',HOME/'assets/reference/reference_policy.json',HOME/'assets/reference/study_reference_manifest.json',HOME/'assets/alert_reference_R1.json',
              *(HOME/n for n in calculation)]:
        if p.is_file():h.update(p.name.encode()+b'\0'+hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def input_digest(run,sid,code):
    """Digest of every byte fetched for the study (Workbench responses are deterministic for an unchanged deposit)."""
    h=hashlib.sha256(code.encode());root=Path(run)/'inputs'
    for p in sorted((root/sid).rglob('*')):
        if p.is_file():h.update(str(p.relative_to(root)).encode()+b'\0'+hashlib.sha256(p.read_bytes()).digest())
    h.update((Path(run)/'live_registry.json').read_bytes())
    return h.hexdigest()


class WarmRenderer:
    """One long-lived renderer process per assessment, so repeated page and settings requests skip start-up."""
    def __init__(self,runtime,env,run):
        log=open(Path(run)/'render_worker.log','a')
        self.proc=subprocess.Popen([sys.executable,str(Path(runtime)/'live_render.py'),'--serve'],stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,stderr=log,text=True,env=env,bufsize=1)
        self.lock=threading.Lock();self.last=time.time()

    def ask(self,payload):
        with self.lock:
            self.last=time.time()
            if self.proc.poll() is not None:raise RuntimeError('renderer stopped')
            self.proc.stdin.write(json.dumps(payload)+'\n');self.proc.stdin.flush()
            line=self.proc.stdout.readline()
            self.last=time.time()
            if not line:raise RuntimeError('renderer stopped')
            return json.loads(line)

    def stop(self):
        try:self.proc.terminate()
        except Exception:pass


class Jobs:
    def __init__(self,ttl=86400,retain=False,resume=False,prepared_cohorts=None,workers=4,fresh_seconds=3600,max_renderers=8):
        self.ttl=ttl;self.retain=retain;self.fresh_seconds=fresh_seconds;self.max_renderers=max_renderers
        self.lock=threading.RLock();self.jobs={};self.current={};self.render_locks={};self.busy=set()
        self.render_slots=threading.BoundedSemaphore(max(2,workers+2))
        self.renderers={}
        self.code=code_version()
        self.result_index_path=WEB/'result_index.json'
        try:self.results={k:v for k,v in json.loads(self.result_index_path.read_text()).items() if v.get('code')==self.code}
        except (OSError,ValueError):self.results={}
        self.bulk_sessions={}
        self.bulk_report_views_path=WEB/'bulk_report_views.json'
        try:self.bulk_report_views=json.loads(self.bulk_report_views_path.read_text())
        except (OSError,ValueError):self.bulk_report_views={}
        self.prepared_studies={}
        for run_id in prepared_cohorts or []:
            from online_pull_common import context
            _,_,_,config=context(run_id)
            for sid in config['study_ids']:self.prepared_studies[sid]=run_id
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=workers,thread_name_prefix='mw-assessment')
        WEB.mkdir(parents=True,exist_ok=True)
        for f in WEB.glob('job-*/job_status.json') if resume else ():
            try:
                job=json.loads(f.read_text())
                if job['state'] in ('fetching','computing','queued'):
                    job.update(state='failed',error='Previous process ended before completion; submit again to refetch')
                    atomic(f,job)
                self.jobs[job['id']]=job
                if job['state']=='complete' and Path(job['run']).resolve()==f.parent.resolve():
                    for sid in job['studies']:self.current[sid]=job['id']
            except (OSError,KeyError,ValueError):continue

    def adopt(self,run):
        run=Path(run).resolve()
        if not run.is_relative_to(HOME/'runs'):raise ValueError('Run must be in online-tool/runs')
        if (run/'REPLAY_NOT_LIVE.json').exists():raise ValueError('Frozen-input replay cannot be presented as a live fetch')
        audit=json.loads((run/'isolation_audit.json').read_text())
        if audit['failures'] or audit['denied_attempts']:raise ValueError('Cannot adopt failed/contaminated run')
        registry=json.loads((run/'live_registry.json').read_text())
        if any(not (run/'assessment/json'/f'{sid.lower()}_v9.json').is_file() for sid in registry):raise ValueError('Incomplete outputs')
        job={'id':'adopt-'+run.name,'run':str(run),'studies':list(registry),'state':'complete',
             'created':time.time(),'updated':time.time(),'retained_validation_run':True}
        with self.lock:
            self.jobs[job['id']]=job
            for sid in registry:self.current[sid]=job['id']

    def resume_online_pull(self,run_id):
        """Explicitly recover this experiment's jobs; never general cache reuse."""
        from online_pull_common import context
        root,_,_,config=context(run_id)
        for path in sorted((root/'online_pull_jobs').glob('job-*/job_status.json')):
            job=json.loads(path.read_text())
            if job.get('online_pull_run_id')!=run_id or Path(job['run']).resolve()!=path.parent.resolve():raise ValueError('Online-pull job identity mismatch')
            if len(job.get('studies',[]))!=1 or job['studies'][0] not in config['study_ids']:raise ValueError('Online-pull study identity mismatch')
            if job['state']=='complete':
                audit=json.loads((path.parent/'isolation_audit.json').read_text())
                if audit['failures'] or audit['denied_attempts']:raise ValueError('Cannot recover a failed/contaminated calculation')
            elif job['state']!='failed':
                job.update(state='failed',error='Server restarted during an unfinished job; the cohort controller will submit a fresh attempt')
                atomic(path,job)
            with self.lock:
                self.jobs[job['id']]=job
                for sid in job['studies']:self.current[sid]=job['id']

    def expire(self,result_days=7):
        if self.retain:return
        now=time.time()
        with self.lock:
            # Reusable calculations are kept for a week, then removed unless a live job still shows them.
            for digest,v in list(self.results.items()):
                if now-v.get('at',0)>result_days*86400:
                    p=Path(v['run']).resolve();self.results.pop(digest)
                    if p.parent==WEB.resolve() and p.name.startswith('job-') and not any(Path(o['run']).resolve()==p for o in self.jobs.values()):
                        shutil.rmtree(p,ignore_errors=True)
            atomic(self.result_index_path,self.results)
        with self.lock:
            stale=[j for j in self.jobs.values() if j['state'] in ('complete','failed') and j['id'] not in self.busy and
                   not j.get('retained_validation_run') and now-j['updated']>self.ttl]
            for j in stale:
                p=Path(j['run']).resolve()
                shared=any(Path(o['run']).resolve()==p for o in self.jobs.values() if o['id']!=j['id'] and o not in stale)
                indexed=any(Path(v['run']).resolve()==p for v in self.results.values())
                if p.parent==WEB.resolve() and p.name.startswith('job-') and not shared and not indexed:
                    shutil.rmtree(p,ignore_errors=True)
                fetch=Path(j.get('fetch_run') or j['run']).resolve()
                if fetch!=p and fetch.parent==WEB.resolve() and fetch.name.startswith('job-'):
                    shutil.rmtree(fetch,ignore_errors=True)
                if not shared:
                    r=self.renderers.pop(str(p),None)
                    if r:r.stop()
                self.jobs.pop(j['id'],None)
                for sid in j['studies']:
                    if self.current.get(sid)==j['id']:self.current.pop(sid,None)

    def get(self,sid):
        self.expire()
        with self.lock:
            key=self.current.get(sid)
            return dict(self.jobs[key]) if key in self.jobs else None

    def explicit(self,sid,job_id):
        with self.lock:
            job=self.jobs.get(job_id)
            if job and sid in job['studies']:return dict(job)
        return None

    def start(self,sid,refresh=False,job_id=None,online_pull_run_id=None):
        if not SID.fullmatch(sid):raise ValueError('Expected ST followed by six digits')
        with self.lock:
            prepared_run=self.prepared_studies.get(sid)
            if not online_pull_run_id and prepared_run:online_pull_run_id=prepared_run
            if job_id:
                same=self.explicit(sid,job_id)
                if same:return same
            old=self.get(sid)
            # Explicitly requested prepared-cohort mode: serve these freshly
            # calculated local reports instantly, using their pinned runtime.
            # Other accessions retain the ordinary fresh-assessment policy.
            if old and prepared_run and old.get('online_pull_run_id')==prepared_run and old['state']=='complete' and not refresh:
                return old
            # An in-progress assessment is shared. A completed one is reused only while its Workbench download is
            # recent (fresh_seconds); the report states the retrieval time and offers to retrieve the study again.
            if old and old['state'] in ('fetching','computing','queued') and not refresh and old.get('online_pull_run_id')==online_pull_run_id:return old
            if (old and old['state']=='complete' and not refresh and not online_pull_run_id and not old.get('online_pull_run_id')
                    and time.time()-old.get('fetched_at',0)<self.fresh_seconds):return old
            ident='job-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
            if online_pull_run_id:
                from online_pull_common import context
                cohort,refs,runtime,config=context(online_pull_run_id,sid)
                run=cohort/'online_pull_jobs'/ident
            else:run=WEB/ident
            run.mkdir(parents=True)
            job={'id':ident,'run':str(run),'studies':[sid],'state':'queued','created':time.time(),'updated':time.time()}
            if online_pull_run_id:job.update(online_pull_run_id=online_pull_run_id,retained_validation_run=True)
            self.jobs[ident]=job;self.current[sid]=ident;atomic(run/'job_status.json',job)
            self.pool.submit(self.build,ident)
            return dict(job)

    def update(self,key,**changes):
        with self.lock:
            home=Path(self.jobs[key].get('fetch_run') or self.jobs[key]['run'])
            self.jobs[key].update(**changes,updated=time.time())
            home=Path(self.jobs[key].get('fetch_run') or home)
            atomic(home/'job_status.json',self.jobs[key])

    def build(self,key):
        job=self.jobs[key];run=Path(job['run']);sid=job['studies'][0];digest=None
        try:
            runtime=HOME;env=ENV.copy();fetch_extra=[]
            if job.get('online_pull_run_id'):
                from online_pull_common import context
                _,refs,runtime,_=context(job['online_pull_run_id'],sid)
                env['MERIT_ONLINE_EXECUTION_ROOT']=str(HOME)
                env.pop('MERIT_EXISTING_PROJECT_ROOT',None)
                fetch_extra=['--shared-reference-inputs',str(refs)]
            for name,cmd in (
                ('fetching',[sys.executable,str(runtime/'fetch_online.py'),'--run-root',str(run),'--studies',sid,'--with-panredu','--allow-partial',*fetch_extra]),
                ('computing',[sys.executable,str(runtime/'online_assess.py'),'--run-root',str(run)])):
                if name=='computing':
                    self.update(key,fetched_at=time.time())
                    digest=input_digest(run,sid,self.code) if not job.get('online_pull_run_id') else None
                    hit=self.results.get(digest) if digest else None
                    if hit and (Path(hit['run'])/'assessment/json'/f'{sid.lower()}_v9.json').is_file() and (Path(hit['run'])/'isolation_audit.json').is_file():
                        # Byte-identical inputs and the same code and references: the calculation is the same.
                        self.update(key,state='complete',run=hit['run'],fetch_run=str(run),reused_calculation_from=hit['job'],input_digest=digest)
                        self.prewarm(key)
                        return
                self.update(key,state=name)
                with (run/f'{name}.log').open('w') as log:
                    p=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
                if p.returncode:raise RuntimeError(failure_message(name,run,sid))
            self.update(key,state='complete')
            self.prewarm(key)
            if digest:
                with self.lock:
                    self.results[digest]={'run':str(run),'job':key,'code':self.code,'study_id':sid,'at':time.time()}
                    atomic(self.result_index_path,self.results)
        except Exception as exc:self.update(key,state='failed',error=str(exc))

    def status(self,job):
        p=Path(job['run']);result={k:v for k,v in job.items() if k not in ('run','fetch_run')}
        f=Path(job.get('fetch_run') or p)/'inputs/fetch_manifest.json'
        if f.exists():
            try:
                reqs=json.loads(f.read_text())['requests']
                result['fetch']={'requests_completed':len(reqs),'bytes':sum(r.get('bytes') or 0 for r in reqs if not r.get('shared_live_cohort_reference')),
                                 'network_requests_completed':sum(not (r.get('shared_live_cohort_reference') or r.get('shared_cohort_reference')) for r in reqs),
                                 'shared_reference_entries':sum(bool(r.get('shared_live_cohort_reference') or r.get('shared_cohort_reference')) for r in reqs),
                                 'errors':[{'url':r['url'],'error':r['error']} for r in reqs if r.get('error')]}
            except ValueError:pass
        f=p/'assessment_progress.jsonl'
        if f.exists():
            lines=f.read_text().splitlines()
            result['stages']=[json.loads(x) for x in lines[-80:] if x.strip()]
        if job['state']=='failed':
            result['log_tail']='\n'.join((p/f'{n}.log').read_text()[-6000:] for n in ('fetching','computing') if (p/f'{n}.log').exists())
        from live_progress import progress
        result['progress']=progress(result)
        return result

    def renderer(self,job,runtime,env):
        key=str(Path(job['run']).resolve())      # one warm renderer per calculation, shared by jobs that reuse it
        with self.lock:
            r=self.renderers.get(key)
            if r and r.proc.poll() is None:return r
            idle=sorted((x.last,k) for k,x in self.renderers.items() if not x.lock.locked())
            while len(self.renderers)>=self.max_renderers and idle:
                _,k=idle.pop(0);self.renderers.pop(k).stop()
            r=self.renderers[key]=WarmRenderer(runtime,env,job['run'])
            return r

    def prewarm(self,key):
        """Start the report renderer as soon as the calculation is ready, so the first page is fast."""
        def go():
            try:
                job=dict(self.jobs[key])
                if job.get('online_pull_run_id'):return
                self.renderer(job,HOME,ENV.copy()).ask({'run':job['run'],'study_id':job['studies'][0],'job_id':key,'action':'warm','form':{}})
            except Exception:pass
        threading.Thread(target=go,name='renderer-prewarm',daemon=True).start()

    def stop_idle_renderers(self,idle_seconds=1200):
        with self.lock:
            for k,r in list(self.renderers.items()):
                if not r.lock.locked() and time.time()-r.last>idle_seconds:
                    self.renderers.pop(k).stop()

    def render(self,job,sid,action='page',form=None):
        if job['state']!='complete':raise ValueError('Assessment has not completed')
        with self.lock: lock=self.render_locks.setdefault(job['id'],threading.Lock())
        retrieved=dt.datetime.fromtimestamp(job['fetched_at'],dt.timezone.utc).isoformat(timespec='seconds') if job.get('fetched_at') else None
        payload={'run':job['run'],'study_id':sid,'job_id':job['id'],'action':action,'form':form or {},'retrieved_utc':retrieved}
        with lock,self.render_slots:
            with self.lock:self.busy.add(job['id'])
            try:
                runtime=HOME;env=ENV.copy()
                if job.get('online_pull_run_id'):
                    from online_pull_common import context
                    _,_,runtime,_=context(job['online_pull_run_id'],sid)
                    env['MERIT_ONLINE_EXECUTION_ROOT']=str(HOME);env.pop('MERIT_EXISTING_PROJECT_ROOT',None)
                value=None
                try:
                    # Warm renderers run the current code only; retained cohort runs keep their own pinned renderer.
                    if Path(runtime).resolve()==HOME.resolve():
                        value=self.renderer(job,runtime,env).ask(payload)
                except Exception:
                    with self.lock:
                        r=self.renderers.pop(str(Path(job['run']).resolve()),None)
                    if r:r.stop()
                if value is None:
                    p=subprocess.run([sys.executable,str(runtime/'live_render.py')],input=json.dumps(payload),
                                     text=True,capture_output=True,env=env)
                    try:value=json.loads(p.stdout)
                    except ValueError:raise RuntimeError('Renderer failed: '+p.stderr[-3000:])
            finally:
                with self.lock:self.busy.discard(job['id'])
        if not value.get('ok'):
            print(value.get('error',''),file=sys.stderr,flush=True)
            raise RuntimeError('The report could not be prepared for this request.')
        if value['isolation']['denied_attempts']:raise RuntimeError('Renderer attempted a forbidden historical read: '+str(value['isolation']['denied_attempts']))
        return value


def failure_message(stage,run,sid):
    """Plain message for the person waiting; technical details stay in the job log (/api/jobs/<id>)."""
    log=''
    try:log=(Path(run)/f'{stage}.log').read_text()[-20000:]
    except OSError:pass
    if 'No analysis IDs in live registry' in log:
        return (f'{sid} has no public analyses on Metabolomics Workbench today. It may be under embargo, not yet released or '
                'withdrawn. MERIT-ML does not show or assess such studies.')
    if stage=='fetching':
        return ('A required study input or reference could not be retrieved. Completed responses are retained, and the run log identifies the source. '
                'No absent-file result was inferred from a failed transfer. Please try again in a few minutes.')
    return ('The assessment of this study could not be completed. The problem has been recorded; please try again later or '
            'contact the MERIT-ML maintainers with the study ID.')


def create_app(jobs,catalog=None,inventory=None):
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=16*1024*1024
    def valid_sid(raw):
        sid=str(raw or '').strip().upper()
        if not SID.fullmatch(sid):raise ValueError('A valid ST accession is required')
        return sid
    class Unavailable(Exception):
        def __init__(self,check):super().__init__(check.get('message'));self.check=check
    def gate(raw):
        """Only IDs in today's public Workbench list (or released since) are assessed or shown."""
        if inventory is None:return valid_sid(raw)
        check=inventory.check(raw)
        if not check['ok']:raise Unavailable(check)
        return check['study_id']
    def home_html():
        home=HOME/'assets/landing.html'
        if home.exists():return home.read_text().replace('__MERIT_MODE__','native').replace('__MERIT_APP_SCRIPT__','')
        return '<!doctype html><html><head></head><body><h1>MERIT-ML</h1></body></html>'
    @app.errorhandler(Unavailable)
    def unavailable(exc):
        check=exc.check;status={'not_a_workbench_id':400,'workbench_unreachable':503}.get(check.get('reason'),404)
        if request.path.startswith('/api/') or request.accept_mimetypes.best=='application/json':
            return jsonify(ok=False,reason=check.get('reason'),error=check.get('message'),latest_public_study=check.get('latest'),
                           redirect=('/?study_id='+check['latest']) if check.get('latest') else None),status
        from live_progress import unavailable_page
        return Response(unavailable_page(home_html(),check),mimetype='text/html',status=status,headers={'Cache-Control':'no-store'})
    def study_form(form):
        sid=gate(form.get('study_id'));form['study_id']=sid
        if form.get('live_origin_study_id') and form['live_origin_study_id']!=sid:
            # Old page scripts collect sample edits at submit time. Such edits
            # and selected classes cannot be transferred to a different study.
            for key in ('live_job_id','label_classes','subjects_confirmed','matrix_overrides'):form.pop(key,None)
        # The loading page resumes THIS study, so its origin must advance too.
        form['live_origin_study_id']=sid
        return sid,form
    def session_data():
        raw=request.form.get('bulk_session','')
        value=json.loads(raw) if raw else request.get_json(force=True)
        studies=value.get('studies')
        if not isinstance(studies,list) or not studies:raise ValueError('Select at least one study')
        if len(studies)>500:raise ValueError('A bulk session is limited to 500 studies')
        for item in studies:item['study_id']=gate(item.get('study_id'))
        return value
    def scoped_bulk(session):
        session_id=session.get('live_session_id') or uuid.uuid4().hex
        if not re.fullmatch(r'[a-f0-9]{32}',session_id):raise ValueError('Invalid live bulk session ID')
        session['live_session_id']=session_id
        with jobs.lock:mapping=jobs.bulk_sessions.setdefault(session_id,{})
        result=[]
        for item in session['studies']:
            sid=item['study_id'];job=jobs.start(sid,job_id=mapping.get(sid));mapping[sid]=job['id'];result.append((sid,job))
        return result
    def bulk_report_link(job,sid,form):
        # Store only view settings and the completed job reference; reports stay interactive.
        key=hashlib.sha256(json.dumps({'study_id':sid,'job_id':job['id'],'form':form},sort_keys=True).encode()).hexdigest()
        with jobs.lock:
            token=next((t for t,v in jobs.bulk_report_views.items() if v.get('key')==key),None)
            if token is None:
                token=uuid.uuid4().hex
                jobs.bulk_report_views[token]={'key':key,'study_id':sid,'job':dict(job),'form':dict(form),'created':time.time()}
                atomic(jobs.bulk_report_views_path,jobs.bulk_report_views)
        return {'study_id':sid,'url':'/bulk/report/'+token,'job_id':job['id']}
    @app.get('/bulk/report/<token>')
    def bulk_report(token):
        from flask import abort
        if not re.fullmatch(r'[a-f0-9]{32}',token):abort(404)
        with jobs.lock:view=jobs.bulk_report_views.get(token)
        if not view:abort(404)
        sid=gate(view['study_id']);saved=dict(view['job'])
        run=Path(saved['run']).resolve()
        if (not run.is_relative_to(HOME/'runs') or saved.get('state')!='complete'
                or sid not in saved.get('studies',[])):
            abort(404)
        if not (run/'assessment/json'/f'{sid.lower()}_v9.json').is_file():
            return Response('This retained assessment has expired. Return to MERIT-ML and assess the study again.',status=410,mimetype='text/plain')
        with jobs.lock:jobs.jobs.setdefault(saved['id'],saved)
        job=jobs.explicit(sid,saved['id'])
        form={**view.get('form',{}),'study_id':sid,'live_job_id':job['id'],'live_origin_study_id':sid}
        return rendered(job,sid,form=form)
    def bulk_wait(session,pending):
        if request.accept_mimetypes.best=='application/json':return jsonify(ok=False,pending=pending,live_session_id=session['live_session_id']),202
        urls=json.dumps(['/api/jobs/'+j['id'] for j in pending])
        value=html.escape(json.dumps(session),quote=True)
        return Response('''<!doctype html><html><body style="font:16px sans-serif;background:#f4f1e9;padding:40px;color:#123135">
<main style="padding:28px;background:white;border-radius:20px"><h1>Preparing matrix downloads</h1><p id="status">Fetching and calculating the selected studies…</p>
<form id="resume" method="post" action="/bulk/download-data"><input type="hidden" name="bulk_session" value="'''+value+'''"></form></main>
<script>const urls='''+urls+''';async function poll(){try{let all=await Promise.all(urls.map(u=>fetch(u).then(r=>r.json())));
let bad=all.find(j=>j.state==='failed');if(bad){document.getElementById('status').textContent=bad.error;return;}
if(all.every(j=>j.state==='complete')){document.getElementById('resume').submit();return;}
document.getElementById('status').textContent=all.filter(j=>j.state==='complete').length+' / '+all.length+' complete';
}catch(e){document.getElementById('status').textContent='Reconnecting…';}setTimeout(poll,2000);}poll();</script></body></html>''',mimetype='text/html',status=202)
    def rendered(job,sid,action='page',form=None):
        value=jobs.render(job,sid,action,form)
        headers={'Cache-Control':'no-store'}
        if value.get('filename'):headers['Content-Disposition']='attachment; filename="'+value['filename']+'"'
        return Response(base64.b64decode(value['data_b64']),mimetype=value['mime'],headers=headers)
    def wait_page(job,sid,form):
        from live_progress import waiting_page
        return Response(waiting_page(job,sid,form),mimetype='text/html',status=202,headers={'Cache-Control':'no-store'})
    @app.errorhandler(Exception)
    def error(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc,HTTPException):return exc
        print('request failed:',request.path,repr(exc),file=sys.stderr,flush=True)
        if request.path.startswith('/api/') or request.method!='GET' and request.accept_mimetypes.best=='application/json':
            return jsonify(ok=False,error=str(exc)),400
        from live_progress import unavailable_page
        message=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else 'The request could not be completed. Please try again.'
        return Response(unavailable_page(home_html(),{'reason':'error','title':'Request not completed','message':message,'latest':None}),
                        mimetype='text/html',status=400,headers={'Cache-Control':'no-store'})
    @app.post('/api/bulk/validate')
    def bulk_validate():
        from bulk_entry import validate
        def classify(sid):
            if inventory is None:return dict(status='unknown',message='Public inventory is not ready; try again')
            with inventory.lock:listed=sid in inventory.studies
            if listed:return dict(status='available',message='Public study available')
            release=inventory.embargo.get(sid)
            if release and release>dt.date.today().isoformat():return dict(status='embargoed',message='Embargoed until '+release)
            public=inventory._public_now(sid)
            if public is None:return dict(status='unknown',message='Could not verify with Workbench; try again')
            return dict(status='available' if public else 'unavailable',message='Public study available' if public else 'Not publicly available; release/embargo status is not confirmed')
        try:return jsonify(validate((request.get_json(silent=True) or {}).get('text'),classify,parallel=True))
        except ValueError as exc:return jsonify(ok=False,error=str(exc)),400
    @app.get('/api/public-studies')
    def public_studies():
        return jsonify(inventory.status() if inventory else {'error':'not initialised'})
    @app.get('/healthz')
    def health():return jsonify(ok=True,mode='live-input',historical_results_dependency=False,
                               reuse_completed_assessments=bool(jobs.prepared_studies),prepared_cohort_studies=len(jobs.prepared_studies),renderer='pinned-current-ui')
    @app.get('/')
    def home():
        if not request.args.get('study_id'):
            # A landing-page visit never loads or renders a previous study.
            from live_progress import enhance_page
            home=HOME/'assets/landing.html'
            if home.exists():return Response(enhance_page(home_html()),mimetype='text/html',headers={'Cache-Control':'no-store'})
            return '<form method="get"><h1>MERIT-ML</h1><label>Study ID <input name="study_id" pattern="ST[0-9]{6}" required></label><button>Search</button></form>'
        args=request.args.to_dict();refresh=args.pop('refresh','')=='1'
        sid,form=study_form(args)
        job=jobs.start(sid,job_id=None if refresh else form.get('live_job_id'),refresh=refresh)
        if refresh:form.pop('live_job_id',None)
        return rendered(job,sid,form=form) if job['state']=='complete' else wait_page(job,sid,form)
    @app.get('/workspace')
    def workspace():
        return Response(status=302,headers={'Location':'/'})
    @app.post('/workflow/run')
    def workflow():
        sid,form=study_form(request.form.to_dict())
        job=jobs.start(sid,job_id=form.get('live_job_id'))
        return rendered(job,sid,form=form) if job['state']=='complete' else wait_page(job,sid,form)
    @app.post('/workflow/load-state')
    def restore():
        if request.files:
            value=json.load(next(iter(request.files.values())).stream)
        elif request.form.get('state_json'):
            value=json.loads(request.form['state_json'])
        else:
            value=request.get_json(silent=True)
        if not isinstance(value,dict):raise ValueError('Supply the assessment JSON as a JSON upload; server file paths are not accepted')
        sid=value.get('study_id') or (value.get('metadata_fair_per_analysis') or {}).get('study_id') or (value.get('structural_analysis_endpoint') or {}).get('study_id')
        sid=gate(sid)
        # Restore choices only. All supplied result values are ignored.
        form={'study_id':sid}
        params={}
        for block in value.values():
            if isinstance(block,dict) and isinstance(block.get('parameters'),dict):params.update(block['parameters'])
        params.update(value.get('v2_scoring_params') or {})
        settings=(value.get('overview_scores') or {}).get('readiness_settings') or {}
        for field,key in (('folds','cv_folds'),('extreme_percentile','readiness_extreme_pct'),('low_percentile','readiness_low_pct')):
            if field in settings:params[key]=settings[field]
        choice=(value.get('label_structure') or {}).get('class_choice')
        if choice:params['label_classes']=json.dumps(choice)
        ml=value.get('ml_task_readiness') or {}
        if ml.get('subjects_confirmed'):params['subjects_confirmed']='true'
        for field,key in (('k','sep_k'),('theta','sep_theta'),('fdr','sep_fdr')):
            if field in ml:params[key]=ml[field]
        form.update({k:str(v) for k,v in params.items() if isinstance(v,(str,int,float,bool))})
        edits=value.get('sample_edits') or (value.get('overview_scores') or {}).get('sample_edits') or {}
        form['matrix_overrides']=json.dumps(edits)
        job=jobs.start(sid)
        return rendered(job,sid,form=form) if job['state']=='complete' else wait_page(job,sid,form)
    @app.post('/bulk/run')
    def bulk_run():
        from live_bulk import page
        session=session_data()
        session['live_session_id']=uuid.uuid4().hex
        source=HOME/'source_snapshot/merit-ui-v9-analysis-endpoint-pilot/v8_layout/merit/ui.py'
        return Response(page(session,source),mimetype='text/html')
    @app.post('/bulk/chunk')
    def bulk_chunk():
        session=session_data();pending=[]
        scoped=scoped_bulk(session)
        for sid,job in scoped:
            if job['state']=='failed':raise RuntimeError(job.get('error') or 'Assessment failed')
            if job['state']!='complete':pending.append(jobs.status(job))
        if pending:return jsonify(ok=False,pending=pending,live_session_id=session['live_session_id']),202
        summaries=[];metrics=[];settings={};reports=[]
        for item in session['studies']:
            sid=item['study_id'];form=dict(item.get('scoring_params') or {})
            form['matrix_overrides']=json.dumps(item.get('matrix_overrides') or {})
            for k in ('label_classes','subjects_confirmed'):
                if k in item:form[k]=item[k]
            result=jobs.render(jobs.explicit(sid,jobs.bulk_sessions[session['live_session_id']][sid]),sid,'bulk-records',form)
            value=json.loads(base64.b64decode(result['data_b64']))
            summaries.extend(value['summary_rows']);metrics.extend(value['metric_rows']);settings[sid]=value['settings']
            reports.append(bulk_report_link(jobs.explicit(sid,jobs.bulk_sessions[session['live_session_id']][sid]),sid,form))
        return jsonify(ok=True,summary_rows=summaries,metric_rows=metrics,settings=settings,reports=reports,n_used=len(session['studies']),errors=[],live_session_id=session['live_session_id'])
    @app.post('/bulk/download-data')
    def bulk_data():
        session=session_data();pending=[]
        for sid,job in scoped_bulk(session):
            if job['state']!='complete':pending.append(jobs.status(job))
        if pending:return bulk_wait(session,pending)
        output=tempfile.TemporaryFile(dir=WEB)
        try:
            with zipfile.ZipFile(output,'w',zipfile.ZIP_STORED) as z:
                for item in session['studies']:
                    sid=item['study_id'];form=dict(item.get('scoring_params') or {})
                    form['matrix_overrides']=json.dumps(item.get('matrix_overrides') or {})
                    form['analysis_ids']=json.dumps(item.get('analysis_ids') or [])
                    result=jobs.render(jobs.explicit(sid,jobs.bulk_sessions[session['live_session_id']][sid]),sid,'data',form)
                    z.writestr(result['filename'],base64.b64decode(result['data_b64']))
                z.writestr('session.json',json.dumps(session))
            output.seek(0)
            response=send_file(output,mimetype='application/zip',as_attachment=True,download_name='merit_bulk_matrix_inputs.zip')
            response.call_on_close(output.close);return response
        except Exception:
            output.close();raise
    @app.post('/api/assess')
    def assess():
        data=request.get_json(silent=True) or request.form
        sid=gate(data.get('study_id'));job=jobs.start(sid,refresh=data.get('refresh') is True,job_id=data.get('live_job_id'),online_pull_run_id=data.get('online_pull_run_id'))
        return jsonify(jobs.status(job)),200 if job['state']=='complete' else 202
    @app.get('/api/jobs')
    def statuses():
        jobs.expire();return jsonify([jobs.status(dict(j)) for j in jobs.jobs.values()])
    @app.get('/api/jobs/<key>')
    def status(key):
        job=jobs.jobs.get(key)
        if not job:return jsonify(error='Unknown job'),404
        return jsonify(jobs.status(dict(job)))
    @app.get('/api/jobs/<key>/ui-state')
    def ui_state(key):
        job=jobs.jobs.get(key)
        if not job:return jsonify(error='Unknown job'),404
        return rendered(job,job['studies'][0],'ui-state')
    @app.get('/api/online-pull/<run_id>')
    def online_pull_status(run_id):
        from online_pull_common import RUN_ID
        if not RUN_ID.fullmatch(run_id):raise ValueError('Invalid run identifier')
        p=HOME/'runs'/run_id/'online_pull_progress.json'
        return jsonify(json.loads(p.read_text())) if p.exists() else (jsonify(error='Run not found'),404)
    @app.get('/online-pull/<run_id>')
    def online_pull_dashboard(run_id):
        from online_pull_dashboard import page
        return Response(page(run_id),mimetype='text/html',headers={'Cache-Control':'no-store'})
    @app.get('/api/online-pull/<run_id>/studies')
    def online_pull_studies(run_id):
        from online_pull_common import RUN_ID
        if not RUN_ID.fullmatch(run_id):raise ValueError('Invalid run identifier')
        root=HOME/'runs'/run_id
        config=json.loads((root/'online_pull_config.json').read_text())
        cp=root/'online_pull_checkpoint.json';records=json.loads(cp.read_text()).get('studies',{}) if cp.exists() else {}
        dp=root/'study_deposition_provenance.json';dates=json.loads(dp.read_text()) if dp.exists() else {}
        rows=[]
        for sid in config['study_ids']:
            rec=records.get(sid,{})
            rows.append({'study_id':sid,'submission_date':dates.get(sid,{}).get('submission_date'),
                         'release_date':dates.get(sid,{}).get('release_dates_raw',[]),'state':rec.get('state','not_started'),
                         'matrices':rec.get('valid_matrices'),
                         'url':'/?study_id='+sid+'&live_job_id='+rec['job_id'] if rec.get('state')=='complete' else None})
        return jsonify(studies=rows,total=len(rows))
    @app.get('/api/online-pull/<run_id>/deposition-dates')
    def online_pull_dates(run_id):
        from online_pull_common import RUN_ID
        if not RUN_ID.fullmatch(run_id):raise ValueError('Invalid run identifier')
        p=HOME/'runs'/run_id/'study_deposition_dates.tsv'
        return send_file(p,mimetype='text/tab-separated-values',as_attachment=True,download_name='study_deposition_dates.tsv') if p.exists() else ('Date provenance not present for this cohort',404)
    @app.get('/api/provenance/<sid>')
    def provenance(sid):
        sid=valid_sid(sid);job=jobs.explicit(sid,request.args.get('live_job_id')) if request.args.get('live_job_id') else jobs.get(sid)
        if not job:return jsonify(error='No fetched study'),404
        p=Path(job['run']);mf=Path(job.get('fetch_run') or p)/'inputs/fetch_manifest.json'
        runtime=HOME
        if job.get('online_pull_run_id'):
            from online_pull_common import context
            _,_,runtime,_=context(job['online_pull_run_id'],sid)
        dates={}
        if job.get('online_pull_run_id'):
            dp=HOME/'runs'/job['online_pull_run_id']/'study_deposition_provenance.json'
            if dp.exists():dates=json.loads(dp.read_text()).get(sid,{})
        policy=p/'inputs/reference/reference_policy.json'
        return jsonify({'study_id':sid,'state':job['state'],'job':job['id'],
                        'calculation_reused_from':job.get('reused_calculation_from'),'input_digest':job.get('input_digest'),
                        'fetch':json.loads(mf.read_text()) if mf.exists() else {},
                        'materialization':json.loads((p/'materialization.json').read_text()) if (p/'materialization.json').exists() else [],
                        'source_snapshot':json.loads((runtime/'source_snapshot_manifest.json').read_text()),
                        'reference':json.loads((runtime/'assets/alert_reference_R1.json').read_text()),
                        'chemical_reference_policy':json.loads(policy.read_text()) if policy.exists() else {'policy':'fresh chemical reference'},
                        'deposition_dates':dates})
    @app.post('/download/feature-list')
    @app.post('/download/v9-matrix')
    @app.post('/download/ml-ready-data')
    @app.post('/download/ml-ready-matrix')
    def download():
        form=request.form.to_dict();sid=valid_sid(form.get('study_id'))
        job=jobs.explicit(sid,form.get('live_job_id')) if form.get('live_job_id') else jobs.get(sid)
        if not job:return jsonify(error='Assess this study first'),404
        return rendered(job,sid,{'/download/feature-list':'feature-list','/download/v9-matrix':'matrix','/download/ml-ready-data':'data',
                                  '/download/ml-ready-matrix':'ml-ready-matrix'}[request.path],form)
    @app.get('/download/acquisition-evidence')
    def acquisition():
        sid=valid_sid(request.args.get('study_id'))
        job=jobs.explicit(sid,request.args.get('live_job_id')) if request.args.get('live_job_id') else jobs.get(sid)
        if not job:return jsonify(error='Assess this study first'),404
        return rendered(job,sid,'acquisition')
    @app.get('/assets/logo.png')
    def logo():
        candidates=[HOME/'source_snapshot/merit-ui-v9-analysis-endpoint-pilot/v8_layout/static/merit-logo-sidebar.png',HOME/'source_snapshot/Logo.png']
        p=next((p for p in candidates if p.exists()),None)
        return Response(p.read_bytes(),mimetype='image/png') if p else ('',404)
    @app.get('/study-browser-data')
    def browser():
        from live_catalog import Catalog,payload
        current=catalog or Catalog(root=HOME/'catalog')
        result=payload(current.merged(jobs),request.args.to_dict())
        result['live_catalog']=current.status()
        return jsonify(result)
    @app.get('/api/catalog')
    def catalog_status():
        return jsonify(catalog.status() if catalog else {'studies':0,'refreshing':False,'error':'catalog not initialized'})
    @app.get('/api/visit-stats')
    def stats():return jsonify(ok=False,reason='Visit analytics are not collected in the isolated live pilot')
    return app


def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8783);p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--adopt-run',action='append',default=[]);p.add_argument('--allow-result-reuse',action='store_true')
    p.add_argument('--resume-online-pull',action='append',default=[],help='Explicitly recover jobs from this same live-only cohort after a service restart')
    p.add_argument('--prepared-cohort',action='append',default=[],help='Explicitly serve locally calculated reports of this cohort without another fetch')
    p.add_argument('--retain',action='store_true');p.add_argument('--ttl-hours',type=float,default=24)
    p.add_argument('--workers',type=int,default=4,help='Assessments calculated at the same time')
    p.add_argument('--fresh-minutes',type=float,default=60,help='A completed assessment is reused while its Workbench download is this recent')
    args=p.parse_args();jobs=Jobs(ttl=args.ttl_hours*3600,retain=args.retain,prepared_cohorts=args.prepared_cohort,
                                  workers=args.workers,fresh_seconds=args.fresh_minutes*60)
    if args.adopt_run and not args.allow_result_reuse:p.error('Live mode refuses prebuilt assessment adoption. Submit a study for a fresh API calculation.')
    for run in args.adopt_run:jobs.adopt(run)
    for run_id in args.resume_online_pull:jobs.resume_online_pull(run_id)
    atomic(WEB/f'server-{args.port}.json',{'pid':os.getpid(),'port':args.port,'started_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    def cleanup():
        while True:
            time.sleep(60)
            jobs.expire();jobs.stop_idle_renderers()
    threading.Thread(target=cleanup,name='temporary-job-cleanup',daemon=True).start()
    from live_catalog import Catalog
    from live_inventory import Inventory
    catalog=Catalog();catalog.start()
    inventory=Inventory();inventory.start()
    create_app(jobs,catalog,inventory).run(host=args.host,port=args.port,threaded=True,use_reloader=False)


if __name__=='__main__':main()
