"""Fail closed before copying files into a public release. Never print secret values."""
from __future__ import annotations
import json
import re
import shlex
import io
import zipfile
from pathlib import Path

PRIVATE_KEY=re.compile(rb'(?m)^-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----\s*$')
TOKEN_VALUE=re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|vcp_[A-Za-z0-9]{30,})\b')
SENSITIVE_NAME=re.compile(r'(?:^|[_-])(?:credentials?|secrets?|tokens?|passwords?)(?:[._-]|$)',re.I)
ASSIGNMENT=re.compile(rb'(?im)^\s*(?:export\s+)?(?:AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|CLOUDFLARE_API_TOKEN|VERCEL_TOKEN|GITHUB_PAT|DOCKERHUB_TOKEN)\s*=\s*[\"\']?([A-Za-z0-9_./+=-]{20,})[\"\']?\s*$')
BAD_NAMES={'yes','yes.pub','id_rsa','id_ed25519','id_ecdsa','authorized_keys','known_hosts','auth.json','credentials.env','config.env','cloudfare','cloudflare'}
RUNTIME_FILES=('bulk_entry.py','online_assess.py','fetch_online.py','fast_correlation.py','live_app.py','live_render.py',
    'live_indexes.py','live_validity.py','live_catalog.py','live_inventory.py','live_design_context.py','mwtab_input.py',
    'live_bulk.py','live_references.py','online_evidence.py','live_study_summary.py','live_progress.py',
    'online_pull_common.py','online_pull_dashboard.py','online_pull_capture.py','online_pull_cohort.py','online_pull_compare.py',
    'online_pull_progress.py','relocate_bundle.py','execution_state.py','native_resources.py','native_guard.py',
    'metric_worker.py','matrix_worker.py','matrix_driver.py','v2_server.py','release_safety.py','versioned_context.py')


def inspect_blob(name,raw,secret_values=()):
    base=Path(name).name.lower()
    if base in BAD_NAMES or base.startswith(('.env','credentials.env')) or base.endswith(('.pem','.key','.p12','.pfx')):return 'private credential/key filename'
    if PRIVATE_KEY.search(raw):return 'private-key material'
    if TOKEN_VALUE.search(raw):return 'credential token pattern'
    if ASSIGNMENT.search(raw):return 'populated credential assignment'
    if any(value in raw for value in secret_values):return 'matches a server-only credential value'
    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for member in z.infolist():
                if not member.is_dir():
                    hit=inspect_blob(member.filename,z.read(member),secret_values)
                    if hit:return 'archive member '+member.filename+': '+hit
    return None


def known_values(root: Path):
    """Use local values only as a comparison set, never as an output or bundled asset."""
    values=set()
    sources=[root/'deploy/credentials.env',root/'deploy/credentials.env.template']
    sources+=list((root/'deploy/server_only_20261105').glob('*.before'))
    for p in sources:
        if not p.is_file():continue
        for line in p.read_text().splitlines():
            m=re.match(r'^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$',line)
            if m and re.search(r'TOKEN|SECRET|PASSWORD|API_KEY|ACCESS_KEY_ID',m[1]):
                try:v=' '.join(shlex.split(m[2],comments=True))
                except ValueError:continue
                if len(v)>=20:values.add(v.encode())
    return values


def findings(files, *, secret_values=()):
    result=[]
    for p in files:
        p=Path(p)
        if p.is_symlink():result.append({'file':str(p),'kind':'symlink requires explicit review'});continue
        if not p.is_file():continue
        name=p.name.lower()
        if name in BAD_NAMES or name.startswith('.env') or name.endswith(('.pem','.key','.p12','.pfx')):
            result.append({'file':str(p),'kind':'private credential/key filename'});continue
        hit=inspect_blob(str(p),p.read_bytes(),secret_values)
        if hit:result.append({'file':str(p),'kind':hit})
    return result


def assert_safe(files, *, root=None):
    files=list(files);bad=findings(files,secret_values=known_values(Path(root)) if root else ())
    if bad:raise RuntimeError('Release blocked by credential scan: '+json.dumps(bad))
    return {'files_scanned':len(files),'credential_findings':0,'secret_values_logged':False}


def scan_tree(root, *, server_root=None):
    root=Path(root)
    files=[p for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts and '.vercel' not in p.relative_to(root).parts]
    return assert_safe(files,root=server_root)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--tree',required=True);p.add_argument('--server-root');a=p.parse_args()
    print(json.dumps(scan_tree(a.tree,server_root=a.server_root)))
