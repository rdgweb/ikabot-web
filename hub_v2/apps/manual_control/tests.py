"""N-72: manual control of a game account."""

import base64
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job, JobLog
from apps.manual_control import gateway, services
from apps.manual_control.models import HeldJob, ManualControlSession

GATEWAY = {"MANUAL_GATEWAY_DOMAIN": "gw.test", "MANUAL_GATEWAY_SCHEME": "https", "MANUAL_GATEWAY_PORT": ""}
GAME = "https://s9-br.ikariam.gameforge.com"


class FakeAgent:
    """Stands for the broker and the agent: records what the hub asks, answers what the test says."""

    def __init__(self):
        self.sent = []          # every request the hub queued
        self.answers = {}       # request id -> answer waiting to be read
        self.reply = None       # callable(payload) -> answer, for requests the gateway waits on
        self._n = 0

    def send(self, node_id, payload, *, ttl=60):
        self._n += 1
        request_id = f"req{self._n}"
        self.sent.append({"node": str(node_id), "id": request_id, **payload})
        return request_id

    def poll(self, request_id):
        return self.answers.pop(request_id, None)

    def call(self, node_id, payload, *, timeout):
        self.send(node_id, payload)
        return self.reply(payload) if self.reply else None

    def ops(self, op):
        return [item for item in self.sent if item["op"] == op]


