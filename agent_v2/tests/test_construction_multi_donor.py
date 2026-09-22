"""N-45/N-46: construction transport (ac=1002) must fall back to multiple donor
cities when the single best donor can't fully cover the need, and should round
shipped amounts up (clamped to the donor's real surplus) instead of sending the
exact-need amount."""

import unittest

from tests.test_construction import ConstructionPlanRunner


def _city(city_id, name, **resources):
    base = {"id": city_id, "name": name, "wood": 0, "wine": 0, "marble": 0, "crystal": 0, "sulfur": 0}
    base.update(resources)
    return base


class ScoreDonorCandidateTests(unittest.TestCase):
    def test_sums_surplus_across_needed_resources_above_the_reserve_guard(self):
        city = _city("1", "Donor", marble=10_000, wood=8_000)
        needed = {"marble": 999, "wood": 999, "wine": 0}

        score = ConstructionPlanRunner._score_donor_candidate(city, needed, {})

        # marble: 10000-5000=5000, wood: 8000-5000=3000, wine ignored (need<=0)
        self.assertEqual(score, 8000)

    def test_reserved_local_reduces_the_score(self):
        city = _city("1", "Donor", marble=10_000)
        needed = {"marble": 999}
        reserved = {"1": {"marble": {"reserved_local": 3000}}}

        score = ConstructionPlanRunner._score_donor_candidate(city, needed, reserved)

        self.assertEqual(score, 2000)  # 10000 - 3000 reserved - 5000 default guard

    def test_stock_at_or_below_the_reserve_scores_zero_not_negative(self):
        city = _city("1", "Donor", marble=4_000)

        score = ConstructionPlanRunner._score_donor_candidate(city, {"marble": 999}, {})

        self.assertEqual(score, 0)


class TakeDonorContributionTests(unittest.TestCase):
    def test_takes_the_lesser_of_need_and_available_without_rounding(self):
        city = _city("1", "Donor", marble=20_000)

        taken = ConstructionPlanRunner._take_donor_contribution(city, {"marble": 3_000}, {}, round_to=0)

        self.assertEqual(taken, {"marble": 3_000})

    def test_rounds_up_to_the_configured_multiple(self):
        city = _city("1", "Donor", marble=20_000)  # plenty of headroom

        taken = ConstructionPlanRunner._take_donor_contribution(city, {"marble": 3_050}, {}, round_to=100)

        self.assertEqual(taken, {"marble": 3_100})  # ceil(3050/100)*100

    def test_rounding_never_exceeds_the_donor_real_surplus(self):
        # available = 6000 - 5000 (default reserve) = 1000; need rounds to 1100 but
        # the donor can only actually spare 1000 — the real surplus always wins.
        city = _city("1", "Donor", marble=6_000)

        taken = ConstructionPlanRunner._take_donor_contribution(city, {"marble": 1_050}, {}, round_to=100)

        self.assertEqual(taken, {"marble": 1_000})

    def test_skips_resources_with_no_remaining_need_or_no_surplus(self):
        city = _city("1", "Donor", marble=20_000, wood=1_000)  # wood below reserve

        taken = ConstructionPlanRunner._take_donor_contribution(
            city, {"marble": 5_000, "wood": 500, "wine": 0}, {}, round_to=0,
        )

        self.assertEqual(taken, {"marble": 5_000})


