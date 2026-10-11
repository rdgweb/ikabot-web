"""N-96: the hub keeps the proxy list true by itself, and a proxy failure no longer blocks a login for hours."""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.api.agent import PROXY_BACKOFF_MAX_HOURS, PROXY_REPAIR_ATTEMPTS, PROXY_REPAIR_WAIT, _proxy_block_duration
from apps.proxy import maintenance
from apps.proxy.models import AccountProxyReservation, ProxyProfile
from apps.settings_app.models import AppSetting

REFUSED = (
    "Falha ao buscar configuração do lobby: HTTPSConnectionPool(host='lobby.ikariam.gameforge.com', port=443): Max retries exceeded "
    "with url: /config/configuration.js (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 407 Proxy Authentication Required')))"
)
SLOW_LOBBY = "Falha ao buscar configuração do lobby: HTTPSConnectionPool(host='lobby.ikariam.gameforge.com', port=443): Read timed out. (read timeout=15)"
SYNC_OK = {"success": True, "total": 0, "created": 0, "updated": 0, "deactivated": 0, "error": ""}


def _inline(work):
    work()


class _Base(TestCase):
    def setUp(self):
        cache.delete(maintenance.LOCK_KEY)
        self.node = Node.objects.create(name="node-px", active=True, agent_last_seen_at=timezone.now())
        self.account = Account.objects.create(node=self.node, label="Lobby PX", email="px@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=96, server_id="s9-br", server_language="br", server_number=9, name="Conta PX",
        )
        self.dead = set()          # addresses that refuse the connection when probed
        self.latency = {}          # address -> how fast it answers when probed
        self._n = 0

    def _proxy(self, *, active=True, ok=True, latency=100, reserve=None, node=None):
        self._n += 1
        proxy = ProxyProfile.objects.create(
            address=f"10.9.0.{self._n}", port=8000 + self._n, active=active, last_test_status=ok, last_test_latency_ms=latency,
            assigned_node=node,
        )
        self.latency[proxy.address] = latency
        if reserve is not None:
            AccountProxyReservation.objects.create(account=reserve, proxy_profile=proxy)
        return proxy

    def _probe(self, proxy_url, timeout=8):
        if any(address in proxy_url for address in self.dead):
            return {"success": False, "ip": "", "latency_ms": 0, "error": "proxy recusou (407)"}
        latency = next((value for address, value in self.latency.items() if f"{address}:" in proxy_url), 120)
        return {"success": True, "ip": "200.1.1.1", "latency_ms": latency, "error": ""}

    def _run(self, sync=None):
        with patch("apps.proxy.services.sync_webshare", return_value=sync or SYNC_OK), patch.object(maintenance, "probe_proxy", side_effect=self._probe):
            return maintenance.run_proxy_maintenance(reason="teste")

    def _reserved(self, account=None):
        return [r.proxy_profile for r in AccountProxyReservation.objects.filter(account=account or self.account).order_by("created_at", "proxy_profile__address")]


