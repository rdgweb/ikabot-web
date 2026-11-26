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


class ColonyRoomTests(unittest.TestCase):
    """The special cycle colonizes next to the target: with every town slot of the palace taken the game has no
    colonize form, so the cycle waits and the missions go on (LordDarkness: 10 towns, palace 9)."""

    def _runner(self, snapshot):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.hub = types.SimpleNamespace(get_snapshot=lambda game_account_id: snapshot)
        return runner

    def test_palace_level_holds_one_town_more(self):
        towns = [{"id": i, "buildings": [{"building": "palaceColony", "level": 9}]} for i in range(9)]
        towns.append({"id": 99, "buildings": [{"building": "palace", "level": 9}]})
        self.assertEqual(self._runner({"cities": towns})._colony_room("ga"), (10, 10))
        self.assertEqual(self._runner({"cities": towns[1:]})._colony_room("ga"), (9, 10))
        self.assertIsNone(self._runner({})._colony_room("ga"))

    def test_retry_after_keeps_the_cycle_off(self):
        import time as _time

        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        base = {"enable_targeted_cycle": True, "targeted_after_hour": 0}
        self.assertTrue(runner._targeted_cycle_due(base))
        self.assertFalse(runner._targeted_cycle_due({**base, "targeted_retry_after": int(_time.time()) + 3600}))
        self.assertTrue(runner._targeted_cycle_due({**base, "targeted_retry_after": int(_time.time()) - 1}))


class MonthPlanTests(unittest.TestCase):
    """N-211: the month's plan — the hub's route comes first (outside the ranking too), the same lobby is never
    raided, and every outcome goes back to the hub."""

    def _runner(self, config=None, next_payload=None):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        self.reports, self.logs = [], []
        runner.hub = types.SimpleNamespace(
            get_piracy_next=lambda game_account_id: next_payload or {"route": None, "hold": False},
            report_piracy_route=lambda route_id, status, message="": self.reports.append((route_id, status, message)),
        )
        runner.get_agent_config = lambda: config or {"accounts": []}
        runner.log = lambda jid, level, text: self.logs.append((level, text))
        runner._find_colony_spot = lambda client, target_city_id: {"island_id": "9", "position": 3, "coord_x": 1, "coord_y": 2}
        return runner

    def _route(self, **extra):
        return {"route_id": "r1", "target_game_account_id": "ga-target", "raider_lobby": "L1", "target_lobby": "L2",
                "stage": 1, "week": 1, "player_name": "Alvo", "target_city_id": "123", "target_island_id": "45", **extra}

    def test_the_plan_route_becomes_the_target_without_the_ranking(self):
        runner = self._runner()
        entry, spot = runner._planned_pair("j", object(), self._route(), "ga-raider")
        self.assertEqual((entry["target_city_id"], entry["_route_id"], entry["_route_keep"]), ("123", "r1", True))
        self.assertEqual(spot["island_id"], "9")
        self.assertEqual(self.reports, [])
        entry, _spot = runner._planned_pair("j", object(), self._route(stage=2), "ga-champion")
        self.assertFalse(entry["_route_keep"])                       # the champion follows its job's conversion

    def test_same_lobby_is_refused_by_the_hub_ids_or_the_agent_config(self):
        runner = self._runner()
        self.assertIsNone(runner._planned_pair("j", object(), self._route(target_lobby="L1"), "ga-raider"))
        self.assertEqual(self.reports[-1][1:], ("failed", "mesmo lobby"))
        config = {"accounts": [{"game_accounts": [{"id": "ga-raider"}, {"id": "ga-target"}]}]}
        runner = self._runner(config=config)
        self.assertIsNone(runner._planned_pair("j", object(), self._route(), "ga-raider"))
        self.assertEqual(self.reports[-1][1], "failed")

    def test_no_colony_spot_or_city_is_reported(self):
        runner = self._runner()
        runner._find_colony_spot = lambda client, target_city_id: None
        self.assertIsNone(runner._planned_pair("j", object(), self._route(), "ga-raider"))
        self.assertEqual(self.reports[-1][1], "failed")
        self.assertIsNone(runner._planned_pair("j", object(), self._route(target_city_id=""), "ga-raider"))

    def test_plan_next_survives_the_hub(self):
        runner = self._runner(next_payload={"route": self._route(), "hold": True, "share_percent": 10, "share_every_hours": 24})
        plan = runner._plan_next("ga")
        self.assertTrue(plan["hold"])
        self.assertFalse(plan["convert_all"])
        self.assertEqual((plan["route"]["route_id"], plan["share_percent"]), ("r1", 10))

        def boom(game_account_id):
            raise RuntimeError("hub fora")

        runner.hub.get_piracy_next = boom
        plan = runner._plan_next("ga")
        # N-216: no answer = keep the points this round (no raid, no conversion)
        self.assertEqual((plan["route"], plan["hold"], plan["convert_all"], plan["share_percent"]), (None, True, False, 0))

    def test_direct_raid_from_the_fortress_or_none(self):
        runner = self._runner()
        runner.is_network_error = lambda exc: False
        client = types.SimpleNamespace(start_piracy_raid=lambda **kw: {"ok": True, "time_remaining": 900, **kw})
        raid = runner._direct_raid("j", client, "77", self._route())
        self.assertEqual((raid["source_city_id"], raid["destination_city_id"]), ("77", "123"))
        client.start_piracy_raid = lambda **kw: {"ok": False}
        self.assertIsNone(runner._direct_raid("j", client, "77", self._route()))

        def out_of_reach(**kw):
            raise RuntimeError("modal")

        client.start_piracy_raid = out_of_reach
        self.assertIsNone(runner._direct_raid("j", client, "77", self._route()))

    def test_share_conversion_takes_only_the_share(self):
        runner = self._runner()
        converted = []
        client = types.SimpleNamespace(convert_piracy_points=lambda city_id, crew: converted.append((city_id, crew)))
        runner._share_convert("j", client, "77", {"conversion_factor": 10}, 5000, 10)
        self.assertEqual(converted, [("77", 50)])                  # 10% of 5000 = 500 points = 50 crew
        runner._share_convert("j", client, "77", {"conversion_factor": 10}, 50, 10)
        self.assertEqual(len(converted), 1)                         # 5 points make no crew


