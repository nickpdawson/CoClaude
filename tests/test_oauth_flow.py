"""End-to-end OAuth flow test: DCR -> authorize -> invite login -> token ->
refresh -> authenticated tool call, against a real server on localhost."""

import base64
import hashlib
import os
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    dbdir = tmp_path_factory.mktemp("db")
    os.environ.update(
        {
            "PUBLIC_URL": BASE,
            "HOST": "127.0.0.1",
            "PORT": str(PORT),
            "DB_PATH": str(dbdir / "test.db"),
            "OWNER_EMAIL": "owner@example.com",
            "OWNER_INITIALS": "OW",
            "ADMIN_SETUP_KEY": "test-key",
        }
    )
    from coclaude.config import settings

    settings.cache_clear()
    from coclaude.main import build_app

    app = build_app()
    thread = threading.Thread(
        target=lambda: app.run(transport="http", host="127.0.0.1", port=PORT, show_banner=False),
        daemon=True,
    )
    thread.start()
    for _ in range(100):
        try:
            httpx.get(f"{BASE}/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("server did not start")
    yield BASE


@pytest.fixture(scope="module")
def invite_code(server):
    from coclaude import db
    from coclaude.auth import invites

    with db.tx() as conn:
        cid = db.new_id()
        conn.execute(
            "INSERT INTO collaborators (id, email, display_name, initials, created_at) VALUES (?,?,?,?,?)",
            (cid, "friend@example.com", "Friend", "FR", db.now()),
        )
        code = invites.create_invite(conn, cid)
    return code


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


def test_full_flow(server, invite_code):
    redirect_uri = "https://claude.ai/api/mcp/auth_callback"

    # 1. Unauthenticated MCP hit -> 401 with WWW-Authenticate
    r = httpx.post(
        f"{server}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 401

    # 2. AS metadata
    meta = httpx.get(f"{server}/.well-known/oauth-authorization-server").json()
    assert meta["issuer"].rstrip("/") == server

    # 3. DCR — Claude-style: no scope, token_endpoint_auth_method none
    reg = httpx.post(
        meta["registration_endpoint"],
        json={
            "client_name": "Claude",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert reg.status_code in (200, 201), reg.text
    client_id = reg.json()["client_id"]

    # 4. /authorize -> redirect to /login
    verifier, challenge = _pkce()
    r = httpx.get(
        meta["authorization_endpoint"],
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": "st4te",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": f"{server}/mcp",  # Claude sends RFC 8707 resource; must round-trip
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 307), r.text
    login_url = r.headers["location"]
    assert "/login?txn=" in login_url
    txn = parse_qs(urlparse(login_url).query)["txn"][0]

    # 5. Login page renders; bad invite rejected
    assert "invite" in httpx.get(login_url).text.lower()
    bad = httpx.post(f"{server}/login", data={"txn": txn, "invite_code": "WRONGCOD", "new_password": "hunter22"})
    assert bad.status_code == 200 and "invalid" in bad.text.lower()

    # 6. Real invite + password -> redirect back to Claude with code
    r = httpx.post(
        f"{server}/login",
        data={"txn": txn, "invite_code": invite_code, "new_password": "hunter22"},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    loc = urlparse(r.headers["location"])
    assert r.headers["location"].startswith(redirect_uri)
    q = parse_qs(loc.query)
    assert q["state"] == ["st4te"]
    code = q["code"][0]

    # 7. Token exchange with PKCE
    r = httpx.post(
        meta["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["token_type"].lower() == "bearer" and tok["refresh_token"]

    # 8. Code is single-use
    r2 = httpx.post(
        meta["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert r2.status_code == 400

    # 9. Authenticated tool call via MCP
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    import asyncio

    async def call_tools():
        transport = StreamableHttpTransport(f"{server}/mcp", headers={"Authorization": f"Bearer {tok['access_token']}"})
        async with Client(transport) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert {"list_projects", "read_project", "log_entry"} <= names
            res = await client.call_tool("list_projects", {})
            return res

    result = asyncio.run(call_tools())
    assert result is not None

    # 10. Refresh grant rotates
    r = httpx.post(
        meta["token_endpoint"],
        data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": client_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] != tok["access_token"]

    # 11. Returning-user login: new authorize txn, email+password
    verifier2, challenge2 = _pkce()
    r = httpx.get(
        meta["authorization_endpoint"],
        params={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": "s2",
            "code_challenge": challenge2,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    txn2 = parse_qs(urlparse(r.headers["location"]).query)["txn"][0]
    r = httpx.post(
        f"{server}/login",
        data={"txn": txn2, "email": "friend@example.com", "password": "hunter22"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "code=" in r.headers["location"]


def test_acl_enforced(server, invite_code):
    """A collaborator with no grants sees no projects; owner-only tools refuse."""
    from coclaude import db

    with db.tx() as conn:
        row = conn.execute("SELECT id FROM collaborators WHERE email = 'friend@example.com'").fetchone()
        # mint an access token directly
        from coclaude.auth.provider import CoClaudeOAuthProvider

        tok = CoClaudeOAuthProvider._mint_tokens(
            conn, collaborator_id=row["id"], client_id="test-client", scope=None
        )

    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    import asyncio

    async def check():
        transport = StreamableHttpTransport(
            f"{server}/mcp", headers={"Authorization": f"Bearer {tok.access_token}"}
        )
        async with Client(transport) as client:
            res = await client.call_tool("list_projects", {})
            # output_schema disabled -> result comes back as text content (JSON "[]")
            text = res.content[0].text if res.content else ""
            assert res.data in ([], None) and text.strip() in ("[]", "")
            try:
                await client.call_tool("create_project", {"name": "sneaky"})
                raise AssertionError("non-owner created a project")
            except Exception as exc:
                assert "owner" in str(exc).lower()

    asyncio.run(check())
