"""The battlefield of a combat report as the game shows it (N-109).

The picture is put together from the game's own pieces, kept in static/game/combat: the
background of the field (with the two reserve panels), the sheet of unit tiles, the hit
point bar and the empty slot. Each piece goes where the game's page puts it; the numbers
come from what report_parser reads. Above and below the field go the names of the two
sides, and under the last round what each unit type had left and lost, who won and what
was taken.
"""
from __future__ import annotations

import io
import unicodedata
from functools import lru_cache
from pathlib import Path

from .report_parser import RESOURCE_LABELS, unit_icon, unit_name

SCALE = 1.39            # the game draws 36px tiles; enlarged so that the numbers read on a phone
FIELD = (905, 397)      # the background, in the game's pixels; every position below is in them
PAD, HEAD, FOOT = 8, 52, 28
TILE, PITCH = 36, 46
BAR = (5, 32)

PAPER = (253, 247, 221)
INK = (97, 45, 4)
MUTED = (150, 110, 60)
NUMBER_BOX = (246, 235, 189)
LOSS = (176, 36, 28)
WIN = (48, 120, 48)
LINE = (222, 196, 140)
AMMO, AMMO_USED = (88, 183, 224), (70, 84, 96)
SIDE = {"attacker": (38, 86, 138), "defender": (160, 48, 38)}
WHITE = (255, 255, 255)

BACKGROUNDS = {"sea": "battlefield_sea_A_D.jpg", "land": "battlefield_land_A_D.jpg", "city": "battlefield_city_A_D.jpg"}
# where the game puts each zone: (how the slots are laid, x, y of the attacker, y of the defender)
ZONES = {
    "air": ("left", 157, 27, 335),
    "airfighter": ("right", 855, 27, 335),
    "artillery": ("center", 501, 27, 335),
    "longRange": ("center", 491, 82, 277),
    "main": ("center", 491, 135, 222),
    "flankLeft": ("left", 170, 135, 222),
    "flankRight": ("left", 775, 135, 222),
}
RESERVE = {"attacker": (4, 5), "defender": (4, 203)}        # the two panels painted on the background
RESERVE_COLUMNS, RESERVE_ROWS = 3, 3
# the unit tiles of the sheet: x of each unit; the rows are faded (0), normal (36) and fallen (72)
SPRITE_X = {
    301: 0, 302: 36, 303: 72, 313: 108, 304: 144, 311: 180, 310: 216, 308: 252, 312: 288, 309: 324, 306: 360, 305: 396,
    307: 432, 314: 468, 316: 504, 315: 540, 317: 576, 318: 576, 319: 612, 321: 648, 320: 685, 322: 722, 323: 833, 324: 907, 325: 944,
}
SHEET = "unitsprites_x34_y35_enhanced.png"

# DejaVu comes with the hub image (fonts-dejavu-core); the second of each pair is a developer machine
FONT_FILES = {
    False: ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/verdana.ttf"),
    True: ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/verdanab.ttf"),
}


def _px(value: float) -> int:
    return int(round(value * SCALE))


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False):
    """A font of `size` game pixels."""
    from PIL import ImageFont

    for path in FONT_FILES[bold]:
        try:
            return ImageFont.truetype(path, _px(size))
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=_px(size))
    except TypeError:                           # an old Pillow only has the bitmap font
        return ImageFont.load_default()


@lru_cache(maxsize=1)
def _has_real_font() -> bool:
    return any(Path(path).exists() for path in FONT_FILES[False])


def _plain(text) -> str:
    """The built-in font has no accented letters: without a real font they are written bare."""
    text = str(text)
    if _has_real_font():
        return text
    text = text.replace("…", "...").replace("·", "-")
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def _number(value) -> str:
    return f"{int(value):,}".replace(",", ".")


