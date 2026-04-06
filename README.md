# ikabot-web

Web panel and automation agent for [Ikariam](https://www.ikariam.gameforge.com/) built on top of the [ikabot](https://github.com/Ikabot-Collective/ikabot) project.

Manage multiple game accounts from a single dashboard: schedule construction upgrades, send resources, train troops, collect daily rewards, and more -- all running 24/7 on your own server.

## Architecture

| Service | Role |
|---------|------|
| **Hub** (`hub_v2`) | Django web panel, REST API, job queue |
| **Agent** (`agent_v2`) | Worker that picks jobs from the hub and executes game actions |
| **MariaDB** | Persistent storage |
| **Redis** | Job queue and caching |

The hub and agent communicate over an internal API. You can run additional agents on remote servers (VPS) to distribute the workload.

## Requirements

- [Docker](https://docs.docker.com/get-docker/) (v20+)
- [Docker Compose](https://docs.docker.com/compose/install/) (v2+)

Verify your installation:

```bash
docker --version
docker compose version
```

## Quick Start

### Option A -- Using prebuilt images (recommended)

No need to clone the full repository. Just grab the two config files:

```bash
# Download docker-compose.yml and .env.example
curl -LO https://raw.githubusercontent.com/rdgweb/ikabot-web/main/docker-compose.yml
curl -LO https://raw.githubusercontent.com/rdgweb/ikabot-web/main/.env.example

# Create your .env
cp .env.example .env
```

Edit `.env` and **change every value that says `change-me`**:

```bash
nano .env    # or use any text editor
```

Start the stack (with IkabotAPI for game login):

```bash
docker compose --profile captcha pull
docker compose --profile captcha up -d
```

### Option B -- Building from source

```bash
git clone https://github.com/rdgweb/ikabot-web.git
cd ikabot-web
cp .env.example .env
```

Edit `.env` and **change every value that says `change-me`**.

```bash
docker compose up -d --build
```

### Verify everything is running

```bash
docker compose ps
```

All services should show `healthy` or `running`. Open the panel at **http://localhost:8000**.

## First Login

On the first boot the hub automatically creates an admin user using the credentials from your `.env`:

| Variable | Default |
|----------|---------|
| `ADMIN_USERNAME` | `admin` |
| `ADMIN_PASSWORD` | *(what you set in `.env`)* |

Log in at **http://localhost:8000** with those credentials.

## Configuration Reference

All configuration is done through the `.env` file. Here are the key variables:

### Required (must change)

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Django cryptographic key -- use a long random string |
| `DB_PASSWORD` | MariaDB user password |
| `MYSQL_ROOT_PASSWORD` | MariaDB root password |
| `REDIS_PASSWORD` | Redis password |
| `APP_SECRET` | Internal encryption key |
| `AGENT_TOKEN` | Shared token between hub and agent -- must match on both |
| `ADMIN_PASSWORD` | Password for the initial admin user |

### Optional

| Variable | Purpose | Default |
|----------|---------|---------|
| `DJANGO_ALLOWED_HOSTS` | Hostnames the hub accepts | `localhost,127.0.0.1` |
| `WEBSHARE_API_KEY` | Proxy rotation API key (webshare.io) | *(empty)* |
| `IKABOTAPI_URL` | External captcha/token solver URL | *(empty)* |
| `HUB_PORT` | Port for the web panel | `8000` |
| `DB_PORT` | Exposed MariaDB port | `3306` |
| `REDIS_PORT` | Exposed Redis port | `6379` |

## Adding a Game Account

1. Open the panel at **http://localhost:8000**
2. Go to **Accounts** and click **Add Account**
3. Enter your Ikariam email and password
4. The agent will log in and sync your cities automatically

## Running a Remote Agent

You can run extra agents on other machines (e.g. a VPS) to distribute actions across different IPs.

On the remote machine:

```bash
docker run -d --restart unless-stopped \
  -e HUB_URL=http://YOUR_HUB_IP:8000 \
  -e REDIS_URL=redis://:YOUR_REDIS_PASSWORD@YOUR_HUB_IP:6379/0 \
  -e AGENT_TOKEN=YOUR_AGENT_TOKEN \
  -e AGENT_NODE_ID=any-unique-id \
  -e AGENT_NAME=agent-vps-01 \
  blackoneal/ikabot-web-agent:latest
```

Make sure `AGENT_TOKEN` matches the one in your hub's `.env` and that ports `8000` and `6379` are accessible from the remote machine.

## IkabotAPI (Blackbox Token & Captcha)

[IkabotAPI](https://github.com/Ikabot-Collective/IkabotAPI) generates blackbox tokens (required for game login) and solves captchas. **It is required for the system to work properly.**

Start the stack with the `captcha` profile to include it:

```bash
docker compose --profile captcha up -d
```

This builds IkabotAPI directly from the official repository. The hub already points to it by default (`http://ikabotapi:5005`).

> **Note:** The first build takes several minutes because it installs Playwright + Chromium.

## Optional Tools

### Captcha Solver (IkabotAPI)

```bash
docker compose --profile captcha up -d
```

### phpMyAdmin

```bash
docker compose --profile tools up -d
```

Access at **http://localhost:8080**.

### All optional services at once

```bash
docker compose --profile captcha --profile tools up -d
```

## Updating

### Prebuilt images

```bash
docker compose pull
docker compose up -d
```

### From source

```bash
git pull
docker compose up -d --build
```

## Troubleshooting

### Panel does not open

```bash
docker compose logs -f hub
```

Check that `DJANGO_ALLOWED_HOSTS` includes the hostname or IP you are using.

### Agent does not connect

- Verify `AGENT_TOKEN` is the same in both hub and agent
- Check that the hub is healthy: `docker compose ps`
- Check agent logs: `docker compose logs -f agent`

### Captcha/token errors

- Verify `IKABOTAPI_URL` is set and reachable from the hub container
- Test connectivity: `docker compose exec hub curl -s http://ikabotapi:5005/`

## Common Commands

```bash
docker compose --profile captcha up -d  # Start all services (recommended)
docker compose down            # Stop all services
docker compose logs -f hub     # Follow hub logs
docker compose logs -f agent   # Follow agent logs
docker compose restart hub     # Restart hub only
docker compose build --no-cache  # Rebuild images from scratch
```

## License

This project builds upon [ikabot](https://github.com/Ikabot-Collective/ikabot) by the Ikabot Collective.
