"""N-64 / N-102: the first page of the hub."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.captcha.models import CaptchaChallenge
from apps.game.models import AccountSnapshot, AccountSnapshotHistory
from apps.jobs.models import Job, Workflow

from .services import overview as services
from .services.overview import (
    account_row, automation_stats, build_alerts, build_dashboard, compact, dotted, empire_cards, empire_trends,
    human_seconds, human_span, sparkline,
)


class FormattingTests(TestCase):
    def test_numbers_fit_a_card(self):
        self.assertEqual(dotted(1234567), "1.234.567")
        self.assertEqual(compact(9999), "9.999")
        self.assertEqual(compact(12500), "12,5 mil")
        self.assertEqual(compact(1234567), "1,23 mi")
        self.assertEqual(compact(-2500000), "-2,50 mi")
        self.assertEqual(compact(3100000000), "3,10 bi")
        self.assertEqual(compact(None), "0")

    def test_spans_read_like_a_person_says_them(self):
        self.assertEqual(human_span(10), "agora")
        self.assertEqual(human_span(60), "1 min")
        self.assertEqual(human_span(45 * 60), "45 min")
        self.assertEqual(human_span(2 * 3600 + 10 * 60), "2h 10min")
        self.assertEqual(human_span(3 * 3600), "3h")
        self.assertEqual(human_span(26 * 3600), "1 dia 2h")
        self.assertEqual(human_span(5 * 86400 + 3600), "5 dias")
        self.assertEqual(human_seconds(0.2), "< 1 s")
        self.assertEqual(human_seconds(8.4), "8 s")
        self.assertEqual(human_seconds(80), "1 min 20 s")
        self.assertEqual(human_seconds(1200), "20 min")

    def test_the_sparkline_uses_a_dot_as_decimal_separator_and_survives_a_flat_series(self):
        self.assertEqual(sparkline([5]), "")
        self.assertEqual(sparkline([1, 3], width=100, height=30), "0.0,28.0 100.0,2.0")
        self.assertEqual(sparkline([7, 7, 7], width=100, height=30), "0.0,15.0 50.0,15.0 100.0,15.0")


class DashboardDataTests(TestCase):
    def setUp(self):
        cache.delete(services.CACHE_KEY)
        cache.delete(services.HISTORY_CACHE_KEY)
        self.now = timezone.now()
        self.node = Node.objects.create(name="node-dash", active=True, agent_last_seen_at=self.now, agent_version="0.1.95")
        self.account = Account.objects.create(node=self.node, label="Lobby Dash", email="dash@example.com", password_enc="x")
        self.alpha = self._game_account(1, "Alpha")
        self.beta = self._game_account(2, "Beta")

    def _game_account(self, number, name):
        return GameAccount.objects.create(
            account=self.account, lobby_account_id=number, server_id=f"s{number}-br", server_language="br",
            server_number=number, name=name,
        )

    def _snapshot(self, game_account, *, gold=1000, income=50, cities=None, military=None, **base):
        return AccountSnapshot.objects.create(
            account=self.account, game_account=game_account,
            base_snapshot={"gold": gold, "income": income, "upkeep": 0, "scientists_upkeep": 0, **base},
            cities=cities if cities is not None else [self._city()],
            military=military if military is not None else {"troops": {"Hoplita": 30}, "fleet": {"Trirreme": 4}, "by_city": []},
        )

    def _city(self, name="Polis", wood=100, capacity=10000, buildings=None):
        return {"id": 1, "name": name, "wood": wood, "wine": 10, "marble": 20, "crystal": 30, "sulfur": 40,
                "warehouse_capacity": capacity, "buildings": buildings or []}

    def _job(self, status, *, action_code=100, minutes_ago=30, seconds=10, game_account=None, **extra):
        finished = self.now - timedelta(minutes=minutes_ago)
        fields = {"finished_at": finished, "started_at": finished - timedelta(seconds=seconds)} if status in ("finished", "error") else {}
        return Job.objects.create(
            account=self.account, game_account=game_account or self.alpha, node=self.node, action_code=action_code,
            status=status, inputs_json="{}", timeout_sec=1800, **{**fields, **extra},
        )

    # --- one account -------------------------------------------------------------------------------------------------

    def test_an_account_row_adds_up_what_the_snapshot_holds(self):
        end_at = int(self.now.timestamp()) + 1830
        snapshot = self._snapshot(
            self.alpha, gold=250000, player_score={"total": 9000},
            cities=[
                self._city("Atenas", wood=500, buildings=[
                    {"building": "townHall", "level": 7, "is_upgrading": True, "construction_end_at": end_at},
                    {"building": "academy", "level": 3, "is_upgrading": False},
                ]),
                self._city("Esparta", wood=9900),                         # 99% of the warehouse
                "lixo",
            ],
            military={"troops": {"Hoplita": 30, "Lanceiro": 5}, "fleet": {"Trirreme": 4}, "by_city": []},
        )
        row = account_row(self.alpha, snapshot, self.now)

        self.assertEqual((row["gold"], row["cities"], row["troops"], row["ships"], row["score"]), (250000, 2, 35, 4, 9000))
        self.assertEqual(row["resources"], 500 + 9900 + 2 * 100)
        self.assertEqual(row["full_cities"], 1)
        self.assertEqual((row["work"]["city"], row["work"]["level"], row["work"]["ends_in"], row["work"]["soon"]), ("Atenas", 8, "30 min", True))
        self.assertEqual((row["state"], row["state_label"], row["stale"]), ("ok", "Em ordem", False))
        self.assertEqual(row["gold_text"], "250,0 mil")

    def test_the_state_of_an_account_is_its_most_serious_condition(self):
        snapshot = self._snapshot(self.alpha, attack_alert_state={"hostile_count": 2}, vacation_state={"active": True})
        self.assertEqual(account_row(self.alpha, snapshot, self.now)["state_label"], "Sob ataque")

        snapshot.base_snapshot = {"gold": 5, "vacation_state": {"active": True}}
        self.assertEqual(account_row(self.alpha, snapshot, self.now, manual_control=True)["state_label"], "Controle manual")
        self.assertEqual(account_row(self.alpha, snapshot, self.now)["state_label"], "Férias")

        self.alpha.login_blocked_until = self.now + timedelta(hours=1)
        self.assertEqual(account_row(self.alpha, snapshot, self.now)["state_label"], "Login bloqueado")
        self.alpha.login_blocked_until = self.now - timedelta(hours=1)          # a block that is over is not a block
        self.assertEqual(account_row(self.alpha, snapshot, self.now)["state_label"], "Férias")

    def test_an_account_that_is_not_being_read_says_so(self):
        self.assertEqual(account_row(self.alpha, None, self.now)["state_label"], "Sem leitura")
        snapshot = self._snapshot(self.alpha)
        AccountSnapshot.objects.filter(pk=snapshot.pk).update(updated_at=self.now - timedelta(hours=5))
        snapshot.refresh_from_db()
        row = account_row(self.alpha, snapshot, self.now)
        self.assertEqual((row["state_label"], row["stale"], row["snapshot_age"]), ("Sem leitura", True, "5h"))

    def test_negative_income_only_alarms_when_the_gold_is_about_to_end(self):
        # the real case: 55 million of gold losing 6 thousand an hour is a choice, not an emergency
        rich = account_row(self.alpha, self._snapshot(self.alpha, gold=55000000, income=-6000), self.now)
        self.assertEqual((rich["state_label"], rich["gold_runway"], rich["income_text"]), ("Em ordem", "", "-6.000"))
        soon = account_row(self.beta, self._snapshot(self.beta, gold=240000, income=-6000), self.now)
        self.assertEqual((soon["state"], soon["state_label"], soon["gold_runway"]), ("warn", "Ouro acabando", "1 dia 16h"))
        soon["gold"], soon["gold_runway"], soon["gold_runway_bad"] = 30000, "5h", True

        alerts = {alert["text"]: alert for alert in build_alerts([rich, soon], [], self.now)}
        self.assertEqual(alerts["Beta: o ouro acaba em 5h"]["level"], "bad")
        self.assertEqual(alerts["1 conta(s) com renda negativa"]["level"], "info")
        self.assertIn("Alpha (-6.000/h)", alerts["1 conta(s) com renda negativa"]["detail"])

    # --- what needs attention -------------------------------------------------------------------------------------

    def test_nothing_wrong_means_no_alert(self):
        self._snapshot(self.alpha)
        self._snapshot(self.beta)
        data = build_dashboard()
        self.assertEqual(data["alerts"], [])
        self.assertEqual([row["state"] for row in data["rows"]], ["ok", "ok"])

    def test_alerts_come_most_serious_first(self):
        self._snapshot(self.alpha, attack_alert_state={"hostile_count": 1})
        self._snapshot(self.beta, income=-900)
        Node.objects.create(name="node-vazio", active=True)                       # offline, but nothing depends on it
        offline = Node.objects.create(name="node-fora", active=True, agent_last_seen_at=self.now - timedelta(hours=2))
        other = Account.objects.create(node=offline, label="Lobby Fora", email="fora@example.com", password_enc="x")
        gamma = GameAccount.objects.create(account=other, lobby_account_id=9, server_id="s9-br", server_language="br", server_number=9, name="Gamma")
        self._snapshot_for(gamma, other)
        CaptchaChallenge.objects.create(status="pending", expires_at=self.now + timedelta(minutes=5), **self._captcha_fields())
        CaptchaChallenge.objects.create(status="pending", expires_at=self.now - timedelta(minutes=5), **self._captcha_fields())

        alerts = build_dashboard()["alerts"]
        levels = [alert["level"] for alert in alerts]
        texts = " | ".join(alert["text"] for alert in alerts)

        self.assertEqual(levels, sorted(levels, key=["bad", "warn", "info"].index))
        self.assertIn("Alpha está sob ataque", texts)
        self.assertIn("1 captcha(s) esperando resposta", texts)                  # the expired one does not count
        self.assertIn("Agente do nó node-fora está offline", texts)
        self.assertNotIn("node-vazio", texts)
        self.assertIn("Beta: o ouro acaba em 1h 6min", texts)                     # 1000 of gold, losing 900 an hour
        self.assertTrue(all(alert["href"] for alert in alerts))

    def _snapshot_for(self, game_account, account):
        return AccountSnapshot.objects.create(account=account, game_account=game_account, base_snapshot={"gold": 1}, cities=[], military={})

    def _captcha_fields(self):
        fields = {}
        names = {field.name for field in CaptchaChallenge._meta.get_fields()}
        if "game_account" in names:
            fields["game_account"] = self.alpha
        if "node" in names:
            fields["node"] = self.node
        return fields

    def test_proxy_shortage_and_full_warehouses_are_warnings(self):
        rows = [account_row(self.alpha, self._snapshot(self.alpha, cities=[self._city(wood=10000)]), self.now)]
        alerts = build_alerts(rows, [], self.now, proxy_result={"without_proxy": ["conta Lobby Dash"]})
        self.assertEqual(
            [(alert["level"], alert["text"]) for alert in alerts],
            [("warn", "Sem proxy livre para: conta Lobby Dash"), ("warn", "1 cidade(s) com armazém cheio")],
        )

    # --- the empire -------------------------------------------------------------------------------------------------

    def test_the_cards_show_the_total_and_how_it_moved_in_24_hours(self):
        rows = [
            account_row(self.alpha, self._snapshot(self.alpha, gold=1500000, income=300), self.now),
            account_row(self.beta, self._snapshot(self.beta, gold=500000, income=-100), self.now),
        ]
        history = {
            rows[0]["id"]: [{"gold": 1000000, "income": 300, "cities": 1, "resources": 200, "troops": 30, "ships": 4},
                            {"gold": 1500000, "income": 300, "cities": 1, "resources": 200, "troops": 30, "ships": 4}],
            rows[1]["id"]: [{"gold": 700000, "income": -100, "cities": 1, "resources": 200, "troops": 30, "ships": 4},
                            {"gold": 500000, "income": -100, "cities": 1, "resources": 200, "troops": 10, "ships": 4}],
        }
        trends = empire_trends(rows, history)
        self.assertEqual((trends["gold"]["delta"], trends["troops"]["delta"], trends["cities"]["delta"]), (300000, -20, 0))

        cards = {card["key"]: card for card in empire_cards(rows, history)}
        self.assertEqual((cards["gold"]["value"], cards["gold"]["full"], cards["gold"]["delta_text"]), ("2,00 mi", "2.000.000", "▲ +300,0 mil em 24h"))
        self.assertEqual((cards["troops"]["delta_text"], cards["troops"]["delta_color"]), ("▼ −20 em 24h", services.BAD))
        self.assertEqual(cards["cities"]["delta_text"], "estável em 24h")
        self.assertEqual((cards["income"]["value"], cards["income"]["note"], cards["income"]["note_bad"]), ("+200", "1 conta(s) no negativo", True))
        self.assertEqual((cards["score"]["delta_text"], cards["score"]["points"]), ("", ""))       # the history has no score
        self.assertTrue(cards["gold"]["points"])

    def test_without_history_the_cards_only_lose_the_variation(self):
        rows = [account_row(self.alpha, self._snapshot(self.alpha), self.now)]
        self.assertEqual(empire_trends(rows, {}), {})
        self.assertEqual({card["delta_text"] for card in empire_cards(rows, None)}, {""})

    def test_the_real_history_feeds_the_cards(self):
        self._snapshot(self.alpha, gold=900)
        for hours_ago, gold in ((30, 400), (0.2, 900)):
            AccountSnapshotHistory.objects.create(
                account=self.account, game_account=self.alpha, captured_at=self.now - timedelta(hours=hours_ago),
                base_snapshot={"gold": gold}, cities=[self._city()], military={"troops": {"Hoplita": 30}, "fleet": {}},
            )
        cards = {card["key"]: card for card in build_dashboard()["cards"]}
        self.assertEqual(cards["gold"]["delta_text"], "▲ +500 em 24h")

    # --- automation -------------------------------------------------------------------------------------------------

    def test_automation_counts_the_jobs_that_ended_in_the_last_24_hours(self):
        for _ in range(3):
            self._job("finished", action_code=100, seconds=10)
        self._job("error", action_code=100, seconds=30)
        self._job("finished", action_code=2, minutes_ago=90, seconds=120)
        self._job("finished", action_code=2, minutes_ago=60 * 30)                 # ended before the window
        self._job("cancelled", action_code=2)
        self._job("scheduled", action_code=2, scheduled_for=self.now + timedelta(minutes=10))

        stats = automation_stats(self.now)

        self.assertEqual((stats["total"], stats["ok"], stats["errors"], stats["success_pct"]), (5, 4, 1, "80,0"))
        self.assertEqual(len(stats["hours"]), 24)
        self.assertEqual(sum(hour["total"] for hour in stats["hours"]), 5)
        self.assertEqual(stats["hours"][-1]["label"], timezone.localtime(self.now).strftime("%Hh"))
        top = stats["top_actions"][0]
        self.assertEqual((top["code"], top["total"], top["errors"], top["ok_pct"], top["average"], top["share_pct"]), (100, 4, 1, 75, "15 s", "100"))
        self.assertEqual([entry["code"] for entry in stats["failing_actions"]], [100])
        self.assertEqual(stats["average"], "36 s")                               # (3*10 + 30 + 120) / 5
        self.assertNotIn(",", stats["hours"][-1]["ok_pct"])                      # goes into a style attribute

    def test_no_jobs_is_not_an_error(self):
        stats = automation_stats(self.now)
        self.assertEqual((stats["total"], stats["success_pct"], stats["average"], stats["top_actions"]), (0, "", "", []))

    def test_what_comes_next_lists_works_and_jobs_in_order(self):
        base = int(self.now.timestamp())
        self._snapshot(self.alpha, cities=[self._city("Atenas", buildings=[
            {"building": "townHall", "level": 3, "is_upgrading": True, "construction_end_at": base + 7200}])])
        self._snapshot(self.beta, cities=[self._city("Tebas", buildings=[
            {"building": "academy", "level": 1, "is_upgrading": True, "construction_end_at": base + 600}])])
        later = self._job("scheduled", scheduled_for=self.now + timedelta(hours=2))
        sooner = self._job("scheduled", scheduled_for=self.now + timedelta(minutes=5), game_account=self.beta)
        running = self._job("running", started_at=self.now - timedelta(minutes=3))

        coming = build_dashboard(now=self.now)["coming"]

        self.assertEqual([work["city"] for work in coming["works"]], ["Tebas", "Atenas"])
        self.assertEqual([job["id"] for job in coming["scheduled"]], [str(sooner.pk), str(later.pk)])
        self.assertEqual((coming["scheduled"][0]["account"], coming["scheduled"][0]["eta"]), ("Beta", "5 min"))
        self.assertEqual([(job["id"], job["since"]) for job in coming["running"]], [(str(running.pk), "3 min")])
        self.assertEqual((coming["works_total"], coming["scheduled_total"], coming["running_total"], coming["idle_accounts"]), (2, 2, 1, []))

    def test_workflows_with_a_problem_follow_the_rule_of_the_job_queue(self):
        self._snapshot(self.alpha)
        self._snapshot(self.beta)
        healthy = Workflow.objects.create(account=self.account, game_account=self.alpha, node=self.node, workflow_type="donate_loop", status="active")
        self._job("scheduled", workflow=healthy, scheduled_for=self.now + timedelta(minutes=5))
        # stored as active, but its latest job ended in error: that is a problem (N-89)
        broken = Workflow.objects.create(
            account=self.account, game_account=self.beta, node=self.node, workflow_type="construction_plan", status="active",
            last_error_summary="Cidade não encontrada no snapshot", last_error_at=self.now - timedelta(minutes=20),
        )
        self._job("error", workflow=broken)
        Workflow.objects.create(account=self.account, node=self.node, workflow_type="donate_loop", status="problem", archived_at=self.now)

        data = build_dashboard(now=self.now)

        self.assertEqual((data["workflows"]["active"], data["workflows"]["problem_total"]), (1, 1))
        problem = data["workflows"]["problems"][0]
        self.assertEqual((problem["id"], problem["account"], problem["error"], problem["ago"]),
                         (str(broken.pk), "Beta", "Cidade não encontrada no snapshot", "20 min"))
        self.assertIn("1 workflow(s) com problema", [alert["text"] for alert in data["alerts"]])

    def test_the_system_footer_knows_which_agent_is_behind(self):
        Node.objects.create(name="node-antigo", active=True, agent_last_seen_at=self.now, agent_version="0.1.10")
        Node.objects.create(name="node-desligado", active=False)
        with self.settings(EXPECTED_AGENT_VERSION="0.1.95"):
            data = build_dashboard()
        nodes = {node["name"]: node for node in data["nodes"]}
        self.assertEqual(sorted(nodes), ["node-antigo", "node-dash"])
        self.assertEqual((nodes["node-dash"]["online"], nodes["node-dash"]["outdated"], nodes["node-dash"]["accounts"]), (True, False, 2))
        self.assertTrue(nodes["node-antigo"]["outdated"])
        self.assertIn("Agente do nó node-antigo tem atualização disponível", [alert["text"] for alert in data["alerts"]])
        self.assertEqual((data["nodes_online"], data["expected_agent_version"]), (2, "0.1.95"))


class DashboardPageTests(TestCase):
    def setUp(self):
        cache.delete(services.CACHE_KEY)
        cache.delete(services.HISTORY_CACHE_KEY)
        self.user = get_user_model().objects.create_user(username="dash-user", email="dash@example.com", password="secret123")
        self.node = Node.objects.create(name="node-pagina", active=True, agent_last_seen_at=timezone.now(), agent_version="0.1.95")
        self.account = Account.objects.create(node=self.node, label="Lobby", email="p@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=1, server_id="s1-br", server_language="br", server_number=1, name="Alpha",
        )

    def test_the_page_shows_every_block(self):
        AccountSnapshot.objects.create(
            account=self.account, game_account=self.ga, base_snapshot={"gold": 1234567, "income": 10},
            cities=[{"id": 1, "name": "Atenas", "wood": 50, "warehouse_capacity": 1000, "buildings": []}], military={},
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:index"))

        self.assertEqual(response.status_code, 200)
        for block in ("dash-attention", "dash-empire", "dash-coming", "dash-automation", "dash-accounts", "dash-system"):
            self.assertContains(response, f'id="{block}"')
        self.assertContains(response, "Tudo em ordem.")
        self.assertContains(response, "1,23 mi")
        self.assertContains(response, "node-pagina")
        self.assertContains(response, reverse("dashboard:live"))
        self.assertContains(response, "<style>")
        # what left the page: the development queue and the telemetry of the installation
        self.assertNotContains(response, "Notas na fila")
        self.assertNotContains(response, "Telemetria")

    def test_the_page_opens_with_nothing_registered(self):
        GameAccount.objects.all().delete()
        Node.objects.all().delete()
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nenhuma conta ativa.")
        self.assertContains(response, "Nenhum nó cadastrado")

    def test_the_page_never_calls_out_to_another_service(self):
        self.client.force_login(self.user)
        with patch("requests.sessions.Session.request", side_effect=AssertionError("no HTTP from the dashboard")):
            self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)

    def test_the_refresh_brings_only_the_blocks(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:live"), HTTP_HX_REQUEST="true")
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body.lstrip().startswith("<div id=\"dash-live\">") or 'id="dash-live"' in body[:400])
        self.assertNotIn("<html", body)
        self.assertNotIn("<style>", body)
        self.assertIn('hx-target="#dash-live"', body)
        self.assertIn('hx-push-url="false"', body)

    def test_the_data_is_built_once_for_requests_close_together(self):
        self.client.force_login(self.user)
        with patch("apps.dashboard.services.overview.build_dashboard", wraps=build_dashboard) as build:
            self.client.get(reverse("dashboard:index"))
            self.client.get(reverse("dashboard:live"))
        self.assertEqual(build.call_count, 1)

    def test_a_session_that_ended_sends_the_browser_to_the_login(self):
        response = self.client.get(reverse("dashboard:live"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], reverse("login"))
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 302)
