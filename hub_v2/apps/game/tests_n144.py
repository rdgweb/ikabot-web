"""N-144: the "+" of a city in Construções offers its buildings and the new ones, and the step goes into the
account's construction plan (merged into the active one, or a new plan)."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.views.dashboard import _quick_build_options
from apps.jobs.models import Job

CITY = {
    "id": 11, "name": "Glitnir", "island_id": 1, "x": 1, "y": 1, "tradegood": 3,
    "buildings": [
        {"position": 0, "building": "townHall", "level": 13, "is_upgrading": False},
        {"position": 4, "building": "warehouse", "level": 20, "is_upgrading": True},
        {"position": 7, "building": "empty", "level": 0},
    ],
    "available_build_options": [
        {"building": "pirateFortress", "name": "Fortaleza dos Piratas", "slot_types": ["sea"], "available_positions": [17],
         "preferred_position": 17, "researched": True},
        {"building": "dockyard", "name": "Estaleiro Comercial", "slot_types": ["dockyard"], "available_positions": [24],
         "preferred_position": 24, "researched": False},
    ],
}


class QuickBuildTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(self.user)
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                             server_number=78, name="Jogador")
        AccountSnapshot.objects.create(account=account, game_account=self.ga, military={}, base_snapshot={}, cities=[CITY])

    def test_the_options_are_the_buildings_and_what_can_be_built(self):
        options = _quick_build_options(CITY)
        self.assertEqual(sorted(b["building"] for b in options["upgrades"]), ["townHall", "warehouse"])   # not the empty slot
        self.assertEqual([b["building"] for b in options["new"]], ["pirateFortress"])     # not researched: left out
        self.assertTrue(next(b for b in options["upgrades"] if b["building"] == "warehouse")["upgrading"])

    def test_the_dashboard_has_the_button(self):
        html = self.client.get("/game/").content.decode()
        self.assertIn("quick-build", html)
        self.assertIn("Construção rápida", html)

    def _send(self, step):
        return self.client.post("/jobs/new/submit/", {
            "game_account": str(self.ga.pk), "action_code": "1002", "auto_transport": "on", "freighter_threshold": "30000",
            "queue_strategy": "eta_first", "research_reduction": "14", "construction_plan_json": json.dumps([step])})

    def test_a_step_creates_the_plan_and_the_next_one_is_merged(self):
        self._send({"city_id": "11", "building_id": "warehouse", "building_name": "Armazém", "building_position": "4",
                    "mode": "upgrade", "target_level": 22})
        jobs = Job.objects.filter(action_code=1002)
        self.assertEqual(jobs.count(), 1)
        self.assertTrue(json.loads(jobs.first().inputs_json)["auto_transport"])
        response = self._send({"city_id": "11", "building_id": "pirateFortress", "building_name": "Fortaleza dos Piratas",
                               "mode": "new", "slot_types": ["sea"], "preferred_position": "17", "target_level": 1})
        self.assertIn("mesclada", response["HX-Trigger"])
        plan = json.loads(Job.objects.get(action_code=1002).inputs_json)["construction_plan_json"]
        self.assertEqual(sorted(s["building_id"] for s in plan), ["pirateFortress", "warehouse"])
