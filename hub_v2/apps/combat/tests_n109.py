"""N-109: reading the combat report of the game and drawing its battlefield."""

import importlib.util
import unittest

from django.test import SimpleTestCase

from apps.combat.services import report_parser

HAS_PILLOW = importlib.util.find_spec("PIL") is not None
EMPTY = '<div class="slot empty"></div>'


def slot(side: int, zone: int, index: int, unit: int, left, lost) -> str:
    return (
        f'<div id="slot{side}_{zone}_{index}" class="slot army_small normal s{unit}"> <div class="hitpoints"></div>'
        f' <div class="loss" style="height:0px"></div> <div class="number center"> {left} (-{lost}) </div> </div>'
    )


def field(field_id: str, zones: dict) -> str:
    inner = "".join(f'<div class="location large {name}"> {"".join(slots)} </div>' for name, slots in zones.items())
    return f'<div class="field " id="{field_id}"> {inner} </div>'


def reserve(res_id: str, units) -> str:
    items = "".join(f'<li> <div class="army_small normal s{unit}"></div>{amount} </li>' for unit, amount in units)
    return f'<div id="{res_id}"> <div class="nav"><ul><li class="res">Reserva</li></ul></div> <div class="units "> <ul>{items}</ul> </div> </div>'


def round_html(number: int, total: int, attacker_zones: dict, defender_zones: dict, *, attacker_reserve=(), defender_reserve=()) -> str:
    """One page of the detailed view, written the way the game writes it."""
    return f'''
<div id="militaryAdvisorDetailedReportView">
 <table class="round_nav"><tr><td style="text-align:center"><span class="headline_big bold">Batalha por #Tebas Round<br />{number} / {total}</span><br /> (03.06.2026 12:{number:02d}:33)</td></tr></table>
 <div class="container_attacker clearfloat"> Atacante:<br/> Alpha[TAG] </div>
 <div id="battlefield" class="battlefield_city_A_D">
  {reserve("resAttacker", attacker_reserve)}
  {field("fieldAttacker", attacker_zones)}
  {reserve("resDefender", defender_reserve)}
  {field("fieldDefender", defender_zones)}
 </div>
 <div class="container_defender">Defensor:<br>Inimigo[XYZ]</div>
 <div>
  <table class="table01 container_morale center border dotted"> <tr> <th class="atter">Atacante</th> <th>Unidades</th> <th colspan="2">Moral</th> </tr>
   <tr> <td class="avatarName">Alpha[TAG]</td> <td class="militarySize">1.339 (-1.005) </td>
    <td class="militaryMorale right" id="slotmorale1"> <div class="morale_bar"><div class="bar" style="width:87%;"></div></div> </td> <td class="morale left">87 % </td> </tr> </table>
  <table class="table01 container_morale center border dotted"> <tr> <th class="deffer">Defensor</th> <th>Unidades</th> <th colspan="2">Moral</th> </tr>
   <tr> <td class="avatarName">Inimigo[XYZ]</td> <td class="militarySize">18 (-40) </td>
    <td class="militaryMorale right" id="slotmorale2"> <div class="morale_bar"><div class="bar" style="width:30%;"></div></div> </td> <td class="morale left">30 % </td> </tr> </table>
 </div>
 <div class="container_events"> <p class="headline">Eventos atuais</p>
  <div class=""> <span class="bold">03.06.2026 12:{number:02d}:33</span> <p class="description">Inimigo[XYZ] juntou-se ao combate, do lado do defensor, com as seguintes unidades: <ul class="unitlist"><li class="army_small normal s315"><div class="amount">57</div></li><li class="army_small normal s314"><div class="amount">1</div></li></ul><div style="clear: both"></div></p> </div>
  <div class="alt"> <span class="bold">03.06.2026 12:{number:02d}:33</span> <p class="description">O exército de Alpha fugiu, devido ao baixo moral.</p> </div>
 </div>
 <script language="javascript"> function x() {{ return '<p class="description">não é evento</p>'; }} </script>
</div>'''


