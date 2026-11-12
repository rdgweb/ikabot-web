import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshotHistory, AccountHistoryMetric
from apps.game.services.kpi_history import build_history_map, PERIODS
from apps.jobs.models import Job, JobLog, Workflow
from apps.orgs.scope import activate, deactivate
from .models import AppSetting, RetentionRun, JobRetentionReceipt
from .retention import POLICY_KEY, DEFAULT_POLICY, get_policy, preview, process_batch


class RetentionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("retention-admin", is_staff=True)
        self.node = Node.objects.create(name="retention-test")
        self.account = Account.objects.create(node=self.node, label="test", email="test@example.org", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s1-br",
                                             server_language="br", server_number=1, name="Test")
        self.now = timezone.now()
        self.policy = {**DEFAULT_POLICY, "enabled": True}
        self.save_policy()

    def save_policy(self):
        AppSetting.objects.update_or_create(pk=POLICY_KEY, defaults={"value": json.dumps(self.policy)})

    def snapshot(self, days, gold=500):
        return AccountSnapshotHistory.objects.create(account=self.account, game_account=self.ga,
            captured_at=self.now-timedelta(days=days), base_snapshot={"gold": gold, "income": 12},
            cities=[{"wood": 20, "wine": 30}], military={"troops": {"Hoplita": 10}, "fleet": {"Trireme": 4}})

    def job(self, **values):
        fields = dict(account=self.account, game_account=self.ga, node=self.node, action_code=1,
                      status="finished", finished_at=self.now-timedelta(days=120), archived_at=self.now-timedelta(days=100),
                      inputs_json='{"important": "keep"}', progress_json='{"large_result": "old"}')
        fields.update(values)
        job = Job.objects.create(**fields)
        log = JobLog.objects.create(job=job, message="old log")
        JobLog.objects.filter(pk=log.pk).update(created_at=self.now-timedelta(days=120))
        return job

    def finish(self, run):
        for index in range(20):
            run = process_batch(run.pk, self.user, start=index == 0, backup=True)
            if run.status == "done":
                return run
        self.fail("maintenance did not finish")

    def test_graphs_identical_after_compaction_for_every_period(self):
        old = self.snapshot(120)
        self.snapshot(45, 600)
        recent = self.snapshot(1, 700)
        before = {period: build_history_map([self.ga.pk], period, now=self.now) for period in PERIODS}
        run = self.finish(preview(self.user))
        self.assertEqual(run.processed["snapshots"], 2)
        self.assertFalse(AccountSnapshotHistory.objects.filter(pk=old.pk).exists())
        self.assertTrue(AccountSnapshotHistory.objects.filter(pk=recent.pk).exists())
        self.assertEqual(AccountHistoryMetric.objects.count(), 2)
        for period in PERIODS:
            self.assertEqual(build_history_map([self.ga.pk], period, now=self.now), before[period])

    def test_preview_and_disabled_policy_do_not_remove_data(self):
        self.snapshot(100)
        self.policy["enabled"] = False
        self.save_policy()
        run = preview(self.user)
        self.assertEqual(run.planned["snapshots"], 1)
        with self.assertRaises(ValueError):
            process_batch(run.pk, self.user, start=True, backup=True)
        self.assertEqual(AccountSnapshotHistory.objects.count(), 1)

    def test_backup_and_unchanged_policy_required(self):
        self.snapshot(100)
        run = preview(self.user)
        with self.assertRaises(ValueError):
            process_batch(run.pk, self.user, start=True)
        self.policy["snapshot_days"] = 60
        self.save_policy()
        with self.assertRaises(ValueError):
            process_batch(run.pk, self.user, start=True, backup=True)
        self.assertEqual(AccountSnapshotHistory.objects.count(), 1)

    def test_jobs_keep_inputs_and_identity_and_protect_live_dependencies(self):
        old = self.job()
        active = self.job(status="running")
        unarchived = self.job(archived_at=None)
        parent = self.job()
        child = self.job(status="queued", source_job_id=parent.pk)
        workflow = Workflow.objects.create(account=self.account, node=self.node, workflow_type="test", status="active")
        live_workflow_job = self.job(workflow=workflow)
        run = self.finish(preview(self.user))
        old.refresh_from_db()
        self.assertEqual(old.inputs_json, '{"important": "keep"}')
        self.assertTrue(json.loads(old.progress_json)["retention_compacted"])
        self.assertFalse(old.logs.exists())
        for job in (active, unarchived, parent, child, live_workflow_job):
            job.refresh_from_db()
            self.assertEqual(job.progress_json, '{"large_result": "old"}')
            self.assertTrue(job.logs.exists())
        self.assertEqual(run.processed["jobs"], 1)
        self.assertEqual(JobRetentionReceipt.objects.count(), 1)
        self.assertEqual(process_batch(run.pk, self.user).processed, run.processed)

    def test_bounded_resumable_batches_and_conflicting_metric_rollback(self):
        first, second = self.snapshot(100), self.snapshot(110)
        run = preview(self.user)
        with patch("apps.settings_app.retention.BATCH_SIZE", 1):
            process_batch(run.pk, self.user, start=True, backup=True)
        self.assertEqual(AccountSnapshotHistory.objects.count(), 1)
        remaining = AccountSnapshotHistory.objects.get()
        AccountHistoryMetric.objects.create(pk=remaining.pk, account=self.account, game_account=self.ga,
                                           captured_at=remaining.captured_at, metrics={"gold": -1})
        with self.assertRaises(ValueError):
            process_batch(run.pk, self.user)
        self.assertTrue(AccountSnapshotHistory.objects.filter(pk=remaining.pk).exists())

    def test_customer_cannot_read_compact_metrics_of_another_scope(self):
        self.snapshot(100)
        self.finish(preview(self.user))
        token = activate(None, deny=True)
        try:
            self.assertFalse(AccountHistoryMetric.objects.exists())
        finally:
            deactivate(token)

    def test_settings_save_does_not_execute_and_get_never_runs_a_batch(self):
        self.snapshot(100)
        self.client.force_login(self.user)
        response = self.client.post(reverse("settings_app:retention-save"),
                                    {"enabled": "on", "snapshot_days": 45, "job_days": 120, "log_days": 60})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_policy()["snapshot_days"], 45)
        response = self.client.post(reverse("settings_app:retention-preview"))
        self.assertEqual(response.status_code, 302)
        run = RetentionRun.objects.get()
        url = reverse("settings_app:retention-run", args=[run.pk])
        self.assertContains(self.client.get(url), "Confirmar e executar")
        self.assertEqual(AccountSnapshotHistory.objects.count(), 1)
        self.client.post(url, {"action": "start"})  # no backup confirmation
        self.assertEqual(AccountSnapshotHistory.objects.count(), 1)
        self.assertEqual(self.client.post(reverse("settings_app:retention-save"), {"snapshot_days": 0}).status_code, 400)

    def test_non_staff_and_other_owners_cannot_execute(self):
        run = preview(self.user)
        url = reverse("settings_app:retention-run", args=[run.pk])
        other = get_user_model().objects.create_user("other-staff", is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.client.post(url, {"action": "start", "backup": "yes"}).status_code, 404)
        other.is_staff = False
        other.save()
        self.assertEqual(self.client.post(reverse("settings_app:retention-preview")).status_code, 404)
