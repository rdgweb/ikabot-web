"""N-159: the Manter surplus goes to the main army, the crumbs are dismissed, units without a target stay."""
import unittest

from runners.military import TrainUnitsRunner


class FakeHub:
    def __init__(self):
        self.spawned = []
        self.logs = []

    def spawn_job(self, parent, action_code, inputs, **kwargs):
        self.spawned.append((action_code, inputs))
        return {"job_id": "x"}

    def report_log(self, job_id, level, msg):
        self.logs.append((level, msg))


def handle(inputs, surplus, targets, current):
    runner = TrainUnitsRunner.__new__(TrainUnitsRunner)
    runner.hub = FakeHub()
    names = {1: "Norte", 2: "Sul", 3: "Leste"}
    runner._handle_surplus("j", inputs, surplus, targets, current, names, {303: "Hoplita", 301: "Fundeiro"})
    return runner.hub.spawned


TARGETS = {1: {"303": 100}, 2: {"303": 100}, 3: {"303": 100}}


class MaintainSurplusTests(unittest.TestCase):
    def test_keep_leaves_everything(self):
        self.assertEqual(handle({}, {2: {"303": 50}}, TARGETS, {}), [])

    def test_gather_sends_the_surplus_to_the_chosen_main_army(self):
        spawned = handle({"surplus_mode": "gather", "consolidate_city_id": "1"},
                         {1: {"303": 9}, 2: {"303": 50}, 3: {"303": 4}}, TARGETS, {})
        self.assertEqual(spawned, [(1202, {"from_city_id": 2, "to_city_id": 1, "units": {"303": 50}}),
                                   (1202, {"from_city_id": 3, "to_city_id": 1, "units": {"303": 4}})])

    def test_gather_dismiss_dismisses_what_is_below_the_percentage(self):
        spawned = handle({"surplus_mode": "gather_dismiss", "consolidate_city_id": "1", "surplus_min_pct": 20},
                         {2: {"303": 50}, 3: {"303": 4}}, TARGETS, {})
        self.assertEqual(spawned, [(1202, {"from_city_id": 2, "to_city_id": 1, "units": {"303": 50}}),
                                   (1204, {"dismiss": {"3": {"303": 4}}, "confirm": True})])

    def test_units_without_a_target_in_the_city_are_never_touched(self):
        spawned = handle({"surplus_mode": "gather_dismiss", "consolidate_city_id": "1"},
                         {2: {"301": 30}}, TARGETS, {})
        self.assertEqual(spawned, [])

    def test_without_a_chosen_city_the_largest_army_is_the_main_one(self):
        spawned = handle({"surplus_mode": "gather"}, {2: {"303": 50}, 3: {"303": 10}}, TARGETS,
                         {1: {"303": 80}, 2: {"303": 150}, 3: {"303": 110}})
        self.assertEqual(spawned, [(1202, {"from_city_id": 3, "to_city_id": 2, "units": {"303": 10}})])


if __name__ == "__main__":
    unittest.main()
