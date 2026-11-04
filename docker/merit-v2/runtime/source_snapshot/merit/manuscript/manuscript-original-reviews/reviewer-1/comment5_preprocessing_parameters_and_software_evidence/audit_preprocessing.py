#!/usr/bin/env python3
"""Independent, exhaustive deposited mwTab metadata inventory; no MERIT imports.

No numerical values are reinterpreted and no processing operation is inferred.
Tags identify text to inspect, not verified processing or a complete parameter set.
Both source formats are retained; neither takes precedence.
"""
import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import json
import re
from pathlib import Path

BASE = Path('/home/shayantan/metabolomics/ML-ready/deploy/merit-v2-docker-2026-11-05/docker/merit-v2/runtime/source_snapshot')
DEFAULT_DUMP = BASE / 'mw-dump-latest-18thSep-2026-version'
DEFAULT_OUT = Path(__file__).resolve().parent

STAGES = {
 'file_conversion': r'\b(?:mzML|mzXML|netCDF|cdf files?|MSConvert|msconvert|ReAdW|raw[ -]?(?:file|data) conversion|convert(?:ed|ing)? (?:the )?(?:raw|vendor|data|files?|spectra))\b',
 'centroiding': r'\bcentroid(?:ed|ing|al)?\b',
 'peak_detection_integration': r'\b(?:peak[ -]?(?:pick(?:ing|ed)?|detect(?:ion|ed)?|integrat(?:ion|ed)?|extract(?:ion|ed)?)|(?:feature|signal)[ -]?(?:detect(?:ion|ed)?|extract(?:ion|ed)?)|integrat(?:ion|ed|ing) (?:of )?(?:the )?(?:chromatograph\w*|peaks?|signals?)|centWave|matchedFilter|findChromPeaks)\b',
 'deconvolution': r'\b(?:deconvol\w*|AMDIS|MS[ -]?DIAL)\b',
 'isotopes_adducts': r'\b(?:deisotop\w*|de[ -]isotop\w*|(?:isotop\w*|adduct) (?:group\w*|cluster\w*|annotat\w*|remov\w*|correct\w*)|CAMERA|isotope correction|natural abundance correction|isotopologue correction)\b',
 'rt_alignment': r'\b(?:align(?:ment|ed|ing)?|retention[ -]time correct\w*|RT correct\w*|obiwarp|LOESS RT|warp(?:ing|ed)?|adjustRtime)\b',
 'feature_grouping_matching': r'\b(?:peak (?:group\w*|match\w*|correspondence)|feature (?:group\w*|match\w*|correspondence)|groupChromPeaks|groupPeaks|PeakDensityParam|minFraction|minfrac|correspondence analysis)\b',
 'signal_noise_thresholds': r'\b(?:signal[ -]to[ -]noise|signal/noise|S/N|SNR|snthresh|noise[ -]?(?:level|threshold|filter)|(?:intensity|height|area|abundance) threshold|minimum (?:peak )?(?:height|intensity)|mass tolerance|m/z tolerance|mz tolerance)\b',
 'prevalence_missingness_filter': r'\b(?:minFraction|minfrac|missing (?:value|data) (?:filter\w*|threshold)|(?:filter\w*|remov\w*|retain\w*|present|detect\w*|observ\w*)[^.;\n]{0,65}\b(?:%|percent|fraction|proportion|samples|missing)|(?:80|50|20)[ -]?(?:percent|%) rule|prevalence filter\w*|occupancy)\b',
 'feature_filtering': r'\b(?:filter(?:ing|ed)?|features? (?:were )?(?:remov\w*|exclud\w*|retain\w*)|remov\w* (?:of )?(?:the )?(?:features?|metabolites?|peaks?|ions?)|(?:coefficient of variation|relative standard deviation|RSD|CV)[^.;\n]{0,65}(?:threshold|filter|%|percent))\b',
 'blank_background_filter': r'\b(?:(?:blank|background)[ -]?(?:subtract\w*|correct\w*|filter\w*|remov\w*)|(?:blank|background)[^.;\n]{0,65}(?:ratio|fold|threshold|subtract|filter)|signal[ -]to[ -]blank)\b',
 'gap_filling': r'\b(?:gap[ -]?fill\w*|fillChromPeaks|fillPeaks|peak[ -]?fill\w*|missing peak(?:s)? (?:were )?(?:fill\w*|integrat\w*)|re[ -]?integrat\w*)\b',
 'imputation_replacement': r'\b(?:imput\w*|(?:missing|undetected|non[ -]?detect\w*|absent) (?:values?|entries|signals?)[^.;\n]{0,90}(?:replac\w*|substitut\w*|assign\w*|set |zero|minimum|half)|(?:replac\w*|substitut\w*)[^.;\n]{0,65}(?:zero|minimum|LOD|LOQ)|half[ -](?:minimum|LOD|LOQ)|k[ -]?(?:nearest[ -]?neighbou?rs?|NN)|random forest imput\w*)\b',
 'normalization': r'\b(?:normali[sz]\w*|PQN|probabilistic quotient|total ion (?:current|count)|total (?:peak )?area|median fold change|quantile norm\w*|TIC norm\w*)\b',
 'scaling': r'\b(?:autoscal\w*|auto[ -]scal\w*|pareto|unit[ -]variance|mean[ -]center\w*|mean[ -]centr\w*|center(?:ed|ing)|centr(?:ed|ing)|z[ -]score|range[ -]scal\w*|scal(?:ed|ing))\b',
 'transformation': r'\b(?:log(?:2|10)?[ -]?(?:transform\w*|scal\w*)|logarithm\w*|log2|log10|cube[ -]root|square[ -]root|Box[ -]Cox|arcsinh|glog|generalized log|variance[ -]stabili\w*)\b',
 'drift_batch_correction': r'\b(?:(?:batch|drift|intensity drop|signal drift|run[ -]order) correct\w*|correct\w*[^.;\n]{0,50}(?:batch|drift)|QC[ -]?(?:RLSC|RSC|RFSC)|LOESS|LOWESS|ComBat|SERRF|RUV|systematic error removal|QC[ -]based robust|quality control[^.;\n]{0,35}(?:correct\w*|spline))\b',
 'nmr_processing': r'\b(?:apodiz\w*|apodis\w*|baseline correct\w*|phas(?:e|ing) correct\w*|zero[ -]fill\w*|Fourier transform\w*|line broaden\w*|(?:spectral|spectra|NMR|adaptive|intelligent)[ -]?(?:bin\w*|bucket\w*)|binning|bucketing|chemical[ -]shift referenc\w*|water (?:region|signal)[^.;\n]{0,35}(?:exclud\w*|remov\w*))\b',
 'annotation_identification': r'\b(?:annotat\w*|identif\w*|library (?:search\w*|match\w*|compar\w*)|spectral (?:search\w*|match\w*)|metabolite assign\w*|feature assign\w*|MS[ -]?FINDER|MS[ -]?DIAL|LipidSearch|LipidMatch|LipidBlast|SIRIUS|Chenomx|NIST|MassBank|GNPS|MetFrag)\b',
 'quantification_calibration': r'\b(?:quantif\w*|quantitat\w*|calibrat\w*|standard curve|internal standard|response factor|isotope dilution)\b',
 'statistics_downstream': r'\b(?:PCA|PLS[ -]?DA|OPLS[ -]?DA|principal component|partial least squares|t[ -]test|ANOVA|multivariate|univariate|statistical analysis|machine learning|random forest|MetaboAnalyst)\b',
 'generic_processing': r'\b(?:(?:data|spectr\w*|chromatogram\w*)[ -]?(?:pre[ -]?process\w*|process\w*|analy[sz]\w*|handl\w*)|pre[ -]?process\w*|post[ -]?process\w*|processing parameters|processing software|processing pipeline)\b',
}

