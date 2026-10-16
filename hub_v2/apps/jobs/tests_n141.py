"""N-141: Ajustar Sacerdotes (ac=35) and Templos e Fé — the temples and island miracles of every account in one
page, and the priests balanced across accounts so each island reaches 100% faith without priests to spare."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.services.temples import islands_for_organization, plan_faith
from apps.profiles.models import Preset, PresetAction, PresetGameAccount
from apps.profiles.services import execute_preset
from core.contracts import ACTION_CATALOG

from .models import Job

CAPACITY = 36330      # island capacity: 7 inhabitants per priest -> 0.019268% of the island per priest


def _temple_city(cid, name, island, priests, max_priests, free):
    share = round(priests * 7 * 100 / CAPACITY, 2)
    return {"id": cid, "name": name, "tradegood": 3, "island_id": island, "x": 83, "y": 88,
            "buildings": [{"building": "temple", "position": 16, "level": 25}],
            "miracle_state": {"priests": priests, "max_priests": max_priests, "citizens": free,
                              "island_conversion_percent": share, "own_conversion_percent": 50.0,
                              "citizens_per_priest": 7, "max_inhabitants": 3500, "max_citizens_on_island": CAPACITY,
                              "wonder_name": "Forja de Hefesto (Nível 5/5)", "activate_state": "enabled",
                              "wonder_effect": "Todas as unidades de combate recebem 20 mais armadura e causam 15% mais dano.",
                              "duration_text": "Duração: 1D", "cooldown_text": "Regeneração: 3D 12h", "island_name": "Haunios"}}


class TemplesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(self.user)
        node = Node.objects.create(name="agente", transport="https")
        self.gas = []
        for n, (city, priests, max_priests, free) in enumerate([("Glitnir", 287, 756, 1290), ("Alfheim", 943, 943, 0)], start=1):
            account = Account.objects.create(node=node, label=f"Conta {n}", email=f"c{n}@example.com", password_enc="x")
            ga = GameAccount.objects.create(account=account, lobby_account_id=n, server_id="s78-br", server_language="br",
                                            server_number=78, name=f"Jogador{n}")
            participants = [{"player": "Outro", "city_name": "Longe", "city_id": 0, "priests": 4463,
                             "conversion_percent": 80.0, "island_share_percent": 85.99}]
            AccountSnapshot.objects.create(
                account=account, game_account=ga, military={},
                base_snapshot={"island_wonders": {"4479": {"level": 5, "max_level_reached": True, "faith_percent": 100.0,
                                                           "participants": participants + [
                                                               {"player": "Eu", "city_name": "Glitnir", "island_share_percent": 5.53},
                                                               {"player": "Eu", "city_name": "Alfheim", "island_share_percent": 18.18}],
                                                           "updated_at": "2026-10-16T18:00:00+00:00"}}},
                cities=[_temple_city(10 + n, city, 4479, priests, max_priests, free)])
            self.gas.append(ga)

    def _jobs(self):
        return {str(j.game_account_id): json.loads(j.inputs_json) for j in Job.objects.filter(action_code=35)}

    def test_the_action_is_in_the_catalog(self):
        self.assertEqual(ACTION_CATALOG[35]["name"], "Ajustar Sacerdotes")

    def test_an_island_over_100_percent_is_balanced_evenly_across_accounts(self):
        island = next(i for i in islands_for_organization() if i.island_id == "4479")
        self.assertAlmostEqual(island.excess, 9.7, places=1)
        self.assertTrue(plan_faith(island))
        targets = {t.city_name: t.target for t in island.own}
        self.assertLessEqual(abs(targets["Glitnir"] - targets["Alfheim"]), 1)        # spread evenly over the accounts
        self.assertLess(targets["Alfheim"], 943)                                      # the excess comes off
        faith = sum(targets.values()) * 7 * 100 / CAPACITY + island.others_share
        self.assertGreaterEqual(faith, 100)
        self.assertLess(faith, 100.05)                                                # and nothing to spare

    def test_the_temples_page_shows_the_islands_and_balances_them(self):
        html = self.client.get("/game/temples/").content.decode()
        self.assertIn("Forja de Hefesto", html)
        self.assertIn("força 5/5", html)
        self.assertIn("sobra 9.7%", html.replace(",", "."))
        self.assertIn("Jogador2", html)
        island = next(i for i in islands_for_organization() if i.island_id == "4479")
        plan_faith(island)
        planned = {t.game_account_id: {t.city_id: t.target} for t in island.own}
        self.client.post("/game/temples/", {"action": "balance"})
        jobs = self._jobs()
        self.assertEqual({k: v["city_targets"] for k, v in jobs.items()}, planned)
        self.assertTrue(all(v["target_mode"] == "city_targets" for v in jobs.values()))

    def test_the_job_form_balances_across_accounts_too(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.gas[0].pk), "action": "35"}).content.decode()
        self.assertIn("Templos e Fé", html)
        self.assertIn('"citizens_per_priest": 7', html)
        self.client.post("/jobs/new/submit/", {"game_account": str(self.gas[0].pk), "action_code": "35",
                                                "target_mode": "island_faith", "target_value": "100", "reserve_citizens": "0"})
        self.assertEqual(len(self._jobs()), 2)                                       # both accounts get their part

    def test_activate_miracle_points_to_the_temples_page(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.gas[0].pk), "action": "11"}).content.decode()
        self.assertIn("Forja de Hefesto", html)                                     # the temple state reaches the form
        self.assertIn("/game/temples/", html)

    def test_the_preset_balances_its_accounts_in_one_plan(self):
        preset = Preset.objects.create(name="Fé", created_by=self.user)
        for ga in self.gas:
            PresetGameAccount.objects.create(preset=preset, game_account=ga)
        PresetAction.objects.create(preset=preset, action_code=35, action_name="Ajustar Sacerdotes", order=1,
                                    inputs_json=json.dumps({"target_mode": "island_faith", "target_value": 100}), per_account_json="{}")
        self.assertNotIn('name="city_mode"', self.client.get(f"/profiles/presets/{preset.pk}/configure/1/").content.decode())
        created, errors = execute_preset(preset, created_by=self.user)
        self.assertEqual((created, errors), (2, []))
