"""N-154: Dispensar Unidades never dismisses more than the city has and does nothing without the confirmation."""
import unittest

from runners.military import DismissUnitsRunner


class FakeClient:
    def __init__(self, counts):
        self.counts = counts                     # {city: {"troops": {...}, "fleet": {...}}}
        self.calls = []

    def fetch_stationed_units(self, city_id, building_type="troops"):
        return {"counts": dict(self.counts[city_id][building_type])}

    def dismiss_units(self, city_id, units, building_type="troops"):
        self.calls.append((city_id, building_type, dict(units)))
        for uid, qty in units.items():
            self.counts[city_id][building_type][uid] -= qty


class FakeHub:
    def __init__(self):
        self.logs = []

    def report_log(self, job_id, level, msg):
        self.logs.append((level, msg))


def run(inputs, client):
    runner = DismissUnitsRunner.__new__(DismissUnitsRunner)
    runner.hub = FakeHub()
    runner.resolve_credentials = lambda *a, **k: {"email": "x"}
    runner.get_or_login_game_client = lambda *a, **k: client
    result = runner.execute({"job_id": "j", "account_id": "a", "game_account_id": "g", "inputs": inputs})
    return result, runner.hub.logs


class DismissUnitsTests(unittest.TestCase):
    def test_troops_and_ships_are_capped_by_what_the_city_has(self):
        client = FakeClient({100: {"troops": {303: 44}, "fleet": {216: 2}}})
        result, logs = run({"confirm": True, "dismiss": '{"100": {"303": 10, "216": 5}}'}, client)
        self.assertTrue(result.success)
        self.assertEqual(client.calls, [(100, "troops", {303: 10}), (100, "fleet", {216: 2})])
        self.assertEqual(result.data["dismissed"], {"100": {"303": 10, "216": 2}})

    def test_nothing_happens_without_the_confirmation(self):
        client = FakeClient({100: {"troops": {303: 44}, "fleet": {}}})
        result, _ = run({"dismiss": {"100": {"303": 10}}}, client)
        self.assertFalse(result.success)
        self.assertEqual(client.calls, [])

    def test_a_unit_the_city_does_not_have_is_skipped(self):
        client = FakeClient({100: {"troops": {303: 0}, "fleet": {}}})
        result, logs = run({"confirm": True, "dismiss": {"100": {"303": 10}}}, client)
        self.assertEqual(client.calls, [])
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
