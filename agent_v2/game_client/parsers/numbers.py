"""Single source of truth for parsing numbers out of Ikariam JSON/HTML.

Previously duplicated (and drifting) across runners/check_status.py,
services/resource_transport.py and services/wine_tavern.py — see N-40. All three
now import from here instead of keeping their own copy.
"""

from __future__ import annotations

import re

# Unicode thousands separators Ikariam/browsers use besides the plain space:
#   U+00A0 NO-BREAK SPACE, U+202F NARROW NO-BREAK SPACE (French locale, e.g. "1 234").
# Stripped before the numeric token is extracted, so "1 234" reads as 1234 instead
# of silently truncating to 1 at the first space.
_THOUSAND_SPACES = re.compile(r"[\s  ]")
_NUMERIC_TOKEN = re.compile(r"-?[\d.,]+")


def _normalize_token(raw: str) -> str | None:
    """Extract the numeric token and resolve , vs . as decimal/thousands separator.

    Handles plain ints/floats and localized strings such as `1.139`, `1,139`,
    `1.139,0`, `1 234` and escaped HTML fragments. Returns None if no digits found.
    """
    stripped = _THOUSAND_SPACES.sub("", raw)
    match = _NUMERIC_TOKEN.search(stripped)
    if not match:
        return None
    token = match.group(0)

    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        parts = token.split(",")
        if len(parts[-1]) == 3 and all(p.isdigit() for p in parts):
            token = "".join(parts)
        else:
            token = token.replace(",", ".")
    elif "." in token:
        parts = token.split(".")
        if len(parts[-1]) == 3 and all(p.isdigit() for p in parts):
            token = "".join(parts)

    return token


def parse_game_float(val, default: float = 0.0) -> float:
    """Parse a number from game JSON/HTML into a float. Never raises."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)

    raw = str(val).strip()
    if not raw:
        return default

    token = _normalize_token(raw)
    if token is None:
        return default
    try:
        return float(token)
    except (ValueError, TypeError):
        return default


def parse_game_int(val, default: int = 0) -> int:
    """Parse a number from game JSON/HTML into an int (truncates any decimals).

    Never raises — returns ``default`` for anything unparseable.
    """
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return int(float(val))

    raw = str(val).strip()
    if not raw:
        return default

    token = _normalize_token(raw)
    if token is None:
        return default
    try:
        return int(float(token))
    except (ValueError, TypeError):
        return default
