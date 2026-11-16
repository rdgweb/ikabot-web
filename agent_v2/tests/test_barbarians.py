"""N-179: Roubar Bárbaros, as the game showed it — each island's village attacked from a town of that island (troops
weigh nothing there), merchant ships for the loot, and the game's own form fields."""
import unittest

from game_client.actions.barbarians import AttackBarbarianVillageAction, loot_ships_for_level
from game_client.actions.barbarians import fetch_village_info
from game_client.actions.barbarians import _parse_defenders
from runners.barbarians import farm_book, ideal_level, BarbariansRunner, _barbarian_movements, _cargo_total, _choose_troops, _battle_outcome, _loot_unit

REAL_LEVEL_20 = '<div class="contentBox01h"> <h3 class="header">Exército de Defesa da Vila dos Bárbaros</h3> <div class="content barbarianVillage"> <table class="table01 militaryList"> <tr> <td> <div class="army s314"> <div class="tooltip">Muralha</div> </div> </td> <td> <div class="army s320"> <div class="tooltip">Membro do Clube de Bárbaros</div> </div> </td> <td> <div class="army s316"> <div class="tooltip">Viking</div> </div> </td> <td> <div class="army s324"> <div class="tooltip">Aríete Bárbaro</div> </div> </td> </tr> <tr> <td class="center"> Nível 10 </td> <td class="center"> 275 </td> <td class="center"> 25 </td> <td class="center"> 18 </td> </tr> </table>'   # the village screen at level 20, as the game sent it (14/11/2026)

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
        village = {"total": 1_200, "gold": 450, "defenders": [["Membro do Clube de Bárbaros", 5]], "defender_ids": {"320": 5},
                   "defenders_found": True}
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
            village.update(total=300, rebuild_end=int(__import__("time").time()) + 7200)   # 900 came back with the attack
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
                              ["changeView", ["barbarianVillage", '<table class="table01 militaryList"><tr><td><div class="army s316"><div class="tooltip">Viking</div></div></td></tr><tr><td class="center">  1.200 </td></tr></table>']],
                              ["updateTemplateData", {"js_islandBarbarianLevel": {"text": "4"}, "js_islandBarbarianResourcegold": {"text": "2.100"},
                                                      "js_islandBarbarianResourceresource": {"text": "700"}, "js_islandBarbarianResourcetradegood2": {"text": "250"},
                                                      "barbarianCityCooldownTimer": {"countdown": {"enddate": 1794450000}}}]])

        client = Client()
        info = fetch_village_info(client, 4477)
        self.assertEqual(asked["method"], "POST")
        self.assertEqual((asked["params"]["view"], asked["params"]["destinationIslandId"]), ("barbarianVillage", "4477"))
        self.assertNotIn("data", asked)
        self.assertEqual((info["level"], info["gold"], info["total"], info["rebuild_end"]), (4, 2100, 950, 1794450000))
        self.assertEqual((info["defenders"], info["defender_ids"], info["defenders_found"]), ([["Viking", 1200]], {"316": 1200}, True))
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


