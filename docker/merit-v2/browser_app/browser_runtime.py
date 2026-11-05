"""The live tool in the user's browser (Pyodide, in a Web Worker). Runs the same steps as one 8791 job, with the same code:
  1. public-study gate      live_inventory.Inventory.check (Workbench list of public studies, embargo dates)
  2. download               fetch_online.py main (--with-panredu), unchanged; only the transport is the browser's
  3. estimate               matrix sizes of the downloaded files x this device's measured speed
  4. calculation            online_assess.py main, unchanged
  5. pages and downloads    live_render.respond, unchanged
Browser-specific adjustments (none changes a calculation):
  - urllib requests go through synchronous XMLHttpRequest (allowed in a worker); forbidden headers (User-Agent, Accept-Encoding)
    are left to the browser; a read timeout raises TimeoutError as urllib does;
  - thread pools run their tasks one after the other (a worker has no threads);
  - os.link raises OSError (no hard links), so the code's own fallback copies the file;
  - lxml.etree and openpyxl are imported before the calculation changes sys.path (a Pyodide import-hook issue).
Progress: every line written to assessment_progress.jsonl and every 'large_correlation' line is sent to the page as it happens.
"""
from __future__ import annotations

import concurrent.futures
import io
import json
import os
import runpy
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import js                              # Pyodide: the worker's JavaScript globals
from pyodide.ffi import to_js

HOME = Path('/home/shayantan/metabolomics/ML-ready/online-tool')
RUNS = HOME / 'runs' / 'browser'
FORBIDDEN = {'user-agent', 'accept-encoding', 'content-length', 'host', 'connection', 'referer', 'origin', 'cookie'}
TRANSPORT_FIXTURE = None  # Local validation builds only; absent from production config.


def post(kind: str, **value):
    send = getattr(js, 'postMessage', None)          # absent outside a worker (the server-side check runs the same code in Node)
    if send is not None:
        send(to_js({'type': kind, **value}, dict_converter=js.Object.fromEntries))


# ---------- transport: urllib -> synchronous XMLHttpRequest -------------------------------------------------------------------
class _Response(io.BytesIO):
    def __init__(self, status, headers, body, url):
        super().__init__(body); self.status = status; self.code = status; self.headers = headers; self.url = url

    def geturl(self): return self.url
    def getcode(self): return self.status


def _xhr(req: urllib.request.Request, timeout=None) -> _Response:
    x = js.XMLHttpRequest.new()
    url=req.full_url
    if TRANSPORT_FIXTURE and url.startswith('https://www.metabolomicsworkbench.org/'):
        url=TRANSPORT_FIXTURE.rstrip('/')+'/'+url.split('.org/',1)[1]
    x.open(req.get_method(), url, False)
    x.responseType = 'arraybuffer'
    if timeout: x.timeout = int(float(timeout) * 1000)
    for k, v in req.header_items():
        if k.lower() not in FORBIDDEN: x.setRequestHeader(k, v)
    try:
        x.send(to_js(req.data) if req.data else None)
    except Exception as exc:                                   # DOMException: TimeoutError / NetworkError
        if 'Timeout' in str(exc) or 'timed out' in str(exc).lower(): raise TimeoutError('The read operation timed out') from None
        raise urllib.error.URLError(str(exc)) from None
    if x.status == 0:
        raise urllib.error.URLError('network error (the browser blocked or could not complete the request)')
    body = bytes(js.Uint8Array.new(x.response).to_py()) if x.response else b''
    headers = {}
    for line in str(x.getAllResponseHeaders() or '').split('\r\n'):
        if ': ' in line: k, v = line.split(': ', 1); headers[k] = v
    if x.status >= 400:
        raise urllib.error.HTTPError(req.full_url, x.status, str(x.statusText), headers, io.BytesIO(body))
    return _Response(x.status, headers, body, str(x.responseURL or req.full_url))


