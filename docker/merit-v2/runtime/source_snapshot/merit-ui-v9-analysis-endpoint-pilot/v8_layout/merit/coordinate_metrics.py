"""FAIR coordinate metrics backed by the manuscript extraction rules."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from merit.models import MetricResult
from merit.manuscript_coordinate_context import study_evidence, COMPONENTS


def evidence_of(study):
    source=(study.provenance.source or study.study.repository or '').lower().strip()
    if source != 'workbench' or not study.provenance.source_root:
        return None
    return deepcopy(study_evidence(str(study.provenance.source_root),study.study.study_id.upper()))


def coordinate_metric(study):
    evidence=evidence_of(study)
    if evidence is None:
        return MetricResult(family='Metadata / FAIR',name='mass_rt_like_metadata_presence',score=0,
                            status='not_assessable',summary='Not assessable: mwTab annotation records are unavailable.',
                            details={'not_assessable':True})
    presence=bool(evidence['present'])
    evidence['not_assessable']=False
    evidence['score_definition']='1 if any analysis in this study has a populated mass/mz-like or RT/RI-like annotation field; otherwise 0.'
    evidence['coverage_definition']='Named MS features deduplicated within analysis; coverage requires a populated digit-containing value. Both means both in the same annotation record.'
    evidence['rt_units_definition']='Named MS analyses with a finite nonnegative numeric RT; units read from MS_RESULTS_FILE and RT headers/values. RI-only and diagnostic-only records excluded.'
    evidence['adduct_definition']='Declared annotation-field or feature-label adducts only; the Text S11 LC-MS cohort excludes Standard/Unknown and deduplicates names within analysis. No adduct inference.'
    evidence['reported_adducts_definition']='Display-only declarations in every mwTab analysis: unchanged manuscript adduct-notation and at-least-half-nonempty-values column rules; no platform, named-feature, RefMet or valid-matrix eligibility filter. Counts distinct normalised labels within analysis. The separate Text S11 LC-MS diagnostic is retained.'
    return MetricResult(family='Metadata / FAIR',name='mass_rt_like_metadata_presence',score=float(presence),
                        status='pass' if presence else 'warn',summary=f"Mass/RT-like annotation metadata: {evidence['category']}.",
                        details=evidence,thresholds={'presence_required_for_full_credit':True},
                        recommendations=[] if presence else ['Report analytical-coordinate metadata in the mwTab metabolite annotation records when available.'])


SCORED_LC_ITEMS=('column','mobile_phase','gradient')

LC_CONTEXT_INTEROPERABILITY_RECOMMENDATION = (
    'Mass/RT metadata without adequate LC method context has limited interoperability for cross-study '
    'annotation transfer. Interpret the FAIR reusability score with this limitation in mind.'
)


def lc_context_recommendations(analyses):
    """Keep existing advice; add the RT/RI-cohort limitation for incomplete basic context."""
    incomplete = [row for row in analyses if not all(row.get(key) for key in SCORED_LC_ITEMS)]
    recommendations = []
    if incomplete:
        recommendations.append(
            'Not every LC analysis reports the column, both mobile phases and the gradient/composition in the dedicated '
            'mwTab chromatography fields. They may be described in free text or in a method file, which are not read.'
        )
    # This is Figure S6C's 2,244-analysis cohort, not every analysis with study-level mass/RT presence.
    if any(row.get('rt_ri_reported_for_named_metabolites', row.get('eligible', False)) for row in incomplete):
        recommendations.append(LC_CONTEXT_INTEROPERABILITY_RECOMMENDATION)
    return recommendations


def lc_metric(study):
    """Basic LC-method context.

        analysis score = (column reported + both mobile phases reported + gradient/composition reported) / 3
                         each item by the manuscript's rules (Figure S6C); all three together give 1
        study score    = mean of the analysis scores over the LC analyses of the study

    Assessed: every analysis with an explicit LC chromatography type (the manuscript's type rule), with or without
    RT/RI-like metadata. No LC analysis: not assessable, left out of the FAIR mean. Only the dedicated mwTab CH fields
    are read; a method described in free text or in a method file is not counted. Flow rate, column temperature,
    dimensions and particle size are reported for information.
    History: until 2026-10-08 15:00 an analysis scored 1 only when all three items were reported (all or nothing);
    Shayantan asked for credit per item because all-or-nothing was too harsh (a column-only analysis scored 0).
    """
    evidence=evidence_of(study)
    keys=(*COMPONENTS,'basic','extended','full','any_component')
    assessed=[{'analysis_id':r['analysis_id'],'rt_ri_reported_for_named_metabolites':bool(r['lc']['eligible']),
               'context_score':sum(r['lc'][k] for k in SCORED_LC_ITEMS)/len(SCORED_LC_ITEMS),**r['lc']}
              for r in evidence['analyses'] if r['lc'].get('is_lc')] if evidence else []
    with_rt=[r for r in assessed if r['rt_ri_reported_for_named_metabolites']]
    without_rt=[r for r in assessed if not r['rt_ri_reported_for_named_metabolites']]
    n=len(assessed)
    score=sum(r['context_score'] for r in assessed)/n if n else 0
    count=sum(r['basic'] for r in assessed)
    totals=lambda rows:{k:sum(r[k] for r in rows) for k in keys}
    return MetricResult(family="Metadata / FAIR",name="lc_method_context_presence",score=score,
                        status='not_assessable' if not n else 'pass' if score>=0.5 else 'warn',
                        summary=(f'{n} LC analys{"is" if n==1 else "es"}: column reported in {sum(r["column"] for r in assessed)}, both mobile phases in '
                                 f'{sum(r["mobile_phase"] for r in assessed)}, gradient in {sum(r["gradient"] for r in assessed)}; all three in {count}.'
                                 if n else 'Not assessable: no analysis with an explicit LC chromatography type.'),
                        details={'not_assessable':not bool(n),'n_eligible_analyses':n,'n_with_basic_context':count,
                                 'n_with_any_component':sum(r['any_component'] for r in assessed),
                                 'n_lc_analyses_with_rt_ri':len(with_rt),'n_lc_analyses_without_rt_ri':len(without_rt),
                                 'per_analysis':assessed,'component_counts':totals(assessed),
                                 'component_counts_with_rt_ri':totals(with_rt),'component_counts_without_rt_ri':totals(without_rt),
                                 'scored_items':list(SCORED_LC_ITEMS),
                                 'score_definition':'Per LC analysis: one third for each of column, both mobile phases and gradient/composition reported in the dedicated chromatography fields (all three = 1); study score is the mean over the LC analyses of the study.',
                                 'cohort_definition':'Every analysis with an explicit LC chromatography type.',
                                 'combinations_definition':'All three: column + both mobile phases + gradient/composition. Extended: all three + flow rate + column temperature. Full: extended + dimensions + particle size.',
                                 'source':'Dedicated mwTab CH fields; free-text method summaries and method files are not read.'},
                        thresholds={'pass_from':0.5},
                        recommendations=lc_context_recommendations(assessed))
