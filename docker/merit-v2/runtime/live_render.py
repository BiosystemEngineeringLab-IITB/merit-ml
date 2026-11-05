"""Isolated renderer for the unchanged, versioned MERIT UI.

Inputs are a newly computed job and submitted form settings. Historical v8/v9
results and deposits are forbidden by the same audit hook as the calculator.
The bridge supplies only fresh study metadata to the obsolete report shell;
all visible metric panels and exports use the freshly built v9-shaped records.
"""
from __future__ import annotations
import base64
import collections
import copy
import csv
import io
import json
import os
import sys
import traceback
import zipfile
from pathlib import Path

_THEME_CSS = """<style id="merit-theme-css">
html[data-theme="dark"]{--ink:#e8eaed;--muted:#b4c0c3;--bg:#111518;--panel:#1b2226}
html[data-theme="dark"] [style*="background:rgba(255,255,255"],html[data-theme="dark"] [style*="background:#fff"],html[data-theme="dark"] [style*="background:white"]{background:#1b2226!important;color:#e8eaed!important}
html[data-theme="dark"] [style*="color:#132327"],html[data-theme="dark"] [style*="color:#51656a"],html[data-theme="dark"] h2,html[data-theme="dark"] h3,html[data-theme="dark"] h4{color:#dce6e9!important}
html[data-theme="dark"] body{background:#111518!important;color:#e8eaed!important}
html[data-theme="dark"] .panel,html[data-theme="dark"] .card,html[data-theme="dark"] .about-card,
html[data-theme="dark"] .v2-tune,html[data-theme="dark"] .tab-panel,html[data-theme="dark"] .tablewrap,
html[data-theme="dark"] details,html[data-theme="dark"] .study-card{background:#1b2226!important;color:#e8eaed!important;border-color:#394247!important}
html[data-theme="dark"] input,html[data-theme="dark"] select,html[data-theme="dark"] textarea{background:#20282d!important;color:#e8eaed!important;border-color:#465158!important}
html[data-theme="dark"] p,html[data-theme="dark"] li,html[data-theme="dark"] td,html[data-theme="dark"] th,
html[data-theme="dark"] label,html[data-theme="dark"] summary{color:#c5ced1}
#merit-theme-toggle{position:fixed;right:16px;top:14px;z-index:99999;width:38px;height:38px;border:1px solid #c7d0d2;border-radius:50%;background:#fff;color:#263238;font:18px Arial;cursor:pointer;box-shadow:0 2px 8px #0002}
html[data-theme="dark"] #merit-theme-toggle{background:#20282d;color:#f3f5f6;border-color:#536168}
</style>"""
_THEME_HTML = """<button id="merit-theme-toggle" type="button" aria-label="Switch color theme" title="Switch color theme">◐</button>
<script>(function(){var r=document.documentElement,k='merit-ml-theme';try{r.dataset.theme=localStorage.getItem(k)||'light'}catch(e){r.dataset.theme='light'}var b=document.getElementById('merit-theme-toggle');function paint(){var d=r.dataset.theme==='dark';b.textContent=d?'☼':'◐';b.setAttribute('aria-label',d?'Switch to light theme':'Switch to dark theme')}paint();b.addEventListener('click',function(){r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';try{localStorage.setItem(k,r.dataset.theme)}catch(e){}paint()})})();</script>"""

import online_assess as oa


