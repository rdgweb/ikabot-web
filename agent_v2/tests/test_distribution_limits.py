"""N-93: the distribution plans within what the port, the fleet and the action points can take,
and a route that could not go is planned again by the next cycle instead of re-running by itself."""

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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
from services.resource_transport import estimate_port_loading_per_minute, fetch_free_ships  # noqa: E402

RESOURCES = ("wood", "wine", "marble", "crystal", "sulfur")


def _city(city_id, name, wood, *, ports=None, points=None):
    """ports: None = the snapshot has no buildings; [] = buildings but no port; [18, 18] = two ports."""
    city = {
        "id": str(city_id), "name": name, "island_id": city_id, "wood": wood,
        "max_resources": {"resource": 500_000}, "resource_production_per_hour": 100,
    }
    if ports is not None:
        city["buildings"] = [{"building": "townHall", "level": 20}] + [{"building": "port", "level": level} for level in ports]
    if points is not None:
        city["action_points_current"], city["action_points_max"] = points
    return city


def _pending(from_city, to_city, wood, *, monitor=False):
    return {
        "job_id": f"job-{from_city}-{to_city}", "status": "scheduled", "monitor_mode": "arrival_check" if monitor else "",
        "from_city": str(from_city), "to_city": str(to_city), "use_freighters": False, "scheduled_for": "",
        "eta_total_seconds": 0, "resources": {"wood": wood, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0},
    }


