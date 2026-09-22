"""N-42: multi-city modify_production (ac=23) must not be all-or-nothing — each city
gets its own retry, a humanized pause separates cities, and the job only fails when
every city failed."""

import unittest
from unittest.mock import MagicMock, call, patch

from runners.resources import ModifyProductionRunner


def _job(city_ids):
    return {
        "job_id": "job-1",
        "account_id": "acc-1",
        "game_account_id": "ga-1",
        "inputs": {"cities": city_ids, "sawmill_percent": "100", "luxury_percent": "100"},
    }


class ModifyProductionMultiCityTests(unittest.TestCase):
    def setUp(self):
        self.runner = ModifyProductionRunner(hub=MagicMock(), sessions=MagicMock())
        self.runner.resolve_credentials = MagicMock(return_value={"server": "s1", "email": "a@b.com", "password": "x"})
        self.runner.get_snapshot = MagicMock(return_value=None)
        self.runner.get_or_login_game_client = MagicMock(return_value=MagicMock())
        self.runner.save_game_client = MagicMock()
        self.runner.log = MagicMock()

    def _entry(self, city_id):
        return {"city_id": city_id, "city_name": f"City {city_id}"}

    @patch("runners.resources.time.sleep")
    def test_single_city_success_does_not_sleep(self, sleep):
        self.runner._apply_one_city = MagicMock(return_value=self._entry("1"))

        result = self.runner.execute(_job(["1"]))

        self.assertTrue(result.success)
        self.assertEqual(result.data["cities"], [self._entry("1")])
        self.assertEqual(result.data["failed_cities"], [])
        sleep.assert_not_called()

    @patch("runners.resources.random.uniform", return_value=3.0)
    @patch("runners.resources.time.sleep")
    def test_pauses_between_cities_but_not_before_the_first_one(self, sleep, _uniform):
        self.runner._apply_one_city = MagicMock(side_effect=[self._entry("1"), self._entry("2")])

        result = self.runner.execute(_job(["1", "2"]))

        self.assertTrue(result.success)
        self.assertEqual(len(result.data["cities"]), 2)
        sleep.assert_called_once_with(3.0)  # one pause, between city 1 and city 2

    @patch("runners.resources.random.uniform", return_value=0)
    @patch("runners.resources.time.sleep")
    def test_retries_once_on_failure_then_succeeds_without_dropping_the_city(self, sleep, _uniform):
        self.runner._apply_one_city = MagicMock(
            side_effect=[RuntimeError("rate limit da ilha"), self._entry("1")],
        )

        result = self.runner.execute(_job(["1"]))

        self.assertTrue(result.success)
        self.assertEqual(self.runner._apply_one_city.call_count, 2)
        self.assertEqual(result.data["cities"], [self._entry("1")])
        self.assertEqual(result.data["failed_cities"], [])
        # retry backoff (~10s) was slept, on top of whatever inter-city pause logic ran
        self.assertIn(call(10), sleep.call_args_list)

    @patch("runners.resources.random.uniform", return_value=0)
    @patch("runners.resources.time.sleep")
    def test_one_city_failing_both_attempts_does_not_block_the_others(self, sleep, _uniform):
        self.runner._apply_one_city = MagicMock(
            side_effect=[
                RuntimeError("falha 1a"), RuntimeError("falha 1b"),  # city "1": both attempts fail
                self._entry("2"),  # city "2": succeeds on first try
            ],
        )

        result = self.runner.execute(_job(["1", "2"]))

        self.assertTrue(result.success)  # partial success is still success=True
        self.assertEqual(result.data["cities"], [self._entry("2")])
        self.assertEqual(len(result.data["failed_cities"]), 1)
        self.assertEqual(result.data["failed_cities"][0]["city_id"], "1")
        self.assertIn("falha 1b", result.data["failed_cities"][0]["error"])

    @patch("runners.resources.random.uniform", return_value=0)
    @patch("runners.resources.time.sleep")
    def test_every_city_failing_returns_an_error_result(self, sleep, _uniform):
        self.runner._apply_one_city = MagicMock(side_effect=RuntimeError("sempre falha"))

        result = self.runner.execute(_job(["1", "2"]))

        self.assertFalse(result.success)
        self.assertEqual(result.data["cities"], [])
        self.assertEqual(len(result.data["failed_cities"]), 2)

    def test_login_failure_still_fails_fast_without_touching_apply_one_city(self):
        self.runner.get_or_login_game_client = MagicMock(side_effect=RuntimeError("login falhou"))
        self.runner._apply_one_city = MagicMock()

        result = self.runner.execute(_job(["1"]))

        self.assertFalse(result.success)
        self.runner._apply_one_city.assert_not_called()


if __name__ == "__main__":
    unittest.main()
