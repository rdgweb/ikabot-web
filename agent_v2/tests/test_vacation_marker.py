"""N-68: a login refused because the account is on vacation is reported to the hub."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "sessions", "core", "runners")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from game_client.exceptions import LoginError  # noqa: E402
from sessions.game_session_service import GameSessionService, LoginCooldownActive, VacationHold  # noqa: E402


class _Hub:
    def __init__(self, fail=False):
        self.vacation_reports = []
        self.proxy_failures = []
        self.fail = fail

    def record_login_vacation(self, *, game_account_id):
        if self.fail:
            raise RuntimeError("hub fora do ar")
        self.vacation_reports.append(game_account_id)
        return {"ok": True}

    def record_login_proxy_failure(self, *, game_account_id, reason=""):
        self.proxy_failures.append(game_account_id)
        return {}


def _service(hub, error, proxies=("http://p1", "http://p2", "http://p3")):
    service = object.__new__(GameSessionService)
    service.hub = hub
    service.attempts = []
    service._check_login_cooldown = lambda **kwargs: None
    service._candidate_proxy_urls = lambda **kwargs: list(proxies)

    def _login(**kwargs):
        service.attempts.append(kwargs["proxy_url"])
        raise error

    service._get_or_login_game_client_with_proxy = _login
    return service


class VacationLoginTests(unittest.TestCase):
    def _login(self, service, logs=None):
        return service.get_or_login_game_client(
            account_id="acc-1", game_account_id="ga-1", creds={},
            log=(lambda level, msg: logs.append((level, msg))) if logs is not None else None,
        )

    def test_vacation_block_is_reported_once_and_not_retried_on_other_proxies(self):
        hub = _Hub()
        logs = []
        service = _service(hub, LoginError("Conta em modo férias"))

        with self.assertRaises(LoginError):
            self._login(service, logs)

        self.assertEqual(hub.vacation_reports, ["ga-1"])
        self.assertEqual(service.attempts, ["http://p1"])     # the proxy is not the problem
        self.assertEqual(hub.proxy_failures, [])
        self.assertTrue(any("ferias" in msg for _level, msg in logs))

    def test_other_login_errors_are_not_vacation(self):
        hub = _Hub()
        service = _service(hub, LoginError("Sessão expirou imediatamente após loginLink"))

        with self.assertRaises(LoginError):
            self._login(service)

        self.assertEqual(hub.vacation_reports, [])

    def test_hub_failure_does_not_hide_the_login_error(self):
        logs = []
        service = _service(_Hub(fail=True), LoginError("nologin_umod"))

        with self.assertRaises(LoginError):
            self._login(service, logs)

        self.assertTrue(any("Falha ao avisar o hub" in msg for _level, msg in logs))

    def test_recognises_the_games_vacation_answers(self):
        for text in ("Conta em modo férias", "nologin_umod", "account in vacation mode"):
            self.assertTrue(GameSessionService._is_vacation_block(LoginError(text)), text)
        for text in ("loginLink falhou: status=400", "Proxy connection failed", ""):
            self.assertFalse(GameSessionService._is_vacation_block(LoginError(text)), text)


class _CooldownHub(_Hub):
    def __init__(self, state):
        super().__init__()
        self.state = state

    def get_login_cooldown(self, *, game_account_id):
        return dict(self.state)


def _guarded_service(state):
    """Real _check_login_cooldown; the login itself just records that it happened."""
    service = object.__new__(GameSessionService)
    service.hub = _CooldownHub(state)
    service.logins = []
    service._candidate_proxy_urls = lambda **kwargs: ["http://p1"]

    def _login(**kwargs):
        service.logins.append(kwargs["proxy_url"])
        return "client"

    service._get_or_login_game_client_with_proxy = _login
    return service


class VacationProtectionTests(unittest.TestCase):
    """After the mandatory period any login ends the vacation: ordinary jobs must wait."""

    def test_ordinary_job_waits_and_never_touches_the_game(self):
        service = _guarded_service({"vacation": True, "blocked_until": ""})

        with self.assertRaises(VacationHold) as ctx:
            service.get_or_login_game_client(account_id="acc-1", game_account_id="ga-1", creds={})

        self.assertEqual(service.logins, [])
        self.assertIsInstance(ctx.exception, LoginCooldownActive)   # the executor reschedules these
        self.assertGreaterEqual(ctx.exception.delay_seconds, 3600)
        self.assertIn("ferias", str(ctx.exception))

    def test_job_meant_to_wake_the_account_may_log_in(self):
        service = _guarded_service({"vacation": True, "blocked_until": ""})

        client = service.get_or_login_game_client(
            account_id="acc-1", game_account_id="ga-1", creds={}, allow_vacation_exit=True,
        )

        self.assertEqual((client, service.logins), ("client", ["http://p1"]))

    def test_account_not_on_vacation_logs_in_as_always(self):
        for state in ({"vacation": False, "blocked_until": ""}, {"blocked_until": ""}):   # old hub: no field
            service = _guarded_service(state)
            service.get_or_login_game_client(account_id="acc-1", game_account_id="ga-1", creds={})
            self.assertEqual(service.logins, ["http://p1"])


class VacationModeRunnerTests(unittest.TestCase):
    def _runner(self, on_vacation):
        from runners.misc import VacationModeRunner

        runner = object.__new__(VacationModeRunner)
        runner.hub = _CooldownHub({"vacation": on_vacation})
        runner.logs = []
        runner.login_calls = []
        runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
        runner.resolve_credentials = lambda *a, **k: {"email": "x"}
        runner.save_game_client = lambda *a, **k: None

        def _login(jid, aid, ga_id, creds, **kwargs):
            runner.login_calls.append(kwargs)
            return object()

        runner.get_or_login_game_client = _login
        return runner

    def test_activating_an_account_already_on_vacation_does_not_log_in(self):
        runner = self._runner(on_vacation=True)
        result = runner.execute({"job_id": "j", "account_id": "a", "game_account_id": "ga-1", "inputs": {"enable": True}})

        self.assertTrue(result.success)
        self.assertEqual(runner.login_calls, [])

    def test_deactivating_is_allowed_to_wake_the_account(self):
        runner = self._runner(on_vacation=True)
        result = runner.execute({"job_id": "j", "account_id": "a", "game_account_id": "ga-1", "inputs": {"enable": False}})

        self.assertTrue(result.success)
        self.assertEqual(runner.login_calls, [{"allow_cached": False, "allow_vacation_exit": True}])


if __name__ == "__main__":
    unittest.main()