def _distribute(cities, *, base=None, live=None, entries=None, **inputs):
    """Run one distribution cycle. ``live``: what the game says is free right now (None = cannot be read)."""
    runner = object.__new__(DistributeResourcesRunner)
    runner.hub = MagicMock()
    runner.hub.get_snapshot.return_value = {
        "cities": cities, "base_snapshot": dict(base or {}), "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    runner.hub.get_construction_reservations.return_value = {"reservations": {}}
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    runner.is_snapshot_stale = lambda snapshot: False
    runner.ensure_status_refresh = MagicMock()
    runner._get_active_transport_entries = lambda jid: [dict(entry, resources=dict(entry["resources"])) for entry in (entries or [])]
    runner._read_free_ships = MagicMock(return_value=live)
    job_inputs = {
        "wood": True, "cities": [city["id"] for city in cities], "strategy": "evenly",
        "useful_transfer_min": 1000, "dispatch_stagger": 0, **inputs,
    }
    result = runner.execute({"job_id": "j1", "account_id": "a1", "game_account_id": "g1", "inputs": job_inputs})
    routes = [call.kwargs["inputs"] for call in runner.hub.spawn_job.call_args_list]
    return runner, result, routes


def _route(route):
    return route["from_city"], route["to_city"], route["wood"]


def _said(runner, text, level=None):
    return any(text in msg for lvl, msg in runner.logs if level in (None, lvl))


class PortSpeedTests(unittest.TestCase):
    def test_the_table_matches_what_production_measured(self):
        # medians of the real loading speed per minute, read from the transport screen on 12/10/2026
        measured = {(18,): 1249.0, (20,): 1566.6, (23,): 2174.0, (24,): 2423.3, (25,): 2685.0, (28,): 3669.7, (30,): 4494.4,
                    (18, 18): 2500.0, (20, 20): 3133.2, (23, 23): 4347.8, (25, 25): 5381.2, (30, 30): 9022.6}
        for levels, real in measured.items():
            self.assertAlmostEqual(estimate_port_loading_per_minute(levels) / real, 1.0, delta=0.01, msg=str(levels))

    def test_a_city_without_a_port_loads_about_600_an_hour(self):
        self.assertEqual(estimate_port_loading_per_minute([]) * 60, 600)
        self.assertEqual(estimate_port_loading_per_minute([0]) * 60, 600)       # a port still at level 0
        self.assertEqual(estimate_port_loading_per_minute(None) * 60, 600)

    def test_levels_past_the_table_keep_growing(self):
        self.assertGreater(estimate_port_loading_per_minute([31]), estimate_port_loading_per_minute([30]))
        self.assertGreater(estimate_port_loading_per_minute([40]), estimate_port_loading_per_minute([31]))

    def test_the_free_ships_come_from_the_header_data_of_the_game(self):
        client = MagicMock()
        client.session.get.return_value.text = json.dumps(
            [["updateGlobalData", {"headerData": {"freeTransporters": "37", "maxTransporters": 58, "freeFreighters": 2}}]]
        )
        self.assertEqual(fetch_free_ships(client), {"transporters": 37, "freighters": 2})
        self.assertEqual(client.session.get.call_args.kwargs["params"], {"view": "updateGlobalData", "ajax": "1"})
        self.assertEqual(client.session.get.call_count, 1)                       # one light request, nothing else

        for broken in ("", "<html>login</html>", "[]", json.dumps([["updateGlobalData", {"headerData": {"gold": 1}}]])):
            client.session.get.return_value.text = broken
            self.assertIsNone(fetch_free_ships(client), broken)


class DonorPortTests(unittest.TestCase):
    def test_a_city_without_a_port_is_not_a_donor(self):
        # the real case: Alexa Trops (no port, ~600/h) chosen as donor on every cycle; 20,000 = 33h of loading
        cities = [_city(1, "Alexa Trops", 90_000, ports=[]), _city(2, "TheTower", 90_000, ports=[18]), _city(3, "Reflexo", 0, ports=[18])]

        runner, result, routes = _distribute(cities, shipment_cap=20_000, useful_transfer_min=5000)

        self.assertEqual([_route(r) for r in routes], [("2", "3", 20_000)])
        self.assertEqual(result.data["status"], "ok")
        warning = next(msg for level, msg in runner.logs if level == "warn" and "Alexa Trops" in msg)
        self.assertIn("fica de fora como doadora: sem porto, carrega ~600/h", warning)
        self.assertIn("uma rota de 20,000 levaria 33.3h; limite de 120 min", warning)

    def test_it_still_receives(self):
        cities = [_city(1, "Sem porto", 0, ports=[]), _city(2, "Com porto", 90_000, ports=[18])]
        _runner, _result, routes = _distribute(cities)
        self.assertEqual([_route(r) for r in routes], [("2", "1", 45_000)])

    def test_a_slow_port_gives_only_what_it_loads_within_the_limit(self):
        # level 1 loads 30 a minute: 3,600 in two hours
        cities = [_city(1, "Novo", 90_000, ports=[1]), _city(2, "Destino", 0, ports=[18])]
        _runner, _result, routes = _distribute(cities)
        self.assertEqual([_route(r) for r in routes], [("1", "2", 3_600)])

        _runner, _result, routes = _distribute(cities, max_loading_minutes=600)
        self.assertEqual([_route(r) for r in routes], [("1", "2", 18_000)])

    def test_what_is_already_lined_up_at_the_port_counts(self):
        cities = [_city(1, "Novo", 90_000, ports=[1]), _city(2, "Destino", 0, ports=[18]), _city(3, "Outro", 90_000, ports=[18])]
        runner, _result, routes = _distribute(cities, entries=[_pending(1, 9, 3_000)])       # 3,000 of its 3,600 already taken
        self.assertEqual({r["from_city"] for r in routes}, {"3"})
        self.assertTrue(_said(runner, "Novo fica de fora como doadora neste ciclo: já tem 3,000 na fila do porto", "warn"))

    def test_the_limit_can_be_turned_off(self):
        cities = [_city(1, "Sem porto", 90_000, ports=[]), _city(2, "Destino", 0, ports=[18])]
        _runner, _result, routes = _distribute(cities, useful_transfer_min=5000)
        self.assertEqual(routes, [])
        _runner, _result, routes = _distribute(cities, max_loading_minutes=0)
        self.assertEqual([_route(r) for r in routes], [("1", "2", 45_000)])

    def test_a_city_the_snapshot_has_no_buildings_for_is_not_judged(self):
        cities = [_city(1, "A", 90_000), _city(2, "B", 0)]
        runner, _result, routes = _distribute(cities)
        self.assertEqual([_route(r) for r in routes], [("1", "2", 45_000)])
        self.assertFalse(_said(runner, "fica de fora"))

    def test_the_route_carries_the_limits_to_the_dispatch(self):
        _runner, _result, routes = _distribute([_city(1, "A", 90_000, ports=[18]), _city(2, "B", 0, ports=[18])], useful_transfer_min=5000)
        self.assertEqual((routes[0]["min_dispatch_total"], routes[0]["max_loading_seconds"], routes[0]["distribution_route"]), (5000, 7200, True))


class ActionPointTests(unittest.TestCase):
    def test_a_city_with_no_action_point_is_not_a_donor(self):
        cities = [_city(1, "A", 90_000, points=(0, 5)), _city(2, "B", 90_000, points=(3, 5)), _city(3, "C", 0, points=(5, 5))]
        runner, _result, routes = _distribute(cities)
        self.assertEqual([_route(r) for r in routes], [("2", "3", 30_000)])
        self.assertTrue(_said(runner, "A fica de fora como doadora neste ciclo: sem ponto de ação livre", "warn"))

    def test_each_route_takes_one_point(self):
        cities = [_city(1, "A", 90_000, points=(1, 5)), _city(2, "B", 0), _city(3, "C", 0)]
        runner, _result, routes = _distribute(cities)
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["from_city"], "1")
        self.assertTrue(_said(runner, "adiada: A sem ponto de ação livre"))

    def test_transports_that_left_after_the_snapshot_count(self):
        # the snapshot says 3 of 3, but three transports of this chain are out from A right now
        cities = [_city(1, "A", 90_000, points=(3, 3)), _city(2, "B", 0)]
        entries = [_pending(1, 7, 100, monitor=True), _pending(1, 8, 100, monitor=True), _pending(1, 9, 100)]
        _runner, _result, routes = _distribute(cities, entries=entries)
        self.assertEqual(routes, [])

    def test_unknown_points_do_not_limit(self):
        _runner, _result, routes = _distribute([_city(1, "A", 90_000), _city(2, "B", 0), _city(3, "C", 0)])
        self.assertEqual(len(routes), 2)


