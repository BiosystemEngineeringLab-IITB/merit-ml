// MERIT-ML Live, page side. The calculation runs in worker.js; this page shows progress, the estimate, the report, and sends the
// report's forms and downloads (built for the 8791 server) to the worker instead of a server.
const $ = id => document.getElementById(id);
const STAGES = [['bootstrap', 'Reading the files', .03], ['metadata', 'Study metadata', .007], ['controls', 'QC and blank samples', .001],
  ['missingness', 'Missing entries and zeros', .015], ['scale', 'Measurement scale', .11], ['low_information', 'Low-information features', .10],
  ['redundancy', 'Redundant features', .39], ['outliers', 'Outlier samples', .08], ['qc_reproducibility', 'QC reproducibility', .03],
  ['annotation', 'Annotation', .03], ['refmet_lookup', 'RefMet names', .001], ['labels', 'Class labels', .001],
  ['separability', 'Class separability', .09], ['extra_evidence_and_readiness', 'FAIR evidence and readiness', .025],
  ['live_context_and_indexes', 'Study summary', .08]];
const TOTAL = STAGES.reduce((s, x) => s + x[2], 0);
let worker, config, job = null, run = null, sid = null, est = null, t0 = 0, done = new Set(), now = null, block = 0, tick = null, wake = null,
  finished = null,                // seconds the finished calculation took; the bar then shows 100% and stops
  fetchedAt = null;               // when the download from Metabolomics Workbench finished (UTC), shown on the report as on 8791
const pending = new Map(); let nextId = 1,readyWaiter=null,autoStarted=false,usedStudy=false;
const fmt = s => s < 90 ? Math.round(s) + ' s' : s < 5400 ? Math.round(s / 60) + ' min' : (s / 3600).toFixed(1) + ' h';

async function boot() {
  config = await (await fetch('config.json', {cache: 'no-store'})).json();
  startWorker();
  $('start').onsubmit = e => { e.preventDefault(); begin($('sid').value.trim().toUpperCase()); };
  $('cancel').onclick = cancel;
  $('startCalc').onclick = compute;
  const q = new URLSearchParams(location.search).get('study_id');
  if (q) $('sid').value = q;
}

function startWorker(resume=false) {
  const currentWorker = new Worker('worker.js');worker=currentWorker;
  worker.onmessage = e=>{if(worker===currentWorker)onMessage(e);};
  worker.onerror = e => {if(worker!==currentWorker)return;const text='The calculation worker stopped: '+(e.message||'memory or browser resource limit');
    if(pending.size){for(const p of pending.values())p.reject(Error(text));pending.clear();}else fail(text);};
  $('go').disabled = true; $('engine').textContent = 'Loading…';
  worker.postMessage({cmd: 'init', config:{...config,...(resume?{resume_run:run,resume_sid:sid}:{})}});
}

function onMessage(ev) {
  const m = ev.data;
  if (m.type === 'status') { show(m.text); if (!job) $('engine').textContent = m.text; }
  if (m.type === 'ready') { $('go').disabled = !!job&&!finished; $('engine').textContent = 'Ready';if(readyWaiter){readyWaiter();readyWaiter=null;return;}
    if(!autoStarted&&new URLSearchParams(location.search).get('study_id')){autoStarted=true;$('start').requestSubmit();} }
  if (m.type === 'gate') return gate(m.check);
  if (m.type === 'estimate') return estimate(m);
  if (m.type === 'stage') return stage(m.record);
  if (m.type === 'block') { block = m.blocks ? m.blocks_done / m.blocks : 0; return paint(); }
  if (m.type === 'computed') return computed(m);
  if(m.type==='task-result'||m.type==='catalog-result'){const p=pending.get(m.id);pending.delete(m.id);return p&&p.resolve(m);}
  if(m.type==='active'){lastActive=m.task;return;}
  if (m.type === 'exported') { const p = pending.get(m.id); pending.delete(m.id); return p && p.resolve(m.data_b64); }
  if (m.type === 'rendered') { const p = pending.get(m.id); pending.delete(m.id); return p && p.resolve(m.result); }
  if (m.type === 'error') {
    if (m.id && pending.has(m.id)) { pending.get(m.id).reject(Error(m.text)); pending.delete(m.id); return; }
    return fail('The assessment could not be completed: ' + m.text, m.details);
  }
}

function show(text, cls = '') { $('status').hidden = false; $('statusText').className = cls; $('statusText').textContent = text; }