ROUND_1 = round_html(
    1, 2,
    {
        "airfighter": [EMPTY, EMPTY], "air": [EMPTY, EMPTY], "flankLeft": [EMPTY, EMPTY, EMPTY], "flankRight": [EMPTY, EMPTY, EMPTY],
        "main": [slot(11, 21, 1, 303, 45, 5), slot(11, 21, 0, 303, "1.250", "1.000"), EMPTY],
        "longRange": [EMPTY] * 3, "artillery": [EMPTY, slot(11, 24, 0, 305, 6, 0), EMPTY],
    },
    {
        "artillery": [EMPTY] * 3, "longRange": [EMPTY] * 3,
        "main": [slot(12, 21, 1, 314, 1, 0), slot(12, 21, 0, 315, 17, 40), EMPTY],
        "flankRight": [EMPTY] * 3, "flankLeft": [EMPTY] * 3, "air": [EMPTY, EMPTY], "airfighter": [EMPTY, EMPTY],
    },
    attacker_reserve=[(312, 37), (310, 1)],
)
ROUND_2 = round_html(
    2, 2,
    {"main": [slot(11, 21, 1, 303, 45, 0), EMPTY], "artillery": [slot(11, 24, 0, 305, 6, 0)]},
    {"main": [slot(12, 21, 1, 314, 0, 1), slot(12, 21, 0, 315, 0, 17)], "artillery": [EMPTY]},
)
DETAILED = ROUND_1 + "\n" + report_parser.ROUND_SEPARATOR + "\n" + ROUND_2

SUMMARY = '''
<h3 class="header">Vista Geral Militar</h3>
<div id="militaryAdvisorReportView"><div id="troopsReport"><div class="contentBox01h">
 <h3 class="header">Batalha por #Tebas <span class="date">(03.06.2026 12:02:33)</span></h3>
 <div class="content">
  <div class="attacker textgreen"> <label>Atacante:</label> <span>Alpha[TAG] de <b><a href="?view=island&cityId=1">Atenas</a></b></span> </div>
  <div class="defender textred"> <label>Defensor:</label> <span><a href="?view=sendIKMessage&receiverId=9">Inimigo[XYZ]</a> de <b><a href="?view=island&cityId=2">Tebas</a></b></span> </div>
  <h5>Resultado da batalha - Round 2</h5>
  <table class="table01 overview militaryList">
   <tr> <td class="col1 textgreen"></td>
    <td class="alt" colspan="2"> <div class="army s315"> <div class="tooltip"> Lanceiro </div> </div> </td>
    <td class="" colspan="2"> <div class="army s305"> <div class="tooltip"> Morteiro </div> </div> </td>
    <td class="alt" colspan="2"> <div class="army s303"> <div class="tooltip"> Hoplita </div> </div> </td>
    <td class="" colspan="2"> <div class="army s314"> <div class="tooltip"> Muralha </div> </div> </td> </tr>
   <tr class="textblue"> <td class="firstCol attackerLine">Alpha</td>
    <td class="center alt" colspan="2">-</td>
    <td class="numbers ">6</td><td class="numbers2 ">(-0)</td>
    <td class="numbers alt">1.295</td><td class="numbers2 alt">(-1.005)</td>
    <td class="center " colspan="2">-</td> </tr>
   <tr> <td colspan="14" class="col1 nobg textred"></td> </tr>
   <tr class="textred"> <td class="firstCol defenderLine">Inimigos</td>
    <td class="numbers alt">0</td><td class="numbers2 alt">(-57)</td>
    <td class="center " colspan="2">-</td>
    <td class="center alt" colspan="2">-</td>
    <td class="numbers ">0</td><td class="numbers2 ">(-1)</td> </tr>
   <tr class="line"> <td colspan="14"></td> </tr>
  </table>
 </div>
 <div class="result">
  <div class="winners headline"> Vencedores: <br />Alpha[TAG] </div>
  <div class="losers headline"> Perdedores: <br />Inimigo[XYZ] </div>
  <div>A cidade de <a href="?view=island&cityId=2">Tebas</a> foi pilhada por Alpha[TAG]. Os seguintes recursos foram roubados :
   <ul class="resources"><li class="value">80.631 <img src="//x/wood.png" title="Materiais de construção" alt="" /></li><li class="value">2.220 <img src="//x/s.png" title="Enxofre" alt="" /></li></ul></div>
 </div>
</div></div></div>'''