SOFTWARE = {
 'XCMS': r'\bXCMS\b', 'IPO': r'\bIPO\b|Isotopologue Parameters Optimization',
 'CAMERA': r'\bCAMERA\b', 'MZmine': r'\bMZ[ -]?mine(?:\s*[234])?\b',
 'MS-DIAL': r'\bMS[ -]?DIAL\b', 'MS-FINDER': r'\bMS[ -]?FINDER\b',
 'MetaboScape': r'\bMetaboScape\b', 'Progenesis': r'\bProgenesis\b',
 'Compound Discoverer': r'\bCompound Discoverer\b', 'SIEVE': r'\bSIEVE\b',
 'MarkerLynx': r'\bMarker[ -]?Lynx\b', 'MassLynx': r'\bMass[ -]?Lynx\b',
 'TargetLynx': r'\bTarget[ -]?Lynx\b', 'UNIFI': r'\bUNIFI\b',
 'MassHunter': r'\bMass[ -]?Hunter\b', 'Mass Profiler Professional': r'\bMass Profiler(?: Professional)?\b|\bMPP\b',
 'Mass Profinder': r'\bMass Profinder\b', 'ChemStation': r'\bChem[ -]?Station\b',
 'Agilent Qualitative Analysis': r'\b(?:Agilent )?Qualitative Analysis\b',
 'Xcalibur': r'\bXcalib[eu]r\b', 'TraceFinder': r'\bTraceFinder\b',
 'LCquan': r'\bLCquan\b', 'Chromeleon': r'\bChromeleon\b',
 'Analyst': r'\bAnalyst(?:\s+(?:software|version|v\.?\s*\d|\d))',
 'MultiQuant': r'\bMulti[ -]?Quant\b', 'SCIEX OS': r'\bSCIEX OS\b',
 'PeakView': r'\bPeak[ -]?View\b', 'MarkerView': r'\bMarker[ -]?View\b',
 'MetabolitePilot': r'\bMetabolite[ -]?Pilot\b', 'LipidView': r'\bLipid[ -]?View\b',
 'LipidSearch': r'\bLipid[ -]?Search\b', 'LipidMatch': r'\bLipid[ -]?Match\b',
 'LipidBlast': r'\bLipid[ -]?Blast\b', 'LipidXplorer': r'\bLipid[ -]?Xplorer\b',
 'Lipid Data Analyzer': r'\bLipid Data Analyzer\b', 'LipidCreator': r'\bLipidCreator\b',
 'LipidAnnotator': r'\bLipid Annotator\b', 'Lipidomics Standards Initiative': r'\bLipidomics Standards Initiative\b',
 'Skyline': r'\bSkyline\b', 'OpenMS': r'\bOpenMS\b', 'ProteoWizard': r'\bProteoWizard\b',
 'MSConvert': r'\bMSConvert\b', 'AMDIS': r'\bAMDIS\b', 'ChromaTOF': r'\bChroma[ -]?TOF\b',
 'ChromaTOF Tile': r'\bChromaTOF Tile\b', 'MetaboliteDetector': r'\bMetaboliteDetector\b',
 'GCMSsolution': r'\bGC[ -]?MS[ -]?solution\b', 'LabSolutions': r'\bLab[ -]?Solutions\b',
 'SmartFormula': r'\bSmartFormula\b', 'DataAnalysis': r'\b(?:Bruker )?DataAnalysis\b',
 'Compass': r'\bCompass\b', 'ProfileAnalysis': r'\bProfileAnalysis\b',
 'MetaboAnalyst': r'\bMetaboAnalyst\b', 'MetAlign': r'\bMetAlign\b',
 'MetaboLyzer': r'\bMetaboLyzer\b', 'Metabolon': r'\bMetabolon\b',
 'El-MAVEN': r'\bEl[ -]?MAVEN\b', 'MAVEN': r'\bMAVEN\b',
 'Trace': r'\b(?:TRACE software|TraceFinder)\b', 'DIALIGN': r'\bDIALIGN\b',
 'iMet-Q': r'\biMet[ -]?Q\b', 'apLCMS': r'\bapLCMS\b',
 'xMSanalyzer': r'\bxMSanalyzer\b', 'xMSannotator': r'\bxMSannotator\b',
 'asari': r'\basari\b', 'patRoon': r'\bpatRoon\b', 'metaX': r'\bmetaX\b',
 'MetFrag': r'\bMetFrag\b', 'SIRIUS': r'\bSIRIUS\b', 'GNPS': r'\bGNPS\b',
 'MS-DaRT': r'\bMS[ -]?DaRT\b', 'Metworks': r'\bMetworks\b',
 'Chenomx': r'\bChenomx\b', 'TopSpin': r'\bTop[ -]?Spin\b',
 'MestReNova': r'\bMestReNova\b|\bMnova\b', 'NMRPipe': r'\bNMRPipe\b',
 'NMRProcFlow': r'\bNMRProcFlow\b', 'rDolphin': r'\brDolphin\b',
 'ASICS': r'\bASICS\b', 'BATMAN': r'\bBATMAN\b', 'Bayesil': r'\bBayesil\b',
 'ACD Labs': r'\bACD(?:[ /-]?Labs|/NMR|/SpecManager)\b', 'SpinWorks': r'\bSpinWorks\b',
 'NUTS': r'\bNUTS\b', 'COLMAR': r'\bCOLMAR\b', 'MetaboLab': r'\bMetaboLab\b',
 'SERRF': r'\bSERRF\b', 'ComBat': r'\bComBat\b',
 'MetNorm': r'\bMetNorm\b', 'NOREVA': r'\bNOREVA\b', 'NOReVA': r'\bNOReVA\b',
 'NIST': r'\bNIST\b', 'MassBank': r'\bMassBank\b', 'HMDB': r'\bHMDB\b',
 'KEGG': r'\bKEGG\b', 'METLIN': r'\bMETLIN\b', 'MoNA': r'\bMoNA\b',
 'MATLAB': r'\bMATLAB\b', 'R': r'\bR (?:software|version|package|program|environment|statistical)|\bR\s*\(v(?:ersion)?[ .]?\d',
 'Python': r'\bPython\b', 'Excel': r'\bExcel\b', 'SPSS': r'\bSPSS\b',
 'SIMCA': r'\bSIMCA(?:[ -]?P)?\b', 'SAS': r'\bSAS (?:software|version|statistical|Institute)\b',
 'GraphPad Prism': r'\b(?:GraphPad|Prism)\b', 'MetaboLights': r'\bMetaboLights\b',
}

