"""Small live progress UI; numerical calculation rules are unchanged."""
import html
import json

STAGES=('bootstrap','metadata','controls','missingness','scale','low_information','redundancy',
        'outliers','qc_reproducibility','annotation','refmet_lookup','labels','separability',
        'extra_evidence_and_readiness','live_context_and_indexes')
LABELS={'bootstrap':'Reading matrices and sample information','metadata':'Checking metadata and FAIR information',
        'controls':'Identifying QC, blank and standard samples','missingness':'Checking missing entries and zeros',
        'scale':'Reading measurement scale and units','low_information':'Checking low-information features',
        'redundancy':'Checking feature correlations','outliers':'Checking sample-median outliers',
        'qc_reproducibility':'Checking QC reproducibility','annotation':'Checking feature annotations',
        'refmet_lookup':'Checking RefMet mappings','labels':'Checking labels and class support',
        'separability':'Calculating class separability','extra_evidence_and_readiness':'Building the readiness profile',
        'live_context_and_indexes':'Preparing study context and report information'}


def progress(status):
    state=status['state'];stages=status.get('stages') or []
    done={r['stage'] for r in stages if r.get('state')=='done'}
    active=next((r['stage'] for r in reversed(stages) if r.get('state')=='started'),None)
    if state=='complete':return {'label':'Assessment complete','detail':'Preparing your study report…','percent':100}
    if state=='failed':return {'label':'Assessment could not be completed','detail':status.get('error') or 'Please try again.','percent':None}
    if state=='queued':return {'label':'Starting assessment','detail':'Waiting for an available calculation slot…','percent':None}
    if state=='fetching':
        n=(status.get('fetch') or {}).get('requests_completed',0)
        return {'label':'Downloading study data','detail':f'{n} source responses received' if n else 'Contacting Metabolomics Workbench…','percent':None}
    return {'label':'Calculating metrics','detail':LABELS.get(active,'Reading inputs and preparing calculations…'),
            'percent':min(99,round(100*len(done)/len(STAGES)))}


STYLE="""<style>
.live-progress-track{height:12px;background:#e6eded;border-radius:999px;overflow:hidden;margin:16px 0 10px}
.live-progress-fill{height:100%;width:0;background:linear-gradient(90deg,#0d6e6e,#278a89);border-radius:999px;transition:width .35s}
.live-progress-track.indeterminate .live-progress-fill{width:35%;animation:live-progress-move 1.5s ease-in-out infinite alternate}
@keyframes live-progress-move{0%{transform:translateX(0)}100%{transform:translateX(185%)}}
@media(prefers-reduced-motion:reduce){.live-progress-track.indeterminate .live-progress-fill{animation:none;width:100%;opacity:.55}}
#live-submit-progress{position:fixed;inset:0;background:rgba(244,241,233,.94);z-index:20000;display:grid;place-items:center;padding:24px;color:#132327;font:15px sans-serif}
#live-submit-progress[hidden]{display:none}
.live-progress-card{box-sizing:border-box;width:min(100%,700px);padding:28px;background:white;border:1px solid rgba(19,35,39,.12);border-radius:20px}
</style>"""


def enhance_page(page,study_id=''):
    """Paint immediately on submission, including from an existing report."""
    script=r'''<script>(function(){
const current=__STUDY__;
const run=document.getElementById('run-form');if(run){let origin=run.elements.namedItem('live_origin_study_id');
if(!origin){origin=document.createElement('input');origin.type='hidden';origin.name='live_origin_study_id';run.appendChild(origin);}origin.value=current;}
function show(form){if(!form||!['run-form','start'].includes(form.id)||!form.checkValidity())return;
const input=form.elements.namedItem('study_id');if(!input)return;const id=input.value.trim().toUpperCase();
if(!/^ST\d{6}$/.test(id))return;input.value=id;
if(id!==current){['live_job_id','label_classes','subjects_confirmed','matrix_overrides'].forEach(n=>{const x=form.elements.namedItem(n);if(x)x.value='';});}
let box=document.getElementById('live-submit-progress');if(!box){box=document.createElement('div');box.id='live-submit-progress';
box.setAttribute('role','status');box.setAttribute('aria-live','polite');
box.innerHTML='<section class="live-progress-card"><h2 style="margin:0 0 8px">MERIT-ML</h2><div class="live-progress-study"></div><div class="live-progress-track indeterminate" role="progressbar" aria-label="Assessment progress"><div class="live-progress-fill"></div></div><p class="live-progress-message" style="margin:0;color:#51656a"></p></section>';document.body.appendChild(box);}
box.hidden=false;box.querySelector('.live-progress-study').textContent=id;
box.querySelector('.live-progress-message').textContent=id===current?'Updating your assessment…':'Starting a fresh assessment…';}
document.addEventListener('submit',e=>show(e.target),true);
const native=HTMLFormElement.prototype.submit;HTMLFormElement.prototype.submit=function(){show(this);return native.call(this);};
window.addEventListener('pageshow',()=>{const box=document.getElementById('live-submit-progress');if(box)box.hidden=true;});
})();</script>'''.replace('__STUDY__',json.dumps(study_id))
    return page.replace('</head>',STYLE+'</head>').replace('</body>',script+'</body>')


