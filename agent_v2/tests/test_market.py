from __future__ import annotations

import sys
import types
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_market_action_module():
    services_pkg = types.ModuleType("services")
    resource_transport = types.ModuleType("services.resource_transport")
    resource_transport._capacity_step_from_percent = lambda *args, **kwargs: 0
    resource_transport._parse_free_transporters = lambda *args, **kwargs: 0
    resource_transport._parse_ship_capacity = lambda *args, **kwargs: 500
    services_pkg.resource_transport = resource_transport

    game_client_pkg = types.ModuleType("game_client")
    game_client_pkg.__path__ = [str(ROOT / "game_client")]
    actions_pkg = types.ModuleType("game_client.actions")
    actions_pkg.__path__ = [str(ROOT / "game_client" / "actions")]
    constants_mod = types.ModuleType("game_client.constants")
    constants_mod.ActionID = types.SimpleNamespace(MARKETPLACE_UPDATE_OFFERS="CityScreen&function=updateOffers")
    constants_mod.GAME_AJAX_HEADERS = {}
    exceptions_mod = types.ModuleType("game_client.exceptions")

    class ActionError(Exception):
        def __init__(self, message, action=None):
            super().__init__(message)
            self.action = action

    class CaptchaRequiredError(Exception):
        pass

    class SessionExpiredError(Exception):
        pass

    exceptions_mod.ActionError = ActionError
    exceptions_mod.CaptchaRequiredError = CaptchaRequiredError
    exceptions_mod.SessionExpiredError = SessionExpiredError

    sys.modules["services"] = services_pkg
    sys.modules["services.resource_transport"] = resource_transport
    sys.modules["game_client"] = game_client_pkg
    sys.modules["game_client.actions"] = actions_pkg
    sys.modules["game_client.constants"] = constants_mod
    sys.modules["game_client.exceptions"] = exceptions_mod

    base_spec = importlib.util.spec_from_file_location(
        "game_client.actions.base_action",
        ROOT / "game_client" / "actions" / "base_action.py",
    )
    assert base_spec and base_spec.loader
    base_module = importlib.util.module_from_spec(base_spec)
    sys.modules["game_client.actions.base_action"] = base_module
    base_spec.loader.exec_module(base_module)

    market_spec = importlib.util.spec_from_file_location(
        "game_client.actions.market",
        ROOT / "game_client" / "actions" / "market.py",
    )
    assert market_spec and market_spec.loader
    market_module = importlib.util.module_from_spec(market_spec)
    sys.modules["game_client.actions.market"] = market_module
    market_spec.loader.exec_module(market_module)
    return market_module, ActionError


market_module, ActionError = _load_market_action_module()
CreateOfferAction = market_module.CreateOfferAction


class _DummyClient:
    def __init__(self):
        self._action_request = "token"
        self.ajax_calls: list[tuple[str, dict]] = []

    def _ajax(self, action: str, params: dict):
        self.ajax_calls.append((action, params))
        return {"ok": True}


class CreateOfferActionTests(unittest.TestCase):
    def setUp(self):
        self.client = _DummyClient()
        self.action = CreateOfferAction(self.client)
        self.action.get_market_context = lambda city_id, bo_pos: (
            [(21, 50), (6, 16), (5, 15), (11, 32), (7, 18)],
            {
                "resource": 0,
                "resourcePrice": 21,
                "tradegood1": 0,
                "tradegood1Price": 6,
                "tradegood2": 0,
                "tradegood2Price": 5,
                "tradegood3": 306,
                "tradegood3Price": 14,
                "tradegood4": 0,
                "tradegood4Price": 7,
            },
        )

    def test_create_offer_adds_to_existing_same_resource_offer_by_default(self):
        result = self.action.execute(
            city_id=123,
            branchoffice_pos=6,
            resource_idx=3,
            amount=400,
            unit_price=15,
        )

        _action, params = self.client.ajax_calls[-1]
        self.assertEqual(params["tradegood3"], "706")
        self.assertEqual(params["tradegood3Price"], "15")
        self.assertEqual(result["existing_offer_amount"], 306)
        self.assertEqual(result["requested_amount"], 400)
        self.assertEqual(result["final_offer_amount"], 706)
        self.assertEqual(result["offer_mode"], "add")

    def test_create_offer_can_replace_same_resource_offer_explicitly(self):
        result = self.action.execute(
            city_id=123,
            branchoffice_pos=6,
            resource_idx=3,
            amount=400,
            unit_price=15,
            offer_mode="replace",
        )

        _action, params = self.client.ajax_calls[-1]
        self.assertEqual(params["tradegood3"], "400")
        self.assertEqual(result["final_offer_amount"], 400)
        self.assertEqual(result["offer_mode"], "replace")

    def test_create_offer_can_clear_same_resource_offer_explicitly(self):
        result = self.action.execute(
            city_id=123,
            branchoffice_pos=6,
            resource_idx=3,
            amount=999,
            unit_price=15,
            offer_mode="clear",
        )

        _action, params = self.client.ajax_calls[-1]
        self.assertEqual(params["tradegood3"], "0")
        self.assertEqual(result["requested_amount"], 0)
        self.assertEqual(result["final_offer_amount"], 0)
        self.assertEqual(result["offer_mode"], "clear")

    def test_create_offer_rejects_unknown_offer_mode(self):
        with self.assertRaises(ActionError):
            self.action.execute(
                city_id=123,
                branchoffice_pos=6,
                resource_idx=3,
                amount=400,
                unit_price=15,
                offer_mode="merge",
            )


if __name__ == "__main__":
    unittest.main()