class _Pen:
    """Writes and paints in the game's pixels; the scale is applied here and nowhere else."""

    def __init__(self, image):
        from PIL import ImageDraw

        self.image = image
        self.draw = ImageDraw.Draw(image)

    def width(self, text, size: int, bold: bool = False) -> float:
        return self.draw.textlength(_plain(text), font=_font(size, bold)) / SCALE

    def text(self, x: float, y: float, text, size: int, fill=INK, bold: bool = False) -> None:
        self.draw.text((_px(x), _px(y)), _plain(text), font=_font(size, bold), fill=fill)

    def right(self, x: float, y: float, text, size: int, fill=INK, bold: bool = False) -> float:
        start = x - self.width(text, size, bold)
        self.text(start, y, text, size, fill, bold)
        return start

    def center(self, x: float, y: float, width: float, text, size: int, fill=INK, bold: bool = False) -> None:
        self.text(x + (width - self.width(text, size, bold)) / 2, y, text, size, fill, bold)

    def fit(self, text, size: int, width: float, bold: bool = False) -> str:
        text = str(text)
        if self.width(text, size, bold) <= width:
            return text
        while text and self.width(text + "…", size, bold) > width:
            text = text[:-1]
        return text + "…"

    def box(self, x: float, y: float, width: float, height: float, fill=None, outline=None, radius: float = 0) -> None:
        shape = (_px(x), _px(y), _px(x + width) - 1, _px(y + height) - 1)
        if radius:
            self.draw.rounded_rectangle(shape, radius=_px(radius), fill=fill, outline=outline)
        else:
            self.draw.rectangle(shape, fill=fill, outline=outline)

    def paste(self, picture, x: float, y: float) -> None:
        self.image.paste(picture, (_px(x), _px(y)), picture if picture.mode == "RGBA" else None)


def _static_file(relative: str) -> Path | None:
    if not relative:
        return None
    try:
        from django.contrib.staticfiles import finders

        found = finders.find(relative)
        if found:
            return Path(found)
    except Exception:  # noqa: BLE001 - outside Django (or before the apps load) the source folder is used
        pass
    path = Path(__file__).resolve().parents[3] / "static" / relative
    return path if path.exists() else None


@lru_cache(maxsize=32)
def _piece(name: str):
    """A file of static/game/combat, as it is."""
    from PIL import Image

    path = _static_file(f"game/combat/{name}")
    if path is None:
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


@lru_cache(maxsize=32)
def _scaled(name: str, width: int, height: int):
    from PIL import Image

    piece = _piece(name)
    return piece.resize((_px(width), _px(height)), Image.LANCZOS) if piece is not None else None


