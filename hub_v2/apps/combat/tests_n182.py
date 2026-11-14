"""N-182: the simulator against barbarian villages — their units, the wall as the village's whole front in the first
round, the adjustment learned from barbarian battles alone, and the planner the job asks before each attack."""
from types import SimpleNamespace

from django.test import TestCase

from apps.combat.services import barbarian_learning, barbarian_planner
from apps.espionage.services import battle_land

UPGRADES = {302: {"offensive": 6, "defensive": 8}, 304: {"offensive": 3, "defensive": 3},
            305: {"offensive": 3, "defensive": 4}, 308: {"offensive": 4, "defensive": 4}}
LEVEL_19 = {302: 60, 304: 35, 305: 12, 308: 50}          # the army that won level 19 in four rounds


class BarbarianFieldTests(TestCase):
    def test_while_the_wall_stands_it_is_the_front_and_the_barbarians_wait(self):
        sim = battle_land.simulate_land_battle(LEVEL_19, {320: 300, 324: 16}, attacker_upgrades=UPGRADES, town_hall_level=5,
                                               wall_level=8, max_rounds=12, calibrated=False)
        first = sim["details"]["rounds"][0]
        self.assertEqual(first["defender_lines"].get("principal") or {}, {})
        self.assertEqual(first["defender_lines"].get("flancos") or {}, {})
        self.assertEqual(first["attacker_lines"].get("flancos") or {}, {})
        self.assertEqual(first["wall_sections"], 0)                          # 12 mortars bring the wall 8 down
        self.assertEqual(sim["winner"], "attacker")
        self.assertGreaterEqual(sim["rounds"], 3)                            # the clubs come out after the wall
        self.assertEqual(sim["attacker_losses"], {})
        morale = [(r["morale"].get("attacker") or {}).get("morale") for r in sim["details"]["rounds"]][:3]
        self.assertEqual(morale, [85, 75, 65])                               # as the real report: tired, outnumbered

    def test_a_player_city_is_fought_as_before(self):
        sim = battle_land.simulate_land_battle({308: 50, 305: 12}, {303: 100}, town_hall_level=5, wall_level=8,
                                               max_rounds=12, calibrated=False)
        self.assertTrue(sim["details"]["rounds"][0]["defender_lines"].get("principal"))

    def test_who_is_a_barbarian_village(self):
        self.assertTrue(battle_land.is_barbarian_village({320: 300, 324: 16}))
        self.assertTrue(battle_land.is_barbarian_village({320: 5, 316: 2}))
        self.assertFalse(battle_land.is_barbarian_village({320: 5, 303: 1}))
        self.assertFalse(battle_land.is_barbarian_village({316: 5}))


class LearningTests(TestCase):
    def test_nothing_to_learn_from_changes_nothing(self):
        result = barbarian_learning.learn()
        self.assertFalse(result["better"])
        self.assertEqual(barbarian_learning.active(), {})

    def test_the_cost_weighs_rounds_morale_wall_and_losses(self):
        sample = {"real": {"winner": "attacker", "rounds": 4, "morale": [85, 75, 65, 55], "wall": [0, None, None, None],
                           "losses": {"attacker": {}, "defender": {"320": 300}}}}
        same = {"winner": "attacker", "rounds": 4, "attacker_losses": {}, "defender_losses": {320: 300},
                "details": {"rounds": [{"morale": {"attacker": {"morale": m}}, "wall_sections": w}
                                       for m, w in ((85, 0), (75, 0), (65, 0), (55, 0))]}}
        self.assertEqual(barbarian_learning.sample_cost(sample, same)["cost"], 0)
        short = {**same, "rounds": 2, "details": {"rounds": same["details"]["rounds"][:2]}}
        self.assertEqual(barbarian_learning.sample_cost(sample, short)["rounds_gap"], -2)


class PlannerTests(TestCase):
    def test_reads_the_village_names(self):
        ids, unknown = barbarian_planner.defenders_from_names([["Membro do Clube de Bárbaros", 325], ["Aríete Bárbaro", 18],
                                                               ["Andarilho de Guerra", 3]])
        self.assertEqual(ids, {320: 325, 324: 18})
        self.assertEqual(unknown, ["Andarilho de Guerra"])

    def test_the_best_army_wins_without_losses_and_fits_the_field(self):
        have = {308: 253, 302: 2519, 304: 661, 305: 291, 303: 4502}
        plan = barbarian_planner.plan(have, {320: 75, 324: 2}, wall_level=1, field_level=5, upgrades=UPGRADES)
        self.assertTrue(plan["can_win"])
        self.assertEqual(plan["prediction"]["attacker_losses"], {})
        self.assertTrue(any(u in plan["army"] for u in (303, 308)))
        for uid, qty in plan["army"].items():
            self.assertLessEqual(qty, have[uid])

    def test_without_a_front_line_it_does_not_attack(self):
        plan = barbarian_planner.plan({305: 12}, {320: 75}, wall_level=1, field_level=5)
        self.assertFalse(plan["can_win"])

    def test_the_agent_api_answers_the_plan(self):
        from apps.combat.api.views import BarbarianPlanView

        request = SimpleNamespace(data={"game_account_id": "", "level": 12, "wall_level": 2,
                                        "defenders": [["Membro do Clube de Bárbaros", 125], ["Aríete Bárbaro", 4]],
                                        "available": {"308": 253, "302": 2519, "305": 291, "304": 661}})
        answer = BarbarianPlanView().post(request).data
        self.assertTrue(answer["can_win"])
        self.assertEqual(answer["defenders"], {"320": 125, "324": 4})
        self.assertTrue(all(isinstance(k, str) for k in answer["army"]))
