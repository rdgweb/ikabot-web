# ikabot-web-supervisor

Minimal, **outbound-only** sidecar for [ikabot-web](https://github.com/rdgweb/ikabot-web) Docker hosts. It lets the Hub apply requested [`blackoneal/ikabot-web-agent`](https://hub.docker.com/r/blackoneal/ikabot-web-agent) updates on a remote host — without ever opening an inbound port on that host.

You install **one supervisor per Docker host** (not per agent). It polls the Hub over outbound HTTPS, and only acts on update requests explicitly targeted at agent containers it manages on that host, with automatic rollback if the replacement container fails to come up healthy.

This image is entirely optional — without it, you update agents manually with `docker compose pull && docker compose up -d`. It exists to let an operator trigger that same update from the Hub's UI instead of SSHing into every host.

**Full documentation and source:** https://github.com/rdgweb/ikabot-web

## Getting the exact install command

The Hub generates a ready-to-run command (with your host's enrollment token filled in) on its **Nós → Docker Hosts** page — copy it from there rather than from a template. It looks like this:

```bash
docker run -d --name ikabot-host-supervisor --restart unless-stopped \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 100 --memory 192m --cpus 0.50 --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e HUB_URL=https://your-hub.example.com \
  -e IKABOT_HOST_ID=<generated-by-hub> \
  -e IKABOT_HOST_TOKEN=<generated-by-hub> \
  blackoneal/ikabot-web-supervisor:latest
```

## Security posture

- **No inbound port.** All communication is the supervisor polling the Hub over HTTPS — nothing listens on this container.
- **Read-only root filesystem**, all Linux capabilities dropped, `no-new-privileges`.
- Only manages containers it created for this host — it cannot touch unrelated containers, and only replaces the image tag/digest the Hub explicitly requested.
- The Docker socket mount is required (it manages containers on the host) — this is why the container is otherwise locked down as tightly as possible.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `HUB_URL` | Base URL of the hub this supervisor reports to |
| `IKABOT_HOST_ID` | This Docker host's ID, from the Hub |
| `IKABOT_HOST_TOKEN` | Enrollment token for this host, from the Hub |
| `SUPERVISOR_POLL_SECONDS` | Poll interval (default `30`) |

## Tags

- `latest` — most recent build from `main`
- `X.Y.Z` / `vX.Y.Z` — a specific released version
- `sha-XXXXXXX` — a specific commit

## Links

- Full README, architecture: https://github.com/rdgweb/ikabot-web
- Issues: https://github.com/rdgweb/ikabot-web/issues
