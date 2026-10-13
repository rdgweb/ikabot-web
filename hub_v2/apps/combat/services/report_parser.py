"""What a combat report of the game says, read from the HTML the agent stores (N-109).

Two pages of the game are kept for a combat: the summary (who fought, what each side had
left and lost, who won, what was taken) and the detailed view, one battlefield per round
(side, zone, slot, unit, how many are left and how many fell in that round).
"""
from __future__ import annotations

import html as html_lib
import re

from core.catalogs import TRAINING_UNITS, UNIT_ICON_MAP

ROUND_SEPARATOR = "<!-- ROUND SEPARATOR -->"
ZONES = ("artillery", "longRange", "main", "flankLeft", "flankRight", "air", "airfighter")
ZONE_LABELS = {
    "artillery": "Artilharia",
    "longRange": "Longo alcance",
    "main": "Linha de frente",
    "flankLeft": "Flanco",
    "flankRight": "Flanco",
    "air": "Bombardeiros",
    "airfighter": "Defesa aérea",
}
SIDE_LABELS = {"attacker": "Atacante", "defender": "Defensor"}
RESOURCE_KEYS = {
    "materiais de construção": "wood", "madeira": "wood", "vinho": "wine", "mármore": "marble",
    "cristal": "glass", "enxofre": "sulfur",
}
RESOURCE_LABELS = {"wood": "Madeira", "wine": "Vinho", "marble": "Mármore", "glass": "Cristal", "sulfur": "Enxofre"}

# the game's numeric ids (css class "s<id>"); the wall fights as a unit of the defender
_UNITS = {int(unit["id"]): unit["name"] for group in TRAINING_UNITS.values() for unit in group}
_UNITS.update({314: "Muralha", 320: "Membro do Clube de Bárbaros"})      # 320: as the game names it in a barbarian village


def unit_name(unit_id) -> str:
    try:
        return _UNITS.get(int(unit_id)) or f"Unidade {unit_id}"
    except (TypeError, ValueError):
        return f"Unidade {unit_id}"


def unit_icon(unit_id) -> str:
    """Static path of the unit picture, or "" when the hub has none (the wall, for one)."""
    try:
        return UNIT_ICON_MAP.get(_UNITS.get(int(unit_id), ""), "")
    except (TypeError, ValueError):
        return ""


def _int(raw) -> int:
    digits = re.sub(r"\D", "", str(raw or ""))
    return int(digits) if digits else 0


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def split_rounds(detailed_html: str) -> list[str]:
    return [part for part in (detailed_html or "").split(ROUND_SEPARATOR) if 'id="battlefield"' in part]


def _side_names(html: str, side: str) -> str:
    found = re.search(rf'class="container_{side}[^"]*"\s*>(.*?)</div>', html, re.S)
    if not found:
        return ""
    lines = [_text(line) for line in re.split(r"<br\s*/?>", found.group(1))]
    return ", ".join(line for line in lines[1:] if line)        # the first line is the label


def _slots(zone_html: str) -> list:
    slots = []
    starts = list(re.finditer(r'<div(?:\s+id="slot\d+_\d+_\d+")?\s+class="slot([^"]*)"', zone_html))
    for position, start in enumerate(starts):
        classes = start.group(1)
        unit = re.search(r"\bs(\d{3})\b", classes)
        if "empty" in classes.split() or not unit:
            slots.append(None)
            continue
        end = starts[position + 1].start() if position + 1 < len(starts) else len(zone_html)
        inner = zone_html[start.end():end]
        number = re.search(r'class="number[^"]*"\s*>\s*([\d.,]+)\s*\(\s*-\s*([\d.,]+)\s*\)', inner)
        # the two bars the game draws beside the unit, in pixels taken from the top of a 32px bar
        damage = re.search(r'class="loss"\s*style="height:\s*(\d+)px', inner)
        ammo = re.search(r'class="ammoLoss"\s*style="height:\s*(\d+)px', inner)
        slot = {"unit": int(unit.group(1)), "left": _int(number.group(1)) if number else 0, "lost": _int(number.group(2)) if number else 0}
        if damage:
            slot["damage"] = min(32, int(damage.group(1)))
        if ammo:
            slot["ammo_used"] = min(32, int(ammo.group(1)))
        slots.append(slot)
    return slots


