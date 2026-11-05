"""Reference cut-offs of the analytical review alerts (decided by the author on 2026-10-25): for sample-level missingness,
low-information features, redundant features (kept by the author on 2026-10-25) and outlier samples, the 2.5th, 5th, 7.5th and 10th
percentiles (numpy.percentile, linear) of the stored default scores of the reference cohort, with the number of matrices alerted at each.
A matrix is alerted on a metric when its value is below the cut-off (strictly: matrices tied at the cut-off are not alerted).

Versioned (after a review on 2026-10-25: a rebuild must not recalculate the reference silently). Each version is kept as
<v9 root>/readiness_reference/<version>.json with the list of its matrices (study, analysis, endpoint) and their four values, its cut-offs
and its counts; readiness_reference/current.json names the version in force. manifest.sections.readiness is written from that file.
  add_readiness_reference.py                      no version yet: creates R1 from every valid matrix. Otherwise restores the version in
                                                  force into the manifest (build_v9_cache.py rewrites the manifest) and checks that the
                                                  current records reproduce its cut-offs on its own matrices; exits 2 if they do not (the
                                                  version in force is kept: nothing is recalculated).
  add_readiness_reference.py --new-version NAME   a new reference from every valid matrix now stored; earlier versions are kept.
Studies added later are judged against the version in force; they enter a reference only with --new-version.
manifest_sync.py checks the manifest against the version file and the version's cut-offs against the current records of its matrices.
usage: add_readiness_reference.py [--new-version NAME] [v9 root]
"""
import datetime, hashlib, json, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
ARGS = [a for a in sys.argv[1:]]
NEW = None
if '--new-version' in ARGS:
    i = ARGS.index('--new-version'); NEW = ARGS[i + 1]; del ARGS[i:i + 2]
V9 = Path(ARGS[0]) if ARGS else Path('/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/merit-cache-workbench-full-v9')
REF = V9 / 'readiness_reference'
KEYS = ('missingness', 'low_information', 'redundancy', 'outliers')
PERCENTILES = (2.5, 5.0, 7.5, 10.0)
PERCENTILE = 2.5
RULES = ('analytical review alerts, two tiers: valid matrix, then an extreme alert when sample-level missingness, low-information '
         'features, redundant features or outlier samples are below (strictly) the extreme percentile (default 2.5) of the reference '
         'cohort, a low alert when below the low percentile (default 10) but not the extreme one (Show tools: 2.5, 5, 7.5, 10); '
         'summary per matrix: sample-count result, alert result (strongest tier, with the number of checks raising each tier) and checks assessed, side by side with no priority; reference unavailable is an error; '
         'units declared is a documentation note; cross-validation sample counts (classes in force, default the '
         'deposited labels): met (>= 2 classes, smallest class >= k folds), not met, or unresolved (classes not assessable); task '
         'profile and interpretation/reuse: values only')


def one_matrix(path):
    v = json.loads(Path(path).read_text()); out = []
    for a in v['analyses']:
        for src, ep in a['endpoints'].items():
            if 'roster' in ep:
                an = ep['sections']['analytical']
                out.append(([v['study_id'], a['analysis_id'], src],
                            [an['sample_missingness'].get('score'), an['low_information']['default'].get('score'),
                             an['redundancy']['default'].get('score'), an['outliers']['default'].get('score')]))
    return out


def current_values(root=V9):
    with Pool(32) as pool:
        return {tuple(k): v for part in pool.map(one_matrix, sorted((Path(root) / 'json').glob('*_v9.json')), chunksize=8) for k, v in part}


def compute(rows):
    """Cut-offs at every kept percentile, values per metric and alert counts, over the given matrices {key: [4 values]}."""
    vals = {k: np.array([r[i] for r in rows.values() if r[i] is not None], dtype=float) for i, k in enumerate(KEYS)}
    sens = {}
    for p in PERCENTILES:
        c = {k: float(np.percentile(vals[k], p)) for k in KEYS}
        alerted = sum(1 for r in rows.values() if any(r[i] is not None and r[i] < c[k] for i, k in enumerate(KEYS)))
        sens[f'{p:g}'] = {'cutoffs': c, 'matrices_with_an_alert': alerted, 'matrices': len(rows)}
    return sens, {k: int(vals[k].size) for k in KEYS}


