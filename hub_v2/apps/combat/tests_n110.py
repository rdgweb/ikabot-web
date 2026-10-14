"""N-110: the simulator checked against real combats, and what it learns from them."""

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.combat.models import CombatReport, SimulationCheck, SimulatorCalibration
from apps.combat.services import learning, replay, report_parser
from apps.combat.services.checks import check_report, normalize, scoreboard, simulate_check
from apps.combat.services.intake import take_in
from apps.combat.tests_n109 import DETAILED, EMPTY, ROUND_1, SUMMARY, round_html, slot

SENT_OK = {"ok": True, "result": {"message_id": 5}}
SEND_MESSAGE = "apps.telegram.services.notifications.send_message"
SEND_PHOTO = "apps.telegram.services.notifications.send_photo"


class ReplayTests(SimpleTestCase):
    def test_the_armies_are_read_from_the_first_round_without_the_wall(self):
        rounds = report_parser.parse_rounds(DETAILED)
        inputs = replay.battle_inputs(rounds)
        self.assertEqual(inputs["attacker"], {303: 2300, 305: 6, 312: 37, 310: 1})      # every slot (left + fallen) and the reserve
        self.assertEqual(inputs["defender"], {315: 57})
        self.assertEqual((inputs["wall_segments"], inputs["field_level"]), (1, 1))
        self.assertEqual(inputs["reinforcements"], {"attacker": {}, "defender": {}})
        self.assertTrue(replay.simulable(inputs))

    def test_who_shows_up_in_a_later_round_is_a_reinforcement(self):
        first = round_html(1, 2, {"main": [slot(11, 21, 0, 303, 40, 10)]}, {"main": [slot(12, 21, 0, 315, 20, 5)]})
        second = round_html(2, 2, {"main": [slot(11, 21, 0, 303, 40, 0), slot(11, 21, 1, 302, 30, 0)]}, {"main": [slot(12, 21, 0, 315, 50, 0)]})
        rounds = report_parser.parse_rounds(first + report_parser.ROUND_SEPARATOR + second)
        inputs = replay.battle_inputs(rounds)
        self.assertEqual(inputs["reinforcements"], {"attacker": {2: {302: 30}}, "defender": {2: {315: 30}}})

    def test_what_happened_is_read_with_the_winner_by_side(self):
        rounds = report_parser.parse_rounds(DETAILED)
        real = replay.real_outcome(rounds, report_parser.parse_summary(SUMMARY))
        self.assertEqual((real["winner"], real["rounds"]), ("attacker", 2))
        self.assertEqual(real["losses"], {"attacker": {303: 1005}, "defender": {315: 57}})   # the wall is not a unit here

    def test_how_far_a_prediction_is(self):
        self.assertEqual(replay.loss_error({303: 30}, {303: 30}), 0.0)
        self.assertEqual(replay.loss_error({}, {}), 0.0)
        self.assertEqual(replay.loss_error({303: 30}, {}), 1.0)
        self.assertEqual(replay.loss_error({303: 30}, {303: 60}), 0.5)
        score = replay.compare({"winner": "attacker", "rounds": 2, "losses": {"attacker": {303: 30}, "defender": {}}},
                               {"winner": "defender", "rounds": 5, "losses": {"attacker": {303: 60}, "defender": {}}})
        self.assertEqual(score, {"winner_known": True, "winner_ok": False, "rounds_gap": 3, "attacker_error": 0.5, "defender_error": 0.0})

    def test_a_field_with_nothing_to_fight_is_not_a_combat_to_learn_from(self):
        rounds = report_parser.parse_rounds(round_html(1, 1, {"main": [slot(11, 21, 0, 303, 40, 0)]}, {"main": [EMPTY]}))
        self.assertFalse(replay.simulable(replay.battle_inputs(rounds)))
        self.assertIsNone(replay.replay_pages("", ""))


