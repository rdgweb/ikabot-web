import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshotHistory, AccountHistoryMetric
from apps.game.services.kpi_history import build_history_map, PERIODS
from apps.jobs.models import Job, JobLog, Workflow
from apps.orgs.scope import activate, deactivate
from .models import AppSetting, RetentionRun, JobRetentionReceipt
from .retention import POLICY_KEY, DEFAULT_POLICY, get_policy, preview, process_batch


class RetentionData(TestCase):
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


class RetentionTests(RetentionData):
    def test_failed_job_batch_rolls_back_result_receipt_and_counters(self):
        from django.db import OperationalError
        job = self.job()
        run = preview(self.user)
        with patch.object(JobRetentionReceipt.objects, "bulk_create", side_effect=OperationalError(1020, "changed row")):
            with self.assertRaises(OperationalError):
                process_batch(run.pk, self.user, start=True, backup=True)
        job.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(job.progress_json, '{"large_result": "old"}')
        self.assertEqual(run.status, "preview")
        self.assertEqual(run.processed, {})
        self.assertFalse(JobRetentionReceipt.objects.exists())
        self.assertEqual(job.logs.count(), 1)

    def test_changed_window_does_not_skip_remaining_jobs(self):
        from django.db import OperationalError
        from . import retention
        jobs = [self.job(), self.job()]
        run = preview(self.user)
        lock = retention._lock_job_rows
        def changed(ids):
            Job.objects.filter(pk__in=ids).update(status="running")
            return lock(ids)
        with patch.object(retention, "JOB_BATCH", 1), patch.object(retention, "_lock_job_rows", changed):
            with self.assertRaises(OperationalError):
                process_batch(run.pk, self.user, start=True, backup=True)
        # The failed transaction also undoes this test's simulated change.
        self.assertFalse(JobRetentionReceipt.objects.exists())
        run = self.finish(run)
        self.assertEqual(run.processed["jobs"], 2)

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

    def test_old_steps_of_a_live_recurring_chain_are_trimmed(self):
        # N-181: a recurring job leaves one finished step per cycle inside an active workflow; the old ones go
        workflow = Workflow.objects.create(account=self.account, node=self.node, workflow_type="test", status="active")
        root = self.job(workflow=workflow)
        steps = []
        previous = root
        for _ in range(3):
            previous = self.job(workflow=workflow, root_job_id=root.pk, source_job_id=previous.pk)
            steps.append(previous)
        live = self.job(workflow=workflow, status="scheduled", root_job_id=root.pk, source_job_id=previous.pk,
                        finished_at=None, archived_at=None)
        # Distinct instants model successive cycles even on hosts with coarse clock resolution.
        for index, job in enumerate([root, *steps, live]):
            Job.objects.filter(pk=job.pk).update(created_at=self.now - timedelta(minutes=5-index))
        run = self.finish(preview(self.user))
        for job in steps[:2]:
            job.refresh_from_db()
            self.assertTrue(json.loads(job.progress_json)["retention_compacted"])
            self.assertFalse(job.logs.exists())
        for job in (root, steps[2], live):                     # the chain's first job and the step before the live one
            job.refresh_from_db()
            self.assertEqual(job.progress_json, '{"large_result": "old"}')
            self.assertTrue(job.logs.exists())
        self.assertEqual(run.processed["jobs"], 2)

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


