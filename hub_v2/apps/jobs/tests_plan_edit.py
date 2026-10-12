"""N-108: prioritizing and cutting steps of a running construction plan reach the list the agent builds from."""

import copy
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import Job
from apps.jobs.services import plan_edit


def _rows(first, last):
    return [{"level": level, "costs": {"wood": 100 * level, "wine": 0, "marble": 10 * level, "glas": 0, "sulfur": 0},
             "base_seconds": 60 * level, "adjusted_seconds": 120 * level} for level in range(first, last + 1)]


def _plan():
    """Two cities; the barracks of Alfa goes 4 -> 35, like the case Rodrigo described."""
    specs = [
        (1, "1", "Alfa", "barracks", "Quartel", 4, 35),
        (2, "1", "Alfa", "warehouse", "Armazém", 10, 12),
        (3, "1", "Alfa", "academy", "Academia", 5, 9),
        (4, "2", "Beta", "barracks", "Quartel", 1, 3),
    ]
    steps, previews = [], []
    for index, city_id, city, building, name, start, target in specs:
        base = {"index": index, "city_id": city_id, "city_name": city, "building_id": building, "building_name": name,
                "building_position": "3", "mode": "upgrade", "target_level": target}
        rows = _rows(start + 1, target)
        totals = {key: sum(row["costs"][key] for row in rows) for key in plan_edit.RESOURCES}
        steps.append(dict(base))
        previews.append({
            **base, "from_level": start, "level_rows": rows, "totals": totals,
            "reserved_local": {key: totals[key] // 2 for key in plan_edit.RESOURCES},
            "missing": {key: totals[key] - totals[key] // 2 for key in plan_edit.RESOURCES},
            "base_seconds": sum(row["base_seconds"] for row in rows),
            "adjusted_seconds": sum(row["adjusted_seconds"] for row in rows),
        })
    inputs = {"construction_plan_json": steps, "construction_plan_steps": previews, "queue_strategy": "smart"}
    plan_edit.recompute_summary(inputs)
    return inputs


def _marks(inputs, key):
    return {step["index"]: (step.get("priority_order", 0), step.get("priority_level", 0)) for step in inputs[key]}


class PlanEditServiceTests(TestCase):
    def test_a_priority_lands_on_both_lists(self):
        inputs = _plan()
        message = plan_edit.set_priority(inputs, 1, 30)

        self.assertEqual(message, "Prioridade 1 em Alfa: Quartel até o nível 30.")
        for key in ("construction_plan_json", "construction_plan_steps"):     # the agent reads the second
            self.assertEqual(_marks(inputs, key)[1], (1, 30), key)
            self.assertEqual(_marks(inputs, key)[2], (0, 0), key)

    def test_priorities_are_numbered_in_order_inside_each_city(self):
        inputs = _plan()
        plan_edit.set_priority(inputs, 1, 30)
        plan_edit.set_priority(inputs, 3, 9)
        plan_edit.set_priority(inputs, 4, 2)           # another city: its own 1
        self.assertEqual(_marks(inputs, "construction_plan_steps"), {1: (1, 30), 2: (0, 0), 3: (2, 9), 4: (1, 2)})

        plan_edit.set_priority(inputs, 1, 20)          # changing the level keeps the place in the order
        self.assertEqual(_marks(inputs, "construction_plan_steps")[1], (1, 20))

        plan_edit.clear_priority(inputs, 1)            # what was second becomes first
        self.assertEqual(_marks(inputs, "construction_plan_steps"), {1: (0, 0), 2: (0, 0), 3: (1, 9), 4: (1, 2)})
        self.assertNotIn("priority_order", inputs["construction_plan_json"][0])

    def test_the_level_has_to_be_inside_the_step(self):
        inputs = _plan()
        for level in (36, -1):
            with self.assertRaises(plan_edit.PlanEditError):
                plan_edit.set_priority(inputs, 1, level)
        with self.assertRaises(plan_edit.PlanEditError):
            plan_edit.set_priority(inputs, 99, 5)
        self.assertEqual(plan_edit.set_priority(inputs, 2, 0), "Prioridade 1 em Alfa: Armazém até o nível 12.")   # no level: the whole step

    def test_cutting_a_level_takes_it_and_the_ones_above(self):
        # "se eu remover o 30, ele tem que tirar do 30 ao 35"
        inputs = _plan()
        before = copy.deepcopy(inputs)
        message = plan_edit.cut_step(inputs, 1, 30, current_level=4)

        self.assertEqual(message, "Quartel em Alfa agora vai até o nível 29 (níveis 30 a 35 retirados).")
        self.assertEqual(inputs["construction_plan_json"][0]["target_level"], 29)
        preview = inputs["construction_plan_steps"][0]
        self.assertEqual(preview["target_level"], 29)
        self.assertEqual([row["level"] for row in preview["level_rows"]], list(range(5, 30)))
        removed_wood = sum(100 * level for level in range(30, 36))
        self.assertEqual(preview["totals"]["wood"], before["construction_plan_steps"][0]["totals"]["wood"] - removed_wood)
        self.assertEqual(preview["adjusted_seconds"], sum(120 * level for level in range(5, 30)))
        self.assertLessEqual(preview["reserved_local"]["wood"], preview["totals"]["wood"])
        self.assertEqual(preview["missing"]["wood"], preview["totals"]["wood"] - preview["reserved_local"]["wood"])
        # the totals of the plan follow
        self.assertEqual(inputs["construction_summary"]["totals"]["wood"], before["construction_summary"]["totals"]["wood"] - removed_wood)
        self.assertEqual(inputs["construction_summary"]["steps"], 4)
        # the other steps were not touched
        self.assertEqual(inputs["construction_plan_steps"][1:], before["construction_plan_steps"][1:])

    def test_cutting_everything_that_is_left_takes_the_step_out_of_both_lists(self):
        inputs = _plan()
        plan_edit.set_priority(inputs, 3, 9)
        plan_edit.set_priority(inputs, 2, 12)
        message = plan_edit.cut_step(inputs, 3, 6, current_level=5)       # academy is at 5: nothing left

        self.assertEqual(message, "Academia saiu do plano em Alfa.")
        for key in ("construction_plan_json", "construction_plan_steps"):
            self.assertEqual([step["index"] for step in inputs[key]], [1, 2, 4], key)
        self.assertEqual(_marks(inputs, "construction_plan_steps")[2], (1, 12))       # the priority left behind moves up
        self.assertEqual(inputs["construction_summary"]["steps"], 3)

    def test_a_cut_below_a_priority_brings_the_priority_down(self):
        inputs = _plan()
        plan_edit.set_priority(inputs, 1, 30)
        plan_edit.cut_step(inputs, 1, 21, current_level=4)
        self.assertEqual(_marks(inputs, "construction_plan_steps")[1], (1, 20))

    def test_the_editor_state_names_every_step_by_its_index(self):
        inputs = _plan()
        plan_edit.set_priority(inputs, 1, 30)
        self.assertEqual(plan_edit.editor_state(inputs)[:2],
                         [{"id": 1, "target": 35, "p_order": 1, "p_level": 30}, {"id": 2, "target": 12, "p_order": 0, "p_level": 0}])


class PlanPatchEndpointTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="plan-user", email="plan@example.com", password="secret123")
        self.node = Node.objects.create(name="node-plan")
        self.account = Account.objects.create(node=self.node, label="Lobby Plan", email="p@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=1, server_id="s1-br", server_language="br", server_number=1, name="Planejador",
        )
        self.job = Job.objects.create(
            account=self.account, game_account=self.ga, node=self.node, action_code=1002, status="scheduled",
            inputs_json=json.dumps(_plan()), timeout_sec=1800,
        )
        self.url = reverse("jobs:job-plan-patch", args=[self.job.pk])
        self.client.force_login(self.user)

    def _inputs(self):
        self.job.refresh_from_db()
        return json.loads(self.job.inputs_json)

    def test_prioritize_is_stored_where_the_agent_reads_and_survives_its_next_cycle(self):
        response = self.client.post(self.url, {"action": "prioritize", "step": 1, "level": 30})
        data = response.json()

        self.assertTrue(data["ok"])
        self.assertIn("Quartel até o nível 30", data["message"])
        self.assertEqual(data["steps"][0], {"id": 1, "target": 35, "p_order": 1, "p_level": 30})
        inputs = self._inputs()
        self.assertEqual(_marks(inputs, "construction_plan_steps")[1], (1, 30))
        # kept as a pending patch too, so the inputs the agent sends back at the end of a cycle do not undo it
        self.assertEqual(_marks(inputs["__pending_patch"], "construction_plan_steps")[1], (1, 30))

    def test_two_cuts_in_a_row_hit_the_steps_that_were_chosen(self):
        # the old screen sent positions; after the first removal the second one hit the step after the chosen
        self.client.post(self.url, {"action": "cut", "step": 2, "level": 11, "current": 10})      # warehouse leaves
        response = self.client.post(self.url, {"action": "cut", "step": 3, "level": 8, "current": 5})

        self.assertTrue(response.json()["ok"])
        inputs = self._inputs()
        self.assertEqual([(s["index"], s["target_level"]) for s in inputs["construction_plan_steps"]], [(1, 35), (3, 7), (4, 3)])
        self.assertEqual([(s["index"], s["target_level"]) for s in inputs["construction_plan_json"]], [(1, 35), (3, 7), (4, 3)])
        self.assertEqual([s["id"] for s in response.json()["steps"]], [1, 3, 4])

    def test_the_strategy_still_changes(self):
        response = self.client.post(self.url, {"queue_strategy": "fifo"})
        self.assertTrue(response.json()["ok"])
        self.assertEqual(self._inputs()["queue_strategy"], "fifo")

    def test_bad_requests_change_nothing(self):
        before = self._inputs()
        for payload in ({"action": "cut", "step": 1, "level": 99}, {"action": "prioritize", "step": 77, "level": 3},
                        {"action": "explode", "step": 1}, {"action": "cut", "step": "x"}, {}):
            response = self.client.post(self.url, payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertFalse(response.json()["ok"])
        self.assertEqual(self._inputs(), before)

    def test_a_plan_that_is_over_cannot_be_edited(self):
        Job.objects.filter(pk=self.job.pk).update(status="finished")
        response = self.client.post(self.url, {"action": "prioritize", "step": 1, "level": 30})
        self.assertEqual(response.status_code, 409)
