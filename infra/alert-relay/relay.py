"""Internal Alertmanager webhook receiver. No third-party Python dependencies."""

import hmac
import html
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MAX_BODY = 262144
MAX_ALERTS = 100
TOKEN_FILE = Path('/run/secrets/github-token')
API_ROOT = 'https://api.github.com'
LOG = logging.getLogger('alert-relay')


class UpstreamError(Exception):
    def __init__(self, uncertain=False):
        super().__init__('GitHub operation failed')
        self.uncertain = uncertain


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_text(value, limit=500):
    text = ''.join(char for char in value if not unicodedata.category(char).startswith('C'))
    text = text[:limit]
    text = re.sub(r'https?://\S+', '[URL omitida]', text, flags=re.I)
    text = re.sub(r'(?i)\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b', '[secreto omitido]', text)
    # Escape Markdown as well as HTML; neutralize mentions and bare host autolinks.
    text = re.sub(r'([\\`*_{}\[\]()#+!|~\-])', r'\\\1', text)
    return html.escape(text, quote=True).replace('@', '&#64;').replace('.', '&#46;')[:600]


def issue_content(alert, marker):
    labels = alert['labels']
    title = 'Prometheus: ' + safe_text(labels.get('alertname', 'alerta'), 160)
    lines = ['Alerta recibida desde Alertmanager.', '', '| Etiqueta | Valor |', '| --- | --- |']
    for key, value in sorted(labels.items()):
        if re.search(r'(?i)secret|token|password|authorization|credential|webhook|url', key):
            value = '[secreto omitido]'
        lines.append(f'| {safe_text(key, 100)} | {safe_text(value)} |')
    # Annotations and generatorURL/externalURL are deliberately not forwarded.
    lines.extend(['', 'Inicio: ' + alert['startsAt'], '', marker])
    return title[:240], '\n'.join(lines)


def validate_payload(payload):
    if not isinstance(payload, dict) or payload.get('version') != '4':
        raise ValueError('Expected Alertmanager webhook version 4')
    if payload.get('status') not in ('firing', 'resolved'):
        raise ValueError('Invalid group status')
    alerts = payload.get('alerts')
    if not isinstance(alerts, list) or len(alerts) > MAX_ALERTS:
        raise ValueError('Invalid alerts list')
    if payload.get('truncatedAlerts', 0) != 0:
        raise ValueError('Truncated notification')
    normalized = []
    for alert in alerts:
        if not isinstance(alert, dict):
            raise ValueError('Invalid alert')
        fingerprint = alert.get('fingerprint')
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[a-fA-F0-9]{1,64}', fingerprint):
            raise ValueError('Invalid fingerprint')
        if alert.get('status') not in ('firing', 'resolved'):
            raise ValueError('Invalid alert status')
        labels = alert.get('labels')
        if not isinstance(labels, dict) or len(labels) > 50 or any(
            not isinstance(key, str) or not isinstance(value, str)
            or len(key) > 256 or len(value) > 4096 for key, value in labels.items()
        ):
            raise ValueError('Invalid labels')
        started = alert.get('startsAt')
        if not isinstance(started, str) or len(started) > 64:
            raise ValueError('Invalid start time')
        instant = datetime.fromisoformat(started.replace('Z', '+00:00'))
        if instant.tzinfo is None or instant.year < 1970:
            raise ValueError('Start time must include timezone')
        normalized.append({
            'fingerprint': fingerprint.lower(), 'status': alert['status'], 'labels': labels,
            'startsAt': instant.astimezone(timezone.utc).isoformat(timespec='microseconds'),
        })
    return normalized


