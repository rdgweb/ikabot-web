"""N-179: Roubar Bárbaros, as the game showed it — each island's village attacked from a town of that island (troops
weigh nothing there), merchant ships for the loot, and the game's own form fields."""
import unittest

from game_client.actions.barbarians import AttackBarbarianVillageAction, loot_ships_for_level
from game_client.actions.barbarians import fetch_village_info
from game_client.actions.barbarians import _parse_defenders
from runners.barbarians import BarbariansRunner, _barbarian_movements, _cargo_total, _choose_troops, _battle_outcome, _loot_unit

VIEW_HTML = """
<input type="hidden" name="cargo_army_303_upkeep" value="3"><input type="hidden" name="cargo_army_315_upkeep" value="1">
<script>new missionController(
    37,
    500,
    300, null, 0, 90, false);
var s = ikariam.controller.sliders["slider_303"]; s.upkeep = 2.58; s.weight = 0; s.unitJourneyTime = 40;
var s = ikariam.controller.sliders["slider_315"]; s.upkeep = 0.86; s.weight = 0; s.unitJourneyTime = 40;
</script>""" + " " * 200


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _Client:
    _server_url = "https://s78-br.ikariam.gameforge.com/index.php"

    def __init__(self):
        self._action_request = "t0"
        self.session = None
        self.sent = []

    def _request(self, method, url, **kwargs):
        if method == "GET":
            return _Resp([["updateGlobalData", {"actionRequest": "t1"}], ["changeView", ["plunder", VIEW_HTML]]])
        self.sent.append(dict(kwargs["data"]))
        return _Resp([["updateGlobalData", {"actionRequest": "t2"}], ["provideFeedback", [{"type": 10, "text": "ok"}]]])


class AttackTests(unittest.TestCase):
    def test_the_form_the_game_expects_and_ships_for_the_loot(self):
        client = _Client()
        result = AttackBarbarianVillageAction(client).execute(from_city_id=63388, island_id=4478, troops={303: 20, 315: 7},
                                                              transporters=50)
        sent = client.sent[0]
        self.assertEqual((sent["function"], sent["destinationCityId"], sent["islandId"]), ("attackBarbarianVillage", "0", "4478"))
        self.assertEqual((sent["cargo_army_303"], sent["cargo_army_303_upkeep"], sent["cargo_army_315_upkeep"]), (20, "3", "1"))
        self.assertEqual(sent["actionRequest"], "t1")          # the token of the screen just opened
        self.assertEqual(result["transporters"], 37)            # 50 wanted, 37 free

    def test_loot_ships_grow_with_the_level(self):
        self.assertEqual(loot_ships_for_level(1), 2)
        self.assertGreater(loot_ships_for_level(33), 150)
        self.assertLess(loot_ships_for_level(20), loot_ships_for_level(30))


class TroopTests(unittest.TestCase):
    def test_low_levels_send_what_fights_and_high_levels_need_the_table(self):
        have = {303: 20, 315: 7, 301: 7, 306: 12, 310: 3}
        self.assertEqual(_choose_troops("schematic", 1, "", have), {303: 7})                 # 1 scout: 2*1+5
        self.assertEqual(_choose_troops("schematic", 9, "", {303: 4502, 302: 2519}), {303: 105})   # 50 scouts
        self.assertIsNone(_choose_troops("schematic", 9, "", {303: 30, 315: 7}))             # 37 against 50 scouts
        self.assertEqual(_choose_troops("available", 1, "", have), {303: 20, 315: 7, 301: 7, 306: 12})
        self.assertIsNone(_choose_troops("schematic", 12, "", have))           # the table asks for units it lacks
        self.assertEqual(_choose_troops("custom", 12, '{"303": 10}', have), {303: 10})
        self.assertEqual(_loot_unit(have), {315: 1})


class _Hub:
    def __init__(self, plan=None, fail=False):
        self.logs = []
        self.plan = plan if plan is not None else {"can_win": True, "army": {"303": 15},
                                                   "prediction": {"rounds": 1, "morale_end": 90, "attacker_losses": {}}}
        self.fail = fail
        self.asked = []

    def report_log(self, job_id, level, msg):
        self.logs.append(msg)

    def plan_barbarian_attack(self, payload):
        self.asked.append(payload)
        if self.fail:
            raise RuntimeError("hub fora")
        return dict(self.plan)


