"""N-57: Demolir Edificios (ac=1303) no fluxo de Acoes do Jogo.

Catalogo -> formulario (partial proprio) -> envio pelo JobSubmitView. So cria
jobs depois de conferir cada item contra o snapshot e das confirmacoes.
"""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.jobs.services.recovery import SAFE_REQUEUE_ACTIONS
from core.actions import ACTION_CATALOG
from core.contracts import get_actions_for_ui


def _building(position, building, level, **extra):
    return {"position": position, "building": building, "level": level, "is_upgrading": False, **extra}


class DemolishActionFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="demolish-tester", email="demolish@example.com", password="secret123",
        )
        self.account = Account.objects.create(
            node=Node.objects.create(name="agent-demolish"),
            label="Lobby Demolish", email="demolish-lobby@example.com", password_enc="enc",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=777, server_id="s9-br",
            server_language="br", server_number=9, name="DemolishPlayer",
        )
        AccountSnapshot.objects.create(
            account=self.account, game_account=self.ga, base_snapshot={}, military={},
            cities=[
                {"id": "501", "name": "Alfa", "tradegood": 1, "buildings": [
                    _building(0, "townHall", 10),
                    _building(1, "warehouse", 5),
                    _building(2, "empty", 0),
                    _building(3, "academy", 7, is_upgrading=True),
                    _building(5, "palaceColony", 4),
                ]},
                {"id": "502", "name": "Beta", "tradegood": 2, "buildings": [
                    _building(0, "townHall", 8),
                    _building(4, "tavern", 3),
                ]},
            ],
        )
        self.client.force_login(self.user)

    @staticmethod
    def _item(city_id, position, building, level, mode="levels", levels=1):
        return {"city_id": city_id, "position": position, "building": building, "level": level, "mode": mode, "levels": levels}

    def _submit(self, items, **extra):
        data = {
            "game_account": str(self.ga.pk), "action_code": "1303",
            "demolish_items_json": json.dumps(items),
            "demolish_acknowledge": "on", "demolish_confirm_word": "DEMOLIR",
        }
        data.update(extra)
        data = {k: v for k, v in data.items() if v is not None}
        return self.client.post(reverse("jobs:job-submit"), data)

    def _jobs(self):
        return Job.objects.filter(game_account=self.ga, action_code=1303).order_by("created_at")

    # ── catalogo / entrada ──

    def test_action_is_listed_in_the_game_actions_catalog(self):
        action = ACTION_CATALOG[1303]
        self.assertTrue(action["ready"])
        self.assertFalse(action.get("ui_hidden"))
        self.assertNotIn(10, ACTION_CATALOG)            # the old "em breve" entry is gone
        self.assertNotIn(1303, SAFE_REQUEUE_ACTIONS)    # recovery must never repeat a demolition
        listed = {a["code"]: a for g in get_actions_for_ui() if g["key"] == "construction" for a in g["actions"]}
        self.assertTrue(listed[1303]["ready"])

        page = self.client.get(reverse("game:action-catalog")).content.decode()
        self.assertIn("Demolir Edificios", page)
        self.assertIn("?action=1303", page)

    def test_form_shows_every_city_with_the_games_own_building_names(self):
        response = self.client.get(reverse("jobs:job-form"), {"ga": str(self.ga.pk), "action": "1303"})

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("demolishForm(", html)
        self.assertIn('name="demolish_items_json"', html)
        self.assertIn('name="demolish_confirm_word"', html)
        self.assertIn('name="dry_run"', html)
        self.assertNotIn("Nenhuma configuracao necessaria", html)
        cities = response.context["demolish_ui"]["cities"] if response.context else None
        if cities is None:
            from apps.jobs.services.demolish import demolish_form_context
            cities = demolish_form_context(AccountSnapshot.objects.get(game_account=self.ga).cities)["demolish_ui"]["cities"]
        alfa = {b["building"]: b for b in cities[0]["buildings"]}
        self.assertEqual([c["name"] for c in cities], ["Alfa", "Beta"])
        self.assertNotIn("empty", alfa)
        self.assertIn("palaceColony", alfa)             # raw game key, not the planner's alias
        self.assertTrue(alfa["townHall"]["blocked"])
        self.assertTrue(alfa["academy"]["blocked"])
        self.assertEqual((alfa["warehouse"]["blocked"], alfa["warehouse"]["level"]), ("", 5))

    def test_panels_open_the_standard_form_and_the_old_modal_is_gone(self):
        link = f"{reverse('jobs:job-form')}?ga={self.ga.pk}&action=1303"
        panel = self.client.get(reverse("game:construction")).content.decode()
        self.assertIn(link, panel)
        self.assertNotIn("demolishModal", panel)
        dashboard = self.client.get(reverse("game:dashboard")).content.decode()
        self.assertIn(link, dashboard)
        with self.assertRaises(NoReverseMatch):
            reverse("game:demolish-buildings")

    # ── envio ──

    def test_creates_one_job_per_city_with_what_the_user_saw(self):
        response = self._submit([
            self._item("501", 1, "warehouse", 5, levels=2),
            self._item("502", 4, "tavern", 3, mode="complete"),
        ])

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response["HX-Trigger"])["jobsCreated"])
        jobs = {json.loads(j.inputs_json)["city_id"]: json.loads(j.inputs_json) for j in self._jobs()}
        self.assertEqual(set(jobs), {"501", "502"})
        self.assertEqual(jobs["501"]["city_name"], "Alfa")
        self.assertFalse(jobs["501"]["dry_run"])
        self.assertEqual(
            {k: jobs["501"]["items"][0][k] for k in ("position", "building", "level", "levels", "complete")},
            {"position": 1, "building": "warehouse", "level": 5, "levels": 2, "complete": False},
        )
        self.assertEqual((jobs["502"]["items"][0]["levels"], jobs["502"]["items"][0]["complete"]), (3, True))

    def test_complete_on_a_level_one_building_is_sent_as_its_last_level(self):
        snapshot = AccountSnapshot.objects.get(game_account=self.ga)
        snapshot.cities[1]["buildings"].append(_building(6, "academy", 1))
        snapshot.save()

        self._submit([self._item("502", 6, "academy", 1, mode="complete")])

        item = json.loads(self._jobs().get().inputs_json)["items"][0]
        self.assertEqual((item["level"], item["levels"], item["complete"]), (1, 1, False))

    def test_needs_both_confirmations_and_gives_the_form_back(self):
        items = [self._item("501", 1, "warehouse", 5, levels=2)]
        for extra in (
            {"demolish_confirm_word": ""},
            {"demolish_confirm_word": "SIM"},
            {"demolish_confirm_word": "DEMOLIR TUDO"},
            {"demolish_acknowledge": None},
        ):
            response = self._submit(items, **extra)
            html = response.content.decode()
            self.assertEqual(self._jobs().count(), 0, extra)
            self.assertIn("demolishForm(", html)                       # the form, not a dead end
            self.assertEqual(json.loads(response["HX-Trigger"])["toast"]["type"], "error")
            self.assertIn('"levels": 2', html)                         # the choice comes back with it

    def test_confirmation_word_is_not_case_sensitive(self):
        # the field shows upper case whatever is typed
        self._submit([self._item("501", 1, "warehouse", 5)], demolish_confirm_word="demolir")
        self.assertEqual(self._jobs().count(), 1)

    def test_dry_run_needs_no_confirmation_and_is_flagged_on_the_job(self):
        response = self._submit(
            [self._item("501", 1, "warehouse", 5)],
            dry_run="1", demolish_confirm_word="", demolish_acknowledge=None,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(self._jobs().get().inputs_json)["dry_run"])

    def test_refuses_anything_that_does_not_match_the_snapshot(self):
        bad_lists = [
            [self._item("501", 0, "townHall", 10)],                    # town hall, never
            [self._item("501", 1, "tavern", 5)],                       # another building there
            [self._item("501", 1, "warehouse", 9)],                    # stale level
            [self._item("501", 1, "warehouse", 5, levels=6)],          # more levels than it has
            [self._item("501", 1, "warehouse", 5, levels=0)],
            [self._item("501", 2, "empty", 0)],                        # empty spot
            [self._item("501", 3, "academy", 7)],                      # under construction
            [self._item("999", 1, "warehouse", 5)],                    # city of another account
            [self._item("501", 1, "warehouse", 5), self._item("501", 1, "warehouse", 5)],  # twice
            [self._item("501", 1, "warehouse", 5), self._item("502", 4, "tavern", 9)],     # one bad cancels all
            [],
            "lixo",
        ]
        for items in bad_lists:
            for dry_run in (None, "1"):
                response = self._submit(items, dry_run=dry_run)
                self.assertEqual(json.loads(response["HX-Trigger"])["toast"]["type"], "error", items)
        self.assertEqual(self._jobs().count(), 0)

    def test_login_required(self):
        self.client.logout()
        response = self._submit([self._item("501", 1, "warehouse", 5)])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._jobs().count(), 0)
