from __future__ import annotations

import os
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS installs (
    install_id      uuid PRIMARY KEY,
    first_seen      date NOT NULL,
    last_seen       date NOT NULL,
    hub_version     text NOT NULL,
    arch            text NOT NULL,
    timezone        text,
    country         char(2),
    lobby_accounts  integer NOT NULL DEFAULT 0,
    game_accounts   integer NOT NULL DEFAULT 0,
    nodes           integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pings (
    day             date NOT NULL,
    install_id      uuid NOT NULL REFERENCES installs(install_id) ON DELETE CASCADE,
    hub_version     text NOT NULL,
    arch            text NOT NULL,
    timezone        text,
    country         char(2),
    lobby_accounts  integer NOT NULL DEFAULT 0,
    game_accounts   integer NOT NULL DEFAULT 0,
    nodes           integer NOT NULL DEFAULT 0,
    PRIMARY KEY (day, install_id)
);

CREATE TABLE IF NOT EXISTS ping_worlds (
    day         date NOT NULL,
    install_id  uuid NOT NULL,
    server      text NOT NULL,
    world       integer NOT NULL,
    accounts    integer NOT NULL,
    PRIMARY KEY (day, install_id, server, world),
    FOREIGN KEY (day, install_id) REFERENCES pings(day, install_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ping_agents (
    day         date NOT NULL,
    install_id  uuid NOT NULL,
    version     text NOT NULL,
    nodes       integer NOT NULL,
    PRIMARY KEY (day, install_id, version),
    FOREIGN KEY (day, install_id) REFERENCES pings(day, install_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ping_usage (
    day         date NOT NULL,
    install_id  uuid NOT NULL,
    category    text NOT NULL,
    jobs        bigint NOT NULL,
    PRIMARY KEY (day, install_id, category),
    FOREIGN KEY (day, install_id) REFERENCES pings(day, install_id) ON DELETE CASCADE
);

ALTER TABLE installs ADD COLUMN IF NOT EXISTS region     text;
ALTER TABLE installs ADD COLUMN IF NOT EXISTS city       text;
ALTER TABLE installs ADD COLUMN IF NOT EXISTS latitude   real;
ALTER TABLE installs ADD COLUMN IF NOT EXISTS longitude  real;
ALTER TABLE installs ADD COLUMN IF NOT EXISTS geo_source text;

-- Raw client IPs live in their own table so retention can be shorter than the rest.
CREATE TABLE IF NOT EXISTS install_ips (
    install_id  uuid NOT NULL REFERENCES installs(install_id) ON DELETE CASCADE,
    day         date NOT NULL,
    ip          inet NOT NULL,
    PRIMARY KEY (install_id, day)
);

CREATE INDEX IF NOT EXISTS pings_install_idx ON pings (install_id);
CREATE INDEX IF NOT EXISTS installs_last_seen_idx ON installs (last_seen);
"""


def init_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            os.environ["DATABASE_URL"],
            min_size=1,
            max_size=5,
            kwargs={"autocommit": False},
            open=True,
        )
        _pool.wait(timeout=30)
        with _pool.connection() as conn:
            conn.execute(SCHEMA)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection():
    assert _pool is not None, "pool not initialised"
    with _pool.connection() as conn:
        yield conn
