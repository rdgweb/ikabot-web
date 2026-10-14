"""N-113: sea battles in the simulator - the field of the port, the fleets, the morale fed by the tenders."""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse

from apps.combat.models import SimulationCheck
from apps.combat.services import replay, report_parser
from apps.combat.services.checks import add_sample, by_kind, kind_of
from apps.combat.tests_n109 import EMPTY, SUMMARY, round_html, slot
from apps.combat.tests_n110 import Base
from apps.espionage.services import battle_land
from apps.espionage.services.battle_land import simulate_land_battle
from apps.espionage.services.battle_sea import is_fleet, recommend_fleet, sea_field, sea_field_from_slots, simulate_sea_battle

RAM, FIRE, TRIREME, MORTAR, ROCKET, SPEEDBOAT, CARRIER, TENDER = 216, 211, 210, 215, 217, 218, 219, 220


def sea_round(number: int, total: int, attacker_zones: dict, defender_zones: dict, **reserves) -> str:
    return round_html(number, total, attacker_zones, defender_zones, **reserves).replace("battlefield_city_A_D", "battlefield_sea_A_D")


SEA_1 = sea_round(
    1, 2,
    {"main": [slot(11, 21, 0, RAM, 5, 0), slot(11, 21, 1, RAM, 5, 0)], "flankLeft": [slot(11, 22, 0, TRIREME, 6, 0)],
     "artillery": [slot(11, 24, 0, ROCKET, 3, 0), EMPTY, EMPTY]},
    {"main": [slot(12, 21, 0, TRIREME, 4, 4)], "artillery": [EMPTY, EMPTY, EMPTY]},
    attacker_reserve=[(TENDER, 5)], defender_reserve=[(TRIREME, 20)],
)
SEA_2 = sea_round(
    2, 2,
    {"main": [slot(11, 21, 0, RAM, 5, 0), slot(11, 21, 1, RAM, 5, 0)], "flankLeft": [slot(11, 22, 0, TRIREME, 6, 0)],
     "artillery": [slot(11, 24, 0, ROCKET, 3, 0), EMPTY, EMPTY], "longRange": [slot(11, 23, 0, MORTAR, 6, 0)]},
    {"main": [slot(12, 21, 0, TRIREME, 0, 8)], "artillery": [EMPTY, EMPTY, EMPTY]},
    attacker_reserve=[(TENDER, 5)], defender_reserve=[(TRIREME, 16)],
)
SEA_DETAILED = SEA_1 + "\n" + report_parser.ROUND_SEPARATOR + "\n" + SEA_2


class Quiet(SimpleTestCase):
    def setUp(self):
        self._bonus = battle_land._learned_bonus
        battle_land._learned_bonus = lambda: (0.0, 0.0)

    def tearDown(self):
        battle_land._learned_bonus = self._bonus


class SeaFieldTests(Quiet):
    def test_the_field_comes_from_the_port_of_the_city(self):
        self.assertEqual([sea_field(level) for level in (None, 0, 7, 8, 14, 15, 21, 22, 28, 29, 40)], [101, 101, 101, 102, 102, 103, 103, 104, 104, 105, 105])
        self.assertEqual([sea_field_from_slots(count) for count in (0, 1, 2, 3, 4, 5, 9)], [101, 101, 102, 103, 104, 105, 105])
        self.assertTrue(is_fleet({RAM: 3}))
        self.assertFalse(is_fleet({303: 30}))

    def test_a_slot_holds_as_many_ships_as_fit_in_it(self):
        """What a report of the biggest field shows: 5 steam rams a slot, 6 triremes on a flank, 3 rocket ships, 3 carriers."""
        sim = simulate_sea_battle({RAM: 80, TRIREME: 45, MORTAR: 15, ROCKET: 20, SPEEDBOAT: 17, CARRIER: 10}, {TRIREME: 846, ROCKET: 26, TENDER: 95}, port_level=30, max_rounds=1)
        first = sim["details"]["rounds"][0]
        self.assertEqual(first["attacker_start_lines"], {"principal": {RAM: 35}, "flancos": {TRIREME: 36}, "longa": {MORTAR: 15}, "artilharia": {ROCKET: 15},
                                                         "bomb": {CARRIER: 6}, "aa": {}, "bagagem": {}})            # no carrier to hunt: the speedboats stay back
        self.assertEqual(first["defender_start_lines"]["principal"], {TRIREME: 56})                                  # triremes hold the front when there is nothing heavier
        self.assertEqual(first["defender_start_lines"]["bagagem"], {TENDER: 95})
        self.assertTrue(sim["naval"])
        self.assertEqual(sim["field_level"], 105)

    def test_the_smallest_field_has_no_flanks_and_speedboats_come_out_against_carriers(self):
        sim = simulate_sea_battle({RAM: 20, TRIREME: 30, SPEEDBOAT: 30}, {FIRE: 30, CARRIER: 4}, port_level=3, max_rounds=1)
        first = sim["details"]["rounds"][0]
        self.assertEqual(first["attacker_start_lines"]["principal"], {RAM: 9})                                       # 3 slots of 15
        self.assertEqual(first["attacker_start_lines"]["flancos"], {})
        self.assertEqual(first["attacker_start_lines"]["aa"], {SPEEDBOAT: 10})
        self.assertEqual(first["defender_start_lines"]["bomb"], {CARRIER: 2})

    def test_on_land_a_slot_counts_the_same_way(self):
        sim = simulate_land_battle({308: 400, 304: 200, 305: 60}, {303: 50}, town_hall_level=25, wall_level=0, max_rounds=1)
        first = sim["details"]["rounds"][0]["attacker_start_lines"]
        self.assertEqual((first["principal"], first["longa"], first["artilharia"]), ({308: 112}, {304: 84}, {305: 30}))   # 16, 12 and 6 a slot, as the game draws


