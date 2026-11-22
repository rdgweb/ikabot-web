"""N-210/N-211: the piracy page and the month's champions — two champions of different lobbies that convert all; the
other accounts keep their points (converting a share now and then) and are raided towards the champions: only sure
wins (crew > target crew x 1.1 / 0.9), never inside a lobby, the same pair once in 7 days. Targets added by hand are
raided by the champions."""
import json
import random
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.orgs.models import Membership, Organization
from apps.orgs.scope import activate, deactivate

from . import services
from .models import PiracyMonth, PiracyRoute, PiracyTarget


def _fortress_city(i):
    return [{"id": str(1000 + i), "island_id": str(500 + i), "name": f"Forte{i}", "x": 50, "y": 50,
             "buildings": [{"building": "pirateFortress", "level": 10, "position": 17}, {"building": "palace", "level": 3}]}]


class Base(TestCase):
    LOBBIES = (("grande", 6), ("medio", 3), ("pequeno", 1))

    def setUp(self):
        self.user = get_user_model()(username="dono")
        self.user._own_organization = True
        self.user.set_password("x")
        self.user.save()
        self.org = Organization.objects.create(name="Org", slug="org")
        Membership.objects.create(organization=self.org, user=self.user, role="owner")
        self.node = Node.objects.create(name="no", organization=self.org, transport="https")
        self.token = self.node.new_agent_token
        self.accounts, self.jobs, n = [], {}, 0
        for label, count in self.LOBBIES:
            lobby = Account.objects.create(node=self.node, label=label, email=f"{label}@example.com", password_enc="x",
                                           organization=self.org)
            for _ in range(count):
                n += 1
                ga = GameAccount.objects.create(account=lobby, lobby_account_id=n, server_id="s1-br", name=f"J{n}",
                                                server_language="br", server_number=1)
                AccountSnapshot.objects.create(account=lobby, game_account=ga, cities=_fortress_city(n), base_snapshot={
                    "player_name": f"J{n}",
                    "piracy_highscore": {"time_left": 28 * 86400, "updated_at": int(timezone.now().timestamp()),
                                         "self_entry": {"place": n, "score": 1000 - n, "target_city_id": str(1000 + n),
                                                        "target_island_id": str(500 + n)}, "entries": []}})
                with patch("apps.jobs.signals.dispatch_job", create=True):
                    self.jobs[ga.pk] = Job.objects.create(account=lobby, node=self.node, game_account=ga, action_code=17,
                                                          status="scheduled")
                self.set_strength(ga, points=5000, crew=1000)
                self.accounts.append(ga)
        self.client.force_login(self.user)

    def set_strength(self, ga, *, points, crew):
        job = self.jobs[ga.pk]
        job.inputs_json = json.dumps({"last_capture_points": points, "last_complete_crew_points": crew})
        job.save(update_fields=["inputs_json"])

    def plan(self, **kwargs):
        token = activate(self.org)
        try:
            return services.plan_month(self.org, "s1-br", rng=random.Random(kwargs.pop("seed", 7)), **kwargs)
        finally:
            deactivate(token)

    def agent(self, path, method="get", **data):
        headers = {"HTTP_X_AGENT_TOKEN": self.token, "HTTP_X_AGENT_NODE_ID": str(self.node.pk)}
        if method == "get":
            return self.client.get(path, **headers).json()
        return self.client.post(path, data, content_type="application/json", **headers).json()


