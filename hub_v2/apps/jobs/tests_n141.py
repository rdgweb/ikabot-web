"""N-141: Ajustar Sacerdotes (ac=35) and the island miracle panel — what the agent read about each temple and each
island's miracle reaches the forms (also Ativar Milagre, which never showed it)."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.profiles.models import Preset, PresetAction, PresetGameAccount
from apps.profiles.services import execute_preset
from core.contracts import ACTION_CATALOG

from .models import Job


def _temple_city(cid, name, island, priests):
    return {"id": cid, "name": name, "tradegood": 1, "island_id": island, "x": 10, "y": cid,
            "buildings": [{"building": "temple", "position": 16, "level": 20}],
            "miracle_state": {"priests": priests, "max_priests": 900, "own_conversion_percent": 50.0,
                              "island_conversion_percent": 30.0, "citizens_per_priest": 7, "max_inhabitants": 3500,
                              "wonder_name": "Templo de Hermes (Nível 2/5)", "activate_state": "enabled", "island_name": "Haunios",
                              "cooldown_text": "", "updated_at": "2026-10-16T10:00:00+00:00"}}


class TempleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(self.user)
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Executor")
        AccountSnapshot.objects.create(
            account=account, game_account=self.ga, military={},
            base_snapshot={"island_wonders": {"4478": {
                "level": 5, "max_level_reached": True, "faith_percent": 56.0, "belief_level": 2, "upgrading": False,
                "participants": [{"player": "Vizinho", "city_name": "Longe", "city_id": 0, "priests": 10,
                                  "conversion_percent": 5.0, "island_share_percent": 2.0},
                                 {"player": "Eu", "city_name": "Valhala", "city_id": 1, "priests": 625,
                                  "conversion_percent": 116.8, "island_share_percent": 11.9}]}}},
            cities=[_temple_city(1, "Valhala", 4478, 625), _temple_city(2, "Hell", 4478, 100),
                    {"id": 3, "name": "Sem templo", "tradegood": 2, "island_id": 4479, "buildings": []}])

    def test_the_action_is_in_the_catalog(self):
        self.assertEqual(ACTION_CATALOG[35]["name"], "Ajustar Sacerdotes")

    def test_the_priests_form_shows_temples_and_the_island_miracle(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.ga.pk), "action": "35"}).content.decode()
        self.assertIn("data-island-miracles", html)
        self.assertIn("Templo de Hermes", html)
        self.assertIn("fé <b class=\"text-ink\">56%</b>", html)
        self.assertIn("Vizinho · Longe", html)
        self.assertIn("força <b class=\"text-ink\">2/5</b>", html)            # strength apart from the name
        self.assertNotIn("Templo de Hermes (Nível", html.split("data-island-miracles")[1])
        self.assertIn('"citizens_per_priest": 7', html)

    def test_activate_miracle_finally_shows_what_was_read(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.ga.pk), "action": "11"}).content.decode()
        self.assertIn("Templo de Hermes", html)
        self.assertIn("data-island-miracles", html)

    def test_one_job_reads_every_temple(self):
        self.client.post("/jobs/new/submit/", {"game_account": str(self.ga.pk), "action_code": "35", "cities": ["1", "2"],
                                                "target_mode": "own_conversion", "target_value": "100", "reserve_citizens": "0"})
        jobs = list(Job.objects.filter(action_code=35))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0].inputs_json)["cities"], ["1", "2"])

    def test_the_preset_has_no_city_list_and_makes_one_job_per_account(self):
        preset = Preset.objects.create(name="Templos", created_by=self.user)
        PresetGameAccount.objects.create(preset=preset, game_account=self.ga)
        PresetAction.objects.create(preset=preset, action_code=35, action_name="Ajustar Sacerdotes", order=1,
                                    inputs_json="{}", per_account_json="{}")
        url = f"/profiles/presets/{preset.pk}/configure/1/"
        self.assertNotIn('name="city_mode"', self.client.get(url).content.decode())
        self.client.post(url, {"game_account": str(self.ga.pk), "action_code": "35", "target_mode": "absolute",
                               "target_value": "300", "reserve_citizens": "0", "cities": ["1"]})
        saved = json.loads(PresetAction.objects.get(preset=preset).inputs_json)
        self.assertNotIn("cities", saved)
        self.assertEqual((saved["target_mode"], str(saved["target_value"])), ("absolute", "300"))
        execute_preset(preset, created_by=self.user)
        self.assertEqual(Job.objects.filter(action_code=35).count(), 1)
