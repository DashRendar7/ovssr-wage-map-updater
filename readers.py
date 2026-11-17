"""Two independent readers for Davis-Bacon wage determination text, plus the rules for picking
the Laborer rate a map row was built from.

Reader A walks the rate block line by line. Reader B runs one regular expression over the whole
block. They share nothing but the final selection rule, so a change is published only when two
different ways of reading the same text give the same numbers."""
import re

CODE_RE = re.compile(r'^\s*((?:[A-Z]{4}\d{4}-\d{3})|(?:SU[A-Z]{2}\d{4}-\d{3})|(?:UAVG-[A-Z]{2}-\d{4}))\s+(\d{2}/\d{2}/\d{4})\s*$')
SEP_RE = re.compile(r'^\s*-{8,}\s*$')
HEAD_RE = re.compile(r'^\s*Rates\s+Fringes\s*$', re.I)
GROUP1_RE = re.compile(r'^LABORERS?\s*:?\s*GROUP\s*(?:1|I|ONE)\b')
GROUP1_SEARCH = re.compile(r'\bLABORERS?\s*:?\s*GROUP\s*(?:1|I|ONE)\b')


def norm(s):
    s = (s or '').upper().replace('…', '')
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'\s*\.+\s*$', '', s)
    s = re.sub(r'\(\s+', '(', s)
    s = re.sub(r'\s+\)', ')', s)
    return s.strip()


def mdy(s):
    return f'{s[6:10]}-{s[0:2]}-{s[3:5]}'


def header(text):
    """Decision number and publication date of the current modification, from the first line."""
    m = re.search(r'General Decision Number:\s*([A-Z]{2}\d{8})\s+(\d{2}/\d{2}/\d{4})', text or '')
    return (m.group(1), mdy(m.group(2))) if m else (None, None)


def blocks(text):
    """All rate blocks: {code: (date, [lines])}. A block runs from its code line to the next rule
    line or the next code line."""
    out = {}
    lines = (text or '').split('\n')
    i = 0
    while i < len(lines):
        m = CODE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not SEP_RE.match(lines[j]) and not CODE_RE.match(lines[j]):
            j += 1
        code = m.group(1)
        if code not in out:
            out[code] = (mdy(m.group(2)), lines[i + 1:j])
        i = j
    return out


def entries_a(lines):
    """Reader A: line state machine. A class name may wrap over several lines; the entry ends on
    the line with the dot leader and the dollar sign."""
    out, buf = [], []
    for raw in lines:
        s = raw.rstrip()
        if not s.strip() or HEAD_RE.match(s):
            buf = []
            continue
        m = re.search(r'(?:\.{2,}|(?<=\))\.)\s*\$\s*(\d[\d,]*\.\d{2})\s*(?:\*\*)?(.*)$', s)  # dot leader, or one dot after ")"
        if m:
            name = ' '.join([b.strip() for b in buf] + [s[:m.start()].strip()])
            fr = m.group(2).strip()
            out.append((norm(name), float(m.group(1).replace(',', '')), fr))
            buf = []
        else:
            buf.append(s)
    return out


ENTRY_B = re.compile(r'(?P<name>(?:(?!(?:\.{2,}|(?<=\))\.)[ \t]*\$).)*?)(?:\.{2,}|(?<=\))\.)[ \t]*\$[ \t]*(?P<base>\d[\d,]*\.\d\d)[ \t]*(?:\*\*)?[ \t]*(?P<fr>[^\n]*)', re.S)


def entries_b(lines):
    """Reader B: one regular expression over the joined block. Header and blank lines are removed
    first; everything between one rate line and the next dot leader is the next class name."""
    body = '\n'.join(l for l in lines if l.strip() and not HEAD_RE.match(l))
    out = []
    for m in ENTRY_B.finditer(body):
        name = re.sub(r'\s+', ' ', m.group('name')).strip()
        out.append((norm(name), float(m.group('base').replace(',', '')), m.group('fr').strip()))
    return out


def _area(name):
    m = re.search(r'\(([^()]*)\)\s*$', name)
    return norm(m.group(1)) if m else ''


def _lead_area(rest):
    """The parenthesised text right after a class name: " (BERKELEY, DORCHESTER)..." -> "BERKELEY, DORCHESTER"."""
    m = re.match(r'\s*\(([^()]*)\)', rest)
    return m.group(1) if m else ''


