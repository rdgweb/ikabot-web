"""N-101: the game panel must start whether it is opened by a full load or through the menu.

The menu navigation swaps only the page content, and Alpine starts the new content
before an external script that arrives with it has loaded. So the panel's components
live in the layout, which every full load brings, and never inside the swapped content.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

PANEL_SCRIPTS = ("js/game-dashboard-modals.js", "js/game-dashboard-charts.js", "js/game-dashboard-core.js")


def _local_scripts(html: str) -> list[str]:
    return [src for src in re.findall(r"""<script[^>]*src=["']([^"']+)""", html) if not src.startswith("http")]


class GamePanelScriptsTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("painel", password="x"))

    def test_every_full_page_brings_the_panel_components(self):
        for path in ("/", "/game/", "/jobs/"):
            html = self.client.get(path).content.decode()
            sources = _local_scripts(html)
            for script in PANEL_SCRIPTS:
                self.assertEqual(sum(script in src for src in sources), 1, f"{script} em {path}")
            # they come before the page content can be started by Alpine (which is deferred)
            self.assertIn("alpine.min.js", "".join(sources))

    def test_the_content_swapped_by_the_menu_does_not_depend_on_a_script_arriving_with_it(self):
        html = self.client.get("/game/", HTTP_HX_BOOSTED="true", HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('x-data="gameDashboard()"', html)
        self.assertNotIn("<html", html)                       # only the page content, as the menu gets it
        self.assertEqual(_local_scripts(html), [])            # nothing local left to load after the swap
