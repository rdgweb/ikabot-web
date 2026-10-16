"""N-141: Poseidon or Hermes when a shipment takes most of the account's ships."""
import unittest
from unittest.mock import MagicMock

from runners import miracle_speed
from runners.miracle_speed import maybe_ask_speed_miracle, ship_share_reached, speed_miracle_city

RULES = {"speed_on_shipments": True, "ship_share": 50, "speed_miracles": ["Templo de Poseidon", "Templo de Hermes"]}


def _snapshot(state="enabled", max_transporters=40):
    return {
        "base_snapshot": {"max_transporters": max_transporters, "max_freighters": 4},
        "cities": [
            {"id": 1, "name": "Sem templo"},
            {"id": 7, "name": "Porto", "miracle_state": {"wonder_name": "Templo de Poseidon (Nível 2/5)", "activate_state": state}},
        ],
    }


class SpeedMiracleTests(unittest.TestCase):
    def setUp(self):
        miracle_speed._asked_at.clear()
        self.runner = MagicMock()

    def _job(self, rules=RULES):
        return {"job_id": "j1", "game_account_id": "ga1", "miracle_rules": rules}

    def test_the_share_of_ships(self):
        self.assertTrue(ship_share_reached(RULES, 20, 40))
        self.assertFalse(ship_share_reached(RULES, 19, 40))
        self.assertFalse(ship_share_reached({**RULES, "speed_on_shipments": False}, 40, 40))
        self.assertFalse(ship_share_reached(RULES, 20, 0))

    def test_only_an_available_miracle_is_asked(self):
        self.assertEqual(speed_miracle_city(_snapshot(), RULES["speed_miracles"]), ("7", "Templo de Poseidon"))
        self.assertIsNone(speed_miracle_city(_snapshot("disabled"), RULES["speed_miracles"]))

    def test_a_big_shipment_spawns_ativar_milagre_once(self):
        self.assertTrue(maybe_ask_speed_miracle(self.runner, self._job(), _snapshot(), 25))
        self.runner.hub.spawn_job.assert_called_once()
        args, kwargs = self.runner.hub.spawn_job.call_args
        self.assertEqual((args[0], kwargs["action_code"], kwargs["inputs"]["city"]), ("j1", 11, "7"))
        self.assertFalse(maybe_ask_speed_miracle(self.runner, self._job(), _snapshot(), 25))   # not again soon
        self.assertEqual(self.runner.hub.spawn_job.call_count, 1)

    def test_small_shipments_and_jobs_without_the_rule_ask_nothing(self):
        self.assertFalse(maybe_ask_speed_miracle(self.runner, self._job(), _snapshot(), 5))
        self.assertFalse(maybe_ask_speed_miracle(self.runner, self._job({}), _snapshot(), 40))
        self.assertTrue(maybe_ask_speed_miracle(self.runner, self._job(), _snapshot(), 2, freighters=True))   # 2 of 4 freighters
        self.runner.hub.spawn_job.assert_called_once()


if __name__ == "__main__":
    unittest.main()