class ProxyMaintenanceTests(_Base):
    def test_proxies_that_left_the_list_are_replaced_and_the_login_block_is_lifted(self):
        # what happened on 09-10/10: the provider replaced all three proxies of one account
        gone = [self._proxy(active=False, ok=None, reserve=self.account) for _ in range(3)]
        fresh = [self._proxy(latency=latency) for latency in (300, 100, 200, 400)]
        self.ga.login_blocked_until = timezone.now() + timedelta(hours=11)
        self.ga.login_block_backoff_hours = 11
        self.ga.login_block_reason = REFUSED
        self.ga.save()

        report = self._run()

        reserved = self._reserved()
        self.assertEqual({proxy.pk for proxy in reserved}, {fresh[1].pk, fresh[2].pk, fresh[0].pk})       # the three fastest
        self.assertFalse(AccountProxyReservation.objects.filter(proxy_profile__in=gone).exists())
        self.assertEqual(report["accounts_replaced"], [{"account": "Lobby PX", "removed": [p.pk for p in gone], "added": [fresh[1].pk, fresh[2].pk, fresh[0].pk]}])
        self.ga.refresh_from_db()
        self.assertEqual((self.ga.login_blocked_until, self.ga.login_block_backoff_hours, self.ga.login_block_reason), (None, 0, ""))
        self.assertEqual(report["blocks_cleared"], ["Conta PX"])
        self.assertEqual(report["without_proxy"], [])

    def test_a_listed_proxy_that_fails_keeps_its_reservation_while_another_works(self):
        works = self._proxy(reserve=self.account)
        failing = self._proxy(reserve=self.account)
        spare = self._proxy()
        self.dead.add(failing.address)

        report = self._run()

        self.assertEqual({p.pk for p in self._reserved()}, {works.pk, failing.pk})        # stable addresses: nothing swapped
        failing.refresh_from_db()
        self.assertIs(failing.last_test_status, False)                                    # ...but it is no longer handed out
        self.assertEqual(report["accounts_replaced"], [])
        self.assertFalse(AccountProxyReservation.objects.filter(proxy_profile=spare).exists())

    def test_an_account_left_with_nothing_usable_gets_its_failing_proxies_replaced(self):
        failing = [self._proxy(reserve=self.account) for _ in range(2)]
        spares = [self._proxy(latency=50), self._proxy(latency=60), self._proxy(latency=70)]
        self.dead.update(proxy.address for proxy in failing)

        self._run()

        # none of its own worked, so they go; it is topped up to the usual three, as a first login would do
        self.assertEqual({p.pk for p in self._reserved()}, {proxy.pk for proxy in spares})

    def test_healthy_reservations_are_left_alone(self):
        mine = [self._proxy(reserve=self.account) for _ in range(3)]
        self._proxy()

        report = self._run()

        self.assertEqual([p.pk for p in self._reserved()], [p.pk for p in mine])
        self.assertEqual((report["tested"], report["passed"], report["accounts_replaced"], report["nodes_replaced"]), (4, 4, [], []))

    def test_a_block_put_by_the_game_is_not_lifted(self):
        self._proxy(reserve=self.account)
        until = timezone.now() + timedelta(hours=3)
        self.ga.login_blocked_until = until
        self.ga.login_block_backoff_hours = 3
        self.ga.login_block_reason = "loginLink falhou: status=400 body=[]"
        self.ga.save()

        report = self._run()

        self.ga.refresh_from_db()
        self.assertEqual((self.ga.login_blocked_until, self.ga.login_block_backoff_hours), (until, 3))
        self.assertEqual(report["blocks_cleared"], [])

    def test_a_node_whose_proxy_is_gone_gets_the_fastest_free_one(self):
        old = self._proxy(active=False, ok=None, node=self.node)
        self.node.proxy = ""                       # what the sync leaves behind
        self.node.save(update_fields=["proxy"])
        taken = self._proxy(latency=10, reserve=self.account)          # an account's proxy is never given to a node
        slow, fast = self._proxy(latency=900), self._proxy(latency=90)

        report = self._run()

        self.node.refresh_from_db()
        fast.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual((fast.assigned_node_id, old.assigned_node_id), (self.node.pk, None))
        self.assertEqual(self.node.proxy, fast.proxy_url)
        self.assertEqual((self.node.external_ip, self.node.ip_source), ("200.1.1.1", "proxy_test"))
        self.assertEqual(report["nodes_replaced"], [{"node": "node-px", "from": old.pk, "to": fast.pk}])
        self.assertEqual({p.pk for p in self._reserved()}, {taken.pk})
        self.assertIsNone(slow.assigned_node_id)

    def test_nodes_without_a_live_agent_or_without_a_proxy_are_left_alone(self):
        asleep = Node.objects.create(name="node-off", active=True, agent_last_seen_at=timezone.now() - timedelta(days=30))
        dead = self._proxy(active=False, ok=None, node=asleep)
        direct = Node.objects.create(name="node-direct", active=True, agent_last_seen_at=timezone.now())
        spare = self._proxy()

        report = self._run()

        dead.refresh_from_db()
        spare.refresh_from_db()
        direct.refresh_from_db()
        self.assertEqual((dead.assigned_node_id, spare.assigned_node_id, direct.proxy), (asleep.pk, None, ""))
        self.assertEqual(report["nodes_replaced"], [])

    def test_no_free_proxy_is_reported_not_hidden(self):
        self._proxy(active=False, ok=None, reserve=self.account)
        self._proxy(active=False, ok=None, node=self.node)

        report = self._run()

        self.assertEqual(sorted(report["without_proxy"]), ["conta Lobby PX", "no node-px"])
        self.assertEqual(self._reserved(), [])

    def test_provider_out_of_reach_still_tests_and_heals_with_what_is_known(self):
        self._proxy(active=False, ok=None, reserve=self.account)
        spare = self._proxy()

        with patch("apps.proxy.services.sync_webshare", side_effect=RuntimeError("webshare fora")), patch.object(maintenance, "probe_proxy", side_effect=self._probe):
            report = maintenance.run_proxy_maintenance(reason="teste")

        self.assertEqual(report["sync"], {"success": False, "error": "RuntimeError"})
        self.assertEqual([p.pk for p in self._reserved()], [spare.pk])

    def test_result_is_kept_for_the_record_without_addresses(self):
        gone = self._proxy(active=False, ok=None, reserve=self.account)
        spare = self._proxy()

        self._run()

        stored = AppSetting.objects.get(key=maintenance.LAST_RESULT_SETTING).value
        self.assertEqual(maintenance.last_result()["accounts_replaced"][0]["added"], [spare.pk])
        self.assertNotIn(gone.address, stored)
        self.assertNotIn(spare.address, stored)
        self.assertIsNotNone(maintenance.last_run())


