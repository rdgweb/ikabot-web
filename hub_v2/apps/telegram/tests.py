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

    def test_island_monitor_message_uses_template(self):
        text = format_message(
            "island_monitor",
            title="Mudança na ilha [83:90]",
            ga_name="BlackShadow701",
            server_id="s78-br",
            body="➖ *M01* sumiu",
        )
        self.assertIn("Mudança na ilha [83:90]", text)
        self.assertIn("BlackShadow701", text)
        self.assertIn("M01", text)


class DefenseJobUnitNamesTests(TestCase):
    """N-98: the snapshot writes unit names without accents; the catalog shows them with."""

    def test_units_are_found_whether_or_not_the_name_has_accents(self):
        from unittest.mock import patch

        from apps.accounts.models import Account, GameAccount, Node
        from apps.game.models import AccountSnapshot
        from apps.telegram.api.webhook import _create_defense_job

        node = Node.objects.create(name="node-tg", active=True)
        account = Account.objects.create(node=node, label="Lobby TG", email="tg@example.com", password_enc="x")
        ga = GameAccount.objects.create(
            account=account, lobby_account_id=98, server_id="s9-br", server_language="br", server_number=9, name="Conta TG",
        )
        AccountSnapshot.objects.create(account=account, game_account=ga, military={"by_city": [
            {"city_id": "1", "troops": {"Hoplita": 1}},
            {"city_id": "2", "troops": {"Ariete": 2, "Aríete": 3, "Medico": 4, "Hoplita": 5}},
        ]})
        with patch("apps.jobs.services.workflows.create_job_with_workflow") as create:
            ok, _message = _create_defense_job("1", str(ga.pk), "troops")
        self.assertTrue(ok)
        units = create.call_args.kwargs["inputs"]["units"]
        self.assertEqual(units.get("307"), 3)      # "Ariete" and "Aríete" are the same unit (the last one read wins)
        self.assertEqual(units.get("313"), 4)      # the id the unit catalog gives "Médico"
        self.assertEqual(units.get("303"), 5)
