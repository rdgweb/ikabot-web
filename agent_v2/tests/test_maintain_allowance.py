"""N-162: the Manter never trains again what is already in a queue for the cities without a shipyard."""
import unittest

from runners.military import TrainUnitsRunner

ALLOW = TrainUnitsRunner._training_allowance
CITIES = [1, 2, 3, 4, 5]                       # 1 = Alexa Trops (shipyard), the others have none
TARGETS = {c: {"211": 10} for c in CITIES}


class TrainingAllowanceTests(unittest.TestCase):
    def test_nothing_anywhere_the_whole_account_lacks_50(self):
        self.assertEqual(ALLOW(CITIES, TARGETS, {}, {}), {"211": 50})

    def test_the_50_in_the_shipyard_queue_cover_the_other_cities(self):
        # Juggernaut81, second cycle: 50 Lança-Chamas training in Alexa Trops for everybody
        self.assertEqual(ALLOW(CITIES, TARGETS, {}, {1: {"211": 50}}), {"211": 0})

    def test_trained_and_waiting_in_the_producer_count_too(self):
        self.assertEqual(ALLOW(CITIES, TARGETS, {1: {"211": 30}}, {1: {"211": 10}}), {"211": 10})

    def test_units_of_other_kinds_do_not_count(self):
        self.assertEqual(ALLOW(CITIES, TARGETS, {1: {"210": 50}}, {}), {"211": 50})


if __name__ == "__main__":
    unittest.main()
