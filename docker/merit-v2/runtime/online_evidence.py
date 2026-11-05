"""Complete v9-compatible evidence from live inputs and existing MERIT rules."""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
from datetime import datetime,timezone
from pathlib import Path


def annotation_evidence(v):
    for analysis in v['analyses']:
        for endpoint in analysis['endpoints'].values():
            if 'roster' not in endpoint:continue
            if not endpoint.get('sections',{}).get('annotation'):continue
            result=endpoint['sections']['annotation']
            # This historical compatibility block used Table S1's lower-bound
            # mapping rule, before v9 added Text S10 spelling normalization.
            # The current v9 mapped count remains in sections.annotation.refmet.
            named=result['named'];mapped=result['named_lower_manuscript']
            endpoint.setdefault('annotation_evidence',{}).update({
                'feature_label_classes':{'class_counts':result['class_counts'],'n_excluded':result['excluded'],
                    'class_examples':{k:list(x)[:3] for k,x in result['class_examples'].items()}},
                'refmet_names_among_named_features':{'named_features':named,'named_features_with_refmet_name':mapped,
                    'share':mapped/named if named else None},
            })


def export_compatibility(v,keep_flagged_index=False):
    """Match the existing stored schema; native results are retained separately."""
    for analysis in v['analyses']:
        for endpoint in analysis['endpoints'].values():
            if 'roster' not in endpoint:continue
            # Schema of the 8782 records: as first built, no flagged_index (kept for studies 8782 rebuilt after 21 Oct);
            # the invalid-item label is set.
            analytical=endpoint['sections']['analytical']
            if not keep_flagged_index:analytical.get('low_information',{}).get('default',{}).pop('flagged_index',None)
            item=analytical.get('sample_missingness',{}).get('items',{}).get('invalid')
            if item is not None:item['label']='Invalid entries'


def panredu_table(path,sid,acq):
    import pandas as pd
    response=json.loads(path.read_text())
    data=response['response']['download-dataframe-csv']['data']
    if data.get('base64'):
        import base64
        text=base64.b64decode(data['content'],validate=True).decode()
    else:text=data['content']
    reader=csv.reader(io.StringIO(text));header=next(reader)
    if not set(acq.REDU_COLUMNS).issubset(header):raise ValueError('Pan-ReDU required columns missing')
    frame=pd.read_csv(io.StringIO(text),usecols=acq.REDU_COLUMNS,dtype=str,keep_default_na=False)
    if set(frame.ATTRIBUTE_DatasetAccession)-{sid}:raise ValueError('Pan-ReDU response includes a different study')
    frame['base']=frame.filename.map(acq.norm_raw)
    frame['ext']=frame.filename.str.lower().str.extract(acq.REDU_FILE_EXTENSION,expand=False).fillna('')
    counts=pd.to_numeric(frame.MS2spectra_count,errors='coerce')
    frame['ms2']='not_determined'
    frame.loc[(counts==0)&frame.ext.isin(['mzml','mzxml'])&(frame.classification=='Unclassified'),'ms2']='zero_unclassified_open'
    frame.loc[frame.classification=='NO_MS2','ms2']='absent'
    frame.loc[counts>0,'ms2']='present'
    frame['model']=frame.MassSpectrometer.str.split('|').str[0].str.strip()
    return frame,text


PINNED=Path(__file__).resolve().parent/'assets/reference'


DUMP_KEY='panredu_dump_20261104/<study>.tsv'


def pinned_panredu(sid):
    """Policy of the pinned Pan-ReDU rows of a study: the 4 Oct 2026 snapshot used by 8782 for the original cohort; for any other
    study the frozen Pan-ReDU dump (https://redu.gnps2.org/dump, 3 Nov 2026) once its split is listed in reference_policy.json
    (a study absent from the dump has no rows); else None (live Pan-ReDU request)."""
    files=json.loads((PINNED/'reference_policy.json').read_text())['files']
    ids=set((PINNED/'original_cohort_4121_study_ids.txt').read_text().split())
    if sid in ids:return files['panredu_20261004/<study>.tsv']
    return files.get(DUMP_KEY)


def _panredu_folder(sid):
    ids=set((PINNED/'original_cohort_4121_study_ids.txt').read_text().split())
    return 'panredu_20261004' if sid in ids else DUMP_KEY.split('/')[0]


def pinned_panredu_table(sid,acq,policy):
    """The study's rows of the snapshot, transformed exactly as build_analysis_evidence.py does."""
    import pandas as pd
    f=PINNED/_panredu_folder(sid)/f'{sid}.tsv'
    if f.exists():frame=pd.read_csv(f,sep='\t',usecols=acq.REDU_COLUMNS,dtype=str,keep_default_na=False)
    else:frame=pd.DataFrame({c:pd.Series(dtype=str) for c in acq.REDU_COLUMNS})
    frame['base']=frame.filename.map(acq.norm_raw)
    frame['ext']=frame.filename.str.lower().str.extract(acq.REDU_FILE_EXTENSION,expand=False).fillna('')
    counts=pd.to_numeric(frame.MS2spectra_count,errors='coerce')
    frame['ms2']='not_determined'
    frame.loc[(counts==0)&frame.ext.isin(['mzml','mzxml'])&(frame.classification=='Unclassified'),'ms2']='zero_unclassified_open'
    frame.loc[frame.classification=='NO_MS2','ms2']='absent'
    frame.loc[counts>0,'ms2']='present'
    frame['model']=frame.MassSpectrometer.str.split('|').str[0].str.strip()
    return frame,frame[acq.REDU_COLUMNS].to_csv(index=False),policy['source_sha256']


