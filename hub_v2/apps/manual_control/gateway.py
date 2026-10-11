"""Game session gateway (N-72): the browser's side of a manual-control session.

Served on its own host name per session (``<token>.<gateway domain>``), so the game
keeps living at ``/`` and nothing in its pages has to be rewritten. Every request
for the game is handed to the agent that owns the session and answered with what
the game answered; the browser never sees the game's cookies, the proxy or any
internal address.

Runs as its own process (config.gateway_wsgi) with threads: a request here waits
for the game, and the hub's own workers must not be the ones waiting.
"""

from __future__ import annotations

import base64
import logging
import re

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt

from . import rpc, services
from .models import ManualControlSession

logger = logging.getLogger(__name__)

RESERVED = "/__ikabot/"
COOKIE_NAME = "ikabot_gate"
# How long the browser waits for the game through the agent.
REQUEST_TIMEOUT_SECONDS = 45
MAX_REQUEST_BODY = 8 * 1024 * 1024

# What the browser may tell the game. Never its cookies, its address or its identity:
# the agent's session supplies those, the same ones the automation uses.
FORWARDED_REQUEST_HEADERS = (
    "Accept", "Content-Type", "X-Requested-With", "If-None-Match", "If-Modified-Since", "Range",
)
# What the game may tell the browser. Set-Cookie stays in the agent.
FORWARDED_RESPONSE_HEADERS = (
    "Content-Type", "Cache-Control", "Expires", "ETag", "Last-Modified", "Content-Disposition",
    "Content-Range", "Accept-Ranges",
)
REWRITTEN_TYPES = ("text/html", "javascript", "json", "text/css", "text/plain", "xml")
# What a page of the game may load. Everything of the game itself comes through this
# session (and so through the account's proxy); the static image/script CDN is the one
# outside host allowed. Gameforge's tracking and consent scripts would otherwise run
# from the operator's own address, next to a session that lives on the proxy's.
STATIC_CDN = "https://*.geo.gfsrv.net"
PAGE_POLICY = (
    f"default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: {STATIC_CDN}; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'self'"
)
STATIC_PATH = re.compile(r"\.(css|js|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot|mp3|ogg|wav|map)$", re.IGNORECASE)

PAGE = """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"><meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f4ecda;
font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#3b2f1e}}main{{max-width:30rem;margin:1rem;padding:1.5rem 1.75rem;
background:#fffdf8;border:1px solid #e0d2b4;border-radius:.75rem;box-shadow:0 10px 30px rgba(59,47,30,.12)}}
h1{{font-size:1.05rem;margin:0 0 .5rem}}p{{font-size:.9rem;line-height:1.45;margin:.35rem 0;color:#5f5240}}</style></head>
<body><main><h1>{title}</h1>{body}</main></body></html>"""

# Added to the game's pages: tells the hub the tab is alive, and that it is leaving.
# A page change inside the game also says "leaving"; the next page takes the lease back.
PAGE_SCRIPT = """<script>(function(){if(window.__ikabotControl)return;window.__ikabotControl=1;var u='/__ikabot/';
function beat(){fetch(u+'heartbeat',{method:'POST',credentials:'same-origin',keepalive:true}).then(function(r){
if(r.status===410){location.replace(u+'ended');}}).catch(function(){});}
setInterval(beat,15000);beat();
window.addEventListener('pagehide',function(){try{navigator.sendBeacon(u+'bye');}catch(e){}});
function pill(){if(!document.body||document.getElementById('ikabot-control-pill'))return;var d=document.createElement('div');
d.id='ikabot-control-pill';d.style.cssText='position:fixed;left:8px;bottom:8px;z-index:2147483647;display:flex;gap:8px;align-items:center;'
+'padding:4px 10px;border-radius:999px;background:rgba(40,30,15,.82);color:#fff;font:600 11px system-ui,sans-serif;opacity:.85';
d.appendChild(document.createTextNode('Controle manual \\u00b7 automa\\u00e7\\u00e3o pausada'));var b=document.createElement('button');
b.type='button';b.textContent='Encerrar';b.style.cssText='border:0;border-radius:999px;padding:2px 8px;font:inherit;cursor:pointer;background:#e8d9b5;color:#3b2f1e';
b.onclick=function(){fetch(u+'end',{method:'POST',credentials:'same-origin'}).then(function(){location.replace(u+'ended');});};
d.appendChild(b);document.body.appendChild(d);}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',pill);}else{pill();}})();</script>"""


