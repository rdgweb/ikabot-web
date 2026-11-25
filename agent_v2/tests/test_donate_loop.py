"""N-130: the donation loop never tries to give more than the city has (construction keeps its part)."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from requests.exceptions import ReadTimeout

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


class NetworkRetryTests(unittest.TestCase):
    def test_game_client_never_reposts_donation_after_lost_response(self):
        from game_client.client import GameClient
        from game_client.exceptions import GameClientError
        from requests.exceptions import ConnectionError

        for error in (ReadTimeout("Read timed out"), ConnectionError("Connection reset")):
            with self.subTest(error=type(error).__name__):
                client = GameClient.__new__(GameClient)
                client._enforce_delay = MagicMock()
                client.session = MagicMock()
                client.session.request.side_effect = error
                with self.assertRaises(GameClientError):
                    client._request("POST", "https://game.invalid", data={"action": "IslandScreen", "function": "donate"})
                client.session.request.assert_called_once()

    def test_read_requests_keep_their_network_retries(self):
        from game_client.client import GameClient
        client = GameClient.__new__(GameClient)
        client._enforce_delay = MagicMock()
        client.session = MagicMock()
        response = MagicMock(status_code=200)
        client.session.request.side_effect = [ReadTimeout("timeout"), response]
        client.captcha_detector = MagicMock()
        client.captcha_detector.check_response.return_value = False
        with patch("game_client.client.time.sleep"):
            self.assertIs(client._request("GET", "https://game.invalid"), response)
        self.assertEqual(client.session.request.call_count, 2)

    def runner_and_job(self):
        runner = DonateLoopRunner.__new__(DonateLoopRunner)
        runner.log = MagicMock()
        runner.resolve_credentials = MagicMock(return_value={"email": "test"})
        runner.get_or_login_game_client = MagicMock()
        job = {"job_id": "j", "account_id": "a", "inputs": {
            "city_id": "123", "donation_type": "wood", "donation_method": 2,
            "method_value": 25, "carry_over_amount": 12, "random_wait_minutes": 0,
        }}
        return runner, job

    def test_login_timeout_preserves_inputs_and_waits_five_minutes(self):
        runner, job = self.runner_and_job()
        runner.get_or_login_game_client.side_effect = ReadTimeout("Read timed out. (read timeout=30)")
        result = runner.execute(job)
        self.assertFalse(result.success)
        self.assertEqual(result.reschedule_seconds, 300)
        self.assertEqual(result.reschedule_inputs, job["inputs"])
        self.assertTrue(result.data["retryable"])
        self.assertFalse(result.data["donation_outcome_unknown"])

    def test_submission_timeout_does_not_immediately_repeat_donation(self):
        runner, job = self.runner_and_job()
        client = runner.get_or_login_game_client.return_value
        client.donate.side_effect = ReadTimeout("Read timed out")
        runner._compute_donation_amount = MagicMock(return_value=100)
        runner._fetch_city_resources = MagicMock(return_value={"wood": 1000})
        runner._reserved_for = MagicMock(return_value=0)
        with patch("runners.donate_loop.fetch_donation_project_state", return_value={
            "remaining": 10000, "is_upgrading": False, "island_id": "77",
        }), patch("runners.donate_loop.fetch_worker_baseline"), patch("runners.donate_loop.fetch_city_context", return_value={}):
            result = runner.execute(job)
        client.donate.assert_called_once()
        self.assertFalse(result.success)
        self.assertEqual(result.reschedule_seconds, 86400)
        self.assertTrue(result.data["donation_outcome_unknown"])
        self.assertEqual(result.reschedule_inputs["carry_over_amount"], 0)


if __name__ == "__main__":
    unittest.main()
