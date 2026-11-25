from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from apps.orgs.models import Membership, Organization
from apps.billing.models import BillingAccount
from .catalog import CATALOG, OWNER
from .models import AccessEvent, AccessMode, Grant, Role
from .policy import allowed, permissions
from .services import confirm, preview


class AccessControlTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="principal")
        self.other = Organization.objects.create(name="Outro cliente", slug="iam-other")
        BillingAccount.objects.update_or_create(organization=self.org, defaults={"status": "exempt"})
        self.root = self.person("iam-root", superuser=True)
        self.owner = self.person("iam-owner", role="owner")
        self.person_a = self.person("iam-person")
        self.person_b = self.person("iam-other-person", organization=self.other)

    def person(self, username, role="viewer", organization=None, superuser=False, staff=False):
        user = get_user_model()(username=username, is_superuser=superuser, is_staff=staff or superuser)
        user._own_organization = True
        user.save()
        Membership.objects.create(user=user, organization=organization or self.org, role=role)
        return user

    def role(self, name="Consulta de agents", perms=None, scope="organization", organization=None):
        return Role.objects.create(name=name, permissions=perms or ["organization.nodes.view"], scope=scope,
                                   organization=organization if scope == "organization" else None)

    def change(self, user, role, organization=None, action="grant", actor=None, **kw):
        result = preview(actor or self.root, {"action": action, "user_id": user.pk, "role_id": str(role.pk), **kw}, organization)
        confirm(actor or self.root, result["token"], organization)
        return result

    def test_migration_preserves_legacy_members_and_staff_without_grants(self):
        staff = self.person("iam-staff", staff=True)
        self.assertTrue(allowed(staff, "platform.settings.manage"))
        self.assertFalse(allowed(staff, "platform.access.manage"))
        self.assertEqual(permissions(self.owner, self.org), OWNER)
        self.assertEqual(Grant.objects.count(), 0)
        self.assertEqual(AccessMode.objects.count(), 0)
        self.assertEqual(Role.objects.filter(system_key__isnull=False).count(), 6)

    def test_preview_has_no_mutation_confirmation_is_single_use_and_audited(self):
        role = self.role(organization=self.org)
        p = preview(self.owner, {"action": "grant", "user_id": self.person_a.pk, "role_id": str(role.pk)}, self.org)
        self.assertEqual(Grant.objects.count(), 0)
        confirm(self.owner, p["token"], self.org)
        self.assertEqual(permissions(self.person_a, self.org), {"organization.nodes.view"})
        self.assertEqual(AccessEvent.objects.get().actor, self.owner)
        with self.assertRaises(ValidationError):
            confirm(self.owner, p["token"], self.org)

    def test_revocation_and_expiry_do_not_restore_legacy_rights(self):
        role = self.role(organization=self.org)
        self.change(self.person_a, role, self.org)
        self.assertTrue(allowed(self.person_a, "organization.nodes.view", self.org))
        self.change(self.person_a, role, self.org, action="revoke")
        self.assertEqual(permissions(self.person_a, self.org), set())
        self.change(self.person_a, role, self.org, expires_at=(timezone.now() + timedelta(hours=1)).isoformat())
        Grant.objects.filter(user=self.person_a).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(permissions(self.person_a, self.org), set())

    def test_route_and_menu_enforcement_for_custom_platform_support(self):
        support = self.role(name="Suporte da API", scope="platform", perms=["platform.api.view"])
        self.change(self.person_a, support)
        self.client.force_login(self.person_a)
        self.assertEqual(self.client.get("/api/docs/").status_code, 200)
        self.assertEqual(self.client.get("/settings/").status_code, 404)
        self.assertEqual(self.client.get("/users/").status_code, 404)
        self.assertEqual(self.client.post("/notes/create/", {}).status_code, 404)
        response = self.client.get("/")
        urls = [item["url"] for section in response.context["NAV_SECTIONS"] for item in section["items"]]
        self.assertIn("/api/docs/", urls)
        self.assertNotIn("/settings/", urls)

    def test_custom_organization_permissions_do_not_grant_other_operations(self):
        role = self.role(organization=self.org)
        self.change(self.person_a, role, self.org)
        self.client.force_login(self.person_a)
        self.assertEqual(self.client.get("/accounts/nodes/").status_code, 200)
        self.assertEqual(self.client.post("/accounts/nodes/create/", {}).status_code, 403)
        self.assertEqual(self.client.get("/accounts/").status_code, 403)
        self.assertEqual(self.client.get("/api/docs/").status_code, 404)

    def test_cross_scope_foreign_member_and_role_are_rejected(self):
        local = self.role(organization=self.org)
        foreign = self.role(organization=self.other)
        for user, role in ((self.person_a, foreign), (self.person_b, local)):
            with self.assertRaises(ValidationError):
                self.change(user, role, self.org, actor=self.owner)
        platform = self.role(scope="platform", perms=["platform.api.view"])
        with self.assertRaises(ValidationError):
            self.change(self.person_a, platform, self.org)

    def test_delegation_ceiling_applies_to_grants_role_edits_and_member_legacy_form(self):
        delegated = self.role(name="Delegado", organization=self.org, perms=["organization.access.view", "organization.access.manage", "organization.nodes.view", "organization.members.manage"])
        self.change(self.person_a, delegated, self.org)
        all_access = Role.objects.get(system_key="owner")
        with self.assertRaises(ValidationError):
            self.change(self.owner, all_access, self.org, actor=self.person_a)
        with self.assertRaises(ValidationError):
            preview(self.person_a, {"action": "role", "name": "Escalada", "permissions": ["organization.billing.manage"]}, self.org)
        self.client.force_login(self.person_a)
        self.assertEqual(self.client.post("/organizacao/membros/", {"action": "invite", "role": "owner"}).status_code, 403)

    def test_last_legacy_owner_and_custom_owner_cannot_be_removed_or_expire(self):
        role = self.role(organization=self.org)
        # Root's legacy membership is viewer, so owner is the sole owner.
        with self.assertRaises(ValidationError):
            self.change(self.owner, role, self.org)
        owner_role = Role.objects.get(system_key="owner")
        self.change(self.owner, owner_role, self.org)
        with self.assertRaises(ValidationError):
            self.change(self.owner, owner_role, self.org, action="revoke")
        with self.assertRaises(ValidationError):
            self.change(self.owner, owner_role, self.org, expires_at=(timezone.now()+timedelta(days=1)).isoformat())

    def test_root_and_system_roles_are_protected(self):
        role = self.role(organization=self.org)
        with self.assertRaises(ValidationError):
            self.change(self.root, role, self.org)
        with self.assertRaises(ValidationError):
            preview(self.owner, {"action": "role", "role_id": str(Role.objects.get(system_key="owner").pk), "name": "Changed", "permissions": ["organization.nodes.view"]}, self.org)

    def test_preview_actor_scope_payload_and_revision_are_verified(self):
        role = self.role(organization=self.org)
        p = preview(self.owner, {"action": "grant", "user_id": self.person_a.pk, "role_id": str(role.pk)}, self.org)
        for actor, organization in ((self.root, self.org), (self.owner, self.other)):
            with self.assertRaises(ValidationError):
                confirm(actor, p["token"], organization)
        with self.assertRaises(signing.BadSignature):
            confirm(self.owner, p["token"] + "invalid", self.org)
        role.permissions = ["organization.billing.view"]
        role.save()
        with self.assertRaises(ValidationError):
            confirm(self.owner, p["token"], self.org)

    def test_confirmation_reloads_actor_after_deactivation(self):
        role = self.role(organization=self.org)
        p = preview(self.owner, {"action": "grant", "user_id": self.person_a.pk, "role_id": str(role.pk)}, self.org)
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        with self.assertRaises(ValidationError):
            confirm(self.owner, p["token"], self.org)

    def test_role_creation_edit_and_history_use_confirmation(self):
        p = preview(self.owner, {"action": "role", "name": "Financeiro", "permissions": ["organization.billing.view", "organization.billing.manage"]}, self.org)
        self.assertFalse(Role.objects.filter(name="Financeiro").exists())
        confirm(self.owner, p["token"], self.org)
        role = Role.objects.get(name="Financeiro")
        self.assertEqual(role.organization, self.org)
        self.assertEqual(AccessEvent.objects.get().action, "role.saved")

    def test_search_pagination_context_and_history_are_scoped(self):
        for n in range(28):
            self.person(f"iam-paged-{n:02}")
        self.client.force_login(self.owner)
        response = self.client.get("/organizacao/acessos/", {"q": "iam-paged"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page_obj"]), 25)
        self.assertEqual(response.context["page_obj"].paginator.count, 28)
        self.assertNotContains(response, self.person_b.username)
        self.assertEqual(self.client.get("/organizacao/acessos/", {"person": self.person_b.pk}).status_code, 404)
        for tab in ("roles", "organizations", "history"):
            self.assertEqual(self.client.get("/organizacao/acessos/", {"tab": tab}).status_code, 200)

    def test_organization_selector_rejects_foreign_org_and_validates_session(self):
        self.client.force_login(self.person_a)
        self.assertEqual(self.client.post("/organizacao/selecionar/", {"organization": self.other.pk}).status_code, 404)
        Membership.objects.create(user=self.person_a, organization=self.other, role="viewer")
        self.assertEqual(self.client.post("/organizacao/selecionar/", {"organization": self.other.pk}).status_code, 302)
        self.assertEqual(self.client.get("/organizacao/").wsgi_request.organization, self.other)
        Membership.objects.filter(user=self.person_a, organization=self.other).delete()
        self.assertEqual(self.client.get("/organizacao/").wsgi_request.organization, self.org)

    def test_scalar_is_local_protected_and_restores_typed_contracts(self):
        self.client.force_login(self.root)
        page = self.client.get("/api/docs/")
        self.assertContains(page, "Scalar.createApiReference")
        self.assertNotContains(page, "cdn.jsdelivr.net")
        self.assertContains(page, "withDefaultFonts:false")
        schema = self.client.get("/api/schema/").json()
        self.assertGreaterEqual(len(schema["paths"]), 81)
        heartbeat = schema["paths"]["/api/agent/heartbeat/"]["post"]
        self.assertIn("requestBody", heartbeat)
        self.assertIn("schemas", schema["components"])
        self.assertEqual(schema["paths"]["/api/agent/supervisor/heartbeat/"]["post"]["security"], [{"HostToken": []}])

    def test_http_confirmation_changes_only_after_preview_and_rechecks_revocation(self):
        self.client.force_login(self.root)
        role = Role.objects.get(system_key="platform_legacy")
        response = self.client.post("/users/permissoes/", {"action": "grant", "user_id": self.person_a.pk, "role_id": role.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.person_a.username)
        self.assertFalse(allowed(self.person_a, "platform.api.view"))
        token = response.context["preview"]["token"]
        self.assertEqual(self.client.post("/users/permissoes/", {"confirmation": token}).status_code, 302)
        self.assertTrue(allowed(self.person_a, "platform.api.view"))
        self.assertFalse(get_user_model().objects.get(pk=self.person_a.pk).is_staff)

    def test_custom_finance_can_change_billing_but_not_integration_or_game(self):
        role = self.role(name="Financeiro", organization=self.org, perms=["organization.billing.view", "organization.billing.manage"])
        self.change(self.person_a, role, self.org)
        from apps.billing.views import _is_owner
        from types import SimpleNamespace
        self.assertTrue(_is_owner(SimpleNamespace(user=self.person_a, organization=self.org)))
        self.client.force_login(self.person_a)
        self.assertEqual(self.client.post("/organizacao/proxies/", {"action": "addon"}).status_code, 403)
        self.assertEqual(self.client.post("/jobs/new/submit/", {}).status_code, 403)

    def test_legacy_last_owner_cannot_be_disabled_or_deleted_through_user_crud(self):
        self.client.force_login(self.root)
        self.assertEqual(self.client.post(f"/users/{self.owner.pk}/delete/").status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())
        self.client.post(f"/users/{self.owner.pk}/edit/", {"username": self.owner.username})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_removed_membership_invalidates_org_grants(self):
        role = self.role(organization=self.org)
        self.change(self.person_a, role, self.org)
        Membership.objects.filter(user=self.person_a, organization=self.org).delete()
        self.assertEqual(permissions(self.person_a, self.org), set())

    def test_delegator_cannot_replace_higher_legacy_platform_access(self):
        staff = self.person("iam-legacy-admin", staff=True)
        delegate = self.role(scope="platform", perms=["platform.access.view", "platform.access.manage"])
        self.change(self.person_a, delegate)
        with self.assertRaises(ValidationError):
            self.change(staff, Role.objects.get(system_key="none_platform"), actor=self.person_a)

    def test_existing_legacy_global_access_is_disclosed_in_org_preview(self):
        staff = self.person("iam-global-legacy", staff=True)
        role = self.role(organization=self.org)
        result = self.change(staff, role, self.org)
        self.assertEqual(result["removed"], [])
        from .catalog import OPERATOR
        self.assertTrue(OPERATOR <= permissions(staff, self.org))


from django.test import TransactionTestCase, skipUnlessDBFeature


class ConcurrentOwnerTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_confirmations_cannot_remove_both_owners(self):
        from concurrent.futures import ThreadPoolExecutor
        from django.db import close_old_connections
        from apps.settings_app.models import AppSetting
        organization = Organization.objects.create(name="Concurrent", slug="concurrent-iam")
        users = []
        for name in ("concurrent-root", "concurrent-a", "concurrent-b"):
            user = get_user_model()(username=name, is_superuser=name.endswith("root"), is_staff=name.endswith("root"))
            user._own_organization = True
            user.save()
            users.append(user)
        root, first, second = users
        for user in (first, second):
            Membership.objects.create(user=user, organization=organization, role="owner")
        role = Role.objects.create(name="Read", scope="organization", organization=organization, permissions=["organization.nodes.view"])
        AppSetting.objects.get_or_create(pk="access_management_lock")
        previews = [preview(root, {"action": "grant", "user_id": user.pk, "role_id": str(role.pk)}, organization)["token"] for user in (first, second)]
        def worker(token):
            close_old_connections()
            try:
                confirm(get_user_model().objects.get(pk=root.pk), token, Organization.objects.get(pk=organization.pk))
                return True
            except ValidationError:
                return False
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, previews))
        self.assertEqual(sum(results), 1)
        managed = AccessMode.objects.filter(scope_key=str(organization.pk)).values_list("user_id", flat=True)
        self.assertEqual(Membership.objects.filter(organization=organization, role="owner").exclude(user_id__in=managed).count(), 1)
