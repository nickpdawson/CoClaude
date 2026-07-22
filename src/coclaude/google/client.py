"""Google API services built from the single owner refresh token.

Scope is `documents` + `drive` (see SCOPES below) so CoClaude can work with
pre-existing docs the owner points it at, not only ones it created itself. A
self-hoster who only ever wants CoClaude to create and manage its own docs can
narrow SCOPES to `drive.file` (and skip the sensitive/restricted-scope consent
verification friction) — the create_doc/read/write paths all work under it.
"""

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .. import db
from ..config import settings

# documents (sensitive) = read/write content of ANY Doc the owner can access, incl.
# pre-existing/externally-created docs. drive (restricted) = metadata for change-
# tracking + sharing those docs to collaborators. Both are configured on the consent
# screen and grantable by the owner behind the "unverified app" warning. Broader than
# drive.file by deliberate owner choice so CoClaude can manage docs it did not create.
SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GoogleNotConnected(Exception):
    pass


def _credentials() -> Credentials:
    with db.tx() as conn:
        row = conn.execute("SELECT refresh_token FROM google_credentials WHERE id = 1").fetchone()
    if row is None:
        raise GoogleNotConnected(
            "Google is not connected yet — the owner must complete /oauth/google/start."
        )
    s = settings()
    return Credentials(
        token=None,
        refresh_token=row["refresh_token"],
        token_uri=TOKEN_URI,
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        scopes=SCOPES,
    )


def docs_service():
    return build("docs", "v1", credentials=_credentials(), cache_discovery=False)


def drive_service():
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def store_refresh_token(refresh_token: str) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO google_credentials (id, refresh_token, obtained_at) VALUES (1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET refresh_token = excluded.refresh_token, obtained_at = excluded.obtained_at",
            (refresh_token, db.now()),
        )


def is_connected() -> bool:
    with db.tx() as conn:
        return conn.execute("SELECT 1 FROM google_credentials WHERE id = 1").fetchone() is not None