def _side(reserve_html: str, field_html: str) -> dict:
    reserve = [
        {"unit": int(unit), "left": _int(amount)}
        for unit, amount in re.findall(r'class="[^"]*\bs(\d{3})\b[^"]*"\s*>\s*</div>\s*([\d.,]+)', reserve_html)
    ]
    zones = {}
    parts = re.split(r'<div class="location[^"]*?\b(\w+)"\s*>', field_html)
    for name, chunk in zip(parts[1::2], parts[2::2]):
        zones[name] = _slots(chunk)
    return {"reserve": reserve, "zones": zones}


def _morale(html: str) -> dict:
    """The armies of each side in the round: what is left, what fell, and the morale."""
    armies: dict[str, list] = {"attacker": [], "defender": []}
    for table in re.findall(r'<table class="[^"]*container_morale[^"]*">(.*?)</table>', html, re.S):
        side = "attacker" if re.search(r'<th class="att', table) else "defender"
        rows = re.findall(
            r'<td class="avatarName">(.*?)</td>\s*<td class="militarySize">(.*?)</td>.*?<td class="morale[^"]*">\s*([\d.,]+)\s*%',
            table, re.S,
        )
        for name, size, percent in rows:
            numbers = re.search(r"([\d.,]+)\s*\(\s*-\s*([\d.,]+)\s*\)", size)
            armies[side].append({
                "name": _text(name),
                "left": _int(numbers.group(1)) if numbers else _int(size),
                "lost": _int(numbers.group(2)) if numbers else 0,
                "morale": min(100, _int(percent)),
            })
    return armies


def _events(html: str) -> list:
    """What the game tells under the field: who joined (and with what), who fled, who was destroyed."""
    start = html.find('class="container_events"')
    if start < 0:
        return []
    end = html.find("<script", start)
    events = []
    for date, body in re.findall(r'<span class="bold">(.*?)</span>\s*<p class="description">(.*?)</p>', html[start:end if end > 0 else None], re.S):
        units = [
            {"unit": int(unit), "amount": _int(amount)}
            for unit, amount in re.findall(r'class="[^"]*\bs(\d{3})\b[^"]*"\s*>\s*<div class="amount">\s*([\d.,]+)', body)
        ]
        text = _text(body.split("<ul")[0]).rstrip(":").strip()
        if text:
            events.append({"date": _text(date), "text": text, "units": units})
    return events


def parse_round(html: str) -> dict | None:
    """One battlefield of the detailed view: both sides, zone by zone."""
    if 'id="battlefield"' not in (html or ""):
        return None
    marks = {}
    for key in ("resAttacker", "fieldAttacker", "resDefender", "fieldDefender"):
        marks[key] = html.find(f'id="{key}"')
    end = html.find('class="container_defender', max(marks.values()))
    order = sorted((position, key) for key, position in marks.items() if position >= 0) + [(end if end > 0 else len(html), "")]
    chunks = {key: html[position:order[index + 1][0]] for index, (position, key) in enumerate(order[:-1])}

    head = re.search(r'class="headline_big[^"]*"\s*>(.*?)Round\s*<br\s*/?>\s*(\d+)\s*/\s*(\d+)\s*</span>(.*?)</td>', html, re.S)
    date = re.search(r"\(([^)]+)\)", head.group(4)) if head else None
    kind = re.search(r'id="battlefield"\s+class="([^"]*)"', html)
    result = {
        "title": _text(head.group(1)).replace("#", "").strip() if head else "",
        "round": int(head.group(2)) if head else 0,
        "total_rounds": int(head.group(3)) if head else 0,
        "date": date.group(1).strip() if date else "",
        "naval": bool(kind and "sea" in kind.group(1).lower()),
        "field": kind.group(1) if kind else "",
        "attacker": _side(chunks.get("resAttacker", ""), chunks.get("fieldAttacker", "")),
        "defender": _side(chunks.get("resDefender", ""), chunks.get("fieldDefender", "")),
        "morale": _morale(html),
        "events": _events(html),
    }
    result["attacker"]["name"] = _side_names(html, "attacker")
    result["defender"]["name"] = _side_names(html, "defender")
    for side in ("attacker", "defender"):
        slots = [slot for zone in result[side]["zones"].values() for slot in zone if slot]
        result[side]["left"] = sum(slot["left"] for slot in slots) + sum(item["left"] for item in result[side]["reserve"])
        result[side]["lost"] = sum(slot["lost"] for slot in slots)
    return result


def parse_rounds(detailed_html: str) -> list[dict]:
    rounds = [parsed for parsed in (parse_round(part) for part in split_rounds(detailed_html)) if parsed]
    return sorted(rounds, key=lambda item: item["round"])


