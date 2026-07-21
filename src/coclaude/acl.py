"""Caller identity + authorization checks for MCP tools."""

import sqlite3

from fastmcp.server.dependencies import get_access_token

from . import db


class NotAuthorized(Exception):
    pass


def current_collaborator(conn: sqlite3.Connection) -> sqlite3.Row:
    token = get_access_token()
    subject = getattr(token, "subject", None) if token else None
    if not subject:
        raise NotAuthorized("No authenticated collaborator on this request.")
    row = conn.execute(
        "SELECT * FROM collaborators WHERE id = ? AND status = 'active'", (subject,)
    ).fetchone()
    if row is None:
        raise NotAuthorized("Your account is disabled or gone. Contact the owner.")
    return row


def require_owner(conn: sqlite3.Connection) -> sqlite3.Row:
    me = current_collaborator(conn)
    if not me["is_owner"]:
        raise NotAuthorized("This tool is owner-only.")
    return me


def require_grant(conn: sqlite3.Connection, me: sqlite3.Row, project_id: str) -> None:
    if me["is_owner"]:
        return
    row = conn.execute(
        "SELECT 1 FROM grants WHERE collaborator_id = ? AND project_id = ?",
        (me["id"], project_id),
    ).fetchone()
    if row is None:
        raise NotAuthorized("You don't have access to that project.")


def granted_projects(conn: sqlite3.Connection, me: sqlite3.Row) -> list[sqlite3.Row]:
    if me["is_owner"]:
        return conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
    return conn.execute(
        """SELECT p.* FROM projects p
           JOIN grants g ON g.project_id = p.id
           WHERE g.collaborator_id = ? ORDER BY p.created_at""",
        (me["id"],),
    ).fetchall()


def doc_in_granted_project(conn: sqlite3.Connection, me: sqlite3.Row, doc_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM docs WHERE id = ? OR google_doc_id = ?", (doc_id, doc_id)).fetchone()
    if row is None:
        raise NotAuthorized("Unknown doc id.")
    require_grant(conn, me, row["project_id"])
    return row
