import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_market_runner_module():
    core_pkg = types.ModuleType("core")
    runner_registry = types.ModuleType("core.runner_registry")
    runner_registry.register_runner = lambda _code: (lambda cls: cls)
    core_pkg.runner_registry = runner_registry

    game_client_pkg = types.ModuleType("game_client")
    actions_pkg = types.ModuleType("game_client.actions")
    market_actions = types.ModuleType("game_client.actions.market")
    market_actions.BuyAction = object
    market_actions.CreateOfferAction = object
    parsers_pkg = types.ModuleType("game_client.parsers")
    html_parser = types.ModuleType("game_client.parsers.html_parser")

    class GamePageParser:
        def extract_action_request(self, _html):
            return ""

    html_parser.GamePageParser = GamePageParser
    parsers_pkg.html_parser = html_parser
    actions_pkg.market = market_actions
    game_client_pkg.actions = actions_pkg
    game_client_pkg.parsers = parsers_pkg

    runners_pkg = types.ModuleType("runners")
    base_mod = types.ModuleType("runners.base")

    class BaseRunner:
        pass

    class RunnerResult:
        def __init__(self, *args, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    base_mod.BaseRunner = BaseRunner
    base_mod.RunnerResult = RunnerResult
    runners_pkg.base = base_mod

    services_pkg = types.ModuleType("services")
    resource_transport = types.ModuleType("services.resource_transport")
    resource_transport._parse_free_transporters = lambda *_args, **_kwargs: 0
    resource_transport._parse_ship_capacity = lambda *_args, **_kwargs: 500
    resource_transport._parse_transport_times = lambda *_args, **_kwargs: {"queue_seconds": 0, "loading_seconds": 0, "travel_seconds": 0, "total_seconds": 0}
    resource_transport.estimate_incoming_transport_wait_seconds = lambda *_args, **_kwargs: None
    resource_transport.fetch_city_state = lambda *_args, **_kwargs: None
    services_pkg.resource_transport = resource_transport

    sys.modules.update(
        {
            "core": core_pkg,
            "core.runner_registry": runner_registry,
            "game_client": game_client_pkg,
            "game_client.actions": actions_pkg,
            "game_client.actions.market": market_actions,
            "game_client.parsers": parsers_pkg,
            "game_client.parsers.html_parser": html_parser,
            "runners": runners_pkg,
            "runners.base": base_mod,
            "services": services_pkg,
            "services.resource_transport": resource_transport,
        }
    )

    spec = importlib.util.spec_from_file_location("market_runner_under_test", ROOT / "runners" / "market.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


MARKET_MODULE = _load_market_runner_module()
InternalMarketBuyRunner = MARKET_MODULE.InternalMarketBuyRunner


class InternalMarketBuyRunnerTests(unittest.TestCase):
    def test_purchase_arrival_requires_logistic_signal(self):
        arrived = InternalMarketBuyRunner._purchase_arrived(
            phase="stock_arrived",
            seen_outbound=False,
            seen_incoming=False,
            seen_returning=False,
            delta=100,
            amount=50,
            movement=None,
            incoming_eta=None,
        )
        self.assertFalse(arrived)

    def test_purchase_arrival_still_rejects_stock_with_only_outbound_seen(self):
        arrived = InternalMarketBuyRunner._purchase_arrived(
            phase="stock_arrived",
            seen_outbound=True,
            seen_incoming=False,
            seen_returning=False,
            delta=100,
            amount=50,
            movement=None,
            incoming_eta=None,
        )
        self.assertFalse(arrived)

    def test_purchase_arrival_accepts_stock_after_returning_seen(self):
        arrived = InternalMarketBuyRunner._purchase_arrived(
            phase="stock_arrived",
            seen_outbound=True,
            seen_incoming=False,
            seen_returning=True,
            delta=100,
            amount=50,
            movement=None,
            incoming_eta=None,
        )
        self.assertTrue(arrived)

    def test_classify_purchase_phase_prefers_incoming_and_stock(self):
        phase = InternalMarketBuyRunner._classify_purchase_phase(
            movement={"event": {"isFleetReturning": False, "isReturning": 0}},
            incoming_eta=180,
            baseline_amount=1000,
            current_amount=1049,
            amount=50,
        )
        self.assertEqual(phase, "returning")

        phase = InternalMarketBuyRunner._classify_purchase_phase(
            movement=None,
            incoming_eta=None,
            baseline_amount=1000,
            current_amount=1050,
            amount=50,
        )
        self.assertEqual(phase, "stock_arrived")

    def test_raise_gold_arrival_waits_until_movement_disappears_after_eta(self):
        runner = InternalMarketBuyRunner()
        logs = []
        runner.log = lambda *_args: logs.append(_args)
        runner._find_purchase_movement = lambda *_args, **_kwargs: {"event": {"missionText": "Trocar(Comprar)", "isFleetReturning": False, "isReturning": 0}, "target": {"cityId": 63355}, "eta_seconds": 120}
        status = runner._check_raise_gold_arrival_status(
            jid="j1",
            client=object(),
            buyer_city_id=37406,
            seller_city_id=63355,
            purchase_started_at=0,
            expected_arrival_seconds=120,
        )
        self.assertEqual(status["state"], "pending")

    def test_raise_gold_arrival_confirms_after_eta_without_movement(self):
        runner = InternalMarketBuyRunner()
        runner.log = lambda *_args, **_kwargs: None
        runner._find_purchase_movement = lambda *_args, **_kwargs: None
        original_time = MARKET_MODULE.time.time
        try:
            MARKET_MODULE.time.time = lambda: 1000
            status = runner._check_raise_gold_arrival_status(
                jid="j1",
                client=object(),
                buyer_city_id=37406,
                seller_city_id=63355,
                purchase_started_at=100,
                expected_arrival_seconds=120,
            )
        finally:
            MARKET_MODULE.time.time = original_time
        self.assertEqual(status["state"], "arrived")

    def test_transporter_shortage_from_preview_reschedules(self):
        runner = InternalMarketBuyRunner()
        calls = []
        runner.hub = types.SimpleNamespace(reschedule_job=lambda jid, delay_seconds, inputs=None: calls.append((jid, delay_seconds, inputs)) or {"ok": True})
        runner.log = lambda *_args, **_kwargs: None
        result = runner._maybe_reschedule_for_transporters(
            jid="j1",
            inputs={"x": 1},
            order_id="o1",
            buyer_city_id=123,
            amount=6320,
            preview={"free_transporters": 8, "ship_capacity": 500, "travel_seconds": 300},
        )
        self.assertIsNotNone(result)
        self.assertEqual(calls[0][0], "j1")
        self.assertGreaterEqual(calls[0][1], 300)

    def test_transporter_shortage_from_error_reschedules(self):
        runner = InternalMarketBuyRunner()
        calls = []
        runner.hub = types.SimpleNamespace(reschedule_job=lambda jid, delay_seconds, inputs=None: calls.append((jid, delay_seconds, inputs)) or {"ok": True})
        runner.log = lambda *_args, **_kwargs: None
        result = runner._maybe_reschedule_for_transporters_from_error(
            jid="j1",
            client=None,
            inputs={"x": 1},
            order_id="o1",
            buyer_city_id=123,
            amount=6320,
            exc_str="Not enough free transporters for market purchase: need 13, have 8 (amount=6320, ship_capacity=500)",
        )
        self.assertIsNotNone(result)
        self.assertEqual(calls[0][0], "j1")


if __name__ == "__main__":
    unittest.main()
