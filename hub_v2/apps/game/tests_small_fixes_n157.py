"""N-145, N-156, N-157, N-158: unit pictures and names, the tab title on internal navigation, the city page text."""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.combat.services.report_parser import unit_name
from core.catalogs import get_unit_info


class UnitPicturesTests(SimpleTestCase):
    def test_every_ship_is_a_ship_and_never_a_hoplite(self):
        for name in ("Reparador", "Barco Mercante", "Cargueiro", "Trireme", "Submergível", "Pironavio", "Rachador a Remos"):
            info = get_unit_info(name)
            self.assertEqual(info["type"], "ship", name)
            self.assertNotIn("hoplita", info["icon"], name)
        self.assertEqual(get_unit_info("Reparador")["icon"], "game/units/barcoreparador.png")

    def test_the_raid_alert_names_the_defending_units(self):
        self.assertEqual(unit_name("308"), "Gigante a Vapor")
        self.assertEqual(unit_name(301), "Fundeiro")


class CityPageTextTests(SimpleTestCase):
    def test_no_broken_accents(self):
        html = (Path(__file__).resolve().parents[1] / "worldintel/templates/worldintel/city_detail.html").read_text("utf-8")
        self.assertFalse("??" in html, "texto quebrado na página da cidade")
        self.assertIn("ação {{ report.action_job_id", html)


class TabTitleTests(TestCase):
    def test_internal_navigation_brings_the_page_title(self):
        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x", is_staff=True))
        html = self.client.get("/espionage/", HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true").content.decode()
        self.assertTrue(html.lstrip().startswith("<title>"), html[:200])
        self.assertNotIn("<!DOCTYPE", html)