class CollectTimingTests(unittest.TestCase):
    """15/11 22:02: the collection left with the village about to be rebuilt (it would arrive to a fight)."""

    def test_no_collection_when_the_village_is_back_before_the_merchants(self):
        import time as _time

        import runners.barbarians as module

        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner._get_available_troops = lambda jid, client, town: {303: 20, 315: 7}
        sent = []
        village = {"total": 16_001, "gold": 0, "rebuild_end": int(_time.time()) + 8 * 60}
        original = module.fetch_village_info
        module.fetch_village_info = lambda client, island_id: dict(village)

        class Game:
            def get_barbarian_state(self, island_id):
                return {"level": 35, "destroyed": True, "under_attack": False}

            def attack_barbarian_village(self, **kw):
                sent.append(kw)
                return {"transporters": kw["transporters"], "free_ships": 40}

        try:
            st = {}
            totals = {"total_attacks": 0, "total_loots": 0, "loot_total": 0, "gold_total": 0}
            runner._island_round("j", Game(), "4477", "37441", st, totals, "schematic", "", True, 1, 50, 0, 0)
            self.assertEqual(sent, [])
            self.assertTrue(any("não coleto" in m.lower() for m in runner.hub.logs))
            village["rebuild_end"] = int(_time.time()) + 3600              # an hour left: it is safe
            runner._island_round("j", Game(), "4477", "37441", st, totals, "schematic", "", True, 1, 50, 0, 0)
            self.assertEqual(len(sent), 1)
        finally:
            module.fetch_village_info = original


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
        module.fetch_village_info = lambda client, island_id: {"total": 21_000, "gold": 24_000, "defenders_found": True,
                                                               "defenders": [["Membro do Clube de Bárbaros", 275]],
                                                               "defender_ids": {"320": 275, "316": 25, "324": 18}, "wall_level": 10}
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
            module.fetch_village_info = lambda client, island_id: {"total": 21_000, "gold": 24_000, "defenders": [],
                                                                   "defenders_found": False}
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

    def test_the_hub_gets_the_ids_and_the_wall_read_on_the_screen(self):
        hub = _Hub({"can_win": True, "army": {"308": 50}, "prediction": {"rounds": 3, "morale_end": 70, "attacker_losses": {}}})
        self._round(hub)
        self.assertEqual((hub.asked[0]["defenders"], hub.asked[0]["wall_level"]), ({"320": 275, "316": 25, "324": 18}, 10))

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

    def test_the_real_level_20_screen(self):
        table = _parse_defenders(REAL_LEVEL_20)
        self.assertTrue(table["found"])
        self.assertEqual(table["units"], {320: 275, 316: 25, 324: 18})       # the wall's column is its level, not a unit
        self.assertEqual(table["wall_level"], 10)
        self.assertEqual(table["names"][324], "Aríete Bárbaro")

    def test_one_unit_no_table_and_only_the_wall(self):
        one = ('<table class="table01 militaryList"><tr><td><div class="army s320"><div class="tooltip">Membro do Clube de '
               'Bárbaros</div></div></td></tr><tr><td class="center"> 2 </td></tr></table>')
        self.assertEqual(_parse_defenders(one)["units"], {320: 2})
        self.assertFalse(_parse_defenders("<div>outra tela</div>")["found"])
        wall = ('<table class="table01 militaryList"><tr><td><div class="army s314"><div class="tooltip">Muralha</div></div>'
                '</td></tr><tr><td class="center"> Nível 3 </td></tr></table>')
        self.assertEqual((_parse_defenders(wall)["found"], _parse_defenders(wall)["units"], _parse_defenders(wall)["wall_level"]),
                         (True, {}, 3))


class ReinforceTests(unittest.TestCase):
    """A battle of ours under way: the simulator takes it up from its last round; what helps is sent and told."""

    def _setup(self, send):
        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner._game_account_id = "ga"
        runner._get_available_troops = lambda jid, client, town: {302: 2519, 304: 661, 305: 291, 312: 608, 310: 10}
        hub = _Hub()
        hub.told = []
        hub.reinforce_barbarian_battle = lambda payload: (hub.asked.append(payload) or
                                                           {"send": send, "with": {"winner": "attacker", "rounds": 6},
                                                            "without": {"winner": "defender", "rounds": 6}})
        hub.send_notification = lambda **kw: hub.told.append(kw)
        runner.hub = hub
        sent = []

        class Game:
            def get_barbarian_state(self, island_id):
                return {"level": 20, "under_attack": True, "wall_level": 10}

            def fetch_combat_reports(self, city_id, limit=10):
                # as the game lists it: no town link, the village's name only in the row's text
                return [{"combat_id": 76, "city_name": "", "text": "13.11.2026 Batalha por Juízo Final", "result": "victory"},
                        {"combat_id": 77, "city_name": "", "text": "14.11.2026 Batalha por Vila de Bárbaros 2", "result": "", "rounds": 2}]

            def fetch_combat_detailed_report(self, city_id, combat_id):
                return {"rounds": [{"round": 1, "html": "r1"}, {"round": 2, "html": "r2"}]}

            def attack_barbarian_village(self, **kw):
                sent.append(kw)
                return {"transporters": 1}

        return runner, hub, Game(), sent

    def test_sends_what_the_simulator_says_once_per_round_and_tells_it(self):
        runner, hub, game, sent = self._setup({"302": 60, "305": 24})
        st = {}
        runner._reinforce_if_needed("j", game, "4477", "37441", st)
        runner._reinforce_if_needed("j", game, "4477", "37441", st)          # same round: nothing more
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["troops"], {302: 60, 305: 24})
        self.assertEqual(hub.asked[0]["round_html"], "r2")                   # the last round fought
        self.assertEqual(hub.told[0]["event"], "barbarian_farm")
        self.assertIn("Enviado: 60 Espadachim, 24 Morteiro", hub.told[0]["body"].splitlines())
        self.assertIn("60 Espadachim, 24 Morteiro", hub.told[0]["body"])

    def test_what_is_still_on_its_way_goes_with_the_question(self):
        import time as _time

        runner, hub, game, sent = self._setup({"303": 50})
        st = {"last_reinforcement": {"army": {"302": 60}, "at": int(_time.time()) - 300}, "village_resources": 37_000,
              "village_gold": 35_600, "cycle_minutes": 160}
        runner._reinforce_if_needed("j", game, "4477", "37441", st)
        self.assertEqual(hub.asked[0]["en_route"], {"302": 60})
        self.assertEqual((hub.asked[0]["loot"], hub.asked[0]["cycle_minutes"]), (72_600, 160))

    def test_nothing_sent_when_nothing_helps(self):
        runner, hub, game, sent = self._setup({})
        runner._reinforce_if_needed("j", game, "4477", "37441", {})
        self.assertEqual((sent, hub.told), ([], []))


