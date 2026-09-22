from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node

from .models import ConstructionResourceReservation, Job, Workflow, WorkflowRun
from .services.recovery import recover_stale_running_jobs
from .services.workflows import create_job_with_workflow, ensure_workflow_for_job, reconcile_workflow_for_job
from .views.create import JobSubmitView


class MarketJobCreationTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-1")
        self.account = Account.objects.create(
            node=self.node,
            label="Conta",
            email="conta@example.com",
            password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=1,
            server_id="s1-br",
            server_language="br",
            server_number=1,
            name="Atenas",
        )
        self.city = {
            "id": 101,
            "name": "Capital",
            "buildings": [
                {"building": "branchOffice", "position": 8},
            ],
        }

    def test_sell_market_job_infers_branch_office_position(self):
        JobSubmitView._create_single_job(
            self.ga,
            9,
            {
                "city_id": "101",
                "resource_idx": "0",
                "amount": 5000,
                "unit_price": 12,
                "_city_choices": {"101": "Capital"},
                "_city_objects": {"101": self.city},
            },
        )

        job = Job.objects.latest("created_at")
        self.assertIn('"branchoffice_pos": 8', job.inputs_json)
        self.assertIn('"city_name": "Capital"', job.inputs_json)

    def test_buy_market_job_infers_buyer_branch_office_position(self):
        JobSubmitView._create_single_job(
            self.ga,
            8,
            {
                "buyer_city_id": "101",
                "seller_city_id": "99999",
                "resource_idx": "1",
                "amount": 2500,
                "_city_choices": {"101": "Capital"},
                "_city_objects": {"101": self.city},
            },
        )

        job = Job.objects.latest("created_at")
        self.assertIn('"buyer_branchoffice_pos": 8', job.inputs_json)
        self.assertIn('"seller_branchoffice_pos": 0', job.inputs_json)
        self.assertIn('"buyer_city_name": "Capital"', job.inputs_json)


class JobChainHistoryPartialTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="jobs-user",
            email="jobs@example.com",
            password="secret123",
        )
        self.node = Node.objects.create(name="node-history")
        self.account = Account.objects.create(
            node=self.node,
            label="Conta History",
            email="history@example.com",
            password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=2,
            server_id="s2-br",
            server_language="br",
            server_number=2,
            name="Esparta",
        )
        self.root = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            status="finished",
            inputs_json="{}",
            timeout_sec=1800,
        )
        self.child_1 = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            status="scheduled",
            inputs_json="{}",
            timeout_sec=1800,
            root_job_id=self.root.pk,
            source_job_id=self.root.pk,
        )
        self.child_2 = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            status="queued",
            inputs_json="{}",
            timeout_sec=1800,
            root_job_id=self.root.pk,
            source_job_id=self.child_1.pk,
        )

    def test_tech_view_shows_the_root_job_alone_without_chain_linking(self):
        """N-59: the tech view must show only the job itself — no aggregating
        or linking to other jobs in its reschedule chain. It must not surface
        a 'ciclos anteriores' link, must not swap in a descendant job's status,
        and must not link to any job other than the row's own."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("jobs:job-list"), {"view": "tech"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ciclo(s) anteriores")
        self.assertNotContains(response, f"job-chain-history-body-{self.root.pk}")
        self.assertNotContains(response, f'href="{reverse("jobs:job-detail", args=[self.child_1.pk])}"')
        self.assertNotContains(response, f'href="{reverse("jobs:job-detail", args=[self.child_2.pk])}"')
        # The row is the root job itself, showing the root's own status
        # (finished) — not child_2's ("queued"), which the old "active
        # descendant" substitution used to show instead.
        self.assertContains(response, f'href="{reverse("jobs:job-detail", args=[self.root.pk])}"')
        self.assertContains(response, "Concluido")

    def test_chain_history_partial_returns_child_jobs_on_demand(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("jobs:job-chain-history", args=[self.root.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Historico da cadeia")
        self.assertContains(response, str(self.child_1.pk)[:8], html=False)
        self.assertContains(response, str(self.child_2.pk)[:8], html=False)


class JobWorkflowViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="jobs-ops-user",
            email="jobs-ops@example.com",
            password="secret123",
        )
        self.node = Node.objects.create(name="node-ops")
        self.account = Account.objects.create(
            node=self.node,
            label="Conta Ops",
            email="ops@example.com",
            password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=3,
            server_id="s3-br",
            server_language="br",
            server_number=3,
            name="Corinto",
        )
        self.root_finished = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            status="finished",
            inputs_json='{"construction_plan_json":[{"city_name":"Corinto","building_name":"Academia","target_level":7}]}',
            timeout_sec=1800,
        )
        self.active_child = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            status="running",
            inputs_json='{"city_name":"Corinto"}',
            timeout_sec=1800,
            root_job_id=self.root_finished.pk,
            source_job_id=self.root_finished.pk,
        )
        self.error_job = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=701,
            status="error",
            inputs_json='{"city_name":"Esparta"}',
            timeout_sec=1800,
        )

    def test_htmx_partial_swap_renders_workflow_table_with_selection_marker(self):
        """N-63: this is the exact response an HTMX pagination/filter click swaps into
        #table-content. It must render standalone (no base_list.html wrapper) and expose
        data-filtered-count, which the persistent x-data root uses to keep the bulk
        'select all filtered' count in sync after each swap."""
        self.client.force_login(self.user)
        ensure_workflow_for_job(self.root_finished)

        response = self.client.get(reverse("jobs:job-list"), {"status": "finished"}, HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-filtered-count=")
        self.assertContains(response, "wf-bulk-check")
        self.assertNotContains(response, "<html")  # standalone partial, not the full page

    def test_default_jobs_page_renders_operational_workflow_view(self):
        self.client.force_login(self.user)
        ensure_workflow_for_job(self.root_finished)
        ensure_workflow_for_job(self.active_child)
        ensure_workflow_for_job(self.error_job)

        response = self.client.get(reverse("jobs:job-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operacao por workflow persistido")
        self.assertContains(response, "bi-box-arrow-up-right")

    def test_workflow_table_has_no_leaked_template_comment(self):
        """N-77: Django's {# #} comment tag does not support multi-line content
        (a documented limitation) — a 5-line {# ... #} in workflow_table.html
        was rendered as literal visible text instead of being stripped. Fixed
        with {% comment %}...{% endcomment %}, which does support multi-line."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("jobs:job-list"))

        self.assertNotContains(response, "Bulk-selection state")
        self.assertNotContains(response, "syncFilteredCount() after each swap")

    def test_workflow_state_filter_uses_active_descendants(self):
        self.client.force_login(self.user)
        ensure_workflow_for_job(self.root_finished)
        active_workflow, _ = ensure_workflow_for_job(self.active_child)
        error_workflow, _ = ensure_workflow_for_job(self.error_job)

        response = self.client.get(reverse("jobs:job-list"), {"status": "active"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(active_workflow.pk)[:8], html=False)
        self.assertNotContains(response, str(error_workflow.pk)[:8], html=False)

    def test_workflow_list_ignores_expired_running_lease_for_active_badge(self):
        self.client.force_login(self.user)
        workflow, run = ensure_workflow_for_job(self.active_child)
        self.active_child.lease_expires_at = timezone.now() - timedelta(minutes=5)
        self.active_child.save(update_fields=["lease_expires_at", "updated_at"])
        scheduled = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            status="scheduled",
            scheduled_for=timezone.now() + timedelta(minutes=20),
            inputs_json='{"city_name":"Corinto"}',
            timeout_sec=1800,
            root_job_id=self.root_finished.pk,
            source_job_id=self.active_child.pk,
            workflow=workflow,
            workflow_run=run,
        )
        workflow.status = "waiting"
        workflow.next_scheduled_for = scheduled.scheduled_for
        workflow.save(update_fields=["status", "next_scheduled_for", "updated_at"])

        response = self.client.get(reverse("jobs:job-list"))

        self.assertEqual(response.status_code, 200)
        row = next(row for row in response.context["workflow_rows"] if row["workflow"].pk == workflow.pk)
        self.assertEqual(row["status"], "waiting")

    def test_technical_view_remains_available(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("jobs:job-list"), {"view": "tech"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Status tecnico")
        self.assertContains(response, "Acao")
        self.assertContains(response, "Cidade")

    def test_workflow_detail_page_renders_runs_and_jobs(self):
        self.client.force_login(self.user)
        root_workflow, root_run = ensure_workflow_for_job(self.root_finished)
        self.active_child.workflow = root_workflow
        self.active_child.workflow_run = root_run
        self.active_child.save(update_fields=["workflow", "workflow_run", "updated_at"])

        response = self.client.get(reverse("jobs:workflow-detail", args=[root_workflow.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "workflow-runs")
        self.assertContains(response, "workflow-logs")


class WorkflowBulkActionScopeTests(TestCase):
    """N-63: 'select all filtered' must only ever affect workflows matching the
    filters actually shown on screen, never every workflow in the system."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="bulk-ops-user", email="bulk-ops@example.com", password="secret123",
        )
        self.node = Node.objects.create(name="node-bulk")
        self.account = Account.objects.create(
            node=self.node, label="Conta Bulk", email="bulk@example.com", password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=9, server_id="s9-br",
            server_language="br", server_number=9, name="Bulk",
        )

        def _workflow(status, category):
            job = Job.objects.create(
                account=self.account, game_account=self.ga, node=self.node,
                action_code=1002, status=status, inputs_json="{}", timeout_sec=1800,
            )
            workflow, _ = ensure_workflow_for_job(job)
            workflow.status = status
            workflow.category = category
            workflow.save(update_fields=["status", "category", "updated_at"])
            return workflow

        self.problem_construction = _workflow("problem", "construction")
        self.finished_construction = _workflow("finished", "construction")
        self.finished_military = _workflow("finished", "military")

    def test_delete_all_with_status_filter_only_deletes_matching_workflows(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("jobs:workflow-bulk-delete"),
            {"delete_all": "true", "querystring": "status=problem"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Workflow.objects.filter(pk=self.problem_construction.pk).exists())
        self.assertTrue(Workflow.objects.filter(pk=self.finished_construction.pk).exists())
        self.assertTrue(Workflow.objects.filter(pk=self.finished_military.pk).exists())

    def test_delete_all_with_category_filter_only_deletes_matching_workflows(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("jobs:workflow-bulk-delete"),
            {"delete_all": "true", "querystring": "category=military"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Workflow.objects.filter(pk=self.problem_construction.pk).exists())
        self.assertTrue(Workflow.objects.filter(pk=self.finished_construction.pk).exists())
        self.assertFalse(Workflow.objects.filter(pk=self.finished_military.pk).exists())

    def test_delete_all_without_querystring_still_deletes_only_active_non_archived(self):
        """No filter applied (blank querystring) must still mean 'every workflow shown
        in the default (non-archived) view' — not literally every row in the table."""
        self.finished_military.archived_at = timezone.now()
        self.finished_military.save(update_fields=["archived_at"])
        self.client.force_login(self.user)

        response = self.client.post(reverse("jobs:workflow-bulk-delete"), {"delete_all": "true", "querystring": ""})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Workflow.objects.filter(pk=self.problem_construction.pk).exists())
        self.assertFalse(Workflow.objects.filter(pk=self.finished_construction.pk).exists())
        # Archived workflow was not part of the (default, non-archived) view — must survive.
        self.assertTrue(Workflow.objects.filter(pk=self.finished_military.pk).exists())

    def test_archive_all_with_status_filter_only_archives_matching_workflows(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("jobs:workflow-bulk-archive"),
            {"delete_all": "true", "querystring": "status=finished&category=military"},
        )

        self.assertEqual(response.status_code, 200)
        self.problem_construction.refresh_from_db()
        self.finished_construction.refresh_from_db()
        self.finished_military.refresh_from_db()
        self.assertIsNone(self.problem_construction.archived_at)
        self.assertIsNone(self.finished_construction.archived_at)
        self.assertIsNotNone(self.finished_military.archived_at)

    def test_explicit_selection_is_unaffected_by_current_filters(self):
        """Explicitly checked workflow_ids must always be deleted regardless of any
        filter — only 'select all filtered' is filter-scoped."""
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("jobs:workflow-bulk-delete"),
            {"workflow_ids[]": [str(self.finished_military.pk)]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Workflow.objects.filter(pk=self.finished_military.pk).exists())
        self.assertTrue(Workflow.objects.filter(pk=self.problem_construction.pk).exists())


class WorkflowFoundationServiceTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-workflow")
        self.account = Account.objects.create(
            node=self.node,
            label="Conta Workflow",
            email="workflow@example.com",
            password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=4,
            server_id="s4-br",
            server_language="br",
            server_number=4,
            name="Mileto",
        )

    def test_create_job_with_workflow_creates_linked_workflow_and_run(self):
        job = create_job_with_workflow(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            inputs={"city_name": "Mileto", "construction_plan_json": [{"city_name": "Mileto"}]},
            status="queued",
        )

        self.assertIsNotNone(job.workflow_id)

    def test_reconcile_marks_workflow_finished_when_chain_has_no_active_jobs(self):
        root = create_job_with_workflow(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            inputs={"construction_plan_json": [{"city_name": "Mileto"}]},
            status="finished",
        )
        child = create_job_with_workflow(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            inputs={"construction_plan_json": [{"city_name": "Mileto"}], "step": 2},
            status="scheduled",
            source_job=root,
            start_new_run=True,
            trigger_type="agent_reschedule",
        )
        child.status = "finished"
        child.save(update_fields=["status", "updated_at"])

        reconcile_workflow_for_job(child)

        child.workflow.refresh_from_db()
        child.workflow_run.refresh_from_db()
        self.assertEqual(child.workflow.status, "finished")
        self.assertEqual(child.workflow.active_run_id, child.workflow_run_id)
        self.assertEqual(child.workflow_run.status, "finished")


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class AgentRescheduleIdempotencyTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-agent")
        self.account = Account.objects.create(
            node=self.node,
            label="Conta Agent",
            email="agent@example.com",
            password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=5,
            server_id="s5-br",
            server_language="br",
            server_number=5,
            name="Argos",
        )

    def test_reschedule_does_not_confuse_monitor_child_with_remaining_followup(self):
        root = create_job_with_workflow(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=2,
            inputs={"from_city": "1", "to_city": "2", "marble": 352738},
            status="finished",
        )
        parent = create_job_with_workflow(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=2,
            inputs={"from_city": "1", "to_city": "2", "marble": 352738},
            status="scheduled",
            source_job=root,
            start_new_run=True,
            trigger_type="agent_reschedule",
        )
        monitor = create_job_with_workflow(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=2,
            inputs={
                "from_city": "1",
                "to_city": "2",
                "monitor_mode": "arrival_check",
                "sent_resources": {"wood": 0, "wine": 0, "marble": 90000, "crystal": 0, "sulfur": 0},
            },
            status="scheduled",
            source_job=parent,
            start_new_run=False,
        )

        response = self.client.post(
            reverse("agent-api:reschedule", args=[parent.pk]),
            data={"delay_seconds": 1262, "inputs": {"marble": 262738}},
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
            HTTP_X_AGENT_NODE_ID=str(self.node.pk),
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertNotEqual(str(monitor.pk), payload["new_job_id"])

        followup = Job.objects.get(pk=payload["new_job_id"])
        self.assertEqual(followup.source_job_id, parent.pk)
        self.assertEqual(followup.action_code, 2)
        self.assertIn('"marble": 262738', followup.inputs_json)
        self.assertIsNotNone(job.workflow_run_id)
        self.assertEqual(job.workflow.workflow_type, "construction_plan")
        self.assertEqual(job.workflow.status, "active")
        self.assertEqual(job.workflow.active_run_id, job.workflow_run_id)
        self.assertEqual(job.workflow_run.sequence, 1)
        self.assertEqual(job.root_job_id, None)

    def test_create_child_job_can_start_new_run_in_same_workflow(self):
        parent = create_job_with_workflow(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=2,
            inputs={"from_city": "1", "to_city": "2"},
            status="queued",
        )

        child = create_job_with_workflow(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=2,
            inputs={"from_city": "1", "to_city": "2", "retry": True},
            status="scheduled",
            source_job=parent,
            start_new_run=True,
            trigger_type="retry",
        )

        self.assertEqual(child.workflow_id, parent.workflow_id)
        self.assertNotEqual(child.workflow_run_id, parent.workflow_run_id)
        self.assertEqual(child.workflow_run.sequence, 2)
        self.assertEqual(str(child.root_job_id), str(parent.pk))
        self.assertEqual(child.workflow.next_scheduled_for, child.scheduled_for)

    def test_ensure_workflow_for_legacy_job_backfills_links(self):
        legacy = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=701,
            inputs_json='{"city_name":"Mileto"}',
            status="error",
            timeout_sec=1800,
        )

        workflow, workflow_run = ensure_workflow_for_job(legacy)
        legacy.refresh_from_db()

        self.assertIsInstance(workflow, Workflow)
        self.assertIsInstance(workflow_run, WorkflowRun)
        self.assertEqual(legacy.workflow_id, workflow.pk)
        self.assertEqual(legacy.workflow_run_id, workflow_run.pk)
        self.assertEqual(workflow.status, "problem")


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class AgentHeartbeatRecoveryTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(
            name="node-heartbeat",
            agent_last_seen_at=timezone.now(),
        )

    @patch("apps.accounts.api.agent.recover_stale_scheduled_jobs")
    @patch("apps.accounts.api.agent.recover_stale_running_jobs")
    def test_heartbeat_runs_recovery_even_while_node_is_online(self, running_mock, scheduled_mock):
        response = self.client.post(
            reverse("agent-accounts:heartbeat"),
            data={
                "node_id": str(self.node.pk),
                "agent_name": "ikabot-agent",
            },
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
            HTTP_X_AGENT_NODE_ID=str(self.node.pk),
        )

        self.assertEqual(response.status_code, 200)
        running_mock.assert_called_once()
        scheduled_mock.assert_called_once()

    @patch("apps.accounts.api.agent.recover_stale_scheduled_jobs")
    @patch("apps.accounts.api.agent.recover_stale_running_jobs")
    def test_heartbeat_recovers_after_long_offline_gap(self, running_mock, scheduled_mock):
        self.node.agent_last_seen_at = timezone.now() - timedelta(minutes=20)
        self.node.save(update_fields=["agent_last_seen_at", "updated_at"])

        response = self.client.post(
            reverse("agent-accounts:heartbeat"),
            data={
                "node_id": str(self.node.pk),
                "agent_name": "ikabot-agent",
            },
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
            HTTP_X_AGENT_NODE_ID=str(self.node.pk),
        )

        self.assertEqual(response.status_code, 200)
        running_mock.assert_called_once()
        scheduled_mock.assert_called_once()

    def test_recovery_cancels_orphan_when_active_followup_exists(self):
        account = Account.objects.create(
            node=self.node,
            label="Conta Recovery",
            email="recovery@example.com",
            password_enc="x",
        )
        ga = GameAccount.objects.create(
            account=account,
            lobby_account_id=8,
            server_id="s8-br",
            server_language="br",
            server_number=8,
            name="Recovery",
        )
        orphan = create_job_with_workflow(
            account=account,
            game_account=ga,
            node=self.node,
            action_code=701,
            inputs={"interval_minutes": 20},
            status="running",
        )
        orphan.started_at = timezone.now() - timedelta(hours=2)
        orphan.last_heartbeat_at = timezone.now() - timedelta(hours=2)
        orphan.lease_expires_at = timezone.now() - timedelta(hours=1)
        orphan.save(update_fields=["started_at", "last_heartbeat_at", "lease_expires_at", "updated_at"])
        create_job_with_workflow(
            account=account,
            game_account=ga,
            node=self.node,
            action_code=701,
            inputs={"interval_minutes": 20},
            status="scheduled",
            scheduled_for=timezone.now() + timedelta(minutes=20),
            source_job=orphan,
            start_new_run=True,
        )

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["recovered"], 1)
        self.assertEqual(result["requeued"], 0)
        self.assertEqual(orphan.status, "cancelled")
        self.assertEqual(
            Job.objects.filter(workflow=orphan.workflow, action_code=701, status="scheduled").count(),
            1,
        )


class ConstructionReservationLifecycleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="jobs-reservation-user",
            email="jobs-reservation@example.com",
            password="secret123",
        )
        self.node = Node.objects.create(name="node-reservation")
        self.account = Account.objects.create(
            node=self.node,
            label="Conta Reservation",
            email="reservation@example.com",
            password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=9,
            server_id="s9-br",
            server_language="br",
            server_number=9,
            name="LordDarkness",
        )
        self.root_job = Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            status="finished",
            inputs_json="{}",
            timeout_sec=1800,
        )
        self.workflow, self.run = ensure_workflow_for_job(self.root_job)
        Job.objects.create(
            account=self.account,
            game_account=self.ga,
            node=self.node,
            action_code=1002,
            status="scheduled",
            inputs_json="{}",
            timeout_sec=1800,
            root_job_id=self.root_job.pk,
            source_job_id=self.root_job.pk,
            workflow=self.workflow,
            workflow_run=self.run,
        )
        self.reservation = ConstructionResourceReservation.objects.create(
            job=self.root_job,
            account=self.account,
            game_account=self.ga,
            city_id="66480",
            city_name="MM1",
            resource="marble",
            reserved_local_amount=100,
            shortfall_amount=50,
            status="active",
        )

    def test_workflow_delete_removes_its_jobs_and_reservations(self):
        """N-59: deleting a workflow must actually delete its jobs too — they
        used to only get cancelled (if active) and orphaned (workflow set to
        NULL), which left them behind forever in the tech view. The
        reservation is cascaded away with its job, not merely cancelled."""
        self.client.force_login(self.user)
        root_job_id = self.root_job.pk
        reservation_id = self.reservation.pk

        response = self.client.post(
            reverse("jobs:workflow-action", args=[self.workflow.pk]),
            {"workflow_action": "delete"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Workflow.objects.filter(pk=self.workflow.pk).exists())
        self.assertFalse(Job.objects.filter(workflow_id=self.workflow.pk).exists())
        self.assertFalse(Job.objects.filter(pk=root_job_id).exists())
        self.assertFalse(ConstructionResourceReservation.objects.filter(pk=reservation_id).exists())

    def test_workflow_cancel_cancels_active_construction_reservations(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("jobs:workflow-action", args=[self.workflow.pk]),
            {"workflow_action": "cancel"},
        )

        self.assertEqual(response.status_code, 200)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "cancelled")
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "cancelled")