class RoundTests(unittest.TestCase):
    def _runner(self):
        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner._get_available_troops = lambda jid, client, town: {303: 20, 315: 7}
        return runner

    def test_attack_with_ships_for_what_the_village_has_then_collect_until_empty_and_count_the_loot(self):
        import runners.barbarians as module

        runner, sent = self._runner(), []
        village = {"total": 1_200, "gold": 450, "defenders": [["Membro do Clube de Bárbaros", 5]]}
        original = module.fetch_village_info
        module.fetch_village_info = lambda client, island_id: dict(village)

        class Game:
            barb = {"level": 3, "destroyed": False, "under_attack": False}

            def get_barbarian_state(self, island_id):
                return dict(self.barb)

            def attack_barbarian_village(self, **kw):
                sent.append(kw)
                return {"transporters": kw["transporters"], "free_ships": 40}

        try:
            game, st = Game(), {}
            totals = {"total_attacks": 0, "total_loots": 0, "loot_total": 0, "gold_total": 0}
            args = ("schematic", "", True, 1, 9, 0, 0)
            runner._island_round("j", game, "4478", "63388", st, totals, *args)
            self.assertEqual((sent[0]["from_city_id"], sent[0]["troops"], sent[0]["transporters"]), ("63388", {303: 15}, 3))
            Game.barb = {"level": 4, "destroyed": True, "under_attack": False}
            village.update(total=300)                                   # 900 came back with the attack
            runner._island_round("j", game, "4478", "63388", st, totals, *args)
            self.assertEqual((sent[1]["troops"], sent[1]["transporters"]), ({315: 1}, 1))
            village.update(total=0)                                     # the collection took the rest
            runner._island_round("j", game, "4478", "63388", st, totals, *args)
            self.assertEqual(len(sent), 2)
            self.assertEqual((totals["loot_total"], totals["gold_total"], totals["total_attacks"], totals["total_loots"]),
                             (1_200, 450, 1, 1))
            Game.barb = {"level": 4, "destroyed": False, "under_attack": True}
            runner._island_round("j", game, "4478", "63388", st, totals, *args)
            self.assertEqual(len(sent), 2)                              # the battle is on
        finally:
            module.fetch_village_info = original


class VillageScreenTests(unittest.TestCase):
    def test_asked_in_the_query_string_like_upstream_and_read(self):
        asked = {}

        class Client:
            _server_url = "u"
            _action_request = "t0"

            def _request(self, method, url, **kw):
                asked.update(method=method, **kw)
                return _Resp([["updateGlobalData", {"actionRequest": "t9"}],
                              ["changeView", ["barbarianVillage", '<div class="army s301"><div class="u">Viking</div></div></td></tr><tr><td class="center">  1.200 </td>']],
                              ["updateTemplateData", {"js_islandBarbarianLevel": {"text": "4"}, "js_islandBarbarianResourcegold": {"text": "2.100"},
                                                      "js_islandBarbarianResourceresource": {"text": "700"}, "js_islandBarbarianResourcetradegood2": {"text": "250"},
                                                      "barbarianCityCooldownTimer": {"countdown": {"enddate": 1794450000}}}]])

        client = Client()
        info = fetch_village_info(client, 4477)
        self.assertEqual(asked["method"], "POST")
        self.assertEqual((asked["params"]["view"], asked["params"]["destinationIslandId"]), ("barbarianVillage", "4477"))
        self.assertNotIn("data", asked)
        self.assertEqual((info["level"], info["gold"], info["total"], info["rebuild_end"]), (4, 2100, 950, 1794450000))
        self.assertEqual(info["defenders"], [["Viking", 1200]])
        self.assertEqual(client._action_request, "t9")


class MovementTests(unittest.TestCase):
    def test_only_the_town_against_its_village(self):
        moves = [
            {"is_own": True, "origin": {"cityId": 37441}, "target": {"cityId": 0, "name": "Bárbaros"}, "event_id": 1},
            {"is_own": True, "origin": {"cityId": 0}, "target": {"cityId": "37441"}, "is_returning": True, "event_id": 2},
            {"is_own": True, "origin": {"cityId": 37441}, "target": {"cityId": 5555}, "mission_text": "Transporte", "event_id": 3},
            {"is_own": True, "origin": {"cityId": 9}, "target": {"cityId": 0}, "event_id": 4},
        ]
        self.assertEqual([m["event_id"] for m in _barbarian_movements(moves, "37441")], [1, 2])
        self.assertEqual(_cargo_total({"wood": 500, "marble": 200, "gold": 900}), (700, 900))

    def test_wait_while_on_the_way_and_count_the_cargo_once(self):
        import time as _time

        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner.is_network_error = lambda exc: False
        now = int(_time.time())
        back = {"is_own": True, "origin": {"cityId": 0}, "target": {"cityId": "37441"}, "is_returning": True,
                "event_id": 7, "event_time": now + 45, "cargo": {"wood": 600, "sulfur": 300}}

        class Game:
            def fetch_military_advisor(self, city_id):
                return {"movement_details": [dict(back)]}

        st, totals, wake = {}, {"loot_total": 0, "gold_total": 0}, []
        self.assertTrue(runner._on_the_way("j", Game(), "4477", "37441", st, totals, wake))
        self.assertTrue(runner._on_the_way("j", Game(), "4477", "37441", st, totals, wake))
        self.assertEqual((totals["loot_total"], st["looted"], st["status"]), (900, 900, "moving"))
        self.assertEqual(wake[0], now + 65)
        self.assertIn("900 recursos (600 madeira, 300 enxofre)", runner.hub.logs[1])

        class Idle:
            def fetch_military_advisor(self, city_id):
                return {"movement_details": []}

        self.assertFalse(runner._on_the_way("j", Idle(), "4477", "37441", st, totals, []))


