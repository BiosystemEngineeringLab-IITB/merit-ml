"""Live MW study discovery, independent of historical assessment results.

The current UI's exact search, alias, facet and pagination functions are
compiled from its pinned Python source. Only their pure dependencies are
loaded; the original UI's cache readers and metric imports are not executed.
Unassessed documentation fields stay unknown rather than becoming absent.
"""
from __future__ import annotations
import ast
import collections
import datetime as dt
import functools
import hashlib
import html
import json
import re
import threading
import uuid
import concurrent.futures
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin
import __future__

from fetch_online import Fetcher,API,records

HOME=Path(__file__).resolve().parent


def normalize(payload):
    result={}
    for r in records(payload):
        sid=str(r.get('study_id') or '').upper()
        if not re.fullmatch(r'ST\d{6}',sid):raise ValueError('Catalog row lacks a valid study ID')
        row=result.setdefault(sid,{'study_id':sid,'title':r.get('study_title') or '',
            'organism':r.get('species') or r.get('subject_species') or '',
            'institute':r.get('institute') or '', 'disease':r.get('disease') or '',
            'platform':'','platform_raw':'','analysis_type':'','analysis_type_raw':'','analysis_types':[],
            'ion_modes':[],'chromatography_types':[],'instruments':[],'sample_types':[],
            'project_type':'','score':None,'band':'','band_label':'',
            'mass_rt_like_metadata_present':None,'mass_rt_like_metadata_score':None,'mzrt_metadata_status':'not assessed'})
        for t in re.split(r'[;,]+',str(r.get('analysis_type') or '')):
            if t.strip() and t.strip() not in row['analysis_types']:row['analysis_types'].append(t.strip())
        row['analysis_type']=row['analysis_type_raw']='; '.join(row['analysis_types'])
    if not result:raise ValueError('Empty study catalog is not treated as a successful refresh')
    return list(result.values())


