"""N-89: one status rule for the rows, the summary cards, the filter and the live update."""

import json
from collections import Counter
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node

from .models import Job, Workflow, WorkflowRun
from .services import workflow_status
from .services.workflows import ensure_workflow_for_job
from .views.jobs import _filtered_workflow_queryset_from_querystring


class WorkflowStatusTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="status", email="status@example.com", password="secret123")
        self.node = Node.objects.create(name="node-status")
        self.account = Account.objects.create(node=self.node, label="Lobby", email="status-lobby@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=5, server_id="s5-br", server_language="br", server_number=5, name="Conta",
        )
        now = timezone.now()
        self.flows = {
            "active": self._workflow(701, ["finished", ("running", {"lease_expires_at": now + timedelta(minutes=3)})]),
            "waiting": self._workflow(702, ["finished", "scheduled"]),
            "problem": self._workflow(601, ["finished", "error"]),
            "problem_with_queue": self._workflow(17, ["scheduled", "error"]),
            "recovered": self._workflow(3, ["error", "finished", "scheduled"]),
            "paused": self._workflow(6, ["finished"], stored="paused"),
            "dead_run": self._workflow(1007, [("running", {"lease_expires_at": now - timedelta(minutes=10)})], stored="waiting"),
            "finished": self._workflow(2, ["finished"]),
            "loop_stopped": self._workflow(1006, ["finished"], recurring=True, stored="waiting"),
        }
        self.expected = {
            "active": "active", "waiting": "waiting", "problem": "problem", "problem_with_queue": "problem",
            "recovered": "waiting", "paused": "paused", "dead_run": "waiting", "finished": "finished",
            "loop_stopped": "problem",
        }
        self.client.force_login(self.user)

    def _job(self, code, status, workflow=None, **extra):
        return Job.objects.create(
            account=self.account, game_account=self.ga, node=self.node, action_code=code,
            status=status, inputs_json="{}", timeout_sec=1800, workflow=workflow, **extra,
        )

    def _workflow(self, code, jobs, *, stored=None, recurring=None):
        first, *rest = [(item, {}) if isinstance(item, str) else item for item in jobs]
        root = self._job(code, first[0], **first[1])
        ensure_workflow_for_job(root)
        root.refresh_from_db()
        workflow = Workflow.objects.get(pk=root.workflow_id)
        for status, extra in rest:
            self._job(code, status, workflow=workflow, **extra)
        if recurring is not None:
            config = json.loads(workflow.config_json or "{}")
            config["recurring"] = recurring
            workflow.config_json = json.dumps(config)
        if stored:
            workflow.status = stored
        workflow.save()
        return workflow

    def _rows(self, **params):
        response = self.client.get(reverse("jobs:job-list"), {"group": "none", **params})
        self.assertEqual(response.status_code, 200)
        return response, {row["workflow"].pk: row["status"] for row in response.context["workflow_rows"]}

    # ── the rule ──

    def test_each_workflow_gets_the_status_of_its_jobs(self):
        statuses = workflow_status.effective_statuses(self.flows.values())

        self.assertEqual({name: statuses[flow.pk] for name, flow in self.flows.items()}, self.expected)

    def test_a_workflow_without_jobs_keeps_the_stored_status(self):
        flow = self.flows["finished"]
        Job.objects.filter(workflow=flow).delete()
        flow.status = "draft"

        self.assertEqual(workflow_status.effective_status(flow, None), "draft")

    def test_an_ended_loop_is_not_a_problem(self):
        flow = self.flows["loop_stopped"]
        for stored in ("finished", "cancelled", "paused"):
            flow.status = stored
            facts = workflow_status.job_facts([flow.pk]).get(flow.pk)
            self.assertNotEqual(workflow_status.effective_status(flow, facts), "problem", stored)

    # ── the same answer everywhere ──

    def test_summary_cards_count_what_the_rows_show(self):
        response, rows = self._rows()
        summary = response.context["workflow_summary"]

        shown = Counter(rows.values())
        self.assertEqual({name: rows[flow.pk] for name, flow in self.flows.items()}, self.expected)
        self.assertEqual(
            {key: summary[key] for key in ("active", "waiting", "problem", "paused", "finished")},
            {"active": 1, "waiting": 3, "problem": 3, "paused": 1, "finished": 1},
        )
        for key in ("active", "waiting", "problem", "paused", "finished"):
            self.assertEqual(summary[key], shown[key], key)
        self.assertEqual(summary["total"], len(self.flows))
        # the stored field alone says something else: that was the old summary
        stored = Counter(Workflow.objects.values_list("status", flat=True))
        self.assertNotEqual(stored.get("problem", 0), summary["problem"])
        html = response.content.decode()
        self.assertIn('data-wf-summary="problem">3</div>', html)
        self.assertIn('data-wf-summary="active">1</div>', html)

    def test_summary_is_global_even_when_the_list_is_filtered_or_paged(self):
        response, rows = self._rows(status="problem", per_page=5)

        self.assertEqual(len(rows), 3)
        self.assertEqual(response.context["workflow_summary"]["waiting"], 3)

    def test_status_filter_matches_the_status_shown(self):
        for status, names in (
            ("problem", {"problem", "problem_with_queue", "loop_stopped"}),
            ("active", {"active"}),
            ("waiting", {"waiting", "recovered", "dead_run"}),
            ("paused", {"paused"}),
            ("finished", {"finished"}),
        ):
            _response, rows = self._rows(status=status)
            self.assertEqual(set(rows), {self.flows[name].pk for name in names}, status)
            self.assertEqual(set(rows.values()), {status})

    def test_bulk_actions_on_all_filtered_use_the_same_filter(self):
        queryset = _filtered_workflow_queryset_from_querystring("status=problem&group=none")

        self.assertEqual(
            set(queryset.values_list("pk", flat=True)),
            {self.flows[name].pk for name in ("problem", "problem_with_queue", "loop_stopped")},
        )

    # ── live ──

    def _live(self, **params):
        response = self.client.get(reverse("jobs:workflow-live"), params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_live_endpoint_agrees_with_the_page(self):
        response, rows = self._rows()

        live = self._live()

        self.assertEqual({pk: item["status"] for pk, item in live["workflows"].items()}, {str(pk): status for pk, status in rows.items()})
        self.assertEqual(live["summary"], response.context["workflow_summary"])
        problem = live["workflows"][str(self.flows["problem"].pk)]
        self.assertEqual((problem["label"], problem["color"]), ("Com problema", "var(--ik-bad)"))
        self.assertEqual(problem["badge"], "badge-danger")

    def test_live_endpoint_follows_the_jobs_without_a_page_reload(self):
        flow = self.flows["active"]
        self.assertEqual(self._live()["workflows"][str(flow.pk)]["status"], "active")

        # the cycle ends and the next one is lined up
        Job.objects.filter(workflow=flow, status="running").update(status="finished", finished_at=timezone.now())
        self._job(701, "scheduled", workflow=flow)
        flow.next_scheduled_for = timezone.now() + timedelta(minutes=30)
        flow.last_event_at = timezone.now()
        flow.save(update_fields=["next_scheduled_for", "last_event_at"])

        live = self._live()
        item = live["workflows"][str(flow.pk)]
        self.assertEqual((item["status"], item["label"]), ("waiting", "Aguardando"))
        self.assertRegex(item["next"], r"^\d\d/\d\d \d\d:\d\d$")
        self.assertTrue(item["last"].endswith("atrás"))
        self.assertEqual((live["summary"]["active"], live["summary"]["waiting"]), (0, 4))

    def test_live_endpoint_only_reads(self):
        before = (Workflow.objects.count(), WorkflowRun.objects.count(), Job.objects.filter(archived_at__isnull=False).count())
        orphan = self._job(100, "scheduled")           # a job without workflow: the page would adopt it

        response = self.client.get(reverse("jobs:workflow-live"))

        self.assertEqual(response["Cache-Control"], "no-store")
        after = (Workflow.objects.count(), WorkflowRun.objects.count(), Job.objects.filter(archived_at__isnull=False).count())
        self.assertEqual(before, after)
        orphan.refresh_from_db()
        self.assertIsNone(orphan.workflow_id)
        self.assertLess(len(response.content), 6000)

    def test_live_endpoint_needs_a_logged_user_and_handles_archived(self):
        Workflow.objects.filter(pk=self.flows["finished"].pk).update(archived_at=timezone.now())

        self.assertNotIn(str(self.flows["finished"].pk), self._live()["workflows"])
        self.assertEqual(list(self._live(archived=1)["workflows"]), [str(self.flows["finished"].pk)])
        self.client.logout()
        self.assertEqual(self.client.get(reverse("jobs:workflow-live")).status_code, 302)

    def test_page_carries_the_hooks_the_live_update_writes_to(self):
        html = self.client.get(reverse("jobs:job-list"), {"group": "menu"}).content.decode()

        self.assertEqual(html.count("data-wf-row="), len(self.flows))
        self.assertEqual(html.count("<span data-wf-badge"), len(self.flows))
        # every header (menu or action) has its status chips and its select toggle
        self.assertEqual(html.count('data-wf-chips="group"') + html.count('data-wf-chips="sub"'), html.count("wf-group-select"))
        for key in ("total", "active", "waiting", "problem", "paused", "finished"):
            self.assertIn(f'data-wf-summary="{key}"', html)
        self.assertIn(f"liveUrl: '{reverse('jobs:workflow-live')}'", html)
        self.assertIn('x-init="syncFilteredCount(); startLive()"', html)
        archived = self.client.get(reverse("jobs:job-list"), {"archived": "1"}).content.decode()
        self.assertIn(f"liveUrl: '{reverse('jobs:workflow-live')}?archived=1'", archived)