class Base(TestCase):
    def setUp(self):
        from apps.telegram.models import NotificationTemplate, TelegramBotConfig

        node = Node.objects.create(name="node-sim")
        account = Account.objects.create(node=node, label="Lobby Sim", email="sim@example.com", password_enc="x")
        self.alpha = GameAccount.objects.create(
            account=account, lobby_account_id=1, server_id="s78-br", server_language="br", server_number=78, name="Alpha",
        )
        TelegramBotConfig.objects.create(pk=1, bot_token="123:abc", enabled=True, chat_id="1000", notify_combat_report=True)
        NotificationTemplate.seed_defaults()
        learning._CACHE["at"] = 0.0

    def tearDown(self):
        learning._CACHE["at"] = 0.0

    def _report(self, **fields):
        values = {"game_account": self.alpha, "combat_id": 4242, "target_city_name": "Tebas", "detailed_html": DETAILED, "summary_html": SUMMARY}
        values.update(fields)
        return CombatReport.objects.create(**values)

    def _take(self, report, **kwargs):
        with patch(SEND_PHOTO, return_value=SENT_OK), patch(SEND_MESSAGE, return_value=SENT_OK):
            return take_in(report, **kwargs)


class CheckTests(Base):
    def test_a_combat_that_is_over_is_fought_again_and_the_comparison_kept(self):
        report = self._report()
        self._take(report, silent=True)
        check = SimulationCheck.objects.get(report=report)
        self.assertEqual(check.real_json["winner"], "attacker")
        self.assertEqual(check.inputs_json["attacker"], {"303": 2300, "305": 6, "312": 37, "310": 1})
        self.assertEqual(check.inputs_json["assumptions"]["own_side"], "attacker")
        self.assertEqual((check.inputs_json["assumptions"]["wall_level"], check.inputs_json["assumptions"]["wall_source"]), (15, "padrão"))
        self.assertIn(check.predicted_json["winner"], ("attacker", "defender", "draw"))
        self.assertTrue(check.winner_ok)                                   # 2300 hoplites against 57 spearmen: even the simulator sees it
        self.assertEqual(scoreboard()["total"], 1)

        self._take(report, silent=True)                                    # delivered again: one comparison, not two
        self.assertEqual(SimulationCheck.objects.count(), 1)

    def test_a_combat_still_going_on_is_not_compared(self):
        report = self._report(detailed_html=ROUND_1, summary_html="")
        self._take(report, silent=True)
        self.assertFalse(SimulationCheck.objects.exists())
        self.assertIsNone(check_report(report))

    def test_the_check_measures_the_simulator_itself_whatever_was_approved(self):
        report = self._report()
        self._take(report, silent=True)
        before = SimulationCheck.objects.get(report=report).predicted_json
        row = SimulatorCalibration.load()
        row.approved_json = {"attacker_pct": -60, "defender_pct": 60}
        row.save()
        learning._CACHE["at"] = 0.0
        check_report(report)
        self.assertEqual(SimulationCheck.objects.get(report=report).predicted_json, before)