PARAMETER_NAMES = r'(?:minFraction|minfrac|minSamples|minsamp|sampleGroups|bw|binSize|binSizeMZ|ppm|peakwidth|peakWidth|mzdiff|snthresh|snt|prefilter|noise|noiselevel|mzCenterFun|integrate|fitgauss|obiwarp|profStep|profstep|mzwid|steps|span|bandwidth|centWave|matchedFilter|PeakDensityParam|CentWaveParam|groupChromPeaks|fillChromPeaks|fillPeaks|retcor|adjustRtime|minimum[ _-]?(?:fraction|peak[ _-]?height|intensity)|mass[ _-]?tolerance|RT[ _-]?tolerance|retention[ _-]?time[ _-]?tolerance|signal[ _-]?to[ _-]?noise|S/N|RSD|CV|LOD|LOQ|LLOQ|LLOQ|FWHM|resolution|bin[ _-]?width)'
PARAM_RX = re.compile(r'(?<!\w)' + PARAMETER_NAMES + r'(?!\w)', re.I)
VERSION_RX = re.compile(r'\b(?:versions?|ver\.?|v\.)\s*[:=]?\s*[A-Z]?\d[\w.\-]*|\b(?:\d+\.\d+(?:\.\d+)*[a-z]?|[AB]\.\d{2}\.\d{2})\b', re.I)
NUMBER_RX = re.compile(r'[-+]?\d+(?:\.\d+)?(?:\s*(?:%|ppm|Da|mDa|s|sec|seconds?|min|minutes?))?', re.I)
ACTION_RX = re.compile(r'\b(?:used|using|processed|performed|applied|converted|detected|picked|integrated|aligned|normalized|normalised|scaled|imputed|filtered|removed|replaced|corrected|quantified|exported|annotated|identified|analy[sz]ed)\b', re.I)
NEG_RX = re.compile(r'\b(?:no|not|without|never|neither|un[ -]?processed|un[ -]?normalized|un[ -]?normalised)\b', re.I)
FILE_RX = re.compile(r'https?://[^\s<>"\]]+|\b[^\s<>"/]+\.(?:pdf|docx?|xlsx?|csv|txt|zip|rar|r|py|m|xml|json|yaml|yml|ipynb|wiff|raw|d)(?=\b|\s|$)', re.I)
DEDICATED_RX = re.compile(r'PROCESSING_PARAMETERS_FILE|SOFTWARE_VERSION|PROCESSED_FILE|ANALYSIS_DETAILS|ANALYSIS_COMMENTS|BASELINE_CORRECTION|BINNED|APODIZATION|LINE_BROADENING|ZERO_FILLING|CHEMICAL_SHIFT_REF|PROCESSING_METHOD', re.I)
PREFIX_RX = re.compile(r'^([A-Z][A-Z0-9_]{0,48}):([^\t]*)\t(.*)$')
QUANT_START = re.compile(r'^(?:MS|NMR)_METABOLITE_DATA_START\s*$')
QUANT_END = re.compile(r'^(?:MS|NMR)_METABOLITE_DATA_END\s*$')
PLACEHOLDER = {'', '-', '--', 'na', 'n/a', 'n.a.', 'none', 'null', 'not applicable', 'not available', 'not provided', 'unknown', 'no', 'n.a'}

