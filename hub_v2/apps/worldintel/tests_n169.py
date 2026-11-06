"""N-169: the world map — its summary (targets by state, ring, first reading day, why cities were read again) and the
pages that serve it."""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.espionage.models import SpyReport
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job

from .models import WorldDump, WorldDumpCity, WorldDumpIsland
from .services.world_map import build_summary, island_detail, search_islands

OK = "Missão completada com sucesso!"
FAILED = "Missão falhada!"
RICH = {"wood": 150000, "wine": 90000}
POOR = {"wood": 1000}


class WorldMapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.node = Node.objects.create(name="agente", transport="https")
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Atenas")
        AccountSnapshot.objects.create(account=self.account, game_account=self.ga,
                                       cities=[{"id": 100, "x": 50, "y": 50}])
        self.dump = WorldDump.objects.create(account=self.account, game_account=self.ga, status="complete",
                                             captured_at=timezone.now())
        near = WorldDumpIsland.objects.create(dump=self.dump, island_id="1", x=52, y=50, resource_type=1, city_count=3)
        far = WorldDumpIsland.objects.create(dump=self.dump, island_id="2", x=50, y=62, resource_type=3, city_count=1)
        for pos, (cid, state, owner) in enumerate([("11", "inactive", "Alvo"), ("12", "inactive", "Rico"),
                                                   ("13", "", "Ativo")]):
            WorldDumpCity.objects.create(dump=self.dump, island=near, position=pos, game_city_id=cid, type="city",
                                         state=state, owner_name=owner, ally_tag="ABC" if owner == "Ativo" else "")
        WorldDumpCity.objects.create(dump=self.dump, island=near, position=5, type="buildplace")
        WorldDumpCity.objects.create(dump=self.dump, island=far, position=0, game_city_id="21", type="city",
                                     state="inactive", owner_name="Longe")
        self.root = Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=16,
                                       status="scheduled", inputs_json=json.dumps({"city_ids": "ga:100"}), timeout_sec=60)
        Job.objects.filter(pk=self.root.pk).update(created_at=timezone.now() - timedelta(days=10))
        self.root.refresh_from_db()
        self.spy = Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=15,
                                      status="finished", inputs_json="{}", timeout_sec=60, root_job_id=self.root.pk)
        self.n = 0

    def _report(self, city, days_ago, status=OK, data=POOR, valid_days=30):
        self.n += 1
        at = timezone.now() - timedelta(days=days_ago)
        report = SpyReport.objects.create(game_account=self.ga, report_id=str(self.n),
                                          target_city_id=city, mission_id=5, result_status=status, data_json=data,
                                          created_by_job=self.spy, expires_at=at + timedelta(days=valid_days))
        SpyReport.objects.filter(pk=report.pk).update(created_at=at)

    def test_summary_counts_targets_ring_first_day_and_why_read_again(self):
        self._report("11", 9, status=FAILED)
        self._report("11", 8)                          # again because the first failed
        self._report("12", 9, data=RICH, valid_days=4)  # rich: 4 days
        self._report("12", 3, data=RICH, valid_days=4)  # read again after it expired
        Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=15, status="running",
                           inputs_json=json.dumps({"target_city_id": "21"}), timeout_sec=60, root_job_id=self.root.pk)

        summary = build_summary(self.dump, self.root)

        rows = {row[0]: row for row in summary["islands"]}
        near, far = rows["1"], rows["2"]
        self.assertEqual(near[7:13], [2, 2, 0, 0, 0, 1])   # 2 targets, both valid, one of them rich
        self.assertEqual(far[7:13], [1, 0, 0, 1, 1, 0])    # never read, being spied now
        self.assertEqual((near[13], far[13]), (0, 2))      # rings of 5: distance 2 and 12
        self.assertEqual(near[14], [1, 2])                 # first good readings: day 1 and day 2 of the campaign
        totals = {k: sum(day[k] for day in summary["rereads"]) for k in summary["rereads"][0]}
        self.assertEqual(totals, {"new": 1, "failed": 1, "old_rule": 0, "rich": 1, "poor": 0, "early": 0})
        self.assertEqual(summary["houses"], [[50, 50, 1]])
        self.assertEqual(len(summary["days"]), 11)

    def test_island_detail_and_search(self):
        self._report("11", 9, status=FAILED)
        self._report("11", 8)
        detail = island_detail(self.dump, "1")
        self.assertEqual(detail["free"], 1)
        city = detail["cities"][0]
        self.assertEqual((city["read"], city["reads"], city["history"][1]["why"]), ("valid", 2, "anterior falhou"))
        self.assertEqual(detail["cities"][1]["read"], "never")
        self.assertEqual(search_islands(self.dump, "abc"), ["1"])
        self.assertEqual(search_islands(self.dump, "lon"), ["2"])

    def test_pages(self):
        user = get_user_model().objects.create_user(username="rodrigo", password="x", is_staff=True, is_superuser=True)
        self.client.force_login(user)
        page = self.client.get("/intel/map/")
        if page.status_code == 404:                       # the app may be mounted elsewhere
            from django.urls import reverse
            page = self.client.get(reverse("worldintel:map"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "wm-canvas")
        from django.urls import reverse
        data = self.client.get(reverse("worldintel:map-data") + f"?dump={self.dump.pk}").json()
        self.assertEqual(len(data["islands"]), 2)
        island = self.client.get(reverse("worldintel:map-island") + f"?dump={self.dump.pk}&island=1")
        self.assertContains(island, "Alvo")
        found = self.client.get(reverse("worldintel:map-search") + f"?dump={self.dump.pk}&q=Rico").json()
        self.assertEqual(found["islands"], ["1"])