class FleetTests(unittest.TestCase):
    CITIES = [_city(1, "A", 90_000, ports=[18]), _city(2, "B", 0, ports=[18])]

    def test_a_route_is_cut_to_what_the_free_ships_carry(self):
        # the real case: 20,000 asks for 40 merchants; HAVIT has 58 and most are out
        runner, result, routes = _distribute(self.CITIES, live={"transporters": 30, "freighters": 0})
        self.assertEqual([_route(r) for r in routes], [("1", "2", 15_000)])
        self.assertTrue(_said(runner, "reduzida de 45,000 para 15,000: é o que 30 mercante(s) livre(s) levam"))
        self.assertTrue(_said(runner, "Frota (jogo): 30 mercante(s) livre(s)"))
        self.assertEqual(result.data["fleet_short_routes"], 0)

    def test_no_route_is_created_when_the_ships_cannot_carry_the_useful_minimum(self):
        runner, result, routes = _distribute(self.CITIES, live={"transporters": 9, "freighters": 0}, useful_transfer_min=5000)
        self.assertEqual(routes, [])
        self.assertEqual((result.data["actions_spawned"], result.data["fleet_short_routes"]), (0, 1))
        self.assertTrue(_said(runner, "Frota insuficiente para A -> B: 45,000 pede 90 mercante(s) e há 9 livre(s)", "warn"))
        # it looks again soon, instead of in twelve hours
        self.assertEqual(result.reschedule_seconds, resources_runner.MIN_DISTRIBUTE_RECHECK)

    def test_the_routes_of_a_cycle_share_the_fleet(self):
        cities = [_city(1, "A", 90_000, ports=[18]), _city(2, "B", 0, ports=[18]), _city(3, "C", 0, ports=[18])]
        _runner, _result, routes = _distribute(cities, live={"transporters": 80, "freighters": 0})
        self.assertEqual(sorted(r["wood"] for r in routes), [10_000, 30_000])        # 60 ships, then the 20 left

    def test_sends_lined_up_but_not_gone_already_count_on_ships(self):
        runner, _result, routes = _distribute(
            self.CITIES, live={"transporters": 30, "freighters": 0}, entries=[_pending(5, 6, 5_000)],
        )
        self.assertEqual([_route(r) for r in routes], [("1", "2", 10_000)])           # 30 - 10 = 20 ships
        self.assertTrue(_said(runner, "10 já contado(s) para envios pendentes"))

    def test_the_load_percent_changes_what_a_ship_carries(self):
        _runner, _result, routes = _distribute(self.CITIES, live={"transporters": 30, "freighters": 0}, transport_load_percent="60")
        self.assertEqual([_route(r) for r in routes], [("1", "2", 9_000)])            # 30 ships of 300

    def test_the_snapshot_learns_the_real_count(self):
        runner, _result, _routes = _distribute(
            self.CITIES, base={"free_transporters": 58, "free_freighters": 0}, live={"transporters": 30, "freighters": 0},
        )
        runner.hub.patch_snapshot_ships.assert_called_once_with("g1", delta_transporters=-28, delta_freighters=0)

        runner, _result, _routes = _distribute(
            self.CITIES, base={"free_transporters": 30, "free_freighters": 0}, live={"transporters": 30, "freighters": 0},
        )
        runner.hub.patch_snapshot_ships.assert_not_called()

    def test_without_the_game_the_snapshot_count_is_used(self):
        runner, _result, routes = _distribute(self.CITIES, base={"free_transporters": 10}, live=None)
        self.assertEqual([_route(r) for r in routes], [("1", "2", 5_000)])
        self.assertTrue(_said(runner, "Frota (snapshot): 10 mercante(s) livre(s)"))

    def test_with_no_count_at_all_nothing_is_limited(self):
        _runner, _result, routes = _distribute(self.CITIES, live=None)
        self.assertEqual([_route(r) for r in routes], [("1", "2", 45_000)])

    def test_the_game_is_only_asked_when_there_is_something_to_send(self):
        runner, _result, routes = _distribute([_city(1, "A", 40_000), _city(2, "B", 40_000)], live={"transporters": 30, "freighters": 0})
        self.assertEqual(routes, [])
        runner._read_free_ships.assert_not_called()

    def test_reading_the_game_never_breaks_the_cycle(self):
        runner = object.__new__(DistributeResourcesRunner)
        runner.logs = []
        runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
        runner.resolve_credentials = MagicMock(return_value={"email": "x"})
        runner.get_or_login_game_client = MagicMock(side_effect=RuntimeError("proxy fora"))
        self.assertIsNone(runner._read_free_ships({"job_id": "j", "account_id": "a", "game_account_id": "g"}))
        self.assertTrue(_said(runner, "usando a contagem do snapshot", "warn"))

        runner.get_or_login_game_client = MagicMock(side_effect=resources_runner.LoginCooldownActive(delay_seconds=600))
        with self.assertRaises(resources_runner.LoginCooldownActive):          # the cycle is postponed by the login rule
            runner._read_free_ships({"job_id": "j", "account_id": "a", "game_account_id": "g"})


