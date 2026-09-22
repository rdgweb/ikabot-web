# ikabot-web-agent

Worker container for the ikabot-web system — pulls queued jobs from the Hub and executes them against [Ikariam](https://www.ikariam.gameforge.com/): construction, resource donations/transport, market, military, espionage, and more.

This is the **Agent**. It does nothing on its own — it needs a running [`blackoneal/ikabot-web-hub`](https://hub.docker.com/r/blackoneal/ikabot-web-hub) to connect to (see that image's page for the full stack). Use this image directly when you want to run **extra agents** — e.g. on a separate VPS/IP — to spread work across different proxies, beyond the one agent already started alongside the hub.

## Running a standalone remote agent

On the remote machine, once you already have a hub running elsewhere:

```bash
docker run -d --restart unless-stopped \
  -e HUB_URL=http://YOUR_HUB_IP:8000 \
  -e REDIS_URL=redis://:YOUR_REDIS_PASSWORD@YOUR_HUB_IP:6379/0 \
  -e AGENT_TOKEN=YOUR_AGENT_TOKEN \
  -e AGENT_NODE_ID=any-unique-id \
  -e AGENT_NAME=agent-vps-01 \
  blackoneal/ikabot-web-agent:latest
```

`AGENT_TOKEN` must match the token configured on your hub, and the hub's ports must be reachable from this machine. Your hub's own **Nós** (Nodes) page generates this exact command pre-filled with your values, including labels the optional supervisor image needs for managed updates.

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
- `vX.Y.Z` — a specific released version

Your hub's **Atualizações** page compares each running agent's reported version against the tags published here and can request an update per node (applied by the optional supervisor image, with automatic rollback on failure).

## Links

- Built on [ikabot](https://github.com/Ikabot-Collective/ikabot) by the Ikabot Collective
