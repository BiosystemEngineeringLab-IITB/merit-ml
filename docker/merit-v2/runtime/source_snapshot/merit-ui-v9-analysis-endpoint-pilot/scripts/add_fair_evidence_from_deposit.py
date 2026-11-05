"""Deposited values behind three Metadata & FAIR scores, for display only: External IDs, Mass/RT metadata, LC-method context.

Nothing in the v9 study files is changed: the values go to <v9 root>/fair_evidence/<st>.json (one file per study, keyed by analysis).
Every value is extracted with the functions that produced the stored scores, and the extraction must reproduce every stored score:
- External IDs (merit/manuscript_refmet.py): the metabolite records of the analysis (metabolites.json of the frozen dump) with a valid
  RefMet name; per database the records whose RefMet entry has that identifier, with up to two examples (RefMet name and identifier)
  read from the RefMet identifier table of 2026-09-29 (outputs/refmet_mapping_20260929/refmet_all_ids_rest_20260929.json), the table
  the scoring flags were built from (all 37,557 flags rebuilt from it are identical). Checked: n_valid_records, n_with_any,
  n_with_all_six = stored.
- Mass/RT metadata (merit/manuscript_coordinate_context.json_presence and its field classifier): the annotation columns of the mwTab
  JSON Metabolites blocks that are mass/m/z-like or RT/RI-like and hold a value, with their class and the first value; the other
  columns read. Checked: field set and presence = stored.
- LC-method context (merit/manuscript_coordinate_context.parse_text and lc_components): the deposited CH:COLUMN_NAME, CH:SOLVENT_A,
  CH:SOLVENT_B and CH:FLOW_GRADIENT text and the chromatography type; per item whether it counts. Checked: column, mobile phases,
  gradient and LC/not LC = stored.
usage: add_fair_evidence_from_deposit.py [v9 root] [workers]
"""
import collections, datetime, json, os, sys
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
B = Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot')
V9 = Path(sys.argv[1]) if len(sys.argv) > 1 else B / 'merit-cache-workbench-full-v9'
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 40
DUMP = Path(os.environ.get('MERIT_MW_DUMP_ROOT', str(B / 'mw-dump-latest-18thSep-2026-version')))
ID_TABLE = B / 'outputs/refmet_mapping_20260929/refmet_all_ids_rest_20260929.json'
sys.path.insert(0, str(HERE.parent / 'v8_layout'))
from merit import manuscript_refmet as mr
from merit import manuscript_coordinate_context as cc
from merit.manuscript_coordinate_fields import _classify_mass_rt_like_field as classify, _mass_rt_value_is_present as present
OUTDIR = V9 / 'fair_evidence'
EXAMPLES = 2
_IDS = None


def ids():
    global _IDS
    if _IDS is None:
        table = {x['name'].strip(): x for x in json.load(ID_TABLE.open()).values()}
        _IDS = (table, {k.lower(): v for k, v in table.items()})
    return _IDS


def external_ids(records):
    """Same record rule and lookup as manuscript_refmet.external_id_summary, with the identifiers themselves."""
    table, table_ci = ids()
    _, _, full, full_ci = mr._reference()
    per = {f: {'n': 0, 'examples': []} for f in mr.FIELDS}
    without, not_in = [], []
    n = 0
    for r in records:
        if mr.category(r.get('refmet_name')) != 'valid':
            continue
        rn = str(r.get('refmet_name')).strip(); n += 1
        e = table.get(rn) or table_ci.get(rn.lower())
        if e is None:
            (without if (rn in full or rn.lower() in full_ci) else not_in).append(rn)
            continue
        for f in mr.FIELDS:
            v = str(e.get(f) or '').strip()
            if v:
                per[f]['n'] += 1
                if len(per[f]['examples']) < EXAMPLES and rn not in [x[0] for x in per[f]['examples']]:
                    per[f]['examples'].append([rn, v])
    uniq = lambda xs: list(dict.fromkeys(xs))
    return {'n_valid_records': n,
            'per_database': [{'field': f, 'label': mr.FIELD_LABELS[f], **per[f]} for f in mr.FIELDS],
            'refmet_entry_without_external_id': {'n': len(without), 'examples': uniq(without)[:3]},
            'name_not_in_current_refmet': {'n': len(not_in), 'examples': uniq(not_in)[:3]}}


def mass_rt(path):
    """The loop of manuscript_coordinate_context.json_presence, keeping the first value of every qualifying column and the column names read."""
    try:
        payload = json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        return None
    fields, columns, blocks = {}, [], 0
    for _, block in (payload.items() if isinstance(payload, dict) else []):
        if not isinstance(block, dict) or not isinstance(block.get('Metabolites'), list):
            continue
        blocks += 1
        for row in block['Metabolites']:
            if not isinstance(row, dict):
                continue
            for name, value in row.items():
                if name not in columns:
                    columns.append(name)
                c = classify(name)
                if c and present(value) and name not in fields:
                    fields[name] = {'name': name, 'class': c, 'example': str(value)[:80]}
    return {'fields': list(fields.values()), 'columns_read': columns, 'blocks': blocks,
            'named_like_but_empty': [c for c in columns if classify(c) and c not in fields]}


def gradient_why(text):
    if not cc.reported(text):
        return 'not reported'
    missing = []
    if not cc.PCT.search(text):
        missing.append('no percentage or ratio')
    if not (cc.TIME.search(text) or 'isocratic' in text.lower()):
        missing.append('no time (and not isocratic)')
    return '; '.join(missing)