class ReportParserTests(SimpleTestCase):
    def test_units_are_named_by_the_ids_the_game_uses(self):
        self.assertEqual(report_parser.unit_name(305), "Morteiro")      # the old text called this one Arqueiro
        self.assertEqual(report_parser.unit_name("304"), "Carabineiro")
        self.assertEqual(report_parser.unit_name(315), "Lanceiro")
        self.assertEqual(report_parser.unit_name(314), "Muralha")
        self.assertEqual(report_parser.unit_name(999), "Unidade 999")
        self.assertEqual(report_parser.unit_name(320), "Membro do Clube de Bárbaros")
        self.assertTrue(report_parser.unit_icon(303).endswith("hoplita.png"))
        self.assertEqual(report_parser.unit_icon(314), "")              # the wall has no picture: it is drawn

    def test_a_round_is_read_side_by_side_and_zone_by_zone(self):
        rounds = report_parser.parse_rounds(DETAILED)
        self.assertEqual([item["round"] for item in rounds], [1, 2])
        first = rounds[0]
        self.assertEqual((first["title"], first["total_rounds"], first["date"]), ("Batalha por Tebas", 2, "03.06.2026 12:01:33"))
        self.assertEqual((first["attacker"]["name"], first["defender"]["name"]), ("Alpha[TAG]", "Inimigo[XYZ]"))
        main = first["attacker"]["zones"]["main"]
        self.assertEqual(main[0], {"unit": 303, "left": 45, "lost": 5, "damage": 0})
        self.assertEqual(main[1], {"unit": 303, "left": 1250, "lost": 1000, "damage": 0})   # thousands come with a dot
        self.assertIsNone(main[2])                                              # an empty slot keeps its place
        self.assertEqual(len(first["attacker"]["zones"]["flankLeft"]), 3)
        self.assertEqual(first["attacker"]["reserve"], [{"unit": 312, "left": 37}, {"unit": 310, "left": 1}])
        self.assertEqual((first["attacker"]["left"], first["attacker"]["lost"]), (45 + 1250 + 6 + 37 + 1, 1005))
        self.assertEqual(first["defender"]["zones"]["main"][1], {"unit": 315, "left": 17, "lost": 40, "damage": 0})
        self.assertEqual(first["field"], "battlefield_city_A_D")
        self.assertEqual(report_parser.round_losses(first, "defender"), {315: 40})
        self.assertEqual(report_parser.round_losses(rounds[1], "defender"), {314: 1, 315: 17})
        self.assertEqual(first["morale"]["attacker"], [{"name": "Alpha[TAG]", "left": 1339, "lost": 1005, "morale": 87}])
        self.assertEqual(first["morale"]["defender"][0]["morale"], 30)
        self.assertEqual(first["events"], [
            {"date": "03.06.2026 12:01:33", "text": "Inimigo[XYZ] juntou-se ao combate, do lado do defensor, com as seguintes unidades",
             "units": [{"unit": 315, "amount": 57}, {"unit": 314, "amount": 1}]},
            {"date": "03.06.2026 12:01:33", "text": "O exército de Alpha fugiu, devido ao baixo moral.", "units": []},
        ])
        self.assertIsNone(report_parser.parse_round("<div>outra página</div>"))
        self.assertEqual(report_parser.parse_rounds(""), [])

    def test_the_summary_has_the_parties_the_units_the_outcome_and_the_loot(self):
        summary = report_parser.parse_summary(SUMMARY)
        self.assertEqual((summary["title"], summary["date"], summary["rounds"]), ("Batalha por Tebas", "03.06.2026 12:02:33", 2))
        self.assertEqual(summary["attacker"], {"name": "Alpha[TAG]", "city": "Atenas"})
        self.assertEqual(summary["defender"], {"name": "Inimigo[XYZ]", "city": "Tebas"})
        units = {item["name"]: (item["attacker"], item["defender"]) for item in summary["units"]}
        self.assertEqual(units["Lanceiro"], (None, {"left": 0, "lost": 57}))
        self.assertEqual(units["Morteiro"], ({"left": 6, "lost": 0}, None))
        self.assertEqual(units["Hoplita"], ({"left": 1295, "lost": 1005}, None))
        self.assertEqual(units["Muralha"], (None, {"left": 0, "lost": 1}))
        self.assertEqual(summary["totals"], {"attacker": {"left": 1301, "lost": 1005}, "defender": {"left": 0, "lost": 58}})
        self.assertEqual((summary["winners"], summary["losers"]), ("Alpha[TAG]", "Inimigo[XYZ]"))
        self.assertEqual(summary["loot"], {"wood": 80631, "sulfur": 2220})
        self.assertIsNone(report_parser.parse_summary("<div>nada</div>"))
        # a unit the hub does not know keeps the name the game gives it in the summary
        renamed = report_parser.parse_summary(SUMMARY.replace("army s305", "army s399").replace(" Morteiro ", " Unidade Nova "))
        self.assertIn("Unidade Nova", [item["name"] for item in renamed["units"]])


@unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")
class BattleImageTests(SimpleTestCase):
    def _size(self, png: bytes):
        import io

        from PIL import Image

        return Image.open(io.BytesIO(png)).size

    def test_a_round_becomes_a_picture_and_the_last_one_carries_the_outcome(self):
        from apps.combat.services.battle_image import FIELD, PAD, SCALE, render_battle

        rounds = report_parser.parse_rounds(DETAILED)
        first = render_battle(rounds[0], own_name="Alpha")
        self.assertTrue(first.startswith(b"\xff\xd8"))                   # a JPEG
        width, height = self._size(first)
        self.assertEqual(width, round((FIELD[0] + 2 * PAD) * SCALE))
        final = render_battle(rounds[1], report_parser.parse_summary(SUMMARY), own_name="Alpha")
        self.assertGreater(self._size(final)[1], self._size(render_battle(rounds[1]))[1])   # the outcome goes under the field
        self.assertLess(len(final), 1_000_000)

    def test_a_unit_without_picture_and_an_empty_side_do_not_break_the_drawing(self):
        from apps.combat.services.battle_image import render_battle

        rounds = report_parser.parse_rounds(round_html(1, 1, {"main": [slot(11, 21, 0, 999, 3, 0)]}, {"main": [EMPTY]}))
        self.assertTrue(render_battle(rounds[0]).startswith(b"\xff\xd8"))


# ── what the hub does when a report arrives, and the pages that show it ──

from unittest.mock import patch  # noqa: E402

from django.test import TestCase  # noqa: E402

from apps.accounts.models import Account, GameAccount, Node  # noqa: E402
from apps.combat.models import CombatReport  # noqa: E402
from apps.combat.services.intake import merge_rounds, take_in  # noqa: E402

SENT_OK = {"ok": True, "result": {"message_id": 5}}
SEND_MESSAGE = "apps.telegram.services.notifications.send_message"
SEND_PHOTO = "apps.telegram.services.notifications.send_photo"


class CombatBase(TestCase):
    def setUp(self):
        from apps.telegram.models import NotificationTemplate, TelegramBotConfig

        node = Node.objects.create(name="node-combat")
        account = Account.objects.create(node=node, label="Lobby Combat", email="combat@example.com", password_enc="x")
        self.alpha = GameAccount.objects.create(
            account=account, lobby_account_id=1, server_id="s78-br", server_language="br", server_number=78, name="Alpha",
        )
        TelegramBotConfig.objects.create(pk=1, bot_token="123:abc", enabled=True, chat_id="1000", notify_combat_report=True)
        NotificationTemplate.seed_defaults()

    def _report(self, **fields):
        values = {"game_account": self.alpha, "combat_id": 4242, "target_city_name": "Tebas [10:20]", "target_owner": "Inimigo"}
        values.update(fields)
        return CombatReport.objects.create(**values)

    def _take(self, report, **kwargs):
        """(what was announced, texts sent) - whichever way the message went out."""
        with patch(SEND_PHOTO, return_value=SENT_OK) as photo, patch(SEND_MESSAGE, return_value=SENT_OK) as message:
            kind = take_in(report, **kwargs)
        texts = [call.kwargs.get("caption", "") for call in photo.call_args_list] + [call.args[1] for call in message.call_args_list]
        return kind, texts


