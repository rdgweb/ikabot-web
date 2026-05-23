import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_piracy_runner_module():
    core_pkg = types.ModuleType("core")
    runner_registry = types.ModuleType("core.runner_registry")
    runner_registry.register_runner = lambda _code: (lambda cls: cls)
    core_pkg.runner_registry = runner_registry

    game_client_pkg = types.ModuleType("game_client")
    exceptions_mod = types.ModuleType("game_client.exceptions")

    class CaptchaRequiredError(Exception):
        pass

    exceptions_mod.CaptchaRequiredError = CaptchaRequiredError
    game_client_pkg.exceptions = exceptions_mod

    runners_pkg = types.ModuleType("runners")
    base_mod = types.ModuleType("runners.base")

    class BaseRunner:
        pass

    class RunnerResult:
        def __init__(self, *args, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    base_mod.BaseRunner = BaseRunner
    base_mod.RunnerResult = RunnerResult
    runners_pkg.base = base_mod

    sys.modules.update(
        {
            "core": core_pkg,
            "core.runner_registry": runner_registry,
            "game_client": game_client_pkg,
            "game_client.exceptions": exceptions_mod,
            "runners": runners_pkg,
            "runners.base": base_mod,
        }
    )

    spec = importlib.util.spec_from_file_location("piracy_runner_under_test", ROOT / "runners" / "piracy.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


PIRACY_MODULE = _load_piracy_runner_module()
PiracyMissionRunner = PIRACY_MODULE.PiracyMissionRunner


class PiracyMissionRunnerTests(unittest.TestCase):
    def test_capture_gain_uses_previous_active_mission_baseline(self):
        gain = PiracyMissionRunner._capture_gain_since_last_mission(
            {"last_capture_points": 5000, "last_time_remaining": 1800},
            5800,
        )
        self.assertEqual(gain, 800)

    def test_capture_gain_ignores_first_idle_observation(self):
        gain = PiracyMissionRunner._capture_gain_since_last_mission(
            {"last_capture_points": 5000, "last_time_remaining": 0},
            5800,
        )
        self.assertEqual(gain, 0)

    def test_capture_gain_never_returns_negative_values(self):
        gain = PiracyMissionRunner._capture_gain_since_last_mission(
            {"last_capture_points": 5800, "last_time_remaining": 1800},
            5000,
        )
        self.assertEqual(gain, 0)


if __name__ == "__main__":
    unittest.main()
