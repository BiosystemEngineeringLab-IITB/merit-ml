import re, pandas as pd
S='/tmp/claude-1004/-home-shayantan-metabolomics/fa6f75df-9cb2-4557-b4f2-aa6e5e67c504/scratchpad/'
m=pd.read_pickle(S+'m.pkl'); nf=pd.read_csv('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/outputs/measurement_scale_20260930/audit_declared_vs_values_20260930/number_format_corruption_mwtab.tsv',sep='\t')
c=m[m.units_category=='concentration'].copy()
P={'':1,'m':1e-3,'u':1e-6,'µ':1e-6,'μ':1e-6,'n':1e-9,'p':1e-12,'f':1e-15}
WORDP={'milli':'m','micro':'u','nano':'n','pico':'p','femto':'f'}
DEN={'l':1,'ml':1e-3,'dl':0.1,'ul':1e-6,'µl':1e-6,'μl':1e-6,'g':1,'mg':1e-3,'kg':1e3,'ug':1e-6,'µg':1e-6,'μg':1e-6,'ng':1e-9}
def parse(u):
    s=u.lower().replace('?g','µg').replace('per','/')
    s=re.sub(r'\b(milli|micro|nano|pico|femto)\s*', lambda x: WORDP[x.group(1)], s)
    s=re.sub(r'moles?\b|molar\b','mol',s); s=re.sub(r'grams?\b','g',s)
    s=s.replace('/i','/l')
    mm=re.search(r'(?<![a-z])([mupnfµμ]?)m(?:ol)?\s*/\s*(\d*)\s*(l|ml|dl|ul|µl|μl|kg|g|mg|ug|µg|μg|ng)(?![a-z])',s) or None
    if mm:
        kind='mol'
    else:
        mm=re.search(r'(?<![a-z])([mupnfµμ]?)g\s*/\s*(\d*)\s*(l|ml|dl|ul|µl|μl|kg|g|mg|ug|µg|μg|ng)(?![a-z])',s); kind='g'
    if mm:
        num=P[mm.group(1)]; k=float(mm.group(2) or 1); d=mm.group(3)
        base='L' if d.endswith('l') else 'g'
        return f'{kind}/{base}', num/(DEN[d]*k)
    mm=re.fullmatch(r'.*?(?<![a-z])([mupnfµμ]?)m(?:ol)?\)?\s*',s)          # bare molar: mM, uM, (nM)
    if mm and not re.search(r'mol\s*$',s.strip()) : return 'mol/L',P[mm.group(1)]
    return None
LIM={'mol/L':1,'mol/g':1e-3,'g/L':100,'g/g':0.1}
rows=[]
for _,r in c.iterrows():
    p=parse(r.units_raw)
    if p is None: rows.append((r.study_id,r.analysis_id,r.units_raw,r.n_features,r.median_nonzero,'','', 'not convertible (per sample/cell/vial or amount only)')); continue
    base,f=p; v=r.median_nonzero*f
    rows.append((r.study_id,r.analysis_id,r.units_raw,r.n_features,r.median_nonzero,base,v,'implausible' if v>LIM[base] else 'plausible'))
o=pd.DataFrame(rows,columns=['study_id','analysis_id','units_raw','n_features','median_nonzero','converted_base','median_converted','flag'])
o=o.merge(nf.rename(columns={'analysis':'analysis_id'})[['analysis_id','pct_multidot','pct_lead0']],on='analysis_id',how='left')
print(o.flag.value_counts())
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
print(o[o.flag=='implausible'].sort_values('median_converted',ascending=False).to_string())
print(o[o.flag.str.startswith('not')].units_raw.value_counts().head(25))
o.to_csv('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/outputs/measurement_scale_20260930/audit_declared_vs_values_20260930/concentration_plausibility.tsv',sep='\t',index=False)
