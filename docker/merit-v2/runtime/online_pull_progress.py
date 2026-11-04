"""Human-readable watch output; reads status only and never starts a job."""
import argparse
from collections import Counter
import datetime as dt
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def pending_breakdown(studies, total):
    """Retries are pending, not terminal failures or untouched studies."""
    states = Counter(item.get('state') for item in studies.values())
    return {'retries_queued': states['retry'], 'running': states['running'],
            'not_started': total - len(studies), 'terminal_failures': states['exhausted']}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--run-root',type=Path);args=ap.parse_args()
    run=args.run_root or Path(json.loads((ROOT/'online_pull_latest.json').read_text())['run_root'])
    p=run/'online_pull_progress.json'
    if not p.exists():print('Preparing online pull:',run.name);return
    checkpoint=run/'online_pull_checkpoint.json'
    state=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    s=state.get('progress') or json.loads(p.read_text());n=s['completed'];total=s['total'];c=s.get('current') or {};width=38;filled=int(width*n/max(total,1))
    print('MERIT-ML ONLINE PULL + METRICS  |  8791')
    print(f'{n}/{total} COMPLETE   [{"#"*filled}{"-"*(width-filled)}]   {100*n/max(total,1):.2f}%')
    if s.get('cohort_description'):print(s['cohort_description'])
    print(f'Failed after all retries: {s["failed"]}   Pending: {s["pending"]}   Valid matrices stored: {s["matrices"]:,}')
    if 'studies' in state:
        counts=pending_breakdown(state['studies'],total)
        print(f'Retries queued: {counts["retries_queued"]}   Running: {counts["running"]}   Not started: {counts["not_started"]}')
    print('Phase:',s['phase_label'])
    if c.get('study_id'):print(f'Current: {c["ordinal"]}/{total}  {c["study_id"]}  attempt {c.get("attempt",1)}')
    if c.get('label'):print(c['label'])
    if c.get('detail'):print(c['detail'])
    if c.get('requests_completed') is not None:print('Source responses:',c['requests_completed'])
    print('Comparison:',s['comparison_state'])
    print(f'Elapsed: {dt.timedelta(seconds=s["elapsed_seconds"])}   Free disk: {s["free_disk_gib"]} GiB')
    if s.get('eta_seconds') is not None:print('Estimated remaining calculation time:',dt.timedelta(seconds=s['eta_seconds']))
    if s.get('error'):print('ATTENTION:',s['error'])
    owner=run/'online_pull_controller.json'
    if owner.exists():
        process=json.loads(owner.read_text());pid=process['pid']
        # A restricted shell may have a different PID namespace; absence of
        # the host PID there is not evidence that the detached process died.
        if process.get('pid_namespace')==os.readlink('/proc/self/ns/pid'):
            try:os.kill(pid,0)
            except ProcessLookupError:
                if not s['phase'].startswith('finished'):print('CONTROLLER IS NOT RUNNING. Resume with online_pull_cohort.py --run-root '+str(run))
        print('Detached controller PID:',pid)
    print('Updated:',s['updated_at_utc'])
    print('Browser:',s['dashboard'])
    print('Output:',run)


if __name__=='__main__':main()
