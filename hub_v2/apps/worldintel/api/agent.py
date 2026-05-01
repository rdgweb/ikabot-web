from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Account, GameAccount
from core.auth.backends import AgentTokenAuthentication
from core.auth.permissions import IsAgent

from ..models import WorldDump, WorldDumpCity, WorldDumpIsland, WorldDumpPlayer
from .serializers import WorldDumpCreateSerializer, WorldDumpAppendSerializer


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

        # Update dump counters
        dump.island_count = WorldDumpIsland.objects.filter(dump=dump).count()
        dump.city_count = (dump.city_count or 0) + city_total
        dump.player_count = WorldDumpPlayer.objects.filter(dump=dump).count()
        dump.save(update_fields=["island_count", "city_count", "player_count", "updated_at"])

    return city_total, len(merged_scores)


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
