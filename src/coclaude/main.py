"""CoClaude server assembly."""

import logging
import threading

from fastmcp import FastMCP

from . import db
from .auth import invites
from .auth.provider import CoClaudeOAuthProvider
from .auth.routes import login_get, login_post
from .config import settings
from .google.bootstrap import google_callback, google_start
from .tools import admin, collaborator

log = logging.getLogger("coclaude")

INSTRUCTIONS = """\
CoClaude connects this chat to shared Google Docs co-worked by several people,
each through their own Claude. Start sessions with read_project ("catch me up").
File new material with log_entry ("log it"): brainstorm -> Live, needs-a-human-
call -> Deciding. promote() moves settled items to Decided. Retire text with
strike(), never deletion. Follow each project's `instructions` from read_project.
"""


def seed_owner() -> None:
    s = settings()
    if not s.owner_email:
        return
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM collaborators WHERE email = ?", (s.owner_email.lower(),)
        ).fetchone()
        if row is None:
            cid = db.new_id()
            conn.execute(
                "INSERT INTO collaborators (id, email, display_name, initials, is_owner, created_at) VALUES (?,?,?,?,1,?)",
                (cid, s.owner_email.lower(), s.owner_name, s.owner_initials.upper(), db.now()),
            )
            row = conn.execute("SELECT * FROM collaborators WHERE id = ?", (cid,)).fetchone()
        pending = conn.execute(
            "SELECT 1 FROM invites WHERE collaborator_id = ? AND redeemed_at IS NULL AND expires_at >= ?",
            (row["id"], db.now()),
        ).fetchone()
        if not row["password_hash"] and not pending:
            code = invites.create_invite(conn, row["id"])
            log.warning("Owner invite code (connect your own Claude with this): %s", code)


def housekeeping() -> None:
    db.purge_expired()
    t = threading.Timer(86400, housekeeping)
    t.daemon = True
    t.start()


def build_app() -> FastMCP:
    s = settings()
    db.init_db()
    seed_owner()
    housekeeping()

    provider = CoClaudeOAuthProvider(s.public_url)
    mcp = FastMCP(name="CoClaude", instructions=INSTRUCTIONS, auth=provider)

    collaborator.register(mcp)
    admin.register(mcp)

    mcp.custom_route("/login", methods=["GET"])(login_get)
    mcp.custom_route("/login", methods=["POST"])(login_post)
    mcp.custom_route("/oauth/google/start", methods=["GET"])(google_start)
    mcp.custom_route("/oauth/google/callback", methods=["GET"])(google_callback)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True})

    @mcp.custom_route("/", methods=["GET"])
    async def root(request):
        from starlette.responses import HTMLResponse

        return HTMLResponse(
            "<!doctype html><meta charset=utf-8>"
            "<title>CoClaude</title>"
            "<div style='font-family:-apple-system,system-ui,sans-serif;max-width:32rem;"
            "margin:4rem auto;padding:0 1rem;color:#1a1a18'>"
            "<h1>CoClaude</h1>"
            "<p>This is an MCP server, not a website. Connect it from your AI assistant "
            "as a custom connector using the URL "
            "<code>" + s.public_url.rstrip("/") + "/mcp</code>, then sign in with the "
            "invite code you were emailed.</p>"
            "<p style='color:#888;font-size:.85rem'>Have an invite but no connector yet? "
            "In Claude: Settings &rarr; Connectors &rarr; Add custom connector.</p></div>"
        )

    return mcp


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = settings()
    app = build_app()
    app.run(transport="http", host=s.host, port=s.port)


if __name__ == "__main__":
    run()