@functools.lru_cache(maxsize=1)
def search_rules():
    path=HOME/'source_snapshot/merit-ui-v9-analysis-endpoint-pilot/v8_layout/merit/ui.py'
    tree=ast.parse(path.read_text());functions={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    constants={}
    for node in tree.body:
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target,ast.Name):
                    try:constants[target.id]=ast.literal_eval(node.value)
                    except (ValueError,TypeError):pass
    env={'re':re,'html':html,'json':json,'Any':Any,'Path':Path,'Counter':collections.Counter,
         'quote':quote,'urljoin':urljoin,'_is_embargoed_study':lambda sid:False}
    selected={};todo=['_study_browser_data_payload']
    while todo:
        name=todo.pop()
        if name in env or name in selected:continue
        if name in constants:env[name]=constants[name];continue
        if name not in functions:continue
        node=functions[name];selected[name]=node
        todo.extend(n.id for n in ast.walk(node) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load))
    # Inject actual rows; do not compile or execute the historical file loader.
    selected.pop('_load_study_browser_data',None)
    # The historical loader's normalization helpers are reused for fresh rows.
    for name in ('_normalize_analysis_type_label','_normalize_platform_label'):
        if name in functions and name not in selected:selected[name]=functions[name]
    env.update(constants)
    utils_path=path.parent/'utils.py'
    normalizer=next(n for n in ast.parse(utils_path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='normalize_label')
    exec(compile(ast.Module(body=[normalizer],type_ignores=[]),str(utils_path),'exec',flags=__future__.annotations.compiler_flag),env)
    exec(compile(ast.Module(body=list(selected.values()),type_ignores=[]),str(path),'exec',flags=__future__.annotations.compiler_flag),env)
    return env


def payload(rows,query=None):
    env=dict(search_rules())
    # Functions retain their definition globals; serialize this small pure
    # call to keep concurrent request row sets from being mixed.
    with _SEARCH_LOCK:
        original=search_rules();original['_load_study_browser_data']=lambda root:rows
        return original['_study_browser_data_payload']('',query or {})


_SEARCH_LOCK=threading.Lock()


class Catalog:
    def __init__(self,root=None,resume=False):
        self.root=Path(root or HOME/'catalog');self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.refreshing=False;self.error=None
        self.rows=[];self.provenance={}
        latest=self.root/'latest.json'
        if resume and latest.exists():
            value=json.loads(latest.read_text());self.rows=value['rows'];self.provenance=value['provenance']

    def adopt(self,pull):
        pull=Path(pull);raw=pull/'inputs/all_study_summaries.json'
        rows=normalize(json.loads(raw.read_text()));manifest=json.loads((pull/'inputs/fetch_manifest.json').read_text())
        entry=next(r for r in reversed(manifest['requests']) if r['file']=='all_study_summaries.json')
        if entry.get('error') or hashlib.sha256(raw.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('Catalog provenance verification failed')
        value={'rows':rows,'provenance':{'url':entry['final_url'],'sha256':entry['sha256'],'fetched_utc':entry['finished_utc'],
                                      'raw_file':str(raw.resolve()),'role':'live repository discovery metadata, no metric scores'}}
        tmp=self.root/'latest.tmp';tmp.write_text(json.dumps(value));tmp.replace(self.root/'latest.json')
        with self.lock:self.rows=rows;self.provenance=value['provenance'];self.error=None

    def refresh(self):
        with self.lock:
            if self.refreshing:return
            self.refreshing=True
        try:
            stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]
            pull=self.root/'pulls'/stamp;f=Fetcher(pull/'inputs')
            # The documented all-study request can time out at repository scale.
            # These disjoint prefix ranges cover every six-digit accession.
            ranges=['ST00'+f'{i:02d}' for i in range(100)]+['ST'+f'{i:02d}' for i in range(1,100)]
            found={};completed=[];errors=[];pending=list(ranges)
            started=dt.datetime.now(dt.timezone.utc).isoformat()
            def one(prefix):
                raw=f.get(API+f'/study/study_id/{prefix}/summary',f'{prefix}.json',timeout=30,attempts=1)
                if raw is None:return prefix,None,'transport failure'
                try:
                    value=json.loads(raw) if raw.strip() else []
                    if value in (None,[],{}):return prefix,[],None
                    rows=normalize(value)
                    if any(not r['study_id'].startswith(prefix) for r in rows):raise ValueError('Prefix identity mismatch')
                    return prefix,rows,None
                except Exception as exc:return prefix,None,str(exc)
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                while pending:
                    batch=pending;pending=[]
                    for prefix,rows,error in pool.map(one,batch):
                        if error:
                            if len(prefix)<8:pending.extend(prefix+str(i) for i in range(10))
                            else:errors.append({'prefix':prefix,'error':error})
                        else:
                            completed.append(prefix)
                            for row in rows:found[row['study_id']]=row
                        with self.lock:
                            self.rows=list(found.values())
                            self.provenance={'role':'fresh MW discovery metadata; no assessment results',
                                'ranges_complete':len(completed),'ranges_failed':len(errors),'studies':len(found),
                                'coverage_complete':False,'started_utc':started}
            f.save()
            provenance={**self.provenance,'coverage_complete':not errors,'finished_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                        'url_template':API+'/study/study_id/<prefix>/summary','requests':len(f.entries),
                        'fetch_manifest_file':str((pull/'inputs/fetch_manifest.json').resolve()),
                        'coverage':'all public six-digit ST identifiers','errors':errors}
            if not found:raise ValueError('No live catalog rows returned')
            value={'rows':list(found.values()),'provenance':provenance}
            tmp=self.root/'latest.tmp';tmp.write_text(json.dumps(value));tmp.replace(self.root/'latest.json')
            (pull/'coverage.json').write_text(json.dumps({'completed_ranges':completed,'errors':errors},indent=2))
            with self.lock:self.rows=value['rows'];self.provenance=provenance
            if errors:raise RuntimeError(f'{len(errors)} catalog accession ranges could not be checked')
        except Exception as exc:
            with self.lock:self.error=str(exc)
        finally:
            with self.lock:self.refreshing=False

    def start(self):
        thread=threading.Thread(target=self.refresh,name='mw-live-catalog',daemon=True);thread.start();return thread

    def status(self):
        with self.lock:return {'studies':len(self.rows),'refreshing':self.refreshing,'error':self.error,'provenance':dict(self.provenance)}

    def merged(self,jobs):
        with self.lock:rows={r['study_id']:dict(r) for r in self.rows}
        with jobs.lock:
            current=[(sid,dict(jobs.jobs[key])) for sid,key in jobs.current.items() if key in jobs.jobs and jobs.jobs[key]['state']=='complete']
        for sid,job in current:
            p=Path(job['run'])/'assessment/study_metadata_index.json'
            try:value=json.loads(p.read_text())
            except (OSError,ValueError):continue
            for row in value.get('studies',[]):
                if row.get('study_id')!=sid:continue
                base=rows.setdefault(sid,{'study_id':sid})
                for key,v in row.items():
                    if v is not None and v!='' and v!=[]:base[key]=v
                present=row.get('mass_rt_like_metadata_present')
                base['mzrt_metadata_status']='present' if present is True else 'absent' if present is False else 'not assessed'
                base['band']=base['band_label']='';base['score']=None
        return list(rows.values())


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path);ap.add_argument('--adopt-pull',type=Path)
    args=ap.parse_args();catalog=Catalog(args.root)
    if args.adopt_pull:catalog.adopt(args.adopt_pull)
    else:catalog.refresh()
    print(json.dumps(catalog.status(),indent=2))
    if catalog.error:raise SystemExit(1)
