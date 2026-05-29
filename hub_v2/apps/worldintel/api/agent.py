from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Account, GameAccount
from apps.espionage.models import SpyReport
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from ..models import WorldDump, WorldDumpCity, WorldDumpIsland, WorldDumpPlayer
from .serializers import WorldDumpCreateSerializer, WorldDumpAppendSerializer


def _recount_dump(dump: WorldDump) -> None:
    dump.island_count = WorldDumpIsland.objects.filter(dump=dump).count()
    dump.city_count = WorldDumpCity.objects.filter(dump=dump, type="city").count()
    dump.player_count = WorldDumpPlayer.objects.filter(dump=dump).count()
    dump.save(update_fields=["island_count", "city_count", "player_count", "updated_at"])


def _save_islands_to_dump(dump: WorldDump, islands_payload: list) -> tuple[int, int]:
    """Save a batch of islands to an existing dump. Returns (city_count_added, player_count_added)."""
    city_total = 0
    merged_scores: dict[str, dict] = {}
    player_city_counts: dict[str, int] = {}
    player_ally: dict[str, tuple[str, str]] = {}
    city_models = []

    with transaction.atomic():
        for island_payload in islands_payload:
            island_cities = [c for c in (island_payload.get("cities") or []) if isinstance(c, dict)]
            island_city_count = sum(1 for c in island_cities if str(c.get("type") or "") == "city")
            island = WorldDumpIsland.objects.create(
                dump=dump,
                island_id=str(island_payload.get("island_id") or "").strip(),
                name=str(island_payload.get("name") or "").strip(),
                x=int(island_payload.get("x") or 0),
                y=int(island_payload.get("y") or 0),
                resource_type=int(island_payload.get("resource_type") or 0),
                resource_name=str(island_payload.get("resource_name") or "").strip(),
                resource_level=int(island_payload.get("resource_level") or 0),
                wood_level=int(island_payload.get("wood_level") or 0),
                miracle_type=int(island_payload.get("miracle_type") or 0),
                miracle_name=str(island_payload.get("miracle_name") or "").strip(),
                miracle_level=int(island_payload.get("miracle_level") or 0),
                city_count=island_city_count or int(island_payload.get("city_count") or 0),
                helios_built=bool(island_payload.get("helios_built")),
            )

            for owner_id, score in (island_payload.get("avatar_scores") or {}).items():
                if isinstance(score, dict) and owner_id not in merged_scores:
                    merged_scores[owner_id] = score

            for city in island_cities:
                owner_id = str(city.get("owner_id") or "").strip()
                if str(city.get("type") or "").strip() == "city":
                    city_total += 1
                    if owner_id:
                        player_city_counts[owner_id] = player_city_counts.get(owner_id, 0) + 1
                        if owner_id not in player_ally:
                            player_ally[owner_id] = (
                                str(city.get("ally_id") or "").strip(),
                                str(city.get("ally_tag") or "").strip(),
                            )
                city_models.append(
                    WorldDumpCity(
                        dump=dump,
                        island=island,
                        position=int(city.get("position") or 0),
                        game_city_id=str(city.get("id") or "").strip(),
                        name=str(city.get("name") or "").strip(),
                        owner_id=str(city.get("owner_id") or "").strip(),
                        owner_name=str(city.get("owner_name") or "").strip(),
                        ally_id=str(city.get("ally_id") or "").strip(),
                        ally_tag=str(city.get("ally_tag") or "").strip(),
                        level=int(city.get("level") or 0),
                        type=str(city.get("type") or "").strip(),
                        state=str(city.get("state") or "").strip(),
                        in_fight=bool(city.get("in_fight")),
                        has_treaties=bool(city.get("has_treaties")),
                        view_able=int(city.get("view_able") or 0),
                        infested_by_plague=bool(city.get("infested_by_plague")),
                        actions_json=city.get("actions") or [],
                    )
                )

        if city_models:
            WorldDumpCity.objects.bulk_create(city_models, batch_size=500)

            # Propagar estados frescos para dumps anteriores do mesmo servidor.
            # Quando uma cidade muda de "inactive" → "vacation" entre dumps,
            # o WorldSpy (que usa o dump mais recente) precisa ver o estado
            # atualizado imediatamente — sem esperar um novo dump completo.
            state_by_city = {
                c.game_city_id: c.state
                for c in city_models
                if c.game_city_id and c.state
            }
            for game_city_id, new_state in state_by_city.items():
                WorldDumpCity.objects.filter(
                    game_city_id=game_city_id,
                ).exclude(
                    dump=dump,  # não toca no dump que acabou de criar
                ).exclude(
                    state=new_state,  # só atualiza se mudou
                ).update(state=new_state)

        # Upsert player records (ignore_conflicts handles duplicates on append)
        player_models = []
        for owner_id, score in merged_scores.items():
            ally_id, ally_tag = player_ally.get(owner_id, ("", ""))
            owner_name = ""
            for cm in city_models:
                if cm.owner_id == owner_id:
                    owner_name = cm.owner_name
                    break
            player_models.append(
                WorldDumpPlayer(
                    dump=dump,
                    owner_id=owner_id,
                    owner_name=owner_name,
                    ally_id=ally_id,
                    ally_tag=ally_tag,
                    world_rank=int(score.get("place") or 0),
                    building_score=int(score.get("building_score") or 0),
                    research_score=int(score.get("research_score") or 0),
                    army_score=int(score.get("army_score") or 0),
                    trader_score=int(score.get("trader_score") or 0),
                    city_count=player_city_counts.get(owner_id, 0),
                )
            )
        if player_models:
            WorldDumpPlayer.objects.bulk_create(player_models, ignore_conflicts=True, batch_size=500)

        _recount_dump(dump)

    return city_total, len(merged_scores)


