"""N-182: the simulator against barbarian villages — their units, the wall as the village's whole front in the first
round, the adjustment learned from barbarian battles alone, and the planner the job asks before each attack."""
from types import SimpleNamespace

from django.test import TestCase

from apps.combat.services import barbarian_learning, barbarian_planner
from apps.espionage.services import battle_land

UPGRADES = {302: {"offensive": 6, "defensive": 8}, 304: {"offensive": 3, "defensive": 3},
            305: {"offensive": 3, "defensive": 4}, 308: {"offensive": 4, "defensive": 4}}
LEVEL_19 = {302: 60, 304: 35, 305: 12, 308: 50}          # the army that won level 19 in four rounds


class BarbarianFieldTests(TestCase):
    def test_while_the_wall_stands_it_is_the_front_and_the_barbarians_wait(self):
        sim = battle_land.simulate_land_battle(LEVEL_19, {320: 300, 324: 16}, attacker_upgrades=UPGRADES, town_hall_level=5,
                                               wall_level=8, max_rounds=12, calibrated=False)
        first = sim["details"]["rounds"][0]
        self.assertEqual(first["defender_lines"].get("principal") or {}, {})
        self.assertEqual(first["defender_lines"].get("flancos") or {}, {})
        self.assertEqual(first["attacker_lines"].get("flancos") or {}, {})
        self.assertEqual(first["wall_sections"], 0)                          # 12 mortars bring the wall 8 down
        self.assertEqual(sim["winner"], "attacker")
        self.assertGreaterEqual(sim["rounds"], 3)                            # the clubs come out after the wall
        self.assertEqual(sim["attacker_losses"], {})
        morale = [(r["morale"].get("attacker") or {}).get("morale") for r in sim["details"]["rounds"]][:3]
        self.assertEqual(morale, [85, 75, 65])                               # as the real report: tired, outnumbered

    def test_a_player_city_is_fought_as_before(self):
        sim = battle_land.simulate_land_battle({308: 50, 305: 12}, {303: 100}, town_hall_level=5, wall_level=8,
                                               max_rounds=12, calibrated=False)
        self.assertTrue(sim["details"]["rounds"][0]["defender_lines"].get("principal"))

    def test_who_is_a_barbarian_village(self):
        self.assertTrue(battle_land.is_barbarian_village({320: 300, 324: 16}))
        self.assertTrue(battle_land.is_barbarian_village({320: 5, 316: 2}))
        self.assertFalse(battle_land.is_barbarian_village({320: 5, 303: 1}))
        self.assertFalse(battle_land.is_barbarian_village({316: 5}))


class LearningTests(TestCase):
    def test_nothing_to_learn_from_changes_nothing(self):
        result = barbarian_learning.learn()
        self.assertFalse(result["better"])
        self.assertEqual(barbarian_learning.active(), {})

    def test_the_cost_weighs_rounds_morale_wall_and_losses(self):
        sample = {"real": {"winner": "attacker", "rounds": 4, "morale": [85, 75, 65, 55], "wall": [0, None, None, None],
                           "losses": {"attacker": {}, "defender": {"320": 300}}}}
        same = {"winner": "attacker", "rounds": 4, "attacker_losses": {}, "defender_losses": {320: 300},
                "details": {"rounds": [{"morale": {"attacker": {"morale": m}}, "wall_sections": w}
                                       for m, w in ((85, 0), (75, 0), (65, 0), (55, 0))]}}
        self.assertEqual(barbarian_learning.sample_cost(sample, same)["cost"], 0)
        short = {**same, "rounds": 2, "details": {"rounds": same["details"]["rounds"][:2]}}
        self.assertEqual(barbarian_learning.sample_cost(sample, short)["rounds_gap"], -2)