class RetrainTests(unittest.TestCase):
    """A farm trains again, in the town's barracks, what each battle cost."""

    def _runner(self, retrain=True):
        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner.hub.get_snapshot = lambda game_account_id: {"cities": [{"id": 37441, "buildings": [
            {"building": "barracks", "position": 6, "level": 30}]}]}
        runner._game_account_id = "ga"
        runner._retrain = retrain
        runner.is_network_error = lambda exc: False
        return runner

    def test_the_losses_go_to_the_barracks(self):
        trained = []

        class Game:
            def fetch_barracks_state(self, city_id, position, building_type):
                return {"units": [{"unit_id": 303, "train_time_seconds": 63}, {"unit_id": 308, "train_time_seconds": 540}]}

            def train_units(self, city_id, position, units, building_type):
                trained.append((city_id, position, units))

        runner = self._runner()
        runner._retrain_losses("j", Game(), "4477", "37441", {"303": 106, "308": 2})
        self.assertEqual(trained, [(37441, 6, {303: 106, 308: 2})])
        st = {}
        self.assertEqual(runner._train_seconds("j", Game(), "37441", st), {"303": 63, "308": 540})
        self.assertIn("refazendo as perdas", runner.hub.logs[-1])

    def test_off_when_the_job_says_so(self):
        runner = self._runner(retrain=False)

        class Game:
            def train_units(self, *a):
                raise AssertionError("não devia treinar")

        runner._retrain_losses("j", Game(), "4477", "37441", {"303": 10})


class FarmBookTests(unittest.TestCase):
    """The farm's book per level (the night of 13-14/11 as it happened) and the level it keeps."""

    PRICES = {"303": 64, "308": 182}
    NIGHT = [
        {"level": 21, "loot": 25_000, "gold": 27_050, "losses": {"303": 31, "308": 10}, "rounds": 8, "cycle_minutes": 188},
        {"level": 22, "loot": 28_600, "gold": 29_700, "losses": {"303": 60, "308": 2}, "rounds": 6, "cycle_minutes": 160},
        {"level": 23, "loot": 32_600, "gold": 32_550, "losses": {"303": 97}, "rounds": 5, "cycle_minutes": 157},
        {"level": 24, "loot": 37_000, "gold": 35_600, "losses": {"303": 106}, "rounds": 6, "cycle_minutes": 157},
    ]

    def test_profit_per_hour_per_level(self):
        book = farm_book(self.NIGHT, self.PRICES)
        self.assertEqual(book[24]["losses_cost"], 106 * 64)
        self.assertEqual(book[24]["profit"], 37_000 + 35_600 - 106 * 64)
        self.assertEqual(book[24]["per_hour"], round((37_000 + 35_600 - 106 * 64) * 60 / 157))

    def test_the_ideal_is_known_only_after_it_falls_two_levels_in_a_row(self):
        self.assertIsNone(ideal_level(farm_book(self.NIGHT, self.PRICES)))           # still getting better
        worse = self.NIGHT + [{"level": 25, "loot": 30_000, "gold": 30_000, "losses": {"303": 400}, "cycle_minutes": 200},
                              {"level": 26, "loot": 30_000, "gold": 30_000, "losses": {"303": 500}, "cycle_minutes": 220}]
        self.assertEqual(ideal_level(farm_book(worse, self.PRICES)), 24)


