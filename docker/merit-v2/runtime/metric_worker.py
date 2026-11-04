"""Disposable, single-matrix request for the original interactive calculation."""
from __future__ import annotations
import copy
import json
import os
import sys
from pathlib import Path


def main():
    request=json.loads(Path(sys.argv[1]).read_text());os.environ['MERIT_V2_METRIC_WORKER']='1'
    from live_render import setup
    ui,pilot,live,out=setup(Path(request['run']))
    from native_resources import atomic,constrain_child
    arguments=request['arguments'];sid=arguments['study_id'];raw=pilot._raw_study(sid)
    from merit import labels_v9 as lb
    items=lb.factor_items(raw,arguments.get('overrides'))
    lb.factor_items=lambda *a,**kw:items
    scoped=copy.deepcopy(raw)
    for a in scoped['analyses']:
        for src,ep in a['endpoints'].items():
            if (a['analysis_id'],src)!=(request['analysis'],request['endpoint']):ep.pop('roster',None)
    pilot._raw_study=lambda *a,**kw:scoped
    constrain_child()
    if os.environ.get('MERIT_TEST_MODE')=='1' and request['endpoint']==os.environ.get('MERIT_TEST_INTERACTIVE_FAIL'):
        raise MemoryError('Injected interactive matrix failure')
    value=getattr(pilot,request['function'])(**arguments)
    atomic(request['output'],value)


if __name__=='__main__':main()
