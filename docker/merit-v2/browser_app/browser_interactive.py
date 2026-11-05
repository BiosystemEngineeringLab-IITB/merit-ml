"""Per-matrix exception boundaries for settings and sample edits in the browser."""
import copy
import functools
import inspect
import json
from pathlib import Path

FUNCTIONS=('_missingness_study','_scale_study','_low_information_study','_redundancy_study',
           '_outlier_study','_qc_blank_study','_ml_study','_annotation_study','annotation_lists','feature_list_payload')
_INSTALLED=False
FAILURES={}
EXCLUDE=set()


def install(pilot,run):
    global _INSTALLED
    if _INSTALLED:return
    _INSTALLED=True
    raw_reader=pilot._raw_study;active=False
    originals={n:getattr(pilot,n) for n in FUNCTIONS if hasattr(pilot,n)}
    from merit import labels_v9 as lb
    original_items=lb.factor_items
    import browser_runtime as br

    def wrap(name,fn):
        original=getattr(fn,'__wrapped__',fn);sig=inspect.signature(fn)
        @functools.wraps(fn)
        def call(*args,**kwargs):
            nonlocal active
            if active:return original(*args,**kwargs)
            bound=sig.bind(*args,**kwargs);bound.apply_defaults();a=dict(bound.arguments);sid=a['study_id']
            raw=raw_reader(sid)
            if raw is None:return None
            full_items=original_items(raw,a.get('overrides'))
            excluded={(aid,src) for function,aid,src in EXCLUDE if function==name}
            if name=='_redundancy_study' or name=='feature_list_payload' and a.get('metric')=='redundancy':
                from merit import structural_v9 as sv,low_information_v9 as li
                params=a.get('params') or {};ov=a.get('overrides') or {}
                li_changed=any(float(params.get(k,v))!=float(v) for k,v in li.DEFAULT_PARAMS.items() if k!='low_information_cv_pct')
                if ov or li_changed:
                    plan=json.loads((Path(run)/'browser_resource_plan.json').read_text())
                    for row in raw['analyses']:
                        for source,ep in row['endpoints'].items():
                            if 'roster' not in ep:continue
                            kept=lambda edits:[p['eligible'] and not p['excluded'] and not p['row'][sv.I_ALIGN] for p in sv.effective_positions(ep['roster'],edits)]
                            changed=li_changed or kept(ov)!=kept(None)
                            if not changed:continue
                            need=br.matrix_need({'positions':sum(kept(ov)),'features':ep['n_features'],'bytes':0})
                            if need['bytes']>plan['budget_bytes']:
                                aid=row['analysis_id'];excluded.add((aid,source))
                                FAILURES[(name,aid,source)]={'function':name,'analysis_id':aid,'endpoint':source,'reason':'After these edits, worst-case pair storage exceeds the browser memory budget; use the full application'}
            current=[None,None]
            # The original implementation still performs one normal pass. Track
            # endpoint access so a failure identifies its matrix, without n passes.
            class Endpoints(dict):
                def __init__(self,aid,values):super().__init__(values);self.aid=aid
                def get(self,src,*defaults):
                    if src in self:
                        current[:]=[self.aid,src]
                        br.post('active',task={'function':name,'analysis':self.aid,'endpoint':src})
                    return super().get(src,*defaults)
                def __getitem__(self,src):
                    if src in self:
                        current[:]=[self.aid,src]
                        br.post('active',task={'function':name,'analysis':self.aid,'endpoint':src})
                    return super().__getitem__(src)
            while True:
                tracked=dict(raw);tracked['analyses']=[]
                for row in raw['analyses']:
                    eps={src:dict(ep) for src,ep in row['endpoints'].items()}
                    for src,ep in eps.items():
                        if (row['analysis_id'],src) in excluded:ep.pop('roster',None)
                    tracked['analyses'].append({**row,'endpoints':Endpoints(row['analysis_id'],eps)})
                pilot._raw_study=lambda *x,**k:tracked
                lb.factor_items=lambda *x,**k:full_items
                active=True
                try:
                    value=original(*args,**kwargs)
                    break
                except (Exception,MemoryError) as exc:
                    aid,src=current
                    if not aid or not src or (aid,src) in excluded:raise
                    excluded.add((aid,src));FAILURES[(name,aid,src)]={'function':name,'analysis_id':aid,'endpoint':src,'reason':str(exc)[-400:] or type(exc).__name__}
                finally:
                    active=False;pilot._raw_study=raw_reader;lb.factor_items=original_items
            entries=value.get('analyses',[]) if isinstance(value,dict) else value or []
            for row in entries:
                for src in list(row.get('endpoints',{})):
                    key=(name,row['analysis_id'],src)
                    if (row['analysis_id'],src) in excluded:
                        reason=FAILURES.get(key,{}).get('reason') or 'This check stopped in a previous browser worker; successful matrices are retained'
                        row['endpoints'][src]={'availability':'not assessed: '+reason,'not_assessed':reason}
                    else:FAILURES.pop(key,None)
            return value
        return call
    for name,fn in originals.items():setattr(pilot,name,wrap(name,fn))


def configure(payload):
    EXCLUDE.clear()
    EXCLUDE.update(tuple(x) for x in payload.get('browser_exclude',[]))


def notice(html,run):
    from html import escape
    failures=list(FAILURES.values())
    p=Path(run)/'matrix_execution.json'
    if p.exists():failures+=json.loads(p.read_text()).get('checks',[])
    failures=[x for x in failures if x.get('state')!='complete' and not (x.get('stage')=='bootstrap' and x.get('code')=='browser_memory_limit')]
    if not failures:return html
    items=''.join('<li>'+escape(' · '.join(str(x.get(k) or 'shared') for k in ('analysis_id','endpoint'))+': '+str(x.get('stage') or x.get('function'))+' — '+str(x.get('reason')) )+'</li>' for x in failures)
    box="<section id='browser-execution-notes' style='padding:12px 18px;background:#fff4e5'><b>Some checks were not assessed.</b> Completed results are retained; blank results are not a score of zero. For these checks, use the <a href='https://github.com/BiosystemEngineeringLab-IITB/merit-ml' target='_blank' rel='noopener'>full MERIT-ML application</a>.<ul>"+items+"</ul></section>"
    import re
    return re.sub(r'(<body[^>]*>)',lambda m:m[0]+box,html,count=1)