class LevelHoldTests(unittest.TestCase):
    def _runner(self, target=0, auto=False):
        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner._target_level_input, runner._target_auto = target, auto
        return runner

    def test_a_drop_without_attack_is_the_decay(self):
        import time as _time

        runner, st = self._runner(), {"level_seen": {"level": 25, "since": int(_time.time()) - 7200}, "attacked_at": 0}
        runner._track_level("j", "4477", 24, st)
        self.assertEqual((st["decay"][0]["from"], st["decay"][0]["to"]), (25, 24))
        self.assertAlmostEqual(st["decay"][0]["hours"], 2.0, places=1)

    def test_above_the_farm_level_it_waits(self):
        runner = self._runner(target=24)
        sent = []

        class Game:
            def get_barbarian_state(self, island_id):
                return {"level": 25, "destroyed": False, "under_attack": False}

            def attack_barbarian_village(self, **kw):
                sent.append(kw)

        import runners.barbarians as module
        original = module.fetch_village_info
        module.fetch_village_info = lambda client, island_id: {"total": 1, "defenders_found": True, "defender_ids": {"316": 425}}
        try:
            st = {}
            runner._island_round("j", Game(), "4477", "37441", st, {"total_attacks": 0, "total_loots": 0},
                                 "schematic", "", True, 1, 50, 0, 0)
        finally:
            module.fetch_village_info = original
        self.assertEqual(sent, [])
        self.assertEqual(st["waiting_level"], 25)
        self.assertIn("espero ela cair", runner.hub.logs[-1])


if __name__ == "__main__":
    unittest.main()



class PopulationTests(unittest.TestCase):
    """15/11: Juízo Final at 2.695 of 3.770 with 1.284 free citizens, happiness ~1.134 and ~22/h of growth, after
    retraining ~1.200 units in a night."""

    POP = {"citizens": 1284, "citizens_gold": 3852, "happiness": 1134, "growth": 22.7, "occupied": 2695, "max": 3770}

    def test_what_a_cycle_can_take_for_ever(self):
        from runners.barbarians import sustainable_citizens

        # full town: happiness 59, 2.359 free; at most 80% of them missing -> 0,02 x (59 + 1.887) x 3,4 h
        self.assertEqual(sustainable_citizens(self.POP, 204), int(22.7 / 1134 * (59 + 0.8 * 2359) * 204 / 60))
        self.assertIsNone(sustainable_citizens({}, 204))

    def test_the_book_marks_what_the_town_cannot_keep_up(self):
        ledger = [{"level": 25, "loot": 41_801, "gold": 38_850, "losses": {"303": 111}, "rounds": 6, "cycle_minutes": 204},
                  {"level": 29, "loot": 65_000, "gold": 53_850, "losses": {"303": 188}, "rounds": 8, "cycle_minutes": 204}]
        book = farm_book(ledger, {"303": 184}, {"303": 1}, self.POP)
        self.assertEqual((book[25]["citizens"], book[29]["citizens"]), (111, 188))
        self.assertTrue(book[25]["sustainable"])
        self.assertFalse(book[29]["sustainable"])

    def test_the_ideal_is_the_best_the_town_keeps_up(self):
        book = {25: {"per_hour": 20_000, "sustainable": True, "citizens": 111},
                26: {"per_hour": 24_000, "sustainable": True, "citizens": 120},
                27: {"per_hour": 30_000, "sustainable": False, "citizens": 173},
                28: {"per_hour": 31_000, "sustainable": False, "citizens": 180}}
        self.assertEqual(ideal_level(book), 26)
        none_kept = {k: {**v, "sustainable": False} for k, v in book.items()}
        self.assertEqual(ideal_level(none_kept), 25)                                 # the one that takes fewest

    def test_retraining_stops_at_the_free_citizens(self):
        trained = []

        class Game:
            def fetch_barracks_state(self, city_id, position, building_type):
                return {"units": [{"unit_id": 303, "train_time_seconds": 63, "citizens": 1, "max_build": 5000},
                                  {"unit_id": 302, "train_time_seconds": 80, "citizens": 1, "max_build": 5000}]}

            def train_units(self, city_id, position, units, building_type):
                trained.append(units)

        runner = RetrainTests._runner(RetrainTests())
        runner._population = lambda jid, client, town, st, fresh=False: {"citizens": 150, "max": 3770, "growth": 22.0}
        st = {}
        runner._retrain_losses("j", Game(), "4477", "37441", {"303": 120, "302": 66}, st)
        self.assertEqual(trained, [{303: 120, 302: 30}])
        self.assertEqual(st["retrain_short"], {"302": 36})
        self.assertIn("cidadãos livres", runner.hub.logs[-1])


    def test_only_the_battles_of_the_new_planner_choose_the_level(self):
        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner._target_level_input, runner._target_auto = 0, True
        old = [{"level": n, "loot": 50_000 + 10_000 * (n - 25), "gold": 40_000, "losses": {"303": 50 * (n - 24)}, "rounds": 6, "cycle_minutes": 200}
               for n in (25, 26, 27, 28)]
        st = {"ledger": old, "prices": {"303": 184}, "population": self.POP}
        self.assertEqual(runner._target_level(st), 0)                       # the old armies do not choose
        st["ledger"] = [{**e, "book": 2} for e in old]
        self.assertEqual(runner._target_level(st), 26)                      # 27 and 28 take more than the town grows


