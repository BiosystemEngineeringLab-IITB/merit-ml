"""Fresh supporting references and metadata; never consume old MERIT results."""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser

PANREDU_COLUMNS = ('ATTRIBUTE_DatasetAccession','filename','MassSpectrometer',
                   'IonizationSourceAndPolarity','ChromatographyAndPhase','classification','MS2spectra_count')


def fetch_panredu(fetcher, sid):
    """Use Pan-ReDU's own read-only filtered-export callback, not a local table."""
    definition_file=fetcher.root/'reference/panredu_callback_definitions.json'
    if not definition_file.exists():
        fetcher.get('https://redu.gnps2.org/selection/_dash-dependencies',
                    'reference/panredu_callback_definitions.json')
    definitions=json.loads(definition_file.read_text())
    definition=next(x for x in definitions if x.get('output')=='download-dataframe-csv.data')
    states={'filter_query':'{ATTRIBUTE_DatasetAccession} = "'+sid+'"',
            'sort_by':[], 'columns':[{'id':x,'name':x} for x in PANREDU_COLUMNS]}
    if any(x['id']!='data-table' or x['property'] not in states for x in definition['state']):
        raise ValueError('Pan-ReDU export requires an unsupported callback state')
    payload={
        'output':'download-dataframe-csv.data',
        'outputs':{'id':'download-dataframe-csv','property':'data'},
        'inputs':[{'id':'download-button','property':'n_clicks','value':1},
                  {'id':'USIdownload-button','property':'n_clicks','value':0}],
        'state':[{**x,'value':states[x['property']]} for x in definition['state']],
        'changedPropIds':['download-button.n_clicks'],
    }
    return fetcher.get('https://redu.gnps2.org/selection/_dash-update-component',
                       f'{sid}/panredu_export_response.json',payload=payload)


class PageCells(HTMLParser):
    def __init__(self):
        super().__init__();self.cells=[];self.parts=[];self.depth=0

    def handle_starttag(self,tag,attrs):
        if tag in ('td','th'):
            if not self.depth:self.parts=[]
            self.depth+=1

    def handle_data(self,data):
        if self.depth:self.parts.append(data)

    def handle_endtag(self,tag):
        if tag in ('td','th') and self.depth:
            self.depth-=1
            if not self.depth:self.cells.append(re.sub(r'\s+',' ',' '.join(self.parts)).strip())


def webpage_raw_data(text, fetched_at, url):
    parser=PageCells();parser.feed(text)
    labels={'raw data available':'raw_data_available','raw data file type(s)':'raw_data_file_types',
            'uploaded file size':'uploaded_file_size'}
    fields={v:'' for v in labels.values()}
    found=set()
    study_ids=[]
    for index,cell in enumerate(parser.cells):
        key=cell.rstrip(':').strip().casefold()
        if key=='study id' and index+1<len(parser.cells):study_ids.append(parser.cells[index+1])
        if key in labels and index+1<len(parser.cells):
            fields[labels[key]]=parser.cells[index+1];found.add(labels[key])
    import urllib.parse
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    expected=(query.get('StudyID') or query.get('STUDY_ID') or [None])[0]
    if expected and study_ids and set(study_ids)!={expected}:raise ValueError('Study webpage identity mismatch')
    if study_ids:status='verified'
    elif fields['raw_data_available'].casefold() not in ('yes','no'):
        status='not_stated' if found else 'fields_not_found'
    else:status='verified'
    return {'page_status':status,**fields,'fetched_at_utc':fetched_at,'source_url':url}
