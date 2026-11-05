// MERIT-ML live tool: the calculation, in a Web Worker so the page never freezes. Messages from the page:
//   {cmd:'init', config}                 load Pyodide 0.29.5, the libraries, the tool's code and the shared reference files
//   {cmd:'prepare', sid, job}            public-study check, download from Metabolomics Workbench, estimate
//   {cmd:'compute', run}                 the calculation (online_assess.py, unchanged)
//   {cmd:'render', payload}              a page or a download (live_render.respond, unchanged)
// Large files are kept in the browser's Cache Storage (versioned URLs), so a second visit downloads almost nothing.
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.29.5/full/';
importScripts(PYODIDE + 'pyodide.js');
importScripts('checkpoints.js','downloads.js');
const HOME = '/home/shayantan/metabolomics/ML-ready/online-tool';
let py = null, cfg = null,currentJob=null;
const say = (type, value = {}) => postMessage({type, ...value});

async function cached(url) {
  const cache = await caches.open('merit-live-' + cfg.version);
  let r = await cache.match(url);
  if (!r) {
    r = await fetch(url);
    if (!r.ok) return null;
    await cache.put(url, r.clone());
  }
  return new Uint8Array(await r.arrayBuffer());
}

function write(path, bytes) {
  py.FS.mkdirTree(path.slice(0, path.lastIndexOf('/')));
  py.FS.writeFile(path, bytes);
}

async function init(config) {
  cfg = config;
  say('status', {text: 'Loading Python in your browser (first visit: about 45 MB, then kept by the browser)'});
  py = await loadPyodide({indexURL: PYODIDE,
    stdout: line => { try { const v = JSON.parse(line); if (v.stage === 'large_correlation') say('block', v); } catch (e) {} },
    stderr: () => {}});
  await py.loadPackage(['numpy', 'scipy', 'pandas', 'scikit-learn', 'lxml', 'ssl', 'sqlite3', 'micropip']);
  for (const w of cfg.wheels) {
    const name = w.split('/').pop(); write('/tmp/wheels/' + name, await cached(w));
    await py.pyimport('micropip').install('emfs:/tmp/wheels/' + name);
  }
  say('status', {text: 'Loading the MERIT-ML code'});
  write('/tmp/code.zip', await cached(cfg.code));
  py.runPython("import zipfile; zipfile.ZipFile('/tmp/code.zip').extractall('/')");
  say('status', {text: 'Loading the reference tables (RefMet)'});
  for (const f of cfg.reference_files) {
    const b = await cached(cfg.reference + f);
    if (!b) throw Error('Reference file not available: ' + f);
    write(HOME + '/' + cfg.reference_paths[f], b);
  }
  py.runPython(`import sys; sys.path.insert(0, '${HOME}/browser_app'); sys.path.insert(0, '${HOME}')
import browser_runtime as br; br.install()`);
  if(cfg.fixture_base){
    if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(cfg.fixture_base))throw Error('Fixtures require a loopback server');
    py.globals.set('_fixture_base',cfg.fixture_base);py.runPython('br.TRANSPORT_FIXTURE=_fixture_base');
  }
  if(config.resume_run){await studyReferences(config.resume_sid);await restoreRun(py,config.resume_run);}
  say('ready', {bench: py.runPython('import browser_runtime as br; br.benchmark()')});
}

async function studyReferences(sid) {
  // per-study reference files (absent for most studies; a missing file means no rows)
  for (const [dir, ext] of cfg.study_reference) {
    const name = (dir.startsWith('design_context') ? sid.toLowerCase() : sid) + ext;   // design-context files are lower case
    const index=JSON.parse(py.FS.readFile(HOME+'/assets/reference/study_reference_manifest.json',{encoding:'utf8'})).files;
    const rel=dir+'/'+name,expected=index[rel];if(!expected)continue;
    const b = await cached(cfg.reference + rel);
    if(!b)throw Error('Required versioned context reference unavailable: '+rel);
    const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',b))].map(x=>x.toString(16).padStart(2,'0')).join('');
    if(digest!==expected.sha256||b.byteLength!==expected.bytes)throw Error('Context reference checksum mismatch: '+rel);
    write(HOME + '/assets/reference/' + rel, b);
  }
}

