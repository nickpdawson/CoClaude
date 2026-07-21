"""The /login page: where a Claude connector's OAuth flow meets a human.

Reached via redirect from /authorize with ?txn=<id>. Accepts either a one-time
invite code (first connection; sets a password for reconnects) or an existing
email + password.
"""

from html import escape
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from .. import db
from . import invites
from .provider import create_auth_code_for_txn

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CoClaude — Sign in</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #f4f2ee; color: #1a1a18;
         display: flex; justify-content: center; padding: 3rem 1rem; }}
  .card {{ background: #fff; border-radius: 12px; padding: 2rem; max-width: 26rem; width: 100%;
           box-shadow: 0 2px 12px rgba(0,0,0,.08); }}
  h1 {{ font-size: 1.3rem; margin: 0 0 .25rem; }}
  p.sub {{ color: #666; margin: 0 0 1.5rem; font-size: .9rem; }}
  fieldset {{ border: 1px solid #ddd; border-radius: 8px; margin: 0 0 1rem; padding: 1rem; }}
  legend {{ font-weight: 600; font-size: .95rem; padding: 0 .4rem; }}
  label {{ display: block; font-size: .8rem; color: #555; margin: .6rem 0 .2rem; }}
  input {{ width: 100%; box-sizing: border-box; padding: .5rem .6rem; border: 1px solid #ccc;
           border-radius: 6px; font-size: 1rem; }}
  button {{ width: 100%; padding: .65rem; border: 0; border-radius: 8px; background: #bd5b3b;
            color: #fff; font-size: 1rem; font-weight: 600; cursor: pointer; margin-top: .5rem; }}
  .err {{ background: #fdecec; color: #a33; border-radius: 8px; padding: .6rem .8rem;
          margin-bottom: 1rem; font-size: .9rem; }}
  .hint {{ font-size: .75rem; color: #888; margin-top: .3rem; }}
</style></head><body><div class="card">
<h1>CoClaude</h1>
<p class="sub">Sign in to connect your Claude to the shared project docs.</p>
{error}
<form method="post" action="/login">
  <input type="hidden" name="txn" value="{txn}">
  <fieldset>
    <legend>First time — invite code</legend>
    <label>Invite code (from your email)</label>
    <input name="invite_code" autocomplete="one-time-code" placeholder="e.g. K7MPQ2XV">
    <label>Choose a password</label>
    <input name="new_password" type="password" autocomplete="new-password">
    <div class="hint">You'll use your email + this password if you ever reconnect.</div>
  </fieldset>
  <fieldset>
    <legend>Returning — sign in</legend>
    <label>Email</label>
    <input name="email" type="email" autocomplete="email">
    <label>Password</label>
    <input name="password" type="password" autocomplete="current-password">
  </fieldset>
  <button type="submit">Connect</button>
</form>
</div></body></html>"""


def _page(txn: str, error: str = "") -> HTMLResponse:
    err_html = f'<div class="err">{escape(error)}</div>' if error else ""
    return HTMLResponse(_PAGE.format(txn=escape(txn), error=err_html))


async def login_get(request: Request) -> HTMLResponse:
    txn = request.query_params.get("txn", "")
    if not txn:
        return HTMLResponse("<p>Missing login session. Start again from Claude.</p>", status_code=400)
    return _page(txn)


async def login_post(request: Request):
    form = await request.form()
    txn = str(form.get("txn", ""))
    invite_code = str(form.get("invite_code", "")).strip()
    new_password = str(form.get("new_password", ""))
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    if not txn:
        return HTMLResponse("<p>Missing login session. Start again from Claude.</p>", status_code=400)

    collaborator_id = None
    if invite_code:
        if len(new_password) < 8:
            return _page(txn, "Choose a password of at least 8 characters along with your invite code.")
        with db.tx() as conn:
            row = invites.redeem_invite(conn, invite_code)
            if row is None:
                return _page(txn, "That invite code is invalid, expired, or already used.")
            invites.set_password(conn, row["id"], new_password)
            collaborator_id = row["id"]
    elif email and password:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM collaborators WHERE email = ? AND status = 'active'", (email,)
            ).fetchone()
        if row is None or not invites.check_password(row, password):
            return _page(txn, "Email or password not recognized.")
        collaborator_id = row["id"]
    else:
        return _page(txn, "Enter an invite code (plus a new password), or your email and password.")

    try:
        code, redirect_uri, state = create_auth_code_for_txn(txn, collaborator_id)
    except ValueError as exc:
        return _page(txn, str(exc))

    sep = "&" if "?" in redirect_uri else "?"
    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)
