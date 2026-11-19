"""Run inside relay via docker exec. Talks only to internal Alertmanager HTTP."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid


def request(path, payload=None):
    target = 'http://alertmanager:9093' + path
    data = json.dumps(payload).encode() if payload is not None else None
    message = urllib.request.Request(target, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(message, timeout=5) as response:
        body = response.read()
        return response.status, json.loads(body) if body and path.startswith('/api/') else None


def main():
    mode = sys.argv[1]
    assert mode in ('fire', 'resolve', 'inspect')
    state = Path('/data/synthetic-test.json')
    now = datetime.now(timezone.utc)
    if not state.exists():
        assert mode == 'fire', 'No synthetic test to resolve'
        alert = {
            'labels': {'alertname': 'TESTSyntheticAlertDelivery', 'severity': 'test',
                       'evidence': 'TEST', 'incident': 'false', 'test_id': uuid.uuid4().hex},
            'annotations': {'summary': 'Authorized synthetic delivery test; NOT an actual incident'},
            'startsAt': now.isoformat(), 'endsAt': (now + timedelta(minutes=10)).isoformat(),
        }
        state.write_text(json.dumps(alert), encoding='utf-8')
    alert = json.loads(state.read_text(encoding='utf-8'))
    print('Alertmanager readiness HTTP', request('/-/ready')[0])
    with urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=5) as response:
        print('Relay health HTTP', response.status)
    try:
        urllib.request.urlopen(urllib.request.Request(
            'http://127.0.0.1:8080/alerts', data=b'{}',
            headers={'Content-Type': 'application/json'}), timeout=5)
        raise AssertionError('Unauthenticated webhook accepted')
    except urllib.error.HTTPError as error:
        assert error.code == 401
        print('Unauthenticated webhook rejected HTTP 401')
    if mode != 'inspect':
        if mode == 'resolve':
            alert['endsAt'] = now.isoformat()
            state.write_text(json.dumps(alert), encoding='utf-8')
        assert datetime.fromisoformat(alert['endsAt']) > now or mode == 'resolve', 'Do not re-fire completed test'
        print('Synthetic', mode, 'HTTP', request('/api/v2/alerts', [alert])[0])
    expected = 'resolved' if mode == 'resolve' else 'active'
    for _ in range(60):
        with sqlite3.connect('file:/data/alerts.sqlite3?mode=ro', uri=True) as database:
            rows = database.execute('SELECT state, issue FROM alerts').fetchall()
        if mode == 'inspect' or (len(rows) == 1 and rows[0][0] == expected):
            print(json.dumps({'evidence': 'TEST', 'actual_incident': False, 'rows': rows}))
            if rows:
                print('https://github.com/SergioMultimedios/medisur-cicd/issues/' + str(rows[0][1]))
            return
        time.sleep(2)
    raise SystemExit('Delivery not confirmed within 120 seconds; do not create a second test')


if __name__ == '__main__':
    main()