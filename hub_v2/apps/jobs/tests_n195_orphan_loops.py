"""N-195: an orphaned run of a loop (agent restarted mid-run) resumes instead of ending the chain."""

import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node

from .models import Job, JobLog, Workflow
from .services.recovery import recover_stale_running_jobs
from .services.workflows import create_job_with_workflow

MAINTAIN_TROOPS = {"city_id": "", "building_type": "troops", "mode": "maintain", "maintain_scope": "selected_uniform",
                   "city_ids": ["101", "102"], "recheck_minutes": 120, "units": {"303": 50}}
MAINTAIN_FLEET = dict(MAINTAIN_TROOPS, building_type="fleet", units={"210": 10})


class OrphanedLoopRecoveryTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="node-n195", agent_last_seen_at=timezone.now())
        self.account = Account.objects.create(node=self.node, label="Conta N195", email="n195@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=195, server_id="s78-br",
                                             server_language="br", server_number=78, name="Executor")

    def _job(self, action_code, inputs, *, status="scheduled", workflow=None, source_job=None):
        job = create_job_with_workflow(
            account=self.account, game_account=self.ga, node=self.node, action_code=action_code, inputs=inputs,
            status=status, scheduled_for=timezone.now() + timedelta(hours=1) if status == "scheduled" else None,
            explicit_workflow=workflow, source_job=source_job,
        )
        return job

    def _orphan(self, action_code, inputs, **kwargs):
        job = self._job(action_code, inputs, status="running", **kwargs)
        stale = timezone.now() - timedelta(hours=2)
        job.started_at = stale
        job.last_heartbeat_at = stale
        job.lease_expires_at = timezone.now() - timedelta(hours=1)
        job.save(update_fields=["started_at", "last_heartbeat_at", "lease_expires_at", "updated_at"])
        return job

    def _active_children(self, job):
        return Job.objects.filter(workflow=job.workflow, action_code=job.action_code,
                                  status__in=("queued", "scheduled", "running")).exclude(pk=job.pk)

    def test_orphaned_maintain_troops_resumes_in_the_same_workflow(self):
        orphan = self._orphan(1005, dict(MAINTAIN_TROOPS, __pending_patch={"recheck_minutes": 60}))

        before = timezone.now()
        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["resumed"], 1)
        self.assertEqual(result["marked_error"], 0)
        self.assertEqual(orphan.status, "cancelled")
        self.assertEqual(orphan.exit_code, 98)
        children = list(self._active_children(orphan))
        self.assertEqual(len(children), 1)
        child = children[0]
        self.assertEqual(child.status, "scheduled")
        self.assertEqual(child.workflow_id, orphan.workflow_id)
        self.assertEqual(child.source_job_id, orphan.pk)
        self.assertEqual(child.workflow_run.trigger_type, "orphan_resume")
        self.assertGreaterEqual(child.scheduled_for, before + timedelta(seconds=290))
        self.assertLessEqual(child.scheduled_for, timezone.now() + timedelta(seconds=310))
        inputs = json.loads(child.inputs_json)
        self.assertEqual(inputs["building_type"], "troops")
        self.assertEqual(inputs["units"], {"303": 50})
        self.assertEqual(inputs["recheck_minutes"], 60)  # the edit made while it ran
        self.assertNotIn("__pending_patch", inputs)
        log = JobLog.objects.filter(job=orphan).order_by("-created_at").first()
        self.assertIn("Execucao orfa recuperada", log.message)
        self.assertIn(str(child.pk), log.message)
        self.assertTrue(JobLog.objects.filter(job=child, message__contains="Retomada do loop").exists())
        self.assertNotEqual(Workflow.objects.get(pk=orphan.workflow_id).status, "finished")

    def test_orphaned_barbarian_raid_resumes(self):
        orphan = self._orphan(19, {"city_id": "101", "island_ids": ["55"], "recheck_minutes": 60})

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["resumed"], 1)
        self.assertEqual(orphan.status, "cancelled")
        self.assertEqual(self._active_children(orphan).count(), 1)

    def test_non_recurring_action_still_ends_as_error(self):
        orphan = self._orphan(1202, {"from_city_id": "101", "to_city_id": "102", "units": {"303": 5}})

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["resumed"], 0)
        self.assertEqual(result["marked_error"], 1)
        self.assertEqual(orphan.status, "error")
        self.assertEqual(self._active_children(orphan).count(), 0)
        self.assertTrue(JobLog.objects.filter(job=orphan, message__contains="marcada como erro").exists())

    def test_one_shot_training_is_not_a_loop(self):
        orphan = self._orphan(1005, {"city_id": "101", "building_type": "troops", "mode": "once", "units": {"303": 50}})

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["resumed"], 0)
        self.assertEqual(orphan.status, "error")
        self.assertEqual(self._active_children(orphan).count(), 0)

    def test_no_duplicate_when_the_loop_already_has_its_next_job(self):
        orphan = self._orphan(1005, MAINTAIN_TROOPS)
        self._job(1005, MAINTAIN_TROOPS, workflow=orphan.workflow)

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["resumed"], 0)
        self.assertEqual(orphan.status, "cancelled")
        self.assertEqual(self._active_children(orphan).count(), 1)

    def test_no_duplicate_when_the_next_job_has_edited_targets(self):
        orphan = self._orphan(1005, MAINTAIN_TROOPS)
        self._job(1005, dict(MAINTAIN_TROOPS, city_ids=["101"]), workflow=orphan.workflow, source_job=orphan)

        result = recover_stale_running_jobs(node=self.node)

        self.assertEqual(result["resumed"], 0)
        self.assertEqual(self._active_children(orphan).count(), 1)

    def test_fleet_job_in_the_same_workflow_does_not_block_troops(self):
        orphan = self._orphan(1005, MAINTAIN_TROOPS)
        fleet = self._job(1005, MAINTAIN_FLEET, workflow=orphan.workflow)

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["resumed"], 1)
        kinds = sorted(json.loads(job.inputs_json)["building_type"] for job in self._active_children(orphan))
        self.assertEqual(kinds, ["fleet", "troops"])
        fleet.refresh_from_db()
        self.assertEqual(fleet.status, "scheduled")

    def test_donation_of_another_city_does_not_block_the_orphaned_city(self):
        donation = {"donation_type": "resource", "donation_method": "2", "method_value": 50, "interval_minutes": 1440}
        orphan = self._orphan(1006, dict(donation, city_id="101"))
        other = self._job(1006, dict(donation, city_id="102"))
        self.assertEqual(other.workflow_id, orphan.workflow_id)  # one donation workflow per account

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["requeued"], 1)
        cities = sorted(json.loads(job.inputs_json)["city_id"] for job in self._active_children(orphan))
        self.assertEqual(cities, ["101", "102"])

    def test_donation_of_the_same_city_is_not_duplicated(self):
        donation = {"donation_type": "resource", "donation_method": "2", "method_value": 50, "interval_minutes": 1440}
        orphan = self._orphan(1006, dict(donation, city_id="101"))
        self._job(1006, dict(donation, city_id="101"))

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["requeued"], 0)
        self.assertEqual(orphan.status, "cancelled")
        self.assertEqual(self._active_children(orphan).count(), 1)

    def test_paused_workflow_is_not_resumed(self):
        orphan = self._orphan(1005, MAINTAIN_TROOPS)
        Workflow.objects.filter(pk=orphan.workflow_id).update(status="paused")

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["resumed"], 0)
        self.assertEqual(orphan.status, "error")
        self.assertEqual(self._active_children(orphan).count(), 0)

    def test_stops_after_three_orphaned_resumes_in_a_row(self):
        orphan = self._orphan(1005, MAINTAIN_TROOPS)
        for _ in range(3):
            self.assertEqual(recover_stale_running_jobs(node=self.node)["resumed"], 1)
            child = self._active_children(orphan).get()
            stale = timezone.now() - timedelta(hours=2)
            Job.objects.filter(pk=child.pk).update(status="running", started_at=stale, last_heartbeat_at=stale,
                                                   lease_expires_at=timezone.now() - timedelta(hours=1))
            orphan = child

        result = recover_stale_running_jobs(node=self.node)

        orphan.refresh_from_db()
        self.assertEqual(result["resumed"], 0)
        self.assertEqual(orphan.status, "error")
        self.assertTrue(JobLog.objects.filter(job=orphan, message__contains="loop nao retomado").exists())