class SeaMoraleTests(Quiet):
    def test_tenders_feed_the_morale_of_a_fleet_three_points_each(self):
        fed = simulate_sea_battle({RAM: 40, TENDER: 5}, {RAM: 60, TENDER: 20}, port_level=30, max_rounds=1)
        attacker = dict(fed["details"]["rounds"][0]["morale"]["attacker"]["changes"])
        self.assertEqual(attacker["supply"], 15)
        self.assertEqual(dict(fed["details"]["rounds"][0]["morale"]["defender"]["changes"])["supply"], 60)

    def test_a_fleet_without_tenders_against_a_much_bigger_one_runs(self):
        """peto against Lucas1: 187 ships without tenders against 967; in the game he left in the third round."""
        sim = simulate_sea_battle({TRIREME: 45, MORTAR: 15, RAM: 80, ROCKET: 20, SPEEDBOAT: 17, CARRIER: 10}, {TRIREME: 846, ROCKET: 26, TENDER: 95},
                                  defender_upgrades={TRIREME: {"offensive": 13, "defensive": 16}}, port_level=30)
        self.assertEqual((sim["winner"], sim["fled"], sim["morale"]["defender"]), ("defender", "attacker", 100))
        self.assertGreater(sim["defender_losses"][TRIREME], 50)

    def test_a_carrier_alone_cannot_hold_the_sea(self):
        sim = simulate_sea_battle({FIRE: 20, TENDER: 3}, {CARRIER: 1}, port_level=25)
        self.assertEqual((sim["winner"], sim["rounds"], sim["attacker_losses"]), ("attacker", 1, {}))


class FleetRecommendationTests(Quiet):
    ENEMY = {TRIREME: 60, RAM: 20}

    def test_the_first_guess_is_kept_when_the_simulator_says_it_wins(self):
        rec = recommend_fleet({RAM: 90, TRIREME: 120, TENDER: 10}, self.ENEMY, first_guess={RAM: 60, TRIREME: 90}, port_level=30)
        self.assertTrue(rec["can_win"])
        self.assertEqual(rec["recommended"], {RAM: 60, TRIREME: 90})
        self.assertEqual(rec["simulation"]["winner"], "attacker")

    def test_a_guess_that_loses_grows_until_it_wins_or_all_is_sent(self):
        rec = recommend_fleet({RAM: 90, TRIREME: 120, TENDER: 10}, self.ENEMY, first_guess={RAM: 6, TRIREME: 10}, port_level=30)
        self.assertTrue(rec["can_win"])
        self.assertGreater(rec["recommended"][RAM], 6)
        self.assertLessEqual(rec["recommended"][RAM], 90)

    def test_when_nothing_wins_the_guess_is_what_goes_and_it_says_so(self):
        rec = recommend_fleet({TRIREME: 5}, {RAM: 300, TRIREME: 300, TENDER: 40}, first_guess={TRIREME: 5}, port_level=30)
        self.assertEqual((rec["can_win"], rec["recommended"]), (False, {TRIREME: 5}))

    def test_an_empty_port_takes_the_guess_or_two_front_ships(self):
        self.assertEqual(recommend_fleet({RAM: 9, TRIREME: 9}, {}, first_guess={TRIREME: 2})["recommended"], {TRIREME: 2})
        self.assertEqual(recommend_fleet({RAM: 9, TRIREME: 9}, {})["recommended"], {RAM: 2})
        self.assertEqual(recommend_fleet({}, self.ENEMY), {"can_win": False, "recommended": {}, "simulation": None})

    def test_the_blockade_fleet_is_checked_in_the_simulator_and_falls_back_to_the_rule(self):
        from unittest.mock import patch

        from apps.espionage.api.views import _fleet_rule_for_blockade, _recommend_fleet_for_blockade

        own = {"Aríete a Vapor": 90, "Trireme": 400}
        enemy = {TRIREME: 200}                                                             # the rule answers with 250 of the best front ship it has...
        rule = _fleet_rule_for_blockade(own, enemy, {})
        self.assertEqual(rule, {RAM: 90, TRIREME: 160})
        checked = _recommend_fleet_for_blockade(own, enemy_fleet=enemy, enemy_upgrades={}, city_level=30)
        self.assertEqual(checked[RAM], 90)
        self.assertGreaterEqual(checked[TRIREME], 160)
        with patch("apps.espionage.services.battle_sea.recommend_fleet", side_effect=RuntimeError("boom")):
            self.assertEqual(_recommend_fleet_for_blockade(own, enemy_fleet=enemy, enemy_upgrades={}), rule)
        self.assertEqual(_recommend_fleet_for_blockade(own, enemy_fleet={}, enemy_upgrades={}), _fleet_rule_for_blockade(own, {}, {}))


