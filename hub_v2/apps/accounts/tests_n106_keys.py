"""N-106 stage 3b-1: each agent has its node's own key, kept as a hash, bound to its node and organization."""
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Account, Node
from apps.jobs.models import Job
from apps.orgs import scope
from apps.orgs.models import Organization
from apps.orgs.services import default_organization
from core.auth.tokens import hash_agent_token
from core.encryption import encrypt


@override_settings(AGENT_TOKEN="chave-unica-antiga", AGENT_SHARED_TOKEN_ENABLED=True, AGENT_ALLOWED_IPS="")
class AgentKeyTests(TestCase):
    def setUp(self):
        self.org_a = default_organization()
        self.org_b = Organization.objects.create(name="Outro cliente", slug="outro")
        self.node_a = Node.objects.create(name="agente-a")
        self.key_a = self.node_a.new_agent_token
        token = scope.activate(self.org_b)
        try:
            self.node_b = Node.objects.create(name="agente-b")
            self.key_b = self.node_b.new_agent_token
            self.account_b = Account.objects.create(node=self.node_b, label="Conta B", email="b@example.com", password_enc=encrypt("senha-b"))
        finally:
            scope.deactivate(token)
        self.account_a = Account.objects.create(node=self.node_a, label="Conta A", email="a@example.com", password_enc=encrypt("senha-a"))
        self.job_a = Job.objects.create(account=self.account_a, node=self.node_a, action_code=100)

    def _config(self, key, node_id, header_node=None):
        return self.client.get(reverse("agent-accounts:config"), {"node_id": node_id},
                               HTTP_X_AGENT_TOKEN=key, HTTP_X_AGENT_NODE_ID=header_node or node_id)

    def test_only_the_hash_is_kept(self):
        node = Node._base_manager.get(pk=self.node_a.pk)
        self.assertTrue(self.key_a.startswith("tok_"))
        self.assertEqual(node.agent_token_hash, hash_agent_token(self.key_a))
        self.assertEqual(node.deploy_token, "")
        self.assertNotIn(self.key_a, str(Node._base_manager.filter(pk=node.pk).values().first()))

    def test_a_node_key_opens_its_own_node(self):
        response = self._config(self.key_b, str(self.node_b.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([acc["label"] for acc in response.json()["accounts"]], ["Conta B"])

    def test_a_node_key_cannot_ask_for_another_node(self):
        # its own node in the header, the other node in the query: refused, no passwords
        response = self._config(self.key_b, str(self.node_a.pk), header_node=str(self.node_b.pk))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("senha-a", response.content.decode())
        # the other node in the header with its own key: the key does not match
        self.assertIn(self._config(self.key_b, str(self.node_a.pk)).status_code, (401, 403))

    def test_a_node_key_only_reaches_its_organization(self):
        response = self.client.get(reverse("agent-jobs:info", args=[self.job_a.pk]),
                                   HTTP_X_AGENT_TOKEN=self.key_b, HTTP_X_AGENT_NODE_ID=str(self.node_b.pk))
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(scope.current())                     # the node's scope did not outlive the request

    def test_the_old_single_key_still_works_while_agents_move(self):
        response = self._config("chave-unica-antiga", str(self.node_a.pk))
        self.assertEqual(response.status_code, 200)
        with override_settings(AGENT_SHARED_TOKEN_ENABLED=False):
            self.assertIn(self._config("chave-unica-antiga", str(self.node_a.pk)).status_code, (401, 403))

    def test_no_built_in_single_key(self):
        with override_settings(AGENT_TOKEN=""):
            self.assertIn(self._config("", str(self.node_a.pk)).status_code, (401, 403))
            self.assertIn(self._config("troque-agent-token", str(self.node_a.pk)).status_code, (401, 403))

    def test_existing_keys_keep_working_after_the_migration(self):
        Node._base_manager.filter(pk=self.node_a.pk).update(deploy_token="tok_antigo", agent_token_hash="")
        module = import_module("apps.accounts.migrations.0016_n106_agent_token_hash")
        module.hash_existing(django_apps, None)
        node = Node._base_manager.get(pk=self.node_a.pk)
        self.assertEqual((node.deploy_token, node.agent_token_hash), ("", hash_agent_token("tok_antigo")))
        self.assertEqual(self._config("tok_antigo", str(self.node_a.pk)).status_code, 200)

    def test_the_key_is_shown_once_and_only_to_staff(self):
        staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(staff)
        self.client.post(reverse("accounts:node-new-key", args=[self.node_a.pk]))
        first = self.client.get(reverse("accounts:node-deploy", args=[self.node_a.pk])).content.decode()
        self.assertIn("só aparece desta vez", first)
        second = self.client.get(reverse("accounts:node-deploy", args=[self.node_a.pk])).content.decode()
        self.assertNotIn("só aparece desta vez", second)
        self.assertIn(self._config(self.key_a, str(self.node_a.pk)).status_code, (401, 403))       # the old key stopped
        customer = get_user_model().objects.create_user("cliente", password="x")
        self.client.force_login(customer)
        self.assertEqual(self.client.get(reverse("accounts:node-deploy", args=[self.node_a.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("accounts:node-new-key", args=[self.node_a.pk])).status_code, 404)