class _Opener:
    def open(self, req, data=None, timeout=None):
        if isinstance(req, str): req = urllib.request.Request(req, data=data)
        return _xhr(req, timeout)


class _Serial:
    """ThreadPoolExecutor stand-in: the tasks run one after the other, in order."""
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def map(self, fn, *its, **k): return [fn(*x) for x in zip(*its)]
    def submit(self, fn, *a, **k):
        f = concurrent.futures.Future()
        try: f.set_result(fn(*a, **k))
        except BaseException as e: f.set_exception(e)
        return f
    def shutdown(self, *a, **k): pass


def _no_link(*a, **k):
    raise OSError('hard links are not available in the browser')


def _guard_import_hook():
    """Pyodide 0.29.5's WindowsToLinuxPathFinder runs a regex on every sys.path entry without checking that it is a string, so a
    non-string entry (None) makes every later first-time import fail with TypeError; CPython's own PathFinder skips such entries.
    Replace it in sys.meta_path by the same finder given only the string entries (seen in pandas' optional zstandard import
    inside DataFrame.to_csv, 11 studies of the cohort run on 4 Nov 2026)."""
    try:
        from _pyodide._importhook import WindowsToLinuxPathFinder as W
    except ImportError:
        return
    class Guarded:
        @classmethod
        def find_spec(cls, fullname, path=None, target=None):
            entries = [p for p in (path if path is not None else sys.path) if isinstance(p, str)]
            return W.find_spec(fullname, entries, target)
    sys.meta_path[:] = [Guarded if f is W else f for f in sys.meta_path]


def install():
    _guard_import_hook()
    urllib.request.build_opener = lambda *handlers: _Opener()
    urllib.request.urlopen = lambda req, data=None, timeout=None, **k: _Opener().open(req, data, timeout)
    concurrent.futures.ThreadPoolExecutor = _Serial
    os.link = _no_link
    import lxml.etree, openpyxl  # noqa: F401
    sys.path.insert(0, str(HOME)); os.chdir(HOME)
    _hook_progress()


def _hook_progress():
    """Send every new line of assessment_progress.jsonl to the page."""
    original = Path.open
    def open_(self, mode='r', *a, **k):
        f = original(self, mode, *a, **k)
        if self.name == 'assessment_progress.jsonl' and 'a' in mode:
            write = f.write
            def w(text):
                for line in str(text).splitlines():
                    try: rec = json.loads(line)
                    except ValueError: continue
                    if rec.get('stage') == 'bootstrap' and rec.get('state') == 'done': _apply_skips(self.parent, rec.get('study_id'))
                    post('stage', record=rec)
                return write(text)
            f.write = w
        return f
    Path.open = open_


# ---------- steps ------------------------------------------------------------------------------------------------------------
_INVENTORY = None


def gate(raw: str) -> dict:
    global _INVENTORY
    import live_inventory
    if _INVENTORY is None:
        _INVENTORY = live_inventory.Inventory(); _INVENTORY.refresh()
    return _INVENTORY.check(raw)


def fetch(sid: str, job: str) -> str:
    run = RUNS / job; run.mkdir(parents=True, exist_ok=True)
    argv = sys.argv
    sys.argv = ['fetch_online.py', '--run-root', str(run), '--studies', sid, '--with-panredu','--allow-partial']
    try: runpy.run_path(str(HOME / 'fetch_online.py'), run_name='__main__')
    finally: sys.argv = argv
    return str(run)