def _replace_islands_in_dump(dump: WorldDump, islands_payload: list) -> tuple[int, int]:
    island_ids = {
        str(item.get("island_id") or "").strip()
        for item in (islands_payload or [])
        if isinstance(item, dict) and str(item.get("island_id") or "").strip()
    }
    if island_ids:
        WorldDumpIsland.objects.filter(dump=dump, island_id__in=island_ids).delete()
    return _save_islands_to_dump(dump, islands_payload)


class WorldDumpCreateView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request):
        serializer = WorldDumpCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            account = Account.objects.get(pk=data["account_id"])
        except Account.DoesNotExist:
            return Response({"error": "Account not found."}, status=status.HTTP_404_NOT_FOUND)

        game_account = None
        if data.get("game_account_id"):
            try:
                game_account = GameAccount.objects.get(pk=data["game_account_id"])
            except GameAccount.DoesNotExist:
                return Response({"error": "GameAccount not found."}, status=status.HTTP_404_NOT_FOUND)

        dump_status = str(data.get("dump_status") or "complete")

        dump = WorldDump.objects.create(
            account=account,
            game_account=game_account,
            source_job_id=data.get("source_job_id"),
            scope_mode=str(data.get("scope_mode") or "own_islands"),
            title=str(data.get("title") or "").strip(),
            filters_json=data.get("filters") or {},
            status=dump_status,
            captured_at=timezone.now(),
        )

        islands_payload = data.get("islands") or []
        if islands_payload:
            _save_islands_to_dump(dump, islands_payload)
            if dump_status == "complete":
                pass  # already saved

        return Response(
            {
                "ok": True,
                "dump_id": str(dump.pk),
                "island_count": dump.island_count,
                "city_count": dump.city_count,
                "player_count": dump.player_count,
                "status": dump.status,
            },
            status=status.HTTP_201_CREATED,
        )


