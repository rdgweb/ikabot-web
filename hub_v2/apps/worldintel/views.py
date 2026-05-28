from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import BigIntegerField, Count, ExpressionWrapper, F, Max, Min, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.accounts.models import GameAccount

from .models import WorldDump, WorldDumpCity, WorldDumpIsland, WorldDumpPlayer


class WorldIntelBaseView(LoginRequiredMixin, TemplateView):
    active_tab = "players"

    def _game_accounts(self):
        return (
            GameAccount.objects.select_related("account")
            .filter(active=True)
            .order_by("name", "server_id")
        )

    def _selected_game_account(self):
        ga_id = str(self.request.GET.get("ga") or "").strip()
        if not ga_id:
            return None
        return self._game_accounts().filter(pk=ga_id).first()

    def _selected_dump(self):
        dump_id = str(self.request.GET.get("dump") or "").strip()
        qs = WorldDump.objects.select_related("account", "game_account")
        selected_ga = self._selected_game_account()
        if selected_ga:
            qs = qs.filter(game_account=selected_ga)
        if dump_id:
            selected = qs.filter(pk=dump_id).first()
            if selected:
                return selected
        return qs.order_by("-captured_at").first()

    def _dump_choices(self, selected_ga):
        qs = WorldDump.objects.select_related("game_account").order_by("-captured_at")
        if selected_ga:
            qs = qs.filter(game_account=selected_ga)
        return qs[:30]

    def _base_context(self):
        selected_ga = self._selected_game_account()
        selected_dump = self._selected_dump()
        return {
            "active_tab": self.active_tab,
            "game_accounts": self._game_accounts(),
            "selected_game_account": selected_ga,
            "selected_dump": selected_dump,
            "dump_choices": self._dump_choices(selected_ga),
            "dump_job_create_url": "/jobs/new/?action=602" + (f"&ga={selected_ga.pk}" if selected_ga else ""),
        }

    @staticmethod
    def _monitor_url(game_account, island_ids):
        if not game_account or not island_ids:
            return ""
        params = urlencode(
            {
                "ga": str(game_account.pk),
                "action": "601",
                "input_monitor_mode": "islands",
                "input_extra_island_ids": ",".join(island_ids),
            }
        )
        return f"/jobs/new/form/?{params}"


class WorldDumpListView(WorldIntelBaseView):
    template_name = "worldintel/dumps.html"
    active_tab = "dumps"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = self._base_context()
        ctx.update(base)
        dumps = WorldDump.objects.select_related("account", "game_account").order_by("-captured_at")
        if base["selected_game_account"]:
            dumps = dumps.filter(game_account=base["selected_game_account"])
        ctx["dumps"] = dumps[:100]
        return ctx


class WorldDumpDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        dump = get_object_or_404(WorldDump, pk=pk)
        dump.delete()
        return HttpResponseRedirect(reverse("worldintel:dumps"))


