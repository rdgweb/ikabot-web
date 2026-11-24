"""N-222: attributable database content, collected explicitly by platform staff.

Byte counts are the stored UTF-8 text/JSON content, not an allocation of InnoDB
pages, indexes, host memory or CPU. Only aggregates leave the database.
"""
import json
import logging
import threading
import uuid
from datetime import timedelta

from django.apps import apps
from django.core.cache import cache
from django.db import OperationalError, connection, connections, models, transaction
from django.db.models import BigIntegerField, Count, F, Func, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.orgs.scope import unscoped
from .models import AppSetting

SNAPSHOT_KEY = "organization_usage_snapshot"
LOCK_KEY = "organization-usage-collection"
ERROR_KEY = "organization-usage-error"
LOCK_SECONDS = 1800
QUERY_SECONDS = 20
BATCH_SIZE = 1000
BATCH_MODELS = {"game.accountsnapshothistory", "jobs.joblog"}
logger = logging.getLogger(__name__)

# Large histories and their direct organization anchors. Other tables remain
# in the physical installation total, without a fabricated per-tenant split.
SOURCES = (
    ("jobs.Job", "account__organization_id", "Jobs"),
    ("jobs.JobLog", "job__account__organization_id", "Logs de jobs"),
    ("jobs.Workflow", "account__organization_id", "Workflows"),
    ("jobs.WorkflowRun", "workflow__account__organization_id", "Ciclos de workflows"),
    ("game.AccountSnapshot", "account__organization_id", "Estado atual das contas"),
    ("game.AccountSnapshotHistory", "account__organization_id", "Histórico detalhado"),
    ("game.AccountHistoryMetric", "account__organization_id", "Métricas dos gráficos"),
    ("espionage.SpyReport", "game_account__account__organization_id", "Relatórios de espionagem"),
    ("combat.CombatReport", "game_account__account__organization_id", "Relatórios de combate"),
    ("worldintel.WorldDump", "account__organization_id", "Coletas do mundo"),
    ("worldintel.WorldDumpCity", "dump__account__organization_id", "Cidades do mundo"),
    ("worldintel.WorldDumpIsland", "dump__account__organization_id", "Ilhas do mundo"),
    ("worldintel.WorldDumpPlayer", "dump__account__organization_id", "Jogadores do mundo"),
    ("telegram.MessageAudit", "game_account__account__organization_id", "Mensagens do Telegram"),
    ("audit.AuditEvent", "organization_id", "Auditoria"),
)


class ByteLength(Func):
    function = "OCTET_LENGTH"
    output_field = BigIntegerField()

    def as_sqlite(self, compiler, connection, **extra):
        return self.as_sql(compiler, connection, template="LENGTH(CAST(%(expressions)s AS BLOB))", **extra)


def snapshot():
    raw = AppSetting.objects.filter(pk=SNAPSHOT_KEY).values_list("value", flat=True).first()
    return json.loads(raw) if raw else None


def collecting():
    return bool(cache.get(LOCK_KEY))


def _aggregate(model, anchor):
    if model._meta.label_lower not in BATCH_MODELS:
        try:
            return _aggregate_query(model, anchor)
        except OperationalError as exc:
            if not exc.args or exc.args[0] != 1969:
                raise
    # A whole-history scan can exceed the production query deadline even at
    # two minutes. Read primary-key windows so each query remains bounded.
    totals = {}
    last = None
    ceiling = model._base_manager.order_by("-pk").values_list("pk", flat=True).first()
    while ceiling is not None:
        window = model._base_manager.filter(pk__lte=ceiling)
        if last is not None:
            window = window.filter(pk__gt=last)
        keys = list(window.order_by("pk").values_list("pk", flat=True)[:BATCH_SIZE])
        if not keys:
            break
        for org_id, count, size in _aggregate_query(model, anchor, (keys[0], keys[-1])):
            result = totals.setdefault(org_id, [0, 0])
            result[0] += int(count)
            result[1] += int(size)
        last = keys[-1]
    return [(org_id, *values) for org_id, values in totals.items()]


