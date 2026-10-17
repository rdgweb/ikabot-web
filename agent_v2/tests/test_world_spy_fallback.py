"""N-152: a round of the world spy campaign never goes on without its fallback (the campaign would die)."""
import unittest
from unittest import mock

import runners.world_spy as world_spy
from runners.world_spy import WorldSpyRunner


class FakeHub:
    def __init__(self, failures):
        self.failures = failures
        self.reschedules = []
        self.spawned = []
        self.logs = []

    def report_log(self, job_id, level, msg):
        self.logs.append((level, msg))

    def get_spy_targets(self, **kwargs):
        return {"targets": [{"game_city_id": "500", "city_name": "Polis", "owner_name": "Alvo", "x": 1, "y": 1}],
                "dump_id": "d", "busy_source_cities": [], "source_coords": {}}

    def reschedule_job(self, job_id, delay_seconds, inputs=None):
        self.reschedules.append(job_id)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("500 Server Error")
        return {"new_job_id": "next-round"}

    def spawn_job(self, parent_job_id, **kwargs):
        self.spawned.append(kwargs)
        return {"job_id": "child"}

    def scan_raid_alerts(self, **kwargs):
        return {}


def run(hub):
    runner = WorldSpyRunner.__new__(WorldSpyRunner)
    runner.hub = hub
    job = {"job_id": "round-1", "game_account_id": "ga", "inputs": {"city_ids": "100", "missions": "5"}}
    with mock.patch.object(world_spy, "FALLBACK_RETRY_WAITS", (0, 0)):
        return runner.execute(job)


class WorldSpyFallbackTests(unittest.TestCase):
    def test_a_passing_failure_is_retried(self):
        hub = FakeHub(failures=1)
        result = run(hub)
        self.assertTrue(result.success)
        self.assertIn("round-1", hub.reschedules)
        self.assertEqual(len(hub.spawned), 1)

    def test_without_a_fallback_no_child_is_sent_and_the_round_comes_back(self):
        hub = FakeHub(failures=3)
        result = run(hub)
        self.assertFalse(result.success)
        self.assertEqual(result.reschedule_seconds, world_spy.ERROR_RESCHEDULE)
        self.assertEqual(result.reschedule_inputs["__campaign_root_id"], "round-1")
        self.assertEqual(hub.spawned, [])
        self.assertEqual(len(hub.reschedules), 3)


if __name__ == "__main__":
    unittest.main()
