"""Detached/resumable full-cohort controller using the live 8791 API.

Only directory names are taken from the frozen dump. No deposit or v9 result is
read by any calculation. Comparison has an explicit post-calculation barrier.
"""
import argparse
import concurrent.futures
import datetime as dt
import fcntl
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from online_pull_common import ROOT,atomic,digest
from online_pull_capture import capture


def utc():return dt.datetime.now(dt.timezone.utc).isoformat()


def http(base,path,body=None,timeout=900):
    req=base+path if body is None else urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)


def initialize(args):
    if args.run_root:return Path(args.run_root).resolve()
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run=ROOT/'runs'/('online_pull_all_studies_'+stamp);run.mkdir(parents=True)
    # Read names ONLY: the frozen source is never passed to a worker.
    ids=sorted(p.name for p in args.study_id_dump.iterdir() if p.is_dir() and re.fullmatch(r'ST\d{6}',p.name))
    if len(ids)!=args.expected_count:raise ValueError(f'Expected {args.expected_count} study IDs; found {len(ids)}')
    (run/'online_pull_study_ids.txt').write_text('\n'.join(ids)+'\n')
    config={'run_id':run.name,'created_at_utc':utc(),'input_policy':'live_only','study_ids':ids,'expected_count':len(ids),
            'study_id_source':str(args.study_id_dump.resolve()),'study_id_source_role':'directory names only',
            'study_id_list_sha256':digest(run/'online_pull_study_ids.txt'),
            'live_url':args.live_url.rstrip('/'),'baseline_v9':str(args.baseline_v9.resolve()),
            'baseline_ui_url':args.baseline_ui_url.rstrip('/'),'compare_served_ui':True,
            'attempts_per_study':args.attempts,'minimum_free_gib':args.minimum_free_gib,
            'references':'one freshly fetched, immutable chemical snapshot for this online-pull run',
            'all_ui_blocks_required':True,'comparison_policy':'after all calculation attempts; literal bytes plus decoded JSON and NumPy payload bytes, no tolerance',
            'independent_model_design_reading':'not run; explicitly disclosed; never copied from a cache'}
    atomic(run/'online_pull_config.json',config)
    atomic(ROOT/'online_pull_latest.json',{'run_root':str(run),'run_id':run.name,'started_at_utc':utc()})
    return run


