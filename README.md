# CoClaude

A self-hosted [MCP](https://modelcontextprotocol.io) server that lets several
people — **each using their own Claude account** — jointly read *and write* a
shared set of Google Docs. It turns a Google Doc into durable, co-owned context
that every collaborator's Claude can see and update, so a group can think and
decide together across separate chats and sessions.

Claude's first-party Google Drive connector is read-only and per-account.
CoClaude is the missing *writable, shared* layer.

## How it works

- **One owner, one Google credential.** The person who runs the server connects
  *their* Google account once. All Docs reads and writes go through that single
  credential. Collaborators never connect Google at all.
- **Collaborators authenticate only to CoClaude.** The server is a full OAuth 2.1
  authorization server (Dynamic Client Registration + PKCE — what Claude custom
  connectors require) with an invite-code sign-in page. The owner invites someone
  by email; they add the connector, enter their invite code, and pick a password.
- **Projects scope access.** A project is a named set of docs plus the
  collaborators granted to it. Every tool call is checked against that ACL.
- **Docs are structured, not freeform.** New docs are scaffolded with an
  *Overview & Instructions* section (onboarding for any AI that reads the doc)
  plus working sections (Live / Deciding / Decided by default). Nothing is ever
  deleted — retired text is struck through so history stays visible.

There is no web UI. All interaction is through each person's Claude via MCP tools.

## Requirements

- **A host** you control with a public HTTPS endpoint (a reverse proxy in front
  of the server — see [Reverse proxy](#reverse-proxy)). Python 3.12 for local
  dev, or Docker for deployment.
- **A Google Cloud project** with an OAuth web client and the Docs + Drive APIs
  enabled (see below).
- **An SMTP server** to email invite codes (any host/user/pass — e.g. your own
  mail server, or a transactional provider).

## Google Cloud setup (one-time)

1. Create (or reuse) a project at <https://console.cloud.google.com>.
2. **Enable both APIs** — *Google Docs API* **and** *Google Drive API*. They are
   separate; Docs alone can create/edit content but Drive is needed to share docs
   to collaborators and to detect changes.
3. **OAuth consent screen → Data access.** Add the scopes CoClaude uses:
   - `https://www.googleapis.com/auth/documents` (sensitive) — read/write the
     content of any doc the owner can access, including pre-existing ones.
   - `https://www.googleapis.com/auth/drive` (restricted) — share those docs to
     collaborators and read modified-time for change tracking.

   > **Narrower alternative:** if you only ever want CoClaude to create and manage
   > *its own* docs, use `https://www.googleapis.com/auth/drive.file` instead and
   > set `SCOPES` in `src/coclaude/google/client.py` to match. That avoids the
   > sensitive/restricted-scope verification friction, at the cost of not being
   > able to attach docs the app didn't create.
4. **Publish the consent screen to *In production*.** In *Testing* mode Google
   expires the refresh token after 7 days. As the sole user (the owner) you can
   grant sensitive/restricted scopes past the "Google hasn't verified this app"
   warning — only the owner ever sees it; collaborators never touch Google.
5. **Create an OAuth client** of type *Web application* and add two redirect URIs:
   - `https://YOUR-DOMAIN/oauth/google/callback`
   - `http://localhost:8788/oauth/google/callback` (for local testing)

   Copy the client ID and secret into `.env`.

## Configure

Copy `.env.example` to `.env` (`chmod 600`) and fill it in:

| Variable | What it is |
|---|---|
| `PUBLIC_URL` | Public base URL of the server, e.g. `https://coclaude.example.com`. Used to build OAuth + connector URLs. |
| `PORT` | Port the server listens on (default `8788`). |
| `DB_PATH` | SQLite file path (default `data/coclaude.db`). Holds the Google refresh token, ACLs, and hashed tokens — back this up. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | From the OAuth web client above. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `MAIL_FROM` | For emailing invite codes (STARTTLS). |
| `ADMIN_SETUP_KEY` | A long random secret that guards the one-time Google-consent bootstrap route. |
| `OWNER_EMAIL` / `OWNER_NAME` / `OWNER_INITIALS` | Seeds the owner account on first boot; an owner invite code is printed to the logs. |

## Run

**Local (dev):**

```sh
uv sync
cp .env.example .env    # then edit
uv run coclaude         # serves http://localhost:8788/mcp (+ /healthz, /login, /)
uv run pytest
```

**Docker (deploy):**

```sh
docker compose up -d --build
docker logs coclaude --tail 50     # first boot prints the owner invite code
```

## Reverse proxy

Put an HTTPS reverse proxy (nginx, Caddy, NPM, …) in front of the server and
route your domain to `http://HOST:8788`. Requirements:

- Terminate TLS and enable WebSockets.
- Disable response buffering and use long read/send timeouts (MCP is streamable
  HTTP): e.g. nginx `proxy_buffering off; proxy_read_timeout 3600s;
  proxy_send_timeout 3600s;`.
- If you sit behind Cloudflare's proxy (orange-cloud), it cuts idle streams
  ~100 s — set the record to **DNS-only** (grey) to avoid connector flakiness.
  Plain MCP POSTs are unaffected either way.

## First-run bootstrap

1. **Connect the owner's Google account (once):** visit
   `https://YOUR-DOMAIN/oauth/google/start?key=<ADMIN_SETUP_KEY>`, consent as the
   owner, and the refresh token is stored. Re-run if the token is ever revoked.
2. **Get the owner invite code:** printed to the logs on first boot
   (`docker logs coclaude | grep -i "invite code"`). Use it to connect your own
   Claude just like any collaborator.

## Onboarding a collaborator

1. **Owner (in their Claude):** `add_collaborator(email, display_name, initials,
   projects)` — creates the account, a one-time invite code, and emails it with
   setup instructions.
2. **Collaborator:** Claude → Settings → Connectors → Add custom connector →
   `https://YOUR-DOMAIN/mcp` → sign-in page → enter invite code → choose a
   password.
3. Then just say **"catch me up on \<project\>"** to start.

## Tools

**Everyone (ACL-scoped):** `list_projects`, `read_project` ("catch me up"),
`read_doc`, `log_entry` ("log it"), `edit_text`, `strike`, `promote`.

**Owner only:** `create_project`, `create_doc` (scaffolds + shares a Google Doc),
`add_collaborator`, `remove_collaborator`, `set_project_instructions`,
`list_collaborators`.

CoClaude is deliberately mode-agnostic: a project can be an open-ended,
long-running exploration or a focused push to a decision — often both at once,
since collaborators work independently. The server's instructions tell each
Claude to read which mode its user is in and match it, rather than defaulting to
"let's make a plan."

## Repo shape

- `src/coclaude/auth/` — OAuth 2.1 AS (FastMCP `OAuthProvider` + SQLite): DCR,
  PKCE, `/login` invite-code page, token rotation.
- `src/coclaude/google/` — owner-credential Google client; Docs JSON → sections/
  markdown; section-anchored `batchUpdate` writes (UTF-16 aware, descending
  order, per-doc locks).
- `src/coclaude/tools/` — collaborator and owner-admin MCP tools.
- `src/coclaude/template.py` — doc scaffold (Overview & Instructions / Live /
  Deciding / Decided).

See `private/documentation/prd.md` for the full PRD and `deploy/crosscut.md` for
a worked example deployment (the author's homelab).
