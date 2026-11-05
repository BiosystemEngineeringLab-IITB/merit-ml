// Stream and bound each response before allocating a complete Python/JS table.
const INPUT_LIMIT=50_000_000, METADATA_LIMIT=10_000_000;
async function downloadInput(url,path,scope={}) {
  const started=new Date().toISOString(),abort=new AbortController();let idle,total;
  const reset=()=>{clearTimeout(idle);idle=setTimeout(()=>abort.abort('No bytes received for 180 seconds'),180000);};
  total=setTimeout(()=>abort.abort('Transfer exceeded 10 minutes'),600000);reset();
  let bytes=0,parts=[],headers={},response=null,error=null,refusal=null,complete=false,meta=[],metaSize=0;
  const mw=path.endsWith('_mwtab.txt'),decoder=mw?new TextDecoder():null;
  let carry='',active=false,keep=0;
  const line=s=>{
    if(s.includes('_DATA_START')){active=true;keep=2;}
    if(s.includes('_DATA_END'))active=false;
    if(!active||keep>0||s.includes('_DATA_START')){metaSize+=s.length*3;if(metaSize>METADATA_LIMIT)throw Error('mwTab metadata exceeds the browser metadata limit');meta.push(s);}
    if(active&&keep>0&&!s.includes('_DATA_START'))keep--;
  };
  py.runPython('import hashlib; _transfer_hash=hashlib.sha256()');
  try {
    const transport=cfg.fixture_base&&url.startsWith('https://www.metabolomicsworkbench.org/')?cfg.fixture_base+'/'+url.split('.org/')[1]:url;
    response=await fetch(transport,{signal:abort.signal,cache:'no-store'});headers=Object.fromEntries(response.headers);
    const reader=response.body.getReader();
    if(!mw&&Number(headers['content-length']||0)>INPUT_LIMIT){refusal='Not assessed in the browser: the declared response size exceeds the 50 MB input safety limit';await reader.cancel();}
    if(!refusal)
    for(;;){
      const {value,done}=await reader.read();if(done){complete=true;break;}reset();bytes+=value.byteLength;
      const chunk=py.toPy(value);py.globals.set('_transfer_chunk',chunk);py.runPython('_transfer_hash.update(_transfer_chunk)');chunk.destroy();
      if(mw){carry+=decoder.decode(value,{stream:true});let i;while((i=carry.indexOf('\n'))>=0){line(carry.slice(0,i+1));carry=carry.slice(i+1);}if(carry.length>METADATA_LIMIT)throw Error('A metadata line is too large for the browser');}
      if(bytes<=INPUT_LIMIT)parts.push(new Blob([value]));
      else {
        refusal='Not assessed in the browser: this response exceeds the 50 MB input safety limit';parts=[];
        if(!mw){await reader.cancel();break;}
      }
    }
    if(mw){carry+=decoder.decode();if(carry)line(carry);}
    if(!response.ok)error='HTTP '+response.status;
  } catch(e){error=String(e.message||e);} finally{clearTimeout(idle);clearTimeout(total);}
  const digest=py.runPython('_transfer_hash.hexdigest()');py.runPython('del _transfer_hash\ntry: del _transfer_chunk\nexcept NameError: pass');
  const entry={url,final_url:response?.url||url,file:path,started_utc:started,finished_utc:new Date().toISOString(),
    http_status:response?.status||null,headers,bytes,declared_response_bytes:Number(headers['content-length']||0)||null,sha256:complete?digest:null,received_sha256:digest,
    hash_scope:complete?'complete response body':'received bytes only; response incomplete',error,resource_refusal:refusal,
    ...scope};
  const full=HOME+'/runs/browser/'+currentJob+'/inputs/'+path;
  if(!error&&!refusal){const blob=new Blob(parts);await storeFiles([{path:full,blob,size:blob.size,stamp:Date.now()}]);return {entry,blob};}
  if(mw&&refusal&&!error&&complete){
    // Not a raw response: a separately declared metadata-only view, with the raw hash above.
    const blob=new Blob(meta);entry.metadata_only_view=true;
    await storeFiles([{path:full,blob,size:blob.size,stamp:Date.now()}]);
  }
  return {entry,blob:null};
}