class JobFormNormalFlowNestingTests(TestCase):
    """Regression guard for the preset-form fix in jobs/forms/_generic_fields.html
    (the trailing </div> is now guarded by the same `not no_footer` as the
    </form> line above it, instead of always rendering). In this normal
    (non-preset) modal flow no_footer is unset/falsy, so both lines must still
    render exactly as before — footer and submit button stay inside the form."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="form-nesting-user", email="form-nesting@example.com", password="secret123",
        )
        self.node = Node.objects.create(name="node-form-nesting")
        self.account = Account.objects.create(
            node=self.node, label="Conta Nesting", email="nesting@example.com", password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=1, server_id="s1-br",
            server_language="br", server_number=1, name="Test",
        )

    def test_submit_button_stays_inside_the_form_for_ac3(self):
        self.client.force_login(self.user)
        with patch("apps.jobs.views.create._get_cities", return_value=[{"id": "11", "name": "Alpha", "buildings": []}]):
            response = self.client.get(reverse("jobs:job-form"), {"ga": str(self.ga.pk), "action": "3"})

        html = response.content.decode()
        form_start = html.index("<form")
        form_end = html.index("</form>", form_start)
        submit_pos = html.index('<button type="submit"')

        self.assertTrue(form_start < submit_pos < form_end)


class MoveForcesFormTests(TestCase):
    """N-80: ac=1202 (Mover Forcas) — adds an 'Ambos' scope option, filters the
    origin city list by actual troop/fleet presence instead of just barracks/
    shipyard presence, and keeps the destination list showing every city."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="move-forces-user", email="move-forces@example.com", password="secret123",
        )
        self.node = Node.objects.create(name="node-move-forces")
        self.account = Account.objects.create(
            node=self.node, label="Conta MF", email="mf@example.com", password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=1, server_id="s1-br",
            server_language="br", server_number=1, name="Test",
        )
        self.cities = [
            {"id": "11", "name": "Alpha", "buildings": [
                {"building": "barracks", "position": 8},
                {"building": "shipyard", "position": 1},
            ]},
        ]

    def _render(self, action_code):
        self.client.force_login(self.user)
        with patch("apps.jobs.views.create._get_cities", return_value=self.cities):
            response = self.client.get(
                reverse("jobs:job-form"), {"ga": str(self.ga.pk), "action": str(action_code)},
            )
        return response.content.decode()

    def test_scope_field_offers_ambos_option_for_move_forces(self):
        from apps.jobs.forms import JobCreateForm

        form = JobCreateForm(action_code=1202, game_account=self.ga, cities=self.cities)
        self.assertIn(("both", "Ambos"), list(form.fields["scope"].choices))

    def test_ambos_toggle_renders_only_for_move_forces_not_training(self):
        html_1202 = self._render(1202)
        self.assertIn("buildingType='both'", html_1202)

        html_1005 = self._render(1005)
        self.assertNotIn("buildingType='both'", html_1005)

    def test_origin_cities_filtered_by_unit_presence_helper_is_wired_up(self):
        html = self._render(1202)
        self.assertIn("originCitiesWithUnits(buildingType, fromQuery)", html)
        self.assertIn("cityHasTroops(c)", html)
        self.assertIn("cityHasFleet(c)", html)

    def test_destination_helper_does_not_filter_by_buildings(self):
        html = self._render(1202)
        fn_start = html.index("destinationCities(query")
        fn_body_start = html.index("{", fn_start)
        fn_body_end = html.index("}", fn_body_start)
        fn_body = html[fn_body_start:fn_body_end]
        self.assertNotIn("has_barracks", fn_body)
        self.assertNotIn("has_shipyard", fn_body)
        self.assertIn(
            "destinationCities(toQuery).filter(c => !originsList().includes(String(c.id)))",
            html,
        )

    def test_units_panel_shows_troops_and_fleet_together_when_scope_is_ambos(self):
        html = self._render(1202)
        self.assertIn("(buildingType === 'troops' || buildingType === 'both')", html)
        self.assertIn("(buildingType === 'fleet' || buildingType === 'both')", html)

    def test_origin_and_destination_grids_have_padding_so_the_ring_is_not_clipped(self):
        """N-80 follow-up: the selection ring (ring-2, a box-shadow) needs room to
        render inside the scrollable grid container, or its own overflow clips it
        — same as the other city grids in this file (ac=1005), which all carry
        p-0.5 alongside overflow-y-auto for this exact reason."""
        html = self._render(1202)
        for marker in ('name="from_city_id"', 'name="to_city_id"'):
            idx = html.index(marker)
            grid_start = html.index('<div class="grid', idx)
            grid_tag_end = html.index('>', grid_start)
            grid_class = html[grid_start:grid_tag_end]
            self.assertIn('overflow-y-auto', grid_class)
            self.assertIn('p-0.5', grid_class)

    def test_no_leaked_multiline_template_comment_in_units_section(self):
        """N-77-style regression: a multi-line {# #} tag leaks as literal text."""
        html = self._render(1202)
        self.assertNotIn('{#', html)
        self.assertNotIn('#}', html)


