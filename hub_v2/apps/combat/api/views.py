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

        loot_json       = data.get("loot_json") or {}
        attacker_losses = data.get("attacker_losses") or {}
        defender_losses = data.get("defender_losses") or {}
        total_loot      = int(data.get("total_loot") or sum(int(v) for v in loot_json.values()))

        defaults = {
            "game_account":   ga,
            "combat_type":    str(data.get("combat_type") or "land").strip(),
            "result":         str(data.get("result") or "").strip(),
            "combat_date":    combat_date,
            "total_rounds":   int(data.get("total_rounds") or 1),
            "source_city_id":  str(data.get("source_city_id") or "").strip(),
            "source_city_name": str(data.get("source_city_name") or "").strip(),
            "target_city_id":  str(data.get("target_city_id") or "").strip(),
            "target_city_name": str(data.get("target_city_name") or "").strip(),
            "target_owner":    str(data.get("target_owner") or "").strip(),
            "target_owner_id": str(data.get("target_owner_id") or "").strip(),
            "loot_json":       loot_json,
            "total_loot":      total_loot,
            "attacker_losses": attacker_losses,
            "defender_losses": defender_losses,
            "summary_html":    str(data.get("summary_html") or ""),
            "detailed_html":   str(data.get("detailed_html") or ""),
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
