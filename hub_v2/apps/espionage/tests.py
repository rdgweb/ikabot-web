import unittest

from apps.espionage.services.battle_land import recommend_attack_force, simulate_land_battle


class BattleLandTests(unittest.TestCase):
    def test_reinforcements_count_toward_total_committed(self):
        sim = simulate_land_battle(
            attacker_units={303: 10},
            defender_units={303: 10},
            town_hall_level=1,
            wall_level=0,
            max_rounds=1,
            attacker_reinforcements_by_round={1: {303: 10}},
        )

        self.assertEqual(sim["attacker_initial"], 20)
        self.assertLessEqual(sim["attacker_survivors_pct"], 100.0)

    def test_hephaestus_bonus_changes_outcome(self):
        base = simulate_land_battle(
            attacker_units={303: 60},
            defender_units={303: 60},
            town_hall_level=5,
            wall_level=0,
            max_rounds=6,
        )
        buffed = simulate_land_battle(
            attacker_units={303: 60},
            defender_units={303: 60},
            town_hall_level=5,
            wall_level=0,
            max_rounds=6,
            attacker_damage_bonus_pct=15.0,
            attacker_armor_bonus=20,
        )

        self.assertGreaterEqual(buffed["attacker_survivors_pct"], base["attacker_survivors_pct"])

    def test_empty_defender_recommends_minimal_force(self):
        rec = recommend_attack_force(
            available_units={303: 500, 305: 20},
            defender_units={},
        )

        self.assertTrue(rec["can_win"])
        self.assertEqual(rec["recommended"], {303: 30, 305: 6})

    def test_size_aware_field_prevents_steam_giant_overfill(self):
        sim = simulate_land_battle(
            attacker_units={308: 100},
            defender_units={303: 1},
            town_hall_level=1,
            wall_level=0,
            max_rounds=1,
        )

        round_1 = sim["details"]["rounds"][0]
        self.assertEqual(round_1["attacker_principal"], 30)

    def test_cristaleira_like_case_is_not_one_round_stomp(self):
        sim = simulate_land_battle(
            attacker_units={303: 30, 305: 1},
            defender_units={303: 20, 315: 16, 313: 30},
            town_hall_level=5,
            wall_level=5,
            max_rounds=12,
        )

        self.assertEqual(sim["winner"], "attacker")
        self.assertGreaterEqual(sim["rounds"], 3)
        self.assertLess(sim["attacker_survivors_pct"], 100.0)
