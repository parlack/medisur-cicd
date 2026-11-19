import contextlib
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock
import urllib.error

import relay


def notification(status='firing', started='2026-11-19T12:00:00Z', fingerprint='1234abcd'):
    return {'version': '4', 'status': status, 'truncatedAlerts': 0, 'alerts': [{
        'status': status, 'fingerprint': fingerprint, 'startsAt': started,
        'endsAt': '0001-01-01T00:00:00Z', 'labels': {'alertname': 'TargetDown', 'severity': 'warning'},
        'annotations': {'summary': 'A target is unavailable'},
        'generatorURL': 'https://monitor.invalid/?token=NEVER_FORWARD',
    }]}


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / 'alerts.sqlite3')
        self.github = Mock(repository='SergioMultimedios/medisur-cicd')
        self.github.create.return_value = 42
        self.github.find.return_value = None
        self.relay = relay.Relay(self.database, self.github)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda: self.relay.db.close())

    def process(self, payload=None):
        self.relay.process(relay.validate_payload(payload or notification()))

    def test_duplicate_active_and_restart(self):
        self.process()
        self.process()
        self.relay.db.close()
        self.relay = relay.Relay(self.database, self.github)
        self.process()
        self.github.create.assert_called_once()

    def test_resolve_duplicate_and_new_episode(self):
        self.process()
        self.process(notification('resolved'))
        self.process(notification('resolved'))
        self.process()
        self.github.close.assert_called_once_with(42)
        self.github.create.assert_called_once()
        self.process(notification(started='2026-11-19T14:00:00Z'))
        self.assertEqual(self.github.create.call_count, 2)
        markers = [call.args[1] for call in self.github.create.call_args_list]
        self.assertNotEqual(markers[0], markers[1])

    def test_unknown_resolve_does_not_create(self):
        self.process(notification('resolved'))
        self.process()
        self.github.create.assert_not_called()
        self.github.close.assert_not_called()

    def test_old_resolve_does_not_close_new_episode(self):
        self.process()
        self.process(notification(started='2026-11-19T14:00:00Z'))
        self.process(notification('resolved'))
        self.github.close.assert_not_called()
        self.github.create.assert_called_once()
        self.process(notification('resolved', started='2026-11-19T14:00:00Z'))
        self.github.close.assert_called_once_with(42)

    def test_explicit_create_failure_is_retryable(self):
        self.github.create.side_effect = [relay.UpstreamError(), 42]
        with self.assertRaises(relay.UpstreamError):
            self.process()
        self.assertIsNone(self.relay.db.execute('SELECT * FROM alerts').fetchone())
        self.process()
        self.assertEqual(self.github.create.call_count, 2)

    def test_failed_new_episode_preserves_resolved_tombstone(self):
        self.process(notification('resolved'))
        self.github.create.side_effect = relay.UpstreamError()
        with self.assertRaises(relay.UpstreamError):
            self.process(notification(started='2026-11-19T14:00:00Z'))
        self.process()
        self.assertEqual(self.github.create.call_count, 1)

    def test_uncertain_create_reconciles_without_second_post(self):
        self.github.create.side_effect = relay.UpstreamError(uncertain=True)
        with self.assertRaises(relay.UpstreamError):
            self.process()
        self.relay.db.close()
        self.relay = relay.Relay(self.database, self.github)
        with self.assertRaises(relay.UpstreamError):
            self.process()
        self.github.find.return_value = 99
        self.process(notification('resolved'))
        self.github.create.assert_called_once()
        self.github.close.assert_called_once_with(99)

    def test_failed_close_keeps_active_state(self):
        self.process()
        self.github.close.side_effect = [relay.UpstreamError(), None]
        with self.assertRaises(relay.UpstreamError):
            self.process(notification('resolved'))
        self.assertEqual(self.relay.db.execute('SELECT state FROM alerts').fetchone()[0], 'active')
        self.process(notification('resolved'))
        self.assertEqual(self.github.close.call_count, 2)

    def test_concurrent_duplicates(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.process(), range(30)))
        self.github.create.assert_called_once()

    def test_lock_contention_requests_retry(self):
        with self.relay.lock:
            with self.assertRaises(relay.UpstreamError):
                self.process()
        self.github.create.assert_not_called()

    def test_partial_batch_retry(self):
        payload = notification()
        payload['alerts'].extend(notification(fingerprint='5678abcd')['alerts'])
        self.github.create.side_effect = [42, relay.UpstreamError(), 43]
        with self.assertRaises(relay.UpstreamError):
            self.process(payload)
        self.process(payload)
        self.assertEqual(self.github.create.call_count, 3)

    def test_repository_is_bound_to_database(self):
        with self.assertRaises(ValueError):
            relay.Relay(self.database, Mock(repository='another/repo'))

    def test_private_check_failure_does_not_write(self):
        self.github.verify_private.side_effect = relay.UpstreamError()
        with self.assertRaises(relay.UpstreamError):
            self.process()
        self.github.create.assert_not_called()


