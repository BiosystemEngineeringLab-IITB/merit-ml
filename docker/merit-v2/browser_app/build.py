"""Build the browser version of the live tool into browser_app/dist/<version>/ (static files only).

dist/<version>/
  index.html, app.js, worker.js      the page and the worker
  config.json                         where the code, wheels and reference files are, and which reference files to load
  code-<sha12>.zip                    online-tool/*.py, browser_app/browser_runtime.py, source_snapshot/ (no compiled files), paths
                                      as on the server (/home/shayantan/metabolomics/ML-ready/online-tool/...), so outputs match 8791
  wheels/                             openpyxl 3.1.5 and et_xmlfile 2.0.0 (browser_pilot/wheels)
  build_manifest.json                 SHA-256 of every bundled file, the Pyodide version and the reference version
usage: build.py --version 20261104 --reference <URL or relative path of merit-live/reference/<version>/>
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = HERE.parent/'runtime' if (HERE.parent/'runtime/source_snapshot').is_dir() else HERE.parent
BROWSER_HOME='/home/shayantan/metabolomics/ML-ready/online-tool'
sys.path.insert(0,str(HOME))
from release_safety import RUNTIME_FILES,assert_safe,scan_tree
PYODIDE = '0.29.5'
GLOBAL_REFERENCE = {'refmet_db.csv': 'assets/reference/refmet_db.csv',
                    'study_reference_manifest.json':'assets/reference/study_reference_manifest.json',
                    'refmet_all_ids_rest_20260929.json': 'assets/reference/refmet_all_ids_rest_20260929.json',
                    'reference_policy.json': 'assets/reference/reference_policy.json',
                    'original_cohort_4121_study_ids.txt': 'assets/reference/original_cohort_4121_study_ids.txt',
                    'alert_reference_R1.json': 'assets/alert_reference_R1.json'}
STUDY_REFERENCE = [['panredu_20261004', '.tsv'], ['design_context_v11_20260601', '.json'], ['panredu_dump_20261104', '.tsv']]


def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()


def code_files():
    yield from (HOME/name for name in RUNTIME_FILES)
    yield from (HERE/name for name in ('browser_runtime.py','browser_tasks.py','browser_outputs.py','browser_interactive.py'))
    yield HOME / 'source_snapshot_manifest.json'
    yield from sorted(p for p in (HOME / 'source_snapshot').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')


def bench_server() -> float:
    """benchmark() of browser_runtime, natively on this server, median of 5 (used to scale the estimate to the user's device)."""
    code = ("import sys,statistics;sys.modules['js']=type(sys)('js');pf=type(sys)('pyodide.ffi');pf.to_js=None;sys.modules['pyodide']=type(sys)('pyodide');"
            "sys.modules['pyodide.ffi']=pf;sys.path.insert(0,%r);import browser_runtime as b;print(statistics.median(b.benchmark() for _ in range(5)))" % str(HERE))
    python=HOME/'.venv/bin/python'
    out = subprocess.run([str(python) if python.exists() else sys.executable, '-c', code], capture_output=True, text=True, env={'OPENBLAS_NUM_THREADS': '1', 'PATH': '/usr/bin'})
    return float(out.stdout.strip())


def inject_calibration(data: bytes) -> bytes:
    """Replace the ESTIMATE constants of browser_runtime.py by browser_app/estimate_calibration.json (calibrate_estimate.py)."""
    import re
    cal = json.loads((HERE / 'estimate_calibration.json').read_text()); m = cal['memory']
    est = {'coef': cal['coef'], 'range_factors': cal['range_factors'], 'bench_server_pyodide_seconds': cal['bench_server_pyodide_seconds'],
           'heap_mb_base': m['heap_mb_base'], 'heap_mb_per_cell': m['heap_mb_per_cell'], 'refuse_cells': cal['refuse_cells'], 'warn_cells': cal['warn_cells']}
    text = data.decode()
    new, k = re.subn(r"ESTIMATE = \{.*?\}\n\n", lambda _: 'ESTIMATE = ' + repr(est) + '\n\n', text, count=1, flags=re.S)
    assert k == 1, 'ESTIMATE block not found'
    return new.encode()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--version', required=True); ap.add_argument('--reference', required=True)
    a = ap.parse_args()
    out = HERE / 'dist' / a.version
    if out.exists(): shutil.rmtree(out)
    (out / 'wheels').mkdir(parents=True)
    bench = bench_server()
    files = {}
    safety=assert_safe(list(code_files()),root=HOME.parent)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in code_files():
            data = p.read_bytes()
            if p.name == 'browser_runtime.py' and (HERE / 'estimate_calibration.json').exists():
                data = inject_calibration(data)
            rel=Path('browser_app')/p.name if p.parent==HERE else p.relative_to(HOME)
            arc=BROWSER_HOME.lstrip('/')+'/'+str(rel)
            if p.suffix=='.py':data=data.replace(str(HOME).encode(),BROWSER_HOME.encode())
            zi = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0)); zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data); files[arc] = {'bytes': len(data), 'sha256': sha(data)}
    code = buf.getvalue(); name = f'code-{sha(code)[:12]}.zip'
    (out / name).write_bytes(code)
    wheels = []
    for w in sorted((HOME / 'browser_pilot/wheels').glob('*.whl')):
        shutil.copy2(w, out / 'wheels' / w.name); wheels.append('wheels/' + w.name)
    # page files; script URLs carry a content hash (app.js?v=<sha12>, worker.js?v=<sha12>) so a browser never keeps running an
    # earlier build's script from its cache next to a newer page
    worker_js = (HERE / 'worker.js').read_bytes()
    app_js = (HERE / 'app.js').read_text()
    assert app_js.count("new Worker('worker.js')") == 1, 'worker URL not found in app.js'
    app_js = app_js.replace("new Worker('worker.js')", f"new Worker('worker.js?v={sha(worker_js)[:12]}')")
    index = (HERE / 'index.html').read_text()
    assert index.count('<script src="app.js"></script>') == 1, 'app.js script tag not found in index.html'
    index = index.replace('<script src="app.js"></script>', f'<script src="app.js?v={sha(app_js.encode())[:12]}"></script>')
    (out / 'worker.js').write_bytes(worker_js); (out / 'app.js').write_text(app_js); (out / 'index.html').write_text(index)
    for extra in ('checkpoints.js','downloads.js'):shutil.copy2(HERE/extra,out/extra)
    shutil.copy2(HOME / 'source_snapshot/Logo.png', out / 'Logo.png')
    config = {'version': a.version, 'code': name, 'wheels': wheels, 'reference': a.reference.rstrip('/') + '/',
              'reference_files': list(GLOBAL_REFERENCE), 'reference_paths': GLOBAL_REFERENCE,
              'study_reference': [[d, e] for d, e in STUDY_REFERENCE],
              'local_app_url':'https://github.com/BiosystemEngineeringLab-IITB/merit-ml'}
    # design-context files are named in lower case (st000001.json); the worker lower-cases the ID for that folder
    (out / 'config.json').write_text(json.dumps(config, indent=1))
    manifest = {'built_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'version': a.version, 'pyodide': PYODIDE,
                'bench_server_seconds': bench, 'reference': a.reference, 'code_zip': name, 'code_zip_sha256': sha(code),
                'page_files': {f: sha((out / f).read_bytes()) for f in ('index.html', 'app.js', 'worker.js', 'checkpoints.js','downloads.js','config.json')},
                'wheels': {w: sha((out / w).read_bytes()) for w in wheels}, 'bundled_files': files}
    manifest['release_safety']=safety
    (out / 'build_manifest.json').write_text(json.dumps(manifest, indent=0))
    scan_tree(out,server_root=HOME.parent)
    print(json.dumps({'out': str(out), 'code_zip_MB': round(len(code) / 1e6, 1), 'files': len(files), 'bench_server_seconds': bench}))


if __name__ == '__main__':
    main()
