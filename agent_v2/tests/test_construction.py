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

    def test_pick_pending_step_for_city_skips_completed_and_promotes_next(self):
        city = self._city()
        first = self._step(index=1, building_id="academy", adjusted_seconds=900, base_seconds=900, wood_cost=10)
        first["target_level"] = 1
        second = self._step(index=2, building_id="warehouse", adjusted_seconds=100, base_seconds=100, wood_cost=10)

        selected = ConstructionPlanRunner._pick_pending_step_for_city([city], [first, second], "1", "fifo", [])

        self.assertIsNotNone(selected)
        self.assertEqual(selected["building_id"], "warehouse")

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

    def test_new_mode_recognizes_existing_building_by_alias_and_skips_completed_step(self):
        city = {
            "id": "37440",
            "name": "MH4",
            "wood": 1000,
            "wine": 0,
            "marble": 1000,
            "crystal": 0,
            "sulfur": 0,
            "resource_production_per_hour": 0,
            "tradegood_production_per_hour": 0,
            "buildings": [
                {"building": "chronosForge", "level": 1, "position": 21, "is_upgrading": False},
            ],
        }
        step = {
            "index": 1,
            "city_id": "37440",
            "city_name": "MH4",
            "building_id": "chronos_forge",
            "building_name": "Forja de Chronos",
            "mode": "new",
            "preferred_position": 21,
            "target_level": 1,
            "level_rows": [
                {
                    "level": 1,
                    "adjusted_seconds": 594,
                    "base_seconds": 220,
                    "costs": {"wood": 25063, "wine": 0, "marble": 22929, "glas": 0, "sulfur": 0},
                }
            ],
        }

        selected = ConstructionPlanRunner._pick_pending_steps([city], [step], "fifo")

        self.assertEqual(selected, [])


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

    def test_started_parallel_uses_smallest_busy_wait_seen_in_cycle(self):
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cities": [
                {
                    "id": "1",
                    "name": "BusyTown",
                    "wood": 10000,
                    "wine": 0,
                    "marble": 10000,
                    "crystal": 0,
                    "sulfur": 0,
                    "resource_production_per_hour": 0,
                    "tradegood_production_per_hour": 0,
                    "buildings": [
                        {"building": "academy", "level": 1, "position": 3, "is_upgrading": True, "construction_end_at": int(datetime.now(timezone.utc).timestamp()) + 300},
                    ],
                },
                {
                    "id": "2",
                    "name": "FreeTown",
                    "wood": 10000,
                    "wine": 0,
                    "marble": 10000,
                    "crystal": 0,
                    "sulfur": 0,
                    "resource_production_per_hour": 0,
                    "tradegood_production_per_hour": 0,
                    "buildings": [
                        {"building": "warehouse", "level": 1, "position": 4, "is_upgrading": False},
                    ],
                },
            ],
        }
        runner = self._runner_with_snapshot(snapshot)
        runner._get_client = lambda job: (pytypes.SimpleNamespace(), "ga-1")
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner._reconcile_snapshot_building_state = lambda **_kwargs: None
        runner._collect_live_step_debug = lambda **_kwargs: {
            "live_stock": {"wood": 10000, "wine": 0, "marble": 10000, "glas": 0, "sulfur": 0},
            "live_costs": None,
            "live_missing": None,
            "button_state": {},
            "debug_line": "",
        }
        CITY_MODULE._get_active_constructions_via_advisor = lambda *_args, **_kwargs: {
            "1": {"city_id": "1", "building_name": "academy", "end_time": int(datetime.now(timezone.utc).timestamp()) + 300}
        }
        CITY_MODULE._confirm_building_state = lambda *args, **kwargs: {"level": 1, "is_upgrading": True, "construction_end_at": int(datetime.now(timezone.utc).timestamp()) + 1800}
        runner._format_step_debug = lambda **_kwargs: "debug"
        class _Client:
            _server_url = "http://example"
            _action_request = "ar"
            def _request(self, *args, **kwargs):
                return pytypes.SimpleNamespace(text="", status_code=200)
            def upgrade(self, **kwargs):
                return {}
        client = _Client()
        runner._get_client = lambda job: (client, "ga-1")

        job = {
            "job_id": "job-2",
            "account_id": "acc-1",
            "game_account_id": "ga-1",
            "inputs": {
                "queue_strategy": "fifo",
                "auto_transport": False,
                "construction_plan_steps": [
                    {
                        "index": 1,
                        "city_id": "1",
                        "city_name": "BusyTown",
                        "building_id": "academy",
                        "building_name": "Academia",
                        "building_position": 3,
                        "mode": "upgrade",
                        "target_level": 2,
                        "level_rows": [{"level": 2, "adjusted_seconds": 1800, "base_seconds": 1800, "costs": {"wood": 1, "wine": 0, "marble": 1, "glas": 0, "sulfur": 0}}],
                    },
                    {
                        "index": 2,
                        "city_id": "2",
                        "city_name": "FreeTown",
                        "building_id": "warehouse",
                        "building_name": "Armazem",
                        "building_position": 4,
                        "mode": "upgrade",
                        "target_level": 2,
                        "level_rows": [{"level": 2, "adjusted_seconds": 1800, "base_seconds": 1800, "costs": {"wood": 1, "wine": 0, "marble": 1, "glas": 0, "sulfur": 0}}],
                    },
                ],
            },
        }

        result = runner.execute(job)

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "started_parallel")
        self.assertLessEqual(result.reschedule_seconds, 420)

    def test_busy_completion_below_target_does_not_skip_multilevel_step(self):
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cities": [
                {
                    "id": "1",
                    "name": "BusyTown",
                    "wood": 100000,
                    "wine": 0,
                    "marble": 100000,
                    "crystal": 0,
                    "sulfur": 0,
                    "resource_production_per_hour": 0,
                    "tradegood_production_per_hour": 0,
                    "buildings": [
                        {"building": "architect", "level": 31, "position": 21, "is_upgrading": True, "construction_end_at": int(datetime.now(timezone.utc).timestamp()) + 300},
                    ],
                },
            ],
        }
        runner = self._runner_with_snapshot(snapshot)
        runner.resolve_credentials = lambda *_args, **_kwargs: {}
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner._format_step_debug = lambda **_kwargs: "debug"
        runner._collect_live_step_debug = lambda **_kwargs: {
            "live_stock": {"wood": 100000, "wine": 0, "marble": 100000, "glas": 0, "sulfur": 0},
            "live_costs": None,
            "live_missing": {"wood": 0, "wine": 0, "marble": 0, "glas": 0, "sulfur": 0},
            "button_state": {"button_found": True, "button_enabled": True},
            "debug_line": "",
        }
        CITY_MODULE._get_active_constructions_via_advisor = lambda *_args, **_kwargs: {}
        CITY_MODULE._live_city_building_state = lambda *_args, **_kwargs: {
            "position": 21,
            "building": "architect",
            "level": 32,
            "is_upgrading": False,
        }
        original_confirm = CITY_MODULE._confirm_building_state
        CITY_MODULE._confirm_building_state = lambda *_args, **_kwargs: {
            "position": 21,
            "building": "architect",
            "level": 32,
            "is_upgrading": True,
            "construction_end_at": int(datetime.now(timezone.utc).timestamp()) + 600,
        }

        class _Client:
            _server_url = "http://example"
            _action_request = "ar"

            def _request(self, *args, **kwargs):
                return pytypes.SimpleNamespace(text="", status_code=200)

            def upgrade(self, **kwargs):
                return {}

        runner._get_client = lambda job: (_Client(), "ga-1")

        job = {
            "job_id": "job-busy-promote",
            "account_id": "acc-1",
            "game_account_id": "ga-1",
            "inputs": {
                "queue_strategy": "fifo",
                "auto_transport": False,
                "construction_plan_steps": [
                    {
                        "index": 1,
                        "city_id": "1",
                        "city_name": "BusyTown",
                        "building_id": "architect",
                        "building_name": "Arquiteto",
                        "building_position": 21,
                        "mode": "upgrade",
                        "target_level": 35,
                        "level_rows": [
                            {"level": 32, "adjusted_seconds": 100, "base_seconds": 100, "costs": {"wood": 1, "wine": 0, "marble": 1, "glas": 0, "sulfur": 0}},
                            {"level": 33, "adjusted_seconds": 100, "base_seconds": 100, "costs": {"wood": 1, "wine": 0, "marble": 1, "glas": 0, "sulfur": 0}},
                        ],
                    },
                ],
            },
        }

        try:
            result = runner.execute(job)
        finally:
            CITY_MODULE._confirm_building_state = original_confirm

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "started_parallel")
        self.assertEqual(result.data["started"][0]["building_id"], "architect")
        self.assertNotIn("skipped_step_indices", result.kwargs["reschedule_inputs"])

    def test_handle_missing_resources_requests_internal_market_for_target_city(self):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        logs = []
        calls = []
        runner.log = lambda jid, level, msg: logs.append((jid, level, msg))
        runner.hub = pytypes.SimpleNamespace(
            create_market_order=lambda **kwargs: (calls.append(kwargs) or {"ok": True, "order_id": "ord-1"})
        )
        runner.resolve_credentials = lambda *_args, **_kwargs: None
        runner.get_or_login_game_client = lambda *_args, **_kwargs: None
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner._estimate_local_wait_seconds = lambda *_args, **_kwargs: 0
        runner._spawn_transport_cover = lambda **_kwargs: False

        city = {
            "id": "39274",
            "name": "Mercado",
            "wood": 0,
            "wine": 0,
            "marble": 0,
            "crystal": 0,
            "sulfur": 0,
            "buildings": [{"building": "branchOffice", "position": 7}],
        }
        pending = {
            "city_name": "Mercado",
            "building_name": "Templo",
            "next_level": 1,
            "level_rows": [
                {"level": 1, "costs": {"wood": 100, "wine": 0, "marble": 0, "glas": 50, "sulfur": 0}},
                {"level": 2, "costs": {"wood": 120, "wine": 0, "marble": 0, "glas": 75, "sulfur": 0}},
            ],
        }
        job = {
            "job_id": "job-1",
            "account_id": "acc-1",
            "game_account_id": "ga-1",
            "inputs": {"auto_transport": True, "auto_market_buy": True},
        }

        wait_seconds, transport_spawned, reschedule_inputs, should_skip_now = runner._handle_missing_resources(
            job=job,
            pending=pending,
            cities=[city],
            city=city,
            missing={"wood": 0, "wine": 0, "marble": 0, "glas": 50, "sulfur": 0},
            support_by_city={},
        )

        self.assertEqual(wait_seconds, CITY_MODULE.TRANSPORT_RECHECK_SECONDS)
        self.assertFalse(transport_spawned)
        self.assertFalse(should_skip_now)
        self.assertIn("last_market_order_requested_at", reschedule_inputs)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["preferred_buyer_city_id"], 39274)
        self.assertEqual(calls[0]["target_city_id"], 39274)
        self.assertEqual(calls[0]["resource_idx"], 3)
        self.assertEqual(calls[0]["source_action_code"], 1002)

    def test_spawn_transport_cover_uses_live_missing_even_when_preview_hides_resource(self):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        logs = []
        spawned = []
        runner.log = lambda jid, level, msg: logs.append((jid, level, msg))
        runner.hub = pytypes.SimpleNamespace(
            spawn_job=lambda source_job_id, action_code, inputs: spawned.append(
                {"source_job_id": source_job_id, "action_code": action_code, "inputs": inputs}
            )
        )

        target_city = {
            "id": "39267",
            "name": "Okolnir",
            "wood": 400000,
            "wine": 100000,
            "marble": 500000,
            "crystal": 0,
            "sulfur": 4000,
            "buildings": [],
        }
        donor_city = {
            "id": "40000",
            "name": "Donor",
            "wood": 10000,
            "wine": 10000,
            "marble": 10000,
            "crystal": 7000,
            "sulfur": 10000,
            "buildings": [],
        }
        pending = {
            "city_name": "Okolnir",
            "building_name": "Esconderijo",
            "next_level": 22,
            "level_rows": [
                {"level": 22, "costs": {"wood": 6363, "wine": 0, "marble": 2718, "glas": 0, "sulfur": 0}},
                {"level": 23, "costs": {"wood": 7000, "wine": 0, "marble": 3000, "glas": 0, "sulfur": 0}},
            ],
        }

        created = runner._spawn_transport_cover(
            job_id="job-transport",
            cities=[target_city, donor_city],
            target_city=target_city,
            pending=pending,
            missing={"wood": 0, "wine": 0, "marble": 0, "glas": 54, "sulfur": 0},
            support_by_city={},
        )

        self.assertTrue(created)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0]["action_code"], 2)
        self.assertEqual(spawned[0]["inputs"]["to_city"], "39267")
        self.assertEqual(spawned[0]["inputs"]["crystal"], 54)

    def test_execute_promotes_next_step_when_live_missing_has_no_eta(self):
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cities": [
                {
                    "id": "39267",
                    "name": "Okolnir",
                    "wood": 414603,
                    "wine": 101122,
                    "marble": 500149,
                    "crystal": 0,
                    "sulfur": 4794,
                    "resource_production_per_hour": 680,
                    "tradegood": 2,
                    "tradegood_production_per_hour": 599,
                    "buildings": [
                        {"building": "safehouse", "level": 21, "position": 19, "is_upgrading": False},
                        {"building": "architect", "level": 31, "position": 22, "is_upgrading": False},
                    ],
                }
            ],
        }
        runner = self._runner_with_snapshot(snapshot)
        runner.resolve_credentials = lambda *_args, **_kwargs: {}
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner._handle_missing_resources = lambda **kwargs: (1800, False, None, True)
        fake_client = pytypes.SimpleNamespace(
            _server_url="https://example.invalid/index.php",
            _action_request="tok",
            _request=lambda *args, **kwargs: pytypes.SimpleNamespace(text='actionRequest:"tok"', status_code=200),
            upgrade=lambda **kwargs: {"errors": [], "notifications": []},
        )
        runner._get_client = lambda _job: (fake_client, "ga-1")

        def _live_debug(**kwargs):
            pending = kwargs["pending"]
            if pending["building_id"] == "hideout":
                return {
                    "live_stock": {"wood": 414603, "wine": 101122, "marble": 500149, "glas": 0, "sulfur": 4794},
                    "live_costs": {"wood": 6861, "wine": 0, "marble": 2669, "glas": 54, "sulfur": 0},
                    "live_missing": {"wood": 0, "wine": 0, "marble": 0, "glas": 54, "sulfur": 0},
                    "button_state": {"button_found": True, "button_enabled": True},
                    "debug_line": "faltando vidro=54",
                }
            return {
                "live_stock": {"wood": 414603, "wine": 101122, "marble": 500149, "glas": 0, "sulfur": 4794},
                "live_costs": None,
                "live_missing": {"wood": 0, "wine": 0, "marble": 0, "glas": 0, "sulfur": 0},
                "button_state": {"button_found": True, "button_enabled": True},
                "debug_line": "",
            }

        runner._collect_live_step_debug = _live_debug
        original_confirm = CITY_MODULE._confirm_building_state
        CITY_MODULE._confirm_building_state = lambda *_args, **_kwargs: {
            "position": 22,
            "building": "architect",
            "level": 31,
            "is_upgrading": True,
            "construction_end_at": int(datetime.now(timezone.utc).timestamp()) + 900,
        }
        try:
            job = {
                "job_id": "job-promote-city",
                "account_id": "acc-1",
                "game_account_id": "ga-1",
                "inputs": {
                    "queue_strategy": "fifo",
                    "auto_transport": True,
                    "construction_plan_steps": [
                        {
                            "index": 27,
                            "city_id": "39267",
                            "city_name": "Okolnir",
                            "building_id": "hideout",
                            "building_name": "Esconderijo",
                            "building_position": 19,
                            "mode": "upgrade",
                            "target_level": 35,
                            "level_rows": [
                                {
                                    "level": 22,
                                    "adjusted_seconds": 14516,
                                    "base_seconds": 14516,
                                    "costs": {"wood": 6363, "wine": 0, "marble": 2718, "glas": 0, "sulfur": 0},
                                }
                            ],
                        },
                        {
                            "index": 28,
                            "city_id": "39267",
                            "city_name": "Okolnir",
                            "building_id": "architect",
                            "building_name": "Escritorio do Arquiteto",
                            "building_position": 22,
                            "mode": "upgrade",
                            "target_level": 35,
                            "level_rows": [
                                {
                                    "level": 32,
                                    "adjusted_seconds": 10161,
                                    "base_seconds": 10161,
                                    "costs": {"wood": 33494, "wine": 0, "marble": 15484, "glas": 0, "sulfur": 0},
                                }
                            ],
                        },
                    ],
                },
            }

            result = runner.execute(job)
        finally:
            CITY_MODULE._confirm_building_state = original_confirm

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "started_parallel")
        self.assertEqual(len(result.data["started"]), 1)
        self.assertEqual(result.data["started"][0]["building_id"], "architect")
        self.assertTrue(
            any(wait.get("building_id") == "hideout" and wait.get("status") == "waiting_real_cost_resources" for wait in result.data["waiting"])
        )

    def test_confirm_building_state_raises_missing_resources_when_feedback_says_so(self):
        CITY_MODULE.time.sleep = lambda *_args, **_kwargs: None
        CITY_MODULE._live_city_building_state = lambda *_args, **_kwargs: {
            "position": 10,
            "building": "empty",
            "level": 0,
            "is_upgrading": False,
        }

        with self.assertRaises(RuntimeError) as ctx:
            CITY_MODULE._confirm_building_state(
                object(),
                city_id=1,
                position=10,
                next_level=1,
                building_name="Torre do Alquimista",
                city_name="lll3lll",
                expect_build=True,
                action_feedback="Nao ha recursos suficientes para esta acao",
                attempts=1,
            )

        self.assertIn("missing_resources:lll3lll:Torre do Alquimista:pos=10", str(ctx.exception))

    def test_execute_uses_live_costs_before_upgrade(self):
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cities": [
                {
                    "id": "500",
                    "name": "Polis",
                    "wood": 100,
                    "wine": 0,
                    "marble": 50,
                    "crystal": 0,
                    "sulfur": 0,
                    "resource_production_per_hour": 0,
                    "tradegood": 2,
                    "tradegood_production_per_hour": 0,
                    "buildings": [
                        {"building": "branchOffice", "level": 4, "position": 8, "is_upgrading": False},
                    ],
                }
            ],
        }
        runner = self._runner_with_snapshot(snapshot)
        runner.resolve_credentials = lambda *_args, **_kwargs: {}
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner._handle_missing_resources = lambda **kwargs: (321, False, None, False)
        runner._resolve_step_state = ConstructionPlanRunner._resolve_step_state
        fake_client = pytypes.SimpleNamespace()
        runner._get_client = lambda _job: (fake_client, "ga-1")
        original_live_stock = CITY_MODULE._live_city_stock_from_game
        CITY_MODULE._live_city_stock_from_game = lambda *_args, **_kwargs: {
            "wood": 100,
            "wine": 0,
            "marble": 50,
            "glas": 0,
            "sulfur": 0,
        }
        runner._get_live_step_costs = lambda **_kwargs: {
            "wood": 200,
            "wine": 0,
            "marble": 100,
            "glas": 0,
            "sulfur": 0,
        }

        job = {
            "job_id": "job-live-cost",
            "account_id": "acc-1",
            "game_account_id": "ga-1",
            "inputs": {
                "queue_strategy": "fifo",
                "auto_transport": False,
                "construction_plan_steps": [
                    {
                        "index": 1,
                        "city_id": "500",
                        "city_name": "Polis",
                        "building_id": "marketplace",
                        "building_name": "Mercado",
                        "building_position": 8,
                        "mode": "upgrade",
                        "target_level": 5,
                        "level_rows": [
                            {
                                "level": 5,
                                "adjusted_seconds": 300,
                                "base_seconds": 300,
                                "costs": {"wood": 10, "wine": 0, "marble": 10, "glas": 0, "sulfur": 0},
                            }
                        ],
                    }
                ],
            },
        }

        try:
            result = runner.execute(job)
        finally:
            CITY_MODULE._live_city_stock_from_game = original_live_stock

        self.assertTrue(result.success)
        self.assertEqual(result.kwargs["reschedule_seconds"], 321)
        self.assertEqual(result.data["waiting"][0]["status"], "waiting_real_cost_resources")
        self.assertEqual(result.data["waiting"][0]["estimated_costs"]["wood"], 10)
        self.assertEqual(result.data["waiting"][0]["live_costs"]["wood"], 200)
        self.assertEqual(result.data["waiting"][0]["live_stock"]["wood"], 100)
        self.assertEqual(result.data["waiting"][0]["missing"]["wood"], 100)
        self.assertTrue(
            any("estimado=" in msg and "custo_real=" in msg and "estoque_real=" in msg for _jid, _level, msg in runner._runner_logs)
        )

    def test_execute_reclassifies_upgrade_not_confirmed_when_live_debug_shows_missing_resources(self):
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cities": [
                {
                    "id": "39273",
                    "name": "Alfheim",
                    "wood": 284126,
                    "wine": 5836,
                    "marble": 122,
                    "crystal": 423852,
                    "sulfur": 8500,
                    "resource_production_per_hour": 0,
                    "tradegood": 3,
                    "tradegood_production_per_hour": 992,
                    "buildings": [
                        {"building": "safehouse", "level": 12, "position": 19, "is_upgrading": False},
                    ],
                }
            ],
        }
        runner = self._runner_with_snapshot(snapshot)
        runner.resolve_credentials = lambda *_args, **_kwargs: {}
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner._handle_missing_resources = lambda **kwargs: (654, False, None, False)
        fake_client = pytypes.SimpleNamespace(
            _server_url="https://example.invalid/index.php",
            _action_request="tok",
            _request=lambda *args, **kwargs: pytypes.SimpleNamespace(text='actionRequest:"tok"', status_code=200),
            upgrade=lambda **kwargs: {"errors": [], "notifications": []},
        )
        runner._get_client = lambda _job: (fake_client, "ga-1")
        costs_seq = iter(
            [
                {"wood": 100, "wine": 0, "marble": 100, "glas": 0, "sulfur": 0},
                {"wood": 1870, "wine": 0, "marble": 603, "glas": 0, "sulfur": 0},
            ]
        )
        runner._get_live_step_costs = lambda **_kwargs: next(costs_seq)
        runner._get_live_step_button_state = lambda **_kwargs: {"button_found": True, "button_enabled": True, "href": "?x=1"}
        original_live_stock = CITY_MODULE._live_city_stock_from_game
        CITY_MODULE._live_city_stock_from_game = lambda *_args, **_kwargs: {
            "wood": 284126,
            "wine": 5836,
            "marble": 122,
            "glas": 423852,
            "sulfur": 8500,
        }
        original_confirm = CITY_MODULE._confirm_building_state
        CITY_MODULE._confirm_building_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError(
                "upgrade_not_confirmed:Alfheim:Esconderijo:pos=19:state={'position': 19, 'building': 'safehouse', 'level': 12, 'is_upgrading': False}:target=13"
            )
        )
        try:
            job = {
                "job_id": "job-safehouse",
                "account_id": "acc-1",
                "game_account_id": "ga-1",
                "inputs": {
                    "queue_strategy": "fifo",
                    "auto_transport": False,
                    "construction_plan_steps": [
                        {
                            "index": 1,
                            "city_id": "39273",
                            "city_name": "Alfheim",
                            "building_id": "safehouse",
                            "building_name": "Esconderijo",
                            "building_position": 19,
                            "mode": "upgrade",
                            "target_level": 13,
                            "level_rows": [
                                {
                                    "level": 13,
                                    "adjusted_seconds": 6360,
                                    "base_seconds": 6360,
                                    "costs": {"wood": 100, "wine": 0, "marble": 100, "glas": 0, "sulfur": 0},
                                }
                            ],
                        }
                    ],
                },
            }

            result = runner.execute(job)
        finally:
            CITY_MODULE._confirm_building_state = original_confirm
            CITY_MODULE._live_city_stock_from_game = original_live_stock

        self.assertTrue(result.success)
        self.assertEqual(result.kwargs["reschedule_seconds"], 654)
        self.assertEqual(result.data["waiting"][0]["status"], "waiting_real_cost_resources")
        self.assertEqual(result.data["waiting"][0]["missing"]["marble"], 481)
        self.assertTrue(any("Upgrade rejeitado por recurso insuficiente" in msg for _jid, _level, msg in runner._runner_logs))


