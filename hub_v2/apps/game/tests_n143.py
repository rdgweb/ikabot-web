"""N-143: Santuário dos Deuses — each city's god, whether it blesses anything, and the god it should worship."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.services.shrine import shrines_for_organization

SHRINE = {
    "city_name": "Valhala", "building_level": 27, "current_favor": 1275, "gods_worshipped": [2, 3, 6],
    "gods": {"2": {"grace_period": "2D 23h", "grace_seconds": 255600}, "3": {"grace_period": "21h 46m", "grace_seconds": 78360},
             "6": {"grace_period": "-", "grace_seconds": 0}},
    "worship_assignments": [
        {"slot": 1, "city_id": 1, "god_id": 3, "god_css": "god_tyche", "current_bonus": "57,00", "generated_resources": "667.489"},
        {"slot": 2, "city_id": 2, "god_id": 3, "god_css": "god_tyche", "current_bonus": "57,00", "generated_resources": "0"},
        {"slot": 3, "city_id": 3, "god_id": 6, "god_css": "god_hephaestos", "current_bonus": "0,00", "generated_resources": "0"},
    ],
}


class ShrinePanelTests(TestCase):
    def setUp(self):
        from apps.orgs.models import Membership
        from apps.orgs.services import default_organization

        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.org = default_organization()
        Membership.objects.get_or_create(organization=self.org, user=user, defaults={"role": Membership.OWNER})
        self.client.force_login(user)
        SHRINE["gods_read_at"] = timezone.now().isoformat()
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="Jogador")
        cities = [
            {"id": 1, "name": "Okolnir", "tradegood": 2, "shrine_state": SHRINE},          # marble + Tique: blessed
            {"id": 2, "name": "Valhala", "tradegood": 1},                                  # wine + Tique: nothing
            {"id": 3, "name": "Hell", "tradegood": 4},                                     # sulfur + Hefesto out of grace
            {"id": 4, "name": "Glitnir", "tradegood": 3},                                  # no god
        ]
        AccountSnapshot.objects.create(account=account, game_account=ga, military={}, base_snapshot={}, cities=cities)
        self.ga = ga

    def test_each_city_says_whether_its_god_blesses_it(self):
        shrine = shrines_for_organization()[0]
        status = {c.name: (c.status, c.recommended) for c in shrine.cities}
        self.assertEqual(status, {"Okolnir": ("ok", 3), "Valhala": ("wrong_good", 2), "Hell": ("no_grace", 6), "Glitnir": ("no_god", 5)})
        self.assertEqual(shrine.bonus, "57,00")
        self.assertEqual([(g["name"], g["in_grace"]) for g in shrine.gods], [("Dionísio", True), ("Tique", True), ("Hefesto", False)])

    def test_the_page_shows_the_shrine(self):
        html = self.client.get("/game/temples/").content.decode()
        self.assertIn("Santuário dos Deuses", html)
        self.assertIn("não produz mármore", html)
        self.assertIn("sem graça", html)
        self.assertIn("game/gods/theia.png", html)          # Glitnir (crystal) should worship Teia

    def test_an_old_reading_does_not_pass_for_grace(self):
        SHRINE["gods_read_at"] = (timezone.now() - timedelta(days=3)).isoformat()      # Dionísio had 2D 23h back then
        from apps.accounts.models import GameAccount
        snap = AccountSnapshot.objects.get(game_account=GameAccount.objects.get(name="Jogador"))
        cities = snap.cities
        cities[0]["shrine_state"] = SHRINE
        snap.cities = cities
        snap.save()
        gods = {g["name"]: (g["in_grace"], g["grace"]) for g in shrines_for_organization()[0].gods}
        self.assertEqual(gods["Dionísio"], (False, "sem graça"))

    def test_the_organization_chooses_pluto_for_crystal_and_aligns(self):
        from apps.jobs.models import Job
        from apps.jobs.services.dispatch import build_payload

        self.client.post("/game/temples/", {"action": "shrine_gods", "shrine_god_3": "4"})
        glitnir = next(c for c in shrines_for_organization({"1": 2, "2": 3, "3": 4, "4": 6})[0].cities if c.name == "Glitnir")
        self.assertEqual(glitnir.recommended, 4)
        html = self.client.get("/game/temples/").content.decode()
        self.assertIn("game/gods/plutus.png", html)
        self.assertIn("Alinhar", html)
        self.client.post("/game/temples/", {"action": "align", "game_account": str(self.ga.pk)})
        job = Job.objects.get(action_code=36)
        self.assertEqual(build_payload(job)["shrine_gods"]["3"], 4)

    def test_saving_the_miracle_rules_keeps_the_shrine_choice(self):
        from apps.game.services.miracle_rules import save_rules, save_shrine_gods, shrine_gods_for

        save_shrine_gods(self.org, {"shrine_god_3": "4"})
        save_rules(self.org, {"defense_on_attack": "1"})
        self.assertEqual(shrine_gods_for(self.org)["3"], 4)


class MiracleTimerTests(TestCase):
    """N-148: the time a miracle stays in effect and the time it comes back, from the Ativar Milagre job."""

    def test_the_timer_counts_from_the_activation(self):
        from apps.game.services.temples import attach_miracle_timers, islands_for_organization
        from apps.jobs.models import Job, JobLog
        from apps.jobs.services.workflows import create_job_with_workflow

        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="Jogador")
        city = {"id": 11, "name": "Hell", "island_id": 9, "buildings": [{"building": "temple", "position": 3, "level": 20}],
                "miracle_state": {"wonder_name": "Templo de Poseidon (Nível 2/5)", "activate_state": "hidden", "priests": 1,
                                  "max_priests": 10, "duration_text": "Duração: 4h", "cooldown_text": "Regeneração: 1D"}}
        AccountSnapshot.objects.create(account=account, game_account=ga, military={}, base_snapshot={}, cities=[city])
        job = create_job_with_workflow(account=account, game_account=ga, node=node, action_code=11,
                                       inputs={"city": "11"}, status="queued")
        activated = timezone.now() - timedelta(hours=1)
        Job.objects.filter(pk=job.pk).update(status="finished", finished_at=activated)
        log = JobLog.objects.create(job=job, message="Milagre ativado: Templo de Poseidon | efeito=+10%")
        JobLog.objects.filter(pk=log.pk).update(created_at=activated)
        islands = islands_for_organization()
        attach_miracle_timers(islands)
        timer = islands[0].timers[0]
        self.assertEqual(round((timer["active_until"] - activated).total_seconds()), 4 * 3600)
        self.assertEqual(round((timer["available_at"] - activated).total_seconds()), 86400)
