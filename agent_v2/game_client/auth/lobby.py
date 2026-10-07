"""
Unified Gameforge lobby authentication.

Consolidates the duplicated auth flows from check_status and discover_characters
into a single, reusable module. All lobby-related HTTP goes through here.

Flow:
1. Try existing gf-token-production cookie (skip full login)
2. Fetch configuration.js for platformGameId + gameEnvironmentId
3. Get blackbox token from hub (ikabotapi)
4. Prepare cookies: connect.js → gameforge.com/config → pixelzirkus
5. OPTIONS preflight
6. POST credentials to spark-web → bearer token
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import TYPE_CHECKING

import requests

from game_client.constants import (
    LOBBY_ACCOUNTS_URL,
    LOBBY_CONFIG_URL,
    LOBBY_LOGIN_URL,
)
from game_client.exceptions import LoginError

if TYPE_CHECKING:
    from core.hub_client import HubClient

logger = logging.getLogger(__name__)


# What this module always sent, for a hub without a regional context (N-74).
_LEGACY_CONTEXT = {
    "locale_configured": False,
    "locale": "en-GB",
    "gf_lang": "en",
    "accept_language": "en-US,en;q=0.5",
    "timezone_id": "",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _gen_rand() -> str:
    """Generate random 4-char hex string (matches ikabot __genRand)."""
    return "".join(random.choices("0123456789abcdef", k=4))


def _fp_eval_id() -> str:
    """Generate fingerprint eval ID (matches ikabot __fp_eval_id)."""
    return (
        _gen_rand() + _gen_rand() + "-"
        + _gen_rand() + "-"
        + _gen_rand() + "-"
        + _gen_rand() + "-"
        + _gen_rand() + _gen_rand() + _gen_rand()
    )


# ── LobbyAuthenticator ──────────────────────────────────────────────────────

class LobbyAuthenticator:
    """Handles all Gameforge lobby authentication.

    Usage::

        from core.proxy import StrictProxySession

        session = StrictProxySession(proxy_url)
        auth = LobbyAuthenticator(session, hub, user_agent)
        token = auth.authenticate(email, password, existing_token="...")
        accounts = auth.fetch_accounts(token)
        login_url = auth.get_login_link(token, account)
    """

    def __init__(self, session: requests.Session, hub: HubClient, user_agent: str, context: dict | None = None):
        self.session = session
        self.hub = hub
        self.user_agent = user_agent
        self._context = dict(context) if context else None

    # ── Regional context (N-74) ──────────────────────────────────────────

    @property
    def context(self) -> dict:
        """Locale, language and timezone this login presents, the same everywhere.

        Comes from the hub (system settings). Not configured there means "what was
        always sent": every header and field below keeps its historical value.
        """
        if self._context is None:
            fetch = getattr(self.hub, "get_login_context", None)
            try:
                loaded = fetch() if callable(fetch) else None
            except Exception:  # noqa: BLE001 - never a reason to fail a login
                loaded = None
            self._context = dict(loaded) if isinstance(loaded, dict) and loaded.get("locale") else dict(_LEGACY_CONTEXT)
        return self._context

    def _accept_language(self, legacy: str = "en-US,en;q=0.5") -> str:
        """`legacy` is what this request always sent; it only changes once a locale is configured."""
        context = self.context
        return str(context.get("accept_language") or legacy) if context.get("locale_configured") else legacy

    def _lobby_page(self, page: str, legacy_locale: str) -> str:
        """Lobby URL a browser in this locale would be on: https://lobby.../pt_BR/hub."""
        context = self.context
        locale = str(context.get("locale") or "").replace("-", "_") if context.get("locale_configured") else legacy_locale
        return f"https://lobby.ikariam.gameforge.com/{locale}/{page}"

    # ── Public API ───────────────────────────────────────────────────────

    def authenticate(self, email: str, password: str, existing_token: str = "") -> str:
        """Full lobby authentication. Returns bearer token.

        Tries existing token first (fast path). Falls back to full login flow.

        Raises:
            LoginError: On authentication failure (bad credentials, 2FA, captcha).
        """
        # Fast path: validate existing token
        if existing_token:
            if self.validate_token(existing_token):
                logger.info("Existing lobby token valid, skipping full login")
                return existing_token
            logger.info("Existing lobby token invalid, doing full login")
            self.session.cookies.clear()

        # Step 1: Get game IDs from configuration.js
        game_environment_id, platform_game_id = self._fetch_game_ids()

        # Step 2: Get blackbox token from hub (ikabotapi)
        blackbox = self._get_blackbox()

        # Step 3-5: Prepare cookies (connect.js, config, pixelzirkus, OPTIONS)
        self._prepare_cookies()

        # Step 6: POST credentials
        token = self._post_credentials(
            email, password, platform_game_id, game_environment_id, blackbox,
        )

        return token

    def validate_token(self, token: str) -> bool:
        """Check if a lobby bearer token is still valid.

        Makes a lightweight GET to /api/users/me.
        """
        cookie_obj = requests.cookies.create_cookie(
            domain=".gameforge.com",
            name="gf-token-production",
            value=token,
        )
        self.session.cookies.set_cookie(cookie_obj)

        self._set_headers({
            "Host": "lobby.ikariam.gameforge.com",
            "Accept": "*/*",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "close",
            "Referer": "https://lobby.ikariam.gameforge.com/",
            "Authorization": f"Bearer {token}",
        })

        try:
            resp = self.session.get(
                "https://lobby.ikariam.gameforge.com/api/users/me",
                timeout=15,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def fetch_accounts(self, lobby_token: str) -> list[dict]:
        """GET /api/users/me/accounts — returns raw account list.

        The API returns either a list or a dict of account objects.
        Each contains: id, server, name, blocked, accountGroup, etc.
        """
        self._set_headers({
            "Host": "lobby.ikariam.gameforge.com",
            "Accept": "application/json",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate",
            "Referer": self._lobby_page("hub", "es_AR"),
            "Authorization": f"Bearer {lobby_token}",
            "DNT": "1",
            "Connection": "close",
        })

        resp = self.session.get(LOBBY_ACCOUNTS_URL, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_login_link(self, lobby_token: str, account: dict) -> str:
        """POST /api/users/me/loginLink — returns redirect URL to game server.

        The returned URL contains game session cookies when followed.

        Args:
            lobby_token: Bearer token from authenticate().
            account: Account dict from fetch_accounts() with 'id' and 'server'.

        Returns:
            Login redirect URL (e.g. https://s61-br.ikariam.gameforge.com/index.php?...).

        Raises:
            LoginError: If loginLink request fails or returns invalid URL.
        """
        # Fresh blackbox for server login
        blackbox = self._get_blackbox()

        self._set_headers({
            "authority": "lobby.ikariam.gameforge.com",
            "method": "POST",
            "path": "/api/users/me/loginLink",
            "scheme": "https",
            "accept": "application/json",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": self._accept_language("en-US,en;q=0.9"),
            "authorization": f"Bearer {lobby_token}",
            "content-type": "application/json",
            "origin": "https://lobby.ikariam.gameforge.com",
            "referer": self._lobby_page("accounts", "en_GB"),
        })

        data = {
            "server": {
                "language": account["server"]["language"],
                "number": account["server"]["number"],
            },
            "clickedButton": "account_list",
            "id": account["id"],
            "blackbox": blackbox,
        }

        resp = self.session.post(
            "https://lobby.ikariam.gameforge.com/api/users/me/loginLink",
            json=data,
            timeout=30,
        )

        resp_json = resp.json()
        if "url" not in resp_json:
            raise LoginError(
                f"loginLink falhou: status={resp.status_code} body={resp.text[:300]}"
            )

        login_url = resp_json["url"]
        logger.info("loginLink URL obtained: %s", login_url[:80])

        # Validate it's a real game server URL
        if not re.search(r"https://s\d+-\w+\.ikariam\.gameforge\.com/index\.php\?", login_url):
            raise LoginError(f"URL inválida do loginLink: {login_url[:100]}")

        return login_url

    def follow_login_link(
        self, login_url: str, server_lang: str, server_number: str,
    ) -> str:
        """Follow the loginLink URL to establish game session cookies.

        Args:
            login_url: URL from get_login_link().
            server_lang: Server language code (e.g. "br").
            server_number: Server number string (e.g. "61").

        Returns:
            HTML of the game page.

        Raises:
            LoginError: If session is immediately invalid (vacation, expired).
        """
        host = f"s{server_number}-{server_lang}.ikariam.gameforge.com"

        self._set_headers({
            "Host": host,
            "Accept": "*/*",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": f"https://{host}",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": f"https://{host}",
            "DNT": "1",
            "Connection": "keep-alive",
        })

        html = self.session.get(login_url, timeout=30).text

        if "nologin_umod" in html:
            raise LoginError("Conta em modo férias")

        if "index.php?logout" in html or '<a class="logout"' in html:
            raise LoginError("Sessão expirou imediatamente após loginLink")

        logger.info("Server login successful, game cookies obtained")
        return html

    # ── Private helpers ──────────────────────────────────────────────────

    def _set_headers(self, headers: dict) -> None:
        """Clear and set session headers, always including User-Agent."""
        self.session.headers.clear()
        headers.setdefault("User-Agent", self.user_agent)
        self.session.headers.update(headers)

    _BLACKBOX_MAX_ATTEMPTS = 3
    _BLACKBOX_MAX_TIMEOUTS = 2       # a token that did not come within the budget gets one more try
    _BLACKBOX_RETRY_SECONDS = 3      # wait 3s, then 6s: short and bounded

    def _get_blackbox(self) -> str:
        """Get blackbox token from hub (ikabotapi), retrying a few times first.

        N-39: this used to swallow any failure and return "", letting login continue
        without a blackbox token — Gameforge then either rejects it or demands a
        captcha, and the resulting error told the user nothing about the real cause.
        Now it retries a few times (ikabotapi hiccups are often transient) and, if
        still failing, raises LoginError with an explicit cause instead of limping
        into a login attempt that's effectively already doomed.

        N-73: the two ways of failing are told apart. "Could not reach it" fails fast
        and is retried with a short growing pause. "Not ready within the time budget"
        already cost a whole budget, so it is tried once more and no further; the hub
        answers before the agent stops waiting, so a retry never overlaps a generation
        still running.
        """
        last_exc: Exception | None = None
        timeouts = 0
        for attempt in range(1, self._BLACKBOX_MAX_ATTEMPTS + 1):
            try:
                token = self._request_blackbox()
                if not token:
                    raise LoginError("ikabotapi retornou um blackbox vazio")
                logger.info(
                    "Blackbox token obtained (%d chars, tentativa %d/%d)",
                    len(token), attempt, self._BLACKBOX_MAX_ATTEMPTS,
                )
                return token
            except Exception as e:  # noqa: BLE001 - every failure mode ends in a retry or a LoginError
                last_exc = e
                if getattr(e, "is_final", False):
                    # the request itself was refused (invalid locale/timezone): trying again changes nothing
                    raise LoginError(f"Blackbox recusado pelo hub: {e}") from None
                timed_out = bool(getattr(e, "is_timeout", False))
                timeouts += int(timed_out)
                logger.warning(
                    "Blackbox token failed (tentativa %d/%d, %s): %s",
                    attempt, self._BLACKBOX_MAX_ATTEMPTS, "tempo esgotado" if timed_out else "indisponivel", e,
                )
                if timeouts >= self._BLACKBOX_MAX_TIMEOUTS:
                    raise LoginError(
                        f"Blackbox nao ficou pronto dentro do limite de tempo ({timeouts} tentativas) — "
                        f"o ikabotapi esta lento ou travado. Ultimo erro: {e}"
                    ) from None
                if attempt < self._BLACKBOX_MAX_ATTEMPTS:
                    time.sleep(self._BLACKBOX_RETRY_SECONDS * attempt)

        raise LoginError(
            f"Blackbox indisponivel apos {self._BLACKBOX_MAX_ATTEMPTS} tentativas — "
            f"verifique o container ikabotapi. Ultimo erro: {last_exc}"
        )

    def _request_blackbox(self) -> str:
        """Ask for the token in the same locale and timezone this login presents (N-74)."""
        context = self.context
        locale = str(context.get("locale") or "") if context.get("locale_configured") else ""
        timezone_id = str(context.get("timezone_id") or "")
        if locale or timezone_id:
            return self.hub.get_blackbox_token(self.user_agent, locale=locale, timezone_id=timezone_id)
        return self.hub.get_blackbox_token(self.user_agent)

    def _fetch_game_ids(self) -> tuple[str, str]:
        """Fetch gameEnvironmentId and platformGameId from configuration.js.

        Returns:
            Tuple of (game_environment_id, platform_game_id).

        Raises:
            LoginError: If IDs cannot be extracted.
        """
        self._set_headers({
            "Host": "lobby.ikariam.gameforge.com",
            "Accept": "*/*",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "close",
            "Referer": "https://lobby.ikariam.gameforge.com/",
        })

        try:
            resp = self.session.get(LOBBY_CONFIG_URL, timeout=15)
            js = resp.text
        except requests.RequestException as e:
            raise LoginError(f"Falha ao buscar configuração do lobby: {e}") from e

        m_env = re.search(r'"gameEnvironmentId":"(.*?)"', js)
        m_plat = re.search(r'"platformGameId":"(.*?)"', js)
        if not m_env or not m_plat:
            raise LoginError("Não foi possível extrair gameEnvironmentId/platformGameId")

        return m_env.group(1), m_plat.group(1)

    def _prepare_cookies(self) -> None:
        """Set up session cookies via connect.js, config, pixelzirkus, and OPTIONS."""
        # connect.js
        self._set_headers({
            "Accept": "*/*",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "close",
            "Referer": "https://lobby.ikariam.gameforge.com/",
        })

        try:
            resp = self.session.get("https://gameforge.com/js/connect.js", timeout=15)
            if "Attention Required" in resp.text:
                raise LoginError("Captcha detectado em connect.js")
        except requests.RequestException as e:
            logger.warning("connect.js failed (non-critical): %s", e)

        # gameforge.com/config
        self._set_headers({
            "Accept": "*/*",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://lobby.ikariam.gameforge.com/",
            "Origin": "https://lobby.ikariam.gameforge.com",
            "DNT": "1",
            "Connection": "close",
        })

        try:
            self.session.get("https://gameforge.com/config", timeout=15)
        except requests.RequestException as e:
            logger.warning("gameforge.com/config failed (non-critical): %s", e)

        # Pixelzirkus tracking (non-critical)
        try:
            fid1 = _fp_eval_id()
            fid2 = _fp_eval_id()

            self._set_headers({
                "Host": "pixelzirkus.gameforge.com",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": self._accept_language(),
                "Accept-Encoding": "gzip, deflate",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://lobby.ikariam.gameforge.com",
                "DNT": "1",
                "Connection": "close",
                "Referer": "https://lobby.ikariam.gameforge.com/",
                "Upgrade-Insecure-Requests": "1",
            })

            self.session.post(
                "https://pixelzirkus.gameforge.com/do/simple",
                data={
                    "product": "ikariam", "server_id": "1", "language": self.context.get("gf_lang") or "en",
                    "location": "VISIT", "replacement_kid": "",
                    "fp_eval_id": fid1,
                    "page": "https%3A%2F%2Flobby.ikariam.gameforge.com%2F",
                    "referrer": "", "fingerprint": "2175408712",
                    "fp_exec_time": "1.00",
                },
                timeout=10,
            )
            self.session.post(
                "https://pixelzirkus.gameforge.com/do/simple",
                data={
                    "product": "ikariam", "server_id": "1", "language": self.context.get("gf_lang") or "en",
                    "location": "fp_eval", "fp_eval_id": fid2,
                    "fingerprint": "2175408712", "fp2_config_id": "1",
                    "page": "https%3A%2F%2Flobby.ikariam.gameforge.com%2F",
                    "referrer": "",
                    "fp2_value": "921af958be7cf2f76db1e448c8a5d89d",
                    "fp2_exec_time": "96.00",
                },
                timeout=10,
            )
        except Exception as e:
            logger.debug("Pixelzirkus tracking failed (non-critical): %s", e)

        # OPTIONS preflight (as ikabot does)
        self._set_headers({
            "Accept": "*/*",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate, br",
            "Access-Control-Request-Headers": "content-type,tnt-installation-id",
            "Access-Control-Request-Method": "POST",
            "Origin": "https://lobby.ikariam.gameforge.com",
            "Referer": "https://lobby.ikariam.gameforge.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-site",
            "TE": "trailers",
        })

        try:
            self.session.options(
                "https://gameforge.com/api/v1/auth/thin/sessions", timeout=10,
            )
        except Exception:
            pass

    def _post_credentials(
        self,
        email: str,
        password: str,
        platform_game_id: str,
        game_environment_id: str,
        blackbox: str,
    ) -> str:
        """POST credentials to spark-web and return the bearer token.

        Raises:
            LoginError: On auth failure.
        """
        self._set_headers({
            "Accept": "*/*",
            "Accept-Language": self._accept_language(),
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://lobby.ikariam.gameforge.com",
            "Referer": "https://lobby.ikariam.gameforge.com/",
            "TNT-Installation-Id": "",
        })

        payload = {
            "identity": email,
            "password": password,
            "locale": self.context.get("locale") or "en-GB",
            "gfLang": self.context.get("gf_lang") or "en",
            "gameId": platform_game_id,
            "gameEnvironmentId": game_environment_id,
            "blackbox": blackbox,
        }

        try:
            resp = self.session.post(LOBBY_LOGIN_URL, json=payload, timeout=30)
        except requests.RequestException as e:
            raise LoginError(f"Falha ao conectar ao lobby: {e}") from e

        if resp.status_code == 403:
            raise LoginError(
                "Login rejeitado — credenciais inválidas ou conta bloqueada"
            )
        if resp.status_code == 409:
            if "OTP_REQUIRED" in resp.text:
                raise LoginError("2FA necessário — não suportado ainda pelo agent")
            if "gf-challenge-id" in resp.headers:
                raise LoginError(
                    "Captcha necessário no lobby — tente novamente mais tarde"
                )
            raise LoginError(f"Conflito no login: {resp.text[:200]}")

        if "token" not in resp.text:
            raise LoginError(
                f"Login falhou com status {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        token = data.get("token")
        if not token:
            raise LoginError("Resposta do lobby não contém token")

        # Set cookie for subsequent requests
        cookie_obj = requests.cookies.create_cookie(
            domain=".gameforge.com",
            name="gf-token-production",
            value=token,
        )
        self.session.cookies.set_cookie(cookie_obj)

        return token