class SeaReplayTests(Quiet):
    def test_a_sea_report_gives_the_fleets_the_field_and_who_joined(self):
        rounds = report_parser.parse_rounds(SEA_DETAILED)
        self.assertTrue(rounds[0]["naval"])
        inputs = replay.battle_inputs(rounds)
        self.assertEqual((inputs["naval"], inputs["field_level"], inputs["wall_segments"]), (True, 103, 0))          # three first-strike slots
        self.assertEqual(inputs["attacker"], {RAM: 10, TRIREME: 6, ROCKET: 3, TENDER: 5})
        self.assertEqual(inputs["defender"], {TRIREME: 28})
        self.assertEqual(inputs["reinforcements"], {"attacker": {2: {MORTAR: 6}}, "defender": {}})
        self.assertTrue(replay.simulable(inputs))
        predicted = replay.simulate(inputs)
        self.assertEqual(predicted["winner"], "attacker")
        self.assertNotIn(MORTAR, predicted["losses"]["attacker"])

    def test_a_land_report_is_still_a_land_report(self):
        rounds = report_parser.parse_rounds(round_html(1, 1, {"main": [slot(11, 21, 0, 303, 40, 0)]}, {"main": [slot(12, 21, 0, 315, 30, 5)]}))
        inputs = replay.battle_inputs(rounds)
        self.assertNotIn("naval", inputs)
        self.assertEqual(inputs["field_level"], 1)


class SeaCheckTests(Base):
    def test_a_sea_combat_that_is_over_is_compared_like_the_land_ones(self):
        report = self._report(combat_id=909, detailed_html=SEA_DETAILED, summary_html=SUMMARY)
        self._take(report, silent=True)
        report.refresh_from_db()
        self.assertEqual(report.combat_type, "naval")
        check = SimulationCheck.objects.get(report=report)
        self.assertEqual((kind_of(check), check.inputs_json["naval"], check.inputs_json["field_level"]), ("sea", True, 103))
        self.assertEqual(check.predicted_json["winner"], "attacker")
        self.assertEqual([group["key"] for group in by_kind(SimulationCheck.objects.all())], ["sea"])

    def test_a_sea_battle_from_outside_is_kept_and_fought_again_the_same(self):
        from apps.combat.services.checks import recheck_sample

        check = add_sample(
            source="ikalogs", external_id="sea1", title="peto VS Lucas1", fought_at="13.08.2026 12:55:26",
            inputs={"naval": True, "field_level": 105, "wall_segments": 0, "attacker": {TRIREME: 45, MORTAR: 15, RAM: 80, ROCKET: 20, SPEEDBOAT: 17, CARRIER: 10},
                    "defender": {TRIREME: 846, ROCKET: 26, TENDER: 95}, "reinforcements": {"attacker": {}, "defender": {}}},
            real={"winner": "defender", "rounds": 3, "losses": {"attacker": {TRIREME: 45, MORTAR: 3, RAM: 7}, "defender": {TRIREME: 145, ROCKET: 18}}},
            upgrades={"attacker": {}, "defender": {TRIREME: [13, 16], ROCKET: [7, 8]}},
        )
        self.assertEqual((check.inputs_json["naval"], kind_of(check), check.winner_ok), (True, "sea", True))
        self.assertLess(check.defender_error, 0.2)                                         # 163 ships lost in the game, about that in the simulator
        first = (check.winner_ok, check.rounds_gap, check.attacker_error, check.defender_error)
        again = recheck_sample(SimulationCheck.objects.get(pk=check.pk))
        self.assertEqual((again.winner_ok, again.rounds_gap, again.attacker_error, again.defender_error), first)

    def test_the_adjustment_of_the_land_recommendation_is_not_learned_from_the_sea(self):
        from apps.combat.services import learning

        self._take(self._report(combat_id=909, detailed_html=SEA_DETAILED, summary_html=SUMMARY), silent=True)
        self.assertEqual(SimulationCheck.objects.count(), 1)
        self.assertEqual(learning.propose()["samples"], 0)

    def test_the_simulator_page_has_the_sea_beside_the_land(self):
        self._take(self._report(combat_id=909, detailed_html=SEA_DETAILED, summary_html=SUMMARY), silent=True)
        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x"))
        page = self.client.get(reverse("combat:simulator")).content.decode()
        self.assertIn("No mar", page)
