"""N-84 phase 3: reading the general market (action 810) -- parser, paging, range sync.

The "game" here is a minimal simulator of what was captured on 2026-10-05 (HAVIT,
Branch Office levels 18 and 20): 10 offers per page, the last range and the last page
are saved, a range above the maximum is IGNORED (the saved one stays), and the saved
range does not follow the building when it is upgraded.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "runners", "core")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from game_client.actions import market_scan as scan_actions  # noqa: E402
from runners import market_scan as scan_runner  # noqa: E402

ROW = (
    '<tr class="alt"> <td class="short_text80">Cidade Um <br/>(Jogador A) </td> <td>3306</td> '
    '<td>129.600 <div class="tooltip">129.600</div> </td> <td><img alt="Marmore"/></td> '
    '<td style="white-space:nowrap;">21 <img class="icon_gold"/> por peca</td> <td>6</td> '
    '<td><a href="?view=takeOffer&destinationCityId=47805&oldView=branchOffice&activeTab=bargain'
    '&cityId=63355&position=8&type=444&resource=2"></a></td> </tr>'
)


def _row(city_id, price, *, amount=1000, distance=3, offer_type=444, resource="2", name="Cidade", player="Jogador"):
    # the list of buy requests (333) has no "goods per minute" column
    per_minute = "<td>10</td> " if offer_type == 444 else ""
    return (
        f'<tr> <td class="short_text80">{name} {city_id} <br/>({player}) </td> {per_minute}'
        f'<td>{amount:,} <div class="tooltip">{amount:,}</div> </td> <td><img alt="x"/></td> '
        f'<td>{price} <img class="icon_gold"/> por peca</td> <td>{distance}</td> '
        f'<td><a href="?view=takeOffer&amp;destinationCityId={city_id}&amp;oldView=branchOffice'
        f'&amp;cityId=1&amp;position=8&amp;type={offer_type}&amp;resource={resource}"></a></td> </tr>'
    ).replace(",", ".")


def _range_select(maximum, selected):
    options = "".join(
        "<option " + ('selected="selected"' if n == selected else "") + f">{n}</option>"   # as the game sends it
        for n in range(1, maximum + 1)
    )
    return f'<select id="rangeSelect" name="range" class="dropdown">{options}</select>'


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Game:
    """Markets of one account: {city_id: {"max", "saved", "offers": {(type, resource): [rows]}}}."""

    def __init__(self, markets):
        self.markets = markets
        self.frozen = False                 # the game refuses to change the saved range
        self.requests = []
        self._action_request = "token"
        self._server_url = "https://s1-br.example/index.php"

    def _request(self, method, url, data=None, headers=None):
        market = self.markets[int(data["cityId"])]
        self.requests.append(dict(data))
        rows = []
        offset = 0
        if "range" in data and 1 <= int(data["range"]) <= market["max"] and not self.frozen:
            market["saved"] = int(data["range"])       # anything above the maximum is ignored
        if "type" in data:
            offset = int(data.get("offset", 0))
            market["page"] = offset
            rows = market["offers"].get((int(data["type"]), str(data["searchResource"])), [])
        html = "<div>" + "x" * 120 + _range_select(market["max"], market["saved"]) + "<table>"
        html += "".join(rows[offset:offset + 10])
        if len(rows) > offset + 10:
            html += f'<a href="?view=branchOffice&offset={offset + 10}">proxima</a>'
        html += "</table></div>"
        return _Resp([["changeView", ["branchOffice", html]]])


def _market(maximum, saved, offers=None):
    return {"max": maximum, "saved": saved, "page": 0, "offers": offers or {}}


class ParserTests(unittest.TestCase):
    def test_offer_row_as_the_game_sends_it(self):
        self.assertEqual(scan_actions.parse_offer_rows("<table>" + ROW + "</table>"), [{
            "city_id": 47805, "city_name": "Cidade Um", "player_name": "Jogador A",
            "goods_per_minute": 3306, "amount": 129600, "unit_price": 21, "distance": 6,
            "offer_type": 444, "resource_idx": 2,
        }])

    def test_rows_without_an_offer_link_are_ignored(self):
        html = "<table><tr><th>Cidade</th><th>Preco</th></tr><tr><td>sem oferta</td></tr>" + _row(9, 30) + "</table>"
        offers = scan_actions.parse_offer_rows(html)
        self.assertEqual([(o["city_id"], o["unit_price"]) for o in offers], [(9, 30)])

    def test_wood_and_buy_requests(self):
        offers = scan_actions.parse_offer_rows(_row(5, 12, offer_type=333, resource="resource"))
        self.assertEqual((offers[0]["offer_type"], offers[0]["resource_idx"]), (333, 0))

    def test_buy_request_row_has_one_column_less(self):
        # as captured: no "goods per minute" cell, and the amount repeated in its tooltip
        row = (
            '<tr> <td class="short_text80">Cidade <br/>(Jogador) </td> '
            '<td>33.000 <div class="tooltip">33.000</div> </td> <td><img alt="Marmore" title="Marmore"/></td> '
            '<td>9 <img class="icon_gold"/> por peca</td> <td>6</td> '
            '<td><a href="?view=takeOffer&destinationCityId=20230&oldView=branchOffice&type=333&resource=2"></a></td> </tr>'
        )
        offer = scan_actions.parse_offer_rows(row)[0]
        self.assertEqual(
            (offer["amount"], offer["unit_price"], offer["distance"], offer["goods_per_minute"], offer["offer_type"]),
            (33000, 9, 6, 0, 333),
        )

    def test_two_numbers_in_a_cell_are_never_glued_together(self):
        row = _row(5, 12).replace("<td>10</td>", "<td>3.306 3.306</td>")
        self.assertEqual(scan_actions.parse_offer_rows(row)[0]["goods_per_minute"], 3306)

    def test_range_state(self):
        self.assertEqual(scan_actions.parse_range_state(_range_select(9, 3)), {"max": 9, "selected": 3})
        self.assertEqual(scan_actions.parse_range_state("<div>sem formulario</div>"), {"max": 0, "selected": 0})


class SearchTests(unittest.TestCase):
    def test_reads_every_page_at_the_maximum_range_and_goes_back_to_the_first(self):
        rows = [_row(100 + i, 10 + i) for i in range(23)]
        game = _Game({7: _market(9, 9, {(444, "2"): rows})})

        result = scan_actions.MarketScanAction(game).search(7, 8, 2)

        self.assertEqual(len(result["offers"]), 23)
        self.assertEqual((result["max_range"], result["pages"], result["truncated"]), (9, 3, False))
        self.assertEqual([r["offset"] for r in game.requests], [0, 10, 20, 0])
        self.assertEqual(game.markets[7]["page"], 0)       # the game keeps the last page
        self.assertEqual(game.markets[7]["saved"], 9)

    def test_saved_range_behind_the_level_is_raised_by_the_search_itself(self):
        game = _Game({7: _market(10, 8, {(444, "2"): [_row(1, 10, distance=2), _row(2, 11, distance=9)]})})
        action = scan_actions.MarketScanAction(game)

        result = action.search(7, 8, 2)

        self.assertEqual(result["max_range"], 10)
        self.assertEqual(game.markets[7]["saved"], 10)
        # asks with the saved range, sees the selector goes further, asks again with exactly that
        self.assertEqual([r.get("range") for r in game.requests], [None, 10])
        action.search(7, 8, 2)
        self.assertEqual([r.get("range") for r in game.requests], [None, 10, 10])

    def test_single_page_needs_no_extra_request(self):
        game = _Game({7: _market(9, 9, {(444, "2"): [_row(1, 10)]})})

        result = scan_actions.MarketScanAction(game).search(7, 8, 2)

        self.assertEqual((len(result["offers"]), result["pages"], len(game.requests)), (1, 1, 1))

    def test_stops_at_max_pages_and_says_so(self):
        game = _Game({7: _market(9, 9, {(444, "2"): [_row(100 + i, 10) for i in range(45)]})})

        result = scan_actions.MarketScanAction(game).search(7, 8, 2, max_pages=2)

        self.assertEqual((len(result["offers"]), result["truncated"]), (20, True))

    def test_buy_requests_use_their_own_listing(self):
        game = _Game({7: _market(9, 9, {(333, "4"): [_row(1, 40, offer_type=333, resource="4")]})})

        result = scan_actions.MarketScanAction(game).search(7, 8, 4, offer_type=scan_actions.OFFER_TYPE_BUY)

        self.assertEqual([(o["offer_type"], o["resource_idx"]) for o in result["offers"]], [(333, 4)])
        self.assertEqual(game.requests[0]["type"], "333")


class SyncRangeTests(unittest.TestCase):
    def test_raises_a_range_left_behind_by_an_upgrade(self):
        game = _Game({7: _market(5, 1)})        # market grew from level 3 to 10, range still 1

        state = scan_actions.MarketScanAction(game).sync_range(7, 8)

        self.assertEqual(state, {"before": 1, "max": 5, "changed": 1})
        self.assertEqual(game.markets[7]["saved"], 5)
        self.assertNotIn("range", game.requests[0])       # the first request only reads
        # exactly the maximum: the game ignores anything above it
        self.assertEqual([r.get("range") for r in game.requests], [None, 5])

    def test_does_not_claim_a_change_the_game_did_not_confirm(self):
        game = _Game({7: _market(5, 1)})
        game.frozen = True

        state = scan_actions.MarketScanAction(game).sync_range(7, 8)

        self.assertEqual(state, {"before": 1, "max": 5, "changed": 0})
        self.assertEqual(game.markets[7]["saved"], 1)

    def test_leaves_a_range_already_at_the_maximum_alone(self):
        game = _Game({7: _market(9, 9)})

        state = scan_actions.MarketScanAction(game).sync_range(7, 8)

        self.assertEqual(state, {"before": 9, "max": 9, "changed": 0})
        self.assertEqual(len(game.requests), 1)


def _city(city_id, name, level, position=8):
    buildings = [{"building": "townHall", "position": 0, "level": 10}]
    if level:
        buildings.append({"building": "branchOffice", "position": position, "level": level})
    return {"id": str(city_id), "name": name, "buildings": buildings}


class _Hub:
    def __init__(self, cities):
        self.cities = cities
        self.saved = []

    def get_snapshot(self, game_account_id=None):
        return {"cities": self.cities}

    def save_public_market_scan(self, *, game_account_id, job_id, scans):
        self.saved.append(scans)
        return {"ok": True, "created": sum(len(s["offers"]) for s in scans)}


def _run(game, cities, **inputs):
    runner = object.__new__(scan_runner.MarketScanRunner)
    runner.hub = _Hub(cities)
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    runner.resolve_credentials = lambda *a, **k: {"email": "x"}
    runner.get_or_login_game_client = lambda *a, **k: game
    runner.save_game_client = lambda *a, **k: None
    result = runner.execute({"job_id": "j1", "account_id": "a1", "game_account_id": "g1", "inputs": inputs})
    return runner, result


class RunnerTests(unittest.TestCase):
    def test_city_choice(self):
        markets = scan_runner.market_cities([_city(1, "A", 4), _city(2, "B", 18), _city(3, "C", 0)])
        self.assertEqual([m["id"] for m in markets], [1, 2])
        self.assertEqual([m["id"] for m in scan_runner.choose_scan_cities(markets, [])], [2])
        self.assertEqual([m["id"] for m in scan_runner.choose_scan_cities(markets, [1])], [1])
        self.assertEqual([m["id"] for m in scan_runner.choose_scan_cities(markets, [3])], [2])   # no market there
        self.assertEqual(scan_runner.parse_city_ids("1, 2;2"), [1, 2])
        self.assertEqual([m["id"] for m in scan_runner.choose_scan_cities(markets, [1], all_cities=True)], [1, 2])

    def test_all_cities_scans_every_market(self):
        game = _Game({
            1: _market(2, 2, {(444, "2"): [_row(50, 21)]}),
            2: _market(9, 9, {(444, "2"): [_row(50, 21), _row(51, 25)]}),
        })

        runner, result = _run(game, [_city(1, "Pequena", 4), _city(2, "Grande", 18)], all_cities=True)

        self.assertEqual((result.data["offers"], sorted(result.data["cities"])), (3, [1, 2]))
        self.assertEqual({s["city_id"]: s["kind"] for s in runner.hub.saved[0]}, {1: "full", 2: "full"})

    def test_scans_the_biggest_market_and_fixes_the_range_of_all(self):
        game = _Game({
            1: _market(2, 1),
            2: _market(9, 9, {(444, "2"): [_row(50, 21), _row(51, 25)], (444, "4"): [_row(60, 40, resource="4")]}),
        })

        runner, result = _run(game, [_city(1, "Pequena", 4), _city(2, "Grande", 18), _city(3, "Sem mercado", 0)])

        self.assertTrue(result.success)
        self.assertEqual((result.data["offers"], result.data["cities"], result.data["ranges_raised"]), (3, [2], 1))
        self.assertEqual(game.markets[1]["saved"], 2)
        scans = {s["city_id"]: s for s in runner.hub.saved[0]}
        self.assertEqual((scans[1]["kind"], scans[1]["range_before"], scans[1]["max_range"]), ("range_sync", 1, 2))
        self.assertEqual((scans[2]["kind"], scans[2]["max_range"], len(scans[2]["offers"])), ("full", 9, 3))
        self.assertTrue(any("alcance salvo era 1" in msg for _level, msg in runner.logs))
        # only listings were requested: nothing was bought, offered or changed besides the search form
        self.assertTrue(all(r["view"] == "branchOffice" and "action" not in r for r in game.requests))

    def test_buy_requests_only_when_asked(self):
        offers = {(444, "1"): [_row(50, 21, resource="1")], (333, "1"): [_row(70, 9, offer_type=333, resource="1")]}

        runner, _result = _run(_Game({2: _market(9, 9, dict(offers))}), [_city(2, "Grande", 18)])
        self.assertEqual([o["offer_type"] for o in runner.hub.saved[0][0]["offers"]], [444])

        runner, _result = _run(_Game({2: _market(9, 9, dict(offers))}), [_city(2, "Grande", 18)], include_buy_requests=True)
        self.assertEqual(sorted(o["offer_type"] for o in runner.hub.saved[0][0]["offers"]), [333, 444])

    def test_account_without_a_market(self):
        runner, result = _run(_Game({}), [_city(3, "Sem mercado", 0)])

        self.assertFalse(result.success)
        self.assertEqual(result.data["error"], "no_market_city")
        self.assertEqual(runner.hub.saved, [])


if __name__ == "__main__":
    unittest.main()
