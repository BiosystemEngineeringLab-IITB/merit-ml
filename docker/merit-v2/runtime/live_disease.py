"""Read the deposited study-level disease declaration; never infer it from sample labels."""
import json
from pathlib import Path
from fetch_online import records

def declaration(payload, study_id):
    from merit.connectors.workbench import MetabolomicsWorkbenchConnector
    values=[]
    for row in records(payload):
        sid=str(row.get('Study ID') or row.get('study_id') or row.get('STUDY_ID') or '').strip().upper()
        if sid and sid!=study_id.upper():continue
        value=MetabolomicsWorkbenchConnector._extract_disease_name(row)
        if value and value not in values:values.append(value)
    return '; '.join(values)

def read(root, study_id):
    try:payload=json.loads((Path(root)/study_id/'disease.json').read_text())
    except (OSError,ValueError):return ''
    return declaration(payload,study_id)
