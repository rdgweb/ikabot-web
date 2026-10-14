"""N-106 stage 1: everything belongs to an organization, and nothing changes for the operator."""
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.accounts.models import Account, DockerHost, Node
from apps.orgs.models import Membership, Organization
from apps.orgs.services import DEFAULT_SLUG, default_organization
from apps.profiles.models import Preset, Profile
from apps.proxy.models import ProxyProfile


class DefaultOrganizationTests(TestCase):
    def test_new_rows_land_in_the_default_organization(self):
        organization = default_organization()
        node = Node.objects.create(name="agente-1")
        rows = [
            node,
            DockerHost.objects.create(name="servidor"),
            Account.objects.create(node=node, label="Lobby", email="a@example.com", password_enc="x"),
            ProxyProfile.objects.create(address="10.0.0.1", port=8080),
            Profile.objects.create(name="perfil", action_code=100),
            Preset.objects.create(name="preset"),
        ]
        for row in rows:
            row.refresh_from_db()
            self.assertEqual(row.organization_id, organization.pk, type(row).__name__)
        self.assertEqual(Organization.objects.filter(slug=DEFAULT_SLUG).count(), 1)
        self.assertEqual(organization.nodes.count(), 1)
        self.assertEqual(organization.accounts.count(), 1)

    def test_users_join_with_the_access_they_have(self):
        User = get_user_model()
        boss = User.objects.create_superuser("rodrigo", "r@example.com", "x")
        operator = User.objects.create_user("amigo", password="x")
        viewer = User.objects.create_user("olheiro", password="x")
        viewer.groups.add(Group.objects.get_or_create(name="viewer")[0])     # the user form adds groups after saving
        roles = dict(Membership.objects.filter(organization=default_organization()).values_list("user__username", "role"))
        self.assertEqual(roles, {"rodrigo": "owner", "amigo": "operator", "olheiro": "viewer"})
        viewer.groups.clear()
        self.assertEqual(Membership.objects.get(user=viewer).role, "operator")
        self.assertEqual(boss.memberships.count(), 1)
        self.assertEqual(operator.memberships.get().organization.slug, DEFAULT_SLUG)

    def test_the_default_comes_back_if_it_was_removed(self):
        Organization.objects.all().delete()
        node = Node.objects.create(name="agente-1")
        self.assertEqual(node.organization.slug, DEFAULT_SLUG)

    def test_data_migration_gives_every_existing_user_a_role(self):
        User = get_user_model()
        User.objects.create_user("antigo", password="x", is_staff=True)
        Membership.objects.all().delete()
        Organization.objects.all().delete()
        module = import_module("apps.orgs.migrations.0002_default_organization")
        module.create_default(django_apps, None)
        organization = Organization.objects.get(slug=DEFAULT_SLUG)
        self.assertEqual(Membership.objects.get(organization=organization, user__username="antigo").role, "owner")

    def test_nothing_changes_on_the_screens(self):
        self.client.force_login(get_user_model().objects.create_user("amigo", password="x"))
        node = Node.objects.create(name="agente-1")
        Account.objects.create(node=node, label="Lobby", email="a@example.com", password_enc="x")
        html = self.client.get("/accounts/").content.decode()
        self.assertIn("Lobby", html)
        self.assertNotIn("Organização", html)
