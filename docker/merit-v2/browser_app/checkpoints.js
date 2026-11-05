// Public study data and completed checks stay on this device, never on MERIT/R2.
// Blob-backed WORKERFS keeps restored files outside the WebAssembly heap.
let checkpointDB;
async function db() {
  if (checkpointDB) return checkpointDB;
  checkpointDB = await new Promise((resolve,reject)=>{
    const r=indexedDB.open('merit-live-checkpoints-v2',1);
    r.onupgradeneeded=()=>r.result.createObjectStore('files',{keyPath:'path'});
    r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);
  });return checkpointDB;
}
async function allFiles() {
  const d=await db();return new Promise((resolve,reject)=>{
    const r=d.transaction('files').objectStore('files').getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);
  });
}
async function storeFiles(puts,deletes=[]) {
  const d=await db();return new Promise((resolve,reject)=>{
    const t=d.transaction('files','readwrite');const s=t.objectStore('files');
    for(const p of deletes)s.delete(p);for(const p of puts)s.put(p);
    t.oncomplete=resolve;t.onerror=()=>reject(t.error);t.onabort=()=>reject(t.error);
  });
}
async function clearFiles(){const d=await db();return new Promise((r,j)=>{const t=d.transaction('files','readwrite');t.objectStore('files').clear();t.oncomplete=r;t.onerror=()=>j(t.error);});}
let restored=new Map();
async function restoreRun(py,run) {
  const files=(await allFiles()).filter(x=>x.path.startsWith(run+'/'));
  files.sort((a,b)=>(b.path.includes('/assessment/')?1:0)-(a.path.includes('/assessment/')?1:0));
  if(!files.length)return;
  const mount=HOME+'/browser_checkpoints';py.FS.mkdirTree(mount);
  py.FS.mount(py.FS.filesystems.WORKERFS,{blobs:files.map((x,i)=>({name:String(i),data:x.blob}))},mount);
  restored=new Map();
  let preload=Number(self.navigator.deviceMemory||8)<4?16*2**20:64*2**20;
  for(let i=0;i<files.length;i++){
    const x=files[i];py.FS.mkdirTree(x.path.slice(0,x.path.lastIndexOf('/')));
    try{py.FS.unlink(x.path);}catch{}
    if(x.size<=preload){
      py.FS.writeFile(x.path,new Uint8Array(await x.blob.arrayBuffer()));py.FS.utime(x.path,x.stamp,x.stamp);preload-=x.size;
    }else py.FS.symlink(mount+'/'+i,x.path);
    restored.set(x.path,x);
  }
  // Copy-on-write only for restored files. Fresh output files use ordinary MEMFS.
  py.runPython(`import builtins,io,os,shutil
_merit_open=builtins.open;_merit_io_open=io.open
def _merit_cow(fn):
 def op(file,mode='r',*a,**kw):
  if isinstance(file,(str,bytes,os.PathLike)) and any(c in mode for c in 'wax+') and os.path.islink(file):
   source=os.path.realpath(file)
   if source.startswith('${HOME}/browser_checkpoints/'):
    if 'x' in mode:raise FileExistsError(file)
    old=_merit_open(source,'rb').read() if 'a' in mode or '+' in mode and 'w' not in mode else None
    os.unlink(file)
    if old is not None:
     with _merit_open(file,'wb') as f:f.write(old)
  return fn(file,mode,*a,**kw)
 return op
builtins.open=_merit_cow(_merit_open);io.open=_merit_cow(_merit_io_open)`);
  write(run+'/browser_file_mounts.json',new TextEncoder().encode(JSON.stringify({role:'Read-only Blob files on this device; original paths retained for provenance',files:files.map((x,i)=>({mounted:mount+'/'+i,original:x.path}))})));
}
async function saveRun(py,run) {
  const puts=[],seen=new Set();
  function walk(dir){
    for(const name of py.FS.readdir(dir)){
      if(name==='.'||name==='..')continue;const path=dir+'/'+name;const st=py.FS.lstat(path);
      if(py.FS.isDir(st.mode)){walk(path);continue;}
      if(!py.FS.isFile(st.mode)&&!py.FS.isLink(st.mode))continue;
      seen.add(path);if(py.FS.isLink(st.mode)&&restored.has(path))continue;
      const old=restored.get(path),stamp=st.mtime.getTime();
      if(old&&old.size===st.size&&old.stamp===stamp&&!old.restored)continue;
      const blob=new Blob([py.FS.readFile(path)],{type:'application/octet-stream'});
      puts.push({path,blob,size:st.size,stamp});
    }
  }
  walk(run);const deletes=[...restored.keys()].filter(p=>!seen.has(p));
  await storeFiles(puts,deletes);
  for(const x of puts)restored.set(x.path,x);for(const p of deletes)restored.delete(p);
}
