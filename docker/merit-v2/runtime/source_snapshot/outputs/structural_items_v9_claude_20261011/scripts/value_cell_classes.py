"""Classes of value-cell entries that are not numbers (rules written after enumerating all 12,986 distinct entries).

missing entry      : states "no value" and nothing else. NOT counted as text.
limit code         : a detection / quantification limit written as text (ND, N/F, BLOQ, <LOD, >200000 ...). Counted as text.
spreadsheet error  : #VALUE!, #DIV/0!, #N/A, #NUM! ... Counted as text.
malformed number   : digits that do not parse as one number (-2.121.589.661, 0,5, Infinity). Counted as text.
                     A malformed exponent such as 1.80.E-04 is NOT in this class: the manuscript reads it as a number, and so does the scan.
other text         : anything else (words, labels, codes such as N). Counted as text.
"""
import html
import re
MISSING = {'', 'na', 'n/a', 'n.a.', 'n.a', 'nan', 'null', 'none', '.', '-', '--', '---', '----', 'missing', 'missing value',
           'not applicable', 'not available', 'not measured', 'no sample', 'no data'}
LIMIT = {'nd', 'n.d.', 'n.d', 'n/d', 'n/f', 'nf', 'nq', 'n.q.', 'not found', 'not detected', 'not quantified', 'bql', 'bdl', 'bloq', 'blod',
         'lod', 'loq', 'llod', 'lloq', 'uloq', 'aloq', 'tr', 'trace'}
_LIMIT_WORD = re.compile(r'(?<![a-z])(lod|loq|llod|lloq|uloq|bloq|blod|bql|bdl)(?![a-z])|quantification|quantitation|detection limit|detected')
CLASSES = ('missing entry', 'limit code', 'spreadsheet error', 'malformed number', 'other text')
TEXT_CLASSES = CLASSES[1:]

def klass(token: str) -> str:
    c = html.unescape(token).strip().casefold()          # '&lt; LOD' is '< LOD', as in the manuscript's token scan
    if c in MISSING or re.fullmatch(r'-+', c):
        return 'missing entry'
    if c.startswith('#'):
        return 'spreadsheet error'
    if c[:1] in '<>≤≥' or c in LIMIT or _LIMIT_WORD.search(c):
        return 'limit code'
    if c in {'inf', '-inf', '+inf', 'infinity', '-infinity'} or (re.search(r'\d', c) and not re.search(r'[a-df-z]', c)):
        return 'malformed number'
    return 'other text'