class Pairs(list):
    """JSON object preserving duplicate keys and order, distinct from a JSON array."""

def unpack(v):
    if isinstance(v, Pairs):
        keys = [k for k, _ in v]
        if len(set(keys)) != len(keys):
            return {'__duplicate_key_object_pairs__': [[k, unpack(x)] for k,x in v]}
        return {k: unpack(x) for k,x in v}
    if isinstance(v,list): return [unpack(x) for x in v]
    return v

def dumps(x): return json.dumps(x, ensure_ascii=False, separators=(',',':'))
def sha(x): return hashlib.sha256(x).hexdigest()
def clean(x): return re.sub(r'\s+', ' ', str(x)).strip()
def substantive(x): return clean(x).casefold() not in PLACEHOLDER
def text_value(x): return x if isinstance(x,str) else dumps(unpack(x))
def now(): return dt.datetime.now(dt.timezone(dt.timedelta(hours=5,minutes=30))).isoformat()

def write_tsv(path, rows, columns=None):
    rows=list(rows)
    if columns is None: columns=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns,delimiter='\t',extrasaction='raise')
        w.writeheader()
        for row in rows:
            w.writerow({k: dumps(v) if isinstance(v,(list,dict)) else v for k,v in row.items()})

def txt_metadata(raw):
    text=raw.decode('utf-8',errors='surrogateescape')
    lines=text.split('\n')
    kept=[]; spans=[]; start=None; excluded=[]; fields=[]; sections=[]
    current=None; body=False; sample_rows=[]; metabolite_rows=[]
    def flush():
        nonlocal current
        if current is not None:
            current['value']=' '.join(current['segments'])
            fields.append(current);current=None
    for i,line in enumerate(lines,1):
        stripped=line.strip('\r')
        if QUANT_START.fullmatch(stripped.strip()):
            flush();start=i+1;kept.append([i,line]);continue
        if start is not None:
            if QUANT_END.fullmatch(stripped.strip()):
                block='\n'.join(lines[start-1:i-1]).encode('utf-8',errors='surrogateescape')
                excluded.append({'start_line':start,'end_line':i-1,'sha256':sha(block),'bytes_without_last_delimiter':len(block)})
                kept.append([i,line]);start=None
            continue
        kept.append([i,line])
        if stripped.startswith('#'):
            flush();sections.append([i,stripped]);body=stripped.startswith('#METABOLITES')
            continue
        if body:
            if stripped:metabolite_rows.append([i,stripped])
            continue
        if stripped.startswith('SUBJECT_SAMPLE_FACTORS'):
            flush();sample_rows.append([i,stripped]);continue
        m=PREFIX_RX.match(stripped)
        if m:
            key=m[1]+':'+m[2].strip().rstrip(':')
            if current and current['field']==key and current['line_end']==i-1:
                current['segments'].append(m[3]);current['line_end']=i
            else:
                flush();current={'field':key,'section':m[1],'line_start':i,'line_end':i,'segments':[m[3]]}
        elif current and stripped and not stripped.startswith(('METABOLITES','MS_METABOLITE_DATA','NMR_METABOLITE_DATA')):
            # Retain malformed/unprefixed continuation text; its exact raw line is archived.
            current['segments'].append(stripped);current['line_end']=i
        else:flush()
    flush()
    if start is not None:
        excluded.append({'start_line':start,'end_line':len(lines),'sha256':sha('\n'.join(lines[start-1:]).encode('utf-8',errors='surrogateescape')),'unterminated':True})
    return {'lines':kept,'excluded_quantitative_spans':excluded,'total_lines':len(lines)},fields,sample_rows,metabolite_rows,sections

