"""N-103: the construction panel says what is happening, what is stopped and why, and what comes next."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.services import construction_board as board
from apps.jobs.models import ConstructionResourceReservation, Job, JobLog


def _rows(first, last, wood=100, seconds=60):
    return [{"level": level, "costs": {"wood": wood * level, "wine": 0, "marble": 0, "glas": 10 * level, "sulfur": 0},
             "adjusted_seconds": seconds * level} for level in range(first, last + 1)]


def _step(index, city_id, city, building, position, target, **extra):
    return {"index": index, "city_id": city_id, "city_name": city, "building_id": building, "building_type": building,
            "building_position": str(position), "mode": "upgrade", "target_level": target, **extra}


class PlanOverviewTests(TestCase):
    LEVELS = {
        "1": {"_pos": {"3": 5, "4": 2}, "_bid": {"academy": 5, "warehouse": 2}},
        "2": {"_pos": {"3": 10}, "_bid": {"academy": 10}},
    }

    def _inputs(self):
        steps = [
            _step(1, "1", "Alfa", "academy", 3, 7),
            _step(2, "1", "Alfa", "warehouse", 4, 4, priority=True),
            _step(3, "2", "Beta", "academy", 3, 10),          # already there
        ]
        preview = [
            {**steps[0], "from_level": 4, "level_rows": _rows(5, 7)},
            {**steps[1], "from_level": 2, "level_rows": _rows(3, 4)},
            {**steps[2], "from_level": 8, "level_rows": _rows(9, 10)},
        ]
        return {"construction_plan_json": steps, "construction_plan_steps": preview}

    def test_only_the_levels_still_to_build_count(self):
        cost, seconds = board.step_remaining({"level_rows": _rows(5, 7)}, 5)
        self.assertEqual(cost, {"wood": 1300, "crystal": 130})            # levels 6 and 7; "glas" is crystal
        self.assertEqual(seconds, 60 * 13)
        self.assertEqual(board.step_remaining({"level_rows": _rows(5, 7)}, 7), ({}, 0))
        self.assertEqual(board.step_remaining(None, 0), ({}, 0))

    def test_the_plan_knows_what_is_done_what_is_left_and_what_it_costs(self):
        overview = board.plan_overview(self._inputs(), self.LEVELS)

        self.assertEqual((overview["total"], overview["done"], overview["pct"], overview["remaining_levels"]), (3, 1, 33, 4))
        self.assertEqual([(e["city_name"], e["building"], e["cur"], e["target"]) for e in overview["pending"]],
                         [("Alfa", "academy", 5, 7), ("Alfa", "warehouse", 2, 4)])
        self.assertEqual(overview["remaining_cost"], {"wood": 1300 + 700, "crystal": 130 + 70})
        # the prioritized step is what the city builds first
        self.assertEqual([e["building"] for e in overview["cities"]["1"]["pending"]], ["warehouse", "academy"])
        self.assertEqual(overview["cities"]["2"]["pending"], [])

    def test_cities_build_in_parallel_so_the_slowest_one_sets_the_time(self):
        inputs = self._inputs()
        inputs["construction_plan_json"].append(_step(4, "2", "Beta", "warehouse", 9, 1))
        inputs["construction_plan_steps"].append({"index": 4, "from_level": 0, "level_rows": _rows(1, 1, seconds=5000)})
        overview = board.plan_overview(inputs, self.LEVELS)
        alfa = 60 * (6 + 7) + 60 * (3 + 4)
        self.assertEqual(overview["cities"]["1"]["seconds"], alfa)
        self.assertEqual(overview["build_seconds"], 5000)                 # Beta alone, not Alfa + Beta

    def test_a_preview_out_of_step_with_the_plan_is_matched_by_index(self):
        inputs = self._inputs()
        inputs["construction_plan_steps"] = list(reversed(inputs["construction_plan_steps"]))[:2]
        overview = board.plan_overview(inputs, self.LEVELS)
        self.assertEqual(overview["remaining_cost"], {"wood": 700, "crystal": 70})     # only the warehouse has a preview left

    def test_a_plan_without_snapshot_levels_starts_from_zero(self):
        overview = board.plan_overview(self._inputs(), {})
        self.assertEqual((overview["done"], overview["remaining_levels"]), (0, 21))

    def test_city_states(self):
        self.assertEqual(board.city_state(working=True, has_pending=True, has_shortfall=True), board.CITY_WORKING)
        self.assertEqual(board.city_state(working=False, has_pending=True, has_shortfall=True), board.CITY_WAITING)
        self.assertEqual(board.city_state(working=False, has_pending=True, has_shortfall=False), board.CITY_QUEUED)
        self.assertEqual(board.city_state(working=False, has_pending=False, has_shortfall=True), board.CITY_IDLE)


class ConstructionPanelPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="build-user", email="build@example.com", password="secret123")
        self.node = Node.objects.create(name="node-build")
        self.account = Account.objects.create(node=self.node, label="Lobby Build", email="b@example.com", password_enc="x")
        self.busy = self._game_account(1, "Obreiro")
        self.lazy = self._game_account(2, "Parado")
        end_at = int(timezone.now().timestamp()) + 3600
        AccountSnapshot.objects.create(account=self.account, game_account=self.busy, base_snapshot={}, military={}, cities=[
            {"id": "1", "name": "Alfa", "tradegood": 1, "buildings": [
                {"position": 0, "building": "townHall", "level": 10, "is_upgrading": False},
                {"position": 3, "building": "academy", "level": 5, "is_upgrading": True, "construction_end_at": end_at},
                {"position": 4, "building": "warehouse", "level": 2, "is_upgrading": False},
                {"position": 5, "building": "empty", "level": 0},
            ]},
            {"id": "2", "name": "Beta", "tradegood": 2, "buildings": [{"position": 3, "building": "academy", "level": 1, "is_upgrading": False}]},
            {"id": "3", "name": "Gama", "tradegood": 3, "buildings": [{"position": 3, "building": "academy", "level": 1, "is_upgrading": False}]},
            {"id": "4", "name": "Delta", "tradegood": 4, "buildings": [{"position": 0, "building": "townHall", "level": 3, "is_upgrading": False}]},
        ])
        AccountSnapshot.objects.create(account=self.account, game_account=self.lazy, base_snapshot={}, military={}, cities=[
            {"id": "9", "name": "Sossego", "tradegood": 1, "buildings": [
                {"position": 0, "building": "townHall", "level": 4, "is_upgrading": False}, {"position": 5, "building": "empty", "level": 0},
            ]},
        ])
        steps = [
            _step(1, "1", "Alfa", "academy", 3, 7),
            _step(2, "1", "Alfa", "warehouse", 4, 4),
            _step(3, "2", "Beta", "academy", 3, 3),
            _step(4, "3", "Gama", "academy", 3, 2),
        ]
        preview = [
            {**steps[0], "level_rows": _rows(6, 7)}, {**steps[1], "level_rows": _rows(3, 4)},
            {**steps[2], "level_rows": _rows(2, 3, seconds=40000)}, {**steps[3], "level_rows": _rows(2, 2)},
        ]
        self.plan = Job.objects.create(
            account=self.account, game_account=self.busy, node=self.node, action_code=1002, status="scheduled",
            scheduled_for=timezone.now() + timedelta(minutes=20), timeout_sec=1800,
            inputs_json=json.dumps({"construction_plan_json": steps, "construction_plan_steps": preview, "queue_strategy": "smart"}),
        )
        # Beta waits for crystal (the plan stores it as "glas"); the reservation carries the id as the city name
        ConstructionResourceReservation.objects.create(
            job=self.plan, account=self.account, game_account=self.busy, city_id="2", city_name="2", resource="glas",
            reserved_local_amount=500, shortfall_amount=182527, status="active",
        )
        JobLog.objects.create(job=self.plan, level="warn", message="Nenhuma cidade doadora com sobra util para Beta")
        self.client.force_login(self.user)

    def _game_account(self, number, name):
        return GameAccount.objects.create(
            account=self.account, lobby_account_id=number, server_id=f"s{number}-br", server_language="br", server_number=number, name=name,
        )

    def test_every_city_gets_its_state(self):
        response = self.client.get(reverse("game:construction"))
        self.assertEqual(response.status_code, 200)

        accounts = response.context["board_accounts"]
        self.assertEqual([acc["name"] for acc in accounts], ["Obreiro"])
        cities = {city["name"]: city for city in accounts[0]["cities"]}
        self.assertEqual({name: city["state"] for name, city in cities.items()},
                         {"Alfa": "working", "Beta": "waiting", "Gama": "queued", "Delta": "idle"})
        self.assertEqual([city["name"] for city in accounts[0]["cities"]], ["Beta", "Alfa", "Gama", "Delta"])   # what needs attention first
        self.assertEqual((cities["Alfa"]["work"]["name"], cities["Alfa"]["work"]["to_level"]), ("Academia", 6))
        self.assertEqual([step["name"] for step in cities["Alfa"]["queue"]], ["Academia", "Armazém"])
        self.assertEqual([(item["key"], item["amount"], item["text"]) for item in cities["Beta"]["missing"]], [("crystal", 182527, "182,5 mil")])
        self.assertEqual(cities["Alfa"]["free_slots"], 1)
        self.assertEqual(response.context["kpi_cities"], {"total": 5, "working": 1, "waiting": 1, "queued": 1, "idle": 2})

    def test_idle_cities_are_named_with_a_way_to_act(self):
        response = self.client.get(reverse("game:construction"))

        groups = response.context["idle_groups"]
        self.assertEqual([(group["name"], group["counts"]["total"], group["counts"]["free_slots"]) for group in groups], [("Parado", 1, 1)])
        self.assertEqual([city["name"] for city in response.context["idle_strays"]], ["Delta"])
        self.assertContains(response, "1 cidade ociosa")
        self.assertContains(response, f'{reverse("jobs:job-form")}?ga={self.lazy.pk}&action=1002')
        self.assertContains(response, "Criar plano de construção")

    def test_a_plan_shows_cost_time_what_is_missing_and_its_last_warning(self):
        response = self.client.get(reverse("game:construction"))

        plan = response.context["plans"][0]
        self.assertEqual((plan["total"], plan["done"], plan["cities_total"], plan["cities_pending"], plan["cities_waiting"]), (4, 0, 3, 3, 1))
        self.assertEqual({item["key"]: item["amount"] for item in plan["remaining_cost"]},
                         {"wood": 100 * (6 + 7 + 3 + 4 + 2 + 3 + 2), "crystal": 10 * (6 + 7 + 3 + 4 + 2 + 3 + 2)})
        self.assertEqual(plan["eta_seconds"], 40000 * 5)                      # Beta is the slowest city
        self.assertEqual([(item["key"], item["amount"]) for item in plan["missing"]], [("crystal", 182527)])
        self.assertEqual(plan["issue"]["text"], "Nenhuma cidade doadora com sobra util para Beta")
        self.assertEqual(plan["next_run_in"], "19 min")
        self.assertContains(response, "Último aviso do plano")
        self.assertContains(response, "próximo ciclo em 19 min")
        # the tools of the plan card are still there
        self.assertContains(response, f"planCard('{self.plan.pk}', 'smart')")
        self.assertContains(response, "Editar etapas")

    def test_the_bottleneck_list_names_the_city_and_counts_crystal(self):
        response = self.client.get(reverse("game:construction"))

        rows = response.context["bottlenecks"]
        self.assertEqual([(row["city_name"], row["ga_name"], row["total"], row["job_id"]) for row in rows],
                         [("Beta", "Obreiro", 182527, str(self.plan.pk))])
        crystal = next(item for item in response.context["reserved_total"] if item["key"] == "crystal")
        self.assertEqual((crystal["reserved"], crystal["shortfall"]), (500, 182527))   # used to be dropped as "glas"

    def test_the_three_questions_and_the_old_tools_are_on_the_page(self):
        response = self.client.get(reverse("game:construction"))
        for block in ("cb-summary", "cb-now", "cb-stopped", "cb-plans"):
            self.assertContains(response, f'id="{block}"')
        self.assertContains(response, "Mapa de níveis")
        self.assertContains(response, 'id="cs-reorder-layouts"')
        self.assertContains(response, "Reorganizar</button>")

    def test_the_page_opens_with_nothing_to_show(self):
        Job.objects.all().delete()
        AccountSnapshot.objects.all().delete()
        response = self.client.get(reverse("game:construction"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nenhuma conta com obra ou plano de construção agora.")
        self.assertContains(response, "Nenhum plano de construção ativo.")
