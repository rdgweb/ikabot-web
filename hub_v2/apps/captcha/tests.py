import base64
import json
from unittest import mock

from django.test import TestCase, override_settings

from apps.captcha.models import CaptchaChallenge

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
# What an agent sent as "image" when the game stopped serving the captcha PNG
# on the old endpoint (N-83): the game's JSON answer.
GAME_JSON = json.dumps([["custom", ["reload", {"link": "?view=city&cityId=39269", "isDevHost": 0}]]]).encode()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@override_settings(AGENT_TOKEN="test-agent-token", AGENT_ALLOWED_IPS="", IKABOTAPI_URL="http://ikabotapi.test")
class CaptchaImageValidationTests(TestCase):
    """The agent captcha endpoints only take real images (N-83)."""

    def _post(self, path: str, payload: dict):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_AGENT_TOKEN="test-agent-token",
        )

    @mock.patch("apps.game.api.agent.http_requests.post")
    def test_challenge_refuses_payload_that_is_not_an_image(self, solver_post):
        response = self._post("/api/agent/captcha/challenge/", {"type": "pirate", "image_b64": _b64(GAME_JSON)})

        self.assertEqual(response.status_code, 400)
        solver_post.assert_not_called()
        self.assertEqual(CaptchaChallenge.objects.count(), 0)

    @mock.patch("apps.game.api.agent.http_requests.post")
    def test_challenge_sends_a_real_png_to_the_solver(self, solver_post):
        solver_post.return_value = mock.Mock(status_code=200, json=lambda: "abc12", raise_for_status=lambda: None)

        response = self._post("/api/agent/captcha/challenge/", {"type": "pirate", "image_b64": _b64(PNG)})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"solution": "ABC12", "challenge_id": None})
        self.assertEqual(solver_post.call_args.kwargs["files"]["image"][1], PNG)
        challenge = CaptchaChallenge.objects.get()
        self.assertEqual((challenge.status, challenge.solve_method, challenge.solution), ("solved", "auto", "ABC12"))

    @mock.patch("apps.game.api.agent.http_requests.post")
    def test_solve_refuses_payload_that_is_not_an_image(self, solver_post):
        response = self._post("/api/agent/captcha/solve/", {"type": "pirate", "images": {"image": _b64(GAME_JSON)}})

        self.assertEqual(response.status_code, 400)
        solver_post.assert_not_called()