function begin(id) {
  if (!id) return;
  for(const p of pending.values())p.reject(Error('Replaced by another study'));pending.clear();clearInterval(tick);
  sid = id;                       // any text goes to the public-study check, which explains an ID that is not a public Workbench study
  job = 'job-' + new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14) + '-' + Math.random().toString(16).slice(2, 10);
  done = new Set(); now = null; block = 0; est = null; run = null; finished = null; fetchedAt = null;
  $('report').hidden = true; $('estimate').hidden = true; $('progress').hidden = true; $('startCalc').hidden = true;
  $('cancel').hidden = false; $('go').disabled = true;
  history.replaceState(null, '', '?study_id=' + id);
  // A new study always receives a new heap; previous studies cannot accumulate in WASM memory.
  if(usedStudy){worker.terminate();readyWaiter=()=>worker.postMessage({cmd:'prepare',sid:id,job});startWorker();}
  else worker.postMessage({cmd:'prepare',sid:id,job});
  usedStudy=true;
}

function gate(c) {
  $('cancel').hidden = true; $('go').disabled = false;
  show(c.message || 'This study cannot be assessed.', 'bad');
  if (c.latest_public_study || c.latest) {
    const latest = c.latest_public_study || c.latest;
    $('estimate').hidden = false;
    $('estimate').innerHTML = `<p>Opening the most recent public study, <b>${latest}</b>, in 8 seconds. <button class="secondary" id="stay">Stay here</button></p>`;
    const t = setTimeout(() => { $('sid').value = latest; begin(latest); }, 8000);
    $('stay').onclick = () => { clearTimeout(t); $('estimate').hidden = true; };
  }
}

const LOCAL = 'the full MERIT-ML application, downloaded and run on a computer with more memory';
const gb = b => b >= 2 ** 30 ? (b / 2 ** 30).toFixed(1) + ' GB' : Math.round(b / 2 ** 20) + ' MB';
const esc = t => String(t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
function localApp() { return config.local_app_url ? `<a href="${esc(config.local_app_url)}" target="_blank" rel="noopener">${LOCAL}</a>` : LOCAL; }

function estimate(m) {
  run = m.run; sid = m.sid; est = m.estimate; fetchedAt = new Date().toISOString().slice(0, 19) + '+00:00';
  const budget = est.budget_mb * 2 ** 20;
  const rows = est.matrices.map(x => {
    const here = x.browser === 'assessed';
    const status = !here ? `<span class="bad">not assessed in the browser · ${esc(x.reason||('estimated memory '+gb(x.need_bytes)))}</span>`
      : !x.features || !x.positions ? '<span class="muted">no values</span>'
      : here ? 'assessed here'
      : `<span class="bad">not assessed in the browser · needs ${x.need_is_minimum ? 'at least' : 'about'} ${gb(x.need_bytes)} (mostly ${esc(x.largest_part)})</span>`;
    const dims=x.transfer_limit?'dimensions not read (input safety limit)':`${x.positions.toLocaleString()} positions × ${x.features.toLocaleString()} features`;
    return `<tr><td>${x.analysis_id}</td><td>${x.endpoint}</td><td>${dims}</td><td>${status}</td></tr>`;
  }).join('');
  const skipped = est.not_assessed || [];
  const limits = `This version admits calculations estimated to fit a ${gb(budget)} memory budget; this is an estimate, not a guarantee.
    Memory need grows with positions × features, with the number of feature pairs (features²) and with
    the number of position pairs (positions²).`;
  const skipText = skipped.length ? `<p class="warn" style="margin-top:6px"><b>${skipped.length} of ${est.matrices.length} matrices are too large for the browser version</b>
    and will be listed as not assessed; the other matrices of ${sid} are assessed as usual. ${limits}
    To assess the large matrices, use ${localApp()}.</p>` : '';
  $('estimate').hidden = false; $('cancel').hidden = true;
  if (est.too_large) {
    $('go').disabled = false;
    $('estimate').innerHTML = `<p style="margin:0">Downloaded <b>${sid}</b>: ${est.matrices.length} matrices (a position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block).</p>
      <table class="est">${rows}</table>
      <p class="bad">None of the matrices of ${sid} fits the memory a browser tab can use. ${limits}</p>
      <p>To assess ${sid}, use ${localApp()}.</p>`;
    $('startCalc').hidden=false;$('go').disabled=false;
    return show(sid + ': only metadata and context can be assessed here. The oversized matrices will be listed as not assessed.', 'warn');
  }
  const long = est.seconds_mid > 900;
  $('estimate').innerHTML = `<p style="margin:0">Downloaded <b>${sid}</b>: ${est.matrices.length} matrices (a position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block).</p>
    <table class="est">${rows}</table>${skipText}
    <p>Estimated time on this device: <b>${fmt(est.seconds_low)} to ${fmt(est.seconds_high)}</b> (most likely about ${fmt(est.seconds_mid)}).</p>
    ${long ? '<p class="warn">This is a long calculation. Keep this tab open and the computer awake; you can cancel at any time.</p>' : ''}`;
  $('cancel').hidden = false;
  show('Ready to calculate ' + sid + '.');
  if (long || skipped.length) { $('startCalc').hidden = false; $('cancel').hidden = true; $('go').disabled = false; } else compute();
}

async function compute() {
  const computingJob=job;
  $('startCalc').hidden = true; $('progress').hidden = false;
  $('stages').innerHTML = STAGES.map(s => `<li data-k="${s[0]}">${s[1]}</li>`).join('');
  t0 = performance.now(); finished = null; tick = setInterval(paint, 1000);
  try { wake = await navigator.wakeLock?.request('screen'); } catch (e) {}
  show('Calculating ' + sid + ' in your browser…');
  try{
    let answer=await rpc('task-init',{run}),previous=null;
    while(answer.tasks.length&&job===computingJob){
      const task=answer.tasks[0],scope=task.analysis+':'+task.endpoint;
      // Reset the calculation heap between matrices. Files are restored as read-only Blobs.
      if(previous!==null&&previous!==scope&&answer.heap_bytes>(est.budget_mb*2**20*.8))await recycle();
      previous=scope;
      try{answer=await rpc('task-exec',{run,task});}
      catch(e){
        if(job!==computingJob)return;
        show('Restoring completed results; marking only the interrupted check as not assessed…','warn');
        await recycle();answer=await rpc('task-fail',{run,task,reason:'Browser worker stopped before this check completed: '+e.message});
      }
    }
    if(job!==computingJob)return;
    if(answer.heap_bytes>(est.budget_mb*2**20*.8))await recycle();
    await computed({result:answer.result,seconds:(performance.now()-t0)/1000});
  }catch(e){if(job===computingJob)fail('The assessment could not continue: '+e.message+'. Completed checkpoints remain on this device.');}
}

function rpc(cmd,fields={}){const id=nextId++;return new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>{pending.delete(id);worker.terminate();reject(Error('This request exceeded the browser time limit (30 minutes)'));},1800000);
  pending.set(id,{resolve:x=>{clearTimeout(timer);resolve(x);},reject:e=>{clearTimeout(timer);reject(e);}});worker.postMessage({cmd,id,...fields});
});}
function recycle(){worker.terminate();return new Promise((resolve,reject)=>{readyWaiter=resolve;startWorker(true);setTimeout(()=>{if(readyWaiter===resolve){readyWaiter=null;reject(Error('The browser engine could not restart'));}},180000);});}