@patch("apps.settings_app.retention.BATCH_PAUSE", 0)
@patch("apps.settings_app.retention._spawn", lambda work: work())
class RetentionInBackgroundTests(RetentionData):
    """N-196: after the confirmation the server goes through the batches; the page may be closed."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()

    def test_confirming_runs_every_batch_on_the_server(self):
        for days in (100, 110, 120):
            self.snapshot(days)
        self.client.force_login(self.user)
        run = preview(self.user)
        url = reverse("settings_app:retention-run", args=[run.pk])
        with patch("apps.settings_app.retention.BATCH_SIZE", 1):
            response = self.client.post(url, {"action": "start", "backup": "yes", "scope": "installation"})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        run.refresh_from_db()
        self.assertEqual((run.status, run.processed["snapshots"]), ("done", 3))
        self.assertEqual(AccountSnapshotHistory.objects.count(), 0)
        self.assertContains(self.client.get(url), "Retenção concluída")

    def test_pause_stops_and_resume_goes_on_from_where_it_stopped(self):
        from .retention import pause, resume, run_in_background

        for days in (100, 110):
            self.snapshot(days)
        run = preview(self.user)
        with patch("apps.settings_app.retention.BATCH_SIZE", 1):
            with patch("apps.settings_app.retention._spawn", lambda work: None):
                run = process_batch(run.pk, self.user, start=True, backup=True)
                run_in_background(run.pk)               # a runner that will never run: the lock is taken
            from django.core.cache import cache

            cache.clear()
            self.assertEqual(pause(run.pk).status, "paused")
            run_in_background(run.pk)                   # paused: the runner stops at once
            self.assertEqual(AccountSnapshotHistory.objects.count(), 1)
            self.client.force_login(self.user)
            self.assertContains(self.client.get(reverse("settings_app:retention-run", args=[run.pk])), "Retomar")
            self.assertEqual(resume(run.pk).status, "running")
        run.refresh_from_db()
        self.assertEqual((run.status, AccountSnapshotHistory.objects.count()), ("done", 0))

    def test_a_run_whose_process_is_gone_is_picked_up_again(self):
        from django.core.cache import cache

        from .retention import resume, resume_stalled

        self.snapshot(100)
        with patch("apps.settings_app.retention.BATCH_SIZE", 1):
            self.snapshot(110)
            run = preview(self.user)
            with patch("apps.settings_app.retention._spawn", lambda work: None):
                run = process_batch(run.pk, self.user, start=True, backup=True)
                resume(run.pk)                          # started on the server; its process then dies
            self.snapshot(130)                          # not in the preview: stays
            cache.clear()                               # the runner's lock expired with its process
            self.assertTrue(resume_stalled())
        run.refresh_from_db()
        self.assertEqual((run.status, AccountSnapshotHistory.objects.count()), ("done", 1))
        self.assertFalse(resume_stalled())              # once a minute at most, and nothing left anyway

    def test_a_run_left_half_way_by_the_old_page_waits_for_someone(self):
        from django.core.cache import cache

        from .retention import resume_stalled

        for days in (100, 110):
            self.snapshot(days)
        run = preview(self.user)
        with patch("apps.settings_app.retention.BATCH_SIZE", 1):
            run = process_batch(run.pk, self.user, start=True, backup=True)    # "running", as the old page left it
        cache.clear()
        self.assertFalse(resume_stalled())
        self.client.force_login(self.user)
        page = self.client.get(reverse("settings_app:retention-run", args=[run.pk]))
        self.assertContains(page, "Retomar")
        self.assertNotContains(page, "Rodando no servidor")
        self.assertEqual(AccountSnapshotHistory.objects.count(), 1)

    def test_a_failing_batch_stops_the_run_and_says_why(self):
        self.snapshot(100)
        run = preview(self.user)
        self.policy["log_days"] = 30                    # the policy changed after the preview
        with patch("apps.settings_app.retention._spawn", lambda work: None):
            run = process_batch(run.pk, self.user, start=True, backup=True)
        self.save_policy()
        from .retention import resume

        run = resume(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, "paused")
        self.assertIn("política mudou", run.processed["error"])
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("settings_app:retention-run", args=[run.pk])), "Parou:")

    def test_a_busy_database_is_waited_for_and_the_batch_tried_again(self):
        from django.db import OperationalError

        from . import retention

        self.snapshot(100)
        run = preview(self.user)
        with patch("apps.settings_app.retention._spawn", lambda work: None):
            run = process_batch(run.pk, self.user, start=True, backup=True)
        self.snapshot(130)
        real = retention.process_batch
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError(1205, "Lock wait timeout exceeded; try restarting transaction")
            return real(*args, **kwargs)

        with patch("apps.settings_app.retention.process_batch", flaky), patch("apps.settings_app.retention.BUSY_WAIT_SECONDS", 0):
            retention.resume(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertNotIn("error", run.processed)

    def test_changed_record_retries_the_entire_job_transaction_once(self):
        from django.db import OperationalError
        from . import retention
        job = self.job()
        run = preview(self.user)
        run.status = "running"
        run.save(update_fields=["status"])
        create = JobRetentionReceipt.objects.bulk_create
        attempts = []
        def flaky(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OperationalError(1020, "changed row")
            return create(*args, **kwargs)
        with patch.object(JobRetentionReceipt.objects, "bulk_create", side_effect=flaky), patch.object(retention, "BUSY_WAIT_SECONDS", 0):
            retention.resume(run.pk)
        run.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertEqual(run.processed["jobs"], 1)
        self.assertEqual(run.processed["logs"], 1)
        self.assertEqual(run.processed["last_retry"]["code"], 1020)
        self.assertEqual(run.processed["last_retry"]["stage"], "jobs")
        self.assertEqual(JobRetentionReceipt.objects.count(), 1)
        self.assertEqual(job.inputs_json, '{"important": "keep"}')

    def test_retry_limit_pauses_with_safe_persistent_diagnostics(self):
        from django.db import OperationalError
        from . import retention
        self.job()
        run = preview(self.user)
        run.status = "running"
        run.save(update_fields=["status"])
        with patch.object(retention, "_lock_job_rows", side_effect=OperationalError(1020, "secret SQL values")) as lock, \
             patch.object(retention, "BUSY_RETRIES", 2), patch.object(retention, "BUSY_WAIT_SECONDS", 0):
            retention.resume(run.pk)
        run.refresh_from_db()
        self.assertEqual(lock.call_count, 3)
        self.assertEqual(run.status, "paused")
        self.assertEqual(run.processed["last_retry"]["attempt"], 2)
        self.assertEqual(run.processed["error_details"]["code"], 1020)
        self.assertEqual(run.processed["error_details"]["stage"], "jobs")
        self.assertNotIn("secret", json.dumps(run.processed))
        self.assertFalse(JobRetentionReceipt.objects.exists())
        self.client.force_login(self.user)
        page = self.client.get(reverse("settings_app:retention-run", args=[run.pk]))
        self.assertContains(page, "Diagnóstico da interrupção")
        self.assertContains(page, "1020")
        self.assertNotContains(page, "secret SQL")

    def test_log_delete_is_rolled_back_before_retry_without_double_counting(self):
        from django.db import OperationalError
        from django.db.models.query import QuerySet
        from . import retention
        job = self.job()
        run = preview(self.user)
        run.status = "running"
        run.save(update_fields=["status"])
        delete = QuerySet.delete
        attempts = []
        def flaky(query):
            if query.model is not JobLog:
                return delete(query)
            attempts.append(1)
            self.assertEqual(job.logs.count(), 1)
            result = delete(query)
            if len(attempts) == 1:
                raise OperationalError(1020, "changed row")
            return result
        with patch.object(QuerySet, "delete", flaky), patch.object(retention, "BUSY_WAIT_SECONDS", 0):
            retention.resume(run.pk)
        run.refresh_from_db()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(run.status, "done")
        self.assertEqual(run.processed["logs"], 1)
        self.assertEqual(run.processed["last_retry"]["stage"], "logs")
        self.assertEqual(run.processed["jobs"], 1)

    def test_non_transient_database_error_does_not_retry(self):
        from django.db import OperationalError
        from . import retention
        self.job()
        run = preview(self.user)
        run.status = "running"
        run.save(update_fields=["status"])
        with patch.object(retention, "_lock_job_rows", side_effect=OperationalError(1146, "missing table")) as lock:
            retention.resume(run.pk)
        run.refresh_from_db()
        self.assertEqual(lock.call_count, 1)
        self.assertEqual(run.status, "paused")
        self.assertEqual(run.processed["error_details"]["code"], 1146)
        self.assertNotIn("last_retry", run.processed)


class RetentionConcurrentJobsTests(TransactionTestCase):
    """Actual second-connection change between selection and row locking (MariaDB CI)."""
    setUp = RetentionData.setUp
    save_policy = RetentionData.save_policy
    job = RetentionData.job

    def _concurrent_protection(self, stage):
        import threading
        from django.db import connections
        from . import retention
        parent = self.job()
        run = preview(self.user)
        if stage == "logs":
            run.planned["jobs"] = 0
            run.save(update_fields=["planned"])
        lock = retention._lock_job_rows
        errors = []
        def writer():
            try:
                self.job(status="running", source_job_id=parent.pk, archived_at=None, finished_at=None)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()
        def after_selection(ids):
            thread = threading.Thread(target=writer)
            thread.start()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "concurrent writer did not finish")
            self.assertFalse(errors, errors)
            return lock(ids)
        with patch.object(retention, "_lock_job_rows", after_selection):
            result = process_batch(run.pk, self.user, start=True, backup=True)
        parent.refresh_from_db()
        self.assertEqual(result.status, "done")
        self.assertEqual(parent.progress_json, '{"large_result": "old"}')
        self.assertEqual(parent.logs.count(), 1)
        self.assertFalse(JobRetentionReceipt.objects.exists())

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_live_dependency_is_rechecked_before_compaction(self):
        self._concurrent_protection("jobs")

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_live_dependency_is_rechecked_before_log_deletion(self):
        self._concurrent_protection("logs")