def json_metadata(raw):
    obj=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=Pairs)
    excluded=[]; duplicates=[]; fields=[]; sample_entries=[]; metabolite_entries=[]
    def trim(x,path=()):
        if isinstance(x,Pairs):
            seen=collections.Counter(k for k,v in x)
            duplicates.extend({'path':list(path),'key':k,'occurrences':n} for k,n in seen.items() if n>1)
            out=Pairs()
            for k,v in x:
                # Exclude only quantitative arrays, not any descriptive metadata.
                if k.casefold()=='data' and (not path or str(path[-1]).endswith('METABOLITE_DATA')):
                    excluded.append({'path':list(path+(k,)),'rows':len(v) if isinstance(v,list) else None,'canonical_sha256':sha(dumps(unpack(v)).encode())})
                else:out.append((k,trim(v,path+(k,))))
            return out
        if isinstance(x,list):return [trim(v,path+(i,)) for i,v in enumerate(x)]
        return x
    meta=trim(obj)
    if not isinstance(meta,Pairs):return unpack(meta),fields,sample_entries,metabolite_entries,excluded,duplicates
    for section,v in meta:
        if section=='SUBJECT_SAMPLE_FACTORS': sample_entries.append(v);continue
        if isinstance(v,Pairs):
            for key,value in v:
                if key=='Metabolites':metabolite_entries.append((section,value));continue
                fields.append({'field':section+':'+key,'section':section,'path':[section,key],'value':text_value(value)})
        else:fields.append({'field':section,'section':section,'path':[section],'value':text_value(v)})
    return unpack(meta),fields,sample_entries,metabolite_entries,excluded,duplicates

def scope(field):
    s=field.split(':',1)[0]
    if s in {'PR','ST','PROJECT','STUDY'}:return 'study_or_project_text'
    if s in {'SP','SAMPLEPREP','CO','COLLECTION','TR','TREATMENT','SU','SUBJECT'}:return 'collection_preparation_or_subject_context'
    if s in {'AN','MS','NM','CH','ANALYSIS','CHROMATOGRAPHY'}:return 'analysis_metadata'
    if s.endswith('METABOLITE_DATA'):return 'quantitative_block_metadata'
    return 'other_metadata'

def origin(field):
    if re.search(r'(?::UNITS|:Units)$',field):return 'units_declaration_not_processing_method'
    if re.search(r'PROCESSING_STORAGE|PROCESSING_METHOD',field):return 'sample_preparation_processing_not_necessarily_computational'
    if re.search(r'PARAMETERS_FILE|PROTOCOL_FILE|METHODS_FILENAME|PROCESSED_FILE',field):return 'referenced_file_not_file_contents'
    if 'SOFTWARE_VERSION' in field:return 'software_field_role_not_assumed'
    if re.search(r'BASELINE_CORRECTION|BINNED|APODIZATION|LINE_BROADENING|ZERO_FILLING|CHEMICAL_SHIFT_REF',field):return 'structured_nmr_processing'
    return 'narrative_or_other_metadata'

