"""N-106: a customer creates a node and installs its agent with one command — HTTPS only, no Redis."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Node
from apps.accounts.views.nodes import install_commands
from apps.orgs import scope
from apps.orgs.models import Membership, Organization


@override_settings(HUB_PUBLIC_URL="https://ikahub.example.com")
class CustomerInstallTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Cliente", slug="cliente")
        self.owner = get_user_model().objects.create_user("dono", password="x")
        Membership.objects.filter(user=self.owner).update(organization=self.org, role="owner")
        self.client.force_login(self.owner)

    def _create_node(self):
        response = self.client.post(reverse("accounts:node-create"), {"name": "meu-pc"})
        node = Node._base_manager.get(name="meu-pc", organization=self.org)
        return response, node

    def test_a_new_node_is_https_and_the_page_has_the_whole_command_once(self):
        response, node = self._create_node()
        self.assertRedirects(response, reverse("accounts:node-deploy", args=[node.pk]), fetch_redirect_response=False)
        self.assertEqual(node.transport, Node.TRANSPORT_HTTPS)
        page = self.client.get(reverse("accounts:node-deploy", args=[node.pk])).content.decode()
        self.assertIn("HUB_URL=https://ikahub.example.com", page)
        self.assertIn(f"AGENT_NODE_ID={node.pk}", page)
        self.assertIn("AGENT_TRANSPORT=https", page)
        self.assertIn("só aparece desta vez", page)
        self.assertNotIn("redis", page.lower())                      # nothing about the broker
        again = self.client.get(reverse("accounts:node-deploy", args=[node.pk])).content.decode()
        self.assertIn("&lt;CHAVE-DO-NO&gt;", again)
        self.assertNotIn("só aparece desta vez", again)

    def test_the_owner_renews_the_key_a_reader_cannot(self):
        _response, node = self._create_node()
        old_hash = Node._base_manager.get(pk=node.pk).agent_token_hash
        self.client.post(reverse("accounts:node-new-key", args=[node.pk]))
        self.assertNotEqual(Node._base_manager.get(pk=node.pk).agent_token_hash, old_hash)
        Membership.objects.filter(user=self.owner).update(role="viewer")
        self.assertEqual(self.client.post(reverse("accounts:node-new-key", args=[node.pk])).status_code, 403)   # N-126: a viewer only looks

    def test_another_organizations_node_and_broker_nodes_are_out_of_reach(self):
        other = Node.objects.create(name="da-plataforma", transport=Node.TRANSPORT_HTTPS)      # default organization
        self.assertEqual(self.client.get(reverse("accounts:node-deploy", args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("accounts:node-new-key", args=[other.pk])).status_code, 404)
        token = scope.activate(self.org)
        try:
            broker = Node.objects.create(name="antigo", transport=Node.TRANSPORT_REDIS)
        finally:
            scope.deactivate(token)
        self.assertEqual(self.client.get(reverse("accounts:node-deploy", args=[broker.pk])).status_code, 404)

    def test_the_menu_shows_the_organizations_admin_not_the_platforms(self):
        html = self.client.get("/").content.decode()
        for href in ('href="/accounts/nodes/"', 'href="/accounts/"', 'href="/organizacao/telegram/"', 'href="/atividades/"'):
            self.assertIn(href, html)
        for href in ('href="/proxy/"', 'href="/notes/"', 'href="/settings/"', 'href="/users/"', 'href="/captcha/"'):
            self.assertNotIn(href, html)

    def test_windows_gets_its_own_line_breaks(self):
        node = Node(name="pc de casa")
        linux, windows = install_commands(hub_url="https://h", node=node, token="tok_x", image="img:1")
        self.assertIn(" \\\n  -e AGENT_TOKEN=tok_x", linux)
        self.assertIn(' `\n  -e "AGENT_TOKEN=tok_x"', windows)
        self.assertIn("AGENT_NAME=pc-de-casa", linux)
