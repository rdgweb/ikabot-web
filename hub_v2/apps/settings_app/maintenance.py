"""The server's upkeep in one place (N-187): disk, database, daily backup, table optimisation.

What it measures (disk, the database and its biggest tables, the backups kept) is read on the page.
What it does runs in the hub, off any request, one thing at a time, and each run is written down:
- the backup dumps the hub's database (consistent, without locking it) compressed into the backups
  folder, only when the disk has room for it, and keeps the newest N;
- the optimisation rebuilds the tables whose free space inside is worth giving back to the disk (what
  the retention leaves inside the table only returns this way), one table at a time, only when the
  disk has room for the copy the database makes;
- the schedule is checked by the agents' heartbeat (as the retention's): a backup a day at its hour,
  the optimisation once a week, the backup first.
Nothing here takes a command from the page: the page only chooses what to run and when.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.db import connection, connections
from django.utils import timezone

logger = logging.getLogger(__name__)

CONFIG_KEY = "maintenance_config"
RUNS_KEY = "maintenance_runs"
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
BACKUP_RE = re.compile(r"^ikabot_hub_(\d{8})_(\d{4})\.sql\.gz$")
DEFAULT_CONFIG = {
    "backup_enabled": False, "backup_hour": 4, "backup_keep": 3,
    "optimize_enabled": False, "optimize_weekday": 6, "optimize_hour": 5, "optimize_min_free_mb": 200,
    "disk_alert_pct": 85,
}
RUNNER_LOCK = "maintenance-runner"
RUNNER_LOCK_SECONDS = 4 * 3600
TICK_KEY = "maintenance-tick"
TICK_SECONDS = 60
MAX_RUNS = 40
DISK_MARGIN = 2 * 1024 ** 3          # always left free after a backup or an optimisation
CHUNK = 1024 * 1024


# --- configuration and the record of what ran ------------------------------------------------

def get_config() -> dict:
    from .utils import get_setting

    try:
        stored = json.loads(get_setting(CONFIG_KEY, "") or "{}")
    except ValueError:
        stored = {}
    config = dict(DEFAULT_CONFIG)
    for key, default in DEFAULT_CONFIG.items():
        if key in stored and type(stored[key]) is type(default):
            config[key] = stored[key]
    return config


def save_config(values: dict) -> dict:
    from .utils import set_setting

    config = get_config()
    for key, default in DEFAULT_CONFIG.items():
        if key in values:
            config[key] = bool(values[key]) if isinstance(default, bool) else int(values[key])
    config["backup_hour"] = max(0, min(23, config["backup_hour"]))
    config["optimize_hour"] = max(0, min(23, config["optimize_hour"]))
    config["optimize_weekday"] = max(0, min(6, config["optimize_weekday"]))
    config["backup_keep"] = max(1, min(30, config["backup_keep"]))
    config["optimize_min_free_mb"] = max(10, config["optimize_min_free_mb"])
    config["disk_alert_pct"] = max(50, min(99, config["disk_alert_pct"]))
    set_setting(CONFIG_KEY, json.dumps(config))
    return config


def runs() -> list[dict]:
    from .utils import get_setting

    try:
        value = json.loads(get_setting(RUNS_KEY, "") or "[]")
    except ValueError:
        value = []
    return [r for r in value if isinstance(r, dict)]


def _record(run: dict) -> None:
    from .utils import set_setting

    kept = [r for r in runs() if r.get("id") != run["id"]]
    kept.append(run)
    set_setting(RUNS_KEY, json.dumps(kept[-MAX_RUNS:], ensure_ascii=False))


def last_run(kind: str, *, status: str | None = None) -> dict | None:
    for run in reversed(runs()):
        if run.get("kind") == kind and (status is None or run.get("status") == status):
            return run
    return None


# --- what the page shows ---------------------------------------------------------------------

def disk() -> dict | None:
    """The disk the backups (and the database, on the same machine) live on."""
    path = BACKUP_DIR if os.path.isdir(BACKUP_DIR) else "/"
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total": usage.total, "used": usage.used, "free": usage.free,
            "pct": round(100 * usage.used / usage.total) if usage.total else 0, "path": path}


def database() -> dict:
    """The hub's database: its size and the biggest tables, with the free space each keeps inside."""
    if connection.vendor != "mysql":
        return {"size": 0, "free": 0, "tables": []}         # the tests' SQLite has no information_schema
    with connection.cursor() as cur:
        cur.execute(
            "SELECT table_name, table_rows, data_length + index_length, data_free FROM information_schema.tables "
            "WHERE table_schema = DATABASE() ORDER BY data_length + index_length DESC")
        rows = cur.fetchall()
    tables = [{"name": r[0], "rows": int(r[1] or 0), "size": int(r[2] or 0), "free": int(r[3] or 0)} for r in rows]
    return {"size": sum(t["size"] for t in tables), "free": sum(t["free"] for t in tables), "tables": tables}


