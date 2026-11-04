"""Check protected/native agreement for selected classes and sample edits on freshly calculated inputs."""
import argparse,json,os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];runtime=root/'docker/merit-v2/runtime'
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);args=p.parse_args();run=args.run.resolve()
program=run/'edit_comparison.py'
program.write_text('''import json,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom live_render import setup\nui,p,live,out=setup(Path(sys.argv[2]))\nfrom merit import labels_v9 as lb\nfrom native_resources import atomic\nraw=p._raw_study("ST000001");items=lb.factor_items(raw)\nfactor=next(k for k,v in items["factors"].items() if len(v)>1)\nchoice={"factors":[factor],"classes":[[v] for v in items["factors"][factor]]}\nrow=raw["analyses"][0]["endpoints"]["mwtab"]["roster"][0]\nedits={row[0]:{"excluded":True}}\ncases={"selected_classes":("_ml_study",{"params":{"label_classes":json.dumps(choice),"cv_folds":3}}),"eligible_edit_ml":("_ml_study",{"overrides":edits}),"eligible_edit_redundancy":("_redundancy_study",{"overrides":edits}),"eligible_edit_missingness":("_missingness_study",{"overrides":edits})}\natomic(sys.argv[3],{case:getattr(p,name)(study_id="ST000001",**kw) for case,(name,kw) in cases.items()})\n''')
for mode in ('protected','direct'):
 env={**os.environ}
 if mode=='direct':env['MERIT_V2_METRIC_WORKER']='1'
 subprocess.run([sys.executable,str(program),str(runtime),str(run),str(run/f'edit_{mode}.json')],env=env,check=True,timeout=300)
a=json.loads((run/'edit_protected.json').read_text());b=json.loads((run/'edit_direct.json').read_text())
checks={name:a[name]==b[name] for name in a}
output=root/'release-artifacts/edit_validation.json';output.write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks));assert all(checks.values())
