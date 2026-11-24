import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, Node
from apps.jobs.models import Job, JobLog
from apps.orgs.models import Membership, Organization
from apps.orgs.scope import activate, deactivate
from . import usage


class OrganizationUsageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Organização A", slug="usage-a")
        self.other = Organization.objects.create(name="Organização B", slug="usage-b")
        self.node = Node.objects.create(name="https-a", organization=self.org, transport="https")
        self.account = Account.objects.create(node=self.node, organization=self.org, label="A", email="a@example.com", password_enc="private")
        self.job = Job.objects.create(account=self.account, node=self.node, action_code=1, inputs_json='{"nome":"ação"}')
        JobLog.objects.create(job=self.job, message="ação 🍷")
        node = Node.objects.create(name="https-b", organization=self.other, transport="https")
        account = Account.objects.create(node=node, organization=self.other, label="B", email="b@example.com", password_enc="private")
        job = Job.objects.create(account=account, node=node, action_code=1)
        JobLog.objects.create(job=job, message="B" * 80)
        self.staff = get_user_model().objects.create_user("usage-staff", is_staff=True)
        self.owner = get_user_model().objects.create_user("usage-owner")
        Membership.objects.create(user=self.owner, organization=self.org, role="owner")

    @patch.object(usage, "SOURCES", (("jobs.JobLog", "job__account__organization_id", "Logs"),))
    def test_counts_utf8_content_by_organization_even_inside_a_customer_scope(self):
        token = activate(self.org)
        try:
            report = usage.collect()
        finally:
            deactivate(token)
        rows = {row["id"]:row for row in report["rows"]}
        self.assertEqual(rows[str(self.org.pk)]["content_bytes"], len("ação 🍷".encode()) + len("info"))
        self.assertEqual(rows[str(self.other.pk)]["content_bytes"], 80 + len("info"))
        self.assertEqual(rows[str(self.org.pk)]["members"], 1)
        self.assertEqual(rows[str(self.other.pk)]["members"], 0)
        self.assertEqual(report["shared"]["content_bytes"], 0)
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("ação 🍷", json.dumps(report, ensure_ascii=False))

    @patch.object(usage, "SOURCES", (("jobs.JobLog", "job__account__organization_id", "Logs"),))
    def test_history_delta_and_partial_results_are_explicit(self):
        usage.collect()
        JobLog.objects.create(job=self.job, message="abc")
        report = usage.collect()
        row = next(row for row in report["rows"] if row["id"]==str(self.org.pk))
        self.assertEqual(row["delta"], 3 + len("info"))
        self.assertEqual(len(report["history"]), 2)
        with patch.object(usage, "_aggregate", side_effect=RuntimeError("db unavailable")):
            partial = usage.collect()
        self.assertEqual(partial["issues"], ["Logs"])
        self.assertTrue(partial["history"][-1]["partial"])
        self.assertTrue(all("delta" not in row for row in partial["rows"]))

    def test_staff_can_read_and_owner_cannot_read_or_collect(self):
        url = reverse("settings_app:usage")
        self.client.force_login(self.owner)
        with patch.object(usage, "start") as start:
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url).status_code, 404)
            start.assert_not_called()
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(url), "Primeira medição")
        with patch.object(usage, "start", return_value=True) as start:
            self.assertRedirects(self.client.post(url), url)
            start.assert_called_once()

    @patch.object(usage, "SOURCES", (("jobs.JobLog", "job__account__organization_id", "Logs"),))
    def test_page_renders_a_saved_report_without_recollecting(self):
        usage.collect()
        self.client.force_login(self.staff)
        with patch.object(usage, "collect") as collect:
            response = self.client.get(reverse("settings_app:usage"))
            self.assertContains(response, "Organização A")
            self.assertContains(response, "Organização B")
            self.assertContains(response, "bases diferentes")
            collect.assert_not_called()

    def test_a_running_collection_is_not_started_twice(self):
        with patch("apps.settings_app.usage.threading.Thread") as thread:
            self.assertTrue(usage.start())
            self.assertFalse(usage.start())
            self.assertEqual(thread.call_count, 1)
            cache.clear()

    def test_navigation_matches_platform_permissions(self):
        from core.context_processors import nav_context
        from django.test import RequestFactory
        for user, visible in ((self.staff, True), (self.owner, False)):
            request = RequestFactory().get("/")
            request.user = user
            urls = [item["url"] for section in nav_context(request)["NAV_SECTIONS"] for item in section["items"]]
            self.assertEqual("/settings/manutencao/" in urls, visible)
            self.assertEqual("/settings/uso/" in urls, visible)
