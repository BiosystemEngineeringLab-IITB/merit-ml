"""Today's public Metabolomics Workbench study list, and the decision whether a requested ID can be assessed.

Source: the REST endpoint that lists every publicly available study and analysis
(/rest/study/study_id/ST/available). It is fetched at start-up and refreshed every few hours. An ID not in the list
is checked once more against /rest/study/study_id/<ID>/analysis, so a study released since the last refresh is
found; Workbench returns an empty answer for embargoed, unreleased and withdrawn studies. Only public studies are
assessed; nothing is shown for any other ID.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

API = 'https://www.metabolomicsworkbench.org/rest'
LIST_URL = API + '/study/study_id/ST/available'
SID = re.compile(r'ST\d{6}')
HOME = Path(__file__).resolve().parent
STORE = HOME / 'runs/web/public_inventory.json'


def _get(url, timeout):
    req = urllib.request.Request(url, headers={'User-Agent': 'MERIT-ML-live/1.0', 'Accept-Encoding': 'identity'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def embargo_dates():
    """Release dates of 8782's embargo list (merit/ui.py _EMBARGOED_STUDIES), read from the code snapshot."""
    ui = HOME / 'source_snapshot/merit-ui-v9-analysis-endpoint-pilot/v8_layout/merit/ui.py'
    try:
        block = re.search(r'_EMBARGOED_STUDIES[^=]*=\s*\{(.*?)\}', ui.read_text(), re.S)
        return dict(re.findall(r'"(ST\d{6})"\s*:\s*"(\d{4}-\d{2}-\d{2})"', block.group(1))) if block else {}
    except OSError:
        return {}


class Inventory:
    def __init__(self, refresh_seconds=3 * 3600, recheck_seconds=600):
        self.refresh_seconds = refresh_seconds; self.recheck_seconds = recheck_seconds
        self.lock = threading.Lock(); self.studies = {}; self.fetched_at = None; self.error = None
        self.negative = {}          # ID -> time it was last confirmed not public
        self.embargo = embargo_dates()
        try:
            value = json.loads(STORE.read_text())
            self.studies = {k: v for k, v in value['studies'].items()}; self.fetched_at = value['fetched_at_utc']
        except (OSError, ValueError, KeyError):
            pass

    # ---- list
    def refresh(self):
        try:
            raw = json.loads(_get(LIST_URL, 180))
            rows = raw.values() if isinstance(raw, dict) else raw
            studies = {}
            for r in rows:
                sid = str(r.get('study_id') or '').upper(); aid = str(r.get('analysis_id') or '').upper()
                if SID.fullmatch(sid):
                    studies.setdefault(sid, [])
                    if aid and aid not in studies[sid]: studies[sid].append(aid)
            if len(studies) < 1000:
                raise ValueError(f'public list unexpectedly short ({len(studies)} studies)')
            stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
            with self.lock:
                self.studies = studies; self.fetched_at = stamp; self.error = None; self.negative.clear()
            STORE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STORE.with_suffix('.tmp'); tmp.write_text(json.dumps({'source': LIST_URL, 'fetched_at_utc': stamp, 'studies': studies}))
            tmp.replace(STORE)
            return True
        except Exception as exc:
            with self.lock: self.error = repr(exc)[:300]
            return False

    def age_seconds(self):
        if not self.fetched_at: return float('inf')
        return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(self.fetched_at)).total_seconds()

    def start(self):
        def loop():
            while True:
                if self.age_seconds() >= self.refresh_seconds or not self.studies:
                    self.refresh()
                time.sleep(60 if not self.studies else 600)
        threading.Thread(target=loop, name='public-inventory', daemon=True).start()

    def latest(self):
        with self.lock:
            return max(self.studies) if self.studies else None

    # ---- decision
    def check(self, raw):
        """{'ok': bool, 'study_id', 'reason', 'message', 'latest'} for a user-supplied identifier."""
        text = str(raw or '').strip(); sid = text.upper()
        latest = self.latest()
        if not SID.fullmatch(sid):
            shown = text if len(text) <= 40 else text[:40] + '…'
            return {'ok': False, 'study_id': sid, 'reason': 'not_a_workbench_id', 'latest': latest,
                    'message': (f'“{shown}” is not a Metabolomics Workbench study ID. Study IDs are ST followed by six digits, '
                                'for example ST000096.') if text else 'Enter a Metabolomics Workbench study ID, for example ST000096.'}
        with self.lock:
            listed = sid in self.studies
            recently_negative = time.time() - self.negative.get(sid, 0) < self.recheck_seconds
        release = self.embargo.get(sid)
        if listed:
            return {'ok': True, 'study_id': sid, 'reason': 'public', 'latest': latest}
        now = None if recently_negative else self._public_now(sid)
        if now:
            return {'ok': True, 'study_id': sid, 'reason': 'public_since_last_list', 'latest': self.latest()}
        if now is None and not recently_negative and not latest:
            return {'ok': False, 'study_id': sid, 'reason': 'workbench_unreachable', 'latest': None,
                    'message': 'Metabolomics Workbench cannot be reached at the moment, so it cannot be confirmed that '
                               f'{sid} is public. Please try again in a few minutes.'}
        with self.lock: self.negative[sid] = time.time()
        if release and release > dt.date.today().isoformat():
            message = (f'{sid} is under embargo in Metabolomics Workbench until {release}. MERIT-ML does not show or assess embargoed '
                       'studies.')
        elif latest and sid > latest:
            message = (f'{sid} is not in the Metabolomics Workbench list of public studies today. The highest public study ID is '
                       f'{latest}; {sid} may not have been released yet.')
        else:
            message = (f'{sid} is not in the Metabolomics Workbench list of public studies today. It may be under embargo, '
                       'not yet released or withdrawn. MERIT-ML does not show or assess such studies.')
        return {'ok': False, 'study_id': sid, 'reason': 'not_public', 'latest': latest, 'message': message}

    def _public_now(self, sid):
        """A study released after the last list refresh: its analysis registry is no longer empty."""
        try:
            value = json.loads(_get(f'{API}/study/study_id/{sid}/analysis', 30) or b'[]')
        except Exception:
            return None          # unknown: Workbench did not answer
        rows = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        aids = [str(r.get('analysis_id') or '').upper() for r in rows if isinstance(r, dict) and str(r.get('study_id') or '').upper() == sid]
        if not aids:
            return False
        with self.lock:
            self.studies[sid] = sorted(set(aids))
        return True

    def status(self):
        with self.lock:
            return {'source': LIST_URL, 'public_studies': len(self.studies), 'latest_public_study': max(self.studies) if self.studies else None,
                    'fetched_at_utc': self.fetched_at, 'refresh_every_hours': self.refresh_seconds / 3600, 'error': self.error}