class SpawnTransportCoverMultiDonorTests(unittest.TestCase):
    def _runner(self):
        runner = ConstructionPlanRunner.__new__(ConstructionPlanRunner)
        spawn_calls = []
        logs = []
        runner.hub = type("Hub", (), {"spawn_job": staticmethod(
            lambda job_id, action_code, inputs: spawn_calls.append(inputs)
        )})()
        runner.log = lambda jid, level, msg: logs.append((level, msg))
        runner._get_snapshot = lambda jid, ga_id: None
        runner._spawn_calls = spawn_calls
        runner._logs = logs
        return runner

    def _pending(self, marble_cost, next_level=5):
        return {
            "city_name": "Target",
            "next_level": next_level,
            "level_rows": [{"level": next_level, "costs": {"marble": marble_cost}}],
        }

    def test_single_donor_that_fully_covers_behaves_like_before(self):
        runner = self._runner()
        target = _city("t", "Target", marble=0)
        donor = _city("1", "Donor", marble=50_000)

        result = runner._spawn_transport_cover(
            job_id="j1", cities=[target, donor], target_city=target,
            pending=self._pending(12_000), missing={}, support_by_city={},
            transport_round_to=0,
        )

        self.assertTrue(result)
        self.assertEqual(len(runner._spawn_calls), 1)
        self.assertEqual(runner._spawn_calls[0]["marble"], 12_000)
        self.assertEqual(runner._spawn_calls[0]["from_city"], "1")
        self.assertFalse(any("doador multiplo" in msg for _, msg in runner._logs))
        self.assertEqual(donor["marble"], 38_000)  # debited locally

    def test_falls_back_to_a_second_donor_when_the_first_does_not_fully_cover(self):
        runner = self._runner()
        target = _city("t", "Target", marble=0)
        donor_a = _city("1", "Donor A", marble=13_000)   # spare: 13000-5000=8000
        donor_b = _city("2", "Donor B", marble=10_000)   # spare: 10000-5000=5000

        result = runner._spawn_transport_cover(
            job_id="j1", cities=[target, donor_a, donor_b], target_city=target,
            pending=self._pending(12_000), missing={}, support_by_city={},
            transport_round_to=0,
        )

        self.assertTrue(result)
        # Donor A (higher surplus, picked first) supplies 8000, donor B supplies
        # the remaining 4000 — nobody has to cover the full 12000 alone.
        by_from_city = {c["from_city"]: c["marble"] for c in runner._spawn_calls}
        self.assertEqual(by_from_city, {"1": 8000, "2": 4000})
        self.assertTrue(any("doador multiplo" in msg for _, msg in runner._logs))
        self.assertEqual(donor_a["marble"], 5_000)
        self.assertEqual(donor_b["marble"], 6_000)

    def test_stops_at_three_donors_even_if_still_short(self):
        runner = self._runner()
        target = _city("t", "Target", marble=0)
        donors = [_city(str(i), f"Donor {i}", marble=6_000) for i in range(1, 5)]  # 4 donors, 1000 spare each

        result = runner._spawn_transport_cover(
            job_id="j1", cities=[target, *donors], target_city=target,
            pending=self._pending(10_000), missing={}, support_by_city={},
            transport_round_to=0,
        )

        self.assertTrue(result)  # partial cover from 3 donors is still a success
        self.assertEqual(len(runner._spawn_calls), 3)
        self.assertEqual(sum(c["marble"] for c in runner._spawn_calls), 3_000)

    def test_no_donor_with_any_surplus_fails_with_the_original_warning(self):
        runner = self._runner()
        target = _city("t", "Target", marble=0)
        donor = _city("1", "Donor", marble=4_000)  # at/below the default reserve

        result = runner._spawn_transport_cover(
            job_id="j1", cities=[target, donor], target_city=target,
            pending=self._pending(12_000), missing={}, support_by_city={},
            transport_round_to=0,
        )

        self.assertFalse(result)
        self.assertEqual(runner._spawn_calls, [])
        self.assertTrue(any("Nenhuma cidade doadora com sobra util" in msg for _, msg in runner._logs))

    def test_rounding_applies_across_the_whole_multi_donor_flow(self):
        runner = self._runner()
        target = _city("t", "Target", marble=0)
        donor = _city("1", "Donor", marble=50_000)

        runner._spawn_transport_cover(
            job_id="j1", cities=[target, donor], target_city=target,
            pending=self._pending(12_437), missing={}, support_by_city={},
            transport_round_to=100,
        )

        self.assertEqual(runner._spawn_calls[0]["marble"], 12_500)  # ceil(12437/100)*100


if __name__ == "__main__":
    unittest.main()
