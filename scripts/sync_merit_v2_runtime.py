"""Capture verified runtime source and global references, never run data or credentials."""
import argparse,hashlib,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'docker/merit-v2/runtime'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sync(source):
 source=Path(source).resolve();sys.path.insert(0,str(source))
 from release_safety import RUNTIME_FILES,assert_safe,scan_tree
 DEST.mkdir(parents=True,exist_ok=True)
 manifest=json.loads((source/'source_snapshot_manifest.json').read_text())
 files=[source/r['bundled'] for r in manifest['files']]+[source/x for x in RUNTIME_FILES]
 refs=('refmet_db.csv','refmet_all_ids_rest_20260929.json','reference_policy.json','original_cohort_4121_study_ids.txt','study_reference_manifest.json')
 files += [source/'assets/reference'/x for x in refs]+[source/'assets'/x for x in ('alert_reference_R1.json','home.html','landing.html')]
 assert_safe(files,root=source.parent)
 for src in files:
  rel=src.relative_to(source)
  if rel.is_absolute() or '..' in rel.parts:raise ValueError(rel)
  dst=DEST/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
 (DEST/'source_snapshot_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 proof={'release':'MERIT v2 Docker','release_version':'v2-live-20261112-r15','release_date':'2026-11-12',
  'source':'8791 live runtime; calculation sources shared with 8782 and browser',
  'study_inputs_included':False,'study_results_included':False,
  'reference_policy':'Same chemical, R1, curated-rule and contextual-reference policy as 8791/browser. Per-study context references are downloaded on demand and checked against the bundled SHA-256 index; quantitative matrices and metric results are always calculated from fresh MW inputs.',
  'files':{str(p.relative_to(source)):sha(p) for p in files}}
 proof['credential_scan']=scan_tree(DEST,server_root=source.parent)
 (DEST/'runtime_capture.json').write_text(json.dumps(proof,indent=2)+'\n')
 print('Captured',len(files),'verified code/rule/reference files; no run data or credentials')
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--source',required=True);sync(a.parse_args().source)