function stage(r) {
  if (r.state === 'started') now = r.stage;
  if (r.state === 'done' || r.state === 'failed'||r.state==='not_assessed') { done.add(r.stage); if (now === r.stage) now = null; block = 0; }
  paint();
}

function progress() {
  let p = 0;
  for (const [k, , w] of STAGES) if (done.has(k)) p += w;
  if (now) { const w = (STAGES.find(s => s[0] === now) || [0, 0, 0])[2]; p += w * (now === 'redundancy' ? block : 0.3); }
  return Math.min(p / TOTAL, 0.99);
}

function paint() {
  if ($('progress').hidden) return;
  if (finished !== null) {        // the run is over: full bar, the time it took, every step ticked
    $('barFill').style.width = '100%';
    $('timing').textContent = `100% · finished in ${fmt(finished)} · ${STAGES.length} assessment steps finished; unassessed checks are listed in the report`;
  } else {
    const p = progress(), el = (performance.now() - t0) / 1000;
    const left = p > 0.15 ? el / p * (1 - p) : Math.max((est?.seconds_mid || 60) - el, 0);
    $('barFill').style.width = (100 * p).toFixed(1) + '%';
    $('timing').textContent = `${Math.round(100 * p)}% · ${fmt(el)} elapsed · about ${fmt(left)} left`;
  }
  for (const li of $('stages').children) li.className = done.has(li.dataset.k) ? 'done' : li.dataset.k === now ? 'now' : '';
}

async function computed(m) {
  clearInterval(tick); try { await wake?.release(); } catch (e) {}
  now = null;
  done = new Set(STAGES.map(s => s[0])); finished = m.seconds; paint();
  const left = (m.result.not_assessed || []).length;
  show(`${sid} assessment finished in ${fmt(m.seconds)} on this device.` + (left ? ` ${left} ${left===1?'matrix is':'matrices are'} listed as not assessed; successful results are retained.` : ''), left ? 'warn' : '');
  $('cancel').hidden = true; $('go').disabled = false;
  await route('/workflow/run', {});
}