def _aggregate_query(model, anchor, bounds=None):
    fields = [f for f in model._meta.concrete_fields if isinstance(f, (models.CharField, models.TextField, models.JSONField))]
    size = Value(0, output_field=BigIntegerField())
    for field in fields:
        size = size + Coalesce(ByteLength(F(field.name)), Value(0), output_field=BigIntegerField())
    records = model._base_manager.all()
    if bounds:
        records = records.filter(pk__gte=bounds[0], pk__lte=bounds[1])
    query = (records.values(anchor).annotate(
        records=Count("pk"), content_bytes=Coalesce(Sum(size), Value(0), output_field=BigIntegerField())
    ).order_by())
    sql, params = query.query.sql_with_params()
    if connection.vendor == "mysql":
        sql = f"SET STATEMENT max_statement_time={QUERY_SECONDS} FOR " + sql
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def collect():
    """Read aggregates, then atomically replace the last completed report."""
    from apps.accounts.models import Account, GameAccount, Node
    from apps.jobs.models import Job
    from apps.orgs.models import Membership, Organization
    from . import maintenance

    started = timezone.now()
    rows = {str(org.pk): {"id": str(org.pk), "name": org.name, "active": org.active,
                         "content_bytes": 0, "breakdown": []} for org in Organization.objects.order_by("name")}
    shared = {"content_bytes": 0, "breakdown": []}
    issues = []
    with unscoped():
        for label, anchor, name in SOURCES:
            try:
                values = _aggregate(apps.get_model(label), anchor)
            except Exception:
                logger.exception("Organization usage collection failed for %s", label)
                issues.append(name)
                continue
            for org_id, count, size in values:
                key = str(uuid.UUID(str(org_id))) if org_id is not None else None
                row = rows.get(key, shared)
                row["content_bytes"] += int(size)
                row["breakdown"].append({"label": name, "records": int(count), "bytes": int(size)})

        counts = (
            (Membership, "organization_id", "members", Q()),
            (Account, "organization_id", "accounts", Q()),
            (GameAccount, "account__organization_id", "subaccounts", Q()),
            (Node, "organization_id", "nodes", Q()),
            (Node, "organization_id", "online", Q(active=True, agent_last_seen_at__gte=started-timedelta(minutes=3))),
            (Job, "account__organization_id", "jobs_active", Q(status__in=("queued", "scheduled", "running"))),
            (Job, "account__organization_id", "jobs_24h", Q(created_at__gte=started-timedelta(days=1))),
            (Job, "account__organization_id", "errors_24h", Q(status="error", finished_at__gte=started-timedelta(days=1))),
        )
        for model, anchor, key, condition in counts:
            for row in rows.values():
                row[key] = 0
            for item in model._base_manager.filter(condition).values(anchor).annotate(total=Count("pk")).order_by():
                if str(item[anchor]) in rows:
                    rows[str(item[anchor])][key] = item["total"]

    for row in [*rows.values(), shared]:
        row["breakdown"].sort(key=lambda item: -item["bytes"])
    data = {"started_at": started.isoformat(), "finished_at": timezone.now().isoformat(),
            "rows": sorted(rows.values(), key=lambda row: -row["content_bytes"]), "shared": shared,
            "issues": issues, "database": maintenance.database(), "disk": maintenance.disk(),
            "content_bytes": sum(r["content_bytes"] for r in rows.values()), "source_count": len(SOURCES)}
    with transaction.atomic():
        setting, _ = AppSetting.objects.get_or_create(pk=SNAPSHOT_KEY)
        setting = AppSetting.objects.select_for_update().get(pk=setting.pk)
        previous = json.loads(setting.value) if setting.value else None
        data["history"] = (previous or {}).get("history", [])[-29:]
        data["history"].append({"at": data["finished_at"], "bytes": data["content_bytes"],
                                "organizations": {key: row["content_bytes"] for key, row in rows.items()},
                                "partial": bool(issues)})
        if previous and not issues and not previous.get("issues"):
            before = {row["id"]: row["content_bytes"] for row in previous.get("rows", [])}
            for row in data["rows"]:
                if row["id"] in before:
                    row["delta"] = row["content_bytes"] - before[row["id"]]
        setting.value = json.dumps(data)
        setting.save(update_fields=["value", "updated_at"])
    return data


def start():
    token = uuid.uuid4().hex
    if not cache.add(LOCK_KEY, token, LOCK_SECONDS):
        return False
    cache.delete(ERROR_KEY)

    def work():
        try:
            collect()
        except Exception:
            logger.exception("Organization usage collection interrupted")
            cache.set(ERROR_KEY, "A coleta foi interrompida. A medição anterior foi preservada; tente atualizar novamente.", 86400)
        finally:
            if cache.get(LOCK_KEY) == token:
                cache.delete(LOCK_KEY)
            connections.close_all()

    try:
        threading.Thread(target=work, name="organization-usage", daemon=True).start()
    except Exception:
        cache.delete(LOCK_KEY)
        raise
    return True
