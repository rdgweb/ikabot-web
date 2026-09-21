"""ikabot telemetry receiver.

Data-handling rules enforced here (and described on /transparencia):
  * the client IP is stored (for approximate location and de-duplication) in its
    own table with a 90-day retention; request headers and access logs are never
    stored (uvicorn runs with --no-access-log and the gateway has access_log off);
  * only the fields declared in ``schemas.py`` are persisted;
  * location comes from the IP (DB-IP City Lite), falling back to the
    client-declared IANA timezone;
  * an installation can erase everything it ever sent (DELETE /v1/installs/{id}).
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
from zoneinfo import available_timezones

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from . import db, geoip, storage
from .schemas import SCHEMA_VERSION, Ping

logger = logging.getLogger("telemetry")

MAX_BODY_BYTES = 32 * 1024
MIN_SECONDS_BETWEEN_PINGS = 60
RETENTION_DAYS = 400
IP_RETENTION_DAYS = 90
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

VALID_TIMEZONES = available_timezones()
_last_ping_by_install: dict[UUID, float] = {}
_last_ping_lock = threading.Lock()


def _today():
    return datetime.now(timezone.utc).date()


def _purge_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            removed = storage.purge_older_than(_today(), RETENTION_DAYS, IP_RETENTION_DAYS)
            logger.info("retention purge removed %s installs", removed)
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("retention purge failed")
        stop.wait(24 * 3600)


def _geoip_loop(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            geoip.refresh_if_needed(_today())
        except Exception:  # noqa: BLE001 - geolocation is optional
            logger.exception("geoip refresh failed")
        stop.wait(24 * 3600)


def client_ip(request: Request) -> str | None:
    """Client address as forwarded by the gateway/reverse proxy (X-Real-IP first)."""
    candidates = [
        request.headers.get("x-real-ip", ""),
        request.headers.get("x-forwarded-for", "").split(",")[0],
        request.client.host if request.client else "",
    ]
    for raw in candidates:
        try:
            return str(ipaddress.ip_address(raw.strip()))
        except ValueError:
            continue
    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db.init_pool()
    stop = threading.Event()
    threading.Thread(target=_geoip_loop, args=(stop,), daemon=True).start()
    threading.Thread(target=_purge_loop, args=(stop,), daemon=True).start()
    yield
    stop.set()
    db.close_pool()


app = FastAPI(title="ikabot telemetry", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def _rate_limited(install_id: UUID) -> bool:
    now = time.monotonic()
    with _last_ping_lock:
        if len(_last_ping_by_install) > 50_000:
            _last_ping_by_install.clear()
        last = _last_ping_by_install.get(install_id)
        if last is not None and now - last < MIN_SECONDS_BETWEEN_PINGS:
            return True
        _last_ping_by_install[install_id] = now
    return False


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"ok": True}


@app.post("/v1/ping", status_code=204)
async def receive_ping(request: Request) -> Response:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        ping = Ping.model_validate_json(body)
    except ValidationError:
        raise HTTPException(status_code=422, detail="invalid payload") from None
    if ping.schema_version != SCHEMA_VERSION:
        raise HTTPException(status_code=422, detail="unsupported schema version")
    if _rate_limited(ping.install_id):
        raise HTTPException(status_code=429, detail="too many requests")

    ip = client_ip(request)
    storage.save_ping(ping, _today(), VALID_TIMEZONES, ip=ip, geo=geoip.lookup(ip))
    return Response(status_code=204)


@app.delete("/v1/installs/{install_id}", status_code=204)
def erase_install(install_id: UUID) -> Response:
    """Erase everything received from one installation (idempotent)."""
    storage.delete_install(install_id)
    _last_ping_by_install.pop(install_id, None)
    return Response(status_code=204)


@app.get("/v1/stats/public")
def public_stats() -> JSONResponse:
    return JSONResponse(storage.public_stats(_today()), headers={"Cache-Control": "public, max-age=300"})


@app.get("/", include_in_schema=False)
@app.get("/transparencia", include_in_schema=False)
def transparency_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "transparencia.html", media_type="text/html; charset=utf-8")
