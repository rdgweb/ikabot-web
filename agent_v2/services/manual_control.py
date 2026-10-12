"""Manual control (N-72): the agent's side.

While the operator plays an account from the hub, the browser's requests arrive
here and are sent to the game with the account's own session: same cookies, same
proxy, same User-Agent the automation uses. Cookies, credentials and the proxy
address never leave the agent; the hub and the browser only get what the game
answered.

Requests come through the broker the agent is already connected to (the hub
cannot reach an agent behind NAT):

    BRPOP ikabot:manual:req:<node_id>     {"id", "op": open|request|close, "session", ...}
    RPUSH ikabot:manual:resp:<id>         {"ok": ..., ...}

The hub only sends them for an account it has taken out of automation, so no job
of that account is running or will start while a session is open here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

REQUEST_KEY = "ikabot:manual:req:{node_id}"
RESPONSE_KEY = "ikabot:manual:resp:{request_id}"
RESPONSE_TTL_SECONDS = 120
# A session the hub never closed (hub restarted, message lost) is dropped after this long unused.
# Longer than the hub's own idle limit, so the hub's close is what normally ends it.
IDLE_SECONDS = 45 * 60
# How long "open" waits for a job of the account that is still finishing in this agent.
OPEN_LOCK_WAIT_SECONDS = 20
GAME_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BODY = 30 * 1024 * 1024
WORKERS = 8

ALLOWED_METHODS = {"GET", "POST", "HEAD"}
# The browser's word is taken only for these; everything else is the session's own.
REQUEST_HEADERS = {"accept", "content-type", "x-requested-with", "if-none-match", "if-modified-since", "range"}
RESPONSE_HEADERS = {
    "content-type", "cache-control", "expires", "etag", "last-modified", "content-disposition",
    "content-range", "accept-ranges", "location",
}


class Busy(Exception):
    """A job of the account is still finishing here; the hub asks again."""


class Refused(Exception):
    """The session cannot be opened; the message is for the operator."""


# How long the agent's own (proxy-less) address is remembered between checks.
DIRECT_IP_TTL_SECONDS = 600


def proxy_fingerprint(host: str, port) -> str:
    """Names a proxy without saying its address: the hub computes the same for the proxies it knows."""
    return hashlib.sha256(f"{str(host).strip().lower()}:{port}".encode()).hexdigest()


class _Entry:
    __slots__ = ("session_id", "account_id", "game_account_id", "client", "origin", "host", "last_used", "lock", "proxy")

    def __init__(self, session_id: str, account_id: str, game_account_id: str):
        self.session_id = session_id
        self.account_id = account_id
        self.game_account_id = game_account_id
        self.client = None
        self.origin = ""
        self.host = ""
        self.last_used = time.monotonic()
        self.lock = threading.Lock()
        self.proxy = {}


def default_client_factory(sessions, hub, proxy_url):
    """Open (or reuse) the account's game session exactly as a job would."""

    def open_client(account_id: str, game_account_id: str, *, allow_cached: bool = True):
        from runners.base import BaseRunner
        from sessions.game_session_service import LoginCooldownActive, VacationHold

        runner = BaseRunner(hub=hub, sessions=sessions, proxy_url=proxy_url())
        creds = runner.resolve_credentials(account_id, {}, game_account_id)
        if not creds:
            raise Refused("O agente nao encontrou as credenciais desta conta na configuracao do no.")
        try:
            return runner.game_sessions.get_or_login_game_client(
                account_id=account_id,
                game_account_id=game_account_id,
                creds=creds,
                log=lambda level, message: logger.info("manual control login [%s]: %s", level, message),
                allow_cached=allow_cached,
            )
        except VacationHold:
            raise Refused(
                "Conta em modo ferias: entrar no jogo tiraria a conta das ferias. "
                "Use Modo Ferias > desativar antes de assumir o controle."
            ) from None
        except LoginCooldownActive as exc:
            raise Refused(
                "O login desta conta esta bloqueado no momento"
                + (f" ({exc.reason[:120]})" if exc.reason else "")
                + "; tente de novo mais tarde."
            ) from None

    def save_client(game_account_id: str, client) -> None:
        from sessions import GameSessionService

        GameSessionService(sessions, hub, proxy_url()).save_game_client(game_account_id, client)

    return open_client, save_client


