"""Painel de Construcoes — visao agregada multi-conta.

Junta tres fontes:
  - Snapshot das cidades (edificios em obra agora + niveis atuais)
  - Jobs de Plano de Construcao (ac=1002) ativos + progresso derivado do snapshot
  - ConstructionResourceReservation (reservas locais e faltas por cidade/recurso)
"""
from __future__ import annotations

import json
import time

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch
from django.templatetags.static import static
from django.views.generic import TemplateView

from apps.accounts.models import Account, GameAccount
from apps.jobs.models import ConstructionResourceReservation, Job
from core.catalogs import get_building_info


RESOURCE_KEYS = ("wood", "wine", "marble", "crystal", "sulfur")
RESOURCE_META = {
    "wood": {"label": "Madeira", "icon": "game/resources/icon_wood.png"},
    "wine": {"label": "Vinho", "icon": "game/resources/icon_wine.png"},
    "marble": {"label": "Marmore", "icon": "game/resources/icon_marble.png"},
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

        # --- Reservas agregadas por ga -> city -> resource ---
        reservations: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
        reserved_total = {k: 0 for k in RESOURCE_KEYS}
        shortfall_total = {k: 0 for k in RESOURCE_KEYS}
        bottleneck_rows: list[dict] = []
        if ga_ids:
            for r in ConstructionResourceReservation.objects.filter(
                game_account_id__in=ga_ids, status="active"
            ):
                res = str(r.resource)
                bucket = (
                    reservations.setdefault(str(r.game_account_id), {})
                    .setdefault(str(r.city_id), {})
                    .setdefault(res, {"reserved_local": 0, "shortfall": 0})
                )
                bucket["reserved_local"] += _si(r.reserved_local_amount)
                bucket["shortfall"] += _si(r.shortfall_amount)
                if res in reserved_total:
                    reserved_total[res] += _si(r.reserved_local_amount)
                    shortfall_total[res] += _si(r.shortfall_amount)
                if _si(r.shortfall_amount) > 0:
                    bottleneck_rows.append({
                        "ga_id": str(r.game_account_id),
                        "city_name": r.city_name or r.city_id,
                        "resource": res,
                        "resource_label": RESOURCE_META.get(res, {}).get("label", res),
                        "resource_icon": static(RESOURCE_META.get(res, {}).get("icon", "game/resources/icon_wood.png")),
                        "shortfall": _si(r.shortfall_amount),
                    })

        # --- Snapshot: em obra agora + lookup de niveis ---
        building_now: list[dict] = []
        level_lookup: dict[str, dict[str, dict[str, int]]] = {}  # ga -> city -> {pos: lvl, bid: lvl}
        ga_meta: dict[str, dict] = {}
        level_map: list[dict] = []

        for acct in accounts:
            for ga in getattr(acct, "active_game_accounts", []):
                snap = getattr(ga, "snapshot", None)
                ga_meta[str(ga.pk)] = {"name": ga.name or ga.server_id, "account": acct.label}
                cities = _city_list(snap)
                city_pos_lvl: dict[str, dict[str, int]] = {}
                lm_rows = []
                for city in cities:
                    if not isinstance(city, dict):
                        continue
                    cid = str(city.get("id") or "")
                    try:
                        tg = int(city.get("tradegood_id") or city.get("produced_tradegood") or city.get("tradegood") or 0)
                    except (TypeError, ValueError):
                        tg = 0
                    pos_lvl = {"_pos": {}, "_bid": {}}
                    # cada bid pode ter varias instancias (ex: multiplos armazens)
                    bid_instances: dict[str, list[dict]] = {}
                    for b in city.get("buildings") or []:
                        if not isinstance(b, dict):
                            continue
                        bid = str(b.get("building") or "")
                        if not bid or bid == "empty":
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
                            building_now.append({
                                "ga_name": ga.name or ga.server_id,
                                "account": acct.label,
                                "city_name": city.get("name") or cid,
                                "tradegood_icon": static(TRADEGOOD_ICON.get(tg, TRADEGOOD_ICON[0])),
                                "building": bid,
                                "from_level": lvl,
                                "to_level": lvl + 1,
                                "end_at": _si(b.get("construction_end_at")),
                            })
                    city_pos_lvl[cid] = pos_lvl
                    # ordena instancias por nivel desc para exibir badges consistentes
                    for insts in bid_instances.values():
                        insts.sort(key=lambda r: -r["level"])
                    lm_rows.append({
                        "city_name": city.get("name") or cid,
                        "tradegood_icon": static(TRADEGOOD_ICON.get(tg, TRADEGOOD_ICON[0])),
                        "bid_instances": bid_instances,
                    })
                level_lookup[str(ga.pk)] = city_pos_lvl
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
        jobs = (
            Job.objects.filter(action_code=1002, status__in=ACTIVE_PLAN_STATUSES)
            .select_related("game_account", "account")
            .order_by("-created_at")
        )
        for job in jobs:
            try:
                inp = json.loads(job.inputs_json or "{}")
            except (ValueError, TypeError):
                inp = {}
            steps = inp.get("construction_plan_json") or []
            if not steps:
                continue
            ga_id = str(job.game_account_id or "")
            city_lookup = level_lookup.get(ga_id, {})
            total = len(steps)
            done = 0
            remaining_levels = 0
            next_steps: list[dict] = []
            city_names: set[str] = set()
            for s in steps:
                cid = str(s.get("city_id") or "")
                pos = str(s.get("building_position") or "")
                bid = str(s.get("building_id") or s.get("building_type") or "")
                target = _si(s.get("target_level"))
                if s.get("city_name"):
                    city_names.add(str(s.get("city_name")))
                cl = city_lookup.get(cid, {})
                cur = _si((cl.get("_pos") or {}).get(pos)) if pos and pos in (cl.get("_pos") or {}) else _si((cl.get("_bid") or {}).get(bid))
                if target and cur >= target:
                    done += 1
                else:
                    remaining_levels += max(0, target - cur)
                    if len(next_steps) < 4:
                        next_steps.append({
                            "icon": _building_icon(bid),
                            "name": _building_name(bid),
                            "cur": cur,
                            "target": target,
                        })
            summary = inp.get("construction_summary") or {}
            eta_seconds = _si(summary.get("adjusted_seconds"))
            missing = {k: _si(v) for k, v in (summary.get("missing") or {}).items() if _si(v) > 0}
            pct = round(done / total * 100) if total else 0
            plans.append({
                "job_id": str(job.pk),
                "ga_name": (job.game_account.name if job.game_account else "") or "",
                "account": job.account.label if job.account_id else "",
                "city_names": sorted(city_names),
                "total": total, "done": done, "pct": pct,
                "remaining_levels": remaining_levels,
                "next_steps": next_steps,
                "eta_seconds": eta_seconds,
                "eta_label": _fmt_eta(eta_seconds),
                "strategy_label": {"smart": "Balanceado", "eta_first": "Menor ETA", "fifo": "Ordem do plano"}.get(
                    inp.get("queue_strategy") or "eta_first", inp.get("queue_strategy") or "eta_first"),
                "status": job.status,
                "missing": [{"key": k, "label": RESOURCE_META.get(k, {}).get("label", k),
                             "icon": static(RESOURCE_META.get(k, {}).get("icon", "game/resources/icon_wood.png")),
                             "amount": v} for k, v in missing.items()],
            })

        for row in bottleneck_rows:
            meta = ga_meta.get(row["ga_id"], {})
            row["ga_name"] = meta.get("name", "")
            row["account"] = meta.get("account", "")
        bottleneck_rows.sort(key=lambda r: -r["shortfall"])

        max_reserved = max([reserved_total[k] for k in RESOURCE_KEYS] + [1])

        ctx.update({
            "now_ts": now,
            "kpi_building_now": len(building_now),
            "kpi_active_plans": len(plans),
            "kpi_next_eta": next((b["end_at"] for b in building_now if b["end_at"] > now), 0),
            "reserved_total": [{"key": k, "label": RESOURCE_META[k]["label"],
                                "icon": static(RESOURCE_META[k]["icon"]),
                                "reserved": reserved_total[k], "shortfall": shortfall_total[k],
                                "pct": round(reserved_total[k] / max_reserved * 100)}
                               for k in RESOURCE_KEYS],
            "kpi_shortfall_total": sum(shortfall_total.values()),
            "building_now": building_now,
            "plans": plans,
            "bottlenecks": bottleneck_rows,
            "level_map": level_map,
            "level_columns": level_columns,
        })
        return ctx
