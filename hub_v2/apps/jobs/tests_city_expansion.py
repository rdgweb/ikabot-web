import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import CityExpansionProposal, CityExpansionScope, Job
from apps.jobs.services.city_expansion import answer_proposal, notify_pending
from apps.jobs.services.dispatch import build_payload
from apps.jobs.services.workflows import create_job_with_workflow


class CityExpansionTests(TestCase):
    def setUp(self):
        self.node = Node.objects.create(name="test", transport="https", agent_version="0.1.159")
        self.account = Account.objects.create(node=self.node, label="Conta", email="test@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Jogador")
        self.snapshot = AccountSnapshot.objects.create(account=self.account, game_account=self.ga,
            cities={"1": {"id": "1", "name": "A"}, "2": {"id": "2", "name": "B"}},
            full_snapshot_updated_at=timezone.now())
        self.chat = patch("apps.telegram.services.config.destination_chat", return_value="123")
        self.chat.start()
        self.addCleanup(self.chat.stop)
        self.send = patch("apps.telegram.services.bot_api.send_message", return_value={"ok": True}).start()
        self.addCleanup(patch.stopall)

    def job(self, code=1005, inputs=None, source=None):
        return create_job_with_workflow(account=self.account, game_account=self.ga, node=self.node,
            action_code=code, inputs=inputs if inputs is not None else {
                "mode": "maintain", "maintain_scope": "selected_uniform", "city_ids": ["1", "2"],
                "target_garrison": {"302": 20}, "building_type": "troops"},
            status="scheduled", source_job=source)

    def dispatch(self, job):
        job.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            return build_payload(job)["inputs"]

    def discover(self, job):
        self.dispatch(job)
        self.snapshot.cities["3"] = {"id": "3", "name": "Nova <cidade>"}
        self.snapshot.save()
        self.dispatch(job)
        return CityExpansionProposal.objects.get()

    def answer(self, proposal, accept=True, chat="123"):
        return answer_proposal(str(proposal.pk), chat_id=chat, accept=accept, user_id="42")

    def test_approval_only_changes_next_execution_and_is_idempotent(self):
        job = self.job()
        proposal = self.discover(job)
        original = Job.objects.get(pk=job.pk).inputs_json
        self.assertTrue(self.answer(proposal)[0])
        self.assertEqual(Job.objects.get(pk=job.pk).inputs_json, original)
        self.assertFalse(self.answer(proposal)[0])
        child = self.job(inputs=json.loads(original), source=job)
        self.assertEqual(self.dispatch(child)["city_ids"], ["1", "2", "3"])
        self.assertEqual(self.send.call_count, 1)
        self.assertIn("&lt;cidade&gt;", self.send.call_args.args[1])

    def test_decline_does_not_repeat_on_later_runs(self):
        job = self.job()
        proposal = self.discover(job)
        self.assertTrue(self.answer(proposal, accept=False)[0])
        self.assertEqual(self.dispatch(job)["city_ids"], ["1", "2"])
        self.assertEqual(self.send.call_count, 1)

    def test_subset_never_offers_expansion(self):
        job = self.job(inputs={"mode": "maintain", "maintain_scope": "selected_uniform", "city_ids": ["1"]})
        self.dispatch(job)
        self.snapshot.cities["3"] = {"id": "3"}
        self.snapshot.save()
        self.dispatch(job)
        self.assertFalse(CityExpansionProposal.objects.exists())

    def test_wrong_chat_cannot_answer(self):
        proposal = self.discover(self.job())
        self.assertFalse(self.answer(proposal, chat="999")[0])
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "pending")

    def test_cancelled_job_and_missing_city_expire_proposal(self):
        job = self.job()
        proposal = self.discover(job)
        Job.objects.filter(pk=job.pk).update(status="cancelled")
        self.assertFalse(self.answer(proposal)[0])
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "expired")

    def test_removed_city_expires_proposal(self):
        proposal = self.discover(self.job())
        self.snapshot.cities.pop("3")
        self.snapshot.save()
        self.assertFalse(self.answer(proposal)[0])

    def test_reappearing_city_does_not_repeat_notification(self):
        job = self.job()
        self.dispatch(job)
        old = self.snapshot.cities.pop("2")
        self.snapshot.save()
        self.dispatch(job)
        self.snapshot.cities["2"] = old
        self.snapshot.save()
        self.dispatch(job)
        self.assertFalse(CityExpansionProposal.objects.exists())

    def test_missing_full_snapshot_does_not_establish_baseline(self):
        self.snapshot.full_snapshot_updated_at = None
        self.snapshot.save()
        self.dispatch(self.job())
        self.assertFalse(CityExpansionScope.objects.exists())

    def test_manual_selection_edit_wins_over_old_approval(self):
        job = self.job()
        proposal = self.discover(job)
        self.answer(proposal)
        params = json.loads(Job.objects.get(pk=job.pk).inputs_json)
        params["city_ids"] = ["2"]
        child = self.job(inputs=params, source=job)
        self.assertEqual(self.dispatch(child)["city_ids"], ["2"])
        self.assertFalse(CityExpansionScope.objects.get().enabled)

    def test_pending_city_edit_blocks_callback(self):
        job = self.job()
        proposal = self.discover(job)
        params = json.loads(Job.objects.get(pk=job.pk).inputs_json)
        params["__pending_patch"] = {"city_ids": ["2"]}
        Job.objects.filter(pk=job.pk).update(inputs_json=json.dumps(params))
        self.assertFalse(self.answer(proposal)[0])

    def test_wine_freezes_first_roster_until_approval(self):
        job = self.job(702, {})
        proposal = self.discover(job)
        self.assertEqual(self.dispatch(job)["city_ids"], ["1", "2"])
        self.answer(proposal)
        self.assertEqual(self.dispatch(job)["city_ids"], ["1", "2", "3"])

    def test_custom_training_targets_are_not_guessed(self):
        job = self.job(inputs={"mode": "maintain", "maintain_scope": "selected_custom", "city_ids": ["1", "2"]})
        self.dispatch(job)
        self.assertFalse(CityExpansionScope.objects.exists())

    def test_old_agent_does_not_promise_wine_consent_it_cannot_enforce(self):
        self.node.agent_version = "0.1.158"
        self.node.save(update_fields=["agent_version"])
        self.dispatch(self.job(702, {}))
        self.assertFalse(CityExpansionScope.objects.exists())

    def test_donation_clones_config_without_runtime_state_or_duplicates(self):
        params = {"city_id": "1", "donation_type": "wood", "donation_method": "2", "method_value": 50,
                  "carry_over": 999, "__recovery": {"attempt": 2}}
        job = self.job(902, params)
        self.job(902, dict(params, city_id="2"))
        proposal = self.discover(job)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(self.answer(proposal)[0])
        new = Job.objects.filter(action_code=902).order_by("-created_at").first()
        inputs = json.loads(new.inputs_json)
        self.assertEqual(inputs["city_id"], "3")
        self.assertEqual(inputs["method_value"], 50)
        self.assertNotIn("carry_over", inputs)
        self.assertNotIn("__recovery", inputs)
        self.assertFalse(self.answer(proposal)[0])
        self.assertEqual(Job.objects.filter(action_code=902).count(), 3)

    def test_failed_telegram_send_retries_later_without_new_proposal(self):
        self.send.return_value = {"ok": False}
        proposal = self.discover(self.job())
        self.assertIsNone(proposal.sent_at)
        notify_pending(proposal.scope_id)
        self.assertEqual(self.send.call_count, 1)
        CityExpansionProposal.objects.filter(pk=proposal.pk).update(last_attempt_at=None)
        self.send.return_value = {"ok": True}
        notify_pending(proposal.scope_id)
        proposal.refresh_from_db()
        self.assertIsNotNone(proposal.sent_at)

    def test_invalid_callback_id_is_safe(self):
        self.assertFalse(answer_proposal("invalid", chat_id="123", accept=True)[0])

    @override_settings(AGENT_TOKEN="test-token", AGENT_ALLOWED_IPS="")
    @patch("apps.jobs.api.transport.manual_active", return_value=False)
    def test_https_claim_uses_frozen_wine_roster(self, manual):
        job = self.job(702, {})
        self.discover(job)
        response = self.client.post("/api/agent/jobs/claim/", {"node_id": str(self.node.pk), "slots": 1},
            content_type="application/json", HTTP_X_AGENT_TOKEN="test-token", HTTP_X_AGENT_NODE_ID=str(self.node.pk))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["jobs"][0]["inputs"]["city_ids"], ["1", "2"])

    def test_running_job_is_not_rewritten(self):
        job = self.job()
        proposal = self.discover(job)
        self.answer(proposal)
        Job.objects.filter(pk=job.pk).update(status="running")
        self.assertEqual(self.dispatch(job)["city_ids"], ["1", "2"])

    def test_other_organization_cannot_read_or_answer(self):
        from apps.orgs import scope
        from apps.orgs.models import Organization

        proposal = self.discover(self.job())
        other = Organization.objects.create(name="Other", slug="other")
        token = scope.activate(other)
        try:
            self.assertFalse(CityExpansionProposal.objects.exists())
            self.assertFalse(self.answer(proposal)[0])
        finally:
            scope.deactivate(token)

    def test_distribution_preserves_settings_and_one_shot_is_ignored(self):
        job = self.job(3, {"cities": ["1", "2"], "loop_enabled": True, "target_stock": 60000})
        proposal = self.discover(job)
        self.answer(proposal)
        params = self.dispatch(job)
        self.assertEqual(params["cities"], ["1", "2", "3"])
        self.assertEqual(params["target_stock"], 60000)
        once = self.job(3, {"cities": ["1", "2", "3"], "loop_enabled": False})
        self.dispatch(once)
        self.assertEqual(CityExpansionScope.objects.count(), 1)

    @patch("apps.telegram.api.webhook.edit_message_text")
    @patch("apps.telegram.api.webhook.answer_callback_query")
    def test_webhook_routes_button_and_removes_keyboard(self, answer, edit):
        from apps.telegram.api.webhook import TelegramWebhookView

        proposal = self.discover(self.job())
        response = TelegramWebhookView()._handle_update({"callback_query": {
            "id": "click", "data": f"city_yes:{proposal.pk}", "from": {"id": 42},
            "message": {"chat": {"id": 123}, "message_id": 10, "text": "Nova cidade"},
        }})
        self.assertEqual(response.status_code, 200)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "accepted")
        answer.assert_called_once()
        edit.assert_called_once()
