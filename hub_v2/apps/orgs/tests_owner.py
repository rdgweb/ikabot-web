"""N-126: Minha organização (overview, sub-accounts, members) and Minha conta; the viewer role only looks."""
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.accounts.models import Account, GameAccount, Node
from apps.settings_app.models import AppSetting

from .models import Membership, Organization

MAIL = {"EMAIL_READY": True, "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend", "SITE_URL": "https://site.example"}


def person(username, organization, role=Membership.OWNER, email=""):
    user = get_user_model()(username=username, email=email or f"{username}@example.com")
    user._own_organization = True
    user.set_password("Senha-Antiga-2026!")
    user.save()
    Membership.objects.create(organization=organization, user=user, role=role)
    return user


class OwnerAreaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Blood", slug="blood")
        self.owner = person("dono", self.org)
        node = Node.objects.create(name="agente", organization=self.org)
        self.account = Account.objects.create(node=node, label="Conta 1", email="c@example.com", password_enc="x", organization=self.org)
        self.worlds = [GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id=f"s{i}", name=f"J{i}",
                                                  server_language="br", server_number=i, active=i < 4) for i in range(6)]
        self.client.force_login(self.owner)

    def test_overview_shows_the_organization_and_the_owner_renames_it(self):
        page = self.client.get("/organizacao/").content.decode()
        for text in ("Blood", "Teste grátis", "Subcontas ativas", "Membros", "Plano"):
            self.assertIn(text, page)
        self.client.post("/organizacao/", {"name": "Blood Norte"})
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Blood Norte")
        operator = person("operador", self.org, Membership.OPERATOR)
        self.client.force_login(operator)
        self.assertEqual(self.client.post("/organizacao/", {"name": "Outro"}).status_code, 404)

    def test_sub_accounts_follow_the_plan(self):
        page = self.client.get("/organizacao/subcontas/").content.decode()
        self.assertIn("4", page)
        self.client.post("/organizacao/subcontas/", {"world": str(self.worlds[5].pk)})          # 5th over a 4 trial
        self.assertFalse(GameAccount._base_manager.get(pk=self.worlds[5].pk).active)
        self.client.post("/organizacao/subcontas/", {"world": str(self.worlds[0].pk)})          # pause one
        self.client.post("/organizacao/subcontas/", {"world": str(self.worlds[5].pk)})          # now it fits
        self.assertTrue(GameAccount._base_manager.get(pk=self.worlds[5].pk).active)
        self.assertFalse(GameAccount._base_manager.get(pk=self.worlds[0].pk).active)

    @override_settings(**MAIL)
    def test_invite_a_member_who_joins_this_organization(self):
        self.client.post("/organizacao/membros/", {"action": "invite", "email": "amigo@example.com", "role": "operator"})
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"https://site\.example(/convite/\S+/)", mail.outbox[0].body).group(1)
        self.client.logout()
        self.client.post(link, {"username": "amigo", "password1": "Ikariam-Amigo-2026!", "password2": "Ikariam-Amigo-2026!", "accept_terms": "1"})
        amigo = get_user_model().objects.get(username="amigo")
        self.assertEqual(list(Membership.objects.filter(user=amigo).values_list("organization__slug", "role")), [("blood", "operator")])
        self.assertIn("Conta 1", self.client.get("/accounts/").content.decode())               # sees the organization's data

    @override_settings(**MAIL)
    def test_an_email_that_already_has_an_account_is_refused(self):
        self.client.post("/organizacao/membros/", {"action": "invite", "email": "dono@example.com", "role": "viewer"})
        self.assertEqual(len(mail.outbox), 0)

    def test_roles_and_removal_keep_an_owner(self):
        mine = Membership.objects.get(user=self.owner)
        other = person("outro", self.org, Membership.VIEWER)
        theirs = Membership.objects.get(user=other)
        self.client.post("/organizacao/membros/", {"action": "role", "member": str(theirs.pk), "role": "operator"})
        theirs.refresh_from_db()
        self.assertEqual(theirs.role, "operator")
        self.client.post("/organizacao/membros/", {"action": "remove", "member": str(mine.pk)})   # never oneself
        self.assertTrue(Membership.objects.filter(pk=mine.pk).exists())
        self.client.post("/organizacao/membros/", {"action": "remove", "member": str(theirs.pk)})
        self.assertFalse(Membership.objects.filter(pk=theirs.pk).exists())
        other.refresh_from_db()
        self.assertFalse(other.is_active)

    def test_a_viewer_only_looks(self):
        viewer = person("leitor", self.org, Membership.VIEWER)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get("/organizacao/subcontas/").status_code, 200)
        response = self.client.post("/organizacao/subcontas/", {"world": str(self.worlds[0].pk)})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(f"/accounts/game-account/{self.worlds[0].pk}/toggle/").status_code, 403)
        self.assertTrue(GameAccount._base_manager.get(pk=self.worlds[0].pk).active)
        self.client.post("/conta/", {"action": "profile", "first_name": "Leitor", "email": "leitor@example.com"})   # their own account is theirs
        viewer.refresh_from_db()
        self.assertEqual(viewer.first_name, "Leitor")

    def test_my_account_name_password_and_tour(self):
        self.client.post("/conta/", {"action": "profile", "first_name": "Rodrigo", "email": "novo@example.com"})
        self.owner.refresh_from_db()
        self.assertEqual((self.owner.first_name, self.owner.email), ("Rodrigo", "novo@example.com"))
        self.client.post("/conta/", {"action": "password", "old_password": "Senha-Antiga-2026!",
                                     "new_password1": "Senha-Nova-2026!", "new_password2": "Senha-Nova-2026!"})
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("Senha-Nova-2026!"))
        self.assertEqual(self.client.get("/conta/").status_code, 200)                            # still logged in
        AppSetting.objects.create(key=f"tour_done:{self.owner.pk}", value="x")
        self.assertRedirects(self.client.post("/conta/", {"action": "tour"}), "/", fetch_redirect_response=False)
        self.assertFalse(AppSetting.objects.filter(key=f"tour_done:{self.owner.pk}").exists())

    def test_plan_and_telegram_have_the_organization_tabs(self):
        for url in ("/organizacao/plano/", "/organizacao/telegram/", "/organizacao/membros/"):
            page = self.client.get(url).content.decode()
            self.assertIn('class="org-tabs"', page, url)
            self.assertIn("você é o dono", page, url)
