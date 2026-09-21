from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.notes.services import RegistryRelease

from .models import AgentUpdateRequest, Node

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
        self.node = Node.objects.create(
            name="blackshadow",
            agent_version="0.1.55",
            agent_last_seen_at=timezone.now(),
            updater_last_seen_at=timezone.now(),
        )

    @patch("apps.notes.services.get_registry_release")
    def test_operator_can_queue_scoped_update(self, get_release):
        get_release.return_value = RegistryRelease(
            "agent", "blackoneal/ikabot-web-agent", "0.1.56"
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:node-request-update", kwargs={"pk": self.node.pk})
        )

        self.assertRedirects(response, reverse("notes:changelog"), fetch_redirect_response=False)
        update = AgentUpdateRequest.objects.get(node=self.node)
        self.assertEqual(update.target_image, "blackoneal/ikabot-web-agent:0.1.56")
        self.assertEqual(update.requested_by, self.user)

    @patch("apps.notes.services.get_registry_release")
    def test_operator_cannot_queue_when_updater_is_offline(self, get_release):
        get_release.return_value = RegistryRelease(
            "agent", "blackoneal/ikabot-web-agent", "0.1.56"
        )
        self.node.updater_last_seen_at = timezone.now() - timedelta(minutes=5)
        self.node.save(update_fields=["updater_last_seen_at"])
        self.client.force_login(self.user)

        self.client.post(reverse("accounts:node-request-update", kwargs={"pk": self.node.pk}))

        self.assertFalse(AgentUpdateRequest.objects.filter(node=self.node).exists())

    def test_updater_poll_claims_only_its_node_request(self):
        update = AgentUpdateRequest.objects.create(
            node=self.node,
            target_version="0.1.56",
            target_image="blackoneal/ikabot-web-agent:0.1.56",
        )
        client = APIClient()
        client.credentials(
            HTTP_X_AGENT_TOKEN=self.node.deploy_token,
            HTTP_X_AGENT_NODE_ID=str(self.node.pk),
        )

        response = client.get(reverse("agent-accounts:update-next"), {
            "node_id": str(self.node.pk),
            "updater_version": "0.1.56",
            "target_container": "ikabot-web-agent-1",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["update"]["id"], str(update.pk))
        update.refresh_from_db()
        self.assertEqual(update.status, "running")
        self.node.refresh_from_db()
        self.assertEqual(self.node.updater_container, "ikabot-web-agent-1")

    def test_updater_status_rejects_cross_node_scope(self):
        other = Node.objects.create(name="other")
        update = AgentUpdateRequest.objects.create(
            node=self.node,
            target_version="0.1.56",
            target_image="blackoneal/ikabot-web-agent:0.1.56",
            status="running",
        )
        client = APIClient()
        client.credentials(
            HTTP_X_AGENT_TOKEN=other.deploy_token,
            HTTP_X_AGENT_NODE_ID=str(other.pk),
        )

        response = client.post(
            reverse("agent-accounts:update-status", kwargs={"update_id": update.pk}),
            {"node_id": str(self.node.pk), "status": "succeeded"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