class ConstructionMarketInterventionTests(unittest.TestCase):
    def test_should_request_market_intervention_uses_threshold_by_reason(self):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        runner.get_system_setting_int = lambda key, default: {
            "construction_market_intervention_gold_eta_hours": 24,
            "construction_market_intervention_resource_eta_hours": 12,
        }.get(key, default)

        self.assertTrue(runner._should_request_market_intervention("market_gold_eta", 24 * 3600))
        self.assertFalse(runner._should_request_market_intervention("market_gold_eta", 23 * 3600))
        self.assertTrue(runner._should_request_market_intervention("donor_eta", 12 * 3600))
        self.assertFalse(runner._should_request_market_intervention("local_eta", 11 * 3600))

    def test_remaining_reserved_by_city_ignores_completed_steps(self):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        runner._resolve_step_state = lambda city, step: (13, None, None) if step["index"] == 1 else (7, None, None)

        cities = [
            {"id": "1", "name": "A"},
            {"id": "2", "name": "B"},
        ]
        plan_steps = [
            {
                "index": 1,
                "city_id": "1",
                "target_level": 13,
                "reserved_local": {"wood": 100, "wine": 0, "marble": 50, "glas": 0, "sulfur": 0},
            },
            {
                "index": 2,
                "city_id": "2",
                "target_level": 8,
                "reserved_local": {"wood": 200, "wine": 0, "marble": 75, "glas": 0, "sulfur": 0},
            },
        ]

        reserved = runner._remaining_reserved_by_city(cities=cities, plan_steps=plan_steps)

        self.assertNotIn("1", reserved)
        self.assertEqual(reserved["2"]["wood"], 200)
        self.assertEqual(reserved["2"]["marble"], 75)

    def test_handle_missing_resources_uses_pending_recheck_when_intervention_exists(self):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        logs = []
        runner.log = lambda jid, level, msg: logs.append((jid, level, msg))
        runner.resolve_credentials = lambda *_args, **_kwargs: None
        runner.get_or_login_game_client = lambda *_args, **_kwargs: None
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner._estimate_local_wait_seconds = lambda *_args, **_kwargs: 0
        runner._spawn_transport_cover = lambda **_kwargs: False
        runner._estimate_donor_wait_seconds = lambda **_kwargs: 0
        runner._try_cover_with_internal_market = lambda **_kwargs: (None, {"available_gold": 100000, "min_gold": 200000}, "buyer_below_min_gold")
        runner._estimate_market_gold_wait_seconds = lambda **_kwargs: 100000
        runner._maybe_request_market_intervention = lambda **_kwargs: {"request_id": "req-1", "status": "pending"}
        runner.get_system_setting_int = lambda key, default: 900 if key == "construction_market_intervention_pending_recheck_seconds" else default
        runner._get_snapshot = lambda *_args, **_kwargs: {"base_snapshot": {"gold": 100000}}

        city = {"id": "39274", "name": "Mercado", "wood": 0, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0, "buildings": []}
        pending = {
            "city_name": "Mercado",
            "building_name": "Templo",
            "next_level": 1,
            "level_rows": [{"level": 1, "costs": {"wood": 0, "wine": 0, "marble": 50, "glas": 0, "sulfur": 0}}],
        }
        job = {
            "job_id": "job-1",
            "account_id": "acc-1",
            "game_account_id": "ga-1",
            "inputs": {"auto_transport": True, "auto_market_buy": True},
        }

        wait_seconds, transport_spawned, reschedule_inputs, should_skip_now = runner._handle_missing_resources(
            job=job,
            pending=pending,
            cities=[city],
            city=city,
            missing={"wood": 0, "wine": 0, "marble": 50, "glas": 0, "sulfur": 0},
            support_by_city={},
        )

        self.assertEqual(wait_seconds, 900)
        self.assertFalse(transport_spawned)
        self.assertFalse(should_skip_now)
        self.assertEqual(reschedule_inputs["market_intervention_request_id"], "req-1")
        self.assertEqual(reschedule_inputs["market_intervention_status"], "pending")


