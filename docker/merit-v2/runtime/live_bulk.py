"""Bulk wrapper using the current profile; no retired study score or band."""
import html
import json
import re
from pathlib import Path


def page(session,source):
    # Preserve the existing bulk workspace's styling. Its old score/band table
    # cannot be populated from v9 profiles without inventing an aggregation.
    text=Path(source).read_text()
    start=text.index('def _bulk_runner_page(')
    css=text[text.index('<style>',start)+7:text.index('</style>',start)]
    css=css.replace('{{','{').replace('}}','}').replace('{_merit_analytics_consent_css()}','')
    value=json.dumps(session,ensure_ascii=False).replace('</','<\\/')
    return '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bulk MERIT-ML</title><style>'''+css+'''</style></head><body><main><section class="panel">
<h1>Bulk MERIT-ML</h1><p class="sub">Every analysis and data file is reported separately, with the classes, settings and sample edits selected for each study.</p>
<div class="actions"><a class="btn" href="/">Back to MERIT-ML UI</a><button id="summary" disabled>Download summary TSV</button>
<button id="metrics" disabled>Download metrics-long TSV</button><button id="session">Download session JSON</button></div>
<div class="progress-shell"><div id="progress-bar"></div></div><p id="status">Starting…</p>
<div id="input-review"></div><section id="study-reports" hidden><h2>Individual study reports</h2><p class="sub">Open a study in a new tab to change its classes, samples and settings. Each report starts with the settings used in this bulk run. Links remain available while the assessment data are retained on this server.</p><ul id="study-report-links"></ul></section><div style="overflow:auto"><table><thead><tr><th>Study</th><th>Analysis</th><th>Data file</th><th>Summary</th><th>Smallest class</th><th>Samples</th><th>Features</th></tr></thead><tbody id="rows"></tbody></table></div>
<p class="sub">No study-level score, band or endpoint priority is assigned.</p></section></main><script>
    const session='''+value+r''';let summaries=[],metrics=[];const reports=new Map();
for(const item of session.input_review||[]){if(item.status==='available')continue;const p=document.createElement('p');p.textContent=item.study_id+' · '+item.message;p.style.color=item.status==='embargoed'?'#996000':item.status==='unknown'?'#51656a':'#b42318';document.getElementById('input-review').appendChild(p);}
const enc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('\"','&quot;').replaceAll("'",'&#39;');
function download(name,data,type='application/json'){let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([data],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),500);}
function tsv(rows){if(!rows.length)return '';let keys=Object.keys(rows[0]);return keys.join('\t')+'\n'+rows.map(r=>keys.map(k=>String(r[k]??'').replace(/[\t\r\n]/g,' ')).join('\t')).join('\n');}
document.getElementById('session').onclick=()=>download('merit_bulk_session.json',JSON.stringify(session,null,2));
document.getElementById('summary').onclick=()=>download('merit_bulk_summary.tsv',tsv(summaries),'text/tab-separated-values');
document.getElementById('metrics').onclick=()=>download('merit_bulk_metrics.tsv',tsv(metrics),'text/tab-separated-values');
async function run(){for(let i=0;i<session.studies.length;i++){let one={...session,studies:[session.studies[i]]};
while(true){let form=new URLSearchParams({bulk_session:JSON.stringify(one)});let r=await fetch('/bulk/chunk',{method:'POST',headers:{'Accept':'application/json'},body:form});let x=await r.json();if(x.live_session_id){session.live_session_id=x.live_session_id;one.live_session_id=x.live_session_id;}
if(r.status===202){document.getElementById('status').textContent=(i+1)+' / '+session.studies.length+' · '+x.pending.map(j=>j.state).join(', ');await new Promise(r=>setTimeout(r,2000));continue;}
if(!r.ok||!x.ok){document.getElementById('status').textContent=x.error||'Bulk assessment failed';return;}
for(const report of x.reports||[]){if(/^ST[0-9]{6}$/.test(report.study_id)&&/^\/bulk\/report\/[a-f0-9]{32}$/.test(report.url))reports.set(report.study_id,report);}
const list=document.getElementById('study-report-links');list.replaceChildren();for(const report of reports.values()){const li=document.createElement('li'),a=document.createElement('a');a.href=report.url;a.target='_blank';a.rel='noopener noreferrer';a.style.textDecoration='underline';a.textContent=report.study_id+' — Open interactive report';li.appendChild(a);list.appendChild(li);}document.getElementById('study-reports').hidden=!reports.size;
summaries.push(...x.summary_rows);metrics.push(...x.metric_rows);break;}
document.getElementById('rows').innerHTML=summaries.map(r=>'<tr>'+['study_id','analysis_id','endpoint','summary','smallest_class','ml_eligible_sample_ids','features'].map(k=>'<td>'+(k==='study_id'&&reports.has(r.study_id)?'<a target="_blank" rel="noopener noreferrer" style="text-decoration:underline" href="'+enc(reports.get(r.study_id).url)+'">'+enc(r[k])+'</a>':enc(r[k]))+'</td>').join('')+'</tr>').join('');
document.getElementById('progress-bar').style.width=((i+1)/session.studies.length*100)+'%';}
document.getElementById('status').textContent='Complete · '+summaries.length+' matrices';document.getElementById('summary').disabled=false;document.getElementById('metrics').disabled=false;}run().catch(e=>{document.getElementById('status').textContent='Bulk assessment interrupted: '+e.message+'; completed rows are retained.';document.getElementById('summary').disabled=!summaries.length;document.getElementById('metrics').disabled=!metrics.length;});
</script></body></html>'''
