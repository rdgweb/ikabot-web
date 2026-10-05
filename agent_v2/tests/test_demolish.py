"""N-57: demolir edificios -- travas, passo a passo, captcha e simulacao.

O "jogo" aqui e um simulador minimo do que foi capturado: demolishBuilding tira
um nivel (no nivel 1 a posicao fica livre) e completeDemolishBuilding so
funciona com o captcha certo.
"""

import base64
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules leave bare stub packages behind; this one needs the real ones.
for _name in [n for n in sys.modules if n.split(".")[0] in ("game_client", "runners", "core")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from game_client.actions.demolish import (  # noqa: E402
    demolish_refusal,
    extract_captcha_png,
)
from runners import demolish as demolish_runner  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
GOOD_CAPTCHA = "GOOD1"


def _pos(building, level, building_id=7, **extra):
    return {"building": building, "level": level, "buildingId": building_id, "isBusy": False, **extra}


def _empty():
    return {"building": "buildingGround land", "level": 0, "buildingId": None}


class _Resp:
    def __init__(self, payload):
        self.text = json.dumps(payload)

    def json(self):
        return json.loads(self.text)


class _Game:
    """Session + city state of one city."""

    def __init__(self, positions, *, frozen=False, captcha_image=True):
        self.positions = positions
        self.frozen = frozen                # the game ignores demolish requests
        self.captcha_image = captcha_image
        self.posts = []
        self.gets = []

    def request(self, method, url, headers=None, timeout=None, params=None, data=None):
        if method == "GET":
            self.gets.append(dict(params))
            complete = params["view"] == "completeBuildingDemolition"
            html = "ajaxHandlerCall('?action=CityScreen&function=demolishBuilding')"
            if complete:
                html = "<form>function=completeDemolishBuilding"
                if self.captcha_image:
                    html += f'<img id="js_captchaImage" src="{PNG_URI}">'
            return _Resp([["updateGlobalData", {"actionRequest": "aa11"}], ["addWindow", [html]]])

        self.posts.append(dict(data))
        position = int(data["position"])
        current = self.positions[position]
        feedback = "Sua ordem foi executada."
        if not self.frozen and int(data["level"]) == int(current.get("level") or 0):
            if data["function"] == "demolishBuilding":
                if current["level"] <= 1:
                    self.positions[position] = _empty()
                else:
                    current["level"] -= 1
            elif data["function"] == "completeDemolishBuilding":
                if data.get("captcha") == GOOD_CAPTCHA:
                    self.positions[position] = _empty()
                else:
                    feedback = "Captcha errado."
        return _Resp([["updateGlobalData", {"actionRequest": "bb22"}], ["provideFeedback", [{"text": feedback, "type": 10}]]])


class _Client:
    def __init__(self, game):
        self.session = game
        self._game = game
        self._server_url = "https://s1-br.example/index.php"
        self._action_request = "token0"

    def _enforce_delay(self):
        pass

    def get_building_positions(self, city_id):
        return [dict(p) for p in self._game.positions]


class _Hub:
    def __init__(self, solutions=()):
        self.solutions = list(solutions)
        self.images = []
        self.snapshot_updates = []

    def create_captcha_challenge(self, captcha_type, image_b64, game_account_id="", display_type="", extra_data=None):
        self.images.append(base64.b64decode(image_b64))
        return {"solution": self.solutions.pop(0) if self.solutions else "", "challenge_id": None}

    def get_snapshot(self, game_account_id=None):
        return {"cities": [{"id": "42", "buildings": []}], "base_snapshot": {}, "military": {}}

    def update_snapshot(self, account_id, payload, game_account_id=None):
        self.snapshot_updates.append(payload)


def _run(game, items, *, hub=None, dry_run=False):
    runner = object.__new__(demolish_runner.DemolishBuildingsRunner)
    runner.hub = hub or _Hub()
    runner.logs = []
    runner.log = lambda jid, level, msg: runner.logs.append((level, msg))
    runner.resolve_credentials = lambda *a, **k: {"email": "x"}
    runner.get_or_login_game_client = lambda *a, **k: _Client(game)
    runner.save_game_client = lambda *a, **k: None
    result = runner.execute({
        "job_id": "j1", "account_id": "a1", "game_account_id": "g1",
        "inputs": {"city_id": "42", "items": items, "dry_run": dry_run},
    })
    return runner, result


def _item(position, building, level, levels=1, complete=False):
    return {"position": position, "building": building, "level": level, "levels": levels, "complete": complete, "name": building}


def _city():
    return [_pos("townHall", 18, 0), _pos("warehouse", 5), _pos("tavern", 2, 9), _empty(), _pos("academy", 7, 4)]


class DemolishRefusalTests(unittest.TestCase):
    def test_only_the_expected_building_at_the_expected_level_passes(self):
        self.assertEqual(demolish_refusal(_pos("warehouse", 5), "warehouse", 5), "")
        self.assertEqual(demolish_refusal(_pos("warehouse", 5), "tavern", 5), "edificio_diferente")
        self.assertEqual(demolish_refusal(_pos("warehouse", 4), "warehouse", 5), "nivel_diferente")
        self.assertEqual(demolish_refusal(_empty(), "warehouse", 5), "posicao_vazia")
        self.assertEqual(demolish_refusal(None, "warehouse", 5), "posicao_vazia")

    def test_town_hall_and_buildings_under_work_are_refused(self):
        self.assertEqual(demolish_refusal(_pos("townHall", 18, 0), "townHall", 18), "nao_demolivel")
        self.assertEqual(demolish_refusal(_pos("warehouse constructionSite", 5), "warehouse", 5), "em_obra")
        self.assertEqual(demolish_refusal(_pos("warehouse", 5, completed=1759000000), "warehouse", 5), "em_obra")
        self.assertEqual(demolish_refusal(_pos("warehouse", 5, inConstructionList=True), "warehouse", 5), "em_obra")
        self.assertEqual(demolish_refusal(_pos("barracks", 5, isBusy=True), "barracks", 5), "ocupado")

    def test_captcha_png_is_read_from_the_confirmation_window(self):
        self.assertEqual(extract_captcha_png(f'<img id="js_captchaImage" src="{PNG_URI}">'), PNG)
        self.assertEqual(extract_captcha_png(json.dumps([f'<img src="{PNG_URI}">'])), PNG)  # JSON-escaped slashes
        self.assertIsNone(extract_captcha_png("<form>sem imagem</form>"))
        self.assertIsNone(extract_captcha_png("data:image/png;base64," + base64.b64encode(b"nao e png").decode()))


class ParseItemsTests(unittest.TestCase):
    def test_malformed_and_out_of_range_items_are_dropped(self):
        items = demolish_runner.parse_items([
            _item(1, "warehouse", 5, 2),
            _item(1, "warehouse", 5, 1),        # same position twice
            _item(2, "tavern", 2, 3),           # more levels than it has
            _item(4, "academy", 7, 0),          # zero levels
            {"position": "x", "building": "wall", "level": 3, "levels": 1},
            _item(5, "", 3, 1),
            "lixo",
        ])
        self.assertEqual([(i["position"], i["levels"]) for i in items], [(1, 2)])

    def test_complete_always_means_every_level_and_json_text_is_accepted(self):
        items = demolish_runner.parse_items(json.dumps([_item(4, "academy", 7, 1, complete=True)]))
        self.assertEqual((items[0]["levels"], items[0]["complete"]), (7, True))


class DemolishRunnerTests(unittest.TestCase):
    def test_takes_exactly_the_requested_levels_one_by_one(self):
        game = _Game(_city())
        runner, result = _run(game, [_item(1, "warehouse", 5, 2)])

        self.assertTrue(result.success)
        self.assertEqual([p["level"] for p in game.posts], ["5", "4"])
        self.assertEqual({p["function"] for p in game.posts}, {"demolishBuilding"})
        self.assertEqual(game.positions[1]["level"], 3)
        self.assertEqual(len(runner.hub.snapshot_updates), 1)

    def test_removing_every_level_frees_the_position(self):
        game = _Game(_city())
        _, result = _run(game, [_item(2, "tavern", 2, 2)])

        self.assertTrue(result.success)
        self.assertEqual(len(game.posts), 2)
        self.assertIsNone(game.positions[2]["buildingId"])

    def test_nothing_is_sent_when_the_game_differs_from_what_the_user_saw(self):
        game = _Game(_city())
        _, result = _run(game, [
            _item(1, "tavern", 5),       # another building is there
            _item(2, "tavern", 9),       # level changed since the panel was loaded
            _item(0, "townHall", 18),    # never
            _item(3, "warehouse", 3),    # empty spot
            _item(30, "warehouse", 3),   # no such position
        ])

        self.assertEqual(game.posts, [])
        self.assertEqual(
            [r["reason"] for r in result.data["results"]],
            ["edificio_diferente", "nivel_diferente", "nao_demolivel", "posicao_vazia", "posicao_invalida"],
        )

    def test_stops_everything_when_the_game_does_not_follow(self):
        game = _Game(_city(), frozen=True)
        _, result = _run(game, [_item(1, "warehouse", 5, 3), _item(4, "academy", 7, 1)])

        self.assertFalse(result.success)
        self.assertEqual(len(game.posts), 1)     # one try, then stop: the academy is never touched
        self.assertEqual([r["status"] for r in result.data["results"]], ["unexpected"])

    def test_running_the_same_job_again_demolishes_nothing_more(self):
        game = _Game(_city())
        items = [_item(1, "warehouse", 5, 2)]
        _run(game, items)
        sent = len(game.posts)
        _, again = _run(game, items)

        self.assertEqual(len(game.posts), sent)
        self.assertEqual(again.data["results"][0]["reason"], "nivel_diferente")
        self.assertEqual(game.positions[1]["level"], 3)

    def test_dry_run_only_opens_the_confirmation_windows(self):
        game = _Game(_city())
        runner, result = _run(game, [_item(1, "warehouse", 5, 2), _item(4, "academy", 7, complete=True)], dry_run=True)

        self.assertTrue(result.success)
        self.assertEqual(game.posts, [])
        self.assertEqual([g["view"] for g in game.gets], ["buildings_demolition", "completeBuildingDemolition"])
        self.assertEqual(runner.hub.images, [])            # captcha is not even sent to the solver
        self.assertEqual(runner.hub.snapshot_updates, [])
        self.assertEqual((game.positions[1]["level"], game.positions[4]["level"]), (5, 7))

    def test_complete_demolition_solves_the_embedded_captcha(self):
        game = _Game(_city())
        hub = _Hub([GOOD_CAPTCHA.lower()])
        _, result = _run(game, [_item(4, "academy", 7, complete=True)], hub=hub)

        self.assertTrue(result.success)
        self.assertEqual(hub.images, [PNG])
        self.assertEqual((game.posts[0]["function"], game.posts[0]["captcha"], game.posts[0]["level"]),
                         ("completeDemolishBuilding", GOOD_CAPTCHA, "7"))
        self.assertIsNone(game.positions[4]["buildingId"])

    def test_wrong_captcha_is_retried_with_a_new_one(self):
        game = _Game(_city())
        hub = _Hub(["WRONG", GOOD_CAPTCHA])
        _, result = _run(game, [_item(4, "academy", 7, complete=True)], hub=hub)

        self.assertTrue(result.success)
        self.assertEqual([p["captcha"] for p in game.posts], ["WRONG", GOOD_CAPTCHA])
        self.assertEqual(len(game.gets), 2)

    def test_complete_without_captcha_image_or_solution_sends_nothing(self):
        game = _Game(_city(), captcha_image=False)
        _, no_image = _run(game, [_item(4, "academy", 7, complete=True)])
        self.assertEqual(game.posts, [])
        self.assertFalse(no_image.success)

        game = _Game(_city())
        _, no_solution = _run(game, [_item(4, "academy", 7, complete=True)], hub=_Hub([]))
        self.assertEqual(game.posts, [])
        self.assertEqual(no_solution.data["results"][0]["status"], "captcha_failed")
        self.assertEqual(game.positions[4]["level"], 7)


if __name__ == "__main__":
    unittest.main()
