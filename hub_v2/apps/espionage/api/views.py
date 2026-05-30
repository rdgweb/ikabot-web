"""
Agent API: salva batch de relatórios de espionagem.

POST /api/agent/espionage/reports/
Auth: AgentTokenAuthentication + IsAgent
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from apps.settings_app.models import AppSetting
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from ..models import SpyReport
from .serializers import SpyReportsSaveSerializer

logger = logging.getLogger(__name__)

_RESOURCE_NAMES = {
    "Madeira": "wood", "Wood": "wood",
    "Vinho": "wine", "Wine": "wine",
    "Mármore": "marble", "Marble": "marble",
    "Cristal": "glass", "Crystal": "glass", "Glass": "glass",
    "Enxofre": "sulfur", "Sulfur": "sulfur",
}

# Frontline/siege unit names (PT-BR + EN + advisor cssClass) — case-insensitive
_FRONTLINE_UNITS = {
    # EN/cssClass
    "hoplite", "swordsman", "spearman", "viking", "spartan", "marksman",
    # PT-BR (snapshot.military.by_city.troops keys)
    "hoplita", "espadachim", "lanceiro", "carabineiro", "fundeiro", "arqueiro",
    "gigante a vapor", "girocóptero", "girocoptero",
}
_SIEGE_UNITS = {
    "ram", "catapult", "mortar", "balloon", "balloonbombardier",
    "aríete", "ariete", "catapulta", "morteiro", "balão-bombardeiro", "balao-bombardeiro",
    "balão bombardeiro", "balao bombardeiro",
}

# PT-BR/EN nome de unidade → unit_id (UNIT_STATS)
_NAME_TO_ID: dict[str, int] = {
    "Fundeiro": 301, "Espadachim": 302, "Hoplita": 303, "Carabineiro": 304,
    "Morteiro": 305, "Catapulta": 306, "Aríete": 307, "Ariete": 307,
    "Gigante a Vapor": 308, "Balão-Bombardeiro": 309, "Balao-Bombardeiro": 309,
    "Balão Bombardeiro": 309,
    "Cozinheiro": 310, "Médico": 311, "Medico": 311, "Girocóptero": 312,
    "Girocoptero": 312, "Arqueiro": 313, "Lanceiro": 315,
    # Navais
    "Trireme": 210, "Lança-Chamas": 211, "Submergível": 212, "Barco Balista": 213,
    "Barco Catapulta": 214, "Barco Morteiro": 215, "Aríete a Vapor": 216,
    "Lança-Foguetes": 217, "Lancha Rápida": 218, "Porta-balões": 219, "Reparador": 220,
}


def _troops_dict_to_ids(troops: dict) -> dict[int, int]:
    """Converte {nome_pt: qty} ou {id: qty} → {unit_id: qty}."""
    out: dict[int, int] = {}
    for k, v in (troops or {}).items():
        try:
            qty = int(v)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        try:
            uid = int(k)
        except (TypeError, ValueError):
            uid = _NAME_TO_ID.get(str(k))
        if uid:
            out[uid] = out.get(uid, 0) + qty
    return out


def _parse_enemy_intel(target_city_id: str, target_owner_id: str, server_id: str) -> dict:
    """Lê reports mission 7 + 26 do alvo + invenções do owner → enemy_units + levels."""
    enemy_units: dict[int, int] = {}
    enemy_off = 0
    enemy_def = 0
    needs_blockade = False

    # Mission 7 — tropas terrestres + warships
    r7 = SpyReport.objects.filter(
        target_city_id=target_city_id, mission_id=7,
        game_account__server_id=server_id,
    ).order_by("-created_at").first()
    if r7 and r7.data_json:
        for cat in (r7.data_json.get("troops_data") or []):
            if not isinstance(cat, dict): continue
            cat_type = (cat.get("category_type") or "").lower()
            is_naval = "naval" in cat_type or "naval" in (cat.get("category","") or "").lower()
            for u in (cat.get("units") or []):
                name = str(u.get("name") or "").strip()
                cnt = int(u.get("count") or 0)
                if cnt <= 0: continue
                if is_naval:
                    needs_blockade = True
                else:
                    uid = _NAME_TO_ID.get(name)
                    if uid:
                        enemy_units[uid] = enemy_units.get(uid, 0) + cnt

    # Mission 26 — upgrades (offensive/defensive max levels)
    r26 = SpyReport.objects.filter(
        target_owner_id=target_owner_id, mission_id=26,
        game_account__server_id=server_id,
    ).order_by("-created_at").first()
    if r26 and r26.data_json:
        for imp in (r26.data_json.get("workshop_improvements") or []):
            if not isinstance(imp, dict): continue
            enemy_off = max(enemy_off, int(imp.get("offensive") or 0))
            enemy_def = max(enemy_def, int(imp.get("defensive") or 0))

    return {
        "enemy_units": enemy_units,
        "enemy_off_level": enemy_off,
        "enemy_def_level": enemy_def,
        "needs_blockade": needs_blockade,
    }


def _simulate_attack(own_troops_pt: dict, enemy_intel: dict, wall_level: int = 1) -> dict:
    """Simula combate atacante (todas tropas da cidade) vs defensores conhecidos."""
    from apps.espionage.services.combat import calculate
    own_units = _troops_dict_to_ids(own_troops_pt)
    if not own_units:
        return {"can_win": False, "surviving_hp_pct": 0.0, "rounds_to_kill_enemy": 0}
    return calculate(
        enemy_units=enemy_intel.get("enemy_units") or {},
        enemy_off_level=enemy_intel.get("enemy_off_level") or 0,
        enemy_def_level=enemy_intel.get("enemy_def_level") or 0,
        wall_level=wall_level,
        own_units=own_units,
    )


def _recommend_units(own_troops_pt: dict, enemy_intel: dict, wall_level: int = 1) -> dict:
    """Recomenda mínimo de unidades pra vencer (com margem de segurança 3×)."""
    from apps.espionage.services.combat import recommend_army
    available = _troops_dict_to_ids(own_troops_pt)
    if not available:
        return {}
    rec = recommend_army(
        enemy_units=enemy_intel.get("enemy_units") or {},
        enemy_off_level=enemy_intel.get("enemy_off_level") or 0,
        enemy_def_level=enemy_intel.get("enemy_def_level") or 0,
        wall_level=wall_level,
        available_units=available,
        safety_margin=3.0,
    )
    return rec.get("recommended") or {}


def _recommend_fleet_for_blockade(own_fleet_pt: dict) -> dict:
    """Recomenda frota pra ocupar porto (blockade). Prioriza Aríete a Vapor, Trireme."""
    fleet_ids = _troops_dict_to_ids(own_fleet_pt)
    if not fleet_ids:
        return {}
    # Prioridade: Aríete a Vapor (216), Trireme (210), Pironavio (223)
    rec: dict[int, int] = {}
    for uid in (216, 210, 223, 211, 213, 214, 215):
        have = fleet_ids.get(uid, 0)
        if have > 0:
            # 2 mínimo, ou todos disponíveis se < 2
            rec[uid] = min(have, 2)
            if sum(rec.values()) >= 2:
                break
    return rec


# Inverso para mostrar nome
_ID_TO_NAME = {v: k for k, v in _NAME_TO_ID.items() if "Ariete" not in k or k == "Aríete"}
_ID_TO_NAME.update({
    301: "Fundeiro", 302: "Espadachim", 303: "Hoplita", 304: "Carabineiro",
    305: "Morteiro", 306: "Catapulta", 307: "Aríete", 308: "Gigante a Vapor",
    309: "Balão-Bombardeiro", 310: "Cozinheiro", 311: "Médico",
    312: "Girocóptero", 313: "Arqueiro", 315: "Lanceiro",
    # Navais
    210: "Trireme", 211: "Lança-Chamas", 212: "Submergível", 213: "Barco Balista",
    214: "Barco Catapulta", 215: "Barco Morteiro", 216: "Aríete a Vapor",
    217: "Lança-Foguetes", 218: "Lancha Rápida", 219: "Porta-balões", 220: "Reparador",
    223: "Pironavio",
})


def _choose_raid_account(target_city_id: str, target_owner_id: str, server_id: str, top_n: int = 5, enemy_intel: dict | None = None):
    """Escolhe contas elegíveis pra roubar.

    Filtros: tem mercantes + tropas frontline+siege, NÃO está em raid/blockade ativa pra esse alvo.
    Prioridade: conta que tem report mission 5 desse alvo (espionou).
    Retorna lista [{ga_id, ga_name, source_city_id, source_city_name, transporters, frontline, siege}]
    ordenada por score (melhor primeiro).
    """
    from apps.game.models import AccountSnapshot
    candidates = []
    for ga in GameAccount.objects.filter(server_id=server_id, active=True):
        snap = AccountSnapshot.objects.filter(game_account=ga).first()
        if not snap:
            continue
        # Skip se já tem raid/blockade ativa pra esse target_city_id
        movs = (snap.movements or {}).get("movement_details") or []
        in_raid = any(
            (m.get("target") or {}).get("cityId") == int(target_city_id or 0)
            and m.get("mission") in {"plunder", "blockade"}
            for m in movs
        )
        if in_raid:
            continue

        base = snap.base_snapshot or {}
        free_t = int(base.get("free_transporters") or 0)
        if free_t < 1:
            continue

        # Iterar cidades, pegar a que tem mais tropas frontline+siege
        cities = snap.cities or []
        military = snap.military or {}
        # by_city pode ser list ou dict
        _bc = military.get("by_city") if isinstance(military, dict) else None
        if isinstance(_bc, list):
            military_by_city = {str(x.get("city_id")): x for x in _bc if isinstance(x, dict)}
        elif isinstance(_bc, dict):
            military_by_city = _bc
        else:
            military_by_city = {}
        best = None
        for c in cities:
            if not isinstance(c, dict):
                continue
            cid = c.get("id")
            troops_holder = military_by_city.get(str(cid)) or military_by_city.get(cid) or {}
            if not isinstance(troops_holder, dict):
                continue
            troops = troops_holder.get("troops") or {}
            fleet  = troops_holder.get("fleet")  or {}
            frontline = sum(int(qty) for name, qty in troops.items() if str(name).lower() in _FRONTLINE_UNITS)
            siege = sum(int(qty) for name, qty in troops.items() if str(name).lower() in _SIEGE_UNITS)
            if frontline <= 0 or siege <= 0:
                continue

            # Simulação de combate (se temos intel do inimigo)
            sim = None
            recommended = None
            recommended_fleet = None
            if enemy_intel and enemy_intel.get("enemy_units"):
                sim = _simulate_attack(troops, enemy_intel, wall_level=1)
                if not sim.get("can_win"):
                    continue  # pula cidade que não vence
                recommended = _recommend_units(troops, enemy_intel, wall_level=1)
            if enemy_intel and enemy_intel.get("needs_blockade"):
                recommended_fleet = _recommend_fleet_for_blockade(fleet)

            # Score = sobra de HP após combate (se simulado) ou frontline+siege
            if sim:
                score = sim.get("surviving_hp_pct", 0) * 1000 + sim.get("own_total_hp", 0)
            else:
                score = frontline + siege * 2
            if best is None or score > best["score"]:
                best = {
                    "city_id": str(cid),
                    "city_name": c.get("name", ""),
                    "frontline": frontline,
                    "siege": siege,
                    "score": score,
                    "sim": sim,
                    "recommended": recommended,
                    "recommended_fleet": recommended_fleet,
                }
        if not best:
            continue

        # Bônus: conta que espionou esse alvo
        has_report = SpyReport.objects.filter(
            target_city_id=target_city_id, mission_id=5, game_account=ga,
        ).exists()
        score_total = best["score"] + (100000 if has_report else 0) + free_t

        candidates.append({
            "ga_id": str(ga.id),
            "ga_name": ga.name or ga.server_id,
            "source_city_id": best["city_id"],
            "source_city_name": best["city_name"],
            "transporters": free_t,
            "frontline": best["frontline"],
            "siege": best["siege"],
            "has_spied": has_report,
            "sim": best.get("sim"),
            "recommended": best.get("recommended"),
            "recommended_fleet": best.get("recommended_fleet"),
            "_score": score_total,
        })

    candidates.sort(key=lambda c: -c["_score"])
    return candidates[:top_n]


def _already_raiding_target(target_city_id: str, server_id: str) -> bool:
    """Retorna True se qualquer conta do server já tem raid/blockade ativo pra esse target.

    Lê snapshot.movements.movement_details (populado pelo ac=13). Considera ativo
    quando há `mission ∈ {plunder, blockade, occupy}` com target.cityId == target.
    """
    from apps.game.models import AccountSnapshot
    try:
        target_int = int(target_city_id)
    except (TypeError, ValueError):
        return False
    for snap in AccountSnapshot.objects.filter(game_account__server_id=server_id):
        movs = (snap.movements or {}).get("movement_details") or []
        if not isinstance(movs, list):
            continue
        for m in movs:
            if not isinstance(m, dict):
                continue
            mission = m.get("mission") or ""
            tgt = m.get("target") or {}
            if mission in {"plunder", "blockade", "occupy"} and tgt.get("cityId") == target_int:
                return True
    return False


def _stale_game_accounts(server_id: str, max_age_minutes: int = 60) -> list[str]:
    """Retorna ga_ids cujo AccountSnapshot está velho ou inexistente."""
    from apps.game.models import AccountSnapshot
    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    stale = []
    for ga in GameAccount.objects.filter(server_id=server_id, active=True):
        snap = AccountSnapshot.objects.filter(game_account=ga).first()
        if not snap or (snap.updated_at and snap.updated_at < cutoff):
            stale.append(str(ga.id))
    return stale


def _parse_resources_from_data(data_json: dict) -> dict[str, int]:
    """Extract resource amounts from spy report data_json."""
    resources: dict[str, int] = {}
    # Format 1: direct lowercase keys (current parser output: wood, wine, marble, crystal, sulfur)
    for k in ("wood", "wine", "marble", "crystal", "glass", "sulfur"):
        if k in data_json:
            try:
                resources[k] = int(data_json[k])
            except (ValueError, TypeError):
                pass
    # Format 2: nested stocks/resources dict with localized names
    stocks = data_json.get("stocks") or data_json.get("resources") or {}
    if isinstance(stocks, dict):
        for name, val in stocks.items():
            key = _RESOURCE_NAMES.get(str(name).strip())
            if key and key not in resources:
                raw = str(val).replace(".", "").replace(",", "").strip()
                try:
                    resources[key] = int(raw)
                except ValueError:
                    pass
    # Format 3: localized names at root level
    for name, key in _RESOURCE_NAMES.items():
        if name in data_json and key not in resources:
            raw = str(data_json[name]).replace(".", "").replace(",", "").strip()
            try:
                resources[key] = int(raw)
            except ValueError:
                pass
    return resources


def _parse_troops_from_data(data_json: dict) -> dict[str, int]:
    """Extract troop counts {unit_name: qty} from spy report data_json."""
    troops: dict[str, int] = {}
    # Format 1 (mission 7 — Observar tropas/frotas): troops_data = [{category, units: [{name, count}]}]
    td = data_json.get("troops_data")
    if isinstance(td, list):
        for cat in td:
            if not isinstance(cat, dict):
                continue
            for u in (cat.get("units") or []):
                if not isinstance(u, dict):
                    continue
                name = str(u.get("name") or "").strip()
                cnt = u.get("count") or 0
                if name and int(cnt) > 0:
                    troops[name] = troops.get(name, 0) + int(cnt)
        return troops

    # Format 2 (legacy): troops/army dict {unit_id: qty}
    raw = data_json.get("troops") or data_json.get("army") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                troops[str(int(k))] = int(v)
            except (ValueError, TypeError):
                pass
    return troops


class ScanRaidAlertsView(APIView):
    """POST /api/agent/espionage/scan-raid-alerts/

    Varre todos reports mission 5 (Inspecionar armazém) válidos do server,
    e dispara alertas Telegram para cidades acima do threshold que ainda não
    foram alertadas (ou foram alertadas mas chegou report novo).

    Botão "Ignorar" do Telegram marca report_id atual como ignorado — próximo
    report novo (id diferente) volta a alertar.

    Payload:
        game_account_id   str (required)  — ga que está disparando
        threshold         int (default 50000)
        raid_source_city  str (optional)  — usado no body do alerta
        raid_transporters int (optional)
        raid_max_trips    int (default 5)
        intel_ttl_hours   int (default 24)
    Returns: {"checked": N, "alerted": N, "skipped": N}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        from datetime import timedelta
        from django.utils import timezone

        from apps.accounts.models import GameAccount
        from apps.espionage.models import RaidAlertSent
        from apps.telegram.services.notifications import notify

        ga_id     = str(request.data.get("game_account_id") or "").strip()
        threshold = int(request.data.get("threshold") or 50000)
        raid_source_city  = str(request.data.get("raid_source_city") or "").strip()
        raid_transporters = int(request.data.get("raid_transporters") or 0)
        raid_max_trips    = int(request.data.get("raid_max_trips") or 5)
        ttl_hours = int(request.data.get("intel_ttl_hours") or 24)

        if not ga_id:
            return Response({"error": "game_account_id required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ga = GameAccount.objects.get(pk=ga_id)
        except GameAccount.DoesNotExist:
            return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

        cutoff = timezone.now() - timedelta(hours=ttl_hours)

        # Coletar reports mais recentes por (target_city_id, mission_id) — server scope.
        # Missão 26 é player-scope: vale qualquer cidade do mesmo owner.
        reports_qs = (
            SpyReport.objects
            .filter(
                mission_id__in=[5, 7, 26],
                created_at__gte=cutoff,
                game_account__server_id=ga.server_id,
            )
            .order_by("target_city_id", "mission_id", "-created_at")
        )

        latest_by_city: dict[str, dict] = {}  # {city_id: {5: report, 6: report, ...}}
        latest_m26_by_owner: dict[str, "SpyReport"] = {}  # missão 26 mais recente por owner
        for r in reports_qs:
            cid = r.target_city_id
            if not cid:
                continue
            slot = latest_by_city.setdefault(cid, {})
            if r.mission_id not in slot:
                slot[r.mission_id] = r
            if r.mission_id == 26 and r.target_owner_id and r.target_owner_id not in latest_m26_by_owner:
                latest_m26_by_owner[r.target_owner_id] = r

        # Para cada cidade com 5+6, anexar mission 26 do mesmo owner (player-scope)
        for cid, slot in latest_by_city.items():
            if 26 not in slot:
                report5 = slot.get(5)
                owner_id = (report5.target_owner_id if report5 else "")
                if owner_id and owner_id in latest_m26_by_owner:
                    slot[26] = latest_m26_by_owner[owner_id]

        # Exige TODAS: mission 5 (recursos) + 7 (tropas/frotas) + 26 (invenções/upgrades)
        complete_cities = {cid: m for cid, m in latest_by_city.items() if 5 in m and 7 in m and 26 in m}

        checked = 0
        alerted = 0
        skipped = 0
        refresh_needed: list[dict] = []

        for cid, missions in complete_cities.items():
            checked += 1
            report_res = missions[5]   # mission 5 — recursos
            report_grn = missions[7]   # mission 7 — movimento de tropas/frotas
            report_upg = missions[26]  # mission 26 — invenções/upgrades (player-scope)
            data_res = report_res.data_json or {}
            data_grn = report_grn.data_json or {}
            resources = _parse_resources_from_data(data_res)
            troops = _parse_troops_from_data(data_grn) or _parse_troops_from_data(data_res)
            total_res = sum(resources.values())
            if total_res < threshold:
                skipped += 1
                continue

            # Combina os ids dos 3 reports — qualquer um novo gera novo alerta
            combined_report_id = f"{report_res.report_id}+{report_grn.report_id}+{report_upg.report_id}"
            report = report_res  # usado para fields gerais (name, owner, etc.)

            ras, _ = RaidAlertSent.objects.get_or_create(
                game_account=ga,
                target_city_id=cid,
            )
            if ras.last_report_id == combined_report_id or ras.ignored_report_id == combined_report_id:
                skipped += 1
                continue
            report_id = combined_report_id

            # ── Antes do alerta, garantir dados frescos ─────────────────────
            # Se ainda não disparou refresh pra esse alvo nesse ciclo, pede ao
            # WorldSpy pra spawnar ac=2 + ac=13 em TODAS contas. Próximo ciclo
            # (snapshots atualizados) escolhe conta e envia Telegram.
            all_gas = list(
                GameAccount.objects
                .filter(server_id=ga.server_id, active=True)
                .values_list("id", flat=True)
            )
            if not ras.pending_since:
                ras.pending_since = timezone.now()
                ras.save(update_fields=["pending_since", "updated_at"])
                refresh_needed.append({
                    "target_city_id": cid,
                    "stale_game_account_ids": [str(g) for g in all_gas],
                })
                continue
            # pending_since velho (>30min) → re-spawna refresh (jobs perdidos?)
            if (timezone.now() - ras.pending_since).total_seconds() > 1800:
                ras.pending_since = timezone.now()
                ras.save(update_fields=["pending_since", "updated_at"])
                refresh_needed.append({
                    "target_city_id": cid,
                    "stale_game_account_ids": [str(g) for g in all_gas],
                })
                continue

            # Já tem raid/blockade ativo pra esse alvo? Pula sem alertar.
            if _already_raiding_target(cid, ga.server_id):
                skipped += 1
                continue

            # Todos snapshots frescos — escolher conta com base na simulação
            target_owner_id = report.target_owner_id or ""
            enemy_intel = _parse_enemy_intel(cid, target_owner_id, ga.server_id)
            choices = _choose_raid_account(cid, target_owner_id, ga.server_id, top_n=5, enemy_intel=enemy_intel)
            top = choices[0] if choices else None

            target_name  = report.target_city_name or cid
            target_owner = report.target_owner or "?"
            # Resolve island_id via dump
            island_id = ""
            try:
                from apps.worldintel.models import WorldDumpCity
                wdc = (WorldDumpCity.objects
                       .filter(game_city_id=cid, dump__game_account__server_id=ga.server_id)
                       .select_related("island")
                       .order_by("-dump__captured_at")
                       .first())
                if wdc and wdc.island:
                    island_id = wdc.island.island_id
            except Exception:
                pass
            _emoji = {"wood": "🪵", "wine": "🍷", "marble": "🏛️", "crystal": "💎", "glass": "💎", "sulfur": "💛"}
            _names = {"wood": "Madeira", "wine": "Vinho", "marble": "Mármore", "crystal": "Cristal", "glass": "Cristal", "sulfur": "Enxofre"}
            res_lines = "\n".join(
                f"  {_emoji.get(k,'•')} {_names.get(k,k)}: {v:,}"
                for k, v in resources.items() if v > 0
            )
            xy = f"[{report.target_x}:{report.target_y}]" if report.target_x is not None else ""
            if troops:
                troops_lines = "\n".join(f"  {qty}× {uid}" for uid, qty in troops.items())
                defense_block = f"⚔️ Defesa:\n{troops_lines}"
            else:
                defense_block = "⚔️ Defesa: sem tropas"

            # Bloco de sugestão da conta escolhida + ETA estimada
            def _eta_minutes(src_city_id: str, target_x, target_y) -> int:
                """Distância euclidiana entre source (snapshot) e target (xy do report)."""
                if target_x is None or target_y is None:
                    return 0
                from apps.game.models import AccountSnapshot
                # Encontrar source no snapshot
                for s in AccountSnapshot.objects.all():
                    for c in (s.cities or []):
                        if str(c.get("id")) == str(src_city_id):
                            sx, sy = c.get("x"), c.get("y")
                            if sx is None or sy is None: return 0
                            import math
                            d = math.sqrt((target_x - sx) ** 2 + (target_y - sy) ** 2)
                            # Aproximação: cada unidade de distância no mapa = ~6min
                            # (mercantes ~60 vel; valor ajustável)
                            return int(d * 6)
                return 0

            if top:
                eta_min = _eta_minutes(top["source_city_id"], report.target_x, report.target_y)
                eta_text = f"~{eta_min}min" if eta_min else "?"
                sim = top.get("sim") or {}
                if sim:
                    rounds = sim.get("rounds_to_kill_enemy", 0)
                    pct = sim.get("surviving_hp_pct", 0)
                    win_text = f"\n  ✅ Vitória: {pct:.0f}% HP restante / {rounds:.1f} rounds"
                else:
                    win_text = "\n  ⚠️ Sem intel completo (mission 7+26) — combate não simulado"
                rec_units = top.get("recommended") or {}
                if rec_units:
                    rec_lines = ", ".join(
                        f"{qty}× {_ID_TO_NAME.get(uid, str(uid))}"
                        for uid, qty in rec_units.items()
                    )
                    troops_text = f"\n🗡️ Tropas a enviar: {rec_lines}"
                else:
                    troops_text = ""
                rec_fleet = top.get("recommended_fleet") or {}
                if rec_fleet:
                    fleet_lines = ", ".join(
                        f"{qty}× {_ID_TO_NAME.get(uid, str(uid))}"
                        for uid, qty in rec_fleet.items()
                    )
                    troops_text += f"\n⚓ Frota p/ bloquear porto: {fleet_lines}"
                origin_block = (
                    f"🚢 Sugestão: {top['source_city_name']} ({top['ga_name']})\n"
                    f"  Mercantes: {top['transporters']} | Frontline: {top['frontline']} | Cerco: {top['siege']}\n"
                    f"  Viagem estimada: {eta_text}"
                    f"{win_text}"
                    f"{troops_text}"
                )
            else:
                origin_block = "⚠️ Nenhuma conta vence esse alvo (ou todas em raid/sem mercantes)"

            title = f"🏴‍☠️ Alvo rico: {target_name} {xy} ({target_owner})"
            body  = (
                f"{title}\n\n"
                f"💎 Total: {total_res:,}\n{res_lines}\n\n"
                f"{defense_block}\n\n"
                f"{origin_block}"
            )

            buttons: list[list[dict]] = []
            if top:
                raid_cb = f"raid_now:{cid}:{island_id}:{top['ga_id']}:{top['source_city_id']}"
                buttons.append([{"text": f"🏴 Roubar com {top['source_city_name']}", "callback_data": raid_cb}])
            # Overrides — até 4 outras opções
            for alt in choices[1:5]:
                cb = f"raid_now:{cid}:{island_id}:{alt['ga_id']}:{alt['source_city_id']}"
                buttons.append([{"text": f"↪ {alt['source_city_name']} ({alt['ga_name']})", "callback_data": cb}])
            buttons.append([{"text": "❌ Ignorar", "callback_data": f"raid_skip:{cid}:{report_id}"}])
            reply_markup = {"inline_keyboard": buttons}

            ok = notify(
                event_key="raid_alert",
                game_account=ga,
                title=title,
                body=body,
                reply_markup=reply_markup,
                metadata={
                    "target_city_id": cid,
                    "report_id":      report_id,
                    "total_resources": total_res,
                },
            )
            if ok:
                ras.last_report_id = report_id
                ras.last_alerted_at = timezone.now()
                ras.pending_since = None
                ras.save(update_fields=["last_report_id", "last_alerted_at", "pending_since", "updated_at"])
                alerted += 1
            else:
                skipped += 1

        return Response({
            "checked": checked,
            "alerted": alerted,
            "skipped": skipped,
            "refresh_needed": refresh_needed,
        })


class MissionsCoveredView(APIView):
    """GET /api/agent/espionage/missions-covered/

    Returns missions that already have a valid recent report for the given
    target. City-scope missions match by target_city_id; player-scope missions
    (3,7,10,21,24,25,26,27) match by target_owner_id (any city of the player).

    Params:
        target_city_id  required
        target_owner_id required for player-scope coverage
        game_account_id optional — scope by server
        intel_ttl_hours optional — default from AppSetting (24h)
    Returns: {"covered": [int, ...]}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    PLAYER_SCOPE = {3, 7, 10, 21, 24, 25, 26, 27}

    def get(self, request):
        target_city_id  = request.query_params.get("target_city_id", "").strip()
        target_owner_id = request.query_params.get("target_owner_id", "").strip()
        ga_id           = request.query_params.get("game_account_id", "").strip()
        try:
            ttl_hours = int(request.query_params.get("intel_ttl_hours") or 0)
        except Exception:
            ttl_hours = 0

        if not target_city_id and not target_owner_id:
            return Response({"covered": []})

        if not ttl_hours:
            try:
                from apps.settings_app.models import AppSetting
                ttl_hours = int(AppSetting.objects.get(key="spy_intel_ttl_hours").value)
            except Exception:
                ttl_hours = 24

        from django.utils import timezone
        from datetime import timedelta
        cutoff = timezone.now() - timedelta(hours=ttl_hours)

        # Server scope
        server_id = ""
        if ga_id:
            try:
                from apps.accounts.models import GameAccount
                ga = GameAccount.objects.get(pk=ga_id)
                server_id = ga.server_id
            except Exception:
                pass

        covered: set[int] = set()
        # City-scope: same city only
        city_qs = SpyReport.objects.filter(
            target_city_id=target_city_id,
            created_at__gte=cutoff,
            result_status__icontains="sucess",
        )
        if server_id:
            city_qs = city_qs.filter(game_account__server_id=server_id)
        for mid in city_qs.values_list("mission_id", flat=True).distinct():
            if mid: covered.add(int(mid))

        # Player-scope: any city of the same owner
        if target_owner_id:
            owner_qs = SpyReport.objects.filter(
                target_owner_id=target_owner_id,
                created_at__gte=cutoff,
                result_status__icontains="sucess",
                mission_id__in=self.PLAYER_SCOPE,
            )
            if server_id:
                owner_qs = owner_qs.filter(game_account__server_id=server_id)
            for mid in owner_qs.values_list("mission_id", flat=True).distinct():
                if mid: covered.add(int(mid))

        return Response({"covered": sorted(covered)})


class SpyIntelView(APIView):
    """GET /api/agent/espionage/intel/?target_city_id=X&game_account_id=Y

    Returns consolidated intel for target city from the latest valid spy reports.
    {
        "resources": {"wood": N, ...},
        "troops":    {"315": 4},
        "fleet":     {},
        "wall_level": 1,
        "last_updated": "ISO datetime",
    }
    Returns {} if no intel found.
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request):
        target_city_id = request.query_params.get("target_city_id", "").strip()
        ga_id          = request.query_params.get("game_account_id", "").strip()

        if not target_city_id:
            return Response({"error": "target_city_id required."}, status=status.HTTP_400_BAD_REQUEST)

        # Scope reports to the same server as the caller's game_account, NOT just
        # the same game_account — spy reports can come from any safehouse in the
        # multi-account config and consolidated intel is shared across them.
        server_id = ""
        if ga_id:
            try:
                from apps.accounts.models import GameAccount
                ga = GameAccount.objects.get(pk=ga_id)
                server_id = ga.server_id
            except Exception:
                pass

        qs = SpyReport.objects.filter(
            target_city_id=target_city_id,
            result_status__icontains="sucess",  # only successful reports
        ).order_by("-created_at")
        if server_id:
            qs = qs.filter(game_account__server_id=server_id)

        # Also include reports without explicit result_status filter as fallback
        if not qs.exists():
            qs = SpyReport.objects.filter(target_city_id=target_city_id).order_by("-created_at")
            if server_id:
                qs = qs.filter(game_account__server_id=server_id)

        if not qs.exists():
            return Response({})

        resources: dict[str, int] = {}
        troops:    dict[str, int] = {}
        fleet:     dict[str, int] = {}
        wall_level = 1
        last_updated = None

        # Consolidate from most recent reports per mission
        seen_missions: set[int | None] = set()
        for report in qs[:20]:
            mid = report.mission_id
            if mid in seen_missions:
                continue
            seen_missions.add(mid)

            data = report.data_json or {}
            if not last_updated or report.created_at > last_updated:
                last_updated = report.created_at

            parsed_res = _parse_resources_from_data(data)
            if parsed_res and not resources:
                resources = parsed_res

            parsed_troops = _parse_troops_from_data(data)
            if parsed_troops and not troops:
                troops = parsed_troops

            # Wall level from data_json if present
            if "wall_level" in data:
                try:
                    wall_level = int(data["wall_level"])
                except (TypeError, ValueError):
                    pass

        return Response({
            "resources":    resources,
            "troops":       troops,
            "fleet":        fleet,
            "wall_level":   wall_level,
            "last_updated": last_updated.isoformat() if last_updated else None,
        })


class SpyReportsSaveView(APIView):
    """
    POST /api/agent/espionage/reports/

    Upserta relatórios de espionagem vindos do runner.
    Retorna: {"saved": N, "new_count": N}
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        serializer = SpyReportsSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            ga = GameAccount.objects.get(pk=data["game_account_id"])
        except GameAccount.DoesNotExist:
            return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

        # Calcula expires_at com base na configuração global (default 48h)
        try:
            expiry_hours = int(AppSetting.objects.get(key="spy_report_expiry_hours").value)
        except (AppSetting.DoesNotExist, ValueError):
            expiry_hours = 48
        expires_at = timezone.now() + timedelta(hours=expiry_hours)

        saved = 0
        new_count = 0

        for report_data in data["reports"]:
            defaults = {
                "game_account": ga,
                "source_city_id": report_data.get("source_city_id") or "",
                "target_city_id": report_data.get("target_city_id") or "",
                "target_city_name": report_data.get("target_city_name") or "",
                "target_x": report_data.get("target_x"),
                "target_y": report_data.get("target_y"),
                "target_owner": report_data.get("target_owner") or "",
                "target_owner_id": report_data.get("target_owner_id") or "",
                "expires_at": expires_at,
                "mission_id": report_data.get("mission_id"),
                "mission_name": report_data.get("mission_name") or "",
                "subject": report_data.get("subject") or "",
                "status": report_data.get("status") or "",
                "result_status": report_data.get("result_status") or "",
                "agents_sent": report_data.get("agents_sent") or 0,
                "agents_lost": report_data.get("agents_lost") or 0,
                "decoys_sent": report_data.get("decoys_sent") or 0,
                "decoys_lost": report_data.get("decoys_lost") or 0,
                "report_html": report_data.get("report_html") or "",
                "report_text": report_data.get("report_text") or "",
                "data_json": report_data.get("data_json") or {},
                "date_str": report_data.get("date_str") or "",
                "is_read": report_data.get("is_read") or False,
            }

            obj, created = SpyReport.objects.get_or_create(
                report_id=report_data["report_id"],
                defaults=defaults,
            )

            if not created:
                for field, value in defaults.items():
                    setattr(obj, field, value)
                update_fields = list(defaults.keys()) + ["updated_at"]
                obj.save(update_fields=update_fields)
            else:
                new_count += 1

            saved += 1

        logger.info(
            "SpyReports: %d salvos (%d novos) para GA %s", saved, new_count, ga.pk
        )
        return Response(
            {"saved": saved, "new_count": new_count},
            status=status.HTTP_200_OK,
        )