class RecallTests(unittest.TestCase):
    """15/11 18:08: the attack on level 34 was called back by hand before it fought; the job read it as a defeat in one
    round and took the 18 minutes until the next attack as its cycle."""

    def test_a_report_is_a_battle_never_a_recall(self):
        # 16/11: 1-round wins without losses were read as "recalled" (a recalled attack leaves no report at all)
        battle = _battle_outcome({"rounds": [], "attacker_losses": {}}, {"result": "victory", "rounds": 1, "combat_id": 9})
        self.assertEqual(battle["result"], "victory")

    def test_a_short_time_between_attacks_is_not_a_cycle(self):
        from runners.barbarians import _cycle

        self.assertEqual(_cycle({"cycle_minutes": 18}), 180)
        self.assertEqual(_cycle({"cycle_minutes": 215}), 215)


class ResourceValueTests(unittest.TestCase):
    """N-194: the loot counts each resource by what it is worth to the accounts (the hub's order), like the losses."""

    VALUES = {"marble": 3.0, "crystal": 2.5, "sulfur": 2.0, "wine": 1.5, "wood": 1.0}

    def test_the_ledger_keeps_the_loot_by_resource(self):
        import time as _time

        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner.is_network_error = lambda exc: False
        now = int(_time.time())
        back = {"is_own": True, "origin": {"cityId": 0}, "target": {"cityId": "37441"}, "is_returning": True,
                "event_id": 9, "event_time": now + 45, "cargo": {"wood": 600, "glass": 50, "sulfur": 300, "gold": 70}}

        class Game:
            def fetch_military_advisor(self, city_id):
                return {"movement_details": [dict(back)]}

        st = {"last_mission": "attack", "attacked_at": now - 3600, "attacked_level": 30}
        runner._on_the_way("j", Game(), "4477", "37441", st, {"loot_total": 0, "gold_total": 0}, [])
        entry = st["ledger"][-1]
        self.assertEqual(entry["loot_by_resource"], {"wood": 600, "crystal": 50, "sulfur": 300})
        self.assertEqual((entry["loot"], entry["gold"]), (950, 70))

    def test_the_book_weighs_the_loot(self):
        from runners.barbarians import loot_value

        ledger = [{"level": 30, "loot": 1000, "gold": 100, "loot_by_resource": {"marble": 1000}, "losses": {"303": 1},
                   "cycle_minutes": 60},
                  {"level": 31, "loot": 1000, "gold": 100, "loot_by_resource": {"wood": 1000}, "losses": {"303": 1},
                   "cycle_minutes": 60}]
        book = farm_book(ledger, {"303": 200}, None, None, self.VALUES)
        self.assertEqual(book[30]["profit"], 3000 + 100 - 200)
        self.assertEqual(book[31]["profit"], 1000 + 100 - 200)
        # without values (an old hub): one each, as before
        self.assertEqual(loot_value(ledger[0], None), 1100)
        self.assertEqual(loot_value({"loot": 1000, "gold": 0}, self.VALUES), 1000 * 2.0)


