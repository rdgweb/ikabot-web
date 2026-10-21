"""N-155: the KPI modals' bars keep their colour (Alpine's :style replaced the static style) and the richest
cities table stays inside its card."""
import re
from pathlib import Path

from django.test import SimpleTestCase

PARTIALS = Path(__file__).resolve().parent / "templates/game/partials"


class KpiBarsTests(SimpleTestCase):
    def test_no_bar_has_its_colour_in_a_static_style_next_to_a_dynamic_one(self):
        for path in PARTIALS.glob("modal_*.html"):
            html = path.read_text("utf-8")
            self.assertFalse(re.search(r'style="[^"]*"[^>]*:style=', html), path.name)

    def test_the_richest_cities_table_fits_its_column(self):
        html = (PARTIALS / "modal_resources.html").read_text("utf-8")
        self.assertNotIn("min-width:560px", html)
        self.assertIn("table-layout: fixed", html)
        self.assertIn('class="gm-table overflow-x-auto rounded-lg" style="min-width: 0;"', html)   # .gm-table has 580px
        self.assertIn(".ik-two-cols > * { min-width: 0; }", html)
