# ikabot-web-agent

Worker container for [ikabot-web](https://github.com/rdgweb/ikabot-web) — pulls queued jobs from the Hub and executes them against [Ikariam](https://www.ikariam.gameforge.com/): construction, resource donations/transport, market, military, espionage, and more.

This is the **Agent**. It does nothing on its own — it needs a running [`blackoneal/ikabot-web-hub`](https://hub.docker.com/r/blackoneal/ikabot-web-hub) to connect to. The default `docker-compose.yml` already starts one agent alongside the hub; use this image directly when you want to run **extra agents** (e.g. on a separate VPS/IP) to spread work across different proxies.

**Full documentation and source:** https://github.com/rdgweb/ikabot-web

## Quick Start (full stack)

```bash
curl -LO https://raw.githubusercontent.com/rdgweb/ikabot-web/main/docker-compose.yml
curl -LO https://raw.githubusercontent.com/rdgweb/ikabot-web/main/.env.example
cp .env.example .env
# edit .env, then:
docker compose --profile captcha up -d
```

## Running a standalone remote agent

On the remote machine, once you have a hub already running:

```bash
docker run -d --restart unless-stopped \
  -e HUB_URL=http://YOUR_HUB_IP:8000 \
  -e REDIS_URL=redis://:YOUR_REDIS_PASSWORD@YOUR_HUB_IP:6379/0 \
  -e AGENT_TOKEN=YOUR_AGENT_TOKEN \
  -e AGENT_NODE_ID=any-unique-id \
  -e AGENT_NAME=agent-vps-01 \
  blackoneal/ikabot-web-agent:latest
```

`AGENT_TOKEN` must match the token configured on the hub, and the hub's ports must be reachable from this machine. The hub's **Nós** (Nodes) page generates this exact command pre-filled for you, including labels the optional supervisor needs for managed updates.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `HUB_URL` | Base URL of the hub this agent reports to |
| `REDIS_URL` | Redis connection string (job queue) |
| `AGENT_TOKEN` | Shared secret — must match the hub's `AGENT_TOKEN` |
| `AGENT_NODE_ID` | Unique identifier for this agent/node |
| `AGENT_NAME` | Friendly name shown in the hub's dashboard |

## Tags

- `latest` — most recent build from `main`
- `X.Y.Z` / `vX.Y.Z` — a specific released version
- `sha-XXXXXXX` — a specific commit

The hub's **Atualizações** page compares each running agent's reported version against the tags published here and can request an update per node (applied by the optional supervisor, with automatic rollback on failure).

## Links

- Full README, architecture, troubleshooting: https://github.com/rdgweb/ikabot-web
- Issues: https://github.com/rdgweb/ikabot-web/issues
- Built on [ikabot](https://github.com/Ikabot-Collective/ikabot) by the Ikabot Collective
