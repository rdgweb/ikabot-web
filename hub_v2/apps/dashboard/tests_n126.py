"""N-126: a customer's panel never shows another organization's data or the platform's pages."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Account, GameAccount, Node
from apps.orgs.models import Membership, Organization


def customer(username, org_name):
    user = get_user_model()(username=username)
    user._own_organization = True
    user.set_password("x")
    user.save()
    organization = Organization.objects.create(name=org_name, slug=org_name.lower())
    Membership.objects.create(organization=organization, user=user, role=Membership.OWNER)
    return user, organization


class CustomerPanelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        node = Node.objects.create(name="no-plataforma")
        account = Account.objects.create(node=node, label="Conta da plataforma", email="p@example.com", password_enc="x")
        GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s1", name="JogadorDaPlataforma",
                                   server_language="br", server_number=1)
        self.user, self.org = customer("cliente", "Blood")
        cnode = Node.objects.create(name="no-cliente", organization=self.org)
        caccount = Account.objects.create(node=cnode, label="Conta do cliente", email="c@example.com", password_enc="x",
                                          organization=self.org)
        GameAccount.objects.create(account=caccount, lobby_account_id=2, server_id="s2", name="JogadorDoCliente",
                                   server_language="br", server_number=2)

    def test_each_organization_gets_its_own_dashboard_even_from_the_cache(self):
        report = {"tested": 160, "passed": 150, "without_proxy": ["no no-plataforma", "conta Conta da plataforma"]}
        with patch("apps.proxy.maintenance.last_result", return_value=report), \
             patch("apps.proxy.maintenance.last_run", return_value=__import__("django.utils.timezone").utils.timezone.now()):
            self.client.force_login(self.staff)
            staff_page = self.client.get("/").content.decode()               # fills the cache first
            self.assertIn("JogadorDaPlataforma", staff_page)
            self.assertIn("Proxies", staff_page)
            self.client.force_login(self.user)
            page = self.client.get("/").content.decode()
        self.assertIn("JogadorDoCliente", page)
        self.assertNotIn("JogadorDaPlataforma", page)
        self.assertNotIn("no-plataforma", page)
        self.assertNotIn("Conta da plataforma", page)
        self.assertNotIn("/proxy/", page)

    def test_no_links_to_the_platform_pages(self):
        self.client.force_login(self.user)
        self.assertNotIn("docker-hosts", self.client.get("/accounts/nodes/").content.decode())
        form = self.client.get("/accounts/nodes/create/").content.decode()
        self.assertNotIn('name="docker_host"', form)
        self.client.force_login(self.staff)
        self.assertIn('name="docker_host"', self.client.get("/accounts/nodes/create/").content.decode())


@override_settings(ASAAS_API_KEY="chave-de-teste", SITE_URL="https://hub.example")
class SubscribeReturnAddressTests(TestCase):
    def test_the_return_address_is_the_published_https_one(self):
        user, _org = customer("dono", "Norte")
        self.client.force_login(user)
        with patch("apps.billing.asaas.create_payment_link", return_value=("lnk", "https://pay.example/lnk")) as create:
            self.client.post("/organizacao/plano/", {"limit": "4"})
        self.assertEqual(create.call_args.kwargs["success_url"], "https://hub.example/organizacao/plano/?ok=1")
