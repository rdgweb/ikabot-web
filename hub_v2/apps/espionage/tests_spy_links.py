"""The spy report list links the target player, city and alliance to Inteligência and shows the score."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.espionage.models import SpyReport
from apps.worldintel.models import WorldDump, WorldDumpPlayer


class SpyReportLinksTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x", is_staff=True))
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                             server_number=78, name="Jogador")
        dump = WorldDump.objects.create(account=account, game_account=self.ga, captured_at=timezone.now())
        WorldDumpPlayer.objects.create(dump=dump, owner_id="110034", owner_name="Herakles-2929", ally_tag="BLOOD",
                                       building_score=1_000_000, research_score=400_000, army_score=32_240)
        SpyReport.objects.create(game_account=self.ga, report_id="r1", target_owner="Herakles-2929", target_owner_id="110034",
                                 target_city_name="Polis", target_city_id="89104", mission_id=5, target_x=88, target_y=90,
                                 data_json={"wood": 47903, "marble": 5937})
        self.dump = dump

    def test_links_score_and_alliance(self):
        html = self.client.get("/espionage/").content.decode()
        self.assertIn("owner_id=110034", html)
        self.assertIn("city_id=89104", html)
        self.assertIn("q=BLOOD", html)
        self.assertIn("[BLOOD]", html)
        self.assertIn("1.432.240", html)
        self.assertIn("47.903", html)                                  # the resources, with the thousands dot
        self.assertIn("q=88:90", html)                                 # the island
        self.assertIn(f"dump={self.dump.pk}", html)                    # opened on the dump that has the player