class ManyIslandsTests(unittest.TestCase):
    """N-194: one town farms the villages of several islands; each island counts only its own movements, and moving
    troops to another town is never an attack."""

    def test_each_island_its_own_movements(self):
        moves = [
            {"is_own": True, "mission": "plunder", "origin": {"cityId": 37436, "islandId": 4478},
             "target": {"cityId": 0, "name": "Vila de Bárbaros", "islandId": 4478}, "event_id": 1},
            {"is_own": True, "mission": "plunder", "origin": {"cityId": 37436, "islandId": 4479},
             "target": {"cityId": 0, "name": "Vila de Bárbaros", "islandId": 4479}, "event_id": 2},
            {"is_own": True, "mission": "deployarmy", "origin": {"cityId": 37441, "islandId": 4478},
             "target": {"cityId": 37436, "name": "Liberado", "islandId": 4478, "hasBarbarianFleet": True}, "event_id": 3},
        ]
        self.assertEqual([m["event_id"] for m in _barbarian_movements(moves, "37436", "4478")], [1])
        self.assertEqual([m["event_id"] for m in _barbarian_movements(moves, "37436", "4479")], [2])
        self.assertEqual([m["event_id"] for m in _barbarian_movements(moves, "37436")], [1, 2])

    def test_the_level_must_pay_its_losses(self):
        units = {"303": {"wood": 20, "sulfur": 14}}
        ledger = [{"level": lvl, "loot": 1000, "gold": 0, "losses": {"303": 10}, "cycle_minutes": 60,
                   "loot_by_resource": {"wood": 1000 * (lvl - 19), "sulfur": sulfur}}
                  for lvl, sulfur in ((20, 500), (21, 500), (22, 100), (23, 100), (24, 100))]
        book = farm_book(ledger, {"303": 10}, None, None, {"wood": 1, "sulfur": 2}, units)
        self.assertTrue(book[21]["covered"])
        self.assertEqual(book[22]["short"], ["sulfur"])                 # 100 sulfur, the losses take 140
        self.assertEqual(ideal_level(book), 21)                         # 22+ would pay more, but not their sulfur



class _ArmyHub:
    """The hub as the farm of several towns sees it (N-194)."""

    def __init__(self):
        self.logs = []
        self.cities = [
            {"id": 37436, "name": "Liberado", "island_id": 4478, "buildings": [{"building": "barracks", "position": 6}]},
            {"id": 37441, "name": "Juízo Final", "island_id": 4477, "buildings": [{"building": "barracks", "position": 7}]},
            {"id": 37435, "name": "Sentença", "island_id": 4479, "buildings": [{"building": "port", "position": 1}]},
            {"id": 37437, "name": "Decreto", "island_id": 4480, "buildings": []},
        ]

    def report_log(self, job_id, level, msg):
        self.logs.append(msg)

    def get_snapshot(self, game_account_id=None):
        return {"cities": self.cities}

    def get_troop_reservations(self, game_account_id, exclude_job_id=""):
        return {"reserved": {"37436": {"303": 100}}, "islands": {"wf-other": ["4477"]}}


class _ArmyGame:
    def __init__(self):
        self.trained, self.stationed = [], []
        self.home = {"37436": {303: 500, 302: 200}, "37441": {303: 90, 308: 40}}

    def fetch_stationed_units(self, city_id, building_type="troops"):
        return {"counts": dict(self.home.get(str(city_id), {}))}

    def fetch_barracks_state(self, city_id, position, kind):
        return {"units": [{"unit_id": 303, "max_build": 1000, "citizens": 1}, {"unit_id": 308, "max_build": 30, "citizens": 2}]}

    def train_units(self, city_id, position, order, kind):
        self.trained.append((city_id, dict(order)))

    def station_units(self, from_city_id, to_city_id, units, scope="troops", to_island_id=None):
        self.stationed.append((from_city_id, to_city_id, dict(units), to_island_id))
        return {"ok": True}


def _army_runner():
    runner = BarbariansRunner.__new__(BarbariansRunner)
    runner.hub = _ArmyHub()
    runner.is_network_error = lambda exc: False
    runner._game_account_id = "ga"
    runner._job_id = "j"
    runner._reserved = {}
    runner._population = lambda jid, client, town, st, fresh=False: {"citizens": 100, "max": 1000}
    return runner


class ArmyTests(unittest.TestCase):
    """N-194: one army for every island, the assault team kept apart, losses trained in any town and brought back."""

    def test_the_reserved_team_is_never_sent(self):
        runner = _army_runner()
        runner._load_reservations("j", "ga")
        self.assertEqual(runner._get_available_troops("j", _ArmyGame(), "37436"), {303: 400, 302: 200})

    def test_all_islands_but_the_ones_of_another_job(self):
        runner = _army_runner()
        others = runner._load_reservations("j", "ga")
        self.assertEqual(runner._account_islands("j", "ga", ["4478"], others), ["4478", "4479", "4480"])

    def test_what_the_army_town_cannot_train_goes_to_another_barracks_and_comes_back(self):
        runner = _army_runner()
        game = _ArmyGame()
        st = {}
        left = runner._retrain_elsewhere("j", game, "4478", "37436", {303: 50, 308: 60}, st)
        # Juízo Final: 80 free citizens of 100 (it keeps 20%): the biggest loss first, 30 giants (the barracks' max,
        # 2 citizens each), then 20 hoplites
        self.assertEqual(game.trained, [(37441, {308: 30, 303: 20})])
        self.assertEqual(left, {303: 30, 308: 30})
        self.assertEqual(st["trained_elsewhere"][0]["city"], "37441")
        runner._gather("j", game, "4478", "37436", st)
        self.assertEqual(game.stationed, [(37441, 37436, {308: 30, 303: 20}, 4478)])
        self.assertEqual(st["trained_elsewhere"], [])

    def test_gather_waits_for_what_is_still_in_training(self):
        runner = _army_runner()
        game = _ArmyGame()
        st = {"trained_elsewhere": [{"city": "37441", "units": {"303": 200}, "at": int(__import__("time").time())}]}
        runner._gather("j", game, "4478", "37436", st)
        self.assertEqual(game.stationed, [(37441, 37436, {303: 90}, 4478)])
        self.assertEqual(st["trained_elsewhere"][0]["units"], {"303": 110})



