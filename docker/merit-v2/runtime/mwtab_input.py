"""Resolve the current REST mwTab JSON envelope by declared identifiers."""
import json
import re


def text_for(data,study_id,analysis_id):
    starts=list(re.finditer(rb'(?m)^#METABOLOMICS WORKBENCH[^\r\n]*',data))
    if not starts:return data
    chunks=[data[m.start():starts[i+1].start() if i+1<len(starts) else len(data)] for i,m in enumerate(starts)]
    def ids(chunk):
        first=chunk.split(b'\n',1)[0]
        st=re.search(rb'\bSTUDY_ID\s*:\s*(ST\d+)',first)
        an=re.search(rb'\bANALYSIS_ID\s*:\s*(AN\d+)',first)
        return st[1].decode() if st else '',an[1].decode() if an else ''
    exact=[c for c in chunks if ids(c)==(study_id,analysis_id)]
    analysis=[c for c in chunks if ids(c)[1]==analysis_id]
    candidates=exact or analysis
    unique={c.strip():c for c in candidates}
    if len(unique)==1:return next(iter(unique.values()))
    if len(chunks)==1 and not ids(chunks[0])[1]:return chunks[0]
    raise ValueError(f'Ambiguous or mismatched mwTab text for {study_id}/{analysis_id}: {[ids(c) for c in chunks]}')


def object_for(value,study_id,analysis_id):
    candidates=[value] if isinstance(value,dict) else [x for x in value if isinstance(x,dict)] if isinstance(value,list) else []
    if not candidates:return {}
    def ids(obj):
        meta=obj.get('METABOLOMICS WORKBENCH') or {}
        return str(meta.get('STUDY_ID') or '').upper(),str(meta.get('ANALYSIS_ID') or '').upper()
    exact=[x for x in candidates if ids(x)==(study_id,analysis_id)]
    analysis=[x for x in candidates if ids(x)[1]==analysis_id]
    blank=[x for x in candidates if not ids(x)[1]]
    choose=exact or analysis or blank
    unique={json.dumps(x,sort_keys=True,separators=(',',':')):x for x in choose}
    if len(unique)==1:return next(iter(unique.values()))
    if not unique:raise ValueError(f'mwTab response has no object for {study_id}/{analysis_id}: {[ids(x) for x in candidates]}')
    raise ValueError(f'Ambiguous mwTab response for {study_id}/{analysis_id}: {[ids(x) for x in choose]}')