def cohort_sha(keys):
    return hashlib.sha256('\n'.join('\t'.join(k) for k in sorted(keys)).encode()).hexdigest()


def make_version(name, rows):
    sens, n = compute(rows)
    ver = {'reference_version': name,
           'reference_cohort': f'all {len(rows):,} valid matrices assessed when {name} was made (default settings); fixed: studies added later do not change it',
           'reference_matrices': len(rows), 'reference_cohort_sha256': cohort_sha(rows),
           'percentile': PERCENTILE, 'comparison': 'strictly below the cut-off', 'cutoffs': sens[f'{PERCENTILE:g}']['cutoffs'],
           'n_matrices_with_a_value': n, 'sensitivity': sens,
           'created_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'), 'script': str(Path(__file__).resolve()),
           'matrices': [[*k, *v] for k, v in sorted(rows.items())]}
    REF.mkdir(exist_ok=True)
    out = REF / f'{name}.json'
    if out.exists():
        sys.exit(f'{out} exists: a version is never overwritten; choose another name')
    out.write_text(json.dumps(ver))
    (REF / 'current.json').write_text(json.dumps({'version': name, 'set_at': ver['created_at']}, indent=1))
    return ver


def check_version(ver, rows):
    """Differences between a version and the current records of its own matrices (empty = the version is reproduced)."""
    mine = {tuple(m[:3]): m[3:] for m in ver['matrices']}
    missing = [k for k in mine if k not in rows]
    changed = [k for k in mine if k in rows and any((a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9) for a, b in zip(mine[k], rows[k]))]
    sens, n = compute({k: rows[k] for k in mine if k in rows})
    cut_diff = [(p, k) for p in sens for k in KEYS if abs(sens[p]['cutoffs'][k] - ver['sensitivity'][p]['cutoffs'][k]) > 1e-9]
    return missing, changed, cut_diff


if __name__ == '__main__':
    rows = current_values()
    if NEW:
        ver = make_version(NEW, rows); status = f'new reference version {NEW} from {len(rows):,} matrices'; code = 0
    elif not (REF / 'current.json').exists():
        ver = make_version('R1', rows); status = f'first reference version R1 from {len(rows):,} matrices'; code = 0
    else:
        name = json.loads((REF / 'current.json').read_text())['version']
        ver = json.loads((REF / f'{name}.json').read_text())
        missing, changed, cut_diff = check_version(ver, rows)
        extra = len(set(rows) - {tuple(m[:3]) for m in ver['matrices']})
        if missing or changed or cut_diff:
            status = (f'REFERENCE NOT REPRODUCED: version {name} kept in force; {len(missing):,} of its matrices missing, {len(changed):,} with '
                      f'changed values, {len(cut_diff)} cut-offs differ (e.g. {(missing or changed or cut_diff)[:3]}). Make a new reference '
                      f'only deliberately: add_readiness_reference.py --new-version NAME')
            code = 2
        else:
            status = f'version {name} in force and reproduced from the current records ({extra:,} matrices outside the reference)'; code = 0
    m = json.loads((V9 / 'manifest.json').read_text())
    m['sections']['readiness'] = {'status': 'filled', 'scope': 'analysis x endpoint; nothing combined across metrics, matrices, analyses or the study',
                                  'rules': RULES, **{k: v for k, v in ver.items() if k != 'matrices'},
                                  'reference_file': f'readiness_reference/{ver["reference_version"]}.json'}
    (V9 / 'manifest.json').write_text(json.dumps(m, indent=1))
    print(json.dumps(m['sections']['readiness'], indent=1)); print(status)
    import subprocess                              # keep manifest.json and index.json equal to the records (manifest_sync.py)
    subprocess.run([sys.executable, str(Path(__file__).resolve().parent / 'manifest_sync.py'), '--write', '--root', str(V9)], check=code == 0)   # the same cache, never the default one
    sys.exit(code)
