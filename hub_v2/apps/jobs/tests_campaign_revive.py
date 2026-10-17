"""N-152: a world spy campaign whose round ended without the next one comes back when a child notifies."""
import json

from django.test import TestCase, override_settings

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job
from apps.jobs.services.campaigns import revive_campaign


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class CampaignReviveTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="agente", transport="https")
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Jogador")
        self.root = self._job("finished", {"city_ids": "100", "missions": "5"})
        self.last = self._job("finished", {"city_ids": "100", "missions": "5", "__campaign_root_id": str(self.root.pk),
                                           "__claimed_targets": {"500": {"target_city_name": "Polis"}}})
        self._point_to(self.last)

    def _job(self, status, inputs, **extra):
        return Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=16,
                                  status=status, inputs_json=json.dumps(inputs), timeout_sec=1800, **extra)

    def _point_to(self, job):
        Job.objects.filter(pk=self.root.pk).update(progress_json=json.dumps({"current_runner_id": str(job.pk)}))

    def _pointer(self):
        return json.loads(Job.objects.get(pk=self.root.pk).progress_json)["current_runner_id"]

    def _notify(self):
        return self.client.post(f"/api/agent/jobs/{self.root.pk}/notify/", data={"child_done": {"target_city_id": "500"}},
                                content_type="application/json", HTTP_X_AGENT_TOKEN="test-agent-token",
                                HTTP_X_AGENT_NODE_ID=str(self.node.pk))

    def test_a_notification_to_an_ended_round_creates_the_next_round(self):
        response = self._notify()
        self.assertEqual(response.status_code, 200, response.content)
        new = Job.objects.get(pk=response.json()["revived"])
        self.assertEqual((new.action_code, new.status, new.source_job_id), (16, "scheduled", self.last.pk))
        inputs = json.loads(new.inputs_json)
        self.assertEqual(inputs["__campaign_root_id"], str(self.root.pk))
        self.assertIn("500", inputs["__claimed_targets"])                      # the claims are kept
        self.assertEqual(json.loads(new.progress_json)["inbox"], [{"target_city_id": "500"}])
        self.assertEqual(self._pointer(), str(new.pk))
        self._notify()                                                           # a second child: same round
        self.assertEqual(Job.objects.filter(action_code=16, status="scheduled").count(), 1)
        self.assertEqual(len(json.loads(Job.objects.get(pk=new.pk).progress_json)["inbox"]), 2)

    def test_an_active_round_is_used_instead_of_a_new_one(self):
        active = self._job("scheduled", {"__campaign_root_id": str(self.root.pk)})
        self.assertEqual(revive_campaign(self.root.pk).pk, active.pk)
        self.assertEqual(self._pointer(), str(active.pk))
        self.assertEqual(Job.objects.filter(action_code=16).count(), 3)

    def test_a_cancelled_campaign_stays_cancelled(self):
        Job.objects.filter(pk=self.last.pk).update(status="cancelled")
        self.assertEqual(self._notify().status_code, 409)
        self.assertFalse(Job.objects.filter(action_code=16, status="scheduled").exists())

    def test_an_inbox_saved_as_repr_still_reaches_the_agent(self):
        from apps.jobs.services.dispatch import _serialize_progress

        Job.objects.filter(pk=self.last.pk).update(progress_json=str({"inbox": [{"target_city_id": "500"}]}))
        self.assertEqual(_serialize_progress(Job.objects.get(pk=self.last.pk))["inbox"], [{"target_city_id": "500"}])
