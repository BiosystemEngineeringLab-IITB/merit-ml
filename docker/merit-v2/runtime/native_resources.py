"""Native subprocess boundaries. Resource failures never represent a numeric score."""
from __future__ import annotations
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def json_default(value):
    if isinstance(value,set):return {'__merit_native_set__':sorted(value)}
    if hasattr(value,'item'):return value.item()
    if hasattr(value,'tolist'):return value.tolist()
    raise TypeError(f'Unsupported worker result type: {type(value).__name__}')


def json_hook(value):
    if set(value)=={'__merit_native_set__'}:return set(value['__merit_native_set__'])
    return value


def atomic(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,allow_nan=True,separators=(',',':'),default=json_default))
    tmp.replace(path)


def memory_available():
    """Available host/cgroup bytes, not total installed RAM."""
    candidates=[]
    try:
        d=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
        candidates.append(int(d['MemAvailable'].split()[0])*1024)
    except (OSError,KeyError,ValueError):pass
    for root,limit,current in (('/sys/fs/cgroup','memory.max','memory.current'),
                                ('/sys/fs/cgroup/memory','memory.limit_in_bytes','memory.usage_in_bytes')):
        try:
            cap=int((Path(root)/limit).read_text());used=int((Path(root)/current).read_text())
            if 0<cap<2**60:candidates.append(max(0,cap-used))
        except (OSError,ValueError):pass
    return min(candidates) if candidates else None


def budget_bytes():
    configured=float(os.environ.get('MERIT_WORKER_MEMORY_MB','0'))
    if configured>0:return int(configured*1024**2)
    available=memory_available()
    return int(available*.65) if available is not None else None


def constrain_child():
    """Reserve headroom for the server. This limits new allocations before an OS OOM."""
    raw=os.environ.get('MERIT_CHILD_BUDGET_BYTES')
    if not raw:return
    import resource
    budget=int(raw)
    try:
        lines=Path('/proc/self/status').read_text().splitlines()
        fields=dict(line.split(':',1) for line in lines if ':' in line)
        vms=int(fields['VmSize'].split()[0])*1024
        rss=int(fields['VmRSS'].split()[0])*1024
        cap=vms+max(0,budget-rss)
        resource.setrlimit(resource.RLIMIT_AS,(cap,cap))
    except (OSError,ValueError,KeyError):pass
    # Prefer the disposable worker over the serving process if Linux must kill.
    try:Path('/proc/self/oom_score_adj').write_text('500')
    except OSError:pass


def run_isolated(command, log, timeout=None, env=None):
    budget=budget_bytes();start=time.monotonic();peak=0
    environment={**os.environ,**(env or {}),'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}
    if budget:environment['MERIT_CHILD_BUDGET_BYTES']=str(budget)
    seconds=float(os.environ.get('MERIT_CHECK_TIMEOUT_SECONDS','1800')) if timeout is None else timeout
    reason=None;code=None
    Path(log).parent.mkdir(parents=True,exist_ok=True)
    with Path(log).open('w') as f:
        p=subprocess.Popen(command,stdout=f,stderr=subprocess.STDOUT,env=environment,start_new_session=True)
        while p.poll() is None:
            try:
                rows=Path(f'/proc/{p.pid}/status').read_text().splitlines()
                rss=next(int(x.split()[1])*1024 for x in rows if x.startswith('VmRSS:'))
                peak=max(peak,rss)
                if budget and rss>budget:code='memory_budget';reason='Worker exceeded the available-memory budget'
            except (OSError,StopIteration,ValueError):pass
            if seconds and time.monotonic()-start>seconds:code='timeout';reason=f'Check exceeded its {seconds:g}-second time limit'
            if reason:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                break
            time.sleep(.1)
        status=p.wait()
    if status and reason is None:
        tail=Path(log).read_text(errors='replace')[-6000:]
        if 'MemoryError' in tail or 'Cannot allocate memory' in tail:code='memory_budget';reason='Not enough memory within the worker budget'
        elif 'No space left on device' in tail:code='disk_full';reason='Not enough writable disk space'
        elif status<0:code='terminated';reason=f'Worker terminated by signal {-status}; memory exhaustion is possible but not confirmed'
        else:code='calculation_error';reason='Calculation failed; see the check log'
    return {'state':'not_assessed' if status or reason else 'complete','reason':reason,'code':code,
            'returncode':status,'elapsed_seconds':round(time.monotonic()-start,3),
            'peak_rss_bytes':peak,'memory_budget_bytes':budget,'log':str(log)}
