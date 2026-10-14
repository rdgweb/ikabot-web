"""N-112: morale, an army that runs, and the wall as the report shows it."""

from django.test import SimpleTestCase
from django.urls import reverse

from apps.combat.models import BattleIntel, SimulationCheck
from apps.combat.services import intel, report_parser
from apps.combat.services.board import round_board
from apps.combat.tests_n109 import EMPTY, SUMMARY, round_html, slot
from apps.combat.tests_n110 import Base
from apps.espionage.services import battle_land
from apps.espionage.services.battle_land import morale_after, morale_changes, recommend_attack_force, simulate_land_battle


def up(weapon: int, armour: int) -> dict:
    return {"offensive": weapon, "defensive": armour}


def tips(*pairs) -> str:
    """What the game registers for the mouse-over of a round, the way it writes it."""
    return '<script language="javascript">' + "".join(f'registerMouseOver("{key}", "{body}");' for key, body in pairs) + "</script>"


WALLED = round_html(
    1, 2,
    {"main": [slot(11, 21, 0, 303, 6, 1)], "artillery": [slot(11, 24, 0, 305, 1, 0)]},
    {"main": [slot(12, 21, 1, 314, 1, 0), slot(12, 21, 0, 314, 1, 0), slot(12, 21, 2, 314, 0, 1)], "artillery": [slot(12, 24, 0, 305, 1, 0)]},
    attacker_reserve=[(310, 7)],
) + tips(
    ("11_21_0", "<h2>Hoplita (Alpha[TAG])</h2><p>Lança Superior (8)</p><p>Escudo Superior (6)</p><p>Pontos de vida: 60%</p><p>Perdas: 1</p>"),
    ("11_24_0", "<h2>Morteiro (Alpha[TAG])</h2><p>Granada Superior (4)</p><p>Muralha Superior (4)</p><p>Munições: 67%</p><p>Pontos de vida: 100%</p>"),
    ("12_24_0", "<h2>Morteiro (Inimigo[XYZ])</h2><p>Granada Armagedon (3)</p><p>Muralha de Aço (2)</p><p>Munições: 67%</p><p>Pontos de vida: 100%</p>"),
    ("12_21_1", "<h2>Muralha (Inimigo[XYZ])</h2><p>Nível da muralha: 7</p><p>Pontos de vida: 100%</p>"),
    ("12_21_0", "<h2>Muralha (Inimigo[XYZ])</h2><p>Nível da muralha: 7</p><p>Pontos de vida: 39%</p>"),
    ("12_21_2", "<h2>Muralha (Inimigo[XYZ])</h2><p>Nível da muralha: 7</p><p>Pontos de vida: 0%</p><p>Perdas: 1</p>"),
    ("morale1", "<h2>Alterações da moral</h2><p>As tropas estão exaustas. A moral foi reduzida em 10%.</p>"
                "<p>A moral dos atacantes diminuiu 5% porque sofreu mais perdas que o adversário.</p>"
                "<p>A moral dos atacantes diminuiu 5% porque o adversário aparenta ser superiores de todas as formas.</p>"
                "<p>Esta refeição preparada pelo cozinheiro Alpha[TAG] aumentou a moral das suas unidades em 87%.</p>"),
    ("morale2", "<h2>Alterações da moral</h2><p>As tropas estão exaustas. A moral foi reduzida em 10%.</p>"
                "<p>A moral dos defensores diminuiu 13% porque a muralha adversária é impenetrável a todas as armas.</p>"
                "<p>Algo que o hub ainda não conhece tirou 2% da moral.</p>"),
)