def matrix_sizes(run: str, sid: str) -> list[dict]:
    """Rows x columns of every downloaded matrix, from the files (header and line counts), before any calculation."""
    out = []; root = Path(run) / 'inputs' / sid
    for d in sorted(p for p in root.glob('AN*') if p.is_dir()):
        for name, kind in ((f'tabular/{d.name}_datatable_slash.txt', 'datatable'), (f'tabular/{d.name}_untarg_data.tsv', 'untarg_data')):
            p = d / name
            if p.is_file() and p.stat().st_size:
                with p.open('rb') as fh:
                    head = fh.readline(); rows = sum(1 for _ in fh)
                out.append({'analysis_id': d.name, 'endpoint': kind, 'positions': rows, 'features': max(head.count(b'\t') - 1, 0), 'bytes': p.stat().st_size})
        p = d / 'mwtab' / f'{d.name}_mwtab.txt'
        if p.is_file():
            active=False;cols=0;nf=0;size=0;header=False
            with p.open('rb') as fh:
                for line in fh:
                    if b'_DATA_START' in line:active=True;header=True;continue
                    if active and b'_DATA_END' in line:break
                    if not active:continue
                    size+=len(line)
                    if header:cols=line.count(b'\t');header=False
                    elif line.strip():nf+=1
            if active:out.append({'analysis_id':d.name,'endpoint':'mwtab','positions':cols,'features':max(nf-1,0),'bytes':size})
    return out


def benchmark() -> float:
    """Seconds this device needs for a fixed amount of the two main kinds of work (parsing and matrix products)."""
    import numpy as np, pandas as pd
    t = time.time()
    a = np.random.default_rng(0).random((200, 2000)).astype(np.float32)
    for _ in range(3): a.T @ a
    pd.to_numeric(pd.Series(np.char.mod('%.6g', np.random.default_rng(1).random(200_000))), errors='coerce')
    return time.time() - t


# Calibration (browser_app/calibrate_estimate.py on the whole-cohort browser-engine run); replaced by the build from
# browser_app/estimate_calibration.json. Model: seconds on the calibration server = const + cells*a + f2n*b + matrices*c.
ESTIMATE = {'coef': {'const': 3.0, 'cells': 2.0e-6, 'f2n': 6.0e-10, 'matrices': 0.0}, 'range_factors': [0.5, 2.0],
            'bench_server_pyodide_seconds': 2.67, 'heap_mb_base': 450.0, 'heap_mb_per_cell': 4.8e-5,
            'refuse_cells': 40_000_000, 'warn_cells': 20_000_000}


# Memory guard, per analysis x endpoint (one matrix). Predicted peak of the calculation worker while one matrix is assessed, in
# bytes, from the downloaded file before any calculation (browser_app/RESOURCE_GUARD.md: fitted on the whole-cohort browser-engine
# run, 4 Nov 2026; every matrix that failed for memory there is predicted over the budget):
#   runtime                     330 MB  (Pyodide, libraries, code, reference tables: measured peak of the smallest studies)
#   reading and ranking values  60 B per cell (positions x features) + 4 B per byte of the file
#   correlated feature pairs    55 B per pair, worst case: every pair of features (|rho| >= 0.80 can hold for all of them; the
#                               native runs have matrices where it does), none below 10 positions (no pair can be evaluated)
#   sample distances            16 B per position pair (square distance matrix, its ranks and index arrays)
# A matrix over the budget is not assessed in the browser; the others are. Budget: 1 GiB (provisional, conservative), halved when
# the browser reports less than 4 GB of device memory.
RESOURCE = {'runtime': 330 * 2 ** 20, 'per_cell': 60, 'per_file_byte': 4, 'per_pair': 55, 'per_position_pair': 16, 'pairs_min_positions': 10,
            'budget': 2 ** 30, 'small_device_budget': 2 ** 29, 'small_device_gb': 4, 'export_limit': 50 * 10 ** 6}
NOT_ASSESSED = 'not_assessed_in_the_browser_version'


PROBE = {'features': 1000, 'factor': 1.5, 'floor': 0.02, 'seed': 0}


