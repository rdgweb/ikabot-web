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
        }
    )

    spec = importlib.util.spec_from_file_location("monitoring_runner_under_test", ROOT / "runners" / "monitoring.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


MONITORING_MODULE = _load_monitoring_module()
IslandMonitorRunner = MONITORING_MODULE.IslandMonitorRunner


class _HubStub:
    def __init__(self):
        self.logs = []
        self.notifications = []

    def report_log(self, job_id, level, msg):
        self.logs.append((job_id, level, msg))

    def send_notification(self, **kwargs):
        self.notifications.append(kwargs)


class _ClientStub:
    def __init__(self, islands):
        self.islands = islands
        self.calls = {}

    def fetch_island_by_id(self, island_id):
        self.calls[island_id] = self.calls.get(island_id, 0) + 1
        item = self.islands[island_id]
        if callable(item):
            return item(self.calls[island_id])
        return item


class IslandMonitorRunnerTests(unittest.TestCase):
    def test_fetch_island_with_retry_retries_until_data_exists(self):
        runner = IslandMonitorRunner()
        runner.hub = _HubStub()
        runner.log = lambda *_args: None

        attempts = {"n": 0}

        class Client:
            def fetch_island_by_id(self, _island_id):
                attempts["n"] += 1
                return {} if attempts["n"] < 3 else {"island_id": "4475", "cities": []}

        island = runner._fetch_island_with_retry("j1", Client(), "4475")
        self.assertEqual(island["island_id"], "4475")
        self.assertEqual(attempts["n"], 3)

    def test_execute_marks_partial_and_preserves_failed_island_state(self):
        runner = IslandMonitorRunner()
        runner.hub = _HubStub()
        runner.resolve_credentials = lambda *_args, **_kwargs: {"server": "s78-br"}
        runner.get_or_login_game_client = lambda *_args, **_kwargs: _ClientStub(
            {
                "4475": lambda _n: {},
                "4480": {
                    "island_id": "4480",
                    "x": 84,
                    "y": 88,
                    "miracle_name": "Bosque",
                    "resource_name": "Mármore",
                    "cities": [
                        {"id": "1", "type": "city", "owner_name": "A", "name": "Cidade A", "ally_tag": "", "in_fight": False},
                    ],
                },
            }
        )
        runner.save_game_client = lambda *_args, **_kwargs: None

        logs = []
        runner.log = lambda _jid, level, msg: logs.append((level, msg))

        result = runner.execute(
            {
                "job_id": "j1",
                "account_id": "a1",
                "game_account_id": "g1",
                "inputs": {
                    "monitor_mode": "islands",
                    "own_city_ids": [],
                    "extra_island_ids": ["4475", "4480"],
                    "notify_new": True,
                    "notify_removed": True,
                    "notify_change": True,
                    "notify_fight": False,
                    "recheck_minutes": 20,
                    "island_state": {
                        "4475": {
                            "10": {"owner_name": "Juggernaut81", "city_name": "M01", "ally_tag": "", "first_seen": 1, "in_fight": False},
                        },
                        "4480": {
                            "1": {"owner_name": "A", "city_name": "Cidade A", "ally_tag": "", "first_seen": 1, "in_fight": False},
                        },
                    },
                },
            }
        )

        self.assertTrue(result.success)
        self.assertTrue(result.data["partial"])
        self.assertEqual(result.data["islands_failed"], ["4475"])
        self.assertEqual(result.reschedule_seconds, 300)
        self.assertIn("4475", result.reschedule_inputs["island_state"])
        self.assertEqual(result.reschedule_inputs["island_state"]["4475"]["10"]["city_name"], "M01")
        self.assertTrue(any("Monitor parcial" in msg for _level, msg in logs))


if __name__ == "__main__":
    unittest.main()
