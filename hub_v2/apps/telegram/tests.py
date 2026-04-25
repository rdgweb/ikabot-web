from django.test import TestCase

from .services.formatters import format_message


class TelegramFormatterTests(TestCase):
    def test_job_failed_message_uses_template(self):
        """job_failed uses the template created in migration 0006."""
        text = format_message(
            "job_failed",
            action_name="Plano de Construcao",
            ga_name="Atenas",
            server_id="s1-br",
            account_name="Conta Principal",
            node_name="node-01",
            exit_code=12,
            error="sem recurso",
            job_id="abc-123",
        )
        self.assertIn("Plano de Construcao falhou", text)
        self.assertIn("Atenas", text)
        self.assertIn("12", text)

    def test_html_in_body_is_escaped(self):
        """Free-text fields must be HTML-escaped before going to Telegram."""
        text = format_message(
            "diplomacy_message",
            sender="Player<script>",
            subject="Tratado & paz",
            game_date="25/04",
            message_body="Aceita? > 500 ouro",
            reply_command="/replyto 1",
            accept_command="/accept 1",
            decline_command="/decline 1",
        )
        self.assertNotIn("<script>", text)
        self.assertIn("Player&lt;script&gt;", text)
        self.assertIn("Tratado &amp; paz", text)
        self.assertIn("&gt; 500 ouro", text)
        # Intentional HTML tags in the template itself must still work
        self.assertIn("<code>", text)
