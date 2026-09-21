import importlib.util
import sys
import types
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_resources_runner_module():
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
            for key, value in kwargs.items():
                setattr(self, key, value)

    base_mod.BaseRunner = BaseRunner
    base_mod.RunnerResult = RunnerResult
    runners_pkg.base = base_mod

    services_pkg = types.ModuleType("services")
    island_donation = types.ModuleType("services.island_donation")
    island_donation.fetch_city_context = lambda *_args, **_kwargs: {}
    resource_transport = types.ModuleType("services.resource_transport")
    resource_transport.split_shipment = lambda *_args, **_kwargs: []
    resource_transport.RESOURCE_ORDER = ("wood", "wine", "marble", "crystal", "sulfur")
    resource_transport.change_current_city = lambda *_args, **_kwargs: None
    resource_transport.confirm_arrival = lambda *_args, **_kwargs: {}
    resource_transport.estimate_next_ship_availability = lambda *_args, **_kwargs: {"wait_seconds": 0, "chosen": None, "entries": [], "fallback_used": False}
    resource_transport.estimate_next_ship_availability_seconds = lambda *_args, **_kwargs: 0
    resource_transport.prepare_transport = lambda *_args, **_kwargs: None
    resource_transport.submit_transport = lambda *_args, **_kwargs: {"ok": True, "feedbacks": []}
    services_pkg.island_donation = island_donation
    services_pkg.resource_transport = resource_transport

    sessions_pkg = types.ModuleType("sessions")
    game_session_service = types.ModuleType("sessions.game_session_service")

    class LoginCooldownActive(Exception):
        pass

    game_session_service.LoginCooldownActive = LoginCooldownActive
    sessions_pkg.game_session_service = game_session_service

    sys.modules.update(
        {
            "core": core_pkg,
            "core.runner_registry": runner_registry,
            "game_client": game_client_pkg,
            "game_client.constants": game_constants,
            "runners": runners_pkg,
            "runners.base": base_mod,
            "services": services_pkg,
            "services.island_donation": island_donation,
            "services.resource_transport": resource_transport,
            "sessions": sessions_pkg,
            "sessions.game_session_service": game_session_service,
        }
    )

    spec = importlib.util.spec_from_file_location("resources_runner_under_test", ROOT / "runners" / "resources.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


RESOURCES_MODULE = _load_resources_runner_module()
DistributeResourcesRunner = RESOURCES_MODULE.DistributeResourcesRunner
SendResourcesRunner = RESOURCES_MODULE.SendResourcesRunner


class ResourceRunnerHelperTests(unittest.TestCase):
    def setUp(self):
        self.runner = DistributeResourcesRunner()
        self.runner.log = lambda *_args, **_kwargs: None

    def test_subtract_active_transport_support_uses_route_modal_and_amounts(self):
        entries = [
            {
                "from_city": "10",
                "to_city": "20",
                "use_freighters": True,
                "resources": {"wood": 200000, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0},
            }
        ]
        remaining, covered = self.runner._subtract_active_transport_support(
            entries,
            from_city="10",
            to_city="20",
            use_freighters=True,
            resources={"wood": 50000},
        )
        self.assertEqual({}, remaining)
        self.assertEqual({"wood": 50000}, covered)
        self.assertEqual(150000, entries[0]["resources"]["wood"])

        remaining, covered = self.runner._subtract_active_transport_support(
            entries,
            from_city="10",
            to_city="20",
            use_freighters=True,
            resources={"wood": 200000},
        )
        self.assertEqual({"wood": 50000}, remaining)
        self.assertEqual({"wood": 150000}, covered)
        self.assertEqual(0, entries[0]["resources"]["wood"])

        remaining, covered = self.runner._subtract_active_transport_support(
            entries,
            from_city="10",
            to_city="20",
            use_freighters=False,
            resources={"wood": 50000},
        )
        self.assertEqual({"wood": 50000}, remaining)
        self.assertEqual({}, covered)

    def test_choose_next_transport_followup_delay_prefers_scheduled_for(self):
        soon = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
        later = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        delay = self.runner._choose_next_transport_followup_delay(
            [
                {"scheduled_for": later, "eta_total_seconds": 0},
                {"scheduled_for": soon, "eta_total_seconds": 999999},
            ]
        )
        self.assertIsNotNone(delay)
        self.assertLessEqual(delay, 45 * 60 + 5)
        self.assertGreaterEqual(delay, 44 * 60)

    def test_estimate_known_chain_ship_delay_uses_arrival_check_same_modal(self):
        send_runner = SendResourcesRunner()
        send_runner.log = lambda *_args, **_kwargs: None
        soon = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
        later = (datetime.now(timezone.utc) + timedelta(minutes=40)).isoformat()

        class HubStub:
            def get_transport_support(self, _job_id):
                return {
                    "entries": [
                        {
                            "job_id": "ignored-monitor",
                            "monitor_mode": "arrival_check",
                            "use_freighters": False,
                            "scheduled_for": later,
                            "from_city": "11",
                            "to_city": "22",
                        },
                        {
                            "job_id": "chosen-monitor",
                            "monitor_mode": "arrival_check",
                            "use_freighters": True,
                            "scheduled_for": soon,
                            "from_city": "33",
                            "to_city": "44",
                        },
                    ]
                }

        send_runner.hub = HubStub()
        delay, entry = send_runner._estimate_known_chain_ship_delay(job_id="root-1", use_freighters=True)
        self.assertIsNotNone(delay)
        self.assertLessEqual(delay, 12 * 60 + 5)
        self.assertGreaterEqual(delay, 11 * 60)
        self.assertEqual("chosen-monitor", entry["job_id"])

    def test_dispatch_transport_skips_tiny_partial_below_minimum(self):
        send_runner = SendResourcesRunner()
        logs = []
        send_runner.log = lambda *_args: logs.append(_args[2] if len(_args) > 2 else "")
        send_runner.resolve_credentials = lambda *_args, **_kwargs: {"server": "s78-br"}
        send_runner.get_or_login_game_client = lambda *_args, **_kwargs: object()
        saved = []
        send_runner.save_game_client = lambda *_args, **_kwargs: saved.append(True)
        send_runner.hub = SimpleNamespace(spawn_job=lambda *a, **k: None)

        plan = SimpleNamespace(
            origin=SimpleNamespace(city_name="VC1", available_resources={"wood": 0, "wine": 4, "marble": 0, "crystal": 0, "sulfur": 0}),
            destination=SimpleNamespace(city_name="CP1"),
            total_requested=4524,
            total_dispatched=4,
            free_transporters=160,
            effective_ship_capacity=500,
            capacity_percent=100,
            ship_capacity=500,
            eta={"queue_seconds": 0, "loading_seconds": 0, "travel_seconds": 1698, "total_seconds": 1698},
            requested={"wood": 0, "wine": 4524, "marble": 0, "crystal": 0, "sulfur": 0},
            dispatched={"wood": 0, "wine": 4, "marble": 0, "crystal": 0, "sulfur": 0},
            remaining={"wood": 0, "wine": 4520, "marble": 0, "crystal": 0, "sulfur": 0},
        )
        RESOURCES_MODULE.prepare_transport = lambda *_args, **_kwargs: plan

        submit_called = {"value": False}
        RESOURCES_MODULE.submit_transport = lambda *_args, **_kwargs: submit_called.__setitem__("value", True) or {"ok": True, "feedbacks": []}

        result = send_runner._dispatch_transport(
            {"job_id": "job-1", "account_id": "acc-1", "game_account_id": "ga-1"},
            {"from_city": "37436", "to_city": "37435", "wine": 4524, "min_dispatch_total": 5000},
        )

        self.assertTrue(result.success)
        self.assertEqual("dispatch_below_minimum", result.data["status"])
        self.assertFalse(submit_called["value"])
        self.assertTrue(saved)
        self.assertTrue(any("Despachavel abaixo do minimo util" in msg for msg in logs))


if __name__ == "__main__":
    unittest.main()
