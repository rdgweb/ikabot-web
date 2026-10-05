"""N-84: the internal market must not waste gold.

- a purchase bigger than the free ships runs in legs; the second leg has to buy
- the buyer city stock goes to the snapshot when the goods arrive
- the construction plan checks the real stock before paying
- one minimum batch for every order
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "runners", "core", "services", "sessions")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from runners import city as city_runner  # noqa: E402
from runners import market as market_runner  # noqa: E402


class _Hub:
    def __init__(self, order_response=None):
        self.patches = []
        self.orders = []
        self.order_response = order_response

    def patch_snapshot_resources(self, game_account_id, city_id, resources=None, incoming_delta=None):
        self.patches.append((game_account_id, city_id, dict(resources or {})))
        return {"ok": True}

    def create_market_order(self, **kwargs):
        self.orders.append(kwargs)
        if self.order_response is not None:
            return dict(self.order_response)
        return {"ok": True, "order_id": "o-1", "amount": kwargs["amount"], "requested_amount": kwargs["amount"]}


def _runner(cls, hub):
    runner = object.__new__(cls)
    runner.hub = hub
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    runner.resolve_credentials = lambda *a, **k: {"email": "x"}
    runner.get_or_login_game_client = lambda *a, **k: object()
    runner.save_game_client = lambda *a, **k: None
    runner.get_system_setting_int = lambda key, default: default
    return runner


class PurchaseLegTests(unittest.TestCase):
    FIRST_LEG_INPUTS = {
        "amount": 60500, "internal_order_id": "o-1", "resource_idx": 2,
        "purchase_monitor_mode": "await_arrival", "purchase_started_at": 1791049362,
        "purchase_expected_outbound_seconds": 1200, "purchase_expected_total_seconds": 2701,
        "purchase_baseline_amount": 1753, "order_total_amount": 70149, "order_completed_amount": 0,
        "order_remaining_after_leg": 9649,
    }

    def test_second_leg_starts_as_a_purchase_even_after_the_hub_merges_inputs(self):
        runner = _runner(market_runner.InternalMarketBuyRunner, _Hub())
        retry = runner._next_partial_purchase_inputs(
            dict(self.FIRST_LEG_INPUTS), remaining_amount=9649, completed_amount=60500, total_amount=70149,
        )

        # the hub does existing.update(patch): removed keys would survive
        merged = dict(self.FIRST_LEG_INPUTS)
        merged.update(retry)

        self.assertFalse(str(merged.get("purchase_monitor_mode") or ""))     # not "await_arrival" any more
        self.assertEqual(merged["purchase_started_at"], 0)
        self.assertEqual(merged["purchase_baseline_amount"], 0)
        self.assertEqual((merged["amount"], merged["order_completed_amount"], merged["order_total_amount"]), (9649, 60500, 70149))

    def test_buyer_city_stock_goes_to_the_snapshot_on_arrival(self):
        hub = _Hub()
        runner = _runner(market_runner.InternalMarketBuyRunner, hub)
        state = types.SimpleNamespace(available_resources={"wood": 10, "wine": 20, "marble": 38921, "crystal": 5, "sulfur": 7})

        with mock.patch.object(market_runner, "fetch_city_state", lambda client, city_id: state):
            runner._patch_buyer_city_stock(jid="j", client=object(), ga_id="ga-1", buyer_city_id=37428)

        self.assertEqual(hub.patches, [("ga-1", 37428, {"wood": 10, "wine": 20, "marble": 38921, "crystal": 5, "sulfur": 7})])

    def test_stock_patch_failure_never_breaks_the_purchase(self):
        runner = _runner(market_runner.InternalMarketBuyRunner, _Hub())

        def _boom(client, city_id):
            raise RuntimeError("jogo fora do ar")

        with mock.patch.object(market_runner, "fetch_city_state", _boom):
            runner._patch_buyer_city_stock(jid="j", client=object(), ga_id="ga-1", buyer_city_id=1)

        self.assertTrue(any(level == "warn" for level, _msg in runner.logs))


class LiveStockBeforeMarketTests(unittest.TestCase):
    JOB = {"job_id": "j", "account_id": "a", "game_account_id": "ga-1"}
    PENDING = {"city_name": "Enxofre", "building_name": "Mercado Negro"}
    # snapshot from before the delivery: 33,386 marble; the step needs 4,612 more
    CITY = {"id": 37428, "name": "Enxofre", "wood": 500000, "wine": 1, "marble": 33386, "crystal": 1, "sulfur": 1}
    MISSING = {"wood": 0, "wine": 0, "marble": 4612, "glas": 0, "sulfur": 0}

    def _live(self, live_stock):
        hub = _Hub()
        runner = _runner(city_runner.ConstructionPlanRunner, hub)
        with mock.patch.object(city_runner, "_live_city_stock_from_game", lambda client, city_id: live_stock):
            result = runner._live_missing_before_market(
                job=dict(self.JOB), city=dict(self.CITY), pending=dict(self.PENDING), missing=dict(self.MISSING),
            )
        return runner, hub, result

    def test_delivery_the_snapshot_did_not_see_cancels_the_need(self):
        _runner_, hub, result = self._live({"wood": 500000, "wine": 1, "marble": 38921, "glas": 1, "sulfur": 1})

        self.assertEqual(result["marble"], 0)
        self.assertFalse(any(result.values()))
        self.assertEqual(hub.patches[0][2]["marble"], 38921)       # the snapshot learns the real stock

    def test_need_is_recomputed_from_the_real_stock(self):
        _r, _h, partly = self._live({"wood": 500000, "wine": 1, "marble": 35000, "glas": 1, "sulfur": 1})
        self.assertEqual(partly["marble"], 4612 - (35000 - 33386))

        _r, _h, worse = self._live({"wood": 500000, "wine": 1, "marble": 30000, "glas": 1, "sulfur": 1})
        self.assertEqual(worse["marble"], 4612 + (33386 - 30000))

    def test_resources_that_were_not_missing_stay_out(self):
        _r, _h, result = self._live({"wood": 0, "wine": 0, "marble": 33386, "glas": 0, "sulfur": 0})
        self.assertEqual(result, {"wood": 0, "wine": 0, "marble": 4612, "glas": 0, "sulfur": 0})

    def test_unreadable_game_stock_is_reported_as_unknown(self):
        _r, hub, result = self._live(None)
        self.assertIsNone(result)
        self.assertEqual(hub.patches, [])


class OrderSizingTests(unittest.TestCase):
    def _cover(self, missing, level_rows, order_response=None):
        hub = _Hub(order_response)
        runner = _runner(city_runner.ConstructionPlanRunner, hub)
        runner._try_cover_with_internal_market(
            jid="j", ga_id="ga-1", target_city={"id": 37428},
            pending={"city_name": "Enxofre", "building_name": "Mercado Negro", "next_level": 25},
            missing=missing, level_rows=level_rows,
        )
        return runner, hub

    def test_tiny_need_is_rounded_up_to_the_minimum_batch(self):
        _r, hub = self._cover({"marble": 0, "glas": 80}, [{"level": 25, "costs": {"glas": 80}}])

        self.assertEqual([o["amount"] for o in hub.orders], [city_runner.INTERNAL_MARKET_MIN_BATCH])

    def test_bigger_need_keeps_its_own_size(self):
        _r, hub = self._cover({"marble": 40000}, [{"level": 25, "costs": {"marble": 40000}}])
        self.assertEqual(hub.orders[0]["amount"], 48000)             # need x 1.2, no next level known

        rows = [{"level": 25, "costs": {"marble": 3039}}, {"level": 26, "costs": {"marble": 7361}}]
        _r, hub = self._cover({"marble": 3039}, rows)
        self.assertEqual(hub.orders[0]["amount"], 10400)             # need + next level

    def test_partial_grant_is_explained_in_the_log(self):
        response = {"ok": True, "order_id": "o-9", "amount": 30000, "requested_amount": 120000}
        runner, _hub = self._cover({"marble": 100000}, [{"level": 25, "costs": {"marble": 100000}}], response)

        text = " ".join(msg for _level, msg in runner.logs)
        self.assertIn("x30000", text)
        self.assertIn("pedido 120000", text)


class OfferCleanupTests(unittest.TestCase):
    """A failed order leaves what was not bought on offer; cleanup takes exactly that out."""

    def _cleanup(self, *, live_total, leftover, keep):
        calls = []

        class _FakeOffer:
            def __init__(self, client):
                pass

            def get_market_context(self, city_id, branchoffice_pos):
                return [], {"tradegood2": live_total}

            def execute(self, **kwargs):
                calls.append(kwargs)
                return {"final_offer_amount": kwargs["amount"]}

        runner = _runner(market_runner.InternalMarketSellRunner, _Hub())
        job = {"job_id": "j", "account_id": "a", "game_account_id": "ga-1", "inputs": {
            "city_id": 39271, "branchoffice_pos": 7, "resource_idx": 2, "amount": keep, "unit_price": 10,
            "offer_mode": "replace" if keep else "clear", "cleanup_only": True, "internal_order_id": "o-1",
            "expected_current_total": 999999, "cleanup_leftover_amount": leftover, "cleanup_keep_total": keep,
        }}
        with mock.patch.object(market_runner, "CreateOfferAction", _FakeOffer):
            result = runner.execute(job)
        return result, calls

    def test_leftover_of_a_partly_delivered_order_is_taken_out(self):
        # 70,149 published, 60,500 bought -> 9,649 left on offer, nothing else there
        result, calls = self._cleanup(live_total=9649, leftover=9649, keep=0)

        self.assertTrue(result.success)
        self.assertEqual((calls[0]["offer_mode"], calls[0]["amount"]), ("clear", 0))

    def test_offers_of_other_open_orders_are_kept(self):
        result, calls = self._cleanup(live_total=15184, leftover=9649, keep=5535)

        self.assertEqual((calls[0]["offer_mode"], calls[0]["amount"]), ("replace", 5535))

    def test_older_untracked_offers_are_left_alone(self):
        result, calls = self._cleanup(live_total=30000, leftover=9649, keep=0)

        self.assertEqual((calls[0]["offer_mode"], calls[0]["amount"]), ("replace", 20351))

    def test_nothing_is_removed_when_the_offer_was_already_bought(self):
        result, calls = self._cleanup(live_total=5535, leftover=9649, keep=5535)

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        self.assertEqual(result.data["cleanup_skipped"], "nothing_to_remove")


if __name__ == "__main__":
    unittest.main()
