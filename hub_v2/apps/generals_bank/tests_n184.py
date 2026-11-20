"""N-184: the Generals Bank — a plan the producers can run, a cycle that ends (by itself or by hand), failures with
their reason, the stock baseline kept in the hub, partial purchases that are not a success."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job

from . import services
from .models import GeneralsBankConfig, GeneralsBankCycle, GeneralsBankCycleTask, GeneralsBankProducer

HOPLITE = 303


def city(city_id, *buildings, citizens=500, **res):
    return {"id": city_id, "name": f"C{city_id}", "free_citizens": citizens,
            "wood": 100000, "wine": 100000, "marble": 100000, "crystal": 100000, "sulfur": 100000, **res,
            "buildings": [{"building": b, "position": i, "level": 10} for i, b in enumerate(buildings)]}


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class GeneralsBankTests(TestCase):
    def setUp(self):
        self.node_bank = Node.objects.create(name="bank-node")
        self.node_prod = Node.objects.create(name="prod-node")
        bank_acc = Account.objects.create(node=self.node_bank, label="b", email="b@example.org", password_enc="x")
        prod_acc = Account.objects.create(node=self.node_prod, label="p", email="p@example.org", password_enc="x")
        self.bank_ga = GameAccount.objects.create(account=bank_acc, lobby_account_id=1, server_id="s1-br", server_language="br",
                                                  server_number=1, name="Banco", active=True)
        self.prod_ga = GameAccount.objects.create(account=prod_acc, lobby_account_id=2, server_id="s1-br", server_language="br",
                                                  server_number=1, name="Produtora", active=True)
        AccountSnapshot.objects.create(account=bank_acc, game_account=self.bank_ga, base_snapshot={"gold": 5000000},
                                       cities=[city(10, "branchOffice")])
        self.snap = AccountSnapshot.objects.create(account=prod_acc, game_account=self.prod_ga, base_snapshot={"gold": 100000},
                                                   cities=[city(20, "barracks", "blackMarket"), city(21, "barracks")])
        self.bank = GeneralsBankConfig.objects.create(bank_game_account=self.bank_ga, min_gold_floor=1000)
        self.producer = GeneralsBankProducer.objects.create(bank_config=self.bank, producer_game_account=self.prod_ga,
                                                            production_template={str(HOPLITE): 20}, keep_net_gold_positive=False)
        self.api = APIClient()
        self.api.credentials(HTTP_X_AGENT_TOKEN="test-agent-token")

    # --- the plan ---------------------------------------------------------------------------
    def test_only_a_city_with_barracks_and_black_market_trains(self):
        plan = services.build_producer_execution_plan(self.bank)
        self.assertEqual({t["city_id"] for t in plan["tasks"]}, {20})
        self.snap.cities = [city(21, "barracks")]
        self.snap.save()
        plan = services.build_producer_execution_plan(self.bank)
        self.assertEqual(plan["tasks"], [])
        self.assertTrue(any("Mercado Negro" in w and "C21" in w for w in plan["warnings"]))

    def test_the_population_reserve_is_kept(self):
        self.producer.min_population_reserve = 495
        self.producer.save()
        plan = services.build_producer_execution_plan(self.bank)
        self.assertEqual(sum(t["quantity_target"] for t in plan["tasks"]), 5)

    def test_no_liquidation_until_it_exists(self):
        self.assertEqual(services.determine_cycle_mode(self.bank), "accumulation")
        answer = self.api.post("/api/agent/generals-bank/cycles/create/",
                               {"bank_config_id": str(self.bank.pk), "mode": "liquidation"}, format="json")
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.json()["error"], "liquidation_not_available")

    # --- the cycle ends ---------------------------------------------------------------------
    def _cycle(self):
        cycle = services.create_accumulation_cycle(self.bank)
        self.assertEqual(cycle.tasks.count(), 1)
        self.assertEqual(cycle.tasks.get().unit_name, services.unit_name(HOPLITE))
        return cycle

    def _update(self, task, **data):
        return self.api.post(f"/api/agent/generals-bank/tasks/{task.pk}/update/", data, format="json").json()

    def test_a_failed_task_ends_the_cycle_with_its_reason(self):
        cycle = self._cycle()
        self._update(cycle.tasks.get(), status="failed", failure_reason="Sem recurso.")
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, "failed")
        self.assertIn("Sem recurso.", cycle.result_note)

    def test_the_bank_buys_what_was_listed_even_if_another_task_failed(self):
        cycle = self._cycle()
        other = GeneralsBankCycleTask.objects.create(cycle=cycle, producer_game_account=self.prod_ga, city_id=20, unit_id=HOPLITE,
                                                     quantity_target=3, status="training")
        first = cycle.tasks.exclude(pk=other.pk).get()
        self._update(first, status="listed", quantity_done=20, unit_price=100)
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, "training")                         # one still training
        answer = self._update(other, status="failed", failure_reason="Mercado Negro sem preço.")
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, "bank_buying")
        self.assertTrue(answer["buy_job_id"])
        offers = json.loads(Job.objects.get(pk=answer["buy_job_id"]).inputs_json)["offers"]
        self.assertEqual([o["task_id"] for o in offers], [str(first.pk)])  # only what was listed

    def test_the_baseline_is_written_once(self):
        cycle = self._cycle()
        task = cycle.tasks.get()
        self.assertEqual(self._update(task, baseline_count=7)["baseline_count"], 7)
        self.assertEqual(self._update(task, baseline_count=27)["baseline_count"], 7)   # a retry never moves it
        status = self.api.get(f"/api/agent/generals-bank/cycles/{cycle.pk}/status/").json()
        self.assertEqual(status["tasks"][0]["baseline_count"], 7)

    def test_a_partial_purchase_is_not_a_success(self):
        cycle = self._cycle()
        other = GeneralsBankCycleTask.objects.create(cycle=cycle, producer_game_account=self.prod_ga, city_id=20, unit_id=HOPLITE,
                                                     quantity_target=3, status="listed")
        first = cycle.tasks.exclude(pk=other.pk).get()
        services.advance_cycle_status(cycle, "bank_buying")
        self.api.post(f"/api/agent/generals-bank/cycles/{cycle.pk}/buy-complete/", {
            "purchases": [{"task_id": str(first.pk), "producer_ga_id": str(self.prod_ga.pk), "unit_id": HOPLITE, "quantity": 20,
                           "unit_price": 100}],
            "failed": [{"task_id": str(other.pk), "reason": "Oferta sumiu."}]}, format="json")
        answer = self.api.post(f"/api/agent/generals-bank/cycles/{cycle.pk}/complete/", {}, format="json").json()
        cycle.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((answer["cycle_status"], cycle.status), ("failed", "failed"))
        self.assertEqual((other.status, other.failure_reason), ("failed", "Oferta sumiu."))
        self.assertIn("Compra parcial", cycle.result_note)

    def test_a_cycle_is_cancelled_by_hand_with_its_jobs(self):
        cycle = self._cycle()
        user = get_user_model().objects.create_superuser("gb-admin", "gb@example.org", "x")
        self.client.force_login(user)
        job_id = cycle.tasks.get().training_job_id
        self.client.post(reverse("generals_bank:cancel-cycle", args=[self.bank.pk]))
        cycle.refresh_from_db()
        self.assertEqual(cycle.status, "cancelled")
        self.assertEqual(cycle.tasks.get().status, "cancelled")
        self.assertEqual(Job.objects.get(pk=job_id).status, "cancelled")
        self.assertIsNone(services.get_active_cycle(self.bank))

    def test_a_task_bought_or_cancelled_does_not_come_back(self):
        cycle = self._cycle()
        services.cancel_cycle(cycle, "teste")
        task = cycle.tasks.get()
        self.assertTrue(self._update(task, status="listed").get("ignored"))
        task.refresh_from_db()
        self.assertEqual(task.status, "cancelled")

    # --- the pages --------------------------------------------------------------------------
    def test_a_paused_producer_comes_back_and_the_pages_show_names(self):
        user = get_user_model().objects.create_superuser("gb-admin2", "gb2@example.org", "x")
        self.client.force_login(user)
        self.producer.is_active = False
        self.producer.save()
        page = self.client.get(reverse("generals_bank:detail", args=[self.bank.pk]))
        self.assertContains(page, "Reativar")
        self.client.post(reverse("generals_bank:producer-reactivate", args=[self.bank.pk, self.producer.pk]))
        self.producer.refresh_from_db()
        self.assertTrue(self.producer.is_active)
        self.assertContains(self.client.get(reverse("generals_bank:list")), "pronta")
        self._cycle()
        partial = self.client.get(reverse("generals_bank:cycle-status-partial", args=[self.bank.pk]))
        self.assertContains(partial, services.unit_name(HOPLITE))
        self.assertNotContains(partial, f"u{HOPLITE}")

    def test_unticked_boxes_are_off_when_adding_a_producer(self):
        user = get_user_model().objects.create_superuser("gb-admin3", "gb3@example.org", "x")
        self.client.force_login(user)
        self.producer.delete()
        self.client.post(reverse("generals_bank:producer-add", args=[self.bank.pk]),
                         {"producer_game_account": str(self.prod_ga.pk), "production_template": "{}"})
        producer = GeneralsBankProducer.objects.get(bank_config=self.bank)
        self.assertFalse(producer.sell_only_cycle_production)
        self.assertFalse(producer.keep_net_gold_positive)
