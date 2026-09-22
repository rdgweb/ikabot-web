# ikabot-web-hub

Web panel and job queue for automating [Ikariam](https://www.ikariam.gameforge.com/), built on top of the [ikabot](https://github.com/Ikabot-Collective/ikabot) project.

Manage multiple game accounts from a single dashboard: schedule construction upgrades, automate resource donations and internal transport, run the market, train troops, collect daily rewards, and more — all running 24/7 on your own server.

This is the **Hub** — the Django web panel, REST API, and job queue. It needs, at minimum: [`blackoneal/ikabot-web-agent`](https://hub.docker.com/r/blackoneal/ikabot-web-agent) (the worker that executes game actions), MariaDB, Redis, and **IkabotAPI** (generates the login token and solves captchas — the system cannot log into the game without it; see Architecture below). Everything below is copy-paste, no other files needed.

## Quick Start

Save this as `docker-compose.yml`:

```yaml
services:
  mariadb:
    image: mariadb:11
    restart: unless-stopped
    environment:
      MARIADB_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MARIADB_DATABASE: ${DB_NAME:-ikabot_hub}
      MARIADB_USER: ${DB_USER:-ikabot}
      MARIADB_PASSWORD: ${DB_PASSWORD}
    volumes:
      - mariadb_data:/var/lib/mysql
    healthcheck:
      test: ["CMD-SHELL", "mariadb-admin ping -h 127.0.0.1 -uroot -p$$MARIADB_ROOT_PASSWORD --silent"]
      interval: 10s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD}"]
    restart: unless-stopped
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$$REDIS_PASSWORD\" ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  ikabotapi:
    build:
      context: https://github.com/Ikabot-Collective/IkabotAPI.git
    restart: unless-stopped
    ports:
      - "5005:5005"

  hub:
    image: blackoneal/ikabot-web-hub:latest
    restart: unless-stopped
    depends_on:
      mariadb: {condition: service_healthy}
      redis: {condition: service_healthy}
    environment:
      DJANGO_SECRET_KEY: ${DJANGO_SECRET_KEY}
      DJANGO_ALLOWED_HOSTS: localhost,127.0.0.1,hub
      ADMIN_USERNAME: ${ADMIN_USERNAME:-admin}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD}
      DB_NAME: ${DB_NAME:-ikabot_hub}
      DB_USER: ${DB_USER:-ikabot}
      DB_PASSWORD: ${DB_PASSWORD}
      DB_HOST: mariadb
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      APP_SECRET: ${APP_SECRET}
      AGENT_TOKEN: ${AGENT_TOKEN}
      IKABOTAPI_URL: http://ikabotapi:5005
    ports:
      - "8000:8000"
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8000/ || exit 1"]
      interval: 15s
      timeout: 6s
      retries: 10

  agent:
    image: blackoneal/ikabot-web-agent:latest
    restart: unless-stopped
    depends_on:
      hub: {condition: service_healthy}
      redis: {condition: service_healthy}
    environment:
      HUB_URL: http://hub:8000
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      AGENT_TOKEN: ${AGENT_TOKEN}
      AGENT_NAME: ikabot-agent-01

volumes:
  mariadb_data:
  redis_data:
```

Save this as `.env` next to it, **changing every value**:

```bash
MYSQL_ROOT_PASSWORD=change-me-root-password
DB_PASSWORD=change-me-db-password
REDIS_PASSWORD=change-me-redis-password
DJANGO_SECRET_KEY=change-me-to-a-long-random-string
APP_SECRET=change-me-app-secret
AGENT_TOKEN=change-me-agent-token
ADMIN_PASSWORD=change-me-admin-password
```

Then:

```bash
docker compose up -d
```

The first `ikabotapi` build takes a few minutes (installs Playwright + Chromium). Once everything is healthy, open **http://localhost:8000** and log in with `admin` / the `ADMIN_PASSWORD` you set.

## Architecture

| Service | Image | Role | Required? |
|---------|-------|------|-----------|
| Hub | `blackoneal/ikabot-web-hub` (this image) | Web panel, REST API, job queue | Yes |
| Agent | `blackoneal/ikabot-web-agent` | Executes queued game actions | Yes |
| **IkabotAPI** | built from [`Ikabot-Collective/IkabotAPI`](https://github.com/Ikabot-Collective/IkabotAPI) | Generates the login (blackbox) token and solves captchas | **Yes — the hub cannot log into the game without it** |
| MariaDB | `mariadb` | Persistent storage | Yes |
| Redis | `redis` | Job queue and caching | Yes |
| Supervisor | `blackoneal/ikabot-web-supervisor` | Per-host, applies agent updates requested from the hub | No, optional |

## Tags

- `latest` — most recent build from `main`
- `vX.Y.Z` — a specific released version

## Updating

```bash
docker compose pull
docker compose up -d
```

## Links

- Built on [ikabot](https://github.com/Ikabot-Collective/ikabot) by the Ikabot Collective
- IkabotAPI: [Ikabot-Collective/IkabotAPI](https://github.com/Ikabot-Collective/IkabotAPI)
