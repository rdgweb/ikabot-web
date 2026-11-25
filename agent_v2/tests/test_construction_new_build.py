"""N-220: a new building is checked against the game's own cost, and a step the game ignores waits longer each time."""
import json
import time
import types as pytypes
import unittest
from datetime import datetime, timezone

from tests.test_construction import CITY_MODULE, ConstructionPlanRunner


BUILDING_GROUND_HTML = """
<ul id="buildings">
<li class="building warehouse">
  <div class="buildinginfo"><div title="Armazém" class="buildingimg warehouse"
    onclick="ajaxHandlerCall('?action=CityScreen&function=build&buildingId=7&position=12');"></div></div>
  <div class="costs"><ul class="resources">
    <li class="wood" title="Material de construção"><span class="accesshint">Material de construção: </span>160</li>
    <li class="time" title="Tempo de construção">10m 30s</li>
  </ul></div>
  <a class="button build" href="?action=CityScreen&function=build&position=12" data-building="7">Construir</a>
</li>
<li class="building optician">
  <div class="buildinginfo"><div title="Oculista" class="buildingimg optician"
    onclick="ajaxHandlerCall('?action=CityScreen&function=build&buildingId=25&position=12');"></div></div>
  <div class="costs"><ul class="resources">
    <li class="wood" title="Material de construção"><span class="accesshint">Material de construção: </span>119</li>
    <li class="glass" title="Cristal"><span class="accesshint">Cristal: </span>1.250</li>
    <li class="time" title="Tempo de construção">7m 56s</li>
  </ul></div>
  <a class="button build" href="?action=CityScreen&function=build&position=12" data-building="25">Construir</a>
</li>
</ul>
"""


def _vision(wood=72):
    return {
        "id": "500", "name": "Vision", "wood": wood, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0,
        "resource_production_per_hour": 0, "tradegood": 1, "tradegood_production_per_hour": 0,
        "buildings": [
            {"building": "townHall", "level": 1, "position": 0, "is_upgrading": False},
            {"building": "empty", "level": 0, "position": 12, "type": "land", "is_upgrading": False},
        ],
    }


def _job(extra=None):
    return {
        "job_id": "job-n220", "account_id": "acc-1", "game_account_id": "ga-1",
        "inputs": {
            "queue_strategy": "fifo",
            "construction_plan_steps": [{
                "index": 4, "city_id": "500", "city_name": "Vision", "building_id": "optician",
                "building_name": "Oculista", "mode": "new", "slot_types": ["land"], "preferred_position": 12,
                "target_level": 1,
                "level_rows": [{"level": 1, "adjusted_seconds": 476, "base_seconds": 220,
                                "costs": {"wood": 67, "wine": 0, "marble": 0, "glas": 0, "sulfur": 0}}],
            }],
            **(extra or {}),
        },
    }


class BuildGroundCostTests(unittest.TestCase):
    def test_reads_the_wanted_building_cost_only(self):
        costs = CITY_MODULE._parse_build_ground_costs(BUILDING_GROUND_HTML, "optician")
        self.assertEqual(costs, {"wood": 119, "wine": 0, "marble": 0, "glas": 1250, "sulfur": 0})
        self.assertEqual(CITY_MODULE._parse_build_ground_costs(BUILDING_GROUND_HTML, "warehouse")["wood"], 160)
        self.assertIsNone(CITY_MODULE._parse_build_ground_costs(BUILDING_GROUND_HTML, "wall"))
        self.assertIsNone(CITY_MODULE._parse_build_ground_costs("", "optician"))

    def test_new_step_live_cost_comes_from_the_empty_slot_screen(self):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        calls = []
        payload = json.dumps([["updateGlobalData", {"actionRequest": "fresh"}],
                              ["changeView", ["buildingGround", BUILDING_GROUND_HTML]]])

        def _request(method, url, params=None, **_kwargs):
            calls.append(dict(params or {}))
            return pytypes.SimpleNamespace(text=payload, status_code=200)

        client = pytypes.SimpleNamespace(_server_url="https://example.invalid/index.php", _action_request="tok",
                                         _request=_request)
        pending = _job()["inputs"]["construction_plan_steps"][0]
        costs = runner._get_live_step_costs(client=client, city_id=500, pending=pending, current_level=0,
                                            position=None, city=_vision())
        self.assertEqual(costs["wood"], 119)
        self.assertEqual((calls[-1]["view"], calls[-1]["position"]), ("buildingGround", "12"))
        self.assertEqual(client._action_request, "fresh")