class ProxyMaintenanceTriggerTests(_Base):
    def test_one_run_at_a_time_and_not_too_often(self):
        with patch.object(maintenance, "_spawn") as spawn:
            self.assertTrue(maintenance.trigger_proxy_maintenance(reason="a"))
            self.assertFalse(maintenance.trigger_proxy_maintenance(reason="b"))        # the first one still holds the lock
            self.assertEqual(spawn.call_count, 1)
        cache.delete(maintenance.LOCK_KEY)
        AppSetting.objects.update_or_create(key=maintenance.LAST_RUN_SETTING, defaults={"value": (timezone.now() - timedelta(minutes=4)).isoformat()})
        with patch.object(maintenance, "_spawn") as spawn:
            self.assertFalse(maintenance.trigger_proxy_maintenance(reason="c"))        # ran four minutes ago
            self.assertFalse(maintenance.trigger_if_stale())
            spawn.assert_not_called()
        AppSetting.objects.filter(key=maintenance.LAST_RUN_SETTING).update(value=(timezone.now() - timedelta(minutes=11)).isoformat())
        with patch.object(maintenance, "_spawn") as spawn:
            self.assertFalse(maintenance.trigger_if_stale())                           # not old enough for the periodic run
            self.assertTrue(maintenance.trigger_proxy_maintenance(reason="d"))         # but a reported failure may start one
            self.assertEqual(spawn.call_count, 1)

    def test_a_run_that_breaks_releases_the_lock(self):
        with patch.object(maintenance, "_spawn", side_effect=_inline), patch.object(maintenance, "run_proxy_maintenance", side_effect=RuntimeError("boom")):
            with self.assertLogs("apps.proxy.maintenance", level="ERROR"):
                self.assertTrue(maintenance.trigger_proxy_maintenance(reason="x"))
        self.assertIsNone(cache.get(maintenance.LOCK_KEY))

    def test_which_failures_are_the_proxys_doing(self):
        self.assertTrue(maintenance.is_proxy_side_failure(REFUSED))
        self.assertTrue(maintenance.is_proxy_side_failure("ProxyError('Unable to connect to proxy', ConnectTimeoutError(...))"))
        for other in (SLOW_LOBBY, "loginLink falhou: status=400 body=[]", "", None, "Captcha necessário no lobby"):
            self.assertFalse(maintenance.is_proxy_side_failure(other), other)


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class LoginBlockAfterProxyFailureTests(_Base):
    def _report(self, mode="record_proxy", reason=REFUSED):
        response = self.client.post(
            f"/api/agent/game-accounts/{self.ga.pk}/login-cooldown/", {"mode": mode, "reason": reason},
            content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.ga.refresh_from_db()
        return response.json()

    def _blocked_for(self):
        return self.ga.login_blocked_until - timezone.now()

    def test_block_durations(self):
        self.assertEqual([_proxy_block_duration(level, proxy_side=True) for level in (1, 2, 3)], [PROXY_REPAIR_WAIT] * 3)
        self.assertEqual([_proxy_block_duration(level, proxy_side=True) for level in (4, 5, 6, 30)], [timedelta(hours=h) for h in (1, 2, 2, 2)])
        self.assertEqual([_proxy_block_duration(level, proxy_side=False) for level in (1, 2, 3, 11)], [timedelta(hours=h) for h in (1, 2, 2, 2)])
        self.assertEqual((PROXY_REPAIR_ATTEMPTS, PROXY_BACKOFF_MAX_HOURS, PROXY_REPAIR_WAIT), (3, 2, timedelta(minutes=3)))

    def test_proxy_refusal_waits_minutes_and_has_the_hub_repair_the_proxies(self):
        gone = self._proxy(active=False, ok=None, reserve=self.account)
        spare = self._proxy()
        with patch.object(maintenance, "_spawn", side_effect=_inline), patch("apps.proxy.services.sync_webshare", return_value=SYNC_OK), \
                patch.object(maintenance, "probe_proxy", side_effect=self._probe):
            answer = self._report()

        # the hub replaced the proxy and, with a working one again, lifted the block it had just set
        self.assertEqual([p.pk for p in self._reserved()], [spare.pk])
        self.assertFalse(AccountProxyReservation.objects.filter(proxy_profile=gone).exists())
        self.assertIsNone(self.ga.login_blocked_until)
        self.assertTrue(answer["active"])                    # what the agent was told: wait a little
        self.assertEqual(answer["backoff_hours"], 1)

    def test_while_the_repair_does_not_work_the_wait_stays_short_then_is_capped(self):
        with patch.object(maintenance, "_spawn"):            # the repair never completes
            waits = []
            for _attempt in range(7):
                self._report()
                waits.append(round(self._blocked_for().total_seconds() / 60))
        self.assertEqual(waits, [3, 3, 3, 60, 120, 120, 120])          # it used to be 60, 120, 180 ... 420 and growing
        self.assertEqual(self.ga.login_block_backoff_hours, 7)

    @patch("apps.jobs.api.agent.notify")
    def test_operator_is_told_once_when_the_repair_did_not_work(self, notify):
        with patch.object(maintenance, "_spawn"):
            for _attempt in range(3):
                self._report()
            notify.assert_not_called()
            self._report()
            self.assertEqual(notify.call_count, 1)
            self._report()
            self.assertEqual(notify.call_count, 1)

    def test_other_connection_trouble_is_capped_and_does_not_touch_the_proxies(self):
        with patch.object(maintenance, "_spawn") as spawn:
            hours = []
            for _attempt in range(4):
                self._report(reason=SLOW_LOBBY)
                hours.append(round(self._blocked_for().total_seconds() / 3600))
            spawn.assert_not_called()
        self.assertEqual(hours, [1, 2, 2, 2])

    def test_a_block_by_the_game_still_grows_by_the_hour(self):
        with patch.object(maintenance, "_spawn") as spawn:
            hours = []
            for _attempt in range(4):
                self._report(mode="record_400", reason="loginLink falhou: status=400 body=[]")
                hours.append(round(self._blocked_for().total_seconds() / 3600))
            spawn.assert_not_called()
        self.assertEqual(hours, [1, 2, 3, 4])

    def test_paused_agent_heartbeat_starts_a_check_and_ordinary_ones_only_when_stale(self):
        self.node.proxy = "http://10.9.0.250:8000"
        self.node.save(update_fields=["proxy"])
        beat = lambda **extra: self.client.post(  # noqa: E731
            "/api/agent/heartbeat/", {"node_id": str(self.node.pk), **extra}, content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )
        AppSetting.objects.update_or_create(key=maintenance.LAST_RUN_SETTING, defaults={"value": (timezone.now() - timedelta(minutes=30)).isoformat()})
        with patch.object(maintenance, "_spawn") as spawn:
            self.assertEqual(beat(external_ip="200.1.1.1").status_code, 200)
            spawn.assert_not_called()                        # healthy agent, last run recent: nothing to do
            self.assertEqual(beat().status_code, 200)        # no exit IP: the agent is paused
            self.assertEqual(spawn.call_count, 1)
        cache.delete(maintenance.LOCK_KEY)
        AppSetting.objects.filter(key=maintenance.LAST_RUN_SETTING).update(value=(timezone.now() - timedelta(hours=7)).isoformat())
        with patch.object(maintenance, "_spawn") as spawn:
            self.assertEqual(beat(external_ip="200.1.1.1").status_code, 200)
            self.assertEqual(spawn.call_count, 1)            # the periodic safety net


class ProxyPageShowsTheLastCheckTests(_Base):
    def test_page_says_when_the_automatic_check_ran_and_what_is_missing(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        self.client.force_login(get_user_model().objects.create_user(username="px", email="px-user@example.com", password="secret123"))
        url = reverse("proxy:list")
        self.assertIn("a verificação automática ainda não rodou", self.client.get(url).content.decode())

        self._proxy(active=False, ok=None, reserve=self.account)       # gone, and nothing free to replace it
        self._proxy(reserve=Account.objects.create(node=self.node, label="Lobby B", email="b@example.com", password_enc="x"))
        self._run()

        page = self.client.get(url).content.decode()
        self.assertIn("Verificação automática:", page)
        self.assertIn("(1 de 1 ok, 1 troca)", page)
        self.assertIn("sem proxy livre para: conta Lobby PX", page)
        self.assertNotIn("{#", page)


class ProxyMaintenanceAnnouncementTests(_Base):
    """A change made by the automatic check is told on Telegram (N-96)."""

    def _scenario(self):
        gone = [self._proxy(active=False, ok=None, reserve=self.account) for _ in range(3)]
        fresh = [self._proxy() for _ in range(4)]
        self._proxy(active=False, ok=None, node=self.node)
        self.ga.login_blocked_until = timezone.now() + timedelta(hours=11)
        self.ga.login_block_backoff_hours = 11
        self.ga.login_block_reason = REFUSED
        self.ga.save()
        return gone + fresh

    def test_a_change_is_told_once_in_plain_words_without_addresses(self):
        proxies = self._scenario()
        with patch("apps.telegram.services.notifications.notify") as notify:
            self._run(sync={**SYNC_OK, "total": 100, "created": 47, "deactivated": 47})

        notify.assert_called_once()
        sent = notify.call_args.kwargs
        self.assertEqual((sent["event_key"], sent["title"]), ("proxy_change", "Proxies: verificacao automatica"))
        self.assertEqual(sent["body"].split("\n"), [
            "Conta Lobby PX: 3 proxy(s) fora de uso, 3 novo(s) reservado(s).",
            "Nó node-px: proxy trocado.",
            "Bloqueio de login liberado: Conta PX.",
            "Lista do provedor: 47 novo(s), 47 retirado(s).",
            "Teste: 4 de 4 proxies ok.",
            "Motivo da verificação: teste.",
        ])
        for proxy in proxies:
            self.assertNotIn(proxy.address, sent["body"])

    def test_no_free_proxy_is_said_clearly(self):
        self._proxy(active=False, ok=None, reserve=self.account)
        with patch("apps.telegram.services.notifications.notify") as notify:
            self._run()
        self.assertIn("SEM proxy livre para: conta Lobby PX.", notify.call_args.kwargs["body"])

    def test_a_check_that_changed_nothing_says_nothing(self):
        self._proxy(reserve=self.account)
        with patch("apps.telegram.services.notifications.notify") as notify:
            self._run()
        notify.assert_not_called()

    def test_telegram_being_down_does_not_undo_the_repair(self):
        self._scenario()
        with patch("apps.telegram.services.notifications.notify", side_effect=RuntimeError("telegram fora")):
            with self.assertLogs("apps.proxy.maintenance", level="WARNING"):
                report = self._run()
        self.assertEqual(len(self._reserved()), 3)
        self.assertEqual(report["blocks_cleared"], ["Conta PX"])

    def test_message_goes_out_through_the_bot_and_can_be_switched_off(self):
        from apps.telegram.models import TelegramBotConfig

        bot = TelegramBotConfig.objects.create(pk=1, bot_token="123:abc", enabled=True, chat_id="555")
        self.assertTrue(bot.notify_proxy_change)            # on unless the operator turns it off
        self._scenario()
        with patch("apps.telegram.services.notifications.send_message", return_value={"ok": True, "result": {"message_id": 1}}) as send:
            self._run()
        chat, text = send.call_args.args[:2]
        self.assertEqual(chat, "555")
        self.assertIn("<b>Proxies: verificacao automatica</b>", text)
        self.assertIn("Conta Lobby PX: 3 proxy(s) fora de uso, 3 novo(s) reservado(s).", text)

        bot.notify_proxy_change = False
        bot.save()
        self.ga.login_blocked_until = None
        self.ga.save()
        AccountProxyReservation.objects.filter(account=self.account).first().proxy_profile.delete()      # one more proxy gone
        ProxyProfile.objects.filter(account_reservation__isnull=True, assigned_node__isnull=True).update(active=True)
        self._proxy(active=False, ok=None, reserve=self.account)
        with patch("apps.telegram.services.notifications.send_message") as send:
            self._run()
        send.assert_not_called()
