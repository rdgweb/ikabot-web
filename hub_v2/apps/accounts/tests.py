from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.notes.services import RegistryRelease

from .models import AgentUpdateRequest, DockerHost, Node

from .services.agent_versions import classify_agent_version, parse_version


class AgentVersionTests(SimpleTestCase):
    def test_parse_version_accepts_release_and_optional_v_prefix(self):
        self.assertEqual(parse_version("0.1.55"), (0, 1, 55))
        self.assertEqual(parse_version("v2.10.3"), (2, 10, 3))

    def test_parse_version_rejects_partial_or_non_numeric_versions(self):
        self.assertIsNone(parse_version("0.1"))
        self.assertIsNone(parse_version("latest"))
        self.assertIsNone(parse_version("01.2.3"))

    def test_classify_agent_version(self):
        self.assertEqual(classify_agent_version("0.1.54", "0.1.55").code, "outdated")
        self.assertEqual(classify_agent_version("0.1.55", "0.1.55").code, "current")
        self.assertEqual(classify_agent_version("0.1.56", "0.1.55").code, "ahead")
        self.assertEqual(classify_agent_version("dev", "0.1.55").code, "invalid")
        self.assertEqual(classify_agent_version("", "0.1.55").code, "missing")
        self.assertEqual(classify_agent_version("0.1.55", "").code, "unverified")


class AgentUpdateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="operator", password="x")
        self.host = DockerHost.objects.create(
            name="docker-main",
            last_seen_at=timezone.now(),
            machine_id="engine-main",
        )
        self.node = Node.objects.create(
            name="blackshadow",
            agent_version="0.1.55",
            agent_last_seen_at=timezone.now(),
            docker_host=self.host,
        )

    @patch("apps.notes.services.get_registry_release")
    def test_operator_can_queue_scoped_update(self, get_release):
        get_release.return_value = RegistryRelease(
            "agent", "blackoneal/ikabot-web-agent", "0.1.56", digest="sha256:" + "a" * 64
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:node-request-update", kwargs={"pk": self.node.pk})
        )

        self.assertRedirects(response, reverse("notes:changelog"), fetch_redirect_response=False)
        update = AgentUpdateRequest.objects.get(node=self.node)
        self.assertEqual(update.target_image, "blackoneal/ikabot-web-agent@sha256:" + "a" * 64)
        self.assertEqual(update.requested_by, self.user)

    @patch("apps.notes.services.get_registry_release")
    def test_operator_cannot_queue_when_updater_is_offline(self, get_release):
        get_release.return_value = RegistryRelease(
            "agent", "blackoneal/ikabot-web-agent", "0.1.56", digest="sha256:" + "a" * 64
        )
        self.host.last_seen_at = timezone.now() - timedelta(minutes=5)
        self.host.save(update_fields=["last_seen_at"])
        self.client.force_login(self.user)

        self.client.post(reverse("accounts:node-request-update", kwargs={"pk": self.node.pk}))

        self.assertFalse(AgentUpdateRequest.objects.filter(node=self.node).exists())

    def test_supervisor_poll_claims_only_a_node_from_its_host(self):
        update = AgentUpdateRequest.objects.create(
            node=self.node,
            target_version="0.1.56",
            target_image="blackoneal/ikabot-web-agent@sha256:" + "a" * 64,
        )
        client = APIClient()
        client.credentials(
            HTTP_X_IKABOT_HOST_TOKEN=self.host.enrollment_token,
            HTTP_X_IKABOT_HOST_ID=str(self.host.pk),
        )

        response = client.get(reverse("agent-accounts:supervisor-update-next"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["update"]["id"], str(update.pk))
        update.refresh_from_db()
        self.assertEqual(update.status, "running")

    def test_supervisor_status_rejects_cross_host_scope(self):
        other_host = DockerHost.objects.create(name="other-host", machine_id="engine-other")
        update = AgentUpdateRequest.objects.create(
            node=self.node,
            target_version="0.1.56",
            target_image="blackoneal/ikabot-web-agent@sha256:" + "a" * 64,
            status="running",
        )
        client = APIClient()
        client.credentials(
            HTTP_X_IKABOT_HOST_TOKEN=other_host.enrollment_token,
            HTTP_X_IKABOT_HOST_ID=str(other_host.pk),
        )

        response = client.post(
            reverse("agent-accounts:supervisor-update-status", kwargs={"update_id": update.pk}),
            {"status": "succeeded"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_heartbeat_binds_engine_and_returns_managed_nodes(self):
        self.host.machine_id = None
        self.host.save(update_fields=["machine_id"])
        client = APIClient()
        client.credentials(
            HTTP_X_IKABOT_HOST_TOKEN=self.host.enrollment_token,
            HTTP_X_IKABOT_HOST_ID=str(self.host.pk),
        )

        response = client.post(
            reverse("agent-accounts:supervisor-heartbeat"),
            {"machine_id": "engine-main", "version": "0.1.0", "image": "supervisor@sha256:abc"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(str(self.node.pk), response.data["managed_node_ids"])
