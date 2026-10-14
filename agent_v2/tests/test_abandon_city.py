"""Abandon colony: the captcha read embedded in the page or from its old address, and only a PNG goes on (N-85)."""

import base64
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules stub `game_client` with bare modules; this one needs the real package.
for _name in [name for name in sys.modules if name == "game_client" or name.startswith("game_client.")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from game_client.actions import abandon_city  # noqa: E402
from game_client.exceptions import ActionError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 40
PNG_B64 = base64.b64encode(PNG).decode("ascii")
HIDDEN = '<input type="hidden" name="action" value="DeleteColony"><input type="hidden" name="cityId" value="77">'


def _page(img: str) -> str:
    """The abandon window as the game answers it: the form inside a JSON-escaped template."""
    form = f'<form id="abolishColony" action="" method="post">{HIDDEN}{img}<input type="text" name="captcha"></form>'
    # the game escapes the slashes too ("<\/form>", "image\/png")
    return json.dumps([["updateGlobalData", {"actionRequest": "tok123"}], ["changeView", ["abolishCity", form]]]).replace("/", "\/")


EMBEDDED = _page(f'<img class="captchaImage" id="js_captchaImage" src="data:image/png;base64,{PNG_B64}">')
BY_ADDRESS = _page('<img class="captchaImage" src="?action=Options&amp;function=createCaptcha&amp;cityId=77">')
NO_IMAGE = _page("")


class _Resp:
    def __init__(self, text: str = "", content: bytes | None = None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")


class _Hub:
    def __init__(self):
        self.images = []

    def create_captcha_challenge(self, kind, image_b64, **kwargs):
        self.images.append(base64.b64decode(image_b64))
        return {"challenge_id": 5, "solution": "abcd"}


class _Session:
    def __init__(self):
        self.posted = []

    def post(self, url, data=None, timeout=None):
        self.posted.append(data)
        return _Resp("ok")


class _Client:
    def __init__(self, page: str, captcha_answer: bytes = PNG):
        self._server_url = "https://s78-br.ikariam.gameforge.com/index.php"
        self._action_request = "tok123"
        self._last_request_time = 0
        self.page = page
        self.captcha_answer = captcha_answer
        self.gets = []
        self.hub = _Hub()
        self.session = _Session()

    def _request(self, method, url, params=None, timeout=None):
        self.gets.append(params)
        if (params or {}).get("view") == "abolishCity":
            return _Resp(self.page)
        return _Resp(content=self.captcha_answer)

    def _enforce_delay(self):
        pass


class PreviewTests(unittest.TestCase):
    def test_an_embedded_image_is_read_from_the_page(self):
        preview = abandon_city.AbandonColonyPreviewAction(_Client(EMBEDDED)).execute(city_id=77)
        self.assertEqual((preview["captcha_embedded"], preview["captcha_image"], preview["captcha_src"]), (True, PNG, ""))
        self.assertEqual(preview["hidden_fields"]["action"], "DeleteColony")

    def test_an_image_by_address_is_kept_as_before(self):
        preview = abandon_city.AbandonColonyPreviewAction(_Client(BY_ADDRESS)).execute(city_id=77)
        self.assertEqual((preview["captcha_embedded"], preview["captcha_image"]), (False, None))
        self.assertEqual(preview["captcha_params"]["function"], "createCaptcha")


class AbandonTests(unittest.TestCase):
    def test_the_embedded_png_goes_to_the_solver_without_another_request(self):
        client = _Client(EMBEDDED)
        result = abandon_city.AbandonColonyAction(client).execute(city_id=77, game_account_id="ga")
        self.assertEqual(client.hub.images, [PNG])
        self.assertEqual(len(client.gets), 1)                                    # only the window, no captcha address
        self.assertEqual((result["ok"], result["captcha_solution"]), (True, "ABCD"))
        self.assertEqual((client.session.posted[0]["captcha"], client.session.posted[0]["action"]), ("ABCD", "DeleteColony"))

    def test_by_address_the_png_is_fetched_and_sent(self):
        client = _Client(BY_ADDRESS)
        abandon_city.AbandonColonyAction(client).execute(city_id=77)
        self.assertEqual(client.hub.images, [PNG])
        self.assertEqual(client.gets[1]["function"], "createCaptcha")

    def test_what_is_not_an_image_never_reaches_the_solver_and_nothing_is_submitted(self):
        reload_json = json.dumps([["custom", ["reload", {"link": "?view=city&cityId=77"}]]]).encode("utf-8")
        client = _Client(BY_ADDRESS, captcha_answer=reload_json)
        with self.assertRaises(ActionError) as caught:
            abandon_city.AbandonColonyAction(client).execute(city_id=77)
        self.assertIn("não devolveu uma imagem", str(caught.exception))
        self.assertIn("custom", str(caught.exception))
        self.assertEqual((client.hub.images, client.session.posted), ([], []))

    def test_a_window_without_any_captcha_says_so(self):
        client = _Client(NO_IMAGE)
        with self.assertRaises(ActionError) as caught:
            abandon_city.AbandonColonyAction(client).execute(city_id=77)
        self.assertIn("Captcha do abandono não encontrado", str(caught.exception))
        self.assertEqual(client.session.posted, [])


if __name__ == "__main__":
    unittest.main()