@lru_cache(maxsize=128)
def _tile(unit: int, fallen: bool = False):
    """The 36px tile of a unit: cut from the game's sheet, or made from the hub's picture of it."""
    from PIL import Image, ImageDraw

    size = (_px(TILE), _px(TILE))
    sheet = _piece(SHEET)
    if sheet is not None and unit in SPRITE_X:
        x, y = SPRITE_X[unit], 72 if fallen else 36
        return sheet.crop((x, y, x + TILE, y + TILE)).resize(size, Image.LANCZOS)
    tile = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=_px(4), fill=(252, 232, 198), outline=(205, 150, 92))
    path = _static_file(unit_icon(unit))
    picture = None
    if path is not None:
        try:
            picture = Image.open(path).convert("RGBA")
            picture.thumbnail((_px(TILE - 4), _px(TILE - 6)), Image.LANCZOS)
        except OSError:
            picture = None
    if picture is not None:
        tile.paste(picture, ((size[0] - picture.width) // 2, (size[1] - picture.height) // 2), picture)
    else:
        label = str(unit)
        font = _font(10, True)
        draw.text(((size[0] - draw.textlength(label, font=font)) / 2, size[1] / 2 - _px(6)), label, font=font, fill=INK)
    if fallen:
        grey = tile.convert("LA").convert("RGBA")
        grey.putalpha(tile.getchannel("A"))
        return grey
    return tile


def _slot_x(layout: str, anchor: int, count: int, index: int) -> int:
    if layout == "right":                       # the air defence fills from the right edge inwards
        return anchor - index * PITCH
    if layout == "center":
        return anchor - count * PITCH // 2 + 5 + index * PITCH
    return anchor + index * PITCH


def _slot(pen: _Pen, x: float, y: float, slot) -> None:
    if slot is None:
        empty = _scaled("empty.png", 35, 35)
        if empty is not None:
            pen.paste(empty, x, y)
        else:
            pen.box(x, y, 35, 35, outline=(255, 255, 255, 120))
        return
    fallen = slot["left"] <= 0
    pen.paste(_tile(slot["unit"], fallen), x, y)
    # hit points: the green bar, eaten from the top by what was lost
    damage = slot.get("damage")
    if damage is None:
        damage = BAR[1] if fallen else 0
    bar = _scaled("hitpoints1.png", *BAR)
    if bar is not None:
        pen.paste(bar, x + TILE, y)
    else:
        pen.box(x + TILE, y, *BAR, fill=WIN)
    if damage:
        pen.box(x + TILE, y, BAR[0], damage, fill=LOSS)
    if "ammo_used" in slot:
        pen.box(x + TILE + BAR[0], y, *BAR, fill=AMMO)
        if slot["ammo_used"]:
            pen.box(x + TILE + BAR[0], y, BAR[0], slot["ammo_used"], fill=AMMO_USED)
    label = f"{_number(slot['left'])} (-{_number(slot['lost'])})"
    size = 9
    while size > 6 and pen.width(label, size) > TILE + 4:       # the box may not reach the next slot
        size -= 1
    width = max(TILE, pen.width(label, size) + 2)
    left = x - (width - TILE) / 2
    pen.box(left, y + TILE - 1, width, 14, fill=NUMBER_BOX)
    pen.center(left, y + TILE + (10 - size) * 0.6, width, label, size, INK)


def _field(pen: _Pen, ox: float, oy: float, round_data: dict) -> None:
    kind = str(round_data.get("field") or "").lower()
    # a barbarian village has no city walls: the open field is the closest background the hub has
    name = BACKGROUNDS["sea" if "sea" in kind else "land" if "land" in kind or "barb" in kind else "city"]
    background = _scaled(name, *FIELD)
    if background is not None:
        pen.paste(background, ox, oy)
    else:
        pen.box(ox, oy, *FIELD, fill=(120, 150, 90))
    for side in ("attacker", "defender"):
        data = round_data[side]
        for zone, slots in (data.get("zones") or {}).items():
            if zone not in ZONES or not slots:
                continue
            layout, anchor, attacker_y, defender_y = ZONES[zone]
            y = attacker_y if side == "attacker" else defender_y
            for index, slot in enumerate(slots):
                _slot(pen, ox + _slot_x(layout, anchor, len(slots), index), oy + y, slot)
        panel_x, panel_y = RESERVE[side]
        pen.center(ox + panel_x, oy + panel_y + 4, 125, "Reserva", 9, INK, bold=True)
        reserve = data.get("reserve") or []
        room = RESERVE_COLUMNS * RESERVE_ROWS
        shown = reserve if len(reserve) <= room else reserve[:room - 1]
        for index, item in enumerate(shown):
            x = ox + panel_x + 7 + (index % RESERVE_COLUMNS) * 40
            y = oy + panel_y + 28 + (index // RESERVE_COLUMNS) * 51
            pen.paste(_tile(item["unit"]), x, y)
            pen.center(x - 2, y + TILE, TILE + 4, _number(item["left"]), 9, INK)
        if len(reserve) > room:
            x = ox + panel_x + 7 + ((room - 1) % RESERVE_COLUMNS) * 40
            y = oy + panel_y + 28 + ((room - 1) // RESERVE_COLUMNS) * 51
            pen.center(x - 2, y + 12, TILE + 4, f"+{len(reserve) - room + 1}", 11, MUTED, bold=True)


def _is_own(name: str, own_name: str) -> bool:
    """The report writes the player with the alliance tag glued on: "Fulano[TAG]"."""
    own = (own_name or "").strip().lower()
    return bool(own) and any(part.strip().lower().split("[")[0] == own for part in (name or "").split(","))


def _side_line(pen: _Pen, y: float, side: str, data: dict, own: bool, morale: list | None = None) -> None:
    """ "ATACANTE Fulano[TAG]" on the left; what is left and what fell in the round on the right."""
    label = "ATACANTE" if side == "attacker" else "DEFENSOR"
    width = FIELD[0] + 2 * PAD
    pen.text(PAD + 2, y + 3, label, 9, SIDE[side], bold=True)
    x = PAD + 2 + pen.width(label, 9, True) + 7
    name = pen.fit(data.get("name") or "?", 13, 430, True)
    pen.text(x, y, name, 13, INK, bold=True)
    x += pen.width(name, 13, True) + 7
    if own:
        tag = "SUA CONTA"
        pen.box(x, y + 2, pen.width(tag, 8, True) + 10, 13, fill=SIDE[side], radius=6)
        pen.text(x + 5, y + 3.5, tag, 8, WHITE, bold=True)
    lost = data.get("lost") or 0
    start = width - PAD - 2
    if morale:
        low, high = min(morale), max(morale)
        label = f"moral {low}%" if low == high else f"moral {low}–{high}%"
        start = pen.right(start, y + 2, label, 11, LOSS if low < 40 else INK) - 10
    start = pen.right(start, y + 2, f"-{_number(lost)} no round" if lost else "sem perdas no round", 11, LOSS if lost else MUTED)
    pen.right(start - 10, y + 2, f"{_number(data.get('left', 0))} restantes", 11, INK, bold=True)


UNITS_PER_ROW = 11
UNIT_BLOCK = 88
EVENT_TILE = 26
MAX_EVENTS = 6


def _wrap(pen: _Pen, text: str, size: int, width: float) -> list[str]:
    lines, current = [], ""
    for word in str(text).split():
        attempt = f"{current} {word}".strip()
        if current and pen.width(attempt, size) > width:
            lines.append(current)
            current = word
        else:
            current = attempt
    return lines + [current] if current else lines


def _event_layout(pen: _Pen, events: list) -> list:
    """[(event, lines of text, height)] for the events that fit under the field."""
    room = FIELD[0] - 84
    layout = []
    for event in events[:MAX_EVENTS]:
        lines = _wrap(pen, event["text"], 10, room)[:3]
        height = len(lines) * 13 + (EVENT_TILE + 14 if event.get("units") else 0) + 6
        layout.append((event, lines, height))
    return layout


def _events(pen: _Pen, y: float, layout: list, hidden: int) -> None:
    """What happened in the round: who joined (and with what), who fled, who was destroyed."""
    from PIL import Image

    pen.box(PAD, y, FIELD[0], 1, fill=LINE)
    y += 7
    pen.text(PAD + 2, y, "NESTE ROUND", 9, MUTED, bold=True)
    y += 15
    for event, lines, height in layout:
        pen.text(PAD + 2, y, (event.get("date") or "")[-8:], 9, MUTED)
        for index, line in enumerate(lines):
            pen.text(PAD + 76, y + index * 13 - 1, line, 10, INK)
        x, top = PAD + 76, y + len(lines) * 13 + 1
        for item in (event.get("units") or [])[:16]:
            tile = _tile(item["unit"]).resize((_px(EVENT_TILE), _px(EVENT_TILE)), Image.LANCZOS)
            pen.paste(tile, x, top)
            pen.center(x - 8, top + EVENT_TILE, EVENT_TILE + 16, _number(item["amount"]), 9, INK, bold=True)
            x += EVENT_TILE + 20
        y += height
    if hidden:
        pen.text(PAD + 76, y - 2, f"+{hidden} evento(s)", 9, MUTED)


def _summary_height(summary: dict | None) -> int:
    if not summary:
        return 0
    blocks = -(-len(summary.get("units") or []) // UNITS_PER_ROW)
    return 50 + blocks * UNIT_BLOCK + (30 if summary.get("loot") else 0) + 6


def _summary(pen: _Pen, y: float, summary: dict) -> None:
    """Who won, what each unit type had left and lost in the whole combat, and what was taken."""
    width = FIELD[0] + 2 * PAD
    pen.box(PAD, y, FIELD[0], 1, fill=LINE)
    y += 8
    winners, losers = summary.get("winners") or "", summary.get("losers") or ""
    if winners:
        pen.text(PAD + 2, y, "VENCEDOR", 9, MUTED, bold=True)
        pen.text(PAD + 2, y + 13, pen.fit(winners, 15, 430, True), 15, WIN, bold=True)
    if losers:
        pen.right(width - PAD - 2, y, "PERDEDOR", 9, MUTED, bold=True)
        pen.right(width - PAD - 2, y + 14, pen.fit(losers, 13, 400), 13, LOSS)
    y += 42
    units = summary.get("units") or []
    column = (FIELD[0] - 70) // UNITS_PER_ROW
    for start in range(0, len(units), UNITS_PER_ROW):
        pen.text(PAD + 2, y + 52, "Atacante", 10, SIDE["attacker"], bold=True)
        pen.text(PAD + 2, y + 68, "Defensor", 10, SIDE["defender"], bold=True)
        for index, unit in enumerate(units[start:start + UNITS_PER_ROW]):
            x = PAD + 70 + index * column
            pen.paste(_tile(unit["unit"]), x + (column - TILE) / 2, y)
            pen.center(x, y + TILE + 1, column, pen.fit(unit.get("name") or unit_name(unit["unit"]), 8, column - 2), 8, MUTED)
            for line, side in ((52, "attacker"), (68, "defender")):
                values = unit.get(side)
                if not values:
                    pen.box(x + column / 2 - 3, y + line + 6, 6, 1, fill=LINE)
                    continue
                count = _number(values["left"])
                fallen = f"-{_number(values['lost'])}" if values["lost"] else ""
                total = pen.width(count, 10, True) + (3 + pen.width(fallen, 10) if fallen else 0)
                left = x + (column - total) / 2
                pen.text(left, y + line, count, 10, SIDE[side], bold=True)
                if fallen:
                    pen.text(left + pen.width(count, 10, True) + 3, y + line, fallen, 10, LOSS)
        y += UNIT_BLOCK
    loot = summary.get("loot") or {}
    if loot:
        from PIL import Image

        x = PAD + 2
        pen.text(x, y + 5, "SAQUE", 9, MUTED, bold=True)
        x += 46
        for key, amount in loot.items():
            path = _static_file(f"game/resources/icon_{key}.png")
            picture = None
            if path is not None:
                try:
                    picture = Image.open(path).convert("RGBA")
                    picture.thumbnail((_px(20), _px(16)), Image.LANCZOS)
                except OSError:
                    picture = None
            if picture is not None:
                pen.paste(picture, x, y + 3)
                x += picture.width / SCALE + 4
            else:
                label = RESOURCE_LABELS.get(key, key)
                pen.text(x, y + 4, label, 10, MUTED)
                x += pen.width(label, 10) + 4
            pen.text(x, y + 2, _number(amount), 12, INK, bold=True)
            x += pen.width(_number(amount), 12, True) + 16


def render_battle(round_data: dict, summary: dict | None = None, *, own_name: str = "") -> bytes:
    """JPEG of one round's battlefield; with `summary`, the outcome of the combat goes under it."""
    from PIL import Image

    width = FIELD[0] + 2 * PAD
    events = round_data.get("events") or []
    layout = _event_layout(_Pen(Image.new("RGB", (4, 4))), events)
    hidden = max(0, len(events) - len(layout))
    events_height = (26 + sum(height for _event, _lines, height in layout) + (12 if hidden else 0)) if layout else 0
    height = HEAD + FIELD[1] + FOOT + events_height + _summary_height(summary)
    image = Image.new("RGB", (_px(width), _px(height)), PAPER)
    pen = _Pen(image)
    morale = {side: [army["morale"] for army in (round_data.get("morale") or {}).get(side) or []] for side in ("attacker", "defender")}

    total = round_data.get("total_rounds") or 0
    label = f"Round {round_data.get('round') or 1}" + (f" de {total}" if total else "")
    start = pen.right(width - PAD - 2, 7, label, 14, INK, bold=True)
    if round_data.get("date"):
        start = pen.right(start - 10, 10, round_data["date"], 10, MUTED)
    title = round_data.get("title") or (summary or {}).get("title") or "Combate"
    pen.text(PAD + 2, 5, pen.fit(title, 16, start - PAD - 16, True), 16, INK, bold=True)

    _side_line(pen, 31, "attacker", round_data["attacker"], _is_own(round_data["attacker"].get("name") or "", own_name), morale["attacker"])
    _field(pen, PAD, HEAD, round_data)
    _side_line(pen, HEAD + FIELD[1] + 6, "defender", round_data["defender"], _is_own(round_data["defender"].get("name") or "", own_name), morale["defender"])
    if layout:
        _events(pen, HEAD + FIELD[1] + FOOT, layout, hidden)
    if summary:
        _summary(pen, HEAD + FIELD[1] + FOOT + events_height, summary)

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90, optimize=True)
    return out.getvalue()
