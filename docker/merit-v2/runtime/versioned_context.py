"""Retrieve declared context reference inputs, never historical matrix metrics.

The browser and native applications use the same versioned public reference
index. Each retrieved file is verified against the bundled SHA-256 index.
"""
import hashlib
import json
import os
import urllib.request
from pathlib import Path
BASE='https://pub-acf151eb41e04ee795a86a8049d54039.r2.dev/merit-live/reference/20261104/'
FOLDERS=(('panredu_20261004','.tsv'),('design_context_v11_20260601','.json'),('panredu_dump_20261104','.tsv'))

def ensure(root,sid):
    root=Path(root);index=json.loads((root/'study_reference_manifest.json').read_text())['files'];records=[]
    for folder,ext in FOLDERS:
        name=(sid.lower() if folder.startswith('design_context') else sid)+ext;rel=folder+'/'+name
        expected=index.get(rel)
        if expected is None:continue
        p=root/rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected['sha256']:
            request=urllib.request.Request(BASE+rel,headers={'User-Agent':'MERIT-ML-online-parity-pilot/1.0'})
            with urllib.request.urlopen(request,timeout=120) as response:raw=response.read(expected['bytes']+1)
            if len(raw)!=expected['bytes'] or hashlib.sha256(raw).hexdigest()!=expected['sha256']:raise ValueError('Context reference checksum mismatch: '+rel)
            p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_name(p.name+f'.{os.getpid()}.tmp');temp.write_bytes(raw);temp.replace(p)
        records.append({'file':rel,'sha256':expected['sha256'],'bytes':expected['bytes'],'url':BASE+rel,'role':'versioned context reference input; not a matrix result'})
    return records