class WorldDumpAppendView(APIView):
    """POST /api/agent/world-dumps/{dump_id}/append/ — add more islands to an existing in-progress dump."""
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, dump_id):
        try:
            dump = WorldDump.objects.get(pk=dump_id)
        except WorldDump.DoesNotExist:
            return Response({"error": "Dump not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = WorldDumpAppendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        islands_payload = data.get("islands") or []
        is_final = bool(data.get("is_final", False))

        if islands_payload:
            _save_islands_to_dump(dump, islands_payload)

        if is_final:
            dump.status = "complete"
            dump.save(update_fields=["status", "updated_at"])

        return Response(
            {
                "ok": True,
                "dump_id": str(dump.pk),
                "island_count": dump.island_count,
                "city_count": dump.city_count,
                "player_count": dump.player_count,
                "status": dump.status,
            }
        )


class WorldSpyTargetsView(APIView):
    """
    GET /api/agent/worldintel/spy-targets/

    Retorna cidades do WorldDump mais recente que são alvos válidos para espionagem.
    Exclui automaticamente todas as cidades pertencentes a contas próprias (GameAccount).

    Query params:
        target_mode: all | inactive | vacation | ally_tag | owner_id (default: all)
        ally_tag:    filtro por tag de aliança (usado com target_mode=ally_tag)
        owner_id:    filtro por owner_id específico (usado com target_mode=owner_id)
        skip_if_valid: 1 | 0 — pula cidades com relatório válido para todas as missões
        missions:    IDs separados por vírgula (1,3,5,6,26) — só relevante com skip_if_valid
        limit:       máximo de cidades a retornar (default: 50, max: 200)
        game_account_id: UUID da conta que vai espionar (usado para pegar dump do mesmo server)
    """
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def get(self, request):
        from collections import defaultdict
        from django.db.models import OuterRef, Subquery, BigIntegerField, Q, Value
        from django.db.models.functions import Coalesce

        # ── Params ────────────────────────────────────────────────────────────
        skip_if_valid   = str(request.query_params.get("skip_if_valid") or "0") in ("1", "true", "yes")
        missions_raw    = str(request.query_params.get("missions") or "").strip()
        game_account_id = str(request.query_params.get("game_account_id") or "").strip()
        only_inactive   = str(request.query_params.get("only_inactive") or "1") in ("1", "true", "yes")
        try:
            intel_ttl_hours = int(request.query_params.get("intel_ttl_hours") or 0)
        except (TypeError, ValueError):
            intel_ttl_hours = 0

        def _int(key, default=None):
            try:
                v = request.query_params.get(key)
                return int(v) if v is not None else default
            except (TypeError, ValueError):
                return default

        x_min = _int("x_min"); x_max = _int("x_max")
        y_min = _int("y_min"); y_max = _int("y_max")
        max_total_score = _int("max_total_score", 0)
        max_army_score  = _int("max_army_score", 0)
        limit = min(200, max(1, _int("limit", 50)))

        # Parse mission list
        mission_ids = []
        for m in missions_raw.split(","):
            try:
                mission_ids.append(int(m.strip()))
            except (ValueError, TypeError):
                pass

        # ── Own city IDs (exclude always) ────────────────────────────────────
        own_lobby_ids = set(
            GameAccount.objects.filter(active=True).values_list("lobby_account_id", flat=True)
        )
        own_city_ids: set[str] = set()
        if own_lobby_ids:
            own_city_ids = set(
                WorldDumpCity.objects.filter(
                    game_city_id__gt="",
                    owner_id__in=own_lobby_ids,
                ).values_list("game_city_id", flat=True).distinct()
            )
        # Also pull from snapshots
        try:
            from apps.game.models import AccountSnapshot
            for snap in AccountSnapshot.objects.all():
                for city in ((snap.data or {}).get("cities") or []):
                    cid = str(city.get("id") or city.get("city_id") or "").strip()
                    if cid:
                        own_city_ids.add(cid)
        except Exception:
            pass

        # ── Latest dump (preferably from same server) ─────────────────────────
        dump_qs = WorldDump.objects.order_by("-captured_at")
        dump = None
        if game_account_id:
            try:
                ga = GameAccount.objects.get(pk=game_account_id)
                dump = dump_qs.filter(game_account__server_id=ga.server_id).first()
            except GameAccount.DoesNotExist:
                pass
        if not dump:
            dump = dump_qs.first()
        if not dump:
            return Response({"targets": [], "dump_id": None, "total": 0})

        # ── Base queryset ─────────────────────────────────────────────────────
        cities_qs = (
            WorldDumpCity.objects
            .filter(dump=dump, type="city", game_city_id__gt="")
            .select_related("island")
            .exclude(game_city_id__in=own_city_ids)
        )

        # State filter
        if only_inactive:
            cities_qs = cities_qs.filter(state="inactive")

        # Region filter
        if x_min is not None:
            cities_qs = cities_qs.filter(island__x__gte=x_min)
        if x_max is not None:
            cities_qs = cities_qs.filter(island__x__lte=x_max)
        if y_min is not None:
            cities_qs = cities_qs.filter(island__y__gte=y_min)
        if y_max is not None:
            cities_qs = cities_qs.filter(island__y__lte=y_max)

        # Score filters — join WorldDumpPlayer via subquery
        if max_total_score and max_total_score > 0:
            from apps.worldintel.models import WorldDumpPlayer as WDP
            player_total_sq = (
                WDP.objects
                .filter(dump=dump, owner_id=OuterRef("owner_id"))
                .annotate(ts=Coalesce("building_score", Value(0)) +
                              Coalesce("research_score", Value(0)) +
                              Coalesce("army_score", Value(0)))
                .values("ts")[:1]
            )
            cities_qs = cities_qs.annotate(
                player_total=Subquery(player_total_sq, output_field=BigIntegerField())
            ).filter(Q(player_total__isnull=True) | Q(player_total__lte=max_total_score))

        if max_army_score and max_army_score > 0:
            from apps.worldintel.models import WorldDumpPlayer as WDP2
            army_sq = (
                WDP2.objects
                .filter(dump=dump, owner_id=OuterRef("owner_id"))
                .values("army_score")[:1]
            )
            cities_qs = cities_qs.annotate(
                player_army=Subquery(army_sq, output_field=BigIntegerField())
            ).filter(Q(player_army__isnull=True) | Q(player_army__lte=max_army_score))

        cities = list(cities_qs[:limit * 3])

        # ── Lock global: excluir targets com job ac=15 ativo ─────────────────
        # Garante que nenhum alvo seja espionado por duas contas simultaneamente.
        # Também coleta source cities ocupadas para informar o runner 16.
        occupied_targets: set[str] = set()
        busy_source_cities: list[str] = []
        try:
            import json as _json
            from apps.jobs.models import Job
            active_spy_inputs = Job.objects.filter(
                action_code=15,
                status__in=["queued", "running", "scheduled"],
            ).values_list("inputs_json", flat=True)
            for inputs_str in active_spy_inputs:
                try:
                    inp = _json.loads(inputs_str) if isinstance(inputs_str, str) else {}
                    tcid = str(inp.get("target_city_id") or "").strip()
                    scid = str(inp.get("city_id") or "").strip()
                    if tcid:
                        occupied_targets.add(tcid)
                    if scid and scid not in busy_source_cities:
                        busy_source_cities.append(scid)
                except Exception:
                    pass
        except Exception:
            pass
        if occupied_targets:
            cities = [c for c in cities if c.game_city_id not in occupied_targets]

        # ── skip_if_valid ─────────────────────────────────────────────────────
        # Missões player-scope: resultado é global ao jogador (não varia por cidade).
        # Para essas, basta UMA cidade válida do mesmo owner_id → skip todas as outras.
        PLAYER_SCOPE_MISSIONS = frozenset({3, 7, 10, 21, 24, 25, 26, 27})

        if skip_if_valid and cities:
            from datetime import timedelta
            city_ids_check  = [c.game_city_id for c in cities]
            owner_ids_check = list({c.owner_id for c in cities if c.owner_id})
            now = timezone.now()

            if intel_ttl_hours and intel_ttl_hours > 0:
                valid_since = now - timedelta(hours=intel_ttl_hours)
                ttl_filter  = {"created_at__gte": valid_since}
            else:
                ttl_filter  = {"expires_at__gt": now}

            if mission_ids:
                mission_set = set(mission_ids)
                city_missions   = mission_set - PLAYER_SCOPE_MISSIONS   # per-city
                player_missions = mission_set & PLAYER_SCOPE_MISSIONS    # per-owner

                # Valid per city (city-scope missions)
                city_valid_map: dict[str, set] = defaultdict(set)
                if city_missions:
                    for row in SpyReport.objects.filter(
                        target_city_id__in=city_ids_check,
                        mission_id__in=list(city_missions),
                        **ttl_filter,
                    ).values("target_city_id", "mission_id").distinct():
                        city_valid_map[row["target_city_id"]].add(row["mission_id"])

                # Valid per owner (player-scope missions — any city of that owner counts)
                owner_valid_map: dict[str, set] = defaultdict(set)
                if player_missions and owner_ids_check:
                    for row in SpyReport.objects.filter(
                        target_owner_id__in=owner_ids_check,
                        mission_id__in=list(player_missions),
                        **ttl_filter,
                    ).values("target_owner_id", "mission_id").distinct():
                        owner_valid_map[row["target_owner_id"]].add(row["mission_id"])

                def _needs_spy(city) -> bool:
                    covered = (
                        city_valid_map.get(city.game_city_id, set())
                        | owner_valid_map.get(city.owner_id or "", set())
                    )
                    return not (mission_set <= covered)

                cities = [c for c in cities if _needs_spy(c)]
            else:
                has_valid = set(
                    SpyReport.objects.filter(
                        target_city_id__in=city_ids_check,
                        **ttl_filter,
                    ).values_list("target_city_id", flat=True).distinct()
                )
                cities = [c for c in cities if c.game_city_id not in has_valid]

        cities = cities[:limit]

        targets = [{
            "game_city_id": c.game_city_id,
            "owner_id":     c.owner_id,
            "owner_name":   c.owner_name,
            "city_name":    c.name,
            "island_id":    c.island.island_id if c.island else "",
            "x":            c.island.x if c.island else 0,
            "y":            c.island.y if c.island else 0,
            "state":        c.state or "",
            "ally_tag":     c.ally_tag or "",
        } for c in cities]

        # Return safehouse coordinates so the agent can sort targets by distance.
        source_coords: dict[str, dict] = {}
        source_cities_param = request.query_params.get("source_cities", "")
        if source_cities_param:
            sc_ids = [c.strip() for c in source_cities_param.split(",") if c.strip()]
            for sc in WorldDumpCity.objects.filter(
                dump=dump, game_city_id__in=sc_ids
            ).select_related("island"):
                if sc.island:
                    source_coords[sc.game_city_id] = {"x": sc.island.x, "y": sc.island.y}

        return Response({
            "targets":            targets,
            "dump_id":            str(dump.pk),
            "dump_captured_at":   dump.captured_at.isoformat() if dump.captured_at else None,
            "total":              len(targets),
            "busy_source_cities": busy_source_cities,
            "source_coords":      source_coords,
        })


class WorldDumpReplaceIslandsView(APIView):
    """POST /api/agent/world-dumps/{dump_id}/replace-islands/ — refresh islands in-place."""
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    def post(self, request, dump_id):
        try:
            dump = WorldDump.objects.get(pk=dump_id)
        except WorldDump.DoesNotExist:
            return Response({"error": "Dump not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = WorldDumpAppendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        islands_payload = data.get("islands") or []

        if islands_payload:
            _replace_islands_in_dump(dump, islands_payload)

        return Response(
            {
                "ok": True,
                "dump_id": str(dump.pk),
                "island_count": dump.island_count,
                "city_count": dump.city_count,
                "player_count": dump.player_count,
                "status": dump.status,
            }
        )


class UpdateCityStateView(APIView):
    """POST /api/agent/worldintel/cities/update-state/

    Called by the spy runner when it detects a city changed state
    (went into vacation mode, player left, etc.) after the dump was captured.

    Updates the state field on the most recent WorldDumpCity for that game_city_id
    across ALL dumps for the given game_account's server.

    Payload:
        game_city_id  str  — game ID of the city
        state         str  — new state ("vacation", "inactive_banned", "gone", "active")
        game_account_id str — (optional) to scope the dump lookup by server
        reason        str  — (optional) human-readable reason for the update
    """

    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]

    VALID_STATES = {"vacation", "inactive_banned", "gone", "active", "inactive"}

    def post(self, request):
        game_city_id    = str(request.data.get("game_city_id") or "").strip()
        new_state       = str(request.data.get("state") or "").strip()
        game_account_id = str(request.data.get("game_account_id") or "").strip()
        reason          = str(request.data.get("reason") or "").strip()

        if not game_city_id:
            return Response({"error": "game_city_id required."}, status=status.HTTP_400_BAD_REQUEST)
        if new_state not in self.VALID_STATES:
            return Response(
                {"error": f"state must be one of {sorted(self.VALID_STATES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Find most recent WorldDumpCity entries for this game_city_id
        qs = WorldDumpCity.objects.filter(game_city_id=game_city_id).order_by("-dump__captured_at")
        if game_account_id:
            # Scope to dumps from the same server as this game_account
            try:
                ga = GameAccount.objects.get(pk=game_account_id)
                qs = qs.filter(dump__game_account__server_id=ga.server_id)
            except (GameAccount.DoesNotExist, Exception):
                pass

        updated = qs.update(state=new_state)

        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            "UpdateCityState: game_city_id=%s → state=%s updated=%s reason=%s",
            game_city_id, new_state, updated, reason,
        )

        return Response({"ok": True, "game_city_id": game_city_id, "state": new_state, "updated_rows": updated})
