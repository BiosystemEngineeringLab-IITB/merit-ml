"""Run existing MERIT calculations on freshly fetched inputs, isolated from caches.

The file-access audit hook rejects reads of all historical deposits, v8/v9 study
results and precomputed evidence tables. Existing Python source and the curated
control rule configuration are allowed; chemical reference records are fetched.
The baseline comparator is a separate program, run only after this process ends.
"""
from __future__ import annotations
import argparse
import collections
import datetime
import hashlib
import gzip
import importlib.util
import json
import os
import re
import shutil
import sys
import traceback
from pathlib import Path

BUNDLE_HOME = Path(__file__).resolve().parent
HOME = Path(os.environ.get('MERIT_ONLINE_EXECUTION_ROOT',str(BUNDLE_HOME))).resolve()
WORKSPACE = HOME.parent
PROJECT_GUARD = Path(os.environ.get('MERIT_PROJECT_GUARD_ROOT','/home/shayantan/metabolomics'))
BASE = Path(os.environ.get('MERIT_EXISTING_PROJECT_ROOT', str(BUNDLE_HOME / 'source_snapshot' if (BUNDLE_HOME / 'source_snapshot').exists() else Path('/home/shayantan/metabolomics/ML-ready'))))
PILOT = BASE / 'merit-ui-v9-analysis-endpoint-pilot'
CODE = PILOT / 'v8_layout'
SCRIPTS = PILOT / 'scripts'
STRUCT = BASE / 'outputs/structural_items_v9_claude_20261011/scripts'
T4 = BASE / 'outputs/table4_descriptors_20260927'
SOURCES = ('datatable', 'mwtab', 'untarg_data')
POST_BUILD_FROM = '2026-10-21'   # curated rows from this day reached the 8782 records through apply_curated_change.py
READS = set()
DENIED = []
MODULES = {}
_GUARD_INSTALLED = False


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=True))


def install_guard():
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:return
    def audit(event, args):
        if event != 'open' or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        p = Path(os.fsdecode(args[0])).resolve()
        mode = args[1]
        flags = args[2] if len(args) > 2 else 0
        writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY | os.O_RDWR))
        if not writing and any(x.startswith(('mw-dump-', 'merit-cache-workbench-')) for x in p.parts):
            DENIED.append({'event': 'historical/result read', 'path': str(p)})
            raise PermissionError(f'Online isolation blocked historical/result input: {p}')
        if writing and p.is_relative_to(PROJECT_GUARD) and not p.is_relative_to(HOME):
            DENIED.append({'event': 'write outside online-tool', 'path': str(p)})
            raise PermissionError(f'Online isolation blocked write: {p}')
        if not writing and p.is_relative_to(PROJECT_GUARD) and not p.is_relative_to(HOME):
            source = p.suffix in ('.py', '.pyc', '.so', '.pth')
            environment = '.venv' in p.parts or p.is_relative_to(Path(sys.prefix).resolve())
            rules = p.name in ('curated_control_corrections.tsv',)
            if not (source or environment or rules):
                DENIED.append({'event': 'historical/result read', 'path': str(p)})
                raise PermissionError(f'Online isolation blocked historical/result input: {p}')
        if not writing and p.is_relative_to(PROJECT_GUARD):
            READS.add(str(p))
    sys.addaudithook(audit)
    _GUARD_INSTALLED = True