@override_settings(**GATEWAY)
class _Base(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-mc", active=True, agent_last_seen_at=timezone.now(), agent_version="0.1.91")
        self.account = Account.objects.create(node=self.node, label="Lobby MC", email="mc@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=72, server_id="s9-br", server_language="br", server_number=9, name="Conta MC",
        )
        self.other = GameAccount.objects.create(
            account=self.account, lobby_account_id=73, server_id="s8-br", server_language="br", server_number=8, name="Outra",
        )
        self.user = get_user_model().objects.create_user("operador", password="x")
        self.agent = FakeAgent()
        for name in ("send", "poll", "call"):
            patcher = patch(f"apps.manual_control.rpc.{name}", side_effect=getattr(self.agent, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dispatched = []
        patcher = patch("apps.jobs.services.dispatch.dispatch_job", side_effect=lambda job, eta=None: self.dispatched.append((job.pk, eta)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _job(self, *, status="scheduled", game_account=None, minutes=0, **extra):
        return Job.objects.create(
            account=self.account, game_account=game_account or self.ga, node=self.node, action_code=2, status=status,
            scheduled_for=timezone.now() + timedelta(minutes=minutes), **extra,
        )

    def _active(self):
        session = services.request_control(self.ga, self.user)
        services.advance(session.pk)                       # asks the agent to open
        self.agent.answers[self.agent.ops("open")[-1]["id"]] = {"ok": True, "host": "s9-br.ikariam.gameforge.com"}
        state = services.advance(session.pk)
        self.assertEqual(state["state"], "active")
        session.refresh_from_db()
        return session


class RequestControlTests(_Base):
    def test_nothing_is_offered_while_the_gateway_is_not_configured(self):
        with override_settings(MANUAL_GATEWAY_DOMAIN=""):
            with self.assertRaises(services.ControlRefused):
                services.request_control(self.ga, self.user)
        self.assertEqual(ManualControlSession.objects.count(), 0)

    def test_refused_when_the_agent_is_offline_or_too_old(self):
        Node.objects.filter(pk=self.node.pk).update(agent_version="0.1.90")
        self.ga.refresh_from_db()
        with self.assertRaisesMessage(services.ControlRefused, "atualize o agente"):
            services.request_control(GameAccount.objects.get(pk=self.ga.pk), self.user)
        Node.objects.filter(pk=self.node.pk).update(agent_version="0.1.91", agent_last_seen_at=timezone.now() - timedelta(hours=1))
        with self.assertRaisesMessage(services.ControlRefused, "offline"):
            services.request_control(GameAccount.objects.get(pk=self.ga.pk), self.user)
        self.assertEqual(services.control_state(self.ga.pk), "automation")

    def test_one_session_per_account(self):
        first = services.request_control(self.ga, self.user)
        self.assertEqual(first.status, "pausing")
        self.assertEqual(services.control_state(self.ga.pk), "pausing")
        self.assertEqual(services.request_control(self.ga, self.user).pk, first.pk)
        someone_else = get_user_model().objects.create_user("outro", password="x")
        with self.assertRaises(services.ControlRefused):
            services.request_control(self.ga, someone_else)
        # the database itself refuses a second open session for the account
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            ManualControlSession.objects.create(
                game_account=self.ga, node=self.node, open_key=str(self.ga.pk), token="t2", expires_at=timezone.now(),
            )
        # another account is free to be taken
        self.assertNotEqual(services.request_control(self.other, self.user).pk, first.pk)

    def test_the_public_name_of_the_session_is_not_the_account(self):
        session = services.request_control(self.ga, self.user)
        self.assertEqual(len(session.token), 32)
        self.assertNotIn(str(self.ga.pk).replace("-", "")[:12], session.token)
        self.assertEqual(services.session_origin(session), f"https://{session.token}.gw.test")


class PreparationTests(_Base):
    def test_waits_for_the_running_job_then_opens_the_session_in_the_agent(self):
        running = self._job(status="running", started_at=timezone.now(), lease_expires_at=timezone.now() + timedelta(minutes=3))
        session = services.request_control(self.ga, self.user)

        state = services.advance(session.pk)
        self.assertEqual((state["state"], state["step"]), ("pausing", "waiting_job"))
        self.assertEqual(state["jobs"][0]["id"], str(running.pk))
        self.assertEqual(self.agent.sent, [])               # nothing is asked of the agent while a job runs

        Job.objects.filter(pk=running.pk).update(status="finished")
        state = services.advance(session.pk)
        self.assertEqual((state["state"], state["step"]), ("pausing", "opening"))
        opened = self.agent.ops("open")
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0]["game_account_id"], str(self.ga.pk))
        self.assertEqual(opened[0]["node"], str(self.node.pk))

        self.assertEqual(services.advance(session.pk)["step"], "opening")   # still waiting for the agent
        self.assertEqual(len(self.agent.ops("open")), 1)                    # and not asked twice

        self.agent.answers[opened[0]["id"]] = {"ok": True, "host": "s9-br.ikariam.gameforge.com"}
        self.assertEqual(services.advance(session.pk)["state"], "active")
        session.refresh_from_db()
        self.assertEqual(session.server_host, "s9-br.ikariam.gameforge.com")
        self.assertEqual(services.control_state(self.ga.pk), "manual")

    def test_a_job_whose_agent_died_does_not_block_the_request(self):
        self._job(status="running", started_at=timezone.now() - timedelta(hours=1), lease_expires_at=timezone.now() - timedelta(minutes=30))
        session = services.request_control(self.ga, self.user)
        self.assertEqual(services.advance(session.pk)["step"], "opening")

    def test_failure_to_open_gives_the_account_back(self):
        held = self._job()
        session = services.request_control(self.ga, self.user)
        services.advance(session.pk)
        self.assertTrue(services.hold_job(held))
        self.agent.answers[self.agent.ops("open")[0]["id"]] = {"ok": False, "error": "Conta em modo ferias"}
        state = services.advance(session.pk)
        self.assertEqual((state["state"], state["reason"]), ("ended", "error"))
        self.assertIn("ferias", state["error"])
        self.assertEqual(services.control_state(self.ga.pk), "automation")
        self.assertEqual([pk for pk, _eta in self.dispatched], [held.pk])   # the job that waited goes on

    def test_agent_busy_is_asked_again_and_silence_times_out(self):
        session = services.request_control(self.ga, self.user)
        services.advance(session.pk)
        self.agent.answers[self.agent.ops("open")[0]["id"]] = {"ok": False, "retry": True}
        self.assertEqual(services.advance(session.pk)["state"], "pausing")
        services.advance(session.pk)
        self.assertEqual(len(self.agent.ops("open")), 2)

        ManualControlSession.objects.filter(pk=session.pk).update(
            open_requested_at=timezone.now() - services.OPEN_TIMEOUT - timedelta(seconds=5),
        )
        state = services.advance(session.pk)
        self.assertEqual((state["state"], state["reason"]), ("ended", "error"))
        self.assertIn("agente nao respondeu", state["error"])

    def test_a_request_nobody_follows_is_dropped(self):
        session = services.request_control(self.ga, self.user)
        ManualControlSession.objects.filter(pk=session.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(services.sweep_expired(), 1)
        self.assertEqual(services.control_state(self.ga.pk), "automation")


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class JobGateTests(_Base):
    def _report(self, job, status="running"):
        return self.client.post(
            f"/api/agent/jobs/{job.pk}/status/", {"status": status, "agent": "a1"},
            content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )

    def test_a_job_does_not_start_while_the_account_is_under_manual_control(self):
        self._active()
        job = self._job()
        response = self._report(job)
        self.assertEqual(response.status_code, 409)          # what the agent already takes as "do not run"
        self.assertEqual(response.json()["reason"], "manual_control")
        job.refresh_from_db()
        self.assertEqual(job.status, "scheduled")            # untouched
        self.assertIsNone(job.started_at)
        self.assertEqual(HeldJob.objects.filter(job=job).count(), 1)

        self._report(job)                                    # the same job knocking again
        self.assertEqual(HeldJob.objects.filter(job=job).count(), 1)
        self.assertEqual(JobLog.objects.filter(job=job, message=services.HELD_MESSAGE).count(), 1)

    def test_held_from_the_moment_control_is_requested(self):
        services.request_control(self.ga, self.user)         # still pausing
        self.assertEqual(self._report(self._job(status="queued")).status_code, 409)

    def test_a_job_already_running_keeps_reporting_and_other_accounts_are_untouched(self):
        running = self._job(status="running", started_at=timezone.now(), lease_expires_at=timezone.now() + timedelta(minutes=3))
        services.request_control(self.ga, self.user)
        self.assertEqual(self._report(running).status_code, 200)             # its heartbeat
        self.assertEqual(self._report(running, "finished").status_code, 200)
        other = self._job(game_account=self.other)
        self.assertEqual(self._report(other).status_code, 200)
        self.assertFalse(HeldJob.objects.exists())

    def test_without_manual_control_nothing_changes(self):
        job = self._job()
        self.assertEqual(self._report(job).status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, "running")

    def test_a_job_arriving_after_the_lease_ran_out_ends_the_control_and_runs(self):
        session = self._active()
        waiting = self._job()
        self._report(waiting)
        ManualControlSession.objects.filter(pk=session.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        late = self._job()
        self.assertEqual(self._report(late).status_code, 200)
        session.refresh_from_db()
        self.assertEqual((session.status, session.end_reason), ("ended", "expired"))
        self.assertEqual([pk for pk, _eta in self.dispatched], [waiting.pk])


class ReleaseTests(_Base):
    def test_ending_hands_the_held_jobs_back_in_order_and_frees_the_account(self):
        session = self._active()
        later = self._job(minutes=-1)
        sooner = self._job(minutes=-5)
        queued = self._job(status="queued", minutes=-3)
        cancelled = self._job(minutes=-4)
        for job in (later, sooner, queued, cancelled):
            self.assertTrue(services.hold_job(job))
        Job.objects.filter(pk=cancelled.pk).update(status="cancelled")      # cancelled by the operator meanwhile

        before = timezone.now()
        self.assertTrue(services.end(session, "closed"))
        self.assertEqual([pk for pk, _eta in self.dispatched], [sooner.pk, queued.pk, later.pk])
        etas = [eta for _pk, eta in self.dispatched]
        self.assertTrue(all(eta > before for eta in etas))
        self.assertEqual(etas, sorted(etas))
        self.assertGreaterEqual((etas[1] - etas[0]).total_seconds(), services.RESUME_SPACING - 0.5)
        sooner.refresh_from_db()
        self.assertEqual(sooner.scheduled_for, etas[0])                     # the queue shows when it really runs
        self.assertFalse(HeldJob.objects.exists())

        session.refresh_from_db()
        self.assertEqual((session.status, session.end_reason, session.open_key), ("ended", "closed", None))
        self.assertEqual((session.cookie_hash, session.start_key_hash), ("", ""))
        self.assertEqual(len(self.agent.ops("close")), 1)                   # the agent saves the cookies back
        self.assertEqual(services.control_state(self.ga.pk), "automation")
        self.assertFalse(services.end(session, "closed"))                   # ending twice does nothing
        self.assertEqual(len(self.dispatched), 3)
        self.assertNotEqual(services.request_control(self.ga, self.user).pk, session.pk)

    def test_the_agent_heartbeat_ends_a_session_whose_tab_is_gone(self):
        session = self._active()
        job = self._job()
        services.hold_job(job)
        ManualControlSession.objects.filter(pk=session.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS=""), patch("apps.proxy.maintenance.trigger_if_stale"):
            response = self.client.post(
                "/api/agent/heartbeat/", {"node_id": str(self.node.pk), "external_ip": "200.1.1.1"},
                content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
            )
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual((session.status, session.end_reason), ("ended", "expired"))
        self.assertEqual([pk for pk, _eta in self.dispatched], [job.pk])

    def test_a_tab_left_open_without_use_stops_holding_the_account(self):
        session = self._active()
        ManualControlSession.objects.filter(pk=session.pk).update(
            last_request_at=timezone.now() - timedelta(minutes=31), expires_at=timezone.now() + timedelta(seconds=50),
        )
        self.assertEqual(services.sweep_expired(), 1)
        session.refresh_from_db()
        self.assertEqual(session.end_reason, "idle")

    def test_a_job_that_arrives_while_the_session_closes_is_not_forgotten(self):
        session = self._active()
        services.end(session, "closed")
        job = self._job()
        HeldJob.objects.create(session=session, job=job)                    # arrived between "closing" and "closed"
        services.sweep_expired()
        self.assertEqual([pk for pk, _eta in self.dispatched], [job.pk])
        self.assertFalse(HeldJob.objects.exists())


@override_settings(ROOT_URLCONF="config.gateway_urls", MIDDLEWARE=[], ALLOWED_HOSTS=[".gw.test", "testserver"], **GATEWAY)
class GatewayTests(_Base):
    def setUp(self):
        super().setUp()
        self.session = self._active()
        self.host = f"{self.session.token}.gw.test"
        self.origin = f"https://{self.host}"

    def _enter(self):
        url = services.new_start_url(self.session)
        self.assertTrue(url.startswith(f"{self.origin}/__ikabot/start/"))
        return self.client.get(url[len(self.origin):], HTTP_HOST=self.host)

    def _game(self, body=b"<html><body>ok</body></html>", status=200, headers=None):
        def reply(payload):
            return {
                "ok": True, "status": status, "origin": GAME,
                "headers": {"content-type": "text/html; charset=utf-8", **(headers or {})},
                "body": base64.b64encode(body).decode(),
            }
        self.agent.reply = reply

    def test_only_session_hosts_are_served(self):
        self.assertEqual(self.client.get("/", HTTP_HOST="nada.gw.test").status_code, 404)
        self.assertEqual(self.client.get("/", HTTP_HOST="testserver").status_code, 404)
        self.assertEqual(self.client.get("/__ikabot/health", HTTP_HOST="testserver").status_code, 200)

    def test_the_way_in_is_a_one_time_key_that_becomes_a_cookie(self):
        self._game()
        self.assertEqual(self.client.get("/", HTTP_HOST=self.host).status_code, 403)        # no cookie, no game
        self.assertEqual(self.agent.ops("request"), [])
        url = services.new_start_url(self.session)
        path = url[len(self.origin):]
        entered = self.client.get(path, HTTP_HOST=self.host)
        self.assertEqual((entered.status_code, entered["Location"]), (302, "/"))
        cookie = entered.cookies[gateway.COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.session.refresh_from_db()
        self.assertNotIn(cookie.value, (self.session.cookie_hash, self.session.start_key_hash))   # only hashes are stored
        self.assertEqual(self.client.get("/", HTTP_HOST=self.host).status_code, 200)

        from django.test import Client

        stranger = Client()
        self.assertEqual(stranger.get(path, HTTP_HOST=self.host).status_code, 403)          # the key was spent
        self.assertEqual(stranger.get("/", HTTP_HOST=self.host).status_code, 403)

    def test_the_game_gets_the_request_and_nothing_about_the_browser_or_the_hub(self):
        self._enter()
        self._game()
        self.client.cookies["do_navegador"] = "segredo"
        response = self.client.post(
            "/index.php?view=city&cityId=7", data="a=1&b=2", content_type="application/x-www-form-urlencoded",
            HTTP_HOST=self.host, HTTP_ACCEPT="text/html", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_USER_AGENT="Navegador do operador", HTTP_REFERER=f"{self.origin}/index.php?view=island",
            HTTP_ORIGIN=self.origin, HTTP_X_FORWARDED_FOR="203.0.113.9", HTTP_ACCEPT_LANGUAGE="de",
        )
        self.assertEqual(response.status_code, 200)
        sent = self.agent.ops("request")[-1]
        self.assertEqual((sent["method"], sent["target"]), ("POST", "/index.php?view=city&cityId=7"))
        self.assertEqual(base64.b64decode(sent["body"]), b"a=1&b=2")
        self.assertEqual(sent["headers"], {
            "Accept": "text/html", "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest",
        })
        self.assertEqual(sent["referer"], "/index.php?view=island")          # the public address never reaches the game
        self.assertIs(sent["origin"], True)
        self.assertEqual((sent["game_account_id"], sent["session"]), (str(self.ga.pk), str(self.session.pk)))
        flat = repr(sent)
        for leaked in ("segredo", "Navegador do operador", "203.0.113.9", "gw.test", gateway.COOKIE_NAME):
            self.assertNotIn(leaked, flat)

    def test_the_answer_is_the_game_page_made_to_live_on_the_session_host(self):
        self._enter()
        page = (
            b'<html><head><script>var u="https:\\/\\/s9-br.ikariam.gameforge.com\\/index.php";</script></head>'
            b'<body><a href="https://s9-br.ikariam.gameforge.com/index.php?view=city">cidade</a>'
            b'<img src="//s9-br.ikariam.gameforge.com/cdn/a.png"><img src="https://gf1.geo.gfsrv.net/x.png"></body></html>'
        )
        self._game(page, headers={"set-cookie": "ikariam=SESSAO; path=/", "cache-control": "no-cache", "x-interno": "1"})
        response = self.client.get("/index.php", HTTP_HOST=self.host)
        html = response.content.decode()
        self.assertNotIn("s9-br.ikariam.gameforge.com", html)
        self.assertIn(f'href="{self.origin}/index.php?view=city"', html)
        self.assertIn(f'"https:\\/\\/{self.host}\\/index.php"', html)
        self.assertIn(f'src="//{self.host}/cdn/a.png"', html)
        self.assertIn("https://gf1.geo.gfsrv.net/x.png", html)               # other hosts are left alone
        self.assertIn("__ikabotControl", html)                               # the tab reports that it is alive
        self.assertLess(html.index("__ikabotControl"), html.index("</body>"))
        self.assertNotIn("Set-Cookie", response)                             # the game's cookie stays in the agent
        self.assertNotIn("SESSAO", repr(response.serialize_headers()))
        self.assertNotIn("X-Interno", response)
        self.assertEqual(response["Cache-Control"], "no-cache")
        # the page may only load from this session and from the static CDN: Gameforge's
        # tracking/consent scripts do not run from the operator's own address
        policy = response["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("https://*.geo.gfsrv.net", policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertNotIn("gameforge.com", policy)
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")

    def test_assets_and_ajax_pass_untouched_and_redirects_stay_in_the_session(self):
        self._enter()
        self._game(b"\x89PNG\x00https://s9-br.ikariam.gameforge.com", headers={"content-type": "image/png"})
        image = self.client.get("/cdn/a.png", HTTP_HOST=self.host)
        self.assertEqual(image.content, b"\x89PNG\x00https://s9-br.ikariam.gameforge.com")
        # the game answers ajax as text/html, sometimes carrying pieces of a page
        self._game(b'[["changeView",["city","<div>x</div></body>"]]]')
        fragment = self.client.get("/index.php?ajax=1", HTTP_HOST=self.host)
        self.assertNotIn(b"__ikabot", fragment.content)
        self.assertNotIn("Content-Security-Policy", fragment)

        self._game(b"", status=302, headers={"location": f"{GAME}/index.php?view=city"})
        moved = self.client.get("/", HTTP_HOST=self.host)
        self.assertEqual((moved.status_code, moved["Location"]), (302, "/index.php?view=city"))
        self._game(b"", status=302, headers={"location": "https://lobby.ikariam.gameforge.com/"})
        gone = self.client.get("/", HTTP_HOST=self.host)
        self.assertEqual(gone.status_code, 502)                              # never sends the operator's browser to the lobby
        self.assertNotIn("Location", gone)

    def test_agent_silence_and_agent_errors_are_told_to_the_operator(self):
        self._enter()
        self.agent.reply = None
        self.assertEqual(self.client.get("/", HTTP_HOST=self.host).status_code, 504)
        self.agent.reply = lambda payload: {"ok": False, "error": "ProxyError: sem resposta"}
        failed = self.client.get("/", HTTP_HOST=self.host)
        self.assertEqual(failed.status_code, 502)
        self.assertIn("ProxyError", failed.content.decode())

    def test_heartbeat_keeps_the_lease_closing_the_tab_shortens_it_and_a_new_page_takes_it_back(self):
        self._enter()
        self._game()
        ManualControlSession.objects.filter(pk=self.session.pk).update(expires_at=timezone.now() + timedelta(seconds=20))
        self.assertEqual(self.client.post("/__ikabot/heartbeat", HTTP_HOST=self.host).status_code, 204)
        self.session.refresh_from_db()
        self.assertGreater(self.session.expires_at, timezone.now() + timedelta(seconds=50))

        self.assertEqual(self.client.post("/__ikabot/bye", HTTP_HOST=self.host).status_code, 204)
        self.session.refresh_from_db()
        self.assertLess(self.session.expires_at, timezone.now() + timedelta(seconds=15))
        self.assertEqual(self.session.status, "active")                      # leaving a page is not yet closing the tab

        self.client.get("/index.php", HTTP_HOST=self.host)                   # the next page of the game
        self.session.refresh_from_db()
        self.assertGreater(self.session.expires_at, timezone.now() + timedelta(seconds=50))

        from django.test import Client

        self.assertEqual(Client().post("/__ikabot/heartbeat", HTTP_HOST=self.host).status_code, 403)

    def test_ending_from_the_tab_gives_the_account_back_and_closes_the_door(self):
        self._enter()
        self._game()
        job = self._job()
        services.hold_job(job)
        self.assertEqual(self.client.post("/__ikabot/end", HTTP_HOST=self.host).status_code, 204)
        self.session.refresh_from_db()
        self.assertEqual((self.session.status, self.session.end_reason), ("ended", "closed"))
        self.assertEqual([pk for pk, _eta in self.dispatched], [job.pk])
        requests_before = len(self.agent.ops("request"))
        self.assertEqual(self.client.get("/", HTTP_HOST=self.host).status_code, 410)
        self.assertEqual(self.client.post("/__ikabot/heartbeat", HTTP_HOST=self.host).status_code, 410)
        self.assertEqual(len(self.agent.ops("request")), requests_before)    # nothing reaches the game any more

    def test_a_lease_that_ran_out_is_noticed_by_the_next_request(self):
        self._enter()
        self._game()
        ManualControlSession.objects.filter(pk=self.session.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get("/", HTTP_HOST=self.host).status_code, 410)
        self.assertEqual(services.control_state(self.ga.pk), "automation")


class HubPagesTests(_Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_the_button_opens_a_session_and_the_page_follows_it_until_the_game_opens(self):
        response = self.client.post(f"/game/control/start/{self.ga.pk}/")
        session = ManualControlSession.objects.get()
        self.assertRedirects(response, f"/game/control/{session.pk}/", fetch_redirect_response=False)
        page = self.client.get(f"/game/control/{session.pk}/")
        self.assertContains(page, "Preparando controle manual")
        self.assertContains(page, f"/game/control/{session.pk}/status/")
        self.assertNotIn("{#", page.content.decode())

        self.assertEqual(self.client.get(f"/game/control/{session.pk}/status/").json()["step"], "opening")
        self.agent.answers[self.agent.ops("open")[0]["id"]] = {"ok": True, "host": "s9-br.ikariam.gameforge.com"}
        state = self.client.get(f"/game/control/{session.pk}/status/").json()
        self.assertEqual(state["state"], "active")
        self.assertTrue(state["url"].startswith(f"https://{session.token}.gw.test/__ikabot/start/"))

    def test_refusal_is_explained_and_changes_nothing(self):
        Node.objects.filter(pk=self.node.pk).update(agent_version="0.1.90")
        response = self.client.post(f"/game/control/start/{self.ga.pk}/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("atualize o agente", response.content.decode())
        self.assertFalse(ManualControlSession.objects.exists())

    def test_cancel_while_preparing_and_end_from_the_panel(self):
        session = services.request_control(self.ga, self.user)
        self.assertEqual(self.client.post(f"/game/control/{session.pk}/end/").json(), {"ok": True})
        session.refresh_from_db()
        self.assertEqual((session.status, session.end_reason), ("ended", "cancelled"))
        state = self.client.get(f"/game/control/{session.pk}/status/").json()
        self.assertEqual(state["state"], "ended")
        self.assertIn("cancelado", state["message"])

        active = self._active()
        chip = self.client.post(f"/game/control/{active.pk}/end/", HTTP_HX_REQUEST="true")
        self.assertContains(chip, "Assumir controle")
        active.refresh_from_db()
        self.assertEqual(active.end_reason, "closed")

    def test_another_user_cannot_enter_or_end_the_session(self):
        session = self._active()
        self.client.force_login(get_user_model().objects.create_user("outro", password="x"))
        self.assertEqual(self.client.get(f"/game/control/{session.pk}/status/").status_code, 403)
        self.assertEqual(self.client.post(f"/game/control/{session.pk}/end/").status_code, 403)
        session.refresh_from_db()
        self.assertEqual(session.status, "active")

    def test_the_chip_shows_the_state_and_is_absent_while_the_feature_is_off(self):
        chip = self.client.get(f"/game/control/chip/{self.ga.pk}/").content.decode()
        self.assertIn("Assumir controle", chip)
        self.assertIn(f"/game/control/start/{self.ga.pk}/", chip)
        self.assertNotIn("every 20s", chip)                                  # nothing to watch while in automation
        # the hub's layout boosts every form and aims every request at the page content:
        # the chip must update only itself, and its form must really open a new tab
        self.assertIn('hx-target="this"', chip)
        self.assertIn('hx-boost="false"', chip)
        self.assertIn('target="_blank"', chip)
        session = self._active()
        chip = self.client.get(f"/game/control/chip/{self.ga.pk}/").content.decode()
        self.assertIn("CONTROLE MANUAL", chip)
        self.assertIn('hx-target="this"', chip)
        self.assertIn(f"/game/control/{session.pk}/end/", chip)
        self.assertIn("every 20s", chip)
        services.end(session, "closed")
        with override_settings(MANUAL_GATEWAY_DOMAIN=""):
            self.assertEqual(self.client.get(f"/game/control/chip/{self.ga.pk}/").content.decode().strip(), "")

    def test_pages_require_login(self):
        self.client.logout()
        self.assertEqual(self.client.post(f"/game/control/start/{self.ga.pk}/").status_code, 302)
        self.assertFalse(ManualControlSession.objects.exists())
