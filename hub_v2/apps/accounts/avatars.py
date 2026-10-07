"""Imagem de cada conta do jogo (N-88).

O valor gravado em GameAccount.avatar e uma de tres coisas:
  ""                         sem imagem: mostra as iniciais
  "game/units/hoplita.png"   uma arte do jogo que ja vem no hub (galeria)
  "data:image/jpeg;base64,…" uma imagem enviada pelo usuario, ja reduzida no navegador

Imagens enviadas ficam no banco (pequenas) e nao em disco: o container do hub e
recriado a cada deploy e nao tem volume de midia.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from functools import lru_cache

from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import reverse

# pastas de arte do jogo oferecidas na galeria, na ordem em que aparecem
GALLERY_GROUPS = (
    ("game/units", "Unidades"),
    ("game/gods", "Deuses"),
    ("game/pirate", "Piratas"),
    ("game/buildings", "Edificios"),
)
MAX_UPLOAD_CHARS = 90_000          # data URI de uma imagem ~128px; o navegador reduz antes de enviar
_DATA_URI = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$")
_MAGIC = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff", "webp": b"RIFF"}


@lru_cache(maxsize=1)
def gallery() -> tuple[dict, ...]:
    """Artes do jogo disponiveis: ({"path", "group", "label"}, ...)."""
    items: list[dict] = []
    for folder, group in GALLERY_GROUPS:
        directory = finders.find(folder)
        if not directory or not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory), key=str.lower):
            if name.lower().endswith(".png"):
                label = os.path.splitext(name)[0].replace("_", " ")
                items.append({"path": f"{folder}/{name}", "group": group, "label": label})
    return tuple(items)


def gallery_for_ui() -> list[dict]:
    """Galeria agrupada, com a URL de cada arte: [{"group", "items": [{"path", "url", "label"}]}]."""
    groups: dict[str, list] = {}
    for item in gallery():
        groups.setdefault(item["group"], []).append({"path": item["path"], "url": static(item["path"]), "label": item["label"]})
    return [{"group": group, "items": items} for group, items in groups.items()]


def clean_avatar(value) -> str:
    """Valor valido para gravar, ou ValueError dizendo o que ha de errado."""
    value = str(value or "").strip()
    if not value:
        return ""
    if value.startswith("data:"):
        if len(value) > MAX_UPLOAD_CHARS:
            raise ValueError("Imagem grande demais. Use uma imagem menor.")
        match = _DATA_URI.match(value)
        if not match:
            raise ValueError("Formato de imagem nao aceito. Use PNG, JPG ou WEBP.")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except Exception as exc:
            raise ValueError("Imagem corrompida.") from exc
        if not raw.startswith(_MAGIC[match.group(1)]):
            raise ValueError("O arquivo enviado nao e uma imagem valida.")
        return value
    if value in {item["path"] for item in gallery()}:
        return value
    raise ValueError("Imagem desconhecida.")


def avatar_bytes(value: str) -> tuple[str, bytes] | None:
    """(content type, bytes) de uma imagem enviada; None para os outros casos."""
    match = _DATA_URI.match(str(value or ""))
    if not match:
        return None
    try:
        return f"image/{match.group(1)}", base64.b64decode(match.group(2))
    except Exception:
        return None


def avatar_url(game_account) -> str:
    """URL da imagem da conta, ou "" quando ela nao tem (mostrar as iniciais)."""
    value = str(getattr(game_account, "avatar", "") or "")
    if not value:
        return ""
    if value.startswith("data:"):
        # a versao no endereco deixa o navegador guardar a imagem ate ela mudar
        version = hashlib.sha1(value.encode("ascii", "ignore")).hexdigest()[:10]
        return f"{reverse('accounts:game-account-avatar', args=[game_account.pk])}?v={version}"
    return static(value)


def initials(name) -> str:
    """Ate duas letras para o lugar da imagem: "Lord Darkness" -> "LD", "HAVIT" -> "HA"."""
    text = str(name or "")
    # so letras contam ("BlackShadow701" -> "BL"); um nome so de numeros usa os numeros
    words = re.findall(r"[^\W\d_]+", text, re.UNICODE) or re.findall(r"\d+", text)
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()
