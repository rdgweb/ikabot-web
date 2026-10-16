"""N-145: the Militar tab names every unit (and shows its picture) even where a move wrote the unit id."""
from django.test import SimpleTestCase

from apps.game.views.dashboard import _named_units


class NamedUnitsTests(SimpleTestCase):
    def test_ids_become_names_and_add_up(self):
        self.assertEqual(_named_units({"303": 50, "Hoplita": 10, "216": 46, "Espadachim": 0}),
                         {"Hoplita": 60, "Aríete a Vapor": 46})

    def test_unknown_or_empty(self):
        self.assertEqual(_named_units({"999": 3}), {"999": 3})
        self.assertEqual(_named_units(None), {})
