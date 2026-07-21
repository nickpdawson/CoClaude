"""One-time owner consent flow to mint the Google refresh token.

GET /oauth/google/start?key=<ADMIN_SETUP_KEY>  -> redirect to Google consent
GET /oauth/google/callback                     -> exchange code, store refresh token
"""

import secrets
from urllib.parse import urlencode

import requests as _requests  # vendored transitively via google-auth
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from ..config import settings
from . import client as gclient

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"

# In-memory CSRF state for the single-admin bootstrap flow.
_pending_states: set[str] = set()


def _redirect_uri() -> str:
    return f"{settings().public_url.rstrip('/')}/oauth/google/callback"


async def google_start(request: Request):
    s = settings()
    if not s.admin_setup_key or request.query_params.get("key") != s.admin_setup_key:
        return HTMLResponse("<p>Forbidden.</p>", status_code=403)
    state = secrets.token_urlsafe(16)
    _pending_states.add(state)
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(gclient.SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return RedirectResponse(f"{AUTH_URI}?{urlencode(params)}", status_code=302)


async def google_callback(request: Request):
    s = settings()
    state = request.query_params.get("state", "")
    code = request.query_params.get("code")
    if state not in _pending_states:
        return HTMLResponse("<p>Invalid state — restart from /oauth/google/start.</p>", status_code=400)
    _pending_states.discard(state)
    if not code:
        return HTMLResponse(
            f"<p>Google returned no code: {request.query_params.get('error', 'unknown error')}</p>",
            status_code=400,
        )
    resp = _requests.post(
        gclient.TOKEN_URI,
        data={
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret,
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    payload = resp.json()
    if resp.status_code != 200 or "refresh_token" not in payload:
        return HTMLResponse(
            "<p>Token exchange failed (no refresh_token — if you consented before, revoke "
            "CoClaude at myaccount.google.com/permissions and retry): "
            f"<pre>{resp.status_code} {payload}</pre></p>",
            status_code=502,
        )
    gclient.store_refresh_token(payload["refresh_token"])
    return HTMLResponse("<p>✅ Google connected. CoClaude can now create and edit docs.</p>")