class LeftoverTests(unittest.TestCase):
    def test_what_is_left_after_the_transport_on_its_way_is_not_sent_in_crumbs(self):
        # the real case: 66 shipments below the useful minimum, the smallest with 7 units
        cities = [_city(1, "A", 90_000), _city(2, "B", 0)]
        runner, result, routes = _distribute(cities, entries=[_pending(1, 2, 44_993, monitor=True)], useful_transfer_min=5000)
        self.assertEqual(routes, [])
        self.assertEqual(result.data["below_minimum_routes"], 1)
        self.assertTrue(_said(runner, "Resto de 7 em A -> B abaixo do mínimo útil de 5,000; não enviado"))

    def test_a_leftover_worth_a_trip_still_goes(self):
        cities = [_city(1, "A", 90_000), _city(2, "B", 0)]
        _runner, _result, routes = _distribute(cities, entries=[_pending(1, 2, 30_000, monitor=True)], useful_transfer_min=5000)
        self.assertEqual([_route(r) for r in routes], [("1", "2", 15_000)])


def _stock(city_id, name, **amounts):
    """A city with several resources (the helper above only sets wood)."""
    city = {"id": str(city_id), "name": name, "island_id": city_id, "max_resources": {"resource": 5_000_000, "1": 5_000_000, "2": 5_000_000, "3": 5_000_000, "4": 5_000_000}}
    city.update(amounts)
    return city