class PlannerTests(TestCase):
    def test_reads_the_village_names(self):
        ids, unknown = barbarian_planner.defenders_from_names([["Membro do Clube de Bárbaros", 325], ["Aríete Bárbaro", 18],
                                                               ["Andarilho de Guerra", 3]])
        self.assertEqual(ids, {320: 325, 324: 18})
        self.assertEqual(unknown, ["Andarilho de Guerra"])

    def test_the_best_army_wins_without_losses_and_fits_the_field(self):
        have = {308: 253, 302: 2519, 304: 661, 305: 291, 303: 4502}
        plan = barbarian_planner.plan(have, {320: 75, 324: 2}, wall_level=1, field_level=5, upgrades=UPGRADES)
        self.assertTrue(plan["can_win"])
        self.assertEqual(plan["prediction"]["attacker_losses"], {})
        self.assertTrue(any(u in plan["army"] for u in (303, 308)))
        for uid, qty in plan["army"].items():
            self.assertLessEqual(qty, have[uid])

    def test_an_empty_village_is_never_planned(self):
        plan = barbarian_planner.plan({308: 253, 305: 291}, {}, wall_level=10, field_level=5)
        self.assertEqual((plan["can_win"], plan["army"]), (False, {}))

    def test_without_a_front_line_it_does_not_attack(self):
        plan = barbarian_planner.plan({305: 12}, {320: 75}, wall_level=1, field_level=5)
        self.assertFalse(plan["can_win"])

    def test_the_agent_api_answers_the_plan(self):
        from apps.combat.api.views import BarbarianPlanView

        request = SimpleNamespace(data={"game_account_id": "", "level": 12, "wall_level": 2,
                                        "defenders": [["Membro do Clube de Bárbaros", 125], ["Aríete Bárbaro", 4]],
                                        "available": {"308": 253, "302": 2519, "305": 291, "304": 661}})
        answer = BarbarianPlanView().post(request).data
        self.assertTrue(answer["can_win"])
        self.assertEqual(answer["defenders"], {"320": 125, "324": 4})
        self.assertTrue(all(isinstance(k, str) for k in answer["army"]))


class ReinforceTests(TestCase):
    """A battle taken up from where it stands (the one sent on 13/11 with 50 steam giants and 20 cooks only)."""

    STATE = {"round": 2, "attacker": {308: 50, 310: 20}, "defender": {320: 275, 316: 25, 324: 18}, "wall_standing": 5,
             "wall_level": 10, "morale": 64, "ammo_pct": {}, "hp_pct": {308: 95}}

    def test_taking_a_battle_up_keeps_its_morale_and_wall(self):
        sim = battle_land.simulate_land_battle({308: 50, 310: 20}, {320: 275, 316: 25, 324: 18}, town_hall_level=5,
                                               wall_level=10, max_rounds=12, calibrated=False, attacker_start_morale=30,
                                               wall_sections_standing=5)
        self.assertEqual(sim["winner"], "defender")
        first = (sim["details"]["rounds"][0]["morale"].get("attacker") or {}).get("morale")
        self.assertLessEqual(first, 30)                                      # it starts where the real one was
        self.assertEqual(sim["details"]["rounds"][0]["wall_sections"], 5)    # and the wall is still whole

    def test_the_reinforcement_turns_the_battle(self):
        available = {302: 2519, 304: 661, 305: 291, 312: 608, 310: 10}
        result = barbarian_planner.reinforce(self.STATE, available, wall_level=10, field_level=5, upgrades=UPGRADES)
        self.assertNotEqual(result["without"]["winner"], "attacker")     # lost, or still open after 20 rounds
        self.assertEqual(result["with"]["winner"], "attacker")
        self.assertTrue(any(u in result["send"] for u in (305, 306, 307)))  # something to bring the wall down
        for uid, qty in result["send"].items():
            self.assertLessEqual(qty, available[uid])


class FarmCostTests(TestCase):
    """For a farm the price is what has to be trained again and the time a round holds the next attack back."""

    def test_unit_price_comes_from_the_game_table(self):
        # N-194: wood 1 gold, sulfur 2 (third in the order), the citizen its tax
        self.assertEqual(barbarian_planner.unit_price(303), 20 * 1 + 14 * 2 + barbarian_planner.CITIZEN_VALUE)
        self.assertGreater(barbarian_planner.unit_price(305), barbarian_planner.unit_price(303))

    def test_a_round_costs_its_share_of_the_cycle(self):
        self.assertEqual(barbarian_planner.round_value(72_000, 180), 6_000)
        self.assertEqual(barbarian_planner.round_value(0, 180), 0)

    def test_what_is_on_its_way_is_not_sent_again(self):
        state = dict(ReinforceTests.STATE)
        available = {302: 2519, 304: 661, 305: 291, 312: 608, 310: 10}
        first = barbarian_planner.reinforce(state, available, wall_level=10, field_level=5, upgrades=UPGRADES)
        on_way = {int(k): v for k, v in first["send"].items()}
        again = barbarian_planner.reinforce(state, available, wall_level=10, field_level=5, upgrades=UPGRADES, en_route=on_way)
        self.assertEqual(again["send"], {})                                 # the same help is already coming


