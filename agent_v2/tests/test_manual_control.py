"""N-72: the agent answers the hub's manual-control requests with the account's own session."""

import base64
import importlib.util
import json
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Loaded by path: other test modules leave stub packages named "services" behind.
_spec = importlib.util.spec_from_file_location("manual_control_under_test", ROOT / "services" / "manual_control.py")
manual_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manual_control)

GAME = "https://s9-br.ikariam.gameforge.com"


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.expiry = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def expire(self, key, seconds):
        self.expiry[key] = seconds

    def answer(self, request_id):
        key = manual_control.RESPONSE_KEY.format(request_id=request_id)
        return json.loads(self.lists[key][-1]) if key in self.lists else None


class FakeResponse:
    def __init__(self, status=200, body=b"ok", headers=None):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": "text/html", **(headers or {})}


class FakeHttpSession:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []
        self.cookies = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        reply = self.replies.pop(0) if self.replies else FakeResponse()
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, replies=None, name="c1"):
        self.name = name
        self._server_url = GAME + "/index.php"      # as the real client has it
        self._cookies = {"ikariam": "velho", "outro": "x"}
        self.session = FakeHttpSession(list(replies or []))


class FakeSessions:
    def __init__(self):
        self.locks = {}
        self.invalidated = []

    def get_lock(self, key):
        return self.locks.setdefault(key, threading.Lock())

    def invalidate_game_session(self, key):
        self.invalidated.append(key)


class _Base(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.sessions = FakeSessions()
        self.clients = [FakeClient()]
        self.opened = []
        self.saved = []
        self.open_error = None

        def open_client(account_id, game_account_id, *, allow_cached=True):
            self.opened.append((account_id, game_account_id, allow_cached))
            if self.open_error:
                raise self.open_error
            return self.clients[min(len(self.opened), len(self.clients)) - 1]

        self.service = manual_control.ManualControlService(
            node_id="node-1", sessions=self.sessions, redis_client=self.redis,
            open_client=open_client, save_client=lambda game_account_id, client: self.saved.append((game_account_id, client)),
        )

    def _message(self, op="request", **extra):
        message = {"id": f"r{len(self.redis.lists) + 1}", "op": op, "session": "s1", "account_id": "acc", "game_account_id": "ga"}
        message.update(extra)
        return message

    def _ask(self, op="request", **extra):
        message = self._message(op, **extra)
        self.service.answer(message)
        return self.redis.answer(message["id"])


class OpenCloseTests(_Base):
    def test_open_uses_the_account_session_and_says_which_server_it_is(self):
        answer = self._ask("open")
        self.assertEqual(answer, {"ok": True, "host": "s9-br.ikariam.gameforge.com"})
        self.assertEqual(self.opened, [("acc", "ga", True)])
        self._ask("open")
        self.assertEqual(len(self.opened), 1)                 # one session, opened once
        self.assertNotIn("velho", json.dumps(answer))         # no cookie in what goes back

    def test_open_waits_for_a_job_of_the_account_and_says_busy_if_it_does_not_finish(self):
        self.sessions.get_lock("ga").acquire()                # a job of this account is still running here
        original = manual_control.OPEN_LOCK_WAIT_SECONDS
        manual_control.OPEN_LOCK_WAIT_SECONDS = 0.05
        try:
            answer = self._ask("open")
        finally:
            manual_control.OPEN_LOCK_WAIT_SECONDS = original
        self.assertFalse(answer["ok"])
        self.assertTrue(answer["retry"])
        self.assertEqual(self.opened, [])
        self.sessions.get_lock("ga").release()
        self.assertTrue(self._ask("open")["ok"])
        self.assertFalse(self.sessions.get_lock("ga").locked())   # and the lock is free for the automation again

    def test_a_refusal_is_explained_to_the_operator(self):
        self.open_error = manual_control.Refused("Conta em modo ferias")
        answer = self._ask("open")
        self.assertEqual(answer, {"ok": False, "error": "Conta em modo ferias"})
        self.open_error = None
        self.assertTrue(self._ask("open")["ok"])              # nothing is left half-open

    def test_close_hands_the_cookies_back_to_the_automation(self):
        self._ask("open")
        client = self.clients[0]
        client.session.cookies = [
            types.SimpleNamespace(name="ikariam", value="novo", domain="s9-br.ikariam.gameforge.com"),
            types.SimpleNamespace(name="ikariam", value="de-outro-site", domain="lobby.ikariam.gameforge.com"),
            types.SimpleNamespace(name="recem-criado", value="y", domain="s9-br.ikariam.gameforge.com"),
        ]
        message = self._message("close")
        self.service.answer(message)
        self.assertIsNone(self.redis.answer(message["id"]))   # nobody waits for the answer of a close
        self.assertEqual(self.saved, [("ga", client)])
        self.assertEqual(client._cookies, {"ikariam": "novo", "outro": "x"})
        self.service.answer(self._message("close"))           # closing again is harmless
        self.assertEqual(len(self.saved), 1)

    def test_a_session_nobody_closed_is_dropped_after_a_long_silence(self):
        self._ask("open")
        self.service._entries["s1"].last_used = time.monotonic() - manual_control.IDLE_SECONDS - 1
        self.service.drop_idle()
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.service._entries, {})

    def test_requests_nobody_waits_for_any_more_are_skipped(self):
        message = self._message("open", expires=time.time() - 1)
        self.service.answer(message)
        self.assertEqual(self.opened, [])
        self.assertIsNone(self.redis.answer(message["id"]))


