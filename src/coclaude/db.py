"""SQLite storage: schema, connections, housekeeping."""

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .config import settings

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS collaborators (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    initials TEXT NOT NULL,
    password_hash TEXT,
    is_owner INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS invites (
    id TEXT PRIMARY KEY,
    code_hash TEXT UNIQUE NOT NULL,
    collaborator_id TEXT NOT NULL REFERENCES collaborators(id),
    expires_at INTEGER NOT NULL,
    redeemed_at INTEGER
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS docs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    google_doc_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS grants (
    collaborator_id TEXT NOT NULL REFERENCES collaborators(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    role TEXT NOT NULL DEFAULT 'writer',
    PRIMARY KEY (collaborator_id, project_id)
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    metadata TEXT NOT NULL,           -- full OAuthClientInformationFull JSON
    created_at INTEGER NOT NULL,
    last_used_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_txns (
    txn_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    state TEXT,
    code_challenge TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL,
    resource TEXT,
    scope TEXT,
    collaborator_id TEXT,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    collaborator_id TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL,
    resource TEXT,
    scope TEXT,
    expires_at INTEGER NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tokens (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('access','refresh')),
    token_hash TEXT UNIQUE NOT NULL,
    collaborator_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    scope TEXT,
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    rotated_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_collab ON tokens(collaborator_id);
CREATE TABLE IF NOT EXISTS last_reads (
    collaborator_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    last_read_at INTEGER NOT NULL,
    last_modified_time TEXT,
    PRIMARY KEY (collaborator_id, doc_id)
);
CREATE TABLE IF NOT EXISTS google_credentials (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    refresh_token TEXT NOT NULL,
    obtained_at INTEGER NOT NULL
);
"""


def now() -> int:
    return int(time.time())


def new_id() -> str:
    return uuid.uuid4().hex


def connect() -> sqlite3.Connection:
    path = settings().db_path
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with tx() as conn:
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def purge_expired() -> None:
    """Drop expired txns/codes/tokens/invites and DCR clients unused for 90 days."""
    t = now()
    with tx() as conn:
        conn.execute("DELETE FROM auth_txns WHERE expires_at < ?", (t,))
        conn.execute("DELETE FROM auth_codes WHERE expires_at < ?", (t,))
        conn.execute("DELETE FROM tokens WHERE expires_at < ? OR (revoked = 1 AND rotated_at < ?)", (t, t - 86400))
        conn.execute("DELETE FROM invites WHERE expires_at < ? AND redeemed_at IS NULL", (t,))
        conn.execute(
            "DELETE FROM oauth_clients WHERE last_used_at < ? AND client_id NOT IN (SELECT DISTINCT client_id FROM tokens WHERE revoked = 0)",
            (t - 90 * 86400,),
        )