def round_losses(parsed: dict, side: str) -> dict:
    """{unit id: how many fell in this round} for one side."""
    losses: dict[int, int] = {}
    for zone in parsed[side]["zones"].values():
        for slot in zone:
            if slot and slot["lost"]:
                losses[slot["unit"]] = losses.get(slot["unit"], 0) + slot["lost"]
    return losses


def _party(html: str, side: str) -> dict:
    found = re.search(rf'class="{side}[^"]*"\s*>\s*<label>.*?</label>\s*<span>(.*?)</span>\s*</div>', html, re.S)
    if not found:
        return {"name": "", "city": ""}
    inner = found.group(1)
    city = re.search(r"<b>(.*?)</b>", inner, re.S)
    name = _text(inner.split("<b>")[0])
    name = re.sub(r"\s+de$", "", name).strip()
    return {"name": name, "city": _text(city.group(1)) if city else ""}


def parse_summary(html: str) -> dict | None:
    """The summary page: the parties, what each unit type had left and lost, the outcome."""
    if "troopsReport" not in (html or "") and "militaryList" not in (html or ""):
        return None
    head = re.search(r'<h3 class="header">((?:(?!<h3).)*?)<span class="date">\s*\(([^)]*)\)', html, re.S)
    units: dict[int, dict] = {}
    order: list[int] = []
    columns: list[int] = []
    table = re.search(r'<table class="[^"]*militaryList[^"]*">(.*?)</table>', html, re.S)
    for row in re.findall(r"<tr[^>]*>.*?</tr>", table.group(1) if table else "", re.S):
        # the header of a block names its units: the game's own name is kept (it knows units the hub does not)
        header = re.findall(r'<div class="(?:army|fleet|ship)[^"]*\bs(\d{3})\b[^"]*"\s*>(?:\s*<div class="tooltip">\s*([^<]*?)\s*</div>)?', row)
        if header:
            columns = [int(unit) for unit, _name in header]
            for unit, name in header:
                unit = int(unit)
                if unit not in units:
                    units[unit] = {"unit": unit, "name": html_lib.unescape(name).strip() or unit_name(unit), "attacker": None, "defender": None}
                    order.append(unit)
            continue
        side = "attacker" if "attackerLine" in row else "defender" if "defenderLine" in row else ""
        if not side or not columns:
            continue
        # one cell per unit of the header: "-" when that side has none, else "left (-lost)"
        cells = re.finditer(
            r'<td class="center[^"]*" colspan="2">\s*-\s*</td>|<td class="numbers\b[^"]*">\s*([\d.,]*)\s*</td>\s*<td class="numbers2[^"]*">\s*\(\s*-?\s*([\d.,]*)\s*\)\s*</td>',
            row,
        )
        for unit, cell in zip(columns, cells):
            if cell.group(1) is None:
                continue
            current = units[unit][side] or {"left": 0, "lost": 0}
            units[unit][side] = {"left": current["left"] + _int(cell.group(1)), "lost": current["lost"] + _int(cell.group(2))}

    winners = re.search(r'class="winners[^"]*"\s*>(.*?)</div>', html, re.S)
    losers = re.search(r'class="losers[^"]*"\s*>(.*?)</div>', html, re.S)
    loot: dict[str, int] = {}
    looted = re.search(r'<ul class="resources">(.*?)</ul>', html, re.S)
    for amount, title in re.findall(r"<li[^>]*>\s*([\d.,]+)\s*<img[^>]+title=\"([^\"]+)\"", looted.group(1) if looted else ""):
        key = RESOURCE_KEYS.get(html_lib.unescape(title).strip().lower())
        if key:
            loot[key] = loot.get(key, 0) + _int(amount)
    round_label = re.search(r"<h5>[^<]*Round\s*(\d+)", html)

    def names(found) -> str:
        if not found:
            return ""
        return ", ".join(line for line in (_text(part) for part in re.split(r"<br\s*/?>", found.group(1))[1:]) if line)

    totals = {}
    for side in ("attacker", "defender"):
        rows = [item[side] for item in units.values() if item[side]]
        totals[side] = {"left": sum(row["left"] for row in rows), "lost": sum(row["lost"] for row in rows)}
    return {
        "title": _text(head.group(1)).replace("#", "").strip() if head else "",
        "date": head.group(2).strip() if head else "",
        "attacker": _party(html, "attacker"),
        "defender": _party(html, "defender"),
        "units": [units[unit] for unit in order],
        "totals": totals,
        "winners": names(winners),
        "losers": names(losers),
        "loot": loot,
        "rounds": int(round_label.group(1)) if round_label else 0,
        "army_lost": "dado como perdido" in html.lower(),
    }
