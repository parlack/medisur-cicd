"""Local HTTP tests; production secrets and upstream services are never used."""
import hashlib
import hmac
import http.client
import http.server
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest import mock
import urllib.error


SERVER_PATH = Path(__file__).resolve().with_name('server.py')
SPEC = importlib.util.spec_from_file_location('webhook_server_under_test', SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
with mock.patch.object(Path, 'read_bytes', return_value=b'import-only-test-secret\n'):
    SPEC.loader.exec_module(server)

TEST_SECRET = b'unit-test-secret-not-a-production-credential'
REPOSITORY = 'SergioMultimedios/medisur-cicd'


class WebhookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        secret_patch = mock.patch.object(server, 'SECRET', TEST_SECRET)
        secret_patch.start()
        self.addCleanup(secret_patch.stop)
        forward_patch = mock.patch.object(
            server.urllib.request, 'urlopen',
            side_effect=AssertionError('Unexpected upstream request'),
        )
        self.forward = forward_patch.start()
        self.addCleanup(forward_patch.stop)

    def request(self, body=b'', *, method='POST', event='ping', headers=None):
        signature = 'sha256=' + hmac.new(TEST_SECRET, body, hashlib.sha256).hexdigest()
        request_headers = {
            'Content-Length': str(len(body)),
            'Content-Type': 'application/json',
            'X-Hub-Signature-256': signature,
            'X-GitHub-Event': event,
        }
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection(
            '127.0.0.1', self.httpd.server_port, timeout=3
        )
        try:
            connection.request(method, '/github-webhook/', body, request_headers)
            response = connection.getresponse()
            self.assertEqual(response.read(), b'')
            self.assertEqual(response.getheader('Content-Length'), '0')
            self.assertEqual(response.getheader('Connection'), 'close')
            return response.status
        finally:
            connection.close()

    @staticmethod
    def payload(repository=REPOSITORY):
        return json.dumps({'repository': {'full_name': repository}}).encode('utf-8')

    def test_valid_ping_returns_200(self):
        self.assertEqual(self.request(self.payload()), 200)
        self.forward.assert_not_called()

    def test_bad_hmac_returns_401(self):
        self.assertEqual(self.request(
            self.payload(), headers={'X-Hub-Signature-256': 'sha256=' + '0' * 64}
        ), 401)
        self.forward.assert_not_called()

    def test_wrong_repository_returns_403(self):
        self.assertEqual(self.request(self.payload('another/repository')), 403)
        self.forward.assert_not_called()

    def test_oversized_body_returns_413_before_reading(self):
        self.assertEqual(self.request(
            headers={'Content-Length': str(server.MAX_BODY + 1)}
        ), 413)
        self.forward.assert_not_called()

    def test_signed_invalid_json_returns_400(self):
        self.assertEqual(self.request(b'{invalid-json'), 400)
        self.forward.assert_not_called()

    def test_get_returns_404(self):
        self.assertEqual(self.request(method='GET'), 404)
        self.forward.assert_not_called()

    def test_signed_push_forwards_exact_body_to_fixed_loopback_url(self):
        body = self.payload()
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        self.forward.side_effect = None
        self.forward.return_value = response
        self.assertEqual(self.request(body, event='push', headers={
            'X-GitHub-Delivery': 'test-delivery',
            'Host': 'untrusted.invalid',
            'Authorization': 'must-not-be-forwarded',
            'X-Forwarded-Host': 'untrusted.invalid',
            'X-Arbitrary': 'must-not-be-forwarded',
        }), 200)
        self.forward.assert_called_once()
        args, kwargs = self.forward.call_args
        self.assertEqual(len(args), 1)
        forwarded = args[0]
        self.assertEqual(forwarded.full_url, 'http://127.0.0.1:8080/github-webhook/')
        self.assertEqual(forwarded.get_method(), 'POST')
        self.assertEqual(forwarded.data, body)
        self.assertEqual(kwargs, {'timeout': 20})
        self.assertEqual(
            {name.lower(): value for name, value in forwarded.header_items()},
            {
                'content-type': 'application/json',
                'x-github-event': 'push',
                'x-github-delivery': 'test-delivery',
                'x-hub-signature-256': 'sha256=' + hmac.new(
                    TEST_SECRET, body, hashlib.sha256
                ).hexdigest(),
            },
        )

    def test_upstream_url_error_returns_502(self):
        self.forward.side_effect = urllib.error.URLError('mock upstream unavailable')
        self.assertEqual(self.request(self.payload(), event='push'), 502)
        self.forward.assert_called_once()

    def test_upstream_timeout_returns_502(self):
        self.forward.side_effect = TimeoutError('mock upstream timeout')
        self.assertEqual(self.request(self.payload(), event='push'), 502)
        self.forward.assert_called_once()


if __name__ == '__main__':
    unittest.main()