class GitHub:
    def __init__(self, repository, token_file=TOKEN_FILE, opener=None, sleep=time.sleep):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid GITHUB_REPOSITORY')
        if any(part in ('.', '..') for part in repository.split('/')):
            raise ValueError('Invalid GITHUB_REPOSITORY')
        self.repository = repository
        self.token_file = token_file
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.sleep = sleep

    def request(self, method, suffix='', payload=None):
        try:
            token = self.token_file.read_text(encoding='utf-8').strip()
            if not token or len(token) > 4096 or not token.isascii() or any(char.isspace() for char in token):
                raise ValueError('Invalid token')
        except (OSError, ValueError):
            raise UpstreamError() from None
        request = urllib.request.Request(
            API_ROOT + '/repos/' + self.repository + suffix,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28',
                     'User-Agent': 'medisur-alert-relay'}, method=method,
        )
        attempts = 1 if method == 'POST' else 3
        for attempt in range(attempts):
            try:
                with self.opener.open(request, timeout=10) as response:
                    raw = response.read(2097153)
                    if len(raw) > 2097152:
                        raise ValueError('Response too large')
                    return json.loads(raw)
            except urllib.error.HTTPError as error:
                retry = error.code in (403, 408, 429) or error.code >= 500
                uncertain = method == 'POST' and (error.code >= 500 or error.code == 408)
                error.close()
                if not retry or attempt + 1 == attempts:
                    raise UpstreamError(uncertain) from None
            except (OSError, ValueError, urllib.error.URLError):
                if attempt + 1 == attempts:
                    raise UpstreamError(method == 'POST') from None
            self.sleep(0.25 * (2 ** attempt))

    def verify_private(self):
        metadata = self.request('GET')
        if not isinstance(metadata, dict) or metadata.get('private') is not True:
            raise UpstreamError()

    def create(self, alert, marker):
        title, body = issue_content(alert, marker)
        response = self.request('POST', '/issues', {'title': title, 'body': body})
        number = response.get('number') if isinstance(response, dict) else None
        if type(number) is not int or number < 1:
            raise UpstreamError(uncertain=True)
        return number

    def close(self, number):
        response = self.request('PATCH', f'/issues/{int(number)}', {'state': 'closed', 'state_reason': 'completed'})
        if not isinstance(response, dict) or response.get('state') != 'closed':
            raise UpstreamError()

    def find(self, marker):
        # Never follow Link headers or webhook-provided URLs. Bound reconciliation work.
        for page in range(1, 11):
            issues = self.request('GET', f'/issues?state=all&per_page=100&page={page}')
            if not isinstance(issues, list):
                raise UpstreamError()
            for issue in issues:
                if not isinstance(issue, dict):
                    raise UpstreamError()
                if 'pull_request' not in issue and marker in (issue.get('body') or '').splitlines():
                    number = issue.get('number')
                    if type(number) is int and number > 0:
                        return number
                    raise UpstreamError()
            if len(issues) < 100:
                return None
        raise UpstreamError()


class Relay:
    def __init__(self, database, github):
        self.github = github
        self.lock = threading.Lock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS settings (repository TEXT NOT NULL)')
        existing = self.db.execute('SELECT repository FROM settings').fetchone()
        if existing and existing[0] != github.repository:
            self.db.close()
            raise ValueError('Database belongs to a different repository')
        if not existing:
            self.db.execute('INSERT INTO settings VALUES (?)', (github.repository,))
        self.db.execute('''CREATE TABLE IF NOT EXISTS alerts (
            fingerprint TEXT PRIMARY KEY, started TEXT NOT NULL, state TEXT NOT NULL,
            issue INTEGER, marker TEXT NOT NULL)''')
        self.db.commit()

    def save(self, fingerprint, started, state, issue, marker):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO alerts VALUES (?, ?, ?, ?, ?)',
                            (fingerprint, started, state, issue, marker))

    def process(self, alerts):
        if not self.lock.acquire(timeout=0.1):
            raise UpstreamError()
        try:
            # Fail closed if the configured destination is not a private repository.
            self.github.verify_private()
            for alert in alerts:
                self.process_one(alert)
        finally:
            self.lock.release()

    def process_one(self, alert):
        fingerprint, started = alert['fingerprint'], alert['startsAt']
        row = self.db.execute('SELECT * FROM alerts WHERE fingerprint=?', (fingerprint,)).fetchone()
        if row and started < row['started']:
            return
        if row and row['state'] == 'pending':
            number = self.github.find(row['marker'])
            if number is None:
                # A timed-out POST may have succeeded. Do not blindly create again.
                raise UpstreamError()
            self.save(fingerprint, row['started'], 'active', number, row['marker'])
            row = self.db.execute('SELECT * FROM alerts WHERE fingerprint=?', (fingerprint,)).fetchone()
        if alert['status'] == 'resolved':
            if row and row['state'] == 'active':
                self.github.close(row['issue'])
            self.save(fingerprint, started, 'resolved', row['issue'] if row else None,
                      row['marker'] if row else '')
            return
        if row and (row['state'] == 'active' or started == row['started']):
            if row['state'] == 'active' and started > row['started']:
                self.save(fingerprint, started, 'active', row['issue'], row['marker'])
            return
        marker = '<!-- medisur-alert-relay:' + uuid.uuid4().hex + ' -->'
        self.save(fingerprint, started, 'pending', None, marker)
        try:
            number = self.github.create(alert, marker)
        except UpstreamError as error:
            if not error.uncertain:
                with self.db:
                    if row:
                        self.save(fingerprint, row['started'], row['state'], row['issue'], row['marker'])
                    else:
                        self.db.execute('DELETE FROM alerts WHERE fingerprint=?', (fingerprint,))
            raise
        self.save(fingerprint, started, 'active', number, marker)


