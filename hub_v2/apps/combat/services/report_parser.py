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


WALL_UNIT = 314


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


# N-112: what the game writes under "Alterações da moral" when the mouse is over the morale of an
# army, by the words that tell one reason from another. The points come from the text itself.
MORALE_REASONS = (
    ("tired", r"exaust"),
    ("more_damage", r"mais danos"),
    ("more_losses", r"mais perdas"),
    ("outnumbered", r"superior"),
    ("bombed", r"bombarde"),
    ("flanks", r"flancos"),
    ("wall", r"impenetr"),
    ("same_damage", r"mesmo dano"),
    ("same_losses", r"mesmo n"),
    ("supply", r"cozinheir|refei|reparador|abasteciment"),
)
MORALE_LABELS = {
    "tired": "Tropas exaustas",
    "more_damage": "Sofreu mais dano",
    "more_losses": "Perdeu mais unidades",
    "outnumbered": "Adversário em maior número",
    "bombed": "Foi bombardeado",
    "flanks": "Flancos atingidos",
    "wall": "Muralha impenetrável",
    "same_damage": "Mesmo dano que o adversário",
    "same_losses": "Mesmas perdas que o adversário",
    "supply": "Cozinheiros",
    "other": "",
}
# an upgrade of armour, by its name (the other line of a unit is the weapon)
_ARMOUR_WORDS = re.compile(r"escudo|capacete|armadura|vestes|muralha|revestimento|cobertura|cesto|couro|blindagem|casco", re.I)


def _tips(html: str) -> dict:
    """What the page shows on mouse-over, by the id it is registered under: {"11_21_0": {"title", "lines"}}."""
    tips = {}
    for key, body in re.findall(r'registerMouseOver\(\s*"([^"]+)"\s*,\s*"((?:[^"\\]|\\.)*)"', html or ""):
        body = body.replace('\\"', '"').replace("\\/", "/")
        title = re.search(r"<h2>(.*?)</h2>", body, re.S)
        tips[key] = {"title": _text(title.group(1)) if title else "", "lines": [_text(line) for line in re.findall(r"<p>(.*?)</p>", body, re.S)]}
    return tips


def _slot_details(tip: dict | None) -> dict:
    """Of a slot: whose it is, how whole (hp, ammunition, in percent), its upgrades, the level of a wall."""
    if not tip:
        return {}
    found: dict = {}
    owner = re.search(r"\(([^()]*(?:\([^()]*\))?[^()]*)\)\s*$", tip["title"])
    if owner:
        found["owner"] = owner.group(1).strip()
    levels: list[tuple[bool, int]] = []
    for line in tip["lines"]:
        upgrade = re.search(r"\((\d+)\)\s*$", line)
        if upgrade:
            levels.append((bool(_ARMOUR_WORDS.search(line)), int(upgrade.group(1))))
            continue
        number = re.search(r"(\d+)\s*%?\s*$", line)
        if not number:
            continue
        value = int(number.group(1))
        lowered = line.lower()
        if lowered.startswith("pontos de vida"):
            found["hp"] = min(100, value)
        elif lowered.startswith("muni"):
            found["ammo"] = min(100, value)
        elif "da muralha" in lowered and ":" in line:
            found["level"] = value
    if levels and "level" not in found:
        # two lines: the weapon, then the armour; one line alone is told apart by its name
        if len(levels) >= 2:
            found["upgrades"] = [levels[0][1], levels[1][1]]
        else:
            found["upgrades"] = [0, levels[0][1]] if levels[0][0] else [levels[0][1], 0]
    return found


def _morale_reasons(tip: dict | None) -> list:
    """Why the morale of an army changed in the round: [{"reason", "points", "text"}]."""
    reasons = []
    for line in (tip or {}).get("lines") or []:
        reason = next((key for key, words in MORALE_REASONS if re.search(words, line, re.I)), "other")
        percent = re.search(r"(\d+)\s*%", line)
        # for what the cooks give, the game writes the morale the army ended with, not the points
        points = -int(percent.group(1)) if percent and reason != "supply" else None
        reasons.append({"reason": reason, "points": points, "text": line})
    return reasons


def split_rounds(detailed_html: str) -> list[str]:
    return [part for part in (detailed_html or "").split(ROUND_SEPARATOR) if 'id="battlefield"' in part]


def _side_names(html: str, side: str) -> str:
    found = re.search(rf'class="container_{side}[^"]*"\s*>(.*?)</div>', html, re.S)
    if not found:
        return ""
    lines = [_text(line) for line in re.split(r"<br\s*/?>", found.group(1))]
    return ", ".join(line for line in lines[1:] if line)        # the first line is the label


def _slots(zone_html: str, tips: dict | None = None) -> list:
    slots = []
    starts = list(re.finditer(r'<div(?:\s+id="slot(\d+_\d+_\d+)")?\s+class="slot([^"]*)"', zone_html))
    for position, start in enumerate(starts):
        classes = start.group(2)
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
        slot.update(_slot_details((tips or {}).get(start.group(1) or "")))
        slots.append(slot)
    return slots


