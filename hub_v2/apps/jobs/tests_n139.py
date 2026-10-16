"""N-139: Alterar Produção takes a luxury percentage per good (each city uses the one of the good it produces) and
its preset has no city list: every city of each account."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.profiles.models import Preset, PresetAction, PresetGameAccount
from apps.profiles.services import execute_preset

from .models import Job


class ModifyProductionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(self.user)
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Executor")
        AccountSnapshot.objects.create(account=account, game_account=self.ga, base_snapshot={}, military={}, cities=[
            {"id": 1, "name": "Vinhedo", "tradegood": 1, "buildings": []},
            {"id": 2, "name": "Cristalina", "tradegood": 3, "buildings": []},
            {"id": 3, "name": "Enxofreira", "tradegood": 4, "buildings": []},
        ])

    def _inputs(self):
        return [json.loads(job.inputs_json) for job in Job.objects.filter(action_code=23).order_by("created_at")]

    def test_each_good_gets_its_percentage_and_nothing_chosen_means_every_city(self):
        self.client.post("/jobs/new/submit/", {
            "game_account": str(self.ga.pk), "action_code": "23", "sawmill_percent": "100", "luxury_percent": "100",
            "luxury_percent_type_3": "150", "luxury_percent_type_4": "35", "luxury_percent_type_1": ""})
        inputs = self._inputs()
        self.assertEqual(sorted(str(i.get("city_id")) for i in inputs), ["1", "2", "3"])
        self.assertTrue(all(i["luxury_percent_by_type"] == {"3": 150, "4": 35} for i in inputs))
        self.assertTrue(all(str(i["luxury_percent"]) == "100" for i in inputs))

    def test_the_preset_keeps_the_percentages_and_shows_no_city(self):
        preset = Preset.objects.create(name="Produção", created_by=self.user)
        PresetGameAccount.objects.create(preset=preset, game_account=self.ga)
        PresetAction.objects.create(preset=preset, action_code=23, action_name="Alterar Produção", order=1,
                                    inputs_json="{}", per_account_json="{}")
        url = f"/profiles/presets/{preset.pk}/configure/1/"
        html = self.client.get(url).content.decode()
        self.assertNotIn('name="city_mode"', html)
        self.assertNotIn('name="cities"', html)
        self.client.post(url, {"game_account": str(self.ga.pk), "action_code": "23", "sawmill_percent": "120",
                               "luxury_percent": "100", "luxury_percent_type_3": "150", "luxury_percent_type_4": "35"})
        saved = json.loads(PresetAction.objects.get(preset=preset).inputs_json)
        self.assertEqual(saved["luxury_percent_by_type"], {"3": 150, "4": 35})
        self.assertNotIn("cities", saved)
        self.assertIn('value="150"', self.client.get(url).content.decode())          # comes back filled in
        execute_preset(preset, created_by=self.user)
        inputs = self._inputs()
        self.assertEqual(sorted(str(i.get("city_id")) for i in inputs), ["1", "2", "3"])
        self.assertTrue(all(i["luxury_percent_by_type"] == {"3": 150, "4": 35} for i in inputs))