class Handler(BaseHTTPRequestHandler):
    server_version = 'AlertRelay'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        pass  # Never log paths, headers, payloads, tokens, or upstream exceptions.

    def reply(self, status):
        body = json.dumps({'status': status}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        if status == 503:
            self.send_header('Retry-After', '15')
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self):
        self.reply(200 if self.path == '/healthz' else 404)

    def do_POST(self):
        if self.path != '/alerts':
            self.reply(404)
            return
        secret = self.server.secret
        provided = self.headers.get_all('Authorization', [])
        if secret and (len(provided) != 1 or not hmac.compare_digest(
            provided[0].encode('utf-8'), b'Bearer ' + secret.encode('utf-8')
        )):
            self.reply(401)
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding'):
            self.reply(400)
            return
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,10}', lengths[0]):
            self.reply(411)
            return
        length = int(lengths[0])
        if length > MAX_BODY:
            self.reply(413)
            return
        if self.headers.get_content_type() != 'application/json':
            self.reply(415)
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('Incomplete body')
            alerts = validate_payload(json.loads(raw))
        except (ValueError, RecursionError, OverflowError):
            self.reply(400)
            return
        except (socket.timeout, OSError):
            self.reply(408)
            return
        try:
            self.server.relay.process(alerts)
        except Exception:
            LOG.warning('Notification deferred; retry required')
            self.reply(503)
            return
        self.reply(200)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, relay, secret=''):
        self.relay = relay
        self.secret = secret
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def shutdown_request(self, request):
        # Early rejection must not reset a TCP connection with an unread body.
        # Half-close first, then drain only a bounded amount for a bounded time.
        try:
            request.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + 0.1
            remaining = MAX_BODY + 65536
            while remaining > 0:
                wait = deadline - time.monotonic()
                if wait <= 0:
                    break
                request.settimeout(wait)
                chunk = request.recv(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        super().shutdown_request(request)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 15\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        LOG.warning('Request failed')


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        secret = os.environ.get('ALERT_RELAY_SECRET', '')
        secret_file = os.environ.get('ALERT_RELAY_SECRET_FILE')
        if not secret and secret_file:
            secret = Path(secret_file).read_text(encoding='utf-8').strip()
            if not secret:
                raise ValueError('Empty secret file')
        if secret and (not secret.isascii() or any(char.isspace() for char in secret)):
            raise ValueError('Secret must be an ASCII bearer token')
        github = GitHub(os.environ.get('GITHUB_REPOSITORY', 'SergioMultimedios/medisur-cicd'))
        relay = Relay('/data/alerts.sqlite3', github)
        server = Server(('0.0.0.0', 8080), relay, secret)
    except Exception:
        LOG.error('Startup failed; check configuration and data/secret permissions')
        return 1
    LOG.info('Internal alert relay listening on port 8080')
    if not secret:
        LOG.warning('Bearer authentication disabled; trusted network required')
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())