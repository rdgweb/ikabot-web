from django.contrib.auth import get_user_model
from django.test import override_settings
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node

from .models import ConstructionResourceReservation, Job, Workflow, WorkflowRun
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

    def test_job_list_does_not_render_chain_history_rows_initially(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("jobs:job-list"), {"view": "tech"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 ciclo(s) anteriores")
        self.assertContains(response, f"job-chain-history-body-{self.root.pk}")
        self.assertContains(response, f'href="{reverse("jobs:job-detail", args=[self.child_2.pk])}"')
        self.assertNotContains(response, f'href="{reverse("jobs:job-detail", args=[self.child_1.pk])}"')
        self.assertNotContains(response, "Historico da cadeia")

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

    def test_default_jobs_page_renders_operational_workflow_view(self):
        self.client.force_login(self.user)
        ensure_workflow_for_job(self.root_finished)
        ensure_workflow_for_job(self.active_child)
        ensure_workflow_for_job(self.error_job)

        response = self.client.get(reverse("jobs:job-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operacao por workflow persistido")
        self.assertContains(response, "bi-box-arrow-up-right")

    def test_workflow_state_filter_uses_active_descendants(self):
        self.client.force_login(self.user)
        ensure_workflow_for_job(self.root_finished)
        active_workflow, _ = ensure_workflow_for_job(self.active_child)
        error_workflow, _ = ensure_workflow_for_job(self.error_job)

        response = self.client.get(reverse("jobs:job-list"), {"status": "active"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(active_workflow.pk)[:8], html=False)
        self.assertNotContains(response, str(error_workflow.pk)[:8], html=False)

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

    def test_workflow_delete_cancels_active_construction_reservations(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("jobs:workflow-action", args=[self.workflow.pk]),
            {"workflow_action": "delete"},
        )

        self.assertEqual(response.status_code, 200)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, "cancelled")
        self.assertFalse(Workflow.objects.filter(pk=self.workflow.pk).exists())

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
