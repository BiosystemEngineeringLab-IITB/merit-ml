"""Pure conditional RT-unit rules from the manuscript analysis; RI excluded."""
import re, math
from merit.manuscript_coordinate_fields import _classify_mass_rt_like_field

NUM=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'

UNIT=re.compile(r'\b(milliseconds?|msecs?|seconds?|secs?|minutes?|mins?|hours?|hrs?)\b|[\[(]\s*(min|s|ms|h)\s*[\])]|(?:\s|_)\b(s|ms|h)\s*$',re.I)

TAIL=re.compile(r'\s*(milliseconds?|msecs?|ms|seconds?|secs?|s|minutes?|mins?|hours?|hrs?|h)\s*$',re.I)

def normalize(s):return re.sub(r'[^a-z0-9]+',' ',s.lower()).strip()

def fieldkind(h):
 if _classify_mass_rt_like_field(h)!='rt-like':return 'other'
 spaced=normalize(h);compact=spaced.replace(' ','')
 if re.search(r'error|toleran|window|delta|similarity|match|range|Δ|δ',h,re.I) or re.search(r'\b(err|std|sd|stdev)\b',spaced):return 'diagnostic'
 isri=bool(re.search(r'\bri\b',spaced) or any(k in compact for k in ['retentionindex','retindex','rindex']))
 isrt=bool(re.search(r'\brt\b',spaced) or compact in ['rt','medrt','basert','rtimes'] or any(k in compact for k in ['retentiontime','rettime']))
 isrt = isrt or bool(re.search(r'\br\.?\s*t\.?(?=\b|\W)',h,re.I))
 if (isri and isrt) or (isrt and re.search(r'\bmt\b|feature|transition',spaced)) or 'retentiontimeindex' in compact:return 'ambiguous'
 if isri:return 'RI'
 if isrt:return 'RT'
 return 'ambiguous'

def canon(s):
 z=s.strip().lower().strip('(). ')
 if re.fullmatch(r'min(?:ute)?s?',z):return 'Minutes'
 if re.fullmatch(r'sec(?:ond)?s?|s',z):return 'Seconds'
 if re.fullmatch(r'milliseconds?|ms|msecs?',z):return 'Milliseconds'
 if re.fullmatch(r'h(?:our)?s?|hrs?',z):return 'Hours'
 return ''

def unitsfrom(s):
 uu=set()
 for m in UNIT.finditer(s.replace('_',' ')):
  for g in m.groups():
   if g and canon(g):uu.add(canon(g))
 return uu

def numeric(v):
 z=TAIL.sub('',str(v).strip());z=re.sub(r'\.E([+-])',r'E\1',z,flags=re.I)
 if not re.fullmatch(NUM,z):return False
 try:return math.isfinite(float(z)) and float(z)>=0
 except:return False