class MoraleRuleTests(SimpleTestCase):
    def test_every_round_tires_and_each_disadvantage_takes_five(self):
        changes = morale_changes(is_attacker=False, damage_taken=900, damage_dealt=100, losses=30, enemy_losses=10, units=100, enemy_units=125,
                                 bombed=True, flanks_broken=True)
        self.assertEqual(changes, [("tired", -10), ("more_damage", -5), ("more_losses", -5), ("outnumbered", -5), ("bombed", -5), ("flanks", -5)])
        self.assertEqual(morale_after(100, changes), 65)

    def test_the_other_side_has_to_be_a_quarter_bigger_to_weigh(self):
        fewer = dict(is_attacker=False, damage_taken=0, damage_dealt=1, losses=0, enemy_losses=1)
        self.assertNotIn(("outnumbered", -5), morale_changes(units=1415, enemy_units=1746, **fewer))     # 1.23 times: seen in a report, no penalty
        self.assertIn(("outnumbered", -5), morale_changes(units=1127, enemy_units=1411, **fewer))        # 1.25 times: penalty

    def test_a_draw_counts_against_who_attacks_and_a_closed_wall_takes_thirteen(self):
        draw = dict(damage_taken=0, damage_dealt=0, losses=0, enemy_losses=0, units=19, enemy_units=20)
        self.assertEqual(morale_changes(is_attacker=True, wall_closed=True, supply=7, **draw),
                         [("tired", -10), ("same_damage", -5), ("same_losses", -5), ("wall", -13), ("supply", 7)])
        self.assertEqual(morale_changes(is_attacker=False, wall_closed=True, **draw), [("tired", -10)])
        self.assertEqual(morale_after(30, morale_changes(is_attacker=True, wall_closed=True, supply=7, **draw)), 4)
        self.assertEqual(morale_after(20, [("tired", -10), ("wall", -13)]), 0)          # never below zero
        self.assertEqual(morale_after(95, [("tired", -10), ("supply", 40)]), 100)       # nor above a hundred


class WallTests(SimpleTestCase):
    def setUp(self):
        self._bonus = battle_land._learned_bonus
        battle_land._learned_bonus = lambda: (0.0, 0.0)

    def tearDown(self):
        battle_land._learned_bonus = self._bonus

    def test_the_wall_has_the_numbers_of_the_game(self):
        self.assertEqual((battle_land._wall_segment_hp(7), battle_land._wall_armor(7)), (9000, 560))
        self.assertEqual((battle_land._wall_segment_hp(0), battle_land._wall_armor(0), battle_land._wall_shot(0)), (0, 0, 0))
        self.assertEqual(battle_land._weapon_raw_damage(305, 1, 4), 6048)                 # a mortar with the fourth grenade: 12% more
        self.assertEqual(battle_land._weapon_raw_damage(303, 0, 8), 400)                  # a hoplite: the old +5 a level

    def test_one_mortar_never_opens_a_level_seven_wall_and_the_army_runs(self):
        """The combat of IMPERIO lll: 61% off one section a shot, the sections share it, the morale goes."""
        sim = simulate_land_battle({303: 7, 305: 1, 310: 7, 311: 7}, {315: 30, 305: 1}, attacker_upgrades={303: up(8, 6), 305: up(4, 4)},
                                   defender_upgrades={305: up(3, 3)}, town_hall_level=25, wall_level=7, max_rounds=40)
        rounds = sim["details"]["rounds"]
        self.assertEqual(rounds[0]["wall_hp"], 7 * 9000 - 5488)                           # 61% of one section, as the report shows
        self.assertEqual([item["morale"]["attacker"]["morale"] for item in rounds[:3]], [87, 74, 61])      # the morale the game showed
        self.assertEqual([item["morale"]["defender"]["morale"] for item in rounds[:3]], [85, 70, 55])
        self.assertEqual((sim["winner"], sim["wall_sections_left"], sim["wall_sections"]), ("defender", 7, 7))

    def test_enough_mortars_open_it(self):
        sim = simulate_land_battle({303: 30, 305: 6, 310: 7}, {315: 30, 305: 1}, attacker_upgrades={305: up(4, 4)}, town_hall_level=25, wall_level=7, max_rounds=40)
        self.assertEqual(sim["winner"], "attacker")
        self.assertLess(sim["wall_sections_left"], 7)

    def test_a_wall_with_nobody_behind_it_does_not_hold_the_city(self):
        """Nine real combats against a wall alone, from level 1 to 39, all end in the first round."""
        sim = simulate_land_battle({303: 30, 305: 1}, {}, attacker_upgrades={305: up(3, 4)}, town_hall_level=25, wall_level=1, max_rounds=40)
        self.assertEqual((sim["winner"], sim["rounds"], sim["fled"]), ("attacker", 1, ""))
        self.assertEqual(sim["morale"]["defender"], 100)                                   # a wall alone has no morale to lose
        strong = simulate_land_battle({303: 100, 307: 30}, {}, town_hall_level=25, wall_level=21, max_rounds=40)       # rams that do not scratch it
        self.assertEqual((strong["winner"], strong["rounds"], strong["wall_sections_left"]), ("attacker", 1, 7))

    def test_with_somebody_behind_it_a_wall_nobody_opens_holds(self):
        held = simulate_land_battle({303: 30, 310: 30}, {310: 20}, town_hall_level=25, wall_level=20, max_rounds=40)  # twenty cooks and a wall
        self.assertEqual((held["winner"], held["wall_never_hurt"], held["wall_sections_left"]), ("defender", True, 7))
        # seven cooks do not feed themselves against five times their number: 92, 84... as a report shows, and they run
        few = simulate_land_battle({303: 30, 310: 30}, {310: 7}, town_hall_level=25, wall_level=20, max_rounds=40)
        self.assertEqual(few["details"]["rounds"][0]["morale"]["defender"]["morale"], 92)
        self.assertEqual((few["winner"], few["fled"]), ("attacker", "defender"))

    def test_the_recommendation_says_when_the_artillery_does_not_open_the_wall(self):
        self.assertEqual(recommend_attack_force({303: 200, 305: 40, 310: 20}, {}, town_hall_level=25, wall_level=30)["recommended"], {303: 30, 305: 6})
        short = recommend_attack_force({303: 7, 305: 1, 310: 7}, {315: 30, 305: 1}, town_hall_level=25, wall_level=7)
        self.assertFalse(short["can_win"])
        self.assertIn("muralha nível 7", short["note"])
        enough = recommend_attack_force({303: 60, 305: 12, 310: 10}, {315: 30, 305: 1}, town_hall_level=25, wall_level=7)
        self.assertTrue(enough["can_win"])
        self.assertEqual(enough["simulation"]["winner"], "attacker")
        self.assertGreaterEqual(enough["recommended"].get(305, 0), 2)                      # two mortars are what opens a section of it


