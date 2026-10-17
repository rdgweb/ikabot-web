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

    def test_the_loop_form_has_the_strategy_switches(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.ga.pk), "action": "1007"}).content.decode()
        self.assertIn("Estratégia", html)
        self.assertIn('name="fill_bars"', html)
        self.assertIn('name="favor_reserve"', html)
        self.assertNotIn("Leitura operacional", html)
        self.assertEqual(html.count('name="keep_worshipped"'), 1)            # not rendered again as a loose checkbox

    def test_the_job_form_shows_each_city_and_its_god(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.ga.pk), "action": "36"}).content.decode()
        self.assertIn("Deus de cada cidade", html)
        self.assertIn('"name": "Glitnir"', html)
        self.assertIn("icon_glass.png", html)                        # Glitnir's luxury good, from the snapshot
        self.assertIn('"suggested": 5', html)                         # Teia pre-picked for crystal
        self.assertIn("game/gods/plutus.png", html)
        self.assertIn("/game/temples/", self.client.get("/game/temples/").content.decode())   # the panel opens this form
        self.assertIn("action=36", self.client.get("/game/temples/").content.decode())

    def test_the_cities_picked_go_in_the_job(self):
        from apps.jobs.models import Job

        self.client.post("/jobs/new/submit/", {"game_account": str(self.ga.pk), "action_code": "36",
                                                "worship": json.dumps({"4": 4}), "by_good": ""})
        job = Job.objects.get(action_code=36)
        self.assertEqual(json.loads(json.loads(job.inputs_json)["worship"]), {"4": 4})

    def test_a_preset_chooses_by_luxury_good(self):
        from apps.profiles.models import Preset, PresetAction, PresetGameAccount

        user = get_user_model().objects.get(username="rodrigo")
        preset = Preset.objects.create(name="Deuses", created_by=user)
        PresetGameAccount.objects.create(preset=preset, game_account=self.ga)
        PresetAction.objects.create(preset=preset, action_code=36, action_name="Deus de Cada Cidade", order=1,
                                    inputs_json=json.dumps({"by_good": json.dumps({"3": 4})}), per_account_json="{}")
        html = self.client.get(f"/profiles/presets/{preset.pk}/configure/1/").content.decode()
        self.assertIn("Deus de cada cidade", html)
        self.assertNotIn('name="city_mode"', html)


class ShrineFocusTests(TestCase):
    """A new Santuário em Loop takes over the account's previous one (switching focus with presets)."""

    def test_the_new_loop_replaces_the_waiting_one(self):
        from apps.jobs.models import Job
        from apps.jobs.services.workflows import create_job_with_workflow

        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="Jogador")
        old = create_job_with_workflow(account=account, game_account=ga, node=node, action_code=1007,
                                       inputs={"god_tyche": True}, status="scheduled")
        new = create_job_with_workflow(account=account, game_account=ga, node=node, action_code=1007,
                                       inputs={"god_plutus": True}, status="queued")
        old.refresh_from_db()
        self.assertEqual(old.status, "cancelled")
        self.assertEqual(new.workflow_id, old.workflow_id)                 # the account keeps one loop workflow
        self.assertEqual(Job.objects.filter(action_code=1007).exclude(status="cancelled").get().pk, new.pk)
        # the loop rescheduling itself is not a new focus
        again = create_job_with_workflow(account=account, game_account=ga, node=node, action_code=1007,
                                         inputs={"god_plutus": True}, status="scheduled", source_job=new)
        new.refresh_from_db()
        self.assertNotEqual(new.status, "cancelled")
        self.assertEqual(again.status, "scheduled")


class ShrinePresetOrderTests(TestCase):
    def test_the_loop_waits_for_the_god_change_even_when_it_comes_first_in_the_preset(self):
        from apps.jobs.models import Job
        from apps.profiles.models import Preset, PresetAction, PresetGameAccount
        from apps.profiles.services import execute_preset

        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="Jogador")
        AccountSnapshot.objects.create(account=account, game_account=ga, military={}, base_snapshot={}, cities=[])
        preset = Preset.objects.create(name="Santuário Ouro", created_by=user)
        PresetGameAccount.objects.create(preset=preset, game_account=ga)
        PresetAction.objects.create(preset=preset, action_code=1007, action_name="Santuário em Loop", order=1,
                                    inputs_json=json.dumps({"keep_worshipped": True, "god_plutus": True, "cycle_hours": 72,
                                                            "favor_recheck_minutes": 180}), per_account_json="{}")
        PresetAction.objects.create(preset=preset, action_code=36, action_name="Deus de Cada Cidade", order=2,
                                    inputs_json=json.dumps({"by_good": json.dumps({"1": 4, "2": 4, "3": 4, "4": 4})}), per_account_json="{}")
        execute_preset(preset, created_by=user)
        align = Job.objects.get(action_code=36)
        loop = Job.objects.get(action_code=1007)
        self.assertLess(align.created_at, loop.created_at)
        self.assertEqual(loop.status, "scheduled")
        self.assertGreater(loop.scheduled_for, timezone.now() + timedelta(minutes=4))


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
