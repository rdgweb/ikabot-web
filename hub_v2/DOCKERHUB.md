# ikabot-web-hub

Web panel and job queue for automating [Ikariam](https://www.ikariam.gameforge.com/), built on top of the [ikabot](https://github.com/Ikabot-Collective/ikabot) project.

Manage multiple game accounts from a single dashboard: schedule construction upgrades, automate resource donations and internal transport, run the market, train troops, collect daily rewards, and more — all running 24/7 on your own server.

This is the **Hub** — the Django web panel, REST API, and job queue. It pairs with [`blackoneal/ikabot-web-agent`](https://hub.docker.com/r/blackoneal/ikabot-web-agent), the worker that actually executes game actions. You need both, plus MariaDB and Redis, to run the system — see the Quick Start below.

**Full documentation, architecture, and source:** https://github.com/rdgweb/ikabot-web

## Quick Start

No need to clone the repository — just grab the two config files:

```bash
curl -LO https://raw.githubusercontent.com/rdgweb/ikabot-web/main/docker-compose.yml
curl -LO https://raw.githubusercontent.com/rdgweb/ikabot-web/main/.env.example
cp .env.example .env
```

Edit `.env` and **change every value that says `change-me`**, then start the stack:

```bash
docker compose --profile captcha pull
docker compose --profile captcha up -d
```

Open the panel at **http://localhost:8000**. On first boot the hub creates an admin user from `ADMIN_USERNAME`/`ADMIN_PASSWORD` in your `.env`.

## Required environment variables

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Django cryptographic key — long random string |
| `DB_PASSWORD` / `MYSQL_ROOT_PASSWORD` | MariaDB credentials |
| `REDIS_PASSWORD` | Redis password |
| `APP_SECRET` | Internal encryption key (proxy/account credentials at rest) |
| `AGENT_TOKEN` | Shared token between hub and agent — must match on both |
| `ADMIN_PASSWORD` | Password for the initial admin user |

Optional: `DJANGO_ALLOWED_HOSTS`, `WEBSHARE_API_KEY` (proxy rotation), `IKABOTAPI_URL` (captcha/blackbox token solver), `HUB_PORT` (default `8000`).

## Architecture

| Service | Image | Role |
|---------|-------|------|
| Hub | `blackoneal/ikabot-web-hub` (this image) | Web panel, REST API, job queue |
| Agent | `blackoneal/ikabot-web-agent` | Executes queued game actions |
| Supervisor | `blackoneal/ikabot-web-supervisor` | Optional, per-host: applies agent updates requested from the hub |
| MariaDB | `mariadb` | Persistent storage |
| Redis | `redis` | Job queue and caching |

## Tags

- `latest` — most recent build from `main`
- `X.Y.Z` / `vX.Y.Z` — a specific released version
- `sha-XXXXXXX` — a specific commit

## Updating

```bash
docker compose pull
docker compose up -d
```

## Links

- Full README, troubleshooting, remote agents, updates: https://github.com/rdgweb/ikabot-web
- Issues: https://github.com/rdgweb/ikabot-web/issues
- Built on [ikabot](https://github.com/Ikabot-Collective/ikabot) by the Ikabot Collective
