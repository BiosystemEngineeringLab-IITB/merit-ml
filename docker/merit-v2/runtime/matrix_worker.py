"""One disposable initial-calculation stage, scoped to one analysis x endpoint."""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--study',required=True);p.add_argument('--analysis');p.add_argument('--endpoint');p.add_argument('--stage',required=True)
    args=p.parse_args();run=args.run_root.resolve()
    import online_assess as oa
    from native_resources import atomic,constrain_child
    oa.dump=atomic
    oa.install_guard()
    import atexit
    atexit.register(lambda:atomic(run/'worker_audits'/f'{os.getpid()}.json',{
        'denied_attempts':oa.DENIED,'files_read':sorted(oa.READS)}))
    live=run/'materialized';out=run/'assessment';out.mkdir(exist_ok=True)
    if args.stage=='materialize':oa.materialize(run);return
    for key,value in {'MERIT_MW_DUMP_ROOT':live,'MERIT_V9_CACHE_ROOT':out}.items():os.environ[key]=str(value)
    for folder in (oa.BASE/'outputs/measurement_scale_20260930',oa.T4,oa.STRUCT,oa.CODE):sys.path.insert(0,str(folder))
    stage,_=oa.configure(live,out,run)
    from merit import low_information_v9 as li,separability_v9 as sp
    # Native version retains exact editable arrays for every matrix that can be calculated.
    li.STORE_ALL_CELLS=sys.maxsize;sp.FULL_LIMIT=sys.maxsize
    constrain_child()
    if os.environ.get('MERIT_TEST_MODE')=='1':
        selected=':'.join((args.study,args.analysis or '',args.endpoint or '',args.stage))
        if selected==os.environ.get('MERIT_TEST_FAIL'):
            raise MemoryError('Injected failure at a single matrix/check boundary')
        if selected==os.environ.get('MERIT_TEST_SLEEP'):
            import time;time.sleep(5)
    path=out/'json'/f'{args.study.lower()}_v9.json'
    if args.stage=='bootstrap':
        builder=stage('build_v9_cache');original=builder.matrix_path
        builder.matrix_path=lambda sid,aid,src:original(sid,aid,src) if src==args.endpoint else (None,None)
        oa.bootstrap(args.study,[args.analysis],live,out,stage)
    elif args.stage=='cross_endpoint':
        items=oa.load('structural_items',oa.STRUCT/'build_structural_items.py',out);items.ROOT=live
        value=json.loads(path.read_text())
        for a in value['analyses']:
            row={'study_id':args.study,'analysis_id':a['analysis_id'],
                 **{src:'valid' if 'roster' in ep else ep['availability'] for src,ep in a['endpoints'].items()}}
            for evidence in items.one(row):
                ep=a['endpoints'][evidence['source']]
                target=ep['sections']['structural']['evidence']
                if 'ids_agree_with_other_endpoints' in target:
                    target['ids_agree_with_other_endpoints']=evidence['ids_agree_with_other_endpoints']
        atomic(path,value)
    elif args.stage=='metadata':oa.metadata(args.study,live,out,stage)
    elif args.stage in ('missingness','scale'):
        missing,scale=oa.prepare_units_and_zeros({args.study:[args.analysis]},live,out,stage)
        # Pairing preparation uses both Tier 1 rosters. Only the target is then scored.
        v=json.loads(path.read_text())
        for a in v['analyses']:
            for src,ep in a['endpoints'].items():
                if (a['analysis_id'],src)!=(args.analysis,args.endpoint):ep.pop('roster',None)
        atomic(path,v);result=(missing if args.stage=='missingness' else scale).one(path)
        if isinstance(result,tuple) and isinstance(result[-1],list) and result[-1]:raise RuntimeError(result[-1])
    elif args.stage=='redundancy':oa.redundancy(path,out,stage)
    elif args.stage=='separability':oa.separability(path,out,stage)
    elif args.stage=='extra_evidence_and_readiness':oa.extra_evidence(path,live,out,stage,run)
    elif args.stage=='live_context_and_indexes':
        from live_indexes import build;build(run)
    else:
        names={'controls':'add_qc_blank_controls_from_rosters','low_information':'add_low_information_from_deposit',
               'outliers':'add_outliers_from_deposit','qc_reproducibility':'add_qc_reproducibility_from_deposit',
               'annotation':'add_annotation_from_deposit','refmet_lookup':'add_refmet_lookup_diagnostic','labels':'add_labels_from_rosters'}
        result=stage(names[args.stage]).one(path)
        if isinstance(result,tuple) and isinstance(result[-1],list) and result[-1]:raise RuntimeError(result[-1])
    atomic(run/'last_worker_audit.json',{'historical_result_or_deposit_reads_forbidden':True,'denied_attempts':oa.DENIED,
                                       'files_read':sorted(oa.READS)})


if __name__=='__main__':main()
