"""N-149: a pirate raid is told apart from an attack (panel, dashboard summary, miracles); N-148: the shipment
rule needs a minimum of ships."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.services.attacks import event_kind, summary
from apps.jobs.models import Job

RAID = {"event_id": "5408750", "mission": "Assalto (em curso)", "origin_player": "Vasco da Gama", "target_city": "Alfheim",
        "target_city_id": "11", "troops": 0, "fleet": 0}
PLUNDER = {"event_id": "77", "mission": "Roubar (em curso)", "mission_class": "plunder", "target_city": "Alfheim",
           "target_city_id": "11", "troops": 300, "fleet": 0}


class KindTests(SimpleTestCase):
    def test_raid_and_attack(self):
        self.assertEqual(event_kind(RAID), "pirate")
        self.assertEqual(event_kind({**PLUNDER, "mission": "Roubar"}), "attack")
        self.assertEqual(event_kind({"mission": "x", "mission_class": "piracyRaid"}), "pirate")
        self.assertEqual(event_kind({"mission": "Ocupar cidade", "kind": "attack"}), "attack")

    def test_summary(self):
        self.assertEqual(summary({"hostile_count": 1, "events": [RAID]}), {"attacks": 0, "pirates": 1})
        self.assertEqual(summary({"hostile_count": 2, "events": [RAID, PLUNDER]}), {"attacks": 1, "pirates": 1})
        self.assertEqual(summary({"hostile_count": 3, "pirate_count": 1, "events": []}), {"attacks": 2, "pirates": 1})
        self.assertEqual(summary(None), {"attacks": 0, "pirates": 0})


class PirateRaidTests(TestCase):
    def setUp(self):
        cache.clear()                      # the panel keeps its context in the cache
        self.user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(self.user)
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                             server_number=78, name="Jogador")
        city = {"id": 11, "name": "Alfheim", "island_id": 1, "tradegood": 3,
                "buildings": [{"building": "temple", "position": 16, "level": 25}],
                "miracle_state": {"wonder_name": "Templo de Atena (Nível 2/5)", "activate_state": "enabled"}}
        AccountSnapshot.objects.create(account=account, game_account=self.ga, military={}, cities=[city],
                                       base_snapshot={"attack_alert_state": {"hostile_count": 1, "events": [RAID]}})

    def test_no_miracle_for_a_pirate_raid(self):
        from apps.game.services.miracle_rules import on_attack_alert

        self.assertEqual(on_attack_alert(self.ga, RAID), [])
        self.assertEqual(on_attack_alert(self.ga, PLUNDER), ["Templo de Atena"])           # a real attack still asks it
        self.assertEqual(Job.objects.filter(action_code=11).count(), 1)

    def test_the_panel_says_pirate_raid_not_under_attack(self):
        html = self.client.get("/game/").content.decode()
        self.assertIn("ASSALTO PIRATA", html)
        self.assertNotIn("SOB ATAQUE", html)

    def test_the_shipment_rule_keeps_a_minimum_of_ships(self):
        from apps.game.services.miracle_rules import agent_rules, rules_for, save_rules
        from apps.orgs.services import default_organization

        org = default_organization()
        self.assertEqual(rules_for(org)["min_ships"], 10)
        save_rules(org, {"speed_on_shipments": "1", "ship_share": "15", "min_ships": "25"})
        self.assertEqual(agent_rules(org)["min_ships"], 25)
