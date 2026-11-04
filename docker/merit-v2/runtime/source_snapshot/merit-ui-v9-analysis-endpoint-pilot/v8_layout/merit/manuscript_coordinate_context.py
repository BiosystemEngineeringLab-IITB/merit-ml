"""Analytical-coordinate and LC-context evidence, with manuscript denominators.

Figure 3C: populated fields in every analysis's JSON Metabolites annotation blocks.
S6A: deduplicated named MS data-block labels; same-record mass/RT coverage.
S6B: numeric RT-bearing named MS analyses; RI and diagnostic fields excluded.
S6C: explicitly LC analyses with named RT/RI coverage; dedicated CH fields only.
Text S11: declared adducts, with its own stricter named-feature/exclusion cohort.
UI reported_adducts: declarations in all analyses, with the same notation/column rules.
These are separate descriptions, never substituted for one another's denominator.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from merit.manuscript_coordinate_fields import _classify_mass_rt_like_field as classify, _mass_rt_value_is_present as present
from merit.manuscript_rt_units import fieldkind, numeric, unitsfrom, canon, TAIL
from merit.manuscript_feature_labels import load_records

MASS = {'mz-like', 'mass-like', 'ambiguous'}
NUM = r'\d+(?:\.\d+)?'
UNNAMED = re.compile(rf'^\s*{NUM}\s*(?:[_@/-]\s*{NUM})?\s*(?:m/?z|min|s|ppm)?\s*$', re.I)
FORMULA = re.compile(r'^(?:(?:Cl|Br|Na|Si|Se|C|H|N|O|S|P|F|I|K)\d*\s*)+(?:\s*\+\s*[\d.]+)?$')
FEATURE_ID = re.compile(rf'^\s*(?:{NUM}\s*m/?z\s*[_@/ -]?\s*{NUM}\s*(?:ri|rt|min|s|n)?|{NUM}\s*(?:ri|rt|min)?\s*[_@/]\s*{NUM}\s*(?:n|m/?z|ri|rt)?|M{NUM}T{NUM}(?:_\d+)?)\s*$', re.I)
PLACEHOLDER = {'', '-', '--', 'na', 'n/a', 'none', 'null', 'not applicable', 'not reported', 'unknown', 'nd', 'n.a.', '.'}
LC = re.compile(r'revers|hilic|philic|normal phase|ion exchange|ion pair|ion chrom|\brp\b|\blc\b|lc[/-]?ms|u?hplc|uplc|\bcsh\b|\bc18\b|\bc30\b|\bc8\b', re.I)
NOT_LC = re.compile(r'\bgc\b|gas|\bce\b|capillary electro|infusion|flow induction', re.I)
PCT = re.compile(rf'{NUM}\s*%|\b\d{{1,3}}\s*:\s*\d{{1,3}}\b|%\s*[A-D]\b')
TIME = re.compile(rf'{NUM}\s*(?:min\b|mins\b|minutes?\b|sec\b|seconds?\b|s\b|h\b)|\bt\s*=\s*{NUM}|{NUM}\s*(?:-|–|to)\s*{NUM}\s*(?:min|s\b)|time\s*[\(\[]?\s*(?:min|s|sec)\b', re.I)
FLOW = re.compile(rf'{NUM}\s*(?:n|u|µ|μ|m)l\s*(?:/|per|\s)\s*min', re.I)
TEMP = re.compile(rf'{NUM}|ambient|room\s*temp', re.I)
DIMS = re.compile(rf'{NUM}\s*(?:mm|cm)?\s*[x×*]\s*{NUM}|{NUM}\s*(?:mm|cm)[^\d]{{0,20}}[x×*]\s*{NUM}', re.I)
PARTICLE = re.compile(rf'{NUM}\s*-?\s*(?:Â)?(?:µm|μm|um|micron)', re.I)
COMPONENTS = ('column', 'mobile_phase', 'gradient', 'flow_rate', 'temperature', 'dimensions', 'particle_size')


def key(value: str) -> str:
    return re.sub(r'\s+', ' ', value.strip().lower()).replace(',', '_').replace("'", '^')


def coverage_named(value: str) -> bool:
    t = value.strip()
    return bool(t) and t.lower() not in {'unknown', 'na', 'n/a', '-'} and not UNNAMED.match(t) and not FORMULA.match(t)


def coordinate_value(value: str) -> bool:
    # S6A deliberately retains the manuscript's digit-containing populated values.
    return present(value) and bool(re.search(r'\d', value))


def reported(value: str) -> bool:
    return value.strip().lower() not in PLACEHOLDER


def lc_components(fields: dict[str, str]) -> dict:
    col = fields.get('COLUMN_NAME', '')
    grad = fields.get('FLOW_GRADIENT', '')
    temperature = fields.get('COLUMN_TEMPERATURE', '')
    out = {
        'column': int(reported(col)),
        'mobile_phase': int(reported(fields.get('SOLVENT_A', '')) and reported(fields.get('SOLVENT_B', ''))),
        'gradient': int(reported(grad) and bool(PCT.search(grad)) and (bool(TIME.search(grad)) or 'isocratic' in grad.lower())),
        'flow_rate': int(bool(FLOW.search(fields.get('FLOW_RATE', '')))),
        'temperature': int(reported(temperature) and bool(TEMP.search(temperature))),
        'dimensions': int(bool(DIMS.search(col))),
        'particle_size': int(bool(PARTICLE.search(col))),
    }
    out['basic'] = int(out['column'] and out['mobile_phase'] and out['gradient'])
    out['extended'] = int(out['basic'] and out['flow_rate'] and out['temperature'])
    out['full'] = int(out['extended'] and out['dimensions'] and out['particle_size'])
    out['any_component'] = int(any(out[k] for k in COMPONENTS))
    return out


def parse_text(text: str) -> dict:
    # splitlines is the manuscript metadata parser's delimiter (matrix reader is unchanged).
    lines = text.splitlines()
    features, annotations, ch, scalar = [], [], {}, {}
    declared, raw_units = set(), []
    valid_matrix = False
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        m = re.match(r'^CH:([A-Z_]+)\s*\t(.*)$', line)
        if m:
            ch[m.group(1)] = (ch.get(m.group(1), '') + ' ' + m.group(2)).strip()
        m = re.match(r'^((?:AN|MS):[A-Z_]+)\s*\t(.*)$', line)
        if m and m.group(1) not in scalar:
            scalar[m.group(1)] = m.group(2).strip()
        if re.match(r'^\s*#?[_0-9]*\s*MS:MS_RESULTS_FILE', line):
            for m in re.finditer(r'RT\s*units\s*:\s*([^\t]*)', line, re.I):
                raw_units.append(m.group(1).strip())
                if canon(m.group(1)):
                    declared.add(canon(m.group(1)))
        if s == 'MS_METABOLITE_DATA_START':
            i += 2
            while i < len(lines) and not lines[i].strip().endswith('_DATA_END'):
                cells = lines[i].split('\t')
                if cells[0].strip().lower() != 'factors':
                    features.append(cells[0].strip())
                    if not valid_matrix:
                        for value in cells[1:]:
                            try:
                                # Workbench also deposits '8.12.E-04'; the manuscript numeric reader accepts it.
                                if math.isfinite(float(re.sub(r'\.E([+-])', r'E\1', value, flags=re.I))):
                                    valid_matrix = True
                                    break
                            except ValueError:
                                pass
                i += 1
        elif s == 'METABOLITES_START':
            header = lines[i+1].split('\t') if i+1 < len(lines) else []
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip() != 'METABOLITES_END':
                rows.append(lines[i].split('\t'))
                i += 1
            annotations.append((header, rows))
        i += 1
    return {'features': features, 'annotations': annotations, 'ch': ch, 'scalar': scalar,
            'declared_units': declared, 'raw_units': raw_units, 'valid_named_ms': valid_matrix}


def named_coverage(parsed: dict) -> tuple[dict, dict]:
    feats = {key(n) for n in parsed['features'] if coverage_named(n)} if parsed['valid_named_ms'] else set()
    annotation = defaultdict(list)
    rt_keys, ri_keys, ambiguous_keys = set(), set(), set()
    annotation_units, headers_with_units, ri_unit_headers = set(), set(), set()
    for header, rows in parsed['annotations']:
        classes = [classify(h) for h in header]
        kinds = [fieldkind(h) for h in header]
        for row in rows:
            if not row or not row[0].strip():
                continue
            k = key(row[0])
            mass = any(j < len(row) and c in MASS and coordinate_value(row[j]) for j,c in enumerate(classes) if j)
            mz = any(j < len(row) and c == 'mz-like' and coordinate_value(row[j]) for j,c in enumerate(classes) if j)
            rt = any(j < len(row) and c == 'rt-like' and coordinate_value(row[j]) for j,c in enumerate(classes) if j)
            annotation[k].append((mass,mz,rt))
            if k not in feats:
                continue
            for j,h in enumerate(header):
                if not j or j >= len(row) or kinds[j] == 'other':
                    continue
                kind,value = kinds[j],row[j]
                if kind == 'RI' and unitsfrom(h):
                    ri_unit_headers.add(h)
                if not numeric(value):
                    continue
                if kind == 'RT':
                    rt_keys.add(k)
                    uu = unitsfrom(h)
                    tail = TAIL.search(value)
                    if tail and canon(tail.group(1)):
                        uu.add(canon(tail.group(1)))
                    if uu:
                        annotation_units |= uu
                        headers_with_units.add(h)
                elif kind == 'RI':
                    ri_keys.add(k)
                elif kind == 'ambiguous':
                    ambiguous_keys.add(k)
    coverage = {'named_features': len(feats)}
    for name,position in [('mass',0),('mz_strict',1),('rt',2)]:
        coverage['cov_'+name] = sum(any(row[position] for row in annotation.get(k,[])) for k in feats)
    coverage['cov_either'] = sum(any(r[0] or r[2] for r in annotation.get(k,[])) for k in feats)
    coverage['cov_both'] = sum(any(r[0] and r[2] for r in annotation.get(k,[])) for k in feats)
    coverage['cov_has_annotation_row'] = sum(bool(annotation.get(k,[])) for k in feats)
    detected = parsed['declared_units'] | annotation_units
    units = {'valid_named_RT_features':len(rt_keys),'valid_named_RI_features':len(ri_keys),
             'numeric_ambiguous_features':len(ambiguous_keys),'RT_eligible':int(bool(rt_keys)),
             'RI_only':int(bool(ri_keys) and not rt_keys),
             'unit_category': next(iter(detected)) if len(detected)==1 else 'Mixed units' if detected else 'No detected unit declaration',
             'MS_RESULTS_FILE_units':'|'.join(sorted(parsed['declared_units'])),
             'annotation_units':'|'.join(sorted(annotation_units)),
             'raw_MS_RESULTS_FILE_RT_units':'|'.join(parsed['raw_units']),
             'unit_headers':'|'.join(sorted(headers_with_units)),
             'RI_headers_with_time_unit_label':'|'.join(sorted(ri_unit_headers))}
    return coverage,units


def json_presence(path: Path) -> dict:
    fields,examples = {},[]
    blocks=0
    try:
        payload=json.loads(path.read_text(encoding='utf-8',errors='replace'))
    except (OSError,ValueError):
        return {'present':False,'category':'None','fields':{},'examples':[],'blocks_scanned':0,'json_available':False}
    for block_name,block in (payload.items() if isinstance(payload,dict) else []):
        if not isinstance(block,dict) or not isinstance(block.get('Metabolites'),list):
            continue
        blocks += 1
        for row in block['Metabolites']:
            if not isinstance(row,dict):
                continue
            for name,value in row.items():
                c=classify(name)
                if c and present(value):
                    fields[name]=c
                    if len(examples)<4:
                        examples.append({'field_name':name,'field_class':c,'example_value':str(value)[:80], 'block':block_name})
    mass=bool(set(fields.values()) & MASS)
    rt='rt-like' in fields.values()
    return {'present':bool(fields),'category':'Both' if mass and rt else 'Mass/mz only' if mass else 'RT/RI only' if rt else 'None',
            'fields':fields,'examples':examples,'blocks_scanned':blocks,'json_available':True}


# Declared adduct notation only: no m/z-to-mass inference and no mass-shift calculations.
GROUPS = {s.lower() for s in ('H','Na','K','Li','NH4','Ag','H2O','NH3','Cl','Br','HCOO','FA','HCOOH','CH3COO','OAc','Ac','HAc','CH3COOH','ACN','CH3CN','MeOH','CH3OH','DMSO','TFA','CO2','IsoProp','H2CO2','HCO2','CH3CO2','e')}
CHARGED = {'h':1,'na':1,'k':1,'li':1,'nh4':1,'ag':1,'cl':-1,'br':-1,'hcoo':-1,'ch3coo':-1,'oac':-1,'ac':-1,'hco2':-1,'ch3co2':-1}
ADDUCT = re.compile(r'^\[?\s*(\d*)\s*M\s*((?:\s*[+-]\s*\d*\s*[A-Za-z][A-Za-z0-9]*)*)\s*\]?\s*(\d*)\s*([+-]?)\s*$')
IN_NAME = re.compile(r'(?<![A-Za-z0-9])[\[(]?\s*\d*M\s*(?:[+-]\s*\d*[A-Za-z][A-Za-z0-9]*)*\s*[\])]?\s*(?:\(?\d*[+-]\)?)?(?![A-Za-z0-9])')


def field_adduct(value: str) -> bool:
    t=value.strip().replace('−','-').replace('–','-').replace(' ','').replace('(','').replace(')','')
    t=re.sub(r'M[+-]Ac-H(?![A-Za-z0-9])','M+CH3COO',t)
    m=ADDUCT.fullmatch(t)
    if not m or (not m.group(2) and not m.group(4)):
        return False
    groups=re.findall(r'([+-])(\d*)([A-Za-z][A-Za-z0-9]*)',m.group(2) or '')
    if any(g.lower() not in GROUPS for _,_,g in groups):
        return False
    charge=sum((1 if sign=='+' else -1)*int(n or 1)*CHARGED.get(g.lower(),0) for sign,n,g in groups)
    return bool(m.group(4) or charge)


def has_field_adduct(value: str) -> bool:
    return any(field_adduct(v) for v in re.split(r'[;,|/]|\band\b',value) if v.strip())


def label_adduct(value: str) -> str:
    for m in IN_NAME.finditer(value):
        raw=m.group(0).strip()
        groups=re.findall(r'[+-]\s*\d*([A-Za-z][A-Za-z0-9]*)',re.sub(r'\(?\d*[+-]\)?$','',raw))
        if re.search(r'M\s*\]',raw) and not groups and re.search(r'[+-]\)?$',raw):
            return raw
        if groups and all(g.lower() in GROUPS for g in groups):
            return raw
    return ''


def platform_lc(parsed: dict) -> bool:
    c=parsed['ch'].get('CHROMATOGRAPHY_TYPE','')
    a=parsed['scalar'].get('AN:ANALYSIS_TYPE','')
    # Text S11 uses Figure 3C's RP/HILIC/other LC categories (distinct from S6C's explicit-LC filter).
    if a.lower()=='nmr': return False
    rp=bool(re.search(r'revers|\brp\b|c18|c30|\bc8\b|\bcsh\b|phenyl|pfp|t3\b',c,re.I))
    hilic=bool(re.search(r'hilic|philic|zic|amide',c,re.I))
    if rp != hilic: return True
    if re.search(r'\bgc|gcxgc|gas|\bce\b|capillary electro|infusion|\bfia\b|flow injection|flow induction',c,re.I): return False
    return bool((rp and hilic) or re.search(r'normal phase|ion exchange|ion pair|ion chrom|\bic\b|\blc\b|lc[/-]?ms|u?hplc|uplc|mixed mode|chiral',c,re.I))


def declared_adducts(parsed: dict, refs: dict[str,str]) -> dict:
    out={'eligible':False,'named_pairs':0,'declared':0,'annotation_field':0,'feature_label':0,'adduct_columns':[], 'examples':[]}
    if not platform_lc(parsed) or not parsed['valid_named_ms']:
        return out
    feats={key(n):n for n in parsed['features'] if coverage_named(n) and not FEATURE_ID.match(n) and refs.get(key(n),'').lower() not in {'standard','unknown'}}
    if not feats:
        return out
    # The manuscript takes the first annotation row for a normalized name; the last annotation header/block is used.
    ann={}
    header=[]
    for header,rows in parsed['annotations']:
        for row in rows:
            if row: ann.setdefault(key(row[0]),[v.strip() for v in row])
    columns=[]
    for j in range(1,len(header)):
        values=[r[j] for r in ann.values() if j<len(r) and r[j] and r[j] not in ('-','NA','n/a','N/A')]
        if values and sum(has_field_adduct(v) for v in values)>=0.5*len(values):
            columns.append(j)
    out.update(eligible=True,named_pairs=len(feats),adduct_columns=[header[j] for j in columns])
    for k,name in feats.items():
        row=ann.get(k,[])
        field=next((j for j in columns if j<len(row) and has_field_adduct(row[j])),None)
        raw=row[field] if field is not None else label_adduct(name)
        if raw:
            source='annotation_field' if field is not None else 'feature_label'
            out['declared'] += 1
            out[source] += 1
            if len(out['examples'])<4:out['examples'].append({'name':name,'source':source,'field':header[field] if field is not None else '', 'reported':raw})
    return out


def reported_adducts(text: str) -> dict:
    """Display declared ions in every mwTab, independently of Text S11 eligibility.

    Uses the manuscript's unchanged notation recognisers and >=50% column rule.
    No platform, numeric-matrix, reported-name or RefMet filter is applied here.
    Counts distinct normalised labels within an analysis, not feature positions.
    """
    lines = text.split('\n')
    annotations, labels = [], {}
    quantitative_starts = {'MS_METABOLITE_DATA_START', 'NMR_METABOLITE_DATA_START', 'NMR_BINNED_DATA_START'}
    i = 0
    while i < len(lines):
        tag = lines[i].strip()
        if tag == 'METABOLITES_START' or tag in quantitative_starts:
            end = tag.removesuffix('_START') + '_END'
            header = lines[i+1].split('\t') if i+1 < len(lines) else []
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip() != end:
                if lines[i].strip():
                    row = lines[i].split('\t')
                    rows.append(row)
                    name = row[0].strip()
                    if name and (tag == 'METABOLITES_START' or name.lower() != 'factors'):
                        labels.setdefault(key(name), (name, tag))
                i += 1
            if tag == 'METABOLITES_START':
                annotations.append((header, rows))
        i += 1
    declared, columns = {}, []
    for header, rows in annotations:
        # First annotation row per normalised name, as in the manuscript rule.
        first_rows = {}
        for row in rows:
            if row:
                first_rows.setdefault(key(row[0]), [v.strip() for v in row])
        selected = []
        for j in range(1, len(header)):
            values = [r[j] for r in first_rows.values()
                      if j < len(r) and r[j] and r[j] not in ('-', 'NA', 'n/a', 'N/A')]
            hits = sum(has_field_adduct(v) for v in values)
            if values and hits >= 0.5 * len(values):
                selected.append(j)
                columns.append({'field': header[j], 'nonempty_values': len(values), 'recognised_values': hits})
        for name_key, row in first_rows.items():
            if not name_key:
                continue
            j = next((j for j in selected if j < len(row) and has_field_adduct(row[j])), None)
            if j is not None:
                declared.setdefault(name_key, {'name': row[0], 'source': 'annotation_field',
                                               'field': header[j], 'reported': row[j]})
    for name_key, (name, block) in labels.items():
        if name_key not in declared:
            raw = label_adduct(name)
            if raw:
                declared[name_key] = {'name': name, 'source': 'feature_label', 'field': '', 'reported': raw}
    values = Counter(row['reported'] for row in declared.values())
    examples, seen_values = [], set()
    for row in declared.values():
        if row['reported'] not in seen_values:
            examples.append(row)
            seen_values.add(row['reported'])
        if len(examples) == 4:
            break
    return {'mwtab_available': bool(text.strip()), 'declared': len(declared),
            'annotation_field': sum(r['source'] == 'annotation_field' for r in declared.values()),
            'feature_label': sum(r['source'] == 'feature_label' for r in declared.values()),
            'adduct_columns': sorted({c['field'] for c in columns}), 'column_evidence': columns,
            'reported_values': [{'reported': value, 'features': count} for value, count in sorted(values.items())],
            'examples': examples}


@lru_cache(maxsize=128)
def study_evidence(source_root: str, study_id: str) -> dict:
    root=Path(source_root)/study_id
    refs=defaultdict(dict)
    for row in load_records(root/'metabolites.json'):
        if row.get('analysis_id'):
            refs[row['analysis_id']][key(str(row.get('metabolite_name','')))]=str(row.get('refmet_name') or '')
    analyses=[]
    for an in sorted(root.glob('AN*')):
        if not an.is_dir():continue
        txt=an/'mwtab'/f'{an.name}_mwtab.txt'
        text=txt.read_text(encoding='utf-8',errors='replace') if txt.exists() else ''
        parsed=parse_text(text)
        coverage,units=named_coverage(parsed)
        ctype=parsed['ch'].get('CHROMATOGRAPHY_TYPE','')
        # is_lc: explicit LC chromatography type (the manuscript's Figure S6C type rule), with or without RT/RI metadata.
        # eligible: the Figure S6C cohort itself = LC and RT/RI-like metadata for at least one named metabolite.
        is_lc=bool(LC.search(ctype) and not NOT_LC.search(ctype))
        lc_eligible=bool(coverage['cov_rt'] and is_lc)
        components=lc_components(parsed['ch'])
        analyses.append({'analysis_id':an.name,'presence':json_presence(an/'mwtab'/f'{an.name}_mwtab.json'),
                         'coverage':coverage,'rt_units':units,
                         'lc':{'eligible':lc_eligible,'is_lc':is_lc,'chromatography_type':ctype,**components,
                               'raw_fields':{k:parsed['ch'].get(k,'') for k in ('COLUMN_NAME','SOLVENT_A','SOLVENT_B','FLOW_GRADIENT','FLOW_RATE','COLUMN_TEMPERATURE')}},
                         'adduct':declared_adducts(parsed,refs[an.name]),'reported_adducts':reported_adducts(text),
                         'platform_lc':platform_lc(parsed),
                         'ion_mode':parsed['scalar'].get('MS:ION_MODE','')})
    fields={k:v for r in analyses for k,v in r['presence']['fields'].items()}
    classes=set(fields.values())
    mass,rt=bool(classes & MASS),'rt-like' in classes
    return {'study_id':study_id,'present':bool(fields),'category':'Both' if mass and rt else 'Mass/mz only' if mass else 'RT/RI only' if rt else 'None',
            'fields':sorted(fields),'field_classes':fields,
            'files_scanned':sum(r['presence']['json_available'] for r in analyses),
            'blocks_scanned':sum(r['presence']['blocks_scanned'] for r in analyses),
            'source':'mwTab Metabolites annotation records', 'analyses':analyses,
            'coverage_totals':dict(sum((Counter(r['coverage']) for r in analyses),Counter()))}