def _probe_columns(run: str, sid: str, m: dict, idx) -> 'np.ndarray':
    """Values of the chosen features (positions x chosen), read line by line so only those columns are kept."""
    import numpy as np
    idx = sorted(idx); out = []
    def num(t):
        try: return float(t)
        except ValueError: return np.nan
    root = Path(run) / 'inputs' / sid / m['analysis_id']
    if m['endpoint'] in ('datatable', 'untarg_data'):
        f = root / TABULAR[m['endpoint']].format(a=m['analysis_id'])
        with f.open('r', encoding='utf-8', errors='replace') as fh:
            fh.readline()
            for line in fh:
                c = line.rstrip('\n').split('\t')
                out.append([num(c[2 + i]) if 2 + i < len(c) else np.nan for i in idx])
        return np.array(out, dtype=float)
    t = (root / 'mwtab' / f"{m['analysis_id']}_mwtab.txt").read_text(encoding='utf-8', errors='replace')
    i = t.find('_DATA_START'); blk = [x for x in t[i:t.find('_DATA_END', i)].split('\n')[1:] if x.strip()]
    rows = blk[1:]
    cols = [[num(v) for v in rows[k].split('\t')[1:1 + m['positions']]] for k in idx if k < len(rows)]
    cols = [c + [np.nan] * (m['positions'] - len(c)) for c in cols]
    return np.array(cols, dtype=float).T if cols else np.zeros((m['positions'], 0))


def pair_share(run: str, sid: str, m: dict) -> dict:
    """Share of feature pairs with |rho| >= 0.80 (the pairs the redundancy step stores), measured on a random sample of up to 1,000
    features with the pipeline's own Spearman code (correlation_v9.spearman_blocks: ranks over each feature's own values, at least
    10 common values). Upper estimate used by the guard: 1.5 x measured + 0.02 (checked on the cohort: RESOURCE_GUARD.md)."""
    import numpy as np
    sys.path.insert(0, str(HOME / 'source_snapshot/merit-ui-v9-analysis-endpoint-pilot/v8_layout'))
    from merit import correlation_v9 as cv
    f = m['features']; k = min(f, PROBE['features'])
    idx = np.random.default_rng(PROBE['seed']).choice(f, size=k, replace=False)
    x = _probe_columns(run, sid, m, idx)
    cnt = np.isfinite(x).sum(axis=0)
    with np.errstate(all='ignore'):
        sd = np.nanstd(x, axis=0)
    x = x[:, (cnt >= 3) & (sd > 0)]
    e = x.shape[1]
    if e < 2 or x.shape[0] < cv.MIN_SAMPLES:
        return {'sampled': int(k), 'evaluated': int(e), 'stored': 0, 'share': 0.0, 'upper': PROBE['floor']}
    _, _, pairs, _ = cv.spearman_blocks(x)
    share = len(pairs) / (e * (e - 1) / 2)
    return {'sampled': int(k), 'evaluated': int(e), 'stored': int(len(pairs)), 'share': share, 'upper': min(1.0, PROBE['factor'] * share + PROBE['floor'])}


