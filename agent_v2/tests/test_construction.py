import importlib.util
import sys
import types
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