def backups() -> list[dict]:
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for name in os.listdir(BACKUP_DIR):
        match = BACKUP_RE.match(name)
        if not match:
            continue
        path = os.path.join(BACKUP_DIR, name)
        stamp = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M")
        out.append({"name": name, "size": os.path.getsize(path),
                    "at": timezone.make_aware(stamp, timezone.get_current_timezone())})
    return sorted(out, key=lambda b: b["at"], reverse=True)


def backup_path(name: str) -> str | None:
    """The file of a backup by its name, only one of ours (no path from the page reaches the disk)."""
    if not BACKUP_RE.match(name or ""):
        return None
    path = os.path.join(BACKUP_DIR, name)
    return path if os.path.isfile(path) else None


def busy() -> bool:
    return bool(cache.get(RUNNER_LOCK))


# --- the work --------------------------------------------------------------------------------

def _spawn(work) -> None:
    """Run `work` off the request. Tests replace this to run it inline."""
    threading.Thread(target=work, name="maintenance", daemon=True).start()


def start(kind: str, *, reason: str = "manual") -> bool:
    """Start a backup or an optimisation in the background; False when something is running already."""
    if kind not in ("backup", "optimize"):
        raise ValueError(kind)
    if not cache.add(RUNNER_LOCK, kind, RUNNER_LOCK_SECONDS):
        return False
    run = {"id": f"{kind}-{int(time.time())}", "kind": kind, "reason": reason, "status": "running",
           "started_at": timezone.now().isoformat(), "finished_at": None, "detail": "", "size": 0}
    _record(run)
    caller = threading.get_ident()

    def work():
        try:
            detail, size, status = (_backup() if kind == "backup" else _optimize())
            run.update(status=status, detail=detail, size=size)
        except Exception as exc:  # noqa: BLE001 - the run says it failed; nothing half done is kept
            logger.exception("Maintenance %s failed", kind)
            run.update(status="failed", detail=str(exc)[:300])
        finally:
            run["finished_at"] = timezone.now().isoformat()
            try:
                _record(run)
            finally:
                cache.delete(RUNNER_LOCK)
                if threading.get_ident() != caller:
                    connections.close_all()

    try:
        _spawn(work)
    except Exception:
        cache.delete(RUNNER_LOCK)
        raise
    return True


