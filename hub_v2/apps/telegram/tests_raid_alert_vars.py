"""N-104: the rich target notice has a template with variables (it used to arrive as one ready-made text)."""
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.espionage.api.views import _raid_alert_variables
from apps.espionage.models import SpyReport
from apps.telegram.constants import EVENT_TYPES
from apps.telegram.models import NotificationTemplate
from apps.telegram.services.formatters import format_message


class RaidAlertTemplateTests(TestCase):
    def setUp(self):
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="NexX711")
        self.report = SpyReport.objects.create(game_account=self.ga, report_id="r1", target_owner="Death Note v",
                                               target_city_name="VIP8", target_city_id="89649", mission_id=5)
        SpyReport.objects.filter(pk=self.report.pk).update(created_at=timezone.now())
        self.report.refresh_from_db()
        NotificationTemplate.seed_defaults()

    def _vars(self):
        top = {"source_city_name": "Juízo Final", "ga_name": "Executor224", "transporters": 142,
               "sim": {"rounds": 5, "attacker_survivors_pct": 94.0}, "recommended": {303: 921},
               "recommended_fleet": {216: 16}, "fleet_can_win": False}
        return _raid_alert_variables(
            report=self.report, target_name="VIP8", target_owner="Death Note v", xy="[68:72]", total=4345284,
            resources={"wood": 2034786, "wine": 227}, troops={"308": 106, "301": 860},
            enemy_intel={"enemy_fleet": {210: 811, 213: 200}, "city_level": 35, "wall_level": 30}, top=top,
            eta_text="~136min", origin_block="🚢 Sugestão: Juízo Final (Executor224)")

    def test_the_variables(self):
        v = self._vars()
        self.assertEqual(v["total"], "4.345.284")
        self.assertIn("860× Fundeiro", v["defense"])
        self.assertIn("811× Trireme", v["port_fleet"])
        self.assertIn("Frota insuficiente", v["fleet_to_send"])
        self.assertIn("Vitória em 5 rounds", v["verdict"])
        self.assertEqual((v["city_level"], v["raid_account"]), (35, "Executor224"))

    def test_the_default_template_uses_them(self):
        text = format_message("raid_alert", game_account=self.ga, **self._vars())
        self.assertIn("Alvo rico: VIP8 [68:72] (Death Note v)", text)
        self.assertIn("cidade nível 35", text)
        self.assertIn("4.345.284", text)
        self.assertIn("106× Gigante a Vapor", text)
        self.assertIn("Frota no porto", text)

    def test_the_notice_is_in_the_telegram_screen_list(self):
        self.assertIn("raid_alert", [e["key"] for e in EVENT_TYPES])
        self.assertIn("{fleet_to_send}", [name for name, _ in NotificationTemplate.variables_for("raid_alert")])
