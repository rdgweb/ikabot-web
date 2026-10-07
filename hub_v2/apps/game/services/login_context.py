"""Regional context of the game login (N-74): browser locale and timezone.

The login presents itself in three places that must tell the same story: the
`Accept-Language` header and the `locale` / `gfLang` fields the agent sends to the
lobby, and the Playwright context in which ikabotapi generates the blackbox token.

Two system settings hold it (`login_locale`, `login_timezone_id`). Both empty is the
compatibility default: the agent keeps sending exactly what it always sent (en-GB,
"en-US,en;q=0.5") and the token is generated with ikabotapi's own defaults (en-GB,
Europe/London). Nothing changes until someone fills them in.
"""

from __future__ import annotations

import re
from zoneinfo import available_timezones

LOCALE_SETTING = "login_locale"
TIMEZONE_SETTING = "login_timezone_id"

# same shapes ikabotapi accepts (apps/token/routes.py, revision fb87efee)
LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
TIMEZONE_PATTERN = re.compile(r"^(UTC|[A-Za-z_]+(/[A-Za-z0-9_+\-]+)+)$")

# what the agent has always sent, and what ikabotapi uses when nothing is asked
LEGACY_LOCALE = "en-GB"
LEGACY_GF_LANG = "en"
LEGACY_ACCEPT_LANGUAGE = "en-US,en;q=0.5"


class InvalidLoginContext(ValueError):
    """A locale or timezone that is not one. The message is meant for the operator."""


def clean_locale(value) -> str:
    """"pt-br" -> "pt-BR"; "" stays "" (not configured). Raises InvalidLoginContext."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not LOCALE_PATTERN.fullmatch(text):
        raise InvalidLoginContext(f"Idioma invalido: {text!r}. Use o formato do navegador, como pt-BR ou en-GB.")
    language, *rest = text.split("-")
    return "-".join([language.lower(), *[part.upper() if len(part) == 2 else part for part in rest]])


def clean_timezone(value) -> str:
    """A timezone that exists ("America/Sao_Paulo"); "" stays "". Raises InvalidLoginContext."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not TIMEZONE_PATTERN.fullmatch(text) or text not in available_timezones():
        raise InvalidLoginContext(f"Fuso horario invalido: {text!r}. Use um nome IANA, como America/Sao_Paulo ou Europe/London.")
    return text


def accept_language(locale: str, gf_lang: str) -> str:
    """The header a browser set to `locale` sends: "pt-BR,pt;q=0.9,en;q=0.8"."""
    parts = [locale]
    if gf_lang != locale:
        parts.append(f"{gf_lang};q=0.9")
    if gf_lang != "en":
        parts.append("en;q=0.8")
    return ",".join(parts)


def login_context() -> dict:
    """What every login should present, from the system settings.

    {"locale_configured", "locale", "gf_lang", "accept_language", "timezone_id"}

    `locale_configured` False means "send what was always sent": the agent keeps its
    historical headers and asks for the token without a locale. `timezone_id` is ""
    when none was chosen (ikabotapi then uses its own default).

    A stored value that is no longer valid is ignored (the login falls back to the
    compatibility default) rather than breaking every login.
    """
    from apps.settings_app.utils import get_setting

    try:
        locale = clean_locale(get_setting(LOCALE_SETTING, ""))
    except InvalidLoginContext:
        locale = ""
    try:
        timezone_id = clean_timezone(get_setting(TIMEZONE_SETTING, ""))
    except InvalidLoginContext:
        timezone_id = ""
    if not locale:
        return {
            "locale_configured": False, "locale": LEGACY_LOCALE, "gf_lang": LEGACY_GF_LANG,
            "accept_language": LEGACY_ACCEPT_LANGUAGE, "timezone_id": timezone_id,
        }
    gf_lang = locale.split("-")[0]
    return {
        "locale_configured": True, "locale": locale, "gf_lang": gf_lang,
        "accept_language": accept_language(locale, gf_lang), "timezone_id": timezone_id,
    }
