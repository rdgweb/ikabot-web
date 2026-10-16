"""N-147: the garrison read by Maintain counts every unit table of the military view, not only the first."""
import unittest

from game_client.actions.military import _parse_city_military_counts


def _table(units):
    heads = "".join(
        f'<th><div class="army s{uid}"><div class="tooltip">{name}</div></div></th>' for uid, name, _ in units
    )
    counts = "".join(f"<td>{qty}</td>" for _, _, qty in units)
    return (f'<table class="table01 militaryList"><tr><th></th>{heads}</tr>'
            f'<tr class="count"><td>Cidade</td>{counts}</tr></table>')


HTML = (
    _table([(303, "Hoplita", "50"), (308, "Gigante a Vapor", "-"), (315, "Lanceiro", "0"), (302, "Espadachim", "1.200")])
    + _table([(307, "Aríete", "-"), (305, "Morteiro", "24"), (310, "Cozinheiro", "31"), (311, "Médico", "31")])
)


class CityMilitaryCountsTests(unittest.TestCase):
    def test_both_tables_are_counted(self):
        self.assertEqual(_parse_city_military_counts(HTML, "troops"), {303: 50, 302: 1200, 305: 24, 310: 31, 311: 31})

    def test_ships_are_not_mixed_with_troops(self):
        self.assertEqual(_parse_city_military_counts(HTML, "fleet"), {})


if __name__ == "__main__":
    unittest.main()