def lc(ch):
    comp = cc.lc_components(ch)
    ctype = ch.get('CHROMATOGRAPHY_TYPE', '')
    g = ch.get('FLOW_GRADIENT', '')
    return {'chromatography_type': ctype, 'is_lc': bool(cc.LC.search(ctype) and not cc.NOT_LC.search(ctype)),
            'items': [{'key': 'column', 'counts': comp['column'], 'fields': {'COLUMN_NAME': ch.get('COLUMN_NAME', '')}},
                      {'key': 'mobile_phase', 'counts': comp['mobile_phase'], 'fields': {'SOLVENT_A': ch.get('SOLVENT_A', ''), 'SOLVENT_B': ch.get('SOLVENT_B', '')}},
                      {'key': 'gradient', 'counts': comp['gradient'], 'fields': {'FLOW_GRADIENT': g}, 'why_not': '' if comp['gradient'] else gradient_why(g)}]}


def one(path):
    v9 = json.loads(Path(path).read_text()); sid = v9['study_id']
    recs = mr.records_by_analysis(DUMP / sid)
    out, problems, c = {}, [], collections.Counter()
    for a in v9['analyses']:
        aid = a['analysis_id']; metrics = ((a.get('sections') or {}).get('metadata') or {}).get('metrics') or {}
        if not metrics:
            continue
        an_dir = DUMP / sid / aid / 'mwtab'
        txt = an_dir / f'{aid}_mwtab.txt'
        ch = cc.parse_text(txt.read_text(encoding='utf-8', errors='replace') if txt.exists() else '')['ch']
        ev = {'external_ids': external_ids(recs.get(aid, [])), 'mass_rt': mass_rt(an_dir / f'{aid}_mwtab.json'), 'lc': lc(ch)}
        # the extraction must reproduce every stored score
        x = metrics.get('external_cross_references_via_refmet') or {}
        s = mr.external_id_summary(recs.get(aid, []))
        if 'n_valid_records' not in x:
            # not assessed by the scoring rule ("this analysis has no valid matrix, so its metabolite records were not assessed"): no values shown
            ev['external_ids'] = None; c['external IDs not assessed (no valid matrix)'] += 1
        elif (s['n_valid_records'], s['n_with_any'], s['n_with_all_six']) != (x.get('n_valid_records'), x.get('n_with_any'), x.get('n_with_all_six')):
            problems.append((sid, aid, 'external IDs', (s['n_valid_records'], s['n_with_any'], s['n_with_all_six']), (x.get('n_valid_records'), x.get('n_with_any'), x.get('n_with_all_six'))))
        if ev['external_ids'] is not None and ev['external_ids']['n_valid_records'] != s['n_valid_records'] or ev['external_ids'] is not None and any(d['n'] != s['per_identifier'][d['field']] for d in ev['external_ids']['per_database']):
            problems.append((sid, aid, 'external IDs per database differ from manuscript_refmet'))
        m = metrics.get('mass_rt_like_metadata_presence') or {}
        if m.get('assessable'):
            got = sorted(f['name'] for f in (ev['mass_rt'] or {}).get('fields', []))
            # the v9 copy of the metadata keeps the first 8 field names (scripts/add_metadata_section_from_v8.py); all are shown here
            if ev['mass_rt'] is None or got[:8] != sorted(m.get('fields') or []) or bool(got) != bool(m.get('present')):
                problems.append((sid, aid, 'mass/RT fields', got, m.get('fields')))
        elif ev['mass_rt'] is not None and m:
            ev['mass_rt'] = None
            c['mass/RT not assessable but JSON readable'] += 1
        l = metrics.get('lc_method_context_presence') or {}
        c['mass/RT: more than 8 fields (stored list truncated, all shown)'] += len((ev['mass_rt'] or {}).get('fields', [])) > 8
        if bool(l.get('assessable')) != ev['lc']['is_lc']:
            problems.append((sid, aid, 'LC or not', l.get('assessable'), ev['lc']['is_lc']))
        elif l.get('assessable'):
            got = tuple(i['counts'] for i in ev['lc']['items'])
            if got != (l.get('column'), l.get('mobile_phase'), l.get('gradient')) or ev['lc']['chromatography_type'] != l.get('chromatography_type'):
                problems.append((sid, aid, 'LC items', got, (l.get('column'), l.get('mobile_phase'), l.get('gradient'))))
        out[aid] = ev; c['analyses'] += 1
    return sid, out, problems, c


if __name__ == '__main__':
    started = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
    files = sorted((V9 / 'json').glob('*_v9.json'))
    results, problems, total = [], [], collections.Counter()
    with Pool(WORKERS) as pool:
        for sid, out, p, c in pool.imap_unordered(one, files, chunksize=4):
            results.append((sid, out)); problems += p; total.update(c)
    if problems:
        for p in problems[:30]:
            print('PROBLEM', p)
        sys.exit(f'{len(problems)} analyses where the extraction does not reproduce the stored score; nothing written')
    OUTDIR.mkdir(exist_ok=True)
    for sid, out in results:
        (OUTDIR / f'{sid.lower()}.json').write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')))
    manifest = json.loads((V9 / 'manifest.json').read_text())
    manifest.setdefault('sections', {}).setdefault('metadata', {})['fair_evidence'] = {
        'status': 'filled', 'scored': False, 'display_only': True, 'folder': 'fair_evidence',
        'rule': 'deposited values behind External IDs, Mass/RT metadata and LC-method context, extracted with the scoring functions; '
                'every stored score reproduced', 'computed_at': started, 'script': str(Path(__file__).resolve()),
        'id_table': str(ID_TABLE), 'counts': dict(total)}
    (V9 / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(f'{len(results):,} studies written; {dict(total)}')
