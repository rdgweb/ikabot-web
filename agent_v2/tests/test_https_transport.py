"""N-106 3b-2: the agent over HTTPS only — jobs claimed from the hub, manual control through the hub."""
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import https_worker  # noqa: E402


class FakeHub:
    def __init__(self, claims=(), manual=()):
        self.claims = list(claims)
        self.manual = list(manual)
        self.claimed_slots = []
        self.answers = []
        self.manual_calls = 0

    def claim_jobs(self, slots):
        self.claimed_slots.append(slots)
        return self.claims.pop(0) if self.claims else {"jobs": [], "manual": False}

    def manual_next(self, wait=20):
        self.manual_calls += 1
        return self.manual.pop(0) if self.manual else {"request": None, "active": False}

    def manual_answer(self, request_id, answer):
        self.answers.append((request_id, answer))


class ManualChannelTests(unittest.TestCase):
    def test_no_session_no_waiting_on_the_hub(self):
        hub = FakeHub()
        channel = https_worker.HttpsManualChannel(hub)
        self.assertIsNone(channel.brpop("ikabot:manual:req:n1", timeout=0.01))
        self.assertEqual(hub.manual_calls, 0)

    def test_requests_come_while_a_session_is_on_and_stop_after(self):
        hub = FakeHub(manual=[{"request": {"id": "abc", "op": "open"}, "active": True}, {"request": None, "active": False}])
        channel = https_worker.HttpsManualChannel(hub)
        channel.active.set()
        key, raw = channel.brpop("ikabot:manual:req:n1")
        self.assertEqual(json.loads(raw), {"id": "abc", "op": "open"})
        self.assertIsNone(channel.brpop("ikabot:manual:req:n1"))
        self.assertFalse(channel.active.is_set())                      # the session ended: no more waiting

    def test_answers_go_back_with_the_request_id(self):
        hub = FakeHub()
        channel = https_worker.HttpsManualChannel(hub)
        channel.rpush("ikabot:manual:resp:abc123", json.dumps({"ok": True}))
        self.assertEqual(hub.answers, [("abc123", {"ok": True})])


class WorkerLoopTests(unittest.TestCase):
    def test_claimed_jobs_run_and_the_loop_stops(self):
        stop = threading.Event()
        ran = []
        hub = FakeHub(claims=[{"jobs": [{"job_id": "j1"}, {"job_id": "j2"}], "manual": True}])
        channel = https_worker.HttpsManualChannel(hub)

        def fake_execute(job, sessions, proxy_url):
            ran.append((job["job_id"], proxy_url))
            if len(ran) == 2:
                stop.set()

        with mock.patch.object(https_worker, "execute_job_payload", fake_execute), mock.patch.object(https_worker, "POLL_SECONDS", 0.05):
            https_worker.run(hub=hub, sessions=object(), proxy_url=lambda: "http://proxy", max_parallel=4, channel=channel, stop=stop)
        self.assertEqual(sorted(job for job, _ in ran), ["j1", "j2"])
        self.assertEqual({proxy for _, proxy in ran}, {"http://proxy"})
        self.assertEqual(hub.claimed_slots[0], 4)
        self.assertTrue(channel.active.is_set())                        # the hub said manual control is on

    def test_a_hub_outage_does_not_kill_the_loop(self):
        stop = threading.Event()
        hub = mock.Mock()
        hub.claim_jobs.side_effect = [ConnectionError("down"), {"jobs": [], "manual": False}]

        def stop_soon(*_args):
            if hub.claim_jobs.call_count >= 2:
                stop.set()

        with mock.patch.object(https_worker, "ERROR_BACKOFF_SECONDS", 0.01), mock.patch.object(https_worker, "POLL_SECONDS", 0.01):
            timer = threading.Timer(0.5, stop.set)
            timer.start()
            https_worker.run(hub=hub, sessions=object(), proxy_url=lambda: "", max_parallel=1,
                             channel=https_worker.HttpsManualChannel(mock.Mock()), stop=stop)
            timer.cancel()
        self.assertGreaterEqual(hub.claim_jobs.call_count, 2)


if __name__ == "__main__":
    unittest.main()
