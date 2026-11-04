"""Compact manuscript-aligned FAIR-coordinate explanations."""
from __future__ import annotations
from collections import Counter
from html import escape


def e(value):return escape(str(value))
def pct(n,d):return f'{100*n/d:.1f}%' if d else 'not assessable'
def line(text):return f"<div style='padding:3px 0;line-height:1.45'>{text}</div>"
def heading(text):return f"<strong style='display:block;padding:8px 0 4px'>{e(text)}</strong>"


def coordinate_popup(metric):
    d=metric.details or {}
    analyses=d.get('analyses',[])
    html=heading('Mass/RT-like metabolite metadata')
    html+=line(f"Study-level presence: <strong>{e(d.get('category','not assessable'))}</strong>. "
               'Score: 100 when any study analysis has a populated mass/mz-like or RT/RI-like annotation field; otherwise 0. '
               'Reads mwTab JSON Metabolites records. Empty and null entries do not count. The same study evidence is shown in every source view.')
    coverage=d.get('coverage_totals',{})
    n=coverage.get('named_features',0)
    html+=heading('Coverage of named MS metabolites · information only')
    if n:
        html+=line(f'{n:,} named metabolite–analysis pairs, deduplicated within each analysis for this manuscript diagnostic.')
        for label,k in [('Mass/mz-like','cov_mass'),('Strict m/z','cov_mz_strict'),('RT/RI-like','cov_rt'),('Either','cov_either'),('Both in the same annotation record','cov_both')]:
            count=coverage.get(k,0)
            html+=line(f'{e(label)}: {count:,} / {n:,} = <strong>{pct(count,n)}</strong>')
    else:html+=line('Not assessable: no eligible named MS metabolites.')
    html+=line('Coverage requires a populated value containing a digit in a matching annotation record. '
               'Coverage counts each normalised name once within an analysis; the Annotation tab counts feature positions. '
               'Mass-like and ambiguous fields are retained separately from strict m/z. Reporting a name or coordinate does not establish identification accuracy.')
    html+=heading('RT units · information only')
    rt=[r for r in analyses if r['rt_units']['RT_eligible']]
    counts=Counter(r['rt_units']['unit_category'] for r in rt)
    if rt:
        html+=line(f'Denominator: {len(rt)} MS analyses with numeric RT for at least one named metabolite. '
                   'RI-only analyses and RT diagnostic fields are excluded. Reads MS_RESULTS_FILE RT-unit declarations and RT annotation headers and values.')
        for label,count in sorted(counts.items()):html+=line(f'{e(label)}: {count} / {len(rt)} = {pct(count,len(rt))}')
        for r in rt:
            u=r['rt_units']
            html+=line(f"<strong>{e(r['analysis_id'])}</strong>: {e(u['unit_category'])}; results-file units: {e(u['MS_RESULTS_FILE_units'] or 'not declared')}; annotation units: {e(u['annotation_units'] or 'not declared')}.")
        html+=line('Mixed means more than one time unit was detected within the analysis. Units are reported as stated; values are not converted.')
    else:html+=line('Not assessable: no named MS metabolite with numeric RT. RI does not require a time unit.')
    html+=heading('Declared adducts · information only')
    ad=[r for r in analyses if r.get('reported_adducts',{}).get('declared',0)]
    count=sum(r['reported_adducts']['declared'] for r in ad)
    if ad:
        html+=line(f'<strong>{count:,} feature labels with declared adducts</strong> across {len(ad)} '
                   f"analys{'is' if len(ad)==1 else 'es'}; "
                   f"annotation fields: {sum(r['reported_adducts']['annotation_field'] for r in ad):,}; "
                   f"feature labels: {sum(r['reported_adducts']['feature_label'] for r in ad):,}.")
        for r in ad:
            a=r['reported_adducts']
            html+=line(f"<strong>{e(r['analysis_id'])}</strong>: {a['declared']:,} feature labels with declared adducts; "
                       f"columns: {e(', '.join(a['adduct_columns']) or 'feature labels only')}; "
                       f"ion mode as deposited: {e(r['ion_mode'] or 'not declared')}.")
            html+=line('Reported notation: '+ '; '.join(f"{e(v['reported'])} ({v['features']:,} labels)" for v in a['reported_values']))
            for x in a['examples'][:2]:html+=line(f"{e(x['name'])}: {e(x['reported'])} ({e(x['field'] or 'feature label')}).")
    else:html+=line('No declared adduct notation detected in the mwTab annotation columns or feature labels.')
    html+=line('Reads every available mwTab analysis, including direct infusion and other non-LC methods. '
               'A column is adduct-bearing when at least half of its nonempty values contain recognised adduct notation, using the manuscript rule. '
               'Annotation fields take precedence over feature labels; each normalised label is counted once within an analysis. '
               'Multiple or nonstandard declarations recognised by that rule are retained as reported; no adduct is inferred from mass, m/z or polarity. '
               'These declarations are shown for information and do not change any score. '
               'This does not certify that m/z, RT, polarity and adduct occur together for every metabolite.')
    return html


def lc_popup(metric):
    d=metric.details or {};n=d.get('n_eligible_analyses',0);c=d.get('component_counts',{})
    html=heading('Basic LC-method context')
    if not n:return html+line('Not assessable: this study has no LC analysis. Left out of the section mean.')
    html+=line(f"<strong>Score: {100*float(metric.score or 0):.1f}</strong> — the mean over {n} LC analys{'is' if n==1 else 'es'}.")
    html+=line('Each LC analysis earns one third for each item it reports: the column, both mobile phases, and the gradient. '
               '<strong>All three together give 100.</strong>')
    html+=line('Only the dedicated chromatography fields are read. A method described in free text or in a method file is not counted, '
               'so a low score does not mean the method is undocumented.')
    scored=[('column','Column'),('mobile_phase','Both mobile phases'),('gradient','Gradient')]
    extra=[('flow_rate','Flow rate'),('temperature','Column temperature'),('dimensions','Column dimensions'),('particle_size','Particle size')]
    html+=heading('Scored items')
    for key,label in scored:html+=line(f'{label}: {c.get(key,0)} / {n}')
    html+=line(f"All three: {c.get('basic',0)} / {n}")
    html+=heading('Also reported · not scored')
    html+=line(' · '.join(f'{label}: {c.get(key,0)} / {n}' for key,label in extra))
    html+=line(f"All three plus flow rate and column temperature: {c.get('extended',0)} / {n}")
    html+=heading('Per analysis')
    for row in d.get('per_analysis',[]):
        found=[label.lower() for key,label in scored if row.get(key)];missing=[label.lower() for key,label in scored if not row.get(key)]
        value=row.get('context_score',sum(bool(row.get(key)) for key,_ in scored)/3)
        text=f"<strong>{e(row['analysis_id'])}</strong>: {100*float(value):.1f} — "
        text+=('reported: '+', '.join(found) if found else 'none of the three reported')
        if missing and found:text+='; not detected: '+', '.join(missing)
        html+=line(text)
        raw=[(field,value) for field,value in row.get('raw_fields',{}).items() if value]
        if raw:
            html+=("<details style='padding:0 0 4px'><summary style='cursor:pointer;color:#51656a;font-size:.78rem'>reported values</summary>"
                   +''.join(line(f"<span style='color:#51656a;font-size:.78rem'><strong>{e(field)}</strong>: {e(value)}</span>") for field,value in raw)+'</details>')
    html+=line("<span style='color:#51656a;font-size:.78rem'>Labelled pass from 50, warn below; never fail. "
               'Reported conditions help to interpret retention times; they do not make them comparable across studies.</span>')
    return html
