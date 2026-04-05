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

    def __init__(self, session: requests.Session, hub: HubClient, user_agent: str):
        self.session = session
        self.hub = hub
        self.user_agent = user_agent

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
            "Accept-Language": "en-US,en;q=0.5",
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
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://lobby.ikariam.gameforge.com/es_AR/hub",
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
            "accept-language": "en-US,en;q=0.9",
            "authorization": f"Bearer {lobby_token}",
            "content-type": "application/json",
            "origin": "https://lobby.ikariam.gameforge.com",
            "referer": "https://lobby.ikariam.gameforge.com/en_GB/accounts",
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
            "Accept-Language": "en-US,en;q=0.5",
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

    def _get_blackbox(self) -> str:
        """Get blackbox token from hub (ikabotapi). Non-critical."""
        try:
            token = self.hub.get_blackbox_token(self.user_agent)
            logger.info("Blackbox token obtained (%d chars)", len(token))
            return token
        except Exception as e:
            logger.warning("Blackbox token failed: %s", e)
            return ""

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
            "Accept-Language": "en-US,en;q=0.5",
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
            "Accept-Language": "en-US,en;q=0.5",
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
            "Accept-Language": "en-US,en;q=0.5",
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
                "Accept-Language": "en-US,en;q=0.5",
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
                    "product": "ikariam", "server_id": "1", "language": "en",
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
                    "product": "ikariam", "server_id": "1", "language": "en",
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
            "Accept-Language": "en-US,en;q=0.5",
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
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://lobby.ikariam.gameforge.com",
            "Referer": "https://lobby.ikariam.gameforge.com/",
            "TNT-Installation-Id": "",
        })

        payload = {
            "identity": email,
            "password": password,
            "locale": "en-GB",
            "gfLang": "en",
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
