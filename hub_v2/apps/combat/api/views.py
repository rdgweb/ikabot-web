"""Agent API — save combat reports from the raid runner."""
from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import GameAccount
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from ..models import CombatReport

logger = logging.getLogger(__name__)


class CombatReportSaveView(APIView):
    """POST /api/agent/combat/reports/

    Upserts a combat report from the raid runner.
    Payload fields (all optional except combat_id and game_account_id):
        game_account_id, combat_id, combat_type, result, combat_date,
        total_rounds, source_city_id, source_city_name, target_city_id,
        target_city_name, target_owner, target_owner_id,
        loot_json, attacker_losses, defender_losses,
        summary_html, detailed_html
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        data = request.data
        game_account_id = str(data.get("game_account_id") or "").strip()
        combat_id_raw   = data.get("combat_id")

        if not combat_id_raw:
            return Response({"error": "combat_id required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            combat_id = int(combat_id_raw)
        except (TypeError, ValueError):
            return Response({"error": "combat_id must be integer."}, status=status.HTTP_400_BAD_REQUEST)

        ga = None
        if game_account_id:
            try:
                ga = GameAccount.objects.get(pk=game_account_id)
            except GameAccount.DoesNotExist:
                pass

        # Parse combat_date
        combat_date = None
        date_raw = str(data.get("combat_date") or "").strip()
        if date_raw:
            for fmt in ("%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    combat_date = timezone.make_aware(datetime.strptime(date_raw, fmt))
                    break
                except ValueError:
                    continue

        # Partial update: só sobrescreve campos que vêm no payload.
        # Permite chamada secundária pra atualizar só loot quando o parser primário falha.
        existing = CombatReport.objects.filter(combat_id=combat_id).first()

        def _get(key, default, transform=lambda x: x):
            raw = data.get(key)
            if raw is None or raw == "":
                return getattr(existing, key, default) if existing else default
            return transform(raw)

        loot_json_in = data.get("loot_json")
        loot_json = loot_json_in if isinstance(loot_json_in, dict) and loot_json_in else (
            existing.loot_json if existing else {}
        )
        total_loot_in = data.get("total_loot")
        total_loot = int(total_loot_in) if total_loot_in not in (None, "", 0) else (
            existing.total_loot if existing else sum(int(v or 0) for v in (loot_json or {}).values())
        )

        defaults = {
            "game_account":     ga if ga else (existing.game_account if existing else None),
            "combat_type":      _get("combat_type", "land", lambda x: str(x).strip()),
            "result":           _get("result", "", lambda x: str(x).strip()),
            "combat_date":      combat_date if combat_date else (existing.combat_date if existing else None),
            "total_rounds":     _get("total_rounds", 1, lambda x: int(x)),
            "source_city_id":   _get("source_city_id", "", lambda x: str(x).strip()),
            "source_city_name": _get("source_city_name", "", lambda x: str(x).strip()),
            "target_city_id":   _get("target_city_id", "", lambda x: str(x).strip()),
            "target_city_name": _get("target_city_name", "", lambda x: str(x).strip()),
            "target_owner":     _get("target_owner", "", lambda x: str(x).strip()),
            "target_owner_id":  _get("target_owner_id", "", lambda x: str(x).strip()),
            "loot_json":        loot_json,
            "total_loot":      total_loot,
            "attacker_losses": data.get("attacker_losses") or (existing.attacker_losses if existing else {}),
            "defender_losses": data.get("defender_losses") or (existing.defender_losses if existing else {}),
            "summary_html":    str(data.get("summary_html") or (existing.summary_html if existing else "")),
            "detailed_html":   str(data.get("detailed_html") or (existing.detailed_html if existing else "")),
        }

        obj, created = CombatReport.objects.update_or_create(
            combat_id=combat_id,
            defaults=defaults,
        )

        logger.info(
            "CombatReport %s %s (combat_id=%s result=%s loot=%s)",
            "criado" if created else "atualizado",
            obj.pk, combat_id, obj.result, total_loot,
        )

        return Response(
            {"ok": True, "combat_report_id": str(obj.pk), "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CombatRecommendView(APIView):
    """POST /api/agent/combat/recommend/

    Fonte única de simulação de combate. Usa apps.espionage.services.battle_land
    (mesmo simulador do alerta Telegram).

    Payload:
        enemy_units:        {"303": 162, ...} ou {303: 162, ...}
        available_units:    {"303": 100, ...} (tropas disponíveis no atacante)
        wall_level:         int (default 15)
        town_hall_level:    int (default 1) — define field slots
        attacker_upgrades:  {unit_id: {offensive: N, defensive: N}}
        defender_upgrades:  {unit_id: {offensive: N, defensive: N}}
        max_loss_pct:       float (default 30.0)
        reserve_pct:        float (default 50.0) — % extra além do mínimo (linha+reserva)

    Resposta:
        {
            "can_win": bool,
            "recommended": {unit_id: qty},
            "simulation": {winner, rounds, attacker_survivors_pct, ...}
        }
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        from apps.espionage.services.battle_land import recommend_attack_force

        def _coerce_units(raw) -> dict:
            return {int(k): int(v) for k, v in (raw or {}).items() if int(v or 0) > 0}

        def _coerce_upgrades(raw) -> dict:
            out = {}
            for k, v in (raw or {}).items():
                if isinstance(v, dict):
                    out[int(k)] = {
                        "offensive": int(v.get("offensive", 0) or 0),
                        "defensive": int(v.get("defensive", 0) or 0),
                    }
            return out

        try:
            enemy_units = _coerce_units(request.data.get("enemy_units"))
            available = _coerce_units(request.data.get("available_units"))
        except (TypeError, ValueError) as exc:
            return Response({"error": f"invalid units: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

        wall_level       = int(request.data.get("wall_level") or 15)
        town_hall_level  = int(request.data.get("town_hall_level") or 1)
        attacker_upgrades = _coerce_upgrades(request.data.get("attacker_upgrades"))
        defender_upgrades = _coerce_upgrades(request.data.get("defender_upgrades"))
        max_loss_pct     = float(request.data.get("max_loss_pct") or 30.0)
        reserve_pct      = float(request.data.get("reserve_pct") or 25.0)

        rec = recommend_attack_force(
            available_units=available,
            defender_units=enemy_units,
            attacker_upgrades=attacker_upgrades,
            defender_upgrades=defender_upgrades,
            town_hall_level=town_hall_level,
            wall_level=wall_level,
            max_loss_pct=max_loss_pct,
        )

        # Aplica reserva: envia linha+reserva (default 50% extra) clampado ao disponível
        recommended_raw = rec.get("recommended") or {}
        if reserve_pct > 0 and recommended_raw:
            multiplier = 1.0 + (reserve_pct / 100.0)
            recommended = {}
            for uid, qty in recommended_raw.items():
                have = available.get(uid, 0)
                recommended[uid] = min(have, int(qty * multiplier))
            rec["recommended"] = recommended

        # Sanitiza pra JSON (keys int → str)
        sim = rec.get("simulation") or {}
        return Response({
            "can_win":     bool(rec.get("can_win")),
            "recommended": {str(k): v for k, v in (rec.get("recommended") or {}).items()},
            "simulation": {
                "winner":               sim.get("winner"),
                "rounds":               sim.get("rounds"),
                "field_level":          sim.get("field_level"),
                "attacker_survivors_pct": sim.get("attacker_survivors_pct"),
                "defender_survivors_pct": sim.get("defender_survivors_pct"),
                "attacker_losses":      {str(k): v for k, v in (sim.get("attacker_losses") or {}).items()},
                "defender_losses":      {str(k): v for k, v in (sim.get("defender_losses") or {}).items()},
            },
            "note": rec.get("note", ""),
        })
