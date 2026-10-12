"""N-101: a military page that could not be read is not "zero units"."""

import unittest

from runners.check_status import keep_unread_cities, merge_unread_military

PREVIOUS = {
    "troops": {"Hoplita": 137, "Médico": 32},
    "fleet": {"Trireme": 5},
    "by_city": [
        {"city_id": 1, "troops": {"Hoplita": 100}, "fleet": {"Trireme": 5}, "occupation_forces": [], "blockade_forces": []},
        {"city_id": 2, "troops": {"Hoplita": 37, "Médico": 32}, "fleet": {}, "occupation_forces": [], "blockade_forces": []},
    ],
}


class MergeUnreadMilitaryTests(unittest.TestCase):
    def test_everything_read_is_kept_as_it_is(self):
        current = {"troops": {"Hoplita": 5}, "fleet": {}, "by_city": [{"city_id": 1, "troops": {"Hoplita": 5}, "fleet": {}}]}
        self.assertEqual(merge_unread_military(current, PREVIOUS), current)

    def test_the_case_of_07_10_no_city_could_be_read(self):
        # what the agent used to save: 0 troops, 0 ships, for four days
        current = {"troops": {}, "fleet": {}, "by_city": [], "unread_city_ids": [1, 2]}
        merged = merge_unread_military(current, PREVIOUS)
        self.assertEqual(merged["troops"], {"Hoplita": 137, "Médico": 32})
        self.assertEqual(merged["fleet"], {"Trireme": 5})
        self.assertEqual([(c["city_id"], c["stale"]) for c in merged["by_city"]], [(1, True), (2, True)])
        self.assertEqual(merged["unread_city_ids"], [1, 2])

    def test_one_city_unread_keeps_its_last_numbers_and_the_others_are_fresh(self):
        current = {
            "troops": {"Hoplita": 90}, "fleet": {"Trireme": 4},
            "by_city": [{"city_id": 1, "troops": {"Hoplita": 90}, "fleet": {"Trireme": 4}}],
            "unread_city_ids": [2],
        }
        merged = merge_unread_military(current, PREVIOUS)
        self.assertEqual(merged["troops"], {"Hoplita": 127, "Médico": 32})       # 90 fresh + 37 kept
        self.assertEqual(merged["fleet"], {"Trireme": 4})
        self.assertNotIn("stale", merged["by_city"][0])
        self.assertTrue(merged["by_city"][1]["stale"])

    def test_a_city_that_really_has_no_units_is_zero(self):
        current = {"troops": {}, "fleet": {}, "by_city": [{"city_id": 1, "troops": {}, "fleet": {}}, {"city_id": 2, "troops": {}, "fleet": {}}]}
        merged = merge_unread_military(current, PREVIOUS)
        self.assertEqual((merged["troops"], merged["fleet"]), ({}, {}))             # read, and empty: the army is gone
        self.assertNotIn("unread_city_ids", merged)

    def test_unread_with_nothing_known_before_stays_unknown(self):
        merged = merge_unread_military({"troops": {}, "fleet": {}, "by_city": [], "unread_city_ids": [9]}, None)
        self.assertEqual((merged["troops"], merged["by_city"], merged["unread_city_ids"]), ({}, [], [9]))
        self.assertEqual(merge_unread_military(None, PREVIOUS), {})


class KeepUnreadCitiesTests(unittest.TestCase):
    IDS = [{"id": 1}, {"id": 2}, {"id": 3}]
    BEFORE = {"1": {"id": 1, "name": "A", "wood": 10}, "2": {"id": 2, "name": "B", "wood": 20}, "9": {"id": 9, "name": "Abandonada", "wood": 5}}

    def test_a_city_that_could_not_be_read_keeps_its_last_data_in_the_game_order(self):
        # the case of 07/10: 9 cities listed, 7 read
        fresh = [{"id": 3, "name": "C", "wood": 33}, {"id": 1, "name": "A", "wood": 11}]
        cities, kept = keep_unread_cities(fresh, self.IDS, self.BEFORE)
        self.assertEqual([(c["id"], c["wood"], c.get("stale", False)) for c in cities], [(1, 11, False), (2, 20, True), (3, 33, False)])
        self.assertEqual(kept, ["2"])

    def test_a_city_that_is_gone_from_the_game_is_not_kept(self):
        cities, kept = keep_unread_cities([{"id": 1, "name": "A"}], [{"id": 1}], self.BEFORE)
        self.assertEqual(([c["id"] for c in cities], kept), ([1], []))

    def test_a_new_city_unread_and_never_seen_is_simply_absent(self):
        cities, kept = keep_unread_cities([], [{"id": 7}], self.BEFORE)
        self.assertEqual((cities, kept), ([], []))

    def test_everything_read_changes_nothing(self):
        fresh = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}]
        cities, kept = keep_unread_cities(fresh, self.IDS, {})
        self.assertEqual((cities, kept), (fresh, []))


if __name__ == "__main__":
    unittest.main()
