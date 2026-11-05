"""Browser-only bounded output writers. No effect in either native application."""
import io
import json
from types import SimpleNamespace
LIMIT=50_000_000

class OutputLimit(ValueError):
    def __init__(self):super().__init__('This output exceeds the browser download limit (50 MB). Use the full application: https://github.com/BiosystemEngineeringLab-IITB/merit-ml. Completed calculations are retained.')

class BoundedBytesIO(io.BytesIO):
    def write(self,data):
        if self.tell()+len(data)>LIMIT:raise OutputLimit()
        return super().write(data)

class BoundedStringIO(io.StringIO):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.bytes_written=0
    def write(self,text):
        n=len(text.encode('utf-8'))
        if self.bytes_written+n>LIMIT:raise OutputLimit()
        self.bytes_written+=n
        return super().write(text)

def dumps(value,*args,**kwargs):
    # iterencode avoids building the final JSON string before enforcing the cap.
    buf=BoundedStringIO();enc=json.JSONEncoder(**kwargs)
    for chunk in enc.iterencode(value):buf.write(chunk)
    return buf.getvalue()

def install_limits():
    import live_render
    from merit import v9_matrix_export
    bounded=SimpleNamespace(BytesIO=BoundedBytesIO,StringIO=BoundedStringIO)
    v9_matrix_export.io=bounded;live_render.io=bounded
    live_render.json=SimpleNamespace(**{k:getattr(json,k) for k in dir(json) if not k.startswith('__')})
    live_render.json.dumps=dumps
