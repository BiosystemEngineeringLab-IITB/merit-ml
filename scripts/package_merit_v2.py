"""Create a code-only upload bundle with an explicit allowlist, no Git history or run data."""
import hashlib
import tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'release-artifacts';out.mkdir(exist_ok=True)
archive=out/'merit-v2-docker-2026-11-05-r5-source.tar.gz'
allowed=['README.md','.gitignore','.gitattributes','LICENSE','SECURITY.md','CODE_OF_CONDUCT.md','.dockerignore','compose.merit-v2.yml',
         'docker/merit-v2','scripts/sync_merit_v2_runtime.py','scripts/package_merit_v2.py','scripts/test_merit_v2.py','scripts/test_merit_v2_edits.py',
         '.github/workflows/merit-v2-docker.yml','docs/merit-v2-validation.md','docs/legacy-v7-readme.md']
import sys
sys.path.insert(0,str(ROOT/'docker/merit-v2/runtime'))
from release_safety import assert_safe
with tarfile.open(archive,'w:gz') as tar:
 for name in allowed:
  root=ROOT/name
  paths=sorted(root.rglob('*')) if root.is_dir() else [root]
  for p in paths:
   rel=p.relative_to(ROOT)
   if not p.is_file() or any(x in ('runs','__pycache__','.git') for x in rel.parts) or p.suffix=='.pyc':continue
   if p.name in ('credentials.env','config.env','cloudfare') or p.is_symlink():raise ValueError('Forbidden asset: '+str(rel))
   assert_safe([p],root=Path('/home/shayantan/metabolomics/ML-ready'))
   tar.add(p,arcname='merit-v2-docker-2026-11-05-r5/'+str(rel),recursive=False)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix(archive.suffix+'.sha256').write_text(digest+'  '+archive.name+'\n')
print(archive,archive.stat().st_size,'bytes',digest)