self.onmessage = async ev => {
  const m = ev.data;
  try {
    if (m.cmd === 'init') return await init(m.config);
    const br = py.pyimport('browser_runtime');
    if (m.cmd === 'prepare') {
      say('status', {text: 'Checking that ' + m.sid + ' is public on Metabolomics Workbench'});
      const g = br.gate(m.sid).toJs({dict_converter: Object.fromEntries});
      if (!g.ok) return say('gate', {check: g});
      await studyReferences(g.study_id);
      say('status', {text: 'Downloading ' + g.study_id + ' from Metabolomics Workbench (large files can take a few minutes)'});
      const run = await fetchStudy(g.study_id, m.job);
      const est = JSON.parse(py.runPython(`import json, browser_runtime as br; json.dumps(br.estimate(${JSON.stringify(run)}, ${JSON.stringify(g.study_id)}, ${Number(self.navigator.deviceMemory) || 'None'}))`));
      return say('estimate', {run, sid: g.study_id, estimate: est});
    }
    if(m.cmd==='task-init'||m.cmd==='task-exec'||m.cmd==='task-fail'||m.cmd==='task-list'){
      py.globals.set('_task_run',m.run);py.globals.set('_task_json',JSON.stringify(m.task||{}));py.globals.set('_task_reason',m.reason||'');
      py.runPython('import browser_tasks as bt, json');
      if(m.cmd==='task-init')py.runPython('bt.initial(_task_run)');
      if(m.cmd==='task-exec'||m.cmd==='task-fail')py.runPython('bt.execute(_task_run,json.loads(_task_json),_task_reason or None)');
      await saveRun(py,m.run);
      const tasks=JSON.parse(py.runPython('json.dumps(bt.tasks(_task_run))'));
      const result=JSON.parse(py.runPython('json.dumps(bt.finish(_task_run))'));
      return say('task-result',{id:m.id,tasks,result,heap_bytes:py._module.HEAP8.byteLength});
    }
    if (m.cmd === 'export') {                  // the whole run folder (inputs, results, audit) as a ZIP, for provenance and checks
      const b64 = br.export_b64(m.run);           // JSON {ok, data_b64} or {ok:false, error} above the size limit
      return say('exported', {id: m.id, data_b64: b64});
    }
    if (m.cmd === 'render') {
      const r = JSON.parse(br.respond(JSON.stringify(m.payload)));
      return say('rendered', {id: m.id, result: r});
    }
    if(m.cmd==='catalog'){
      const catalogPath=m.run+'/public_catalog.json';
      const existing=JSON.parse(py.FS.readFile(catalogPath,{encoding:'utf8'}));
      if(!existing.some(x=>x.study_title||x.title)){
        currentJob=m.run.split('/').pop();
        const response=await downloadInput('https://www.metabolomicsworkbench.org/rest/study/study_id/ST/summary','catalog/all_study_summaries.json');
        if(response.blob&&!response.entry.error){
          py.globals.set('_catalog_text',await response.blob.text());
          try{
            const text=py.runPython('import live_catalog,json;json.dumps(live_catalog.normalize(json.loads(_catalog_text)))');
            write(catalogPath,new TextEncoder().encode(text));
          }catch(e){say('status',{text:'Study IDs are available; full discovery metadata could not be parsed.'});}
        }
        await saveRun(py,m.run);
      }
      const ans=py.runPython(`import live_catalog,browser_runtime,json
from pathlib import Path
_rows=json.loads((Path(${JSON.stringify(m.run)})/'public_catalog.json').read_text())
json.dumps(live_catalog.payload(_rows if any('title' in r for r in _rows) else live_catalog.normalize(_rows),json.loads(${JSON.stringify(JSON.stringify(m.query||{}))})))`);
      return say('catalog-result',{id:m.id,result:JSON.parse(ans)});
    }
  } catch (e) {
    const full = String(e && e.message || e);
    const lines = full.trim().split('\n').filter(x => x.trim());
    say('error', {cmd: m.cmd, id: m.id, text: (lines[lines.length - 1] || 'unknown error').slice(0, 400), details: full.slice(-4000)});
  }
};