def place_key(name):
    s = (name or '').upper().replace('SAINT ', 'ST ').replace('ST.', 'ST ')
    s = re.sub(r'\b(COUNTIES|COUNTY|PARISHES|PARISH)\b', '', s)
    return re.sub(r'[^A-Z]', '', s)


def area_has(area, county):
    """True if a parenthesised area such as "CLARENDON, DILLON, LEE" or "ORLEANS, JEFFERSON AND
    ST. BERNARD PARISHES" names the county."""
    parts = re.split(r',|\bAND\b|&', area or '')
    return place_key(county) in {place_key(p) for p in parts if p.strip()}


def select(entries, label, county=None):
    """Pick the entry a stored map label refers to. Returns (entry, reason) or (None, reason).
    The stored label can be the first part of a longer class name ("LABORER: COMMON OR GENERAL" for
    "LABORER: COMMON OR GENERAL; MASON TENDER - ..."), or leave out an area list that the
    determination adds per group of counties; then the entry whose area names the county is used."""
    trunc = '…' in (label or '')
    L = norm((label or '').split('…')[0]) if trunc else norm(label)
    if L.startswith('LABORER GROUP 1'):
        cands = []
        for e in entries:
            g = GROUP1_SEARCH.search(e[0])
            if g:
                cands.append((e[0][g.start():],) + tuple(e[1:]))
        m = re.match(r'LABORER GROUP 1\s*\((.*?)\)?$', L)
        area = norm(m.group(1)) if m else ''
        if area:
            hits = [e for e in cands if (_area(e[0]) == area) or (trunc and _area(e[0]).startswith(area))]
        else:
            hits = cands
    else:
        hits = [e for e in entries if e[0] == L or (trunc and e[0].startswith(L))]
        if not hits:
            semi = [e for e in entries if e[0].startswith(L + ';')]
            paren = [e for e in entries if e[0].startswith(L + ' (')]
            if paren and county:  # "LABORER: COMMON OR GENERAL (BERKELEY)": keep the entry naming the county
                paren = [e for e in paren if area_has(_lead_area(e[0][len(L):]), county)]
                if not paren and not semi:
                    return None, 'no area entry names %s' % county
            hits = semi + paren
        if not hits:
            hits = [e for e in entries if e[0].endswith(' ' + L) or (trunc and (' ' + L) in e[0])]
    if not hits:
        return None, 'class not found'
    vals = {(h[1], h[2]) for h in hits}
    if len(vals) > 1:
        return None, 'class matches %d entries with different rates' % len(hits)
    return hits[0], 'ok'


def fringe_value(fr):
    s = (fr or '').replace('**', ' ').strip()
    if s == '':
        return 0.0
    m = re.match(r'(\d[\d,]*\.\d{2})(?:\s|$)', s)
    return float(m.group(1).replace(',', '')) if m else None


def read_rate(text, code, label, reader, county=None):
    """One reader's answer for one stored row: dict with base, fringe, code, date, or an error."""
    bl = blocks(text)
    get = entries_a if reader == 'A' else entries_b
    tried = []
    if code in bl:
        tried = [code]
    else:
        # Union identifiers sometimes move to the next number; look within the same local.
        prefix = code[:8]
        tried = sorted(c for c in bl if c[:8] == prefix)
    found = []
    for c in tried:
        date, lines = bl[c]
        e, why = select(get(lines), label, county)
        if e:
            found.append((c, date, e))
    if not found:
        return {'ok': False, 'why': 'rate block %s not found' % code if not tried else 'class not found in %s' % ', '.join(tried)}
    if len(found) > 1:
        return {'ok': False, 'why': 'class found in several blocks: ' + ', '.join(f[0] for f in found)}
    c, date, e = found[0]
    fv = fringe_value(e[2])
    if fv is None:
        return {'ok': False, 'why': 'fringe is not a plain amount (%s)' % e[2][:30]}
    return {'ok': True, 'code': c, 'date': date, 'base': round(e[1], 2), 'fringe': round(fv, 2)}


def read_both(text, code, label, county=None):
    a = read_rate(text, code, label, 'A', county)
    b = read_rate(text, code, label, 'B', county)
    if a['ok'] and b['ok'] and all(a[k] == b[k] for k in ('code', 'date', 'base', 'fringe')):
        return {'ok': True, **a}
    return {'ok': False, 'why': 'readers disagree' if (a['ok'] and b['ok']) else (a.get('why') or b.get('why')), 'A': a, 'B': b}