def setup(run):
    run=Path(run).resolve()
    if not run.is_relative_to(oa.HOME/'runs'): raise ValueError('Job must be inside online-tool/runs')
    live=run/'materialized'; out=run/'assessment'
    for key,value in {'MERIT_MW_DUMP_ROOT':live,'MERIT_V9_CACHE_ROOT':out,
                      'MERIT_UI_PRECOMPUTED_ROOT':out,'MERIT_PRECOMPUTED_ROOT':out,
                      'MERIT_DERIVED_OUTPUT_ROOT':out/'derived','MERIT_CACHE_ONLY':'1'}.items():
        os.environ[key]=str(value)
    sys.dont_write_bytecode=True
    oa.install_guard()
    # Rendering/settings changes must never make a second, hidden API request.
    def no_network(event,args):
        if event in ('socket.connect','socket.getaddrinfo'):
            raise PermissionError('Network is restricted to the explicit fetch stage')
    sys.addaudithook(no_network)
    for p in (oa.BASE/'outputs/measurement_scale_20260930',oa.T4,oa.STRUCT,oa.CODE):sys.path.insert(0,str(p))
    oa.configure(live,out,run)
    from merit import ui, pilot_analysis_endpoint as pilot, sample_annotations_v9 as sa, readiness_v9 as rd
    sa.DUMP=live
    rd.reference=lambda: json.loads((oa.BUNDLE_HOME/'assets/alert_reference_R1.json').read_text())
    # Remove dependency on the retired v8 scoring engine. It does not supply
    # any v9 metric: the actual tab/render/export functions below are unchanged.
    def profile(state,params,matrix_overrides=None):
        if state is None:return None
        state=copy.deepcopy(state)
        state['v2_scoring_params']=dict(params)
        state['v2_matrix_overrides']=matrix_overrides or {}
        return state
    ui._v2_apply_scoring_profile=profile
    ui._default_precomputed_root=lambda:str(out)
    original_sources=ui._source_availability_panel
    def live_sources(availability,tier):
        import re
        text=original_sources(availability,tier)
        note=("<p style='margin:10px 0 0;font-size:.75rem;color:#51656a;line-height:1.5'>"
              "Counts show analyses with a valid quantitative matrix at each endpoint. "
              "A mwTab metadata record can exist without a valid quantitative data block. "
              "Each available matrix is assessed separately; no endpoint is preferred.</p>")
        return re.sub(r"<p style='margin:10px 0 0;.*?</p>",lambda m:note,text,flags=re.S)
    ui._source_availability_panel=live_sources
    from merit import acquisition_context_ui as context_ui
    context_ui.DESIGN_TIP=('Provenance is recorded for each study: original-cohort reviewed design labels come from the versioned context reference; '
        'other studies use the original MERIT programmatic classifier on fresh MW metadata and sample structure. '
        'No new language-model reading is performed. A programmatic confidence label is a heuristic category, not a validated probability. '
        'Nothing here enters a score or alert.')
    original_design_line=context_ui._design_line
    def live_design_line(design):
        text=original_design_line(design)
        if design.get('language_model_status')=='not_run':
            from html import escape
            evidence='; '.join(design.get('rule_evidence') or [])
            text+=("<div style='margin-top:4px;font-size:.77rem;color:#51656a'>Rule-based reading computed from this live assessment; "
                   "independent language-model reading not performed.</div>"
                   "<details style='font-size:.77rem;margin-top:4px'><summary>Rule evidence</summary>"+escape(evidence)+"</details>")
        return text
    context_ui._design_line=live_design_line
    if sys.platform!='emscripten' and os.environ.get('MERIT_NATIVE_SUPERVISION')=='1':
        from native_guard import install
        install(ui,pilot,run,live,out)
    return ui,pilot,live,out


