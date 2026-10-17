"""N-149: pirate raids are told apart from attacks. N-148: a miracle activated from one temple marks the other
temples of the same island, and a rule that finds the miracle unavailable is not an error."""
import unittest
from unittest.mock import MagicMock

from runners.economy import ActivateMiracleRunner
from runners.monitoring import mission_kind


class MissionKindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(mission_kind("", "Assalto (em curso)"), "pirate")
        self.assertEqual(mission_kind("piracyRaid", "x"), "pirate")
        self.assertEqual(mission_kind("plunder", "Roubar (em curso)"), "attack")
        self.assertEqual(mission_kind("", "Ocupar cidade"), "attack")


class IslandMiracleTests(unittest.TestCase):
    def test_the_other_temples_of_the_island_are_marked(self):
        runner = ActivateMiracleRunner.__new__(ActivateMiracleRunner)
        runner.hub = MagicMock()
        runner.log = MagicMock()
        snapshot = {"base_snapshot": {}, "military": {}, "cities": [
            {"id": 1, "island_id": 9, "miracle_state": {"activate_state": "enabled", "wonder_name": "Templo de Poseidon"}},
            {"id": 2, "island_id": 9, "miracle_state": {"activate_state": "enabled", "wonder_name": "Templo de Poseidon"}},
            {"id": 3, "island_id": 7, "miracle_state": {"activate_state": "enabled", "wonder_name": "Colosso"}},
        ]}
        runner._persist_miracle_state(job_id="j", account_id="a", game_account_id="g", snapshot=snapshot, city_id="1",
                                      state={"activate_state": "hidden", "cooldown_text": "Regeneração: 1D"}, last_status="activated")
        cities = {c["id"]: c for c in runner.hub.update_snapshot.call_args[0][1]["cities"]}
        self.assertEqual(cities[2]["miracle_state"]["activate_state"], "hidden")          # same island: not available
        self.assertEqual(cities[2]["miracle_state"]["last_status"], "activated_elsewhere")
        self.assertEqual(cities[3]["miracle_state"]["activate_state"], "enabled")         # another island: untouched


if __name__ == "__main__":
    unittest.main()
