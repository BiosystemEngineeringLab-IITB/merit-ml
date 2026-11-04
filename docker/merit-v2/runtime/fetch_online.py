"""Fetch public MW inputs from the network; never consult a local deposit/cache.

Raw responses and HTTP provenance are saved beneath one new run directory.
The datatable URL deliberately ends in /datatable/; /datatable/file is forbidden.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = 'https://www.metabolomicsworkbench.org'
API = BASE + '/rest'
FETCH_WORKERS = 8
HOME = Path(__file__).resolve().parent
# Global references pinned to the exact files 8782 uses (assets/reference/reference_policy.json).
PINNED = HOME / 'assets/reference'
PINNED_FILES = ('refmet_db.csv', 'refmet_all_ids_rest_20260929.json')


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject the forbidden endpoint before urllib follows a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if '/datatable/file' in newurl.lower():
            raise RuntimeError('Forbidden redirect to /datatable/file')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def records(value):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return list(value.values()) if value and all(isinstance(x, dict) for x in value.values()) else [value]
    return []


class Fetcher:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.entries = []

    def get(self, url, relative, *, payload=None, timeout=300, attempts=5, timeout_attempts=2):
        # Workbench builds some large responses (metabolites, factors) for 90-170 s before the first byte: wait up to
        # 300 s per request, and repeat a timed-out request only once so a hung endpoint costs at most ~10 min.
        if '/datatable/file' in url.lower():
            raise ValueError('Forbidden endpoint: /datatable/file')
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root):
            raise ValueError(relative)
        start = dt.datetime.now(dt.timezone.utc).isoformat()
        data = None
        error = None
        headers = {}
        code = None
        final = url
        timeouts = 0
        for attempt in range(attempts):
            try:
                request_headers={'User-Agent': 'MERIT-ML-online-parity-pilot/1.0', 'Accept-Encoding': 'identity'}
                body=None if payload is None else json.dumps(payload).encode()
                if body is not None: request_headers['Content-Type']='application/json'
                req = urllib.request.Request(url, data=body, headers=request_headers)
                opener = urllib.request.build_opener(SafeRedirectHandler())
                with opener.open(req, timeout=timeout) as response:
                    if '/datatable/file' in response.url:
                        raise RuntimeError('Forbidden redirect to /datatable/file')
                    data = response.read()
                    code, headers, final = response.status, dict(response.headers), response.url
                error = None
                break
            except urllib.error.HTTPError as exc:
                code, data, error = exc.code, exc.read(), str(exc)
                if exc.code < 500 and exc.code != 429:
                    break
            except Exception as exc:
                error = repr(exc)
                if isinstance(exc, TimeoutError) or 'timed out' in str(exc):
                    timeouts += 1
                    if timeouts >= timeout_attempts:
                        break
            if attempt+1 < attempts:
                time.sleep(min(8, 2 ** attempt))
        if data is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        entry = {'url': url, 'final_url': final, 'file': str(relative), 'started_utc': start,
                 'finished_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'http_status': code,
                 'bytes': len(data) if data is not None else None,
                 'sha256': hashlib.sha256(data).hexdigest() if data is not None else None,
                 'headers': headers, 'error': error}
        if payload is not None:
            entry.update(method='POST',request_json=payload)
        self.entries.append(entry)
        self.save()
        return data

    def save(self):
        # Calls are made by a small thread pool; replace atomically using a
        # thread-specific temporary name to avoid truncating the live manifest.
        import threading
        path = self.root / f'.fetch_manifest.{threading.get_ident()}.tmp'
        path.write_text(json.dumps({'schema': 'merit.online.fetch.v1', 'requests': list(self.entries)}, indent=2))
        path.replace(self.root / 'fetch_manifest.json')

    def require_success(self,files):
        """A failed HTTP transfer is not evidence that a field/table is absent."""
        latest={e['file']:e for e in self.entries}
        def valid(name):
            e=latest.get(name,{})
            if e.get('source_kind')=='versioned_reference_asset':
                return (name.startswith('reference/') and e.get('reference_policy') in ('match_current_8782','pinned_8782')
                        and not e.get('error') and (self.root/name).is_file()
                        and hashlib.sha256((self.root/name).read_bytes()).hexdigest()==e.get('sha256'))
            return not e.get('error') and e.get('http_status')==200
        bad=[{'file':name,'status':latest.get(name,{}).get('http_status'),
              'error':latest.get(name,{}).get('error','response not received')}
             for name in files if name not in latest or not valid(name)]
        if bad:raise RuntimeError('Live input retrieval incomplete; no assessment was inferred from the failed responses: '+json.dumps(bad))

    def study(self, sid):
        raw = self.get(f'{API}/study/study_id/{sid}/analysis', f'{sid}/analysis.json')
        self.require_success([f'{sid}/analysis.json'])
        if raw is None:
            raise RuntimeError(f'No analysis registry for {sid}')
        analyses = records(json.loads(raw))
        for record in analyses:
            if record.get('study_id') and str(record['study_id']).upper()!=sid:
                raise ValueError(f'Analysis registry identity mismatch for {sid}')
        aids = list(dict.fromkeys(str(x.get('analysis_id') or '').upper() for x in analyses if x.get('analysis_id')))
        if any(not re.fullmatch(r'AN\d{6}',aid) for aid in aids):raise ValueError('Invalid analysis identifier in registry')
        if not aids:
            raise RuntimeError(f'No analysis IDs in live registry for {sid}')
        jobs = [(f'{API}/study/study_id/{sid}/{kind}', f'{sid}/{kind}.json')
                for kind in ('summary', 'factors', 'metabolites', 'disease')]
        jobs += [(f'{BASE}/data/DRCCMetadata.php?Mode=Study&StudyID={sid}', f'{sid}/repository_page.html')]
        for aid in aids:
            jobs.extend([
                (f'{API}/study/analysis_id/{aid}/mwtab/txt', f'{sid}/{aid}/mwtab/{aid}_mwtab.txt'),
                (f'{API}/study/analysis_id/{aid}/mwtab/json', f'{sid}/{aid}/mwtab/{aid}_mwtab.json'),
                (f'{API}/study/analysis_id/{aid}/datatable/', f'{sid}/{aid}/tabular/{aid}_datatable_slash.txt'),
                (f'{API}/study/analysis_id/{aid}/untarg_data', f'{sid}/{aid}/tabular/{aid}_untarg_data.tsv'),
            ])
        with concurrent.futures.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            list(pool.map(lambda job: self.get(*job), jobs))
        self.require_success([job[1] for job in jobs])
        print(json.dumps({'stage': 'fetched', 'study_id': sid, 'analyses': aids}), flush=True)
        return aids


def panredu_pinned(sid):
    """The 4 Oct 2026 Pan-ReDU snapshot used by 8782 is authoritative for the original cohort; the frozen Pan-ReDU dump
    (3 Nov 2026, split per study) for every other study once it is listed in reference_policy.json. No live request then."""
    ids = (PINNED / 'original_cohort_4121_study_ids.txt').read_text().split()
    if sid in set(ids): return True
    return 'panredu_dump_20261104/<study>.tsv' in json.loads((PINNED / 'reference_policy.json').read_text()).get('files', {})


def link_pinned_references(fetcher):
    import os, shutil
    policy = json.loads((PINNED / 'reference_policy.json').read_text())
    for name in PINNED_FILES:
        source = PINNED / name
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != policy['files'][name]['sha256']:
            raise ValueError('Pinned reference modified: ' + name)
        dest = fetcher.root / 'reference' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            try: os.link(source, dest)
            except OSError: shutil.copyfile(source, dest)
        fetcher.entries.append({'url': None, 'final_url': None, 'file': 'reference/' + name, 'http_status': None,
                                'bytes': source.stat().st_size, 'sha256': digest, 'error': None, 'headers': {},
                                'source_kind': 'versioned_reference_asset', 'reference_policy': 'pinned_8782',
                                'started_utc': None, 'finished_utc': None})
    dest = fetcher.root / 'reference/reference_policy.json'
    dest.write_text(json.dumps(policy, indent=1))
    fetcher.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-root', required=True, type=Path)
    ap.add_argument('--studies', nargs='+', default=['ST000001', 'ST000096', 'ST002324', 'ST000788'])
    ap.add_argument('--append', action='store_true', help='Add newly fetched studies to this same live-input run; never read a historical deposit')
    ap.add_argument('--with-panredu', action='store_true', help='Fetch the public Pan-ReDU metadata for each requested study as external supporting evidence')
    ap.add_argument('--shared-reference-inputs',type=Path,help='Reuse only the freshly downloaded chemical reference snapshot of this same online-pull cohort')
    args = ap.parse_args()
    f = Fetcher(args.run_root / 'inputs')
    studies = {}
    if args.append:
        if (args.run_root / 'live_registry.json').exists(): studies = json.loads((args.run_root / 'live_registry.json').read_text())
        if (f.root / 'fetch_manifest.json').exists(): f.entries = json.loads((f.root / 'fetch_manifest.json').read_text())['requests']
    # Chemical references are the pinned files used by 8782, linked (not downloaded)
    # so every assessment uses the same versions; no historical study result is read.
    link_pinned_references(f)
    for sid in args.studies:
        if not (len(sid) == 8 and sid.startswith('ST') and sid[2:].isdigit()):
            raise ValueError(sid)
        studies[sid] = f.study(sid)
        if args.with_panredu and not panredu_pinned(sid):
            from live_references import fetch_panredu
            fetch_panredu(f,sid)
    (args.run_root / 'live_registry.json').write_text(json.dumps(studies, indent=2))
    f.save()


if __name__ == '__main__':
    main()
