"""Expose only authenticated GitHub webhooks, never Jenkins administration."""
import hashlib
import hmac
import http.server
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

SECRET = Path('/etc/medisur/secrets/github_webhook_secret').read_bytes().strip()
MAX_BODY = 2 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()))


class Handler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        pass

    def respond(self, status):
        self.send_response(status)
        self.send_header('Content-Length', '0')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

    def do_POST(self):
        if self.path != '/github-webhook/':
            return self.respond(404)
        try:
            size = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            return self.respond(400)
        if not 0 <= size <= MAX_BODY or self.headers.get('Transfer-Encoding'):
            return self.respond(413)
        try:
            body = self.rfile.read(size)
        except (TimeoutError, OSError):
            return self.respond(408)
        if len(body) != size:
            return self.respond(400)
        expected = 'sha256=' + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        signature = self.headers.get('X-Hub-Signature-256', '')
        if not re.fullmatch(r'sha256=[a-f0-9]{64}', signature) or not hmac.compare_digest(expected, signature):
            return self.respond(401)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return self.respond(400)
        if not isinstance(payload, dict) or not isinstance(payload.get('repository'), dict):
            return self.respond(400)
        if payload['repository'].get('full_name') != 'SergioMultimedios/medisur-cicd':
            return self.respond(403)
        event = self.headers.get('X-GitHub-Event')
        if event == 'ping':
            return self.respond(200)
        if event not in ('push', 'pull_request'):
            return self.respond(202)
        headers = {name: self.headers[name] for name in ('X-GitHub-Event', 'X-GitHub-Delivery', 'X-Hub-Signature-256') if self.headers.get(name)}
        headers['Content-Type'] = 'application/json'
        try:
            with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8080/github-webhook/', data=body, headers=headers), timeout=20) as response:
                status = response.status
        except (urllib.error.URLError, TimeoutError):
            status = 502
        self.respond(status)

    def do_GET(self):
        self.respond(404)


if __name__ == '__main__':
    http.server.ThreadingHTTPServer(('127.0.0.1', 8081), Handler).serve_forever()