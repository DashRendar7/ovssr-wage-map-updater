"""Checks SAM.gov for changed Davis-Bacon general wage determinations and updates the map data.

Rules:
- A determination is re-read only when SAM.gov shows a new modification (or a new year's number).
- Every affected rate is read by two independent readers (readers.py). The map changes only when
  both readers agree. Otherwise the county keeps its last confirmed rate and is marked for a
  hand check.
- The determination's own county list is compared with the map's counties; a county that is no
  longer listed is marked for a hand check rather than moved anywhere.
- Rows that already carried a hand-written note are never changed automatically; they are marked.
"""
import json, os, re, time, datetime, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import readers

BASE = 'https://sam.gov'
UA = 'OVSSR-DB-wage-map/1.0 (daily check for huggingface.co/spaces/DanielK1320/OVSSRDBRates; public Davis-Bacon data)'
STATES = ['OH', 'KY', 'TN', 'AL', 'GA', 'FL', 'MS', 'SC', 'LA', 'AR']


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def log(*a):
    print('[updater]', *a, flush=True)


def get_json(url, tries=4):
    err = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/hal+json, application/json', 'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = 'HTTP %s' % e.code
        except Exception as e:  # network trouble: retry
            err = str(e)
        time.sleep(3 * (i + 1))
    raise RuntimeError('%s failed: %s' % (url, err))


def active_wds(state):
    """Active determinations for one state: {ref: {rev, types, counties}}."""
    out, page = {}, 0
    while True:
        j = get_json(f'{BASE}/api/prod/sgs/v1/search/?index=dbra&page={page}&size=100&mode=search&is_active=true&sort=title&state={state}') or {}
        for r in (j.get('_embedded') or {}).get('results') or []:
            ref = r.get('fullReferenceNumber')
            if not ref:
                continue
            st = ((r.get('location') or {}).get('state') or {})
            out[ref] = {'rev': int(r.get('revisionNumber') or 0), 'types': r.get('constructionTypes') or [],
                        'counties': [c.get('value') for c in st.get('counties') or [] if c.get('value')]}
        pages = int(((j.get('page') or {}).get('totalPages')) or 0)
        page += 1
        if page >= pages:
            return out
        time.sleep(0.4)


def fetch_doc(ref, rev):
    j = get_json(f'{BASE}/api/prod/wdol/v1/wd/{ref}/{rev}')
    return (j or {}).get('document') or ''


def ckey(name):
    s = (name or '').upper().replace('SAINT ', 'ST ').replace('ST. ', 'ST ')
    s = re.sub(r'\b(COUNTY|PARISH)\b', '', s)
    return re.sub(r'[^A-Z]', '', s)


def kind(code):
    if code.startswith('SU'):
        return 'Survey'
    if code.startswith('UAVG'):
        return 'Union average'
    return 'Union'


def current_ref(wd, active):
    """The active determination that continues "wd": the same number, or the same number in a
    later year (DOL renumbers every January, keeping the last four digits)."""
    st = wd[:2]
    act = active.get(st) or {}
    if wd in act:
        return wd
    later = sorted(r for r in act if r[:2] == st and r[6:] == wd[6:] and r[2:6] > wd[2:6])
    return later[-1] if later else None


