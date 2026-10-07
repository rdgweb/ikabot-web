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

## Pinned revision and what the hub expects from it (N-74)

`docker-compose.yml` builds `ikabotapi` from a **fixed commit**, not from the
branch: `fb87efee84e476c74829a7a5a91cea5f3beeb93e` (upstream #41, 2026-07-08).
Every login depends on this service, so it only changes when someone decides to.

What the hub relies on at this revision:

| Call | Used for | Notes |
| --- | --- | --- |
| `GET /v1/token?user_agent=` | blackbox token | `user_agent` must be in `SupportedUserAgents.json` (override above), otherwise HTTP 400 |
| `GET /v1/token?...&locale=&timezone_id=` | same token, generated in a browser context with that locale and timezone | both optional; defaults `en-GB` / `Europe/London`; a value that does not look like a locale / IANA timezone is HTTP 400 |
| captcha routes | pirate and lobby captchas | unchanged by N-74 |

The hub sends `locale` / `timezone_id` only when the agent asks for them, which it
does when the system settings "Idioma do navegador no login" / "Fuso horario do
navegador no login" are filled in (Configuracoes > politica de snapshot). Empty
settings mean the request is exactly what it was before N-74.

Compatibility both ways:

- **Older ikabotapi** (before #41, e.g. `34a070c`): it rejects the unknown
  parameters with 400/422. The hub then asks again with `user_agent` only, logs a
  warning and answers `context_applied: false`; the login goes on with the token
  the old service knows how to make.
- **Newer ikabotapi**: `75fee72` (#42, 2026-10-07) only changes the pirate captcha
  upload validation; not adopted yet because it was not tested here.

To move the pin: read the upstream diff since the pinned commit, rebuild
(`docker compose build ikabotapi`), check one token with and without
`locale`/`timezone_id` and one real login, then change the hash in
`docker-compose.yml` and this section.
