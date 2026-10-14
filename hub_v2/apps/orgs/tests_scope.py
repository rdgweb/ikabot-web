"""N-106 stage 3a: one organization never sees another's data."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job
from apps.orgs import scope
from apps.orgs.models import Membership, Organization
from apps.orgs.scope import GLOBAL_MODELS, SCOPES, OrgScopedManager
from apps.orgs.services import default_organization
from apps.profiles.models import Preset
from apps.proxy.models import ProxyProfile

PLATFORM_APPS = {"admin", "auth", "contenttypes", "sessions", "notes", "orgs", "audit", "telemetry", "settings_app"}
FULL_PATH = {"org": "", "account": "__organization", "game_account": "__account__organization", "node": "__organization", "preset": "__organization"}


def org_path(label):
    lookup, anchor, _shared = SCOPES[label]
    return lookup + FULL_PATH[anchor]


class OrganizationScopeTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.org_a = default_organization()
        self.org_b = Organization.objects.create(name="Outro cliente", slug="outro")
        self.user_a = User.objects.create_user("rodrigo", password="x", is_staff=True)        # joins A (default) as owner
        self.user_b = User.objects.create_user("cliente", password="x")
        Membership.objects.filter(user=self.user_b).update(organization=self.org_b, role="owner")
        self.data = {org.slug: self._fill(org, tag) for org, tag in ((self.org_a, "A"), (self.org_b, "B"))}

    def _fill(self, organization, tag):
        token = scope.activate(organization)
        try:
            node = Node.objects.create(name="agente")                          # same name in both organizations
            account = Account.objects.create(node=node, label=f"Conta {tag}", email=f"{tag}@example.com", password_enc="x")
            world = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                               server_number=78, name=f"Jogador {tag}")
            job = Job.objects.create(account=account, game_account=world, node=node, action_code=100)
            preset = Preset.objects.create(name="Rotina")
            proxy = ProxyProfile.objects.create(address=f"10.0.0.{ord(tag)}", port=80)
        finally:
            scope.deactivate(token)
        return {"node": node, "account": account, "world": world, "job": job, "preset": preset, "proxy": proxy}

    def test_every_model_with_customer_data_is_scoped(self):
        missing = []
        for model in apps.get_models():
            label = model._meta.label
            if model._meta.app_label in PLATFORM_APPS or label in GLOBAL_MODELS:
                continue
            if label not in SCOPES or not isinstance(model._default_manager, OrgScopedManager):
                missing.append(label)
        self.assertEqual(missing, [], "modelo com dado de cliente sem filtro por organização")

    def test_each_organization_sees_only_its_rows(self):
        for organization in (self.org_a, self.org_b):
            token = scope.activate(organization)
            try:
                for label in SCOPES:
                    model = apps.get_model(label)
                    visible = set(model.objects.values_list("pk", flat=True))
                    shared = SCOPES[label][2]
                    expected = set(model._base_manager.filter(**{org_path(label): organization}).values_list("pk", flat=True))
                    if shared:
                        expected |= set(model._base_manager.filter(**{f"{SCOPES[label][0]}__isnull": True}).values_list("pk", flat=True))
                    self.assertEqual(visible, expected, f"{label} em {organization.slug}")
            finally:
                scope.deactivate(token)
        self.assertEqual(Node._base_manager.filter(name="agente").count(), 2)

    def test_the_screens_show_only_the_own_organization(self):
        self.client.force_login(self.user_b)
        html = self.client.get(reverse("accounts:account-list")).content.decode()
        self.assertIn("Conta B", html)
        self.assertNotIn("Conta A", html)
        other = self.data["principal"]
        self.assertEqual(self.client.get(reverse("accounts:account-detail", args=[other["account"].pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("jobs:job-detail", args=[other["job"].pk])).status_code, 404)
        nodes = self.client.get(reverse("accounts:node-list")).content.decode()
        self.assertNotIn(str(other["node"].pk), nodes)

    def test_what_a_person_creates_lands_in_their_organization(self):
        self.client.force_login(self.user_b)
        self.client.post(reverse("accounts:account-create"), {
            "node": self.data["outro"]["node"].pk, "label": "Nova", "email": "n@example.com", "password": "x",
        })
        self.assertEqual(Account._base_manager.get(label="Nova").organization, self.org_b)

    def test_the_form_does_not_offer_another_organizations_node(self):
        self.client.force_login(self.user_b)
        response = self.client.post(reverse("accounts:account-create"), {
            "node": self.data["principal"]["node"].pk, "label": "Intrusa", "email": "i@example.com", "password": "x",
        })
        self.assertFalse(Account._base_manager.filter(label="Intrusa").exists())
        self.assertEqual(response.status_code, 200)                                          # form error, not saved

    def test_a_person_without_organization_sees_nothing(self):
        lost = get_user_model().objects.create_user("perdido", password="x")
        Membership.objects.filter(user=lost).delete()
        self.client.force_login(lost)
        html = self.client.get(reverse("accounts:account-list")).content.decode()
        self.assertNotIn("Conta A", html)
        self.assertNotIn("Conta B", html)

    @override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
    def test_the_agent_api_is_not_narrowed_by_a_person(self):
        node = self.data["outro"]["node"]
        response = self.client.get(reverse("agent-accounts:config"), {"node_id": str(node.pk)}, HTTP_X_AGENT_TOKEN="test-agent-token")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([acc["label"] for acc in response.json()["accounts"]], ["Conta B"])


class PlatformPagesTests(TestCase):
    """Customers never reach the pages of the platform itself."""

    def test_a_customer_does_not_reach_platform_pages(self):
        User = get_user_model()
        customer = User.objects.create_user("cliente", password="x")
        self.client.force_login(customer)
        for url in ("/notes/", "/settings/", "/users/", "/captcha/", "/telegram/", "/proxy/", "/accounts/docker-hosts/", "/django-admin/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.get("/settings/tour/state/").status_code, 200)
        self.assertEqual(self.client.post("/settings/tour/toggle/", {"enabled": "0"}).status_code, 404)

    def test_staff_still_reaches_them(self):
        staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(staff)
        for url in ("/notes/", "/settings/", "/users/", "/proxy/"):
            self.assertIn(self.client.get(url).status_code, (200, 302), url)