def matrix_need(m: dict, share: float = 1.0) -> dict:
    n, f, b = m['positions'], m['features'], m['bytes']; r = RESOURCE
    pairs = int(share * f * (f - 1) // 2) if n >= r['pairs_min_positions'] else 0
    parts = {'reading and ranking the values': r['per_cell'] * n * f + r['per_file_byte'] * b,
             'correlations between features': r['per_pair'] * pairs, 'distances between samples': r['per_position_pair'] * n * n}
    return {'bytes': r['runtime'] + sum(parts.values()), 'largest_part': max(parts, key=parts.get), 'parts': parts, 'feature_pairs': pairs}


def plan(run: str, sid: str, device_memory_gb=None) -> dict:
    """Which matrices this browser assesses; the others are listed with their sizes and the reason. Written to the run folder."""
    small = device_memory_gb is not None and float(device_memory_gb) < RESOURCE['small_device_gb']
    budget = RESOURCE['small_device_budget'] if small else RESOURCE['budget']
    rows = []
    transfers=Path(run)/'browser_transfer_limits.json'
    refused=json.loads(transfers.read_text()) if transfers.exists() else []
    for m in matrix_sizes(run, sid):
        need = matrix_need(m) if m['positions'] and m['features'] else None
        probe = None
        # The number of correlated pairs decides: measure it on a sample of features instead of assuming every pair.
        if need and need['bytes'] > budget and matrix_need(m, 0.0)['bytes'] <= budget:
            post('status', text=f"Checking how much memory {m['analysis_id']} {m['endpoint']} needs ({m['positions']:,} positions × {m['features']:,} features)")
            try:
                probe = pair_share(run, sid, m); need = matrix_need(m, probe['upper'])
            except Exception as e:                       # unreadable sample: keep the worst case
                probe = {'error': repr(e)[:200]}
        minimum = False
        if need and probe is None and need['bytes'] > budget:       # over the budget even without any pair: report that part only
            need = matrix_need(m, 0.0); minimum = True
        rows.append({**m, 'need_bytes': need['bytes'] if need else 0, 'largest_part': need['largest_part'] if need else None,
                     'need_is_minimum': minimum, 'feature_pairs': need['feature_pairs'] if need else 0, 'pair_probe': probe,
                     'browser': 'assessed' if not need or need['bytes'] <= budget else 'not assessed'})
    for r in refused:
        if r.get('endpoint') not in ('datatable','mwtab','untarg_data'):continue
        rows=[x for x in rows if (x['analysis_id'],x['endpoint'])!=(r['analysis_id'],r['endpoint'])]
        rows.append({**r,'positions':r.get('positions',0),'features':r.get('features',0),'bytes':r.get('bytes',0),
            'need_bytes':max(budget+1,r.get('bytes',0)*4+RESOURCE['runtime']),'need_is_minimum':True,
            'largest_part':'downloading and reading the file','browser':'not assessed'})
    p = {'study_id': sid, 'budget_bytes': budget, 'device_memory_gb': device_memory_gb, 'model': RESOURCE, 'matrices': rows,
         'not_assessed': [r for r in rows if r['browser'] == 'not assessed'],
         'assessable': [r for r in rows if r['browser'] == 'assessed' and r['positions'] and r['features']]}
    (Path(run) / 'browser_resource_plan.json').write_text(json.dumps(p, indent=1))
    return p


def _gb(x): return f'{x / 2 ** 30:.1f} GB' if x >= 2 ** 30 else f'{x / 2 ** 20:.0f} MB'


def skip_note(r: dict, budget: int) -> str:
    if r.get('transfer_limit'):return r['reason']+'; its data were not assessed, rather than found absent'
    return (f"not assessed in the browser version: {r['positions']:,} positions × {r['features']:,} features would need {'at least' if r.get('need_is_minimum') else 'about'} "
            f"{_gb(r['need_bytes'])} of memory (mostly for {r['largest_part']}), more than the {_gb(budget)} a browser tab can use safely; "
            "the full MERIT-ML application on a computer with more memory assesses it")


def _apply_skips(out: Path, sid):
    """After the matrices are read (bootstrap), the matrices of the plan that this browser cannot hold are marked not assessed:
    no later step calculates them (every step works on endpoints with a sample roster), and every page and download shows why."""
    run = Path(out).parent if Path(out).name == 'assessment' else Path(out)
    pf = run / 'browser_resource_plan.json'
    if not sid or not pf.exists(): return
    p = json.loads(pf.read_text()); skip = {(r['analysis_id'], r['endpoint']): r for r in p['not_assessed']}
    if not skip: return
    path = run / 'assessment' / 'json' / f'{sid.lower()}_v9.json'
    v = json.loads(path.read_text()); changed = []
    for a in v['analyses']:
        for src in list(a['endpoints']):
            r = skip.get((a['analysis_id'], src))
            if r and a['endpoints'][src].get('availability') != NOT_ASSESSED:
                a['endpoints'][src] = {'availability': NOT_ASSESSED, 'note': skip_note(r, p['budget_bytes']),
                                       'browser_resource_limit': {k: r[k] for k in ('positions', 'features', 'need_bytes', 'largest_part')} | {'budget_bytes': p['budget_bytes']}}
                changed.append([a['analysis_id'], src])
    path.write_text(json.dumps(v, ensure_ascii=False, separators=(',', ':'), allow_nan=True))
    p['applied'] = changed; pf.write_text(json.dumps(p, indent=1))


def estimate(run: str, sid: str, device_memory_gb=None) -> dict:
    """Per matrix: will it be assessed here (memory guard), and the run-time range of the ones that will, scaled by this device's
    benchmark."""
    p = plan(run, sid, device_memory_gb); c = ESTIMATE['coef']
    sizes = p['assessable']
    cells = sum(m['positions'] * m['features'] for m in sizes)
    f2n = sum(m['features'] ** 2 * m['positions'] for m in sizes)
    factor = benchmark() / ESTIMATE['bench_server_pyodide_seconds']
    mid = (c['const'] + c['cells'] * cells + c['f2n'] * f2n + c['matrices'] * len(sizes)) * factor
    lo, hi = ESTIMATE['range_factors']
    peak = max((m['need_bytes'] for m in sizes), default=RESOURCE['runtime'])
    return {'matrices': p['matrices'], 'cells': cells, 'device_factor': round(factor, 2),
            'seconds_low': round(mid * lo), 'seconds_high': round(mid * hi), 'seconds_mid': round(mid),
            'memory_mb': round(peak / 2 ** 20), 'budget_mb': round(p['budget_bytes'] / 2 ** 20),
            'not_assessed': [{**r, 'note': skip_note(r, p['budget_bytes'])} for r in p['not_assessed']],
            'too_large': not sizes and bool(p['not_assessed']), 'near_limit': False}


def export_b64(run: str) -> str:
    """The run record (every file of the run folder) as a base64 ZIP, or '' with a reason when it is too large to hand over."""
    import base64, zipfile
    from browser_outputs import BoundedBytesIO,OutputLimit
    r = Path(run); buf = BoundedBytesIO()
    try:
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in sorted(r.rglob('*')):
                if p.is_file(): z.write(p, str(p.relative_to(r)))
    except OutputLimit as exc:return json.dumps({'ok':False,'too_large':True,'error':str(exc)})
    return json.dumps({'ok': True, 'data_b64': base64.b64encode(buf.getvalue()).decode()})


TABULAR = {'datatable': 'tabular/{a}_datatable_slash.txt', 'untarg_data': 'tabular/{a}_untarg_data.tsv'}


def _set_aside(run: str):
    """Datatable and untarg_data files of matrices not assessed here are removed from this run's inputs before the calculation, so
    no step reads them (reading alone held 1.1 GB for ST002866's two large untarg_data files) and their memory is freed. Their
    size and SHA-256 stay in browser_resource_plan.json. An mwTab file also holds the study's metadata and is kept; its matrix is
    marked not assessed right after it is read."""
    import hashlib
    pf = Path(run) / 'browser_resource_plan.json'
    if not pf.exists(): return
    p = json.loads(pf.read_text()); removed = p.get('removed_inputs', [])
    for r in p['not_assessed']:
        rel=TABULAR.get(r['endpoint'],'mwtab/{a}_mwtab.txt').format(a=r['analysis_id'])
        f = Path(run) / 'inputs' / p['study_id'] / r['analysis_id'] / rel
        if f.is_file():
            h = hashlib.sha256()
            with f.open('rb') as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b''): h.update(chunk)
            removed.append({'file': str(f.relative_to(run)), 'bytes': f.stat().st_size, 'sha256': h.hexdigest(),
                            'reason': 'matrix not assessed in the browser version (memory guard)'})
            if r['endpoint']=='mwtab':
                # Keep metadata and the sample header, never parse a refused quantitative block.
                tmp=f.with_suffix('.metadata.tmp');active=False;headers=0
                with f.open('rb') as src,tmp.open('wb') as dst:
                    for line in src:
                        if b'_DATA_START' in line:active=True;headers=2;dst.write(line);continue
                        if active and b'_DATA_END' in line:active=False;dst.write(line);continue
                        if not active or headers:dst.write(line)
                        if active and headers:headers-=1
                tmp.replace(f)
                removed[-1]['derived_view']='Metadata and sample header retained; quantitative feature rows omitted before parsing'
            else:f.unlink()
    p['removed_inputs'] = removed; pf.write_text(json.dumps(p, indent=1))