class EvenlyToleranceTests(unittest.TestCase):
    """N-94: accounts already level to a few percent kept shipping a million units every 60 hours."""

    # the real shape: HAVIT's wine on 12/10, five cities within 4% of the average
    LEVEL = [_city(1, "A", 251_500), _city(2, "B", 240_000), _city(3, "C", 243_000), _city(4, "D", 232_800), _city(5, "E", 243_700)]

    def test_cities_that_are_about_level_trade_nothing(self):
        runner, result, routes = _distribute(self.LEVEL, useful_transfer_min=5000, shipment_cap=20_000)
        self.assertEqual(routes, [])
        self.assertEqual(result.data["actions_spawned"], 0)
        self.assertTrue(_said(runner, "Tolerancia wood: 10% da media (242,200) = 24,220 | 0 cidade(s) fora da faixa; nada a igualar"))
        runner._read_free_ships.assert_not_called()                     # nothing to send: the game is not even asked

    def test_without_tolerance_it_behaves_as_before(self):
        _runner, _result, routes = _distribute(self.LEVEL, useful_transfer_min=5000, shipment_cap=20_000, evenly_tolerance_percent=0)
        self.assertEqual([_route(r) for r in routes], [("1", "4", 9_300)])        # A is 9,300 above the average: a shipment for 4%

    def test_a_city_far_from_the_average_is_still_levelled(self):
        cities = [_city(1, "A", 400_000), _city(2, "B", 240_000), _city(3, "C", 243_000), _city(4, "D", 232_800), _city(5, "E", 243_700)]
        runner, _result, routes = _distribute(cities, useful_transfer_min=5000, shipment_cap=50_000)
        self.assertTrue(routes)
        self.assertEqual({r["from_city"] for r in routes}, {"1"})
        self.assertEqual(sum(r["wood"] for r in routes), 128_100)                # down to the average, not to the edge of the band
        self.assertTrue(_said(runner, "5 cidade(s) fora da faixa"))            # the pile pulls the average up: the others fall below the band

    def test_a_hole_is_filled_from_cities_that_are_inside_the_band(self):
        cities = [_city(1, "A", 250_000), _city(2, "B", 250_000), _city(3, "C", 250_000), _city(4, "D", 100_000)]
        _runner, _result, routes = _distribute(cities, useful_transfer_min=5000, shipment_cap=50_000)
        self.assertEqual({r["to_city"] for r in routes}, {"4"})
        self.assertEqual(sum(r["wood"] for r in routes), 112_500)

    def test_the_band_is_never_smaller_than_the_useful_minimum(self):
        # Carcereiro's crystal: about 3,000 per city; 10% would be 300
        cities = [_city(1, "A", 7_000), _city(2, "B", 1_000), _city(3, "C", 1_000)]
        runner, _result, routes = _distribute(cities, useful_transfer_min=5000)
        self.assertEqual(routes, [])
        self.assertTrue(_said(runner, "= 5,000 | 0 cidade(s) fora da faixa"))

    def test_the_biggest_imbalance_goes_first(self):
        # the real case: a pile of +87k of crystal waited four cycles behind 20,000-capped routes of other resources
        cities = [
            _stock(1, "A", wood=260_000, crystal=187_000),
            _stock(2, "B", wood=200_000, crystal=100_000),
            _stock(3, "C", wood=200_000, crystal=100_000),
            _stock(4, "D", wood=140_000, crystal=100_000),
        ]
        _runner, _result, routes = _distribute(
            cities, crystal=True, useful_transfer_min=5000, shipment_cap=20_000, max_routes_per_cycle=1,
        )
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["from_city"], "1")
        self.assertGreater(routes[0].get("crystal", 0), 0)                       # the crystal pile, not the wood that comes first in the list

    def test_other_strategies_ignore_the_tolerance(self):
        cities = [_city(1, "A", 251_500), _city(2, "B", 30_000)]
        _runner, _result, routes = _distribute(
            cities, strategy="targets", minimum_stock=20_000, target_stock=60_000, origin_reserve_stock=50_000, useful_transfer_min=5000,
        )
        self.assertEqual([_route(r) for r in routes], [("1", "2", 30_000)])


