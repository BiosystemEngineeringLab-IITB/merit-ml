"""Bulk entry validation; no assessment or score computation."""
import re
from concurrent.futures import ThreadPoolExecutor

def validate(text, classify, parallel=False):
    text=str(text or '')
    if len(text)>100000:raise ValueError('Paste at most 100,000 characters')
    tokens=list(dict.fromkeys(t.upper() for t in re.split(r'[\s,;|]+',text.strip()) if t))
    if not tokens:raise ValueError('Enter at least one study ID')
    if len(tokens)>500:raise ValueError('A bulk assessment accepts at most 500 distinct entries')
    def one(sid):
        if not re.fullmatch(r'ST[0-9]{6}',sid):return dict(study_id=sid,status='invalid',message='Invalid ID: use ST followed by six digits')
        return dict(study_id=sid,**classify(sid))
    if parallel:
        with ThreadPoolExecutor(max_workers=6) as pool:items=list(pool.map(one,tokens))
    else:items=[one(t) for t in tokens]
    return dict(ok=True,items=items,available=[r['study_id'] for r in items if r['status']=='available'],duplicates_removed=len([t for t in re.split(r'[\s,;|]+',text.strip()) if t])-len(tokens))