def _backup() -> tuple[str, int, str]:
    config = get_config()
    db = settings.DATABASES["default"]
    os.makedirs(BACKUP_DIR, exist_ok=True)
    previous = backups()
    need = max(int(previous[0]["size"] * 1.5) if previous else 0, int(database()["size"] * 0.35)) + DISK_MARGIN
    free = shutil.disk_usage(BACKUP_DIR).free
    if free < need:
        return f"Sem espaço: {free // 1024 ** 2} MB livres, precisa de {need // 1024 ** 2} MB.", 0, "skipped"
    name = timezone.localtime().strftime("ikabot_hub_%Y%m%d_%H%M.sql.gz")
    final = os.path.join(BACKUP_DIR, name)
    partial = final + ".partial"
    command = ["mariadb-dump", "--single-transaction", "--quick", "--skip-lock-tables", "--no-tablespaces", "--hex-blob",
               "-h", str(db.get("HOST") or "localhost"), "-P", str(db.get("PORT") or "3306"), "-u", str(db.get("USER") or ""),
               str(db.get("NAME") or "")]
    env = {**os.environ, "MYSQL_PWD": str(db.get("PASSWORD") or "")}      # never on the command line
    started = time.time()
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env) as dump, \
                gzip.open(partial, "wb", compresslevel=6) as out:
            while True:
                chunk = dump.stdout.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
            error = dump.stderr.read().decode("utf-8", "replace")[-300:]
            dump.wait()
            if dump.returncode != 0:
                raise RuntimeError(f"mariadb-dump saiu com {dump.returncode}: {error.strip()}")
        os.replace(partial, final)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    size = os.path.getsize(final)
    removed = []
    for old in backups()[config["backup_keep"]:]:
        os.remove(os.path.join(BACKUP_DIR, old["name"]))
        removed.append(old["name"])
    detail = f"{name}: {size // 1024 ** 2} MB em {round((time.time() - started) / 60, 1)} min"
    if removed:
        detail += f"; apagados os mais antigos ({len(removed)})"
    return detail, size, "ok"


def _optimize() -> tuple[str, int, str]:
    from .models import RetentionRun

    if RetentionRun.objects.filter(status="running").exists():
        # both rewrite the same big tables: the retention first, the optimisation on another day
        return "Adiada: a retenção está rodando (as duas mexem nas mesmas tabelas).", 0, "skipped"
    config = get_config()
    floor = config["optimize_min_free_mb"] * 1024 ** 2
    done, skipped, gained = [], [], 0
    for table in database()["tables"]:
        if table["free"] < floor:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]+", table["name"]):
            continue
        if shutil.disk_usage(BACKUP_DIR if os.path.isdir(BACKUP_DIR) else "/").free < table["size"] * 1.2 + DISK_MARGIN:
            skipped.append(table["name"])          # the database copies the table while it rebuilds it
            continue
        with connection.cursor() as cur:
            cur.execute(f"OPTIMIZE TABLE `{table['name']}`")
            cur.fetchall()
        after = next((t for t in database()["tables"] if t["name"] == table["name"]), table)
        gained += max(0, table["size"] - after["size"])
        done.append(f"{table['name']} (-{max(0, table['size'] - after['size']) // 1024 ** 2} MB)")
    if not done and not skipped:
        return "Nenhuma tabela com espaço livre suficiente para valer a pena.", 0, "ok"
    detail = ("Otimizadas: " + ", ".join(done)) if done else "Nada otimizado"
    if skipped:
        detail += f"; sem espaço em disco para: {', '.join(skipped)}"
    return detail, gained, "ok" if done else "skipped"


# --- the schedule ----------------------------------------------------------------------------

def _ran_today(kind: str, now) -> bool:
    run = last_run(kind)
    if not run or not run.get("started_at"):
        return False
    try:
        started = timezone.localtime(datetime.fromisoformat(run["started_at"]))
    except ValueError:
        return False
    return started.date() == now.date()


def _close_interrupted() -> None:
    """A run still "running" with nobody running it was cut by a restart."""
    if busy():
        return
    for run in runs():
        if run.get("status") == "running":
            run.update(status="failed", detail="Interrompido (o hub reiniciou durante a execução).",
                       finished_at=timezone.now().isoformat())
            _record(run)


def tick() -> str | None:
    """Called by every agent heartbeat (at most once a minute does anything): start what is due."""
    if not cache.add(TICK_KEY, 1, TICK_SECONDS):
        return None
    _close_interrupted()
    if busy():
        return None
    config = get_config()
    now = timezone.localtime()
    if config["backup_enabled"] and now.hour >= config["backup_hour"] and not _ran_today("backup", now):
        return "backup" if start("backup", reason="agenda") else None
    if (config["optimize_enabled"] and now.weekday() == config["optimize_weekday"] and now.hour >= config["optimize_hour"]
            and not _ran_today("optimize", now)
            and (not config["backup_enabled"] or _ran_today("backup", now))):
        return "optimize" if start("optimize", reason="agenda") else None
    return None
