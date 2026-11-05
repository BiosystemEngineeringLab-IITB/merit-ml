"""End-to-end release validation using raw live-response fixtures, never imported results.

Expected assessment is used only after building, by this separate comparator.
Run with the release's Python environment (or inside its Docker image).
"""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

os.environ.setdefault('MERIT_NATIVE_SUPERVISION','1')
ROOT=Path(__file__).resolve().parents[1];RUNTIME=ROOT/'docker/merit-v2/runtime'
p=argparse.ArgumentParser();p.add_argument('--fixture',type=Path,required=True);p.add_argument('--expected',type=Path,required=True)
p.add_argument('--output',type=Path,default=ROOT/'release-artifacts/validation.json');p.add_argument('--skip-build',action='store_true')
a=p.parse_args();checks=[];started=time.time()
def check(name,condition,details=None):
 checks.append({'check':name,'passed':bool(condition),'details':details});print(name, bool(condition),flush=True)
 if not condition:raise AssertionError(name+': '+str(details))
def fixture(name):
 run=RUNTIME/'runs'/name;run.mkdir(parents=True,exist_ok=True)
 shutil.copytree(a.fixture/'inputs',run/'inputs',dirs_exist_ok=True)
 shutil.copyfile(a.fixture/'live_registry.json',run/'live_registry.json')
 (run/'REPLAY_NOT_LIVE.json').write_text(json.dumps({'validation_only':True,'no_results_imported':True}))
 return run

def build(run,env=None):
 with (run/'driver.log').open('w') as f:
  subprocess.run([sys.executable,str(RUNTIME/'matrix_driver.py'),'--run-root',str(run)],env={**os.environ,**(env or {})},stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)

def render(run,action='page',form=None,env=None):
 payload={'run':str(run),'study_id':'ST000001','action':action,'form':form or {}}
 proc=subprocess.run([sys.executable,str(RUNTIME/'live_render.py')],input=json.dumps(payload),text=True,capture_output=True,env={**os.environ,**(env or {})},timeout=600)
 if proc.returncode:raise RuntimeError(proc.stdout[-2000:]+proc.stderr[-2000:])
 value=json.loads(proc.stdout);check('render '+action,value['ok']);return base64.b64decode(value['data_b64'])

def results(run):return json.loads((run/'assessment/json/st000001_v9.json').read_text())
try:
 run=fixture('v2_validation_clean')
 if not a.skip_build:build(run)
 execution=json.loads((run/'matrix_execution.json').read_text())
 check('all initial checks complete',not execution['partial'],[x for x in execution['checks'] if x['state']!='complete'])
 actual=results(run);expected=json.loads(a.expected.read_text())
 for src in ('datatable','mwtab'):
  ep=actual['analyses'][0]['endpoints'][src];baseline=expected['analyses'][0]['endpoints'][src]
  check('exact complete endpoint sections '+src,ep['sections']==baseline['sections'])
  check('exact roster '+src,ep['roster']==baseline['roster'])
 page=render(run).decode();check('complete page renders',len(page)>100000 and 'ST000001' in page)
 check('clean page has no execution-failure panel','merit-v2-execution-notes' not in page)
 for form in ({'cv_folds':'3'},{'redundancy_cutoff':'0.95'}):
  # Valid parameter names are checked directly by the runtime comparison below.
  render(run,form=form)
 # Independently compare protected function results against the same source without protection.
 comparison=run/'compare_interactive.py'
 comparison.write_text('''import json,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom live_render import setup\nui,p,live,out=setup(Path(sys.argv[2]))\nfrom native_resources import atomic\nargs={"study_id":"ST000001","params":{},"overrides":{}}\nfrom merit import labels_v9 as lb,correlation_v9 as cr\ncases=[("_low_information_study",args),("_redundancy_study",{**args,"params":{**cr.DEFAULT_PARAMS,"redundancy_cutoff":.95}}),("_ml_study",{**args,"params":{**lb.DEFAULT_PARAMS,"cv_folds":3}})]\natomic(sys.argv[3],{n:getattr(p,n)(**kw) for n,kw in cases})\n''')
 for mode in ('protected','direct'):
  env={**os.environ}
  if mode=='direct':env['MERIT_V2_METRIC_WORKER']='1'
  subprocess.run([sys.executable,str(comparison),str(RUNTIME),str(run),str(run/(mode+'.json'))],env=env,check=True,timeout=300)
 check('interactive function parity',json.loads((run/'protected.json').read_text())==json.loads((run/'direct.json').read_text()))
 fault=fixture('v2_validation_fault')
 if not a.skip_build:build(fault,{'MERIT_TEST_MODE':'1','MERIT_TEST_FAIL':'ST000001:AN000001:datatable:redundancy'})
 failures=json.loads((fault/'matrix_execution.json').read_text());v=results(fault)
 failed=[x for x in failures['checks'] if x['state']!='complete']
 check('one injected check fails',len(failed)==1,failed)
 check('failure identified by endpoint/check',failed[0]['endpoint']=='datatable' and failed[0]['stage']=='redundancy' and failed[0]['code']=='memory_budget')
 ep=v['analyses'][0]['endpoints']['datatable'];an=ep['sections']['analytical']
 check('failed check is absent, not zero','redundancy' not in an)
 check('subsequent independent checks retained','outliers' in an and 'annotation' in ep['sections'] and 'ml' in ep['sections'])
 check('other endpoint preserved exactly',v['analyses'][0]['endpoints']['mwtab']['sections']==actual['analyses'][0]['endpoints']['mwtab']['sections'])
 page=render(fault).decode();check('partial report renders and explains failure','merit-v2-execution-notes' in page and 'Not enough memory' in page)
 render(fault,action='feature-list',form={'metric':'redundancy'})
 sys.path.insert(0,str(RUNTIME));from native_resources import run_isolated
 timeout=run_isolated([sys.executable,'-c','import time;time.sleep(10)'],run/'timeout.log',timeout=.3)
 check('supervisor stops stuck check',timeout['code']=='timeout' and timeout['elapsed_seconds']<3)
finally:
 a.output.parent.mkdir(parents=True,exist_ok=True)
 a.output.write_text(json.dumps({'checks':checks,'passed':sum(x['passed'] for x in checks),'elapsed_seconds':time.time()-started},indent=2)+'\n')
