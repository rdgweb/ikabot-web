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

    misc_mod = types.ModuleType("runners.misc")
    for _helper in ("detect_founded_city", "fetch_city_payload", "fetch_owned_cities"):
        setattr(misc_mod, _helper, lambda *args, **kwargs: None)
    runners_pkg.misc = misc_mod

    sys.modules.update(
        {
            "core": core_pkg,
            "core.runner_registry": runner_registry,
            "game_client": game_client_pkg,
            "game_client.exceptions": exceptions_mod,
            "runners": runners_pkg,
            "runners.base": base_mod,
            "runners.misc": misc_mod,
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

    def test_started_mission_keeps_active_baseline_for_next_run(self):
        state_inputs = {
            "last_capture_points": 5800,
            "last_time_remaining": 0,
        }
        new_time_remaining = 1800
        next_inputs = {
            **state_inputs,
            "last_time_remaining": new_time_remaining,
        }
        gain = PiracyMissionRunner._capture_gain_since_last_mission(
            next_inputs,
            6600,
        )
        self.assertEqual(gain, 800)


class PiracyStatsAndFinalPhaseTests(unittest.TestCase):
    def test_totals_without_a_start_begin_again_together(self):
        window = PiracyMissionRunner._stats_window({"total_crew_converted": 2747, "total_missions_started": 15}, 1000)
        self.assertEqual(window, {"piracy_stats_since": 1000, "total_crew_converted": 0, "total_missions_started": 0})

    def test_totals_with_a_start_are_kept(self):
        inputs = {"piracy_stats_since": 500, "total_crew_converted": 113, "total_missions_started": 1}
        self.assertEqual(PiracyMissionRunner._stats_window(inputs, 1000), inputs)

    def test_final_phase_counts_down_from_the_ranking_reading(self):
        inputs = {"final_phase_enabled": True, "final_phase_hours": 48,
                  "piracy_highscore_time_left": 50 * 3600, "piracy_highscore_updated_at": 1000}
        self.assertIsNone(PiracyMissionRunner._final_phase_left(inputs, 1000))               # 50 h left
        self.assertEqual(PiracyMissionRunner._final_phase_left(inputs, 1000 + 3 * 3600), 47 * 3600)
        self.assertIsNone(PiracyMissionRunner._final_phase_left(inputs, 1000 + 51 * 3600))   # the ranking turned

    def test_final_phase_off_or_without_ranking(self):
        self.assertIsNone(PiracyMissionRunner._final_phase_left({"final_phase_hours": 48, "piracy_highscore_time_left": 10,
                                                                 "piracy_highscore_updated_at": 1}, 2))
        self.assertIsNone(PiracyMissionRunner._final_phase_left({"final_phase_enabled": True}, 2))


if __name__ == "__main__":
    unittest.main()


class CaptchaBackoffTests(unittest.TestCase):
    def test_backoff_grows_and_is_capped(self):
        delays = [PIRACY_MODULE._captcha_backoff_seconds(streak) for streak in (1, 2, 3, 4, 5, 6, 50)]
        self.assertEqual(delays, [300, 600, 1200, 2400, 3600, 3600, 3600])

    def test_backoff_tolerates_bad_streak_values(self):
        self.assertEqual(PIRACY_MODULE._captcha_backoff_seconds(0), 300)
        self.assertEqual(PIRACY_MODULE._captcha_backoff_seconds(None), 300)
