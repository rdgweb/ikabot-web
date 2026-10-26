"""N-163: the world spy campaign runs in passes and asks the hub for targets by value."""
import unittest

from runners.world_spy import WorldSpyRunner


class FakeHub:
    def __init__(self, targets):
        self.targets = targets
        self.asked = {}
        self.logs = []
        self.reschedules = []

    def report_log(self, job_id, level, msg):
        self.logs.append((level, msg))

    def get_spy_targets(self, **kwargs):
        self.asked = kwargs
        return {"targets": self.targets, "dump_id": "d", "busy_source_cities": [], "source_coords": {}}

    def reschedule_job(self, job_id, delay_seconds, inputs=None):
        self.reschedules.append(inputs)
        return {"new_job_id": "next"}

    def spawn_job(self, parent_job_id, **kwargs):
        return {"job_id": "child"}

    def scan_raid_alerts(self, **kwargs):
        return {}


def run(inputs, targets):
    runner = WorldSpyRunner.__new__(WorldSpyRunner)
    runner.hub = FakeHub(targets)
    job = {"job_id": "round", "game_account_id": "ga",
           "inputs": {"city_ids": "100", "missions": "5,6,26", "raid_alert_threshold": 200000, **inputs}}
    return runner.execute(job), runner.hub


class WorldSpyPassTests(unittest.TestCase):
    def test_the_hub_is_asked_with_the_pass_the_covered_players_and_value_first(self):
        _, hub = run({"__pass_started_at": "2026-10-25T10:00:00+00:00", "__spied_player_ids": ["name:fulano"]},
                     [{"game_city_id": "5", "city_name": "A", "owner_name": "B", "missing_missions": [5]}])
        self.assertEqual(hub.asked["pass_since"], "2026-10-25T10:00:00+00:00")
        self.assertEqual(hub.asked["covered_owners"], ["name:fulano"])
        self.assertTrue(hub.asked["prioritize"])
        self.assertEqual(hub.asked["rich_threshold"], 200000)
        self.assertEqual(hub.reschedules[0]["__pass_started_at"], "2026-10-25T10:00:00+00:00")   # kept in the fallback

    def test_a_finished_pass_schedules_the_next_one_from_scratch(self):
        result, hub = run({"__pass_started_at": "2026-10-25T10:00:00+00:00", "__spied_player_ids": ["name:x"],
                           "pass_interval_hours": 72}, [])
        self.assertEqual(result.reschedule_seconds, 72 * 3600)
        self.assertEqual(result.reschedule_inputs["__pass_started_at"], "")
        self.assertEqual(result.reschedule_inputs["__spied_player_ids"], [])
        self.assertTrue(any("Volta concluída" in m for _, m in hub.logs))

    def test_interval_zero_ends_the_campaign(self):
        result, _ = run({"pass_interval_hours": 0}, [])
        self.assertFalse(result.reschedule_seconds)


if __name__ == "__main__":
    unittest.main()
