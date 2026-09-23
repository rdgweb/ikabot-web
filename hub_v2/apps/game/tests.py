import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Account, GameAccount, Node
from apps.game.models import AccountSnapshot, AccountSnapshotHistory
from apps.game.views.dashboard import DashboardView
from apps.jobs.models import ConstructionResourceReservation, Job


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class PatchSnapshotResourcesTests(TestCase):
    def setUp(self):
        node = Node.objects.create(name="agent-1")
        self.account = Account.objects.create(
            node=node,
            label="Lobby",
            email="test@example.com",
            password_enc="enc",
        )
        self.game_account = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=123,
            server_id="s1-br",
            server_language="br",
            server_number=1,
            name="Player",
        )
        self.snapshot = AccountSnapshot.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={},
            cities=[
                {
                    "id": "42",
                    "name": "Wine City",
                    "wood": 100,
                    "wine": 200,
                    "current_resources": {"resource": 100, "1": 200},
                }
            ],
            military={},
        )

    def test_patch_resources_updates_city_fields_and_current_resources(self):
        response = self.client.patch(
            "/api/agent/snapshots/patch-resources/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "city_id": "42",
                    "resources": {"wine": 1234, "wood": 99},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)
        self.snapshot.refresh_from_db()
        city = self.snapshot.cities[0]
        self.assertEqual(city["wine"], 1234)
        self.assertEqual(city["wood"], 99)
        self.assertEqual(city["current_resources"]["1"], 1234)
        self.assertEqual(city["current_resources"]["resource"], 99)

    def test_patch_incoming_delta_accumulates_without_overwriting_stock(self):
        response = self.client.patch(
            "/api/agent/snapshots/patch-resources/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "city_id": "42",
                    "incoming_delta": {"wine": 500},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)
        self.snapshot.refresh_from_db()
        city = self.snapshot.cities[0]
        self.assertEqual(city["wine"], 200)
        self.assertEqual(city["incoming_resources"]["wine"], 500)

        response = self.client.patch(
            "/api/agent/snapshots/patch-resources/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "city_id": "42",
                    "incoming_delta": {"wine": -200},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)
        self.snapshot.refresh_from_db()
        self.assertEqual(self.snapshot.cities[0]["incoming_resources"]["wine"], 300)

    def test_patch_base_updates_gold_without_full_snapshot_refresh(self):
        self.snapshot.base_snapshot = {"gold": 100, "income": 50}
        self.snapshot.save(update_fields=["base_snapshot", "updated_at"])

        response = self.client.patch(
            "/api/agent/snapshots/patch-base/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "patch": {"gold": 4321},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)
        self.snapshot.refresh_from_db()
        self.assertEqual(self.snapshot.base_snapshot["gold"], 4321)
        self.assertEqual(self.snapshot.base_snapshot["income"], 50)


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="")
class DashboardCacheInvalidationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="tester",
            email="tester@example.com",
            password="secret123",
        )
        node = Node.objects.create(name="agent-cache")
        self.account = Account.objects.create(
            node=node,
            label="Lobby Cache",
            email="cache@example.com",
            password_enc="enc",
        )
        self.game_account = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=321,
            server_id="s2-br",
            server_language="br",
            server_number=2,
            name="CachePlayer",
        )
        AccountSnapshot.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={"gold": 100, "income": 50, "upkeep": -10, "scientists_upkeep": -5},
            cities=[],
            military={},
        )

    def _dashboard_kpi(self):
        request = self.factory.get("/game/")
        request.user = self.user
        response = DashboardView.as_view()(request)
        response.render()
        return response.context_data["kpi"]

    def test_dashboard_cache_is_invalidated_when_snapshot_updates(self):
        first_kpi = self._dashboard_kpi()
        self.assertEqual(first_kpi["gold"], 100)
        self.assertEqual(first_kpi["income"], 35)

        response = self.client.post(
            "/api/agent/snapshots/",
            data=json.dumps(
                {
                    "game_account_id": str(self.game_account.pk),
                    "base_snapshot": {
                        "gold": 999,
                        "income": 80,
                        "upkeep": -20,
                        "scientists_upkeep": -10,
                    },
                    "cities": [],
                    "military": {},
                }
            ),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

        self.assertEqual(response.status_code, 200)

        second_kpi = self._dashboard_kpi()
        self.assertEqual(second_kpi["gold"], 999)
        self.assertEqual(second_kpi["income"], 50)


class DashboardHistoryLazyLoadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="history-tester",
            email="history@example.com",
            password="secret123",
        )
        node = Node.objects.create(name="agent-history")
        self.account = Account.objects.create(
            node=node,
            label="Lobby History",
            email="history-cache@example.com",
            password_enc="enc",
        )
        self.game_account = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=654,
            server_id="s6-br",
            server_language="br",
            server_number=6,
            name="HistoryPlayer",
        )
        AccountSnapshot.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={"gold": 1000, "income": 100, "upkeep": -25, "scientists_upkeep": -5},
            cities=[],
            military={},
        )
        AccountSnapshotHistory.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={"gold": 1000, "income": 100, "upkeep": -25, "scientists_upkeep": -5},
            cities=[],
            military={},
            captured_at=timezone.now(),
        )

    def test_dashboard_initial_context_does_not_embed_history(self):
        request = self.factory.get("/game/")
        request.user = self.user
        response = DashboardView.as_view()(request)
        response.render()

        self.assertEqual(response.context_data["kpi_history"], {})

    def test_dashboard_history_endpoint_returns_history_payload(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("game:dashboard-history"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(str(self.game_account.pk), payload["history"])


class DashboardConstructionReservationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="reservation-tester",
            email="reservation@example.com",
            password="secret123",
        )
        node = Node.objects.create(name="agent-reservation")
        self.account = Account.objects.create(
            node=node,
            label="Lobby Reservation",
            email="reservation-cache@example.com",
            password_enc="enc",
        )
        self.game_account = GameAccount.objects.create(
            account=self.account,
            lobby_account_id=777,
            server_id="s7-br",
            server_language="br",
            server_number=7,
            name="ReservationPlayer",
        )
        AccountSnapshot.objects.create(
            account=self.account,
            game_account=self.game_account,
            base_snapshot={"gold": 1000, "income": 100},
            cities=[
                {
                    "id": "42",
                    "name": "Marble City",
                    "wood": 100,
                    "wine": 200,
                    "marble": 300,
                    "crystal": 400,
                    "sulfur": 500,
                    "warehouse_capacity": 5000,
                    "max_resources": {"wood": 5000, "wine": 5000, "marble": 5000, "glass": 5000, "sulfur": 5000},
                    "market_resources": {},
                    "produced_tradegood": 2,
                    "resource_production_per_hour": 10,
                    "tradegood_production_per_hour": 20,
                    "population": 100,
                    "wine_consumption": 0,
                    "buildings": [],
                }
            ],
            military={},
        )
        self.job = Job.objects.create(
            account=self.account,
            game_account=self.game_account,
            action_code=1002,
            status="scheduled",
            inputs_json="{}",
        )
        ConstructionResourceReservation.objects.create(
            job=self.job,
            account=self.account,
            game_account=self.game_account,
            city_id="42",
            city_name="Marble City",
            resource="marble",
            reserved_local_amount=1234,
            shortfall_amount=567,
            status="active",
        )

    def test_dashboard_city_resource_projection_contains_construction_reservation(self):
        request = self.factory.get("/game/")
        request.user = self.user
        response = DashboardView.as_view()(request)
        response.render()

        card = response.context_data["account_cards"][0]
        city = card["cities"][0]
        marble = next(res for res in city["resource_projections"] if res["key"] == "marble")

        self.assertEqual(marble["construction_reserved_local"], 1234)
        self.assertEqual(marble["construction_reserved_shortfall"], 567)


class DashboardPlayerScoreTests(TestCase):
    """N-53: the game panel shows each account's total score (collected by
    Check Status into base_snapshot.player_score) next to the gold."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_user(
            username="score-tester", email="score@example.com", password="secret123",
        )
        account = Account.objects.create(
            node=Node.objects.create(name="agent-score"),
            label="Lobby Score", email="score-lobby@example.com", password_enc="enc",
        )
        self.game_account = GameAccount.objects.create(
            account=account, lobby_account_id=987, server_id="s7-br",
            server_language="br", server_number=7, name="ScorePlayer",
        )
        self.snapshot = AccountSnapshot.objects.create(
            account=account, game_account=self.game_account,
            base_snapshot={
                "gold": 1000,
                "player_score": {"total": 135555, "building": 116472, "research": 11694, "army": 117, "place": 3692},
            },
            cities=[], military={},
        )

    def _render(self):
        request = self.factory.get("/game/")
        request.user = self.user
        response = DashboardView.as_view()(request)
        response.render()
        return response

    def test_total_score_rank_and_breakdown_are_shown_next_to_the_gold(self):
        response = self._render()
        html = response.content.decode()

        card = response.context_data["account_cards"][0]
        self.assertEqual(card["player_score"]["total"], 135555)
        self.assertIn("bi-trophy-fill", html)
        self.assertRegex(html, r"135[.,]555")
        self.assertRegex(html, r"#3[.,]692")
        self.assertIn("Mestres de Alvenaria", html)

    def test_breakdown_rows_are_hidden_when_only_the_official_total_is_known(self):
        self.snapshot.base_snapshot = {"gold": 1000, "player_score": {"total": 135555, "place": 3692}}
        self.snapshot.save()
        cache.clear()

        html = self._render().content.decode()

        self.assertRegex(html, r"135[.,]555")
        self.assertNotIn("Mestres de Alvenaria", html)

    def test_nothing_is_shown_before_check_status_collected_the_score(self):
        self.snapshot.base_snapshot = {"gold": 1000}
        self.snapshot.save()
        cache.clear()

        html = self._render().content.decode()

        self.assertNotIn("bi-trophy-fill", html)


class ReorderBuildingsTests(TestCase):
    """N-50: reorganizar edificios -- the construction panel exposes each
    city's 25 slots (with what each slot accepts) and posting the swaps
    creates an ac=1302 job for the agent."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="reorder-tester", email="reorder@example.com", password="secret123",
        )
        self.account = Account.objects.create(
            node=Node.objects.create(name="agent-reorder"),
            label="Lobby Reorder", email="reorder-lobby@example.com", password_enc="enc",
        )
        self.ga = GameAccount.objects.create(
            account=self.account, lobby_account_id=555, server_id="s9-br",
            server_language="br", server_number=9, name="ReorderPlayer",
        )
        land = [4, 7, 9, 11]
        self.snapshot = AccountSnapshot.objects.create(
            account=self.account, game_account=self.ga, base_snapshot={},
            cities=[{"id": "501", "name": "Alfa", "tradegood": 1, "buildings": [
                {"position": 0, "building": "townHall", "level": 10, "is_upgrading": False, "building_id": 0, "ground_id": 0, "allowed": [0]},
                {"position": 1, "building": "warehouse", "level": 5, "is_upgrading": False, "building_id": 7, "ground_id": 2, "allowed": land},
                {"position": 2, "building": "empty", "type": "land", "level": 0, "is_upgrading": False, "building_id": None, "ground_id": 2, "allowed": land},
            ]}],
            military={},
        )

    def test_parse_swaps_accepts_pairs_and_rejects_garbage(self):
        from apps.game.views.reorder_buildings import parse_swaps

        self.assertEqual(parse_swaps("[[1, 2], [2, 1]]"), [[1, 2], [2, 1]])
        for bad in ("", "x", "[]", "[[1, 1]]", "[[1, 25]]", "[[1]]", '{"a": 1}', "[[1, \"a\"]]"):
            self.assertIsNone(parse_swaps(bad), bad)

    def test_posting_swaps_creates_a_reorder_job(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("game:reorder-buildings"), {
            "game_account_id": str(self.ga.pk), "city_id": "501", "swaps": "[[1, 2]]",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        job = Job.objects.get(game_account=self.ga, action_code=1302)
        self.assertEqual(json.loads(job.inputs_json), {"city_id": "501", "city_name": "Alfa", "swaps": [[1, 2]]})

    def test_city_from_another_account_or_bad_swaps_are_refused(self):
        self.client.force_login(self.user)
        url = reverse("game:reorder-buildings")

        other_city = self.client.post(url, {"game_account_id": str(self.ga.pk), "city_id": "999", "swaps": "[[1, 2]]"})
        bad_swaps = self.client.post(url, {"game_account_id": str(self.ga.pk), "city_id": "501", "swaps": "[[1, 99]]"})

        self.assertEqual(other_city.status_code, 400)
        self.assertEqual(bad_swaps.status_code, 400)
        self.assertFalse(Job.objects.filter(action_code=1302).exists())

    def test_panel_exposes_the_layout_and_readiness(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("game:construction"))

        layout = response.context["reorder_layouts"][f"{self.ga.pk}:501"]
        self.assertTrue(layout["ready"])
        self.assertEqual([s["b"] for s in layout["slots"]], ["townHall", "warehouse", "empty"])
        self.assertEqual(layout["slots"][1]["a"], [4, 7, 9, 11])
        self.assertContains(response, 'id="cs-reorder-layouts"')
        self.assertContains(response, "open-reorder")

    def test_layout_is_not_ready_before_check_status_stored_allowed(self):
        cities = self.snapshot.cities
        for b in cities[0]["buildings"]:
            b.pop("allowed")
        self.snapshot.cities = cities
        self.snapshot.save()
        self.client.force_login(self.user)

        response = self.client.get(reverse("game:construction"))

        self.assertFalse(response.context["reorder_layouts"][f"{self.ga.pk}:501"]["ready"])

    def test_game_panel_buildings_tab_links_to_the_reorder_of_each_city(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("game:dashboard"))

        self.assertContains(response, f'{reverse("game:construction")}?reorder={self.ga.pk}:501')

    def test_construction_panel_has_a_visible_reorganizar_button_and_deep_link(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("game:construction"), {"reorder": f"{self.ga.pk}:501"})

        self.assertContains(response, "Reorganizar</button>")
        self.assertContains(response, "URLSearchParams(window.location.search).get('reorder')")

    def test_layout_carries_the_game_map_geometry(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("game:construction"))

        layout = response.context["reorder_layouts"][f"{self.ga.pk}:501"]
        warehouse = layout["slots"][1]
        self.assertEqual((warehouse["ax"], warehouse["ay"]), (730, 738))
        self.assertTrue(warehouse["sp"]["src"].endswith("game/city/b/warehouse_l.png"))
        self.assertEqual([t["src"].rsplit("/", 1)[1] for t in layout["bg"]],
                         ["phase4_nw.jpg", "phase4_ne.jpg", "phase4_sw.jpg", "phase4_se.jpg"])
        self.assertContains(response, 'id="cs-city-sprites"')


class CityMapTests(TestCase):
    """N-50: the in-game city map (art and geometry copied from the game CSS)."""

    def test_shore_buildings_face_the_water_on_each_side(self):
        from apps.game import city_map

        self.assertTrue(city_map.building_sprite("port", 1)["src"].endswith("port_r.png"))
        self.assertTrue(city_map.building_sprite("port", 2)["src"].endswith("port_l.png"))
        self.assertEqual(city_map.building_sprite("port", 1)["w"], 209)

    def test_empty_slots_use_the_flag_of_their_ground(self):
        from apps.game import city_map

        self.assertTrue(city_map.building_sprite("empty", 21, "land")["src"].endswith("flag_red.png"))
        self.assertTrue(city_map.building_sprite("empty", 24, "dockyard")["src"].endswith("flag_blue.png"))
        wall = city_map.building_sprite("empty", 14, "wall")
        self.assertTrue(wall["src"].endswith("flag_yellow.png"))
        self.assertEqual((wall["w"], wall["h"]), (196, 99))

    def test_default_and_custom_sprite_sizes(self):
        from apps.game import city_map

        self.assertEqual(city_map.building_sprite("warehouse", 5), {
            "src": city_map.building_sprite("warehouse", 5)["src"], "x": -59, "y": -68, "w": 172, "h": 140,
        })
        self.assertEqual(city_map.building_sprite("pirateFortress", 17)["w"], 340)
        self.assertIsNone(city_map.building_sprite("somethingNew", 3))

    def test_background_follows_phase_and_capital(self):
        from apps.game import city_map

        self.assertTrue(city_map.background_tiles(2, True)[0]["src"].endswith("phase2_capital_nw.jpg"))
        self.assertTrue(city_map.background_tiles(0, False)[3]["src"].endswith("phase4_se.jpg"))
        self.assertTrue(city_map.background_tiles(9, False)[0]["src"].endswith("phase5_nw.jpg"))

    def test_all_referenced_art_exists_in_static(self):
        from pathlib import Path

        from django.conf import settings
        from apps.game import city_map

        table = city_map.sprite_table()
        files = [s["src"] for s in table["buildings"].values()] + [s["src"] for s in table["shore"].values()] \
            + [s["src"] for s in table["empty"].values()]
        files += [t["src"] for p in range(1, 6) for cap in (False, True) for t in city_map.background_tiles(p, cap)]
        roots = [Path(d) for d in getattr(settings, "STATICFILES_DIRS", [])] + [Path(settings.BASE_DIR) / "static"]
        missing = [
            src for src in files
            if not any((root / src.split("/static/", 1)[-1]).exists() for root in roots)
        ]
        self.assertEqual(missing, [])

    def test_view_window_shows_every_position(self):
        from apps.game import city_map

        x, y, width, height = city_map.sprite_table()["view"]
        for pos, (ax, ay) in city_map.POSITIONS.items():
            self.assertTrue(x <= ax and ax + city_map.ANCHOR[0] <= x + width, pos)
            self.assertTrue(y <= ay and ay + city_map.ANCHOR[1] <= y + height, pos)
