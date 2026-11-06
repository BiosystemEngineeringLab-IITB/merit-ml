"""Browser/citation metadata generated from the newly fetched MW records only."""
import json
import re
from pathlib import Path
from fetch_online import records


def section_values(objects, section, key):
    """Keep every declared value, accepting empty or repeated MW sections."""
    values = set()
    for obj in objects:
        block = obj.get(section, {})
        if block is None:
            blocks = []
        elif isinstance(block, dict):
            blocks = [block]
        elif isinstance(block, list):
            blocks = block
        else:
            raise TypeError(f'{section}: expected a metadata object or list of objects')
        for item in blocks:
            if not isinstance(item, dict):
                raise TypeError(f'{section}: metadata list contains a non-object')
            value = str(item.get(key) or '').strip()
            if value:
                values.add(value)
    return sorted(values)


def build(run):
    run=Path(run);out=run/'assessment';live=run/'materialized'
    registry=json.loads((run/'live_registry.json').read_text())
    rows=[];citations={}
    for sid,aids in registry.items():
        ss=records(json.loads((live/sid/'summary.json').read_text()))
        s=ss[0] if ss else {};objects=[]
        for aid in aids:
            try:
                value=json.loads((live/sid/aid/'mwtab'/f'{aid}_mwtab.json').read_text())
                if isinstance(value,dict):objects.append(value)
            except (OSError,ValueError):continue
        values=lambda sec,key:section_values(objects,sec,key)
        raw=json.loads((out/'json'/f'{sid.lower()}_v9.json').read_text())
        flags=[a.get('sections',{}).get('metadata',{}).get('metrics',{}).get('mass_rt_like_metadata_presence',{}).get('score') for a in raw['analyses']]
        row={'study_id':sid,'title':s.get('study_title',''),'organism':s.get('species',''),
             'institute':s.get('institute',''),'analysis_type':s.get('analysis_type',''),
             'analysis_types':values('ANALYSIS','ANALYSIS_TYPE'),'ion_modes':values('MS','ION_MODE'),
             'chromatography_types':values('CHROMATOGRAPHY','CHROMATOGRAPHY_TYPE'),
             'instruments':values('MS','INSTRUMENT_NAME'),'sample_types':values('SUBJECT','SUBJECT_TYPE'),
             'project_type':'; '.join(values('PROJECT','PROJECT_TYPE')),
             'disease':'','score':None,'band':'','analysis_ids':aids,
             'mass_rt_like_metadata_present':any(x and x>0 for x in flags),
             'mass_rt_like_metadata_score':max((x for x in flags if x is not None),default=None)}
        from live_disease import read as disease_of
        row['disease']=disease_of(live,sid)
        rows.append(row)
        doi=next(iter(values('PROJECT','DOI')),'')
        doi=re.sub(r'^https?://(?:dx\.)?doi\.org/','',doi,flags=re.I)
        project_id=next(iter(values('METABOLOMICS WORKBENCH','PROJECT_ID')),'')
        page=run/'inputs'/sid/'repository_page.html'
        if not project_id and page.exists():
            ids=set(re.findall(r'\bPR\d{6}\b',page.read_text(errors='replace')))
            if len(ids)==1:project_id=next(iter(ids))
        (live/sid/'manifest.json').write_text(json.dumps({'study_id':sid,'analyses':{a:{} for a in aids},'source':'fresh API analysis registry'}))
        publications=[]
        for rawpub in sorted(set(values('PROJECT','PUBLICATIONS')+values('STUDY','PUBLICATIONS'))):
            pm=re.search(r'(?:pubmed/|pubmed\.ncbi\.nlm\.nih\.gov/|PMID\s*:?\s*)(\d+)',rawpub,re.I)
            dm=re.search(r'\b10\.\d{4,9}/[^\s<>";]+',rawpub)
            publications.append({'citation':rawpub,'pubmed_id':pm[1] if pm else '',
                                 'doi':dm[0].rstrip('.,)') if dm else ''})
        citations[sid]={'project_id':project_id,'project_doi':doi,'doi_url':f'https://doi.org/{doi}' if doi else '',
                        'related_publications':publications, 'source':'fresh mwTab PROJECT/STUDY fields and verified study-page project ID; no historical citation index'}
        reviewed=Path(__file__).resolve().parent/'assets/reference/design_context_v11_20260601'/f'{sid.lower()}.json'
        p=out/'design_context'/f'{sid.lower()}.json';p.parent.mkdir(exist_ok=True)
        if reviewed.exists():
            # Reviewed design labels (language-model reading + rules, frozen 1 June 2026) used by 8782 for the cohort.
            p.write_bytes(reviewed.read_bytes())
        else:
            from live_design_context import compute
            context=compute(run,sid,raw)
            context.update(declared_study_types=values('STUDY','STUDY_TYPE'),declared_project_types=values('PROJECT','PROJECT_TYPE'))
            p.write_text(json.dumps(context))
        from live_study_summary import compute as compute_summary
        summaries=compute_summary(run,sid,raw)
        p=out/'ui_summaries'/f'{sid.lower()}.json';p.parent.mkdir(exist_ok=True);p.write_text(json.dumps(summaries,ensure_ascii=False))
    for name,payload in [('index.json',{'studies':{s:{'state_path':''} for s in registry}}),
                         ('study_metadata_index.json',{'studies':rows,'scope':'studies fetched in this live session only'}),
                         ('citation_index.json',{'studies':citations})]:
        (out/name).write_text(json.dumps(payload,ensure_ascii=False))


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--run-root',required=True,type=Path);build(ap.parse_args().run_root)
