"""N-206: common presets of the platform — every customer organization gets a copy, without the platform's accounts or
per-account settings, even with no account of its own; a copy it deletes does not come back."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.orgs.models import Membership, Organization
from apps.orgs.services import default_organization

from .models import Preset, PresetAction, PresetGameAccount


def _user(username, **extra):
    user = get_user_model()(username=username, **extra)
    user._own_organization = True
    user.set_password("x")
    user.save()
    return user


class CommonPresetTests(TestCase):
    def setUp(self):
        platform = default_organization()
        self.admin = _user("rodrigo", is_staff=True, is_superuser=True)
        node = Node.objects.create(name="no-plataforma", organization=platform)
        account = Account.objects.create(node=node, label="Conta da plataforma", email="p@example.com", password_enc="x",
                                         organization=platform)
        self.world = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s1-br", name="JogadorDaPlataforma",
                                                server_language="br", server_number=1)
        self.preset = Preset._base_manager.create(name="Pesquisa", description="pesquisa sozinha", organization=platform,
                                                  catalog_category="research")
        PresetAction._base_manager.create(preset=self.preset, action_code=18, action_name="Pesquisar", order=0,
                                          inputs_json=json.dumps({"branch_economy": 1}),
                                          per_account_json=json.dumps({str(self.world.pk): {"branch_economy": 9}}))
        PresetGameAccount._base_manager.create(preset=self.preset, game_account=self.world)
        self.tester = _user("testador")
        self.org = Organization.objects.create(name="Teste", slug="teste")
        Membership.objects.create(organization=self.org, user=self.tester, role="owner")

    def test_only_the_administrator_marks_it_and_customers_get_a_clean_copy(self):
        self.client.force_login(self.tester)
        self.client.post(f"/profiles/presets/{self.preset.pk}/common/", {"on": "1"})
        self.preset.refresh_from_db()
        self.assertFalse(self.preset.is_common)
        self.client.force_login(self.admin)
        self.client.post(f"/profiles/presets/{self.preset.pk}/common/", {"on": "1"})
        self.preset.refresh_from_db()
        self.assertTrue(self.preset.is_common)

        self.client.force_login(self.tester)
        page = self.client.get("/profiles/presets/").content.decode()
        self.assertIn("Pesquisa", page)
        self.assertNotIn("JogadorDaPlataforma", page)
        copy = Preset._base_manager.get(organization=self.org)
        self.assertEqual((copy.name, copy.source_id, copy.catalog_category), ("Pesquisa", self.preset.pk, "research"))
        action = PresetAction._base_manager.get(preset=copy)
        self.assertEqual((json.loads(action.inputs_json), json.loads(action.per_account_json)), ({"branch_economy": 1}, {}))
        self.assertFalse(PresetGameAccount._base_manager.filter(preset=copy).exists())     # none of the platform's accounts
        detail = self.client.get(f"/profiles/presets/{copy.pk}/").content.decode()
        self.assertNotIn("JogadorDaPlataforma", detail)
        self.assertEqual(self.client.get(f"/profiles/presets/{self.preset.pk}/").status_code, 404)   # the original stays the platform's

        # deleted, it does not come back; the platform's own organization never gets copies
        Preset._base_manager.filter(pk=copy.pk).delete()
        self.client.get("/profiles/presets/")
        self.assertFalse(Preset._base_manager.filter(organization=self.org).exists())
        self.assertEqual(Preset._base_manager.filter(organization=default_organization()).count(), 1)
