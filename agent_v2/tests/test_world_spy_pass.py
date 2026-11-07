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

    def test_only_targets_with_an_active_spy_job_stay_reserved(self):
        runner = WorldSpyRunner.__new__(WorldSpyRunner)
        runner.hub = FakeHub([{"game_city_id": "5", "city_name": "A", "owner_name": "B", "missing_missions": [5]}])
        runner.hub.get_spy_targets = lambda **kw: {"targets": [{"game_city_id": "5", "city_name": "A", "owner_name": "B", "missing_missions": [5]}],
                                                   "dump_id": "d", "busy_source_cities": ["9"], "source_coords": {}, "active_targets": ["7"]}
        runner.execute({"job_id": "round", "game_account_id": "ga",
                        "inputs": {"city_ids": "100,9", "missions": "5", "__claimed_targets": {"7": {}, "8": {}}}})
        self.assertNotIn("8", runner.hub.reschedules[0]["__claimed_targets"])
        self.assertIn("7", runner.hub.reschedules[0]["__claimed_targets"])

    def test_rereads_take_at_most_a_quarter_of_the_free_safehouses(self):
        # N-173: 8 free houses, rich rereads first in the list: 2 of them, then the never read
        runner = WorldSpyRunner.__new__(WorldSpyRunner)
        rereads = [{"game_city_id": f"r{n}", "city_name": "R", "owner_name": f"o{n}", "missing_missions": [5],
                    "last_stock": 900000, "priority": 0} for n in range(6)]
        new = [{"game_city_id": f"n{n}", "city_name": "N", "owner_name": f"p{n}", "missing_missions": [5],
                "last_stock": None, "priority": 1} for n in range(6)]
        runner.hub = FakeHub(rereads + new)
        sent = []
        runner.hub.spawn_job = lambda parent_job_id, **kw: sent.append(kw["inputs"]["target_city_id"]) or {"job_id": "c"}
        runner.execute({"job_id": "round", "game_account_id": "ga",
                        "inputs": {"city_ids": ",".join(str(100 + n) for n in range(8)), "missions": "5"}})
        self.assertEqual(sum(1 for t in sent if t.startswith("r")), 2)
        self.assertEqual(len(sent), 8)

    def test_interval_zero_ends_the_campaign(self):
        result, _ = run({"pass_interval_hours": 0}, [])
        self.assertFalse(result.reschedule_seconds)


if __name__ == "__main__":
    unittest.main()
