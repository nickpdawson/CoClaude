"""Invite creation/redemption and collaborator password management."""

import sqlite3

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .. import db
from .tokens import INVITE_TTL, hash_token, new_invite_code

_ph = PasswordHasher()


def create_invite(conn: sqlite3.Connection, collaborator_id: str) -> str:
    """Create a fresh invite for a collaborator, invalidating unredeemed older ones."""
    code = new_invite_code()
    conn.execute(
        "DELETE FROM invites WHERE collaborator_id = ? AND redeemed_at IS NULL", (collaborator_id,)
    )
    conn.execute(
        "INSERT INTO invites (id, code_hash, collaborator_id, expires_at) VALUES (?,?,?,?)",
        (db.new_id(), hash_token(code), collaborator_id, db.now() + INVITE_TTL),
    )
    return code


def redeem_invite(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    """Return the collaborator row for a valid, unredeemed invite code; marks it redeemed."""
    row = conn.execute(
        """SELECT i.id AS invite_id, c.* FROM invites i
           JOIN collaborators c ON c.id = i.collaborator_id
           WHERE i.code_hash = ? AND i.redeemed_at IS NULL AND i.expires_at >= ?
             AND c.status = 'active'""",
        (hash_token(code.strip().upper()), db.now()),
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE invites SET redeemed_at = ? WHERE id = ?", (db.now(), row["invite_id"]))
    return row


def set_password(conn: sqlite3.Connection, collaborator_id: str, password: str) -> None:
    conn.execute(
        "UPDATE collaborators SET password_hash = ? WHERE id = ?",
        (_ph.hash(password), collaborator_id),
    )


def check_password(row: sqlite3.Row, password: str) -> bool:
    if not row["password_hash"]:
        return False
    try:
        _ph.verify(row["password_hash"], password)
        return True
    except VerifyMismatchError:
        return False