class TravelTests(TestCase):
    def test_the_slowest_unit_sets_the_way_to_the_village(self):
        self.assertAlmostEqual(barbarian_planner.travel_minutes({308: 50, 305: 12}), 9.75, places=2)     # speed 60, as seen
        self.assertAlmostEqual(barbarian_planner.travel_minutes({303: 20}), 6.5, places=2)              # hoplites at 90
        self.assertAlmostEqual(barbarian_planner.travel_minutes({308: 50, 309: 10}), 19.5, places=2)    # a bombardier at 30


class FieldAndCapTests(TestCase):
    """15/11: from level 30 on the game drew the field of seven front slots; a battle the simulator leaves open is
    not a win (it had planned level 32 with steam giants holding the wall while the barbarians waited behind it)."""

    def test_the_village_field_grows_at_30(self):
        self.assertEqual([barbarian_planner.field_for_level(n) for n in (9, 10, 29, 30, 35)], [1, 5, 5, 10, 10])

    def test_an_open_battle_is_not_won(self):
        sim = {"winner": "attacker", "undecided": True, "rounds": 20, "attacker_losses": {}, "morale": {"attacker": 80}}
        self.assertEqual(barbarian_planner._key(sim, {303: 10})[0], 1)
        self.assertEqual(barbarian_planner._key({**sim, "undecided": False}, {303: 10})[0], 0)

    def test_the_simulator_says_when_the_rounds_ran_out(self):
        sim = battle_land.simulate_land_battle({303: 5}, {320: 300, 324: 16}, town_hall_level=5, wall_level=8,
                                               max_rounds=2, calibrated=False)
        self.assertTrue(sim["undecided"])
        short = battle_land.simulate_land_battle({308: 50, 305: 12}, {320: 5}, town_hall_level=1, max_rounds=12, calibrated=False)
        self.assertFalse(short["undecided"])


class LossFloorTests(TestCase):
    def test_one_unit_foreseen_in_a_battle_without_losses_is_a_small_error(self):
        self.assertEqual(barbarian_learning.loss_error({}, {303: 1}), 0.05)
        self.assertEqual(barbarian_learning.loss_error({303: 100}, {303: 50}), 0.5)


class ReinforceOnlyWhenItMattersTests(TestCase):
    def test_with_help_on_its_way_only_a_lost_battle_gets_more(self):
        state = dict(ReinforceTests.STATE)
        available = {302: 2519, 304: 661, 305: 291, 312: 608, 310: 10}
        small = {302: 5}
        again = barbarian_planner.reinforce(state, available, wall_level=10, field_level=5, upgrades=UPGRADES, en_route=small)
        if again["send"]:
            self.assertEqual(again["without"]["winner"] != "attacker", True)



class CitizenTests(TestCase):
    """15/11: retraining the losses took ~1.200 citizens in a night; a citizen is worth its tax until it grows back."""

    def test_a_citizen_is_its_tax_while_it_grows_back(self):
        self.assertEqual(barbarian_planner.CITIZEN_VALUE, 150)                   # 3 gold/h, 2% of the happiness per hour
        town = {"citizens": 2299, "citizens_gold": 6899, "happiness": 408, "growth": 8.19}
        self.assertAlmostEqual(barbarian_planner.citizen_value(town), round((6899 / 2299) / (8.19 / 408), 1))
        self.assertEqual(barbarian_planner.citizen_value({"citizens": 5, "citizens_gold": 99999}), 150)   # nonsense: the rule

    def test_the_town_sets_the_price_of_a_loss(self):
        barbarian_planner.set_town({"citizens": 2299, "citizens_gold": 6899, "happiness": 408, "growth": 8.19}, {"308": 2})
        try:
            value = barbarian_planner.citizen_value({"citizens": 2299, "citizens_gold": 6899, "happiness": 408, "growth": 8.19})
            self.assertAlmostEqual(barbarian_planner.unit_price(308), 65 + 87 * 2 + 2 * value)
            self.assertAlmostEqual(barbarian_planner.unit_price(303), 20 + 14 * 2 + value)
        finally:
            barbarian_planner.set_town()

    def test_losses_beyond_the_free_citizens_weigh_more(self):
        sim = {"winner": "attacker", "rounds": 6, "attacker_losses": {303: 200}, "morale": {"attacker": 80}}
        barbarian_planner.set_town({"citizens": 50, "max": 3770})
        try:
            short = barbarian_planner._key(sim, {303: 400})[1]
        finally:
            barbarian_planner.set_town()
        plenty = barbarian_planner._key(sim, {303: 400})[1]
        self.assertAlmostEqual(short - plenty, 150 * 150, delta=1)

    def test_what_a_cycle_can_take_for_ever(self):
        pop = {"citizens": 1284, "happiness": 1134, "growth": 22.7, "occupied": 2695, "max": 3770}
        self.assertEqual(barbarian_planner.sustainable_citizens(pop, 204), int(22.7 / 1134 * (59 + 0.8 * 2359) * 204 / 60))


