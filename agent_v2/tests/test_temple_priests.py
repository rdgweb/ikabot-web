"""N-141: priests are set like scientists, and each island's miracle (level, faith, who takes part) is read and kept."""
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from game_client.actions.miracle import MiracleAction
from runners.economy import AdjustPriestsRunner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "wonder_view_s78.json"


def _temple(priests, *, maximum=900, citizens=2000, per_priest=7, inhabitants=3746, island=4478, name="Cidade"):
    return {"city_name": name, "island_id": island, "priests": priests, "max_priests": maximum, "citizens": citizens,
            "citizens_per_priest": per_priest, "max_inhabitants": inhabitants, "wonder_name": "Bosque Sagrado de Hades",
            "activate_state": "disabled", "own_conversion_percent": 0, "island_conversion_percent": 0}


class WonderViewTests(unittest.TestCase):
    def test_the_island_miracle_view_is_read(self):
        state = MiracleAction.parse_wonder_payload(json.loads(FIXTURE.read_text(encoding="utf-8")), island_id=4478)
        self.assertEqual((state["level"], state["max_level_reached"], state["belief_level"], state["faith_percent"]), (5, True, 2, 56.0))
        self.assertFalse(state["upgrading"])
        own = [p for p in state["participants"] if p["city_id"]]
        self.assertTrue(own)
        self.assertEqual(own[1]["priests"], 625)
        self.assertEqual((own[1]["conversion_percent"], own[1]["island_share_percent"], own[1]["temple_position"]), (116.8, 11.9, 16))
        self.assertTrue(all(p["player"] for p in state["participants"]))   # a player's later rows keep the name


class DesiredPriestsTests(unittest.TestCase):
    def test_each_mode(self):
        pick = AdjustPriestsRunner.desired_priests
        self.assertEqual(pick(_temple(100), "absolute", 300, 0), 300)
        self.assertEqual(pick(_temple(100), "percent_max", 50, 0), 450)
        self.assertEqual(pick(_temple(100), "own_conversion", 100, 0), 536)        # 3.746 inhabitants / 7 per priest
        self.assertEqual(pick(_temple(100), "keep", 0, 0), 100)

    def test_never_beyond_the_maximum_or_the_free_citizens(self):
        pick = AdjustPriestsRunner.desired_priests
        self.assertEqual(pick(_temple(100, maximum=200), "absolute", 999, 0), 200)
        self.assertEqual(pick(_temple(100, citizens=50), "absolute", 999, 10), 140)  # 40 free after the reserve


class AdjustPriestsRunnerTests(unittest.TestCase):
    def _runner(self, snapshot):
        runner = AdjustPriestsRunner(hub=MagicMock(), sessions=MagicMock())
        runner.hub.get_snapshot.return_value = snapshot
        runner.resolve_credentials = MagicMock(return_value={"server": "s1", "email": "a@b.com", "password": "x"})
        runner.save_game_client = MagicMock()
        runner.log = MagicMock()
        return runner

    @patch("runners.economy.time.sleep")
    def test_sets_who_needs_reads_each_island_once_and_writes_once(self, _sleep):
        snapshot = {"base_snapshot": {}, "military": {}, "cities": [
            {"id": 1, "name": "A", "island_id": 4478, "buildings": [{"building": "temple", "position": 16}]},
            {"id": 2, "name": "B", "island_id": 4478, "buildings": [{"building": "temple", "position": 7}]},
            {"id": 3, "name": "C", "island_id": 4479, "buildings": []},
        ]}
        runner = self._runner(snapshot)
        client = MagicMock()
        states = {1: [_temple(100), _temple(300)], 2: [_temple(300)]}
        client.get_temple_miracle_state.side_effect = lambda city_id, position: states[city_id].pop(0)
        client.get_wonder_state.return_value = {"level": 5, "max_level_reached": True, "faith_percent": 56.0, "participants": []}
        runner.get_or_login_game_client = MagicMock(return_value=client)
        result = runner.execute({"job_id": "j", "account_id": "a", "game_account_id": "g",
                                 "inputs": {"target_mode": "absolute", "target_value": 300}})
        self.assertTrue(result.success)
        client.set_priests.assert_called_once_with(1, 16, 300)
        client.get_wonder_state.assert_called_once_with(4478, 1)
        runner.hub.update_snapshot.assert_called_once()
        written = runner.hub.update_snapshot.call_args.args[1]
        self.assertEqual(set(written["base_snapshot"]["island_wonders"]), {"4478"})
        self.assertEqual([c.get("miracle_state", {}).get("priests") for c in written["cities"]], [300, 300, None])

    @patch("runners.economy.time.sleep")
    def test_only_reading_never_changes_priests(self, _sleep):
        snapshot = {"base_snapshot": {}, "military": {}, "cities": [
            {"id": 1, "name": "A", "island_id": 4478, "buildings": [{"building": "temple", "position": 16}]}]}
        runner = self._runner(snapshot)
        client = MagicMock()
        client.get_temple_miracle_state.return_value = _temple(10)
        client.get_wonder_state.return_value = {"level": 1, "max_level_reached": False, "faith_percent": 0.0, "participants": []}
        runner.get_or_login_game_client = MagicMock(return_value=client)
        runner.execute({"job_id": "j", "account_id": "a", "game_account_id": "g", "inputs": {"target_mode": "keep"}})
        client.set_priests.assert_not_called()


if __name__ == "__main__":
    unittest.main()