class IntakeTests(CombatBase):
    def test_rounds_delivered_apart_become_one_report_in_order(self):
        merged = merge_rounds(ROUND_2, ROUND_1)
        self.assertEqual([item["round"] for item in report_parser.parse_rounds(merged)], [1, 2])
        again = merge_rounds(merged, ROUND_1)                                  # the same round delivered twice
        self.assertEqual([item["round"] for item in report_parser.parse_rounds(again)], [1, 2])
        self.assertEqual(merge_rounds("", ROUND_2).count("battlefield_city_A_D"), 1)

    def test_each_round_is_told_once_and_then_the_outcome(self):
        report = self._report(detailed_html=ROUND_1)
        kind, texts = self._take(report)
        self.assertEqual(kind, "round")
        self.assertIn("Round 1 · Tebas [10:20]", texts[0])
        report.refresh_from_db()
        self.assertEqual((report.finished, report.notified_round, report.notified_final), (False, 1, False))
        self.assertEqual((report.attacker_name, report.defender_name, report.battle_title), ("Alpha[TAG]", "Inimigo[XYZ]", "Batalha por Tebas"))
        self.assertEqual(report.attacker_losses, {"303": 1005})                 # no summary yet: added up from the rounds
        from django.utils import timezone

        self.assertEqual(timezone.localtime(report.combat_date).strftime("%d/%m/%Y %H:%M"), "03/06/2026 12:01")   # the game's date, not the delivery's

        self.assertEqual(self._take(report), ("", []))                          # delivered again: nothing new to tell

        report.detailed_html, report.summary_html = merge_rounds(report.detailed_html, ROUND_2), SUMMARY
        kind, texts = self._take(report)
        self.assertEqual(kind, "final")
        self.assertEqual(len(texts), 1)                                         # the outcome, not round 2 and then the outcome
        self.assertIn("Vitória em 2 rounds", texts[0])                          # Alpha is among the winners
        self.assertIn("Saque: 80.631 madeira", texts[0])
        report.refresh_from_db()
        self.assertEqual((report.finished, report.result, report.notified_final, report.total_rounds), (True, "victory", True, 2))
        self.assertEqual((report.total_loot, report.defender_losses), (82851, {"315": 57, "314": 1}))
        self.assertEqual(self._take(report), ("", []))

    def test_a_combat_that_arrives_already_over_is_one_message(self):
        report = self._report(detailed_html=DETAILED, summary_html=SUMMARY)
        kind, texts = self._take(report)
        self.assertEqual((kind, len(texts)), ("final", 1))

    def test_old_combats_delivered_to_fill_the_history_are_not_told(self):
        report = self._report(detailed_html=DETAILED, summary_html=SUMMARY)
        self.assertEqual(self._take(report, silent=True), ("", []))
        report.refresh_from_db()
        self.assertEqual((report.notified_final, report.notified_round, report.finished), (True, 2, True))

    def test_the_agent_endpoint_joins_the_new_rounds_and_answers_whether_it_is_over(self):
        from rest_framework.test import APIRequestFactory

        from apps.combat.api.views import CombatReportSaveView

        factory = APIRequestFactory()

        def post(payload):
            request = factory.post("/api/agent/combat/reports/", {"game_account_id": str(self.alpha.pk), **payload}, format="json")
            with patch.object(CombatReportSaveView, "authentication_classes", []), patch.object(CombatReportSaveView, "permission_classes", []), \
                    patch(SEND_PHOTO, return_value=SENT_OK), patch(SEND_MESSAGE, return_value=SENT_OK):
                return CombatReportSaveView.as_view()(request).data

        first = post({"combat_id": 99, "target_city_name": "Tebas", "detailed_html": ROUND_1, "append_rounds": True})
        self.assertEqual((first["finished"], first["announced"], first["rounds"]), (False, "round", 1))
        second = post({"combat_id": 99, "detailed_html": ROUND_2, "append_rounds": True, "summary_html": SUMMARY, "result": "victory"})
        self.assertEqual((second["finished"], second["announced"], second["rounds"]), (True, "final", 2))
        report = CombatReport.objects.get(combat_id=99)
        self.assertEqual(len(report_parser.parse_rounds(report.detailed_html)), 2)
        self.assertEqual(post({"combat_id": 99, "detailed_html": ROUND_2, "append_rounds": True})["announced"], "")


