"""N-210/N-211: the piracy page and the month's champions — two champions of different lobbies that convert all; the
other accounts keep their points (converting a share now and then) and are raided towards the champions: only sure
wins (crew > target crew x 1.1 / 0.9), never inside a lobby, the same pair once in 7 days. Targets added by hand are
raided by the champions."""
import json
import random
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.orgs.models import Membership, Organization
from apps.orgs.scope import activate, deactivate

from . import flow, services
from . import tree as daytree
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

    def passers(self, month, now=None):
        return [m.game_account for m in services.members_now(month, now) if m.role == "passer"]

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
        self.assertEqual(month.members.filter(role="origin").count(), len(self.accounts) - 2)   # N-263: the day picks the passers
        for node in daytree.build(month).nodes.values():                 # N-263: every hop between two lobbies
            if node.parent is not None:
                self.assertNotEqual(node.ga.account_id, node.parent.ga.account_id)

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
        # N-263: an account with a passer day ahead converts by the crew plan instead of the daily share
        share = 0 if data.get("convert_total") is not None else 3
        self.assertEqual((data["convert_all"], data["hold"], data["share_percent"], data["route"]), (False, True, share, None))

    def test_the_champion_takes_only_sure_wins_never_its_lobby_and_once_a_week(self):
        month = self.plan(direct_points=0)
        champion_m = month.members.filter(role="champion").first()
        champion = champion_m.game_account
        group = [m.game_account for m in month.members.filter(group=champion_m.group).exclude(pk=champion_m.pk)]
        self.set_strength(champion, points=0, crew=30000)
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        route = PiracyRoute.objects.select_related("target").get(raider=champion, status="planned")
        # N-218: as soon as possible — the raid comes back in the same answer
        self.assertLessEqual(route.due_from, timezone.now())
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
        passer = month.members.filter(role="origin").first().game_account
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
        # the ranking turns inside the plan (no raid right before it); N-263: the roles change every day at turn_hour
        self.assertAlmostEqual((month.turn_at - timezone.now()).total_seconds(), 3600, delta=60)
        turn = services.next_rotation(month)
        self.assertEqual((turn.astimezone(flow.GAME_TZ).hour, turn.minute), (services.TURN_HOUR, 0))   # the game's clock
        self.assertEqual(services.rotation_index(month, turn + timedelta(minutes=1)),
                         services.rotation_index(month, turn - timedelta(minutes=1)) + 1)
        self.assertEqual(services.next_rotation(month, turn + timedelta(minutes=1)), turn + timedelta(days=1))
        origin = month.members.filter(role="origin").first().game_account
        # N-216: in the 15 minutes before it starts the account already keeps its points (no conversion, no raid)
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")
        self.assertEqual((data["hold"], data["convert_all"], data["share_percent"], data["route"]), (True, False, 0, None))


class EligibilityTests(Base):
    def test_accounts_without_fortress_or_job_stay_out(self):
        Job.objects.filter(game_account=self.accounts[2]).update(status="error")       # N-263: an old job is no job
        Job.objects.filter(game_account=self.accounts[0]).delete()
        AccountSnapshot.objects.filter(game_account=self.accounts[1]).update(cities=[])
        token = activate(self.org)
        try:
            able = services.eligible("s1-br")
        finally:
            deactivate(token)
        self.assertNotIn(self.accounts[0], able)
        self.assertNotIn(self.accounts[1], able)
        self.assertNotIn(self.accounts[2], able)
        month = self.plan()
        self.assertFalse(month.members.filter(game_account__in=self.accounts[:2]).exists())


class PageTests(Base):
    def test_the_page_plans_adds_targets_and_closes(self):
        self.assertContains(self.client.get("/pirataria/"), "Sem plano neste mês")
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
        month = self.plan(min_points=20000, direct_points=0)
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
        month = self.plan(direct_points=0)
        champion = month.members.filter(role="champion").first().game_account
        self.set_strength(champion, points=0, crew=30000)
        self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        route = PiracyRoute.objects.get(status="planned")
        page = self.client.get("/pirataria/?server=s1-br").content.decode()
        self.assertIn("Agora", page)                                       # N-255: the feed
        self.assertIn(f"{champion.name} ← {route.target.name}", page)
        self.assertIn("sai agora", page)


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
        month = self.plan(direct_points=0)
        champion = month.members.filter(role="champion").first().game_account
        self.set_strength(champion, points=0, crew=30000)
        data = self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        route = PiracyRoute.objects.get(status="planned")
        self.assertEqual(data["route"]["route_id"], str(route.pk))            # N-218: right away
        self.assertLessEqual(route.due_from, month.turn_at - services.TURN_MARGIN)
        self.assertLessEqual(route.due_until, month.turn_at)
        # too close to the turn: nothing is drawn (the points go back to 0)
        PiracyRoute.objects.all().delete()
        PiracyMonth.objects.filter(pk=month.pk).update(turn_at=timezone.now() + timedelta(minutes=10))
        self.agent(f"/api/agent/piracy/next/?game_account_id={champion.pk}")
        self.assertFalse(PiracyRoute.objects.exists())