def state_for(sid,live,out):
    from merit.models import AssessmentReport
    from fetch_online import records
    raw=json.loads((out/'json'/f'{sid.lower()}_v9.json').read_text())
    try:rows=records(json.loads((live/sid/'summary.json').read_text()))
    except (OSError,ValueError):rows=[]
    meta=rows[0] if rows else {}
    summary={**meta,'study_id':sid,'source':'workbench',
             'title':meta.get('study_title') or meta.get('title') or sid,
             'organism':meta.get('species',''),
             'n_analyses':len(raw['analyses'])}
    # Fill the current UI's metadata shell from live declarations and rosters,
    # rather than supplying blank legacy summary fields from a mock report.
    combined=live/sid/'__merit_combined_online.json'
    if combined.exists():
        c=json.loads(combined.read_text())
        summary['description']=c.get('project_summary') or c.get('collection_summary') or ''
    from merit.connectors.workbench import MetabolomicsWorkbenchConnector
    try:disease=json.loads((live/sid/'disease.json').read_text())
    except (OSError,ValueError):disease={}
    summary['disease']=MetabolomicsWorkbenchConnector._extract_disease_name(disease)
    summary['has_disease_endpoint']=bool(summary['disease'])
    summary['submission_date']=meta.get('submission_date','');summary['release_date']=meta.get('release_date','')
    summary['analysis_type_label']=meta.get('analysis_type','')
    empty={k:[] for k in ('schema_validation','metadata_readiness','analytical_readiness','annotation_readiness',
                          'cohort_bias','ml_readiness','class_separability','cross_study_harmonization','remediations_applied')}
    report=AssessmentReport(source={'source':'workbench','study_id':sid},ingestion_summary=summary,software_versions={},**empty)
    shell={'actions':[],'section_scores':{},'recommendation':''}
    by_source={src:[a['analysis_id'] for a in raw['analyses'] if 'roster' in a['endpoints'].get(src,{})]
               for src in oa.SOURCES}
    available={src:bool(ids) for src,ids in by_source.items()}
    source_availability={**available,**{src+'_count':len(ids) for src,ids in by_source.items()},
                         'analyses_by_source':by_source}
    # The source shell exists for the current UI's tab namespace only. There is
    # no source priority and no numeric old readiness result is provided.
    source_assessments={}
    summary_file=out/'ui_summaries'/f'{sid.lower()}.json'
    source_summaries=json.loads(summary_file.read_text()) if summary_file.exists() else {}
    for src,ok in available.items():
        if not ok:source_assessments[src]=None;continue
        sm=copy.deepcopy(summary);seen={};measured=set();per=[]
        for a in raw['analyses']:
            ep=a['endpoints'].get(src) or {}
            if 'roster' not in ep:continue
            for row in ep['roster']:
                if row[3]:continue
                measured.add(row[0])
                if not row[2]:seen.setdefault(row[0],row[5] if row[4] else row[1])
            annotation=ep.get('sections',{}).get('annotation') or {}
            named=annotation.get('named',0);eligible=annotation.get('eligible',0)
            tier='no_annotations' if not eligible else 'unannotated' if not named else 'named' if named/eligible>=.7 else 'mixed'
            per.append({**(a.get('sections',{}).get('analytical',{}).get('data_summary') or {}),
                        'analysis_id':a['analysis_id'],'n_features':ep['n_features'],
                        'n_named_features':named,'n_eligible_features':eligible,'annotation_tier':tier})
        compact=lambda s:' | '.join(x.strip() for x in s.split('|') if x.strip())
        counts=collections.Counter(compact(s) for s in seen.values() if s and s.strip()!='-')
        sm.update(n_samples=len(measured),n_biological_samples=len(seen),n_labeled_samples=sum(counts.values()),
                  n_classes=len(counts),class_counts=dict(counts),n_feature_matrices=len(per),
                  n_features=sum(a['n_features'] for a in per),per_analysis=per)
        if src in source_summaries:sm=source_summaries[src]
        sr=copy.deepcopy(report);sr.ingestion_summary=sm
        source_assessments[src]={'_report':sr,'readiness_score':shell,'source':src,
                                'source_tier':'tier2' if src=='untarg_data' else 'tier1','ingestion_summary':sm}
    # As the 8782 workflow state: primary = first available of datatable > mwtab > untarg_data (None when none);
    # priority tier 1 when datatable or mwTab is available; the primary source's tier (tier 1 also when none).
    primary=next((src for src in oa.SOURCES if available[src]),None)
    source_availability['priority_tier']='tier1' if available['datatable'] or available['mwtab'] else 'tier2'
    return {'study_id':sid,'profile':'full','final_report':report,'readiness_score':shell,
            'source_availability':source_availability,'source_assessments':source_assessments,
            'source_tier':'tier2' if primary=='untarg_data' else 'tier1',
            'primary_source':primary,'bundle':{'acquisition_source':'remote_fetch'},'remediations':[],
            'online_provenance':{'input':'fresh MW REST responses','fetch_manifest':'/api/provenance/'+sid,
                                 'historical_results_read':False}}


_SETUP={}