class FleeTests(SimpleTestCase):
    ARMIES = ({302: 200, 303: 200, 305: 100, 308: 75, 309: 100, 310: 30, 311: 40, 312: 100}, {302: 927, 303: 1000, 304: 1000, 308: 658})

    def setUp(self):
        self._bonus = battle_land._learned_bonus
        battle_land._learned_bonus = lambda: (0.0, 0.0)

    def tearDown(self):
        battle_land._learned_bonus = self._bonus

    def test_a_bigger_army_without_cooks_runs_and_loses(self):
        """The combat of DRACENA: 3.326 against 659, no cooks; in the game it ran in the fifth round."""
        sim = simulate_land_battle(*self.ARMIES, town_hall_level=25, wall_level=0, max_rounds=40)
        self.assertEqual((sim["winner"], sim["fled"], sim["morale"]["defender"]), ("attacker", "defender", 0))
        self.assertEqual(sim["morale"]["attacker"], 100)                                   # thirty cooks hold it
        self.assertIn(sim["rounds"], (4, 5))
        self.assertGreater(sum(self.ARMIES[1].values()) - sum(sim["defender_losses"].values()), 2000)     # most of it still alive

    def test_without_morale_the_simulator_fights_to_the_end_as_before(self):
        sim = simulate_land_battle(*self.ARMIES, town_hall_level=25, wall_level=0, max_rounds=40, morale=False)
        self.assertEqual((sim["winner"], sim["fled"]), ("defender", ""))

    def test_cooks_keep_the_defender_fighting_and_barbarians_never_run(self):
        attacker, defender = self.ARMIES
        fed = simulate_land_battle(attacker, {**defender, 310: 40}, town_hall_level=25, wall_level=0, max_rounds=40)
        self.assertEqual((fed["winner"], fed["fled"]), ("defender", ""))
        wild = simulate_land_battle({303: 30}, {320: 400}, town_hall_level=25, wall_level=0, max_rounds=8)
        self.assertEqual((wild["morale"]["defender"], wild["fled"] == "defender"), (100, False))


