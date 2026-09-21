#!/bin/sh
# Read-only role used by Grafana. Tables are created later by the API (owner: telemetry),
# so default privileges make every future table readable by grafana_ro.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE grafana_ro LOGIN PASSWORD '${GRAFANA_RO_PASSWORD}';
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
SQL
