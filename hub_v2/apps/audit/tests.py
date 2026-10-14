"""N-120: who did what, when and from where — append only, merged per request, never secrets."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, Node
from apps.audit import services
from apps.audit.models import AppendOnly, AuditEvent
from apps.proxy.models import ProxyProfile


def actions():
    return list(AuditEvent.objects.order_by("id").values_list("action", flat=True))


class AuditTests(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = get_user_model().objects.create_superuser("rodrigo", "r@example.com", "senha-boa")

    def test_login_and_failed_login_with_the_real_address(self):
        self.client.post(reverse("login"), {"username": "rodrigo", "password": "senha-boa"}, HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
        self.client.logout()
        self.client.post(reverse("login"), {"username": "rodrigo", "password": "errada"}, REMOTE_ADDR="198.51.100.2")
        events = list(AuditEvent.objects.order_by("id"))
        self.assertEqual([e.action for e in events], ["login", "logout", "login_failed"])
        self.assertEqual(events[0].ip, "203.0.113.7")
        self.assertEqual(events[0].actor_id, self.boss.pk)
        self.assertEqual((events[2].result, events[2].actor_kind, events[2].ip), ("failed", "anonymous", "198.51.100.2"))
        self.assertNotIn("errada", str(events[2].details) + events[2].target_label)

    def test_what_a_person_does_on_the_screens(self):
        self.client.force_login(self.boss)
        node = Node.objects.create(name="agente-1")
        self.client.post(reverse("accounts:account-create"), {
            "node": node.pk, "label": "Principal", "email": "a@example.com", "password": "segredo-do-jogo",
        })
        event = AuditEvent.objects.get(action="account.created")
        self.assertEqual((event.actor_label, event.target_label, event.category), ("rodrigo", "Principal", "contas"))
        self.assertEqual(event.organization.slug, "principal")
        self.assertNotIn("segredo-do-jogo", str(AuditEvent.objects.values_list("details", "target_label")))

    def test_the_agent_and_commands_are_not_someone(self):
        node = Node.objects.create(name="agente-1")                      # outside a request: a command, a test
        node.agent_last_seen_at = timezone.now()
        node.save(update_fields=["agent_last_seen_at"])
        self.assertEqual(actions(), [])

    def test_many_rows_in_one_request_are_one_event(self):
        def bulk_save(request):
            from django.http import HttpResponse

            for i in range(5):
                ProxyProfile.objects.create(address=f"10.0.0.{i}", port=80)
            return HttpResponse("ok")

        request = self._request()
        token = services.begin(request)
        bulk_save(request)
        services.finish(token, write=True)
        event = AuditEvent.objects.get()
        self.assertEqual((event.action, event.count), ("proxyprofile.created", 5))
        self.assertTrue(event.target_label.startswith("P") and "e mais 4" in event.target_label)   # proxies by id, never address
        self.assertNotIn("10.0.0", event.target_label)

    def test_a_request_that_breaks_records_nothing(self):
        request = self._request()
        token = services.begin(request)
        Node.objects.create(name="agente-1")
        services.finish(token, write=False)                               # the middleware passes write=False on a 500
        self.assertEqual(actions(), [])

    def test_append_only(self):
        services.record("login", user=self.boss)
        event = AuditEvent.objects.get()
        event.target_label = "outro"
        with self.assertRaises(AppendOnly):
            event.save()
        with self.assertRaises(AppendOnly):
            event.delete()
        with self.assertRaises(AppendOnly):
            AuditEvent.objects.all().delete()
        with self.assertRaises(AppendOnly):
            AuditEvent.objects.update(action="x")

    def test_retention_removes_only_what_is_old(self):
        services.record("login", user=self.boss)
        old = AuditEvent(action="login", category="acesso", created_at=timezone.now() - timedelta(days=400))
        old.save()
        cache.clear()
        self.assertEqual(services.purge_if_due(), 1)
        self.assertEqual(AuditEvent.objects.count(), 1)
        self.assertEqual(services.purge_if_due(), 0)                     # once a day

    def test_password_change_is_recorded_without_the_password(self):
        self.client.force_login(self.boss)
        other = get_user_model().objects.create_user("amigo", password="antiga")
        request = self._request()
        token = services.begin(request)
        other.set_password("nova-senha")
        other.save()
        services.finish(token, write=True)
        event = AuditEvent.objects.get(action="user.password")
        self.assertEqual(event.target_label, "amigo")
        self.assertNotIn("nova-senha", str(event.details))

    @override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
    def test_credentials_given_to_the_agent_once_an_hour(self):
        from core.encryption import encrypt

        node = Node.objects.create(name="agente-1", active=True)
        Account.objects.create(node=node, label="Principal", email="a@example.com", password_enc=encrypt("segredo"))
        for _ in range(2):
            response = self.client.get(reverse("agent-accounts:config"), {"node_id": str(node.pk)},
                                       HTTP_X_AGENT_TOKEN="test-agent-token", HTTP_X_FORWARDED_FOR="192.0.2.9")
            self.assertEqual(response.status_code, 200)
        event = AuditEvent.objects.get(action="credentials.delivered")
        self.assertEqual((event.actor_kind, event.actor_label, event.details, event.ip),
                         ("agent", "agente-1", {"contas": ["Principal"]}, "192.0.2.9"))
        self.assertNotIn("segredo", str(event.details))

    def test_the_owner_reads_it_and_others_do_not(self):
        services.record("login", user=self.boss)
        self.client.force_login(self.boss)
        html = self.client.get(reverse("audit:list")).content.decode()
        self.assertIn("Entrou", html)
        self.assertIn('href="/atividades/"', html)
        operator = get_user_model().objects.create_user("amigo", password="x")
        operator.groups.add(Group.objects.get_or_create(name="operator")[0])
        self.client.force_login(operator)
        self.assertEqual(self.client.get(reverse("audit:list")).status_code, 403)

    def test_filters(self):
        services.record("login", user=self.boss)
        services.record("login_failed", actor_kind="anonymous", actor_label="intruso", result="failed")
        self.client.force_login(self.boss)
        response = self.client.get(reverse("audit:list"), {"falhas": "1"})
        self.assertEqual([e.action for e in response.context["page"]], ["login_failed"])
        response = self.client.get(reverse("audit:list"), {"categoria": "acesso", "quem": "rodrigo"})
        self.assertEqual({e.action for e in response.context["page"]}, {"login"})          # force_login is a login too

    def _request(self):
        from django.test import RequestFactory

        request = RequestFactory().post("/accounts/", REMOTE_ADDR="198.51.100.5", HTTP_USER_AGENT="teste")
        request.user = self.boss
        return request