class SimulatorPlanTests(unittest.TestCase):
    """No table: the army is the one the hub's simulator chose; without its answer nothing is sent."""

    def _round(self, hub):
        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = hub
        runner._game_account_id = "ga"
        runner._get_available_troops = lambda jid, client, town: {308: 253, 302: 2519, 305: 291}
        sent = []

        class Game:
            def get_barbarian_state(self, island_id):
                return {"level": 20, "destroyed": False, "under_attack": False, "wall_level": 10}

            def attack_barbarian_village(self, **kw):
                sent.append(kw)
                return {"transporters": kw["transporters"], "free_ships": 180}

        import runners.barbarians as module
        original = module.fetch_village_info
        module.fetch_village_info = lambda client, island_id: {"total": 21_000, "gold": 24_000,
                                                               "defenders": [["Membro do Clube de Bárbaros", 325]]}
        try:
            st = {}
            runner._island_round("j", Game(), "4477", "37441", st, {"total_attacks": 0, "total_loots": 0},
                                 "schematic", "", True, 1, 50, 0, 0)
        finally:
            module.fetch_village_info = original
        return sent, st

    def test_attacks_with_the_simulator_army_and_keeps_the_prediction(self):
        hub = _Hub({"can_win": True, "army": {"308": 50, "302": 60, "305": 12},
                    "prediction": {"rounds": 4, "morale_end": 70, "attacker_losses": {}}})
        sent, st = self._round(hub)
        self.assertEqual(sent[0]["troops"], {308: 50, 302: 60, 305: 12})
        self.assertEqual(sent[0]["transporters"], 42)                     # 21.000 / 500
        self.assertEqual((hub.asked[0]["level"], hub.asked[0]["wall_level"]), (20, 10))
        self.assertEqual((st["attack_plan"]["rounds"], st["attack_plan"]["morale_end"]), (4, 70))

    def test_no_defenders_read_means_no_attack_and_no_plan_asked(self):
        import runners.barbarians as module

        hub = _Hub({"can_win": True, "army": {"308": 50}, "prediction": {"rounds": 1}})
        original = module.fetch_village_info
        try:
            module.fetch_village_info = lambda client, island_id: {"total": 21_000, "gold": 24_000, "defenders": []}
            runner = BarbariansRunner.__new__(BarbariansRunner)
            runner.hub, runner._game_account_id = hub, "ga"
            runner._get_available_troops = lambda jid, client, town: {308: 253}
            sent = []

            class Game:
                def get_barbarian_state(self, island_id):
                    return {"level": 20, "destroyed": False, "under_attack": False, "wall_level": 10}

                def attack_barbarian_village(self, **kw):
                    sent.append(kw)

            runner._island_round("j", Game(), "4477", "37441", {}, {"total_attacks": 0, "total_loots": 0},
                                 "schematic", "", True, 1, 50, 0, 0)
        finally:
            module.fetch_village_info = original
        self.assertEqual((sent, hub.asked), ([], []))
        self.assertIn("não ataco sem saber quem defende", hub.logs[-1])

    def test_no_attack_when_it_cannot_win_the_hub_fails_or_a_defender_is_unknown(self):
        for hub in (_Hub({"can_win": False, "army": {}, "note": "perde"}), _Hub(fail=True),
                    _Hub({"can_win": True, "army": {"308": 50}, "unknown": ["Andarilho de Guerra"]})):
            sent, _ = self._round(hub)
            self.assertEqual(sent, [])


class ReportTests(unittest.TestCase):
    def test_the_report_says_rounds_morale_and_why(self):
        rnd = ('<table class="container_morale"><tr><td>Atacante</td><td>Unidades</td><td>Moral</td></tr><tr><td>Executor</td>'
               '<td>157 (-0)</td><td>{m} %</td></tr></table><script>registerMouseOver("morale104197", " Alterações da moral '
               'As tropas estão exaustas. A moral foi reduzida em 10%. ",true,true);</script>')
        detailed = {"rounds": [{"html": rnd.format(m=85)}, {"html": rnd.format(m=55)}], "attacker_losses": {}}
        out = _battle_outcome(detailed, {"result": "victory", "rounds": 2, "combat_id": 9})
        self.assertEqual((out["morale_start"], out["morale_end"], out["rounds"], out["losses"]), (85, 55, 2, {}))
        self.assertEqual(out["reasons"], ["As tropas estão exaustas. A moral foi reduzida em 10%."])

    def test_several_defender_types_are_paired_in_order(self):
        html = ('<tr><td><div class="army s320"><div class="u">Membro do Clube de Bárbaros</div></div></td>'
                '<td><div class="army s324"><div class="u">Atirador de Machados</div></div></td></tr>'
                '<tr><td class="center">325</td><td class="center">18</td></tr>')
        self.assertEqual(_parse_defenders(html), [("Membro do Clube de Bárbaros", 325), ("Atirador de Machados", 18)])


if __name__ == "__main__":
    unittest.main()
