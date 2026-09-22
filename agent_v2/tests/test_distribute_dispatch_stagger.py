"""N-44: consecutive fleet dispatches spawned by DistributeResourcesRunner (ac=3,
routes) must not all fire at once — a widening random delay is added to each
dispatch after the first, so the batch loses its metronome-even cadence."""

import unittest
from unittest.mock import MagicMock, patch

from runners.resources import DistributeResourcesRunner


class NextDispatchDelayTests(unittest.TestCase):
    """The pure accumulator math — this is the actual N-44 logic."""

    def test_first_dispatch_is_never_delayed(self):
        self.assertEqual(DistributeResourcesRunner._next_dispatch_delay(0.0, 0, True), 0.0)

    def test_disabled_never_adds_delay_regardless_of_count(self):
        self.assertEqual(DistributeResourcesRunner._next_dispatch_delay(0.0, 1, False), 0.0)
        self.assertEqual(DistributeResourcesRunner._next_dispatch_delay(12.0, 4, False), 12.0)

    @patch("runners.resources.random.uniform")
    def test_window_widens_with_each_prior_dispatch(self, uniform):
        uniform.return_value = 0  # isolate the window bounds passed in, not the draw

        DistributeResourcesRunner._next_dispatch_delay(0.0, 1, True)
        uniform.assert_called_with(5, 40)  # dispatch #2: n=1 -> [5*1, 40*1]

        DistributeResourcesRunner._next_dispatch_delay(0.0, 2, True)
        uniform.assert_called_with(10, 80)  # dispatch #3: n=2 -> [5*2, 40*2]

        DistributeResourcesRunner._next_dispatch_delay(0.0, 3, True)
        uniform.assert_called_with(15, 120)  # dispatch #4: n=3 -> [5*3, 40*3]

    def test_delay_accumulates_on_top_of_the_running_total(self):
        with patch("runners.resources.random.uniform", return_value=10.0):
            total = DistributeResourcesRunner._next_dispatch_delay(0.0, 0, True)  # dispatch 1: 0
            total = DistributeResourcesRunner._next_dispatch_delay(total, 1, True)  # dispatch 2: +10
            total = DistributeResourcesRunner._next_dispatch_delay(total, 2, True)  # dispatch 3: +10

        self.assertEqual(total, 20.0)

    def test_real_rng_growth_is_strictly_widening_on_average(self):
        # Coarse sanity check with the real RNG: later windows should, on average,
        # add more than earlier ones (not a hard per-call guarantee, since both are
        # random — averaged over many draws it must hold).
        import statistics

        early = [DistributeResourcesRunner._next_dispatch_delay(0.0, 1, True) for _ in range(500)]
        late = [DistributeResourcesRunner._next_dispatch_delay(0.0, 4, True) for _ in range(500)]

        self.assertGreater(statistics.mean(late), statistics.mean(early))


class DistributeRunnerDispatchWiringTests(unittest.TestCase):
    """Confirms execute() actually threads the accumulated delay into each
    hub.spawn_job() call, across several routes in one distribution cycle."""

    def _city(self, city_id):
        return {
            "id": city_id, "name": f"City {city_id}",
            "max_resources": {"resource": 10**9, "1": 10**9, "2": 10**9, "3": 10**9, "4": 10**9},
            "current_resources": {}, "wood": 0, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0,
        }

    def _run(self, num_routes: int):
        runner = DistributeResourcesRunner(hub=MagicMock(), sessions=MagicMock())
        runner.log = MagicMock()
        runner._get_snapshot = MagicMock(return_value={
            "cities": [self._city(str(i)) for i in range(num_routes + 1)],
            "base_snapshot": {"free_freighters": 0},
        })
        runner.is_snapshot_stale = MagicMock(return_value=False)
        runner._get_construction_reservations = MagicMock(return_value={})
        runner._build_distribution_plan = MagicMock(return_value=({}, {"logs": []}))
        runner._get_active_transport_entries = MagicMock(return_value=[])
        runner._subtract_active_transport_support = MagicMock(
            side_effect=lambda active_entries, *, from_city, to_city, use_freighters, resources: (resources, {}),
        )
        runner._choose_next_distribution_check = MagicMock(return_value=0)
        runner.save_game_client = MagicMock()

        routes = [
            {
                "from_city": "0", "from_city_name": "City 0",
                "to_city": str(i), "to_city_name": f"City {i}",
                "resources": {"wood": 10000}, "total": 10000, "critical": False,
            }
            for i in range(1, num_routes + 1)
        ]
        runner._route_entries_from_allocations = MagicMock(return_value=routes)

        job = {
            "job_id": "job-1", "account_id": "acc-1", "game_account_id": "ga-1",
            "inputs": {
                "wood": True, "cities": [str(i) for i in range(num_routes + 1)],
                "max_routes_per_cycle": num_routes, "loop_enabled": False,
            },
        }
        with patch("runners.resources.split_shipment", return_value=[({"wood": 10000}, False)]):
            runner.execute(job)
        return runner

    @patch("runners.resources.random.uniform", return_value=10.0)
    def test_delay_seconds_grows_across_consecutive_route_dispatches(self, _uniform):
        runner = self._run(num_routes=3)

        delays = [call.kwargs["delay_seconds"] for call in runner.hub.spawn_job.call_args_list]
        self.assertEqual(delays, [0, 10, 20])  # 0, +10, +10 (cumulative, per the mocked draw)

    def test_stagger_can_be_disabled_via_job_input(self):
        runner = DistributeResourcesRunner(hub=MagicMock(), sessions=MagicMock())
        runner.log = MagicMock()
        runner._get_snapshot = MagicMock(return_value={
            "cities": [self._city(str(i)) for i in range(3)],
            "base_snapshot": {"free_freighters": 0},
        })
        runner.is_snapshot_stale = MagicMock(return_value=False)
        runner._get_construction_reservations = MagicMock(return_value={})
        runner._build_distribution_plan = MagicMock(return_value=({}, {"logs": []}))
        runner._get_active_transport_entries = MagicMock(return_value=[])
        runner._subtract_active_transport_support = MagicMock(
            side_effect=lambda active_entries, *, from_city, to_city, use_freighters, resources: (resources, {}),
        )
        runner._choose_next_distribution_check = MagicMock(return_value=0)
        runner.save_game_client = MagicMock()
        runner._route_entries_from_allocations = MagicMock(return_value=[
            {"from_city": "0", "from_city_name": "C0", "to_city": "1", "to_city_name": "C1",
             "resources": {"wood": 10000}, "total": 10000, "critical": False},
            {"from_city": "0", "from_city_name": "C0", "to_city": "2", "to_city_name": "C2",
             "resources": {"wood": 10000}, "total": 10000, "critical": False},
        ])

        job = {
            "job_id": "job-1", "account_id": "acc-1", "game_account_id": "ga-1",
            "inputs": {
                "wood": True, "cities": ["0", "1", "2"], "max_routes_per_cycle": 2,
                "loop_enabled": False, "dispatch_stagger": 0,
            },
        }
        with patch("runners.resources.split_shipment", return_value=[({"wood": 10000}, False)]):
            runner.execute(job)

        delays = [call.kwargs["delay_seconds"] for call in runner.hub.spawn_job.call_args_list]
        self.assertEqual(delays, [0, 0])


if __name__ == "__main__":
    unittest.main()
