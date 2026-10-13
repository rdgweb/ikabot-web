"""The battlefield of a round laid out for the page (N-109).

Same geometry as the picture sent on Telegram (battle_image), but as positions in percent of
the field, so that the page builds it with the game's pieces in HTML and it follows the width
of the screen.
"""
from __future__ import annotations

from .battle_image import BACKGROUNDS, BAR, FIELD, PITCH, RESERVE, RESERVE_COLUMNS, SPRITE_X, TILE, ZONES, _is_own, _slot_x
from .report_parser import ZONE_LABELS, unit_icon, unit_name

SHEET_SIZE = (979, 108)


def _number(value) -> str:
    return f"{int(value or 0):,}".replace(",", ".")


def _percent(value: float, total: float) -> str:
    return f"{value / total * 100:.3f}"


def tile(unit: int, fallen: bool = False) -> dict:
    """How the page shows a unit: a cut of the game's sheet, or the hub's own picture of it."""
    if unit in SPRITE_X:
        x = SPRITE_X[unit] / (SHEET_SIZE[0] - TILE) * 100
        return {"name": unit_name(unit), "sprite": f"{x:.3f}% {100 if fallen else 50}%", "icon": "", "fallen": fallen}
    return {"name": unit_name(unit), "sprite": "", "icon": unit_icon(unit), "fallen": fallen}


def _place(x: float, y: float) -> dict:
    return {"left": _percent(x, FIELD[0]), "top": _percent(y, FIELD[1])}


def round_board(round_data: dict, own_name: str = "") -> dict:
    """One round as the template draws it."""
    kind = str(round_data.get("field") or "").lower()
    slots, reserves = [], []
    for side in ("attacker", "defender"):
        data = round_data[side]
        for zone, zone_slots in (data.get("zones") or {}).items():
            if zone not in ZONES or not zone_slots:
                continue
            layout, anchor, attacker_y, defender_y = ZONES[zone]
            y = attacker_y if side == "attacker" else defender_y
            for index, slot in enumerate(zone_slots):
                item = _place(_slot_x(layout, anchor, len(zone_slots), index), y)
                if slot is None:
                    item["empty"] = True
                else:
                    fallen = slot["left"] <= 0
                    damage = slot.get("damage")
                    item.update(tile(slot["unit"], fallen))
                    item.update({
                        "label": f"{_number(slot['left'])} (-{_number(slot['lost'])})",
                        "count": _number(slot["left"]),
                        "lost": _number(slot["lost"]) if slot["lost"] else "",
                        "title": f"{unit_name(slot['unit'])} · {ZONE_LABELS.get(zone, zone)} · {_number(slot['left'])} (-{_number(slot['lost'])})",
                        "damage": _percent(BAR[1] if damage is None and fallen else damage or 0, BAR[1]),
                        "ammo": _percent(slot["ammo_used"], BAR[1]) if "ammo_used" in slot else "",
                    })
                slots.append(item)
        panel_x, panel_y = RESERVE[side]
        units = []
        for index, item in enumerate((data.get("reserve") or [])[:9]):
            place = _place(panel_x + 7 + (index % RESERVE_COLUMNS) * 40, panel_y + 28 + (index // RESERVE_COLUMNS) * 51)
            units.append({**place, **tile(item["unit"]), "amount": _number(item["left"])})
        reserves.append({"title": _place(panel_x, panel_y + 4), "units": units, "hidden": max(0, len(data.get("reserve") or []) - 9)})

    sides = {}
    morale = round_data.get("morale") or {}
    for side in ("attacker", "defender"):
        data = round_data[side]
        sides[side] = {
            "name": data.get("name") or "?",
            "own": _is_own(data.get("name") or "", own_name),
            "left": _number(data.get("left")),
            "lost": _number(data.get("lost")) if data.get("lost") else "",
            "armies": [
                {"name": army["name"], "left": _number(army["left"]), "lost": _number(army["lost"]) if army["lost"] else "", "morale": army["morale"]}
                for army in morale.get(side) or []
            ],
        }
    events = [
        {
            "time": (event.get("date") or "")[-8:],
            "text": event["text"],
            "units": [{**tile(item["unit"]), "amount": _number(item["amount"])} for item in event.get("units") or []],
        }
        for event in round_data.get("events") or []
    ]
    return {
        "number": round_data["round"],
        "date": round_data.get("date") or "",
        "background": "game/combat/" + BACKGROUNDS["sea" if "sea" in kind else "land" if "land" in kind or "barb" in kind else "city"],
        "slots": slots,
        "reserves": reserves,
        "sides": sides,
        "events": events,
    }


# what the stylesheet needs to cut the sheet and size the pieces, in percent of the field
GEOMETRY = {
    "slot_width": _percent(TILE, FIELD[0]),
    "slot_height": _percent(TILE, FIELD[1]),
    "sheet_width": _percent(SHEET_SIZE[0], TILE),
    "sheet_height": _percent(SHEET_SIZE[1], TILE),
    "bar_width": _percent(BAR[0], TILE),
    "bar_height": _percent(BAR[1], TILE),
    "panel_width": _percent(125, FIELD[0]),
    "pitch": PITCH,
}
