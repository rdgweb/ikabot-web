"""N-64: the dashboard must reflect the system as it actually is today —
workflow health, real agent status, the notes queue, and usage telemetry —
instead of the original MVP-era raw job counters/list."""

from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Workflow
from apps.notes.models import Note

from .views import _donut_segments


class DonutSegmentsTests(TestCase):
    def test_percentages_and_offsets_are_computed_correctly(self):
        chart = _donut_segments([("a", 3, "#111"), ("b", 1, "#222")])

        self.assertEqual(chart["total"], 4)
        a, b = chart["segments"]
        # Formatted as locale-independent strings ("." separator) — SVG
        # attributes break under pt-BR's Django-localized "," separator.
        self.assertEqual(a["percent"], "75.00")
        self.assertEqual(a["remainder"], "25.00")
        self.assertEqual(a["dash_offset"], "-0.00")
        self.assertEqual(b["percent"], "25.00")
        self.assertEqual(b["dash_offset"], "-75.00")

    def test_zero_count_segments_are_dropped(self):
        chart = _donut_segments([("a", 0, "#111"), ("b", 2, "#222")])

        self.assertEqual(len(chart["segments"]), 1)
        self.assertEqual(chart["segments"][0]["label"], "b")

    def test_empty_input_has_zero_total_and_no_segments(self):
        chart = _donut_segments([])

        self.assertEqual(chart["total"], 0)
        self.assertEqual(chart["segments"], [])


class DashboardViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="dash-user", email="dash@example.com", password="secret123",
        )
        self.node_online = Node.objects.create(name="node-online")
        self.node_offline = Node.objects.create(name="node-offline")
        self.account = Account.objects.create(
            node=self.node_online, label="Conta Dash", email="dash-acc@example.com", password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=1, server_id="s1-br",
            server_language="br", server_number=1, name="Test",
        )

    def _get(self):
        self.client.force_login(self.user)
        with patch("apps.dashboard.views.requests.get", side_effect=ConnectionError("no network in tests")):
            return self.client.get(reverse("dashboard:index"))

    def test_dashboard_loads_even_when_telemetry_is_unreachable(self):
        """The telemetry service is a separate deployment — a network failure
        there must never break the dashboard page itself."""
        response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["telemetry"])
        self.assertContains(response, "Servico de telemetria indispon")

    def test_telemetry_panel_renders_the_fetched_summary(self):
        self.client.force_login(self.user)
        fake_response = Mock()
        fake_response.json.return_value = {
            "active_installations_7d": 3, "active_installations_30d": 9,
        }
        fake_response.raise_for_status = Mock()
        with patch("apps.dashboard.views.requests.get", return_value=fake_response):
            response = self.client.get(reverse("dashboard:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["telemetry"]["active_installations_7d"], 3)
        self.assertContains(response, "3")
        self.assertContains(response, "9")

    def test_problem_workflows_surface_on_the_dashboard(self):
        healthy = Workflow.objects.create(
            account=self.account, node=self.node_online, workflow_type="donate_loop", status="active",
        )
        broken = Workflow.objects.create(
            account=self.account, node=self.node_online, workflow_type="construction_plan",
            status="problem", last_error_summary="Cidade nao encontrada no snapshot",
        )
        from django.utils import timezone

        archived_problem = Workflow.objects.create(
            account=self.account, node=self.node_online, workflow_type="donate_loop",
            status="problem", archived_at=timezone.now(),
        )

        response = self._get()

        problem_pks = {wf.pk for wf in response.context["problem_workflows"]}
        self.assertIn(broken.pk, problem_pks)
        self.assertNotIn(healthy.pk, problem_pks)
        # Archived workflows are out of scope — they're not "current" problems.
        self.assertNotIn(archived_problem.pk, problem_pks)
        self.assertContains(response, "Cidade nao encontrada no snapshot")
        self.assertEqual(response.context["workflows_problem"], 1)

    def test_node_health_shows_agent_version_and_online_state(self):
        from django.utils import timezone

        self.node_online.agent_version = "0.2.86"
        self.node_online.agent_last_seen_at = timezone.now()
        self.node_online.save(update_fields=["agent_version", "agent_last_seen_at"])

        response = self._get()

        self.assertEqual(response.context["online_nodes"], 1)
        self.assertEqual(response.context["total_nodes"], 2)
        self.assertContains(response, "v0.2.86")

    def test_notes_queue_counts_and_doing_list(self):
        Note.objects.create(title="Aberta 1", status="open")
        Note.objects.create(title="Autorizada 1", status="authorized")
        doing = Note.objects.create(
            title="Em andamento 1", status="doing", priority="urgent", claimed_by_label="Claude (Sonnet 5)",
        )

        response = self._get()

        self.assertEqual(response.context["notes_queue_count"], 2)  # open + authorized
        self.assertEqual(response.context["notes_doing_count"], 1)
        self.assertIn(doing, response.context["notes_doing_list"])
        self.assertContains(response, "Claude (Sonnet 5)")

    def test_accounts_by_world_reflects_active_game_accounts(self):
        GameAccount.objects.create(
            account=self.account, lobby_account_id=2, server_id="s1-br",
            server_language="br", server_number=1, name="Second",
        )
        other_account = Account.objects.create(
            node=self.node_online, label="Outra Conta", email="other@example.com", password_enc="x", active=False,
        )
        GameAccount.objects.create(
            account=other_account, lobby_account_id=3, server_id="s9-de",
            server_language="de", server_number=9, name="Inactive owner",
        )

        response = self._get()

        chart = response.context["accounts_by_world"]
        by_world = {seg["label"]: seg["count"] for seg in chart["segments"]}
        self.assertEqual(by_world.get("s1-br"), 2)
        # The inactive account's game account must not be counted.
        self.assertNotIn("s9-de", by_world)
