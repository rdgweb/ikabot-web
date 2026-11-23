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
                self.set_strength(ga, points=15000, crew=1000)
                self.accounts.append(ga)
        self.client.force_login(self.user)

    def set_strength(self, ga, *, points, crew):
        job = self.jobs[ga.pk]
        job.inputs_json = json.dumps({"last_capture_points": points, "last_complete_crew_points": crew})
        job.save(update_fields=["inputs_json"])

    def plan(self, **kwargs):
        kwargs.setdefault("now", timezone.now() - services.START_DELAY - timedelta(minutes=1))   # already running
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
        self.assertIsNone(data["route"])                                     # drawn for a time within the next day
        route = PiracyRoute.objects.select_related("target").get(raider=champion, status="planned")
        self.assertLessEqual(route.due_from, timezone.now() + timedelta(hours=services.DRAW_HOURS, minutes=1))
        PiracyRoute.objects.filter(pk=route.pk).update(due_from=timezone.now() - timedelta(minutes=1))
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        self.assertEqual(data["route"]["route_id"], str(route.pk))
        self.assertIn(route.target, group)
        self.assertNotEqual(route.target.account_id, champion.account_id)
        self.assertGreaterEqual(services.win_chance(route.raider_crew, route.target_crew) * 100, month.min_win_percent)
        self.assertEqual(self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")["route"]["route_id"], str(route.pk))
        self.agent(f"/api/agent/piracy/routes/{route.pk}/", "post", status="sent")
        self.agent(f"/api/agent/piracy/routes/{route.pk}/", "post", status="done", message="ganhou")
        self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        for later in PiracyRoute.objects.filter(raider=champion, status="planned"):
            self.assertNotEqual(later.target_id, route.target_id)          # not twice in 7 days
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

    def test_it_starts_15_minutes_after_the_draw_and_runs_through_the_next_ranking(self):
        for snap in AccountSnapshot.objects.all():
            snap.base_snapshot["piracy_highscore"]["time_left"] = 3600       # the ranking turns in an hour
            snap.save()
        month = self.plan(now=timezone.now())
        self.assertAlmostEqual((month.starts_at - timezone.now()).total_seconds(), 15 * 60, delta=30)
        self.assertGreater(month.ends_at, timezone.now() + timedelta(days=29))
        # the roles swap at the turn, then every 7 days from it
        self.assertAlmostEqual((month.turn_at - timezone.now()).total_seconds(), 3600, delta=60)
        self.assertEqual(services.next_rotation(month), month.turn_at)
        self.assertEqual(services.rotation_index(month, month.turn_at - timedelta(minutes=1)), 0)
        self.assertEqual(services.rotation_index(month, month.turn_at + timedelta(minutes=1)), 1)
        self.assertEqual(services.next_rotation(month, month.turn_at + timedelta(minutes=1)), month.turn_at + timedelta(days=7))
        origin = month.members.filter(role="origin").first().game_account
        self.assertFalse(self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")["hold"])   # 15 minutes still


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


class ThresholdPauseTests(Base):
    def test_below_the_points_nothing_and_paused_everyone_plays_normally(self):
        month = self.plan(min_points=20000)
        champion = month.members.filter(role="champion").first().game_account
        origin = month.members.filter(role="origin").first().game_account
        self.set_strength(champion, points=0, crew=30000)
        self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        self.assertFalse(PiracyRoute.objects.exists())                       # everyone holds 15000 < 20000
        PiracyMonth.objects.filter(pk=month.pk).update(min_points=10000)
        self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        self.assertEqual(PiracyRoute.objects.filter(status="planned").count(), 1)
        self.client.post("/pirataria/", {"action": "pause", "server": "s1-br"})
        self.assertFalse(PiracyRoute.objects.filter(status="planned").exists())
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")
        self.assertEqual((data["route"], data["hold"]), (None, False))
        self.client.post("/pirataria/", {"action": "resume", "server": "s1-br"})
        self.assertTrue(self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")["hold"])


class TargetSearchTests(Base):
    def test_only_cities_with_piracy_and_never_an_own_player(self):
        from apps.worldintel.models import WorldDump, WorldDumpCity, WorldDumpIsland

        dump = WorldDump.objects.create(account=self.accounts[0].account, game_account=self.accounts[0], captured_at=timezone.now())
        island = WorldDumpIsland.objects.create(dump=dump, island_id="77", x=40, y=41)
        WorldDumpCity.objects.create(dump=dump, island=island, game_city_id="5001", name="Porto", owner_name="Inimigo",
                                     actions_json={"piracy_raid": 1})
        WorldDumpCity.objects.create(dump=dump, island=island, game_city_id="5002", name="Vila", owner_name="Inimigo2")
        WorldDumpCity.objects.create(dump=dump, island=island, game_city_id="5003", name="Minha", owner_name="J1",
                                     actions_json={"piracy_raid": 1})
        page = self.client.get("/pirataria/alvos/?server=s1-br&q=Inim").content.decode()
        self.assertIn("Porto", page)
        self.assertNotIn("Vila", page)
        self.assertIn('value="5001"', page)
        self.assertNotIn("Minha", self.client.get("/pirataria/alvos/?server=s1-br&q=J1").content.decode())
        self.client.post("/pirataria/", {"action": "add_target", "server": "s1-br", "player_name": "Inimigo",
                                         "city_id": "5001", "island_id": "77"})
        self.assertEqual(PiracyTarget.objects.get().city_id, "5001")


class ProjectionTests(Base):
    def test_what_a_champion_can_gather(self):
        month = self.plan()
        champion_m = month.members.filter(role="champion").first()
        champion = champion_m.game_account
        self.set_strength(champion, points=0, crew=30000)                     # beats everyone (crew 1000) for sure
        rows = {ga.pk: {"crew": 30000 if ga == champion else 1000, "capture_points": 0 if ga == champion else 15000}
                for ga in self.accounts}
        result = services.projection(month, rows)[champion_m.group]
        others = month.members.filter(group=champion_m.group).exclude(role="champion").select_related("game_account")
        reachable = [m for m in others if m.game_account.account_id != champion.account_id or m.role == "origin"]
        self.assertGreater(result["expected"], 0)
        self.assertLessEqual(result["expected"], result["held"])
        self.assertEqual(result["held"], 15000 * others.count())
        same_lobby = [d for d in result["detail"] if d["ga"].account_id == champion.account_id and d["way"] == "direto"]
        for d in same_lobby:
            self.assertEqual(d["chance"], 0)                                  # never straight from its own lobby
        rows[champion.pk]["crew"] = 900                                       # weaker than all: gathers nothing
        self.assertEqual(services.projection(month, rows)[champion_m.group]["expected"], 0)


class BudgetTests(Base):
    """How much each account may convert: below the crew its raider beats with the month's chance (cap); a passer needs
    the crew that beats its origins (floor)."""

    def test_caps_and_floors_follow_the_crews(self):
        self.assertAlmostEqual(services.win_chance(services.ratio_for(95) * 1000, 1000), 0.95, delta=0.005)
        month = self.plan()
        crews = {ga.pk: 30000 for ga in self.accounts}
        champion_m = month.members.filter(role="champion").first()
        crews[champion_m.game_account_id] = 40000
        result = services.budgets(month, crews)
        ratio = services.ratio_for(month.min_win_percent)
        for m in month.members.filter(group=champion_m.group, role="passer").select_related("game_account"):
            b = result[m.game_account_id]
            self.assertEqual(b["cap"], int(40000 / ratio))                      # the champion still beats it
            if b["floor"]:
                self.assertEqual(b["floor"], int(30000 * ratio) + 1)            # it beats its origins
        self.assertNotIn(champion_m.game_account_id, result)                    # the champion converts all
        origin = month.members.filter(role="origin").select_related("game_account").first()
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={origin.game_account_id}")
        self.assertIn("crew_cap", data)

    def test_the_page_shows_the_next_raids(self):
        month = self.plan()
        champion = month.members.filter(role="champion").first().game_account
        self.set_strength(champion, points=0, crew=30000)
        self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        route = PiracyRoute.objects.get(status="planned")
        page = self.client.get("/pirataria/?server=s1-br").content.decode()
        self.assertIn("Próximos ataques", page)
        self.assertIn(timezone.localtime(route.due_from).strftime("%d/%m %H:%M"), page)


class CapWinsTests(Base):
    def test_a_passer_never_needs_more_crew_than_its_champion_beats(self):
        month = self.plan()
        champion_m = month.members.filter(role="champion").first()
        crews = {ga.pk: 30000 for ga in self.accounts}
        crews[champion_m.game_account_id] = 33000                             # beats ~28.7k at 95%
        result = services.budgets(month, crews)
        for m in month.members.filter(group=champion_m.group, role="passer"):
            b = result[m.game_account_id]
            self.assertTrue(b["floor"] is None or b["floor"] <= b["cap"])    # origins of 30k would need ~34.5k: left out


class ChainChecks:
    """N-215: origin -> passer -> champion, and one lobby never raids itself: the whole chain closes by lobby."""

    def groups_now(self, month, now=None):
        members = services.members_now(month, now)
        out = {}
        for m in members:
            g = out.setdefault(m.group, {"champion": None, "passer": [], "origin": []})
            if m.role == "champion":
                g["champion"] = m.game_account
            else:
                g[m.role].append(m.game_account)
        return out

    def assert_chain_closes(self, month, now=None):
        for number, g in self.groups_now(month, now).items():
            links = services.chain(g["champion"], g["passer"], g["origin"])
            self.assertEqual(links["wrong"], [], f"grupo {number}: passagem do lobby da campeã")
            self.assertEqual(links["blocked"], [], f"grupo {number}: origem sem ninguém de outro lobby")
            self.assertEqual(links["idle"], [], f"grupo {number}: passagem sem origem de outro lobby")


class GroupingTests(ChainChecks, Base):
    def test_the_chain_closes_by_lobby_for_any_draw(self):
        for seed in range(8):
            month = self.plan(seed=seed)
            self.assertEqual(month.members.count(), len(self.accounts))
            self.assert_chain_closes(month)

    def test_the_big_lobby_feeds_the_others(self):
        # as on s78: champions of the big lobby (6) and of the middle one (3); the small one (1) is the only third lobby
        big = [ga for ga in self.accounts if ga.account.label == "grande"]
        middle = [ga for ga in self.accounts if ga.account.label == "medio"]
        month = self.plan(champions=[big[0], middle[0]])
        self.assert_chain_closes(month)
        groups = self.groups_now(month)
        of_big = next(g for g in groups.values() if g["champion"] == big[0])
        self.assertTrue(of_big["passer"])
        self.assertTrue(all(p.account.label != "grande" for p in of_big["passer"]))
        # the big lobby's own accounts may be origins there: a passer of another lobby takes their points
        self.assertTrue(any(o.account.label == "grande" for o in of_big["origin"]))

    def test_a_passer_amid_origins_of_its_lobby_is_shown_as_blocked(self):
        big = [ga for ga in self.accounts if ga.account.label == "grande"]
        middle = [ga for ga in self.accounts if ga.account.label == "medio"]
        links = services.chain(middle[0], [big[0]], big[1:4])            # the Totem case of 22/11
        self.assertEqual(links["idle"], [big[0]])
        self.assertEqual(links["blocked"], [])                           # the champion takes them straight
        links = services.chain(big[0], [big[1]], big[2:4])
        self.assertEqual((links["wrong"], links["blocked"]), ([big[1]], big[2:4]))
        month = self.plan(champions=[big[0], middle[0]])
        group = month.members.get(role="champion", game_account=middle[0]).group
        month.members.exclude(role="champion").delete()                  # the plan as it was: a passer amid its lobby
        month.members.create(game_account=big[1], role="passer", group=group)
        for ga in big[2:4]:
            month.members.create(game_account=ga, role="origin", group=group)
        page = self.client.get("/pirataria/?server=s1-br").content.decode()
        self.assertIn("sem origem de outro lobby", page)
        self.assertIn("Refazer grupos corrige", page)

    def test_regroup_keeps_the_champions(self):
        month = self.plan()
        before = sorted(month.champions.values_list("game_account_id", flat=True))
        self.client.post("/pirataria/", {"action": "regroup", "server": "s1-br"})
        month = PiracyMonth.objects.get(status="open")
        self.assertEqual(sorted(month.champions.values_list("game_account_id", flat=True)), before)


class RotationTests(ChainChecks, Base):
    def test_the_passers_change_every_turn_and_the_chain_still_closes(self):
        big = [ga for ga in self.accounts if ga.account.label == "grande"]
        small = [ga for ga in self.accounts if ga.account.label == "pequeno"]
        month = self.plan(champions=[big[0], small[0]])
        first = {m.game_account_id: m.role for m in services.members_now(month)}
        champions = {k for k, v in first.items() if v == "champion"}
        seen, passers_by_week = set(), []
        for week in range(6):
            now = month.starts_at + timedelta(days=7 * week, minutes=1)
            roles = {m.game_account_id: m.role for m in services.members_now(month, now)}
            self.assertEqual({k for k, v in roles.items() if v == "champion"}, champions)
            self.assertEqual(sum(1 for v in roles.values() if v == "passer"), sum(1 for v in first.values() if v == "passer"))
            self.assert_chain_closes(month, now)
            passers_by_week.append({k for k, v in roles.items() if v == "passer"})
            seen |= passers_by_week[-1]
        self.assertNotEqual(passers_by_week[0], passers_by_week[1])         # the roles do swap
        self.assertGreater(len(seen), len(passers_by_week[0]))              # more accounts get to pass points on
        for week in passers_by_week:                                        # never a passer of its champion's lobby
            for pk in week:
                ga = next(ga for ga in self.accounts if ga.pk == pk)
                group = next(m.group for m in services.members_now(month) if m.game_account_id == pk)
                champion = next(m.game_account for m in services.members_now(month) if m.group == group and m.role == "champion")
                self.assertNotEqual(ga.account_id, champion.account_id)


class FinalPhaseTests(Base):
    def test_nobody_converts_in_the_final_phase(self):
        month = self.plan()
        origin = month.members.filter(role="origin").first().game_account
        job = self.jobs[origin.pk]
        job.inputs_json = json.dumps({"last_capture_points": 15000, "last_complete_crew_points": 1000,
                                      "piracy_final_phase_active": True})
        job.save(update_fields=["inputs_json"])
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")
        self.assertEqual((data["hold"], data["share_percent"], data["crew_floor"]), (True, 0, None))


class TurnResetTests(Base):
    def test_before_the_turn_the_raid_is_drawn_before_it(self):
        for snap in AccountSnapshot.objects.all():
            snap.base_snapshot["piracy_highscore"]["time_left"] = 5 * 3600     # the ranking turns in 5 hours
            snap.save()
        month = self.plan()
        champion = month.members.filter(role="champion").first().game_account
        self.set_strength(champion, points=0, crew=30000)
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        route = PiracyRoute.objects.get(status="planned")
        self.assertEqual(data["route_at"], int(route.due_from.timestamp()))   # the agent keeps the ship free for it
        self.assertLessEqual(route.due_from, month.turn_at - services.TURN_MARGIN)
        self.assertLessEqual(route.due_until, month.turn_at)
        # too close to the turn: nothing is drawn (the points go back to 0)
        PiracyRoute.objects.all().delete()
        PiracyMonth.objects.filter(pk=month.pk).update(turn_at=timezone.now() + timedelta(minutes=10))
        self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        self.assertFalse(PiracyRoute.objects.exists())


class JobCardTests(Base):
    """N-211 (22/11): the job card said "Converter tudo automaticamente" (the job's own mode) while the agent follows the
    plan: in the plan the card says the account's part, read only (nothing drawn)."""

    def test_card_shows_the_part_in_the_plan(self):
        from apps.jobs.views.jobs import JobListView

        month = self.plan()
        roles = services.month_roles(month)
        champion = next(ga for ga in self.accounts if roles.get(ga.pk) == "campeã")
        holder = next(ga for ga in self.accounts if roles.get(ga.pk) in ("origem", "passagem"))
        self.assertEqual(JobListView._piracy_plan(self.jobs[champion.pk])["text"], "converte tudo e junta os pontos")
        shown = JobListView._piracy_plan(self.jobs[holder.pk])
        self.assertEqual(shown["role"], roles[holder.pk])
        self.assertTrue(shown["text"].startswith(f"converte {month.share_percent}% a cada {month.share_every_hours}h"))
        self.assertFalse(PiracyRoute.objects.exists())                  # reading the card draws nothing
        services.close_month(month)
        self.assertIsNone(JobListView._piracy_plan(self.jobs[holder.pk]))
