"""N-138: Gerenciar Treino Militar takes troops and fleet in one request (one workflow, one job per kind),
Distribuir really splits the training, the per-city targets keep cities without barracks, resuming restarts every
branch, and the presets keep what was chosen and decide the cities by a rule instead of one account's cities."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.profiles.models import Preset, PresetAction, PresetGameAccount
from apps.profiles.services import execute_preset

from .models import Job, Workflow
from .services.training import split_by_kind

HOPLITE, ARCHER, TRIREME = "303", "313", "210"


def _city(cid, name, *buildings):
    return {"id": cid, "name": name, "tradegood": 1, "x": 10, "y": cid,
            "buildings": [{"building": b, "position": i + 3, "level": 5} for i, b in enumerate(buildings)]}


class _TrainingSetup(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("rodrigo", password="x", is_staff=True)
        self.client.force_login(self.user)
        self.node = Node.objects.create(name="agente", transport="https")
        self.account = Account.objects.create(node=self.node, label="Conta", email="c@example.com", password_enc="x")
        self.ga = GameAccount.objects.create(account=self.account, lobby_account_id=1, server_id="s78-br",
                                             server_language="br", server_number=78, name="Executor")
        AccountSnapshot.objects.create(account=self.account, game_account=self.ga, base_snapshot={}, military={},
                                       cities=[_city(1, "Porto", "barracks", "shipyard"), _city(2, "Quartel", "barracks"),
                                               _city(3, "Vila")])

    def _submit(self, **fields):
        data = {"game_account": str(self.ga.pk), "action_code": "1005", "building_type": "troops", "recheck_minutes": "120"}
        data.update(fields)
        return self.client.post("/jobs/new/submit/", data)

    def _jobs(self):
        return [(job, json.loads(job.inputs_json)) for job in Job.objects.filter(action_code=1005).order_by("created_at")]


class TrainingRequestTests(_TrainingSetup):

    def test_split_keeps_each_kind_apart(self):
        parts = split_by_kind({"mode": "maintain", "building_type": "fleet", "position": 7,
                               "target_garrison": {HOPLITE: 10, TRIREME: 4},
                               "city_targets": {"3": {HOPLITE: 20}, "1": {TRIREME: 6}}})
        self.assertEqual([p["building_type"] for p in parts], ["troops", "fleet"])
        self.assertEqual((parts[0]["target_garrison"], parts[1]["target_garrison"]), ({HOPLITE: 10}, {TRIREME: 4}))
        self.assertEqual((parts[0]["city_targets"], parts[1]["city_targets"]), ({"3": {HOPLITE: 20}}, {"1": {TRIREME: 6}}))
        self.assertEqual({p["position"] for p in parts}, {0})

    def test_troops_and_fleet_in_one_request_make_one_workflow(self):
        self._submit(mode="maintain", maintain_scope="selected_custom", city_ids=["1", "3"],
                     **{f"units_{HOPLITE}": "10", f"units_{TRIREME}": "4"},
                     city_targets_json=json.dumps({"3": {HOPLITE: 25}, "1": {TRIREME: 6}}))
        jobs = self._jobs()
        self.assertEqual([i["building_type"] for _, i in jobs], ["troops", "fleet"])
        self.assertEqual(len({job.workflow_id for job, _ in jobs}), 1)
        troops = jobs[0][1]
        self.assertEqual(troops["city_targets"], {"3": {HOPLITE: 25}})        # a city without barracks keeps its target
        self.assertEqual(troops["city_ids"], ["1", "3"])

    def test_distribute_splits_evenly_across_the_chosen_cities_that_train(self):
        self._submit(mode="distribute", city_ids=["1", "2", "3"], **{f"units_{HOPLITE}": "7", f"units_{TRIREME}": "3"})
        jobs = self._jobs()
        troops = [(i["city_id"], i["units"]) for _, i in jobs if i["building_type"] == "troops"]
        fleet = [(i["city_id"], i["units"]) for _, i in jobs if i["building_type"] == "fleet"]
        self.assertEqual(troops, [("1", {HOPLITE: 4}), ("2", {HOPLITE: 3})])   # "Vila" has no barracks
        self.assertEqual(fleet, [("1", {TRIREME: 3})])                          # only "Porto" has a shipyard
        self.assertTrue(all(i["mode"] == "once" for _, i in jobs))
        self.assertEqual(len({job.workflow_id for job, _ in jobs}), 1)

    def test_resume_restarts_troops_and_fleet(self):
        self._submit(mode="maintain", maintain_scope="selected_uniform", city_ids=["1"],
                     **{f"units_{HOPLITE}": "10", f"units_{TRIREME}": "4"})
        workflow = Workflow.objects.get()
        Job.objects.filter(workflow=workflow).update(status="cancelled")
        Workflow.objects.filter(pk=workflow.pk).update(status="paused")
        self.client.post(f"/jobs/workflows/{workflow.pk}/action/", {"workflow_action": "resume"})
        resumed = Job.objects.filter(workflow=workflow, status="scheduled")
        self.assertEqual(sorted(json.loads(j.inputs_json)["building_type"] for j in resumed), ["fleet", "troops"])

    def test_the_form_comes_back_with_both_kinds_and_every_city(self):
        html = self.client.get("/jobs/new/form/", {"ga": str(self.ga.pk), "action": "1005"}).content.decode()
        self.assertIn("data-training-summary", html)
        self.assertNotIn("Consolidação opcional", html)


class TrainingPresetTests(_TrainingSetup):
    def _preset(self, action_code):
        preset = Preset.objects.create(name="Treino", created_by=self.user)
        PresetGameAccount.objects.create(preset=preset, game_account=self.ga)
        PresetAction.objects.create(preset=preset, action_code=action_code, action_name="x", order=1,
                                    inputs_json="{}", per_account_json="{}")
        return preset

    def test_the_preset_keeps_both_kinds_and_spreads_them_by_rule(self):
        preset = self._preset(1005)
        self.client.post(f"/profiles/presets/{preset.pk}/configure/1/", {
            "game_account": str(self.ga.pk), "action_code": "1005", "building_type": "troops", "mode": "maintain",
            "maintain_scope": "local", "preset_cities": "training", "recheck_minutes": "120",
            f"units_{HOPLITE}": "10", f"units_{TRIREME}": "4", "city_id": "1"})
        saved = json.loads(PresetAction.objects.get(preset=preset).inputs_json)
        self.assertEqual(saved["target_garrison"], {HOPLITE: 10, TRIREME: 4})
        self.assertEqual((saved["maintain_scope"], saved["preset_cities"]), ("selected_uniform", "training"))
        self.assertNotIn("city_id", saved)
        created, errors = execute_preset(preset, created_by=self.user)
        self.assertEqual((created, errors), (2, []))
        jobs = {i["building_type"]: i for _, i in self._jobs()}
        self.assertEqual(jobs["troops"]["city_ids"], ["1", "2"])               # cities with barracks
        self.assertEqual(jobs["fleet"]["city_ids"], ["1"])                     # cities with a shipyard
        self.assertEqual(len({j.workflow_id for j, _ in self._jobs()}), 1)

    def test_the_preset_screen_shows_no_account_cities(self):
        preset = self._preset(1005)
        html = self.client.get(f"/profiles/presets/{preset.pk}/configure/1/").content.decode()
        self.assertNotIn('name="city_mode"', html)
        self.assertIn("isPreset: true", html)

    def test_the_unit_upgrade_preset_keeps_the_choice_and_uses_every_workshop(self):
        preset = self._preset(1203)
        self.client.post(f"/profiles/presets/{preset.pk}/configure/1/", {
            "game_account": str(self.ga.pk), "action_code": "1203", "city_id": "1", "priority_mode": "defensive_first",
            "min_crystal_reserve": "500", "unit_targets_json": json.dumps([{"key": "hoplita", "attack_max": 5, "defense_max": None}])})
        saved = json.loads(PresetAction.objects.get(preset=preset).inputs_json)
        self.assertEqual(saved["unit_targets"], [{"key": "hoplita", "attack_max": 5, "defense_max": None}])
        self.assertEqual((saved["priority_mode"], saved["min_crystal_reserve"]), ("defensive_first", 500))
        self.assertNotIn("city_id", saved)
        execute_preset(preset, created_by=self.user)
        job = Job.objects.get(action_code=1203)
        self.assertNotIn("city_id", json.loads(job.inputs_json))
        html = self.client.get(f"/profiles/presets/{preset.pk}/configure/1/").content.decode()
        self.assertIn("Todas com Oficina", html)
        self.assertIn('"priority_mode": "defensive_first"', html)                  # comes back filled in
