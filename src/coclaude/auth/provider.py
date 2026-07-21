"""OAuth 2.1 authorization server backed by SQLite.

FastMCP's OAuthProvider mounts the full AS surface (/.well-known/*, /register,
/authorize, /token, /revoke, PKCE). We supply storage and the login redirect:
/authorize parks the request in auth_txns and sends the human to /login, which
binds a collaborator and mints the authorization code (see routes.py).
"""

import json

from fastmcp.server.auth import OAuthProvider
from fastmcp.server.auth.auth import AccessToken
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from .. import db
from .tokens import (
    ACCESS_TTL,
    CODE_TTL,
    REFRESH_TTL,
    ROTATE_GRACE,
    TXN_TTL,
    hash_token,
    new_token,
)


class CoClaudeOAuthProvider(OAuthProvider):
    def __init__(self, public_url: str):
        super().__init__(
            base_url=public_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.public_url = public_url.rstrip("/")

    # ---- client registry (DCR) ----

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT metadata FROM oauth_clients WHERE client_id = ?", (client_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE oauth_clients SET last_used_at = ? WHERE client_id = ?",
                (db.now(), client_id),
            )
        return OAuthClientInformationFull.model_validate_json(row["metadata"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with db.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO oauth_clients (client_id, metadata, created_at, last_used_at) VALUES (?,?,?,?)",
                (client_info.client_id, client_info.model_dump_json(), db.now(), db.now()),
            )

    # ---- authorization ----

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        txn_id = db.new_id()
        with db.tx() as conn:
            conn.execute(
                """INSERT INTO auth_txns
                   (txn_id, client_id, state, code_challenge, redirect_uri,
                    redirect_uri_explicit, resource, scope, expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    txn_id,
                    client.client_id,
                    params.state,
                    params.code_challenge,
                    str(params.redirect_uri),
                    1 if params.redirect_uri_provided_explicitly else 0,
                    str(params.resource) if params.resource else None,
                    " ".join(params.scopes) if params.scopes else None,
                    db.now() + TXN_TTL,
                ),
            )
        return f"{self.public_url}/login?txn={txn_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM auth_codes WHERE code_hash = ? AND used = 0 AND expires_at >= ?",
                (hash_token(authorization_code), db.now()),
            ).fetchone()
        if row is None or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=row["scope"].split() if row["scope"] else [],
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=bool(row["redirect_uri_explicit"]),
            resource=AnyUrl(row["resource"]) if row["resource"] else None,
            subject=row["collaborator_id"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        code_hash = hash_token(authorization_code.code)
        with db.tx() as conn:
            row = conn.execute(
                "SELECT collaborator_id FROM auth_codes WHERE code_hash = ? AND used = 0",
                (code_hash,),
            ).fetchone()
            if row is None:
                raise ValueError("invalid authorization code")
            conn.execute("UPDATE auth_codes SET used = 1 WHERE code_hash = ?", (code_hash,))
            return self._mint_tokens(
                conn,
                collaborator_id=row["collaborator_id"],
                client_id=client.client_id,
                scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
            )

    # ---- refresh ----

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND kind = 'refresh' AND expires_at >= ?",
                (hash_token(refresh_token), db.now()),
            ).fetchone()
        if row is None or row["client_id"] != client.client_id:
            return None
        # Rotated tokens stay valid for a short grace window (Claude retry races).
        if row["revoked"] and not (row["rotated_at"] and db.now() - row["rotated_at"] <= ROTATE_GRACE):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scope"].split() if row["scope"] else [],
            expires_at=row["expires_at"],
            subject=row["collaborator_id"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND kind = 'refresh'",
                (hash_token(refresh_token.token),),
            ).fetchone()
            if row is None:
                raise ValueError("invalid refresh token")
            conn.execute(
                "UPDATE tokens SET revoked = 1, rotated_at = COALESCE(rotated_at, ?) WHERE id = ?",
                (db.now(), row["id"]),
            )
            scope = " ".join(scopes) if scopes else row["scope"]
            return self._mint_tokens(
                conn, collaborator_id=row["collaborator_id"], client_id=client.client_id, scope=scope
            )

    # ---- access tokens ----

    async def load_access_token(self, token: str) -> AccessToken | None:
        with db.tx() as conn:
            row = conn.execute(
                """SELECT t.*, c.status FROM tokens t
                   JOIN collaborators c ON c.id = t.collaborator_id
                   WHERE t.token_hash = ? AND t.kind = 'access'
                     AND t.revoked = 0 AND t.expires_at >= ?""",
                (hash_token(token), db.now()),
            ).fetchone()
        if row is None or row["status"] != "active":
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scope"].split() if row["scope"] else [],
            expires_at=row["expires_at"],
            subject=row["collaborator_id"],
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM tokens WHERE token_hash = ?", (hash_token(token.token),)
            ).fetchone()
            if row is None:
                return
            # Revoking a refresh token also kills the collaborator+client's access tokens.
            conn.execute("UPDATE tokens SET revoked = 1 WHERE id = ?", (row["id"],))
            if row["kind"] == "refresh":
                conn.execute(
                    "UPDATE tokens SET revoked = 1 WHERE collaborator_id = ? AND client_id = ? AND kind = 'access'",
                    (row["collaborator_id"], row["client_id"]),
                )

    # ---- helpers ----

    @staticmethod
    def _mint_tokens(conn, *, collaborator_id: str, client_id: str, scope: str | None) -> OAuthToken:
        access, refresh = new_token(), new_token()
        t = db.now()
        conn.executemany(
            "INSERT INTO tokens (id, kind, token_hash, collaborator_id, client_id, scope, expires_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
            [
                (db.new_id(), "access", hash_token(access), collaborator_id, client_id, scope, t + ACCESS_TTL, t),
                (db.new_id(), "refresh", hash_token(refresh), collaborator_id, client_id, scope, t + REFRESH_TTL, t),
            ],
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            scope=scope,
            refresh_token=refresh,
        )


def create_auth_code_for_txn(txn_id: str, collaborator_id: str) -> tuple[str, str, str | None]:
    """Called by /login after the human authenticates.

    Binds the parked /authorize transaction to a collaborator and mints the
    single-use authorization code. Returns (code, redirect_uri, state).
    """
    code = new_token()
    with db.tx() as conn:
        txn = conn.execute(
            "SELECT * FROM auth_txns WHERE txn_id = ? AND expires_at >= ?", (txn_id, db.now())
        ).fetchone()
        if txn is None:
            raise ValueError("login session expired — retry connecting from Claude")
        conn.execute("DELETE FROM auth_txns WHERE txn_id = ?", (txn_id,))
        conn.execute(
            """INSERT INTO auth_codes
               (code_hash, client_id, collaborator_id, code_challenge, redirect_uri,
                redirect_uri_explicit, resource, scope, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                hash_token(code),
                txn["client_id"],
                collaborator_id,
                txn["code_challenge"],
                txn["redirect_uri"],
                txn["redirect_uri_explicit"],
                txn["resource"],
                txn["scope"],
                db.now() + CODE_TTL,
            ),
        )
    return code, txn["redirect_uri"], txn["state"]
