"""IP -> approximate location using the free DB-IP "City Lite" database (CC BY 4.0).

The database is optional: without it (or for private/unroutable addresses) the
receiver falls back to the country derived from the client-declared timezone.
"""

from __future__ import annotations

import gzip
import ipaddress
import logging
import os
import shutil
import threading
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import maxminddb

logger = logging.getLogger("telemetry.geoip")

GEOIP_DIR = Path(os.environ.get("GEOIP_DIR", "/data/geoip"))
DB_FILE = GEOIP_DIR / "dbip-city-lite.mmdb"
URL_TEMPLATE = "https://download.db-ip.com/free/dbip-city-lite-{year}-{month:02d}.mmdb.gz"
AUTO_UPDATE = os.environ.get("GEOIP_AUTO_UPDATE", "true").lower() in ("1", "true", "yes")
REFRESH_DAYS = 35

_reader: maxminddb.Reader | None = None
_lock = threading.Lock()


@dataclass(frozen=True)
class Geo:
    country: str | None = None
    region: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


def is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _open() -> None:
    global _reader
    with _lock:
        if _reader is not None:
            _reader.close()
            _reader = None
        if DB_FILE.exists():
            _reader = maxminddb.open_database(str(DB_FILE))
            logger.info("geoip database loaded (%s)", DB_FILE)


def _download(target_day: date) -> bool:
    url = URL_TEMPLATE.format(year=target_day.year, month=target_day.month)
    tmp = DB_FILE.with_suffix(".tmp")
    try:
        GEOIP_DIR.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "ikabot-telemetry/0.1 (+https://github.com/rdgweb/ikabot-web)"})
        with urllib.request.urlopen(request, timeout=300) as response, open(tmp, "wb") as raw:
            with gzip.GzipFile(fileobj=response) as unzipped:
                shutil.copyfileobj(unzipped, raw)
        os.replace(tmp, DB_FILE)
        logger.info("geoip database downloaded from %s", url)
        return True
    except Exception as exc:  # noqa: BLE001 - optional feature
        logger.warning("geoip download failed (%s): %s", url, exc)
        tmp.unlink(missing_ok=True)
        return False


def refresh_if_needed(today: date) -> None:
    """Load the local database and (re)download it monthly when auto-update is on."""
    stale = not DB_FILE.exists() or (today - date.fromtimestamp(DB_FILE.stat().st_mtime)).days >= REFRESH_DAYS
    if stale and AUTO_UPDATE:
        prev_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        _download(today) or _download(prev_month)
    _open()


def lookup(ip: str | None) -> Geo:
    if not ip or _reader is None or not is_public(ip):
        return Geo()
    try:
        record = _reader.get(ip) or {}
    except (ValueError, maxminddb.InvalidDatabaseError):
        return Geo()
    location = record.get("location") or {}
    subdivisions = record.get("subdivisions") or [{}]
    lat, lon = location.get("latitude"), location.get("longitude")
    return Geo(
        country=(record.get("country") or {}).get("iso_code"),
        region=((subdivisions[0].get("names") or {}).get("en")),
        city=((record.get("city") or {}).get("names") or {}).get("en"),
        latitude=round(lat, 2) if isinstance(lat, (int, float)) else None,
        longitude=round(lon, 2) if isinstance(lon, (int, float)) else None,
    )