class LocalSearchTests(TestCase):
    """15/11, level 34: the grid fills the reserve only to not look outnumbered, so carabineers with the reserve's
    hoplites were never tried; the walk from the best army finds what is between."""

    def test_the_walk_adds_what_lowers_the_cost(self):
        def fight(army):
            lost = max(0, 300 - 2 * army.get(304, 0))                 # each carabineer saves two hoplites
            return {"winner": "attacker", "rounds": 8, "attacker_losses": {303: lost} if lost else {},
                    "morale": {"attacker": 80}}

        start = {303: 400, 308: 140}
        best = (barbarian_planner._key(fight(start), start), start, fight(start))
        (key, army, _sim), tried = barbarian_planner._improve(best, {303: 1000, 308: 271, 304: 661}, 10, fight, 0.0)
        self.assertGreaterEqual(army.get(304, 0), 104)
        self.assertLess(key[1], best[0][1])
        self.assertGreater(tried, 0)


class ShortCycleTests(TestCase):
    def test_an_attack_sent_again_is_not_a_cycle(self):
        self.assertEqual(barbarian_planner.round_value(189_100, 18), barbarian_planner.round_value(189_100, 180))


class FarmValueTests(TestCase):
    """N-194: resources are not worth the same; the order is the accounts' need (Rodrigo, 15/11), wine first only when
    the account's taverns use more than it produces."""

    def test_the_order_and_wood_last(self):
        from apps.combat.services import farm_value

        v = farm_value.resource_values()
        self.assertGreater(v["marble"], v["crystal"])
        self.assertGreater(v["crystal"], v["sulfur"])
        self.assertGreater(v["sulfur"], v["wine"])
        self.assertGreater(v["wine"], v["wood"])

    def test_wine_short_goes_first(self):
        from types import SimpleNamespace

        from apps.combat.services import farm_value

        short = SimpleNamespace(snapshot=SimpleNamespace(cities=[
            {"tradegood": 1, "tradegood_production_per_hour": 500, "wine_consumption": 300},
            {"tradegood": 2, "tradegood_production_per_hour": 900, "wine_consumption": 400}]))
        covered = SimpleNamespace(snapshot=SimpleNamespace(cities=[
            {"tradegood": 1, "tradegood_production_per_hour": 900, "wine_consumption": 300},
            {"tradegood": 4, "tradegood_production_per_hour": 600, "wine_consumption": 400}]))
        self.assertTrue(farm_value.wine_short(short))
        self.assertFalse(farm_value.wine_short(covered))
        v = farm_value.resource_values(short)
        self.assertEqual(max(v, key=v.get), "wine")
        self.assertEqual(min(v, key=v.get), "wood")
        self.assertEqual(max(farm_value.resource_values(covered), key=farm_value.resource_values(covered).get), "marble")

    def test_loot_and_what_the_losses_take(self):
        from apps.combat.services import farm_value

        values = farm_value.DEFAULT_VALUES
        # 15/11, level 34: the merchants brought this; the plan's losses
        loot = [42401, 16671, 14066, 9378, 11983]
        self.assertEqual(farm_value.loot_value(loot, 78600, values),
                         42401 * 1 + 16671 * 1.5 + 14066 * 3 + 9378 * 2.5 + 11983 * 2 + 78600)
        need = farm_value.losses_resources({302: 138, 303: 23, 308: 97})
        self.assertEqual(need, {"wood": 138 * 15 + 23 * 20 + 97 * 65, "sulfur": 138 * 14 + 23 * 14 + 97 * 87})
        rows = {r["resource"]: r for r in farm_value.coverage(loot, {302: 138, 303: 23, 308: 97})}
        self.assertTrue(rows["sulfur"]["covered"])
        self.assertEqual(rows["sulfur"]["need"], 10693)
        self.assertEqual(farm_value.as_resources({"glass": 5, "wood": 2}), {"crystal": 5, "wood": 2})
        # an old entry (only the total): the resources at the average value
        self.assertEqual(farm_value.loot_value(None, 100, values, total=1000), 1000 * 2.0 + 100)

    def test_the_walk_takes_blocks_of_the_reserve(self):
        steps = barbarian_planner._steps(10)
        self.assertIn(200, steps[303])
        self.assertIn(400, steps[302])


