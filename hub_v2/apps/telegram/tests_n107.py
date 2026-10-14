"""N-107: without HTTPS the polling service answers Telegram with the same code as the webhook, buttons included."""

from unittest.mock import patch

from django.test import TestCase

from apps.telegram.management.commands.telegram_poll import Command

WEBHOOK = "apps.telegram.api.webhook"


class PollingTests(TestCase):
    def setUp(self):
        # N-106: updates are only handled for a chat linked to an organization
        from apps.orgs.services import default_organization
        from apps.telegram.models import TelegramBotConfig

        TelegramBotConfig.objects.create(pk=1, organization=default_organization(), chat_id="555", link_status="linked")

    def test_a_button_pressed_is_answered_as_the_webhook_answers_it(self):
        update = {"update_id": 10, "callback_query": {
            "id": "cb1", "data": "defend_skip:abc", "from": {"id": 7},
            "message": {"message_id": 99, "text": "Ataque chegando", "chat": {"id": 555}},
        }}
        with patch(f"{WEBHOOK}.answer_callback_query") as answer, patch(f"{WEBHOOK}.edit_message_text") as edit:
            Command()._process_update(update)
        answer.assert_called_once_with("cb1", "Ignorado.")
        edit.assert_called_once()
        self.assertEqual(edit.call_args.args[:2], ("555", 99))
        self.assertIn("Ataque ignorado", edit.call_args.args[2])

    def test_a_message_goes_through_the_same_commands(self):
        update = {"update_id": 11, "message": {"text": "/captcha 4242 ABCD", "chat": {"id": 555}, "from": {"id": 7, "username": "rodrigo"}}}
        with patch(f"{WEBHOOK}.send_message") as send:
            Command()._process_update(update)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "555")
        self.assertIn("Captcha não encontrado", send.call_args.args[1])          # the /captcha command of the webhook, not the old polling

    def test_when_the_shared_handler_breaks_the_old_one_still_answers(self):
        update = {"update_id": 12, "message": {"text": "/start 123456", "chat": {"id": 555}, "from": {"id": 7, "username": "rodrigo"}}}
        with patch(f"{WEBHOOK}.TelegramWebhookView.handle_update", side_effect=RuntimeError("boom")), \
                patch("apps.telegram.management.commands.telegram_poll.send_message") as send:
            Command()._process_update(update)
        send.assert_called_once()
        self.assertIn("invalido", send.call_args.args[1].lower())

    def test_the_webhook_still_checks_its_secret_before_handling(self):
        from apps.telegram.models import TelegramBotConfig

        TelegramBotConfig.objects.filter(pk=1).update(bot_token="123:abc", enabled=True, webhook_secret="s3cret")
        update = {"update_id": 13, "callback_query": {"id": "cb2", "data": "defend_skip:x", "from": {"id": 7}, "message": {"message_id": 1, "text": "x", "chat": {"id": 555}}}}
        with patch(f"{WEBHOOK}.answer_callback_query") as answer:
            wrong = self.client.post("/api/telegram/webhook/errado/", update, content_type="application/json")
            right = self.client.post("/api/telegram/webhook/s3cret/", update, content_type="application/json")
        self.assertEqual((wrong.status_code, right.status_code), (200, 200))
        answer.assert_called_once_with("cb2", "Ignorado.")
