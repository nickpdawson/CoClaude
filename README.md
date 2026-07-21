# CoClaude

A self-hosted MCP server that lets multiple people — each using **their own Claude
account** — jointly read *and write* shared Google Docs. Projects scope docs to
collaborators; the owner's single Google credential (scope `drive.file`) does all
Docs I/O; collaborators authenticate to this server only (OAuth 2.1 + invite codes).

See `private/documentation/prd.md` for the full PRD and `deploy/crosscut.md` for
the live deployment runbook.

## Quick start (dev)

```sh
uv sync
cp .env.example .env   # fill in
uv run coclaude        # serves http://localhost:8788/mcp (+ /healthz, /login)
uv run pytest
```

## Shape

- `src/coclaude/auth/` — OAuth 2.1 AS (FastMCP OAuthProvider + SQLite): DCR,
  PKCE, /login invite-code page, token rotation.
- `src/coclaude/google/` — owner-credential Google client; Docs JSON → sections/
  markdown; section-anchored batchUpdate writes (UTF-16 aware, descending order,
  per-doc locks).
- `src/coclaude/tools/` — collaborator tools (`read_project`, `log_entry`,
  `strike`, `promote`, …) and owner admin tools (`create_project`, `create_doc`,
  `add_collaborator` → emailed invite, …).
- `src/coclaude/template.py` — doc scaffold: Overview & Instructions / Live /
  Deciding / Decided ("librarian, not just reader" ritual).

## Collaborator onboarding

1. Owner: `add_collaborator(email, name, initials, projects)` — invite emailed.
2. Collaborator: Claude → Settings → Connectors → Add custom connector →
   `https://coclaude.dzsec.net/mcp` → sign-in page → invite code + choose password.
3. Say "catch me up on <project>".