def _side(reserve_html: str, field_html: str, tips: dict | None = None) -> dict:
    reserve = [
        {"unit": int(unit), "left": _int(amount)}
        for unit, amount in re.findall(r'class="[^"]*\bs(\d{3})\b[^"]*"\s*>\s*</div>\s*([\d.,]+)', reserve_html)
    ]
    zones = {}
    parts = re.split(r'<div class="location[^"]*?\b(\w+)"\s*>', field_html)
    for name, chunk in zip(parts[1::2], parts[2::2]):
        zones[name] = _slots(chunk, tips)
    return {"reserve": reserve, "zones": zones}


def _morale(html: str, tips: dict | None = None) -> dict:
    """The armies of each side in the round: what is left, what fell, the morale and why it changed."""
    armies: dict[str, list] = {"attacker": [], "defender": []}
    for table in re.findall(r'<table class="[^"]*container_morale[^"]*">(.*?)</table>', html, re.S):
        side = "attacker" if re.search(r'<th class="att', table) else "defender"
        rows = re.findall(
            r'<td class="avatarName">(.*?)</td>\s*<td class="militarySize">(.*?)</td>(.*?)<td class="morale[^"]*">\s*([\d.,]+)\s*%',
            table, re.S,
        )
        for name, size, between, percent in rows:
            numbers = re.search(r"([\d.,]+)\s*\(\s*-\s*([\d.,]+)\s*\)", size)
            army = {
                "name": _text(name),
                "left": _int(numbers.group(1)) if numbers else _int(size),
                "lost": _int(numbers.group(2)) if numbers else 0,
                "morale": min(100, _int(percent)),
            }
            bar = re.search(r'id="slot(morale\d+)"', between)
            reasons = _morale_reasons((tips or {}).get(bar.group(1))) if bar else []
            if reasons:
                army["reasons"] = reasons
            armies[side].append(army)
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
    tips = _tips(html)
    result = {
        "title": _text(head.group(1)).replace("#", "").strip() if head else "",
        "round": int(head.group(2)) if head else 0,
        "total_rounds": int(head.group(3)) if head else 0,
        "date": date.group(1).strip() if date else "",
        "naval": bool(kind and "sea" in kind.group(1).lower()),
        "field": kind.group(1) if kind else "",
        "attacker": _side(chunks.get("resAttacker", ""), chunks.get("fieldAttacker", ""), tips),
        "defender": _side(chunks.get("resDefender", ""), chunks.get("fieldDefender", ""), tips),
        "morale": _morale(html, tips),
        "events": _events(html),
    }
    result["attacker"]["name"] = _side_names(html, "attacker")
    result["defender"]["name"] = _side_names(html, "defender")
    for side in ("attacker", "defender"):
        slots = [slot for zone in result[side]["zones"].values() for slot in zone if slot]
        result[side]["left"] = sum(slot["left"] for slot in slots) + sum(item["left"] for item in result[side]["reserve"])
        result[side]["lost"] = sum(slot["lost"] for slot in slots)
        # N-112: the upgrades the round shows for this side, {unit: [weapon, armour]}, and per player
        upgrades: dict[int, list] = {}
        players: dict[str, dict] = {}
        for slot in slots:
            if slot.get("upgrades") and slot["unit"] != WALL_UNIT:
                upgrades.setdefault(slot["unit"], slot["upgrades"])
                if slot.get("owner"):
                    players.setdefault(slot["owner"], {}).setdefault(slot["unit"], slot["upgrades"])
        if upgrades:
            result[side]["upgrades"] = upgrades
            result[side]["players"] = players
    wall = [slot for slot in result["defender"]["zones"].get("main") or [] if slot and slot["unit"] == WALL_UNIT]
    if wall:
        # the sections of the wall drawn in the round; one that fell shows 0 left
        result["wall"] = {
            "level": next((slot["level"] for slot in wall if slot.get("level")), 0),
            "sections": len(wall),
            "standing": sum(1 for slot in wall if slot["left"] > 0),
            "hp": [slot.get("hp", 100 if slot["left"] > 0 else 0) for slot in wall],
        }
    return result


def _fill_supply(rounds: list) -> None:
    """How many points the cooks (or the tenders) gave back, which the game does not write.

    It writes the morale the army ended the round with; the points are what is missing between
    that and the morale before less what the round took. Left unknown when the round before is
    not there, or when the morale ended at zero (the count stops there).
    """
    before: dict[tuple, int] = {}
    for item in rounds:
        for side, armies in (item.get("morale") or {}).items():
            for army in armies:
                key = (side, army["name"])
                previous = before.get(key, 100 if item["round"] == 1 else None)
                taken = sum(reason["points"] for reason in army.get("reasons") or [] if reason["points"])
                for reason in army.get("reasons") or []:
                    if reason["reason"] == "supply" and reason["points"] is None and previous is not None and army["morale"] > 0:
                        reason["points"] = max(0, army["morale"] - (previous + taken))
                before[key] = army["morale"]


def parse_rounds(detailed_html: str) -> list[dict]:
    rounds = [parsed for parsed in (parse_round(part) for part in split_rounds(detailed_html)) if parsed]
    rounds = sorted(rounds, key=lambda item: item["round"])
    _fill_supply(rounds)
    return rounds


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
