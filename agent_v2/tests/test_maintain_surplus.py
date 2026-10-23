"""N-159: the Manter surplus goes to the main army (units without a target too), the crumbs are dismissed."""
import unittest

from runners.military import TrainUnitsRunner


class FakeHub:
    def __init__(self, by_city=None):
        self.spawned = []
        self.logs = []
        self.by_city = by_city or []

    def get_snapshot(self, game_account_id=None):
        return {"military": {"by_city": self.by_city}}

    def spawn_job(self, parent, action_code, inputs, **kwargs):
        self.spawned.append((action_code, inputs))
        return {"job_id": "x"}

    def report_log(self, job_id, level, msg):
        self.logs.append((level, msg))


def handle(inputs, surplus, targets, current, by_city=None):
    runner = TrainUnitsRunner.__new__(TrainUnitsRunner)
    runner.hub = FakeHub(by_city)
    names = {1: "Norte", 2: "Sul", 3: "Leste"}
    runner._handle_surplus("j", inputs, surplus, targets, current, names, {303: "Hoplita", 301: "Fundeiro"}, "ga")
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

    def test_units_without_a_target_go_to_the_main_army_and_are_never_dismissed(self):
        # the city keeps only what was asked: cooks and medics without a target go too, whatever the percentage
        spawned = handle({"surplus_mode": "gather_dismiss", "consolidate_city_id": "1"},
                         {2: {"301": 3, "303": 4}}, TARGETS, {})
        self.assertEqual(spawned, [(1202, {"from_city_id": 2, "to_city_id": 1, "units": {"301": 3}}),
                                   (1204, {"dismiss": {"2": {"303": 4}}, "confirm": True})])

    def test_without_a_chosen_city_the_city_with_most_troops_is_the_main_one_for_troops_and_ships(self):
        by_city = [{"city_id": 1, "troops": {"Hoplita": 80}}, {"city_id": 2, "troops": {"Hoplita": 150, "Médico": 5}},
                   {"city_id": 3, "troops": {"Hoplita": 110}}]
        spawned = handle({"surplus_mode": "gather"}, {2: {"303": 50}, 3: {"303": 10}}, TARGETS, {}, by_city)
        self.assertEqual(spawned, [(1202, {"from_city_id": 3, "to_city_id": 2, "units": {"303": 10}})])
        ships = handle({"surplus_mode": "gather"}, {1: {"216": 7}}, {1: {"216": 8}, 2: {"216": 8}}, {}, by_city)
        self.assertEqual(ships, [(1202, {"from_city_id": 1, "to_city_id": 2, "units": {"216": 7}})])


if __name__ == "__main__":
    unittest.main()