def respond(payload):
    run=Path(payload['run']);sid=payload.get('study_id','').strip().upper()
    key=str(run.resolve())
    if key not in _SETUP:_SETUP[key]=setup(run)
    ui,pilot,live,out=_SETUP[key]
    if payload.get('action')=='warm':
        return {'ok':True,'mime':'text/plain','filename':None,'data_b64':'','isolation':{'denied_attempts':oa.DENIED,'files_read':[]}}
    form=dict(payload.get('form') or {})
    form.update(study_id=sid,source='workbench',precomputed_root=str(out),profile='full')
    params=ui._coerce_v2_scoring_params(form)
    params['label_classes']=str(form.get('label_classes') or '')
    params['subjects_confirmed']=str(form.get('subjects_confirmed') or '')
    overrides=ui._coerce_v2_matrix_overrides(form)
    action=payload.get('action','page')
    if action in ('page','ui-state'):
        state=state_for(sid,live,out) if sid else None
        result=ui._page(state=state,defaults=form)
        if action=='ui-state':
            marker='window.__MERIT_STATE_JSON = '
            value=json.JSONDecoder().raw_decode(result.split(marker,1)[1])[0]
            value['online_assessment']={'study_id':sid,'job_id':payload.get('job_id') or run.name,
                                       'source':'fresh MW responses','historical_results_used':False}
            return {'ok':True,'mime':'application/json','filename':f'{sid}_online_pull_ui_state.json',
                    'data_b64':base64.b64encode(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).decode(),
                    'isolation':{'denied_attempts':oa.DENIED,'files_read':sorted(oa.READS)}}
        # All styling, panel HTML and interaction JS come from the exact source
        # snapshot. This thin live-provenance strip is deliberately additional.
        if sid:
            job_id=str(payload.get('job_id') or run.name)
            when=str(payload.get('retrieved_utc') or '')
            when_text=''
            if when:
                import datetime as _dt
                try:when_text=_dt.datetime.fromisoformat(when).strftime('%d %b %Y, %H:%M UTC')
                except ValueError:when_text=when
            strip=("<div id='online-provenance' style='padding:8px 18px;background:#eaf4f3;color:#22494b;font:13px sans-serif'>"
                   "Assessment input: Metabolomics Workbench"+(f" · retrieved {when_text}" if when_text else '')+" · "
                   f"<a href='/?study_id={sid}&amp;refresh=1'>Retrieve again</a> · "
                   f"<a href='/api/provenance/{sid}?live_job_id={job_id}' target='_blank'>Input provenance</a></div>")
            result=result.replace('<body', '<body',1)
            import re
            result=re.sub(r'(<body[^>]*>)',lambda m:m[0]+strip,result,count=1)
            script=r'''<script>(function(){
const job=__JOB__,study=__STUDY__;
const paths=new Set(['/workflow/run','/download/feature-list','/download/v9-matrix','/download/ml-ready-data','/download/ml-ready-matrix']);
function token(form){let u=new URL(form.action,location.href);if(u.origin!==location.origin||!paths.has(u.pathname))return;
let id=form.elements.namedItem('study_id');let st=id?String(id.value).trim().toUpperCase():study;
let field=form.elements.namedItem('live_job_id');if(st!==study){if(field)field.remove();return;}
if(!field){field=document.createElement('input');field.type='hidden';field.name='live_job_id';form.appendChild(field);}field.value=job;}
document.querySelectorAll('form').forEach(token);
document.addEventListener('submit',e=>token(e.target),true);
const native=HTMLFormElement.prototype.submit;
HTMLFormElement.prototype.submit=function(){token(this);return native.call(this);};
const fetchNative=window.fetch;
window.fetch=function(input,init){let u=new URL(typeof input==='string'?input:input.url,location.href);
if(u.origin===location.origin&&paths.has(u.pathname)&&init&&init.body){let b=init.body;
if(b instanceof FormData||b instanceof URLSearchParams){let st=b.get('study_id');if(!st||st.trim().toUpperCase()===study)b.set('live_job_id',job);}}
return fetchNative.call(this,input,init);};
document.querySelectorAll('a[href*="/download/acquisition-evidence"]').forEach(a=>{let u=new URL(a.href,location.href);u.searchParams.set('live_job_id',job);a.href=u.href;});
if(window.__MERIT_STATE_JSON)window.__MERIT_STATE_JSON.online_assessment={job_id:job,study_id:study,source:'fresh MW responses',historical_results_used:false};
})();</script>'''.replace('__JOB__',json.dumps(job_id)).replace('__STUDY__',json.dumps(sid))
            result=result.replace('</body>',script+'</body>')
        if '</head>' in result and 'merit-theme-css' not in result:
            result=result.replace('</head>',_THEME_CSS+'</head>',1)
        if '</body>' in result and 'merit-theme-toggle' not in result:
            result=result.replace('</body>',_THEME_HTML+'</body>',1)
        from live_progress import enhance_page
        result=enhance_page(result,sid)
        data=result.encode();mime='text/html';filename=None
    elif action=='feature-list':
        metric=form.get('metric','')
        if metric not in ('low_information','redundancy','outliers','qc_reproducibility','controls','annotation'):raise ValueError('Unknown metric')
        result=pilot.public(pilot.feature_list_payload(sid,metric,params,overrides))
        data=json.dumps(result,ensure_ascii=False,indent=1).encode();mime='application/json';filename=f'{sid}_{metric}.json'
    elif action=='matrix':
        from merit import v9_matrix_export
        data,filename=v9_matrix_export.matrix_zip(sid,form.get('analysis_id',''),form.get('endpoint',''),params,overrides)
        mime='application/zip'
    elif action=='ml-ready-matrix':
        from merit import v9_matrix_export
        data,filename=v9_matrix_export.ml_ready_zip(sid,form.get('analysis_id',''),form.get('endpoint',''),params,overrides)
        mime='application/zip'
    elif action=='acquisition':
        records=(pilot.acquisition_context(sid) or {}).get('records') or []
        if not records:raise ValueError('No acquisition evidence is available')
        output=io.StringIO(newline=''); writer=csv.DictWriter(output,fieldnames=list(records[0]),delimiter='\t',lineterminator='\n')
        writer.writeheader();writer.writerows(records);data=output.getvalue().encode();mime='text/tab-separated-values';filename=f'{sid}_acquisition_evidence.tsv'
    elif action=='data':
        # The retired study-level export uses different eligibility rules. Use
        # the current per-matrix exporter for every valid endpoint instead.
        from merit import v9_matrix_export
        raw=pilot._raw_study(sid);selected=ui._ml_export_clean_analysis_ids(form.get('analysis_ids',''))
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_STORED) as z:
            for a in raw['analyses']:
                if selected and a['analysis_id'] not in selected:continue
                for src,e in a['endpoints'].items():
                    if 'roster' not in e:continue
                    part,name=v9_matrix_export.matrix_zip(sid,a['analysis_id'],src,params,overrides)
                    z.writestr(name,part)
            z.writestr('settings.json',json.dumps({'parameters':params,'sample_edits':overrides,
                'note':'Every valid analysis × endpoint, all deposited positions and values, with current eligibility flags. No endpoint chosen or averaged.'}))
        data=stream.getvalue();filename=f'{sid}_all_matrix_inputs.zip'
        mime='application/zip'
    elif action=='browser':
        data=json.dumps(ui._study_browser_data_payload(str(out),form),ensure_ascii=False).encode();mime='application/json';filename=None
    elif action=='bulk-page':
        data=ui._bulk_runner_page(ui._bulk_clean_session(form.get('bulk_session','')),str(out)).encode();mime='text/html';filename=None
    elif action=='bulk-records':
        value=pilot.public(pilot.export_overview_block(sid,params,overrides));summaries=[];metrics=[]
        for a in value['analyses']:
            if not any(e.get('availability')=='valid' for e in a['endpoints'].values()):
                summaries.append({'study_id':sid,'analysis_id':a['analysis_id'],'endpoint':'',
                    'summary':'No valid quantitative matrix available','class_counts':'not assessable',
                    'smallest_class':None,'classes':None,'ml_eligible_sample_ids':None,'features':None,
                    'analytical_alerts':'not assessable','checks_assessed':0,'class_support':None,
                    'separability':None,'chance':None})
                continue
            for src,e in a['endpoints'].items():
                if e.get('availability')!='valid':continue
                rd=e.get('readiness') or {};task=rd.get('task') or {};d=rd.get('data') or {};p=e.get('profile') or {}
                row={'study_id':sid,'analysis_id':a['analysis_id'],'endpoint':src,'summary':rd.get('summary'),
                     'class_counts':task.get('result'),'smallest_class':task.get('smallest'),'classes':task.get('n_classes'),
                     'ml_eligible_sample_ids':p.get('eligible_sample_ids'),'features':p.get('features'),
                     'analytical_alerts':d.get('result'),'checks_assessed':d.get('assessed'),'class_support':task.get('class_support'),
                     'separability':task.get('separability'),'chance':task.get('chance')}
                summaries.append(row)
                for metric,item in e.get('items',{}).items():
                    metrics.append({'study_id':sid,'analysis_id':a['analysis_id'],'endpoint':src,'metric':metric,
                                    'value':item.get('value'),'detail':item.get('detail')})
        data=json.dumps({'summary_rows':summaries,'metric_rows':metrics,'settings':value['readiness_settings']},ensure_ascii=False).encode()
        mime='application/json';filename=None
    else:raise ValueError('Unknown action')
    return {'ok':True,'mime':mime,'filename':filename,'data_b64':base64.b64encode(data).decode(),
            'isolation':{'denied_attempts':oa.DENIED,'files_read':sorted(oa.READS)}}


def serve():
    """Warm renderer for one assessment: one JSON request per input line, one JSON answer per output line."""
    run=None
    for line in sys.stdin:
        if not line.strip():continue
        try:
            value=json.loads(line)
            if run is None:run=str(Path(value['run']).resolve())
            elif str(Path(value['run']).resolve())!=run:raise ValueError('A warm renderer serves one assessment only')
            answer=respond(value)
        except Exception:
            answer={'ok':False,'error':traceback.format_exc(),'denied_attempts':oa.DENIED}
        sys.stdout.write(json.dumps(answer,ensure_ascii=False)+'\n');sys.stdout.flush()


if __name__=='__main__':
    if '--serve' in sys.argv:
        serve();sys.exit(0)
    try:
        value=json.load(sys.stdin)
        print(json.dumps(respond(value),ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok':False,'error':traceback.format_exc(),'denied_attempts':oa.DENIED}))
        sys.exit(1)