def check(rows, prev_status, selftest=False, workers=4):
    """Returns (new_rows, status). With selftest=True nothing is changed: every determination is
    re-read at the modification the map was built from and compared with the map."""
    t0 = time.time()
    status = dict(prev_status or {})
    log('start', 'selftest' if selftest else 'check')
    active = {}
    for st in STATES:
        active[st] = active_wds(st)
        log('active', st, len(active[st]))
    by_wd = {}
    for i, r in enumerate(rows):
        by_wd.setdefault(r['wd'], []).append(i)
    rows = [dict(r) for r in rows]
    if not selftest:
        for r in rows:  # coverage flags are worked out afresh on every run
            if r.get('flag_kind') == 'coverage':
                r['flag'], r['flag_kind'] = '', ''
    todo, gone, county_issues = [], [], []
    for wd, idx in by_wd.items():
        ref = current_ref(wd, active)
        if ref is None:
            gone.append(wd)
            continue
        info = active[wd[:2]][ref]
        # County coverage: every county the map assigns to this determination must still be listed.
        listed = {ckey(c) for c in info['counties']}
        missing = sorted({rows[i]['county'] for i in idx if ckey(rows[i]['county']) not in listed})
        if missing:
            county_issues.append({'wd': wd, 'current': ref, 'missing': missing})
        base_rev = int(rows[idx[0]]['wd_mod'])
        if selftest:
            todo.append((wd, wd, base_rev, idx))
        elif ref != wd or info['rev'] != base_rev:
            todo.append((wd, ref, info['rev'], idx))

    def work(item):
        wd, ref, rev, idx = item
        try:
            text = fetch_doc(ref, rev)
        except Exception as e:
            return item, None, str(e)
        return item, text, None

    results, mismatches, changes, flagged, errors = [], [], [], [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for item, text, err in ex.map(work, todo):
            wd, ref, rev, idx = item
            if err or not text:
                errors.append({'wd': ref, 'rev': rev, 'error': err or 'empty document'})
                continue
            hdr_ref, pub = readers.header(text)
            groups = {}
            for i in idx:
                groups.setdefault((rows[i]['rate_id'], rows[i]['classification']), []).append(i)
            for (code, label), gi in groups.items():
                res = readers.read_both(text, code, label)
                r0 = rows[gi[0]]
                if selftest:
                    exp = (r0['rate_id'], r0['rate_effective'], round(r0['base'], 2), round(r0['fringe'] or 0, 2))
                    got = (res.get('code'), res.get('date'), res.get('base'), res.get('fringe')) if res['ok'] else None
                    if got != exp:
                        mismatches.append({'wd': wd, 'rev': rev, 'rate_id': code, 'label': label, 'rows': len(gi),
                                           'counties': sorted({rows[i]['county'] for i in gi})[:6], 'expected': exp, 'got': got,
                                           'why': None if res['ok'] else res.get('why'),
                                           'A': res.get('A'), 'B': res.get('B')})
                    continue
                for i in gi:
                    r = rows[i]
                    old = (r['base'], r['fringe'], r['rate_effective'], r['rate_id'])
                    if res['ok'] and not r.get('note_fixed'):
                        r.update({'wd': ref, 'wd_mod': rev, 'wd_published': pub or r['wd_published'], 'base': res['base'],
                                  'fringe': res['fringe'], 'total': round(res['base'] + res['fringe'], 2), 'rate_id': res['code'],
                                  'rate_effective': res['date'], 'rate_source': kind(res['code']), 'flag': '', 'flag_kind': ''})
                        if (r['base'], r['fringe'], r['rate_effective'], r['rate_id']) != old:
                            changes.append({'when': now(), 'state': r['state'], 'county': r['county'], 'type': r['type'], 'wd': ref, 'mod': rev,
                                            'old': {'base': old[0], 'fringe': old[1], 'rate_effective': old[2], 'rate_id': old[3]},
                                            'new': {'base': r['base'], 'fringe': r['fringe'], 'rate_effective': r['rate_effective'], 'rate_id': r['rate_id']}})
                    else:
                        why = 'this county carries a hand-written note' if r.get('note_fixed') else res.get('why')
                        r['flag'] = 'DOL published %s modification %s%s. The automatic re-read could not confirm this rate (%s), so the rate shown is the last confirmed one and may be out of date. Check the determination.' % (
                            ref, rev, (' on ' + pub) if pub else '', why)
                        r['flag_kind'] = 'reread'
                        flagged.append({'state': r['state'], 'county': r['county'], 'type': r['type'], 'wd': ref, 'mod': rev, 'why': why})
    if not selftest:
        for wd in gone:
            for i in by_wd[wd]:
                rows[i]['flag'] = 'SAM.gov no longer lists determination %s as active and no later version was found. Check SAM.gov for the determination that now covers this county.' % wd
                rows[i]['flag_kind'] = 'coverage'
                flagged.append({'state': rows[i]['state'], 'county': rows[i]['county'], 'type': rows[i]['type'], 'wd': wd, 'why': 'determination no longer active'})
        missing_keys = {(ci['wd'], c) for ci in county_issues for c in ci['missing']}
        for wd, idx in by_wd.items():
            for i in idx:
                if (wd, rows[i]['county']) in missing_keys:
                    rows[i]['flag'] = 'The current version of %s no longer lists this county. Check SAM.gov for the determination that now covers it.' % wd
                    rows[i]['flag_kind'] = 'coverage'
                    flagged.append({'state': rows[i]['state'], 'county': rows[i]['county'], 'type': rows[i]['type'], 'wd': wd, 'why': 'county no longer listed'})
    secs = round(time.time() - t0)
    if selftest:
        tested = sum(len(x[3]) for x in todo)
        bad_rows = sum(m['rows'] for m in mismatches)
        drift = sorted({x[0] for x in todo if (current_ref(x[0], active) or '') != x[0] or active[x[0][:2]].get(x[0], {}).get('rev') != x[2]})
        status['selftest'] = {'when': now(), 'seconds': secs, 'determinations': len(todo), 'rows_tested': tested,
                              'rows_matching': tested - bad_rows - sum(len(by_wd[e['wd']]) for e in errors if e['wd'] in by_wd),
                              'mismatches': mismatches[:300], 'mismatch_count': len(mismatches), 'errors': errors[:50],
                              'county_issues': county_issues[:100], 'gone': gone, 'changed_since_baseline': drift}
        log('selftest done', secs, 's;', len(mismatches), 'mismatches;', len(errors), 'errors;', len(county_issues), 'county issues')
        return None, status
    all_changes = (status.get('changes') or []) + changes
    status.update({'last_check': now(), 'last_check_seconds': secs, 'last_check_ok': not errors,
                   'determinations_watched': len(by_wd), 'determinations_reread': len(todo), 'errors': errors[:50],
                   'changes': all_changes[-500:], 'flagged': [f for f in flagged][:1000], 'flagged_count': len(flagged),
                   'county_issues': county_issues[:100]})
    if changes:
        status['last_change'] = now()
    log('check done', secs, 's;', len(todo), 're-read;', len(changes), 'changed rows;', len(flagged), 'flagged;', len(errors), 'errors')
    return rows, status
