import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_monitoring_module():
    core_pkg = types.ModuleType("core")
    runner_registry = types.ModuleType("core.runner_registry")
    runner_registry.register_runner = lambda _code: (lambda cls: cls)
    core_pkg.runner_registry = runner_registry

    game_client_pkg = types.ModuleType("game_client")
    parsers_pkg = types.ModuleType("game_client.parsers")
    html_parser = types.ModuleType("game_client.parsers.html_parser")

    class GamePageParser:
        def extract_action_request(self, _html):
            return ""

    html_parser.GamePageParser = GamePageParser
    parsers_pkg.html_parser = html_parser
    game_client_pkg.parsers = parsers_pkg

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
    resource_transport = types.ModuleType("services.resource_transport")
    resource_transport.estimate_incoming_transport_wait_seconds = lambda *_args, **_kwargs: None
    wine_tavern = types.ModuleType("services.wine_tavern")
    wine_tavern.find_tavern_position = lambda *_args, **_kwargs: None
    wine_tavern.find_townhall_position = lambda *_args, **_kwargs: None
    wine_tavern.open_tavern_page = lambda *_args, **_kwargs: None
    wine_tavern.open_townhall_page = lambda *_args, **_kwargs: None
    wine_tavern.set_tavern_service = lambda *_args, **_kwargs: None
    services_pkg.resource_transport = resource_transport
    services_pkg.wine_tavern = wine_tavern

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
            "game_client.parsers": parsers_pkg,
            "game_client.parsers.html_parser": html_parser,
            "runners": runners_pkg,
            "runners.base": base_mod,
            "services": services_pkg,
            "services.resource_transport": resource_transport,
            "services.wine_tavern": wine_tavern,
            "sessions": sessions_pkg,
            "sessions.game_session_service": game_session_service,
        }
    )

    spec = importlib.util.spec_from_file_location("alert_wine_runner_under_test", ROOT / "runners" / "monitoring.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


MONITORING_MODULE = _load_monitoring_module()
AlertWineRunner = MONITORING_MODULE.AlertWineRunner


class AlertWineRunnerTests(unittest.TestCase):
    def test_pending_planned_transport_aggregates_only_non_monitor_entries(self):
        runner = AlertWineRunner()
        runner.log = lambda *_args, **_kwargs: None

        class HubStub:
            def get_transport_support(self, _job_id):
                return {
                    "entries": [
                        {
                            "from_city": "37436",
                            "to_city": "37435",
                            "monitor_mode": "",
                            "resources": {"wine": 1200},
                        },
                        {
                            "from_city": "37436",
                            "to_city": "37435",
                            "monitor_mode": "arrival_check",
                            "resources": {"wine": 900},
                        },
                        {
                            "from_city": "37436",
                            "to_city": "37435",
                            "resources": {"wine": 300},
                        },
                        {
                            "from_city": "37437",
                            "to_city": "37436",
                            "resources": {"wine": 700},
                        },
                    ]
                }

        runner.hub = HubStub()
        pending = runner._pending_planned_transport_summary("job-1")
        self.assertEqual(1500, pending["by_target"]["37435"])
        self.assertEqual(700, pending["by_target"]["37436"])
        self.assertEqual(1500, pending["by_source"]["37436"])
        self.assertEqual(700, pending["by_source"]["37437"])
        self.assertNotIn("99999", pending["by_target"])

    def test_pick_donor_discounts_pending_outbound_from_same_source(self):
        runner = AlertWineRunner()
        city_infos = [
            {
                "id": "target",
                "wine": 1000,
                "raw": {"wine_consumption": 10, "wine": 1000},
                "net": -10,
            },
            {
                "id": "donor_a",
                "name": "A",
                "wine": 20000,
                "net": 0,
                "raw": {"wine_consumption": 0, "wine": 20000},
            },
            {
                "id": "donor_b",
                "name": "B",
                "wine": 12000,
                "net": 0,
                "raw": {"wine_consumption": 0, "wine": 12000},
            },
        ]

        donor, candidates = runner._pick_donor(
            city_infos,
            target_id="target",
            donor_reserve_hours=168,
            useful_transfer_min=5000,
            requested=6000,
            planned_by_source={"donor_a": 11000},
        )
        self.assertIsNotNone(donor)
        self.assertEqual("donor_b", donor["id"])
        self.assertTrue(candidates)


if __name__ == "__main__":
    unittest.main()
