"""Writes the files the OVSSR dashboard reads from the OVSSRDBRates Hugging Face Space.

The dashboard reads Hugging Face files through a connector that returns at most 80,000 bytes per
read, so the 3,168 county rates are split into parts well under that size. dash/status.json lists
the parts with their row counts and carries the checker's health, so the dashboard can confirm it
received every row. Run after update.py; reads data/rows.json and data/status.json, writes dash/.
"""
import json, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA, OUT = os.path.join(HERE, 'data'), os.path.join(HERE, 'dash')
LIMIT = 60000  # bytes per part, leaving room under the connector's 80,000


def dumps(x):
    return json.dumps(x, ensure_ascii=False, separators=(',', ':'))


def parts(columns, rows, prefix):
    out, cur = [], []
    for r in rows:
        cur.append([r.get(c) for c in columns])
        if len(dumps({'columns': columns, 'rows': cur}).encode('utf-8')) > LIMIT:
            cur.pop()
            out.append(cur)
            cur = [[r.get(c) for c in columns]]
    if cur:
        out.append(cur)
    files = []
    for i, chunk in enumerate(out, 1):
        name = '%s_%02d.json' % (prefix, i)
        body = dumps({'part': i, 'of': len(out), 'columns': columns, 'rows': chunk})
        assert len(body.encode('utf-8')) < 78000, name
        with open(os.path.join(OUT, name), 'w', encoding='utf-8') as f:
            f.write(body)
        files.append({'file': name, 'rows': len(chunk)})
    return files


def main():
    os.makedirs(OUT, exist_ok=True)
    for n in os.listdir(OUT):
        if n.endswith('.json'):
            os.remove(os.path.join(OUT, n))
    d = json.load(open(os.path.join(DATA, 'rows.json'), encoding='utf-8'))
    st = json.load(open(os.path.join(DATA, 'status.json'), encoding='utf-8'))
    rows = []
    for r in d['rows']:
        r = dict(r)
        flag = r.pop('flag', '') or ''
        r.pop('flag_kind', None)
        r.pop('note_fixed', None)
        if flag:
            r['note'] = (r['note'] + ' ' if r.get('note') else '') + '⚠ ' + flag
        rows.append(r)
    ids = [r['id'] for r in rows]
    assert len(set(ids)) == len(ids), 'duplicate ids'
    columns = sorted({k for r in rows for k in r})
    files = parts(columns, rows, 'rates')
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    cut = (now - datetime.timedelta(days=60)).isoformat()
    t = st.get('selftest') or {}
    manifest = {
        'kind': 'ovssr-davis-bacon-laborer-rates',
        'exported': now.isoformat(),
        'rows': len(rows),
        'parts': files,
        'meta': d.get('meta', []),
        'check': {
            'last_check': st.get('last_check'), 'last_check_ok': bool(st.get('last_check_ok')),
            'last_error': st.get('last_error', ''), 'last_check_attempt': st.get('last_check_attempt', ''),
            'last_run_url': st.get('last_run_url', ''),
            'determinations_watched': st.get('determinations_watched'), 'determinations_reread': st.get('determinations_reread'),
            'selftest_when': t.get('when'), 'selftest_rows_tested': t.get('rows_tested'),
            'selftest_rows_matching': t.get('rows_matching'), 'selftest_mismatches': t.get('mismatch_count'),
            'flagged_count': st.get('flagged_count', 0), 'errors': len(st.get('errors') or []),
        },
        'flagged': (st.get('flagged') or [])[:60],
        'changes': [c for c in (st.get('changes') or []) if c.get('when', '') >= cut][-120:],
    }
    body = dumps(manifest)
    assert len(body.encode('utf-8')) < 78000, 'status.json too large'
    with open(os.path.join(OUT, 'status.json'), 'w', encoding='utf-8') as f:
        f.write(body)
    print('dash export:', len(rows), 'rows in', len(files), 'parts')


if __name__ == '__main__':
    main()