class SupplyTests(unittest.TestCase):
    """N-194 (Rodrigo, 15/11): the village is attacked from its own island; what that island lacks comes from the
    army's town: the simulator's army plus 15%, less what is already there, never the whole army."""

    def test_only_what_the_plan_asks_plus_15_percent_moves(self):
        import runners.barbarians as module

        runner = _army_runner()
        runner._garrison = {"37435": {"303": 50}}
        game = _ArmyGame()
        game.home["37435"] = {303: 50}                                   # Sentença: only its garrison
        game.get_barbarian_state = lambda island_id: {"level": 12}
        planned = []

        def plan(jid, island_id, level, barb, village, available, st):
            planned.append(dict(available))
            return {"army": {"303": 200, "302": 40}, "second_wave": {}}

        runner._simulator_plan = plan
        original = module.fetch_village_info
        module.fetch_village_info = lambda client, island_id: {"total": 1000}
        try:
            state = {"4479": {"status": "ready", "level": 12, "short_level": 12}}
            wake = []
            runner._supply("j", game, ["4479"], {"4479": "37435", "4478": "37436"}, state, "37436", wake)
        finally:
            module.fetch_village_info = original
        self.assertEqual(planned[0], {303: 500, 302: 200})              # the garrison is never counted
        self.assertEqual(game.stationed, [(37436, 37435, {303: 230, 302: 46}, 4479)])
        self.assertEqual(state["4479"]["incoming"]["units"], {"303": 230, "302": 46})
        self.assertTrue(wake)

    def test_a_village_its_island_can_win_gets_nothing(self):
        runner = _army_runner()
        game = _ArmyGame()
        state = {"4479": {"status": "ready", "level": 12, "short_level": None}}
        runner._supply("j", game, ["4479"], {"4479": "37435"}, state, "37436", [])
        self.assertEqual(game.stationed, [])

    def test_each_island_its_own_town(self):
        runner = _army_runner()
        towns = runner._island_towns("j", "ga", ["4478", "4479", "4477"], {"4478": "37436", "4479": "37436"})
        self.assertEqual(towns, {"4478": "37436", "4479": "37435", "4477": "37441"})



class ShipsTests(unittest.TestCase):
    """N-194 (Rodrigo, 15/11): the village's leftovers are collected as soon as the merchants are back (not after the
    troops), and a big village is not attacked when the free merchants cannot bring its loot."""

    def test_only_troops_coming_back_lets_the_collection_go(self):
        import time as _time

        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner.is_network_error = lambda exc: False
        now = int(_time.time())
        troops_home = {"is_own": True, "mission": "plunder", "origin": {"cityId": 0, "islandId": 4477},
                       "target": {"cityId": "37441", "islandId": 4477}, "is_returning": True, "event_id": 70,
                       "event_time": now + 600, "cargo": {}, "troops": {"phalanx": 300}}

        class Game:
            moves = [dict(troops_home)]

            def fetch_military_advisor(self, city_id):
                return {"movement_details": list(self.moves)}

        st = {"last_mission": "attack", "attacked_at": now - 7200}
        self.assertFalse(runner._on_the_way("j", Game(), "4477", "37441", st, {"loot_total": 0, "gold_total": 0}, []))
        Game.moves = [dict(troops_home, cargo={"wood": 5000}, event_id=71)]      # the merchants still on the way
        self.assertTrue(runner._on_the_way("j", Game(), "4477", "37441", st, {"loot_total": 0, "gold_total": 0}, []))

    def test_no_attack_while_the_merchants_are_away(self):
        import time as _time

        import runners.barbarians as module

        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub(plan={"can_win": True, "army": {"303": 300},
                                "prediction": {"rounds": 8, "morale_end": 70, "attacker_losses": {"303": 90}}})
        runner._get_available_troops = lambda jid, client, town: {303: 1000}
        runner._population = lambda jid, client, town, st, fresh=False: {}
        runner._train_seconds = lambda jid, client, town, st: {}
        runner._route = lambda jid, island_id, st, village: (st.update(route={"free_ships": 100, "at": int(_time.time())}) or {})
        sent = []
        village = {"total": 110_500, "gold": 78_000, "defenders": [["Viking", 650]], "defender_ids": {"316": 650},
                   "defenders_found": True}
        original = module.fetch_village_info
        module.fetch_village_info = lambda client, island_id: dict(village)

        class Game:
            def get_barbarian_state(self, island_id):
                return {"level": 34, "destroyed": False, "under_attack": False}

            def attack_barbarian_village(self, **kw):
                sent.append(kw)
                return {"transporters": kw["transporters"], "free_ships": 100}

        try:
            st = {}
            totals = {"total_attacks": 0, "total_loots": 0, "loot_total": 0, "gold_total": 0}
            runner._fleet_size = lambda: 189
            runner._island_round("j", Game(), "4477", "37441", st, totals, "schematic", "", True, 1, 50, 0, 0)
            self.assertEqual(sent, [])                                       # 89 of the 189 are away with other loot
            self.assertEqual(st["ships_short"], 34)
            runner._fleet_size = lambda: 110                                 # the fleet itself is smaller than the loot:
            runner._island_round("j", Game(), "4477", "37441", st, totals, "schematic", "", True, 1, 50, 0, 0)
            self.assertEqual(len(sent), 1)                                   # attacked with what there is (the rest is collected)
        finally:
            module.fetch_village_info = original



