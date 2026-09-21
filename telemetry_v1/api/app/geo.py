"""Timezone -> country lookup using only the IANA tz database (no IP, no GeoIP)."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def _zone_countries() -> dict[str, str]:
    mapping: dict[str, str] = {}
    text = (resources.files("tzdata.zoneinfo") / "zone.tab").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            mapping[parts[2]] = parts[0]
    return mapping


def country_for_timezone(timezone: str) -> str | None:
    """Return the ISO 3166-1 alpha-2 code for an IANA timezone, if it has one."""
    return _zone_countries().get(timezone)