def _plan(*, requested, dispatched, free=40, queue=0, loading=60, travel=1200):
    amounts = lambda value: {name: (value if name == "wood" else 0) for name in RESOURCES}  # noqa: E731
    return SimpleNamespace(
        origin=SimpleNamespace(city_name="Origem", available_resources=amounts(90_000)),
        destination=SimpleNamespace(city_name="Destino", available_resources=amounts(0)),
        total_requested=requested, total_dispatched=dispatched, free_transporters=free,
        effective_ship_capacity=500, capacity_percent=100, ship_capacity=500,
        eta={"queue_seconds": queue, "loading_seconds": loading, "travel_seconds": travel, "total_seconds": queue + loading + travel},
        requested=amounts(requested), dispatched=amounts(dispatched), remaining=amounts(requested - dispatched),
    )


def _send(plan, *, distribution=True, feedbacks=None, ok=True, **inputs):
    runner = object.__new__(SendResourcesRunner)
    runner.hub = MagicMock()
    runner.hub.get_transport_support.return_value = {"entries": []}
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    runner.resolve_credentials = lambda *a, **k: {"email": "x"}
    runner.get_or_login_game_client = lambda *a, **k: MagicMock()
    runner.save_game_client = MagicMock()
    runner.ensure_status_refresh = MagicMock()
    runner.checkpoint = MagicMock()
    job_inputs = {"from_city": "1", "to_city": "2", "wood": plan.total_requested, **inputs}
    if distribution:
        job_inputs["distribution_route"] = True
    job = {"job_id": "j1", "account_id": "a1", "game_account_id": "g1", "inputs": job_inputs}
    answer = {"ok": ok, "feedbacks": feedbacks or ([{"type": 10}] if ok else [])}
    with patch.object(resources_runner, "prepare_transport", return_value=plan), \
            patch.object(resources_runner, "submit_transport", return_value=answer) as submit, \
            patch.object(resources_runner, "estimate_next_ship_availability",
                         return_value={"wait_seconds": 300, "chosen": None, "entries": [], "fallback_used": False}) as estimate:
        result = runner.execute(job)
    return runner, result, submit, estimate


