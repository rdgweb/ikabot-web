"""N-101: the history behind the game panel's KPI charts."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot, AccountSnapshotHistory
from apps.game.services.dashboard_cache import bump_dashboard_cache_version
from apps.game.services.kpi_history import PERIODS, build_history_map, clean_period, instants


class KpiHistoryTests(TestCase):
    def setUp(self):
        bump_dashboard_cache_version()
        self.now = timezone.now().replace(minute=0, second=0, microsecond=0)
        self.node = Node.objects.create(name="node-kpi", active=True)
        self.account = Account.objects.create(node=self.node, label="Lobby KPI", email="kpi@example.com", password_enc="x")
        self.accounts = [
            GameAccount.objects.create(
                account=self.account, lobby_account_id=100 + index, server_id=f"s{index}-br", server_language="br",
                server_number=index, name=f"Conta {index}",
            )
            for index in (1, 2, 3)
        ]

    def _snap(self, game_account, hours_ago, gold, **base):
        return AccountSnapshotHistory.objects.create(
            account=self.account, game_account=game_account, captured_at=self.now - timedelta(hours=hours_ago),
            base_snapshot={"gold": gold, **base}, cities=[{"wood": 10, "wine": 1, "marble": 2, "crystal": 3, "sulfur": 4}],
            military={"troops": {"Hoplita": 3, "Lanceiro": 2}, "fleet": {"Aríete a Vapor": 2}, "by_city": []},
        )

    def _sums(self, data, metric="gold"):
        count = len(next(iter(data.values())))
        return [sum(series[index][metric] for series in data.values()) for index in range(count)]

    def test_every_account_counts_at_every_point(self):
        # the real case of 12/10: in the last hour only some accounts had a new snapshot
        first, second, third = self.accounts
        for hours in (30, 20, 10, 2):
            self._snap(first, hours, 100)
            self._snap(second, hours, 200)
        self._snap(third, 30, 400)                  # this account had no snapshot for 30 hours
        self._snap(first, 0.5, 110)                 # and only this one has a fresh one
        data = build_history_map([str(a.pk) for a in self.accounts], "24h", now=self.now)

        points = len(instants("24h", self.now))
        self.assertEqual(points, 25)
        self.assertEqual({len(series) for series in data.values()}, {points})
        times = [[point["captured_at"] for point in series] for series in data.values()]
        self.assertTrue(all(row == times[0] for row in times))          # the same instants for everyone
        sums = self._sums(data)
        self.assertEqual(sums[:-1], [700] * (points - 1))               # never "6 of 10 accounts"
        self.assertEqual(sums[-1], 710)                                  # only the real change shows

    def test_an_account_counts_with_its_last_value_before_the_period(self):
        self._snap(self.accounts[0], 200, 999)
        data = build_history_map([str(self.accounts[0].pk)], "24h", now=self.now)
        self.assertEqual({point["gold"] for point in data[str(self.accounts[0].pk)]}, {999})

    def test_an_account_that_started_inside_the_period_does_not_make_a_step(self):
        self._snap(self.accounts[0], 5, 50)
        series = build_history_map([str(self.accounts[0].pk)], "24h", now=self.now)[str(self.accounts[0].pk)]
        self.assertEqual({point["gold"] for point in series}, {50})

    def test_an_account_without_history_is_left_out_and_the_metrics_are_the_panel_ones(self):
        self._snap(self.accounts[0], 1, 70, income=1000, upkeep=-300, scientists_upkeep=-100)
        data = build_history_map([str(a.pk) for a in self.accounts], "24h", now=self.now)
        self.assertEqual(list(data), [str(self.accounts[0].pk)])
        last = data[str(self.accounts[0].pk)][-1]
        self.assertEqual((last["gold"], last["cities"], last["resources"], last["troops"], last["ships"]), (70, 1, 20, 5, 2))
        self.assertEqual(last["income"], 600)                            # net income, as the top number (N-76)

    def test_periods(self):
        self.assertEqual({period: len(instants(period, self.now)) for period in PERIODS}, {"24h": 25, "7d": 29, "30d": 31})
        self.assertEqual((clean_period("7d"), clean_period("30D"), clean_period("x"), clean_period(None)), ("7d", "30d", "24h", "24h"))
        self._snap(self.accounts[0], 24 * 20, 10)
        self._snap(self.accounts[0], 24 * 3, 30)
        series = build_history_map([str(self.accounts[0].pk)], "30d", now=self.now)[str(self.accounts[0].pk)]
        self.assertEqual((series[0]["gold"], series[-1]["gold"]), (10, 30))

    def test_the_page_does_not_carry_the_history_and_the_endpoint_serves_it_by_period(self):
        AccountSnapshot.objects.create(account=self.account, game_account=self.accounts[0], base_snapshot={"gold": 70})
        self._snap(self.accounts[0], 1, 70)
        self.client.force_login(get_user_model().objects.create_user("kpi", password="x"))
        page = self.client.get("/game/")
        self.assertEqual(page.context_data["kpi_history"], {})          # built only when a KPI modal opens
        for period, points in (("24h", 25), ("7d", 29), ("30d", 31), ("bogus", 25)):
            answer = self.client.get("/game/history/", {"period": period}).json()
            self.assertEqual(answer["period"], period if period != "bogus" else "24h")
            self.assertEqual(len(answer["history"][str(self.accounts[0].pk)]), points)
        html = page.content.decode()
        self.assertIn("setHistoryPeriod(option[0])", html)               # the period switch is in the modals
        self.assertEqual(html.count("setHistoryPeriod(option[0])"), 6)
