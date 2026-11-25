import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from core.login_diagnostics import login_diagnostic, safe_url
from core.proxy import StrictProxySession
from game_client.auth.lobby import LobbyAuthenticator


class LoginDiagnosticsTests(unittest.TestCase):
    def test_http_rejection_keeps_status_and_url_for_login_error(self):
        session = StrictProxySession()
        request = requests.Request('POST', 'https://lobby.ikariam.gameforge.com/api/users/me/loginLink').prepare()
        with patch.object(requests.Session, 'send', return_value=SimpleNamespace(status_code=400)):
            session.send(request, timeout=30)
        data = json.loads(login_diagnostic(RuntimeError('response body withheld'), session, 1, 3))
        self.assertEqual(data['http']['status'], 400)
        self.assertEqual(data['http']['method'], 'POST')

    def test_failed_http_request_keeps_useful_context_without_secrets(self):
        session = StrictProxySession('http://proxy-user:proxy-password@proxy.invalid:8080')
        session.login_stage = 'follow_login_link'
        request = requests.Request('GET', 'https://s78-br.ikariam.gameforge.com/index.php?token=private-token#private-fragment').prepare()
        failure = requests.ReadTimeout('response secret-body', request=request)
        with patch.object(requests.Session, 'send', side_effect=failure):
            try:
                session.send(request, timeout=30)
            except requests.ReadTimeout as exc:
                text = login_diagnostic(exc, session, 1, 3)
        data = json.loads(text)
        self.assertEqual(data['stage'], 'follow_login_link')
        self.assertEqual(data['http']['url'], 'https://s78-br.ikariam.gameforge.com/index.php')
        self.assertEqual(data['http']['method'], 'GET')
        self.assertEqual(data['http']['timeout_seconds'], 30)
        self.assertGreaterEqual(data['http']['duration_seconds'], 0)
        self.assertEqual(data['traceback'][0]['type'], 'ReadTimeout')
        self.assertTrue(data['traceback'][0]['frames'])
        for secret in ('private-token', 'private-fragment', 'secret-body', 'proxy-user', 'proxy-password'):
            self.assertNotIn(secret, text)

    def test_cause_trace_excludes_messages_source_and_locals(self):
        try:
            try:
                raise requests.ConnectionError('password=private-password')
            except requests.ConnectionError as inner:
                raise RuntimeError('cookie=private-cookie') from inner
        except RuntimeError as exc:
            data = json.loads(login_diagnostic(exc, None, 3, 3))
        self.assertEqual([e['type'] for e in data['traceback']], ['RuntimeError', 'ConnectionError'])
        self.assertNotIn('private-password', str(data))
        self.assertNotIn('private-cookie', str(data))
        self.assertEqual(set(data['traceback'][0]['frames'][0]), {'file', 'function', 'line'})

    def test_url_removes_credentials_query_and_fragment(self):
        self.assertEqual(safe_url('https://user:password@game.invalid:443/index.php?token=private#secret'),
                         'https://game.invalid:443/index.php')
        self.assertEqual(safe_url('https://[invalid'), '[invalid-url]')

    def test_login_link_log_does_not_expose_url_token(self):
        auth = LobbyAuthenticator.__new__(LobbyAuthenticator)
        auth.session = Mock()
        auth._context = {}
        auth._get_blackbox = Mock(return_value='private-blackbox')
        auth._set_headers = Mock()
        auth._accept_language = Mock(return_value='pt-BR')
        auth.session.post.return_value = SimpleNamespace(status_code=200, json=lambda: {
            'url': 'https://s78-br.ikariam.gameforge.com/index.php?token=private-link-token'})
        with patch('game_client.auth.lobby.logger') as logger:
            auth.get_login_link('private-lobby-token', {'id': 1, 'server': {'language': 'br', 'number': 78}})
        self.assertNotIn('private-link-token', str(logger.mock_calls))


if __name__ == '__main__':
    unittest.main()