class LearningTests(Base):
    INPUTS = {"field_level": 25, "wall_segments": 0, "attacker": {303: 300, 305: 12}, "defender": {315: 250, 303: 120},
              "reinforcements": {"attacker": {}, "defender": {}}}

    def _check(self, combat_id: int, defender_pct: int):
        """A stored combat whose real losses are what the simulator gives with the defender hitting `defender_pct` harder."""
        report = CombatReport.objects.create(game_account=self.alpha, combat_id=combat_id, finished=True, result="victory")
        truth = simulate_check(self.INPUTS, {}, defender_pct=defender_pct)
        base = simulate_check(self.INPUTS, {})
        score = replay.compare(truth, base)
        return SimulationCheck.objects.create(
            report=report, inputs_json={**normalize(self.INPUTS), "assumptions": {}}, real_json=truth, predicted_json=base,
            winner_ok=score["winner_ok"], rounds_gap=score["rounds_gap"], attacker_error=score["attacker_error"], defender_error=score["defender_error"],
        )

    def test_the_proposal_is_the_adjustment_that_brings_the_simulator_closest_to_what_happened(self):
        self._check(1, 40)
        base = SimulationCheck.objects.get().attacker_error
        self.assertGreater(base, 0)                                        # the simulator, as it is, misses this combat
        proposal = learning.propose()
        self.assertEqual((proposal["samples"], proposal["better"]), (1, True))
        self.assertEqual((proposal["attacker_pct"], proposal["defender_pct"]), (0, 40))
        self.assertEqual(proposal["after"]["attacker_hit"], 100)           # with the adjustment it reproduces the combat
        self.assertGreater(proposal["after"]["attacker_hit"], proposal["before"]["attacker_hit"])
        self.assertEqual(SimulatorCalibration.load().proposed_json["samples"], 1)

    def test_nothing_changes_in_the_simulator_until_the_proposal_is_approved_and_it_takes_enough_combats(self):
        self._check(1, 40)
        learning.propose()
        self.assertFalse(learning.approve("rodrigo"))                      # one combat says too little
        self.assertEqual(learning.active_bonus(), (0.0, 0.0))

        with patch.object(learning, "MIN_CHECKS", 1):
            self.assertTrue(learning.approve("rodrigo"))
        approved = SimulatorCalibration.load().approved_json
        self.assertEqual((approved["approved_by"], approved["samples"]), ("rodrigo", 1))
        self.assertEqual(learning.active_bonus(), (float(approved["attacker_pct"]), float(approved["defender_pct"])))

        from apps.espionage.services import battle_land

        self.assertEqual(battle_land._learned_bonus(), learning.active_bonus())   # what every recommendation now adds
        learning.revoke()
        self.assertEqual(learning.active_bonus(), (0.0, 0.0))

    def test_without_a_gain_there_is_nothing_to_approve(self):
        self._check(1, 0)                                                  # the simulator already gets this one right
        proposal = learning.propose()
        self.assertEqual((proposal["attacker_pct"], proposal["defender_pct"], proposal["better"]), (0, 0, False))
        with patch.object(learning, "MIN_CHECKS", 1):
            self.assertFalse(learning.approve("rodrigo"))


