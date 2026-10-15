"""N-130: the donation loop never tries to give more than the city has (construction keeps its part)."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from runners.donate_loop import DonateLoopRunner
from services.donation_automation import cap_donation, is_quantity_refused


class CapDonationTests(unittest.TestCase):
    def test_the_case_from_the_logs_gives_what_the_city_has(self):
        # planned 9,930 of wood (25% of a day's production), the construction left 4,000 and reserves 1,500
        given, carry = cap_donation(planned=9_930, remaining=2_278_241, stock=4_000, reserved=1_500, cycle_amount=9_930)
        self.assertEqual(given, 2_500)
        self.assertEqual(carry, 7_430)

    def test_never_more_than_the_island_still_needs(self):
        self.assertEqual(cap_donation(planned=9_000, remaining=3_000, stock=50_000, reserved=0, cycle_amount=9_000), (3_000, 0))

    def test_reservations_bigger_than_the_stock_give_nothing(self):
        self.assertEqual(cap_donation(planned=5_000, remaining=10_000, stock=2_000, reserved=3_000, cycle_amount=5_000), (0, 5_000))

    def test_carry_over_never_grows_past_one_cycle(self):
        # 3 cycles of 5,000 with nothing to give: the next one plans at most 5,000 + 5,000
        _given, carry = cap_donation(planned=15_000, remaining=100_000, stock=0, reserved=0, cycle_amount=5_000)
        self.assertEqual(carry, 5_000)

    def test_the_game_refusal_is_recognised(self):
        self.assertTrue(is_quantity_refused("Server errors for action IslandScreen&function=donate: ['Não podes doar uma quantidade dessas. Por favor verifica']"))
        self.assertFalse(is_quantity_refused("Connection reset by peer"))


class CityStockTests(unittest.TestCase):
    def test_every_resource_is_read_from_the_city_page(self):
        html = ('updateGlobalData({"headerData":{"currentResources":JSON.parse(\'{\\"citizens\\":1,\\"population\\":2,'
                '\\"resource\\":4321,\\"2\\":222,\\"1\\":111,\\"4\\":444,\\"3\\":333}\'),'
                'maxResources: JSON.parse(\'{\\"resource\\":100000,\\"1\\":100000}\'), resourceProduction: 0.5,'
                '["updateBackgroundData", {"islandId":"77","name":"Polis","tradegood":"3"}], ["updateTemplateData", {}]')
        runner = DonateLoopRunner.__new__(DonateLoopRunner)
        runner._current_client = SimpleNamespace(_server_url="https://s1.example", _request=MagicMock(return_value=SimpleNamespace(text=html)))
        stock = runner._fetch_city_resources("123")
        self.assertEqual((stock["wood"], stock["wine"], stock["marble"], stock["crystal"], stock["sulfur"]), (4321, 111, 222, 333, 444))

    def test_reservations_come_from_the_hub_and_never_break_the_cycle(self):
        runner = DonateLoopRunner.__new__(DonateLoopRunner)
        runner.log = MagicMock()
        runner.hub = MagicMock()
        runner.hub.get_construction_reservations.return_value = {"reservations": {"123": {"wood": 1500, "glas": 70}}}
        self.assertEqual(runner._reserved_for("j", "ga", "123", "wood"), 1500)
        self.assertEqual(runner._reserved_for("j", "ga", "123", "crystal"), 70)
        runner.hub.get_construction_reservations.side_effect = RuntimeError("hub fora")
        self.assertEqual(runner._reserved_for("j", "ga", "123", "wood"), 0)


if __name__ == "__main__":
    unittest.main()