def assess(run: str) -> dict:
    _set_aside(run)
    argv = sys.argv
    sys.argv = ['online_assess.py', '--run-root', run]
    t = time.time()
    try: runpy.run_path(str(HOME / 'online_assess.py'), run_name='__main__')
    finally: sys.argv = argv
    audit = json.loads((Path(run) / 'isolation_audit.json').read_text())
    pf = Path(run) / 'browser_resource_plan.json'
    applied = json.loads(pf.read_text()).get('applied', []) if pf.exists() else []
    return {'seconds': round(time.time() - t, 1), 'failures': audit.get('failures'), 'matrix_reuse': audit.get('matrix_reuse'),
            'fast_correlation': audit.get('fast_correlation'), 'not_assessed': applied}


def respond(payload_json: str) -> str:
    from browser_outputs import install_limits,OutputLimit
    import live_render
    import browser_interactive as bi
    payload=json.loads(payload_json)
    ui,pilot,_,_=live_render.setup(Path(payload['run'])) if str(Path(payload['run']).resolve()) not in live_render._SETUP else live_render._SETUP[str(Path(payload['run']).resolve())]
    live_render._SETUP[str(Path(payload['run']).resolve())]=(ui,pilot,Path(payload['run'])/'materialized',Path(payload['run'])/'assessment')
    install_limits()
    bi.install(pilot,payload['run']);bi.configure(payload)
    # Refuse potentially huge feature-pair lists before constructing their dictionaries.
    if payload.get('action')=='feature-list' and payload.get('form',{}).get('metric')=='redundancy':
        params=ui._coerce_v2_scoring_params(payload.get('form',{}));overrides=ui._coerce_v2_matrix_overrides(payload.get('form',{}))
        stats=pilot._redundancy_study(payload['study_id'],params,overrides,False)
        pairs=sum((e.get('rec') or e).get('pairs_above',0) or 0 for row in stats or [] for e in row['endpoints'].values())
        if pairs*120>RESOURCE['export_limit']:return json.dumps({'ok':False,'too_large':True,'error':'The estimated feature-pair list exceeds the browser download budget. Increase the correlation cut-off or use https://github.com/BiosystemEngineeringLab-IITB/merit-ml.'})
    try:a = live_render.respond(payload)
    except OutputLimit as exc:return json.dumps({'ok':False,'too_large':True,'error':str(exc)})
    if a.get('mime')=='text/html' and a.get('data_b64'):
        import base64
        a['data_b64']=base64.b64encode(bi.notice(base64.b64decode(a['data_b64']).decode(),payload['run']).encode()).decode()
    size = len(a.get('data_b64') or '') * 3 // 4
    if size > RESOURCE['export_limit']:
        a = {'ok': False, 'too_large': True, 'bytes': size,
             'error': (f"This download would be {size / 1e6:.0f} MB, more than the browser version can hand over safely "
                       f"({RESOURCE['export_limit'] / 1e6:.0f} MB). Narrow it (a higher cut-off, fewer analyses) or use the full MERIT-ML "
                       "application on a computer with more memory.")}
    return json.dumps(a)
