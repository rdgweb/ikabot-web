"""N-135: a paused workflow stays paused — a follow-up the system creates while it finishes in-flight work does
not turn it back to active (and later "finished") — and resuming restarts the workflow's own action."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node

from .models import Job, Workflow
from .services.workflow_status import effective_status, job_facts
from .services.workflows import create_job_with_workflow


class PausedWorkflowTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente", transport="https")
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Executor")
        self.plan = self._job(1002, "running", trigger="manual")
        self.workflow = self.plan.workflow
        self.buy = self._job(801, "running", source=self.plan, trigger="agent_spawn")
        Workflow.objects.filter(pk=self.workflow.pk).update(status="paused")
        self.workflow.refresh_from_db()

    def _job(self, action_code, status, source=None, trigger="agent_spawn"):
        return create_job_with_workflow(account=self.account, game_account=self.ga, node=self.node,
                                        action_code=action_code, inputs={"plan": action_code}, status=status,
                                        source_job=source, start_new_run=source is None, trigger_type=trigger)

    def test_a_follow_up_created_while_paused_keeps_the_pause(self):
        self._job(801, "scheduled", source=self.buy, trigger="agent_reschedule")
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "paused")
        facts = job_facts([self.workflow.pk]).get(self.workflow.pk)
        self.assertEqual(effective_status(self.workflow, facts), "paused")

    def test_what_the_user_starts_takes_it_out_of_the_pause(self):
        self._job(801, "queued", source=self.buy, trigger="manual_retry")
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "active")

    def test_resume_restarts_the_plan_not_the_last_market_follow_up(self):
        Job.objects.filter(workflow=self.workflow).update(status="finished")
        last_buy = self._job(801, "finished", source=self.buy, trigger="agent_reschedule")
        self.assertGreater(last_buy.created_at, self.plan.created_at)
        user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(user)
        response = self.client.post(f"/jobs/workflows/{self.workflow.pk}/action/", {"workflow_action": "resume"})
        self.assertLess(response.status_code, 400)
        resumed = Job.objects.filter(workflow=self.workflow).order_by("-created_at").first()
        self.assertEqual((resumed.action_code, resumed.source_job_id, resumed.status), (1002, self.plan.pk, "scheduled"))
        self.workflow.refresh_from_db()
        self.assertEqual(self.workflow.status, "waiting")
