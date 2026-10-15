"""N-131: the platform agent add-on — paid first when it costs R$ 5,00 or more, created by the platform's
supervisor with a proxy always required, never changed by the customer, removed when it ends."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts import platform_agent
from apps.accounts.models import Account, AgentProvisionRequest, DockerHost, Node
from apps.billing.models import BillingAccount
from apps.billing.services import account_for, handle_event
from apps.notes.services import RegistryRelease
from apps.orgs.models import Membership, Organization
from apps.orgs.services import default_organization
from apps.proxy.models import ProxyProfile
from core.auth.tokens import hash_agent_token

DIGEST = "sha256:" + "b" * 64
RELEASE = RegistryRelease(component="agent", repository="blackoneal/ikabot-web-agent", version="0.1.110", digest=DIGEST)


class PlatformAgentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.host = DockerHost._base_manager.create(name="docker-plataforma", organization=default_organization(),
                                                    last_seen_at=timezone.now())
        self.host_token = self.host.new_enrollment_token
        self.settings_ctx = override_settings(PLATFORM_AGENT_HOST_ID=str(self.host.pk), PLATFORM_AGENT_LIMIT=2,
                                              ASAAS_API_KEY="k", ASAAS_SANDBOX=True, SITE_URL="https://hub.example",
                                              ASAAS_WEBHOOK_TOKEN="t" * 40)
        self.settings_ctx.enable()
        self.addCleanup(self.settings_ctx.disable)
        for i in range(30):
            ProxyProfile._base_manager.create(organization=default_organization(), provider="webshare", webshare_id=f"w{i}",
                                              address=f"10.0.1.{i}", port=9000 + i, active=True, last_test_status=True,
                                              last_test_latency_ms=50)
        user = get_user_model()(username="dono")
        user._own_organization = True
        user.set_password("x")
        user.save()
        self.user = user
        self.org = Organization.objects.create(name="Blood", slug="blood")
        Membership.objects.create(organization=self.org, user=user, role="owner")
        self.own_node = Node.objects.create(name="pc-casa", organization=self.org, transport="https")
        self.account = Account._base_manager.create(node=self.own_node, label="Conta 1", email="c@example.com", password_enc="x",
                                                    organization=self.org)
        self.client.force_login(user)

    def _subscribe(self, days_left=15):
        billing = account_for(self.org)
        BillingAccount.objects.filter(pk=billing.pk).update(status=BillingAccount.ACTIVE, subscription_id="sub_1", customer_id="cus_1",
                                                            limit=10, price=Decimal("29.90"), paid_until=timezone.now() + timedelta(days=days_left))

    def _proxies_on(self):
        with patch("apps.billing.asaas.update_subscription_value"):
            self.client.post("/organizacao/proxies/", {"action": "addon", "account": str(self.account.pk), "on": "1"})

    def _agent(self, on="1", **patches):
        with patch("apps.billing.asaas.update_subscription_value") as update, \
             patch("apps.billing.asaas.create_charge", return_value=("pay_ag", "https://sandbox.asaas.com/i/pay_ag")) as charge, \
             patch("apps.notes.services.get_registry_release", return_value=RELEASE):
            response = self.client.post("/organizacao/proxies/", {"action": "agent", "account": str(self.account.pk), "on": on})
        return response, update, charge

    def _supervisor(self, path, method="get", **data):
        headers = {"HTTP_X_IKABOT_HOST_ID": str(self.host.pk), "HTTP_X_IKABOT_HOST_TOKEN": self.host_token}
        with patch("apps.notes.services.get_registry_release", return_value=RELEASE):
            if method == "get":
                return self.client.get(path, **headers)
            return self.client.post(path, data, content_type="application/json", **headers)

    def test_without_proxies_the_agent_is_refused(self):
        self._subscribe()
        self._agent()
        self.account.refresh_from_db()
        self.assertFalse(self.account.platform_agent)
        self.assertFalse(AgentProvisionRequest._base_manager.exists())

    def test_the_prorated_month_is_paid_first_then_the_agent_is_created(self):
        self._subscribe(days_left=15)
        self._proxies_on()
        response, _update, charge = self._agent()
        self.assertRedirects(response, "https://sandbox.asaas.com/i/pay_ag", fetch_redirect_response=False)
        self.assertGreater(charge.call_args.kwargs["value"], Decimal("5"))
        self.assertFalse(AgentProvisionRequest._base_manager.exists())             # nothing before the payment
        with patch("apps.billing.asaas.update_subscription_value") as update, patch("apps.billing.services._notify"):
            handle_event({"id": "evt_ag", "event": "PAYMENT_CONFIRMED",
                          "payment": {"object": "payment", "id": "pay_ag", "customer": "cus_1", "value": 7.0}})
        self.assertEqual(update.call_args.args[1], Decimal("29.90") + Decimal("4.90") + Decimal("14.90"))
        self.account.refresh_from_db()
        node = self.account.node
        self.assertTrue(node.platform_managed and self.account.platform_agent and self.account.platform_agent_billed)
        self.assertEqual((node.organization_id, self.account.previous_node_id), (self.org.pk, self.own_node.pk))
        self.assertIn(node.proxy, [p.proxy_url for p in ProxyProfile._base_manager.filter(lent_to=self.account)])

        payload = self._supervisor("/api/agent/supervisor/provision/next/").json()["provision"]
        env = payload["environment"]
        self.assertEqual((payload["action"], env["AGENT_REQUIRE_PROXY"], env["HUB_URL"]), ("create", "1", "https://hub.example"))
        self.assertEqual(payload["image"], f"blackoneal/ikabot-web-agent@{DIGEST}")
        self.assertEqual(hash_agent_token(env["AGENT_TOKEN"]), Node._base_manager.get(pk=node.pk).agent_token_hash)
        self.assertEqual(AgentProvisionRequest._base_manager.get().token_enc, "")    # the key in clear text left once
        self.assertIsNone(self._supervisor("/api/agent/supervisor/provision/next/").json()["provision"])
        self._supervisor(f"/api/agent/supervisor/provision/{payload['id']}/status/", "post", status="succeeded", message="ok")
        self.assertEqual(AgentProvisionRequest._base_manager.get().status, "succeeded")

    def test_a_small_prorated_month_starts_at_once_and_the_slots_are_counted(self):
        self._subscribe(days_left=0.1)
        self._proxies_on()
        _response, _update, charge = self._agent()
        charge.assert_not_called()
        self.account.refresh_from_db()
        self.assertTrue(self.account.platform_agent)
        self.assertFalse(self.account.platform_agent_billed)
        with self.settings(PLATFORM_AGENT_LIMIT=1):
            other = Account._base_manager.create(node=self.own_node, label="Conta 2", email="d@example.com", password_enc="x",
                                                 organization=self.org)
            self.assertIn("esgotadas", platform_agent.why_not(other))

    def test_switching_off_lasts_the_period_then_the_container_goes_and_the_account_returns(self):
        self._subscribe(days_left=0.1)
        self._proxies_on()
        self._agent()
        self.account.refresh_from_db()
        Account._base_manager.filter(pk=self.account.pk).update(platform_agent_billed=True)
        self._agent(on="0")
        self.account.refresh_from_db()
        node = self.account.platform_agent_node
        self.assertIsNotNone(self.account.platform_agent_ends_at)
        platform_agent.end_due(now=timezone.now() + timedelta(days=2))
        self.account.refresh_from_db()
        self.assertEqual(self.account.node_id, self.own_node.pk)
        self.assertFalse(self.account.platform_agent)
        node.refresh_from_db()
        self.assertFalse(node.active)
        self.assertTrue(AgentProvisionRequest._base_manager.filter(action="remove", node_ref=node.pk).exists())

    def test_the_customer_never_changes_the_platform_node(self):
        self._subscribe(days_left=0.1)
        self._proxies_on()
        self._agent()
        self.account.refresh_from_db()
        node = self.account.node
        for url in (f"/accounts/nodes/{node.pk}/edit/", f"/accounts/nodes/{node.pk}/new-key/", f"/accounts/nodes/{node.pk}/delete/",
                    f"/accounts/nodes/{node.pk}/toggle/"):
            response = self.client.post(url)
            self.assertRedirects(response, "/accounts/nodes/", fetch_redirect_response=False)
        self.assertTrue(Node._base_manager.get(pk=node.pk).active)

    def test_another_machine_never_receives_platform_work(self):
        self._subscribe(days_left=0.1)
        self._proxies_on()
        self._agent()
        other = DockerHost._base_manager.create(name="outra", organization=default_organization(), last_seen_at=timezone.now())
        headers = {"HTTP_X_IKABOT_HOST_ID": str(other.pk), "HTTP_X_IKABOT_HOST_TOKEN": other.new_enrollment_token}
        self.assertIsNone(self.client.get("/api/agent/supervisor/provision/next/", **headers).json()["provision"])
