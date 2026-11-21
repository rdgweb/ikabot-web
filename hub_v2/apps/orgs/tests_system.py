"""N-203: the system's administrator over every organization, and a tester using the platform's proxies and agent
without charge (trial, exemption or courtesy)."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, AgentProvisionRequest, DockerHost, Node
from apps.audit.models import AuditEvent
from apps.billing.models import BillingAccount
from apps.billing.services import account_for, free_addons, set_agent_addon, set_proxy_addon, state_for
from apps.notes.services import RegistryRelease
from apps.proxy.models import ProxyProfile

from .models import Membership, Organization
from .services import default_organization

RELEASE = RegistryRelease(component="agent", repository="blackoneal/ikabot-web-agent", version="0.1.178",
                          digest="sha256:" + "c" * 64)


def _user(username, **extra):
    user = get_user_model()(username=username, **extra)
    user._own_organization = True
    user.set_password("x")
    user.save()
    return user


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.host = DockerHost._base_manager.create(name="docker-plataforma", organization=default_organization(),
                                                    last_seen_at=timezone.now())
        ctx = override_settings(PLATFORM_AGENT_HOST_ID=str(self.host.pk), PLATFORM_AGENT_LIMIT=5)
        ctx.enable()
        self.addCleanup(ctx.disable)
        release = patch("apps.notes.services.get_registry_release", return_value=RELEASE)
        release.start()
        self.addCleanup(release.stop)
        for i in range(30):
            ProxyProfile._base_manager.create(organization=default_organization(), provider="webshare", webshare_id=f"w{i}",
                                              address=f"10.0.2.{i}", port=9100 + i, active=True, last_test_status=True,
                                              last_test_latency_ms=50)
        self.tester = _user("testador")
        self.org = Organization.objects.create(name="Teste", slug="teste")
        Membership.objects.create(organization=self.org, user=self.tester, role="owner")
        self.billing = account_for(self.org)
        self.node = Node.objects.create(name="pc", organization=self.org, transport="https")
        self.account = Account._base_manager.create(node=self.node, label="Conta T", email="t@example.com", password_enc="x",
                                                    organization=self.org)


class FreeAddonsTests(Base):
    def test_a_trial_switches_proxies_and_agent_on_without_charge(self):
        self.assertEqual(state_for(self.org).code, "trial")
        self.assertEqual(free_addons(self.billing)[::2], (True, "teste"))
        with patch("apps.billing.asaas.update_subscription_value") as update:
            self.assertTrue(set_proxy_addon(self.billing, self.account, True)["ok"])
            outcome = set_agent_addon(self.billing, self.account, True)
        update.assert_not_called()
        self.assertEqual((outcome["ok"], outcome.get("free")), (True, True))
        self.account.refresh_from_db()
        self.assertTrue(self.account.platform_proxies and self.account.platform_agent and self.account.node.platform_managed)
        self.assertTrue(AgentProvisionRequest._base_manager.filter(action="create").exists())

    def test_without_trial_nor_courtesy_they_are_refused(self):
        BillingAccount.objects.filter(pk=self.billing.pk).update(trial_ends_at=timezone.now() - timedelta(days=1))
        self.billing.refresh_from_db()
        self.assertFalse(free_addons(self.billing)[0])
        self.assertFalse(set_proxy_addon(self.billing, self.account, True)["ok"])
        self.assertFalse(set_agent_addon(self.billing, self.account, True)["ok"])

    def test_a_courtesy_works_after_the_trial_and_ends_them_on_its_date(self):
        BillingAccount.objects.filter(pk=self.billing.pk).update(trial_ends_at=timezone.now() - timedelta(days=1),
                                                                 addons_free_until=timezone.now() + timedelta(days=10))
        self.billing.refresh_from_db()
        free, until, why = free_addons(self.billing)
        self.assertEqual((free, why), (True, "cortesia"))
        self.assertTrue(set_proxy_addon(self.billing, self.account, True)["ok"])
        self.account.refresh_from_db()
        self.assertEqual(self.account.platform_proxies_ends_at, until)


class AdministratorTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = _user("rodrigo", is_staff=True, is_superuser=True)
        self.client.force_login(self.admin)
        self.url = f"/sistema/organizacoes/{self.org.pk}/"

    def test_only_the_system_administrator_sees_it(self):
        self.assertEqual(self.client.get("/sistema/organizacoes/").status_code, 200)
        self.assertContains(self.client.get(self.url), "Conta T")
        self.client.force_login(self.tester)
        self.assertEqual(self.client.get("/sistema/organizacoes/").status_code, 404)
        self.assertEqual(self.client.post(self.url, {"action": "exempt"}).status_code, 404)
        self.billing.refresh_from_db()
        self.assertNotEqual(self.billing.status, BillingAccount.EXEMPT)

    def test_extend_exempt_and_courtesy(self):
        before = self.billing.trial_ends_at
        self.client.post(self.url, {"action": "extend", "days": "14"})
        self.billing.refresh_from_db()
        self.assertAlmostEqual((self.billing.trial_ends_at - before).total_seconds(), 14 * 86400, delta=5)
        self.client.post(self.url, {"action": "exempt"})
        self.assertEqual(state_for(self.org).code, "exempt")
        self.client.post(self.url, {"action": "unexempt"})
        self.assertEqual(state_for(self.org).code, "trial")
        day = (timezone.localdate() + timedelta(days=20)).isoformat()
        self.client.post(self.url, {"action": "courtesy", "until": day})
        self.billing.refresh_from_db()
        self.assertEqual(timezone.localtime(self.billing.addons_free_until).date().isoformat(), day)
        self.assertTrue(AuditEvent.objects.filter(action="organization.courtesy").exists())

    def test_switching_an_accounts_agent_on_and_pausing_takes_it_back(self):
        self.client.post(self.url, {"action": "proxies_on", "account": str(self.account.pk)})
        self.client.post(self.url, {"action": "agent_on", "account": str(self.account.pk)})
        self.account.refresh_from_db()
        self.assertTrue(self.account.platform_agent)
        self.client.post(self.url, {"action": "pause"})
        self.org.refresh_from_db()
        self.assertEqual(state_for(self.org).code, "paused")
        self.account.refresh_from_db()
        self.assertFalse(self.account.platform_agent or self.account.platform_proxies)
        self.assertEqual(self.account.node_id, self.node.pk)                     # back on its own node
        self.client.post(self.url, {"action": "resume"})
        self.org.refresh_from_db()
        self.assertEqual(state_for(self.org).code, "trial")

    def test_delete_needs_the_exact_name_and_keeps_the_platform(self):
        self.client.post(self.url, {"action": "proxies_on", "account": str(self.account.pk)})
        self.client.post(self.url, {"action": "delete", "confirm_name": "teste"})
        self.assertTrue(Organization._base_manager.filter(pk=self.org.pk).exists())
        self.client.post(self.url, {"action": "delete", "confirm_name": "Teste"})
        self.assertFalse(Organization._base_manager.filter(pk=self.org.pk).exists())
        self.assertFalse(Account._base_manager.filter(pk=self.account.pk).exists())
        self.assertEqual(ProxyProfile._base_manager.filter(lent_to__isnull=False).count(), 0)   # lent proxies came back
        self.assertEqual(ProxyProfile._base_manager.count(), 30)
        self.tester.refresh_from_db()
        self.assertFalse(self.tester.is_active)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertTrue(AuditEvent.objects.filter(action="organization.deleted", organization=default_organization()).exists())
        platform = default_organization()
        page = self.client.get(f"/sistema/organizacoes/{platform.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "proxies_on")                               # the platform has no add-ons
        self.client.post(f"/sistema/organizacoes/{platform.pk}/", {"action": "delete", "confirm_name": platform.name})
        self.assertTrue(Organization._base_manager.filter(pk=platform.pk).exists())


class PlatformAccountCreateTests(Base):
    def setUp(self):
        super().setUp()
        Account._base_manager.filter(pk=self.account.pk).delete()
        self.node.delete()
        self.client.force_login(self.tester)

    def test_a_tester_creates_the_account_with_the_platforms_agent(self):
        page = self.client.get("/accounts/create/")
        self.assertContains(page, "Usar o agente da plataforma")
        response = self.client.post("/accounts/create/", {"label": "Nova", "email": "n@example.com", "password": "segredo",
                                                          "platform_agent": "on"})
        self.assertEqual(response.status_code, 302)
        account = Account._base_manager.get(label="Nova")
        self.assertTrue(account.platform_proxies and account.platform_agent)
        self.assertTrue(account.node.platform_managed)
        self.assertEqual(Node._base_manager.filter(organization=self.org).count(), 1)   # the waiting node went away
        # the trial ends: the agent goes and the account stays on the switched-off platform node (no node of its own)
        BillingAccount.objects.filter(pk=self.billing.pk).update(trial_ends_at=timezone.now() - timedelta(days=1))
        from apps.accounts import platform_agent

        self.assertEqual(platform_agent.end_due(self.org), 1)
        account.refresh_from_db()
        self.assertFalse(account.platform_agent or account.node.active)

    def test_without_node_nor_the_platform_it_is_refused(self):
        response = self.client.post("/accounts/create/", {"label": "Nova", "email": "n@example.com", "password": "segredo"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Account._base_manager.filter(label="Nova").exists())

    def test_the_tour_points_a_tester_to_the_platform(self):
        data = self.client.get("/settings/tour/state/").json()
        self.assertTrue(data["platform"])
        self.assertEqual(data["platform_agents"], 0)