class SecondWaveTests(TestCase):
    """N-194: bombardiers sent right after the army join later and do not slow it down on the way."""

    def test_the_bombardiers_go_after_when_that_is_cheaper(self):
        calls = []

        def fake(attacker, defender, **kw):
            calls.append(kw.get("attacker_reinforcements_by_round"))
            helped = bool(kw.get("attacker_reinforcements_by_round"))
            return {"winner": "attacker", "rounds": 5 if helped else 7, "attacker_losses": {303: 40 if helped else 90},
                    "morale": {"attacker": 80}}

        from unittest import mock
        army = {303: 300, 308: 100}
        sim = fake(army, {})
        key = barbarian_planner._key(sim, army, 1000.0)
        with mock.patch("apps.espionage.services.battle_land.simulate_land_battle", fake):
            second, best_sim, tried = barbarian_planner._second_wave(key, army, sim, {303: 300, 308: 100, 309: 60},
                                                                     {316: 600}, 10, 20, {}, 1000.0)
        self.assertTrue(second)
        self.assertIn(309, second["army"])
        self.assertGreaterEqual(second["round"], 2)
        self.assertEqual(best_sim["rounds"], 5)

    def test_no_spare_bombardiers_no_second_wave(self):
        sim = {"winner": "attacker", "rounds": 5, "attacker_losses": {}, "morale": {"attacker": 80}}
        second, _sim, tried = barbarian_planner._second_wave((0, 0, 0), {303: 10}, sim, {303: 10}, {320: 5}, 5, 0, {}, 0.0)
        self.assertIsNone(second)
        self.assertEqual(tried, 0)


class IncomeGoldTests(TestCase):
    """N-194: the income screen shows the barbarians' gold per hour (gold only)."""

    def test_gold_per_hour_from_the_ledger(self):
        import json as _json
        import time as _time

        from apps.accounts.models import Account, GameAccount, Node
        from apps.combat.services import farm_value
        from apps.jobs.models import Job

        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="X")
        now = int(_time.time())
        state = {"4478": {"ledger": [{"at": now - 3600, "loot": 1000, "gold": 48000, "losses": {"303": 1}},
                                     {"at": now - 30 * 3600, "loot": 1000, "gold": 99999, "losses": {}}]}}
        Job.objects.create(account=account, game_account=ga, node=node, action_code=19, status="scheduled",
                           inputs_json=_json.dumps({"islands_state": state}))
        out = farm_value.barbarian_hourly([str(ga.pk)])
        self.assertEqual(out[str(ga.pk)]["per_hour"], 2000)          # 48.000 in 24 h; the battle of 30 h ago is out


