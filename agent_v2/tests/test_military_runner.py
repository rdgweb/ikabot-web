import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_military_runner_module():
    core_pkg = types.ModuleType("core")
    runner_registry = types.ModuleType("core.runner_registry")
    runner_registry.register_runner = lambda _code: (lambda cls: cls)
    core_pkg.runner_registry = runner_registry

    catalogs = types.ModuleType("core.catalogs")
    catalogs.TRAINING_UNITS = {
        "fleet": [{"id": 216, "name": "Ariete a Vapor"}],
        "troops": [],
    }
    core_pkg.catalogs = catalogs

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
    resource_transport.change_current_city = lambda *_args, **_kwargs: None
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
            "core.catalogs": catalogs,
            "runners": runners_pkg,
            "runners.base": base_mod,
            "services": services_pkg,
            "services.resource_transport": resource_transport,
            "sessions": sessions_pkg,
            "sessions.game_session_service": game_session_service,
        }
    )

    spec = importlib.util.spec_from_file_location("military_runner_under_test", ROOT / "runners" / "military.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TrainUnitsRunnerMaintainTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_military_runner_module()
        self.original_sleep = self.module.time.sleep
        self.module.time.sleep = lambda *_args, **_kwargs: None

    def tearDown(self):
        self.module.time.sleep = self.original_sleep

    def _runner(self, snapshot, client, spawned, updates=None):
        runner = self.module.TrainUnitsRunner()
        runner.hub = SimpleNamespace(
            get_snapshot=lambda **_kwargs: snapshot,
            spawn_job=lambda *args, **kwargs: spawned.append({"args": args, "kwargs": kwargs}),
            update_snapshot=lambda *args, **kwargs: (updates.append((args, kwargs)) if updates is not None else None),
        )
        runner.resolve_credentials = lambda *_args, **_kwargs: {"ok": True}
        runner.get_or_login_game_client = lambda *_args, **_kwargs: client
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner.log = lambda *_args, **_kwargs: None
        return runner

    def test_multi_city_spawns_1202_from_surplus_before_training(self):
        snapshot = {
            "cities": [
                {
                    "id": 39272,
                    "name": "Hell",
                    "island_id": 10,
                    "buildings": [{"building": "shipyard", "position": 1}],
                },
                {
                    "id": 39267,
                    "name": "Okolnir",
                    "island_id": 11,
                    "buildings": [{"building": "barracks", "position": 8}],
                },
            ],
            "military": {"by_city": []},
        }
        train_calls = []

        class Client:
            def fetch_stationed_units(self, city_id, building_type):
                self.last_building_type = building_type
                if city_id == 39272:
                    return {"counts": {216: 21}}
                return {"counts": {}}

            def fetch_barracks_state(self, city_id, position, building_type):
                return {"training_queue": []}

            def train_units(self, *args, **kwargs):
                train_calls.append((args, kwargs))

        spawned = []
        runner = self._runner(snapshot, Client(), spawned)

        result = runner._maintain_multi_city(
            "job-1",
            "account-1",
            "ga-1",
            {
                "city_ids": ["39272", "39267"],
                "target_garrison": {"216": 2},
            },
            "fleet",
            "selected_uniform",
            120,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "redistribution_scheduled")
        self.assertEqual(len(spawned), 2)
        self.assertEqual(spawned[0]["kwargs"]["action_code"], 1202)
        self.assertEqual(
            spawned[0]["kwargs"]["inputs"],
            {"from_city_id": 39272, "to_city_id": 39267, "units": {"216": 2}},
        )
        self.assertEqual(spawned[1]["kwargs"]["action_code"], 13)
        self.assertEqual(spawned[1]["kwargs"]["delay_seconds"], 45)
        self.assertEqual(spawned[1]["kwargs"]["inputs"]["retime_followup_action_code"], 1005)
        self.assertEqual(
            spawned[1]["kwargs"]["inputs"]["expected_movements"],
            [
                {
                    "from_city_id": 39272,
                    "to_city_id": 39267,
                    "from_city_name": "Hell",
                    "to_city_name": "Okolnir",
                    "units": {"216": 2},
                }
            ],
        )
        self.assertEqual(train_calls, [])

    def test_multi_city_updates_military_snapshot_from_live_garrison(self):
        snapshot = {
            "base_snapshot": {},
            "cities": [
                {
                    "id": 39272,
                    "name": "Hell",
                    "island_id": 10,
                    "buildings": [{"building": "shipyard", "position": 1}],
                },
                {
                    "id": 39267,
                    "name": "Okolnir",
                    "island_id": 11,
                    "buildings": [{"building": "shipyard", "position": 1}],
                },
            ],
            "military": {
                "by_city": [
                    {"city_id": 39272, "city_name": "Hell", "fleet": {"Ariete a Vapor": 21}, "troops": {}},
                    {"city_id": 39267, "city_name": "Okolnir", "fleet": {}, "troops": {}},
                ]
            },
        }

        class Client:
            def fetch_stationed_units(self, city_id, building_type):
                if city_id == 39272:
                    return {"counts": {216: 19}}
                if city_id == 39267:
                    return {"counts": {216: 2}}
                return {"counts": {}}

            def fetch_barracks_state(self, city_id, position, building_type):
                return {"training_queue": []}

            def train_units(self, *args, **kwargs):
                raise AssertionError("should not train")

        updates = []
        runner = self._runner(snapshot, Client(), [], updates=updates)

        result = runner._maintain_multi_city(
            "job-1",
            "account-1",
            "ga-1",
            {
                "city_ids": ["39272", "39267"],
                "target_garrison": {"216": 2},
            },
            "fleet",
            "selected_uniform",
            120,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "at_target")
        self.assertEqual(len(updates), 1)
        payload = updates[0][0][1]
        by_city = payload["military"]["by_city"]
        hell = next(row for row in by_city if row["city_id"] == 39272)
        okolnir = next(row for row in by_city if row["city_id"] == 39267)
        self.assertEqual(hell["fleet"], {"Ariete a Vapor": 19})
        self.assertEqual(okolnir["fleet"], {"Ariete a Vapor": 2})

    def test_multi_city_trains_extra_for_cities_without_shipyard_when_no_surplus(self):
        snapshot = {
            "cities": [
                {
                    "id": 66482,
                    "name": "MM3",
                    "island_id": 10,
                    "buildings": [{"building": "shipyard", "position": 1}],
                },
                {
                    "id": 66486,
                    "name": "EE2",
                    "island_id": 11,
                    "buildings": [{"building": "shipyard", "position": 1}],
                },
                {"id": 66480, "name": "MM1", "island_id": 12, "buildings": []},
                {"id": 66481, "name": "MM2", "island_id": 13, "buildings": []},
            ],
            "military": {"by_city": []},
        }
        train_calls = []

        class Client:
            def fetch_stationed_units(self, city_id, building_type):
                return {"counts": {}}

            def fetch_barracks_state(self, city_id, position, building_type):
                return {"training_queue": []}

            def train_units(self, city_id, position, units, building_type):
                train_calls.append((city_id, units))

        spawned = []
        runner = self._runner(snapshot, Client(), spawned)

        result = runner._maintain_multi_city(
            "job-1",
            "account-1",
            "ga-1",
            {
                "city_ids": ["66482", "66486", "66480", "66481"],
                "target_garrison": {"216": 2},
            },
            "fleet",
            "selected_uniform",
            120,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "trained")
        self.assertEqual(spawned, [])
        self.assertEqual(dict(train_calls), {66482: {216: 4}, 66486: {216: 4}})

    def test_multi_city_counts_training_queue_before_training_extra(self):
        snapshot = {
            "cities": [
                {
                    "id": 66482,
                    "name": "MM3",
                    "island_id": 10,
                    "buildings": [{"building": "shipyard", "position": 1}],
                },
                {
                    "id": 66486,
                    "name": "EE2",
                    "island_id": 11,
                    "buildings": [{"building": "shipyard", "position": 1}],
                },
                {"id": 66480, "name": "MM1", "island_id": 12, "buildings": []},
            ],
            "military": {"by_city": []},
        }
        train_calls = []

        class Client:
            def fetch_stationed_units(self, city_id, building_type):
                return {"counts": {}}

            def fetch_barracks_state(self, city_id, position, building_type):
                return {"training_queue": [{"unit_id": 216, "quantity": 2}]}

            def train_units(self, city_id, position, units, building_type):
                train_calls.append((city_id, units))

        runner = self._runner(snapshot, Client(), [])

        result = runner._maintain_multi_city(
            "job-1",
            "account-1",
            "ga-1",
            {
                "city_ids": ["66482", "66486", "66480"],
                "target_garrison": {"216": 2},
            },
            "fleet",
            "selected_uniform",
            120,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "trained")
        self.assertEqual(train_calls, [(66482, {216: 2})])

    def test_multi_city_reschedules_to_training_queue_eta_when_queue_covers_deficit(self):
        snapshot = {
            "cities": [
                {
                    "id": 66482,
                    "name": "MM3",
                    "island_id": 10,
                    "buildings": [{"building": "shipyard", "position": 1}],
                },
            ],
            "military": {"by_city": []},
        }
        train_calls = []

        class Client:
            def fetch_stationed_units(self, city_id, building_type):
                return {"counts": {}}

            def fetch_barracks_state(self, city_id, position, building_type):
                return {"training_queue": [{"unit_id": 216, "quantity": 2, "remaining_seconds": 600}]}

            def train_units(self, city_id, position, units, building_type):
                train_calls.append((city_id, units))

        runner = self._runner(snapshot, Client(), [])

        result = runner._maintain_multi_city(
            "job-1",
            "account-1",
            "ga-1",
            {
                "city_ids": ["66482"],
                "target_garrison": {"216": 2},
            },
            "fleet",
            "selected_uniform",
            120,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "at_target")
        self.assertEqual(result.reschedule_seconds, 690)
        self.assertEqual(train_calls, [])


class StationUnitsRunnerSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_military_runner_module()

    def test_station_runner_decrements_source_military_snapshot(self):
        snapshot = {
            "base_snapshot": {},
            "cities": [{"id": 39272, "name": "Hell"}, {"id": 39267, "name": "Okolnir"}],
            "military": {
                "by_city": [
                    {"city_id": 39272, "fleet": {"Ariete a Vapor": 21}, "troops": {}},
                    {"city_id": 39267, "fleet": {}, "troops": {}},
                ]
            },
        }
        updates = []

        class Hub:
            def get_snapshot(self, **_kwargs):
                return snapshot

            def update_snapshot(self, *args, **kwargs):
                updates.append((args, kwargs))
                return {"ok": True}

        runner = self.module.StationUnitsRunner()
        runner.hub = Hub()
        runner.log = lambda *_args, **_kwargs: None

        runner._patch_snapshot_after_station(
            job_id="job-1",
            account_id="account-1",
            game_account_id="ga-1",
            from_city_id=39272,
            units={216: 2},
            scope="fleet",
            catalog={216: "Ariete a Vapor"},
        )

        self.assertEqual(len(updates), 1)
        payload = updates[0][0][1]
        by_city = payload["military"]["by_city"]
        hell = next(row for row in by_city if row["city_id"] == 39272)
        okolnir = next(row for row in by_city if row["city_id"] == 39267)
        self.assertEqual(hell["fleet"], {"Ariete a Vapor": 19})
        self.assertEqual(okolnir["fleet"], {})


class MilitaryMovementsRunnerRetimeTests(unittest.TestCase):
    def setUp(self):
        self.module = _load_military_runner_module()
        self.original_time = self.module.time.time
        self.module.time.time = lambda: 1000

    def tearDown(self):
        self.module.time.time = self.original_time

    def test_military_movements_retimes_followup_from_movement_eta(self):
        calls = []

        class Hub:
            def get_snapshot(self, **_kwargs):
                return {"cities": [{"id": 1, "name": "Any"}]}

            def update_military_movements(self, *args, **kwargs):
                calls.append(("update", args, kwargs))

            def retime_root_followup_job(self, **kwargs):
                calls.append(("retime", kwargs))
                return {"updated": True}

        class Client:
            def fetch_military_advisor(self, city_id):
                return {
                    "has_active_battle": False,
                    "port_occupied": False,
                    "ships_moving": 3,
                    "movements": ["deployfleet"],
                    "movement_details": [
                        {
                            "mission": "deployfleet",
                            "event_date": "0:10:00",
                            "event_time": 1300,
                            "is_returning": False,
                            "origin": {"name": "Other"},
                            "target": {"name": "Elsewhere", "avatarName": "OtherPlayer"},
                            "fleet": {"steamboat": 99},
                        },
                        {
                            "mission": "deployfleet",
                            "event_date": "5:20:04",
                            "event_time": 1600,
                            "is_returning": False,
                            "origin": {"name": "Hell"},
                            "target": {"name": "Glitnir", "avatarName": "BlackShadow701"},
                            "fleet": {"steamboat": 2},
                        },
                        {
                            "mission": "deployfleet",
                            "event_date": "5:32:16",
                            "event_time": 2800,
                            "is_returning": False,
                            "origin": {"name": "Hell"},
                            "target": {"name": "Okolnir", "avatarName": "BlackShadow701"},
                            "fleet": {"steamboat": 2},
                        },
                    ],
                }

        runner = self.module.MilitaryMovementsRunner()
        runner.hub = Hub()
        runner.resolve_credentials = lambda *_args, **_kwargs: {"ok": True}
        runner.get_or_login_game_client = lambda *_args, **_kwargs: Client()
        runner.save_game_client = lambda *_args, **_kwargs: None
        runner.log = lambda *_args, **_kwargs: None

        result = runner.execute(
            {
                "job_id": "job-13",
                "account_id": "account-1",
                "game_account_id": "ga-1",
                "root_job_id": "root-1",
                "inputs": {
                    "retime_followup_action_code": 1005,
                    "expected_movements": [
                        {"from_city_name": "Hell", "to_city_name": "Glitnir"},
                        {"from_city_name": "Hell", "to_city_name": "Okolnir"},
                    ],
                },
            }
        )

        self.assertTrue(result.success)
        retime = next(item for item in calls if item[0] == "retime")
        self.assertEqual(retime[1]["root_job_id"], "root-1")
        self.assertEqual(retime[1]["action_code"], 1005)
        self.assertEqual(retime[1]["delay_seconds"], 1800 + 90)


if __name__ == "__main__":
    unittest.main()