function cancel() {
  worker.terminate(); clearInterval(tick); for (const p of pending.values()) p.reject(Error('cancelled')); pending.clear();
  show('Cancelled. Temporary checkpoints can be cleared by starting another study or clearing this site’s browser data.'); $('cancel').hidden = true; $('progress').hidden = true; $('startCalc').hidden = true; job = null;usedStudy=false;
  startWorker();
}

function fail(text, details) {
  clearInterval(tick); $('cancel').hidden = true; $('go').disabled = false;
  show(text, 'bad');
  if (details) { const d = document.createElement('details'); d.innerHTML = '<summary class="muted">Technical details</summary><pre style="white-space:pre-wrap;font-size:.75rem"></pre>';
    d.querySelector('pre').textContent = details; $('statusText').appendChild(d); }
}

let lastActive=null;
async function render(action, form) {
  const renderingJob=job;
  lastActive=null;
  const payload={run,study_id:sid,action,form,job_id:job,retrieved_utc:fetchedAt};
  const excluded=[];
  for(;;){
    try{return await rpc('render',{payload:{...payload,browser_exclude:excluded}});}
    catch(e){
      if(job!==renderingJob)throw e;
      if(!lastActive?.function)throw e;
      const key=[lastActive.function,lastActive.analysis,lastActive.endpoint];
      if(excluded.some(x=>x.join(':')===key.join(':')))throw e;
      excluded.push(key);await recycle();lastActive=null;
    }
  }
}

// The run record (inputs, results, audit of every file read), as a ZIP in base64; used by the checks and for provenance.
window.__meritExport = () => new Promise((resolve, reject) => { const id = nextId++; pending.set(id, {resolve, reject}); worker.postMessage({cmd: 'export', id, run}); });
window.__meritState = () => ({sid, run, job, done: [...done], now, estimate: est, status: $('statusText').textContent, report: !$('report').hidden});

const bytesOf = b64 => { const s = atob(b64), u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u; };
const DOWNLOADS = {'/download/feature-list': 'feature-list', '/download/v9-matrix': 'matrix', '/download/ml-ready-data': 'data',
                   '/download/ml-ready-matrix': 'ml-ready-matrix', '/download/acquisition-evidence': 'acquisition'};

async function route(path, fields) {
  const routingJob=job;
  try {
    if (path === '/' && fields.study_id && fields.study_id.toUpperCase() !== sid) { $('sid').value = fields.study_id; return begin(fields.study_id.toUpperCase()); }
    if (path === '/workflow/run' || path === '/') {
      const r = await render('page', fields);
      if(job!==routingJob)return;
      if (!r.ok) return show('The page could not be built: ' + (r.error || 'unknown error').split('\n').filter(x => x.trim()).pop(), 'bad');
      const html = new TextDecoder().decode(bytesOf(r.data_b64));
      $('report').srcdoc = bridge(notice(html)); $('report').hidden = false;
      return;
    }
    if (path.startsWith('/api/provenance/')) {          // the run record: inputs as downloaded, every result, the audit of files read
      show('Preparing the run record…');
      const ans = JSON.parse(await window.__meritExport());
      if (!ans.ok) return show(ans.error, 'warn');
      const b64 = ans.data_b64;
      const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([bytesOf(b64)], {type: 'application/zip'}));
      a.download = `${sid}_run_record_${job}.zip`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      return show(`${sid}: run record downloaded (retained inputs, completed results and provenance; omitted or refused response bodies are recorded with their reasons).`);
    }
    if (DOWNLOADS[path]) {
      show('Preparing the download from your settings…');
      const r = await render(DOWNLOADS[path], fields);
      if (!r.ok) return show(r.too_large ? r.error : 'The download could not be built: ' + (r.error || 'unknown error').split('\n').filter(x => x.trim()).pop(), r.too_large ? 'warn' : 'bad');
      const blob = new Blob([bytesOf(r.data_b64)], {type: r.mime});
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = r.filename || 'download'; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      return show(`${r.filename} downloaded (built from the current settings on this device).`);
    }
    show('This part of the report needs the MERIT-ML server and is not available in the browser version: ' + path, 'warn');
  } catch (e) { if(job===routingJob)show('The request could not be completed: ' + e.message, 'bad'); }
}

