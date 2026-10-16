"""N-142: a preset can show up in Ações do Jogo, in the category chosen for it, and be run from there."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job
from apps.profiles.models import Preset, PresetAction, PresetGameAccount


class PresetInCatalogTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(self.user)
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                             server_number=78, name="Jogador")
        self.preset = Preset.objects.create(name="Ajuste de trabalhadores", created_by=self.user)
        PresetGameAccount.objects.create(preset=self.preset, game_account=self.ga)
        PresetAction.objects.create(preset=self.preset, action_code=35, action_name="Ajustar Sacerdotes", order=1,
                                    inputs_json=json.dumps({"target_mode": "keep"}), per_account_json="{}")

    def test_only_presets_with_a_category_show_up(self):
        self.assertNotIn("Ajuste de trabalhadores", self.client.get("/game/actions/").content.decode())
        self.client.post(f"/profiles/presets/{self.preset.pk}/catalog/", {"catalog_category": "temple"})
        self.preset.refresh_from_db()
        self.assertEqual(self.preset.catalog_category, "temple")
        html = self.client.get("/game/actions/").content.decode()
        self.assertIn("Ajuste de trabalhadores", html)
        self.assertIn("Ajustar Sacerdotes", html)
        self.assertIn("1 preset", html)

    def test_an_unknown_category_is_not_kept(self):
        self.client.post(f"/profiles/presets/{self.preset.pk}/catalog/", {"catalog_category": "nada"})
        self.preset.refresh_from_db()
        self.assertEqual(self.preset.catalog_category, "")

    def test_the_detail_page_offers_the_categories(self):
        html = self.client.get(f"/profiles/presets/{self.preset.pk}/").content.decode()
        self.assertIn('name="catalog_category"', html)
        self.assertIn("Ações do Jogo · Templo", html)

    def test_running_from_the_catalog_comes_back_to_it(self):
        response = self.client.post(f"/profiles/presets/{self.preset.pk}/execute/?next=/game/actions/")
        self.assertEqual(response["Location"], "/game/actions/")
        self.assertEqual(Job.objects.filter(action_code=35).count(), 1)
        response = self.client.post(f"/profiles/presets/{self.preset.pk}/execute/?next=https://example.com/")
        self.assertIn(str(self.preset.pk), response["Location"])          # never to another site
