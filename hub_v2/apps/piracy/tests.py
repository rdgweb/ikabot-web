"""N-210/N-211: the piracy page and the month's champions — two champions of different lobbies, points passing through
other lobbies (never inside one), the same raider never on the same account twice in a week."""
import random
from collections import Counter
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.orgs.models import Membership, Organization
from core.auth.tokens import hash_agent_token

from . import services
from .models import PiracyMonth, PiracyRoute


def _fortress_city(i):
    return [{"id": str(1000 + i), "island_id": str(500 + i), "name": f"Forte{i}", "x": 50, "y": 50,
             "buildings": [{"building": "pirateFortress", "level": 10, "position": 17}]}]


class Base(TestCase):
    def setUp(self):
        self.user = get_user_model()(username="dono")
        self.user._own_organization = True
        self.user.set_password("x")
        self.user.save()
        self.org = Organization.objects.create(name="Org", slug="org")
        Membership.objects.create(organization=self.org, user=self.user, role="owner")
        self.node = Node.objects.create(name="no", organization=self.org, transport="https")
        self.token = self.node.new_agent_token
        # 4 lobbies: 10, 4, 1 and 1 accounts, like the real organization
        self.accounts = []
        n = 0
        for label, count in (("grande", 10), ("medio", 4), ("pequeno1", 1), ("pequeno2", 1)):
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
                self.accounts.append(ga)
                with patch("apps.jobs.signals.dispatch_job", create=True):
                    Job.objects.create(account=lobby, node=self.node, game_account=ga, action_code=17, status="scheduled")
        self.client.force_login(self.user)