class ReportMatchTests(unittest.TestCase):
    """N-194 (16/11): several islands attack at once; each island takes only its own battle report (the defenders its
    village had), never one another island took, never the barbarians' revenge on our town (it has a town link)."""

    def _runner(self, state):
        runner = BarbariansRunner.__new__(BarbariansRunner)
        runner.hub = _Hub()
        runner.is_network_error = lambda exc: False
        runner._state = state
        runner._retrain = False
        return runner

    def _game(self, reports, joined):
        class Game:
            def fetch_combat_reports(self, city_id, limit=10):
                return [dict(r) for r in reports]

            def fetch_combat_detailed_report(self, city_id, combat_id):
                html = f"Bárbaros juntou-se ao combate, do lado do defensor, com as seguintes unidades: {joined[combat_id]}"
                return {"rounds": [{"round": 1, "html": html}], "attacker_losses": {}}

        return Game()

    def test_each_island_its_own_report(self):
        reports = [
            {"combat_id": 500, "text": "Batalha por Juízo Final", "result": "defeat", "rounds": 2, "city_id_target": 37441},
            {"combat_id": 501, "text": "Batalha por Vila de Bárbaros", "result": "victory", "rounds": 1, "city_id_target": 0},
            {"combat_id": 502, "text": "Batalha por Vila de Bárbaros", "result": "victory", "rounds": 2, "city_id_target": 0},
        ]
        joined = {500: "360 160 35", 501: "50", 502: "75 2"}
        a = {"report_pending": True, "attacked_level": 9, "attacked_at": 1, "defenders": [["Membro do Clube de Bárbaros", 75],
                                                                                          ["Aríete Bárbaro", 2]]}
        b = {"report_pending": True, "attacked_level": 8, "attacked_at": 1, "defenders": [["Membro do Clube de Bárbaros", 50]]}
        runner = self._runner({"4479": a, "4480": b})
        runner._learn_from_report("j", self._game(reports, joined), "4479", "37435", a)
        runner._learn_from_report("j", self._game(reports, joined), "4480", "37437", b)
        self.assertEqual((a["last_report_id"], a["last_battle"]["result"]), (502, "victory"))
        self.assertEqual((b["last_report_id"], b["last_battle"]["result"]), (501, "victory"))

    def test_the_book_counts_the_other_jobs_battles(self):
        runner = self._runner({})
        runner._target_auto = True
        runner._target_level_input = 0
        low = [{"level": lvl, "loot": 4000, "gold": 7000, "losses": {}, "cycle_minutes": 70 + lvl, "book": 2}
               for lvl in (9, 10, 11, 12)]
        st = {"ledger": low, "prices": {}}
        runner._account_ledger = [{"level": 34, "loot": 94_000, "gold": 78_000, "losses": {"303": 90}, "cycle_minutes": 215, "book": 2}]
        self.assertEqual(runner._target_level(st), 0)                   # the 34 pays more: keep climbing
