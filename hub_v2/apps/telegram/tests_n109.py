"""N-109: the text of the other notices, and the combat report with the picture of the battlefield."""

import unittest
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.combat.models import CombatReport
from apps.combat.tests_n109 import DETAILED, HAS_PILLOW, SUMMARY
from apps.proxy.maintenance import announcement, announcement_parts
from apps.telegram.constants import NOT_EMITTED
from apps.telegram.models import MessageAudit, NotificationTemplate, TelegramBotConfig
from apps.telegram.services.combat import combat_context, combat_picture
from apps.telegram.services.formatters import format_message
from apps.telegram.services.notifications import CAPTION_LIMIT, notify

SENT_OK = {"ok": True, "result": {"message_id": 91}}
REFUSED = {"ok": False, "description": "Bad Request: PHOTO_INVALID_DIMENSIONS"}
SEND_MESSAGE = "apps.telegram.services.notifications.send_message"
SEND_PHOTO = "apps.telegram.services.notifications.send_photo"


class Base(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-n109")
        self.account = Account.objects.create(node=self.node, label="Lobby N109", email="n109@example.com", password_enc="x")
        self.alpha = GameAccount.objects.create(
            account=self.account, lobby_account_id=1, server_id="s78-br", server_language="br", server_number=78, name="Alpha",
        )
        self.bot = TelegramBotConfig.objects.create(
            pk=1, bot_token="123:abc", enabled=True, chat_id="1000", notify_combat_report=True, notify_attack_alert=True,
            notify_proxy_change=True, notify_job_failed=True,
        )
        NotificationTemplate.seed_defaults()

    def _report(self, **changes):
        fields = {
            "game_account": self.alpha, "combat_id": 4242, "result": "victory", "total_rounds": 2,
            "target_city_name": "Tebas [10:20]", "target_owner": "Inimigo", "summary_html": SUMMARY, "detailed_html": DETAILED,
        }
        fields.update(changes)
        return CombatReport.objects.create(**fields)


class CombatTextTests(Base):
    def test_the_end_of_a_combat_is_written_from_the_report_the_hub_keeps(self):
        self._report()
        text = format_message("combat_report", game_account=self.alpha, phase="final", combat_id=4242, result="victory", city_name="Tebas [10:20]")
        self.assertIn("⚔️ <b>Vitória em 2 rounds · Tebas [10:20]</b>", text)
        self.assertIn("Alpha[TAG] × Inimigo[XYZ]", text)
        self.assertIn("Atacante: Hoplita -1.005", text)                 # the whole combat, by the right unit names
        self.assertIn("Defensor: Lanceiro -57, Muralha -1", text)
        self.assertIn("Saque: 80.631 madeira · 2.220 enxofre", text)

    def test_a_round_tells_only_what_fell_in_it(self):
        self._report()
        context = combat_context({"phase": "round", "combat_id": 4242, "round": 1, "total_rounds": 2, "result": "victory"})
        self.assertEqual(context["headline"], "Round 1")
        self.assertEqual((context["attacker_losses"], context["defender_losses"]), ("Hoplita -1.005", "Lanceiro -40"))
        self.assertEqual((context["loot_line"], context["winners"]), ("", ""))       # nobody won yet
        text = format_message("combat_report", **context)
        self.assertIn("Round 1 · Tebas", text)
        self.assertNotIn("Saque", text)
        self.assertEqual(combat_context(context), context)                           # filling it twice changes nothing

    def test_without_a_stored_report_the_notice_uses_what_the_agent_sent(self):
        context = combat_context({
            "phase": "final", "combat_id": 777, "result": "defeat", "city_name": "Argos", "owner_name": "Fulano", "total_rounds": 1,
            "attacker_losses": {"305": 2, "303": 30}, "defender_losses": {},
        })
        self.assertEqual(context["headline"], "Derrota em 1 round")
        self.assertEqual(context["attacker_losses"], "Hoplita -30, Morteiro -2")     # 305 is the mortar, not the archer
        self.assertEqual(context["defender_losses"], "sem perdas")
        self.assertEqual(context["defender"], "Fulano")
        self.assertIsNone(combat_picture(context, self.alpha))

    def test_the_text_can_be_rewritten_with_the_variables_of_the_combat(self):
        self._report()
        NotificationTemplate.objects.filter(event_key="combat_report").update(
            title_template="{result}: {city}", body_template="{attacker} perdeu {attacker_losses}\n{loot_line}")
        text = format_message("combat_report", phase="final", combat_id=4242, result="victory", city_name="Tebas")
        self.assertIn("<b>Vitória: Tebas</b>", text)
        self.assertIn("Alpha[TAG] perdeu Hoplita -1.005", text)
        self.assertEqual(NotificationTemplate.for_event("combat_report").unknown_variables(), [])


class CombatNoticeTests(Base):
    def _notify(self, photo_result=SENT_OK, **metadata):
        with patch(SEND_PHOTO, return_value=photo_result) as photo, patch(SEND_MESSAGE, return_value=SENT_OK) as message:
            sent = notify(event_key="combat_report", game_account=self.alpha, account=self.account, title="Combate — final", **metadata)
        return sent, photo, message

    @unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")
    def test_the_notice_goes_as_a_picture_with_the_text_as_caption(self):
        self._report()
        sent, photo, message = self._notify(
            phase="final", combat_id=4242, result="victory", city_name="Tebas [10:20]", owner_name="Inimigo", total_rounds=2,
            attacker_losses={"303": 1005}, defender_losses={"315": 57},
        )
        self.assertTrue(sent)
        message.assert_not_called()
        chat_id, picture = photo.call_args.args[:2]
        self.assertEqual(chat_id, "1000")
        self.assertTrue(picture.startswith(b"\xff\xd8"))                  # a JPEG: the field is a photograph
        caption = photo.call_args.kwargs["caption"]
        self.assertIn("Vitória em 2 rounds", caption)
        self.assertLessEqual(len(caption), CAPTION_LIMIT)
        audit = MessageAudit.objects.get()
        self.assertEqual((audit.channel, audit.status), ("combat_report", "sent"))
        self.assertIn("Hoplita -1.005", audit.context_json)             # the history keeps the text, not the picture
        self.assertNotIn("PNG", audit.context_json)

    @unittest.skipUnless(HAS_PILLOW, "Pillow is not installed")
    def test_a_refused_picture_does_not_lose_the_text(self):
        self._report()
        sent, photo, message = self._notify(photo_result=REFUSED, phase="round", combat_id=4242, round=1, total_rounds=2, result="victory")
        self.assertTrue(sent)
        photo.assert_called_once()
        self.assertIn("Round 1 · Tebas", message.call_args.args[1])

    def test_without_a_stored_report_only_the_text_goes(self):
        sent, photo, message = self._notify(phase="final", combat_id=555, result="defeat", city_name="Argos", total_rounds=3,
                                            attacker_losses={"303": 3}, defender_losses={})
        self.assertTrue(sent)
        photo.assert_not_called()
        text = message.call_args.args[1]
        self.assertIn("Derrota em 3 rounds · Argos", text)
        self.assertIn("Atacante: Hoplita -3", text)

    def test_a_long_text_goes_after_the_picture_not_cut_as_a_caption(self):
        with patch(SEND_PHOTO, return_value=SENT_OK) as photo, patch(SEND_MESSAGE, return_value=SENT_OK) as message:
            from apps.telegram.services.notifications import _send

            _send("1000", "x" * (CAPTION_LIMIT + 1), b"\x89PNG", None)
        self.assertNotIn("caption", photo.call_args.kwargs)
        self.assertEqual(len(message.call_args.args[1]), CAPTION_LIMIT + 1)


class OtherNoticesTests(Base):
    def test_every_notice_that_is_sent_has_variables_of_its_own_and_a_default_that_uses_only_them(self):
        for event_key in ("attack_alert", "job_failed", "job_done", "combat_report", "proxy_change", "island_monitor", "cinema_available", "diplomacy_message"):
            self.assertIn(event_key, NotificationTemplate.EVENT_VARS, event_key)
            template = NotificationTemplate.for_event(event_key)
            self.assertIsNotNone(template, event_key)                   # 0017 created the two that were missing
            template.reset_to_default()
            self.assertEqual(template.unknown_variables(), [], event_key)
        names = [name for name, _label in NotificationTemplate.variables_for("proxy_change")]
        self.assertNotIn("{ga_name}", names)                            # a notice about the system, not about an account
        self.assertIn("{ga_name}", [name for name, _label in NotificationTemplate.variables_for("attack_alert")])

    def test_the_attack_alert_is_written_from_what_the_alert_carries(self):
        NotificationTemplate.for_event("attack_alert").reset_to_default()
        text = format_message("attack_alert", ga_name="Alpha", server_id="s78-br", mission="Roubar", target_city="Atenas",
                              origin_player="Fulano", origin_city="Argos", troops=70, fleet=12, time_left_human="21min 22s")
        self.assertIn("🆘 <b>Roubar em Atenas</b>", text)
        self.assertIn("Fulano (Argos) → Atenas", text)
        self.assertIn("Tropas: 70 · Frotas: 12", text)
        self.assertIn("Chegada em 21min 22s", text)

    def test_the_proxy_notice_is_sent_by_parts_and_reads_the_same_as_before(self):
        report = {
            "accounts_replaced": [{"account": "Principal", "removed": ["a", "b"], "added": ["c"]}], "blocks_cleared": ["Alpha"],
            "sync": {"success": True, "created": 3, "deactivated": 2}, "passed": 18, "tested": 20, "reason": "falha de proxy no login de Alpha",
        }
        parts = announcement_parts(report)
        self.assertEqual(parts["tests"], "Teste: 18 de 20 proxies ok.")
        self.assertIn("Conta Principal: 2 proxy(s) fora de uso, 1 novo(s) reservado(s).", parts["changes"])
        self.assertEqual(announcement(report).splitlines()[-1], "Motivo da verificação: falha de proxy no login de Alpha.")
        self.assertEqual((announcement_parts({}), announcement({})), ({}, ""))
        with patch(SEND_MESSAGE, return_value=SENT_OK) as message:
            sent = notify(event_key="proxy_change", title="Proxies: verificação automática", body=announcement(report), **parts)
        self.assertTrue(sent)
        text = message.call_args.args[1]
        self.assertIn("🔁 <b>Proxies: verificação automática</b>", text)
        self.assertIn("Bloqueio de login liberado: Alpha.", text)
        self.assertIn("Lista do provedor: 3 novo(s), 2 retirado(s).", text)
        self.assertEqual(text.count("Teste: 18 de 20"), 1)


class ScreenTests(Base):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_user(username="n109", email="n109@example.com", password="secret123"))

    def test_the_notices_nothing_sends_are_set_apart(self):
        self.assertEqual(NOT_EMITTED, {"build_complete", "research_complete", "low_wine", "daily_summary"})
        response = self.client.get("/telegram/")
        groups = {group["title"]: {item["template"].event_key for item in group["items"]} for group in response.context["template_groups"]}
        self.assertEqual(groups["Ainda sem envio"], NOT_EMITTED)
        self.assertLessEqual({"combat_report", "proxy_change", "attack_alert", "job_failed"}, groups["Avisos"])
        self.assertContains(response, "sem envio")

    def test_the_editor_of_the_combat_shows_its_variables_and_a_real_combat(self):
        from django.urls import reverse

        template = NotificationTemplate.for_event("combat_report")
        page = self.client.get(reverse("telegram:template-edit", args=[template.pk])).content.decode()
        self.assertIn("{headline}", page)
        self.assertIn("com dados de exemplo", page)
        self.assertIn("Vitória em 2 rounds", page)                      # the made-up sample, while the hub keeps no combat
        self.assertNotIn("data:image/jpeg", page)

        self._report()
        page = self.client.get(reverse("telegram:template-edit", args=[template.pk])).content.decode()
        self.assertIn("Alpha[TAG] × Inimigo[XYZ]", page)                # now the latest stored combat
        self.assertEqual("data:image/jpeg" in page, HAS_PILLOW)

    def test_the_test_button_sends_the_combat_with_its_picture(self):
        from django.urls import reverse

        self._report()
        template = NotificationTemplate.for_event("combat_report")
        with patch(SEND_PHOTO, return_value=SENT_OK) as photo, patch(SEND_MESSAGE, return_value=SENT_OK) as message:
            page = self.client.post(reverse("telegram:template-test", args=[template.pk])).content.decode()
        self.assertIn("Teste enviado para o chat global.", page)
        if HAS_PILLOW:
            self.assertIn("Teste do modelo", photo.call_args.kwargs["caption"])
            message.assert_not_called()
        else:
            self.assertIn("Teste do modelo", message.call_args.args[1])
