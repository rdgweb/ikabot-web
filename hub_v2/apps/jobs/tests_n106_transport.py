"""N-106 3b-2: agents over HTTPS only — jobs claimed from the hub, manual control through the hub."""
import secrets
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job
from apps.jobs.services.dispatch import dispatch_job
from apps.manual_control import rpc
from apps.manual_control.models import HeldJob, ManualControlSession


@override_settings(AGENT_TOKEN="", AGENT_ALLOWED_IPS="")
class HttpsTransportTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente-https", transport=Node.TRANSPORT_HTTPS)
        self.key = self.node.new_agent_token
        self.other = Node.objects.create(name="agente-redis")
        self.other_key = self.other.new_agent_token
        account = Account.objects.create(node=self.node, label="Conta", email="a@example.com", password_enc="x")
        self.world = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                                server_number=78, name="Jogador")
        with patch("apps.jobs.signals.dispatch_job"):
            self.due = Job.objects.create(account=account, game_account=self.world, node=self.node, action_code=100)
            self.later = Job.objects.create(account=account, node=self.node, action_code=100, status="scheduled",
                                            scheduled_for=timezone.now() + timedelta(hours=1))
            self.held = Job.objects.create(account=account, node=self.node, action_code=100)
        session = ManualControlSession.objects.create(game_account=self.world, node=self.node, token=secrets.token_hex(8),
                                                      expires_at=timezone.now() + timedelta(minutes=5), status="ended")
        HeldJob.objects.create(job=self.held, session=session)

    def _claim(self, slots=4, key=None, node=None):
        node = node or self.node
        return self.client.post(reverse("agent-jobs:claim"), {"node_id": str(node.pk), "slots": slots},
                                content_type="application/json", HTTP_X_AGENT_TOKEN=key or self.key, HTTP_X_AGENT_NODE_ID=str(node.pk))

    def test_the_agent_gets_only_its_due_jobs_and_never_twice(self):
        response = self._claim()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([job["job_id"] for job in response.json()["jobs"]], [str(self.due.pk)])   # not later, not held
        self.assertEqual(self._claim().json()["jobs"], [])                                          # claimed: not again
        Job._base_manager.filter(pk=self.due.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(len(self._claim().json()["jobs"]), 1)                                      # the agent never started it

    def test_slots_limit_what_comes(self):
        with patch("apps.jobs.signals.dispatch_job"):
            for _ in range(3):
                Job.objects.create(account=self.due.account, node=self.node, action_code=100)
        self.assertEqual(len(self._claim(slots=2).json()["jobs"]), 2)

    def test_a_broker_node_gets_nothing_here(self):
        response = self._claim(key=self.other_key, node=self.other)
        self.assertEqual(response.json(), {"jobs": [], "transport": "redis", "manual": False})

    def test_an_agent_cannot_claim_for_another_node(self):
        response = self.client.post(reverse("agent-jobs:claim"), {"node_id": str(self.node.pk), "slots": 4},
                                    content_type="application/json", HTTP_X_AGENT_TOKEN=self.other_key, HTTP_X_AGENT_NODE_ID=str(self.other.pk))
        self.assertEqual(response.status_code, 403)

    def test_dispatch_leaves_https_jobs_in_the_database(self):
        eta = timezone.now() + timedelta(minutes=10)
        with patch("apps.jobs.services.dispatch.celery_app.send_task") as send:
            dispatch_job(self.due, eta=eta)
        send.assert_not_called()
        self.due.refresh_from_db()
        self.assertEqual(self.due.scheduled_for, eta)
        with patch("apps.jobs.services.dispatch.celery_app.send_task") as send:
            dispatch_job(Job.objects.create(account=self.due.account, node=self.other, action_code=100))
        send.assert_called()                                                                        # broker nodes as before

    def test_manual_control_goes_through_the_hub(self):
        request_id = rpc.send(self.node.pk, {"op": "open", "session": "s1"})
        self.assertTrue(self._claim().json()["manual"])
        response = self.client.get(reverse("agent-jobs:manual-next"), {"node_id": str(self.node.pk), "wait": 1},
                                   HTTP_X_AGENT_TOKEN=self.key, HTTP_X_AGENT_NODE_ID=str(self.node.pk))
        self.assertEqual(response.json()["request"]["id"], request_id)
        # another node cannot answer it, even knowing the id
        stranger = self.client.post(reverse("agent-jobs:manual-answer"), {"node_id": str(self.other.pk), "id": request_id, "answer": {"ok": True}},
                                    content_type="application/json", HTTP_X_AGENT_TOKEN=self.other_key, HTTP_X_AGENT_NODE_ID=str(self.other.pk))
        self.assertEqual(stranger.status_code, 404)
        own = self.client.post(reverse("agent-jobs:manual-answer"), {"node_id": str(self.node.pk), "id": request_id, "answer": {"ok": True, "host": "s78"}},
                               content_type="application/json", HTTP_X_AGENT_TOKEN=self.key, HTTP_X_AGENT_NODE_ID=str(self.node.pk))
        self.assertEqual(own.status_code, 200)
        self.assertEqual(rpc.wait(request_id, timeout=1), {"ok": True, "host": "s78"})

    def test_no_session_no_waiting(self):
        response = self.client.get(reverse("agent-jobs:manual-next"), {"node_id": str(self.node.pk), "wait": 1},
                                   HTTP_X_AGENT_TOKEN=self.key, HTTP_X_AGENT_NODE_ID=str(self.node.pk))
        self.assertEqual(response.json(), {"request": None, "active": False})
