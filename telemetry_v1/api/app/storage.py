from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from . import db
from .geo import country_for_timezone
from .geoip import Geo
from .schemas import Ping

# Groups smaller than this are folded into "other" on the public page so a
# tiny world/country can never single out one installation.
PUBLIC_MIN_GROUP = 5


def save_ping(
    ping: Ping, today: date, valid_timezones: set[str], ip: str | None = None, geo: Geo | None = None,
) -> None:
    timezone = ping.timezone if ping.timezone in valid_timezones else None
    geo = geo or Geo()
    # Prefer the IP-derived country; fall back to the client-declared timezone.
    country = geo.country or (country_for_timezone(timezone) if timezone else None)
    geo_source = "ip" if geo.country else ("timezone" if country else None)
    counts = ping.counts

    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO installs (install_id, first_seen, last_seen, hub_version, arch,
                                  timezone, country, lobby_accounts, game_accounts, nodes,
                                  region, city, latitude, longitude, geo_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (install_id) DO UPDATE SET
                last_seen = EXCLUDED.last_seen, hub_version = EXCLUDED.hub_version,
                arch = EXCLUDED.arch, timezone = EXCLUDED.timezone, country = EXCLUDED.country,
                lobby_accounts = EXCLUDED.lobby_accounts, game_accounts = EXCLUDED.game_accounts,
                nodes = EXCLUDED.nodes, region = EXCLUDED.region, city = EXCLUDED.city,
                latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude,
                geo_source = EXCLUDED.geo_source
            """,
            (ping.install_id, today, today, ping.hub_version, ping.arch, timezone, country,
             counts.lobby_accounts, counts.game_accounts, counts.nodes,
             geo.region, geo.city, geo.latitude, geo.longitude, geo_source),
        )
        if ip:
            conn.execute(
                "INSERT INTO install_ips (install_id, day, ip) VALUES (%s, %s, %s) "
                "ON CONFLICT (install_id, day) DO UPDATE SET ip = EXCLUDED.ip",
                (ping.install_id, today, ip),
            )
        # One snapshot per install per day: replace today's rows (children cascade).
        conn.execute("DELETE FROM pings WHERE day = %s AND install_id = %s", (today, ping.install_id))
        conn.execute(
            """
            INSERT INTO pings (day, install_id, hub_version, arch, timezone, country,
                               lobby_accounts, game_accounts, nodes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (today, ping.install_id, ping.hub_version, ping.arch, timezone, country,
             counts.lobby_accounts, counts.game_accounts, counts.nodes),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO ping_worlds VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(today, ping.install_id, w.server, w.world, w.accounts) for w in ping.worlds],
            )
            cur.executemany(
                "INSERT INTO ping_agents VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(today, ping.install_id, a.version, a.nodes) for a in ping.agents],
            )
            cur.executemany(
                "INSERT INTO ping_usage VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(today, ping.install_id, u.category, u.jobs) for u in ping.usage_30d],
            )


def delete_install(install_id: UUID) -> None:
    with db.connection() as conn:
        conn.execute("DELETE FROM installs WHERE install_id = %s", (install_id,))


def purge_older_than(today: date, retention_days: int, ip_retention_days: int) -> int:
    cutoff = today - timedelta(days=retention_days)
    with db.connection() as conn:
        conn.execute("DELETE FROM install_ips WHERE day < %s", (today - timedelta(days=ip_retention_days),))
        deleted = conn.execute("DELETE FROM installs WHERE last_seen < %s", (cutoff,)).rowcount
        conn.execute("DELETE FROM pings WHERE day < %s", (cutoff,))
    return deleted


def _fold_small(rows: list[tuple[str, int]]) -> list[dict]:
    kept = [{"name": name, "installs": n} for name, n in rows if n >= PUBLIC_MIN_GROUP]
    other = sum(n for _, n in rows if n < PUBLIC_MIN_GROUP)
    if other:
        kept.append({"name": "other", "installs": other})
    return kept


def public_stats(today: date) -> dict:
    since_30 = today - timedelta(days=30)
    since_7 = today - timedelta(days=7)
    with db.connection() as conn:
        active_30 = conn.execute("SELECT count(*) FROM installs WHERE last_seen >= %s", (since_30,)).fetchone()[0]
        active_7 = conn.execute("SELECT count(*) FROM installs WHERE last_seen >= %s", (since_7,)).fetchone()[0]
        versions = conn.execute(
            "SELECT hub_version, count(*) FROM installs WHERE last_seen >= %s GROUP BY 1 ORDER BY 2 DESC",
            (since_30,),
        ).fetchall()
        countries = conn.execute(
            "SELECT coalesce(country, 'unknown'), count(*) FROM installs WHERE last_seen >= %s "
            "GROUP BY 1 ORDER BY 2 DESC",
            (since_30,),
        ).fetchall()
    return {
        "generated_on": today.isoformat(),
        "active_installations_7d": active_7,
        "active_installations_30d": active_30,
        "hub_versions": _fold_small(versions),
        "countries": _fold_small(countries),
        "min_group_size": PUBLIC_MIN_GROUP,
    }
