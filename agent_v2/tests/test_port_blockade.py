"""N-75: a blockaded port takes no part in the distribution and stops a dispatch before it starts."""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "runners", "core", "services")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from runners import resources as resources_runner  # noqa: E402
from runners.resources import DistributeResourcesRunner, SendResourcesRunner  # noqa: E402
from services import resource_transport as transport  # noqa: E402
from services.resource_transport import CityState, PortBlockedError, is_game_flag_set  # noqa: E402


def _city(city_id, name, wood, *, island=1, blocked=False, by="Inimigo"):
    city = {
        "id": str(city_id), "name": name, "island_id": island, "wood": wood,
        "max_resources": {"resource": 500_000}, "resource_production_per_hour": 100,
    }
    if blocked:
        city["harbour_occupied"] = blocked if blocked is not True else 1
        city["port_controller_name"] = by
    return city


def _distribute(cities, **inputs):
    runner = object.__new__(DistributeResourcesRunner)
    runner.hub = MagicMock()
    runner.hub.get_snapshot.return_value = {
        "cities": cities, "base_snapshot": {}, "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    runner.hub.get_construction_reservations.return_value = {"reservations": {}}
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    runner.is_snapshot_stale = lambda snapshot: False
    runner.ensure_status_refresh = MagicMock()
    runner._get_active_transport_entries = lambda jid: []
    job_inputs = {
        "wood": True, "cities": [city["id"] for city in cities], "strategy": "evenly",
        "useful_transfer_min": 1000, "dispatch_stagger": 0, **inputs,
    }
    result = runner.execute({"job_id": "j1", "account_id": "a1", "game_account_id": "g1", "inputs": job_inputs})
    routes = [call.kwargs["inputs"] for call in runner.hub.spawn_job.call_args_list]
    return runner, result, routes


def _route(route):
    return route["from_city"], route["to_city"], route["wood"]


class DistributionPlanTests(unittest.TestCase):
    def test_without_a_blockade_every_city_takes_part(self):
        _runner, result, routes = _distribute([_city(1, "A", 90_000), _city(2, "B", 30_000), _city(3, "C", 0)])

        self.assertEqual(result.data["status"], "ok")
        self.assertEqual(sorted(_route(r) for r in routes), [("1", "2", 10_000), ("1", "3", 40_000)])

    def test_blockaded_destination_gets_nothing_and_leaves_the_average(self):
        cities = [_city(1, "A", 90_000), _city(2, "B", 30_000), _city(3, "C", 0, blocked=True, by="Corsario")]

        runner, result, routes = _distribute(cities)

        # the average is of A and B only (60k), not of the three (40k)
        self.assertEqual([_route(r) for r in routes], [("1", "2", 30_000)])
        self.assertEqual(result.data["status"], "ok")
        warning = next(msg for level, msg in runner.logs if level == "warn" and "Porto bloqueado" in msg)
        self.assertIn("C (por Corsario)", warning)
        self.assertIn("2 cidade(s) seguem no plano", warning)

    def test_blockaded_donor_sends_nothing(self):
        cities = [_city(1, "A", 90_000, blocked=True), _city(2, "B", 30_000), _city(3, "C", 0)]

        _runner, _result, routes = _distribute(cities)

        self.assertEqual([_route(r) for r in routes], [("2", "3", 15_000)])

    def test_same_island_is_excluded_too(self):
        # goods always travel by ship, the same island included
        cities = [_city(1, "A", 90_000, island=7), _city(2, "B", 30_000, island=7), _city(3, "C", 0, island=7, blocked=True)]

        _runner, _result, routes = _distribute(cities)

        self.assertTrue(all("3" not in (r["from_city"], r["to_city"]) for r in routes))
        self.assertEqual([_route(r) for r in routes], [("1", "2", 30_000)])

    def test_less_than_two_cities_left_says_why_and_tries_again_later(self):
        cities = [_city(1, "A", 90_000), _city(2, "B", 0, blocked=True)]

        runner, result, routes = _distribute(cities)

        self.assertEqual(routes, [])
        self.assertTrue(result.success)
        self.assertEqual((result.data["status"], result.data["blocked_cities"], result.data["eligible_cities"]), ("ports_blocked", ["2"], 1))
        self.assertEqual(result.reschedule_seconds, resources_runner.MIN_DISTRIBUTE_RECHECK)
        self.assertTrue(any("restam menos de duas cidades" in msg for _level, msg in runner.logs))
        runner.ensure_status_refresh.assert_called_once()            # how the end of the blockade is noticed
        runner.hub.get_construction_reservations.assert_not_called()   # nothing reserved or planned

    def test_a_single_run_does_not_loop_on_a_blockade(self):
        _runner, result, _routes = _distribute([_city(1, "A", 90_000), _city(2, "B", 0, blocked=True)], loop_enabled=False)

        self.assertEqual((result.data["status"], result.reschedule_seconds), ("ports_blocked", 0))

    def test_once_the_blockade_is_over_the_city_is_back_in_the_plan(self):
        blocked = [_city(1, "A", 90_000), _city(2, "B", 30_000), _city(3, "C", 0, blocked=True)]
        _runner, _result, during = _distribute(blocked)

        freed = [_city(1, "A", 90_000), _city(2, "B", 30_000), _city(3, "C", 0)]
        _runner, _result, after = _distribute(freed)

        self.assertNotIn("3", {r["to_city"] for r in during})
        self.assertIn("3", {r["to_city"] for r in after})

    def test_values_the_game_uses_for_no_blockade(self):
        for value in (None, 0, "0", "", False, "false"):
            self.assertFalse(is_game_flag_set(value), repr(value))
            cities = [_city(1, "A", 90_000), _city(2, "B", 30_000), _city(3, "C", 0)]
            cities[2]["harbour_occupied"] = value
            _runner, _result, routes = _distribute(cities)
            self.assertIn("3", {r["to_city"] for r in routes}, repr(value))
        for value in (1, "1", True, "true", {"id": 9}):
            self.assertTrue(is_game_flag_set(value), repr(value))

    def test_routes_are_marked_as_coming_from_the_distribution(self):
        _runner, _result, routes = _distribute([_city(1, "A", 90_000), _city(2, "B", 0)])

        self.assertTrue(routes and all(route["distribution_route"] is True for route in routes))


def _state(city_id, name, *, blocked=False, by=""):
    return CityState(
        city_id=str(city_id), city_name=name, island_id="1",
        available_resources={"wood": 50_000, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0},
        free_space={"wood": 100_000, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0},
        port_positions=[1], harbour_occupied=blocked, port_controller=by,
    )


class LiveStateTests(unittest.TestCase):
    def _page(self, **background):
        data = {"name": "Porto Velho", "islandId": 77, "position": [{"building": "townHall"}, {"building": "port"}], **background}
        return (
            '[["updateBackgroundData",' + json.dumps(data) + '],["updateTemplateData",{}]] '
            '"currentResources":{"resource":1000,"1":0,"2":0,"3":0,"4":0},"maxResources":{"resource":5000,"1":0,"2":0,"3":0,"4":0},'
            '"maxResourcesWithModifier":{}'
        )

    def _fetch(self, **background):
        client = MagicMock()
        client.session.get.return_value.text = self._page(**background)
        return transport.fetch_city_state(client, 5)

    def test_city_state_reads_the_blockade_from_the_game(self):
        blocked = self._fetch(harbourOccupied=1, portControllerName="Corsario")
        self.assertEqual((blocked.harbour_occupied, blocked.port_controller, blocked.city_name), (True, "Corsario", "Porto Velho"))

        for free in (self._fetch(), self._fetch(harbourOccupied=0), self._fetch(harbourOccupied="0"), self._fetch(harbourOccupied=None)):
            self.assertEqual((free.harbour_occupied, free.port_controller), (False, ""))

    def test_prepare_stops_before_opening_the_transport_screen(self):
        client = MagicMock()
        for blocked_side in ("origin", "destination", "both"):
            states = {
                "1": _state(1, "Origem", blocked=blocked_side in ("origin", "both"), by="Corsario"),
                "2": _state(2, "Destino", blocked=blocked_side in ("destination", "both")),
            }
            client.reset_mock()
            with patch.object(transport, "change_current_city"), \
                    patch.object(transport, "fetch_city_state", side_effect=lambda _client, city_id: states[str(city_id)]):
                with self.assertRaises(PortBlockedError) as ctx:
                    transport.prepare_transport(client, from_city_id=1, to_city_id=2, requested={"wood": 5000})

            expected = {"origin": ["1"], "destination": ["2"], "both": ["1", "2"]}[blocked_side]
            self.assertEqual([state.city_id for state in ctx.exception.blocked], expected)
            client.session.get.assert_not_called()         # the transport screen was never requested
            client.session.post.assert_not_called()
        self.assertIn("Origem (bloqueado por Corsario)", str(ctx.exception))


def _send(inputs, *, error=None):
    runner = object.__new__(SendResourcesRunner)
    runner.hub = MagicMock()
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    runner.resolve_credentials = lambda *a, **k: {"email": "x"}
    runner.get_or_login_game_client = lambda *a, **k: MagicMock()
    runner.save_game_client = MagicMock()
    runner.ensure_status_refresh = MagicMock()
    job = {"job_id": "j1", "account_id": "a1", "game_account_id": "g1", "inputs": {"from_city": "1", "to_city": "2", "wood": 5000, **inputs}}
    with patch.object(resources_runner, "prepare_transport", side_effect=error) as prepare, \
            patch.object(resources_runner, "submit_transport") as submit:
        result = runner.execute(job)
    return runner, result, prepare, submit


class DispatchTests(unittest.TestCase):
    BLOCKED = PortBlockedError([_state(2, "Destino", blocked=True, by="Corsario")])

    def test_route_from_a_stale_snapshot_is_dropped_without_committing_anything(self):
        runner, result, prepare, submit = _send({"distribution_route": True}, error=self.BLOCKED)

        prepare.assert_called_once()
        submit.assert_not_called()                              # no ship, no resource left the city
        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "port_blocked")
        self.assertEqual(result.data["blocked_cities"], [{"city_id": "2", "city_name": "Destino", "port_controller": "Corsario"}])
        self.assertFalse(result.reschedule_seconds)             # the next distribution cycle plans again
        warning = next(msg for level, msg in runner.logs if level == "warn")
        self.assertIn("porto bloqueado em Destino (bloqueado por Corsario)", warning)
        self.assertIn("Nenhum barco ou recurso foi comprometido", warning)
        runner.ensure_status_refresh.assert_called_once()        # so the snapshot learns about it
        runner.hub.spawn_job.assert_not_called()                 # no arrival monitor for a transport that never left

    def test_manual_send_fails_clearly(self):
        _runner, result, _prepare, submit = _send({}, error=self.BLOCKED)

        submit.assert_not_called()
        self.assertFalse(result.success)
        self.assertEqual(result.data["error"], "port_blocked")

    def test_a_failed_status_refresh_does_not_hide_the_result(self):
        runner = object.__new__(SendResourcesRunner)
        runner.hub = MagicMock()
        runner.log = lambda *a, **k: None
        runner.resolve_credentials = lambda *a, **k: {"email": "x"}
        runner.get_or_login_game_client = lambda *a, **k: MagicMock()
        runner.save_game_client = MagicMock()
        runner.ensure_status_refresh = MagicMock(side_effect=RuntimeError("hub fora"))
        job = {"job_id": "j1", "account_id": "a1", "game_account_id": "g1",
               "inputs": {"from_city": "1", "to_city": "2", "wood": 5000, "distribution_route": True}}
        with patch.object(resources_runner, "prepare_transport", side_effect=self.BLOCKED), \
                patch.object(resources_runner, "submit_transport") as submit:
            result = runner.execute(job)

        submit.assert_not_called()
        self.assertEqual(result.data["status"], "port_blocked")


if __name__ == "__main__":
    unittest.main()
