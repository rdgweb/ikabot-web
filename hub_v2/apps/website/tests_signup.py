"""N-125: open sign-up confirmed by e-mail, and forgot my password."""
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.billing.models import BillingAccount
from apps.billing.services import account_for
from apps.orgs.models import Membership, TermsAcceptance
from apps.orgs.services import TERMS_VERSION, default_organization

GOOD = {"email": "novo@example.com", "username": "novato", "organization": "Aliança Sul",
        "password1": "Ikariam-Teste-2026!", "password2": "Ikariam-Teste-2026!", "accept_terms": "1"}
OPEN = {"SITE_URL": "https://site.example", "SIGNUP_OPEN": True, "EMAIL_READY": True,
        "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend", "UMAMI_URL": "", "UMAMI_WEBSITE_ID": ""}


def link_in(message) -> str:
    return re.search(r"https://site\.example(/\S+)", message.body).group(1)


@override_settings(**OPEN)
class SignupTests(TestCase):
    def setUp(self):
        cache.clear()

    def _send(self, **change):
        return self.client.post("/pt/cadastro/enviar/", {**GOOD, **change}, HTTP_X_REAL_IP="203.0.113.20")

    def test_sign_up_confirm_and_start_the_trial(self):
        self.assertIn("Começar grátis", self.client.get("/pt/").content.decode())
        self.assertRedirects(self._send(), "/pt/cadastro/?ok=1", fetch_redirect_response=False)
        user = get_user_model().objects.get(username="novato")
        self.assertFalse(user.is_active)                                        # nothing works before the e-mail
        self.assertFalse(Membership.objects.filter(user=user).exists())
        self.assertEqual(TermsAcceptance.objects.get(user=user).version, TERMS_VERSION)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["novo@example.com"])
        self.assertFalse(self.client.login(username="novato", password=GOOD["password1"]))
        confirm = link_in(mail.outbox[0])
        self.assertRedirects(self.client.get(confirm), "/", fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        membership = Membership.objects.get(user=user)
        self.assertEqual((membership.organization.name, membership.role), ("Aliança Sul", "owner"))
        self.assertNotEqual(membership.organization, default_organization())
        self.assertEqual(account_for(membership.organization).status, BillingAccount.TRIAL)
        self.assertEqual(self.client.get("/").status_code, 200)                  # logged in, in the panel
        self.client.logout()
        self.assertEqual(self.client.get(confirm).status_code, 410)              # one use

    def test_wrong_input_is_refused(self):
        get_user_model().objects.create_user("antigo", email="Usado@Example.com", password="x")
        for change, text in (({"email": "usado@example.com"}, "já tem conta"), ({"username": "antigo"}, "já existe"),
                             ({"password2": "outra"}, "não conferem"), ({"accept_terms": ""}, "aceitar os termos"),
                             ({"username": "com espaço"}, "sem espaços")):
            response = self._send(**change)
            self.assertEqual(response.status_code, 400, change)
            self.assertIn(text, response.content.decode())
        self.assertEqual(len(mail.outbox), 0)

    def test_robots_and_floods(self):
        self._send(website="http://spam.example")
        self.assertFalse(get_user_model().objects.filter(username="novato").exists())
        for i in range(3):
            self._send(email=f"n{i}@example.com", username=f"novo{i}")
        self.assertIn("Muitos cadastros", self._send(email="n9@example.com", username="novo9").content.decode())

    def test_a_forged_link_does_nothing(self):
        self._send()
        self.assertEqual(self.client.get("/conta/confirmar/inventado/").status_code, 410)
        self.assertFalse(get_user_model().objects.get(username="novato").is_active)

    @override_settings(SIGNUP_OPEN=False)
    def test_closed_sign_up_keeps_the_invitation(self):
        page = self.client.get("/pt/cadastro/").content.decode()
        self.assertIn("Cadastro por convite", page)
        self.assertIn("Pedir convite", self.client.get("/pt/").content.decode())
        self._send()
        self.assertFalse(get_user_model().objects.filter(username="novato").exists())

    @override_settings(EMAIL_READY=False)
    def test_without_smtp_there_is_no_open_sign_up(self):
        self.assertIn("Cadastro por convite", self.client.get("/pt/cadastro/").content.decode())


@override_settings(**OPEN)
class PasswordResetTests(TestCase):
    def test_forgot_my_password_by_email(self):
        user = get_user_model().objects.create_user("rodrigo", email="r@example.com", password="antiga-123")
        self.assertIn("Esqueci minha senha", self.client.get("/login/").content.decode())
        self.assertRedirects(self.client.post("/senha/esqueci/", {"email": "r@example.com"}), "/senha/enviado/",
                             fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        link = link_in(mail.outbox[0])
        page = self.client.get(link, follow=True)
        self.assertIn("Criar uma senha nova", page.content.decode())
        form_url = page.redirect_chain[-1][0]
        self.client.post(form_url, {"new_password1": "Nova-Senha-2026!", "new_password2": "Nova-Senha-2026!"})
        user.refresh_from_db()
        self.assertTrue(user.check_password("Nova-Senha-2026!"))

    def test_unknown_email_looks_the_same_and_sends_nothing(self):
        self.assertRedirects(self.client.post("/senha/esqueci/", {"email": "ninguem@example.com"}), "/senha/enviado/",
                             fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)
