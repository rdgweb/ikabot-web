"""N-129: customers' own proxies, the platform-proxies add-on, and maintenance that never crosses organizations."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, Node
from apps.billing.models import BillingAccount
from apps.billing.services import account_for
from apps.orgs.models import Membership, Organization
from apps.orgs.proxies_view import parse_lines
from apps.orgs.services import default_organization

from . import maintenance, platform
from .models import AccountProxyReservation, ProxyProfile


def healthy(proxy, ok=True):
    ProxyProfile._base_manager.filter(pk=proxy.pk).update(active=True, last_test_status=ok, last_test_latency_ms=100)


def fake_test(proxy):
    ProxyProfile._base_manager.filter(pk=proxy.pk).update(last_test_status=True, last_test_latency_ms=90)
    return {"success": True}


class ParseTests(TestCase):
    def test_the_formats_people_paste(self):
        good, bad = parse_lines("1.2.3.4:8080:joao:segredo\nana:s3nh@proxy.example.com:3128\nhttp://u:p@5.6.7.8:80\n9.9.9.9:1080\nlixo aqui\n1.1.1.1:99999")
        self.assertEqual([(g["address"], g["port"], g["username"]) for g in good],
                         [("1.2.3.4", 8080, "joao"), ("proxy.example.com", 3128, "ana"), ("5.6.7.8", 80, "u"), ("9.9.9.9", 1080, "")])
        self.assertEqual(len(bad), 2)


@override_settings(ASAAS_API_KEY="k", ASAAS_SANDBOX=True)
class PlatformProxiesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.platform = default_organization()
        self.pool = []
        for i in range(25):
            proxy = ProxyProfile._base_manager.create(organization=self.platform, provider="webshare", webshare_id=f"w{i}",
                                                      address=f"10.0.0.{i}", port=8000 + i)
            healthy(proxy)
            self.pool.append(proxy)
        user = get_user_model()(username="dono")
        user._own_organization = True
        user.set_password("x")
        user.save()
        self.org = Organization.objects.create(name="Blood", slug="blood")
        Membership.objects.create(organization=self.org, user=user, role="owner")
        self.user = user
        self.node = Node.objects.create(name="agente-cliente", organization=self.org, transport="https")
        self.account = Account._base_manager.create(node=self.node, label="Conta cliente", email="c@example.com",
                                                    password_enc="x", organization=self.org)
        self.client.force_login(user)

    def _subscribe(self):
        account = account_for(self.org)
        BillingAccount.objects.filter(pk=account.pk).update(status=BillingAccount.ACTIVE, subscription_id="sub_1", customer_id="cus_1",
                                                            limit=10, price=Decimal("29.90"), paid_until=timezone.now() + timedelta(days=20))

    def test_the_customer_adds_and_tests_their_own_proxies_and_sees_only_theirs(self):
        # the page tests in parallel threads; SQLite in tests does not take parallel writes
        with patch("apps.orgs.proxies_view._test_many", side_effect=lambda proxies: [fake_test(p) for p in proxies]):
            self.client.post("/organizacao/proxies/", {"action": "add", "lines": "1.2.3.4:8080:joao:segredo\n5.6.7.8:3128"})
        mine = ProxyProfile._base_manager.filter(organization=self.org)
        self.assertEqual(mine.count(), 2)
        self.assertTrue(all(p.last_test_status for p in mine))
        page = self.client.get("/organizacao/proxies/").content.decode()
        self.assertIn("1.2.3.4:8080", page)
        self.assertNotIn("10.0.0.1:", page)                                     # the platform's pool is not theirs
        self.assertNotIn("segredo", page)

    def test_the_add_on_needs_a_subscription_and_lends_3_exclusive_proxies(self):
        self.client.post("/organizacao/proxies/", {"action": "addon", "account": str(self.account.pk), "on": "1"})
        self.assertFalse(ProxyProfile._base_manager.filter(lent_to=self.account).exists())   # no subscription yet
        self._subscribe()
        with patch("apps.billing.asaas.update_subscription_value") as update:
            self.client.post("/organizacao/proxies/", {"action": "addon", "account": str(self.account.pk), "on": "1"})
        lent = list(ProxyProfile._base_manager.filter(lent_to=self.account))
        self.assertEqual(len(lent), 3)
        self.assertTrue(all(p.organization_id == self.org.pk for p in lent))
        self.assertEqual(AccountProxyReservation._base_manager.filter(account=self.account).count(), 3)
        self.assertEqual(update.call_args.args[1], Decimal("29.90") + Decimal("4.90"))
        self.node.refresh_from_db()
        self.assertIn(self.node.proxy, [p.proxy_url for p in lent])               # the node uses one of the 3
        # the customer's agent asks for its lobby proxies: exactly these, never another one of the pool
        from apps.orgs import scope
        from apps.proxy.services import reserve_lobby_proxies

        token = scope.activate(self.org)
        try:
            self.assertEqual({p.pk for p in reserve_lobby_proxies(account=self.account)}, {p.pk for p in lent})
        finally:
            scope.deactivate(token)

    def test_switching_off_lasts_until_the_due_date_then_they_go_back(self):
        self._subscribe()
        with patch("apps.billing.asaas.update_subscription_value"):
            self.client.post("/organizacao/proxies/", {"action": "addon", "account": str(self.account.pk), "on": "1"})
            self.client.post("/organizacao/proxies/", {"action": "addon", "account": str(self.account.pk), "on": "0"})
        self.assertEqual(ProxyProfile._base_manager.filter(lent_to=self.account).count(), 3)   # still theirs
        platform.end_due(now=timezone.now() + timedelta(days=21))
        self.assertFalse(ProxyProfile._base_manager.filter(lent_to=self.account).exists())
        self.assertFalse(AccountProxyReservation._base_manager.filter(account=self.account).exists())
        self.assertEqual(ProxyProfile._base_manager.filter(organization=self.platform).count(), 25)
        self.node.refresh_from_db()
        self.assertEqual(self.node.proxy, "")

    def test_the_pool_keeps_a_reserve_for_the_platform(self):
        self.assertEqual(platform.accounts_available(), (25 - platform.PLATFORM_RESERVE) // 3)

    def test_maintenance_never_crosses_organizations(self):
        # the platform's account with a dead proxy, and a free healthy proxy of the customer
        pacc = Account._base_manager.create(node=Node.objects.create(name="no-plataforma"), label="Plataforma", email="p@example.com",
                                            password_enc="x", organization=self.platform)
        dead = self.pool[0]
        AccountProxyReservation._base_manager.create(account=pacc, proxy_profile=dead)
        ProxyProfile._base_manager.filter(pk=dead.pk).update(active=False)
        own = ProxyProfile._base_manager.create(organization=self.org, address="7.7.7.7", port=7777)
        healthy(own)
        # the customer's account (no add-on) with a dead proxy of their own
        cdead = ProxyProfile._base_manager.create(organization=self.org, address="8.8.8.8", port=8888, active=False)
        AccountProxyReservation._base_manager.create(account=self.account, proxy_profile=cdead)
        report = {"accounts_replaced": [], "without_proxy": [], "nodes_replaced": []}
        maintenance._heal_accounts(report)
        platform_got = set(AccountProxyReservation._base_manager.filter(account=pacc).values_list("proxy_profile__organization", flat=True))
        customer_got = set(AccountProxyReservation._base_manager.filter(account=self.account).values_list("proxy_profile__organization", flat=True))
        self.assertEqual(platform_got, {self.platform.pk})                         # never the customer's 7.7.7.7
        self.assertEqual(customer_got, {self.org.pk})                              # their own, never the platform's
        self.assertTrue(AccountProxyReservation._base_manager.filter(account=self.account, proxy_profile=own).exists())

    def test_a_lent_proxy_that_dies_is_given_back_and_replaced(self):
        self._subscribe()
        with patch("apps.billing.asaas.update_subscription_value"):
            self.client.post("/organizacao/proxies/", {"action": "addon", "account": str(self.account.pk), "on": "1"})
        victim = ProxyProfile._base_manager.filter(lent_to=self.account).first()
        ProxyProfile._base_manager.filter(lent_to=self.account).update(active=False)        # all three gone
        maintenance._heal_accounts({"accounts_replaced": [], "without_proxy": [], "nodes_replaced": []})
        victim.refresh_from_db()
        self.assertIsNone(victim.lent_to_id)
        self.assertEqual(victim.organization_id, self.platform.pk)
        self.assertEqual(ProxyProfile._base_manager.filter(lent_to=self.account, active=True).count(), 3)
