"""N-140: one's own troops and warships on their way are kept, so a move between cities is not taken as a loss."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.combat_watch import own_units_in_transit  # noqa: E402

NOW = 1_000_000


def _move(own=True, hostile=False, troops=0, ships=None, fleet_amount=0, resources=None, eta=NOW + 600, returning=False):
    return {
        "isOwnArmyOrFleet": own, "isHostile": hostile, "eventTime": eta,
        "event": {"missionText": "Estacionar", "isReturning": returning},
        "origin": {"name": "M4"}, "target": {"name": "M1"},
        "army": {"amount": troops},
        "fleet": {"amount": fleet_amount, "ships": ships or []},
        "resources": resources or [],
    }


class UnitsInTransitTests(unittest.TestCase):
    def test_own_troops_and_warships_count_merchants_do_not(self):
        moves = [
            _move(troops=50),
            _move(ships=[{"cssClass": "ship_ram", "amount": "3"}, {"cssClass": "ship_transport", "amount": "12"}]),
            _move(fleet_amount=8, resources=[{"amount": "5.000"}]),        # a trade transport: merchants only
            _move(own=False, hostile=True, troops=900),                      # someone else's attack
            _move(troops=10, eta=NOW - 5),                                   # already arrived
        ]
        out = own_units_in_transit(moves, NOW)
        self.assertEqual([(item["troops"], item["ships"]) for item in out], [(50, 0), (0, 3)])
        self.assertEqual(out[0]["origin"], "M4")

    def test_returning_units_count_too(self):
        out = own_units_in_transit([_move(troops=7, returning=True)], NOW)
        self.assertTrue(out[0]["returning"])


if __name__ == "__main__":
    unittest.main()