class WorldPlayerListView(WorldIntelBaseView):
    template_name = "worldintel/players.html"
    active_tab = "players"

    def get_template_names(self):
        htmx = getattr(self.request, "htmx", None)
        # Return partial only for inline HTMX updates (not boosted navigation)
        if htmx and not getattr(htmx, "boosted", False):
            return ["worldintel/partials/players_table.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = self._base_context()
        ctx.update(base)
        current_dump = base["selected_dump"]
        q = str(self.request.GET.get("q") or "").strip()
        limit = min(250, max(25, int(str(self.request.GET.get("limit") or "100")) if str(self.request.GET.get("limit") or "").isdigit() else 100))
        page_num = max(1, int(str(self.request.GET.get("page") or "1")) if str(self.request.GET.get("page") or "").isdigit() else 1)
        ctx["q"] = q
        ctx["limit"] = limit
        ctx["limit_choices"] = [50, 100, 150, 250]
        if not current_dump:
            ctx["players"] = []
            ctx["has_scores"] = False
            ctx["page_obj"] = None
            return ctx

        # Try WorldDumpPlayer first (has scores)
        players_qs = WorldDumpPlayer.objects.filter(dump=current_dump)
        has_scores = players_qs.exists()

        if has_scores:
            players_qs = players_qs.annotate(
                score_sum=ExpressionWrapper(
                    F("building_score") + F("research_score") + F("army_score"),
                    output_field=BigIntegerField(),
                )
            )
            if q:
                players_qs = players_qs.filter(
                    Q(owner_name__icontains=q) | Q(ally_tag__icontains=q)
                )
            sort = str(self.request.GET.get("sort") or "rank")
            order_map = {
                "rank": "world_rank",
                "total": "-score_sum",
                "building": "-building_score",
                "army": "-army_score",
                "research": "-research_score",
                "cities": "-city_count",
                "name": "owner_name",
            }
            players_qs = players_qs.order_by(order_map.get(sort, "world_rank"))
            ctx["sort"] = sort
        else:
            # Fallback: aggregate from cities
            cities_qs = WorldDumpCity.objects.filter(dump=current_dump, type="city")
            if q:
                cities_qs = cities_qs.filter(
                    Q(owner_name__icontains=q)
                    | Q(ally_tag__icontains=q)
                    | Q(name__icontains=q)
                )
            players_qs = (
                cities_qs.values("owner_id", "owner_name", "ally_tag")
                .annotate(city_count=Count("id"), island_count=Count("island_id", distinct=True))
                .order_by("-city_count", "owner_name")
            )
            ctx["sort"] = "cities"

        paginator = Paginator(players_qs, limit)
        page_obj = paginator.get_page(page_num)
        ctx["players"] = list(page_obj.object_list)
        ctx["page_obj"] = page_obj
        ctx["has_scores"] = has_scores
        ctx["sort_options"] = [
            ("Rank", "rank"), ("Pontos", "total"), ("Construção", "building"), ("Exército", "army"),
            ("Pesquisa", "research"), ("Cidades", "cities"), ("Nome", "name"),
        ]

        # Actual city counts for this page (WorldDumpPlayer.city_count can be stale)
        if has_scores and ctx["players"]:
            page_owner_ids = [p.owner_id for p in ctx["players"] if p.owner_id]
            if page_owner_ids:
                actual_counts = dict(
                    WorldDumpCity.objects.filter(
                        dump=current_dump, owner_id__in=page_owner_ids, type="city"
                    )
                    .values("owner_id")
                    .annotate(c=Count("id"))
                    .values_list("owner_id", "c")
                )
                for player in ctx["players"]:
                    player.actual_city_count = actual_counts.get(player.owner_id, player.city_count)

        # Vacation/inactive state sets for player list icons
        player_list = ctx["players"]
        owner_ids = [
            (p.owner_id if hasattr(p, "owner_id") else p.get("owner_id"))
            for p in player_list
        ]
        owner_ids = [oid for oid in owner_ids if oid]
        if owner_ids:
            state_qs = (
                WorldDumpCity.objects.filter(dump=current_dump, owner_id__in=owner_ids)
                .exclude(state="")
                .values("owner_id", "state")
                .distinct()
            )
            vacation_ids = set()
            inactive_ids = set()
            inactive_banned_ids = set()
            for row in state_qs:
                if row["state"] == "vacation":
                    vacation_ids.add(row["owner_id"])
                elif row["state"] == "inactive_banned":
                    inactive_banned_ids.add(row["owner_id"])
                elif row["state"] == "inactive":
                    inactive_ids.add(row["owner_id"])
            ctx["vacation_owner_ids"] = vacation_ids
            ctx["inactive_owner_ids"] = inactive_ids
            ctx["inactive_banned_owner_ids"] = inactive_banned_ids

            # First valid spy target city per player (for "Enviar Espião" button)
            spy_targets_qs = (
                WorldDumpCity.objects.filter(
                    dump=current_dump, owner_id__in=owner_ids, type="city"
                )
                .exclude(game_city_id="")
                .exclude(game_city_id="-1")
                .select_related("island")
                .order_by("owner_id", "island__x", "island__y")
            )
            spy_target_map: dict = {}
            for city in spy_targets_qs:
                if city.owner_id not in spy_target_map:
                    spy_target_map[city.owner_id] = city
            ctx["spy_target_map"] = spy_target_map
        else:
            ctx["vacation_owner_ids"] = set()
            ctx["inactive_owner_ids"] = set()
            ctx["inactive_banned_owner_ids"] = set()
            ctx["spy_target_map"] = {}

        # Base query string (without sort/page) for sort links and pagination links
        qs_parts = []
        if current_dump:
            qs_parts.append(f"dump={current_dump.pk}")
        if base.get("selected_game_account"):
            qs_parts.append(f"ga={base['selected_game_account'].pk}")
        if q:
            qs_parts.append(f"q={q}")
        qs_parts.append(f"limit={limit}")
        ctx["base_qs"] = "&".join(qs_parts)
        return ctx


class WorldPlayerDetailView(WorldIntelBaseView):
    template_name = "worldintel/player_detail.html"
    active_tab = "players"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = self._base_context()
        ctx.update(base)
        current_dump = base["selected_dump"]
        if not current_dump:
            raise Http404("Nenhum dump disponivel.")

        owner_id = str(self.request.GET.get("owner_id") or "").strip()
        owner_name = str(self.request.GET.get("owner_name") or "").strip()
        if not owner_id and not owner_name:
            raise Http404("Player nao informado.")

        cities = WorldDumpCity.objects.filter(dump=current_dump, type="city").select_related("island")
        if owner_id:
            cities = cities.filter(owner_id=owner_id)
        else:
            cities = cities.filter(owner_name=owner_name)
        cities = cities.order_by("island__x", "island__y", "name")
        city_list = list(cities)
        if not city_list:
            raise Http404("Player nao encontrado neste dump.")

        first = city_list[0]
        island_ids = []
        seen_islands = set()
        for city in city_list:
            sid = str(city.island.island_id or "").strip()
            if sid and sid not in seen_islands:
                seen_islands.add(sid)
                island_ids.append(sid)

        player_score = WorldDumpPlayer.objects.filter(
            dump=current_dump, owner_id=first.owner_id
        ).first() if first.owner_id else None

        ctx["player"] = {
            "owner_id": first.owner_id,
            "owner_name": first.owner_name,
            "ally_tag": first.ally_tag,
            "city_count": len(city_list),
            "island_count": len(island_ids),
            "fight_count": sum(1 for city in city_list if city.in_fight),
            "vacation_count": sum(1 for city in city_list if city.state == "vacation"),
            "inactive_banned_count": sum(1 for city in city_list if city.state == "inactive_banned"),
            "inactive_count": sum(1 for city in city_list if city.state == "inactive"),
            "score": player_score,
        }
        ctx["cities"] = city_list
        ctx["monitor_url"] = self._monitor_url(current_dump.game_account, island_ids)
        spy_target = next(
            (c for c in city_list if c.game_city_id and c.game_city_id != "-1"),
            None,
        )
        ctx["spy_target"] = spy_target
        return ctx


class WorldCityDetailView(WorldIntelBaseView):
    """Detalhe de uma cidade específica do WorldDump + relatórios de espionagem vinculados."""

    template_name = "worldintel/city_detail.html"
    active_tab = "players"

    def get_context_data(self, **kwargs):
        from apps.espionage.models import SpyReport

        ctx = super().get_context_data(**kwargs)
        base = self._base_context()
        ctx.update(base)
        current_dump = base["selected_dump"]

        city_id = str(self.request.GET.get("city_id") or "").strip()
        if not city_id:
            raise Http404("city_id não informado.")

        city = WorldDumpCity.objects.select_related("island", "dump").filter(
            game_city_id=city_id
        ).order_by("-dump__captured_at").first()

        if not city:
            raise Http404("Cidade não encontrada no dump.")

        # Relatórios de espionagem para esta cidade (todos, ordenados por mais recente)
        spy_reports = SpyReport.objects.filter(
            target_city_id=city_id
        ).select_related("game_account", "game_account__account").order_by("-created_at")[:50]

        # Último relatório válido por missão
        from django.utils import timezone
        now = timezone.now()
        latest_by_mission: dict = {}
        for report in spy_reports:
            mid = report.mission_id
            if mid not in latest_by_mission:
                latest_by_mission[mid] = report

        ctx["city"] = city
        ctx["spy_reports"] = spy_reports
        ctx["latest_by_mission"] = latest_by_mission
        ctx["now"] = now

        # URL para criar job de espionagem nesta cidade
        owner_id = city.owner_id or ""
        owner_name = city.owner_name or ""
        ctx["spy_job_url"] = (
            f"/jobs/new/form/?action=15"
            f"&input_target_city_id={city.game_city_id}"
            f"&input_target_city_name={city.name}"
            f"&input_target_owner={owner_name}"
            f"&input_target_owner_id={owner_id}"
            f"&input_island_id={city.island.island_id}"
        )
        # Back to player detail
        ctx["player_url"] = (
            f"/intel/players/detail/?owner_id={owner_id}"
            + (f"&dump={current_dump.pk}" if current_dump else "")
            + (f"&ga={base['selected_game_account'].pk}" if base.get("selected_game_account") else "")
        )
        return ctx


class WorldIslandListView(WorldIntelBaseView):
    template_name = "worldintel/islands.html"
    active_tab = "islands"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = self._base_context()
        ctx.update(base)
        current_dump = base["selected_dump"]
        q = str(self.request.GET.get("q") or "").strip()
        resource = str(self.request.GET.get("resource") or "").strip()
        ctx["q"] = q
        ctx["resource"] = resource
        if not current_dump:
            ctx["islands"] = []
            return ctx

        islands = WorldDumpIsland.objects.filter(dump=current_dump).prefetch_related("cities")
        if q:
            islands = islands.filter(
                Q(name__icontains=q)
                | Q(miracle_name__icontains=q)
                | Q(cities__owner_name__icontains=q)
                | Q(cities__ally_tag__icontains=q)
            ).distinct()
        if resource.isdigit():
            islands = islands.filter(resource_type=int(resource))

        rows = []
        for island in islands.order_by("x", "y")[:300]:
            owners = []
            for city in island.cities.all():
                if city.type != "city" or not city.owner_name:
                    continue
                label = city.owner_name
                if city.ally_tag:
                    label += f" [{city.ally_tag}]"
                owners.append(label)
            rows.append(
                {
                    "island_id": island.island_id,
                    "name": island.name,
                    "x": island.x,
                    "y": island.y,
                    "resource_name": island.resource_name,
                    "miracle_name": island.miracle_name,
                    "city_count": island.city_count,
                    "owners": owners[:4],
                    "monitor_url": self._monitor_url(current_dump.game_account, [str(island.island_id)]),
                }
            )
        ctx["islands"] = rows
        return ctx
