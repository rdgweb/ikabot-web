"""N-111: the economic balance of the internal market - what each account sold and bought, and who paid whom."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job

from .balance import build_balance, delivered_amount
from .models import InternalMarketOrder, PublicMarketOffer, PublicMarketScan

WOOD, MARBLE, CRYSTAL = 0, 2, 3


class BalanceBase(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-balance")
        self.alpha = self._account("Alpha", 1, gold=5_000_000)
        self.beta = self._account("Beta", 2, gold=900_000)
        self.gamma = self._account("Gamma", 3, gold=120_000)
        self.order(self.alpha, self.beta, WOOD, 1000, 35)                               # Alpha pays Beta 35.000
        self.order(self.beta, self.alpha, MARBLE, 200, 10)                              # Beta pays Alpha 2.000
        self.part = self.order(self.alpha, self.gamma, CRYSTAL, 500, 16, status="failed", done=300)   # 300 of 500 arrived: 4.800
        self.order(self.gamma, self.beta, WOOD, 400, 35, status="failed")               # nothing arrived

    def _account(self, name: str, number: int, *, gold: int):
        account = Account.objects.create(node=self.node, label=f"Lobby {name}", email=f"{name.lower()}@example.com", password_enc="x")
        game_account = GameAccount.objects.create(
            account=account, lobby_account_id=number, server_id="s78-br", server_language="br", server_number=78, name=name,
        )
        AccountSnapshot.objects.create(account=account, game_account=game_account, base_snapshot={"gold": gold}, cities=[])
        return game_account

    def order(self, buyer, seller, resource, amount, price, *, status="completed", done=None, days_ago=0):
        job = None
        if done is not None:
            job = Job.objects.create(
                account=buyer.account, game_account=buyer, node=self.node, action_code=801, status="error",
                inputs_json=json.dumps({"order_completed_amount": done}),
            )
        order = InternalMarketOrder.objects.create(
            buyer_account=buyer.account, buyer_game_account=buyer, buyer_node=self.node,
            seller_account=seller.account, seller_game_account=seller, seller_node=self.node,
            resource_idx=resource, amount=amount, unit_price=price, status=status, buy_job=job,
        )
        if days_ago:
            InternalMarketOrder.objects.filter(pk=order.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return order

    def party(self, balance: dict, name: str) -> dict:
        return next(item for item in balance["accounts"] if item["name"] == name)


class BalanceTests(BalanceBase):
    def test_what_changed_hands_is_added_up(self):
        balance = build_balance(30)
        self.assertEqual(balance["totals"], {"gold": 41_800, "amount": 1500, "orders": 3, "partial": 1, "empty": 1})

    def test_each_account_has_what_it_sold_what_it_bought_and_the_gold_left(self):
        balance = build_balance(30)
        self.assertEqual([item["name"] for item in balance["accounts"]], ["Beta", "Gamma", "Alpha"])        # who gained most first
        alpha, beta, gamma = (self.party(balance, name) for name in ("Alpha", "Beta", "Gamma"))
        self.assertEqual((alpha["sold_gold"], alpha["bought_gold"], alpha["net"]), (2_000, 39_800, -37_800))
        self.assertEqual((beta["sold_gold"], beta["bought_gold"], beta["net"]), (35_000, 2_000, 33_000))
        self.assertEqual((gamma["sold_gold"], gamma["bought_gold"], gamma["net"]), (4_800, 0, 4_800))
        self.assertEqual(alpha["bought_list"], [{"idx": WOOD, "amount": 1000}, {"idx": CRYSTAL, "amount": 300}])
        self.assertEqual(alpha["sold_list"], [{"idx": MARBLE, "amount": 200}])
        self.assertEqual((alpha["sales"], alpha["purchases"], alpha["gold_now"]), (1, 2, 5_000_000))
        self.assertEqual((alpha["sold_pct"], beta["sold_pct"], gamma["sold_pct"]), (5, 95, 100))

    def test_who_paid_whom_already_takes_off_what_went_back(self):
        flows = build_balance(30)["flows"]
        self.assertEqual([(flow["payer"], flow["receiver"], flow["gold"], flow["back"]) for flow in flows],
                         [("Alpha", "Beta", 33_000, 2_000), ("Alpha", "Gamma", 4_800, 0)])
        self.assertEqual((flows[0]["pct"], flows[1]["pct"]), (100, 15))

    def test_two_accounts_that_paid_each_other_the_same_are_even(self):
        self.order(self.beta, self.alpha, MARBLE, 3300, 10)                             # Beta pays Alpha the 33.000 it was owed
        flows = build_balance(30)["flows"]
        self.assertEqual([(flow["payer"], flow["receiver"]) for flow in flows], [("Alpha", "Gamma")])

    def test_the_price_of_each_resource_beside_the_general_market(self):
        scan = PublicMarketScan.objects.create(game_account=self.alpha, city_id=1, scanned_at=timezone.now())
        for price, internal in ((40, False), (48, False), (20, True)):                 # an offer of an own account is not the outside price
            PublicMarketOffer.objects.create(scan=scan, kind="sell", resource_idx=WOOD, city_id=9, amount=5000, unit_price=price, is_internal=internal)
        PublicMarketOffer.objects.create(scan=scan, kind="sell", resource_idx=CRYSTAL, city_id=9, amount=5000, unit_price=12)
        prices = {item["idx"]: item for item in build_balance(30)["prices"]}
        self.assertEqual(sorted(prices), [WOOD, MARBLE, CRYSTAL])                       # only what was traded
        wood = prices[WOOD]
        self.assertEqual((wood["average"], wood["amount"], wood["orders"], wood["low"], wood["high"]), (35, 1000, 1, 35, 35))
        self.assertEqual((wood["outside"], wood["saved"]), (40, 5_000))                 # 1.000 at 40 outside, 35.000 paid here
        self.assertEqual((prices[CRYSTAL]["outside"], prices[CRYSTAL]["saved"]), (12, -1_200))      # dearer here than outside
        self.assertEqual((prices[MARBLE]["outside"], prices[MARBLE]["saved"]), (None, None))

    def test_an_average_between_two_prices_is_weighted_by_what_was_bought(self):
        self.order(self.gamma, self.beta, WOOD, 3000, 31)
        wood = next(item for item in build_balance(30)["prices"] if item["idx"] == WOOD)
        self.assertEqual((wood["average"], wood["low"], wood["high"], wood["orders"]), (32, 31, 35, 2))

    def test_the_period_leaves_older_orders_out(self):
        self.order(self.gamma, self.alpha, MARBLE, 1000, 10, days_ago=40)
        self.assertEqual(build_balance(30)["totals"]["gold"], 41_800)
        self.assertEqual(build_balance(7)["totals"]["gold"], 41_800)
        self.assertEqual(build_balance(0)["totals"]["gold"], 51_800)

    def test_the_gold_of_each_day_ends_today(self):
        self.order(self.gamma, self.alpha, MARBLE, 1000, 10, days_ago=2)
        balance = build_balance(30)
        chart = balance["chart"]
        self.assertEqual(len(chart), 14)
        self.assertEqual(chart[-1]["date"], timezone.localdate())
        self.assertEqual((chart[-1]["gold"], chart[-1]["pct"]), (41_800, 100))
        self.assertEqual((chart[-3]["gold"], chart[-3]["pct"]), (10_000, 24))
        self.assertEqual((chart[0]["gold"], chart[0]["pct"]), (0, 0))
        self.assertEqual(balance["chart_peak"], 41_800)
        self.assertEqual(len(build_balance(7)["chart"]), 7)

    def test_how_much_of_an_order_arrived(self):
        self.assertEqual(delivered_amount(self.part), 300)
        self.part.buy_job.inputs_json = json.dumps({"order_completed_amount": 9999})    # never more than the order
        self.assertEqual(delivered_amount(self.part), 500)
        self.part.buy_job.inputs_json = "not json"
        self.assertEqual(delivered_amount(self.part), 0)
        self.part.status = "completed"
        self.assertEqual(delivered_amount(self.part), 500)

    def test_an_order_whose_account_is_gone_still_counts_under_the_lobby_account(self):
        order = self.order(self.gamma, self.beta, MARBLE, 100, 10)
        InternalMarketOrder.objects.filter(pk=order.pk).update(buyer_game_account=None)
        balance = build_balance(30)
        lobby = self.party(balance, "Lobby Gamma")
        self.assertEqual((lobby["bought_gold"], lobby["gold_now"]), (1_000, None))


class BalancePageTests(BalanceBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x"))

    def test_the_page_shows_the_accounts_the_flows_and_the_prices(self):
        response = self.client.get(reverse("market:balance"))
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        for text in ("Ouro movimentado", "41.800", "Quem comprou de quem", "Preço por recurso", "Ouro em caixa", "5.000.000", "+33.000", "-37.800",
                     f"?seller={self.beta.pk}#orders-section", f"?buyer={self.alpha.pk}#orders-section", "1 só em parte"):
            self.assertIn(text, page)
        self.assertEqual([period["active"] for period in response.context["periods"]], [False, True, False])

    def test_the_period_comes_from_the_address_and_a_strange_one_falls_back(self):
        self.assertEqual(self.client.get(reverse("market:balance") + "?days=7").context["days"], 7)
        self.assertEqual(self.client.get(reverse("market:balance") + "?days=0").context["days"], 0)
        self.assertEqual(self.client.get(reverse("market:balance") + "?days=abc").context["days"], 30)
        self.assertEqual(self.client.get(reverse("market:balance") + "?days=13").context["days"], 30)

    def test_a_period_with_nothing_delivered_says_so(self):
        InternalMarketOrder.objects.all().delete()
        page = self.client.get(reverse("market:balance")).content.decode()
        self.assertIn("Nenhuma compra interna entregue neste período.", page)
        self.assertNotIn("Quem comprou de quem", page)

    def test_the_market_page_links_the_balance_and_the_orders_filter_by_who_bought_and_who_sold(self):
        page = self.client.get(reverse("market:dashboard") + f"?seller={self.beta.pk}").content.decode()
        self.assertIn(reverse("market:balance"), page)
        self.assertIn(f'<option value="{self.beta.pk}" selected>Beta</option>', page)
        sold = self.client.get(reverse("market:orders-partial"), {"seller": str(self.beta.pk)})
        self.assertEqual(len(sold.context["object_list"]), 2)
        bought = self.client.get(reverse("market:orders-partial"), {"buyer": str(self.alpha.pk), "status": "completed"})
        self.assertEqual(len(bought.context["object_list"]), 1)

    def test_the_page_asks_for_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("market:balance")).status_code, 302)


class RelationsTests(BalanceBase):
    """N-114: who bought from whom, each purchase, and what it was for."""

    def test_the_work_behind_a_purchase_is_read_from_the_order(self):
        from .balance import short_gold, why

        order = InternalMarketOrder(reason_detail="Tribunal | Esconderijo | missing wood=21887 for level 32")
        self.assertEqual(why(order), {"city": "Tribunal", "building": "Esconderijo", "icon": "game/buildings/safehouse.png", "level": 32, "missing": 21887})
        order.reason_detail = "Pena Capital | Armazem | missing wood=2674 for level 26 | pedido 5000, atendido 2674"
        self.assertEqual((why(order)["building"], why(order)["icon"]), ("Armazém", "game/buildings/warehouse.png"))
        order.reason_detail = "Capital | Residencia do Governador | missing marble=10 for level 7"
        self.assertEqual(why(order)["building"], "Residência do Governador")
        order.reason_detail = "Capital | Prédio Novo | missing marble=10 for level 7"
        self.assertEqual((why(order)["building"], why(order)["icon"]), ("Prédio Novo", ""))
        order.reason_detail = "comprado na mão"
        self.assertIsNone(why(order))
        self.assertEqual([short_gold(value) for value in (8_476_425, 2_000_000, 420_000, 35_000, 4_800, 0)], ["8,5 mi", "2 mi", "420 mil", "35 mil", "4.800", "0"])

    def test_each_pair_of_accounts_has_its_purchases_both_ways(self):
        relations = {(rel["payer"], rel["receiver"]): rel for rel in build_balance(30)["relations"]}
        self.assertEqual(sorted(relations), [("Alpha", "Beta"), ("Alpha", "Gamma"), ("Gamma", "Beta")])
        beta = relations[("Alpha", "Beta")]
        self.assertEqual((beta["gold"], beta["gross"], beta["count"], beta["even"], beta["nothing"], beta["pct"]), (33_000, 37_000, 2, False, False, 100))
        self.assertEqual({(deal["buyer"], deal["seller"], deal["gold"], deal["state"]) for deal in beta["deals"]},
                         {("Alpha", "Beta", 35_000, "done"), ("Beta", "Alpha", 2_000, "done")})
        gamma = relations[("Alpha", "Gamma")]["deals"][0]
        self.assertEqual((gamma["amount"], gamma["ordered"], gamma["state"], gamma["state_label"]), (300, 500, "partial", "Em parte"))
        nothing = relations[("Gamma", "Beta")]
        self.assertEqual((nothing["nothing"], nothing["empty"], nothing["pct"], nothing["deals"][0]["state_label"]), (True, 1, 0, "Sem entrega"))

    def test_a_purchase_says_where_it_came_from_where_it_went_and_why_it_failed(self):
        AccountSnapshot.objects.filter(game_account=self.beta).update(cities=[{"id": 77, "name": "Porto Beta"}])
        AccountSnapshot.objects.filter(game_account=self.gamma).update(cities=[{"id": 55, "name": "Vila Gamma"}])
        order = self.order(self.gamma, self.beta, MARBLE, 900, 10, status="failed")
        InternalMarketOrder.objects.filter(pk=order.pk).update(
            seller_city_id=77, target_city_id=55,
            result_note="Compra interna falhou. | Ouro insuficiente para a compra apos varias tentativas (guard pre-compra). | cleanup_job_id=abc remain=900",
        )
        deal = next(deal for rel in build_balance(30)["relations"] for deal in rel["deals"] if deal["pk"] == order.pk)
        self.assertEqual((deal["from_city"], deal["to_city"]), ("Porto Beta", "Vila Gamma"))
        self.assertEqual(deal["failure"], "Ouro insuficiente para a compra apos varias tentativas (guard pre-compra).")
        InternalMarketOrder.objects.filter(pk=order.pk).update(reason_detail="Tribunal | Templo | missing marble=900 for level 31")
        deal = next(deal for rel in build_balance(30)["relations"] for deal in rel["deals"] if deal["pk"] == order.pk)
        self.assertEqual(deal["to_city"], "Tribunal")                                      # the work names the city the resource was for

    def test_the_grid_has_buyers_down_and_sellers_across(self):
        matrix = build_balance(30)["matrix"]
        self.assertEqual(matrix["sellers"], ["Alpha", "Beta", "Gamma"])
        self.assertEqual([row["buyer"] for row in matrix["rows"]], ["Alpha", "Beta"])        # Gamma paid nothing that arrived
        alpha = matrix["rows"][0]
        self.assertEqual([(cell["seller"], cell["short"], cell["pct"]) for cell in alpha["cells"]], [("Alpha", "", 0), ("Beta", "35 mil", 100), ("Gamma", "4.800", 14)])
        self.assertEqual((alpha["total"], alpha["cells"][0]["alpha"], alpha["cells"][1]["alpha"]), (39_800, "0", "0.56"))

    def test_the_works_put_together_what_each_one_cost_and_who_sold_it(self):
        first = self.order(self.gamma, self.beta, WOOD, 1000, 30)
        second = self.order(self.gamma, self.alpha, MARBLE, 500, 10)
        InternalMarketOrder.objects.filter(pk__in=[first.pk, second.pk]).update(reason_detail="Tribunal | Esconderijo | missing wood=1000 for level 32")
        works = build_balance(30)["works"]
        hideout = next(work for work in works if work["building"] == "Esconderijo")
        self.assertEqual((hideout["city"], hideout["level"], hideout["gold"], hideout["orders"]), ("Tribunal", 32, 35_000, 2))
        self.assertEqual((hideout["resources_list"], hideout["sellers_list"]), ([{"idx": WOOD, "amount": 1000}, {"idx": MARBLE, "amount": 500}], ["Beta", "Alpha"]))
        self.assertIn("Sem motivo registrado", [work["building"] for work in works])        # the orders without a reason, together

    def test_the_page_shows_the_grid_the_pairs_and_the_works(self):
        order = self.order(self.gamma, self.beta, WOOD, 1000, 30)
        InternalMarketOrder.objects.filter(pk=order.pk).update(reason_detail="Tribunal | Esconderijo | missing wood=1000 for level 32")
        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x"))
        page = self.client.get(reverse("market:balance")).content.decode()
        for text in ("Quem comprou de quem", "Compras entre as contas", "Para quê", "Esconderijo 32", "Em parte",
                     reverse("market:order-detail", args=[order.pk]), "game/buildings/safehouse.png", "Alpha pagou 35.000 de ouro a Beta"):
            self.assertIn(text, page)
