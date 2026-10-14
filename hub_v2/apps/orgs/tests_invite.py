"""N-106: beta by invitation — one-use link, own organization, terms accepted and recorded."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, Node
from apps.orgs.models import Invite, Membership, TermsAcceptance
from apps.orgs.services import TERMS_VERSION, create_invite, default_organization
from core.auth.tokens import hash_agent_token

GOOD = {"username": "convidado", "password1": "Ikariam-Beta-2026!", "password2": "Ikariam-Beta-2026!", "accept_terms": "1"}


class InviteTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        node = Node.objects.create(name="agente")                         # the default organization's data
        Account.objects.create(node=node, label="Conta do Rodrigo", email="r@example.com", password_enc="x")

    def _invite(self):
        invite, token = create_invite(email="Amigo@Example.com", organization_name="Aliança Norte", created_by=self.staff)
        return invite, token

    def test_the_team_creates_an_invite_and_sees_the_link_once(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("invites"), {"email": "amigo@example.com", "organization_name": "Aliança Norte"})
        page = self.client.get(reverse("invites")).content.decode()
        self.assertIn("/convite/", page)
        token = page.split("/convite/")[1].split("/")[0]
        invite = Invite.objects.get()
        self.assertEqual(invite.token_hash, hash_agent_token(token))
        self.assertNotIn(token, str(Invite.objects.values().first()))
        self.assertNotIn("/convite/", self.client.get(reverse("invites")).content.decode())

    def test_accepting_creates_its_own_organization_and_records_the_terms(self):
        invite, token = self._invite()
        response = self.client.post(reverse("invite-accept", args=[token]), GOOD, HTTP_X_REAL_IP="203.0.113.9")
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        user = get_user_model().objects.get(username="convidado")
        memberships = list(Membership.objects.filter(user=user).values_list("organization__name", "role"))
        self.assertEqual(memberships, [("Aliança Norte", "owner")])            # never the default organization
        acceptance = TermsAcceptance.objects.get(user=user)
        self.assertEqual((acceptance.version, acceptance.ip), (TERMS_VERSION, "203.0.113.9"))
        self.assertEqual(user.email, "amigo@example.com")
        invite.refresh_from_db()
        self.assertIsNotNone(invite.used_at)
        html = self.client.get(reverse("accounts:account-list")).content.decode()   # logged in as the new owner
        self.assertNotIn("Conta do Rodrigo", html)
        self.assertEqual(self.client.get(reverse("invite-accept", args=[token])).context["invite"], None)  # one use

    def test_an_expired_or_unknown_link_does_nothing(self):
        invite, token = self._invite()
        Invite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.post(reverse("invite-accept", args=[token]), GOOD).status_code, 410)
        self.assertEqual(self.client.post(reverse("invite-accept", args=["inventado"]), GOOD).status_code, 410)
        self.assertFalse(get_user_model().objects.filter(username="convidado").exists())

    def test_the_form_refuses_what_is_wrong(self):
        _invite, token = self._invite()
        for change, message in ((({"accept_terms": ""}), "aceitar os termos"), ({"password2": "outra"}, "não conferem"),
                                ({"username": "rodrigo"}, "já existe"), ({"password1": "123", "password2": "123"}, "")):
            response = self.client.post(reverse("invite-accept", args=[token]), {**GOOD, **change})
            self.assertEqual(response.status_code, 200)
            if message:
                self.assertIn(message, response.content.decode())
        self.assertFalse(get_user_model().objects.filter(username="convidado").exists())

    def test_customers_do_not_reach_the_invitations_and_the_terms_are_public(self):
        customer = get_user_model().objects.create_user("cliente", password="x")
        self.client.force_login(customer)
        self.assertEqual(self.client.get(reverse("invites")).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("terms")).status_code, 200)

    def test_a_logged_person_cannot_use_an_invite(self):
        _invite, token = self._invite()
        self.client.force_login(self.staff)
        response = self.client.post(reverse("invite-accept", args=[token]), GOOD)
        self.assertIn("Saia da sua conta", response.content.decode())
        self.assertFalse(get_user_model().objects.filter(username="convidado").exists())
        self.assertEqual(default_organization().memberships.filter(user__username="convidado").count(), 0)