class RouteAndReserveTests(TestCase):
    """N-194: the way to another island counts as the attack screen showed it; an army the free ships cannot carry
    never leaves; the assault team is kept apart."""

    def test_the_journey_of_the_screen_is_the_travel(self):
        barbarian_planner.set_route({"303": 2400, "309": 4800}, {"303": 1, "309": 3}, 100)
        barbarian_planner._FARM["joining"] = False                      # a new attack, as plan() sets it
        try:
            self.assertEqual(barbarian_planner.travel_minutes({303: 10}), 40)
            self.assertEqual(barbarian_planner.travel_minutes({303: 10, 309: 1}), 80)
            sim = {"winner": "attacker", "rounds": 5, "attacker_losses": {}, "morale": {"attacker": 80}}
            self.assertEqual(barbarian_planner._key(sim, {303: 100})[0], 0)
            self.assertEqual(barbarian_planner._key(sim, {303: 101})[0], 2)        # 101 weight, 100 carried
        finally:
            barbarian_planner.set_route()
        self.assertAlmostEqual(barbarian_planner.travel_minutes({303: 10}), 6.5, delta=0.1)   # its own island again

    def test_reservations_of_the_account(self):
        import json as _json

        from apps.accounts.models import Account, GameAccount, Node
        from apps.combat.services.reservations import account_reservations, parse_reserve
        from apps.jobs.models import Job

        node = Node.objects.create(name="agente", transport="https")
        account = Account.objects.create(node=node, label="Conta", email="c@example.com", password_enc="x")
        ga = GameAccount.objects.create(account=account, lobby_account_id=1, server_id="s78-br", server_language="br",
                                        server_number=78, name="X")
        mine = Job.objects.create(account=account, game_account=ga, node=node, action_code=19, status="scheduled",
                                  inputs_json=_json.dumps({"island_ids": "4478,4479", "reserve_units": '{"37436": {"303": 120}}'}))
        Job.objects.create(account=account, game_account=ga, node=node, action_code=19, status="scheduled",
                           inputs_json=_json.dumps({"island_ids": "4477"}))
        Job.objects.create(account=account, game_account=ga, node=node, action_code=19, status="finished",
                           inputs_json=_json.dumps({"island_ids": "4480", "reserve_units": {"1": {"303": 5}}}))
        out = account_reservations(str(ga.pk), exclude_job_id=str(mine.pk))
        self.assertEqual(out["reserved"], {"37436": {"303": 120}})
        self.assertEqual(sorted(i for ids in out["islands"].values() for i in ids), ["4477"])
        self.assertEqual(parse_reserve('{"x": {"303": 1}, "5": {"303": -2, "304": 3}}'), {"5": {"304": 3}})
        Job.objects.create(account=account, game_account=ga, node=node, action_code=1005, status="scheduled",
                           inputs_json=_json.dumps({"building_type": "troops", "mode": "maintain", "maintain_scope": "selected_uniform",
                                                    "target_garrison": {"303": 50, "305": 6}, "city_ids": ["37436", "37441"]}))
        self.assertEqual(account_reservations(str(ga.pk))["garrison"],
                         {"37436": {"303": 50, "305": 6}, "37441": {"303": 50, "305": 6}})


class FrontLineTests(TestCase):
    """N-194: the hoplites (or giants) hold the front; swordsmen alone only when the town has none."""

    def test_the_front_is_held(self):
        self.assertFalse(barbarian_planner._holds_the_front({302: 10}, {302: 100, 303: 100}))
        self.assertTrue(barbarian_planner._holds_the_front({303: 10, 302: 10}, {302: 100, 303: 100}))
        self.assertTrue(barbarian_planner._holds_the_front({302: 10}, {302: 100}))          # no hoplites in the town

    def test_low_levels_go_with_hoplites(self):
        result = barbarian_planner.plan({302: 500, 303: 500, 304: 100}, {320: 5}, wall_level=0, field_level=1,
                                        explain_training=False)
        self.assertTrue(result["can_win"])
        self.assertGreater(result["army"].get(303, 0) + result["army"].get(308, 0), 0)


class WallSiegeTests(TestCase):
    """N-194 (Rodrigo, 16/11): a village with a wall gets mortars (or other artillery) when the town has them."""

    def test_a_wall_asks_for_artillery(self):
        try:
            barbarian_planner._FARM["wall_level"] = 2
            self.assertFalse(barbarian_planner._holds_the_front({303: 50, 304: 37}, {303: 500, 304: 100, 305: 30}))
            self.assertTrue(barbarian_planner._holds_the_front({303: 50, 305: 6}, {303: 500, 304: 100, 305: 30}))
            self.assertTrue(barbarian_planner._holds_the_front({303: 50}, {303: 500}))              # no artillery at home
            barbarian_planner._FARM["wall_level"] = 0
            self.assertTrue(barbarian_planner._holds_the_front({303: 50}, {303: 500, 305: 30}))     # no wall
        finally:
            barbarian_planner._FARM["wall_level"] = 0

    def test_the_plan_takes_mortars_against_a_wall(self):
        result = barbarian_planner.plan({303: 500, 304: 100, 305: 30}, {320: 100, 324: 2}, wall_level=1, field_level=5,
                                        explain_training=False)
        self.assertTrue(any(result["army"].get(u, 0) for u in (305, 306, 307)))
