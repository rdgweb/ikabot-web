import importlib.util
import sys
import types
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone


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

    def test_route_chunk_is_active_requires_same_route_modal_and_resource_signature(self):
        entries = [
            {
                "from_city": "10",
                "to_city": "20",
                "use_freighters": True,
                "resources": {"wood": 200000, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0},
            }
        ]
        self.assertTrue(
            self.runner._route_chunk_is_active(
                entries,
                from_city="10",
                to_city="20",
                use_freighters=True,
                resources={"wood": 50000},
            )
        )
        self.assertFalse(
            self.runner._route_chunk_is_active(
                entries,
                from_city="10",
                to_city="20",
                use_freighters=False,
                resources={"wood": 50000},
            )
        )
        self.assertFalse(
            self.runner._route_chunk_is_active(
                entries,
                from_city="10",
                to_city="20",
                use_freighters=True,
                resources={"marble": 50000},
            )
        )

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


if __name__ == "__main__":
    unittest.main()