class PlanTests(Base):
    def test_two_champions_of_different_lobbies_and_everyone_has_a_place(self):
        month = self.plan()
        champions = [m.game_account for m in month.members.filter(role="champion")]
        self.assertEqual(len(champions), 2)
        self.assertNotEqual(champions[0].account_id, champions[1].account_id)
        self.assertEqual(month.members.count(), len(self.accounts))
        for passer in month.members.filter(role="passer").select_related("game_account"):
            champion = month.members.get(role="champion", group=passer.group).game_account
            self.assertNotEqual(passer.game_account.account_id, champion.account_id)

    def test_next_month_draws_accounts_that_were_not_champions(self):
        first = {c.game_account_id for c in self.plan().champions.all()}
        second = {c.game_account_id for c in self.plan(seed=8).champions.all()}
        self.assertFalse(first & second)
        self.assertEqual(PiracyMonth.objects.filter(status="open").count(), 1)

    def test_the_champion_converts_all_the_others_keep_and_share(self):
        month = self.plan()
        champion = month.members.filter(role="champion").first().game_account
        origin = month.members.filter(role="origin").first().game_account
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        self.assertEqual((data["convert_all"], data["hold"]), (True, False))
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")
        self.assertEqual((data["convert_all"], data["hold"], data["share_percent"], data["route"]), (False, True, 10, None))

    def test_the_champion_takes_only_sure_wins_never_its_lobby_and_once_a_week(self):
        month = self.plan()
        champion_m = month.members.filter(role="champion").first()
        champion = champion_m.game_account
        group = [m.game_account for m in month.members.filter(group=champion_m.group).exclude(pk=champion_m.pk)]
        self.set_strength(champion, points=0, crew=30000)
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        route = PiracyRoute.objects.select_related("target").get(pk=data["route"]["route_id"])
        self.assertIn(route.target, group)
        self.assertNotEqual(route.target.account_id, champion.account_id)
        self.assertGreaterEqual(services.win_chance(route.raider_crew, route.target_crew) * 100, month.min_win_percent)
        self.assertEqual(self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")["route"]["route_id"], str(route.pk))
        self.agent(f"/api/agent/piracy/routes/{route.pk}/", "post", status="sent")
        self.agent(f"/api/agent/piracy/routes/{route.pk}/", "post", status="done", message="ganhou")
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        if data["route"]:
            self.assertNotEqual(data["route"]["target_game_account_id"], str(route.target_id))     # not twice in 7 days
        # a crew without the chance asked takes nothing (1100 against 1000: about 72%)
        PiracyRoute.objects.filter(raider=champion, status="planned").update(status="skipped")
        self.set_strength(champion, points=0, crew=1100)
        self.assertIsNone(self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")["route"])
        for route in PiracyRoute.objects.select_related("raider", "target"):
            self.assertNotEqual(route.raider.account_id, route.target.account_id)

    def test_targets_by_hand_are_raided_by_the_champions_only(self):
        month = self.plan()
        target = PiracyTarget.objects.create(organization=self.org, server_id="s1-br", player_name="Inimigo", city_id="999",
                                             island_id="88")
        champion = month.members.filter(role="champion").first().game_account
        passer = month.members.filter(role="passer").first().game_account
        for ga in self.accounts:
            self.set_strength(ga, points=0, crew=1000)                       # nothing to gather: the target comes next
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        self.assertEqual((data["route"]["target_city_id"], data["route"]["outside"]), ("999", True))
        self.assertEqual(PiracyRoute.objects.get(pk=data["route"]["route_id"]).outside_id, target.pk)
        self.assertIsNone(self.agent(f"/api/agent/piracy/next/?game_account_id={passer.pk}")["route"])

    def test_nothing_before_the_month_starts(self):
        for snap in AccountSnapshot.objects.all():
            snap.base_snapshot["piracy_highscore"]["time_left"] = 3600       # the ranking turns in an hour
            snap.save()
        month = self.plan()
        self.assertGreater(month.starts_at, timezone.now() + timedelta(minutes=50))
        origin = month.members.filter(role="origin").first().game_account
        self.assertFalse(self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")["hold"])


class EligibilityTests(Base):
    def test_accounts_without_fortress_or_job_stay_out(self):
        Job.objects.filter(game_account=self.accounts[0]).delete()
        AccountSnapshot.objects.filter(game_account=self.accounts[1]).update(cities=[])
        token = activate(self.org)
        try:
            able = services.eligible("s1-br")
        finally:
            deactivate(token)
        self.assertNotIn(self.accounts[0], able)
        self.assertNotIn(self.accounts[1], able)
        month = self.plan()
        self.assertFalse(month.members.filter(game_account__in=self.accounts[:2]).exists())


class PageTests(Base):
    def test_the_page_plans_adds_targets_and_closes(self):
        self.assertContains(self.client.get("/pirataria/"), "Campeãs do mês")
        self.client.post("/pirataria/", {"action": "plan", "server": "s1-br", "share_percent": "20", "share_every_hours": "12"})
        month = PiracyMonth.objects.get(status="open")
        self.assertEqual((month.share_percent, month.share_every_hours), (20, 12))
        page = self.client.get("/pirataria/?server=s1-br").content.decode()
        for champion in month.champions.all():
            self.assertIn(champion.game_account.name, page)
        self.client.post("/pirataria/", {"action": "add_target", "server": "s1-br", "player_name": "Inimigo"})
        self.client.post("/pirataria/", {"action": "add_target", "server": "s1-br", "player_name": "J1"})   # an own account: refused
        self.assertEqual(list(PiracyTarget.objects.values_list("player_name", flat=True)), ["Inimigo"])
        self.assertContains(self.client.get("/pirataria/?server=s1-br"), "Inimigo")
        same = [str(self.accounts[0].pk), str(self.accounts[1].pk)]                      # both of the big lobby
        self.client.post("/pirataria/", {"action": "choose", "server": "s1-br", "champion": same})
        self.assertEqual(PiracyMonth.objects.get(status="open").pk, month.pk)
        self.client.post("/pirataria/", {"action": "close", "server": "s1-br"})
        self.assertFalse(PiracyMonth.objects.filter(status="open").exists())

    def test_another_organization_sees_nothing(self):
        self.plan()
        PiracyTarget.objects.create(organization=self.org, server_id="s1-br", player_name="Inimigo")
        other = get_user_model()(username="outro")
        other._own_organization = True
        other.set_password("x")
        other.save()
        org = Organization.objects.create(name="Outra", slug="outra")
        Membership.objects.create(organization=org, user=other, role="owner")
        self.client.force_login(other)
        page = self.client.get("/pirataria/?server=s1-br").content.decode()
        self.assertNotIn("J1<", page)
        self.assertNotIn("Inimigo", page)


class WinChanceTests(TestCase):
    """Each side gets an independent -10%..+10% on its crew; the bigger crew wins."""

    def test_the_chance_follows_the_crews(self):
        self.assertAlmostEqual(services.win_chance(1000, 1000), 0.5, places=2)
        self.assertAlmostEqual(services.win_chance(1150, 1000), 0.954, delta=0.01)
        self.assertEqual(services.win_chance(1223, 1000), 1.0)
        self.assertLess(services.win_chance(1000, 1223), 0.01)
        self.assertIsNone(services.win_chance(None, 1000))
        self.assertTrue(services.sure_win(1150, 1000, 95))
        self.assertFalse(services.sure_win(1100, 1000, 95))