class CombatPagesTests(CombatBase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_user(username="combat", email="combat@example.com", password="secret123"))
        self.over = self._report(detailed_html=DETAILED, summary_html=SUMMARY)
        self._take(self.over, silent=True)
        self.live = self._report(combat_id=4300, detailed_html=ROUND_1)
        self._take(self.live, silent=True)

    def test_the_list_shows_each_combat_and_filters_the_ones_going_on(self):
        response = self.client.get("/combat/")
        self.assertEqual(response.status_code, 200)
        cards = {item["report"].combat_id: item for item in response.context["cards"]}
        self.assertEqual((cards[4242]["label"], cards[4242]["title"], cards[4242]["loot"]), ("Vitória", "Tebas", "82.851"))
        self.assertEqual((cards[4242]["attacker"], cards[4242]["defender_lost"]), ("Alpha[TAG]", "58"))
        self.assertEqual(cards[4300]["label"], "Em andamento")
        self.assertEqual(response.context["live_count"], 1)
        live = self.client.get("/combat/", {"state": "live"})
        self.assertEqual([item["report"].combat_id for item in live.context["cards"]], [4300])
        mine = self.client.get("/combat/", {"account": str(self.alpha.pk), "state": "victory"})
        self.assertEqual([item["report"].combat_id for item in mine.context["cards"]], [4242])
        self.assertEqual(self.client.get("/combat/", {"account": "lixo"}).status_code, 200)

    def test_the_page_builds_the_field_with_the_pieces_of_the_game(self):
        from apps.combat.services.board import round_board

        board = round_board(report_parser.parse_rounds(DETAILED)[0], "Alpha")
        slot = next(item for item in board["slots"] if item.get("label") == "45 (-5)")
        self.assertEqual((slot["left"], slot["top"]), ("47.182", "34.005"))         # where the game puts the first front-line slot of three
        self.assertEqual((slot["sprite"], slot["damage"], slot["ammo"]), ("7.635% 50%", "0.000", ""))
        wall = next(item for item in board["slots"] if item.get("label") == "1 (-0)")
        self.assertTrue(wall["sprite"].endswith("50%"))
        self.assertEqual(sum(1 for item in board["slots"] if item.get("empty")), 33)
        self.assertEqual([unit["amount"] for unit in board["reserves"][0]["units"]], ["37", "1"])
        self.assertEqual((board["sides"]["attacker"]["own"], board["sides"]["attacker"]["armies"][0]["morale"]), (True, 87))
        self.assertEqual(board["events"][0]["units"][0]["amount"], "57")
        self.assertEqual(board["background"], "game/combat/battlefield_city_A_D.jpg")
        fallen = round_board(report_parser.parse_rounds(DETAILED)[1])
        self.assertTrue(next(item for item in fallen["slots"] if item.get("label") == "0 (-17)")["sprite"].endswith("100%"))
        page = self.client.get(f"/combat/{self.over.pk}/").content.decode()
        self.assertIn('class="cbf"', page)
        self.assertIn("left: 47.182%; top: 34.005%;", page)
        self.assertNotIn('<img class="cbt-field"', page)                           # no stretched picture on the page

    def test_a_round_can_be_sent_to_telegram_from_the_page(self):
        import json

        with patch(SEND_PHOTO, return_value=SENT_OK) as photo, patch(SEND_MESSAGE, return_value=SENT_OK) as message:
            response = self.client.post(f"/combat/{self.over.pk}/send/", {"round": "1"})
            texts = [call.kwargs.get("caption", "") for call in photo.call_args_list] + [call.args[1] for call in message.call_args_list]
            self.assertEqual(len(texts), 1)
            self.assertIn("Round 1 · Tebas", texts[0])
            self.assertEqual(json.loads(response["HX-Trigger"])["toast"]["type"], "success")
            self.client.post(f"/combat/{self.over.pk}/send/", {"round": "2"})               # the last round of a finished combat: the outcome
            texts = [call.kwargs.get("caption", "") for call in photo.call_args_list] + [call.args[1] for call in message.call_args_list]
            self.assertIn("Vitória em 2 rounds", texts[-1])
        from apps.telegram.models import TelegramBotConfig

        TelegramBotConfig.objects.update(notify_combat_report=False)
        with patch(SEND_PHOTO, return_value=SENT_OK) as photo, patch(SEND_MESSAGE, return_value=SENT_OK) as message:
            response = self.client.post(f"/combat/{self.over.pk}/send/", {"round": "1"})
        self.assertEqual((photo.call_count + message.call_count, json.loads(response["HX-Trigger"])["toast"]["type"]), (0, "error"))

    def test_a_combat_is_paged_by_round(self):
        response = self.client.get(f"/combat/{self.over.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.context["numbers"], response.context["current"]), ([1, 2], 2))      # opens on the last round
        self.assertContains(response, f"/combat/{self.over.pk}/round/2.jpg")
        self.assertEqual(self.client.get(f"/combat/{self.over.pk}/", {"round": "1"}).context["current"], 1)
        self.assertEqual([unit["name"] for unit in response.context["units"]], ["Lanceiro", "Morteiro", "Hoplita", "Muralha"])

    @unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")
    def test_the_picture_of_a_round_is_served_and_a_round_that_does_not_exist_is_not(self):
        response = self.client.get(f"/combat/{self.over.pk}/round/1.jpg")
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/jpeg"))
        self.assertTrue(response.content.startswith(b"\xff\xd8"))
        self.assertIn("86400", response["Cache-Control"])
        self.assertIn("max-age=60", self.client.get(f"/combat/{self.live.pk}/round/1.jpg")["Cache-Control"])   # may still gain the outcome
        self.assertEqual(self.client.get(f"/combat/{self.over.pk}/round/9.jpg").status_code, 404)