class MultiOriginMoveForcesTests(TestCase):
    """N-81: move forces from several origin cities and/or to several
    destination cities in a single submission. Each origin moves everything
    it has of the selected scope, split equally across destinations when
    there is more than one (multi_move_pairs_json, built client-side from
    the snapshot) -- no agent/runner changes needed, StationUnitsRunner
    already processes each job's from_city_id/to_city_id independently."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="multi-origin-user", email="multi-origin@example.com", password="secret123",
        )
        self.node = Node.objects.create(name="node-multi-origin")
        self.account = Account.objects.create(
            node=self.node, label="Conta MO", email="mo@example.com", password_enc="x",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=1, server_id="s1-br",
            server_language="br", server_number=1, name="Test",
        )
        self.cities = [
            {"id": "11", "name": "Alpha", "buildings": []},
            {"id": "12", "name": "Beta", "buildings": []},
            {"id": "13", "name": "Gamma", "buildings": []},
        ]

    def test_fan_out_creates_one_job_per_origin_destination_pair(self):
        import json as _json

        count = JobSubmitView()._create_jobs(
            self.ga,
            1202,
            {},
            {
                "scope": "troops",
                "_multi_move_pairs": {
                    "11": {"13": {"301": 5}},
                    "12": {"13": {"301": 3}, "": {}},
                },
            },
            self.cities,
        )

        self.assertEqual(count, 2)
        jobs = Job.objects.filter(account=self.account, action_code=1202).order_by("created_at")
        self.assertEqual(jobs.count(), 2)
        by_from_city = {}
        for job in jobs:
            job_inputs = _json.loads(job.inputs_json)
            by_from_city[job_inputs["from_city_id"]] = job_inputs

        self.assertEqual(set(by_from_city.keys()), {"11", "12"})
        self.assertEqual(by_from_city["11"]["units"], {"301": 5})
        self.assertEqual(by_from_city["11"]["to_city_id"], "13")
        self.assertEqual(by_from_city["11"]["scope"], "troops")
        self.assertEqual(by_from_city["12"]["units"], {"301": 3})
        self.assertEqual(by_from_city["12"]["to_city_id"], "13")

    def test_fan_out_splits_one_origin_across_several_destinations(self):
        """1 origin -> 3 destinations: the frontend is responsible for the
        equal split (remainder handed out unit-by-unit); the backend just
        fans out whatever pairs it receives, one job each."""
        import json as _json

        count = JobSubmitView()._create_jobs(
            self.ga, 1202, {},
            {
                "scope": "troops",
                "_multi_move_pairs": {"11": {"12": {"301": 4}, "13": {"301": 3}}},
            },
            self.cities,
        )

        self.assertEqual(count, 2)
        jobs = Job.objects.filter(account=self.account, action_code=1202).order_by("created_at")
        units_by_dest = {}
        for job in jobs:
            job_inputs = _json.loads(job.inputs_json)
            self.assertEqual(job_inputs["from_city_id"], "11")
            units_by_dest[job_inputs["to_city_id"]] = job_inputs["units"]
        self.assertEqual(units_by_dest, {"12": {"301": 4}, "13": {"301": 3}})

    def test_normalize_parses_city_id_lists_and_pairs_json_from_post(self):
        from django.test import RequestFactory

        rf = RequestFactory()
        request = rf.post("/jobs/new/submit/", data={
            "from_city_ids": ["11", "12"],
            "to_city_ids": ["13"],
            "multi_move_pairs_json": '{"11": {"13": {"301": 5}}, "12": {"13": {"301": 3}}}',
            "scope": "troops",
        })
        normalized = JobSubmitView._normalize_station_units_inputs({}, request)
        self.assertEqual(normalized["_multi_move_pairs"], {"11": {"13": {"301": 5}}, "12": {"13": {"301": 3}}})

    def test_normalize_ignores_single_city_on_both_sides(self):
        """Plain single-origin/single-destination submissions must not
        accidentally trigger the fan-out path."""
        from django.test import RequestFactory

        rf = RequestFactory()
        request = rf.post("/jobs/new/submit/", data={
            "from_city_ids": ["11"], "to_city_ids": ["13"], "scope": "troops",
        })
        normalized = JobSubmitView._normalize_station_units_inputs({}, request)
        self.assertNotIn("_multi_move_pairs", normalized)

    def test_multi_origin_and_multi_destination_toggles_render_for_move_forces(self):
        self.client.force_login(self.user)
        with patch("apps.jobs.views.create._get_cities", return_value=self.cities):
            response = self.client.get(
                reverse("jobs:job-form"), {"ga": str(self.ga.pk), "action": "1202"},
            )
        html = response.content.decode()
        self.assertIn("toggleMultiOrigin()", html)
        self.assertIn("toggleMultiDestination()", html)
        self.assertIn('name="multi_move_pairs_json"', html)
        self.assertIn('name="from_city_ids"', html)
        self.assertIn('name="to_city_ids"', html)
