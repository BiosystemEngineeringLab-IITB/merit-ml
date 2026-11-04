"""Post-calculation comparison, never an input provider to the online builder.

Every file is inventoried. Literal bytes, decoded JSON and NumPy member payload
bytes are separate results. Missing/extra files and unavailable studies remain
in the coverage denominator. No numeric rounding or tolerance is applied.
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import shutil
import sys
import time
import zipfile
import urllib.parse
import urllib.request
from pathlib import Path
import numpy as np
from online_pull_common import atomic,digest

MISSING=object()
PRIMARY_PATHS={
    *('/structural/items/'+k for k in ('sample_support','samples_with_factor','numeric_values','unique_sample_ids')),
    '/analytical/sample_missingness/score','/analytical/measurement_scale/score',
    *('/analytical/'+k+'/default/score' for k in ('low_information','redundancy','outliers')),
    '/annotation/coverage/score','/annotation/refmet/score','/labels/class_support','/labels/cv_feasible','/ml/default/score',
    *('/metadata/metrics/'+k+'/score' for k in ('fair_study_metadata_compliance','fair_metabolite_identifier_resolvability',
      'external_cross_references_via_refmet','mass_rt_like_metadata_presence','lc_method_context_presence'))}


def canonical(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=True).encode()


def diff(a,b,p=''):
    if isinstance(a,dict) and isinstance(b,dict):
        for k in sorted(set(a)|set(b)):yield from diff(a.get(k,MISSING),b.get(k,MISSING),p+'/'+str(k).replace('~','~0').replace('/','~1'))
    elif isinstance(a,list) and isinstance(b,list):
        # Analyses are joined by ID, not accidentally paired after insertion.
        if a and b and all(isinstance(x,dict) and 'analysis_id' in x for x in a+b):
            aa={x['analysis_id']:x for x in a};bb={x['analysis_id']:x for x in b}
            if len(aa)==len(a) and len(bb)==len(b):yield from diff(aa,bb,p);return
        if len(a)!=len(b):yield p+'/length',len(a),len(b)
        for i in range(max(len(a),len(b))):yield from diff(a[i] if i<len(a) else MISSING,b[i] if i<len(b) else MISSING,p+'/'+str(i))
    elif a is MISSING or b is MISSING or canonical(a)!=canonical(b):
        yield p,{'missing':True} if a is MISSING else a,{'missing':True} if b is MISSING else b


def leaves(x,p=''):
    if isinstance(x,dict):
        for k,v in x.items():yield from leaves(v,p+'/'+str(k).replace('~','~0').replace('/','~1'))
    elif isinstance(x,list):
        for i,v in enumerate(x):yield from leaves(v,p+'/'+str(i))
    else:yield p,x


def metric_values(study):
    """Every scalar in every scored/informational section, joined by AN/source."""
    result={}
    for p,v in leaves(study.get('study_sections',{})):result[('study','study',p)]=v
    for a in study.get('analyses',[]):
        aid=a['analysis_id']
        for p,v in leaves(a.get('sections',{})):result[(aid,'analysis',p)]=v
        for src,e in a.get('endpoints',{}).items():
            for p,v in leaves(e.get('sections',{})):result[(aid,src,p)]=v
    return result


def record_metrics(x,y,mw,scores,sections,study_rows):
    sid=x.get('study_id') or y.get('study_id');left,right=metric_values(x),metric_values(y)
    for key in sorted(set(left)|set(right)):
        xx,yy=left.get(key,MISSING),right.get(key,MISSING)
        exact=xx is not MISSING and yy is not MISSING and canonical(xx)==canonical(yy)
        kind='number' if (isinstance(xx,(int,float)) and not isinstance(xx,bool)) or (isinstance(yy,(int,float)) and not isinstance(yy,bool)) else 'other'
        scores['all']+=1;scores['exact']+=exact;scores[kind]+=1;scores[kind+'_exact']+=exact
        if key[2] in PRIMARY_PATHS:
            scores['primary_item_checks']+=1;scores['primary_item_exact']+=exact
            if kind=='number':scores['primary_numeric_checks']+=1;scores['primary_numeric_exact']+=exact
        area=key[2].split('/')[1] if '/' in key[2] else key[2];t=sections.setdefault((key[1],area),collections.Counter());t['checks']+=1;t['exact']+=exact
        mw.writerow([sid,*key,exact,canonical({'missing':True} if xx is MISSING else xx).decode(),canonical({'missing':True} if yy is MISSING else yy).decode()])
    oa={r['analysis_id']:r for r in x.get('analyses',[])};na={r['analysis_id']:r for r in y.get('analyses',[])}
    for aid in sorted(set(oa)|set(na)):
        oe=oa.get(aid,{}).get('endpoints',{});ne=na.get(aid,{}).get('endpoints',{})
        for ep in sorted(set(oe)|set(ne)):
            le,re=oe.get(ep,{}),ne.get(ep,{})
            study_rows.append({'study_id':sid,'analysis_id':aid,'endpoint':ep,
                'baseline_valid':'roster' in le,'online_valid':'roster' in re,
                'sample_positions_identical':canonical(le.get('roster'))==canonical(re.get('roster')),
                'feature_count_identical':le.get('n_features')==re.get('n_features'),
                'complete_endpoint_json_identical':canonical(le)==canonical(re)})


def stream_hash(f):
    h=hashlib.sha256();size=0
    while True:
        b=f.read(4*1024*1024)
        if not b:break
        h.update(b);size+=len(b)
    return h.hexdigest(),size


def npy_info(f):
    version=np.lib.format.read_magic(f)
    shape,order,dtype=np.lib.format._read_array_header(f,version)
    sha,size=stream_hash(f)
    return {'shape':list(shape),'fortran_order':order,'dtype':str(dtype.descr if dtype.fields else dtype.str),
            'payload_sha256':sha,'payload_bytes':size,'contains_objects':bool(dtype.hasobject)}


def arrays(path):
    if path.suffix=='.npy':
        with path.open('rb') as f:return {'array':npy_info(f)}
    result={}
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            with z.open(name) as f:
                if name.endswith('.npy'):result[name]=npy_info(f)
                else:
                    sha,size=stream_hash(f);result[name]={'payload_sha256':sha,'payload_bytes':size}
    return result


def json_value(path):
    if path.name.endswith('.json.gz'):
        with gzip.open(path,'rt') as f:return json.load(f)
    return json.loads(path.read_text())


def snapshot_reference(source,dest,notify):
    source=Path(source);dest=Path(dest);files=sorted(p for p in source.rglob('*') if p.is_file())
    needed=sum(p.stat().st_size for p in files)+25*1024**3
    while shutil.disk_usage(dest.parent).free<needed:
        notify('waiting_for_disk',f'Comparison snapshot needs {needed/1024**3:.1f} GiB free, including the 25 GiB reserve; no data deleted')
        time.sleep(10)
    rows=[]
    for i,p in enumerate(files,1):
        rel=p.relative_to(source);target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True)
        for attempt in range(3):
            before=p.stat();shutil.copy2(p,target);after=p.stat()
            if (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns):break
        else:raise RuntimeError('Reference changed repeatedly while snapshotting: '+str(p))
        rows.append({'file':str(rel),'bytes':target.stat().st_size,'sha256':digest(target),
                     'reference_mtime_ns':after.st_mtime_ns})
        if i%25==0 or i==len(files):notify('snapshotting_v9',f'{i:,} / {len(files):,} reference files snapshotted')
    return rows


def served_cache_root(fallback):
    """Verify only the cache-path variable of the actual 8782 process."""
    found={}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            command=(p/'cmdline').read_bytes()
            if b'flask' not in command or b'8782' not in command:continue
            for entry in (p/'environ').read_bytes().split(b'\0'):
                if entry.startswith(b'MERIT_V9_CACHE_ROOT='):
                    path=str(Path(entry.split(b'=',1)[1].decode()).resolve());found.setdefault(path,[]).append(int(p.name))
        except (OSError,PermissionError):continue
    if len(found)>1:raise RuntimeError('Multiple distinct 8782 reference-cache paths found; refusing an ambiguous reference')
    path=next(iter(found),str(fallback))
    return path,{'resolved_cache_root':path,'matching_pids':found.get(path,[]),
                 'verified_from_process_environment':bool(found),'configured_fallback':str(fallback)}


def compare_served_ui(cohort,config,notify):
    """Read the actual default report served by 8782, after calculation only."""
    from online_pull_capture import UI_KEYS
    out=cohort/'online_pull_comparison';exports=cohort/'online_pull_8782_ui_exports';exports.mkdir(exist_ok=True)
    counts=collections.Counter();errors=[]
    with (out/'online_pull_served_ui_block_overlap.tsv').open('w') as fh,gzip.open(out/'online_pull_served_ui_field_differences.jsonl.gz','wt') as dh:
        w=csv.writer(fh,delimiter='\t');w.writerow(['study_id','block','baseline_present','online_present','exact_decoded_json'])
        for i,sid in enumerate(config['study_ids'],1):
            p=exports/f'{sid.lower()}_ui_state.json.gz';new=cohort/'online_pull_ui_exports'/p.name
            try:
                if not p.exists():
                    req=urllib.request.Request(config['baseline_ui_url']+'/workflow/run',data=urllib.parse.urlencode({'study_id':sid}).encode())
                    with urllib.request.urlopen(req,timeout=600) as r:html=r.read().decode()
                    value=json.JSONDecoder().raw_decode(html.split('window.__MERIT_STATE_JSON = ',1)[1])[0]
                    with p.open('wb') as raw:
                        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as f:f.write(canonical(value))
                old=json_value(p);live=json_value(new) if new.exists() else {}
                for k in UI_KEYS:
                    exact=k in old and k in live and canonical(old[k])==canonical(live[k]);counts['checks']+=1;counts['exact']+=exact
                    counts['missing_online']+=k not in live;counts['missing_baseline']+=k not in old
                    w.writerow([sid,k,k in old,k in live,exact])
                for pointer,left,right in diff(old,live):dh.write(json.dumps({'study_id':sid,'pointer':pointer,'baseline':left,'online':right},ensure_ascii=False)+'\n')
            except Exception as exc:
                errors.append({'study_id':sid,'error':repr(exc)});w.writerow([sid,'<report-error>',False,new.exists(),False])
            notify('comparing_served_ui',f'{i:,} / {len(config["study_ids"]):,} actual 8782 default reports checked')
    atomic(out/'online_pull_served_ui_errors.json',errors)
    return {**dict(counts),'report_errors':len(errors),'error_details':'online_pull_served_ui_errors.json',
            'expected_checks':len(config['study_ids'])*len(UI_KEYS),
            'exact_block_overlap_pct':100*counts['exact']/(len(config['study_ids'])*len(UI_KEYS)) if config['study_ids'] else None}


def compare(cohort,notify=None):
    cohort=Path(cohort);notify=notify or (lambda phase,detail:print(phase,detail,flush=True))
    config=json.loads((cohort/'online_pull_config.json').read_text())
    barrier=json.loads((cohort/'online_pull_calculation_finished.json').read_text())
    if set(barrier['studies_finished'])!=set(config['study_ids']):raise ValueError('Comparison cannot begin before every cohort study has finished its calculation attempts')
    out=cohort/'online_pull_comparison';out.mkdir(exist_ok=True)
    baseline=cohort/'online_pull_8782_v9_reference_snapshot'
    inventory_file=out/'online_pull_reference_inventory.json'
    if not inventory_file.exists():
        if config.get('baseline_ui_url'):
            config['baseline_v9'],resolution=served_cache_root(config['baseline_v9'])
            atomic(out/'online_pull_reference_source_resolution.json',resolution)
        rows=snapshot_reference(config['baseline_v9'],baseline,notify)
        atomic(inventory_file,{'source':config['baseline_v9'],'captured_after_calculation':True,'files':rows})
    else:config['baseline_v9']=json.loads(inventory_file.read_text())['source']
    old={row['file']:baseline/row['file'] for row in json.loads(inventory_file.read_text())['files']}
    cache=cohort/'online_pull_v9_compatible_cache';new={str(p.relative_to(cache)):p for p in cache.rglob('*') if p.is_file()}
    paths=sorted(set(old)|set(new));counts=collections.Counter();scores=collections.Counter();arrays_count=collections.Counter();sections={}
    file_path=out/'online_pull_file_byte_overlap.tsv';diff_path=out/'online_pull_every_field_difference.jsonl.gz'
    metric_path=out/'online_pull_all_metric_scalar_overlap.tsv.gz';array_path=out/'online_pull_numpy_payload_byte_overlap.tsv.gz'
    study_path=out/'online_pull_study_matrix_overlap.tsv';study_rows=[]
    with file_path.open('w') as fh,gzip.open(diff_path,'wt') as dh,gzip.open(metric_path,'wt') as mh,gzip.open(array_path,'wt') as ah:
        fw=csv.DictWriter(fh,fieldnames=['file','status','literal_bytes_equal','decoded_equal','baseline_sha256','online_sha256','error'],delimiter='\t');fw.writeheader()
        mw=csv.writer(mh,delimiter='\t');mw.writerow(['study_id','analysis_id','endpoint','metric_pointer','exact','baseline_json','online_json'])
        aw=csv.writer(ah,delimiter='\t');aw.writerow(['file','array','exact_payload','baseline_json','online_json'])
        for i,rel in enumerate(paths,1):
            a,b=old.get(rel),new.get(rel);record={'file':rel,'status':'both' if a and b else 'missing_online' if a else 'online_only',
                'literal_bytes_equal':False,'decoded_equal':'','baseline_sha256':digest(a) if a else '',
                'online_sha256':digest(b) if b else '','error':''}
            counts[record['status']]+=1
            if a and b:
                record['literal_bytes_equal']=record['baseline_sha256']==record['online_sha256'];counts['literal_equal']+=record['literal_bytes_equal']
                try:
                    if rel.endswith(('.json','.json.gz')):
                        x,y=json_value(a),json_value(b);record['decoded_equal']=canonical(x)==canonical(y);counts['json_comparable']+=1;counts['json_exact']+=record['decoded_equal']
                        if not record['decoded_equal']:
                            for pointer,left,right in diff(x,y):
                                dh.write(json.dumps({'file':rel,'pointer':pointer,'baseline':left,'online':right},ensure_ascii=False)+'\n');counts['different_fields']+=1
                    elif rel.endswith(('.npz','.npy')):
                        x,y=arrays(a),arrays(b)
                        for name in sorted(set(x)|set(y)):
                            exact=name in x and name in y and canonical(x[name])==canonical(y[name]);arrays_count['checks']+=1;arrays_count['exact']+=exact
                            aw.writerow([rel,name,exact,json.dumps(x.get(name)),json.dumps(y.get(name))])
                    # Text/CSV/TSV and all other formats retain literal byte
                    # checks, never position-based inferred correspondence.
                except Exception as exc:record['error']=repr(exc);counts['comparison_errors']+=1
            if rel.startswith('json/') and rel.endswith('_v9.json'):
                try:record_metrics(json_value(a) if a else {},json_value(b) if b else {},mw,scores,sections,study_rows)
                except Exception as exc:record['error']=str(exc);counts['comparison_errors']+=1
            if not (a and b) and rel.endswith(('.npz','.npy')):
                try:
                    x=arrays(a) if a else {};y=arrays(b) if b else {}
                    for name in sorted(set(x)|set(y)):
                        arrays_count['checks']+=1
                        aw.writerow([rel,name,False,json.dumps(x.get(name)),json.dumps(y.get(name))])
                except Exception as exc:record['error']=str(exc);counts['comparison_errors']+=1
            fw.writerow(record)
            if i%25==0 or i==len(paths):notify('comparing_v9',f'{i:,} / {len(paths):,} files compared')
    with study_path.open('w') as f:
        fields=list(study_rows[0]) if study_rows else ['study_id','analysis_id','endpoint'];w=csv.DictWriter(f,fieldnames=fields,delimiter='\t');w.writeheader();w.writerows(study_rows)
    study_files={sid:(baseline/'json'/f'{sid.lower()}_v9.json',cache/'json'/f'{sid.lower()}_v9.json') for sid in config['study_ids']}
    common_studies=sum(a.exists() and b.exists() for a,b in study_files.values());equal_studies=sum(a.exists() and b.exists() and digest(a)==digest(b) for a,b in study_files.values())
    percentage=lambda n,d:100*n/d if d else None
    ui=compare_served_ui(cohort,config,notify) if config.get('compare_served_ui',True) else {'not_requested':True}
    summary={'compared_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'cohort_studies':len(config['study_ids']),
        'studies_with_both_records':common_studies,'literal_study_files_equal':equal_studies,
        'literal_study_file_overlap_pct_of_all_studies':percentage(equal_studies,len(study_files)),
        'file_counts':dict(counts),'literal_file_overlap_pct_shared_files':percentage(counts['literal_equal'],counts['both']),
        'literal_file_overlap_pct_union':percentage(counts['literal_equal'],len(paths)),
        'decoded_json_overlap_pct':percentage(counts['json_exact'],counts['json_comparable']),
        'every_section_scalar':dict(scores),'exact_numeric_scalar_overlap_pct':percentage(scores['number_exact'],scores['number']),
        'primary_scored_item_overlap_pct':percentage(scores['primary_numeric_exact'],scores['primary_numeric_checks']),
        'actual_8782_served_ui':ui,
        'numpy_payloads':dict(arrays_count),'numpy_payload_overlap_pct':percentage(arrays_count['exact'],arrays_count['checks']),
        'by_section':[{'endpoint':k[0],'section':k[1],**dict(v)} for k,v in sorted(sections.items())],
        'calculation_failures':barrier['failed'],'baseline_source':config['baseline_v9'],
        'provenance_not_filtered_out':True,'tolerance':0,'all_compression_bytes_and_payload_bytes_reported_separately':True,
        'notes':['All files and fields are retained in the comparison, including paths, dates, provenance and missing values.',
                 'A raw compressed-file mismatch can coexist with identical decoded array payloads or JSON.',
                 'Array payload equality is positional storage equality; it does not infer changed biological measurements when labels or ordering differ.',
                 'Fresh input/reference changes and the unavailable independent model reading can prevent literal historical identity.']}
    atomic(out/'online_pull_overlap_summary.json',summary)
    text=f'''# Online pull versus the v9 cache feeding 8782

Studies: {len(study_files)}. Comparable study records: {common_studies}. Byte-identical study records: {equal_studies}.

| Comparison | Exact / compared | Overlap (%) |
|---|---:|---:|
| Literal study JSON, all cohort studies | {equal_studies} / {len(study_files)} | {summary['literal_study_file_overlap_pct_of_all_studies']} |
| Literal files, shared paths | {counts['literal_equal']} / {counts['both']} | {summary['literal_file_overlap_pct_shared_files']} |
| Decoded JSON, no fields excluded | {counts['json_exact']} / {counts['json_comparable']} | {summary['decoded_json_overlap_pct']} |
| Numeric scalars, every metric/informational section | {scores['number_exact']} / {scores['number']} | {summary['exact_numeric_scalar_overlap_pct']} |
| Primary numeric scored items | {scores['primary_numeric_exact']} / {scores['primary_numeric_checks']} | {summary['primary_scored_item_overlap_pct']} |
| NumPy member payload bytes, shape/dtype/order included | {arrays_count['exact']} / {arrays_count['checks']} | {summary['numpy_payload_overlap_pct']} |

Actual default UI block overlap from the reports served by 8782: {ui}. These reports were requested only after cohort computation finished.

Failed studies remain in the all-study denominator: {len(barrier['failed'])}. Missing and additional files are listed, not dropped. All field differences are in online_pull_every_field_difference.jsonl.gz. Paths, timestamps and provenance were not silently normalized away. See online_pull_overlap_summary.json for section-level counts and caveats.
'''
    (out/'ONLINE_PULL_OVERLAP_REPORT.md').write_text(text)
    return summary


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run-root',required=True,type=Path);args=ap.parse_args();print(json.dumps(compare(args.run_root),indent=2))
