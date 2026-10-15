"""N-124: plans by tier of active sub-accounts, 7-day trial, Asaas payment and what happens when it is late."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.accounts.services.discover_service import sync_game_accounts
from apps.jobs.models import Job
from apps.orgs.models import Membership, Organization
from apps.orgs.services import default_organization

from . import plans
from .models import BillingAccount, BillingEvent
from .services import account_for, handle_event, state_of

TOKEN = "t" * 40


def customer(username="cliente", role=Membership.OWNER, organization=None):
    user = get_user_model()(username=username)
    user._own_organization = True
    user.set_password("x")
    user.save()
    organization = organization or Organization.objects.create(name=f"Org {username}", slug=f"org-{username}")
    Membership.objects.create(organization=organization, user=user, role=role)
    return user, organization


class PlanRulesTests(TestCase):
    def setUp(self):
        _user, self.org = customer()

    def test_prices_and_tiers(self):
        self.assertEqual(plans.price_for(4), Decimal("14.90"))
        self.assertEqual(plans.price_for(10), Decimal("29.90"))
        self.assertEqual(plans.price_for(80), Decimal("149.90"))
        self.assertEqual(plans.price_for(90), Decimal("171.00"))
        self.assertEqual(plans.tier_for(5), 10)
        self.assertEqual(plans.brl(Decimal("1149.9")), "R$ 1.149,90")

    def test_the_main_organization_is_exempt_and_a_new_one_starts_a_trial(self):
        self.assertEqual(account_for(default_organization()).status, BillingAccount.EXEMPT)
        account = account_for(self.org)
        self.assertEqual(account.status, BillingAccount.TRIAL)
        self.assertAlmostEqual((account.trial_ends_at - self.org.created_at).total_seconds(), 7 * 86400, delta=2)

    def test_trial_then_paused_then_read_only(self):
        account = account_for(self.org)
        end = account.trial_ends_at
        self.assertEqual(state_of(account, end - timedelta(hours=1)).code, "trial")
        self.assertEqual(state_of(account, end - timedelta(hours=1)).limit, 4)
        paused = state_of(account, end + timedelta(hours=1))
        self.assertEqual((paused.code, paused.allows_work, paused.read_only), ("paused", False, False))
        self.assertEqual(state_of(account, end + timedelta(days=31)).code, "read_only")

    def test_paid_plan_grace_and_cancellation(self):
        account = account_for(self.org)
        due = timezone.now()
        account.status, account.paid_until, account.limit = BillingAccount.ACTIVE, due, 10
        self.assertEqual(state_of(account, due - timedelta(days=1)).code, "active")
        self.assertEqual(state_of(account, due - timedelta(days=1)).limit, 10)
        self.assertEqual(state_of(account, due + timedelta(days=2)).code, "overdue")
        self.assertTrue(state_of(account, due + timedelta(days=2)).allows_work)
        self.assertEqual(state_of(account, due + timedelta(days=4)).code, "paused")
        account.status = BillingAccount.CANCELED
        self.assertEqual(state_of(account, due - timedelta(days=1)).code, "active")      # until the end of what was paid
        self.assertEqual(state_of(account, due + timedelta(days=1)).code, "paused")      # no grace once canceled


@override_settings(AGENT_TOKEN="", AGENT_ALLOWED_IPS="")
class EnforcementTests(TestCase):
    def setUp(self):
        self.user, self.org = customer()
        self.node = Node.objects.create(name="agente-cliente", transport=Node.TRANSPORT_HTTPS, organization=self.org)
        self.key = self.node.new_agent_token
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x",
                                              organization=self.org)
        self.worlds = [GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id=f"s{i}-br", name=f"J{i}",
                                                  server_language="br", server_number=i) for i in range(4)]
        with patch("apps.jobs.signals.dispatch_job"):
            self.job = Job.objects.create(account=self.account, node=self.node, action_code=100)

    def _claim(self):
        return self.client.post(reverse("agent-jobs:claim"), {"node_id": str(self.node.pk), "slots": 4},
                                content_type="application/json", HTTP_X_AGENT_TOKEN=self.key, HTTP_X_AGENT_NODE_ID=str(self.node.pk))

    def test_a_paused_organization_gets_no_jobs_until_it_pays(self):
        account = account_for(self.org)
        BillingAccount.objects.filter(pk=account.pk).update(trial_ends_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self._claim().json()["jobs"], [])
        self.assertTrue(Job._base_manager.filter(pk=self.job.pk, status="queued").exists())         # nothing lost
        BillingAccount.objects.filter(pk=account.pk).update(status=BillingAccount.ACTIVE, paid_until=timezone.now() + timedelta(days=20))
        self.assertEqual(len(self._claim().json()["jobs"]), 1)

    def test_turning_on_a_sub_account_beyond_the_plan_is_refused(self):
        self.client.force_login(self.user)
        extra = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s9-br", name="J9",
                                           server_language="br", server_number=9, active=False)
        response = self.client.post(reverse("accounts:game-account-toggle", args=[extra.pk]))
        self.assertIn("4 subcontas", response["HX-Trigger"])
        extra.refresh_from_db()
        self.assertFalse(extra.active)
        self.worlds[0].active = False
        self.worlds[0].save()
        self.client.post(reverse("accounts:game-account-toggle", args=[extra.pk]))
        extra.refresh_from_db()
        self.assertTrue(extra.active)

    def test_a_new_sub_account_beyond_the_plan_arrives_paused(self):
        sync_game_accounts(str(self.account.pk), [
            {"lobby_account_id": 1, "server_id": f"s{i}-br", "server_number": i, "name": f"J{i}"} for i in range(5)
        ])
        self.assertFalse(GameAccount._base_manager.get(server_id="s4-br").active)
        self.assertEqual(GameAccount._base_manager.filter(account=self.account, active=True).count(), 4)

    def test_read_only_after_30_days(self):
        account = account_for(self.org)
        BillingAccount.objects.filter(pk=account.pk).update(trial_ends_at=timezone.now() - timedelta(days=40))
        self.client.force_login(self.user)
        response = self.client.post(reverse("accounts:game-account-toggle", args=[self.worlds[0].pk]))
        self.assertEqual(response.status_code, 402)
        self.assertEqual(self.client.get(reverse("billing:plan")).status_code, 200)              # the plan page still works
        self.assertIn("Somente leitura", self.client.get(reverse("billing:plan")).content.decode())

    def test_the_main_organization_is_never_held(self):
        staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(account_for(default_organization()).status, BillingAccount.EXEMPT)
        self.assertNotIn("Ver plano", self.client.get("/").content.decode())


@override_settings(ASAAS_API_KEY="chave-de-teste", ASAAS_SANDBOX=True, ASAAS_WEBHOOK_TOKEN=TOKEN)
class PlanPageTests(TestCase):
    def setUp(self):
        self.user, self.org = customer()
        self.client.force_login(self.user)

    def test_the_owner_subscribes_through_an_asaas_link(self):
        page = self.client.get(reverse("billing:plan")).content.decode()
        self.assertIn("R$ 14,90", page)
        self.assertIn("Teste grátis", page)
        with patch("apps.billing.asaas.create_payment_link", return_value=("lnk_1", "https://sandbox.asaas.com/c/lnk_1")) as create:
            response = self.client.post(reverse("billing:plan"), {"limit": "10"})
        self.assertRedirects(response, "https://sandbox.asaas.com/c/lnk_1", fetch_redirect_response=False)
        self.assertEqual(create.call_args.kwargs["value"], Decimal("29.90"))
        account = BillingAccount.objects.get(organization=self.org)
        self.assertEqual((account.link_id, account.link_limit), ("lnk_1", 10))

    def test_a_boosted_request_leaves_to_the_asaas_with_a_full_page_load(self):
        with patch("apps.billing.asaas.create_payment_link", return_value=("lnk_2", "https://sandbox.asaas.com/c/lnk_2")):
            response = self.client.post(reverse("billing:plan"), {"limit": "4"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], "https://sandbox.asaas.com/c/lnk_2")

    def test_an_operator_cannot_pay_or_change(self):
        operator, _org = customer("operador", role=Membership.OPERATOR, organization=self.org)
        self.client.force_login(operator)
        self.assertIn("Só o dono", self.client.get(reverse("billing:plan")).content.decode())
        self.assertEqual(self.client.post(reverse("billing:plan"), {"limit": "10"}).status_code, 404)

    def _subscribed(self, days_left=15, limit=10):
        BillingAccount.objects.filter(organization=account_for(self.org).organization).update(
            status=BillingAccount.ACTIVE, subscription_id="sub_1", customer_id="cus_1", limit=limit,
            price=plans.price_for(limit), paid_until=timezone.now() + timedelta(days=days_left))

    def test_going_up_charges_the_difference_and_waits_for_it(self):
        self._subscribed(days_left=15)
        account = BillingAccount.objects.get(organization=self.org)
        start, end = __import__("apps.billing.services", fromlist=["period_of"]).period_of(account)
        expected = plans.prorated(10, 20, start, end, timezone.now())
        self.assertIn(plans.brl(expected), self.client.get(reverse("billing:plan")).content.decode())   # shown on the tier
        with patch("apps.billing.asaas.create_charge", return_value=("pay_up", "https://sandbox.asaas.com/i/pay_up")) as charge, \
             patch("apps.billing.asaas.update_subscription_value") as update:
            response = self.client.post(reverse("billing:plan"), {"limit": "20"})
        self.assertRedirects(response, "https://sandbox.asaas.com/i/pay_up", fetch_redirect_response=False)
        self.assertAlmostEqual(float(charge.call_args.kwargs["value"]), float(expected), places=2)
        self.assertGreater(charge.call_args.kwargs["value"], Decimal("8"))      # about half of R$ 20,00
        update.assert_not_called()                                               # the subscription waits for the payment
        account.refresh_from_db()
        self.assertEqual((account.limit, account.upgrade_limit, account.upgrade_payment_id), (10, 20, "pay_up"))
        self.assertIn("esperando o pagamento", self.client.get(reverse("billing:plan")).content.decode())

    def test_going_down_waits_for_the_next_due_date(self):
        self._subscribed(days_left=15, limit=20)
        with patch("apps.billing.asaas.update_subscription_value") as update:
            self.client.post(reverse("billing:plan"), {"limit": "10"})
        self.assertEqual(update.call_args.args[:2], ("sub_1", Decimal("29.90")))   # the next charge comes lower
        account = BillingAccount.objects.get(organization=self.org)
        self.assertEqual((account.limit, account.pending_limit), (20, 10))         # still 20 until the due date
        with patch("apps.billing.asaas.update_subscription_value") as update:
            self.client.post(reverse("billing:plan"), {"action": "keep"})
        update.assert_called_once()
        account.refresh_from_db()
        self.assertIsNone(account.pending_limit)

    def test_a_few_cents_go_up_without_a_charge(self):
        self._subscribed(days_left=0.2)
        with patch("apps.billing.asaas.create_charge") as charge, patch("apps.billing.asaas.update_subscription_value"):
            self.client.post(reverse("billing:plan"), {"limit": "20"})
        charge.assert_not_called()
        self.assertEqual(BillingAccount.objects.get(organization=self.org).limit, 20)

    def test_change_and_cancel_an_active_subscription(self):
        self._subscribed(days_left=20)
        with patch("apps.billing.asaas.update_subscription_value") as update:
            self.client.post(reverse("billing:plan"), {"limit": "4"})
        self.assertEqual(update.call_args.args[:2], ("sub_1", Decimal("14.90")))
        self.assertEqual(BillingAccount.objects.get(organization=self.org).pending_limit, 4)
        with patch("apps.billing.asaas.cancel_subscription") as cancel:
            self.client.post(reverse("billing:plan"), {"action": "cancel"})
        cancel.assert_called_once_with("sub_1")
        self.assertEqual(BillingAccount.objects.get(organization=self.org).status, BillingAccount.CANCELED)

    def test_a_tier_smaller_than_what_is_active_is_refused(self):
        node = Node.objects.create(name="agente-x", organization=self.org)
        account = Account.objects.create(node=node, label="C", email="x@example.com", password_enc="x", organization=self.org)
        for i in range(5):
            GameAccount.objects.create(account=account, lobby_account_id=1, server_id=f"s{i}", name=f"J{i}", server_language="br", server_number=i)
        with patch("apps.billing.asaas.create_payment_link") as create:
            self.client.post(reverse("billing:plan"), {"limit": "4"})
        create.assert_not_called()

    def test_the_trial_ending_shows_on_every_page(self):
        BillingAccount.objects.filter(pk=account_for(self.org).pk).update(trial_ends_at=timezone.now() + timedelta(days=2))
        self.assertIn("teste grátis termina", self.client.get("/accounts/").content.decode())


@override_settings(ASAAS_WEBHOOK_TOKEN=TOKEN)
class WebhookTests(TestCase):
    def setUp(self):
        _user, self.org = customer()
        account = account_for(self.org)
        account.link_id, account.link_limit = "lnk_9", 20
        account.save()

    def _send(self, event, obj, event_id=None, token=TOKEN):
        body = {"id": event_id or f"evt_{event}_{obj.get('id')}", "event": event, "dateCreated": "2026-10-14 10:00:00", obj["object"]: obj}
        return self.client.post(reverse("asaas-webhook"), json.dumps(body), content_type="application/json", HTTP_ASAAS_ACCESS_TOKEN=token)

    def test_without_the_right_token_nothing_happens(self):
        self.assertEqual(self._send("PAYMENT_RECEIVED", {"object": "payment", "id": "pay_1"}, token="errado").status_code, 401)
        with self.settings(ASAAS_WEBHOOK_TOKEN=""):
            self.assertEqual(self._send("PAYMENT_RECEIVED", {"object": "payment", "id": "pay_1"}).status_code, 404)
        self.assertFalse(BillingEvent.objects.exists())

    def test_subscription_payment_late_and_canceled(self):
        today = timezone.localdate()
        self._send("SUBSCRIPTION_CREATED", {"object": "subscription", "id": "sub_9", "paymentLink": "lnk_9", "customer": "cus_9",
                                            "value": 49.9, "status": "ACTIVE"})
        account = BillingAccount.objects.get(organization=self.org)
        self.assertEqual((account.subscription_id, account.customer_id, account.limit), ("sub_9", "cus_9", 20))
        self.assertEqual(account.status, BillingAccount.TRIAL)                                     # not paid yet
        with patch("apps.billing.services._notify") as notify:
            response = self._send("PAYMENT_RECEIVED", {"object": "payment", "id": "pay_1", "subscription": "sub_9",
                                                       "customer": "cus_9", "value": 49.9, "dueDate": today.isoformat()})
            self.assertEqual(response.json(), {"received": True, "result": "paid"})
            notify.assert_called_once()
            again = self._send("PAYMENT_RECEIVED", {"object": "payment", "id": "pay_1", "subscription": "sub_9"})
            self.assertEqual(again.json()["result"], "duplicate")
        account.refresh_from_db()
        self.assertEqual(account.status, BillingAccount.ACTIVE)
        self.assertGreater(account.paid_until, timezone.now() + timedelta(days=27))
        self.assertEqual(state_of(account).code, "active")
        with patch("apps.billing.services._notify") as notify:
            self._send("PAYMENT_OVERDUE", {"object": "payment", "id": "pay_2", "subscription": "sub_9"})
            self.assertIn("venceu", notify.call_args.args[1])
        self._send("SUBSCRIPTION_DELETED", {"object": "subscription", "id": "sub_9"})
        account.refresh_from_db()
        self.assertEqual(account.status, BillingAccount.CANCELED)
        self.assertEqual(BillingEvent.objects.filter(organization=self.org).count(), 4)

    def test_the_difference_paid_raises_the_tier_without_extending_the_period(self):
        account = BillingAccount.objects.get(organization=self.org)
        until = timezone.now() + timedelta(days=15)
        BillingAccount.objects.filter(pk=account.pk).update(status=BillingAccount.ACTIVE, subscription_id="sub_9", customer_id="cus_9",
                                                            limit=10, price=Decimal("29.90"), paid_until=until,
                                                            upgrade_payment_id="pay_up", upgrade_limit=20, upgrade_value=Decimal("10.00"))
        with patch("apps.billing.asaas.update_subscription_value") as update, patch("apps.billing.services._notify"):
            response = self._send("PAYMENT_RECEIVED", {"object": "payment", "id": "pay_up", "customer": "cus_9", "value": 10.0,
                                                       "dueDate": timezone.localdate().isoformat()})
        self.assertEqual(response.json()["result"], "upgraded")
        update.assert_called_once()
        self.assertEqual(update.call_args.args[:2], ("sub_9", Decimal("49.90")))
        account.refresh_from_db()
        self.assertEqual((account.limit, account.upgrade_payment_id), (20, ""))
        self.assertEqual(account.paid_until, until)                                # the period is the same

    def test_the_lower_tier_starts_with_the_new_period_and_pauses_the_extra(self):
        account = BillingAccount.objects.get(organization=self.org)
        BillingAccount.objects.filter(pk=account.pk).update(status=BillingAccount.ACTIVE, subscription_id="sub_9", customer_id="cus_9",
                                                            limit=10, price=Decimal("29.90"), paid_until=timezone.now(), pending_limit=4)
        node = Node.objects.create(name="agente-w", organization=self.org)
        game = Account.objects.create(node=node, label="C", email="w@example.com", password_enc="x", organization=self.org)
        for i in range(6):
            GameAccount.objects.create(account=game, lobby_account_id=1, server_id=f"w{i}", name=f"W{i}", server_language="br", server_number=i)
        with patch("apps.billing.services._notify") as notify:
            self._send("PAYMENT_RECEIVED", {"object": "payment", "id": "pay_next", "subscription": "sub_9",
                                            "dueDate": timezone.localdate().isoformat()})
        account.refresh_from_db()
        self.assertEqual((account.limit, account.pending_limit), (4, None))
        self.assertEqual(GameAccount._base_manager.filter(account=game, active=True).count(), 4)
        self.assertFalse(GameAccount._base_manager.get(server_id="w5").active)        # the most recent went first
        self.assertTrue(any("pausadas" in call.args[1] for call in notify.call_args_list))

    def test_a_notice_about_nobody_is_kept_and_ignored(self):
        response = self._send("PAYMENT_RECEIVED", {"object": "payment", "id": "pay_x", "subscription": "sub_estranha"})
        self.assertEqual(response.json()["result"], "unknown")


class SitePricesTests(TestCase):
    def test_portuguese_prices_in_reais_english_in_dollars(self):
        self.assertIn("R$ 14,90", self.client.get("/pt/precos/").content.decode())
        self.assertIn("US$ 4", self.client.get("/en/pricing/").content.decode())