class Controller:
    def __init__(self,run):
        self.run=Path(run);self.config=json.loads((self.run/'online_pull_config.json').read_text())
        self.ids=self.config['study_ids'];self.records={};self.started=time.time();self.phase='preparing';self.current={};self.error=None
        dates=self.run/'study_deposition_provenance.json'
        self.date_provenance=json.loads(dates.read_text()) if dates.exists() else {}
        path=self.run/'online_pull_checkpoint.json'
        if path.exists():
            state=json.loads(path.read_text());self.records=state.get('studies',{});self.started=state.get('started_epoch',self.started)
        self.log=self.run/'online_pull_events.jsonl';self.last_save=0

    def event(self,kind,**extra):
        record={'utc':utc(),'event':kind,**extra}
        with self.log.open('a') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
        print(json.dumps(record,ensure_ascii=False),flush=True)

    def save(self):
        completed=sum(v.get('state')=='complete' for v in self.records.values())
        failed=sum(v.get('state')=='exhausted' for v in self.records.values())
        matrices=sum(v.get('valid_matrices',0) for v in self.records.values() if v.get('state')=='complete')
        elapsed=time.time()-self.started
        new_cohort=self.config.get('cohort_type')=='new_deposits'
        comparison_state=('not applicable: new studies have no historical per-study baseline' if new_cohort else
                          'running' if self.phase.startswith(('compar','snapshot')) else
                          'finished' if self.phase.startswith('finished') else 'queued until all cohort calculation attempts finish')
        recent=sorted(((sid,v) for sid,v in self.records.items() if v.get('state')=='complete'),
                      key=lambda kv:kv[1].get('completed_at_utc',''),reverse=True)[:20]
        progress={'run_id':self.run.name,'phase':self.phase,'phase_label':self.phase.replace('_',' ').capitalize(),
                  'completed':completed,'total':len(self.ids),'failed':failed,'pending':len(self.ids)-completed-failed,
                  'matrices':matrices,'current':self.current,'elapsed_seconds':round(elapsed),
                  'eta_seconds':round(elapsed/completed*(len(self.ids)-completed-failed)) if completed>=5 else None,
                  'free_disk_gib':round(shutil.disk_usage(self.run).free/1024**3,1),
                  'updated_at_utc':utc(),'error':self.error,
                  'comparison_state':comparison_state,
                  'cohort_description':self.config.get('cohort_description','Original 4,121-study online comparison'),
                  'reference_policy':self.config.get('references','fresh chemical references'),
                  'recent_completed':[{'study_id':sid,'submission_date':self.date_provenance.get(sid,{}).get('submission_date'),
                                       'matrices':v.get('valid_matrices',0),
                                       'url':'/?study_id='+sid+'&live_job_id='+v['job_id']} for sid,v in recent],
                  'dashboard':self.config['live_url']+'/online-pull/'+self.run.name}
        atomic(self.run/'online_pull_progress.json',progress)
        atomic(self.run/'online_pull_checkpoint.json',{'started_epoch':self.started,'studies':self.records,'progress':progress})
        self.last_save=time.time()

    def ensure_disk(self):
        limit=self.config['minimum_free_gib']
        while shutil.disk_usage(self.run).free<limit*1024**3:
            self.error=f'Waiting for disk space: less than {limit} GiB free. No input or result has been deleted.'
            self.phase='waiting_for_disk';self.save();time.sleep(10)
        self.error=None

    def prepare(self):
        self.phase='preparing_pinned_runtime';self.save()
        tools=self.run/'online_pull_pipeline_code';tools.mkdir(exist_ok=True)
        for name in ('online_pull_cohort.py','online_pull_capture.py','online_pull_compare.py','online_pull_progress.py','online_pull_common.py','test_online_pull.py',
                     'validate_new_study_annotation.py','discover_new_studies.py','prepare_new_studies_runtime.py','validate_new_studies_runtime.py','test_new_studies_pipeline.py'):
            if not (tools/name).exists():shutil.copy2(ROOT/name,tools/name)
        atomic(self.run/'online_pull_pipeline_code_manifest.json',{'files':{p.name:digest(p) for p in tools.iterdir() if p.is_file()}})
        runtime=self.run/'online_pull_runtime'
        if not (runtime/'bundle_manifest.json').exists():
            if runtime.exists():shutil.rmtree(runtime)  # this run's incomplete code-only copy
            from package_live_tool import build
            build(runtime)
        refs=self.run/'online_pull_shared_reference_inputs';complete=refs/'online_pull_reference_ready.json'
        if not complete.exists():
            self.phase='fetching_shared_live_reference';self.current={'label':'Downloading fresh RefMet references once for this cohort'};self.save()
            from fetch_online import Fetcher,API,BASE
            f=Fetcher(refs)
            jobs=[(API+'/refmet/all/','reference/refmet_all.json'),(API+'/refmet/all_ids','reference/refmet_all_ids.json'),
                  (BASE+'/databases/refmet/refmet_download.php','reference/refmet_download.csv')]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda x:f.get(*x),jobs))
            f.require_success([j[1] for j in jobs])
            atomic(complete,{'downloaded_live_at_utc':utc(),'requests':f.entries,'same_run_only':True})
        self.event('runtime_and_live_reference_ready',runtime=str(runtime),reference_ready=str(complete))

    def wait_job(self,sid,job,record):
        last_event=None;connection_failures=0
        while True:
            try:
                job=http(self.config['live_url'],'/api/jobs/'+job['id'],timeout=60);connection_failures=0
            except urllib.error.HTTPError as exc:
                if exc.code==404:raise RuntimeError('8791 restarted or lost this job; a new explicit cohort attempt is needed')
                raise
            except Exception as exc:
                connection_failures+=1;self.error='Waiting for 8791: '+str(exc);self.save()
                if connection_failures>=36:raise RuntimeError('8791 unavailable for repeated status checks') from exc
                time.sleep(5);continue
            self.error=None;pr=job.get('progress') or {};self.phase='online_pull_and_calculation'
            self.current={'study_id':sid,'ordinal':self.ids.index(sid)+1,'job_id':job['id'],'job_state':job['state'],
                          'label':pr.get('label',job['state']),'detail':pr.get('detail',''),
                          'requests_completed':job.get('fetch',{}).get('requests_completed',0),'attempt':record['attempts']}
            if job['state']!=last_event:self.event('job_phase',study_id=sid,job_id=job['id'],phase=job['state']);last_event=job['state']
            self.save()
            if job['state']=='failed':raise RuntimeError(job.get('error','calculation failed')+'\n'+job.get('log_tail','')[-12000:])
            if job['state']=='complete':return job
            time.sleep(5)

    def one(self,sid):
        self.ensure_disk();record=self.records.setdefault(sid,{'attempts':0,'history':[]})
        # A completed study is never fetched twice when the controller resumes.
        if record.get('state')=='complete':return
        record['attempts']+=1;record['state']='running';record['started_at_utc']=utc()
        try:
            job=None
            if record.get('job_id'):
                try:
                    existing=http(self.config['live_url'],'/api/jobs/'+record['job_id'],timeout=60)
                    if existing['state'] in ('queued','fetching','computing','complete'):job=existing
                except Exception:pass
            if job is None:
                job=http(self.config['live_url'],'/api/assess',{'study_id':sid,'online_pull_run_id':self.run.name,'refresh':True})
            record['job_id']=job['id'];record['job_root']=str(self.run/'online_pull_jobs'/job['id']);self.save()
            self.event('study_started',study_id=sid,ordinal=self.ids.index(sid)+1,total=len(self.ids),job_id=job['id'],attempt=record['attempts'])
            job=self.wait_job(sid,job,record)
            self.phase='capturing_all_ui_blocks';self.current.update(label='Rendering all default UI blocks and checking every supporting store',detail='');self.save()
            public=http(self.config['live_url'],'/api/jobs/'+job['id']+'/ui-state',timeout=3600)
            if self.config.get('validate_annotation_against_current_8782'):
                self.phase='checking_annotation_parity';self.current.update(label='Checking every Annotation record and per-feature evidence against current 8782 logic');self.save()
                with (Path(record['job_root'])/'annotation_algorithm_check.log').open('a') as log:
                    subprocess.run([sys.executable,str(ROOT/'validate_new_study_annotation.py'),'--run-root',str(self.run),
                                    '--job-root',record['job_root'],'--study-id',sid],check=True,stdout=log,stderr=subprocess.STDOUT,
                                   env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1'})
            self.phase='retaining_online_pull_outputs';self.save()
            info=capture(self.run,record['job_root'],sid,public)
            if sid in self.date_provenance:
                from fetch_online import records
                evidence=self.date_provenance[sid]
                raw=Path(record['job_root'])/'inputs'/sid/'summary.json'
                live_dates={str(x.get('submission_date') or '').strip() for x in records(json.loads(raw.read_text()))}
                if live_dates!={evidence['submission_date']}:
                    raise ValueError('Submission date changed or conflicts with cohort selection: '+str(sorted(live_dates)))
                info['deposition_provenance']={**evidence,'assessment_summary_file':str(raw),'assessment_summary_sha256':digest(raw)}
                atomic(self.run/'online_pull_study_manifests'/f'{sid.lower()}.json',info)
            record.update(state='complete',completed_at_utc=utc(),valid_matrices=info['valid_matrices'],analyses=info['analyses'])
            self.event('study_completed',study_id=sid,completed=sum(x.get('state')=='complete' for x in self.records.values()),total=len(self.ids),matrices=info['valid_matrices'])
        except Exception:
            error=traceback.format_exc();record['history'].append({'attempt':record['attempts'],'job_id':record.get('job_id'),'error':error,'utc':utc()})
            record['state']='exhausted' if record['attempts']>=self.config['attempts_per_study'] else 'retry'
            # A terminal failed build must receive fresh inputs on retry. A
            # capture failure can resume the successfully calculated snapshot.
            if job and job.get('state')=='failed':record.pop('job_id',None)
            self.event('study_attempt_failed',study_id=sid,attempt=record['attempts'],state=record['state'],error=error[-2500:])
            atomic(self.run/'online_pull_failures'/f'{sid.lower()}.json',record)
        self.save()

    def execute(self):
        for attempt in range(3):
            try:self.prepare();self.error=None;break
            except Exception:
                if attempt==2:raise
                self.error=traceback.format_exc();self.phase='retrying_reference_preparation';self.save();time.sleep(30)
        for sid in self.config.get('preflight_studies',[]):
            if sid not in self.ids:raise ValueError('Preflight study is outside the selected cohort')
            if self.records.get(sid,{}).get('state') not in ('complete','exhausted'):self.one(sid)
        while True:
            pending=[sid for sid in self.ids if self.records.get(sid,{}).get('state') not in ('complete','exhausted')]
            if not pending:break
            for sid in pending:self.one(sid)
            if any(self.records.get(sid,{}).get('state')=='retry' for sid in self.ids):
                self.phase='waiting_before_retry_pass';self.current={};self.save();time.sleep(30)
        failed={sid:r for sid,r in self.records.items() if r['state']!='complete'}
        atomic(self.run/'online_pull_calculation_finished.json',{'finished_at_utc':utc(),'studies_finished':self.ids,'failed':failed,
              'succeeded':[s for s in self.ids if self.records[s]['state']=='complete']})
        cache=self.run/'online_pull_v9_compatible_cache';cache.mkdir(exist_ok=True)
        atomic(cache/'checkpoint.json',{'done':len(self.ids)-len(failed),'total':len(self.ids),'problems':len(failed),'online_pull_run':self.run.name})
        atomic(cache/'manifest.json',{'schema':'merit.online_pull.v9_compatible.v1','input':'fresh online sources only',
              'chemical_reference_policy':self.config.get('references','fresh chemical references'),
              'pinned_runtime':str(self.run/'online_pull_runtime'),'shared_live_reference':str(self.run/'online_pull_shared_reference_inputs'),
              'completed_studies':len(self.ids)-len(failed),'failed_studies':sorted(failed),'all_output_folders_retained':True})
        if self.config.get('cohort_type')=='new_deposits':
            gate=json.loads((self.run/'current_8782_source_and_reference_gate.json').read_text())
            source_manifest=json.loads((self.run/'online_pull_runtime/source_snapshot_manifest.json').read_text())
            drift=[r['source'] for r in source_manifest['files'] if digest(r['source'])!=r['source_sha256']]
            atomic(self.run/'online_new_studies_completion.json',{
                'finished_at_utc':utc(),'selected_studies':len(self.ids),'completed':len(self.ids)-len(failed),
                'failed':sorted(failed),'matrices':sum(v.get('valid_matrices',0) for v in self.records.values() if v.get('state')=='complete'),
                'reference_gate':gate,'8782_source_changes_since_pinning':drift,
                'date_provenance':'study_deposition_provenance.json; exact dates verified against fetched study summaries',
                'comparison':'No per-study historical baseline exists; all required output blocks and stores checked locally.'})
            self.phase='finished_with_failures' if failed else 'finished';self.current={};self.save()
            self.event('new_studies_run_finished',completed=len(self.ids)-len(failed),failed=len(failed),source_drift=drift)
            return
        self.current={};self.phase='comparing_v9';self.save()
        # Deliberately imported only after the full cohort pass and retries.
        sys.path.insert(0,str(self.run/'online_pull_pipeline_code'))
        from online_pull_compare import compare
        def notify(phase,detail):
            self.phase=phase;self.current={'label':detail};self.save()
        result=compare(self.run,notify)
        self.phase='finished_with_failures' if failed or result['file_counts'].get('comparison_errors') or result['actual_8782_served_ui'].get('report_errors') else 'finished'
        self.current={};self.save();self.event('run_finished',phase=self.phase,overlap_summary=str(self.run/'online_pull_comparison/online_pull_overlap_summary.json'))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run-root',type=Path)
    ap.add_argument('--study-id-dump',type=Path,default=ROOT.parent/'mw-dump-latest-18thSep-2026-version')
    ap.add_argument('--expected-count',type=int,default=4121);ap.add_argument('--live-url',default='http://127.0.0.1:8791')
    ap.add_argument('--baseline-v9',type=Path,default=ROOT.parent/'merit-cache-workbench-full-v9')
    ap.add_argument('--baseline-ui-url',default='http://127.0.0.1:8782');ap.add_argument('--attempts',type=int,default=3)
    ap.add_argument('--minimum-free-gib',type=float,default=25);ap.add_argument('--prepare-only',action='store_true')
    args=ap.parse_args()
    global_lock=(ROOT/'online_pull_cohort_active.lock').open('a')
    fcntl.flock(global_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    run=initialize(args)
    print('ONLINE_PULL_RUN_ROOT='+str(run),flush=True)
    lock=(run/'online_pull_controller.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    c=Controller(run);atomic(run/'online_pull_controller.json',{'pid':os.getpid(),'pid_namespace':os.readlink('/proc/self/ns/pid'),
                                                             'started_at_utc':utc(),'python':sys.executable})
    try:
        c.save()
        if not args.prepare_only:c.execute()
    except Exception:
        c.phase='controller_error';c.error=traceback.format_exc();c.save();c.event('controller_error',error=c.error);raise


if __name__=='__main__':main()
