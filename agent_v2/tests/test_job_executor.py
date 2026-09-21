import importlib.util
import sys
import types
import unittest
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]


def _load_job_executor_module():
    core_pkg = types.ModuleType("core")
    config_mod = types.ModuleType("core.config")
    config_mod.settings = types.SimpleNamespace(agent_name="test-agent")

    hub_client_mod = types.ModuleType("core.hub_client")

    class HubClient:
        last_instance = None

        def __init__(self):
            self.calls = []
            self._status_calls = 0
            HubClient.last_instance = self

        def report_status(self, job_id, **kwargs):
            self.calls.append(("report_status", job_id, kwargs))
            self._status_calls += 1
            if self._status_calls == 2:
                raise requests.ReadTimeout("timed out while finalizing")

        def report_log(self, job_id, level, message):
            self.calls.append(("report_log", job_id, level, message))

        def reschedule_job(self, job_id, seconds, inputs=None):
            self.calls.append(("reschedule_job", job_id, seconds, inputs))

    hub_client_mod.HubClient = HubClient

    runner_registry = types.ModuleType("core.runner_registry")

    class FakeRunner:
        def __init__(self, **_kwargs):
            pass

        def execute(self, _job):
            return types.SimpleNamespace(success=True, reschedule_seconds=123, reschedule_inputs={"wine": 10})

    runner_registry.get_runner = lambda _action_code: FakeRunner

    sessions_mod = types.ModuleType("sessions")

    class _Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class SessionManager:
        keys = []

        def get_lock(self, key):
            self.keys.append(key)
            return _Lock()

    sessions_mod.SessionManager = SessionManager

    gss_mod = types.ModuleType("sessions.game_session_service")

    class LoginCooldownActive(Exception):
        delay_seconds = 0

    gss_mod.LoginCooldownActive = LoginCooldownActive

    sys.modules.update(
        {
            "core": core_pkg,
            "core.config": config_mod,
            "core.hub_client": hub_client_mod,
            "core.runner_registry": runner_registry,
            "sessions": sessions_mod,
            "sessions.game_session_service": gss_mod,
        }
    )

    spec = importlib.util.spec_from_file_location("job_executor_under_test", ROOT / "job_executor.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module, HubClient, SessionManager


JOB_EXECUTOR_MODULE, HUB_CLIENT_CLASS, SESSION_MANAGER_CLASS = _load_job_executor_module()


class JobExecutorTests(unittest.TestCase):
    def test_network_failure_after_followup_commit_does_not_reschedule_original_job_twice(self):
        job = {
            "job_id": "job-1",
            "action_code": 2,
            "account_id": "acc-1",
            "game_account_id": "ga-1",
        }

        JOB_EXECUTOR_MODULE.execute_job_payload(job, SESSION_MANAGER_CLASS())
        hub = HUB_CLIENT_CLASS.last_instance
        reschedules = [call for call in hub.calls if call[0] == "reschedule_job"]
        self.assertEqual(1, len(reschedules))
        self.assertEqual(("reschedule_job", "job-1", 123, {"wine": 10}), reschedules[0])

    def test_jobs_keep_game_account_execution_lock(self):
        job = {
            "job_id": "job-2",
            "action_code": 2,
            "account_id": "acc-1",
            "game_account_id": "ga-1",
        }
        sessions = SESSION_MANAGER_CLASS()

        JOB_EXECUTOR_MODULE.execute_job_payload(job, sessions)

        self.assertEqual(["ga-1"], sessions.keys)


if __name__ == "__main__":
    unittest.main()
