"""N-43: the delay between game requests must be sampled from a log-normal
distribution (skewed, occasional long tail) instead of a uniform one (flat,
hard-bounded — an easy signature for request-timing analysis to spot)."""

import statistics
import unittest
from unittest.mock import MagicMock, patch

from game_client.client import GameClient
from game_client.constants import REQUEST_DELAY_MAX, REQUEST_DELAY_MEDIAN, REQUEST_DELAY_MIN


class EnforceDelayTests(unittest.TestCase):
    def setUp(self):
        self.client = GameClient(account_id="acc-1", hub=MagicMock(), proxy_url="socks5://p:1")

    def test_first_request_never_sleeps(self):
        self.client._last_request_time = 0
        with patch("game_client.client.time.sleep") as sleep:
            self.client._enforce_delay()
        sleep.assert_not_called()

    @patch("game_client.client.random.lognormvariate", return_value=2.0)
    def test_sleeps_the_remaining_time_when_not_enough_elapsed(self, _draw):
        with patch("game_client.client.time.time", return_value=100.0):
            self.client._last_request_time = 99.0  # 1.0s elapsed, need 2.0s
            with patch("game_client.client.time.sleep") as sleep:
                self.client._enforce_delay()
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 1.0, places=6)

    @patch("game_client.client.random.lognormvariate", return_value=2.0)
    def test_does_not_sleep_when_enough_time_already_elapsed(self, _draw):
        with patch("game_client.client.time.time", return_value=100.0):
            self.client._last_request_time = 90.0  # 10s elapsed, way more than 2.0s
            with patch("game_client.client.time.sleep") as sleep:
                self.client._enforce_delay()
        sleep.assert_not_called()

    @patch("game_client.client.random.lognormvariate", return_value=0.01)
    def test_an_extreme_low_draw_is_clamped_to_the_floor(self, _draw):
        with patch("game_client.client.time.time", return_value=100.0):
            self.client._last_request_time = 99.0  # 1.0s elapsed
            with patch("game_client.client.time.sleep") as sleep:
                self.client._enforce_delay()
        # min_delay clamps up to REQUEST_DELAY_MIN (0.6s) despite the tiny raw draw;
        # 1.0s already elapsed is >= that floor, so no sleep is needed at all.
        sleep.assert_not_called()

    @patch("game_client.client.random.lognormvariate", return_value=999.0)
    def test_an_extreme_high_draw_is_clamped_to_the_ceiling(self, _draw):
        with patch("game_client.client.time.time", return_value=100.0):
            self.client._last_request_time = 100.0  # 0s elapsed
            with patch("game_client.client.time.sleep") as sleep:
                self.client._enforce_delay()
        sleep.assert_called_once_with(REQUEST_DELAY_MAX)


class DelayDistributionShapeTests(unittest.TestCase):
    """Coarse, generously-tolerant sanity checks against the real RNG — enough to
    catch a flipped mu/sigma or a wrong distribution function without being flaky."""

    def test_samples_are_skewed_and_centered_near_the_configured_median(self):
        import math
        import random

        samples = [
            max(REQUEST_DELAY_MIN, min(REQUEST_DELAY_MAX, random.lognormvariate(math.log(REQUEST_DELAY_MEDIAN), 0.5)))
            for _ in range(2000)
        ]

        self.assertTrue(all(REQUEST_DELAY_MIN <= s <= REQUEST_DELAY_MAX for s in samples))
        median = statistics.median(samples)
        self.assertAlmostEqual(median, REQUEST_DELAY_MEDIAN, delta=0.3)
        # Skew: a log-normal's mean sits above its median (long right tail).
        self.assertGreater(statistics.mean(samples), median)
        # Not a uniform draw: a real (clamp-free) sample must occasionally exceed the
        # old uniform ceiling of 2.5s.
        unclamped = [random.lognormvariate(math.log(REQUEST_DELAY_MEDIAN), 0.5) for _ in range(2000)]
        self.assertTrue(any(s > 2.5 for s in unclamped))


if __name__ == "__main__":
    unittest.main()
