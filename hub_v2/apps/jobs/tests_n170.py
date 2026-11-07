"""N-170/N-171/N-172: the piracy job detail says what the job created and since when, when the ranking turns and the
final phase; the rich target alert counts the trip the way the game does."""
import json
import time
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.espionage.services import travel
from apps.jobs.views.jobs import JobListView


def _job(**inputs):
    return SimpleNamespace(action_code=17, inputs_json=json.dumps(inputs))


class PiracyDetailTests(SimpleTestCase):
    def test_created_crew_points_since_and_share(self):
        since = int(time.time()) - 2 * 86400
        shown = JobListView._piracy_display(_job(
            last_capture_points=1134, last_complete_crew_points=8669, total_crew_converted=226,
            last_conversion_factor=10, piracy_stats_since=since))
        self.assertEqual((shown["crew_created"], shown["points_spent"], shown["crew_per_day"]), (226, 2260, 113))
        self.assertEqual(shown["crew_created_pct"], 3)
        self.assertIsNotNone(shown["stats_since"])

    def test_ranking_clock_and_final_phase(self):
        now = int(time.time())
        waiting = JobListView._piracy_display(_job(
            piracy_highscore_time_left=100 * 3600, piracy_highscore_updated_at=now - 3600,
            final_phase_enabled=True, final_phase_hours=72))
        self.assertAlmostEqual(waiting["ranking_left"], 99 * 3600, delta=5)
        self.assertFalse(waiting["final_phase_active"])
        self.assertTrue(waiting["final_phase_in_human"])
        active = JobListView._piracy_display(_job(
            piracy_highscore_time_left=10 * 3600, piracy_highscore_updated_at=now,
            final_phase_enabled=True, final_phase_hours=72))
        self.assertTrue(active["final_phase_active"])


class TravelTests(SimpleTestCase):
    def test_merchant_trip_matches_the_measured_transports(self):
        self.assertEqual(travel.journey_seconds(1, 60), 600)
        self.assertEqual(travel.journey_seconds(0, 60), 300)

    def test_troops_go_at_their_slowest_unit(self):
        self.assertAlmostEqual(travel.troops_journey(16.97, [305, 303]), 20365, delta=5)   # the raid of 07/11
        self.assertEqual(travel.troops_journey(30, [303]), 24000)                          # hoplites (90)
        self.assertEqual(travel.troops_journey(30, [303, 309]), 72000)                     # a balloon (30)
        self.assertEqual(travel.fleet_journey(30, [210, 215]), 36000)      # mortar ship (30)
        self.assertEqual(travel.human(36000), "10h")
        self.assertEqual(travel.human(5400), "1h30")
