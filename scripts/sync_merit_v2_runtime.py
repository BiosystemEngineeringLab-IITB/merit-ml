"""Capture an explicit code-only native live runtime; never copy run data or credentials."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'docker/merit-v2/runtime'
FILES=('online_assess.py','fetch_online.py','fast_correlation.py','live_app.py','live_render.py',
       'live_indexes.py','live_validity.py','live_catalog.py','live_inventory.py','live_design_context.py',
       'mwtab_input.py','live_bulk.py','live_references.py','online_evidence.py','live_study_summary.py',
       'live_progress.py','online_pull_common.py','online_pull_dashboard.py','online_pull_capture.py',
       'online_pull_cohort.py','online_pull_compare.py','online_pull_progress.py','relocate_bundle.py')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def sync(source):
    source=Path(source).resolve();DEST.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((source/'source_snapshot_manifest.json').read_text())
    for r in manifest['files']:
        rel=Path(r['bundled'])
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='source_snapshot':raise ValueError(rel)
        src=source/rel;dst=DEST/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        r['bundled_sha256']=sha(dst)
    # Relocation remains a build-time operation; preserve the captured source root.
    (DEST/'source_snapshot_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for name in FILES:shutil.copy2(source/name,DEST/name)
    for name in ('alert_reference_R1.json','home.html'):
        target=DEST/'assets'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/'assets'/name,target)
    refs=DEST/'assets/reference';refs.mkdir(parents=True,exist_ok=True)
    policy=json.loads((source/'assets/reference/reference_policy.json').read_text())
    names=('refmet_db.csv','refmet_all_ids_rest_20260929.json')
    policy['files']={k:v for k,v in policy['files'].items() if k in names or k=='code_distributed_names_and_flags'}
    policy['scope']='Pinned global chemical reference assets only. Study inputs, reviewed study labels and Pan-ReDU study snapshots are not bundled.'
    for name in names:shutil.copy2(source/'assets/reference'/name,refs/name)
    (refs/'reference_policy.json').write_text(json.dumps(policy,indent=2)+'\n')
    # No study-specific reference snapshots: request Pan-ReDU live for every study.
    (refs/'original_cohort_4121_study_ids.txt').write_text('')
    p=DEST/'live_render.py';s=p.read_text()
    anchor='    return ui,pilot,live,out\n'
    if s.count(anchor)!=1:raise RuntimeError('Renderer setup changed; review the v2 integration hook')
    s=s.replace(anchor,'    from native_guard import install\n    install(ui,pilot,run,live,out)\n'+anchor)
    p.write_text(s)
    p=DEST/'online_assess.py';s=p.read_text()
    s=s.replace("pct=ep['sections']['annotation']['coverage']['score'] if valid else None", "pct=ep.get('sections',{}).get('annotation',{}).get('coverage',{}).get('score') if valid else None")
    s=s.replace("sec=ep['sections'];an=sec['analytical']", "sec=ep['sections'];an=sec.get('analytical',{})")
    s=s.replace("an['sample_missingness']['score']", "an.get('sample_missingness',{}).get('score')")
    s=s.replace("an[k]['default']['score']", "an.get(k,{}).get('default',{}).get('score')")
    s=s.replace("u=an['measurement_scale'];items['units']={'value':u['score'],'flag':u['units']['declared']}", "u=an.get('measurement_scale',{});items['units']={'value':u.get('score'),'flag':u.get('units',{}).get('declared')}")
    s=s.replace("rd.task_result(sec['labels'],sec['ml']['default'],5)", "rd.task_result(sec.get('labels',{}),sec.get('ml',{}).get('default',{}),5)")
    s=s.replace("except Exception:\n                pos, nf = [], 0", "except MemoryError:\n                raise\n            except Exception:\n                pos, nf = [], 0")
    p.write_text(s)
    p=DEST/'online_evidence.py';s=p.read_text()
    s=s.replace("if not keep_flagged_index:analytical['low_information']['default'].pop('flagged_index',None)", "if not keep_flagged_index:analytical.get('low_information',{}).get('default',{}).pop('flagged_index',None)")
    s=s.replace("analytical['sample_missingness']['items'].get('invalid')", "analytical.get('sample_missingness',{}).get('items',{}).get('invalid')")
    p.write_text(s)
    # Incomplete evidence must remain incomplete, rather than fail the entire shared-metadata stage.
    p=DEST/'online_evidence.py';s=p.read_text()
    s=s.replace("            result=endpoint['sections']['annotation']", "            if not endpoint.get('sections',{}).get('annotation'): continue\n            result=endpoint['sections']['annotation']")
    p.write_text(s)
    files=[DEST/r['bundled'] for r in manifest['files']]+[DEST/x for x in FILES]+[DEST/'assets'/x for x in ('alert_reference_R1.json','home.html')]+list(refs.iterdir())
    proof={'release':'MERIT v2 Docker','release_date':'2026-11-05','source':'8791 native live runtime',
           'study_inputs_included':False,'study_results_included':False,
           'files':{str(p.relative_to(DEST)):sha(p) for p in files}}
    (DEST/'runtime_capture.json').write_text(json.dumps(proof,indent=2)+'\n')
    print('Captured',len(files),'code/rule/asset files in',DEST)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--source',required=True);sync(a.parse_args().source)
