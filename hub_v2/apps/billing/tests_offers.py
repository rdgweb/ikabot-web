"""N-243: characters choose the plan; GameForge lobbies determine optional services."""
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Account, Node
from apps.jobs.models import Job
from apps.billing.models import BillingAccount
from apps.billing.offers import estimate_monthly
from apps.billing.services import set_billing_open
from apps.billing.tests import customer
from apps.website import content


class MonthlyEstimateTests(TestCase):
    def test_six_characters_in_one_lobby_and_in_six_lobbies_have_different_addons(self):
        one = estimate_monthly(active_subaccounts=6, platform_agents=1, platform_proxies=1)
        six = estimate_monthly(active_subaccounts=6, platform_agents=6, platform_proxies=6)
        self.assertEqual((one.limit, one.base, one.total), (10, Decimal('29.90'), Decimal('49.70')))
        self.assertEqual((six.base, six.agents, six.proxies, six.total),
                         (Decimal('29.90'), Decimal('89.40'), Decimal('29.40'), Decimal('148.70')))

    def test_customer_infrastructure_has_no_platform_addon_and_large_tiers_round_up(self):
        own = estimate_monthly(active_subaccounts=6)
        self.assertEqual(own.total, Decimal('29.90'))
        large = estimate_monthly(active_subaccounts=81)
        self.assertEqual((large.limit, large.base), (90, Decimal('171.00')))

    def test_estimate_follows_the_actual_addon_price_table(self):
        with patch('apps.billing.plans.ADDON_AGENT', Decimal('18.00')):
            self.assertEqual(estimate_monthly(active_subaccounts=4, platform_agents=2).agents, Decimal('36.00'))


@override_settings(SITE_URL='https://site.example', UMAMI_URL='', UMAMI_WEBSITE_ID='')
class PublicOfferTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_simulation_discloses_total_without_creating_resources_or_payments(self):
        with patch('apps.billing.asaas.create_payment_link') as payment, patch('apps.billing.asaas.create_charge') as charge:
            for lang in ('pt', 'en'):
                response = self.client.get(content.path_of(lang, 'pricing'), {
                    'estimate': '1', 'active_subaccounts': '6', 'lobbies': '6', 'platform_agents': '6', 'platform_proxies': '6'})
                self.assertContains(response, 'R$ 148,70')
                self.assertContains(response, 'R$ 89,40')
                self.assertContains(response, 'GameForge')
                self.assertNotContains(response, 'US$')
                self.assertEqual(response.context['estimate']['limit'], 10)
            payment.assert_not_called()
            charge.assert_not_called()
        for model in (Account, Node, Job, BillingAccount):
            self.assertEqual(model._base_manager.count(), 0)

    def test_invalid_counts_do_not_return_a_misleading_total(self):
        values = {'estimate': '1', 'active_subaccounts': '6', 'lobbies': '1', 'platform_agents': '2', 'platform_proxies': '0'}
        response = self.client.get('/pt/precos/', values)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(response.context['estimate'])
        for key, bad in [('active_subaccounts', '301'), ('platform_agents', '-1'), ('lobbies', 'garbage')]:
            response = self.client.get('/pt/precos/', {**values, 'platform_agents': '0', key: bad})
            self.assertEqual(response.status_code, 400)

    def test_signup_and_closed_beta_keep_prices_separate_from_actual_payment(self):
        set_billing_open(False)
        page = self.client.get('/pt/cadastro/')
        self.assertContains(page, 'por lobby/mês')
        self.assertContains(page, 'não cria assinatura paga automaticamente')
        self.assertContains(self.client.get('/pt/precos/'), 'As assinaturas ainda não estão abertas')
        english = self.client.get('/en/pricing/').content.decode()
        self.assertIn('"priceCurrency": "BRL"', english)


class CheckoutDisclosureTests(TestCase):
    def setUp(self):
        cache.clear()
        set_billing_open(True)
        user, self.org = customer('offer-owner')
        self.client.force_login(user)

    @patch('apps.billing.asaas.configured', return_value=True)
    @patch('apps.accounts.platform_agent.accounts_with_agent', return_value=6)
    @patch('apps.billing.views._addons', return_value=6)
    def test_checkout_shows_both_full_recurring_cost_and_unchanged_initial_amount(self, *_):
        page = self.client.get('/organizacao/plano/')
        self.assertContains(page, 'R$ 148,70')
        self.assertContains(page, 'checkout cobra a faixa do plano')
        option = next(o for o in page.context['options'] if o['limit'] == 10)
        self.assertEqual((option['price'], option['total']), ('R$ 29,90', 'R$ 148,70'))
        with patch('apps.billing.asaas.create_payment_link', return_value=('offer-link', 'https://sandbox.asaas.com/offer')) as pay:
            response = self.client.post('/organizacao/plano/', {'limit': '10'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pay.call_args.kwargs['value'], Decimal('29.90'))
