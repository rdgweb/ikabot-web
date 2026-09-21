"""Receiver tests. Need a Postgres reachable through DATABASE_URL (see README)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app import db, geoip, main, storage


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        with db.connection() as conn:
            conn.execute("TRUNCATE installs CASCADE")
        main._last_ping_by_install.clear()
        yield test_client


def payload(**overrides):
    body = {
        "schema": 1,
        "install_id": str(uuid.uuid4()),
        "hub_version": "0.2.80",
        "arch": "amd64",
        "timezone": "America/Cuiaba",
        "counts": {"lobby_accounts": 2, "game_accounts": 5, "nodes": 1},
        "agents": [{"version": "0.1.57", "nodes": 1}],
        "worlds": [{"server": "br", "world": 61, "accounts": 3}, {"server": "br", "world": 44, "accounts": 2}],
        "usage_30d": [{"category": "military", "jobs": 120}],
    }
    body.update(overrides)
    return body


def scalar(sql, *args):
    with db.connection() as conn:
        return conn.execute(sql, args).fetchone()[0]


def test_valid_ping_is_stored_with_country_from_timezone(client):
    body = payload()
    assert client.post("/v1/ping", json=body).status_code == 204

    assert scalar("SELECT count(*) FROM installs") == 1
    assert scalar("SELECT country FROM installs") == "BR"
    assert scalar("SELECT sum(accounts) FROM ping_worlds") == 5
    assert scalar("SELECT jobs FROM ping_usage WHERE category = 'military'") == 120


def test_same_day_ping_replaces_previous_snapshot(client):
    body = payload()
    client.post("/v1/ping", json=body)
    main._last_ping_by_install.clear()
    body["counts"]["game_accounts"] = 9
    body["worlds"] = [{"server": "es", "world": 7, "accounts": 9}]
    assert client.post("/v1/ping", json=body).status_code == 204

    assert scalar("SELECT count(*) FROM pings") == 1
    assert scalar("SELECT game_accounts FROM installs") == 9
    assert scalar("SELECT count(*) FROM ping_worlds") == 1


def test_unknown_fields_are_dropped_and_never_stored(client):
    body = payload(email="leak@example.com", extra={"password": "hunter2"})
    body["counts"]["ip"] = "203.0.113.5"
    assert client.post("/v1/ping", json=body).status_code == 204

    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM installs").fetchall()
        columns = [d.name for d in conn.execute("SELECT * FROM installs").description]
    assert "email" not in columns and "ip" not in columns
    assert "leak@example.com" not in str(rows) and "hunter2" not in str(rows)


def test_unknown_timezone_gives_no_country(client):
    assert client.post("/v1/ping", json=payload(timezone="Mars/Olympus")).status_code == 204
    assert scalar("SELECT country FROM installs") is None
    assert scalar("SELECT timezone FROM installs") is None


@pytest.mark.parametrize(
    "bad",
    [
        {"schema": 2},
        {"install_id": "not-a-uuid"},
        {"hub_version": "x" * 100},
        {"hub_version": "1.0; DROP TABLE installs"},
        {"arch": "mips"},
        {"counts": {"game_accounts": -1}},
        {"worlds": [{"server": "BRASIL", "world": 1, "accounts": 1}]},
        {"worlds": [{"server": "br", "world": 0, "accounts": 1}]},
    ],
)
def test_invalid_payloads_are_rejected(client, bad):
    assert client.post("/v1/ping", json=payload(**bad)).status_code == 422
    assert scalar("SELECT count(*) FROM installs") == 0


def test_garbage_and_oversized_bodies_are_rejected(client):
    assert client.post("/v1/ping", content=b"not json").status_code == 422
    assert client.post("/v1/ping", content=b"{" + b" " * (40 * 1024) + b"}").status_code == 413


def test_rate_limit_per_install(client):
    body = payload()
    assert client.post("/v1/ping", json=body).status_code == 204
    assert client.post("/v1/ping", json=body).status_code == 429


def test_erase_removes_everything_for_that_install_only(client):
    mine, other = payload(), payload()
    client.post("/v1/ping", json=mine)
    client.post("/v1/ping", json=other)

    assert client.delete(f"/v1/installs/{mine['install_id']}").status_code == 204
    assert client.delete(f"/v1/installs/{mine['install_id']}").status_code == 204  # idempotent

    assert scalar("SELECT count(*) FROM installs") == 1
    assert scalar("SELECT count(*) FROM ping_worlds") == 2
    assert scalar("SELECT install_id::text FROM installs") == other["install_id"]


def test_public_stats_fold_small_groups(client):
    for _ in range(5):
        client.post("/v1/ping", json=payload(hub_version="0.2.80"))
    client.post("/v1/ping", json=payload(hub_version="0.2.50", timezone="Europe/Lisbon"))

    stats = client.get("/v1/stats/public").json()
    assert stats["active_installations_30d"] == 6
    assert {"name": "0.2.80", "installs": 5} in stats["hub_versions"]
    assert {"name": "other", "installs": 1} in stats["hub_versions"]
    assert {"name": "BR", "installs": 5} in stats["countries"]
    assert all(item["name"] != "0.2.50" for item in stats["hub_versions"])


def test_transparency_page_and_health(client):
    assert client.get("/healthz").json() == {"ok": True}
    page = client.get("/transparencia")
    assert page.status_code == 200 and "Transparência" in page.text


def test_ip_is_stored_from_forwarded_header_with_timezone_country_fallback(client):
    body = payload()
    assert client.post("/v1/ping", json=body, headers={"X-Real-IP": "203.0.113.7"}).status_code == 204

    assert scalar("SELECT host(ip) FROM install_ips") == "203.0.113.7"
    assert scalar("SELECT geo_source FROM installs") == "timezone"  # no geoip database in tests
    assert scalar("SELECT country FROM installs") == "BR"


def test_geoip_result_overrides_timezone_country(client, monkeypatch):
    monkeypatch.setattr(
        geoip, "lookup",
        lambda ip: geoip.Geo(country="PT", region="Lisbon", city="Lisbon", latitude=38.72, longitude=-9.14),
    )
    assert client.post("/v1/ping", json=payload(), headers={"X-Real-IP": "198.51.100.9"}).status_code == 204

    assert scalar("SELECT country FROM installs") == "PT"
    assert scalar("SELECT city FROM installs") == "Lisbon"
    assert scalar("SELECT geo_source FROM installs") == "ip"
    assert scalar("SELECT country FROM pings") == "PT"


def test_invalid_forwarded_ip_is_ignored(client):
    assert client.post("/v1/ping", json=payload(), headers={"X-Real-IP": "not-an-ip; DROP TABLE x"}).status_code == 204
    assert scalar("SELECT count(*) FROM installs") == 1


def test_private_addresses_are_not_geolocated():
    assert not geoip.is_public("192.168.3.202")
    assert not geoip.is_public("10.0.0.5")
    assert geoip.is_public("8.8.8.8")
    assert geoip.lookup("192.168.3.202") == geoip.Geo()


def test_erase_also_removes_the_ip(client):
    body = payload()
    client.post("/v1/ping", json=body, headers={"X-Real-IP": "203.0.113.7"})
    assert scalar("SELECT count(*) FROM install_ips") == 1

    client.delete(f"/v1/installs/{body['install_id']}")
    assert scalar("SELECT count(*) FROM install_ips") == 0


def test_ips_are_purged_after_their_shorter_retention(client):
    body = payload()
    client.post("/v1/ping", json=body, headers={"X-Real-IP": "203.0.113.7"})
    with db.connection() as conn:
        conn.execute("UPDATE install_ips SET day = current_date - 100")

    storage.purge_older_than(main._today(), main.RETENTION_DAYS, main.IP_RETENTION_DAYS)

    assert scalar("SELECT count(*) FROM install_ips") == 0
    assert scalar("SELECT count(*) FROM installs") == 1  # the rest is kept until 400 days
