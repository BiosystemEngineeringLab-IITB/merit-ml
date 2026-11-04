"""Read-only, auto-refreshing status for the explicitly requested cohort run."""
import json
from online_pull_common import RUN_ID


def page(run_id):
    if not RUN_ID.fullmatch(run_id):raise ValueError('Invalid online-pull run ID')
    return r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MERIT-ML · Online pull</title>
<style>body{font:15px sans-serif;background:#f4f1e9;color:#132327;margin:0;padding:28px}main{max-width:1000px;margin:auto;padding:28px;background:white;border:1px solid #dbe3e3;border-radius:20px}h1{font-size:24px}.track{height:14px;border-radius:999px;overflow:hidden;background:#e6eded;margin:18px 0}.fill{height:100%;background:#0d6e6e;transition:width .4s}.stats{display:flex;flex-wrap:wrap;gap:24px;color:#51656a}#count{font-size:38px;font-weight:700;color:#0d6e6e}#detail{white-space:pre-wrap;color:#51656a;line-height:1.7}a{color:#0d6e6e}</style></head>
<body><main><a href="/">MERIT-ML</a><h1>Online pull and metric calculation</h1><p id="run"></p><p id="cohort"></p><div id="count">Starting…</div><div class="track" role="progressbar" aria-label="Studies completed"><div class="fill" style="width:0"></div></div><div class="stats" id="stats"></div><h2 id="phase" style="font-size:18px"></h2><p id="detail"></p><p id="error" style="color:#9b2335"></p><p id="policy" style="font-size:13px;color:#6f7e83">All study inputs are fetched online.</p><h2 style="font-size:18px">Studies and saved assessments</h2><p>Completed links open the locally calculated assessment; they do not start another pull.</p><p><a id="dates">Download deposition-date provenance</a></p><label>Find a study <input id="filter" placeholder="ST005313"></label><table style="width:100%;border-collapse:collapse;margin-top:12px;text-align:left"><thead><tr><th>Study</th><th>Submitted</th><th>Status</th><th>Matrices</th><th>Assessment</th></tr></thead><tbody id="studies"></tbody></table></main>
<script>const id=__ID__;document.getElementById('run').textContent=id;
document.getElementById('dates').href='/api/online-pull/'+id+'/deposition-dates';let rows=[];
function table(){const target=document.getElementById('studies');target.replaceChildren();const q=document.getElementById('filter').value.trim().toUpperCase();
for(const row of rows.filter(x=>!q||x.study_id.includes(q))){const tr=document.createElement('tr');for(const key of ['study_id','submission_date','state','matrices']){const td=document.createElement('td');td.style.padding='6px 0';td.textContent=row[key]??'—';tr.appendChild(td);}const td=document.createElement('td');if(row.url){const a=document.createElement('a');a.href=row.url;a.textContent='Open';a.target='_blank';td.appendChild(a);}tr.appendChild(td);target.appendChild(tr);}}
document.getElementById('filter').addEventListener('input',table);
async function poll(){try{const r=await fetch('/api/online-pull/'+id,{cache:'no-store'});if(!r.ok)throw Error('Status not available yet');const s=await r.json();
document.getElementById('count').textContent=s.completed+' / '+s.total+' studies complete';document.querySelector('.fill').style.width=(100*s.completed/Math.max(s.total,1))+'%';
document.getElementById('cohort').textContent=s.cohort_description||'';document.getElementById('policy').textContent=s.reference_policy||'Study inputs are fetched online; all outputs and input provenance are retained.';
document.getElementById('stats').textContent='Failed: '+s.failed+' · Pending: '+s.pending+' · Matrices stored: '+s.matrices+' · Free disk: '+(s.free_disk_gib||'?')+' GiB';
document.getElementById('phase').textContent=s.phase_label||s.phase;const c=s.current||{};
document.getElementById('detail').textContent=(c.study_id?'Study '+c.ordinal+' / '+s.total+': '+c.study_id+'\n':'')+(c.label||'')+(c.detail?'\n'+c.detail:'')+'\nComparison: '+(s.comparison_state||'queued')+'\nUpdated: '+s.updated_at_utc;
document.getElementById('error').textContent=s.error||'';
const rr=await fetch('/api/online-pull/'+id+'/studies',{cache:'no-store'});if(rr.ok){rows=(await rr.json()).studies;table();}
}catch(e){document.getElementById('error').textContent='Reconnecting to status…';}setTimeout(poll,5000);}poll();</script></body></html>'''.replace('__ID__',json.dumps(run_id))
