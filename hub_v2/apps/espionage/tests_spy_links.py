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
        from apps.worldintel.models import WorldDumpCity, WorldDumpIsland

        island = WorldDumpIsland.objects.create(dump=dump, island_id="9", name="Ilha", x=88, y=90)
        WorldDumpCity.objects.create(dump=dump, island=island, type="city", owner_id="110034", owner_name="Herakles-2929",
                                     game_city_id="89104")

    def test_links_score_and_alliance(self):
        html = self.client.get("/espionage/").content.decode()
        self.assertIn("owner_id=110034", html)
        self.assertIn("city_id=89104", html)

    def test_a_city_missing_from_the_dump_has_no_link(self):
        SpyReport.objects.create(game_account=self.ga, report_id="r3", target_owner="Herakles-2929", target_owner_id="110034",
                                 target_city_name="Nova", target_city_id="99999", mission_id=5)
        self.assertNotIn("city_id=99999", self.client.get("/espionage/").content.decode())
        self.assertIn("q=BLOOD", html)
        self.assertIn("[BLOOD]", html)
        self.assertIn("1.432.240", html)
        self.assertIn("47.903", html)                                  # the resources, with the thousands dot
        self.assertIn("q=88:90", html)                                 # the island's players
        self.assertIn(f"dump={self.dump.pk}", html)                    # the latest dump

    def test_a_report_without_the_owner_id_is_found_by_name(self):
        SpyReport.objects.create(game_account=self.ga, report_id="r2", target_owner="Herakles-2929",
                                 target_city_name="Polis", target_city_id="89104", mission_id=6)
        html = self.client.get("/espionage/").content.decode()
        self.assertEqual(html.count("[BLOOD]"), 2)              # both rows know the alliance


class IslandPlayersTests(TestCase):
    def test_coordinates_list_the_players_of_the_island(self):
        from apps.worldintel.models import WorldDumpCity, WorldDumpIsland

        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x", is_staff=True))
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="Jogador")
        dump = WorldDump.objects.create(account=account, game_account=ga, captured_at=timezone.now())
        island = WorldDumpIsland.objects.create(dump=dump, island_id="1", name="Turetia", x=87, y=84)
        other = WorldDumpIsland.objects.create(dump=dump, island_id="2", name="Longe", x=10, y=10)
        for owner, isl in (("UmSeteUm", island), ("CorujaVerde", island), ("Distante", other)):
            WorldDumpPlayer.objects.create(dump=dump, owner_id=owner, owner_name=owner)
            WorldDumpCity.objects.create(dump=dump, island=isl, type="city", owner_id=owner, owner_name=owner, game_city_id=owner)
        html = self.client.get("/intel/players/", {"dump": str(dump.pk), "q": "87:84"}).content.decode()
        self.assertIn("Turetia", html)
        self.assertIn("UmSeteUm", html)
        self.assertIn("CorujaVerde", html)
        self.assertNotIn("Distante", html)
