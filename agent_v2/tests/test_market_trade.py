"""N-87: buying and selling from the general market -- price guards, limits, the sell form.

The "game" is a minimal simulator of the screens captured on 2026-10-05 (HAVIT): the
Branch Office listing, the takeOffer form and the final transportOperations POST.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "runners", "core", "services")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from game_client.actions import market as market_actions  # noqa: E402
from game_client.actions import market_sell  # noqa: E402
from game_client.exceptions import ActionError  # noqa: E402
from runners import market as market_runner  # noqa: E402
from runners import market_sell as sell_runner  # noqa: E402

OURS, POS = 39274, 10
THEIRS = 20230
MARBLE = 2


def _row(city_id, price, amount, *, offer_type=444, resource="2", name="Cidade", player="Jogador"):
    per_minute = "<td>10</td> " if offer_type == 444 else ""      # buy requests have no such column
    shown = f"{amount:,}".replace(",", ".")
    return (
        f'<tr> <td class="short_text80">{name} <br/>({player}) </td> {per_minute}'
        f'<td>{shown} <div class="tooltip">{shown}</div> </td> <td><img alt="x"/></td> '
        f'<td>{price} <img class="icon_gold"/> por peca</td> <td>6</td> '
        f'<td><a href="?view=takeOffer&destinationCityId={city_id}&oldView=branchOffice&activeTab=bargain'
        f'&cityId={OURS}&position={POS}&type={offer_type}&resource={resource}"></a></td> </tr>'
    )


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Game:
    """One of our cities and the market around it."""

    def __init__(self, *, sells=(), requests=(), ships=63, stock=250_000, take_prices=None):
        self.rows = {444: list(sells), 333: list(requests)}
        self.ships = ships
        self.stock = stock
        self.take_prices = take_prices          # {field: price} shown by the takeOffer form
        self.listings = []
        self.takes = []
        self.posts = []
        self._action_request = "token"
        self._server_url = "https://s1-br.example/index.php"

    def _request(self, method, url, data=None, headers=None, **kwargs):
        if data.get("view") == "takeOffer":
            self.takes.append(dict(data))
            prices = self.take_prices if self.take_prices is not None else {"tradegood2Price": 9, "tradegood4Price": 9}
            fields = "".join(f'<input type="text" name="{name}" value="{value}"/>' for name, value in prices.items())
            html = (
                '<form id="transportForm">' + "x" * 120
                + '<input type="hidden" name="avatar2Name" value="Dono Real"/><input type="hidden" name="city2Name" value="Cidade Real"/>'
                + f'<input type="text" name="normalTransportersMax" value="{self.ships}"/>' + fields + "</form>"
            )
            header = {"currentResources": {"resource": 1, "1": 2, "2": self.stock, "3": 4, "4": self.stock}}
            return _Resp([["updateGlobalData", {"headerData": header}], ["changeView", ["takeOffer", html]]])
        self.listings.append(dict(data))
        rows = self.rows.get(int(data["type"]), [])
        offset = int(data.get("offset", 0))
        options = "".join("<option " + ('selected="selected"' if n == 10 else "") + f">{n}</option>" for n in range(1, 11))
        html = "<div>" + "x" * 120 + f'<select id="rangeSelect" name="range">{options}</select><table>'
        html += "".join(rows[offset:offset + 10])
        if len(rows) > offset + 10:
            html += f'<a href="?view=branchOffice&offset={offset + 10}">proxima</a>'
        return _Resp([["changeView", ["branchOffice", html + "</table></div>"]]])

    def _ajax(self, action, params):
        self.posts.append(dict(params))
        return {"ok": True}


def _buy(game, amount, **kwargs):
    action = market_actions.BuyAction(game)
    action.execute(
        buyer_city_id=OURS, buyer_branchoffice_pos=POS, seller_city_id=THEIRS, seller_branchoffice_pos=0,
        resource_idx=MARBLE, amount=amount, **kwargs,
    )
    return action


class BuyGuardTests(unittest.TestCase):
    def test_buys_when_the_price_is_within_the_limit(self):
        game = _Game(sells=[_row(THEIRS, 21, 129_600)])

        action = _buy(game, 2_000, max_unit_price=21, cap_to_available=True, cap_to_ships=True)

        post = game.posts[0]
        self.assertEqual((post["function"], post["destinationCityId"], post["cityId"]), ("buyGoodsAtAnotherBranchOffice", THEIRS, OURS))
        self.assertEqual((post["cargo_tradegood2"], post["transporters"], post["type"]), (2_000, 4, 444))
        self.assertEqual(action.last_purchase["amount"], 2_000)
        self.assertEqual(action.last_purchase["unit_price"], 21)

    def test_refuses_an_offer_that_got_more_expensive(self):
        game = _Game(sells=[_row(THEIRS, 25, 129_600)])

        with self.assertRaises(ActionError) as ctx:
            _buy(game, 2_000, max_unit_price=21)

        self.assertIn("25", str(ctx.exception))
        self.assertEqual((game.posts, game.takes), ([], []))        # nothing was even opened

    def test_buys_what_the_offer_has(self):
        game = _Game(sells=[_row(THEIRS, 21, 18_000)])

        action = _buy(game, 50_000, cap_to_available=True, cap_to_ships=True)

        self.assertEqual(game.posts[0]["cargo_tradegood2"], 18_000)
        self.assertEqual(action.last_purchase["ships"], 36)

    def test_buys_what_fits_in_the_free_ships(self):
        game = _Game(sells=[_row(THEIRS, 21, 129_600)], ships=10)

        _buy(game, 50_000, cap_to_available=True, cap_to_ships=True)

        self.assertEqual((game.posts[0]["cargo_tradegood2"], game.posts[0]["transporters"]), (5_000, 10))

    def test_internal_market_purchase_keeps_its_exact_behaviour(self):
        # no guards passed: the amount is never changed and too few ships is an error
        game = _Game(sells=[_row(THEIRS, 21, 1_000)])
        _buy(game, 3_000)
        self.assertEqual(game.posts[0]["cargo_tradegood2"], 3_000)

        game = _Game(sells=[_row(THEIRS, 21, 129_600)], ships=2)
        with self.assertRaises(ActionError):
            _buy(game, 3_000)
        self.assertEqual(game.posts, [])


def _run(runner_cls, game, inputs):
    runner = object.__new__(runner_cls)
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    runner.resolve_credentials = lambda *a, **k: {"email": "x"}
    runner.get_or_login_game_client = lambda *a, **k: game
    runner.save_game_client = lambda *a, **k: None
    result = runner.execute({"job_id": "j1", "account_id": "a1", "game_account_id": "g1", "inputs": inputs})
    return runner, result


class BuyRunnerTests(unittest.TestCase):
    INPUTS = {
        "buyer_city_id": str(OURS), "buyer_branchoffice_pos": POS, "seller_city_id": str(THEIRS),
        "seller_branchoffice_pos": 0, "resource_idx": "2", "amount": 50_000,
    }

    def test_reports_what_was_really_bought(self):
        game = _Game(sells=[_row(THEIRS, 21, 18_000)])

        runner, result = _run(market_runner.BuyMarketRunner, game, {**self.INPUTS, "max_unit_price": "21"})

        self.assertTrue(result.success)
        self.assertEqual((result.data["amount"], result.data["unit_price"]), (18_000, 21))
        self.assertTrue(any(level == "warn" and "comprado 18000" in msg for level, msg in runner.logs))

    def test_price_above_the_limit_fails_without_buying(self):
        game = _Game(sells=[_row(THEIRS, 30, 18_000)])

        runner, result = _run(market_runner.BuyMarketRunner, game, {**self.INPUTS, "max_unit_price": 21})

        self.assertFalse(result.success)
        self.assertEqual(game.posts, [])


def _sell(game, amount, **kwargs):
    return market_sell.SellToRequestAction(game).execute(
        city_id=OURS, branchoffice_pos=POS, buyer_city_id=THEIRS, resource_idx=MARBLE, amount=amount, **kwargs,
    )


class SellToRequestTests(unittest.TestCase):
    def test_sends_the_form_the_game_expects(self):
        game = _Game(requests=[_row(THEIRS, 9, 33_000, offer_type=333)])

        result = _sell(game, 5_000, min_unit_price=8)

        post = game.posts[0]
        self.assertEqual((post["action"], post["function"]), ("transportOperations", "sellGoodsAtAnotherBranchOffice"))
        self.assertEqual((post["cityId"], post["destinationCityId"], post["position"], post["type"]), (OURS, THEIRS, POS, 333))
        self.assertEqual((post["cargo_tradegood2"], post["tradegood2Price"], post["transporters"]), (5_000, 8, 10))
        self.assertEqual((post["cargo_tradegood4"], post["tradegood4Price"]), (0, 9))      # other goods untouched
        self.assertEqual((post["avatar2Name"], post["city2Name"]), ("Dono Real", "Cidade Real"))
        self.assertEqual((result["amount"], result["unit_price"], result["dry_run"]), (5_000, 9, False))
        self.assertEqual(game.listings[0]["type"], "333")

    def test_dry_run_posts_nothing(self):
        game = _Game(requests=[_row(THEIRS, 9, 33_000, offer_type=333)])

        result = _sell(game, 5_000, dry_run=True)

        self.assertEqual(game.posts, [])
        self.assertEqual((result["amount"], result["ships"], result["min_unit_price"], result["dry_run"]), (5_000, 10, 9, True))

    def test_amount_is_limited_by_the_request_the_stock_and_the_ships(self):
        row = _row(THEIRS, 9, 33_000, offer_type=333)
        self.assertEqual(_sell(_Game(requests=[row]), 90_000, dry_run=True)["amount"], 31_500)            # 63 ships
        self.assertEqual(_sell(_Game(requests=[row], ships=200), 90_000, dry_run=True)["amount"], 33_000)   # request
        self.assertEqual(_sell(_Game(requests=[row], stock=1_200), 90_000, dry_run=True)["amount"], 1_200)  # stock

    def test_refuses_when_the_buyer_pays_less_than_the_minimum(self):
        game = _Game(requests=[_row(THEIRS, 9, 33_000, offer_type=333)], take_prices={"tradegood2Price": 7})

        with self.assertRaises(ActionError) as ctx:
            _sell(game, 5_000, min_unit_price=9)

        self.assertIn("7", str(ctx.exception))
        self.assertEqual(game.posts, [])

    def test_request_that_is_gone(self):
        with self.assertRaises(ActionError):
            _sell(_Game(requests=[_row(999, 9, 33_000, offer_type=333)]), 5_000)
        with self.assertRaises(ActionError):       # listed, but the form no longer asks for marble
            _sell(_Game(requests=[_row(THEIRS, 9, 33_000, offer_type=333)], take_prices={"tradegood4Price": 9}), 5_000)

    def test_nothing_in_stock(self):
        game = _Game(requests=[_row(THEIRS, 9, 33_000, offer_type=333)], stock=0)

        with self.assertRaises(ActionError):
            _sell(game, 5_000)
        self.assertEqual(game.posts, [])

    def test_request_on_the_second_page_is_found_and_the_first_page_is_left_saved(self):
        others = [_row(500 + i, 12, 1_000, offer_type=333) for i in range(10)]
        game = _Game(requests=others + [_row(THEIRS, 9, 33_000, offer_type=333)])

        result = _sell(game, 5_000, dry_run=True)

        self.assertEqual(result["amount"], 5_000)
        self.assertEqual([l["offset"] for l in game.listings], [0, 10, 0])

    def test_city_stock_parser(self):
        payload = [["updateGlobalData", {"headerData": {"currentResources": {"resource": 5.9, "2": "70", "citizens": 1}}}]]
        self.assertEqual(market_sell.parse_city_stock(payload), {0: 5, 1: 0, 2: 70, 3: 0, 4: 0})
        self.assertEqual(market_sell.parse_city_stock([["changeView", []]]), {})


class SellRunnerTests(unittest.TestCase):
    INPUTS = {"city_id": str(OURS), "branchoffice_pos": POS, "buyer_city_id": str(THEIRS), "resource_idx": "2", "amount": "5000"}

    def test_simulation(self):
        game = _Game(requests=[_row(THEIRS, 9, 33_000, offer_type=333)])

        runner, result = _run(sell_runner.MarketSellToRequestRunner, game, {**self.INPUTS, "dry_run": True})

        self.assertTrue(result.success)
        self.assertEqual((result.data["status"], result.data["amount"]), ("dry_run", 5_000))
        self.assertEqual(game.posts, [])
        self.assertTrue(any("Nada foi enviado" in msg for _level, msg in runner.logs))

    def test_real_sale(self):
        game = _Game(requests=[_row(THEIRS, 9, 3_000, offer_type=333)])

        runner, result = _run(sell_runner.MarketSellToRequestRunner, game, {**self.INPUTS, "min_unit_price": "9"})

        self.assertTrue(result.success)
        self.assertEqual((result.data["status"], result.data["amount"]), ("sold", 3_000))
        self.assertEqual(game.posts[0]["cargo_tradegood2"], 3_000)
        self.assertTrue(any(level == "warn" and "limitado a 3000" in msg for level, msg in runner.logs))

    def test_missing_inputs_and_failures_do_not_post(self):
        game = _Game(requests=[_row(THEIRS, 9, 3_000, offer_type=333)])
        _runner, result = _run(sell_runner.MarketSellToRequestRunner, game, {**self.INPUTS, "amount": 0})
        self.assertFalse(result.success)

        _runner, result = _run(sell_runner.MarketSellToRequestRunner, game, {**self.INPUTS, "min_unit_price": 50})
        self.assertFalse(result.success)
        self.assertEqual(game.posts, [])


if __name__ == "__main__":
    unittest.main()
