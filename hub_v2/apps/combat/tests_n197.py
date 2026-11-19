"""N-197: the simulator's rules read from the barbarian reports round by round, the adjustment learned per kind of unit,
the planner's margin for a village with both kinds, and the round by round comparison kept with each check."""
import json

from django.test import TestCase

from apps.combat.services import barbarian_learning, barbarian_planner, divergence
from apps.espionage.services import battle_land

UPGRADES = {302: {"offensive": 6, "defensive": 8}, 304: {"offensive": 3, "defensive": 3},
            305: {"offensive": 3, "defensive": 4}, 308: {"offensive": 4, "defensive": 4}}


def sim(attacker, defender, **kw):
    kw.setdefault("town_hall_level", 5)
    kw.setdefault("max_rounds", 12)
    return battle_land.simulate_land_battle(attacker, defender, calibrated=False, **kw)


class RulesTests(TestCase):
    def test_a_hit_takes_one_unit_at_most(self):
        # 10 steam giants (some 700 a hit) against club men of 240: the round costs the village 10 at most, not 29
        first = sim({308: 10}, {320: 100}, wall_level=0)["details"]["rounds"][0]
        self.assertGreaterEqual(first["defender_alive"][320], 90)

    def test_rams_stand_in_the_artillery_and_swing_the_axe_at_troops(self):
        result = sim({302: 60, 304: 35, 305: 12, 308: 50}, {320: 300, 324: 16}, attacker_upgrades=UPGRADES, wall_level=8)
        self.assertIn(324, result["details"]["rounds"][0]["defender_start_lines"]["artilharia"])
        # the real battle lost nothing; with the 2100 of the ram on the giants the simulator lost 6 (one is left: it
        # pools a slot's damage where the game spreads it over the ten giants)
        self.assertLessEqual(sum(result["attacker_losses"].values()), 1)
        self.assertEqual(result["winner"], "attacker")

    def test_the_barbarians_come_through_the_sections_already_down(self):
        rounds = sim({303: 207, 304: 74, 305: 24, 302: 60, 308: 25}, {316: 400, 324: 22}, wall_level=14)["details"]["rounds"]
        opened = next(i for i, r in enumerate(rounds) if 0 < r["wall_sections"] < 5)
        following = rounds[opened + 1]["defender_start_lines"]["principal"]
        self.assertTrue(following.get(316))
        self.assertLessEqual(following[316], (5 - rounds[opened]["wall_sections"]) * 30)

    def test_the_village_has_no_flanks(self):
        for r in sim({308: 50, 302: 60}, {320: 300}, wall_level=0)["details"]["rounds"]:
            self.assertFalse(r["defender_start_lines"].get("flancos"))

    def test_a_shooter_out_of_shots_takes_an_empty_front_slot(self):
        rounds = sim({308: 25, 304: 37}, {320: 300}, attacker_upgrades=UPGRADES, wall_level=0)["details"]["rounds"]
        self.assertNotIn(304, rounds[0]["attacker_start_lines"]["principal"])     # three rounds of shots first
        self.assertTrue(any(304 in r["attacker_start_lines"]["principal"] for r in rounds[3:]))

    def test_shooters_fight_on_without_a_front(self):
        self.assertEqual(sim({303: 1, 304: 22}, {320: 50}, town_hall_level=1, wall_level=0)["winner"], "attacker")

    def test_club_men_take_the_front_first_in_a_village_with_both(self):
        rounds = sim({308: 100, 305: 24}, {320: 100, 316: 250, 324: 20}, wall_level=12)["details"]["rounds"]
        front = next(r["defender_start_lines"]["principal"] for r in rounds if r["defender_start_lines"].get("principal"))
        self.assertIn(320, front)


class LearnedPerUnitTests(TestCase):
    def setUp(self):
        barbarian_learning._CACHE.update({"at": 1e18, "value": None})

    def tearDown(self):
        barbarian_learning._CACHE.update({"at": 0.0, "value": None})

    def _use(self, bands):
        barbarian_learning._CACHE.update({"at": 1e18, "value": {"bands": bands}})

    def test_each_unit_takes_its_own_kind(self):
        self._use({"club": {"attacker_pct": -25, "defender_pct": -15, "wall_armor_pct": 65, "engine": barbarian_learning.ENGINE},
                   "viking": {"attacker_pct": 20, "defender_pct": 35, "wall_armor_pct": 115, "engine": barbarian_learning.ENGINE}})
        units, wall = barbarian_learning.unit_adjustment({320: 200, 316: 125, 324: 18})
        self.assertEqual(units[320], (-25.0, -15.0))
        self.assertEqual(units[316], (20.0, 35.0))
        self.assertEqual(units[324], (-25.0, -15.0))                      # the rams go with the village's main kind
        self.assertEqual(wall, 65.0)

    def test_what_was_learned_on_the_old_rules_is_not_used(self):
        self._use({"club": {"attacker_pct": -55, "defender_pct": -75, "wall_armor_pct": 85}})
        self.assertEqual(barbarian_learning.unit_adjustment({320: 300}), ({}, None))

    def test_each_kind_is_learned_from_the_villages_with_only_it(self):
        self.assertEqual(barbarian_learning.pure_band({320: 300, 324: 16}), "club")
        self.assertEqual(barbarian_learning.pure_band({316: 400, 324: 22}), "viking")
        self.assertIsNone(barbarian_learning.pure_band({320: 200, 316: 125}))


class PlannerMarginTests(TestCase):
    def test_a_village_with_both_kinds_is_planned_with_a_margin(self):
        out = barbarian_planner.plan({308: 150, 305: 24, 302: 120}, {320: 100, 316: 250, 324: 20}, wall_level=12,
                                     explain_training=False)
        self.assertEqual(out.get("margin_pct"), barbarian_planner.MIXED_MARGIN_PCT)
        if out["can_win"]:
            self.assertGreaterEqual(int(out["prediction"].get("morale_end") or 0), barbarian_planner.MIXED_MORALE_FLOOR)

    def test_a_village_of_one_kind_has_none(self):
        out = barbarian_planner.plan({308: 50, 305: 12}, {320: 150, 324: 4}, wall_level=4, explain_training=False)
        self.assertNotIn("margin_pct", out)


class RoundComparisonTests(TestCase):
    def test_a_unit_in_another_line_and_too_many_alive_are_kept(self):
        real = [{"attacker": {"zones": {"main": [{"unit": 304, "left": 7, "lost": 0}]}, "reserve": []},
                 "defender": {"zones": {"main": [{"unit": 320, "left": 10, "lost": 20}]}, "reserve": []}}]
        simulated = {"details": {"rounds": [{"attacker_start_lines": {"principal": {}}, "attacker_alive": {304: 7},
                                             "defender_start_lines": {"principal": {320: 30}}, "defender_alive": {320: 30}}]}}
        found = divergence.compare(real, simulated, {"attacker": {304: 7}, "defender": {320: 30}})
        kinds = {(f["kind"], f["unit"]) for f in found}
        self.assertIn(("linha", 304), kinds)                              # the game had it at the front, the simulator behind
        self.assertIn(("vivos", 320), kinds)                              # 10 left in the game, 30 in the simulator
        rows = divergence.summary([type("C", (), {"predicted_json": {"divergences": found}, "report_id": None})()])
        self.assertTrue(any("linha de frente" in r["text"] for r in rows))
        json.dumps(found)
