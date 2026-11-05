import re, glob, os, csv
ROOT="/home/shayantan/metabolomics/ML-ready/online-tool/source_snapshot/mw-dump-latest-18thSep-2026-version"
MULTIDOT=re.compile(r"^-?\d{1,3}(?:\.\d{3}){2,}$")
LEAD0=re.compile(r"^-?0\d+$")
COMMA=re.compile(r"^-?\d+,\d+$")
START=re.compile(r"^([A-Z_]+_DATA)_START")
out=[]
for f in glob.glob(f"{ROOT}/ST*/AN*/mwtab/*_mwtab.txt"):
    st,an=f.split("/")[-4:-2]
    blk=None; n=a=b=c=0
    for line in open(f,encoding="utf-8",errors="replace").read().split("\n"):
        m=START.match(line)
        if m:
            blk=None if ("METABOLITES" in m.group(1) or "EXTENDED" in m.group(1)) else m.group(1); continue
        if line.endswith("_DATA_END") or "_DATA_END" in line[:40]:
            blk=None; continue
        if blk is None or line.startswith(("Samples","Factors")): continue
        for t in line.split("\t")[1:]:
            t=t.strip()
            if not t: continue
            n+=1
            if MULTIDOT.match(t): a+=1
            elif LEAD0.match(t): b+=1
            elif COMMA.match(t): c+=1
    if a or b or c:
        out.append([st,an,n,a,b,c,round(100*a/n,2) if n else 0,round(100*b/n,2) if n else 0,round(100*c/n,2) if n else 0])
out.sort(key=lambda r:-(r[3]+r[4]+r[5])/max(r[2],1))
with open("/tmp/claude-1004/-home-shayantan-metabolomics/fa6f75df-9cb2-4557-b4f2-aa6e5e67c504/scratchpad/numfmt.tsv","w") as fh:
    w=csv.writer(fh,delimiter="\t"); w.writerow(["study","analysis","cells","multidot","leading0_int","comma_dec","pct_multidot","pct_lead0","pct_comma"]); w.writerows(out)
print(len(out),"matrices with any hit")
for r in out[:40]: print(r)
