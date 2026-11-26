"""N-245: a buy the game refuses for lack of action points keeps the same order and waits, instead of failing it."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "runners", "core", "services", "sessions")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from runners import market as market_runner  # noqa: E402

REFUSED = ("Server errors for action transportOperations&function=buyGoodsAtAnotherBranchOffice: "
           "['A cidade não tem mais pontos de ação disponíveis!']")


class _Hub:
    def __init__(self):
        self.rescheduled = []
        self.failed = []

    def reschedule_job(self, jid, delay_seconds=0, inputs=None):
        self.rescheduled.append((delay_seconds, dict(inputs or {})))

    def market_order_fail(self, order_id, note=""):
        self.failed.append((order_id, note))


def _runner():
    runner = object.__new__(market_runner.InternalMarketBuyRunner)
    runner.hub = _Hub()
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    return runner


def _try(runner, inputs, error=REFUSED):
    return runner._maybe_reschedule_for_action_points(
        jid="j", inputs=inputs, order_id="o-1", buyer_city_name="Coisa 1", exc_str=error)


class ActionPointsTests(unittest.TestCase):
    def test_same_order_waits_longer_each_time(self):
        runner = _runner()
        result = _try(runner, {"amount": 5000})
        self.assertTrue(result.success)
        self.assertEqual(result.data["status"], "action_points_wait")
        delay, inputs = runner.hub.rescheduled[-1]
        self.assertEqual((delay, inputs["action_points_retry_count"], inputs["amount"]), (600, 1, 5000))
        _try(runner, {"action_points_retry_count": 2})
        self.assertEqual(runner.hub.rescheduled[-1][0], 2400)
        _try(runner, {"action_points_retry_count": 7})
        self.assertEqual(runner.hub.rescheduled[-1][0], 3600)
        self.assertEqual(runner.hub.failed, [])

    def test_gives_up_after_the_last_try(self):
        runner = _runner()
        self.assertIsNone(_try(runner, {"action_points_retry_count": runner._ACTION_POINTS_MAX_RETRIES}))
        self.assertEqual(runner.hub.rescheduled, [])
        self.assertEqual(runner.hub.failed[0][0], "o-1")

    def test_other_errors_are_not_touched(self):
        runner = _runner()
        self.assertIsNone(_try(runner, {}, error="Server errors: ['Você não tem ouro suficiente']"))
        self.assertEqual((runner.hub.rescheduled, runner.hub.failed), ([], []))


if __name__ == "__main__":
    unittest.main()