def waiting_page(job,study_id,form):
    form={**form,'live_job_id':job['id']}
    fields=''.join(f'<input type="hidden" name="{html.escape(k,quote=True)}" value="{html.escape(str(v),quote=True)}">' for k,v in form.items())
    page='''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MERIT-ML · assessment in progress</title>'''+STYLE+'''</head>
<body style="margin:0;font:15px sans-serif;background:#f4f1e9;color:#132327;padding:32px 20px">
<main class="live-progress-card" style="margin:7vh auto"><a href="/" style="color:#0d6e6e;text-decoration:none;font-weight:700">MERIT-ML</a>
<h1 style="font-size:24px;margin:22px 0 8px">'''+html.escape(study_id)+'''</h1>
<p id="live-phase" role="status" aria-live="polite">Starting assessment</p>
<div id="live-progress" class="live-progress-track indeterminate" role="progressbar" aria-label="Assessment progress" aria-valuemin="0" aria-valuemax="100"><div class="live-progress-fill"></div></div>
<p id="live-detail" style="color:#51656a;font-size:14px">Contacting Metabolomics Workbench…</p>
<p style="font-size:12px;color:#6f7e83">Large matrices can take longer. You can leave this page open while the assessment runs.</p>
<form id="resume" method="post" action="/workflow/run">'''+fields+'''</form>
<details style="border-top:1px solid #e6eded;padding-top:16px;margin-top:22px"><summary style="cursor:pointer;color:#0d6e6e">Assess another study</summary>
<form method="get" action="/" style="margin-top:12px;display:flex;gap:8px"><input name="study_id" placeholder="e.g. ST000096" pattern="[Ss][Tt][0-9]{6}" required aria-label="Study ID" style="min-width:0;flex:1;padding:10px;border:1px solid #ccd9d9;border-radius:8px"><button style="border:0;border-radius:8px;background:#0d6e6e;color:white;padding:10px 16px">Assess</button></form></details>
</main><script>
const target=__TARGET__;
async function poll(){try{const r=await fetch(target,{cache:'no-store'});if(!r.ok)throw Error('Status unavailable');const j=await r.json();const p=j.progress||{};
document.getElementById('live-phase').textContent=p.label||j.state;document.getElementById('live-detail').textContent=p.detail||'';
const track=document.getElementById('live-progress');const fill=track.firstElementChild;const n=p.percent;
if(typeof n==='number'){track.classList.remove('indeterminate');track.setAttribute('aria-valuenow',n);fill.style.width=n+'%';}
else{track.classList.add('indeterminate');track.removeAttribute('aria-valuenow');}
if(j.state==='complete'){document.getElementById('resume').submit();return;}if(j.state==='failed'){track.classList.remove('indeterminate');return;}
}catch(e){document.getElementById('live-detail').textContent='Reconnecting to the assessment…';}setTimeout(poll,1500);}poll();
</script></body></html>'''
    return page.replace('__TARGET__',json.dumps('/api/jobs/'+job['id']))


def unavailable_page(home_html,check,seconds=8):
    """Landing page with a dialog: why the ID is not shown, then a countdown to the most recent public study."""
    latest=check.get('latest')
    title=check.get('title') or ('Not a Metabolomics Workbench study ID' if check.get('reason')=='not_a_workbench_id' else 'Study not available')
    target='/?study_id='+latest if latest else ''
    go=(f'<p style="margin:14px 0 0">The most recent public study on Metabolomics Workbench is <strong>{html.escape(latest)}</strong>. '
        f'Opening it in <span id="live-redirect-seconds">{seconds}</span> s.</p>'
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:18px">'
        f'<a id="live-redirect-go" href="{html.escape(target,quote=True)}" style="background:#0d6e6e;color:white;border-radius:8px;padding:10px 16px;text-decoration:none">Open {html.escape(latest)} now</a>'
        f'<button id="live-redirect-stay" type="button" style="border:1px solid #ccd9d9;background:white;border-radius:8px;padding:10px 16px;cursor:pointer">Stay here</button></div>') if latest else (
        '<div style="margin-top:18px"><button id="live-redirect-stay" type="button" style="border:1px solid #ccd9d9;background:white;border-radius:8px;padding:10px 16px;cursor:pointer">Close</button></div>')
    dialog=('<div id="live-unavailable" role="alertdialog" aria-modal="true" aria-labelledby="live-unavailable-title" '
            'style="position:fixed;inset:0;background:rgba(19,35,39,.45);z-index:20001;display:grid;place-items:center;padding:20px;font:15px sans-serif">'
            '<section style="box-sizing:border-box;width:min(100%,560px);background:white;border-radius:16px;padding:26px;color:#132327;box-shadow:0 18px 50px rgba(0,0,0,.25)">'
            f'<h2 id="live-unavailable-title" style="margin:0 0 10px;font-size:20px">{html.escape(title)}</h2>'
            f'<p style="margin:0;line-height:1.5">{html.escape(check.get("message") or "")}</p>{go}</section></div>')
    script=("<script>(function(){const box=document.getElementById('live-unavailable');let n=__N__;const target=__T__;"
            "const out=document.getElementById('live-redirect-seconds');let timer=target?setInterval(function(){n-=1;if(out)out.textContent=n;"
            "if(n<=0){clearInterval(timer);location.href=target;}},1000):null;"
            "document.getElementById('live-redirect-stay').addEventListener('click',function(){if(timer)clearInterval(timer);box.remove();});"
            "const go=document.getElementById('live-redirect-go');if(go)go.focus();})();</script>")
    script=script.replace('__N__',str(int(seconds))).replace('__T__',json.dumps(target))
    page=enhance_page(home_html)
    return page.replace('</body>',dialog+script+'</body>',1)