class DispatchTests(unittest.TestCase):
    def test_a_route_with_no_ship_goes_back_to_the_plan_instead_of_re_running(self):
        # the real case: 5,216 "Sem capacidade imediata; reagendando" in ten days
        runner, result, submit, estimate = _send(_plan(requested=20_000, dispatched=0, free=0))

        submit.assert_not_called()
        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "nothing_dispatchable")
        self.assertFalse(getattr(result, "reschedule_seconds", 0))
        estimate.assert_not_called()                                    # no extra request to the game either
        runner.hub.spawn_job.assert_not_called()
        self.assertTrue(_said(runner, "nenhum mercante livre agora. A rota volta ao planejamento no próximo ciclo", "warn"))

    def test_a_send_asked_by_hand_still_waits_for_the_ships(self):
        _runner, result, submit, _estimate = _send(_plan(requested=20_000, dispatched=0, free=0), distribution=False)
        submit.assert_not_called()
        self.assertEqual((result.data["status"], result.reschedule_seconds), ("waiting_ships", 300))

    def test_the_part_that_did_not_fit_is_not_queued_as_its_own_shipment(self):
        runner, result, submit, _estimate = _send(_plan(requested=20_000, dispatched=15_000, free=30))

        submit.assert_called_once()
        self.assertEqual(result.data["status"], "partial_dispatch")
        self.assertEqual(result.data["remaining"]["wood"], 5_000)
        self.assertFalse(getattr(result, "reschedule_seconds", 0))
        self.assertEqual(runner.hub.spawn_job.call_count, 1)           # only the arrival monitor of what left
        self.assertEqual(runner.hub.spawn_job.call_args.kwargs["inputs"]["monitor_mode"], "arrival_check")
        self.assertTrue(_said(runner, "Restante de 5,000 não coube agora; fica para o próximo ciclo da distribuição"))

    def test_a_send_asked_by_hand_still_delivers_the_rest_later(self):
        _runner, result, _submit, _estimate = _send(_plan(requested=20_000, dispatched=15_000, free=30), distribution=False)
        self.assertEqual(result.data["status"], "partial_dispatch")
        self.assertGreater(result.reschedule_seconds, 0)
        self.assertEqual(result.reschedule_inputs["wood"], 5_000)

    def test_below_the_useful_minimum_nothing_leaves(self):
        _runner, result, submit, _estimate = _send(_plan(requested=20_000, dispatched=3_000, free=6), min_dispatch_total=5_000)
        submit.assert_not_called()
        self.assertEqual(result.data["status"], "dispatch_below_minimum")
        self.assertFalse(getattr(result, "reschedule_seconds", 0))

    def test_no_action_point_is_not_an_error_for_a_route(self):
        # the real case: 104 jobs in error in ten days with this answer from the game
        feedback = [{"type": 11, "text": "A cidade não tem mais pontos de ação disponíveis!"}]
        runner, result, submit, _estimate = _send(_plan(requested=20_000, dispatched=20_000), ok=False, feedbacks=feedback)

        submit.assert_called_once()
        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "no_action_points")
        runner.ensure_status_refresh.assert_called_once()               # so the next plan knows
        runner.hub.spawn_job.assert_not_called()
        self.assertTrue(_said(runner, "Origem está sem pontos de ação", "warn"))

    def test_for_a_send_asked_by_hand_it_still_is(self):
        feedback = [{"type": 11, "text": "A cidade não tem mais pontos de ação disponíveis!"}]
        _runner, result, _submit, _estimate = _send(_plan(requested=20_000, dispatched=20_000), ok=False, feedbacks=feedback, distribution=False)
        self.assertFalse(result.success)
        self.assertEqual(result.data["error"], "transport_submit_failed")

    def test_a_port_with_a_long_queue_is_left_alone(self):
        plan = _plan(requested=20_000, dispatched=20_000, queue=7_000, loading=1_000)
        runner, result, submit, _estimate = _send(plan, max_loading_seconds=7_200)

        submit.assert_not_called()
        self.assertEqual((result.data["status"], result.data["port_seconds"]), ("port_too_slow", 8_000))
        self.assertTrue(_said(runner, "o porto de Origem levaria 133 min (fila 116 min + carga 16 min)", "warn"))

        _runner, result, submit, _estimate = _send(_plan(requested=20_000, dispatched=20_000, queue=600, loading=1_000), max_loading_seconds=7_200)
        submit.assert_called_once()
        self.assertEqual(result.data["status"], "sent")

    def test_a_whole_shipment_goes_as_before(self):
        runner, result, submit, _estimate = _send(_plan(requested=20_000, dispatched=20_000), min_dispatch_total=5_000, max_loading_seconds=7_200)
        submit.assert_called_once()
        self.assertEqual(result.data["status"], "sent")
        runner.hub.patch_snapshot_ships.assert_called_once_with("g1", delta_transporters=-40)


if __name__ == "__main__":
    unittest.main()
