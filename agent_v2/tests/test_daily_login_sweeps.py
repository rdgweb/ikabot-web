"""N-82: the daily login (ac=6) sends the daily bonus once per game cycle and
comes back during the day ("sweeps") to collect favor from tasks finished
after the reset -- previously it only ran right after the reset, when almost no
task was done yet, and everything finished later was lost at the next reset."""

import unittest

from runners.auth import DAILY_MIN_DELAY, DailyLoginRunner, _next_daily_delay

H = 3600
M = 60


def _delay(reset_in, sweep_h=4, before_min=30, margin_min=15, fallback_h=24):
    return _next_daily_delay(
        reset_in,
        sweep_interval_hours=sweep_h,
        sweep_before_reset_minutes=before_min,
        reschedule_margin_minutes=margin_min,
        fallback_interval_hours=fallback_h,
    )


class NextDailyDelayTests(unittest.TestCase):
    def test_mid_day_waits_one_sweep_interval(self):
        self.assertEqual(_delay(20 * H), (4 * H, "sweep"))

    def test_last_sweep_is_placed_before_the_reset(self):
        self.assertEqual(_delay(2 * H), (2 * H - 30 * M, "sweep"))

    def test_inside_the_final_window_goes_to_after_the_reset(self):
        self.assertEqual(_delay(20 * M), (20 * M + 15 * M, "reset"))

    def test_sweeps_disabled_keeps_the_old_once_a_day_behaviour(self):
        self.assertEqual(_delay(20 * H, sweep_h=0), (20 * H + 15 * M, "reset"))

    def test_no_countdown_uses_the_smaller_of_fallback_and_sweep(self):
        self.assertEqual(_delay(0), (4 * H, "fallback"))
        self.assertEqual(_delay(0, sweep_h=0), (24 * H, "fallback"))

    def test_never_schedules_below_the_minimum_delay(self):
        delay, kind = _delay(1, before_min=0, margin_min=0)
        self.assertEqual(kind, "reset")
        self.assertEqual(delay, DAILY_MIN_DELAY)


class _FakeClient:
    def __init__(self, end_at, tasks, favor=1000, reset_in=20 * H):
        self.end_at = end_at
        self.tasks = tasks
        self.favor = favor
        self.reset_in = reset_in
        self.bonus_calls = 0
        self.favor_calls = []

    def get_daily_tasks_state(self, city_id):
        return {
            "city_name": "Bye",
            "current_favor": self.favor,
            "favor_limit": 2500,
            "tasks_done": sum(1 for t in self.tasks if t.get("done")),
            "tasks_count": len(self.tasks),
            "collectible_tasks_count": sum(1 for t in self.tasks if t.get("collectible")),
            "countdown_seconds": self.reset_in,
            "countdown_end_at": self.end_at,
            "tasks": [dict(t) for t in self.tasks],
        }

    def collect_daily_login_bonus(self, city_id):
        self.bonus_calls += 1

    def collect_daily_task_favor(self, city_id, task_id):
        self.favor_calls.append(task_id)
        for t in self.tasks:
            if t["task_id"] == task_id:
                t["collectible"] = False
                self.favor += t.get("reward_favor", 100)

    def get_daily_city_overview(self, city_id):
        return {"ambrosia_fountain_active": False, "city_cinema_active": False}


class _FakeHub:
    def get_snapshot(self, game_account_id=None):
        return None


def _run(client, inputs):
    runner = DailyLoginRunner.__new__(DailyLoginRunner)
    runner.hub = _FakeHub()
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append(msg)
    runner.resolve_credentials = lambda *a, **k: {"email": "x"}
    runner.get_or_login_game_client = lambda *a, **k: client
    runner.save_game_session = lambda *a, **k: None
    job = {"job_id": "j1", "account_id": "a1", "game_account_id": "g1", "inputs": dict(inputs)}
    return runner, runner.execute(job)


class DailyLoginRunnerSweepTests(unittest.TestCase):
    BASE = {"city": 100, "collect_fountain": False}

    def test_first_run_of_the_cycle_sends_the_bonus_and_schedules_a_sweep(self):
        client = _FakeClient("2026-09-23T03:00:00+00:00", [{"task_id": 10, "collectible": True, "done": True}])

        _, result = _run(client, self.BASE)

        self.assertEqual(client.bonus_calls, 1)
        self.assertEqual(client.favor_calls, [10])
        self.assertEqual(result.reschedule_inputs["bonus_cycle_end_at"], "2026-09-23T03:00:00+00:00")
        self.assertEqual(result.data["next_run_kind"], "sweep")
        self.assertEqual(result.reschedule_seconds, 4 * H)

    def test_sweep_in_the_same_cycle_collects_favor_without_resending_the_bonus(self):
        client = _FakeClient(
            "2026-09-23T03:00:00+00:00",
            [{"task_id": 18, "collectible": True, "done": True}, {"task_id": 30, "collectible": True, "done": True}],
        )

        runner, result = _run(client, {**self.BASE, "bonus_cycle_end_at": "2026-09-23T03:00:00+00:00"})

        self.assertEqual(client.bonus_calls, 0)
        self.assertEqual(client.favor_calls, [18, 30])
        self.assertTrue(any("ja enviado neste ciclo" in m for m in runner.logs))

    def test_a_new_cycle_sends_the_bonus_again(self):
        client = _FakeClient("2026-09-24T03:00:00+00:00", [])

        _, result = _run(client, {**self.BASE, "bonus_cycle_end_at": "2026-09-23T03:00:00+00:00"})

        self.assertEqual(client.bonus_calls, 1)
        self.assertEqual(result.reschedule_inputs["bonus_cycle_end_at"], "2026-09-24T03:00:00+00:00")

    def test_full_favor_leaves_tasks_for_the_next_sweep(self):
        client = _FakeClient("2026-09-23T03:00:00+00:00", [{"task_id": 10, "collectible": True, "done": True}], favor=2500)

        runner, result = _run(client, self.BASE)

        self.assertEqual(client.favor_calls, [])
        self.assertEqual(result.data["next_run_kind"], "sweep")
        self.assertTrue(any("nova tentativa na proxima passada" in m for m in runner.logs))

    def test_existing_jobs_without_the_new_inputs_get_sweeps_by_default(self):
        client = _FakeClient("2026-09-23T03:00:00+00:00", [])

        _, result = _run(client, {"city": 100})

        self.assertEqual(result.data["next_run_kind"], "sweep")

    def test_sweeps_can_be_turned_off(self):
        client = _FakeClient("2026-09-23T03:00:00+00:00", [], reset_in=20 * H)

        _, result = _run(client, {**self.BASE, "sweep_interval_hours": 0})

        self.assertEqual(result.data["next_run_kind"], "reset")
        self.assertEqual(result.reschedule_seconds, 20 * H + 15 * M)


if __name__ == "__main__":
    unittest.main()