class ManualControlService(threading.Thread):
    """Answers the hub's manual-control requests for the accounts of this node."""

    def __init__(self, *, node_id: str, sessions, redis_client, open_client, save_client, fetch_ip=None):
        super().__init__(daemon=True, name="manual-control")
        # fetch_ip(proxy_url) -> the address the outside world sees through that proxy ("" = no proxy)
        self.fetch_ip = fetch_ip
        self._direct_ip = ("", 0.0)
        self.node_id = str(node_id)
        self.sessions = sessions
        self.redis = redis_client
        self.open_client = open_client
        self.save_client = save_client
        self._entries: dict[str, _Entry] = {}
        self._entries_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="manual-control")
        self._running = True

    # -- loop ----------------------------------------------------------------------

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        key = REQUEST_KEY.format(node_id=self.node_id)
        logger.info("Manual control service listening")
        while self._running:
            try:
                item = self.redis.brpop(key, timeout=5)
            except Exception as exc:  # noqa: BLE001 - the broker comes back; keep listening
                logger.warning("Manual control: broker read failed (%s); retrying", type(exc).__name__)
                time.sleep(5)
                continue
            try:
                self.drop_idle()
                if item:
                    message = json.loads(item[1])
                    if isinstance(message, dict):
                        self._pool.submit(self.answer, message)
            except Exception:  # noqa: BLE001
                logger.warning("Manual control: could not take a request", exc_info=True)

    def answer(self, message: dict) -> None:
        request_id = str(message.get("id") or "")
        if float(message.get("expires") or 0) and time.time() > float(message["expires"]):
            return  # nobody is waiting for it any more
        try:
            result = self.handle(message)
        except Busy:
            result = {"ok": False, "retry": True, "error": "Um job desta conta ainda esta terminando no agente."}
        except Refused as exc:
            result = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the operator gets the reason, the service keeps running
            logger.warning("Manual control: %s failed: %s", message.get("op"), exc)
            result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if result is None or not request_id:
            return
        try:
            key = RESPONSE_KEY.format(request_id=request_id)
            self.redis.rpush(key, json.dumps(result))
            self.redis.expire(key, RESPONSE_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.warning("Manual control: could not answer request %s", request_id, exc_info=True)

    def handle(self, message: dict) -> dict | None:
        op = message.get("op")
        if op == "open":
            entry = self._entry(message)
            return {"ok": True, "host": entry.host, "proxy": entry.proxy}
        if op == "request":
            return self._request(message)
        if op == "close":
            self._close(str(message.get("session") or ""))
            return None
        return {"ok": False, "error": f"Operacao desconhecida: {op}"}

    # -- sessions ------------------------------------------------------------------

    def _entry(self, message: dict) -> _Entry:
        session_id = str(message.get("session") or "")
        account_id = str(message.get("account_id") or "")
        game_account_id = str(message.get("game_account_id") or "")
        if not session_id or not account_id or not game_account_id:
            raise Refused("Pedido de controle manual incompleto.")
        with self._entries_lock:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = self._entries[session_id] = _Entry(session_id, account_id, game_account_id)
        with entry.lock:
            if entry.client is None:
                self._open(entry)
        entry.last_used = time.monotonic()
        return entry

    def _open(self, entry: _Entry, *, allow_cached: bool = True) -> None:
        # Same lock a job of this account takes: if one is still finishing, wait for it.
        lock = self.sessions.get_lock(entry.game_account_id)
        if not lock.acquire(timeout=OPEN_LOCK_WAIT_SECONDS):
            raise Busy()
        try:
            client = self.open_client(entry.account_id, entry.game_account_id, allow_cached=allow_cached)
        finally:
            lock.release()
        # the client's address is the game's entry page (.../index.php); the browser's
        # paths are relative to the server itself
        server = urlparse(str(getattr(client, "_server_url", "") or ""))
        if not server.scheme or not server.hostname:
            raise Refused("A sessao do jogo abriu sem endereco de servidor.")
        entry.client = client
        entry.origin = f"{server.scheme}://{server.netloc}"
        entry.host = server.hostname
        entry.proxy = self._proxy_facts(client)
        logger.info("Manual control: session %s open for game account %s", entry.session_id, entry.game_account_id)

    def _proxy_facts(self, client) -> dict:
        """Which proxy this session's requests leave through, and whether the exit address confirms it.

        Never the address or the credentials: a fingerprint the hub can match, and the outcome of the check.
        """
        proxy_url = str(getattr(getattr(client, "session", None), "_proxy_url", "") or "").strip()
        if not proxy_url:
            return {"used": False}
        parsed = urlparse(proxy_url if "://" in proxy_url else "http://" + proxy_url)
        host = (parsed.hostname or "").lower()
        facts = {"used": True, "fingerprint": proxy_fingerprint(host, parsed.port or 0), "checked": False}
        if self.fetch_ip is None:
            return facts
        try:
            exit_ip = str(self.fetch_ip(proxy_url) or "").strip()
            direct_ip = self._own_address()
        except Exception:  # noqa: BLE001 - the check is information; it never stops a session
            logger.warning("Manual control: could not check the exit address", exc_info=True)
            return facts
        if exit_ip:
            facts["checked"] = True
            facts["matches_proxy"] = exit_ip == host
            facts["is_direct"] = bool(direct_ip) and exit_ip == direct_ip
        return facts

    def _own_address(self) -> str:
        """The agent's address without any proxy (to notice a proxy that is not being used)."""
        value, at = self._direct_ip
        if value and time.monotonic() - at < DIRECT_IP_TTL_SECONDS:
            return value
        value = str(self.fetch_ip("") or "").strip()
        self._direct_ip = (value, time.monotonic())
        return value

    def _close(self, session_id: str) -> None:
        with self._entries_lock:
            entry = self._entries.pop(session_id, None)
        if entry is None or entry.client is None:
            return
        try:
            # hand the cookies back to the automation as the game left them
            known = getattr(entry.client, "_cookies", None)
            if isinstance(known, dict):
                for cookie in entry.client.session.cookies:
                    if cookie.name in known and entry.host.endswith(str(cookie.domain or "").lstrip(".")):
                        known[cookie.name] = cookie.value
            self.save_client(entry.game_account_id, entry.client)
        except Exception:  # noqa: BLE001 - the automation logs in again if it has to
            logger.warning("Manual control: could not save the session back", exc_info=True)
        logger.info("Manual control: session %s closed", session_id)

    def drop_idle(self) -> None:
        now = time.monotonic()
        with self._entries_lock:
            idle = [key for key, entry in self._entries.items() if now - entry.last_used > IDLE_SECONDS]
        for session_id in idle:
            self._close(session_id)

    # -- requests ------------------------------------------------------------------

    def _request(self, message: dict) -> dict:
        method = str(message.get("method") or "GET").upper()
        target = str(message.get("target") or "/")
        if method not in ALLOWED_METHODS:
            return {"ok": False, "error": f"Metodo nao permitido: {method}"}
        if not target.startswith("/"):
            return {"ok": False, "error": "Endereco invalido."}

        entry = self._entry(message)
        client = entry.client
        response = self._send(entry, method, target, message)
        if self._session_lost(entry, response):
            with entry.lock:
                if entry.client is client:  # a page is many requests: only the first one logs in again
                    logger.info("Manual control: game session of %s expired; logging in again", entry.game_account_id)
                    self.sessions.invalidate_game_session(entry.game_account_id)
                    self._open(entry, allow_cached=False)
            if method != "GET":
                # what was posted belonged to the old session: send the browser back to the game
                return {"ok": True, "status": 303, "headers": {"location": entry.origin + "/"}, "body": "", "origin": entry.origin}
            response = self._send(entry, method, target, message)
        entry.last_used = time.monotonic()

        body = response.content or b""
        if len(body) > MAX_RESPONSE_BODY:
            return {"ok": False, "error": "Resposta do jogo grande demais para esta sessao."}
        headers = {name.lower(): value for name, value in response.headers.items() if name.lower() in RESPONSE_HEADERS}
        return {
            "ok": True,
            "status": response.status_code,
            "headers": headers,
            "body": base64.b64encode(body).decode() if body else "",
            "origin": entry.origin,
        }

    def _send(self, entry: _Entry, method: str, target: str, message: dict):
        headers: dict = {
            name: value for name, value in (message.get("headers") or {}).items()
            if str(name).lower() in REQUEST_HEADERS and isinstance(value, str)
        }
        sent = {name.lower() for name in headers}
        # The session's defaults were made for the automation's ajax calls. A page the
        # operator opens is not one, so these follow what the browser really did.
        if "x-requested-with" not in sent:
            headers["X-Requested-With"] = None
        referer = str(message.get("referer") or "")
        headers["Referer"] = entry.origin + referer if referer.startswith("/") else None
        headers["Origin"] = entry.origin if message.get("origin") else None
        raw = message.get("body") or ""
        return entry.client.session.request(
            method,
            entry.origin + target,
            headers=headers,
            data=base64.b64decode(raw) if raw else None,
            allow_redirects=False,
            timeout=GAME_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _session_lost(entry: _Entry, response) -> bool:
        """The game sends an expired session to the lobby/login instead of answering."""
        if response.status_code not in (301, 302, 303, 307, 308):
            return False
        location = str(response.headers.get("Location") or "")
        if "://" not in location and not location.startswith("//"):
            return "login" in location.lower()
        return (urlparse(location).hostname or "") != entry.host


def start_manual_control(*, sessions, hub, proxy_url) -> ManualControlService | None:
    """Start the service for this node. ``proxy_url`` is a callable: the node proxy can change."""
    try:
        import redis

        from core.config import settings

        client = redis.Redis.from_url(settings.redis_url, socket_keepalive=True, health_check_interval=25)
        from core.heartbeat import _fetch_external_ip

        open_client, save_client = default_client_factory(sessions, hub, proxy_url)
        service = ManualControlService(
            node_id=settings.agent_node_id, sessions=sessions, redis_client=client,
            open_client=open_client, save_client=save_client, fetch_ip=_fetch_external_ip,
        )
        service.start()
        return service
    except Exception:  # noqa: BLE001 - the automation never depends on this
        logger.warning("Manual control service could not start", exc_info=True)
        return None
