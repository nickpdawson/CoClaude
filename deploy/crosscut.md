# CoClaude — Ops Runbook (crosscut deployment)

**Deployed:** 2026-07-20 · **Host:** crosscut (CT 224 on gallatin, 10.15.25.25, DEV VLAN 1525) · `/opt/coclaude`
**Public URL:** https://coclaude.dzsec.net (connector URL for Claude: `https://coclaude.dzsec.net/mcp`)

## Path of a request

```
Claude (Anthropic cloud 160.79.104.0/21)
  → Cloudflare (coclaude.dzsec.net, proxied/orange)
  → WAN → whistler NPM 10.25.1.182:443 (proxy host #39, LE cert #66 via CF DNS-01)
  → crosscut 10.15.25.25:8788 (docker: coclaude)      [needs pfSense DMZ→DEV rule: dev_servers/dev_ports]
```

Internal LAN: AD DNS A record `coclaude.dzsec.net → 10.25.1.182` (zone `dzsec.net`).

## Operate

```sh
ssh administrator@10.15.25.25          # DZsec Admins key
cd /opt/coclaude
docker logs coclaude --tail 50
docker compose restart                  # config change
docker compose up -d --build            # after code rsync
sqlite3 data/coclaude.db                # state (host path; container sees /data)
```

- Secrets: `/opt/coclaude/.env` (600). Holds Google client secret, SMTP creds, ADMIN_SETUP_KEY.
- State: `/opt/coclaude/data/coclaude.db` (SQLite WAL). Holds the **Google refresh token**, collaborator ACL, hashed tokens. Back this dir up (PBS covers CT 224? — verify).
- Update from ridge: `rsync -a --delete --exclude .venv --exclude data --exclude private --exclude .git ~/Development/CoClaude/ administrator@10.15.25.25:/opt/coclaude/ && ssh administrator@10.15.25.25 'cd /opt/coclaude && docker compose up -d --build'`

## Bootstrap / recovery

- **Google consent (one-time, owner):** visit `https://coclaude.dzsec.net/oauth/google/start?key=<ADMIN_SETUP_KEY from .env>` → Google consent as nickpdawson@gmail.com → stores refresh token. Re-run if token revoked (revoke first at myaccount.google.com/permissions or exchange returns no refresh_token).
- **Owner invite code:** printed to `docker logs coclaude` on first boot (`grep "invite code"`). New invite for anyone: owner runs the `add_collaborator` MCP tool.
- **Forgotten password:** no reset flow in v1 — owner re-runs `add_collaborator` with their email (creates fresh invite; keeps identity/grants).

## NPM notes (whistler)

- Proxy host #39: coclaude.dzsec.net → http://10.15.25.25:8788, websockets ON, Force SSL, HTTP/2, advanced: `proxy_buffering off; proxy_cache off; proxy_read_timeout/send_timeout 3600s`.
- Cert #66: LE via **Cloudflare DNS-01** (same CF creds as hs.dzsec.net cert #65) — auto-renews in NPM.
- Created via API with a temporary `coclaude-deploy@dzsec.net` admin (bcrypt+sqlite pattern from the Lapland runbook); **removed after** — user table is back to just administrator@dzsec.net.

## Claude connector compatibility (critical)

Claude custom connectors (Desktop + web) silently reject a FastMCP 2.14 server even when the whole handshake returns 200s (you'll see "not connected" + no tools). Three settings are required and are in the code — do not remove:
- `FastMCP(..., include_fastmcp_meta=False)`
- `@mcp.tool(output_schema=None)` on every tool
- `app.run(..., json_response=True)`

These make `tools/list` entries exactly `{name, description, inputSchema}`. If tools stop appearing after a FastMCP upgrade, re-check that the response carries no `outputSchema`/`_meta`.

## Gotchas

- **CF proxied (orange):** idle streams cut ~100s (524). MCP POSTs fine. If Claude connector SSE flakes → flip CF record to DNS-only (grey), nothing else changes.
- Claude re-registers a fresh DCR client per (re)connect — `oauth_clients` rows accumulate; purged automatically after 90 days unused.
- Section headings in docs are anchors — renaming "Live/Deciding/Decided" in a doc breaks tool targeting (fuzzy match helps; error lists real sections).
- Google consent screen must stay **In production** or the refresh token dies weekly.