class SimulatorPagesTests(Base):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_user(username="sim", email="sim@example.com", password="secret123"))
        self.report = self._report()
        self._take(self.report, silent=True)

    def test_the_simulator_page_shows_the_score_and_each_comparison(self):
        response = self.client.get("/combat/simulador/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["board"]["total"], 1)
        card = response.context["cards"][0]
        self.assertEqual((card["title"], card["real_winner"], card["real_rounds"]), ("Tebas", "Atacante", 2))
        self.assertEqual(card["sides"][0]["real"], "1.005")
        self.assertEqual(response.context["missing"], learning.MIN_CHECKS - 1)
        self.assertContains(response, "Faltam")
        self.assertNotContains(response, 'value="approve"')                 # not enough combats to approve anything

    def test_recomputing_stores_a_proposal_and_approving_too_early_is_refused(self):
        self.assertEqual(self.client.post("/combat/simulador/acao/", {"action": "propose"}).status_code, 302)
        self.assertEqual(SimulatorCalibration.load().proposed_json["samples"], 1)
        self.client.post("/combat/simulador/acao/", {"action": "approve"})
        self.assertEqual(SimulatorCalibration.load().approved_json, {})

    def test_the_combat_page_and_the_list_carry_the_comparison(self):
        page = self.client.get(f"/combat/{self.report.pk}/")
        self.assertContains(page, "Previsto × real")
        self.assertEqual(page.context["sim"]["real_winner"], "Atacante")
        listing = self.client.get("/combat/")
        self.assertContains(listing, "/combat/simulador/")
        self.assertEqual(listing.context["simulator"]["total"], 1)


class ExternalSampleTests(Base):
    """Combats read from a public battle log (IkaLogs) feed the same checks."""

    def _add(self, **changes):
        from apps.combat.services.checks import add_sample

        sample = {
            "source": "ikalogs", "external_id": "abc123", "title": "Fulano VS Beltrano", "fought_at": "21.06.2026 16:03:38",
            "inputs": {"field_level": 25, "wall_segments": 0, "attacker": {"303": 300, "305": 12}, "defender": {"315": 250},
                       "reinforcements": {"attacker": {"2": {"303": 50}}, "defender": {}}},
            "real": {"winner": "attacker", "rounds": 2, "losses": {"attacker": {"303": 20}, "defender": {"315": 250}}},
        }
        sample.update(changes)
        return add_sample(**sample)

    def test_a_combat_from_outside_is_kept_as_a_check_without_a_report(self):
        check = self._add()
        check.refresh_from_db()                                            # as it is kept: unit ids become text
        self.assertIsNone(check.report)
        self.assertEqual((check.source, check.external_id, check.title), ("ikalogs", "abc123", "Fulano VS Beltrano"))
        self.assertEqual(check.inputs_json["reinforcements"]["attacker"], {"2": {"303": 50}})
        self.assertEqual(check.real_json["losses"]["defender"], {"315": 250})
        self._add(title="Outro nome")                                      # read again: the same check, updated
        self.assertEqual(SimulationCheck.objects.count(), 1)
        board = scoreboard()
        self.assertEqual((board["total"], board["own"], board["winner_known"]), (1, 0, 1))

    def test_a_unit_the_simulator_has_no_numbers_for_leaves_the_combat_out(self):
        unknown = {"field_level": 5, "wall_segments": 5, "attacker": {"303": 100}, "defender": {"320": 75, "324": 2}, "reinforcements": {"attacker": {}, "defender": {}}}
        self.assertIsNone(self._add(inputs=unknown))
        self.assertFalse(SimulationCheck.objects.exists())

    def test_a_report_that_names_no_winner_does_not_count_for_the_winner_score(self):
        self._add()
        self._add(external_id="def456", real={"winner": "", "rounds": 14, "losses": {"attacker": {"303": 200}, "defender": {"315": 100}}})
        board = scoreboard()
        self.assertEqual((board["total"], board["winner_known"]), (2, 1))
        self.assertIn(board["winner_pct"], (0, 100))                        # one decided combat: all or nothing, the other does not dilute it
        learning.propose()                                                 # and the search copes with it
        self.assertEqual(SimulatorCalibration.load().proposed_json["samples"], 2)

    def test_losses_are_compared_over_the_rounds_the_real_combat_lasted(self):
        from apps.combat.services.checks import by_kind, simulate_check

        inputs = {"field_level": 25, "wall_segments": 0, "attacker": {"303": 2000}, "defender": {"303": 2000}, "reinforcements": {"attacker": {}, "defender": {}}}
        whole = simulate_check(inputs, {})
        cut = simulate_check(inputs, {}, real_rounds=2)
        self.assertGreater(whole["rounds"], 2)
        self.assertEqual((cut["rounds"], cut["winner"]), (whole["rounds"], whole["winner"]))     # who wins is still the whole fight
        self.assertLess(sum(cut["losses"]["attacker"].values()), sum(whole["losses"]["attacker"].values()))   # the losses, two rounds of it
        self._add()
        self._add(external_id="w1", inputs={"field_level": 25, "wall_segments": 7, "attacker": {"303": 50}, "defender": {}, "reinforcements": {"attacker": {}, "defender": {}}},
                  real={"winner": "attacker", "rounds": 1, "losses": {"attacker": {"303": 5}, "defender": {}}})
        self.assertEqual([(group["key"], group["total"]) for group in by_kind(SimulationCheck.objects.all())], [("wall", 1), ("open", 1)])

    def test_a_wall_only_field_counts_as_a_fight(self):
        wall = {"field_level": 25, "wall_segments": 7, "attacker": {"303": 50, "305": 30}, "defender": {}, "reinforcements": {"attacker": {}, "defender": {}}}
        check = self._add(inputs=wall, real={"winner": "attacker", "rounds": 1, "losses": {"attacker": {"303": 5}, "defender": {}}})
        self.assertEqual((check.inputs_json["assumptions"]["wall_level"], check.inputs_json["assumptions"]["wall_source"]), (15, "padrão"))

    def test_the_simulator_page_links_an_outside_combat_to_its_log(self):
        from django.contrib.auth import get_user_model

        self._add()
        self.client.force_login(get_user_model().objects.create_user(username="sim2", email="sim2@example.com", password="secret123"))
        response = self.client.get("/combat/simulador/")
        card = response.context["cards"][0]
        self.assertEqual((card["url"], card["external"], card["source"]), ("https://ikalogs.ru/report/abc123/", True, "IkaLogs"))
        self.assertContains(response, "https://ikalogs.ru/report/abc123/")