class ReportReadingTests(SimpleTestCase):
    def test_the_round_tells_the_wall_the_upgrades_and_why_the_morale_changed(self):
        parsed = report_parser.parse_rounds(WALLED)[0]
        self.assertEqual(parsed["wall"], {"level": 7, "sections": 3, "standing": 2, "hp": [100, 39, 0]})
        self.assertEqual(parsed["attacker"]["upgrades"], {303: [8, 6], 305: [4, 4]})
        self.assertEqual(parsed["defender"]["upgrades"], {305: [3, 2]})                   # the wall is not a unit with upgrades
        self.assertEqual(parsed["defender"]["players"], {"Inimigo[XYZ]": {305: [3, 2]}})
        hoplites = parsed["attacker"]["zones"]["main"][0]
        self.assertEqual((hoplites["hp"], hoplites["owner"], hoplites["upgrades"]), (60, "Alpha[TAG]", [8, 6]))
        self.assertEqual(parsed["attacker"]["zones"]["artillery"][0]["ammo"], 67)
        attacker, defender = parsed["morale"]["attacker"][0], parsed["morale"]["defender"][0]
        self.assertEqual([(item["reason"], item["points"]) for item in attacker["reasons"]],
                         [("tired", -10), ("more_losses", -5), ("outnumbered", -5), ("supply", 7)])        # 100 - 20 + 7 = the 87 the game wrote
        self.assertEqual([(item["reason"], item["points"]) for item in defender["reasons"]], [("tired", -10), ("wall", -13), ("other", -2)])

    def test_a_single_upgrade_line_is_told_apart_by_its_name(self):
        html = round_html(1, 1, {"main": [slot(11, 21, 0, 303, 40, 0), slot(11, 21, 1, 302, 30, 0)]}, {"main": [EMPTY]}) + tips(
            ("11_21_0", "<h2>Hoplita (Alpha[TAG])</h2><p>Escudo Superior (5)</p><p>Pontos de vida: 100%</p>"),
            ("11_21_1", "<h2>Espadachim (Alpha[TAG])</h2><p>Espada de Aço (3)</p><p>Pontos de vida: 100%</p>"),
        )
        self.assertEqual(report_parser.parse_rounds(html)[0]["attacker"]["upgrades"], {303: [0, 5], 302: [3, 0]})

    def test_a_page_without_mouse_over_reads_as_before(self):
        parsed = report_parser.parse_rounds(round_html(1, 1, {"main": [slot(11, 21, 0, 303, 40, 0)]}, {"main": [slot(12, 21, 0, 314, 1, 0)]}))[0]
        self.assertEqual(parsed["wall"], {"level": 0, "sections": 1, "standing": 1, "hp": [100]})
        self.assertNotIn("upgrades", parsed["attacker"])
        self.assertNotIn("reasons", parsed["morale"]["attacker"][0])

    def test_the_page_of_the_round_carries_the_reasons_and_the_wall(self):
        board = round_board(report_parser.parse_rounds(WALLED)[0], "Alpha")
        self.assertEqual(board["wall"]["level"], 7)
        mood = board["moods"][0]
        self.assertEqual((mood["name"], mood["morale"]), ("Alpha[TAG]", 87))
        self.assertEqual([(chip["points"], chip["label"]) for chip in mood["chips"]],
                         [("-10", "exaustão"), ("-5", "mais perdas"), ("-5", "em menor número"), ("+7", "cozinheiros")])
        self.assertEqual(board["moods"][1]["chips"][-1]["label"], "Algo que o hub ainda não conhece tirou 2% da moral.")


