"""N-84 phase 3: Mercado geral -- what the scans of action 810 saw on offer."""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from core.actions import ACTION_CATALOG

from .models import PublicMarketOffer, PublicMarketScan

MARBLE = 2
SULFUR = 4


def _offer(city_id, price, *, resource=MARBLE, amount=10_000, distance=3, offer_type=444, player="Outro"):
    return {
        "city_id": city_id, "city_name": f"Cidade {city_id}", "player_name": player, "goods_per_minute": 100,
        "amount": amount, "unit_price": price, "distance": distance, "offer_type": offer_type, "resource_idx": resource,
    }


def _scan(city_id, offers, *, kind="full", level=18, max_range=9, before=9, name="Grande"):
    return {
        "city_id": city_id, "city_name": name, "kind": kind, "bo_level": level,
        "max_range": max_range, "range_before": before, "offers": offers,
    }


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class PublicMarketTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="market-tester", email="market@example.com", password="secret123",
        )
        self.account = Account.objects.create(
            node=Node.objects.create(name="agent-scan"), label="Lobby", email="scan@example.com", password_enc="enc",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=31, server_id="s9-br",
            server_language="br", server_number=9, name="Varredor",
        )
        AccountSnapshot.objects.create(
            account=self.account, game_account=self.ga, base_snapshot={}, military={},
            cities=[
                {"id": "501", "name": "Grande", "buildings": [{"building": "branchOffice", "position": 8, "level": 18}]},
                {"id": "502", "name": "Pequena", "buildings": [{"building": "branchOffice", "position": 4, "level": 4}]},
            ],
        )
        self.client.force_login(self.user)

    def _save(self, scans, **extra):
        payload = {"game_account_id": str(self.ga.pk), "scans": scans}
        payload.update(extra)
        return self.client.post(
            "/api/agent/market/public-scan/", data=json.dumps(payload),
            content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )

    def _page(self, **params):
        response = self.client.get(reverse("market:public-market"), params)
        self.assertEqual(response.status_code, 200)
        return response

    def test_scan_is_saved_and_own_cities_are_marked(self):
        response = self._save([_scan(501, [_offer(900, 21), _offer(502, 35, player="Varredor")])])

        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.json()["scans"], response.json()["created"]), (1, 2))
        scan = PublicMarketScan.objects.get()
        self.assertEqual((scan.city_id, scan.kind, scan.bo_level, scan.max_range, scan.offers_count), (501, "full", 18, 9, 2))
        marks = dict(PublicMarketOffer.objects.values_list("city_id", "is_internal"))
        self.assertEqual(marks, {900: False, 502: True})

    def test_new_scan_replaces_the_previous_one_of_the_same_city(self):
        self._save([_scan(501, [_offer(900, 21), _offer(901, 22)])])
        self._save([_scan(501, [_offer(902, 19)])])

        self.assertEqual(PublicMarketScan.objects.count(), 1)
        self.assertEqual(list(PublicMarketOffer.objects.values_list("city_id", flat=True)), [902])

    def test_range_sync_keeps_the_offers_of_a_full_scan(self):
        self._save([_scan(501, [_offer(900, 21)], before=3)])
        self._save([_scan(501, [], kind="range_sync", level=20, max_range=10, before=9)])

        scan = PublicMarketScan.objects.get()
        self.assertEqual((scan.kind, scan.bo_level, scan.max_range, scan.range_before, scan.offers_count), ("full", 20, 10, 9, 1))
        self.assertEqual(PublicMarketOffer.objects.count(), 1)

    def test_range_sync_of_a_city_never_scanned_is_listed_without_offers(self):
        self._save([_scan(502, [], kind="range_sync", level=4, max_range=2, before=1, name="Pequena")])

        page = self._page().content.decode()
        self.assertEqual(PublicMarketScan.objects.get().kind, "range_sync")
        self.assertIn("Pequena", page)
        self.assertIn("so alcance", page)

    def test_api_rejects_bad_requests(self):
        self.assertEqual(self._save("nope").status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/agent/market/public-scan/", data=json.dumps({"game_account_id": str(self.ga.pk), "scans": []}),
                content_type="application/json",
            ).status_code in (401, 403), True,
        )
        self.assertEqual(PublicMarketScan.objects.count(), 0)

    def test_page_lists_cheapest_first_and_summarises_external_offers(self):
        self._save([_scan(501, [
            _offer(900, 30), _offer(901, 21, amount=5_000), _offer(502, 5, player="Varredor"),
            _offer(910, 40, resource=SULFUR), _offer(920, 9, offer_type=333),
        ])])

        response = self._page()
        self.assertEqual([o.city_id for o in response.context["offers"]], [502, 901, 900, 910])
        marble = response.context["summary"][MARBLE]
        # the account's own offer at 5 gold is not the market price
        self.assertEqual((marble["best_price"], marble["external_count"], marble["external_amount"], marble["count"]), (21, 2, 15_000, 3))
        self.assertIn("minha conta", response.content.decode())

    def test_filters(self):
        self._save([_scan(501, [
            _offer(900, 30), _offer(502, 5, player="Varredor"), _offer(910, 40, resource=SULFUR),
            _offer(920, 9, offer_type=333), _offer(921, 12, offer_type=333),
        ])])

        self.assertEqual([o.city_id for o in self._page(resource=SULFUR).context["offers"]], [910])
        self.assertEqual([o.city_id for o in self._page(hide_internal=1).context["offers"]], [900, 910])
        # buy requests: who pays more comes first
        self.assertEqual([o.city_id for o in self._page(kind="buy").context["offers"]], [921, 920])
        self.assertEqual(self._page(resource="abc").context["selected_resource"], None)

    def test_offer_seen_from_two_cities_is_listed_once(self):
        self._save([_scan(501, [_offer(900, 21)]), _scan(502, [_offer(900, 21)], name="Pequena")])

        self.assertEqual(PublicMarketOffer.objects.count(), 2)
        self.assertEqual(len(self._page().context["offers"]), 1)

    def test_empty_page_and_links(self):
        page = self._page().content.decode()
        self.assertIn("Nenhuma oferta para mostrar", page)
        self.assertIn(f"{reverse('jobs:job-form')}?ga={self.ga.pk}&action=810", page)
        self.assertIn(reverse("market:public-market"), self.client.get(reverse("market:dashboard")).content.decode())

    def test_action_is_in_the_catalog_with_its_form(self):
        action = ACTION_CATALOG[810]
        self.assertEqual((action["name"], action["runner"], action["ready"]), ("Varrer Mercado", "market_scan", True))
        response = self.client.get(reverse("jobs:job-form"), {"ga": str(self.ga.pk), "action": "810"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Varrer Mercado", response.content.decode())
