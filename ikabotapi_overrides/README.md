# ikabotapi overrides

`ikabotapi` is a **third-party service** (built from
`https://github.com/Ikabot-Collective/IkabotAPI.git` — see `docker-compose.yml`),
not something this repo publishes or controls. This folder holds small file
overrides for it, applied at container start via a bind mount, so we can fix
things upstream hasn't without forking or patching that repo.

## SupportedUserAgents.json

`ikabotapi`'s `TokenGenerator.get_token(user_agent)` only uses the `user_agent`
it's given when that exact string is present in
`apps/token/SupportedUserAgents.json`; otherwise it **silently falls back** to
a random string from the `fake_useragent` library — while still launching a
real **Chromium** browser via Playwright to generate the blackbox token
regardless of which browser that random string claims to be.

`agent_v2/game_client/constants.py`'s `USER_AGENTS` pool is the same `user_agent`
the agent sends to the game server AND passes to `get_blackbox_token()`. So for
every login to present a consistent, current, Chromium-shaped fingerprint (see
N-37), both lists must contain the **exact same strings** — this file is that
list, kept byte-identical to `agent_v2/game_client/constants.py::USER_AGENTS`.

Upstream's own copy of this file (as of 2026-09-21) is stale and malformed:
several strings are truncated (e.g. `Safari/537.3` missing its trailing `6`,
`Firefox/124.` with a dangling dot) and it mixes in Firefox/Edge/Opera/IE
strings — served through a Chromium engine, that mismatch is itself a bot
signal. This override replaces it with a small, valid, Chrome/Chromium-only,
Windows+Linux pool.

`docker-compose.yml` mounts this file over the container's copy:

```yaml
ikabotapi:
  volumes:
    - ./ikabotapi_overrides/SupportedUserAgents.json:/ikabotapi/apps/token/SupportedUserAgents.json:ro
```

**Updating the pool:** edit this file and
`agent_v2/game_client/constants.py::USER_AGENTS` together, keep the entries
identical, then restart the `ikabotapi` container (`docker compose up -d --no-deps
--force-recreate ikabotapi`) — no rebuild needed, it's a bind mount.
