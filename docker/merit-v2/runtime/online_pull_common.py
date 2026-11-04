"""Paths and durable metadata for explicit live-only cohort experiments."""
import hashlib
import json
import os
import re
from pathlib import Path

ROOT=Path(os.environ.get('MERIT_ONLINE_EXECUTION_ROOT',str(Path(__file__).resolve().parent))).resolve()
RUN_ID=re.compile(r'online_pull_(?:all_studies|new_studies_after_\d{8})_[A-Za-z0-9_-]+')


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2));tmp.replace(path)


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def context(run_id,sid=None):
    if not isinstance(run_id,str) or not RUN_ID.fullmatch(run_id):raise ValueError('Invalid online-pull run identifier')
    path=ROOT/'runs'/run_id
    config=json.loads((path/'online_pull_config.json').read_text())
    if config['run_id']!=run_id or config['input_policy']!='live_only':raise ValueError('Not a live-only cohort')
    if sid and sid not in config['study_ids']:raise ValueError('Study is outside this cohort')
    refs=path/'online_pull_shared_reference_inputs'
    if not (refs/'fetch_manifest.json').exists():raise ValueError('Declared cohort reference snapshot is not ready')
    runtime=path/'online_pull_runtime'
    if not (runtime/'bundle_manifest.json').exists():raise ValueError('Pinned online-pull runtime missing')
    return path,refs,runtime,config
