"""Painel de Construcoes — visao agregada multi-conta.

Junta tres fontes:
  - Snapshot das cidades (edificios em obra agora + niveis atuais)
  - Jobs de Plano de Construcao (ac=1002) ativos + progresso derivado do snapshot
  - ConstructionResourceReservation (reservas locais e faltas por cidade/recurso)

N-103: the page answers three questions, in this order: what is happening (every city,
by account, with its state), what is stopped and why (idle cities, cities waiting for a
resource), and what comes next (each plan with the cost and the building time left).
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.templatetags.static import static
from django.utils import timezone
from django.views.generic import TemplateView

from apps.accounts.models import Account, GameAccount
from apps.dashboard.services.overview import compact, human_span
from apps.game import city_map
from apps.game.services import construction_board as board
from apps.jobs.models import ConstructionResourceReservation, Job, JobLog
from core.catalogs import get_building_info


RESOURCE_KEYS = ("wood", "wine", "marble", "crystal", "sulfur")
RESOURCE_META = {
    "wood": {"label": "Madeira", "icon": "game/resources/icon_wood.png"},
    "wine": {"label": "Vinho", "icon": "game/resources/icon_wine.png"},
    "marble": {"label": "Mármore", "icon": "game/resources/icon_marble.png"},
    "crystal": {"label": "Cristal", "icon": "game/resources/icon_glass.png"},
    "sulfur": {"label": "Enxofre", "icon": "game/resources/icon_sulfur.png"},
}
TRADEGOOD_ICON = {
    0: "game/resources/icon_wood.png",
    1: "game/resources/icon_wine.png",
    2: "game/resources/icon_marble.png",
    3: "game/resources/icon_glass.png",
    4: "game/resources/icon_sulfur.png",
}
ACTIVE_PLAN_STATUSES = ("queued", "running", "scheduled")
# Ordem canonica das colunas do mapa de niveis (Prefeitura sempre primeira).
# Ids fora desta lista vao para o fim, ordenados por nome.
GLOBAL_BUILDING_ORDER = (
    # civicos / residencias
    "townHall", "palace", "palaceColony", "governorsResidence", "academy",
    # armazenamento lado a lado
    "warehouse", "dump",
    # redutores lado a lado (madeira, vinho, marmore, cristal, enxofre, tempo)
    "carpentering", "vineyard", "architect", "optician", "fireworker", "chronos_forge", "chronosForge",
    # militar / naval / social
    "tavern", "museum", "barracks", "shipyard", "port", "wall", "hideout", "safehouse",
    # producao diversa
    "forester", "winegrower", "stonemason", "glassblowing", "alchemist",
    # especiais
    "temple", "embassy", "workshop", "branchOffice", "marketplace",
    "blackMarket", "pirateFortress", "marineChartArchive", "shrineOfOlympus", "dockyard",
)


def _si(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _building_icon(building_id: str) -> str:
    info = get_building_info(building_id)
    icon = info.get("icon")
    if icon:
        return static(f"game/buildings/{icon}")
    return static("game/buildings/townhall.png")


def _building_name(building_id: str) -> str:
    return str(get_building_info(building_id).get("name") or building_id)


def _city_list(snapshot):
    raw = snapshot.cities if snapshot else []
    if isinstance(raw, dict):
        return raw.get("cities", []) or []
    if isinstance(raw, list):
        return raw
    return []


def _reorder_layout(city: dict) -> tuple[list[dict], bool]:
    """The city's 25 slots for the drag-and-drop reorder (N-50).

    `ready` is False until a Check Status stored what each slot accepts
    (`allowed`); without it the UI can't apply the game's own rules.
    """
    slots: list[dict] = []
    ready = True
    for b in sorted(
        (b for b in city.get("buildings") or [] if isinstance(b, dict)),
        key=lambda b: _si(b.get("position"), 99),
    ):
        bid = str(b.get("building") or "empty")
        empty = bid == "empty"
        allowed = b.get("allowed")
        if not isinstance(allowed, list) or (not empty and b.get("building_id") is None):
            ready = False
        position = _si(b.get("position"))
        ground_type = str(b.get("type") or "") or city_map.GROUND_TYPES.get(b.get("ground_id"), "")
        anchor = city_map.POSITIONS.get(position, (0, 0))
        slots.append({
            "ax": anchor[0],
            "ay": anchor[1],
            "sp": city_map.building_sprite(bid, position, ground_type),
            "p": position,
            "b": bid,
            "n": "Espaço livre" if empty else _building_name(bid),
            "i": "" if empty else _building_icon(bid),
            "l": 0 if empty else _si(b.get("level")),
            "u": bool(b.get("is_upgrading")),
            "id": None if empty else b.get("building_id"),
            "a": allowed if isinstance(allowed, list) else [],
            "t": str(b.get("type") or ""),
            "g": b.get("ground_id"),
        })
    return slots, ready and bool(slots)


def _resource_chips(amounts: dict) -> list[dict]:
    """[{key, label, icon, amount, text}] for the resources that have an amount, in the usual order."""
    return [
        {"key": key, "label": RESOURCE_META[key]["label"], "icon": static(RESOURCE_META[key]["icon"]),
         "amount": _si(amounts.get(key)), "text": compact(amounts.get(key))}
        for key in RESOURCE_KEYS if _si(amounts.get(key)) > 0
    ]


def _step_chip(entry: dict) -> dict:
    return {
        "icon": _building_icon(entry["building"]), "name": _building_name(entry["building"]),
        "cur": entry["cur"], "target": entry["target"], "new": entry.get("new", False),
    }


def _fmt_eta(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{seconds}s"


class ConstructionPanelView(LoginRequiredMixin, TemplateView):
    template_name = "game/construction.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        now = int(time.time())

        accounts = (
            Account.objects.filter(active=True)
            .select_related("node")
            .prefetch_related(
                Prefetch(
                    "game_accounts",
                    queryset=GameAccount.objects.filter(active=True).select_related("snapshot"),
                    to_attr="active_game_accounts",
                ),
            )
            .order_by("label")
        )

        ga_ids = [ga.pk for acct in accounts for ga in getattr(acct, "active_game_accounts", [])]

        # --- Reservas: totais por recurso e o que falta em cada cidade / em cada plano ---
        reserved_total = {k: 0 for k in RESOURCE_KEYS}
        shortfall_total = {k: 0 for k in RESOURCE_KEYS}
        reservations = list(
            ConstructionResourceReservation.objects.filter(game_account_id__in=ga_ids, status="active")
        ) if ga_ids else []
        for r in reservations:
            res = board.resource_key(r.resource)      # the plan calls crystal "glas"
            if res not in reserved_total:
                continue
            reserved_total[res] += _si(r.reserved_local_amount)
            shortfall_total[res] += _si(r.shortfall_amount)

        # --- Snapshot: em obra agora + lookup de niveis ---
        building_now: list[dict] = []
        level_lookup: dict[str, dict[str, dict[str, int]]] = {}  # ga -> city -> {pos: lvl, bid: lvl}
        level_lookup_cities: dict[str, list[dict]] = {}  # ga -> [{id, name}]
        ga_meta: dict[str, dict] = {}
        level_map: list[dict] = []
        reorder_layouts: dict[str, dict] = {}  # "ga:city" -> layout for drag-and-drop (N-50)
        city_facts: dict[str, list[dict]] = {}  # ga -> what the board needs of each city

        for acct in accounts:
            for ga in getattr(acct, "active_game_accounts", []):
                snap = getattr(ga, "snapshot", None)
                ga_meta[str(ga.pk)] = {"name": ga.name or ga.server_id, "account": acct.label}
                cities = _city_list(snap)
                city_pos_lvl: dict[str, dict[str, int]] = {}
                ga_city_list: list[dict] = []
                lm_rows = []
                for city in cities:
                    if not isinstance(city, dict):
                        continue
                    cid = str(city.get("id") or "")
                    ga_city_list.append({"id": cid, "name": str(city.get("name") or cid)})
                    try:
                        tg = int(city.get("tradegood_id") or city.get("produced_tradegood") or city.get("tradegood") or 0)
                    except (TypeError, ValueError):
                        tg = 0
                    pos_lvl = {"_pos": {}, "_bid": {}}
                    # cada bid pode ter varias instancias (ex: multiplos armazens)
                    bid_instances: dict[str, list[dict]] = {}
                    works: list[dict] = []
                    free_slots = 0
                    for b in city.get("buildings") or []:
                        if not isinstance(b, dict):
                            continue
                        bid = str(b.get("building") or "")
                        if not bid or bid == "empty":
                            free_slots += 1
                            continue
                        lvl = _si(b.get("level"))
                        pos = str(b.get("position"))
                        upg = bool(b.get("is_upgrading"))
                        pos_lvl["_pos"][pos] = lvl
                        prev = pos_lvl["_bid"].get(bid, -1)
                        if lvl > prev:
                            pos_lvl["_bid"][bid] = lvl
                        bid_instances.setdefault(bid, []).append({"level": lvl, "is_upgrading": upg})
                        if b.get("is_upgrading"):
                            work = {
                                "ga_name": ga.name or ga.server_id,
                                "account": acct.label,
                                "city_name": city.get("name") or cid,
                                "tradegood_icon": static(TRADEGOOD_ICON.get(tg, TRADEGOOD_ICON[0])),
                                "building": bid,
                                "icon": _building_icon(bid),
                                "name": _building_name(bid),
                                "from_level": lvl,
                                "to_level": lvl + 1,
                                "end_at": _si(b.get("construction_end_at")),
                            }
                            building_now.append(work)
                            works.append(work)
                    city_pos_lvl[cid] = pos_lvl
                    works.sort(key=lambda w: (w["end_at"] == 0, w["end_at"]))
                    city_facts.setdefault(str(ga.pk), []).append({
                        "id": cid,
                        "name": str(city.get("name") or cid),
                        "tradegood_icon": static(TRADEGOOD_ICON.get(tg, TRADEGOOD_ICON[0])),
                        "works": works,
                        "free_slots": free_slots,
                        "stock": {key: _si(city.get(key)) for key in RESOURCE_KEYS},
                    })
                    # ordena instancias por nivel desc para exibir badges consistentes
                    for insts in bid_instances.values():
                        insts.sort(key=lambda r: -r["level"])
                    reorder_key = f"{ga.pk}:{cid}"
                    slots, ready = _reorder_layout(city)
                    reorder_layouts[reorder_key] = {
                        "ga_id": str(ga.pk),
                        "ga_name": ga.name or ga.server_id,
                        "city_id": cid,
                        "city_name": city.get("name") or cid,
                        "ready": ready,
                        "slots": slots,
                        "bg": city_map.background_tiles(_si(city.get("phase"), 4), bool(city.get("is_capital"))),
                    }
                    lm_rows.append({
                        "city_name": city.get("name") or cid,
                        "tradegood_icon": static(TRADEGOOD_ICON.get(tg, TRADEGOOD_ICON[0])),
                        "bid_instances": bid_instances,
                        "reorder_key": reorder_key,
                        "game_account_id": str(ga.pk),
                    })
                level_lookup[str(ga.pk)] = city_pos_lvl
                level_lookup_cities[str(ga.pk)] = ga_city_list
                if lm_rows:
                    level_map.append({
                        "ga_name": ga.name or ga.server_id,
                        "account": acct.label,
                        "rows": lm_rows,
                    })

        # Colunas globais: uniao de TODOS os edificios de todas as contas, ordem
        # canonica (Prefeitura sempre primeira) — mesmas colunas em todas as
        # tabelas para preencher a largura de forma uniforme.
        global_bids: set[str] = set()
        for acc in level_map:
            for row in acc["rows"]:
                global_bids.update(row["bid_instances"].keys())
        order_index = {b: i for i, b in enumerate(GLOBAL_BUILDING_ORDER)}
        col_ids = sorted(
            global_bids,
            key=lambda b: (order_index.get(b, len(GLOBAL_BUILDING_ORDER)), _building_name(b)),
        )
        level_columns = [{"building_id": b, "name": _building_name(b)} for b in col_ids]
        for acc in level_map:
            for row in acc["rows"]:
                cells = []
                for b in col_ids:
                    insts = row["bid_instances"].get(b, [])
                    cells.append({
                        "instances": insts,
                        "count": len(insts),
                        "total": sum(i["level"] for i in insts),
                        "any_upgrading": any(i["is_upgrading"] for i in insts),
                    })
                row["cells"] = cells

        building_now.sort(key=lambda r: (r["end_at"] == 0, r["end_at"]))

        # --- Planos ativos (jobs 1002) ---
        plans: list[dict] = []
        pending_by_city: dict[tuple[str, str], list[dict]] = {}   # (ga, city) -> what the plans still build there
        plan_of_city: dict[tuple[str, str], str] = {}
        in_plan_order: set[tuple[str, str]] = set()               # cities whose plan builds strictly in its own order
        plan_of_ga: dict[str, str] = {}
        jobs = list(
            Job.objects.filter(action_code=1002, status__in=ACTIVE_PLAN_STATUSES)
            .select_related("game_account", "account")
            .order_by("-created_at")
        )
        # the last thing each plan complained about, as the "why" of a plan that is not moving
        issues: dict[str, dict] = {}
        if jobs:
            recent = JobLog.objects.filter(
                job__in=jobs, level__in=("warn", "warning", "error"), created_at__gte=timezone.now() - timedelta(hours=24),
            ).order_by("job_id", "-created_at").values_list("job_id", "message", "created_at")
            for job_id, message, created_at in recent:
                issues.setdefault(str(job_id), {
                    "text": str(message)[:220],
                    "ago": human_span((timezone.now() - created_at).total_seconds()),
                })
        for job in jobs:
            try:
                inp = json.loads(job.inputs_json or "{}")
            except (ValueError, TypeError):
                inp = {}
            if not inp.get("construction_plan_json"):
                continue
            ga_id = str(job.game_account_id or "")
            overview = board.plan_overview(inp, level_lookup.get(ga_id, {}))
            plan_of_ga.setdefault(ga_id, str(job.pk))
            pending_steps: list[dict] = []
            for entry in overview["pending"]:
                key = (ga_id, entry["city_id"])
                pending_by_city.setdefault(key, []).append(entry)
                plan_of_city.setdefault(key, str(job.pk))
                if (inp.get("queue_strategy") or "eta_first") == "fifo":
                    in_plan_order.add(key)
                pending_steps.append({
                    "id": entry["index"],
                    "p_order": entry["p_order"] if entry["priority"] else 0,
                    "p_level": entry["p_level"] if entry["priority"] else 0,
                    "city_id": entry["city_id"],
                    "city_name": entry["city_name"],
                    "icon": _building_icon(entry["building"]),
                    "name": _building_name(entry["building"]),
                    "cur": entry["cur"],
                    "target": entry["target"],
                })
            build_seconds = overview["build_seconds"]
            strategy = inp.get("queue_strategy") or "eta_first"
            next_run = job.scheduled_for
            plans.append({
                "job_id": str(job.pk),
                "ga_name": (job.game_account.name if job.game_account else "") or "",
                "account": job.account.label if job.account_id else "",
                "city_names": sorted({city["city_name"] for city in overview["cities"].values()}),
                "cities_total": len(overview["cities"]),
                "cities_pending": sum(1 for city in overview["cities"].values() if city["pending"]),
                "cities_waiting": 0,        # set below, once every city has its state
                "total": overview["total"], "done": overview["done"], "pct": overview["pct"],
                "remaining_levels": overview["remaining_levels"],
                "next_steps": [_step_chip(entry) for entry in overview["pending"][:4]],
                "pending_steps": pending_steps,
                "steps_id": f"cs-{job.pk}-steps",
                "cities_id": f"cs-{job.pk}-cities",
                "ga_cities": [{"id": str(c.get("id")), "name": str(c.get("name") or c.get("id"))}
                              for c in (level_lookup_cities.get(ga_id) or [])],
                "eta_seconds": build_seconds,
                "eta_label": _fmt_eta(build_seconds) if build_seconds else "",
                "finish_at": timezone.localtime(timezone.now() + timedelta(seconds=build_seconds)) if build_seconds else None,
                "remaining_cost": _resource_chips(overview["remaining_cost"]),
                "strategy": strategy,
                "strategy_label": {"smart": "Balanceado", "eta_first": "Menor ETA", "fifo": "Ordem do plano"}.get(strategy, strategy),
                "status": job.status,
                "running": job.status == "running",
                "next_run_in": human_span((next_run - timezone.now()).total_seconds()) if next_run and next_run > timezone.now() else "",
                "issue": issues.get(str(job.pk)),
                "missing": [],              # what its stopped cities need to start; set below
                "missing_total": 0,
            })

        # --- Por conta: cada cidade com o seu estado (em obra, esperando recurso, na fila, ociosa) ---
        board_accounts: list[dict] = []
        idle_groups: list[dict] = []
        totals = Counter()
        for ga_id, cities in city_facts.items():
            rows = []
            for city in cities:
                key = (ga_id, city["id"])
                pending = sorted(
                    pending_by_city.get(key, []),
                    key=lambda entry: (not entry["priority"], entry["p_order"] if entry["priority"] else 0, entry["idx"]),
                )
                # N-103: "falta" is what the city needs to START its next building, and only when
                # it is not building. (The reservations add up the next level of every pending
                # building, each against the whole stock: a number that answers no question.)
                # a plan in "ordem do plano" waits for its first step; the others start whatever fits first
                # (N-108) a prioritized step is the only thing the city builds until it gets there
                candidates = pending[:1] if (key in in_plan_order or (pending and pending[0]["priority"])) else pending
                gap = board.cheapest_start(candidates, city["stock"]) if pending and not city["works"] else None
                missing = gap["missing"] if gap else {}
                state = board.city_state(working=bool(city["works"]), has_pending=bool(pending), has_shortfall=bool(missing))
                next_step = None
                if gap:
                    next_step = {**_step_chip(gap["entry"]), "target": gap["entry"]["cur"] + 1}
                rows.append({
                    "id": city["id"], "name": city["name"], "tradegood_icon": city["tradegood_icon"],
                    "state": state, "state_label": board.CITY_STATE_LABELS[state],
                    "work": city["works"][0] if city["works"] else None,
                    "next": next_step,
                    "queue": [_step_chip(entry) for entry in pending[:3]],
                    "queue_more": max(0, len(pending) - 3),
                    "missing": _resource_chips(missing),
                    "missing_raw": missing,
                    "free_slots": city["free_slots"],
                    "job_id": plan_of_city.get(key, ""),
                })
            counts = board.account_counts(rows)
            totals.update({name: counts[name] for name in ("total", "working", "waiting", "queued", "idle")})
            meta = ga_meta.get(ga_id, {})
            entry = {
                "ga_id": ga_id, "name": meta.get("name", ""), "account": meta.get("account", ""),
                "cities": sorted(rows, key=lambda row: (board.CITY_STATE_ORDER[row["state"]], row["name"].lower())),
                "counts": counts,
                "job_id": plan_of_ga.get(ga_id, ""),
                "all_idle": counts["total"] > 0 and counts["idle"] == counts["total"],
            }
            if entry["all_idle"]:
                idle_groups.append(entry)
            else:
                board_accounts.append(entry)
        waiting_rows = [
            (entry, row) for entry in board_accounts for row in entry["cities"] if row["state"] == board.CITY_WAITING
        ]
        waiting_by_job = Counter(row["job_id"] for _entry, row in waiting_rows if row["job_id"])
        missing_by_job: dict[str, Counter] = {}
        missing_all: Counter = Counter()
        for _entry, row in waiting_rows:
            missing_all.update(row["missing_raw"])
            if row["job_id"]:
                missing_by_job.setdefault(row["job_id"], Counter()).update(row["missing_raw"])
        for plan in plans:
            plan["cities_waiting"] = waiting_by_job.get(plan["job_id"], 0)
            plan_missing = missing_by_job.get(plan["job_id"], {})
            plan["missing"] = _resource_chips(plan_missing)
            plan["missing_total"] = sum(plan_missing.values())
        board_accounts.sort(key=lambda entry: (-entry["counts"]["waiting"], entry["name"].lower()))
        idle_groups.sort(key=lambda entry: (-entry["counts"]["total"], entry["name"].lower()))
        # cities that are idle inside an account that does have something going on
        idle_strays = [
            {"ga_id": entry["ga_id"], "ga_name": entry["name"], "name": row["name"], "tradegood_icon": row["tradegood_icon"],
             "free_slots": row["free_slots"]}
            for entry in board_accounts for row in entry["cities"] if row["state"] == board.CITY_IDLE
        ]

        # --- Cidades paradas por recurso: o que cada uma precisa para comecar a proxima obra ---
        bottleneck_rows = [
            {
                "ga_name": entry["name"], "account": entry["account"], "city_name": row["name"],
                "next": row["next"], "items": row["missing"], "total": sum(row["missing_raw"].values()),
                "job_id": row["job_id"],
            }
            for entry, row in waiting_rows
        ]
        bottleneck_rows.sort(key=lambda r: -r["total"])

        max_reserved = max([reserved_total[k] for k in RESOURCE_KEYS] + [1])

        ctx.update({
            "now_ts": now,
            "kpi_building_now": len(building_now),
            "kpi_active_plans": len(plans),
            "kpi_next_eta": next((b["end_at"] for b in building_now if b["end_at"] > now), 0),
            "kpi_cities": {name: totals[name] for name in ("total", "working", "waiting", "queued", "idle")},
            "kpi_plans_waiting": sum(1 for plan in plans if plan["missing_total"]),
            "reserved_total": [{"key": k, "label": RESOURCE_META[k]["label"],
                                "icon": static(RESOURCE_META[k]["icon"]),
                                "reserved": reserved_total[k], "shortfall": shortfall_total[k],
                                "reserved_text": compact(reserved_total[k]), "shortfall_text": compact(shortfall_total[k]),
                                "pct": round(reserved_total[k] / max_reserved * 100)}
                               for k in RESOURCE_KEYS],
            "kpi_shortfall_total": sum(missing_all.values()),
            "kpi_shortfall_text": compact(sum(missing_all.values())),
            "kpi_missing": _resource_chips(missing_all),
            "building_now": building_now,
            "plans": plans,
            "board_accounts": board_accounts,
            "idle_groups": idle_groups,
            "idle_strays": idle_strays,
            "bottlenecks": bottleneck_rows,
            "level_map": level_map,
            "level_columns": level_columns,
            "reorder_layouts": reorder_layouts,
            "city_sprites": city_map.sprite_table(),
            "res_icons": {k: static(RESOURCE_META[k]["icon"]) for k in RESOURCE_KEYS},
        })
        return ctx
