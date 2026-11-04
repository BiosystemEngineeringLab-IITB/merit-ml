"""Use the manuscript's exact input-validity functions, without its plotting CLI.

Only pure constants and functions are compiled from the bundled source. No
cohort enumeration, stored availability table, plotting import or old result is
executed or loaded.
"""
import ast
import math
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def rules():
    from online_assess import BASE
    path=BASE/'merit/manuscript/figures/Figure-2/generate_figure2b_source_availability_frozen_cache.py'
    tree=ast.parse(path.read_text());names={'BLOCKS','STARTS','ENDS','MALFORMED_EXP','VALID','INVALID','ABSENT'}
    functions={'is_numeric','samples_by_features','mwtab_text'};selected=[]
    for node in tree.body:
        if isinstance(node,ast.Assign):
            targets={n.id for t in node.targets for n in ast.walk(t) if isinstance(n,ast.Name)}
            if targets & names:selected.append(node)
        elif isinstance(node,ast.FunctionDef) and node.name in functions:selected.append(node)
    env={'re':re,'math':math,'Path':Path}
    exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),env)
    assert functions<=set(env)
    return env


def is_valid(path,source):
    r=rules()
    status=r['mwtab_text'](Path(path)) if source=='mwtab' else r['samples_by_features'](Path(path))
    return status==r['VALID']
