"""N-261: an agent update is queued, never refused for a busy node — the node stops taking new jobs, finishes what is
running, then updates; the queue goes on with the new version."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import node_drain
from apps.accounts.models import Account, AgentUpdateRequest, DockerHost, Node
from apps.jobs.models import Job
from apps.notes.services import RegistryRelease

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "n261"}}
IMAGE = "blackoneal/ikabot-web-agent@sha256:" + "a" * 64


@override_settings(AGENT_TOKEN="", AGENT_ALLOWED_IPS="", CACHES=LOCMEM)
class UpdateQueueTests(TestCase):
    def setUp(self):
        cache.clear()
        self.host = DockerHost.objects.create(name="docker-main", last_seen_at=timezone.now(), machine_id="engine-main")
        self.host_token = self.host.new_enrollment_token
        self.node = Node.objects.create(name="ocupado", transport=Node.TRANSPORT_HTTPS, agent_version="0.1.55",
                                        agent_last_seen_at=timezone.now(), docker_host=self.host)
        self.key = self.node.new_agent_token
        account = Account.objects.create(node=self.node, label="Conta", email="a@example.com", password_enc="x")
        with patch("apps.jobs.signals.dispatch_job"):
            self.running = Job.objects.create(account=account, node=self.node, action_code=100, status="running")
            self.waiting = Job.objects.create(account=account, node=self.node, action_code=100)

    def _claim(self):
        return self.client.post(reverse("agent-jobs:claim"), {"node_id": str(self.node.pk), "slots": 4},
                                content_type="application/json", HTTP_X_AGENT_TOKEN=self.key,
                                HTTP_X_AGENT_NODE_ID=str(self.node.pk)).json()["jobs"]

    def _supervisor_poll(self):
        client = APIClient()
        client.credentials(HTTP_X_IKABOT_HOST_TOKEN=self.host_token, HTTP_X_IKABOT_HOST_ID=str(self.host.pk))
        return client.get(reverse("agent-accounts:supervisor-update-next")).data["update"]

    @patch("apps.notes.services.get_registry_release")
    def test_a_busy_node_queues_the_update(self, get_release):
        get_release.return_value = RegistryRelease("agent", "blackoneal/ikabot-web-agent", "0.1.56", digest="sha256:" + "a" * 64)
        user = get_user_model().objects.create_superuser(username="rdg", password="x", email="r@example.com")
        self.client.force_login(user)
        self.client.post(reverse("accounts:node-request-update", kwargs={"pk": self.node.pk}))
        self.assertEqual(AgentUpdateRequest.objects.get(node=self.node).status, "queued")

    def test_pending_update_drains_the_node_then_updates(self):
        update = AgentUpdateRequest.objects.create(node=self.node, target_version="0.1.56", target_image=IMAGE)
        self.assertEqual(self._claim(), [])                    # no new job while the update waits
        self.assertIsNone(self._supervisor_poll())             # a job is still running
        Job._base_manager.filter(pk=self.running.pk).update(status="finished")
        self.assertEqual(self._supervisor_poll()["id"], str(update.pk))   # the queued job does not hold it back
        update.refresh_from_db()
        self.assertEqual(update.status, "running")
        AgentUpdateRequest.objects.filter(pk=update.pk).update(status="succeeded")
        self.assertEqual([job["job_id"] for job in self._claim()], [str(self.waiting.pk)])   # the queue goes on

    def test_cancel_gives_the_node_back(self):
        AgentUpdateRequest.objects.create(node=self.node, target_version="0.1.56", target_image=IMAGE)
        user = get_user_model().objects.create_superuser(username="rdg", password="x", email="r@example.com")
        self.client.force_login(user)
        self.client.post(reverse("accounts:node-cancel-update", kwargs={"pk": self.node.pk}))
        self.assertEqual(AgentUpdateRequest.objects.get(node=self.node).status, "cancelled")
        self.assertEqual(len(self._claim()), 1)

    def test_an_update_never_applied_stops_holding_the_node(self):
        update = AgentUpdateRequest.objects.create(node=self.node, target_version="0.1.56", target_image=IMAGE)
        AgentUpdateRequest.objects.filter(pk=update.pk).update(created_at=timezone.now() - timedelta(hours=7))
        self.assertEqual(len(self._claim()), 1)
        update.refresh_from_db()
        self.assertEqual(update.status, "failed")

    def test_platform_deploy_drain(self):
        node_drain.start_drain(self.node, 600)
        self.assertEqual(self._claim(), [])
        self.assertEqual(node_drain.busy_jobs(self.node), 1)
        node_drain.stop_drain(self.node)
        self.assertEqual(len(self._claim()), 1)
