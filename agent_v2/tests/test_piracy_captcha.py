"""Piracy captcha: the image comes embedded in the capture response (N-83)."""

import base64
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Other test modules stub `game_client` with bare modules; this one needs the real package.
for _name in [name for name in sys.modules if name == "game_client" or name.startswith("game_client.")]:
    if not getattr(sys.modules[_name], "__file__", None):
        del sys.modules[_name]

from game_client.actions import piracy as piracy_actions  # noqa: E402
from game_client.exceptions import CaptchaRequiredError  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
PNG_B64 = base64.b64encode(PNG).decode("ascii")
# What the old createCaptcha endpoint answers since 2026-09-22 (real shape, token-free).
RELOAD_JSON = json.dumps([["custom", ["reload", {"link": "?view=city&cityId=39269", "isDevHost": 0}]], ["popupData", None]])


def _capture_response(*, ship_in_port: bool, captcha: bool, image: bool = True, time_remaining: int = 0, token: str = "abc123"):
    template = {
        "load_js": {"params": json.dumps({
            "showPirateFortressShip": 1 if ship_in_port else 0,
            "ongoingMissionTimeRemaining": time_remaining,
        })},
    }
    if captcha:
        template["captchaNeeded"] = 1
        if image:
            template["js_captchaImage"] = {"src": f"data:image/png;base64,{PNG_B64}"}
    return json.dumps([
        ["updateGlobalData", {"actionRequest": token}],
        ["updateTemplateData", template],
    ])


class _Resp:
    def __init__(self, text: str = "", content: bytes | None = None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")

    def json(self):
        return json.loads(self.text)


class _Session:
    def __init__(self, posts, legacy=b""):
        self._posts = list(posts)
        self.posted = []
        self.gets = []
        self._legacy = legacy

    def post(self, url, data=None, headers=None, timeout=None):
        self.posted.append(dict(data or {}))
        return _Resp(self._posts.pop(0))

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append(dict(params or {}))
        return _Resp(content=self._legacy)


class _Hub:
    def __init__(self, solutions):
        self._solutions = list(solutions)
        self.images = []

    def create_captcha_challenge(self, captcha_type, image_b64, game_account_id=""):
        self.images.append(base64.b64decode(image_b64))
        return {"solution": self._solutions.pop(0), "challenge_id": None}


class _Client:
    def __init__(self, session, hub):
        self.session = session
        self.hub = hub
        self._server_url = "https://s1-br.example/index.php"
        self._action_request = "token0"


class ExtractCaptchaImageTests(unittest.TestCase):
    def test_reads_the_png_embedded_in_the_template_data(self):
        text = _capture_response(ship_in_port=True, captcha=True)
        self.assertEqual(piracy_actions.extract_captcha_image(text, json.loads(text)), PNG)

    def test_reads_the_png_from_the_raw_json_escaped_text(self):
        raw = '... "src":"data:image\\/png;base64,' + PNG_B64.replace("/", "\\/") + '" ...'
        self.assertEqual(piracy_actions.extract_captcha_image(raw, None), PNG)

    def test_no_image_in_the_response(self):
        text = _capture_response(ship_in_port=True, captcha=True, image=False)
        self.assertIsNone(piracy_actions.extract_captcha_image(text, json.loads(text)))
        self.assertIsNone(piracy_actions.extract_captcha_image(RELOAD_JSON, json.loads(RELOAD_JSON)))

    def test_data_uri_that_is_not_a_png_is_ignored(self):
        fake = base64.b64encode(b"not an image at all").decode("ascii")
        data = [["updateTemplateData", {"js_captchaImage": {"src": f"data:image/png;base64,{fake}"}}]]
        self.assertIsNone(piracy_actions.extract_captcha_image(json.dumps(data), data))

    def test_is_png_bytes(self):
        self.assertTrue(piracy_actions.is_png_bytes(PNG))
        self.assertFalse(piracy_actions.is_png_bytes(RELOAD_JSON.encode()))
        self.assertFalse(piracy_actions.is_png_bytes(b""))
        self.assertFalse(piracy_actions.is_png_bytes("texto"))


@mock.patch.object(piracy_actions.time, "sleep", lambda *_a, **_k: None)
class PiracyMissionCaptchaFlowTests(unittest.TestCase):
    def _run(self, session, hub):
        return piracy_actions.PiracyMissionAction(_Client(session, hub)).execute(39269, 9, game_account_id="ga-1")

    def test_embedded_captcha_is_solved_and_answered(self):
        session = _Session([
            _capture_response(ship_in_port=True, captcha=True, token="aa11"),
            _capture_response(ship_in_port=False, captcha=False, time_remaining=1800, token="bb22"),
        ])
        hub = _Hub(["abcde"])
        result = self._run(session, hub)

        self.assertTrue(result["success"])
        self.assertEqual(result["time_remaining"], 1800)
        self.assertEqual(hub.images, [PNG])  # the real picture, never the JSON
        self.assertEqual(session.gets, [])   # old createCaptcha endpoint not needed
        answer = session.posted[1]
        self.assertEqual((answer["captchaNeeded"], answer["captcha"]), ("1", "ABCDE"))
        self.assertEqual(answer["actionRequest"], "aa11")  # token refreshed from the first answer
        self.assertEqual(session.posted[0]["templateView"], "pirateFortress")

    def test_no_captcha_means_no_solver_call(self):
        session = _Session([_capture_response(ship_in_port=False, captcha=False, time_remaining=900)])
        hub = _Hub([])
        self.assertTrue(self._run(session, hub)["success"])
        self.assertEqual(hub.images, [])

    def test_unsolved_captcha_asks_for_a_fresh_one(self):
        session = _Session([
            _capture_response(ship_in_port=True, captcha=True),
            _capture_response(ship_in_port=True, captcha=True),
            _capture_response(ship_in_port=False, captcha=False, time_remaining=600),
        ])
        hub = _Hub(["", "XYZ12"])
        self.assertTrue(self._run(session, hub)["success"])
        self.assertEqual(len(hub.images), 2)
        self.assertNotIn("captcha", session.posted[1])      # plain capture = new captcha
        self.assertEqual(session.posted[2]["captcha"], "XYZ12")

    def test_json_is_never_sent_to_the_solver_as_image(self):
        attempts = piracy_actions.PiracyMissionAction._MAX_CAPTCHA_RETRIES
        session = _Session(
            [_capture_response(ship_in_port=True, captcha=True, image=False)] * (attempts + 1),
            legacy=RELOAD_JSON.encode("utf-8"),
        )
        hub = _Hub([])
        with self.assertRaises(CaptchaRequiredError):
            self._run(session, hub)
        self.assertEqual(hub.images, [])
        self.assertEqual(len(session.gets), attempts)
        self.assertNotIn("ajax", session.gets[0])

    def test_old_endpoint_still_accepted_when_it_answers_a_png(self):
        session = _Session(
            [
                _capture_response(ship_in_port=True, captcha=True, image=False),
                _capture_response(ship_in_port=False, captcha=False, time_remaining=300),
            ],
            legacy=PNG,
        )
        hub = _Hub(["OLD99"])
        self.assertTrue(self._run(session, hub)["success"])
        self.assertEqual(hub.images, [PNG])


if __name__ == "__main__":
    unittest.main()
