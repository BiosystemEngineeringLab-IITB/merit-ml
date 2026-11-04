"""Relocate only bundled absolute source paths after copying to another server."""
import datetime
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
manifest=ROOT/'source_snapshot_manifest.json'
value=json.loads(manifest.read_text());old=value['bundled_root'];new=str(ROOT/'source_snapshot')
if old!=new:
    for row in value['files']:
        p=ROOT/row['bundled']
        if hashlib.sha256(p.read_bytes()).hexdigest()!=row['bundled_sha256']:raise RuntimeError('Unexpected bundle modification: '+str(p))
        if p.suffix=='.py':p.write_text(p.read_text().replace(old,new))
        row['bundled_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    value['bundled_root']=new
    value.setdefault('relocation_history',[]).append({'from':old,'to':new,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    manifest.write_text(json.dumps(value,indent=2))
print('Bundle root:',new)
