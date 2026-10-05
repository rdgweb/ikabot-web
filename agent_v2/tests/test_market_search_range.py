"""N-84 phase 2: finding a seller's offer across ranges and pages of the market listing.

Behaviour captured on 2026-10-05 (HAVIT, Branch Office level 18): the game saves the
last range and the last page used; a range above the maximum is clamped to it; the
listing has 10 offers per page, cheapest first.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "core")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from game_client.actions import market as market_actions  # noqa: E402

MAX = market_actions.MAX_SEARCH_RANGE
SELLER = 66481


class _Listing(market_actions.BuyAction):
    """BuyAction over a fake game: pages[(range, offset)] = (seller_listed, has_next_page)."""

    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def _get_branch_office_html(self, city_id, bo_pos, resource_str, search_range=MAX, offset=0):
        self._last_search = (int(search_range), int(offset))
        self.requests.append((int(search_range), int(offset)))
        listed, has_next = self.pages.get((int(search_range), int(offset)), (False, False))
        html = "<table>"
        if listed:
            html += f"SELLER:{SELLER}"
        if has_next:
            html += f'<a href="?view=branchOffice&offset={offset + 10}">'
        return html

    def _find_offer_in_listing(self, html, seller_city_id, resource_str):
        return {"city_id": seller_city_id, "type": "444"} if f"SELLER:{seller_city_id}" in html else None


class FindOfferTests(unittest.TestCase):
    def _find(self, pages, search_range=None):
        action = _Listing(pages)
        offer = action.find_offer(37428, 16, SELLER, "2", search_range=search_range)
        return action, offer

    def test_usual_case_is_one_request_at_the_maximum_range(self):
        action, offer = self._find({(MAX, 0): (True, True)}, search_range=2)

        self.assertIsNotNone(offer)
        self.assertEqual(action.requests, [(MAX, 0)])
        action.restore_max_range(37428, 16, "2")
        self.assertEqual(action.requests, [(MAX, 0)])            # already at the maximum: nothing to restore

    def test_seller_hidden_by_foreign_offers_is_found_with_the_narrow_range(self):
        action, offer = self._find({(MAX, 0): (False, True), (2, 0): (True, False)}, search_range=2)

        self.assertIsNotNone(offer)
        self.assertEqual(action.requests, [(MAX, 0), (2, 0)])
        action.restore_max_range(37428, 16, "2")
        self.assertEqual(action.requests[-1], (MAX, 0))          # the saved range goes back to the maximum

    def test_narrow_range_also_follows_its_pages(self):
        pages = {(MAX, 0): (False, True), (2, 0): (False, True), (2, 10): (True, False)}
        action, offer = self._find(pages, search_range=2)

        self.assertIsNotNone(offer)
        self.assertEqual(action.requests, [(MAX, 0), (2, 0), (2, 10)])

    def test_without_a_known_distance_the_maximum_range_is_paged(self):
        pages = {(MAX, 0): (False, True), (MAX, 10): (True, True)}
        action, offer = self._find(pages, search_range=None)

        self.assertIsNotNone(offer)
        self.assertEqual(action.requests, [(MAX, 0), (MAX, 10)])
        action.restore_max_range(37428, 16, "2")
        self.assertEqual(action.requests[-1], (MAX, 0))          # page 2 must not stay saved

    def test_gives_up_when_no_page_has_the_seller(self):
        pages = {(MAX, 0): (False, True), (2, 0): (False, False), (MAX, 10): (False, False)}
        action, offer = self._find(pages, search_range=2)

        self.assertIsNone(offer)
        self.assertEqual(action.requests, [(MAX, 0), (2, 0), (MAX, 10)])   # stops when there is no next page

    def test_page_limit_bounds_the_search(self):
        pages = {(MAX, offset): (False, True) for offset in range(0, 1000, 10)}
        action, offer = self._find(pages, search_range=None)

        self.assertIsNone(offer)
        self.assertEqual(len(action.requests), market_actions.MAX_OFFER_PAGES)

    def test_single_page_listing_is_not_asked_twice(self):
        action, offer = self._find({(MAX, 0): (False, False)}, search_range=None)

        self.assertIsNone(offer)
        self.assertEqual(action.requests, [(MAX, 0)])


class _Resp:
    def json(self):
        return [["changeView", ["branchOffice", "<div>" + "x" * 200 + "</div>"]]]


class _Client:
    _server_url = "https://s1-br.example/index.php"
    _action_request = "abc"

    def __init__(self):
        self.posts = []

    def _request(self, method, url, data=None, headers=None, **kwargs):
        self.posts.append(dict(data or {}))
        return _Resp()


class SearchRequestTests(unittest.TestCase):
    def test_every_search_states_its_range_and_page(self):
        client = _Client()
        action = market_actions.BuyAction(client)

        action._get_branch_office_html(37428, 16, "2")
        action._get_branch_office_html(37428, 16, "2", search_range=3, offset=10)

        self.assertEqual((client.posts[0]["range"], client.posts[0]["offset"]), (MAX, 0))
        self.assertEqual((client.posts[1]["range"], client.posts[1]["offset"]), (3, 10))

    def test_public_offer_listing_resets_the_saved_page(self):
        client = _Client()
        market_actions.GetOffersAction(client)._get_branch_office_html(37428, 16, "2")

        self.assertEqual((client.posts[0]["range"], client.posts[0]["offset"]), (MAX, 0))


if __name__ == "__main__":
    unittest.main()
