"""N-132: construction-support reads only the active transports of a plan, never the whole tree of replans
(each one carries the plan's full step list)."""
import json

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Account, GameAccount, Node

from .models import Job


@override_settings(AGENT_TOKEN="", AGENT_ALLOWED_IPS="")
class ConstructionSupportTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente", transport="https")
        self.key = self.node.new_agent_token
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s1-br",
                                             server_language="br", server_number=1, name="Atenas")
        self.root = self._job(1002, "finished", {"construction_plan_steps": ["passo"] * 50})

    def _job(self, action_code, status, inputs, root=None, source=None):
        return Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=action_code,
                                  status=status, inputs_json=json.dumps(inputs), timeout_sec=1800,
                                  root_job_id=root.pk if root else None, source_job_id=source.pk if source else None)

    def _support(self, job):
        return self.client.get(f"/api/agent/jobs/{job.pk}/construction-support/",
                               HTTP_X_AGENT_TOKEN=self.key, HTTP_X_AGENT_NODE_ID=str(self.node.pk))

    def test_only_active_transports_come_back_and_the_replans_are_never_read(self):
        replan = self._job(1002, "scheduled", {"construction_plan_steps": ["passo"] * 50}, self.root, self.root)
        moving = self._job(2, "scheduled", {"wood": 500, "to_city": "7", "to_city_name": "Capital"}, self.root, replan)
        arriving = self._job(2, "running", {"monitor_mode": "arrival_check", "sent_resources": {"glas": 40}}, self.root, replan)
        self._job(2, "finished", {"wood": 900}, self.root, replan)
        self._job(2, "queued", {"monitor_mode": "other", "wood": 900}, self.root, replan)

        with CaptureQueriesContext(connection) as queries:
            response = self._support(replan)
        self.assertEqual(response.status_code, 200)
        entries = {entry["job_id"]: entry for entry in response.json()["entries"]}
        self.assertEqual(set(entries), {str(moving.pk), str(arriving.pk)})
        self.assertEqual(entries[str(moving.pk)]["resources"]["wood"], 500)
        self.assertEqual(entries[str(arriving.pk)]["resources"]["crystal"], 40)
        self.assertTrue(entries[str(moving.pk)]["scheduled_for"] == "")
        tree_reads = [q["sql"] for q in queries.captured_queries
                      if "inputs_json" in q["sql"] and "root_job_id" in q["sql"].split(" WHERE ", 1)[-1]]
        self.assertTrue(tree_reads)
        self.assertTrue(all('"action_code" = 2' in sql or "`action_code` = 2" in sql for sql in tree_reads))

    def test_old_jobs_without_a_root_are_still_walked(self):
        first = self._job(1002, "finished", {})
        child = self._job(1002, "scheduled", {}, source=first)
        Job.objects.filter(pk=child.pk).update(root_job_id=None)
        moving = self._job(2, "queued", {"marble": 30}, source=child)
        Job.objects.filter(pk=moving.pk).update(root_job_id=None)
        self._job(2, "error", {"marble": 30}, source=child)
        response = self._support(child)
        self.assertEqual([entry["job_id"] for entry in response.json()["entries"]], [str(moving.pk)])