async function fetchStudy(sid,job) {
  currentJob=job;await clearFiles();restored=new Map();
  const run=HOME+'/runs/browser/'+job,entries=[],refused=[];
  const api='https://www.metabolomicsworkbench.org/rest';
  let rec=await downloadInput(api+'/study/study_id/'+sid+'/analysis',sid+'/analysis.json');entries.push(rec.entry);
  if(rec.entry.error||!rec.blob)throw Error('Analysis registry could not be retrieved; no study results were inferred');
  py.globals.set('_registry_text',await rec.blob.text());py.globals.set('_registry_sid',sid);
  const aids=JSON.parse(py.runPython(`import json,re
from fetch_online import records
_rows=records(json.loads(_registry_text))
if any(r.get('study_id') and str(r['study_id']).upper()!=_registry_sid for r in _rows):raise ValueError('Analysis registry identity mismatch')
_aids=list(dict.fromkeys(str(r['analysis_id']).upper() for r in _rows if r.get('analysis_id')))
if not _aids or any(not re.fullmatch(r'AN\\d{6}',a) for a in _aids):raise ValueError('Invalid or empty analysis registry')
json.dumps(_aids)`));
  const jobs=['summary','factors','metabolites','disease'].map(k=>[api+'/study/study_id/'+sid+'/'+k,sid+'/'+k+'.json',{}]);
  jobs.push(['https://www.metabolomicsworkbench.org/data/DRCCMetadata.php?Mode=Study&StudyID='+sid,sid+'/repository_page.html',{}]);
  for(const aid of aids)for(const [suffix,rel,endpoint] of [
    ['mwtab/txt','mwtab/'+aid+'_mwtab.txt','mwtab'],['mwtab/json','mwtab/'+aid+'_mwtab.json',null],
    ['datatable/','tabular/'+aid+'_datatable_slash.txt','datatable'],['untarg_data','tabular/'+aid+'_untarg_data.tsv','untarg_data']])
    jobs.push([api+'/study/analysis_id/'+aid+'/'+suffix,sid+'/'+aid+'/'+rel,{analysis_id:aid,endpoint}]);
  for(let i=0;i<jobs.length;i++){
    const [url,path,scope]=jobs[i];say('status',{text:`Downloading ${sid}: ${i+1}/${jobs.length} · ${path.split('/').pop()}`});
    const result=await downloadInput(url,path,scope);entries.push(result.entry);
    if(result.entry.resource_refusal&&scope.endpoint)refused.push({...scope,transfer_limit:true,bytes:result.entry.declared_response_bytes||result.entry.bytes,reason:result.entry.resource_refusal,positions:0,features:0});
  }
  await restoreRun(py,run);write(run+'/live_registry.json',new TextEncoder().encode(JSON.stringify({[sid]:aids})));
  write(run+'/public_catalog.json',new TextEncoder().encode(py.runPython("json.dumps([{'study_id':sid,'analysis_type':''} for sid in (__import__('browser_runtime')._INVENTORY.studies if __import__('browser_runtime')._INVENTORY else {})])")));
  write(run+'/inputs/fetch_manifest.json',new TextEncoder().encode(JSON.stringify({schema:'merit.online.fetch.v1',requests:entries})));
  write(run+'/browser_transfer_limits.json',new TextEncoder().encode(JSON.stringify(refused)));
  // Global/versioned reference inputs are identical to the native fetcher's policy.
  py.runPython(`from pathlib import Path
from fetch_online import Fetcher,link_pinned_references
_f=Fetcher(Path(${JSON.stringify(run)})/'inputs',allow_partial=True)
_f.entries=json.loads((_f.root/'fetch_manifest.json').read_text())['requests'];link_pinned_references(_f)`);
  py.runPython(`from fetch_online import panredu_pinned
if not panredu_pinned(${JSON.stringify(sid)}):
 from live_references import fetch_panredu
 fetch_panredu(_f,${JSON.stringify(sid)})`);
  await saveRun(py,run);return run;
}
