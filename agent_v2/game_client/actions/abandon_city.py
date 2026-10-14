"""Abandon colony helpers.

N-85: since 2026-09 the game embeds the captchas as a base64 PNG in the page (the same change
N-83 met in piracy); the old createCaptcha address answers JSON instead of an image. The
abandon form is read both ways: the embedded image first, the old address only when there is
none. Whatever is not a PNG never reaches the solver: the job fails saying what came instead.
"""

from __future__ import annotations

import base64
import re
import time
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlparse

from ..exceptions import ActionError
from .base_action import BaseAction
from .piracy import extract_captcha_image, is_png_bytes


def _parse_hidden_inputs(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in re.finditer(
        r'<input[^>]+type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
        html,
        flags=re.IGNORECASE,
    ):
        out[unescape(match.group(1))] = unescape(match.group(2))
    return out


def _extract_abolish_form(html: str) -> str:
    marker = "value=\\\"DeleteColony\\\""
    idx = html.find(marker)
    if idx >= 0:
        start = html.rfind("<form", 0, idx)
        end = html.find("<\\/form>", idx)
        if start >= 0 and end >= 0:
            return html[start:end + len("<\\/form>")].replace('\\"', '"').replace("\\/", "/")

    marker = 'value="DeleteColony"'
    idx = html.find(marker)
    if idx >= 0:
        start = html.rfind("<form", 0, idx)
        end = html.find("</form>", idx)
        if start >= 0 and end >= 0:
            return html[start:end + len("</form>")]
    return ""


def _extract_captcha_src(html: str) -> str:
    """The address of the captcha image in the form ("" when the image is embedded or missing)."""
    match = re.search(r'<img[^>]+class=\\"captchaImage\\"[^>]+src=\\"([^\\"]+)\\"', html, flags=re.IGNORECASE)
    src = unescape(match.group(1)).replace("\\/", "/") if match else ""
    if not src:
        match = re.search(r'<img[^>]+class="captchaImage"[^>]+src="([^"]+)"', html, flags=re.IGNORECASE)
        src = unescape(match.group(1)) if match else ""
    return "" if src.startswith("data:") else src


def _describe(content: bytes) -> str:
    """What came instead of an image, short enough for a job log."""
    head = (content or b"")[:60]
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return f"{len(content or b'')} bytes que não são PNG"
    return f"{len(content or b'')} bytes, começando por {text!r}" if text else "resposta vazia"


class AbandonColonyPreviewAction(BaseAction):
    """Fetch the abandon colony form and captcha metadata."""

    def execute(self, city_id: int | str, **kwargs: Any) -> dict[str, Any]:
        params = {
            "view": "abolishCity",
            "cityId": str(city_id),
            "backgroundView": "city",
            "currentCityId": str(city_id),
        }
        resp = self.client._request("GET", self.client._server_url, params=params, timeout=30)
        html = resp.text
        form_html = _extract_abolish_form(html)
        if not form_html:
            raise ActionError("Abandon colony form not found", action="abandon_colony_preview")
        hidden = _parse_hidden_inputs(form_html)
        captcha_src = _extract_captcha_src(form_html)
        # the image embedded in the page, as the game sends it since 2026-09 (None = not there)
        captcha_image = extract_captcha_image(html)
        captcha_params = dict(parse_qsl(urlparse(captcha_src).query, keep_blank_values=True)) if captcha_src else {}
        action_request_match = re.search(r'actionRequest\s*:\s*"([a-zA-Z0-9_\-]+)"', html)
        return {
            "city_id": str(city_id),
            "hidden_fields": hidden,
            "captcha_src": captcha_src,
            "captcha_params": captcha_params,
            "action_request": action_request_match.group(1) if action_request_match else self.client._action_request,
            "captcha_embedded": captcha_image is not None,
            "captcha_image": captcha_image,
            "html": form_html,
        }


class AbandonColonyAction(BaseAction):
    """Solve the captcha and submit DeleteColony."""

    def execute(
        self,
        city_id: int | str,
        *,
        game_account_id: str = "",
        captcha_timeout_sec: int = 120,
        **kwargs: Any,
    ) -> dict[str, Any]:
        preview = AbandonColonyPreviewAction(self.client).execute(city_id=city_id)
        image = preview.get("captcha_image")
        if not image:
            captcha_params = dict(preview.get("captcha_params") or {})
            if not captcha_params:
                raise ActionError("Captcha do abandono não encontrado: nem embutido na página nem por endereço", action="abandon_colony")
            captcha_resp = self.client._request(
                "GET",
                self.client._server_url,
                params=captcha_params,
                timeout=30,
            )
            image = captcha_resp.content
        if not is_png_bytes(image):
            # the solver (and the person on Telegram) only reads a PNG; anything else is a change in the game
            raise ActionError(f"O jogo não devolveu uma imagem de captcha para o abandono ({_describe(image)})", action="abandon_colony")

        image_b64 = base64.b64encode(image).decode("ascii")
        challenge = self.client.hub.create_captcha_challenge(
            "pirate",
            image_b64,
            game_account_id=game_account_id or "",
            display_type="abandon",
            extra_data={"context": "abandon_colony", "city_id": str(city_id)},
        )
        solution = str(challenge.get("solution") or "").strip().upper()
        if not solution and challenge.get("challenge_id"):
            solution = self.client.hub.poll_captcha_solution(
                challenge["challenge_id"],
                timeout_sec=max(30, int(captcha_timeout_sec)),
                interval=10,
            ).strip().upper()
        if not solution:
            raise ActionError("Captcha solution not available for abandon colony", action="abandon_colony")

        payload = dict(preview.get("hidden_fields") or {})
        payload["action"] = payload.get("action", "DeleteColony")
        payload["cityId"] = payload.get("cityId", str(city_id))
        payload["captchaNeeded"] = "1"
        payload["captcha"] = solution
        action_request = str(preview.get("action_request") or "").strip()
        if action_request and "actionRequest" not in payload:
            payload["actionRequest"] = action_request

        # Submit directly to avoid the generic captcha detector short-circuiting this flow.
        self.client._enforce_delay()
        resp = self.client.session.post(
            self.client._server_url,
            data=payload,
            timeout=30,
        )
        self.client._last_request_time = time.time()
        html = resp.text
        if "captchaImage" in html and "DeleteColony" in html:
            raise ActionError("Abandon colony still requires captcha confirmation", action="abandon_colony")
        return {
            "ok": True,
            "city_id": str(city_id),
            "captcha_solution": solution,
        }
