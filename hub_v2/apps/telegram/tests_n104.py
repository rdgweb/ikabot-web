"""N-104: one cinema message a day, the island notice from data, and a text per account."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.telegram.models import DigestEntry, MessageAudit, NotificationTemplate, TelegramAccountConfig, TelegramBotConfig
from apps.telegram.services import digests
from apps.telegram.services.formatters import format_message

SENT_OK = {"ok": True, "result": {"message_id": 77}}


class Base(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-tg")
        self.account = Account.objects.create(node=self.node, label="Lobby TG", email="tg@example.com", password_enc="x")
        self.alpha = self._ga(1, "Alpha")
        self.beta = self._ga(2, "Beta")
        self.gamma = self._ga(3, "Gamma")
        self.bot = TelegramBotConfig.objects.create(pk=1, bot_token="123:abc", enabled=True, chat_id="1000", notify_cinema_available=True)
        NotificationTemplate.seed_defaults()

    def _ga(self, number, name):
        return GameAccount.objects.create(
            account=self.account, lobby_account_id=number, server_id="s78-br", server_language="br", server_number=78, name=name,
        )


class CinemaDigestTests(Base):
    def _flush(self, now=None):
        with patch("apps.telegram.services.bot_api.send_message", return_value=SENT_OK) as send:
            sent = digests.flush_due(now=now)
        return sent, send

    def test_one_message_a_day_lists_every_account_with_a_session(self):
        # five hours to the reset: due in four
        digests.record_cinema(self.alpha, available=True, reset_in_seconds=5 * 3600, city_name="Atenas")
        digests.record_cinema(self.beta, available=True, reset_in_seconds=5 * 3600, city_name="Tebas")
        digests.record_cinema(self.gamma, available=False, reset_in_seconds=5 * 3600, city_name="Argos")

        sent, send = self._flush()
        self.assertEqual((sent, send.call_count), (0, 0))                       # not yet: it goes an hour before the day turns

        sent, send = self._flush(now=timezone.now() + timedelta(hours=4, minutes=1))
        self.assertEqual((sent, send.call_count), (1, 1))                       # ONE message, not one per account
        chat_id, text = send.call_args.args[:2]
        self.assertEqual(chat_id, "1000")
        self.assertIn("Cineteatro disponível em 2 conta(s)", text)
        self.assertIn("• Alpha — Atenas", text)
        self.assertIn("• Beta — Tebas", text)
        self.assertNotIn("Gamma", text)                                         # no session there
        self.assertIn("O dia do jogo vira às", text)
        audit = MessageAudit.objects.get()
        self.assertEqual((audit.channel, audit.status, audit.external_ref), ("cinema_available", "sent", "77"))

        sent, send = self._flush(now=timezone.now() + timedelta(hours=4, minutes=30))
        self.assertEqual((sent, send.call_count), (0, 0))                       # the same day is not sent again

    def test_an_account_that_used_its_session_leaves_the_list(self):
        digests.record_cinema(self.alpha, available=True, reset_in_seconds=5 * 3600, city_name="Atenas")
        digests.record_cinema(self.beta, available=True, reset_in_seconds=5 * 3600, city_name="Tebas")
        digests.record_cinema(self.beta, available=False, reset_in_seconds=4 * 3600, city_name="Tebas")     # watched it later
        self.assertEqual(DigestEntry.objects.count(), 2)

        _sent, send = self._flush(now=timezone.now() + timedelta(hours=4, minutes=1))
        self.assertIn("em 1 conta(s)", send.call_args.args[1])
        self.assertNotIn("Beta", send.call_args.args[1])

    def test_nobody_with_a_session_sends_nothing(self):
        digests.record_cinema(self.alpha, available=False, reset_in_seconds=1800)
        sent, send = self._flush()
        self.assertEqual((sent, send.call_count), (0, 0))
        self.assertFalse(MessageAudit.objects.exists())
        self.assertIsNotNone(DigestEntry.objects.get().sent_at)                 # the day is closed all the same

    def test_a_report_that_arrives_after_the_digest_is_not_sent_on_its_own(self):
        digests.record_cinema(self.alpha, available=True, reset_in_seconds=1800)        # less than an hour left: due now
        self.assertEqual(self._flush()[0], 1)
        digests.record_cinema(self.beta, available=True, reset_in_seconds=1700)
        sent, send = self._flush()
        self.assertEqual((sent, send.call_count), (0, 0))
        self.assertEqual(MessageAudit.objects.count(), 1)

    def test_the_next_game_day_gets_its_own_message(self):
        digests.record_cinema(self.alpha, available=True, reset_in_seconds=1800)
        self.assertEqual(self._flush()[0], 1)
        tomorrow = timezone.now() + timedelta(days=1)
        with patch("apps.telegram.services.digests.timezone.now", return_value=tomorrow):
            digests.record_cinema(self.alpha, available=True, reset_in_seconds=1800)
        self.assertEqual(self._flush(now=tomorrow)[0], 1)
        self.assertEqual(MessageAudit.objects.count(), 2)

    def test_the_switches_still_decide_who_is_listed(self):
        TelegramAccountConfig.objects.create(game_account=self.beta, enabled=True, notify_cinema_available=True)
        self.bot.notify_cinema_available = False                                  # off in the global list
        self.bot.save()
        digests.record_cinema(self.alpha, available=True, reset_in_seconds=1800)
        digests.record_cinema(self.beta, available=True, reset_in_seconds=1800)

        _sent, send = self._flush()
        self.assertEqual(send.call_count, 1)
        self.assertIn("• Beta", send.call_args.args[1])                          # on for this account
        self.assertNotIn("Alpha", send.call_args.args[1])

    def test_an_agent_that_does_not_send_the_reset_time_still_gets_one_message(self):
        digests.record_cinema(self.alpha)                                         # the old payload: available, nothing else
        entry = DigestEntry.objects.get()
        self.assertIsNone(entry.due_at)
        year, month, day = (int(part) for part in entry.day_key.split("-"))
        late = timezone.now().replace(year=year, month=month, day=day, hour=23, minute=30)
        self.assertEqual(self._flush(now=late.replace(hour=22, minute=0))[0], 0)
        self.assertEqual(self._flush(now=late)[0], 1)

    def test_a_bot_that_is_off_sends_nothing(self):
        self.bot.enabled = False
        self.bot.save()
        digests.record_cinema(self.alpha, available=True, reset_in_seconds=1800)
        self.assertEqual(self._flush()[0], 0)


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class AgentNotifyTests(Base):
    def _post(self, payload):
        return self.client.post(
            "/api/agent/notify/", data=json.dumps(payload), content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )

    def test_a_cinema_report_is_kept_for_the_digest_instead_of_being_sent(self):
        with patch("apps.telegram.services.notifications.send_message", return_value=SENT_OK) as send:
            response = self._post({
                "event": "cinema_available", "game_account_id": str(self.alpha.pk), "title": "Cineteatro disponivel",
                "metadata": {"available": True, "reset_in_seconds": 7200, "city_name": "Atenas"},
            })
        self.assertEqual(response.json(), {"ok": True, "sent": False, "queued": True})
        send.assert_not_called()
        entry = DigestEntry.objects.get()
        self.assertEqual((entry.game_account_id, entry.active, json.loads(entry.data_json)), (self.alpha.pk, True, {"city_name": "Atenas"}))
        self.assertAlmostEqual((entry.due_at - timezone.now()).total_seconds(), 3600, delta=30)

    def test_the_island_notice_is_written_here_from_what_changed(self):
        self.bot.notify_island_monitor = True
        self.bot.save()
        with patch("apps.telegram.services.notifications.send_message", return_value=SENT_OK) as send:
            self._post({
                "event": "island_monitor", "game_account_id": str(self.alpha.pk), "title": "Mudança na ilha [50:50]",
                "body": "🏝 Ilha [50:50] — Colosso · Vinho · Colosso\n➕ Fulano colonizou Nova",
                "metadata": {
                    "island": "Ilha [50:50] — Colosso", "coords": "[50:50]", "resource": "Vinho", "miracle": "Colosso",
                    "changes": [
                        {"kind": "new", "city": "Nova", "owner": "Fulano", "ally": "ABC"},
                        {"kind": "owner_change", "city": "Velha", "owner": "Beltrano", "prev_owner": "Ciclano", "ally": ""},
                        {"kind": "fight_start", "city": "Velha", "owner": "Beltrano"},
                        {"kind": "bogus", "city": "x"},
                    ],
                },
            })
        text = send.call_args.args[1]
        self.assertIn("<b>Mudança na ilha [50:50]</b>", text)
        self.assertIn("Ilha [50:50] — Colosso · Vinho · Colosso", text)
        self.assertIn("Visto por Alpha", text)
        self.assertIn("➕ Fulano [ABC] colonizou Nova", text)
        self.assertIn("🔄 Velha: Ciclano → Beltrano", text)
        self.assertIn("⚔️ Batalha em Velha (de Beltrano)", text)
        self.assertNotIn("*", text)
        self.assertEqual(MessageAudit.objects.get().channel, "island_monitor")

    def test_an_agent_that_still_sends_the_ready_text_loses_only_the_stars(self):
        self.bot.notify_island_monitor = True
        self.bot.save()
        with patch("apps.telegram.services.notifications.send_message", return_value=SENT_OK) as send:
            self._post({
                "event": "island_monitor", "game_account_id": str(self.alpha.pk), "title": "Mudança na ilha [50:50]",
                "body": "🏝 *Ilha [50:50] — Colosso* · Vinho · Colosso\n➕ *Fulano* colonizou *Nova*",
            })
        text = send.call_args.args[1]
        self.assertIn("➕ Fulano colonizou Nova", text)
        self.assertNotIn("*", text)


class TemplateScopeTests(Base):
    CONTEXT = {"sender": "Fulano", "subject": "Oi", "message_body": "texto", "ga_name": "Alpha"}

    def test_an_account_can_have_its_own_text(self):
        NotificationTemplate.objects.create(
            event_key="diplomacy_message", game_account=self.alpha, icon="📬",
            title_template="Carta para {ga_name}", body_template="{sender}: {message_body}",
        )
        own = format_message("diplomacy_message", game_account=self.alpha, **self.CONTEXT)
        other = format_message("diplomacy_message", game_account=self.beta, **{**self.CONTEXT, "ga_name": "Beta"})
        self.assertEqual(own, "📬 <b>Carta para Alpha</b>\nFulano: texto")
        self.assertIn("<b>Diplomacia - Beta</b>", other)                         # the global text
        self.assertIn("De: Fulano", other)
        self.assertIn("Diplomacia - Alpha", format_message("diplomacy_message", **self.CONTEXT))    # no account: global

    def test_one_text_per_event_and_scope(self):
        NotificationTemplate.objects.create(event_key="diplomacy_message", game_account=self.alpha, title_template="a")
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotificationTemplate.objects.create(event_key="diplomacy_message", game_account=self.alpha, title_template="b")
        with self.assertRaises(IntegrityError), transaction.atomic():
            NotificationTemplate.objects.create(event_key="diplomacy_message", title_template="outro global")
        NotificationTemplate.objects.create(event_key="diplomacy_message", game_account=self.beta, title_template="c")

    def test_seeding_is_safe_to_repeat_and_knows_the_new_notices(self):
        NotificationTemplate.seed_defaults()
        NotificationTemplate.seed_defaults()
        globals_ = NotificationTemplate.objects.filter(game_account__isnull=True)
        self.assertEqual(globals_.count(), len(NotificationTemplate.DEFAULTS))
        self.assertTrue(globals_.filter(event_key="cinema_available").exists())
        self.assertEqual(globals_.get(event_key="island_monitor").title_template, "Mudança na ilha {coords}")

    def test_a_typo_in_a_variable_is_pointed_out(self):
        template = NotificationTemplate.for_event("island_monitor")
        self.assertEqual(template.unknown_variables(), [])
        template.body_template = "{island} {cordenadas} {changes} {ga_name}"
        self.assertEqual(template.unknown_variables(), ["cordenadas"])
        names = [name for name, _label in NotificationTemplate.variables_for("island_monitor")]
        self.assertIn("{changes}", names)
        self.assertIn("{ga_name}", names)

    def test_restoring_brings_the_default_back(self):
        template = NotificationTemplate.for_event("cinema_available")
        template.title_template = "outra coisa"
        template.save()
        self.assertTrue(template.reset_to_default())
        self.assertEqual(NotificationTemplate.for_event("cinema_available").title_template, "Cineteatro disponível em {count} conta(s)")


class TelegramPagesTests(Base):
    """The second half of N-104: the menu, the editor of each text and the history."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_user(username="tg-user", email="tg-user@example.com", password="secret123", is_staff=True))
        self.island = NotificationTemplate.for_event("island_monitor")
        self.job_failed = NotificationTemplate.for_event("job_failed")

    def _url(self, name, pk):
        from django.urls import reverse

        return reverse(f"telegram:{name}", args=[pk])

    def test_the_page_has_the_four_sections_and_groups_the_texts(self):
        MessageAudit.objects.create(channel="cinema_available", status="sent", title="x")
        response = self.client.get("/telegram/")
        self.assertEqual(response.status_code, 200)
        for label in ("Avisos", "Mensagens", "Conexão", "Histórico"):
            self.assertContains(response, label)
        groups = {group["title"]: [item["template"].event_key for item in group["items"]] for group in response.context["template_groups"]}
        self.assertLessEqual({"cinema_available", "diplomacy_message", "island_monitor", "job_failed"}, set(groups["Avisos"]))
        self.assertIn("low_wine", groups["Ainda sem envio"])                              # N-109: nothing sends it
        self.assertEqual(groups["Avisos"][0], "cinema_available")                         # the most sent comes first
        self.assertContains(response, "1 em 14 dias")

    def test_the_editor_lists_the_variables_of_that_notice_and_previews_the_last_real_one(self):
        page = self.client.get(self._url("template-edit", self.island.pk)).content.decode()
        self.assertIn("{changes}", page)
        self.assertNotIn("{exit_code}", page)                                             # variables of jobs do not belong here
        self.assertIn("com dados de exemplo", page)
        self.assertIn("Fulano [ABC] colonizou Nova Polis", page)

        MessageAudit.objects.create(
            channel="island_monitor", status="sent", title="Mudança na ilha [10:20]", game_account=self.alpha,
            context_json=json.dumps({"ga_name": "Alpha", "title": "Mudança na ilha [10:20]", "island": "Ilha [10:20]", "coords": "[10:20]",
                                     "changes": "➖ Velha sumiu (era de Fulano)", "changes_count": 1}),
        )
        page = self.client.get(self._url("template-edit", self.island.pk)).content.decode()
        self.assertIn("com o último aviso real", page)
        self.assertIn("Velha sumiu (era de Fulano)", page)
        self.assertIn("Mudança na ilha [10:20]", page)

        # a notice from before the text had these variables is not a preview: it would show holes
        cinema = NotificationTemplate.for_event("cinema_available")
        MessageAudit.objects.create(channel="cinema_available", status="sent", title="Cineteatro", game_account=self.alpha,
                                    context_json=json.dumps({"ga_name": "Alpha", "title": "Cineteatro", "body": "sessão disponível"}))
        page = self.client.get(self._url("template-edit", cinema.pk)).content.decode()
        self.assertIn("com dados de exemplo", page)
        self.assertIn("O dia do jogo vira às 00:00", page)

        failed = self.client.get(self._url("template-edit", self.job_failed.pk)).content.decode()
        self.assertIn("{exit_code}", failed)
        self.assertNotIn("{sender}", failed)                                              # N-109: its own variables only

    def test_saving_a_text_with_a_variable_that_does_not_exist_says_so(self):
        response = self.client.post(self._url("template-edit", self.island.pk), {
            "icon": "🏝️", "title_template": "Ilha {coords}", "body_template": "{cordenadas}\n{changes}",
        })
        page = response.content.decode()
        self.island.refresh_from_db()
        self.assertEqual(self.island.title_template, "Ilha {coords}")
        self.assertIn("não preenche", page)
        self.assertIn("{cordenadas}", page)
        self.assertEqual(response["HX-Trigger"], "showToast")

        page = self.client.post(self._url("template-reset", self.island.pk)).content.decode()
        self.island.refresh_from_db()
        self.assertEqual(self.island.title_template, "Mudança na ilha {coords}")
        self.assertIn("Texto padrão restaurado.", page)
        self.assertNotIn("não preenche", page)

    def test_the_test_button_sends_the_saved_text_to_the_global_chat(self):
        with patch("apps.telegram.services.notifications.send_message", return_value=SENT_OK) as send:
            page = self.client.post(self._url("template-test", self.island.pk)).content.decode()
        self.assertIn("Teste enviado para o chat global.", page)
        chat_id, text = send.call_args.args[:2]
        self.assertEqual(chat_id, "1000")
        self.assertIn("Teste do modelo", text)
        self.assertIn("Mudança na ilha [83:90]", text)

        self.bot.chat_id = ""
        self.bot.save()
        with patch("apps.telegram.services.notifications.send_message", return_value=SENT_OK) as send:
            page = self.client.post(self._url("template-test", self.island.pk)).content.decode()
        send.assert_not_called()
        self.assertIn("Não há bot ativo com chat global", page)

    def test_an_account_gets_a_text_of_its_own_and_can_go_back_to_the_global(self):
        page = self.client.post(self._url("template-account-create", self.island.pk), {"game_account": str(self.alpha.pk)}).content.decode()
        own = NotificationTemplate.objects.get(event_key="island_monitor", game_account=self.alpha)
        self.assertEqual((own.title_template, own.body_template), (self.island.title_template, self.island.body_template))
        self.assertIn("Alpha agora tem um texto próprio", page)
        self.assertIn(self._url("template-edit", own.pk), page)
        self.assertNotIn(f'<option value="{self.alpha.pk}"', page)                        # no second text for the same account

        self.client.post(self._url("template-edit", own.pk), {"icon": "🏝️", "title_template": "Só da Alpha {coords}", "body_template": "{changes}"})
        self.assertIn("Só da Alpha [1:2]", format_message("island_monitor", game_account=self.alpha, coords="[1:2]", changes="x"))
        self.assertIn("Mudança na ilha [1:2]", format_message("island_monitor", game_account=self.beta, coords="[1:2]", changes="x"))

        own_editor = self.client.get(self._url("template-edit", own.pk)).content.decode()
        self.assertIn("texto só desta conta", own_editor)
        self.assertIn(self._url("template-row", self.island.pk), own_editor)             # closing goes back to the row of the list

        page = self.client.post(self._url("template-account-delete", own.pk)).content.decode()
        self.assertFalse(NotificationTemplate.objects.filter(pk=own.pk).exists())
        self.assertIn("Alpha voltou a usar o texto global.", page)
        self.assertEqual(self.client.post(self._url("template-account-create", self.island.pk), {"game_account": "lixo"}).status_code, 200)
        self.assertEqual(NotificationTemplate.objects.filter(game_account__isnull=False).count(), 0)

    def test_the_history_filters_by_kind_of_notice_and_names_it(self):
        MessageAudit.objects.create(channel="cinema_available", status="sent", title="Cineteatro em 2 contas")
        MessageAudit.objects.create(channel="island_monitor", status="sent", title="Mudança na ilha", game_account=self.alpha)
        everything = self.client.get("/telegram/audit/").content.decode()
        self.assertIn("Cineteatro disponível", everything)
        self.assertIn("Monitor de ilha", everything)
        only = self.client.get("/telegram/audit/", {"channel": "island_monitor"}).content.decode()
        self.assertIn("Mudança na ilha", only)
        self.assertNotIn("Cineteatro em 2 contas", only)


class IslandTextTests(TestCase):
    def test_lines(self):
        text, count = digests.island_changes_text([
            {"kind": "removed", "city": "Velha", "owner": "Fulano"},
            {"kind": "fight_end", "city": "Velha"},
            "lixo",
        ])
        self.assertEqual((text, count), ("➖ Velha sumiu (era de Fulano)\n🏳 Batalha encerrada em Velha", 2))
        self.assertEqual(digests.island_changes_text(None), ("", 0))
