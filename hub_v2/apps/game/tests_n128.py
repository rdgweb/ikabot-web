"""N-128: Ações do Jogo and the job modal's "choose action" step can be searched by name, description, field and
code, without accents getting in the way."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from core.contracts import get_actions_for_ui


class ActionSearchTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(user)

    def _actions(self):
        return {action["code"]: action for group in get_actions_for_ui() for action in group["actions"]}

    def test_the_search_text_has_both_names_the_fields_and_no_accents(self):
        buy = self._actions()[8]
        for piece in ("comprar do mercado", "buy from market", "preco maximo por unidade", "cidade compradora", " 8 ", "mercado"):
            self.assertIn(piece.strip(), buy["search"])
        self.assertNotIn("ç", buy["search"])
        self.assertIn("Preço máximo por unidade", buy["fields"].split("|"))
        self.assertIn("loop", self._actions()[1002]["search"])            # the Loop tag can be searched too

    def test_the_catalog_page_has_the_search_and_the_real_count(self):
        html = self.client.get("/game/actions/").content.decode()
        total = sum(len(group["actions"]) for group in get_actions_for_ui())
        self.assertIn("data-action-search-input", html)
        self.assertIn(f"{total} ações disponíveis", html)
        self.assertEqual(html.count("data-action-item "), total)

    def test_the_job_modal_lists_the_actions_with_the_search(self):
        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="Atenas")
        html = self.client.get("/jobs/new/actions/", {"ga": str(ga.pk)}).content.decode()
        self.assertIn("data-action-search-input", html)
        self.assertIn('data-search="comprar do mercado', html)
