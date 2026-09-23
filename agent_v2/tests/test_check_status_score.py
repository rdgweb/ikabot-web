"""N-53: Check Status reads the account's own total score out of the island
view (avatarScores) so the game panel can show it next to the gold."""

import unittest

from runners.check_status import _own_player_score


def _island(own_city_id="100", owner_id="555", scores=None):
    return {
        "cities": [
            {"id": "99", "owner_id": "777"},
            {"id": own_city_id, "owner_id": owner_id},
        ],
        "avatar_scores": scores if scores is not None else {
            "555": {"place": 3057, "building_score": 267381, "research_score": 120559, "army_score": 967},
            "777": {"place": 1, "building_score": 999999, "research_score": 999999, "army_score": 999999},
        },
    }


class OwnPlayerScoreTests(unittest.TestCase):
    def test_picks_the_owner_of_the_given_city_and_sums_the_three_scores(self):
        score = _own_player_score(_island(), "100")

        self.assertEqual(score, {
            "total": 267381 + 120559 + 967,
            "building": 267381,
            "research": 120559,
            "army": 967,
            "place": 3057,
        })

    def test_matches_city_id_given_as_int(self):
        self.assertEqual(_own_player_score(_island(), 100)["place"], 3057)

    def test_returns_none_when_city_is_not_on_the_island(self):
        self.assertIsNone(_own_player_score(_island(), "404"))

    def test_returns_none_when_owner_has_no_score_entry(self):
        self.assertIsNone(_own_player_score(_island(scores={}), "100"))


if __name__ == "__main__":
    unittest.main()
