"""The garrison report (mission 6) with units in the city is read (it broke the report collection: _NAME_TO_ID)."""
import unittest

from game_client.actions.spy import _parse_troops_from_html

HTML = (
    '<table class="reportTable"><tr><th class="barracks">Quartel</th>'
    '<th><img alt="Hoplita"></th><th><img alt="Morteiro"></th></tr>'
    '<tr><td>Tropas em Polis</td><td class="count"><b>120</b></td><td class="count"><b>6</b></td></tr></table>'
)


class GarrisonReportTests(unittest.TestCase):
    def test_units_are_read_with_their_ids(self):
        units = {u["name"]: (u["count"], u["unit_id"]) for u in _parse_troops_from_html(HTML)[0]["units"]}
        self.assertEqual(units, {"Hoplita": (120, 303), "Morteiro": (6, 305)})


if __name__ == "__main__":
    unittest.main()
