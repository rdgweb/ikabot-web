"""Where the simulator and the game part, round by round (N-197).

Each combat the hub checks is fought again and put beside its report one round at a time: in which
line each kind of unit stood (the front, a flank, the shooters, the artillery, the reserve), how many
of each kind were still alive, and how many sections of the wall stood. What differs is kept with
the check; the simulator page counts what keeps differing, with one battle to look at, so a rule the
simulator does not know (a shooter out of shots taking the front, a ram in the artillery slots)
shows itself without anyone digging in the reports.
"""
from __future__ import annotations

from collections import defaultdict

WALL_UNIT = 314
# the report's zones in the simulator's lines
ZONE_LINE = {"main": "principal", "flankLeft": "flancos", "flankRight": "flancos", "longRange": "longa",
             "artillery": "artilharia", "air": "bomb", "airfighter": "aa"}
LINE_LABEL = {"principal": "linha de frente", "flancos": "flanco", "longa": "longo alcance", "artilharia": "artilharia",
              "bomb": "bombardeiros", "aa": "antiaéreo", "bagagem": "apoio", "reserva": "reserva"}
SIDE_LABEL = {"attacker": "atacante", "defender": "defensor"}
ALIVE_MIN_GAP = 5               # units: a smaller gap in a round is the dice, not a rule
ALIVE_SHARE = 0.2               # and a fifth of what was there
MAX_ITEMS = 40


def _real_lines(side: dict) -> tuple[dict[int, set], dict[int, int]]:
    lines: dict[int, set] = defaultdict(set)
    alive: dict[int, int] = defaultdict(int)
    for zone, slots in (side.get("zones") or {}).items():
        for slot in slots or []:
            if not slot or int(slot["unit"]) == WALL_UNIT:
                continue
            uid = int(slot["unit"])
            lines[uid].add(ZONE_LINE.get(zone, zone))
            alive[uid] += int(slot.get("left") or 0)
    for item in side.get("reserve") or []:
        uid = int(item["unit"])
        if uid == WALL_UNIT or not int(item.get("left") or 0):
            continue
        lines[uid].add("reserva")
        alive[uid] += int(item.get("left") or 0)
    return lines, alive


def _sim_lines(deployed: dict, alive_start: dict) -> dict[int, set]:
    lines: dict[int, set] = defaultdict(set)
    placed: dict[int, int] = defaultdict(int)
    for line, units in (deployed or {}).items():
        for uid, qty in (units or {}).items():
            if int(qty or 0) > 0:
                # the cooks and doctors have no slot on the field: the report shows them in the reserve
                lines[int(uid)].add("reserva" if line == "bagagem" else line)
                placed[int(uid)] += int(qty)
    for uid, qty in (alive_start or {}).items():
        if int(qty or 0) > placed.get(int(uid), 0):
            lines[int(uid)].add("reserva")
    return lines


def compare(real_rounds: list[dict], sim: dict, start_units: dict | None = None) -> list[dict]:
    """[{kind: "linha"|"vivos"|"muralha", round, side, unit, real, sim}] for what differs (`start_units`: each side's
    army when the battle began, {"attacker": {unit: qty}, "defender": {...}})."""
    sim_rounds = (sim.get("details") or {}).get("rounds") if isinstance(sim.get("details"), dict) else None
    if not sim_rounds:
        return []
    out: list[dict] = []
    alive_before = {side: {int(u): int(q) for u, q in ((start_units or {}).get(side) or {}).items()} for side in ("attacker", "defender")}
    for index, real in enumerate(real_rounds):
        if index >= len(sim_rounds):
            break
        simulated = sim_rounds[index]
        number = index + 1
        for side in ("attacker", "defender"):
            real_lines, real_alive = _real_lines(real.get(side) or {})
            # where each one stood when the round began (as the report draws it), not who was left there at its end
            sim_lines = _sim_lines(simulated.get(f"{side}_start_lines") or simulated.get(f"{side}_lines") or {}, alive_before[side])
            sim_alive = {int(u): int(q) for u, q in (simulated.get(f"{side}_alive") or {}).items()}
            for uid in sorted(set(real_lines) | set(sim_lines)):
                got, want = sorted(real_lines.get(uid, set())), sorted(sim_lines.get(uid, set()))
                if got and want and got != want:
                    out.append({"kind": "linha", "round": number, "side": side, "unit": uid, "real": got, "sim": want})
            for uid in sorted(set(real_alive) | set(sim_alive)):
                got, want = int(real_alive.get(uid, 0)), int(sim_alive.get(uid, 0))
                if abs(got - want) >= max(ALIVE_MIN_GAP, ALIVE_SHARE * max(got, want, 1)):
                    out.append({"kind": "vivos", "round": number, "side": side, "unit": uid, "real": got, "sim": want})
            alive_before[side] = sim_alive
        wall = real.get("wall")
        if wall and simulated.get("wall_sections") is not None:
            got, want = int(wall.get("standing") or 0), int(simulated.get("wall_sections") or 0)
            if got != want:
                out.append({"kind": "muralha", "round": number, "side": "defender", "unit": WALL_UNIT, "real": got, "sim": want})
        if len(out) >= MAX_ITEMS:
            break
    return out[:MAX_ITEMS]


def summary(checks, limit: int = 12) -> list[dict]:
    """What keeps differing across the checks: one row per difference, with how many battles show it and one of them."""
    from .report_parser import unit_name

    rows: dict[tuple, dict] = {}
    for check in checks:
        found = (check.predicted_json or {}).get("divergences") or []
        seen_here = set()
        for item in found:
            if item.get("kind") == "linha":
                key = ("linha", item["side"], int(item["unit"]), tuple(item["real"]), tuple(item["sim"]))
            elif item.get("kind") == "vivos":
                key = ("vivos", item["side"], int(item["unit"]), "menos" if int(item["sim"]) < int(item["real"]) else "mais")
            else:
                key = ("muralha", "defender", WALL_UNIT, "menos" if int(item["sim"]) < int(item["real"]) else "mais")
            if key in seen_here:
                continue
            seen_here.add(key)
            row = rows.setdefault(key, {"key": key, "battles": 0, "example": check, "round": item["round"],
                                        "real": item["real"], "sim": item["sim"]})
            row["battles"] += 1
    out = []
    for row in sorted(rows.values(), key=lambda r: -r["battles"])[:limit]:
        kind, side, uid = row["key"][0], row["key"][1], row["key"][2]
        name = "Muralha" if uid == WALL_UNIT else unit_name(uid)
        if name.startswith("Unidade"):
            from apps.espionage.services.unit_stats import UNIT_STATS

            name = (UNIT_STATS.get(uid) or {}).get("name") or name
        if kind == "linha":
            text = (f"{name} ({SIDE_LABEL[side]}): no jogo em {', '.join(LINE_LABEL.get(x, x) for x in row['real'])}; "
                    f"no simulador em {', '.join(LINE_LABEL.get(x, x) for x in row['sim'])}")
        elif kind == "vivos":
            text = f"{name} ({SIDE_LABEL[side]}): o simulador deixa {'menos' if row['key'][3] == 'menos' else 'mais'} vivos que o jogo"
        else:
            text = f"Muralha: o simulador deixa {'menos' if row['key'][3] == 'menos' else 'mais'} partes de pé que o jogo"
        out.append({"kind": kind, "text": text, "battles": row["battles"], "check": row["example"], "round": row["round"],
                    "real": row["real"], "sim": row["sim"]})
    return out
