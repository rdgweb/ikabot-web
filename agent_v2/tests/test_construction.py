import importlib.util
import sys
import types as pytypes
import types
import unittest
from pathlib import Path
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]


def _load_city_runner_module():
    core_pkg = types.ModuleType("core")
    runner_registry = types.ModuleType("core.runner_registry")
    runner_registry.register_runner = lambda _code: (lambda cls: cls)
    core_pkg.runner_registry = runner_registry

    game_client_pkg = types.ModuleType("game_client")
    game_constants = types.ModuleType("game_client.constants")
    game_constants.GAME_AJAX_HEADERS = {}
    game_client_pkg.constants = game_constants

    runners_pkg = types.ModuleType("runners")
    base_mod = types.ModuleType("runners.base")

    class BaseRunner:
        pass

    class RunnerResult:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            for key, value in kwargs.items():
                setattr(self, key, value)

    base_mod.BaseRunner = BaseRunner
    base_mod.RunnerResult = RunnerResult
    runners_pkg.base = base_mod

    services_pkg = types.ModuleType("services")
    resource_transport = types.ModuleType("services.resource_transport")
    resource_transport.estimate_incoming_transport_wait_seconds = lambda *_args, **_kwargs: 0
    resource_transport.change_current_city = lambda *_args, **_kwargs: None
    island_donation = types.ModuleType("services.island_donation")
    island_donation.extract_city_data = lambda _html: {}
    services_pkg.resource_transport = resource_transport
    services_pkg.island_donation = island_donation

    sys.modules.update(
        {
            "core": core_pkg,
            "core.runner_registry": runner_registry,
            "game_client": game_client_pkg,
            "game_client.constants": game_constants,
            "runners": runners_pkg,
            "runners.base": base_mod,
            "services": services_pkg,
            "services.resource_transport": resource_transport,
            "services.island_donation": island_donation,
        }
    )

    spec = importlib.util.spec_from_file_location("city_runner_under_test", ROOT / "runners" / "city.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


CITY_MODULE = _load_city_runner_module()
ConstructionPlanRunner = CITY_MODULE.ConstructionPlanRunner


class ConstructionRouteTests(unittest.TestCase):
    def test_upgrade_action_constant_points_to_new_endpoint(self):
        constants_source = (ROOT / "game_client" / "constants.py").read_text(encoding="utf-8")
        self.assertIn('BUILD = "BuildNewBuilding"', constants_source)
        self.assertIn('UPGRADE_BUILDING = "UpgradeExistingBuilding"', constants_source)


class ConstructionQueueStrategyTests(unittest.TestCase):
    def _city(self):
        return {
            "id": "1",
            "name": "Atenas",
            "wood": 10_000_000,
            "wine": 0,
            "marble": 0,
            "crystal": 0,
            "sulfur": 0,
            "resource_production_per_hour": 0,
            "tradegood_production_per_hour": 0,
            "buildings": [
                {"building": "academy", "level": 1, "position": 3},
                {"building": "warehouse", "level": 1, "position": 4},
            ],
        }

    def _step(self, *, index, building_id, adjusted_seconds, base_seconds, wood_cost):
        return {
            "index": index,
            "city_id": "1",
            "city_name": "Atenas",
            "building_id": building_id,
            "building_name": building_id,
            "mode": "upgrade",
            "target_level": 2,
            "level_rows": [
                {
                    "level": 2,
                    "adjusted_seconds": adjusted_seconds,
                    "base_seconds": base_seconds,
                    "costs": {
                        "wood": wood_cost,
                        "wine": 0,
                        "marble": 0,
                        "glas": 0,
                        "sulfur": 0,
                    },
                }
            ],
        }

    def test_fifo_keeps_plan_order(self):
        city = self._city()
        first = self._step(index=1, building_id="academy", adjusted_seconds=900, base_seconds=900, wood_cost=10)
        second = self._step(index=2, building_id="warehouse", adjusted_seconds=100, base_seconds=100, wood_cost=1000000)

        selected = ConstructionPlanRunner._pick_pending_steps([city], [first, second], "fifo")

        self.assertEqual(selected[0]["building_id"], "academy")

    def test_eta_first_prefers_shorter_eta(self):
        city = self._city()
        slow = self._step(index=1, building_id="academy", adjusted_seconds=900, base_seconds=200, wood_cost=10)
        fast = self._step(index=2, building_id="warehouse", adjusted_seconds=100, base_seconds=100, wood_cost=1000000)

        selected = ConstructionPlanRunner._pick_pending_steps([city], [slow, fast], "eta_first")

        self.assertEqual(selected[0]["building_id"], "warehouse")

    def test_smart_balances_time_and_cost(self):
        city = self._city()
        balanced = self._step(index=1, building_id="academy", adjusted_seconds=900, base_seconds=200, wood_cost=10)
        expensive = self._step(index=2, building_id="warehouse", adjusted_seconds=100, base_seconds=100, wood_cost=1000000)

        selected = ConstructionPlanRunner._pick_pending_steps([city], [balanced, expensive], "smart")

        self.assertEqual(selected[0]["building_id"], "academy")

    def test_position_specific_upgrade_uses_selected_building_instance(self):
        city = {
            "id": "39274",
            "name": "lll1lll",
            "wood": 54830,
            "wine": 4810,
            "marble": 4075,
            "crystal": 800,
            "sulfur": 33196,
            "resource_production_per_hour": 120,
            "tradegood_production_per_hour": 67,
            "buildings": [
                {"building": "warehouse", "level": 3, "position": 5},
                {"building": "warehouse", "level": 3, "position": 6},
                {"building": "warehouse", "level": 2, "position": 7},
                {"building": "warehouse", "level": 3, "position": 15},
                {"building": "warehouse", "level": 11, "position": 23},
            ],
        }
        step = {
            "index": 1,
            "city_id": "39274",
            "city_name": "lll1lll",
            "building_id": "warehouse",
            "building_name": "warehouse",
            "building_position": 23,
            "mode": "upgrade",
            "target_level": 12,
            "level_rows": [
                {
                    "level": 12,
                    "adjusted_seconds": 5724,
                    "base_seconds": 1908,
                    "costs": {"wood": 1629, "wine": 0, "marble": 1028, "glas": 0, "sulfur": 0},
                }
            ],
        }

        selected = ConstructionPlanRunner._pick_pending_steps([city], [step], "fifo")

        self.assertEqual(selected[0]["building_position"], 23)
        self.assertEqual(selected[0]["current_level"], 11)
        self.assertEqual(selected[0]["next_level"], 12)


class ConstructionRunnerExecutionTests(unittest.TestCase):
    def _runner_with_snapshot(self, snapshot):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        runner.hub = pytypes.SimpleNamespace()
        runner.sessions = None
        runner._config_cache = None
        runner._config_cache_at = 0.0
        runner_logs = []
        refresh_calls = []
        runner.log = lambda jid, level, msg: runner_logs.append((jid, level, msg))
        runner._get_snapshot = lambda jid, ga_id: snapshot
        runner._ensure_status_refresh = lambda jid: refresh_calls.append(jid)
        runner.get_snapshot_stale_seconds = lambda: 999999
        runner.get_agent_config = lambda: {}
        runner._get_open_construction_support = lambda jid: {}
        runner._runner_logs = runner_logs
        runner._refresh_calls = refresh_calls
        return runner

    def test_execute_position_specific_upgrade_waits_on_real_target_level(self):
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cities": [
                {
                    "id": "39274",
                    "name": "lll1lll",
                    "wood": 1000,
                    "wine": 4810,
                    "marble": 100,
                    "crystal": 800,
                    "sulfur": 33196,
                    "resource_production_per_hour": 120,
                    "tradegood": 2,
                    "tradegood_production_per_hour": 67,
                    "buildings": [
                        {"building": "warehouse", "level": 3, "position": 5},
                        {"building": "warehouse", "level": 11, "position": 23},
                    ],
                }
            ],
        }
        runner = self._runner_with_snapshot(snapshot)
        job = {
            "job_id": "job-1",
            "account_id": "acc-1",
            "game_account_id": "ga-1",
            "inputs": {
                "queue_strategy": "fifo",
                "auto_transport": False,
                "construction_plan_steps": [
                    {
                        "index": 1,
                        "city_id": "39274",
                        "city_name": "lll1lll",
                        "building_id": "warehouse",
                        "building_name": "Armazem",
                        "building_position": 23,
                        "mode": "upgrade",
                        "target_level": 12,
                        "level_rows": [
                            {
                                "level": 12,
                                "adjusted_seconds": 5724,
                                "base_seconds": 1908,
                                "costs": {"wood": 1629, "wine": 0, "marble": 1028, "glas": 0, "sulfur": 0},
                            }
                        ],
                    }
                ],
            },
        }

        result = runner.execute(job)

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "waiting_parallel")
        self.assertEqual(result.data["waiting"][0]["status"], "waiting_resources")


if __name__ == "__main__":
    unittest.main()