class UnconfirmedBackoffTests(unittest.TestCase):
    def _runner(self, snapshot, builds):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        runner.hub = pytypes.SimpleNamespace()
        runner.sessions = None
        runner._config_cache = None
        runner._config_cache_at = 0.0
        runner.logs = []
        runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
        runner._get_snapshot = lambda jid, ga_id: snapshot
        runner._ensure_status_refresh = lambda *args: None
        runner.get_snapshot_stale_seconds = lambda: 999999
        runner.get_agent_config = lambda: {}
        runner._get_open_construction_support = lambda jid: {}
        runner.resolve_credentials = lambda *_args, **_kwargs: {}
        runner.is_snapshot_stale = lambda *_args, **_kwargs: False
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner._collect_live_step_debug = lambda **kwargs: {
            "live_stock": {"wood": 72, "wine": 0, "marble": 0, "glas": 0, "sulfur": 0},
            "live_costs": None, "live_missing": None, "button_state": None, "debug_line": ""}
        client = pytypes.SimpleNamespace(
            _server_url="https://example.invalid/index.php", _action_request="tok",
            _request=lambda *args, **kwargs: pytypes.SimpleNamespace(text='actionRequest:"tok"', status_code=200),
            build=lambda **kwargs: builds.append(kwargs) or {"errors": [], "notifications": []},
        )
        runner._get_client = lambda _job: (client, "ga-1")
        return runner

    def _run(self, runner, job):
        original = CITY_MODULE._confirm_building_state

        def _never(*_args, **_kwargs):
            raise RuntimeError("build_not_confirmed:Vision:Oculista:pos=12:state={}")

        CITY_MODULE._confirm_building_state = _never
        try:
            return runner.execute(job)
        finally:
            CITY_MODULE._confirm_building_state = original

    def test_ignored_build_waits_longer_and_is_not_retried_meanwhile(self):
        snapshot = {"updated_at": datetime.now(timezone.utc).isoformat(), "cities": [_vision(wood=100)]}
        builds = []
        result = self._run(self._runner(snapshot, builds), _job())
        self.assertEqual(len(builds), 1)
        state = result.reschedule_inputs["unconfirmed_steps"]["500:4"]
        self.assertEqual(state["n"], 1)
        self.assertEqual(result.reschedule_seconds, 300)

        # third refusal in a row: 45 min
        result = self._run(self._runner(snapshot, builds),
                           _job({"unconfirmed_steps": {"500:4": {"n": 2, "until": int(time.time()) - 1}}}))
        self.assertEqual(result.reschedule_inputs["unconfirmed_steps"]["500:4"]["n"], 3)
        self.assertEqual(result.reschedule_seconds, 45 * 60)

        # while the pause lasts, the step is not sent again
        builds.clear()
        result = self._run(self._runner(snapshot, builds),
                           _job({"unconfirmed_steps": {"500:4": {"n": 3, "until": int(time.time()) + 1800}}}))
        self.assertEqual(builds, [])
        self.assertTrue(any(w.get("status") == "waiting_unconfirmed_backoff" for w in result.data["waiting"]))
        self.assertGreater(result.reschedule_seconds, 1700)

    def test_pause_never_grows_past_six_hours(self):
        snapshot = {"updated_at": datetime.now(timezone.utc).isoformat(), "cities": [_vision(wood=100)]}
        result = self._run(self._runner(snapshot, []),
                           _job({"unconfirmed_steps": {"500:4": {"n": 9, "until": 0}}}))
        self.assertEqual(result.reschedule_seconds, 6 * 3600)


if __name__ == "__main__":
    unittest.main()