class MonthPlanConversionTests(unittest.TestCase):
    """N-211: what an account of the plan may convert — never above the crew its raider still beats; a passer
    converts what it lacks to beat its origins."""

    def _runner(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.log = lambda jid, level, text: None
        self.converted = []
        self.client = types.SimpleNamespace(convert_piracy_points=lambda city_id, crew: self.converted.append(crew))
        return runner

    def test_the_share_stops_at_the_cap(self):
        runner = self._runner()
        state = {"conversion_factor": 10, "complete_crew_points": 29900}
        self.assertEqual(runner._share_convert("j", self.client, "1", state, 20000, 10, crew_cap=30000), 100)
        self.assertEqual(runner._share_convert("j", self.client, "1", {**state, "complete_crew_points": 30000}, 20000, 10, crew_cap=30000), 0)
        self.assertEqual(runner._share_convert("j", self.client, "1", state, 20000, 10), 200)   # no cap: the whole share
        self.assertEqual(self.converted, [100, 200])

    def test_a_passer_converts_what_it_lacks(self):
        runner = self._runner()
        state = {"conversion_factor": 10, "complete_crew_points": 30000}
        self.assertEqual(runner._floor_convert("j", self.client, "1", state, 50000, 31000), 1000)
        # N-218: what it has does not reach it: nothing (half a way does not raid, and it would only spend the points)
        self.assertEqual(runner._floor_convert("j", self.client, "1", state, 5000, 31000), 0)
        self.assertEqual(runner._floor_convert("j", self.client, "1", state, 50000, 29000), 0)


class PlanRouteAtTests(unittest.TestCase):
    def test_plan_next_reads_when_the_raid_is(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.hub = types.SimpleNamespace(get_piracy_next=lambda game_account_id: {"route": None, "hold": False, "route_at": 1800000000})
        self.assertEqual(runner._plan_next("ga")["route_at"], 1800000000)
        runner.hub = types.SimpleNamespace(get_piracy_next=lambda game_account_id: {"route": None})
        self.assertEqual(runner._plan_next("ga")["route_at"], 0)


class DirectRaidConfirmTests(unittest.TestCase):
    """The game does not always confirm the raid in words: a ship that left means the raid went."""

    def test_a_busy_ship_confirms_the_raid(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.log = lambda jid, level, text: None
        runner.is_network_error = lambda exc: False
        client = types.SimpleNamespace(start_piracy_raid=lambda **kw: {"ok": False},
                                       get_piracy_state=lambda city_id: {"time_remaining": 2500})
        raid = runner._direct_raid("j", client, "77", {"target_city_id": "1", "target_island_id": "2"})
        self.assertEqual((raid["ok"], raid["time_remaining"]), (True, 2500))
        client.get_piracy_state = lambda city_id: {"time_remaining": 0}
        self.assertIsNone(runner._direct_raid("j", client, "77", {"target_city_id": "1", "target_island_id": "2"}))


class NoPlanColonyTests(unittest.TestCase):
    """N-211 (22/11): out of the fortress's reach, the plan founds no temporary colony for now: the route goes back to
    the hub as failed (the hub draws someone else)."""

    def test_out_of_reach_founds_no_colony(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.log = lambda *a, **k: None
        reported = []
        runner._report_route = lambda jid, route_id, status, message="": reported.append((route_id, status, message))
        runner._planned_pair = lambda *a, **k: self.fail("no colony while PLAN_COLONY is off")
        self.assertFalse(PIRACY_MODULE.PLAN_COLONY)
        self.assertIsNone(runner._out_of_reach("j", object(), {"route_id": "r1", "player_name": "Alvo"}, "ga"))
        self.assertEqual(reported, [("r1", "failed", "fora do alcance da fortaleza")])


class ColonyGoneTests(unittest.TestCase):
    """N-211 (22/11): the temporary colony abandoned by hand: the job no longer tries to abandon a town that is gone."""

    def test_gone_only_when_the_list_was_read(self):
        original = PIRACY_MODULE.fetch_owned_cities
        try:
            PIRACY_MODULE.fetch_owned_cities = lambda client: [{"id": "37430"}, {"id": "11"}]
            self.assertTrue(PiracyMissionRunner._colony_gone(object(), 96517))
            self.assertFalse(PiracyMissionRunner._colony_gone(object(), 37430))
            PIRACY_MODULE.fetch_owned_cities = lambda client: []
            self.assertFalse(PiracyMissionRunner._colony_gone(object(), 96517))      # nothing read: not gone
            PIRACY_MODULE.fetch_owned_cities = lambda client: (_ for _ in ()).throw(RuntimeError("rede"))
            self.assertFalse(PiracyMissionRunner._colony_gone(object(), 96517))
        finally:
            PIRACY_MODULE.fetch_owned_cities = original


class HubSilentTests(unittest.TestCase):
    """N-216 (23/11): no answer from the hub = keep the points this round (never the job's own convert-all)."""

    def test_no_answer_holds(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)

        class Hub:
            def get_piracy_next(self, game_account_id):
                raise RuntimeError("hub fora")

        runner.hub = Hub()
        plan = runner._plan_next("ga")
        self.assertEqual((plan["hold"], plan["convert_all"], plan["share_percent"], plan["crew_floor"], plan["route"]),
                         (True, False, 0, None, None))


class CrewBonusConvertTests(unittest.TestCase):
    """N-218: the held points add up to 1.400 of strength (7.000 points or more); converting takes it away."""

    def _runner(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.logs = []
        runner.log = lambda jid, level, text: runner.logs.append(text)
        return runner

    PLAN = {"bonus_max": 1400, "bonus_full": 7000, "base_cap": None}

    def test_the_passer_converts_only_what_is_above_the_bonus_limit(self):
        runner, converted = self._runner(), []

        class Game:
            def convert_piracy_points(self, city_id, crew):
                converted.append(crew)

        # 20.000 points (bonus 1.400) and base 100: strength 1.500; to reach 2.000 it needs 500 crew = 5.000 points
        state = {"conversion_factor": 10, "complete_crew_points": 1500}
        self.assertEqual(runner._floor_convert("j", Game(), "1", state, 20000, 2000, dict(self.PLAN)), 500)
        self.assertEqual(converted, [500])
        # 8.000 points: only 1.000 are above the limit (100 crew); below it each point converted lowers the strength
        converted.clear()
        state = {"conversion_factor": 10, "complete_crew_points": 1500}
        self.assertEqual(runner._floor_convert("j", Game(), "1", state, 8000, 1700, dict(self.PLAN)), 0)
        self.assertEqual(converted, [])
        self.assertIn("nem convertendo", runner.logs[-1])

    def test_the_share_never_takes_the_base_above_the_hub_limit(self):
        runner, converted = self._runner(), []

        class Game:
            def convert_piracy_points(self, city_id, crew):
                converted.append(crew)

        state = {"conversion_factor": 10, "complete_crew_points": 1500}       # 20.000 points: base 100
        plan = dict(self.PLAN, base_cap=150)
        self.assertEqual(runner._share_convert("j", Game(), "1", state, 20000, 10, None, plan), 50)
        self.assertEqual(converted, [50])

    def test_the_conversion_time(self):
        self.assertEqual(PiracyMissionRunner._conversion_seconds({"conversion_time_base": 60, "conversion_time_per_unit": 3}, 100), 360)


class ClearTargetedStateTests(unittest.TestCase):
    """N-218 (23/11): the hub merges the rescheduled inputs: the special cycle's keys go empty, not left out."""

    def test_keys_go_empty(self):
        cleaned = PIRACY_MODULE._clear_targeted_state({"_targeted_phase": "wait_abandon", "_targeted_temp_city_id": 96517,
                                                       "mission_level": 7})
        self.assertEqual(cleaned, {"_targeted_phase": None, "_targeted_temp_city_id": None, "mission_level": 7})
        merged = {"_targeted_phase": "wait_abandon", "_targeted_temp_city_id": 96517}
        merged.update(cleaned)                                           # what the hub does with them
        self.assertFalse(str(merged.get("_targeted_phase") or "").strip())


class PeriodSwitchTests(unittest.TestCase):
    """N-224: day 08-22 with 1h missions, night with 8h: the mission ends by the switch — the biggest that fits."""

    CFG = {"schedule_by_time": True, "day_start_hour": 8, "day_end_hour": 22, "day_mission_level": 9, "night_mission_level": 15}

    def at(self, hour, minute=0):
        from datetime import datetime as _dt
        return _dt(2026, 11, 25, hour, minute, 30)

    def test_seconds_to_the_switch_both_sides_and_over_midnight(self):
        left = PIRACY_MODULE._seconds_to_switch
        self.assertEqual(left(self.CFG, self.at(6)), 2 * 3600 - 30)        # night -> 08:00
        self.assertEqual(left(self.CFG, self.at(21, 30)), 30 * 60 - 30)   # day -> 22:00
        self.assertEqual(left(self.CFG, self.at(23)), 9 * 3600 - 30)       # after midnight -> 08:00
        self.assertIsNone(left({"day_start_hour": 8, "day_end_hour": 8}, self.at(6)))

    def test_the_biggest_mission_that_fits(self):
        fit, left = PIRACY_MODULE._fitting_level, PIRACY_MODULE._seconds_to_switch
        self.assertEqual(fit(15, 17, left(self.CFG, self.at(22))), 15)     # 22:00: the 8h night mission
        self.assertEqual(fit(15, 17, left(self.CFG, self.at(6))), 11)      # 06:00: 2h, up to 08:00 (not 8h)
        self.assertEqual(fit(9, 17, left(self.CFG, self.at(21, 30))), 7)   # 21:30: 30 min, not 1h into the night
        self.assertEqual(fit(9, 17, left(self.CFG, self.at(10))), 9)       # the middle of the day: the configured 1h
        self.assertEqual(fit(15, 9, left(self.CFG, self.at(22))), 9)       # the fortress caps it
        self.assertIsNone(fit(15, 17, 60 - PIRACY_MODULE.SWITCH_TOLERANCE - 1))   # nothing fits: wait
        self.assertEqual(fit(9, 17, None), 9)                              # not by time: as configured


class FreshNumbersTests(unittest.TestCase):
    """N-254: the agent sends the strength and points it has just read; the hub decides on them."""

    def test_plan_next_sends_the_numbers_of_now(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        asked = []

        class Hub:
            def get_piracy_next(self, game_account_id, crew=None, points=None):
                asked.append((game_account_id, crew, points))
                return {"route": {"route_id": "r1", "convert_points": 5000}, "hold": True}

        runner.hub = Hub()
        plan = runner._plan_next("ga", 1500, 20000)
        self.assertEqual(asked, [("ga", 1500, 20000)])
        self.assertEqual(plan["route"]["convert_points"], 5000)


class BonusStepTests(unittest.TestCase):
    """Measured 25/11: the bonus is all or nothing — 1.400 from 7.000 points, 0 below."""

    def test_all_or_nothing(self):
        plan = {"bonus_max": 1400, "bonus_full": 7000}
        self.assertEqual([PiracyMissionRunner._bonus(p, plan) for p in (0, 5769, 6999, 7000, 40000)], [0, 0, 0, 1400, 1400])

    def test_below_the_limit_converting_only_adds(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.log = lambda *a, **k: None
        converted = []

        class Game:
            def convert_piracy_points(self, city_id, crew):
                converted.append(crew)

        state = {"conversion_factor": 10, "complete_crew_points": 100}      # 5.000 points: no bonus, base 100
        plan = {"bonus_max": 1400, "bonus_full": 7000, "base_cap": None}
        self.assertEqual(runner._floor_convert("j", Game(), "1", state, 5000, 400, plan), 300)


class PlanTurnFieldsTests(unittest.TestCase):
    """N-255: the passer's conversion ahead and the drawn share come from the hub."""

    def test_plan_next_reads_the_new_fields(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.hub = types.SimpleNamespace(get_piracy_next=lambda game_account_id: {
            "route": None, "hold": True, "share_percent": 3, "share_jitter": [2, 1],
            "preconvert_points": 3330, "preconvert_target": "LordDarkness"})
        plan = runner._plan_next("ga")
        self.assertEqual((plan["share_jitter"], plan["preconvert_points"], plan["preconvert_target"]), ([2, 1], 3330, "LordDarkness"))


class ConvertCheckedTests(unittest.TestCase):
    """N-263: a conversion is checked against the game's answer — the Carcereiro (26/11) logged 17.900 points
    converted and waited 212 min for a conversion that never happened (it had been raided just before)."""

    def _runner(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        runner.logs = []
        runner.log = lambda jid, level, text: runner.logs.append((level, text))
        runner.is_network_error = lambda exc: False
        return runner

    def test_the_points_left_say_whether_it_came_in(self):
        runner, sink = self._runner(), {}
        game = types.SimpleNamespace(convert_piracy_points=lambda city_id, crew: {
            "capture_points": 8062, "crew_points": 2560, "complete_crew_points": 4000})
        self.assertEqual(runner._convert_checked("j", game, "1", 1790, 25962, 10, sink), 1790)
        self.assertEqual(sink["last_capture_points"], 8062)

    def test_nothing_taken_is_no_conversion(self):
        runner, sink = self._runner(), {}
        game = types.SimpleNamespace(convert_piracy_points=lambda city_id, crew: {
            "capture_points": 1131, "crew_points": 770, "complete_crew_points": 2210})
        # it held 1.131 (raided just before): the 17.900 asked could not leave
        self.assertEqual(runner._convert_checked("j", game, "1", 1790, 25962, 10, sink), 0)
        self.assertEqual(sink["last_capture_points"], 1131)
        self.assertIn("não entrou", runner.logs[-1][1])
        game = types.SimpleNamespace(convert_piracy_points=lambda city_id, crew: {},
                                     get_piracy_state=lambda city_id: {"capture_points": 25962})
        self.assertEqual(runner._convert_checked("j", game, "1", 1790, 25962, 10, {}), 0)


class SentSecondsTests(unittest.TestCase):
    def test_the_hub_hears_when_the_raid_lands(self):
        runner = PiracyMissionRunner.__new__(PiracyMissionRunner)
        sent = []
        runner.hub = types.SimpleNamespace(report_piracy_route=lambda route_id, status, message="", **kw: sent.append((status, kw)))
        runner.log = lambda jid, level, text: None
        runner._report_route("j", "r1", "sent", "assalto direto da fortaleza", seconds=900)
        runner._report_route("j", "r1", "done", "ganhou")
        self.assertEqual(sent, [("sent", {"seconds": 900}), ("done", {})])
