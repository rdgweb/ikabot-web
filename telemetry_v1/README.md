# ikabot telemetry

Receiver and dashboards for the **opt-in, anonymous** usage telemetry of ikabot hubs
(client code: `hub_v2/apps/telemetry`). Public explanation for users: `/transparencia`.

```
hub (opt-in, 1 ping/day) ──HTTPS──> gateway (nginx, no access log)
                                      ├── /            -> api      (FastAPI: validate, derive country, store)
                                      └── /grafana/    -> grafana  (read-only role, provisioned dashboards)
                                                          db (Postgres, private network)
```

## Data handling (keep the code, the hub UI and /transparencia consistent)

- **Consent first**: the hub sends nothing until an admin opts in; disabling asks the receiver to erase everything.
- **What is stored**: the schema-v1 payload (`api/app/schemas.py`; unknown fields are dropped) plus the client **IP**,
  seen by the receiver on the connection (never sent in the JSON). The IP lives in its own table (`install_ips`)
  with a **90-day retention**; everything else is kept up to **400 days**. Both purges run daily.
- **Location**: IP -> country/region/city/approx. lat-lon via the free DB-IP City Lite database
  (downloaded and refreshed monthly into the `geoip` volume; `GEOIP_AUTO_UPDATE=false` to disable). Private/unroutable
  addresses and installs without a database fall back to the country of the client-declared IANA timezone.
  DB-IP data is CC BY 4.0: keep the attribution on `/transparencia`.
- **No request logs**: nginx `access_log off`, uvicorn `--no-access-log`. If a reverse proxy sits in front
  (Nginx Proxy Manager), add `access_log off;` in the host's *Advanced* tab and make sure it forwards the client address (`X-Real-IP`, or `CF-Connecting-IP` when behind Cloudflare, which the gateway prefers).
- **Erasure**: `DELETE /v1/installs/<install_id>` removes everything an install sent, IP included.
- **Public stats** fold groups smaller than 5 into "other"; IPs and cities are never published.
- **Access**: only the maintainer (Grafana login, or `psql` on the private database). Grafana is not public.

## Run

```bash
cp .env.example .env        # fill in the three passwords and TELEMETRY_PUBLIC_URL
docker compose up -d --build
```

- Gateway listens on `${TELEMETRY_PORT:-8088}`; point the reverse proxy for `telemetry.<domain>` at it.
- Grafana: `${TELEMETRY_PUBLIC_URL}/grafana/` (login `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`).
  The datasource and the **"ikabot — visão geral de uso"** dashboard are provisioned from `grafana/`.
- Dashboards are code: edit `grafana/dashboards/ikabot-overview.json` (UI edits are disabled).

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/ping` | body = schema v1 (see `/transparencia`); `204` ok, `422` invalid, `429` >1/min per install |
| `DELETE` | `/v1/installs/{install_id}` | idempotent erase |
| `GET` | `/v1/stats/public` | aggregated, small groups folded |
| `GET` | `/`, `/transparencia` | public transparency page |

## Tests

```bash
docker compose run --rm --no-deps -e DATABASE_URL=postgresql://telemetry:<pw>@db:5432/telemetry \
  -v "$PWD/api/tests:/srv/tests" -v "$PWD/api/pytest.ini:/srv/pytest.ini" api python -m pytest -q
```

(Tests `TRUNCATE` the tables — run them against a scratch database, never the production one.)

## Changing the payload

Update all three together: `hub_v2/apps/telemetry/services.py::build_payload`,
`api/app/schemas.py` and `api/static/transparencia.html` — and bump `SCHEMA_VERSION` for breaking changes.
