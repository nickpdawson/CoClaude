"""Collaborator-facing MCP tools (project- and ACL-scoped)."""

import time

from fastmcp import FastMCP

from .. import acl, db
from ..google import docs_read, docs_write


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _stamp(me, text: str, tags: list[str] | None = None) -> str:
    tag_str = " " + " ".join(f"[{t.strip('[]')}]" for t in tags) if tags else ""
    return f"[{me['initials']} {_today()}] {text.strip()}{tag_str}"


def _doc_payload(conn, me, doc_row) -> dict:
    doc = docs_write.get_doc(doc_row["google_doc_id"])
    modified = docs_write.doc_modified_time(doc_row["google_doc_id"])
    prev = conn.execute(
        "SELECT * FROM last_reads WHERE collaborator_id = ? AND doc_id = ?",
        (me["id"], doc_row["id"]),
    ).fetchone()
    changed = prev is None or (modified and prev["last_modified_time"] != modified)
    conn.execute(
        "INSERT INTO last_reads (collaborator_id, doc_id, last_read_at, last_modified_time) VALUES (?,?,?,?) "
        "ON CONFLICT(collaborator_id, doc_id) DO UPDATE SET last_read_at = excluded.last_read_at, "
        "last_modified_time = excluded.last_modified_time",
        (me["id"], doc_row["id"], db.now(), modified),
    )
    return {
        "doc_id": doc_row["id"],
        "title": doc_row["title"],
        "url": docs_write.doc_url(doc_row["google_doc_id"]),
        "modified_time": modified,
        "changed_since_your_last_read": changed,
        "sections": [s.name for s in docs_read.build_sections(doc)],
        "content_markdown": docs_read.render_markdown(doc),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(output_schema=None)
    def list_projects() -> list[dict]:
        """List the projects you have access to, with their docs."""
        with db.tx() as conn:
            me = acl.current_collaborator(conn)
            out = []
            for p in acl.granted_projects(conn, me):
                docs = conn.execute(
                    "SELECT * FROM docs WHERE project_id = ? ORDER BY created_at", (p["id"],)
                ).fetchall()
                out.append(
                    {
                        "project_id": p["id"],
                        "name": p["name"],
                        "description": p["description"],
                        "docs": [
                            {
                                "doc_id": d["id"],
                                "title": d["title"],
                                "url": docs_write.doc_url(d["google_doc_id"]),
                            }
                            for d in docs
                        ],
                    }
                )
            return out

    @mcp.tool(output_schema=None)
    def read_project(project: str) -> dict:
        """Catch up on a project: its working conventions, every doc's current
        content, and whether each doc changed since you last read it.
        `project` is a project id or name. Call this at the start of a session
        ("catch me up")."""
        with db.tx() as conn:
            me = acl.current_collaborator(conn)
            p = conn.execute(
                "SELECT * FROM projects WHERE id = ? OR name = ?", (project, project)
            ).fetchone()
            if p is None:
                raise ValueError(f"No project {project!r}. Use list_projects.")
            acl.require_grant(conn, me, p["id"])
            docs = conn.execute(
                "SELECT * FROM docs WHERE project_id = ? ORDER BY created_at", (p["id"],)
            ).fetchall()
            return {
                "project_id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "instructions": p["instructions"],
                "your_identity": {"name": me["display_name"], "initials": me["initials"]},
                "docs": [_doc_payload(conn, me, d) for d in docs],
            }

    @mcp.tool(output_schema=None)
    def read_doc(doc_id: str) -> dict:
        """Read one doc's full current content (rendered as markdown, with
        section names and change-since-last-read info)."""
        with db.tx() as conn:
            me = acl.current_collaborator(conn)
            d = acl.doc_in_granted_project(conn, me, doc_id)
            return _doc_payload(conn, me, d)

    @mcp.tool(output_schema=None)
    def log_entry(doc_id: str, text: str, section: str = "Live", tags: list[str] | None = None) -> str:
        """Append an entry to a doc section (default: Live, the brainstorm layer).
        The entry is automatically stamped with the caller's initials and date.
        Tags like idea/maybe/?/wild are appended as [tag] markers."""
        with db.tx() as conn:
            me = acl.current_collaborator(conn)
            d = acl.doc_in_granted_project(conn, me, doc_id)
        entry = _stamp(me, text, tags)
        docs_write.append_to_section(d["google_doc_id"], section, entry)
        return f"Logged to {section!r} in {d['title']!r}: {entry}"

    @mcp.tool(output_schema=None)
    def edit_text(doc_id: str, find: str, replace: str, occurrence: int = 1) -> str:
        """Replace exact text in a doc. Prefer strike() + log_entry() to preserve
        history; use this only for typos or updating your own recent entries."""
        with db.tx() as conn:
            me = acl.current_collaborator(conn)
            d = acl.doc_in_granted_project(conn, me, doc_id)
        docs_write.replace_text(d["google_doc_id"], find, replace, occurrence)
        return f"Replaced occurrence {occurrence} of {find!r} in {d['title']!r}."

    @mcp.tool(output_schema=None)
    def strike(doc_id: str, text: str, occurrence: int = 1) -> str:
        """Strike through exact text in a doc (retire it without deleting —
        history stays visible)."""
        with db.tx() as conn:
            me = acl.current_collaborator(conn)
            d = acl.doc_in_granted_project(conn, me, doc_id)
        docs_write.strike_text(d["google_doc_id"], text, occurrence)
        return f"Struck through {text!r} in {d['title']!r}."

    @mcp.tool(output_schema=None)
    def promote(
        doc_id: str,
        text: str,
        rationale: str,
        from_section: str = "Deciding",
        to_section: str = "Decided",
    ) -> str:
        """Promote a settled item: strikes `text` in from_section and appends a
        decision line (with rationale, initials, date) to to_section."""
        with db.tx() as conn:
            me = acl.current_collaborator(conn)
            d = acl.doc_in_granted_project(conn, me, doc_id)
        entry = f"✅ {_stamp(me, text.strip())} — {rationale.strip()}"
        docs_write.promote(d["google_doc_id"], text, entry, from_section, to_section)
        return f"Promoted to {to_section!r} in {d['title']!r}: {entry}"