class IntelTests(Base):
    def _walled(self, **fields):
        fields.setdefault("summary_html", "")
        return self._report(combat_id=777, target_city_id="5501", target_city_name="Tebas", target_owner="Inimigo", detailed_html=WALLED, **fields)

    def test_a_report_teaches_the_wall_of_the_city_and_the_upgrades_of_the_enemy(self):
        report = self._walled()
        self._take(report, silent=True)
        self.assertEqual(intel.known_wall_level("s78-br", "5501"), 7)
        self.assertEqual(intel.known_upgrades("s78-br", "Inimigo[XYZ]"), {305: {"offensive": 3, "defensive": 2}})
        self.assertEqual(intel.known_upgrades("s78-br", "inimigo"), {305: {"offensive": 3, "defensive": 2}})       # with or without the alliance
        self.assertEqual(intel.known_upgrades("s78-br", "Alpha"), {})                                              # the account itself is not intel
        self.assertEqual(BattleIntel.objects.count(), 2)
        self.assertIsNone(intel.known_wall_level("s79-br", "5501"))                                                # another world, another city
        self._take(report, silent=True)                                                                            # read again: the same two rows
        self.assertEqual(BattleIntel.objects.count(), 2)

    def test_an_upgrade_seen_once_is_not_lost_when_a_later_report_shows_less(self):
        BattleIntel.objects.create(server_id="s78-br", kind="upgrades", key="inimigo", value_json={"305": [5, 1], "303": [2, 2]})
        self._take(self._walled(), silent=True)
        self.assertEqual(intel.known_upgrades("s78-br", "Inimigo"), {305: {"offensive": 5, "defensive": 2}, 303: {"offensive": 2, "defensive": 2}})

    def test_the_recommendation_uses_the_wall_and_the_upgrades_known(self):
        from apps.espionage.api.views import _parse_enemy_intel

        guessed = _parse_enemy_intel("5501", "", "s78-br", "Inimigo")
        self.assertEqual((guessed["wall_known"], guessed["enemy_upgrades"]), (False, {}))
        self._take(self._walled(), silent=True)
        known = _parse_enemy_intel("5501", "", "s78-br", "Inimigo")
        self.assertEqual((known["wall_level"], known["wall_known"]), (7, True))
        self.assertEqual(known["enemy_upgrades"], {305: {"offensive": 3, "defensive": 2}})

    def test_the_check_fights_with_the_wall_and_the_upgrades_of_the_report(self):
        report = self._walled(summary_html=SUMMARY)
        self._take(report, silent=True)
        guess = SimulationCheck.objects.get(report=report).inputs_json["assumptions"]
        self.assertEqual((guess["wall_level"], guess["wall_source"]), (7, "relatório"))
        self.assertEqual(guess["upgrades"]["attacker"], {"303": {"offensive": 8, "defensive": 6}, "305": {"offensive": 4, "defensive": 4}})
        self.assertEqual(guess["upgrades"]["defender"], {"305": {"offensive": 3, "defensive": 2}})

    def test_a_combat_from_outside_fought_again_keeps_the_same_score(self):
        """Fought again from what is stored (JSON, unit ids as text), the comparison is the one made when it came in."""
        from apps.combat.services.checks import add_sample, recheck_sample

        check = add_sample(
            source="ikalogs", external_id="abc", title="A VS B", fought_at="",
            inputs={"field_level": 25, "wall_segments": 0, "attacker": {303: 300, 305: 12}, "defender": {315: 250, 303: 120}, "reinforcements": {"attacker": {}, "defender": {}}},
            real={"winner": "attacker", "rounds": 3, "losses": {"attacker": {303: 40}, "defender": {315: 250, 303: 120}}},
            upgrades={"attacker": {303: [8, 6]}, "defender": {}},
        )
        first = (check.winner_ok, check.rounds_gap, check.attacker_error, check.defender_error)
        self.assertLess(check.defender_error, 1.0)
        again = recheck_sample(SimulationCheck.objects.get(pk=check.pk))
        self.assertEqual((again.winner_ok, again.rounds_gap, again.attacker_error, again.defender_error), first)
        self.assertEqual(again.inputs_json["assumptions"]["upgrades"]["attacker"], {"303": {"offensive": 8, "defensive": 6}})

    def test_the_combat_page_shows_the_morale_card_and_the_level_of_the_wall(self):
        from django.contrib.auth import get_user_model

        report = self._walled()
        self._take(report, silent=True)
        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x"))
        page = self.client.get(reverse("combat:detail", args=[report.pk])).content.decode()
        for text in ("cbd-moods", "exaustão", "em menor número", "<b>+7</b> cozinheiros", "muralha fechada", "Nível 7", "<b>2</b> de 3 de pé"):
            self.assertIn(text, page)
