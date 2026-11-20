"""N-184: the Generals Bank runners — the bank does not sleep holding the agent, a failing task tells the hub why, a
refused request from the hub is read, and a timeout ends the cycle."""
import unittest
from unittest.mock import MagicMock

from runners.generals_bank import GeneralsBankBuyRunner, GeneralsBankManageRunner, GeneralsBankProducerTaskRunner


def runner(cls, hub):
    r = cls.__new__(cls)
    r.hub = hub
    r.log = lambda *a, **k: None
    r.checkpoint = lambda *a, **k: None
    return r


class BuyTests(unittest.TestCase):
    def test_the_wait_for_the_offers_is_a_reschedule_not_a_sleep(self):
        hub = MagicMock()
        r = runner(GeneralsBankBuyRunner, hub)
        r.resolve_credentials = MagicMock(side_effect=AssertionError("must not log in yet"))
        result = r.execute({"job_id": "j", "account_id": "a", "inputs": {"cycle_id": "c", "offers": [{"unit_id": 303}]}})
        self.assertTrue(result.success)
        self.assertEqual(result.reschedule_seconds, 90)
        self.assertTrue(result.reschedule_inputs["_waited"])


class ProducerTaskTests(unittest.TestCase):
    def test_a_task_without_black_market_fails_with_its_reason(self):
        hub = MagicMock()
        hub.bank_get_cycle_status.return_value = {"ok": True, "tasks": [
            {"task_id": "t1", "status": "training", "city_id": 20, "bm_city_id": 21, "unit_id": 303, "quantity_target": 5}]}
        r = runner(GeneralsBankProducerTaskRunner, hub)
        result = r.execute({"job_id": "j", "account_id": "a", "inputs": {"cycle_id": "c", "task_id": "t1"}})
        self.assertFalse(result.success)
        kwargs = hub.bank_update_task.call_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertIn("Mercado Negro", kwargs["failure_reason"])


class ManageTests(unittest.TestCase):
    def test_a_refusal_from_the_hub_reschedules_the_automatic_loop(self):
        hub = MagicMock()
        hub.bank_get_config.return_value = {"ok": True, "auto_cycle_enabled": True, "auto_cycle_interval_minutes": 30}
        hub.bank_create_cycle.return_value = {"ok": False, "error": "http_400", "http_status": 400}
        r = runner(GeneralsBankManageRunner, hub)
        result = r.execute({"job_id": "j", "account_id": "a", "inputs": {"bank_config_id": "b", "_auto_loop": True}})
        self.assertTrue(result.success)
        self.assertEqual(result.reschedule_seconds, 30 * 60)

    def test_a_timeout_ends_the_cycle_in_the_hub(self):
        hub = MagicMock()
        hub.bank_get_config.return_value = {"ok": True}
        hub.bank_get_cycle_status.return_value = {"ok": True, "status": "training", "tasks": []}
        r = runner(GeneralsBankManageRunner, hub)
        result = r.execute({"job_id": "j", "account_id": "a",
                            "inputs": {"bank_config_id": "b", "_cycle_id": "c", "_poll_count": 999}})
        self.assertFalse(result.success)
        hub.bank_cycle_fail.assert_called_once()


class HubAnswerTests(unittest.TestCase):
    def test_a_refused_request_comes_back_as_an_answer(self):
        from core.hub_client import HubClient

        client = HubClient.__new__(HubClient)
        client.base_url = "http://hub"
        response = MagicMock(status_code=409)
        response.json.return_value = {"error": "active_cycle_exists", "cycle_id": "c9"}
        client.session = MagicMock()
        client.session.post.return_value = response
        answer = client.bank_create_cycle("b", "accumulation")
        self.assertEqual((answer["ok"], answer["error"], answer["cycle_id"]), (False, "active_cycle_exists", "c9"))


if __name__ == "__main__":
    unittest.main()
