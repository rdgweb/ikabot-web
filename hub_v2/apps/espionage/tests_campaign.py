"""N-150: the world spy campaign in numbers, on the job detail page of any of its rounds."""
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.espionage.models import SpyReport
from apps.espionage.services.campaign import campaign_summary
from apps.jobs.models import Job, JobLog


class WorldSpyCampaignTests(TestCase):
    def setUp(self):
        cache.clear()
        self.node = Node.objects.create(name="agente", transport="https")
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Jogador")
        self.root = self._job(16, "finished", {})
        JobLog.objects.create(job=self.root, message="200 alvos pendentes | safehouses ocupadas=12/20")
        self.round_2 = self._job(16, "scheduled", {"__campaign_root_id": str(self.root.pk)}, root_job_id=self.root.pk)
        spy = {"__root_job_id": str(self.root.pk)}
        self.done = self._job(15, "finished", {**spy, "target_city_id": "100"}, root_job_id=self.root.pk)
        self.retry = self._job(15, "failed", {**spy, "target_city_id": "100"}, root_job_id=self.root.pk)  # same city, then finished
        self.running = self._job(15, "scheduled", {**spy, "target_city_id": "200"}, root_job_id=self.root.pk)
        self.broken = self._job(15, "failed", {**spy, "target_city_id": "300"}, root_job_id=self.root.pk)
        self._job(15, "finished", {"target_city_id": "999"})                        # not part of the campaign
        JobLog.objects.create(job=self.running, message="Treinando 5 espião(ões) (custo: 750 ouro, 270 cristal)")
        SpyReport.objects.create(game_account=self.ga, report_id="r1", created_by_job=self.done, mission_id=5,
                                 target_city_id="100", agents_lost=0, result_status="Sucesso")
        SpyReport.objects.create(game_account=self.ga, report_id="r0", created_by_job=self.done, mission_id=1,
                                 target_city_id="100", agents_lost=1, result_status="Sucesso")    # the trip: not a reading
        SpyReport.objects.create(game_account=self.ga, report_id="r2", created_by_job=self.done, mission_id=6,
                                 target_city_id="100", agents_lost=2, result_status="Falhado")

    def _job(self, action_code, status, inputs, **extra):
        return Job.objects.create(account=self.account, game_account=self.ga, node=self.node, action_code=action_code,
                                  status=status, inputs_json=json.dumps(inputs), timeout_sec=1800, **extra)

    def test_the_numbers_of_the_campaign(self):
        c = campaign_summary(self.round_2)
        self.assertEqual(c["root_id"], str(self.root.pk))
        self.assertEqual(c["rounds"], 2)
        self.assertEqual((c["targets_done"], c["targets_running"], c["targets_error"]), (1, 1, 1))
        self.assertEqual((c["pending"], c["pending_capped"], c["safehouses_busy"], c["safehouses"]), (200, True, 12, 20))
        missions = {m["id"]: (m["ok"], m["failed"]) for m in c["missions"]}
        self.assertEqual(missions, {5: (1, 0), 6: (0, 1)})
        self.assertEqual((c["spies_trained"], c["training_gold"], c["training_crystal"]), (5, 750, 270))
        self.assertEqual((c["spies_lost"], c["reports"], c["reports_failed"], c["stock_cities"]), (3, 2, 1, 1))

    def test_other_jobs_have_no_campaign(self):
        self.assertIsNone(campaign_summary(self.done))

    def test_the_job_detail_shows_the_panel(self):
        self.client.force_login(get_user_model().objects.create_user("rodrigo", password="x", is_staff=True))
        page = self.client.get(f"/jobs/{self.round_2.pk}/").content.decode()
        self.assertIn(f"/jobs/{self.round_2.pk}/campaign-panel/", page)            # the page does not wait for the card
        self.assertNotIn("Campanha de espionagem", page)
        html = self.client.get(f"/jobs/{self.round_2.pk}/campaign-panel/").content.decode()
        self.assertIn("Campanha de espionagem", html)
        self.assertIn("Início da campanha", html)
        self.assertIn("200+", html)