class ConstructionLiveStockTests(unittest.TestCase):
    def test_live_city_stock_prefers_update_global_data_header_resources(self):
        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload
                self.text = ""

            def json(self):
                return self._payload

        class FakeClient:
            _server_url = "https://example.invalid/index.php"

            def _request(self, method, url, params=None, headers=None):
                if params == {"view": "updateGlobalData", "ajax": "1"}:
                    return FakeResponse(
                        [
                            [
                                "updateGlobalData",
                                {
                                    "headerData": {
                                        "currentResources": {
                                            "resource": 1515574,
                                            "1": 322043,
                                            "2": 186909,
                                            "3": 50650,
                                            "4": 777,
                                        }
                                    }
                                },
                            ]
                        ]
                    )
                raise AssertionError(f"unexpected params: {params}")

        stock = CITY_MODULE._live_city_stock_from_game(FakeClient(), 39271)

        self.assertEqual(
            stock,
            {
                "wood": 1515574,
                "wine": 322043,
                "marble": 186909,
                "glas": 50650,
                "sulfur": 777,
            },
        )

    def test_live_city_stock_switches_city_context_before_reading_header(self):
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload
                self.text = ""

            def json(self):
                return self._payload

        class FakeClient:
            _server_url = "https://example.invalid/index.php"

            def _request(self, method, url, params=None, headers=None):
                calls.append(("request", params))
                if params == {"view": "updateGlobalData", "ajax": "1"}:
                    return FakeResponse([["updateGlobalData", {"headerData": {"currentResources": {"resource": 1, "1": 2, "2": 3, "3": 4, "4": 5}}}]])
                raise AssertionError(f"unexpected params: {params}")

        original_change = CITY_MODULE.change_current_city
        CITY_MODULE.change_current_city = lambda client, city_id: calls.append(("change", city_id))
        try:
            stock = CITY_MODULE._live_city_stock_from_game(FakeClient(), 39271)
        finally:
            CITY_MODULE.change_current_city = original_change

        self.assertEqual(calls[0], ("change", 39271))
        self.assertEqual(stock["wood"], 1)


if __name__ == "__main__":
    unittest.main()