def _page(title: str, *lines: str, status: int = 200) -> HttpResponse:
    body = "".join(f"<p>{escape(line)}</p>" for line in lines)
    response = HttpResponse(PAGE.format(title=escape(title), body=body), status=status, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _ended_page(status: int = 410) -> HttpResponse:
    return _page(
        "Controle manual encerrado",
        "Esta sessao do jogo foi encerrada e a automacao da conta voltou.",
        "Para jogar de novo por aqui, use Assumir controle no painel da conta.",
        status=status,
    )


def session_token(request) -> str:
    """The session named by the host of this request ("" when it is not a session host)."""
    domain = services.gateway_domain()
    host = request.get_host().split(":", 1)[0].lower()
    if not domain or not host.endswith("." + domain):
        return ""
    label = host[: -len(domain) - 1]
    return label if label and "." not in label else ""


def _secure_cookie() -> bool:
    return str(getattr(settings, "MANUAL_GATEWAY_SCHEME", "") or "https") == "https"


@csrf_exempt
def serve(request, path: str = ""):
    full_path = "/" + path
    if full_path == RESERVED + "health":
        return HttpResponse("ok", content_type="text/plain")

    token = session_token(request)
    session = services.find_by_token(token) if token else None
    if session is None:
        return _page("Sessao nao encontrada", "Este endereco nao corresponde a uma sessao de controle manual.", status=404)

    if full_path.startswith(RESERVED):
        return _reserved(request, session, full_path[len(RESERVED):])

    if session.status != ManualControlSession.ACTIVE:
        return _ended_page()
    if services._is_expired(session):
        services.end(session, "expired")
        return _ended_page()
    if not services.cookie_ok(session, request.COOKIES.get(COOKIE_NAME, "")):
        return _page(
            "Entrada nao autorizada",
            "Esta sessao so abre no navegador que a iniciou. Abra de novo pelo painel da conta (Assumir controle).",
            status=403,
        )
    return _forward(request, session)


def _reserved(request, session: ManualControlSession, name: str):
    if name.startswith("start/") and request.method == "GET":
        cookie = services.consume_start_key(session, name[len("start/"):])
        if cookie is None:
            return _page(
                "Link de entrada invalido",
                "Este link ja foi usado ou a sessao terminou. Abra de novo pelo painel da conta (Assumir controle).",
                status=403,
            )
        response = HttpResponseRedirect("/")
        response.set_cookie(COOKIE_NAME, cookie, httponly=True, secure=_secure_cookie(), samesite="Lax", path="/")
        response["Cache-Control"] = "no-store"
        return response

    if name == "ended":
        return _ended_page(status=200)

    authorized = services.cookie_ok(session, request.COOKIES.get(COOKIE_NAME, ""))
    active = session.status == ManualControlSession.ACTIVE
    if request.method != "POST" or name not in ("heartbeat", "bye", "end"):
        return _page("Nao encontrado", "Endereco desconhecido.", status=404)
    if not active:
        return HttpResponse(status=410)
    if not authorized:
        return HttpResponse(status=403)
    if name == "heartbeat":
        if services._is_expired(session):
            services.end(session, "expired")
            return HttpResponse(status=410)
        services.touch(session)
    elif name == "bye":
        services.touch(session, closing=True)
    else:
        services.end(session, "closed")
    return HttpResponse(status=204)


def _request_target(request) -> str:
    """Path and query exactly as the browser sent them."""
    raw = request.META.get("RAW_URI") or request.get_full_path()
    if not raw.startswith("/"):  # absolute-form request line
        rest = raw.split("://", 1)[-1]
        raw = "/" + rest.split("/", 1)[1] if "/" in rest else "/"
    return raw


def _forward(request, session: ManualControlSession):
    body = request.body
    if len(body) > MAX_REQUEST_BODY:
        return _page("Requisicao grande demais", "O envio excede o limite desta sessao.", status=413)

    origin = services.session_origin(session)
    headers = {name: request.headers[name] for name in FORWARDED_REQUEST_HEADERS if name in request.headers}
    referer = request.headers.get("Referer", "")
    payload = services._agent_payload(session, "request")
    payload.update({
        "method": request.method,
        "target": _request_target(request),
        "headers": headers,
        # the agent turns these into the game's own origin; the public address never reaches the game
        "referer": referer[len(origin):] or "/" if referer.startswith(origin) else "",
        "origin": "Origin" in request.headers,
        "body": base64.b64encode(body).decode() if body else "",
    })

    answer = rpc.call(session.node_id, payload, timeout=REQUEST_TIMEOUT_SECONDS)
    services.touch(session, game_request=True)
    if answer is None:
        return _page(
            "O agente nao respondeu",
            "A requisicao ao jogo passa pelo agente da conta, que nao respondeu a tempo. Tente recarregar.",
            "Se continuar, encerre o controle pelo painel: a automacao so volta quando esta sessao terminar.",
            status=504,
        )
    if not answer.get("ok"):
        logger.warning("Manual session %s: agent error: %s", session.pk, str(answer.get("error"))[:200])
        return _page("Falha ao falar com o jogo", str(answer.get("error") or "Erro no agente.")[:300], status=502)

    if not STATIC_PATH.search(request.path):
        services.count_request(session)
    return _game_response(answer, origin=origin)


def _game_response(answer: dict, *, origin: str) -> HttpResponse:
    game_origin = str(answer.get("origin") or "")
    game_host = game_origin.split("://", 1)[-1]
    policy = ""
    headers = {str(k).lower(): str(v) for k, v in (answer.get("headers") or {}).items()}
    body = base64.b64decode(answer.get("body") or "")
    status = int(answer.get("status") or 502)
    content_type = headers.get("content-type", "")

    location = headers.get("location", "")
    if location:
        if game_origin and location.startswith(game_origin):
            location = location[len(game_origin):] or "/"
        elif game_host and location.startswith("//" + game_host):
            location = location[len(game_host) + 2:] or "/"
        elif "://" in location or location.startswith("//"):
            # the game is sending the browser somewhere else (the lobby): the session is gone
            return _page(
                "A sessao do jogo terminou",
                "O jogo encerrou esta sessao (logout ou expiracao). Encerre o controle e abra de novo pelo painel.",
                status=502,
            )

    if body and game_host and any(kind in content_type.lower() for kind in REWRITTEN_TYPES):
        body = _rewrite(body, game_host=game_host, origin=origin)
        # only whole pages: the game answers its ajax calls as text/html too
        whole_page = body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))
        if status == 200 and "text/html" in content_type.lower() and whole_page:
            policy = PAGE_POLICY
            marker = body.rfind(b"</body>")
            if marker != -1:
                body = body[:marker] + PAGE_SCRIPT.encode() + body[marker:]

    response = HttpResponse(body, status=status)
    del response["Content-Type"]
    for name in FORWARDED_RESPONSE_HEADERS:
        value = headers.get(name.lower())
        if value:
            response[name] = value
    if location:
        response["Location"] = location
    if policy:
        response["Content-Security-Policy"] = policy
    response["X-Robots-Tag"] = "noindex, nofollow"
    # other sites the page loads from (the game's CDN) do not learn the session's address
    response["Referrer-Policy"] = "same-origin"
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


def _rewrite(body: bytes, *, game_host: str, origin: str) -> bytes:
    """Absolute addresses of the game inside a page become addresses of this session."""
    host = game_host.encode()
    ours = origin.encode()
    our_host = origin.split("://", 1)[-1].encode()
    escaped = ours.replace(b"/", b"\\/")
    for scheme in (b"https:", b"http:"):
        body = body.replace(scheme + b"//" + host, ours)
        body = body.replace(scheme + b"\\/\\/" + host, escaped)
    return body.replace(b"//" + host, b"//" + our_host)