class PlanTests(Base):
    def _month(self):
        from apps.orgs.scope import activate, deactivate

        token = activate(self.org)
        try:
            return services.plan_month(self.org, "s1-br", rng=random.Random(7))
        finally:
            deactivate(token)

    def test_two_champions_of_different_lobbies_and_the_fixed_rules(self):
        month = self._month()
        champions = [c.game_account for c in month.champions.all()]
        self.assertEqual(len(champions), 2)
        self.assertNotEqual(champions[0].account_id, champions[1].account_id)
        self.assertEqual(month.routes.values("week").distinct().count(), 4)           # 28 days: 4 weeks
        for route in month.routes.select_related("raider", "target"):
            self.assertNotEqual(route.raider.account_id, route.target.account_id)      # never inside a lobby
            self.assertLess(route.due_from, route.due_until)
        pairs = Counter((r.week, r.raider_id, r.target_id) for r in month.routes.all())
        self.assertEqual(max(pairs.values()), 1)                                       # once a week per pair
        champion_ids = {c.pk for c in champions}
        for route in month.routes.filter(stage=2):
            self.assertIn(route.raider_id, champion_ids)
        self.assertFalse(month.routes.filter(stage=1, raider_id__in=champion_ids).exists())
        # every account takes part, the champions on the receiving end
        involved = set(month.routes.values_list("raider_id", flat=True)) | set(month.routes.values_list("target_id", flat=True))
        self.assertGreaterEqual(len(involved), 14)

    def test_next_month_draws_accounts_that_were_not_champions(self):
        first = {c.game_account_id for c in self._month().champions.all()}
        second = {c.game_account_id for c in self._month().champions.all()}
        self.assertFalse(first & second)
        self.assertEqual(PiracyMonth.objects.filter(status="open").count(), 1)

    def test_the_agent_gets_its_route_in_its_window_and_reports(self):
        month = self._month()
        route = month.routes.filter(stage=1, week=1).select_related("raider", "target").first()
        headers = {"HTTP_X_AGENT_TOKEN": self.token, "HTTP_X_AGENT_NODE_ID": str(self.node.pk)}
        data = self.client.get(f"/api/agent/piracy/next/?game_account_id={route.raider_id}", **headers).json()
        route = PiracyRoute.objects.select_related("raider", "target").get(pk=data["route"]["route_id"])   # one of its origins
        self.assertEqual((route.raider_id, route.week, route.stage), (route.raider_id, 1, 1))
        self.assertNotEqual(data["route"]["raider_lobby"], data["route"]["target_lobby"])
        self.assertTrue(data["route"]["target_city_id"])
        self.assertTrue(self.client.get(f"/api/agent/piracy/next/?game_account_id={route.target_id}", **headers).json()["hold"])
        # the champion's stage comes in the second half of the week
        champion = month.champions.first().game_account
        self.assertIsNone(self.client.get(f"/api/agent/piracy/next/?game_account_id={champion.pk}", **headers).json()["route"])
        self.client.post(f"/api/agent/piracy/routes/{route.pk}/", {"status": "sent"}, content_type="application/json", **headers)
        self.client.post(f"/api/agent/piracy/routes/{route.pk}/", {"status": "done", "message": "ok"}, content_type="application/json", **headers)
        route.refresh_from_db()
        self.assertEqual(route.status, PiracyRoute.DONE)
        # a window that passed without a raid is skipped
        PiracyRoute.objects.filter(pk__in=list(month.routes.filter(stage=1, week=1, status="planned").values_list("pk", flat=True)))\
            .update(due_until=timezone.now() - timedelta(minutes=1))
        services.expire()
        self.assertFalse(month.routes.filter(stage=1, week=1, status="planned").exists())

    def test_the_page_plans_and_shows_the_month(self):
        page = self.client.get("/pirataria/")
        self.assertContains(page, "Campeãs do mês")
        self.client.post("/pirataria/", {"action": "plan", "server": "s1-br"})
        month = PiracyMonth.objects.get(status="open")
        page = self.client.get("/pirataria/?server=s1-br").content.decode()
        for champion in month.champions.all():
            self.assertIn(champion.game_account.name, page)
        self.assertIn("Caminho dos pontos", page)
        same = [str(self.accounts[0].pk), str(self.accounts[1].pk)]                     # both of the big lobby
        self.client.post("/pirataria/", {"action": "choose", "server": "s1-br", "champion": same})
        self.assertEqual(PiracyMonth.objects.filter(status="open").get().pk, month.pk)  # refused: same lobby
        self.client.post("/pirataria/", {"action": "close", "server": "s1-br"})
        self.assertFalse(PiracyMonth.objects.filter(status="open").exists())

    def test_another_organization_sees_nothing(self):
        self._month()
        other = get_user_model()(username="outro")
        other._own_organization = True
        other.set_password("x")
        other.save()
        org = Organization.objects.create(name="Outra", slug="outra")
        Membership.objects.create(organization=org, user=other, role="owner")
        self.client.force_login(other)
        page = self.client.get("/pirataria/?server=s1-br").content.decode()
        self.assertNotIn("J1<", page)
        self.assertNotIn("Caminho dos pontos", page)


class EligibilityTests(Base):
    def test_accounts_without_fortress_or_job_stay_out_and_a_turning_ranking_plans_the_next(self):
        from apps.orgs.scope import activate, deactivate

        Job.objects.filter(game_account=self.accounts[0]).delete()
        AccountSnapshot.objects.filter(game_account=self.accounts[1]).update(cities=[])
        token = activate(self.org)
        try:
            able = services.eligible("s1-br")
            self.assertNotIn(self.accounts[0], able)
            self.assertNotIn(self.accounts[1], able)
            with self.assertRaises(ValueError):
                services.plan_month(self.org, "s1-br", champions=[self.accounts[0], self.accounts[12]])
            for snap in AccountSnapshot.objects.all():
                snap.base_snapshot["piracy_highscore"]["time_left"] = 3600
                snap.save()
            month = services.plan_month(self.org, "s1-br", rng=random.Random(3))
        finally:
            deactivate(token)
        self.assertGreater(month.starts_at, timezone.now() + timedelta(minutes=50))   # from the turn on
        involved = set(month.routes.values_list("raider_id", flat=True)) | set(month.routes.values_list("target_id", flat=True))
        self.assertFalse({self.accounts[0].pk, self.accounts[1].pk} & involved)