def load(name, path, output):
    if name in MODULES:
        return MODULES[name]
    old = sys.argv
    sys.argv = [str(path), str(output), '1']
    try:
        spec = importlib.util.spec_from_file_location('online_' + name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.argv = old
    MODULES[name] = module
    return module


def package_module(name, path):
    """Load an existing package implementation with its relative imports intact."""
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    parent, _, attribute = name.rpartition('.')
    if parent in sys.modules:setattr(sys.modules[parent],attribute,module)
    return module


def single_object_body(raw):
    """Text of the single object inside a REST '[ {...} ]' envelope, or None when not exactly one object."""
    body = raw.strip()
    if not (body.startswith(b'[') and body.endswith(b']')):
        return None
    inner = body[1:-1].strip()
    if not (inner.startswith(b'{') and inner.endswith(b'}')):
        return None
    try:
        value = json.loads(raw, strict=False)
        return inner if isinstance(value, list) and len(value) == 1 else None
    except ValueError:
        return inner if inner.count(b'"METABOLOMICS WORKBENCH"') == 1 else None


def materialize(run):
    """Normalize API envelope only; original HTTP responses remain untouched."""
    raw, live = run / 'inputs', run / 'materialized'
    registry = json.loads((run / 'live_registry.json').read_text())
    requests=json.loads((raw/'fetch_manifest.json').read_text())['requests']
    failed_files={x['file'] for x in requests if (x.get('error') or x.get('resource_refusal')) and not x.get('metadata_only_view')}
    changes = []
    for sid, aids in registry.items():
        for source in (raw / sid).rglob('*'):
            if not source.is_file():
                continue
            if str(source.relative_to(raw)) in failed_files:
                changes.append({'input':str(source.relative_to(run)),'operation':'failed input retained as provenance, not used as deposited data'})
                continue
            dest = live / source.relative_to(raw)
            if source.stat().st_size == 0 and len(source.relative_to(raw / sid).parts) > 1:
                # An empty analysis-level response (mwTab or table) is absent, as in the deposit snapshot.
                changes.append({'input': str(source.relative_to(run)), 'operation': 'empty response treated as absent file'})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.name.endswith('_mwtab.txt'):
                # 8782 read the REST mwTab text as stored by its downloader: the response body plus one
                # trailing newline (true for all 6,523 unchanged analyses of the cohort). No record selection.
                data=source.read_bytes();aid=source.name.removesuffix('_mwtab.txt')
                stored=data.decode('utf-8','replace').encode('utf-8')+b'\n' if data else data
                dest.write_bytes(stored)
                changes.append({'input':str(source.relative_to(run)),'output':str(dest.relative_to(run)),
                    'operation':'mwTab text stored as the deposit snapshot stores it (response body + trailing newline); raw HTTP body retained',
                    'study_id':sid,'analysis_id':aid,'raw_sha256':hashlib.sha256(data).hexdigest(),
                    'stored_sha256':hashlib.sha256(stored).hexdigest()})
                continue
            if source.name.endswith('_untarg_data.tsv'):
                # The deposit stored untarg_data as decoded text: invalid UTF-8 bytes replaced by U+FFFD (all 8 cases checked).
                data=source.read_bytes();stored=data.decode('utf-8','replace').encode('utf-8')
                dest.write_bytes(stored)
                if stored!=data:changes.append({'input':str(source.relative_to(run)),'output':str(dest.relative_to(run)),
                                                'operation':'invalid UTF-8 bytes replaced by U+FFFD, as stored in the deposit snapshot'})
                continue
            if source.name.endswith('_mwtab.json') and b'{' not in source.read_bytes():
                # e.g. '[' alone: Workbench has no mwTab JSON for this analysis; the deposit has no file.
                changes.append({'input': str(source.relative_to(run)), 'operation': 'mwTab JSON without any object treated as absent file'})
                continue
            if source.name.endswith('_mwtab.json'):
                inner=single_object_body(source.read_bytes())
                if inner is not None:
                    # The deposit stored the one analysis object, not the list envelope now returned by REST.
                    # Keep its exact text (valid or not, as deposited) so every reader sees what 8782 saw.
                    dest.write_bytes(inner+b'\n')
                    changes.append({'input':str(source.relative_to(run)),'output':str(dest.relative_to(run)),
                                    'operation':'single-object REST list envelope removed; object text unchanged'})
                    continue
                try:
                    value = json.loads(source.read_text())
                    if isinstance(value, (list,dict)):
                        from mwtab_input import object_for
                        aid=source.name.removesuffix('_mwtab.json')
                        try:selected=object_for(value,sid,aid)
                        except ValueError:selected=None
                        if selected is None or not selected:
                            # No unique object for this analysis: keep the body unchanged, as the deposit did.
                            shutil.copyfile(source,dest);continue
                        wb=selected.get('METABOLOMICS WORKBENCH',{})
                        if wb.get('ANALYSIS_ID') and wb['ANALYSIS_ID']!=aid:
                            changes.append({'input':str(source.relative_to(run)),'analysis_id':aid,'declared_analysis_id':wb['ANALYSIS_ID'],
                                            'operation':'preserve differing analysis header as deposited'})
                        if wb.get('STUDY_ID') and wb['STUDY_ID']!=sid:
                            changes.append({'input':str(source.relative_to(run)),'requested_study_id':sid,
                                            'declared_study_id':wb['STUDY_ID'],'analysis_id':aid,
                                            'operation':'preserve differing study header of an analysis returned by the requested live study registry'})
                        dump(dest, selected)
                        changes.append({'input': str(source.relative_to(run)), 'output': str(dest.relative_to(run)),
                                        'operation': 'select the unique mwTab JSON object by exact study and analysis IDs; never select by array position',
                                        'objects_returned':len(value) if isinstance(value,list) else 1})
                        continue
                except ValueError:
                    pass
            shutil.copyfile(source, dest)
        for aid in aids:
            p=live/sid/aid/'mwtab'/f'{aid}_mwtab.txt'
            text=p.read_text(errors='replace') if p.exists() else ''
            header=text.split('\n',1)[0]
            for key,want in (('STUDY_ID',sid),('ANALYSIS_ID',aid)):
                values=re.findall(key+r'\s*[:=]\s*((?:ST|AN)\d+)',header)
                if values and any(x!=want for x in values):
                    changes.append({'input':str(p.relative_to(run)),'requested_study_id':sid,'declared_study_ids':values,
                                    'analysis_id':aid,'operation':'preserve differing header as deposited'})
        dump(live/sid/'manifest.json',{'study_id':sid,'analyses':{a:{} for a in aids},'source':'fresh API analysis registry'})
        page=raw/sid/'repository_page.html'
        if page.exists():
            from live_references import webpage_raw_data
            request=next(x for x in reversed(requests) if x['file']==f'{sid}/repository_page.html')
            record=webpage_raw_data(page.read_text(errors='replace'),request['finished_utc'],request['final_url'])
            dump(live/sid/'repository_webpage_raw_data.json',record)
            changes.append({'input':str(page.relative_to(run)),
                            'output':str((live/sid/'repository_webpage_raw_data.json').relative_to(run)),
                            'operation':'extract declared raw-data page fields; preserve actual live fetch provenance'})
    dump(run / 'materialization.json', changes)
    return live, registry


def configure(live, out, run):
    import numpy as np
    import matrix_paths
    matrix_paths.ROOT = live
    import table4_descriptors as t4
    t4.CACHE = live
    t4.dvm.CACHE = live
    t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_feats': list(feats), '_mat': mat}
    if pinned_references(run):
        configure_pinned(live, run)
    else:
        configure_fresh_reference(live, run)
    # v9 copied FAIR from the current v8 implementation; its own bundled
    # historical metric class predates the accepted raw-data webpage source.
    import merit.metrics
    package_module('merit.deposited_raw_files',BASE/'merit-ui-v8/merit/deposited_raw_files.py')
    package_module('merit.metrics.metadata',BASE/'merit-ui-v8/merit/metrics/metadata.py')
    install_matrix_reuse(t4)
    import fast_correlation                              # bit-identical float32 products where exact (fast_correlation.py)
    from merit import correlation_v9
    fast_correlation.install(correlation_v9)
    return stage_loader(live, out, t4), t4


def pinned_references(run):
    p = Path(run) / 'inputs/reference/reference_policy.json'
    return p.exists() and json.loads(p.read_text()).get('policy') == 'pinned_8782'


def configure_pinned(live, run):
    """8782's own reference code and files, unchanged: names/flags from merit/data, classes from refmet_db.csv."""
    ref = Path(run) / 'inputs/reference/refmet_db.csv'
    dest = live / 'refmet_db.csv'
    if not dest.exists():
        try: os.link(ref, dest)
        except OSError: shutil.copyfile(ref, dest)
    from merit import manuscript_refmet  # noqa: F401  (unmodified module; reads its code-distributed data)
    from merit import annotation_v9 as an
    an.DUMP = live


def configure_fresh_reference(live, run):
    """Legacy runs that downloaded a fresh RefMet release."""
    full = json.loads((run / 'inputs/reference/refmet_all.json').read_text())
    ids = json.loads((run / 'inputs/reference/refmet_all_ids.json').read_text())
    full = list(full.values()) if isinstance(full, dict) else full
    ids = list(ids.values()) if isinstance(ids, dict) else ids
    names = {str(x.get('name') or '').strip() for x in full}
    fields = ('pubchem_cid', 'inchi_key', 'chebi_id', 'kegg_id', 'hmdb_id', 'lm_id')
    flags = {str(x['name']).strip(): ''.join('1' if str(x.get(f) or '').strip() else '0' for f in fields) for x in ids}
    # The original module runs a self-test on import, which opens its chemical
    # lookup assets before callers can substitute the reference function. Build
    # those *generic reference* assets from the freshly fetched chemical records,
    # and adapt only their directory/date constants in an isolated source copy.
    refs = run / 'chemical_reference'; refs.mkdir(exist_ok=True)
    with gzip.open(refs / 'refmet_external_id_flags_online.json.gz', 'wt') as f:
        json.dump({'fields': list(fields), 'flags': flags}, f)
    with gzip.open(refs / 'refmet_names_online.txt.gz', 'wt') as f: f.write('\n'.join(sorted(names)))
    source = CODE / 'merit/manuscript_refmet.py'
    text = source.read_text()
    needle = 'DATA = Path(__file__).resolve().parent / "data"'
    assert text.count(needle) == 1
    text = text.replace(needle, f'DATA = Path({str(refs)!r})')
    fetched=json.loads((run/'inputs/fetch_manifest.json').read_text())['requests']
    day=next(x.get('reference_date') or x['started_utc'].split('T')[0] for x in fetched if x['url'].endswith('/refmet/all_ids'))
    names_day=next(x.get('reference_date') or x['started_utc'].split('T')[0] for x in fetched if x['url'].endswith('/refmet/all/'))
    text = text.replace('ID_TABLE_DATE = "2026-09-29"', f'ID_TABLE_DATE = {day!r}')
    text = text.replace('NAME_LIST_DATE = "2026-09-27"', f'NAME_LIST_DATE = {names_day!r}')
    text = text.replace('refmet_external_id_flags_20260929.json.gz', 'refmet_external_id_flags_online.json.gz').replace('refmet_names_20260927.txt.gz', 'refmet_names_online.txt.gz')
    adapted = run / 'adapted_code/manuscript_refmet.py'; adapted.parent.mkdir(exist_ok=True); adapted.write_text(text)
    spec = importlib.util.spec_from_file_location('merit.manuscript_refmet', adapted)
    mr = importlib.util.module_from_spec(spec); sys.modules['merit.manuscript_refmet'] = mr; spec.loader.exec_module(mr)
    from merit import annotation_v9 as an
    an.DUMP = live
    # Use the same lookup algorithm with the current, independently fetched
    # public reference. Historical lookup outputs are never opened.
    mr._reference = lambda: (flags, {k.lower(): v for k, v in flags.items()}, names, {x.lower() for x in names})
    classes = {}
    csv_file=run/'inputs/reference/refmet_download.csv'
    if csv_file.exists():
        import csv
        class_rows=[{(k or '').strip():(v or '').strip() for k,v in row.items()}
                    for row in csv.DictReader(csv_file.open())]
    else:class_rows=full
    for row in class_rows:
        name = str(row.get('refmet_name') or row.get('name') or '').strip()
        cl = tuple(str(row.get(k) or '').strip() for k in ('super_class', 'main_class', 'sub_class'))
        classes.setdefault(name, cl)
        classes.setdefault('\0' + name.lower(), cl)
    an.refmet_classes = lambda: classes


MATRIX_REUSE_MB = float(os.environ.get('MERIT_MATRIX_REUSE_MB', '1500'))
MATRIX_REUSE = {'hits': 0, 'reads': 0, 'evicted': 0, 'not_kept': 0, 'budget_mb': MATRIX_REUSE_MB}


def install_matrix_reuse(t4):
    """Read each deposited matrix once per run. Every stage calls table4_descriptors.one((study, analysis, endpoint)), which parses
    the whole file; a large study was parsed 12 times (65% of its run time). The first call is kept and every call, the first
    included, gets its own copy, so a stage never sees another stage's changes and every value is the one a fresh read gives.
    Matrices are kept up to MERIT_MATRIX_REUSE_MB (least recently used dropped first); beyond that a matrix is read again."""
    if getattr(t4.one, '_matrix_reuse', False):
        return
    import collections
    original, kept = t4.one, collections.OrderedDict()
    size = lambda r: int(getattr(r.get('_mat'), 'nbytes', 0)) + 64 * (len(r.get('_sids') or ()) + len(r.get('_feats') or ()))
    def fresh(r):
        c = dict(r)
        for k in ('_sids', '_feats'):
            if k in c: c[k] = list(c[k])
        if '_mat' in c: c['_mat'] = c['_mat'].copy()
        return c
    def one(job):
        key = (str(t4.CACHE), tuple(job))
        if key in kept:
            MATRIX_REUSE['hits'] += 1; kept.move_to_end(key)
            return fresh(kept[key])
        MATRIX_REUSE['reads'] += 1
        r = original(job)
        budget = MATRIX_REUSE_MB * 1e6
        if size(r) <= budget:
            kept[key] = fresh(r)
            while sum(size(v) for v in kept.values()) > budget:
                kept.popitem(last=False); MATRIX_REUSE['evicted'] += 1
        else:
            MATRIX_REUSE['not_kept'] += 1
        return r
    one._matrix_reuse = True
    t4.one = one


def stage_loader(live, out, t4):
    # Existing scripts have some hard-coded globals. Redirect their input and
    # output roots explicitly; no live cache script entrypoint is executed.
    def stage(name):
        m = load(name, SCRIPTS / f'{name}.py', out)
        if hasattr(m, 'V9'): m.V9 = out
        if hasattr(m, 'DUMP'): m.DUMP = live
        if hasattr(m, 'RUNNING'):
            m.RUNNING = out / 'running'
            m.RUNNING.mkdir(exist_ok=True)
        if hasattr(m, 'RES'):
            m.RES = out / 'correlation/_results'; m.RES.mkdir(parents=True, exist_ok=True)
            m.TMP = out / 'correlation/_tmp'
        if hasattr(m, 'dvm'): m.dvm.CACHE = live
        t4.CACHE = live
        t4.dvm.CACHE = live
        t4.describe = lambda source, sids, labels, feats, mat: {'_sids': list(sids), '_feats': list(feats), '_mat': mat}
        return m
    return stage


def bootstrap(sid, aids, live, out, stage):
    from live_validity import is_valid
    from merit import structural_v9 as sv
    from merit.manuscript_sample_rules import is_qc_or_blank, tier1_alignment
    from merit.manuscript_matrix_readers import _lines
    from merit.curated_corrections import correction_for, addition_for, describe
    builder = stage('build_v9_cache')
    items = load('structural_items', STRUCT / 'build_structural_items.py', out); items.ROOT = live
    enumeration = load('enumeration', STRUCT / 'enumerate_value_cell_tokens_eligible.py', out); enumeration.ROOT = live
    from value_cell_classes import klass, TEXT_CLASSES
    v = {'schema': 'merit.cache.v9.study', 'study_id': sid, 'roster_fields': list(sv.ROSTER_FIELDS), 'analyses': []}
    for aid in aids:
        mw = live / sid / aid / 'mwtab' / f'{aid}_mwtab.txt'
        ssf = {}
        for line in (_lines(mw) if mw.is_file() else []):
            if line.startswith('SUBJECT_SAMPLE_FACTORS'):
                c = line.split('\t')
                if len(c) > 2 and c[2].strip(): ssf.setdefault(c[2].strip(), (c[1].strip(), c[3].strip() if len(c) > 3 else ''))
        row = {'study_id': sid, 'analysis_id': aid}
        parsed = {}
        for src in SOURCES:
            path, _ = builder.matrix_path(sid, aid, src)
            try:
                pos, nf = builder.positions_of(path, src) if path else ([], 0)
            except MemoryError:
                raise
            except Exception:
                pos, nf = [], 0
            valid = bool(pos and nf and is_valid(path,src))
            row[src] = 'valid' if valid else 'no_file' if path is None or path.stat().st_size == 0 else 'invalid_or_empty'
            if valid: parsed[src] = (path, pos, nf)
        evidence = {x['source']: x for x in items.one(row)}
        for token in enumeration.one(row):
            r = evidence[token['source']]
            by = collections.Counter(); examples = collections.Counter()
            for text, count in token['tokens'].items():
                k = klass(text); by[k] += count
                if k != 'missing entry': examples[text] += count
            text = sum(by[k] for k in TEXT_CLASSES); denom = token['value_cells'] - by['missing entry']
            r.update(value_cells=token['value_cells'], numeric_cells=token['numeric_cells'], missing_entry_cells=by['missing entry'],
                     text_cells=text, text_cells_by_class={k: by[k] for k in TEXT_CLASSES if by[k]},
                     text_examples=[f'{t} ×{n}' for t, n in examples.most_common(6)], numeric_values=1-text/denom if denom else None,
                     malformed_exponent_cells=token.get('malformed_exponent_cells', 0),
                     malformed_exponent_examples=token.get('malformed_exponent_examples', []))
            vals = [r[k] for k in sv.ITEM_KEYS if r.get(k) is not None]
            r['structural'] = sum(vals)/len(vals) if vals else None; r['n_scored_items'] = len(vals)
        analysis = {'analysis_id': aid, 'endpoints': {}}
        for src in SOURCES:
            ep = {'availability': row[src]}
            if src in parsed:
                path, pos, nf = parsed[src]
                alignment = tier1_alignment(path, mw) if src == 'datatable' else None
                removed = set(alignment['all_zero_absent_sample_ids']) if alignment else set()
                roster = [[i, lab, int(is_qc_or_blank(i, lab)), int(i in removed), int(i in ssf),
                           ssf[i][1] if i in ssf else '', ssf[i][0] if i in ssf else '', *counts] for i, lab, counts in pos]
                curated = []
                for r in roster:
                    c = correction_for(sid, r[0], r[1]) if r[2] else addition_for(sid, r[0], r[1], r[5])
                    # Rows decided from 21 Oct were applied to the 8782 records after their build (apply_sample_kind_rule).
                    if c and str(c.get('decided_on', '')) >= POST_BUILD_FROM: c = None
                    if c:
                        r[2] = int(c['decision'] in ('blank', 'standard'))
                        curated.append({'sample_id': r[0], 'label_on_position': r[1], 'matched': describe(c), 'decision': c['decision'],
                                        'reason': c['reason'], 'decided_by': c['decided_by'], 'decided_on': c['decided_on']})
                rec = sv.compute(roster, sample_table_lines=len(ssf))
                ev = {k: val for k, val in evidence[src].items() if k not in ('study_id', 'analysis_id', 'source')}
                if curated:
                    for k in builder.DEPENDS_ON_QC_FLAG: ev.pop(k, None)
                    ev['curated_correction_note'] = (f'{len(curated)} positions changed QC/blank status after a curated correction; evidence that depends on '
                                                     'which positions are QC/blank was recomputed from the roster or left out')
                ep.update(file=str(path.relative_to(live)), file_in_study_folder=sid, n_features=nf, sample_table_lines=len(ssf), roster=roster,
                          sections={'structural': {'score': rec['structural'], 'items': {k: rec[k] for k in sv.ITEM_KEYS},
                                                   'parameters': dict(sv.DEFAULT_PARAMS), 'evidence': ev}})
                if curated: ep['curated_corrections'] = curated
            analysis['endpoints'][src] = ep
        v['analyses'].append(analysis)
    path = out / 'json' / f'{sid.lower()}_v9.json'; dump(path, v)
    apply_sample_kind_rule(path, out)
    return path


def apply_sample_kind_rule(path, out):
    """QC/blank flag = (qc_blank_v9.sample_kind is not 'sample') for every position, as in the 8782 records.

    8782 reached this state in two steps after its build: apply_sample_table_rule.py (21 Oct) changed positions
    decided by the sample-table rule alone, and apply_curated_change.py changed positions decided by a curated row.
    Each step left its own notes. Both steps are reproduced here with their exact code, each on its own positions."""
    from merit import qc_blank_v9 as qb, structural_v9 as sv
    rule = load('apply_sample_table_rule', SCRIPTS / 'apply_sample_table_rule.py', out)
    depends = rule.DEPENDS_ON_QC_FLAG
    v = json.loads(path.read_text()); st = v['study_id']; rebuilt = False
    for a in v['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep:
                continue
            listed = []; curated_changed = []
            for r in ep['roster']:
                kind, row = qb._decision(st, r[0], qb.rule_factors(r))
                new = int(kind != 'sample')
                if new == r[2]:
                    continue
                if row is None:      # apply_sample_table_rule.one
                    if not r[3]:
                        listed.append({'sample_id': r[0], 'label_on_position': r[1], 'sample_table_factors': r[5] if r[4] else None,
                                       'qc_blank_before': r[2], 'qc_blank_now': new, 'kind_now': kind})
                else:                # apply_curated_change.py
                    curated_changed.append(r[0])
                r[2] = new
            cur = rule.curated_list(st, ep['roster'])
            if cur: ep['curated_corrections'] = cur
            else: ep.pop('curated_corrections', None)
            sec = ep['sections']['structural']
            def recompute():
                rec = sv.compute(ep['roster'], sample_table_lines=ep.get('sample_table_lines'))
                sec['score'] = rec['structural']; sec['items'] = {k: rec[k] for k in sv.ITEM_KEYS}
                ev = sec['evidence']
                for k in depends: ev.pop(k, None)
                for k in list(ev):
                    if k in rec and not isinstance(rec[k], (list, dict)): ev[k] = rec[k]
                return ev
            if listed:
                ep['sample_table_rule_changes'] = listed
                recompute()['sample_table_rule_note'] = (f'{len(listed)} positions changed ML eligibility under the sample-table rule (2026-10-21); '
                                                         'counts recomputed from the roster, evidence that depends on the QC/blank flag left out')
            measured = [i for i in curated_changed if not next(r for r in ep['roster'] if r[0] == i)[sv.I_ALIGN]]
            rebuilt = rebuilt or bool(listed) or bool(measured)
            if measured:
                recompute()['curated_correction_note'] = (f'{len(cur)} positions changed QC/blank status after a curated correction; evidence that depends on '
                                                          'which positions are QC/blank was recomputed from the roster or left out')
    dump(path, v)
    # rebuild_sample_metrics_for_studies.py was run for the studies whose measured positions changed after the build
    dump(out / 'post_build' / f"{st.lower()}.json", {'rebuilt_after_build': rebuilt})


def prepare_units_and_zeros(registry, live, out, stage):
    import extract_units as eu
    import extract_untarg_units as ru
    unit_rows, result_rows, zeros, per = {}, {}, {}, {}
    zo = load('zero_origin', T4 / 'zero_origin.py', out); zo.dvm.CACHE = live
    zp = load('per_sample_zero', BASE / 'merit/manuscript/supplementary/sample_missingness_scoring/datatable_zero_provenance_per_sample.py', out)
    zp.dvm.CACHE = live
    for sid, aids in registry.items():
        study = json.loads((out / 'json' / f'{sid.lower()}_v9.json').read_text())
        for a in study['analyses']:
            aid = a['analysis_id']; path = live / sid / aid / 'mwtab' / f'{aid}_mwtab.txt'; key = (sid, aid)
            raw, _ = eu.units_of(path); info = eu.classify(raw)
            unit_rows[key] = {'units_raw': raw, 'units_family': eu.FAMILY.get(info['display_category'], 'Not declared') if raw else 'Not declared'}
            status, fname, raw, prefix = ru.results_units(path); info = eu.classify(raw)
            if raw and not eu.clean(raw):           # extract_untarg_units.main
                status = 'results-file line with a placeholder or a number'
            result_rows[key] = {'units_as_written': raw, 'units_family': eu.FAMILY.get(info['display_category'], 'Not declared') if eu.clean(raw) else 'Not declared',
                                'source_of_declaration': status}
            if all('roster' in a['endpoints'][k] for k in ('datatable', 'mwtab')):
                from merit import cell_counts_v9 as counts_reader
                dt = a['endpoints']['datatable']
                counted, _ = counts_reader.counts_of(live / dt['file'], 'datatable')
                z = zo.one((*key, dt['n_features'], len(dt['roster']), sum(n[-1] for _, n in counted)))
                if z.get('error'): raise RuntimeError(z['error'])
                if isinstance(z, tuple): raise RuntimeError('unexpected zero_origin output')
                zeros[key] = z
                _, counts = zp.per_sample(key)
                mv = stage('add_missingness_from_deposit').mv
                per[key] = {s: [int(c[k]) for k in mv.PROVENANCE] for s, c in counts.items()}
    missing = stage('add_missingness_from_deposit'); missing._zo = zeros; missing._prov = per
    scale = stage('add_scale_from_deposit'); scale._tables = (unit_rows, result_rows, set(zeros))
    def unit_text(sid, aid):
        # add_scale_from_deposit.data_block_units_text, with the job's input folder in place of the deposit
        p = live / sid / aid / 'mwtab' / f'{aid}_mwtab.txt'
        if not p.is_file():
            return None
        texts = [line.split('\t', 1)[1].strip() if '\t' in line else '' for line in p.read_text(encoding='utf-8', errors='replace').split('\n') if scale.UNITS_LINE.match(line)]
        return list(dict.fromkeys(texts)) or None
    scale.data_block_units_text = unit_text
    return missing, scale


def readable_mwtab_matrix(sid, aid, stage):
    """8782 took per-analysis metabolite-record results from its earlier engine, which assessed an analysis when it could
    read a mwTab matrix (positions and features), even one the validity rule rejects (7 of 9 such analyses)."""
    builder = stage('build_v9_cache')
    path, _ = builder.matrix_path(sid, aid, 'mwtab')
    if path is None: return False
    try:
        pos, nf = builder.positions_of(path, 'mwtab')
    except Exception:
        return False
    return bool(pos and nf)


def metadata(sid, live, out, stage):
    """Regenerate FAIR directly with the existing formulas, never from v8."""
    from merit import manuscript_coordinate_context as cc, manuscript_refmet as mr
    from merit.connectors.workbench import MetabolomicsWorkbenchConnector
    from merit.metrics.metadata import FairStudyMetadataComplianceMetric
    conn = MetabolomicsWorkbenchConnector()
    path = out / 'json' / f'{sid.lower()}_v9.json'; v = json.loads(path.read_text())
    # As the connector does for the deposit: only analyses whose mwTab JSON file exists are passed.
    aidpaths = {a['analysis_id']: live / sid / a['analysis_id'] / 'mwtab' / f"{a['analysis_id']}_mwtab.json" for a in v['analyses']}
    aidpaths = {k: p for k, p in aidpaths.items() if p.is_file()}
    factors = json.loads((live / sid / 'factors.json').read_text())
    metabolites = json.loads((live / sid / 'metabolites.json').read_text())
    payload = conn._build_payload_from_latest_dump(sid, aidpaths, {}, analysis_ids=[a['analysis_id'] for a in v['analyses']], factors_payload=factors,
                                                 metabolites_payload=conn._as_rows(metabolites))
    cp = live / sid / '__merit_combined_online.json'; dump(cp, payload)
    canonical = conn.normalize_bundle({'json_path': str(cp), 'file_manifest': [str(cp)], 'source_root': str(live),
                                       'study_id': sid, 'tabular_resolution': [], 'connector': conn.connector_name})
    fair = FairStudyMetadataComplianceMetric().compute(canonical)
    evidence = cc.study_evidence(str(live), sid)
    byid = {a['analysis_id']: a for a in evidence['analyses']}
    records = mr.records_by_analysis(live / sid)
    convert = stage('add_metadata_section_from_v8')
    order = convert.ORDER
    for a in v['analyses']:
        aid = a['analysis_id']; r = mr.mapping_summary(records.get(aid, [])); e = mr.external_id_summary(records.get(aid, [])); item = byid[aid]; lc = item['lc']
        share = r['n_valid'] / r['n_records'] if r['n_records'] else None
        eshare = e['n_with_any'] / e['n_valid_records'] if e['n_valid_records'] else None
        metrics = {
            order[0]: {'scope': 'study', 'score': fair.score, 'assessable': True},
            order[1]: {'scope': 'analysis', 'score': share, 'assessable': share is not None, 'n_records': r['n_records'], 'n_valid': r['n_valid'],
                       **({'reason': 'no metabolite record deposited for this analysis'} if share is None else {})},
            order[2]: {'scope': 'analysis', 'score': eshare, 'assessable': eshare is not None, 'n_valid_records': e['n_valid_records'],
                       'n_with_any': e['n_with_any'], 'n_with_all_six': e['n_with_all_six'],
                       **({'reason': 'no record with a valid RefMet name'} if eshare is None else {})},
            order[3]: convert.mass_metric(item),
            order[4]: ({'scope': 'analysis', 'score': sum(bool(lc[k]) for k in ('column', 'mobile_phase', 'gradient')) / 3,
                        'assessable': True, 'chromatography_type': lc['chromatography_type'],
                        **{k: lc[k] for k in ('column', 'mobile_phase', 'gradient')},
                        'rt_ri_reported_for_named_metabolites': bool(item['coverage']['cov_rt'])}
                       if lc['is_lc'] else {'scope': 'analysis', 'score': None, 'assessable': False, 'reason': 'not an LC analysis'}),
        }
        if not any('roster' in x for x in a['endpoints'].values()) and not readable_mwtab_matrix(sid, aid, stage):
            # add_metadata_section_from_v8.py: the metabolite records of an analysis without a valid matrix are not assessed.
            for k in (order[1], order[2]):
                metrics[k] = {'scope': 'analysis', 'score': None, 'assessable': False,
                              'reason': 'this analysis has no valid matrix, so its metabolite records were not assessed'}
        vals = [metrics[k]['score'] for k in order if metrics[k]['assessable'] and metrics[k]['score'] is not None]
        a.setdefault('sections', {})['metadata'] = {'scope': 'analysis: one record per analysis, not per endpoint',
                                                  'score': sum(vals)/len(vals) if vals else None, 'n_assessable_metrics': len(vals), 'metrics': metrics}
    v.setdefault('study_sections', {})['metadata'] = {'fair_study_metadata_compliance': {'score': fair.score, 'status': fair.status,
                                                                                     'passed': fair.details['passed'], 'total': fair.details['total'],
                                                                                     'checks': fair.details['checks'],
                                                                                     'field_evidence':fair.details['field_evidence']}}
    # Recompute the original study-level documentation rollups from live data.
    # These are legacy export fields; no stored study result is loaded.
    from merit.metrics.metadata import (FairMetaboliteIdentifierResolvabilityMetric,
        ExternalCrossReferencesViaRefmetMetric,MassRtLikeMetadataPresenceMetric,LcMethodContextMetric)
    study_metrics=[fair]+[cls().compute(canonical) for cls in (
        FairMetaboliteIdentifierResolvabilityMetric,ExternalCrossReferencesViaRefmetMetric,
        MassRtLikeMetadataPresenceMetric,LcMethodContextMetric)]
    block=v['study_sections']['metadata'];mass=study_metrics[3]
    block['mass_rt_like_metadata_presence']={'score':mass.score,'status':mass.status,
        'present':mass.details.get('present'),'category':mass.details.get('category'),
        'not_assessable':bool(mass.details.get('not_assessable'))}
    block['v8_study_metric_scores']={m.name:{'score':m.score,'status':m.status} for m in study_metrics}
    from merit.readiness_score import _mean_score
    block['v8_study_reusability_score']=round(_mean_score(study_metrics,5),3)
    dump(path, v)
    dump(out / 'fair_evidence' / f'{sid.lower()}.json', evidence)
    # The standard per-analysis metadata rows are freshly reconstructed from
    # the connector's parsing of the live records, not copied from v8.
    ac = stage('add_analytical_section_from_v8')
    info = {x['analysis_id']: x for x in payload['analyses'].values()}
    stype = payload.get('summary', {}).get('analysis_type', '')
    for a in v['analyses']:
        aid = a['analysis_id']; row = ac.row_from_analysis_metadata(info[aid], stype)
        section = a.setdefault('sections', {}).setdefault('analytical', {})
        section['scope'] = 'analysis: one record per analysis, not per endpoint'
        section['score'] = None
        section['data_summary'] = row
        section['data_summary_source'] = 'per-analysis summary row' if any('roster' in x for x in a['endpoints'].values()) else 'analysis metadata (the analysis has no valid matrix on any endpoint)'
        section['data_summary_provenance'] = ac.provenance(sid, aid, row, info[aid].get('analysis_type', ''),
                                                        {str(x.get('analysis_type') or '').strip() for k, x in info.items() if k != aid} - {''})
    dump(path, v)


def header_identity_status(sid, aid, txt, js, proc):
    """source_header_identity_status of the accepted Reviewer 1 comment 5 audit, for one analysis
    (verify_record_identity.py + annotate_identity_findings.py, restricted to this analysis's two files)."""
    expected = {'STUDY_ID': sid, 'ANALYSIS_ID': aid}
    files = []
    if txt.is_file():
        claims = collections.defaultdict(set)
        for line in txt.read_text(errors='surrogateescape').split('\n'):
            if line.startswith('#METABOLOMICS WORKBENCH'):
                for k, value in re.findall(r'\b(STUDY_ID|ANALYSIS_ID):([^\s]*)', line):
                    if value: claims[k].add(value)
        files.append(claims)
    if js.is_file():
        claims = collections.defaultdict(set)
        try:
            fields = proc.json_metadata(js.read_bytes())[1]
            for f in fields:
                if f['field'] in ('METABOLOMICS WORKBENCH:STUDY_ID', 'METABOLOMICS WORKBENCH:ANALYSIS_ID') and f['value'].strip():
                    claims[f['field'].partition(':')[2]].add(f['value'].strip())
        except Exception:
            text = js.read_text(errors='surrogateescape')
            for m in re.finditer(r'"METABOLOMICS WORKBENCH"\s*:\s*\{([^}]*?)\}', text, re.S):
                for k, value in re.findall(r'"(STUDY_ID|ANALYSIS_ID)"\s*:\s*"([^"\n]*)"', m[1]):
                    if value: claims[k].add(value)
        files.append(claims)
    if any(values != {expected[k]} for c in files for k, values in c.items()):
        return 'conflicting_source_header'
    if files and all(c['STUDY_ID'] and c['ANALYSIS_ID'] for c in files):
        return 'all_available_headers_consistent'
    return 'at_least_one_missing_header_identifier_or_no_file'


def rebuilt_after_rule(v,out=None):
    """True when 8782 rebuilt the study's sample-level records after its build (rebuild_sample_metrics_for_studies.py, which
    also wrote low-information flagged_index): measured positions changed after the build (recorded by apply_sample_kind_rule)."""
    p=Path(out or '.')/'post_build'/f"{v['study_id'].lower()}.json"
    return bool(json.loads(p.read_text()).get('rebuilt_after_build')) if p.exists() else False


def extra_evidence(path, live, out, stage, run, reuse_native=False):
    """Regenerate informational evidence from the live records, not old ledgers."""
    from merit import sample_annotations_v9 as sa, acquisition_context as ax, readiness_v9 as rd
    sa.DUMP = live
    evmod = stage('add_fair_evidence_from_deposit')
    evmod.ID_TABLE = run / ('inputs/reference/refmet_all_ids_rest_20260929.json' if pinned_references(run) else 'inputs/reference/refmet_all_ids.json')
    evmod._IDS = None
    sid, evidence, problems, _ = evmod.one(path)
    if problems: raise RuntimeError(problems)
    dump(out / 'fair_evidence' / f'{sid.lower()}.json', evidence)
    processing_root = BASE / 'merit/manuscript/manuscript-original-reviews/reviewer-1/comment5_preprocessing_parameters_and_software_evidence'
    proc = load('processing_rules', processing_root / 'audit_preprocessing.py', out)
    proc_add = stage('add_processing_evidence_from_audit')
    context_root = BASE / 'merit/manuscript/manuscript-original-reviews/reviewer-1/comment1_analyzer_class_and_acquisition_evidence'
    # openpyxl is used by the existing audit module's export functions; the
    # installed system package suffices and no export/data file is loaded.
    import site
    sys.path.append('/usr/lib/python3/dist-packages')
    sys.path.append(site.getusersitepackages())
    acq = load('acquisition_rules', context_root / 'build_analysis_evidence.py', out)
    native_path=out/'native_json'/path.name
    input_path=native_path if reuse_native and native_path.exists() else path
    v = json.loads(input_path.read_text()); runtime = {}; rawsets = {}
    missing_external = []
    for a in v['analyses']:
        aid = a['analysis_id']; folder = live / sid / aid / 'mwtab'; txt = folder / f'{aid}_mwtab.txt'; js = folder / f'{aid}_mwtab.json'
        text = txt.read_text(errors='replace') if txt.is_file() else ''
        tf = proc.txt_metadata(txt.read_bytes())[1] if txt.is_file() else []
        try:
            jf = proc.json_metadata(js.read_bytes())[1] if js.is_file() else []
        except ValueError:
            jf = []  # not valid JSON: the accepted audit records json_parsed=False and reads the text file only
        fields = [('txt', f) for f in tf] + [('json', f) for f in jf]
        tools, steps, minfrac = set(), set(), []
        declared = collections.defaultdict(lambda: {'txt': [], 'json': []})
        for fmt, f in fields:
            raw = f['value']; search = re.sub(r'(?<=[a-z])-\s+(?=[a-z])', '', raw, flags=re.I)
            if proc.substantive(raw):
                tools.update(proc.software_hits(search, {k: re.compile(p, re.I) for k,p in proc.SOFTWARE.items()}))
                steps.update(k for k,p in proc.STAGES.items() if re.search(p, search, re.I))
            key = proc_add.canon(f['field']); clean = re.sub(r'\s+', ' ', raw).strip()
            if clean.casefold() not in proc_add.PLACEHOLDER and clean not in declared[key][fmt]: declared[key][fmt].append(clean)
        dedicated = []
        for field, label in proc_add.FIELDS:
            vals = declared[field]; use = vals['txt'] or vals['json']
            if use: dedicated.append({'field':field,'label':label,'value':' | '.join(use),'read_from':'mwTab text file' if vals['txt'] else 'mwTab JSON'})
        pe = {'dedicated': dedicated, 'software': sorted(tools), 'steps': sorted(steps), 'minfrac': minfrac,
              'header_status': header_identity_status(sid, aid, txt, js, proc),
              'files': 'none' if not txt.is_file() and not js.is_file() else ('mwTab JSON only' if not txt.is_file() else 'mwTab text and JSON')}
        section=a.setdefault('sections',{}).setdefault('analytical',{});section['processing_evidence']=pe
        parsed=acq.read_mwtab(txt)
        # Initialize only the published schema, never values from old analyses.
        rec={k:(None if 'redu' in k.lower() else '') for group in ax.FIELD_GROUPS.values() for k in group}
        if not parsed['has_mwtab']:
            # build_analysis_evidence.py: an analysis without a mwTab text file keeps only these fields.
            rec.update(study_id=sid,analysis_id=aid,modality='No mwTab file',resolution_class='',resolution_class_basis='no mwTab file')
            for src in SOURCES:rec[src+'_source']=a['endpoints'][src]['availability']
            parsed={'raw_files':set(),'fields':{},'has_mwtab':False}
        else:
            f=parsed['fields'];atype=f.get('AN:ANALYSIS_TYPE','');itype=f.get('MS:INSTRUMENT_TYPE','');iname=f.get('MS:INSTRUMENT_NAME','')
            has_ms=any(k.startswith('MS:') for k in f);has_nmr=any(k.startswith('NM:') for k in f)
            modality='NMR' if ('NMR' in atype.upper() or has_nmr and not has_ms) else 'MS' if has_ms or 'MS' in atype.upper() else 'Not stated'
            rec.update(study_id=sid,analysis_id=aid,modality=modality,analysis_type_field=atype,instrument_type_field=itype,instrument_name_field=iname,
                       ms_type_field=f.get('MS:MS_TYPE',''),ion_mode_field=f.get('MS:ION_MODE',''),chromatography_type_field=f.get('CH:CHROMATOGRAPHY_TYPE',''))
            if modality=='MS':
                cls,basis,family=acq.combine(itype,iname);rec.update(resolution_class=cls,resolution_class_basis=basis,analyser_family=family,
                                                                 class_from_type=acq.type_class(itype)[0],class_from_name=acq.name_class(iname)[0])
            else:rec.update(resolution_class='',resolution_class_basis='not an MS analysis' if modality=='NMR' else 'modality not stated')
            rec.update(acq.text_block(parsed))
            if modality=='MS':rec.update(acq.SM.scan_mode_columns(f,acq.ANALYSIS_SECTIONS))
            for src in SOURCES:
                ep=a['endpoints'][src];valid='roster' in ep;rec[src+'_source']=ep['availability']
                pct=ep.get('sections',{}).get('annotation',{}).get('coverage',{}).get('score') if valid else None
                # The manuscript context table writes these supporting annotation
                # percentages to one decimal; primary annotation scores stay exact.
                rec[src+'_pct_named_labels']=f'{pct:.1f}' if pct is not None else ''
        marker='roster' in a['endpoints']['untarg_data'];result=bool(rec.get('results_file_line'))
        rec['repository_marker']='valid untarg_data matrix and results-file declaration' if marker and result else 'valid untarg_data matrix only' if marker else 'results-file declaration only' if result else 'none'
        rec['acquisition_evidence_profile']=acq.evidence_profile(rec['acquisition_wording_any_section'],marker or result) if rec['modality']=='MS' else ''
        pct=rec['untarg_data_pct_named_labels']
        rec['untarg_data_label_type']='' if not pct else 'mostly without a metabolite name (<=10% named)' if float(pct)<=10 else 'mostly named (>=90% named)' if float(pct)>=90 else 'mixed'
        inside=lambda sections,yes:any((x in acq.ANALYSIS_SECTIONS)==yes for x in sections.split(';') if x)
        rec['items_detected']='; '.join(k for k,ok in (
            ('wording: untargeted (AN/CH/MS sections)',inside(rec['untargeted_stated_in_sections'],True)),
            ('wording: untargeted (other sections)',inside(rec['untargeted_stated_in_sections'],False)),
            ('wording: targeted (AN/CH/MS sections)',inside(rec['targeted_stated_in_sections'],True)),
            ('wording: targeted (other sections)',inside(rec['targeted_stated_in_sections'],False)),
            ('wording: MRM/SRM/PRM (AN/CH/MS sections)',bool(rec['mrm_srm_prm_mentioned_analysis_sections'])),
            ('repository: valid untarg_data matrix',marker),('repository: results-file declaration',result),
            ('repository: results file declares m/z and RT',str(rec['results_file_has_mz']).lower() in ('yes','neutral masses') and str(rec['results_file_has_rt']).lower()=='yes'),
            ('labels: untarg_data mostly without a metabolite name',rec['untarg_data_label_type'].startswith('mostly without')),
        ) if ok)
        rec['raw_file_names_in_mwtab']=str(float(len(parsed['raw_files']))) if parsed['has_mwtab'] else '';rawsets[aid]=parsed['raw_files']
        section['acquisition_evidence']=rec
        missing_external.append({'analysis_id':aid,'missing':'Pan-ReDU file-level evidence and its analysis/study joins',
                                 'reason':'not exposed by the MW endpoints fetched for this run; not copied from the historical evidence table'})
        runtime[aid]={}
        for src,ep in a['endpoints'].items():
            if 'roster' in ep:runtime[aid][src]={'sample_annotations':sa.compute(sid,aid,ep['roster'])}
    for a in v['analyses']:
        s=rawsets[a['analysis_id']]
        a['sections']['analytical']['acquisition_evidence']['raw_file_set_shared_with_another_analysis']='yes' if s and sum(x==s for x in rawsets.values())>1 else ''
    from online_evidence import annotation_evidence,acquisition_evidence,export_compatibility
    annotation_evidence(v)
    missing_external=acquisition_evidence(v,live,run,out,acq,ax)
    # Save every current-code field before producing the legacy-compatible view.
    dump(out/'native_json'/path.name,v)
    export_compatibility(v,keep_flagged_index=rebuilt_after_rule(v,out))
    dump(path,v)
    from merit import manuscript_coordinate_context
    runtime['_coordinate_context']=manuscript_coordinate_context.study_evidence(str(live),sid)
    dump(out/'runtime'/f'{sid.lower()}.json',runtime)
    dump(out/'external_dependencies'/f'{sid.lower()}.json',missing_external)
    refpath=BUNDLE_HOME/'assets/alert_reference_R1.json'
    if refpath.exists():
        reference=json.loads(refpath.read_text());rd.reference=lambda: reference
        profiles={}
        for a in v['analyses']:
            profiles[a['analysis_id']]={}
            for src,ep in a['endpoints'].items():
                if 'roster' not in ep:continue
                sec=ep['sections'];an=sec.get('analytical',{})
                vals={'missingness':an.get('sample_missingness',{}).get('score'),**{k:an.get(k,{}).get('default',{}).get('score') for k in ('low_information','redundancy','outliers')}}
                items={k:{'value':x} for k,x in vals.items()};u=an.get('measurement_scale',{});items['units']={'value':u.get('score'),'flag':u.get('units',{}).get('declared')}
                data=rd.data_result(items);task=rd.task_result(sec.get('labels'),sec.get('ml',{}).get('default'),5)
                profiles[a['analysis_id']][src]={'summary':rd.summary(data,task),'summary_parts':rd.summary_parts(data,task),'data':data,'task':task}
        dump(out/'readiness'/f'{sid.lower()}.json',profiles)


def redundancy(path, out, stage):
    mod = stage('add_redundancy_from_deposit')
    deferred, problems = mod.phase1(path)
    if problems: raise RuntimeError(problems)
    # The original builder uses this same block phase for >20,000 features.
    # Keep the block order deterministic and bound memory; no feature subsample.
    import numpy as np
    for stem in deferred:
        meta=json.loads(Path(f'{stem}__meta.json').read_text())
        f=len(meta['ev']); bs=mod.cv.block_size(f)
        # Exact shortcut: a distinct pair cannot have ten common values when
        # fewer than two features have ten values individually. The original
        # block code then returns only its forced diagonal entries (1, count1).
        possible=np.ones(f,dtype=bool) if meta['complete'] else np.load(f'{stem}__m.npy',mmap_mode='r').sum(axis=0)>=mod.cv.MIN_PAIR
        if possible.sum()<2:
            sums=np.ones(f);cnt=np.ones(f,dtype=np.int64)
            mod.finish(tuple(meta['key']),meta['n'],np.array(meta['ev']),meta['names'],sums,cnt,
                       np.zeros((0,2),dtype=np.int32),np.zeros(0,dtype=np.float32),None)
            print(json.dumps({'stage':'large_correlation','matrix':meta['key'],'exact_shortcut':'no distinct pair can reach the minimum common-value count','features':f}),flush=True)
            for p in mod.TMP.glob(Path(stem).name+'__*'):p.unlink()
            continue
        sums=np.zeros(f); cnt=np.zeros(f,dtype=np.int64); ii=[]; jj=[]; vv=[]
        for a in range(0,f,bs):
            _,(s,c,i,j,v)=mod.phase2_block((stem,meta['complete'],meta['n'],a,min(f,a+bs)))
            sums[a:a+len(s)]=s; cnt[a:a+len(c)]=c; ii.append(i); jj.append(j); vv.append(v)
            print(json.dumps({'stage':'large_correlation','matrix':meta['key'],'blocks_done':a//bs+1,'blocks':(f+bs-1)//bs}),flush=True)
        _,_,pairs,rho=mod.cv.merge_blocks(sums,cnt,ii,jj,vv)
        mod.finish(tuple(meta['key']),meta['n'],np.array(meta['ev']),meta['names'],sums,cnt,pairs,rho,None)
        mod._P.clear()
        for p in mod.TMP.glob(Path(stem).name+'__*'): p.unlink()
    v = json.loads(path.read_text())
    for a in v['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep: continue
            r = json.loads((mod.RES / f"{v['study_id']}__{a['analysis_id']}__{src}.json").read_text())
            ep['sections']['analytical']['redundancy'] = {k: r[k] for k in ('file', 'features', 'evaluated', 'values_stored', 'pairs_stored')}
            ep['sections']['analytical']['redundancy']['default'] = r['0.90']
    dump(path, v)


def separability(path, out, stage):
    mod = stage('add_separability_from_deposit')
    _, problems = mod.one(path)
    if problems: raise RuntimeError(problems)
    v = json.loads(path.read_text())
    for a in v['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' not in ep: continue
            r = ep['sections'].pop('ml_next'); old = out / r['store']; new = out / str(r['store']).replace('separability_next/', 'separability/')
            new.parent.mkdir(parents=True, exist_ok=True)
            old.replace(new); Path(str(old).replace('.npz', '_ranks.npy')).replace(Path(str(new).replace('.npz', '_ranks.npy')))
            r['store'] = str(new.relative_to(out))
            # Use exactly the current default-refresh call on newly built stores.
            r['default'] = mod.sp.compute(ep['roster'], v['study_id'], new, r['n_features'], conflicts=ep.get('sample_table_conflicts'))
            ep['sections']['ml'] = r
    dump(path, v)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--run-root', required=True, type=Path)
    ap.add_argument('--studies', nargs='+', help='Process only these studies from this run\'s freshly fetched registry')
    ap.add_argument('--extras-only', action='store_true', help='Resume informational stages from this same run\'s computed intermediates')
    args = ap.parse_args(); run = args.run_root.resolve()
    if not run.is_relative_to(HOME / 'runs'): raise ValueError('Run directory must be inside online-tool/runs')
    failed_fetches=[x for x in json.loads((run/'inputs/fetch_manifest.json').read_text())['requests'] if x.get('error')]
    if failed_fetches: raise RuntimeError('Fetch failed; missing data cannot be inferred from a transport failure: '+str([(x['url'],x['error']) for x in failed_fetches]))
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'; sys.dont_write_bytecode = True
    os.environ['OPENBLAS_NUM_THREADS'] = '1'; os.environ['OMP_NUM_THREADS'] = '1'
    out = run / 'assessment'; out.mkdir(parents=True, exist_ok=True)
    install_guard()
    live, registry = materialize(run)
    if args.studies:
        registry = {k: registry[k] for k in args.studies}
    os.environ['MERIT_MW_DUMP_ROOT'] = str(live); os.environ['MERIT_V9_CACHE_ROOT'] = str(out)
    for p in (BASE / 'outputs/measurement_scale_20260930', T4, STRUCT, CODE): sys.path.insert(0, str(p))
    stage, t4 = configure(live, out, run)
    failures = []
    def progress(sid, step, state, error=None):
        record = {'study_id': sid, 'stage': step, 'state': state, 'error': error, 'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        with (run / 'assessment_progress.jsonl').open('a') as f: f.write(json.dumps(record) + '\n')
        print(json.dumps(record), flush=True)
    try:
        paths = {}
        for sid, aids in registry.items():
            if args.extras_only:
                paths[sid] = out / 'json' / f'{sid.lower()}_v9.json'
                if not paths[sid].exists(): raise FileNotFoundError(paths[sid])
            else:
                progress(sid, 'bootstrap', 'started'); paths[sid] = bootstrap(sid, aids, live, out, stage); progress(sid, 'bootstrap', 'done')
        missing, scale = (None, None) if args.extras_only else prepare_units_and_zeros(registry, live, out, stage)
        for sid, path in paths.items():
            tasks = [
                ('metadata', lambda: metadata(sid, live, out, stage)),
                ('controls', lambda: stage('add_qc_blank_controls_from_rosters').one(path)),
                ('missingness', lambda: missing.one(path)),
                ('scale', lambda: scale.one(path)),
                ('low_information', lambda: stage('add_low_information_from_deposit').one(path)),
                ('redundancy', lambda: redundancy(path, out, stage)),
                ('outliers', lambda: stage('add_outliers_from_deposit').one(path)),
                ('qc_reproducibility', lambda: stage('add_qc_reproducibility_from_deposit').one(path)),
                ('annotation', lambda: stage('add_annotation_from_deposit').one(path)),
                ('refmet_lookup', lambda: stage('add_refmet_lookup_diagnostic').one(path)),
                ('labels', lambda: stage('add_labels_from_rosters').one(path)),
                ('separability', lambda: separability(path, out, stage)),
                ('extra_evidence_and_readiness', lambda: extra_evidence(path, live, out, stage, run,reuse_native=args.extras_only)),
            ]
            if args.extras_only: tasks = tasks[-1:]
            for name, action in tasks:
                progress(sid, name, 'started')
                try:
                    result = action()
                    if isinstance(result, tuple) and isinstance(result[-1], list) and result[-1]:
                        raise RuntimeError(result[-1])
                    progress(sid, name, 'done')
                except Exception:
                    error = traceback.format_exc(); failures.append({'study_id': sid, 'stage': name, 'error': error})
                    progress(sid, name, 'failed', error)
        if not failures:
            progress('all','live_context_and_indexes','started')
            try:
                from live_indexes import build
                build(run)
                progress('all','live_context_and_indexes','done')
            except Exception:
                error=traceback.format_exc();failures.append({'study_id':'all','stage':'live_context_and_indexes','error':error})
                progress('all','live_context_and_indexes','failed',error)
    finally:
        audit = {'historical_result_or_deposit_reads_forbidden': True,
                                          'denied_attempts': DENIED, 'files_read': sorted(READS),
                                          'existing_python_modules': {k: str(v.__file__) for k, v in MODULES.items()},
                                          'failures': failures, 'matrix_reuse': dict(MATRIX_REUSE),
                                          'fast_correlation': __import__('fast_correlation').STATS if 'fast_correlation' in sys.modules else None}
        dump(run / 'isolation_audit.json', audit)
        dump(run / 'isolation_audits' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S') + f'_{os.getpid()}.json'), audit)
    if failures: raise SystemExit(f'{len(failures)} stage failures; see isolation_audit.json')


if __name__ == '__main__': main()