class RequestTests(_Base):
    def test_the_request_goes_to_the_game_with_the_session_and_only_what_a_browser_may_say(self):
        self.clients[0].session.replies = [FakeResponse(
            200, b"<html>pagina</html>",
            {"Content-Type": "text/html; charset=utf-8", "Set-Cookie": "ikariam=SEGREDO", "Cache-Control": "no-cache", "Server": "nginx"},
        )]
        answer = self._ask(
            method="post", target="/index.php?view=city", body=base64.b64encode(b"a=1").decode(),
            headers={"Accept": "text/html", "Content-Type": "application/x-www-form-urlencoded", "Cookie": "x=1", "Host": "evil", "User-Agent": "outro"},
            referer="/index.php?view=island", origin=True,
        )
        method, url, kwargs = self.clients[0].session.calls[0]
        self.assertEqual((method, url), ("POST", f"{GAME}/index.php?view=city"))
        self.assertEqual(kwargs["data"], b"a=1")
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertEqual(kwargs["headers"], {
            "Accept": "text/html", "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": None,                        # a page the operator opens is not an ajax call
            "Referer": f"{GAME}/index.php?view=island", "Origin": GAME,
        })
        self.assertEqual(answer["status"], 200)
        self.assertEqual(base64.b64decode(answer["body"]), b"<html>pagina</html>")
        self.assertEqual(answer["origin"], GAME)
        self.assertEqual(answer["headers"], {"content-type": "text/html; charset=utf-8", "cache-control": "no-cache"})
        self.assertNotIn("SEGREDO", json.dumps(answer))      # the game's cookie never leaves the agent

    def test_static_files_are_asked_at_the_server_root_not_under_the_entry_page(self):
        answer = self._ask(target="/skin/compiled-ltr-common_0.css?rev=1")
        self.assertEqual(self.clients[0].session.calls[0][1], f"{GAME}/skin/compiled-ltr-common_0.css?rev=1")
        self.assertEqual(answer["origin"], GAME)

    def test_an_ajax_call_stays_an_ajax_call_and_a_plain_get_sends_no_origin(self):
        self._ask(target="/index.php?ajax=1", headers={"X-Requested-With": "XMLHttpRequest"})
        headers = self.clients[0].session.calls[0][2]["headers"]
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")
        self.assertIsNone(headers["Origin"])
        self.assertIsNone(headers["Referer"])
        self.assertIsNone(self.clients[0].session.calls[0][2]["data"])

    def test_a_request_opens_the_session_by_itself_after_an_agent_restart(self):
        self.assertTrue(self._ask(target="/")["ok"])
        self.assertEqual(self.opened, [("acc", "ga", True)])

    def test_only_the_game_can_be_reached(self):
        self.assertFalse(self._ask(target="https://example.com/")["ok"])
        self.assertFalse(self._ask(method="DELETE", target="/")["ok"])
        self.assertEqual(self.clients[0].session.calls, [])

    def test_an_expired_game_session_logs_in_again_and_repeats_the_page(self):
        self.clients = [
            FakeClient([FakeResponse(302, b"", {"Location": "https://lobby.ikariam.gameforge.com/pt_BR/"})], name="old"),
            FakeClient([FakeResponse(200, b"cidade")], name="new"),
        ]
        answer = self._ask(target="/index.php?view=city")
        self.assertEqual(self.opened, [("acc", "ga", True), ("acc", "ga", False)])
        self.assertEqual(self.sessions.invalidated, ["ga"])
        self.assertEqual(base64.b64decode(answer["body"]), b"cidade")
        self.assertEqual(self.clients[1].session.calls[0][1], f"{GAME}/index.php?view=city")

    def test_a_post_on_an_expired_session_is_not_repeated(self):
        self.clients = [
            FakeClient([FakeResponse(302, b"", {"Location": "/index.php?view=login"})], name="old"),
            FakeClient([], name="new"),
        ]
        answer = self._ask(method="POST", target="/index.php", body=base64.b64encode(b"action=x").decode())
        self.assertEqual((answer["status"], answer["headers"]["location"]), (303, f"{GAME}/"))
        self.assertEqual(self.clients[1].session.calls, [])   # what was posted belonged to the old session

    def test_a_redirect_inside_the_game_is_just_passed_on(self):
        self.clients[0].session.replies = [FakeResponse(302, b"", {"Location": f"{GAME}/index.php?view=city"})]
        answer = self._ask(target="/")
        self.assertEqual((answer["status"], answer["headers"]["location"]), (302, f"{GAME}/index.php?view=city"))
        self.assertEqual(len(self.opened), 1)

    def test_a_network_failure_is_an_answer_not_a_dead_service(self):
        self.clients[0].session.replies = [ConnectionError("proxy sem resposta")]
        answer = self._ask(target="/")
        self.assertFalse(answer["ok"])
        self.assertIn("ConnectionError", answer["error"])
        self.assertTrue(self._ask(target="/")["ok"])


class LoopTests(_Base):
    def test_the_service_reads_its_node_queue_and_answers(self):
        queue = [json.dumps(self._message("open", id="loop1")).encode()]
        seen = []

        def brpop(key, timeout=5):
            seen.append(key)
            if queue:
                return (key, queue.pop(0))
            self.service.stop()
            return None

        self.redis.brpop = brpop
        self.service.run()
        self.service._pool.shutdown(wait=True)
        self.assertEqual(seen[0], "ikabot:manual:req:node-1")
        self.assertEqual(self.redis.answer("loop1"), {"ok": True, "host": "s9-br.ikariam.gameforge.com"})
        self.assertEqual(self.redis.expiry["ikabot:manual:resp:loop1"], manual_control.RESPONSE_TTL_SECONDS)


if __name__ == "__main__":
    unittest.main()
