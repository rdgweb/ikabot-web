import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_misc_module():
    core_pkg = types.ModuleType("core")
    runner_registry = types.ModuleType("core.runner_registry")
    runner_registry.register_runner = lambda _code: (lambda cls: cls)
    core_pkg.runner_registry = runner_registry

    runners_pkg = types.ModuleType("runners")
    base_mod = types.ModuleType("runners.base")

    class BaseRunner:
        @staticmethod
        def is_network_error(_exc):
            return False

        def network_error_result(self, _job_id, exc, reschedule_seconds=300):
            return RunnerResult(
                success=False,
                reschedule_seconds=reschedule_seconds,
                data={"error": str(exc), "retryable": True},
            )

    class RunnerResult:
        def __init__(self, *args, **kwargs):
            self.success = kwargs.get("success", True)
            self.reschedule_seconds = kwargs.get("reschedule_seconds", 0)
            self.reschedule_inputs = kwargs.get("reschedule_inputs")
            self.data = kwargs.get("data", {})

    base_mod.BaseRunner = BaseRunner
    base_mod.RunnerResult = RunnerResult
    runners_pkg.base = base_mod

    sys.modules.update(
        {
            "core": core_pkg,
            "core.runner_registry": runner_registry,
            "runners": runners_pkg,
            "runners.base": base_mod,
        }
    )

    spec = importlib.util.spec_from_file_location("misc_runner_under_test", ROOT / "runners" / "misc.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


MISC_MODULE = _load_misc_module()
ColonizeRunner = MISC_MODULE.ColonizeRunner
VacationModeRunner = MISC_MODULE.VacationModeRunner


class _ClientStub:
    def __init__(self, result=None):
        self.result = result or {"ok": True}
        self.preview = {
            "capacity": 0,
            "max_capacity": 5,
            "transporters": 0,
        }

    def activate_vacation_mode(self, *, city_id):
        return self.result

    def get_colonization_preview(self, *, source_city_id, island_id, position):
        return {
            **self.preview,
            "source_city_id": str(source_city_id),
            "island_id": str(island_id),
            "position": int(position),
        }

    def start_colonization(self, *, source_city_id, island_id, position, resources=None):
        return {
            "feedback": ["Sua ordem foi executada."],
            "source_city_id": str(source_city_id),
            "island_id": str(island_id),
            "position": int(position),
            "resources": resources or {},
        }


class VacationModeRunnerTests(unittest.TestCase):
    def test_enable_requires_fresh_login_to_be_blocked(self):
        runner = VacationModeRunner()
        runner.resolve_credentials = lambda *_args, **_kwargs: {"server": "s78-br"}
        runner.hub = types.SimpleNamespace(get_snapshot=lambda **_kwargs: {"cities": [{"id": 39271}]})
        runner.sessions = types.SimpleNamespace(invalidate_game_session=lambda *_args, **_kwargs: None)
        runner.log = lambda *_args, **_kwargs: None
        runner.save_game_client = lambda *_args, **_kwargs: None

        first_client = _ClientStub()
        calls = {"n": 0}

        def fake_get_client(*_args, **kwargs):
            calls["n"] += 1
            if kwargs.get("allow_cached") is False:
                raise Exception("Conta em modo ferias")
            return first_client

        runner.get_or_login_game_client = fake_get_client

        result = runner.execute(
            {
                "job_id": "j1",
                "account_id": "a1",
                "game_account_id": "g1",
                "inputs": {"enable": True},
            }
        )

        self.assertTrue(result.success)
        self.assertTrue(result.data["enabled"])
        self.assertTrue(result.data["confirmed"])
        self.assertEqual(result.data["confirmation"], "fresh_login_blocked")
        self.assertEqual(calls["n"], 2)

    def test_enable_fails_when_fresh_login_still_succeeds(self):
        runner = VacationModeRunner()
        runner.resolve_credentials = lambda *_args, **_kwargs: {"server": "s78-br"}
        runner.hub = types.SimpleNamespace(get_snapshot=lambda **_kwargs: {"cities": [{"id": 39271}]})
        runner.sessions = types.SimpleNamespace(invalidate_game_session=lambda *_args, **_kwargs: None)
        runner.log = lambda *_args, **_kwargs: None
        runner.save_game_client = lambda *_args, **_kwargs: None

        first_client = _ClientStub()
        second_client = _ClientStub()

        def fake_get_client(*_args, **kwargs):
            if kwargs.get("allow_cached") is False:
                return second_client
            return first_client

        runner.get_or_login_game_client = fake_get_client

        result = runner.execute(
            {
                "job_id": "j1",
                "account_id": "a1",
                "game_account_id": "g1",
                "inputs": {"enable": True},
            }
        )

        self.assertFalse(result.success)
        self.assertEqual(result.data["error"], "vacation_mode_not_confirmed")
        self.assertEqual(result.data["detail"], "fresh_login_still_succeeds")

    def test_disable_fails_when_fresh_login_is_still_blocked(self):
        runner = VacationModeRunner()
        runner.resolve_credentials = lambda *_args, **_kwargs: {"server": "s78-br"}
        runner.log = lambda *_args, **_kwargs: None
        runner.get_or_login_game_client = lambda *_args, **_kwargs: (_ for _ in ()).throw(Exception("Conta em modo ferias"))

        result = runner.execute(
            {
                "job_id": "j1",
                "account_id": "a1",
                "game_account_id": "g1",
                "inputs": {"enable": False},
            }
        )

        self.assertFalse(result.success)
        self.assertEqual(result.data["error"], "vacation_mode_still_active")


class ColonizeRunnerTests(unittest.TestCase):
    def test_colonize_requires_inputs(self):
        runner = ColonizeRunner()
        runner.log = lambda *_args, **_kwargs: None
        result = runner.execute(
            {
                "job_id": "j1",
                "account_id": "a1",
                "game_account_id": "g1",
                "inputs": {},
            }
        )
        self.assertFalse(result.success)
        self.assertEqual(result.data["error"], "missing_required_inputs")

    def test_colonize_calls_preview_and_start(self):
        runner = ColonizeRunner()
        runner.resolve_credentials = lambda *_args, **_kwargs: {"server": "s78-br"}
        runner.log = lambda *_args, **_kwargs: None
        runner.save_game_client = lambda *_args, **_kwargs: None
        client = _ClientStub()
        runner.get_or_login_game_client = lambda *_args, **_kwargs: client

        result = runner.execute(
            {
                "job_id": "j1",
                "account_id": "a1",
                "game_account_id": "g1",
                "inputs": {
                    "source_city_id": 39269,
                    "island_id": 4478,
                    "position": 5,
                    "wood": 2250,
                    "wine": 2500,
                },
            }
        )

        self.assertTrue(result.success)
        self.assertGreater(result.reschedule_seconds, 0)
        self.assertEqual(result.data["status"], "founding_started")
        self.assertEqual(result.data["source_city_id"], "39269")
        self.assertEqual(result.data["island_id"], "4478")
        self.assertEqual(result.data["position"], 5)
        self.assertEqual(result.data["resources"], {"wood": 2250, "wine": 2500})
        self.assertEqual(result.data["feedback"], ["Sua ordem foi executada."])
        self.assertEqual(result.reschedule_inputs["_phase"], "wait_founding")


if __name__ == "__main__":
    unittest.main()