class PayloadTests(unittest.TestCase):
    def test_sanitization(self):
        alert = notification()['alerts'][0]
        alert['labels'].update({
            'description': '<script>bad</script>\n@everyone [click](https://evil.invalid/secret) `x` | y\x00',
            'api_token': 'SUPERSECRET', 'normal': 'ghp_ABCD123456', 'webhook': 'ANOTHERSECRET',
        })
        title, body = relay.issue_content(alert, '<!-- known-marker -->')
        for forbidden in ('<script>', '@everyone', 'https://', '\x00', 'SUPERSECRET',
                          'ANOTHERSECRET', 'NEVER_FORWARD', 'ghp_ABCD123456'):
            self.assertNotIn(forbidden, title + body)
        self.assertIn('&lt;script&gt;', body)
        self.assertIn('&#64;', body)
        self.assertIn('\\|', body)
        self.assertIn('<!-- known-marker -->', body)

    def test_content_stays_under_github_character_limit(self):
        alert = notification()['alerts'][0]
        alert['labels'] = {str(index) + '&' * 250: '&' * 4096 for index in range(50)}
        title, body = relay.issue_content(alert, '<!-- marker -->')
        self.assertLess(len(body), 65536)
        self.assertLessEqual(len(title), 240)

    def test_validate_full_batch_before_effects(self):
        invalid = [[], {}, {'version': '3'}, notification()]
        invalid[-1]['alerts'].append({'status': 'invalid'})
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                relay.validate_payload(payload)

    def test_invalid_alert_fields(self):
        for key, value in [('fingerprint', '../../escape'), ('startsAt', '2026-11-19'),
                           ('startsAt', '<html>'), ('labels', {'value': 100}), ('status', 'unknown')]:
            payload = notification()
            payload['alerts'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                relay.validate_payload(payload)

    def test_truncation_and_alert_count_limits(self):
        payload = notification()
        payload['truncatedAlerts'] = 1
        with self.assertRaises(ValueError):
            relay.validate_payload(payload)
        payload['truncatedAlerts'] = 0
        payload['alerts'] *= 101
        with self.assertRaises(ValueError):
            relay.validate_payload(payload)

    def test_equivalent_timezones_normalized(self):
        first = relay.validate_payload(notification())[0]
        second = relay.validate_payload(notification(started='2026-11-19T06:00:00-06:00'))[0]
        self.assertEqual(first['startsAt'], second['startsAt'])


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = Path(self.temp.name) / 'token'
        self.token.write_text('test-token-not-real', encoding='utf-8')
        self.opener = Mock()
        self.github = relay.GitHub('SergioMultimedios/medisur-cicd', self.token, self.opener, lambda _: None)

    def response(self, payload):
        return contextlib.closing(io.BytesIO(json.dumps(payload).encode()))

    def http_error(self, code):
        return urllib.error.HTTPError('https://api.github.com', code, 'secret upstream message', {}, None)

    def test_private_required(self):
        self.opener.open.return_value = self.response({'private': False})
        with self.assertRaises(relay.UpstreamError):
            self.github.verify_private()

    def test_fixed_destination_no_webhook_url_and_token_header(self):
        self.opener.open.return_value = self.response({'number': 42})
        self.assertEqual(self.github.create(notification()['alerts'][0], '<!-- marker -->'), 42)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/repos/SergioMultimedios/medisur-cicd/issues')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-token-not-real')
        self.assertNotIn(b'NEVER_FORWARD', request.data)
        self.assertEqual(self.opener.open.call_args.kwargs['timeout'], 10)

    def test_get_retries_but_post_does_not(self):
        self.opener.open.side_effect = [self.http_error(503), self.response({'private': True})]
        self.github.verify_private()
        self.assertEqual(self.opener.open.call_count, 2)
        self.opener.reset_mock()
        self.opener.open.side_effect = socket.timeout('sensitive upstream')
        with self.assertRaises(relay.UpstreamError) as error:
            self.github.create(notification()['alerts'][0], '<!-- marker -->')
        self.assertTrue(error.exception.uncertain)
        self.assertEqual(str(error.exception), 'GitHub operation failed')
        self.opener.open.assert_called_once()

    def test_rate_limit_and_rejected_create(self):
        for code in (401, 403, 422, 429):
            self.opener.open.side_effect = self.http_error(code)
            with self.subTest(code=code), self.assertRaises(relay.UpstreamError) as error:
                self.github.create(notification()['alerts'][0], '<!-- marker -->')
            self.assertFalse(error.exception.uncertain)

    def test_post_server_error_is_uncertain(self):
        self.opener.open.side_effect = self.http_error(502)
        with self.assertRaises(relay.UpstreamError) as error:
            self.github.create(notification()['alerts'][0], '<!-- marker -->')
        self.assertTrue(error.exception.uncertain)

    def test_malformed_success_is_uncertain(self):
        self.opener.open.return_value = self.response({'number': 'not-a-number'})
        with self.assertRaises(relay.UpstreamError) as error:
            self.github.create(notification()['alerts'][0], '<!-- marker -->')
        self.assertTrue(error.exception.uncertain)

    def test_find_uses_marker_and_ignores_pull_requests(self):
        self.opener.open.return_value = self.response([
            {'number': 8, 'body': '<!-- marker -->', 'pull_request': {}},
            {'number': 9, 'body': 'test\n<!-- marker -->'},
        ])
        self.assertEqual(self.github.find('<!-- marker -->'), 9)

    def test_find_pagination_bound(self):
        self.opener.open.side_effect = lambda *args, **kwargs: self.response([{'number': 8, 'body': ''}] * 100)
        with self.assertRaises(relay.UpstreamError):
            self.github.find('<!-- marker -->')
        self.assertEqual(self.opener.open.call_count, 10)

    def test_redirect_disabled(self):
        self.assertIsNone(relay.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))

    def test_invalid_repository(self):
        for repository in ('org/repo?token=x', '../repo', 'org/repo/issues', 'https://evil.invalid'):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                relay.GitHub(repository)

    def test_missing_token_is_generic_failure(self):
        self.token.unlink()
        with self.assertRaises(relay.UpstreamError):
            self.github.verify_private()
        self.opener.open.assert_not_called()

    def test_token_rotation(self):
        self.opener.open.side_effect = lambda *args, **kwargs: self.response({'private': True})
        self.github.verify_private()
        self.token.write_text('rotated-test-token', encoding='utf-8')
        self.github.verify_private()
        self.assertEqual(self.opener.open.call_args.args[0].get_header('Authorization'), 'Bearer rotated-test-token')


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        self.server = relay.Server(('127.0.0.1', 0), self.backend, 'test-secret')
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, body=None, path='/alerts', method='POST', headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            data = json.dumps(notification()).encode() if body is None else body
            all_headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer test-secret'}
            if headers:
                all_headers.update(headers)
            connection.request(method, path, data, all_headers)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_firing_success(self):
        self.assertEqual(self.request()[0], 200)
        self.backend.process.assert_called_once()

    def test_bearer_enforced_and_optional(self):
        self.assertEqual(self.request(headers={'Authorization': 'Bearer incorrect'})[0], 401)
        self.backend.process.assert_not_called()
        self.server.secret = ''
        self.assertEqual(self.request(headers={'Authorization': ''})[0], 200)

    def test_body_limit_before_read(self):
        self.assertEqual(self.request(b'', headers={'Content-Length': str(relay.MAX_BODY + 1)})[0], 413)
        self.backend.process.assert_not_called()

    def test_invalid_payload_and_unsupported_encoding(self):
        self.assertEqual(self.request(b'{bad json')[0], 400)
        self.assertEqual(self.request(headers={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.request(headers={'Content-Encoding': 'gzip'})[0], 400)
        self.assertEqual(self.request(headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request(headers={'Content-Length': '-1'})[0], 411)
        self.backend.process.assert_not_called()

    def test_routes_are_exact(self):
        self.assertEqual(self.request(path='/alerts?url=https://evil.invalid')[0], 404)
        self.assertEqual(self.request(path='/healthz', method='GET')[0], 200)
        self.assertEqual(self.request(method='GET')[0], 404)

    def test_failure_503_without_secret_logs(self):
        self.backend.process.side_effect = RuntimeError('SECRET https://evil.invalid/?token=leak')
        with self.assertLogs('alert-relay', 'WARNING') as logs:
            status, body, headers = self.request()
        self.assertEqual(status, 503)
        self.assertEqual(headers['Retry-After'], '15')
        self.assertNotIn('SECRET', str(logs.output) + body.decode())
        self.assertNotIn('evil.invalid', str(logs.output) + body.decode())

    def test_thread_limit_returns_503(self):
        for _ in range(16):
            self.server.slots.acquire()
        try:
            self.assertEqual(self.request()[0], 503)
        finally:
            for _ in range(16):
                self.server.slots.release()

    def test_invalid_later_alert_has_no_effect(self):
        payload = notification()
        payload['alerts'].append({'bad': 'value'})
        self.assertEqual(self.request(json.dumps(payload).encode())[0], 400)
        self.backend.process.assert_not_called()


if __name__ == '__main__':
    unittest.main()