def software_hits(text, patterns):
    """Literal alternatives are only a fast prefilter; original regex decides."""
    lower=text.casefold();result=[]
    for name,rx in patterns.items():
        seeds=re.findall(r'\\b([A-Za-z][A-Za-z0-9]*)',rx.pattern)
        if seeds and all(len(s)>=3 for s in seeds) and not any(s.casefold() in lower for s in seeds):continue
        if rx.search(text):result.append(name)
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dump',type=Path,default=DEFAULT_DUMP);ap.add_argument('--out',type=Path,default=DEFAULT_OUT);ap.add_argument('--shard',type=int,default=0);ap.add_argument('--shards',type=int,default=1)
    args=ap.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True)
    started=now(); stage_rx={k:re.compile(v,re.I) for k,v in STAGES.items()};software_rx={k:re.compile(v,re.I) for k,v in SOFTWARE.items()}
    (out/'rules.json').write_text(json.dumps({'warning':'Mention tags only; no verified execution, causal attribution, parameter completeness or defaults inferred. Counts union TXT and JSON, retain source scope.','stage_patterns':STAGES,'software_resource_service_patterns':SOFTWARE,'parameter_names_pattern':PARAMETER_NAMES,'dedicated_field_pattern':DEDICATED_RX.pattern,'placeholders':sorted(PLACEHOLDER)},indent=2))
    inventory=[]
    for manifest in sorted(args.dump.glob('ST*/manifest.json')):
        m=json.loads(manifest.read_text());sid=manifest.parent.name
        for aid in sorted(m['analyses']):inventory.append((sid,aid))
    assert len(inventory)==len(set(inventory))==6696,(len(inventory),len(set(inventory)))
    assert len({s for s,a in inventory})==4121
    expected={(sid,aid,fmt) for sid,aid in inventory for fmt in ('txt','json')}
    actual={(f.parts[-4],f.parts[-3],f.suffix[1:]) for f in args.dump.glob('ST*/AN*/mwtab/*_mwtab.*') if f.suffix in {'.txt','.json'}}
    assert actual <= expected, sorted(actual-expected)
    inventory=inventory[args.shard::args.shards]
    analyses=[];sources=[];errors=[];candidates=[];parameter_rows=[];software_rows=[];reference_rows=[];ancillary_rows=[];field_counters={};all_field_rows=0
    stage_analysis=collections.defaultdict(set);tool_analysis=collections.defaultdict(set)
    all_f=gzip.open(out/'all_metadata_fields.jsonl.gz','wt',encoding='utf-8',errors='surrogateescape')
    arc=gzip.open(out/'metadata_lossless_archive.jsonl.gz','wt',encoding='utf-8',errors='surrogateescape')
    evidence_f=gzip.open(out/'processing_evidence_full.jsonl.gz','wt',encoding='utf-8',errors='surrogateescape')
    def consider(field,sid,aid,fmt,path):
        nonlocal all_field_rows
        all_field_rows+=1;value=field['value'];eid=f'{sid}/{aid}/{fmt}/{all_field_rows:08d}'
        row={'evidence_id':eid,'study_id':sid,'analysis_id':aid,'format':fmt,'source_path':str(path),'scope':scope(field['field']),'origin':origin(field['field']),**field}
        all_f.write(dumps(row)+'\n')
        fc=field_counters.setdefault((fmt,field['field']),{'records':0,'substantive_records':0,'analyses':set(),'substantive_analyses':set(),'candidate_analyses':set(),'examples':[]})
        fc['records']+=1;fc['analyses'].add((sid,aid))
        if substantive(value):fc['substantive_records']+=1;fc['substantive_analyses'].add((sid,aid))
        if len(fc['examples'])<3 and substantive(value):fc['examples'].append({'study_id':sid,'analysis_id':aid,'value':value})
        # Join a hyphenated word broken at a physical wrapped line for search only.
        search=re.sub(r'(?<=[a-z])-\s+(?=[a-z])','',value,flags=re.I)
        stages=[k for k,r in stage_rx.items() if r.search(search)]
        tools=software_hits(search,software_rx)
        dedicated=bool(DEDICATED_RX.search(field['field']))
        if not substantive(value) or not (stages or tools or dedicated):return None
        fc['candidate_analyses'].add((sid,aid))
        row.update(stage_mentions=stages,software_resource_service_mentions=tools,dedicated_field=dedicated,contains_action_word=bool(ACTION_RX.search(search)),contains_negation_word=bool(NEG_RX.search(search)))
        evidence_f.write(dumps(row)+'\n')
        candidate={k:row[k] for k in ('evidence_id','study_id','analysis_id','format','source_path','scope','origin','field','stage_mentions','software_resource_service_mentions','dedicated_field','contains_action_word','contains_negation_word')}
        candidate.update(full_value=value,normalized_value_sha256=sha(clean(value).encode('utf-8',errors='surrogateescape')),line_start=field.get('line_start',''),line_end=field.get('line_end',''),json_path=field.get('path',[]))
        candidates.append(candidate)
        for stage in stages:stage_analysis[stage].add((sid,aid))
        for tool in tools:
            tool_analysis[tool].add((sid,aid))
            for hit in list(software_rx[tool].finditer(search))[:20]:
                context=search[max(0,hit.start()-140):min(len(search),hit.end()+220)]
                software_rows.append({'study_id':sid,'analysis_id':aid,'evidence_id':eid,'software_resource_or_service':tool,'deposited_match':hit.group(),'context':context,'version_like_strings_in_context':list(dict.fromkeys(m.group() for m in VERSION_RX.finditer(context))),'scope':row['scope'],'origin':row['origin'],'field':field['field']})
        for hit in PARAM_RX.finditer(search):
            lo=max(0,hit.start()-120);hi=min(len(search),hit.end()+240);context=search[lo:hi]
            parameter_rows.append({'study_id':sid,'analysis_id':aid,'evidence_id':eid,'parameter_term':hit.group(),'context':context,'numeric_strings_in_context':list(dict.fromkeys(m.group() for m in NUMBER_RX.finditer(context))),'stage_mentions':stages,'field':field['field'],'scope':row['scope'],'origin':row['origin'],'caution':'Candidate context; numbers may belong to acquisition, citations, software versions or other parameters. No automatic parameter value assignment.'})
        for hit in FILE_RX.finditer(search):reference_rows.append({'study_id':sid,'analysis_id':aid,'evidence_id':eid,'reference':hit.group(),'field':field['field'],'contents_fetched':False})
        return row
    for n,(sid,aid) in enumerate(inventory,1):
        entry={'study_id':sid,'analysis_id':aid,'txt_present':False,'json_present':False,'txt_parsed':False,'json_parsed':False,'txt_field_count':0,'json_field_count':0,'analysis_type_declarations':[],'stage_mentions':[],'software_resource_service_mentions':[],'dedicated_processing_field_names':[],'candidate_evidence_ids':[],'has_analysis_metadata_processing_mention':False,'has_parameter_term_candidate':False}
        for fmt in ('txt','json'):
            path=args.dump/sid/aid/'mwtab'/f'{aid}_mwtab.{fmt}'
            if not path.exists():continue
            entry[fmt+'_present']=True;raw=path.read_bytes();before_stat=path.stat()
            source={'study_id':sid,'analysis_id':aid,'format':fmt,'path':str(path),'bytes':len(raw),'sha256':sha(raw),'mtime_ns':before_stat.st_mtime_ns,'parse_ok':False}
            sources.append(source)
            try:
                if fmt=='txt':
                    meta,fields,samples,metabolites,sections=txt_metadata(raw)
                    source.update(excluded_quantitative_spans=meta['excluded_quantitative_spans'],archived_nonquantitative_lines=len(meta['lines']),duplicate_json_keys=[])
                    ancillary=samples+metabolites
                    # Entire rows stay archived. Only candidate rows are exported as secondary hints.
                    for line_no,line in ancillary:
                        tags=[k for k,r in stage_rx.items() if r.search(line)]
                        tools=software_hits(line,software_rx)
                        if tags or tools:ancillary_rows.append({'study_id':sid,'analysis_id':aid,'format':fmt,'source_path':str(path),'location':line_no,'row_kind':'sample_metadata' if line.startswith('SUBJECT_SAMPLE_FACTORS') else 'metabolite_metadata','stage_mentions':tags,'software_resource_service_mentions':tools,'full_row':line,'caution':'Sample/metabolite metadata hint only, excluded from analysis processing counts.'})
                else:
                    meta,fields,samples,metabolites,excluded,duplicates=json_metadata(raw)
                    source.update(excluded_quantitative_arrays=excluded,duplicate_json_keys=duplicates)
                    # Preserve every nonquantitative JSON value in archive. Text equivalents
                    # of sample/metabolite metadata are searched separately below.
                    for kind,vals in [('sample_metadata',samples),('metabolite_metadata',metabolites)]:
                        for ix,v in enumerate(vals):
                            full=text_value(v);tags=[k for k,r in stage_rx.items() if r.search(full)];tools=software_hits(full,software_rx)
                            if tags or tools:ancillary_rows.append({'study_id':sid,'analysis_id':aid,'format':fmt,'source_path':str(path),'location':ix,'row_kind':kind,'stage_mentions':tags,'software_resource_service_mentions':tools,'full_row':full,'caution':'Sample/metabolite metadata hint only, excluded from analysis processing counts.'})
                arc.write(dumps({'study_id':sid,'analysis_id':aid,'format':fmt,'source_path':str(path),'source_sha256':source['sha256'],'metadata':meta})+'\n')
                source['parse_ok']=True;entry[fmt+'_parsed']=True;entry[fmt+'_field_count']=len(fields)
                for f in fields:
                    if f['field'].endswith(':ANALYSIS_TYPE') and substantive(f['value']):entry['analysis_type_declarations'].append({'format':fmt,'field':f['field'],'value':f['value']})
                    ev=consider(f,sid,aid,fmt,path)
                    if ev:
                        entry['stage_mentions']+=ev['stage_mentions'];entry['software_resource_service_mentions']+=ev['software_resource_service_mentions'];entry['candidate_evidence_ids'].append(ev['evidence_id'])
                        if ev['dedicated_field']:entry['dedicated_processing_field_names'].append(f['field'])
                        if ev['scope']=='analysis_metadata' and ev['stage_mentions'] and ev['origin']=='narrative_or_other_metadata':entry['has_analysis_metadata_processing_mention']=True
                        if PARAM_RX.search(f['value']):entry['has_parameter_term_candidate']=True
                assert path.stat().st_mtime_ns==before_stat.st_mtime_ns, 'Source changed during read'
            except Exception as exc:
                errors.append({'study_id':sid,'analysis_id':aid,'format':fmt,'path':str(path),'error':repr(exc)})
        for k in ('stage_mentions','software_resource_service_mentions','dedicated_processing_field_names'):entry[k]=sorted(set(entry[k]))
        analyses.append(entry)
        if n%100==0 or n==len(inventory):
            progress={'started':started,'updated':now(),'analyses_processed':n,'analyses_total':len(inventory),'candidate_fields':len(candidates),'errors':len(errors),'last_record':sid+'/'+aid}
            (out/'progress.json').write_text(json.dumps(progress,indent=2));print(dumps(progress),flush=True)
    all_f.close();arc.close();evidence_f.close()
    write_tsv(out/'per_analysis_processing_evidence.tsv',analyses)
    write_tsv(out/'processing_evidence_full.tsv',candidates)
    write_tsv(out/'software_mentions_with_context.tsv',software_rows)
    write_tsv(out/'parameter_term_candidates.tsv',parameter_rows)
    write_tsv(out/'referenced_files_and_links.tsv',reference_rows)
    write_tsv(out/'ancillary_sample_metabolite_mentions.tsv',ancillary_rows)
    field_rows=[]
    for (fmt,name),v in sorted(field_counters.items()):
        field_rows.append({'format':fmt,'field':name,'records':v['records'],'analyses':len(v['analyses']),'substantive_records':v['substantive_records'],'substantive_analyses':len(v['substantive_analyses']),'candidate_analyses':len(v['candidate_analyses']),'full_examples':v['examples']})
    write_tsv(out/'all_field_inventory.tsv',field_rows)
    def counts(sets):return [{'name':k,'analyses':len(v),'studies':len({s for s,a in v}),'pct_6696':100*len(v)/6696} for k,v in sorted(sets.items(),key=lambda x:(-len(x[1]),x[0]))]
    write_tsv(out/'stage_mention_distribution.tsv',counts(stage_analysis))
    write_tsv(out/'software_mention_distribution.tsv',counts(tool_analysis))
    (out/'source_manifest.json').write_text(json.dumps(sources,indent=2))
    (out/'errors.json').write_text(json.dumps(errors,indent=2))
    summary={'started':started,'finished':now(),'dump':str(args.dump),'studies':4121,'analyses':len(analyses),'source_files':len(sources),'txt_files':sum(x['format']=='txt' for x in sources),'json_files':sum(x['format']=='json' for x in sources),'parsed_files':sum(x['parse_ok'] for x in sources),'both_formats':sum(x['txt_present'] and x['json_present'] for x in analyses),'neither_format':sum(not x['txt_present'] and not x['json_present'] for x in analyses),'all_metadata_field_records':all_field_rows,'candidate_field_records':len(candidates),'analyses_with_candidate_field':sum(bool(x['candidate_evidence_ids']) for x in analyses),'analyses_with_stage_term':sum(bool(x['stage_mentions']) for x in analyses),'analyses_with_software_resource_service_term':sum(bool(x['software_resource_service_mentions']) for x in analyses),'analyses_with_analysis_scope_narrative_stage_term':sum(x['has_analysis_metadata_processing_mention'] for x in analyses),'analyses_with_parameter_term_candidate':sum(x['has_parameter_term_candidate'] for x in analyses),'analyses_with_dedicated_processing_field':sum(bool(x['dedicated_processing_field_names']) for x in analyses),'parameter_candidates':len(parameter_rows),'software_mention_contexts':len(software_rows),'referenced_files_links':len(reference_rows),'ancillary_candidate_records':len(ancillary_rows),'duplicate_json_key_objects':sum(len(x.get('duplicate_json_keys',[])) for x in sources),'errors':len(errors),'warning':'All counts are literal evidence-screening counts, not verified processing use or parameter-complete pipelines. Full values, scopes, negations and both source formats are retained.'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2),flush=True)
    assert not errors,errors

if __name__=='__main__':main()
