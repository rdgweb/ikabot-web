"""What the first page of the hub shows (N-64 / N-102).

The page answers, in this order: does anything need me now, how is the empire,
what is happening and what comes next, is the automation healthy, how is each
account, and is the system itself fine. Everything comes from what the hub already
stores; nothing here talks to the game or changes anything.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import GameAccount, Node
from apps.game.models import AccountSnapshot
from apps.game.services.attacks import summary as attack_summary
from apps.game.services.gold import net_gold_income
from apps.game.services.vacation import read_vacation_state
from apps.jobs.models import Job, Workflow

RESOURCES = ("wood", "wine", "marble", "crystal", "sulfur")
# A snapshot older than this means the account is not being read.
STALE_SNAPSHOT = timedelta(hours=3)
FULL_WAREHOUSE_PCT = 95
# Spending more than it earns only matters when the gold is about to end.
GOLD_RUNWAY_WARN = timedelta(hours=48)
GOLD_RUNWAY_BAD = timedelta(hours=12)
LEVELS = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
# N-126: one cache per organization (the scope of the request), never one for everybody
CACHE_KEY = "dashboard:home:v2:{org}"
CACHE_SECONDS = 30
HISTORY_CACHE_KEY = "dashboard:home:history24h:{org}"
HISTORY_CACHE_SECONDS = 600

# One colour and one image per number, the same ones the game panel uses.
EMPIRE_CARDS = (
    ("gold", "Ouro", "#b8860b", "rgba(184,134,11,0.12)", "game/resources/icon_gold.png", ""),
    ("income", "Renda líquida/h", "#2d7a2d", "rgba(45,122,45,0.12)", "game/resources/icon_gold.png", ""),
    ("resources", "Recursos", "#8c6335", "rgba(140,99,53,0.13)", "game/resources/icon_wood.png", ""),
    ("cities", "Cidades", "#4f86b9", "rgba(79,134,185,0.14)", "game/buildings/townhall.png", ""),
    ("troops", "Tropas", "#bb5143", "rgba(187,81,67,0.12)", "game/units/hoplita.png", ""),
    ("ships", "Navios", "#3f7397", "rgba(63,115,151,0.13)", "game/units/trieme.png", ""),
    ("score", "Pontuação", "#8e6fb0", "rgba(142,111,176,0.14)", "", "bi-trophy-fill"),
)
GOOD, BAD, MUTED = "#2d7a2d", "#b3402f", "var(--ik-muted)"


def _si(value, default=0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def dotted(value) -> str:
    """1234567 -> "1.234.567"."""
    return f"{_si(value):,}".replace(",", ".")


def compact(value) -> str:
    """1234567 -> "1,23 mi": a number that fits a card."""
    value = _si(value)
    size = abs(value)
    for limit, unit in ((10 ** 9, "bi"), (10 ** 6, "mi")):
        if size >= limit:
            return f"{value / limit:.2f}".replace(".", ",") + f" {unit}"
    if size >= 10 ** 4:
        return f"{value / 1000:.1f}".replace(".", ",") + " mil"
    return dotted(value)


def human_span(seconds: float) -> str:
    """"3 min", "2h 10min", "4 dias": how long, for a person."""
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "1 min" if seconds >= 30 else "agora"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}min" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days} dia{'s' if days > 1 else ''}" + (f" {hours}h" if hours and days < 3 else "")


def human_seconds(seconds: float) -> str:
    """How long a job takes: "8 s", "1 min 20 s"."""
    if seconds < 1:
        return "< 1 s"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes} min {seconds} s" if seconds and minutes < 10 else f"{minutes} min"


def _building_name(code: str) -> str:
    from core.catalogs import get_building_info

    info = get_building_info(code) or {}
    return str(info.get("name") or code or "Prédio")


def _action_name(code) -> str:
    from core.actions.registry import get_action_info

    info = get_action_info(_si(code)) or {}
    return str(info.get("name") or f"Ação #{code}")


def _units(value) -> int:
    return sum(max(_si(count), 0) for count in value.values()) if isinstance(value, dict) else 0


def _account_name(game_account) -> str:
    return game_account.name or game_account.server_id


def account_row(game_account, snapshot, now, *, manual_control: bool = False) -> dict:
    """One account as the dashboard sees it."""
    base = (snapshot.base_snapshot if snapshot else None) or {}
    cities = (snapshot.cities if snapshot and isinstance(snapshot.cities, list) else None) or []
    cities = [city for city in cities if isinstance(city, dict)]
    military = (snapshot.military if snapshot and isinstance(snapshot.military, dict) else None) or {}
    name = _account_name(game_account)
    resources = 0
    full_cities = 0
    works = []
    for city in cities:
        resources += sum(_si(city.get(key)) for key in RESOURCES)
        capacity = _si(city.get("warehouse_capacity"))
        if capacity > 0 and max(_si(city.get(key)) for key in RESOURCES) * 100 >= capacity * FULL_WAREHOUSE_PCT:
            full_cities += 1
        for building in city.get("buildings") or []:
            if isinstance(building, dict) and building.get("is_upgrading"):
                end_at = _si(building.get("construction_end_at"))
                left = end_at - now.timestamp()
                works.append({
                    "account": name,
                    "city": str(city.get("name") or city.get("id") or ""),
                    "building": _building_name(str(building.get("building") or "")),
                    "level": _si(building.get("level")) + 1,
                    "end_at": end_at,
                    "ends_in": human_span(left) if left > 0 else "concluindo",
                    "soon": 0 < left <= 3600,
                })
    works.sort(key=lambda work: work["end_at"] or 10 ** 12)
    attack = base.get("attack_alert_state") if isinstance(base.get("attack_alert_state"), dict) else {}
    score = base.get("player_score") if isinstance(base.get("player_score"), dict) else {}
    vacation = bool(read_vacation_state(base)["active"])
    blocked_until = game_account.login_blocked_until
    blocked_until = blocked_until if blocked_until and blocked_until > now else None
    age = (now - snapshot.updated_at) if snapshot else None
    income = net_gold_income(base) if snapshot else 0
    gold = _si(base.get("gold"))
    # hours until the gold ends at the current rate (None: it is not falling)
    runway = timedelta(hours=max(gold, 0) / -income) if income < 0 else None
    row = {
        "id": str(game_account.pk),
        "name": name,
        "server": game_account.server_id,
        "node": game_account.account.node.name,
        "gold": gold,
        "income": income,
        "gold_runway": human_span(runway.total_seconds()) if runway is not None and runway <= GOLD_RUNWAY_WARN else "",
        "gold_runway_bad": runway is not None and runway <= GOLD_RUNWAY_BAD,
        "cities": len(cities),
        "resources": resources,
        "troops": _units(military.get("troops")),
        "ships": _units(military.get("fleet")),
        "score": _si(score.get("total")),
        "full_cities": full_cities,
        "works": works,
        "work": works[0] if works else None,
        "hostile": attack_summary(attack)["attacks"],
        "pirate_raids": attack_summary(attack)["pirates"],
        "vacation": vacation,
        "blocked_until": blocked_until,
        "blocked_reason": (game_account.login_block_reason or "")[:90] if blocked_until else "",
        "manual_control": manual_control,
        "has_snapshot": snapshot is not None,
        "snapshot_age": human_span(age.total_seconds()) if age is not None else "",
        "stale": snapshot is None or age > STALE_SNAPSHOT,
        "gold_text": compact(base.get("gold")),
        "income_text": ("+" if income > 0 else "") + compact(income),
        "resources_text": compact(resources),
    }
    if row["hostile"]:
        row["state"], row["state_label"] = "bad", "Sob ataque"
    elif row["pirate_raids"]:
        row["state"], row["state_label"] = "warn", "Assalto pirata"
    elif blocked_until:
        row["state"], row["state_label"] = "bad", "Login bloqueado"
    elif manual_control:
        row["state"], row["state_label"] = "info", "Controle manual"
    elif vacation:
        row["state"], row["state_label"] = "info", "Férias"
    elif row["stale"]:
        row["state"], row["state_label"] = "warn", "Sem leitura"
    elif row["gold_runway"]:
        row["state"], row["state_label"] = ("bad" if row["gold_runway_bad"] else "warn"), "Ouro acabando"
    else:
        row["state"], row["state_label"] = "ok", "Em ordem"
    return row


def build_alerts(rows, nodes, now, *, problem_workflows=0, pending_captchas=0, proxy_result=None, platform=True) -> list[dict]:
    """What needs the operator, most serious first. Empty means everything is in order."""
    alerts = []
    game, jobs = reverse("game:dashboard"), reverse("jobs:job-list")

    def add(level, icon, text, href="", detail=""):
        alerts.append({"level": level, "icon": icon, "text": text, "href": href, "detail": detail})

    for row in rows:
        if row["hostile"]:
            add("bad", "bi-exclamation-triangle-fill", f"{row['name']} está sob ataque", game,
                f"{row['hostile']} movimento(s) hostil(is) a caminho.")
        elif row["pirate_raids"]:
            add("warn", "bi-flag-fill", f"{row['name']}: assalto pirata a caminho", game,
                "Quem defende é a tripulação da Fortaleza dos Piratas da cidade.")
    for row in rows:
        if row["blocked_until"]:
            local = timezone.localtime(row["blocked_until"])
            add("bad", "bi-lock-fill", f"{row['name']}: login bloqueado até {local:%d/%m %H:%M}",
                reverse("accounts:account-list"), row["blocked_reason"])
    if pending_captchas:
        add("bad", "bi-puzzle-fill", f"{pending_captchas} captcha(s) esperando resposta",
            reverse("captcha:config") if platform else "/organizacao/telegram/",
            "Os jobs dessas contas ficam parados até alguém resolver.")
    for node in nodes:
        if node["accounts"] and not node["online"]:
            detail = f"Sem contato há {node['seen_ago']}; " if node["seen_ago"] else "Nunca se conectou; "
            add("bad", "bi-hdd-network", f"Agente do nó {node['name']} está offline", reverse("accounts:node-list"),
                detail + f"{node['accounts']} conta(s) dependem dele.")
    if problem_workflows:
        add("warn", "bi-bug-fill", f"{problem_workflows} workflow(s) com problema", jobs + "?workflow_state=problem",
            "Pararam por erro e esperam uma decisão.")
    for row in rows:
        if row["stale"] and not row["vacation"] and not row["blocked_until"] and not row["manual_control"]:
            add("warn", "bi-clock-history", f"{row['name']} sem leitura" + (f" há {row['snapshot_age']}" if row["snapshot_age"] else ""),
                jobs, "O Verificar Status desta conta não está rodando ou está falhando.")
    if platform:
        from apps.game.services.ikabotapi_watch import is_stuck

        if is_stuck():
            add("bad", "bi-robot", "ikabotapi não está gerando o blackbox", "",
                "Os logins estão parados. Reinicie o ikabotapi.")
    without_proxy = [str(name) for name in ((proxy_result or {}).get("without_proxy") or [])] if platform else []
    if without_proxy:
        add("warn", "bi-shield-exclamation", "Sem proxy livre para: " + ", ".join(without_proxy[:4]), reverse("proxy:list"),
            "A lista de proxies não tem reserva suficiente.")
    for row in rows:
        if row["gold_runway"] and not row["vacation"]:
            add("bad" if row["gold_runway_bad"] else "warn", "bi-coin", f"{row['name']}: o ouro acaba em {row['gold_runway']}", game,
                f"Tem {compact(row['gold'])} e perde {dotted(-row['income'])} por hora.")
    full = sum(row["full_cities"] for row in rows)
    if full:
        add("warn", "bi-box-seam-fill", f"{full} cidade(s) com armazém cheio", game, "A produção do recurso cheio está sendo perdida.")
    for node in nodes:
        if node["online"] and node["outdated"]:
            add("info", "bi-arrow-repeat", f"Agente do nó {node['name']} tem atualização disponível", reverse("accounts:node-list"),
                f"Está na versão {node['version']}.")
    for row in rows:
        if row["manual_control"]:
            add("info", "bi-controller", f"{row['name']} está em controle manual", game,
                "A automação da conta está pausada até a aba do jogo fechar.")
    negative = sorted((row for row in rows if row["income"] < 0 and not row["gold_runway"] and not row["vacation"]), key=lambda row: row["income"])
    if negative:
        add("info", "bi-graph-down-arrow", f"{len(negative)} conta(s) com renda negativa", game,
            ", ".join(f"{row['name']} ({dotted(row['income'])}/h)" for row in negative[:4]) + ". Nenhuma perto de ficar sem ouro.")
    on_vacation = [row["name"] for row in rows if row["vacation"]]
    if on_vacation:
        add("info", "bi-umbrella-fill", f"{len(on_vacation)} conta(s) de férias", game, ", ".join(on_vacation[:5]))
    alerts.sort(key=lambda alert: LEVELS[alert["level"]])
    return alerts


def empire_totals(rows) -> dict:
    keys = ("gold", "income", "cities", "resources", "troops", "ships", "score")
    return {key: sum(row[key] for row in rows) for key in keys}


def sparkline(values: list[float], width: int = 120, height: int = 30) -> str:
    """Points of an SVG polyline for a tiny chart ("" when there is nothing to draw)."""
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    step = width / (len(values) - 1)
    if high == low:
        return " ".join(f"{index * step:.1f},{height / 2:.1f}" for index in range(len(values)))
    return " ".join(
        f"{index * step:.1f},{height - 2 - (value - low) / (high - low) * (height - 4):.1f}" for index, value in enumerate(values)
    )


def empire_trends(rows, history: dict | None) -> dict:
    """{metric: {"delta", "points"}} over the last 24 hours, from the KPI history of the game panel."""
    series = [history[row["id"]] for row in rows if (history or {}).get(row["id"])]
    if not series:
        return {}
    count = min(len(points) for points in series)
    trends = {}
    for metric in ("gold", "income", "cities", "resources", "troops", "ships"):
        sums = [sum(_si(points[index].get(metric)) for points in series) for index in range(count)]
        trends[metric] = {"delta": sums[-1] - sums[0], "points": sparkline(sums)}
    return trends


def empire_cards(rows, history: dict | None) -> list[dict]:
    totals = empire_totals(rows)
    trends = empire_trends(rows, history)
    negative = sum(1 for row in rows if row["income"] < 0)
    full = sum(row["full_cities"] for row in rows)
    notes = {
        "income": f"{negative} conta(s) no negativo" if negative else "",
        "resources": f"{full} cidade(s) com armazém cheio" if full else "",
        "cities": f"em {len(rows)} conta(s)",
    }
    cards = []
    for key, label, color, soft, image, icon in EMPIRE_CARDS:
        value = totals[key]
        trend = trends.get(key)
        if trend is None:
            delta_text, delta_color = "", MUTED
        elif trend["delta"] == 0:
            delta_text, delta_color = "estável em 24h", MUTED
        else:
            up = trend["delta"] > 0
            delta_text = ("▲ +" if up else "▼ −") + compact(abs(trend["delta"])) + " em 24h"
            delta_color = GOOD if up else BAD
        cards.append({
            "key": key, "label": label, "color": color, "soft": soft, "image": image, "icon": icon,
            "value": ("+" if key == "income" and value > 0 else "") + compact(value),
            "full": dotted(value),
            "value_color": (GOOD if value >= 0 else BAD) if key == "income" else "var(--ik-ink)",
            "delta_text": delta_text, "delta_color": delta_color,
            "points": trend["points"] if trend else "",
            "note": notes.get(key, ""),
            "note_bad": key in ("income", "resources") and bool(notes.get(key)),
        })
    return cards


def node_rows(now) -> list[dict]:
    from apps.accounts.services.agent_versions import classify_agent_version

    expected = str(getattr(settings, "EXPECTED_AGENT_VERSION", "") or "").strip()
    rows = []
    for node in Node.objects.filter(active=True).order_by("name"):
        seen = node.agent_last_seen_at
        rows.append({
            "id": str(node.pk),
            "name": node.name,
            "online": node.is_online,
            "version": node.agent_version or "",
            "outdated": classify_agent_version(node.agent_version, expected).code == "outdated",
            "accounts": GameAccount.objects.filter(account__node=node, active=True, account__active=True).count(),
            "seen_ago": human_span((now - seen).total_seconds()) if seen else "",
        })
    return rows


# Why the jobs failed, read from each failed job's last error/warning log (N-136): (key, label, colour, words)
ERROR_CAUSES = (
    ("ikabotapi", "ikabotapi lento ou travado (login)", "#8f3c44", ("blackbox",)),
    ("hub", "Comunicação com o hub", "#c29a2e", ("connection reset", "connection aborted", "connectionerror", "bad gateway",
                                                "502 server", "503 server", "504 server", "read timed out", "remotedisconnected")),
    ("game", "O jogo recusou a ação", "#3e84a8", ("server errors for action", "não podes", "nao podes", "feedback=")),
    ("orphan", "Agente parou de responder", "#6f6b63", ("orfa", "órfã", "orfã")),
    ("credentials", "Credenciais indisponíveis", "#8c6335", ("missing_credentials", "credenciais nao", "credenciais não")),
)
CAUSE_SAMPLE = 500


def error_causes(error_ids, error_ends) -> list[dict]:
    """[{label, color, count, share_pct, last}] for the failed jobs, most frequent first."""
    from apps.jobs.models import JobLog

    error_ids = list(error_ids)[-CAUSE_SAMPLE:]
    if not error_ids:
        return []
    last_message: dict = {}
    logs = (JobLog.objects.filter(job_id__in=error_ids).exclude(level="info")
            .order_by("job_id", "-created_at").values_list("job_id", "message"))
    for job_id, message in logs.iterator(chunk_size=2000):
        last_message.setdefault(job_id, message)
    found: dict[str, dict] = {}
    for job_id in error_ids:
        text = str(last_message.get(job_id) or "").lower()
        key, label, color = "other", "Outros", "#b3402f"
        for cause_key, cause_label, cause_color, words in ERROR_CAUSES:
            if any(word in text for word in words):
                key, label, color = cause_key, cause_label, cause_color
                break
        entry = found.setdefault(key, {"key": key, "label": label, "color": color, "count": 0, "last_at": None})
        entry["count"] += 1
        ended = error_ends.get(job_id)
        if ended and (entry["last_at"] is None or ended > entry["last_at"]):
            entry["last_at"] = ended
    causes = sorted(found.values(), key=lambda entry: -entry["count"])
    most = causes[0]["count"] if causes else 1
    for entry in causes:
        entry["share_pct"] = str(max(round(entry["count"] * 100 / most), 3))
        entry["last"] = timezone.localtime(entry.pop("last_at")).strftime("%Hh%M") if entry.get("last_at") else ""
    return causes


def automation_stats(now) -> dict:
    """Jobs that ended in the last 24 hours: by hour, the success rate, the busiest actions and how long they take."""
    since = now - timedelta(hours=24)
    hours = []
    index = {}
    cursor = timezone.localtime(since).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for position in range(24):
        index[cursor.strftime("%d%H")] = position
        hours.append({"label": cursor.strftime("%Hh"), "show_label": position % 3 == 2, "ok": 0, "error": 0})
        cursor += timedelta(hours=1)
    actions: dict[int, dict] = {}
    ok = errors = 0
    ended = Job.objects.filter(finished_at__gte=since, status__in=("finished", "error"))
    columns = ended.values_list("finished_at", "started_at", "status", "action_code", "id")
    error_ends: dict = {}
    for finished_at, started_at, status, action_code, job_id in columns.iterator(chunk_size=2000):
        failed = status == "error"
        if failed:
            error_ends[job_id] = finished_at
        bucket = index.get(timezone.localtime(finished_at).strftime("%d%H"))
        if bucket is not None:
            hours[bucket]["error" if failed else "ok"] += 1
        entry = actions.setdefault(action_code, {"code": action_code, "total": 0, "errors": 0, "seconds": 0.0, "timed": 0})
        entry["total"] += 1
        entry["errors"] += failed
        if started_at and finished_at >= started_at:
            entry["seconds"] += (finished_at - started_at).total_seconds()
            entry["timed"] += 1
        errors += failed
        ok += not failed
    peak = max((hour["ok"] + hour["error"] for hour in hours), default=0) or 1
    for hour in hours:
        hour["total"] = hour["ok"] + hour["error"]
        # as plain strings: a template would write "12,5" in pt-BR, which is not valid CSS
        hour["ok_pct"] = f"{hour['ok'] * 100 / peak:.1f}"
        # a single error still shows, but no longer as tall as several jobs (N-136)
        hour["error_pct"] = f"{max(hour['error'] * 100 / peak, 2):.1f}" if hour["error"] else "0"
    for entry in actions.values():
        entry["name"] = _action_name(entry["code"])
        entry["ok_pct"] = round((entry["total"] - entry["errors"]) * 100 / entry["total"])
        entry["average"] = human_seconds(entry["seconds"] / entry["timed"]) if entry["timed"] else ""
        entry["total_text"] = dotted(entry["total"])
    top = sorted(actions.values(), key=lambda entry: -entry["total"])[:6]
    busiest = top[0]["total"] if top else 1
    for entry in top:
        entry["share_pct"] = str(max(round(entry["total"] * 100 / busiest), 2))
    total = ok + errors
    timed = sum(entry["timed"] for entry in actions.values())
    return {
        "hours": hours, "peak": peak, "total": total, "total_text": dotted(total), "ok": ok, "errors": errors,
        "success_pct": f"{ok * 100 / total:.1f}".replace(".", ",") if total else "",
        "average": human_seconds(sum(entry["seconds"] for entry in actions.values()) / timed) if timed else "",
        "top_actions": top,
        "failing_actions": sorted((entry for entry in actions.values() if entry["errors"]), key=lambda entry: -entry["errors"])[:4],
        "causes": error_causes(sorted(error_ends, key=lambda job_id: error_ends[job_id]), error_ends),
    }


def coming_up(rows, now) -> dict:
    works = sorted((work for row in rows for work in row["works"]), key=lambda work: work["end_at"] or 10 ** 12)

    def job_line(job):
        return {
            "id": str(job.pk),
            "name": _action_name(job.action_code),
            "account": _account_name(job.game_account) if job.game_account_id else "",
        }

    scheduled = []
    upcoming = Job.objects.filter(status="scheduled", scheduled_for__isnull=False).select_related("game_account").order_by("scheduled_for")
    for job in upcoming[:8]:
        left = (job.scheduled_for - now).total_seconds()
        scheduled.append({**job_line(job), "at": timezone.localtime(job.scheduled_for), "eta": human_span(left) if left > 30 else "agora"})
    running = []
    for job in Job.objects.filter(status="running").select_related("game_account").order_by("started_at")[:6]:
        running.append({**job_line(job), "since": human_span((now - job.started_at).total_seconds()) if job.started_at else ""})
    return {
        "works": works[:8], "works_total": len(works),
        "idle_accounts": [row["name"] for row in rows if row["has_snapshot"] and not row["works"] and not row["vacation"]],
        "scheduled": scheduled, "scheduled_total": Job.objects.filter(status="scheduled").count(),
        "running": running, "running_total": Job.objects.filter(status__in=("running", "queued")).count(),
    }


def workflow_health(now) -> dict:
    """Workflows as the job queue shows them: the status is the reality of the jobs (N-89), not the stored field."""
    from apps.jobs.services import workflow_status
    from apps.jobs.services.workflows import workflow_type_info

    live = list(Workflow.objects.filter(archived_at__isnull=True).select_related("game_account"))
    statuses = workflow_status.effective_statuses(live, workflow_status.job_facts())
    broken = [workflow for workflow in live if statuses[workflow.pk] == "problem"]
    broken.sort(key=lambda workflow: workflow.last_error_at or workflow.updated_at, reverse=True)
    return {
        "active": sum(1 for status in statuses.values() if status in ("active", "waiting")),
        "problem_total": len(broken),
        "problems": [
            {
                "id": str(workflow.pk),
                "title": workflow_type_info(workflow.workflow_type)["label"],
                "account": _account_name(workflow.game_account) if workflow.game_account_id else "",
                "error": (workflow.last_error_summary or "").strip()[:160],
                "ago": human_span((now - workflow.last_error_at).total_seconds()) if workflow.last_error_at else "",
            }
            for workflow in broken[:6]
        ],
    }


def history_24h(game_account_ids) -> dict:
    """The 24-hour KPI history, kept for a few minutes: it is the heavy part and moves slowly."""
    key = HISTORY_CACHE_KEY.format(org=_org_key())
    history = cache.get(key)
    if history is None:
        from apps.game.services.kpi_history import build_history_map

        history = build_history_map(list(game_account_ids), "24h")
        cache.set(key, history, HISTORY_CACHE_SECONDS)
    return history


def _org_key() -> str:
    from apps.orgs.scope import current_organization

    organization = current_organization()
    return str(organization.pk) if organization is not None else "none"


def _is_platform() -> bool:
    """The proxy pool and its maintenance belong to the platform: only its own organization sees them."""
    from apps.orgs.scope import current_organization
    from apps.orgs.services import DEFAULT_SLUG

    organization = current_organization()
    return organization is None or organization.slug == DEFAULT_SLUG


def build_dashboard(now=None) -> dict:
    from apps.captcha.models import CaptchaChallenge
    from apps.manual_control.models import ManualControlSession
    from apps.proxy import maintenance

    now = now or timezone.now()
    accounts = list(
        GameAccount.objects.filter(active=True, account__active=True).select_related("account__node").order_by("name", "server_id")
    )
    snapshots = {snapshot.game_account_id: snapshot for snapshot in AccountSnapshot.objects.filter(game_account__in=accounts)}
    controlled = set(
        ManualControlSession.objects.filter(status=ManualControlSession.ACTIVE).values_list("game_account_id", flat=True)
    )
    rows = [account_row(ga, snapshots.get(ga.pk), now, manual_control=ga.pk in controlled) for ga in accounts]
    nodes = node_rows(now)
    workflows = workflow_health(now)
    pending_captchas = CaptchaChallenge.objects.filter(status__in=("pending", "solving")).exclude(expires_at__lt=now).count()
    platform = _is_platform()
    try:
        proxy_result, proxy_at = (maintenance.last_result(), maintenance.last_run()) if platform else ({}, None)
    except Exception:  # noqa: BLE001 - the proxy summary is a detail of this page
        proxy_result, proxy_at = {}, None
    try:
        history = history_24h([row["id"] for row in rows if row["has_snapshot"]])
    except Exception:  # noqa: BLE001 - without the history the cards only lose the 24h variation
        history = {}
    alerts = build_alerts(
        rows, nodes, now, problem_workflows=workflows["problem_total"], pending_captchas=pending_captchas, proxy_result=proxy_result,
        platform=platform,
    )
    return {
        "now": now,
        "alerts": alerts,
        "alerts_serious": sum(1 for alert in alerts if alert["level"] != "info"),
        "cards": empire_cards(rows, history),
        "rows": sorted(rows, key=lambda row: (LEVELS[row["state"]], row["name"].lower())),
        "coming": coming_up(rows, now),
        "automation": automation_stats(now),
        "workflows": workflows,
        "nodes": nodes,
        "nodes_online": sum(1 for node in nodes if node["online"]),
        "proxy": {
            "tested": _si(proxy_result.get("tested")), "passed": _si(proxy_result.get("passed")),
            "ago": human_span((now - proxy_at).total_seconds()),
        } if proxy_at else None,
        "expected_agent_version": str(getattr(settings, "EXPECTED_AGENT_VERSION", "") or "").strip(),
    }


def dashboard_data() -> dict:
    """The dashboard, rebuilt at most every CACHE_SECONDS (the page refreshes itself every minute)."""
    key = CACHE_KEY.format(org=_org_key())
    data = cache.get(key)
    if data is None:
        data = build_dashboard()
        cache.set(key, data, CACHE_SECONDS)
    return data
