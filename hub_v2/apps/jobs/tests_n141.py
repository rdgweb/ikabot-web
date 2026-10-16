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


class TempleAccounts(TestCase):
    """Two accounts with a temple on the same island (Haunios), plus one other player there."""

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


class TemplesTests(TempleAccounts):
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


class PopulationTests(TempleAccounts):
    """Free citizens are not always enough to reach 100%: the plan says how far each island gets."""

    def _short_island(self, free_glitnir):
        snap = AccountSnapshot.objects.get(game_account=self.gas[0])
        cities = snap.cities
        cities[0]["miracle_state"]["citizens"] = free_glitnir
        cities[0]["free_citizens"] = free_glitnir
        # the other players leave: the island needs far more priests than we have citizens for
        snap.base_snapshot["island_wonders"]["4479"]["participants"] = [
            {"player": "Eu", "city_name": "Glitnir", "island_share_percent": 5.53},
            {"player": "Eu", "city_name": "Alfheim", "island_share_percent": 18.18}]
        snap.cities = cities
        snap.save()
        for other in AccountSnapshot.objects.exclude(pk=snap.pk):
            other.base_snapshot["island_wonders"]["4479"]["participants"] = snap.base_snapshot["island_wonders"]["4479"]["participants"]
            other.save()
        return next(i for i in islands_for_organization() if i.island_id == "4479")

    def test_without_free_citizens_the_plan_says_how_far_it_gets(self):
        island = self._short_island(100)
        self.assertTrue(plan_faith(island))
        targets = {t.city_name: t for t in island.own}
        self.assertEqual(targets["Glitnir"].target, 287 + 100)              # every free citizen, not more
        self.assertEqual(targets["Glitnir"].limit, "populacao")
        self.assertEqual(targets["Alfheim"].limit, "templo")                # already full
        self.assertGreater(island.short_priests, 0)
        html = self.client.get("/game/temples/").content.decode()
        self.assertIn("faltam", html)
        self.assertIn("sem livres", html)

    def test_the_reserve_of_free_citizens_is_kept(self):
        island = self._short_island(100)
        plan_faith(island, reserve=40)
        self.assertEqual(next(t for t in island.own if t.city_name == "Glitnir").target, 287 + 60)
        self.client.post("/game/temples/", {"action": "balance", "reserva": "40"})
        job = self._jobs()[str(self.gas[0].pk)]
        self.assertEqual(job["reserve_citizens"], 40)
        self.assertEqual(job["city_targets"], {"11": 347})


class MiracleRulesTests(TempleAccounts):
    """N-141 phase 2: Atena/Hefesto on an attack, Colosso when losing, Poseidon/Hermes on big shipments."""

    def setUp(self):
        super().setUp()
        from apps.orgs.models import Membership
        from apps.orgs.services import default_organization

        self.org = default_organization()
        Membership.objects.get_or_create(organization=self.org, user=self.user, defaults={"role": Membership.OWNER})
        snap = AccountSnapshot.objects.get(game_account=self.gas[0])
        cities = snap.cities
        cities[0]["miracle_state"]["wonder_name"] = "Templo de Atena (Nível 3/5)"
        athena_far = _temple_city(99, "Longe", 777, 10, 100, 10)
        athena_far["miracle_state"]["wonder_name"] = "Colosso (Nível 2/5)"
        snap.cities = cities + [athena_far]
        snap.save()

    def _miracle_jobs(self):
        return [json.loads(j.inputs_json)["city"] for j in Job.objects.filter(action_code=11)]

    def test_rules_default_on_and_are_saved_from_the_page(self):
        from apps.game.services.miracle_rules import rules_for

        self.assertTrue(rules_for(self.org)["defense_on_attack"])
        self.client.post("/game/temples/", {"action": "rules", "colossus_when_losing": "1", "ship_share": "70"})
        rules = rules_for(self.org)
        self.assertEqual((rules["defense_on_attack"], rules["colossus_when_losing"], rules["ship_share"]), (False, True, 70))
        self.assertIn('name="ship_share"', self.client.get("/game/temples/").content.decode())

    def test_an_attack_asks_athena_once_and_offers_the_buttons(self):
        from apps.game.services.miracle_rules import defense_buttons, on_attack_alert

        asked = on_attack_alert(self.gas[0], {"target_city_id": "11", "target_city": "Glitnir"})
        self.assertEqual(asked, ["Templo de Atena"])                     # any temple of the account will do
        self.assertEqual(self._miracle_jobs(), ["11"])
        self.assertEqual(on_attack_alert(self.gas[0], {"target_city_id": "11"}), [])   # not twice within the hour
        labels = [b["text"] for b in defense_buttons(self.gas[0], asked)]
        self.assertIn("✨ Atena (pedido)", labels)
        self.assertIn("✨ Colosso", labels)

    def test_the_rule_off_asks_nothing(self):
        from apps.game.services.miracle_rules import on_attack_alert, save_rules

        save_rules(self.org, {"colossus_when_losing": "1"})
        self.assertEqual(on_attack_alert(self.gas[0], {"target_city_id": "11"}), [])
        self.assertEqual(self._miracle_jobs(), [])

    def test_the_telegram_button_asks_the_miracle(self):
        from apps.game.services.miracle_rules import activate_from_telegram

        ok, msg = activate_from_telegram(str(self.gas[0].pk), "99")
        self.assertTrue(ok, msg)
        self.assertEqual(self._miracle_jobs(), ["99"])
        self.assertFalse(activate_from_telegram(str(self.gas[0].pk), "12345")[0])

    def test_losing_a_defense_asks_the_colossus(self):
        from apps.combat.models import CombatReport
        from apps.game.services.miracle_rules import on_combat_round

        report = CombatReport(game_account=self.gas[0], combat_id=1, result="", target_city_id="11", target_city_name="Glitnir",
                              defender_name="Jogador1 [ALI]", finished=False,
                              defender_losses={"303": 40}, attacker_losses={"303": 5})
        self.assertEqual(on_combat_round(report), ["Colosso"])
        self.assertEqual(self._miracle_jobs(), ["99"])
        report.defender_losses, report.attacker_losses = {"303": 5}, {"303": 40}
        Job.objects.filter(action_code=11).delete()
        self.assertEqual(on_combat_round(report), [])                     # winning: nothing

    def test_shipments_carry_the_rule_to_the_agent(self):
        from apps.jobs.services.dispatch import build_payload
        from apps.jobs.services.workflows import create_job_with_workflow

        ga = self.gas[0]
        job = create_job_with_workflow(account=ga.account, game_account=ga, node=ga.account.node, action_code=2,
                                       inputs={"from_city": "11", "to_city": "12", "wood": 500}, status="queued")
        rules = build_payload(job)["miracle_rules"]
        self.assertEqual((rules["speed_on_shipments"], rules["ship_share"]), (True, 50))
        self.assertIn("Templo de Poseidon", rules["speed_miracles"])
        other = create_job_with_workflow(account=ga.account, game_account=ga, node=ga.account.node, action_code=35,
                                         inputs={"target_mode": "keep"}, status="queued")
        self.assertNotIn("miracle_rules", build_payload(other))
