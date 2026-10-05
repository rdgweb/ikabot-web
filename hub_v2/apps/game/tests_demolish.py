"""N-57: demolir edificios -- o painel so cria jobs ac=1303 depois de conferir
cada item contra o snapshot e de receber a palavra de confirmacao."""

import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.jobs.models import Job
from apps.jobs.services.recovery import SAFE_REQUEUE_ACTIONS
from core.actions import ACTION_CATALOG


def _building(position, building, level, **extra):
    return {"position": position, "building": building, "level": level, "is_upgrading": False, **extra}


class DemolishBuildingsTests(TestCase):
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
                ]},
                {"id": "502", "name": "Beta", "tradegood": 2, "buildings": [
                    _building(0, "townHall", 8),
                    _building(4, "tavern", 3),
                ]},
            ],
        )
        self.client.force_login(self.user)

    def _post(self, items, **extra):
        data = {"game_account_id": str(self.ga.pk), "items": json.dumps(items), "confirm": "DEMOLIR", **extra}
        return self.client.post(reverse("game:demolish-buildings"), data)

    @staticmethod
    def _item(city_id, position, building, level, mode="levels", levels=1):
        return {"city_id": city_id, "position": position, "building": building, "level": level, "mode": mode, "levels": levels}

    def _jobs(self):
        return Job.objects.filter(game_account=self.ga, action_code=1303).order_by("created_at")

    def test_creates_one_job_per_city_with_what_the_user_saw(self):
        response = self._post([
            self._item("501", 1, "warehouse", 5, levels=2),
            self._item("502", 4, "tavern", 3, mode="complete"),
        ])

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        jobs = {json.loads(j.inputs_json)["city_id"]: json.loads(j.inputs_json) for j in self._jobs()}
        self.assertEqual(set(jobs), {"501", "502"})
        self.assertEqual(jobs["501"]["city_name"], "Alfa")
        self.assertFalse(jobs["501"]["dry_run"])
        self.assertEqual(
            {k: jobs["501"]["items"][0][k] for k in ("position", "building", "level", "levels", "complete")},
            {"position": 1, "building": "warehouse", "level": 5, "levels": 2, "complete": False},
        )
        # complete = every level, flagged for the captcha flow
        self.assertEqual((jobs["502"]["items"][0]["levels"], jobs["502"]["items"][0]["complete"]), (3, True))

    def test_needs_the_typed_confirmation_word(self):
        for confirm in ("", "demolir", "SIM", "DEMOLIR TUDO"):
            response = self._post([self._item("501", 1, "warehouse", 5)], confirm=confirm)
            self.assertEqual(response.status_code, 400, confirm)
        self.assertEqual(self._jobs().count(), 0)

    def test_dry_run_needs_no_word_and_is_flagged_on_the_job(self):
        response = self._post([self._item("501", 1, "warehouse", 5)], confirm="", dry_run="1")

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
            [],
            "lixo",
        ]
        for items in bad_lists:
            response = self._post(items)
            self.assertEqual(response.status_code, 400, items)
            self.assertFalse(response.json()["ok"])
        self.assertEqual(self._jobs().count(), 0)

    def test_one_bad_item_cancels_the_whole_request(self):
        response = self._post([self._item("501", 1, "warehouse", 5), self._item("502", 4, "tavern", 9)])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._jobs().count(), 0)

    def test_login_required(self):
        self.client.logout()
        response = self._post([self._item("501", 1, "warehouse", 5)])

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._jobs().count(), 0)

    def test_action_is_registered_hidden_and_never_requeued(self):
        action = ACTION_CATALOG[1303]
        self.assertTrue(action["ready"])
        self.assertTrue(action["ui_hidden"])
        self.assertNotIn(1303, SAFE_REQUEUE_ACTIONS)   # a demolition must never be repeated by recovery
        self.assertNotIn(10, ACTION_CATALOG)           # the old "em breve" entry is gone

    def test_panel_and_dashboard_offer_the_demolish_entry(self):
        panel = self.client.get(reverse("game:construction")).content.decode()
        self.assertIn("demolishModal()", panel)
        self.assertIn(f"open-demolish', {{key: '{self.ga.pk}:501'}}", panel)
        self.assertIn(reverse("game:demolish-buildings"), panel)

        dashboard = self.client.get(reverse("game:dashboard")).content.decode()
        self.assertIn(f"?demolish={self.ga.pk}:501", dashboard)
