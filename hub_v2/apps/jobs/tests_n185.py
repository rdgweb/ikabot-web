"""N-185: merging steps into a running construction plan keeps what the agent marked on the same steps, never adds a
level over the target, never duplicates a step, and keeps one set of reservations, on the plan's root job."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.jobs.models import ConstructionResourceReservation, Job
from apps.jobs.services import construction_preview
from apps.jobs.views import create


def _rows(last):
    return [{"level": level, "wood": 100 * level, "marble": 0, "wine": 0, "glass": 0, "sulfur": 0,
             "building_time": "1m"} for level in range(1, last + 1)]


class PreviewTargetTests(TestCase):
    def setUp(self):
        self.ga = mock.Mock()
        city = {"id": "1", "name": "Alfa", "wood": 10_000, "marble": 0, "wine": 0, "crystal": 0, "sulfur": 0,
                "buildings": [{"building": "optician", "level": 31, "position": 7}]}
        tables = {key: _rows(40) for key in set(construction_preview.BUILDING_TO_IKA_TOOLS.values())}
        self.patches = [mock.patch.object(construction_preview, "get_ika_tools_tables", return_value=tables),
                        mock.patch.object(construction_preview, "_get_snapshot_city", return_value=city)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()

    def _preview(self, target):
        return construction_preview.build_construction_plan_preview(game_account=self.ga, steps=[{
            "city_id": "1", "building_id": "optician", "building_position": "7", "mode": "upgrade", "target_level": target}])

    def test_a_step_already_at_target_builds_nothing_more(self):
        step = self._preview(30).steps[0]
        self.assertTrue(step["done"])
        self.assertEqual((step["from_level"], step["target_level"], step["level_rows"]), (31, 30, []))

    def test_a_step_below_target_still_goes_to_the_target(self):
        step = self._preview(33).steps[0]
        self.assertEqual([row["level"] for row in step["level_rows"]], [32, 33])


class MergeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="n185", email="n185@example.com", password="x")
        node = Node.objects.create(name="node-n185")
        account = Account.objects.create(node=node, label="Lobby", email="l@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s1-br",
                                             server_language="br", server_number=1, name="Monarca")
        plan = [
            {"index": 1, "city_id": "1", "building_id": "warehouse", "mode": "upgrade", "building_position": "3", "target_level": 9},
            {"index": 2, "city_id": "1", "building_id": "academy", "mode": "upgrade", "building_position": "4", "target_level": 9},
            {"index": 3, "city_id": "2", "building_id": "tavern", "mode": "new", "preferred_position": "8", "target_level": 5},
        ]
        inputs = {"construction_plan_json": plan, "construction_plan_steps": [dict(s) for s in plan],
                  "skipped_step_indices": [2], "blocked_step_indices": [3], "unconfirmed_steps": {"2:3": {"n": 2, "until": 0}}}
        self.root = Job.objects.create(account=account, game_account=self.ga, node=node, action_code=1002,
                                       status="finished", inputs_json=json.dumps(inputs), timeout_sec=1800)
        self.job = Job.objects.create(account=account, game_account=self.ga, node=node, action_code=1002,
                                      status="scheduled", inputs_json=json.dumps(inputs), timeout_sec=1800,
                                      root_job_id=self.root.pk)
        for job in (self.root, self.job):
            ConstructionResourceReservation.objects.create(job=job, account=account, game_account=self.ga, city_id="1",
                                                           city_name="Alfa", resource="wood", reserved_local_amount=10)

    def _merge(self, new_steps):
        def preview(*, game_account, steps, **_kwargs):
            # the new steps cost nothing, so the reorder puts them first and every old number changes
            out = []
            for i, step in enumerate(steps, start=1):
                cost = 0 if step.get("building_id") in ("barracks", "port") else 100 * i
                out.append({**step, "index": i, "city_name": "Alfa", "level_rows": [{"level": 1, "costs": {"wood": cost}, "base_seconds": cost}],
                            "missing": {}, "reserved_local": {"wood": 5}, "totals": {"wood": cost}})
            return construction_preview.ConstructionPlanPreview(
                steps=out, totals={}, reserved_local={}, missing={}, base_seconds=0, adjusted_seconds=0, notes=[])

        view = create.JobSubmitView()
        request = RequestFactory().post("/")
        request.user = self.user
        with mock.patch.object(create, "build_construction_plan_preview", side_effect=preview), \
                mock.patch.object(create.JobSubmitView, "_render_success", return_value=""):
            view._merge_construction_plan(request, self.job, self.ga, new_steps, {"name": "Plano"})
        self.job.refresh_from_db()
        return json.loads(self.job.inputs_json)

    def test_marks_follow_their_steps_and_no_duplicate(self):
        inputs = self._merge([
            {"city_id": "1", "building_id": "barracks", "mode": "upgrade", "building_position": "5", "target_level": 3},
            {"city_id": "1", "building_id": "warehouse", "mode": "upgrade", "building_position": "3", "target_level": 9},  # already there
        ])
        plan = inputs["construction_plan_json"]
        self.assertEqual(len(plan), 4)
        by_building = {step["building_id"]: step["index"] for step in plan}
        self.assertEqual(by_building["barracks"], 1)
        self.assertEqual(inputs["skipped_step_indices"], [by_building["academy"]])
        self.assertEqual(inputs["blocked_step_indices"], [by_building["tavern"]])
        self.assertEqual(list(inputs["unconfirmed_steps"]), [f"2:{by_building['tavern']}"])
        self.assertTrue(all("_old_index" not in step for step in plan))

    def test_reservations_live_once_on_the_root(self):
        self._merge([{"city_id": "1", "building_id": "port", "mode": "upgrade", "building_position": "1", "target_level": 2}])
        active = ConstructionResourceReservation.objects.filter(game_account=self.ga, status="active")
        self.assertEqual({row.job_id for row in active}, {self.root.pk})
        self.assertFalse(ConstructionResourceReservation.objects.filter(job=self.job).exists())
