"""MERIT v2 native live entry point; no historical study-result bundle."""
import hashlib
import json
import os
import selectors
import sys
import threading
import time
from pathlib import Path
import live_app as app
import live_catalog
os.environ['MERIT_NATIVE_SUPERVISION']='1'

_Catalog=live_catalog.Catalog
class NativeCatalog(_Catalog):
    def __init__(self,root=None,resume=False):
        super().__init__(root=root or app.HOME/'runs/catalog',resume=resume)
live_catalog.Catalog=NativeCatalog

original_run=app.subprocess.run
def isolated_assessment(command,*args,**kwargs):
    if isinstance(command,list) and len(command)>1 and Path(command[1]).name=='online_assess.py':
        command=[*command];command[1]=str(Path(__file__).parent/'matrix_driver.py')
    # A blocked renderer cannot hold a browser request indefinitely.
    if isinstance(command,list) and len(command)>1 and Path(command[1]).name=='live_render.py':
        kwargs.setdefault('timeout',float(os.environ.get('MERIT_RENDER_TIMEOUT_SECONDS','3600')))
    return original_run(command,*args,**kwargs)
app.subprocess.run=isolated_assessment
old_version=app.code_version
def version():
    h=hashlib.sha256(old_version().encode())
    for name in ('v2_server.py','matrix_driver.py','matrix_worker.py','native_resources.py','native_guard.py','metric_worker.py'):
        h.update((Path(__file__).parent/name).read_bytes())
    return h.hexdigest()
app.code_version=version

def ask(self,payload):
    with self.lock:
        self.last=time.time()
        if self.proc.poll() is not None:raise RuntimeError('renderer stopped')
        self.proc.stdin.write(json.dumps(payload)+'\n');self.proc.stdin.flush()
        with selectors.DefaultSelector() as select:
            select.register(self.proc.stdout,selectors.EVENT_READ)
            if not select.select(float(os.environ.get('MERIT_RENDER_TIMEOUT_SECONDS','3600'))):
                self.stop();raise RuntimeError('Rendering exceeded its time limit; completed calculations are retained')
            line=self.proc.stdout.readline()
        if not line:raise RuntimeError('renderer stopped; completed calculations are retained')
        self.last=time.time();return json.loads(line)
app.WarmRenderer.ask=ask
old_build=app.Jobs.build
def build(self,key):
    old_build(self,key)
    job=self.jobs[key];path=Path(job['run'])/'matrix_execution.json'
    if job['state']=='complete' and path.exists():
        report=json.loads(path.read_text())
        if report.get('partial'):
            # A fresh retry must not reuse an incomplete calculation solely because inputs match.
            with self.lock:
                self.results={k:v for k,v in self.results.items() if v.get('run')!=job['run']}
                app.atomic(self.result_index_path,self.results)
            self.update(key,partial_results=True,execution_report=f'/api/v2/execution/{key}')
app.Jobs.build=build
old_create=app.create_app
def create(jobs,catalog=None,inventory=None):
    service=old_create(jobs,catalog,inventory)
    @service.after_request
    def add_version(response):
        response.headers['X-MERIT-Version']='v2-live-20261108-r11'
        return response
    @service.get('/api/v2/execution/<key>')
    def execution(key):
        from flask import jsonify,abort
        job=jobs.jobs.get(key)
        if not job:abort(404)
        path=Path(job['run'])/'matrix_execution.json'
        return jsonify(json.loads(path.read_text()) if path.exists() else {'state':'pending'})
    return service
app.create_app=create

if __name__=='__main__':
    # One study at a time keeps memory predictable; matrices/checks are independently supervised.
    if '--workers' not in sys.argv:sys.argv+=['--workers','1']
    app.main()
