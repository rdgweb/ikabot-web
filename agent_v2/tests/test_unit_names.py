"""N-145: what the agent patches into the snapshot after moving units is written with the unit names."""
import unittest

from runners.military import unit_names_by_id


class UnitNamesTests(unittest.TestCase):
    def test_ids_have_the_game_names(self):
        names = unit_names_by_id()
        self.assertEqual(names[303], "Hoplita")
        self.assertEqual(names[216], "Aríete a Vapor")
        self.assertEqual(names[217], "Lança-Foguetes")


if __name__ == "__main__":
    unittest.main()