def original_redu_summary(acq):
    """Reuse the original nested helper verbatim, without executing cohort main()."""
    tree=ast.parse(Path(acq.__file__).read_text())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    function=next(n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='redu_summary')
    module=ast.Module(body=[function],type_ignores=[])
    scope={}
    exec(compile(module,str(acq.__file__),'exec'),acq.__dict__,scope)
    return scope['redu_summary']


def acquisition_evidence(v,live,run,out,acq,ax):
    """Populate Pan-ReDU fields only after an actual successful live export."""
    import pandas as pd
    sid=v['study_id'];source=run/'inputs'/sid/'panredu_export_response.json'
    records=[a['sections']['analytical']['acquisition_evidence'] for a in v['analyses']]
    missing=[];export_sha=None
    manifest=json.loads((run/'inputs/fetch_manifest.json').read_text())['requests']
    request=next((r for r in reversed(manifest) if r['file']==f'{sid}/panredu_export_response.json'),None)
    pinned=pinned_panredu(sid)
    retrieved=pinned is not None or (source.exists() and request is not None and not request.get('error') and request.get('http_status')==200)
    if retrieved:
        if pinned is not None:
            frame,content,export_sha=pinned_panredu_table(sid,acq,pinned)
        else:
            frame,content=panredu_table(source,sid,acq)
            export_sha=hashlib.sha256(content.encode()).hexdigest()
        export=out/'external_evidence'/sid/'panredu_export.csv';export.parent.mkdir(parents=True,exist_ok=True);export.write_text(content)
        summarize=original_redu_summary(acq)
        rawsets={}
        for row in records:
            aid=row['analysis_id'];m=acq.read_mwtab(live/sid/aid/'mwtab'/f'{aid}_mwtab.txt')
            for group in ax.FIELD_GROUPS.values():
                for key in group:
                    if 'redu' in key.lower():row[key]=''
            if not m['has_mwtab']:continue   # build_analysis_evidence.py: no Pan-ReDU join without a mwTab text file
            raw=m['raw_files'];rawsets[(sid,aid)]=raw
            row['redu_study_listed']='yes' if len(frame) else ''
            if len(frame):
                row.update(summarize(frame,'redu_study'))
                matched=frame[frame.base.isin(raw)] if raw else frame.iloc[0:0]
                row['redu_raw_files_matched']=len(matched)
                if len(matched):row.update(summarize(matched,'redu_analysis'))
            agree=lambda a,b:'' if not a or not b or a==acq.IND else 'agree' if a==b else 'ReDU has both classes' if b=='both classes' else 'disagree'
            for scope in ('study','analysis'):
                row['analyser_class_vs_redu_'+scope]=agree(row['resolution_class'],row['redu_'+scope+'_resolution_class'])
            if row['redu_analysis_ms2_status_strict'] in ('present','both'):
                extra='Pan-ReDU: MS2 spectra in matched files'
                row['items_detected']='; '.join(x for x in (row['items_detected'],extra) if x)
        shortlist_keys=('panredu_ms2_evidence_status','panredu_ms2_link_scope','panredu_ms2_raw_file_coverage_pct',
                        'panredu_ms2_shared_raw_files','panredu_ms2_analysis_shortlist')
        assessed=acq.add_ms2_shortlist(pd.DataFrame(records).drop(columns=list(shortlist_keys)),frame,rawsets)
        # The reviewed acquisition block stores TSV strings, not JSON numbers.
        for row in assessed.to_dict('records'):
            target=next(x for x in records if x['analysis_id']==row['analysis_id'])
            for key,value in row.items():
                if 'redu' in key.lower():
                    # The original whole-cohort TSV stores nullable file counts
                    # as floats; retain that lexical schema for compatibility.
                    nullable_count=key=='redu_raw_files_matched' or (key.startswith(('redu_study_','redu_analysis_'))
                        and (key.endswith('_files') or '_files_' in key))
                    target[key]=str(float(value)) if nullable_count and value!='' else str(value)
    else:
        missing=[{'analysis_id':a['analysis_id'],'missing':'Pan-ReDU file-level evidence and its analysis/study joins',
                  'reason':('live external lookup failed; absence must not be inferred: '+str(request.get('error')) if request else 'live external lookup unavailable or not requested; absence must not be inferred')} for a in v['analyses']]
    evidence_path=out/'acquisition_evidence'/f'{sid.lower()}.tsv';evidence_path.parent.mkdir(parents=True,exist_ok=True)
    fields=[k for group in ax.FIELD_GROUPS.values() for k in group]
    with evidence_path.open('w',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,delimiter='\t');writer.writeheader();writer.writerows(records)
    code=Path(acq.__file__);scan=Path(acq.SM.__file__)
    v.setdefault('study_sections',{})['analytical']={'acquisition_context':{
        'schema':ax.SCHEMA,'informational':True,'provenance':{
            'build_code_sha256':hashlib.sha256(code.read_bytes()).hexdigest(),
            'datatable_endpoint':'/datatable/','embedded_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'evidence_sha256':hashlib.sha256(evidence_path.read_bytes()).hexdigest(),'evidence_table':str(evidence_path),
            'frozen_source_dump':None,'live_source_root':str(live),
            'panredu_export_sha256':export_sha,'panredu_source':('all_sampleinformation.tsv' if pinned is not None else 'live filtered study export') if retrieved else None,
            'panredu_url':'https://redu.gnps2.org/selection/','rules_file':str(code.parent/'RULES.md'),
            'rules_sha256':hashlib.sha256((code.parent/'RULES.md').read_bytes()).hexdigest(),
            'scan_mode_code_sha256':hashlib.sha256(scan.read_bytes()).hexdigest(),
            'scope':'One row per study–analysis record; supporting evidence does not assign acquisition type.'}}}
    return missing
