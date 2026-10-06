"""N-84 phase 3: Mercado geral -- what the scans of action 810 saw on offer."""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.jobs.services.recovery import SAFE_REQUEUE_ACTIONS
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

    def test_odd_numbers_from_a_scan_never_break_the_save(self):
        offer = _offer(900, 21)
        offer.update({"goods_per_minute": 3_300_033_000, "amount": -5, "distance": "x"})

        self.assertEqual(self._save([_scan(501, [offer])]).status_code, 201)
        saved = PublicMarketOffer.objects.get()
        self.assertEqual((saved.goods_per_minute, saved.amount, saved.distance), (2_147_483_647, 0, 0))

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

    # ── N-87: atualizar, comprar e vender pela pagina ──

    def _other_account(self, name, *, cities, base_snapshot=None, active=True):
        account = Account.objects.create(
            node=Node.objects.create(name=f"node-{name}"), label=name, email=f"{name}@example.com", password_enc="enc",
        )
        ga = GameAccount.objects.create(
            account=account, lobby_account_id=GameAccount.objects.count() + 100, server_id="s9-br",
            server_language="br", server_number=9, name=name, active=active,
        )
        AccountSnapshot.objects.create(
            account=account, game_account=ga, base_snapshot=base_snapshot or {}, military={}, cities=cities,
        )
        return ga

    def _market_city(self, city_id):
        return {"id": str(city_id), "name": f"C{city_id}", "buildings": [{"building": "branchOffice", "position": 3, "level": 10}]}

    def test_refresh_creates_one_scan_per_account_with_a_market(self):
        second = self._other_account("Segunda", cities=[self._market_city(601)])
        self._other_account("SemMercado", cities=[{"id": "701", "name": "Vila", "buildings": []}])
        self._other_account("DeFerias", cities=[self._market_city(801)], base_snapshot={"vacation_state": {"active": True}})
        self._other_account("Inativa", cities=[self._market_city(901)], active=False)

        response = self.client.post(reverse("market:public-market-refresh"))

        self.assertEqual(response.status_code, 302)
        jobs = Job.objects.filter(action_code=810)
        self.assertEqual({j.game_account_id for j in jobs}, {self.ga.pk, second.pk})
        inputs = json.loads(jobs.first().inputs_json)
        self.assertEqual((inputs["all_cities"], inputs["include_buy_requests"]), (True, True))
        # a second click does not pile up scans on accounts that are still scanning
        self.client.post(reverse("market:public-market-refresh"))
        self.assertEqual(Job.objects.filter(action_code=810).count(), 2)
        page = self._page()
        self.assertEqual(page.context["pending_scans"], 2)
        self.assertIn("varredura(s) em andamento", page.content.decode())

    def test_refresh_needs_a_post(self):
        self.assertEqual(self.client.get(reverse("market:public-market-refresh")).status_code, 405)
        self.assertEqual(Job.objects.filter(action_code=810).count(), 0)

    def test_sell_offer_opens_the_buy_action_filled_in(self):
        self._save([_scan(501, [_offer(900, 21, amount=129_600, distance=6)])])

        offer = self._page().context["offers"][0]
        url = offer.trades[0]["url"]
        self.assertEqual(offer.trades[0]["account"], "Varredor")
        for part in (
            f"ga={self.ga.pk}", "action=8", "input_buyer_city_id=501", "input_seller_city_id=900",
            "input_resource_idx=2", "input_amount=129600", "input_max_unit_price=21",
        ):
            self.assertIn(part, url)
        form = self.client.get(url).content.decode()
        self.assertIn("Comprar do Mercado", form)
        self.assertIn('value="129600"', form)
        self.assertIn('value="21"', form)

    def test_buy_request_opens_the_sell_action_filled_in(self):
        self._save([_scan(501, [_offer(920, 9, amount=33_000, offer_type=333)])])

        offer = self._page(kind="buy").context["offers"][0]
        url = offer.trades[0]["url"]
        for part in ("action=811", "input_city_id=501", "input_buyer_city_id=920", "input_amount=33000", "input_min_unit_price=9"):
            self.assertIn(part, url)
        form = self.client.get(url).content.decode()
        self.assertIn("Vender para Pedido de Compra", form)
        self.assertIn('value="33000"', form)

    def test_trade_forms_use_the_styled_market_form_with_nothing_left_in_the_generic_grid(self):
        self._save([_scan(501, [_offer(900, 21, amount=129_600), _offer(920, 9, amount=33_000, offer_type=333)])])
        buy = self.client.get(self._page().context["offers"][0].trades[0]["url"]).content.decode()
        sell = self.client.get(self._page(kind="buy").context["offers"][0].trades[0]["url"]).content.decode()

        for html, title, fields in (
            (buy, "Comprar de um vendedor", ("buyer_city_id", "seller_city_id", "resource_idx", "amount", "max_unit_price", "seller_label")),
            (sell, "Vender para um pedido de compra", ("city_id", "buyer_city_id", "resource_idx", "amount", "min_unit_price", "buyer_label", "dry_run")),
        ):
            self.assertIn(title, html)
            self.assertIn("market-job-cities-data", html)       # city cards, not a plain select
            self.assertNotIn("<select", html)
            for name in fields:
                self.assertEqual(html.count(f'name="{name}"'), 1, name)
        # the chosen city and resource arrive selected
        self.assertIn("selectedCityId: '501'", buy)
        self.assertIn("selectedResource: '2'", sell)

    def test_every_account_that_reaches_an_offer_can_trade_it_nearest_first(self):
        second = self._other_account("Segunda", cities=[self._market_city(601)])
        self._save([_scan(501, [_offer(900, 21, distance=8)])])
        self.client.post(
            "/api/agent/market/public-scan/",
            data=json.dumps({"game_account_id": str(second.pk), "scans": [_scan(601, [_offer(900, 21, distance=2)], name="C601")]}),
            content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        offers = self._page().context["offers"]
        self.assertEqual(len(offers), 1)
        self.assertEqual([(t["account"], t["distance"]) for t in offers[0].trades], [("Segunda", 2), ("Varredor", 8)])
        only_mine = self._page(account=str(self.ga.pk)).context["offers"]
        self.assertEqual([t["account"] for t in only_mine[0].trades], ["Varredor"])

    def test_one_picker_and_one_button_per_offer(self):
        second = self._other_account("Segunda", cities=[self._market_city(601)])
        self._save([_scan(501, [_offer(900, 21, distance=8), _offer(901, 30, distance=4)]),
                    _scan(502, [_offer(900, 21, distance=5)], name="Pequena")])
        self.client.post(
            "/api/agent/market/public-scan/",
            data=json.dumps({"game_account_id": str(second.pk), "scans": [_scan(601, [_offer(900, 21, distance=2)], name="C601")]}),
            content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        response = self._page()
        shared = next(o for o in response.context["offers"] if o.city_id == 900)
        # listed by account, with the nearest city (Segunda, distance 2) as the default
        self.assertEqual(
            [(t["account"], t["city"], t["nearest"]) for t in shared.trade_options],
            [("Segunda", "C601", True), ("Varredor", "Pequena", False), ("Varredor", "Grande", False)],
        )
        page = response.content.decode()
        self.assertEqual(page.count("<select"), 1)                       # only the offer more than one city reaches
        self.assertEqual(page.count("bi-cart-plus"), 2)                  # one button per offer
        self.assertIn(f'hx-get="{shared.trades[0]["url"]}"'.replace("&", "&amp;"), page)

    def test_an_account_is_not_offered_its_own_city(self):
        self._save([_scan(501, [_offer(502, 5, player="Varredor")])])

        offer = self._page().context["offers"][0]
        self.assertTrue(offer.is_internal)
        self.assertEqual(offer.trades, [])

    def test_resource_filter_keeps_the_summary_of_the_other_resources(self):
        self._save([_scan(501, [_offer(900, 30), _offer(910, 40, resource=SULFUR)])])

        response = self._page(resource=SULFUR)
        self.assertEqual([o.city_id for o in response.context["offers"]], [910])
        self.assertEqual(response.context["summary"][MARBLE]["best_price"], 30)

    def _submit(self, action_code, **fields):
        data = {"game_account": str(self.ga.pk), "action_code": str(action_code)}
        data.update(fields)
        return self.client.post(reverse("jobs:job-submit"), data)

    def test_sell_job_gets_the_market_position_of_the_chosen_city(self):
        self._submit(811, city_id="501", buyer_city_id="920", resource_idx="2", amount="5000", min_unit_price="9", dry_run="on")

        job = Job.objects.get(action_code=811)
        inputs = json.loads(job.inputs_json)
        self.assertEqual((inputs["branchoffice_pos"], str(inputs["city_id"]), str(inputs["buyer_city_id"])), (8, "501", "920"))
        self.assertEqual((int(inputs["amount"]), int(inputs["min_unit_price"]), inputs["dry_run"]), (5000, 9, True))

    def test_buy_job_carries_the_price_limit(self):
        self._submit(8, buyer_city_id="501", seller_city_id="900", resource_idx="2", amount="2000", max_unit_price="21")

        inputs = json.loads(Job.objects.get(action_code=8).inputs_json)
        self.assertEqual((inputs["buyer_branchoffice_pos"], int(inputs["max_unit_price"]), int(inputs["amount"])), (8, 21, 2000))

    def test_scan_form_makes_a_single_job_whatever_the_city_choice(self):
        self._submit(810)
        self._submit(810, city_ids=["501", "502"])

        jobs = [json.loads(j.inputs_json) for j in Job.objects.filter(action_code=810).order_by("created_at")]
        self.assertEqual(len(jobs), 2)
        self.assertEqual([str(c) for c in jobs[1]["city_ids"]], ["501", "502"])

    def test_sell_action_is_in_the_catalog(self):
        action = ACTION_CATALOG[811]
        self.assertEqual((action["runner"], action["ready"]), ("market_sell_to_request", True))
        self.assertNotIn(811, SAFE_REQUEUE_ACTIONS)     # recovery must never repeat a sale
        self.assertNotIn(8, SAFE_REQUEUE_ACTIONS)

    def test_action_is_in_the_catalog_with_its_form(self):
        action = ACTION_CATALOG[810]
        self.assertEqual((action["name"], action["runner"], action["ready"]), ("Varrer Mercado", "market_scan", True))
        response = self.client.get(reverse("jobs:job-form"), {"ga": str(self.ga.pk), "action": "810"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Varrer Mercado", response.content.decode())