// Injected first into the report page: forms, requested submissions, server links and the two fetch endpoints go to the page above.
const BRIDGE = `<script>(()=>{const up=(path,fields)=>parent.postMessage({merit:'route',path,fields},'*');let seq=0;const waiting=new Map();
window.addEventListener('message',e=>{if(e.source!==parent||!e.data||e.data.merit!=='fetch-result')return;const p=waiting.get(e.data.id);if(p){waiting.delete(e.data.id);p(new Response(JSON.stringify(e.data.result),{status:e.data.status||200,headers:{'Content-Type':'application/json'}}));}});
const send=(f,sub)=>{const fd=new FormData(f);if(sub&&sub.name)fd.append(sub.name,sub.value);const o={};for(const[k,v]of fd.entries())if(typeof v==='string')o[k]=v;
 up(new URL(f.getAttribute('action')||'/workflow/run','http://x').pathname,o);};
document.addEventListener('submit',e=>{e.preventDefault();send(e.target,e.submitter);},true);
HTMLFormElement.prototype.submit=function(){send(this);};
HTMLFormElement.prototype.requestSubmit=function(s){const ev=new SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:s||null});if(this.dispatchEvent(ev))send(this,s);};
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('a[href^="/"]');if(!a)return;e.preventDefault();
 const u=new URL(a.getAttribute('href'),'http://x');up(u.pathname,Object.fromEntries(u.searchParams));},true);
const f0=window.fetch;window.fetch=(u,o)=>{const s=String(u);if(s.startsWith('/api/visit-stats'))return Promise.resolve(new Response('{"ok":false}',{headers:{'Content-Type':'application/json'}}));
 if(s.startsWith('/study-browser-data'))return new Promise(resolve=>{const id=++seq;waiting.set(id,resolve);parent.postMessage({merit:'catalog',id,query:Object.fromEntries(new URL(s,'http://x').searchParams)},'*');});
 if(s.startsWith('/'))return Promise.resolve(new Response(JSON.stringify({error:'not available in the browser version'}),{status:404,headers:{'Content-Type':'application/json'}}));return f0(u,o);};
})();<\/script>`;
// Matrices the browser did not assess (memory guard): one notice under the top line of the report, with sizes and reason.
function notice(html) {
  const skipped = (est && est.not_assessed) || [];
  if (!skipped.length) return html;
  const items = skipped.map(x => `<li><b>${x.analysis_id} · ${x.endpoint}</b>: ${x.transfer_limit?esc(x.reason):`${x.positions.toLocaleString()} positions × ${x.features.toLocaleString()} features, ${x.need_is_minimum ? 'at least' : 'about'} ${gb(x.need_bytes)} of memory needed (mostly ${esc(x.largest_part)})`}</li>`).join('');
  const box = `<div id='browser-not-assessed' style='padding:8px 18px;background:#fff4e5;color:#5a3b00;font:13px sans-serif;border-bottom:1px solid #f0d2a8'>
    <b>Browser memory limit: ${skipped.map(x=>esc(x.analysis_id+' · '+x.endpoint)).join('; ')} could not be assessed.</b>
    <p style='margin:5px 0'>Their empty results do not mean the data are absent. These tables were skipped because of browser memory limits. Other matrices are assessed below. Use the <a href='https://github.com/BiosystemEngineeringLab-IITB/merit-ml' target='_blank' rel='noopener'>full MERIT-ML application on GitHub</a> to assess them.</p>
    <details><summary style='cursor:pointer'>Memory estimates for ${skipped.length} of ${est.matrices.length} matrices</summary>
    <ul style='margin:6px 0 4px 18px;padding:0'>${items}</ul>
    <div>A position is one sample row of a datatable or untarg_data file, or one sample column of an mwTab data block. They appear in the report as “not assessed in the browser version”. To assess them, use ${localApp()}.</div></details></div>`;
  const i = html.indexOf("<div id='online-provenance'");
  if (i < 0) return html.replace(/(<body[^>]*>)/i, m => m + box);
  const j = html.indexOf('</div>', i) + 6;
  return html.slice(0, j) + box + html.slice(j);
}

function bridge(html) { return html.replace(/<head[^>]*>/i, m => m + BRIDGE).split('/assets/logo.png').join('Logo.png'); }   // logo served next to the page
window.addEventListener('message', async e => { if(e.source!==$('report').contentWindow||!e.data)return;
  if(e.data.merit==='route')return route(e.data.path,e.data.fields||{});
  if(e.data.merit==='catalog'){try{const a=await rpc('catalog',{query:e.data.query,run});e.source.postMessage({merit:'fetch-result',id:e.data.id,result:a.result},'*');}
    catch(error){e.source.postMessage({merit:'fetch-result',id:e.data.id,status:503,result:{error:error.message}},'*');}}
});

boot();
