"""Daily run, started by GitHub Actions (.github/workflows/update.yml).

1. Loads the current map rows (data/rows.json). On the very first run there is none yet, so it
   downloads the verified Nov. 16, 2026 baseline from the Hugging Face Space and checks that the
   file is byte-for-byte the verified one.
2. Asks SAM.gov which determinations changed and re-reads only those (updater.py, readers.py).
3. Writes data/rows.json and data/status.json. The map on Hugging Face reads both files from here.

Set SELFTEST=true to re-read every determination at the modification the map was built from and
report whether both readers reproduce the verified rates. A self-test changes no rates.
"""
import hashlib, json, os, sys, traceback, urllib.request
import updater

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
ROWS = os.path.join(DATA, 'rows.json')
STATUS = os.path.join(DATA, 'status.json')
BASELINE_URL = ('https://huggingface.co/spaces/DanielK1320/OVSSRDBRates/resolve/'
                '97ba1651ed87e55182aea84afe201f907b1b815c/data/dbra.json')
BASELINE_SHA256 = 'c165e894318c49f5140e668a5a9a58835f1fc71dc725b69294da835dc3d32e2f'


def bootstrap():
    req = urllib.request.Request(BASELINE_URL, headers={'User-Agent': updater.UA})
    raw = urllib.request.urlopen(req, timeout=120).read()
    got = hashlib.sha256(raw).hexdigest()
    if got != BASELINE_SHA256:
        raise SystemExit('The downloaded baseline is not the verified file (sha256 %s). Stopping.' % got)
    base = json.loads(raw.decode('utf-8'))
    rows = base['rows']
    for r in rows:
        r.setdefault('flag', '')
        r.setdefault('flag_kind', '')
    updater.log('baseline loaded:', len(rows), 'rows')
    return base.get('meta', []), rows


def write_rows(meta, rows):
    rows = sorted(rows, key=lambda r: (r['state'], r['county'], r['type']))
    with open(ROWS, 'w', encoding='utf-8') as f:
        f.write('{"meta":' + json.dumps(meta, ensure_ascii=False) + ',\n"rows":[\n')
        f.write(',\n'.join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows))
        f.write('\n]}\n')


def summary(status, selftest):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    lines = []
    if selftest:
        t = status.get('selftest') or {}
        lines += ['## Self-test', '',
                  '- Determinations re-read: %s' % t.get('determinations'),
                  '- Map rows tested: %s' % t.get('rows_tested'),
                  '- Rows where both readers reproduced the verified rate: **%s**' % t.get('rows_matching'),
                  '- Mismatched rate groups: %s' % t.get('mismatch_count'),
                  '- Fetch errors: %s' % len(t.get('errors') or []),
                  '- County-list issues: %s' % len(t.get('county_issues') or []),
                  '- Determinations modified on SAM.gov since the baseline: %s' % (', '.join(t.get('changed_since_baseline') or []) or 'none')]
        for m in (t.get('mismatches') or [])[:40]:
            lines.append('  - %s %s "%s": expected %s, got %s (%s)' % (m['wd'], m['rate_id'], m['label'], m['expected'], m['got'], m.get('why')))
    else:
        lines += ['## SAM.gov check', '',
                  '- Last completed check: %s (%s)' % (status.get('last_check') or 'none yet', 'ok' if status.get('last_check_ok') else 'some determinations could not be fetched'),
                  '- Determinations watched: %s; re-read: %s' % (status.get('determinations_watched'), status.get('determinations_reread')),
                  '- Rows needing a hand check: %s' % status.get('flagged_count'),
                  '- Last rate change: %s' % (status.get('last_change') or 'none since the baseline')]
        if status.get('last_error'):
            lines.append('- Error: %s' % status['last_error'])
    text = '\n'.join(lines) + '\n'
    print(text)
    if path:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(text)


def main():
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(ROWS):
        d = json.load(open(ROWS, encoding='utf-8'))
        meta, rows = d.get('meta', []), d['rows']
    else:
        meta, rows = bootstrap()
    status = json.load(open(STATUS, encoding='utf-8')) if os.path.exists(STATUS) else {'baseline': '2026-11-16'}
    # Rows that came with a hand-written note are never changed automatically.
    for r in rows:
        r['note_fixed'] = bool(r.get('note'))
    selftest = os.environ.get('SELFTEST', '').strip().lower() == 'true'
    run = os.environ.get('GITHUB_RUN_ID')
    if run:
        status['last_run_url'] = '%s/%s/actions/runs/%s' % (os.environ.get('GITHUB_SERVER_URL', 'https://github.com'), os.environ.get('GITHUB_REPOSITORY', ''), run)
    failed = False
    try:
        new_rows, status = updater.check(rows, status, selftest=selftest)
        if new_rows is not None:
            rows = new_rows
            status.pop('last_error', None)
            status.pop('last_check_attempt', None)
    except Exception as e:  # SAM.gov unreachable or changed: keep the last confirmed rates, record why
        traceback.print_exc()
        failed = True
        status = dict(status, last_check_attempt=updater.now(), last_error=str(e)[:500])
    for r in rows:
        r.pop('note_fixed', None)
    write_rows(meta, rows)
    with open(STATUS, 'w', encoding='utf-8') as f:
        json.dump(status, f, indent=1, ensure_ascii=False)
    summary(status, selftest)
    if failed:
        sys.exit(1)  # the run shows as failed, so GitHub emails the owner


if __name__ == '__main__':
    main()