class ConvertingBoardTests(Base):
    def test_passer_conversion_renders_in_local_time(self):
        month = self.plan()
        passer = month.members.filter(role="origin").first().game_account
        until = int((timezone.now() + timedelta(minutes=20)).timestamp())
        job = self.jobs[passer.pk]
        inputs = json.loads(job.inputs_json)
        inputs["_plan_convert_until"] = until
        job.inputs_json = json.dumps(inputs)
        job.save(update_fields=["inputs_json"])
        result = services.board(month, {})
        node = next(n for n in result["passers"] + result["origins"] if n["ga"].pk == passer.pk)
        expected = datetime.fromtimestamp(until, tz=timezone.get_current_timezone())
        self.assertEqual(node["text"], f"convertendo · pronta {expected:%H:%M}")
        response = self.client.get("/pirataria/", {"server": "s1-br"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "convertendo")


class JobCardTests(Base):
    """N-211 (22/11): the job card said "Converter tudo automaticamente" (the job's own mode) while the agent follows the
    plan: in the plan the card says the account's part, read only (nothing drawn)."""

    def test_card_shows_the_part_in_the_plan(self):
        from apps.jobs.views.jobs import JobListView

        month = self.plan()
        roles = services.month_roles(month)
        champion = next(ga for ga in self.accounts if roles.get(ga.pk) == "campeã")
        holder = next(ga for ga in self.accounts if roles.get(ga.pk) == "origem")
        self.assertEqual(JobListView._piracy_plan(self.jobs[champion.pk])["text"], "converte tudo e junta os pontos")
        shown = JobListView._piracy_plan(self.jobs[holder.pk])
        self.assertEqual(shown["role"], roles[holder.pk])
        plan = services.plan_for(holder)
        if plan["convert_total"] is None:                               # no passer day ahead: the daily share
            self.assertTrue(shown["text"].startswith(f"converte {month.share_percent - services.SHARE_JITTER_DOWN}-{month.share_percent + services.SHARE_JITTER_UP}% a cada {month.share_every_hours}h"))
        elif plan["convert_total"]:                                     # N-263: the crew plan
            self.assertIn("aos poucos até", shown["text"])
        else:
            self.assertIn("segura os pontos", shown["text"])
        self.assertFalse(PiracyRoute.objects.exists())                  # reading the card draws nothing
        services.close_month(month)
        self.assertIsNone(JobListView._piracy_plan(self.jobs[holder.pk]))


class RegroupGapTests(Base):
    """N-216 (23/11): LordDarkness converted 17.626 points in the 15 minutes between a regroup and the new plan."""

    def test_regroup_never_releases_the_accounts(self):
        month = self.plan()
        origin = month.members.filter(role="origin").first().game_account
        self.client.post("/pirataria/", {"action": "regroup", "server": "s1-br"})
        new = PiracyMonth.objects.get(status="open")
        self.assertGreater(new.starts_at, timezone.now())                    # the new plan waits its 15 minutes
        for ga in self.accounts:
            data = self.agent(f"/api/agent/piracy/next/?game_account_id={ga.pk}")
            self.assertTrue(data["hold"], ga.name)
            self.assertEqual((data.get("convert_all"), data.get("share_percent"), data["route"]), (False, 0, None))
        self.assertTrue(self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")["starts_at"])


class ChampionBalanceTests(Base):
    """N-217: both champions may take the same account; the one far ahead leaves it to the other (with a margin)."""

    def test_the_champion_far_ahead_leaves_the_target(self):
        big = [ga for ga in self.accounts if ga.account.label == "grande"]
        middle = [ga for ga in self.accounts if ga.account.label == "medio"]
        small = [ga for ga in self.accounts if ga.account.label == "pequeno"]
        month = self.plan(champions=[big[0], middle[0]], direct_points=0)
        for ga in self.accounts:
            self.set_strength(ga, points=0, crew=1000)
        target = small[0]                                                    # of a third lobby: both may take it
        self.set_strength(target, points=15000, crew=1000)
        self.set_strength(big[0], points=0, crew=30000)
        self.set_strength(middle[0], points=0, crew=30000)
        # within the margin it is free: the one that asks first takes it
        PiracyRoute.objects.create(month=month, group=1, week=1, stage=2, raider=big[0], target=middle[1],
                                   target_points=20000, status="done", due_from=timezone.now(), due_until=timezone.now())
        PiracyRoute.objects.create(month=month, group=1, week=1, stage=2, raider=middle[0], target=big[1],
                                   target_points=18000, status="done", due_from=timezone.now(), due_until=timezone.now())
        self.agent(f"/api/agent/piracy/next/?game_account_id={big[0].pk}")
        self.assertTrue(PiracyRoute.objects.filter(raider=big[0], target=target, status="planned").exists())
        PiracyRoute.objects.filter(raider=big[0], target=target).delete()
        # far ahead (60.000 against 18.000): it leaves the target to the other champion, which takes it
        PiracyRoute.objects.create(month=month, group=1, week=1, stage=2, raider=big[0], target=middle[2],
                                   target_points=40000, status="done", due_from=timezone.now(), due_until=timezone.now())
        self.agent(f"/api/agent/piracy/next/?game_account_id={big[0].pk}")
        self.assertFalse(PiracyRoute.objects.filter(raider=big[0], target=target, status="planned").exists())
        self.agent(f"/api/agent/piracy/next/?game_account_id={middle[0].pk}")
        self.assertTrue(PiracyRoute.objects.filter(raider=middle[0], target=target, status="planned").exists())
        self.assertEqual(services.champion_takes(month)[big[0].pk], 60000)


class CrewBonusTests(Base):
    """N-218: the held points add up to 1.400 of strength (from 7.000 points); raids as soon as possible; the list of
    accounts waiting says who wins them."""

    def test_the_bonus_and_what_to_convert(self):
        self.assertEqual((services.crew_bonus(0), services.crew_bonus(5769), services.crew_bonus(7000), services.crew_bonus(40000)),
                         (0, 0, 1400, 1400))                                # all or nothing (measured 25/11)
        # below the limit the bonus is already 0: converting there only adds
        self.assertEqual(services.convert_for(100, 5000, 400, None), 3000)
        # base 100 with 20.000 points (strength 1.500): 2.000 needs 500 crew, i.e. 5.000 points
        self.assertEqual(services.convert_for(100, 20000, 2000, None), 5000)
        # with 8.000 points only 1.000 are above the limit: 1.700 cannot be reached by converting
        self.assertIsNone(services.convert_for(100, 8000, 1700, None))
        # the base limit stops it
        self.assertIsNone(services.convert_for(100, 20000, 2000, 400))
        self.assertEqual(services.convert_for(100, 20000, 1400, None), 0)

    def test_waiting_accounts_say_who_wins_them(self):
        big = [ga for ga in self.accounts if ga.account.label == "grande"]
        middle = [ga for ga in self.accounts if ga.account.label == "medio"]
        month = self.plan(champions=[big[0], middle[0]])
        self.set_strength(middle[0], points=0, crew=30000)
        rows = {}
        for ga in self.accounts:
            s = services.strength(ga)
            rows[ga.pk] = {"ga": ga, "crew": s["crew"], "capture_points": s["points"],
                           "role": services.month_roles(month).get(ga.pk, "")}
        waiting = services.upcoming(month, rows)["waiting"]
        of_big = next(r for r in waiting if r["ga"] in big[1:])
        self.assertIn(f"{middle[0].name} vence (100%)", of_big["why"])
        self.assertNotIn("ninguém", of_big["why"])


class MonthRollTests(Base):
    """N-255: at the ranking turn the next month starts by itself, with champions that were not yet."""

    def test_the_next_month_starts_at_the_turn(self):
        month = self.plan(pair_days=4, rotate_days=3)
        before = set(month.champions.values_list("game_account_id", flat=True))
        PiracyMonth.objects.filter(pk=month.pk).update(ends_at=timezone.now() - timedelta(minutes=1))
        services.expire()
        month.refresh_from_db()
        self.assertEqual(month.status, "closed")
        new = PiracyMonth.objects.get(status="open")
        self.assertFalse(before & set(new.champions.values_list("game_account_id", flat=True)))
        self.assertEqual((new.pair_days, new.rotate_days), (4, 3))
        origin = new.members.filter(role="origin").first().game_account
        self.assertTrue(self.agent(f"/api/agent/piracy/next/?game_account_id={origin.pk}")["hold"])   # never out of a plan


class LostRaidTests(Base):
    """A raid sent and never answered by the agent does not stay "on its way" forever (HAVIT -> Executor, 23/11)."""

    def _route(self, month, raider, target, sent_hours_ago):
        now = timezone.now()
        return PiracyRoute.objects.create(month=month, group=1, week=1, stage=2, raider=raider, target=target,
                                          target_points=18867, due_from=now - timedelta(hours=sent_hours_ago),
                                          due_until=now + timedelta(hours=6), status=PiracyRoute.SENT,
                                          sent_at=now - timedelta(hours=sent_hours_ago))

    def test_an_old_sent_raid_is_closed_and_frees_its_target(self):
        month = self.plan()
        lost = self._route(month, self.accounts[0], self.accounts[7], sent_hours_ago=7)
        fresh = self._route(month, self.accounts[1], self.accounts[8], sent_hours_ago=1)
        services.expire()
        lost.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(lost.status, PiracyRoute.SKIPPED)
        self.assertIn("sem retorno", lost.message)
        self.assertEqual(fresh.status, PiracyRoute.SENT)               # a raid still flying is left alone

    def test_a_new_raid_sent_closes_the_older_one_of_the_same_ship(self):
        month = self.plan()
        lost = self._route(month, self.accounts[0], self.accounts[7], sent_hours_ago=2)
        now = timezone.now()
        new = PiracyRoute.objects.create(month=month, group=1, week=1, stage=2, raider=self.accounts[0],
                                         target=self.accounts[8], due_from=now, due_until=now + timedelta(hours=6))
        services.report(new, PiracyRoute.SENT, "assalto direto da fortaleza")
        lost.refresh_from_db()
        self.assertEqual(lost.status, PiracyRoute.SKIPPED)


class DailyFlowPiecesTests(Base):
    """N-263: what the page and the plan know of each account — the points a raid took, when a raid lands, raids of
    old roles dropped, and the page with the day's agenda and who takes whom."""

    def test_a_raided_account_holds_nothing_until_read_again(self):
        month = self.plan()
        target, raider = self.accounts[1], self.accounts[7]
        job = self.jobs[target.pk]
        read = int((timezone.now() - timedelta(hours=1)).timestamp())
        job.inputs_json = json.dumps({"last_capture_points": 25962, "last_complete_crew_points": 2210,
                                      "last_state_updated_at": read})
        job.save(update_fields=["inputs_json"])
        self.assertEqual(services.strength(target)["points"], 25962)
        PiracyRoute.objects.create(month=month, group=1, week=1, stage=1, raider=raider, target=target, target_points=25962,
                                   status="done", done_at=timezone.now(), due_from=timezone.now(), due_until=timezone.now())
        after = services.strength(target)
        self.assertEqual((after["points"], after["crew"]), (0, 2210 - services.CREW_BONUS_MAX))
        # a raid still on its way that has already landed counts too (no second raid on an empty account)
        PiracyRoute.objects.filter(target=target).delete()
        PiracyRoute.objects.create(month=month, group=1, week=1, stage=1, raider=raider, target=target, status="sent",
                                   sent_at=timezone.now() - timedelta(minutes=20), arrives_at=timezone.now() - timedelta(minutes=5),
                                   due_from=timezone.now(), due_until=timezone.now() + timedelta(hours=6))
        self.assertEqual(services.strength(target)["points"], 0)

    def test_the_raid_says_when_it_lands(self):
        month = self.plan()
        route = PiracyRoute.objects.create(month=month, group=1, week=1, stage=2, raider=self.accounts[0],
                                           target=self.accounts[7], due_from=timezone.now(),
                                           due_until=timezone.now() + timedelta(hours=6))
        self.agent(f"/api/agent/piracy/routes/{route.pk}/", "post", status="sent", message="x", seconds=1800)
        route.refresh_from_db()
        self.assertAlmostEqual((route.arrives_at - timezone.now()).total_seconds(), 1800, delta=30)



class TreeTests(Base):
    """N-263: the day's tree — every account hangs on a champion or on an account that beats it, of another lobby,
    the pair free; as many hops as needed, the strength growing towards the champions."""

    def lobbies(self):
        big = [ga for ga in self.accounts if ga.account.label == "grande"]
        middle = [ga for ga in self.accounts if ga.account.label == "medio"]
        small = [ga for ga in self.accounts if ga.account.label == "pequeno"]
        return big, middle, small

    def setup_chain(self, **kwargs):
        big, middle, small = self.lobbies()
        month = self.plan(champions=[big[0], middle[0]], **kwargs)
        for ga in self.accounts:
            self.set_strength(ga, points=0, crew=500)
        self.set_strength(big[0], points=0, crew=30000)
        self.set_strength(middle[0], points=0, crew=30000)
        self.set_strength(small[0], points=15000, crew=5000)
        self.set_strength(big[1], points=12000, crew=3000)
        self.set_strength(middle[1], points=12000, crew=1500)
        self.set_strength(big[2], points=12000, crew=1000)
        return month, big, middle, small

    def test_the_chain_grows_in_strength_and_crosses_lobbies(self):
        month, big, middle, small = self.setup_chain()
        t = daytree.build(month)
        ratio = services.ratio_for(month.min_win_percent)
        for node in t.nodes.values():
            if node.parent is None:
                continue
            self.assertNotEqual(node.ga.account_id, node.parent.ga.account_id)
            self.assertGreaterEqual(node.parent.power, node.at_raid * ratio)
        self.assertTrue(t.node(small[0]).parent.champion)               # the strongest goes straight to a champion
        self.assertEqual(t.node(big[1]).parent.ga, small[0])            # 3.000 hangs on the 5.000 of another lobby
        self.assertEqual(t.node(big[1]).depth, 2)
        self.assertEqual(t.node(small[0]).role, "passer")
        self.assertIn(small[0].pk, {m.game_account_id for m in services.members_now(month) if m.role == "passer"})

    def test_direct_points_go_straight_to_a_champion(self):
        month, big, middle, small = self.setup_chain(direct_points=20000)
        self.set_strength(big[1], points=25000, crew=3000)
        t = daytree.build(month)
        self.assertTrue(t.node(big[1]).parent.champion)
        self.assertEqual(t.node(big[1]).why, "direto")

    def test_the_hops_are_timed_back_from_the_delivery(self):
        month, big, middle, small = self.setup_chain()
        t = daytree.build(month)
        carrier = t.node(small[0])
        for child in carrier.children:
            self.assertLessEqual(child.send_at, carrier.send_at)        # its own ones are in before it is taken
            self.assertGreaterEqual(child.send_at, carrier.collect_from)
        self.assertEqual(carrier.taken_at, t.deliver)                    # the champion's last raid lands at the delivery

    def test_the_raider_takes_its_own_ones_when_their_time_comes(self):
        month, big, middle, small = self.setup_chain()
        t = daytree.build(month)
        child = next(c for c in t.node(small[0]).children if c.points >= month.min_points)
        early = self.agent(f"/api/agent/piracy/next/?game_account_id={small[0].pk}")
        if child.send_at > timezone.now():
            self.assertIsNone(early["route"])
            self.assertGreater(early["route_at"], int(timezone.now().timestamp()))
        data = services.next_for(small[0], now=child.send_at + timedelta(minutes=1))
        self.assertEqual(data["route"]["target_game_account_id"], str(child.ga.pk))

    def test_an_account_no_chain_reaches_gets_someone_converting_for_it(self):
        month, big, middle, small = self.setup_chain()
        self.set_strength(big[3], points=15000, crew=6000)              # too strong for every account of another lobby
        PiracyRoute.objects.create(month=month, group=1, week=1, stage=1, raider=middle[0], target=big[3], status="done",
                                   due_from=timezone.now(), due_until=timezone.now())   # its champion's pair is locked
        self.set_strength(small[0], points=40000, crew=5000)
        t = daytree.build(month)
        self.assertIn(big[3].pk, {n.pk for n in t.unreached})
        ratio = services.ratio_for(month.min_win_percent)
        target = int(6000 * ratio) + 1
        builder = t.builders[small[0].pk]
        self.assertEqual((builder["target"], builder["for"]), (target, big[3]))
        self.assertEqual(builder["convert"], services.convert_for(5000 - 1400, 40000, target, int(30000 / ratio) - 1400))
        plan = services.plan_for(small[0])
        self.assertEqual((plan["convert_total"], plan["share_percent"]), (builder["convert"], 0))

    def test_raids_the_tree_no_longer_has_are_dropped(self):
        month, big, middle, small = self.setup_chain()
        stale = PiracyRoute.objects.create(month=month, group=1, week=1, stage=1, raider=middle[2], target=big[1],
                                           due_from=timezone.now(), due_until=timezone.now() + timedelta(hours=6))
        services.drop_stale(month, daytree.build(month))
        stale.refresh_from_db()
        self.assertEqual((stale.status, stale.message), ("skipped", "a corrente mudou"))

    def test_the_page_shows_the_tree(self):
        month, big, middle, small = self.setup_chain()
        page = self.client.get("/pirataria/?server=s1-br").content.decode()
        self.assertIn("Corrente de hoje", page)
        self.assertIn("recolhe", page)
        self.assertIn(small[0].name, page)
