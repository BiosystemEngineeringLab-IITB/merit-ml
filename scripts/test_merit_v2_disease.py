"""Regression for deposited disease declarations, including flat MW endpoint records.

Run with the release Python dependencies. Optionally check a completed assessment:
  python test_merit_v2_disease.py --runtime /app --run /app/runs/<job>
"""
import argparse,base64,json,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,default=Path(__file__).resolve().parents[1]/'docker/merit-v2/runtime');p.add_argument('--run',type=Path);a=p.parse_args()
sys.path.insert(0,str(a.runtime));import online_assess as oa
sys.path.insert(0,str(oa.CODE));from live_disease import declaration
cases=[({'Disease':'Cancer'},'Cancer'),({'disease':'Cancer'},'Cancer'),
 ({'1':{'Study ID':'ST000010','Disease':'Cancer'}},'Cancer'),
 ([{'Study ID':'ST000010','Disease':'Cancer'},{'Study ID':'ST000011','Disease':'Diabetes'}],'Cancer'),
 ([{'Disease':'Cancer'},{'Disease':'Cancer'},{'Disease':'Obesity'}],'Cancer; Obesity'),
 ({'Disease':'None'},''),({'Disease':'NA'},''),
 ({'Study ID':'ST000011','Disease':'Diabetes'},''),({},''),([],'')]
for raw,expected in cases:assert declaration(raw,'ST000010')==expected,(raw,expected)
if a.run:
 for path in (a.run/'assessment/ui_summaries').glob('*.json'):
  sid=path.stem.upper();from live_disease import read
  expected=read(a.run/'materialized',sid)
  summaries=json.loads(path.read_text());assert all(v['disease']==expected for v in summaries.values())
  payload={'run':str(a.run),'study_id':sid,'action':'page'}
  child=subprocess.run([sys.executable,str(a.runtime/'live_render.py')],input=json.dumps(payload),capture_output=True,text=True,check=True,timeout=180)
  result=json.loads(child.stdout);assert result['ok'],result
  html=base64.b64decode(result['data_b64']).decode()
  if expected:assert 'disease context:' in html and expected in html and 'no study-level disease metadata' not in html
print(json.dumps({'parser_cases_passed':len(cases),'completed_run_checked':bool(a.run)}))
