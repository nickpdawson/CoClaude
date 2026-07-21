"""Owner-only admin MCP tools."""

from fastmcp import FastMCP

from .. import acl, db, mailer, template
from ..auth import invites
from ..google import docs_write


def _project(conn, ref: str):
    p = conn.execute("SELECT * FROM projects WHERE id = ? OR name = ?", (ref, ref)).fetchone()
    if p is None:
        raise ValueError(f"No project {ref!r}.")
    return p


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def create_project(name: str, description: str = "", instructions: str = "") -> dict:
        """(Owner only) Create a project — a named scope of docs + collaborators.
        `instructions` are per-project working conventions surfaced to every
        collaborator's Claude via read_project."""
        with db.tx() as conn:
            acl.require_owner(conn)
            pid = db.new_id()
            conn.execute(
                "INSERT INTO projects (id, name, description, instructions, created_at) VALUES (?,?,?,?,?)",
                (pid, name, description, instructions, db.now()),
            )
        return {"project_id": pid, "name": name}

    @mcp.tool
    def create_doc(
        project: str,
        title: str,
        share_with_emails: list[str] | None = None,
        description: str = "",
        custom_sections: list[str] | None = None,
    ) -> dict:
        """(Owner only) Create a scaffolded Google Doc in a project (sections:
        Overview & Instructions / Live / Deciding / Decided unless
        custom_sections is given) and share it with the listed human emails."""
        with db.tx() as conn:
            acl.require_owner(conn)
            p = _project(conn, project)
        reqs = template.build_scaffold_requests(
            p["name"], description, p["instructions"], custom_sections
        )
        google_doc_id = docs_write.create_scaffold_doc(title, reqs, share_with_emails or [])
        with db.tx() as conn:
            did = db.new_id()
            conn.execute(
                "INSERT INTO docs (id, project_id, google_doc_id, title, created_at) VALUES (?,?,?,?,?)",
                (did, p["id"], google_doc_id, title, db.now()),
            )
        return {"doc_id": did, "title": title, "url": docs_write.doc_url(google_doc_id)}

    @mcp.tool
    def add_collaborator(
        email: str,
        display_name: str,
        initials: str,
        projects: list[str],
        send_email: bool = True,
    ) -> dict:
        """(Owner only) Invite a collaborator to one or more projects (ids or
        names). Creates their account + a one-time invite code and emails it to
        them with connector setup instructions. The code is also returned here
        in case you want to pass it along yourself."""
        with db.tx() as conn:
            acl.require_owner(conn)
            project_rows = [_project(conn, ref) for ref in projects]
            existing = conn.execute(
                "SELECT * FROM collaborators WHERE email = ?", (email.lower(),)
            ).fetchone()
            if existing:
                cid = existing["id"]
                conn.execute("UPDATE collaborators SET status = 'active' WHERE id = ?", (cid,))
            else:
                cid = db.new_id()
                conn.execute(
                    "INSERT INTO collaborators (id, email, display_name, initials, created_at) VALUES (?,?,?,?,?)",
                    (cid, email.lower(), display_name, initials.upper(), db.now()),
                )
            for p in project_rows:
                conn.execute(
                    "INSERT OR IGNORE INTO grants (collaborator_id, project_id) VALUES (?,?)",
                    (cid, p["id"]),
                )
            code = invites.create_invite(conn, cid)
        emailed = False
        email_error = None
        if send_email:
            try:
                mailer.send_invite(email, display_name, code, [p["name"] for p in project_rows])
                emailed = True
            except Exception as exc:  # surface, don't fail the invite
                email_error = str(exc)
        return {
            "collaborator_email": email.lower(),
            "invite_code": code,
            "expires_in_days": 7,
            "granted_projects": [p["name"] for p in project_rows],
            "email_sent": emailed,
            **({"email_error": email_error} if email_error else {}),
        }

    @mcp.tool
    def remove_collaborator(email: str) -> str:
        """(Owner only) Disable a collaborator: revokes all their tokens and
        project grants immediately. (Their Google account access to already-
        shared docs must be removed in Drive separately if desired.)"""
        with db.tx() as conn:
            acl.require_owner(conn)
            row = conn.execute(
                "SELECT * FROM collaborators WHERE email = ?", (email.lower(),)
            ).fetchone()
            if row is None:
                raise ValueError(f"No collaborator {email!r}.")
            if row["is_owner"]:
                raise ValueError("Refusing to remove the owner.")
            conn.execute("UPDATE collaborators SET status = 'disabled' WHERE id = ?", (row["id"],))
            conn.execute("UPDATE tokens SET revoked = 1 WHERE collaborator_id = ?", (row["id"],))
            conn.execute("DELETE FROM grants WHERE collaborator_id = ?", (row["id"],))
            conn.execute(
                "DELETE FROM invites WHERE collaborator_id = ? AND redeemed_at IS NULL", (row["id"],)
            )
        return f"Disabled {email} and revoked their access."

    @mcp.tool
    def set_project_instructions(project: str, instructions: str) -> str:
        """(Owner only) Set/replace a project's working conventions (shown to
        every collaborator's Claude in read_project, and baked into the Overview
        section of future docs)."""
        with db.tx() as conn:
            acl.require_owner(conn)
            p = _project(conn, project)
            conn.execute(
                "UPDATE projects SET instructions = ? WHERE id = ?", (instructions, p["id"])
            )
        return f"Instructions updated for {p['name']!r}."

    @mcp.tool
    def list_collaborators(project: str | None = None) -> list[dict]:
        """(Owner only) List collaborators (optionally filtered to one project)
        with their grants and status."""
        with db.tx() as conn:
            acl.require_owner(conn)
            rows = conn.execute("SELECT * FROM collaborators ORDER BY created_at").fetchall()
            out = []
            for c in rows:
                grants = conn.execute(
                    """SELECT p.name FROM grants g JOIN projects p ON p.id = g.project_id
                       WHERE g.collaborator_id = ?""",
                    (c["id"],),
                ).fetchall()
                names = [g["name"] for g in grants]
                if project and project not in names:
                    continue
                pending = conn.execute(
                    "SELECT 1 FROM invites WHERE collaborator_id = ? AND redeemed_at IS NULL AND expires_at >= ?",
                    (c["id"], db.now()),
                ).fetchone()
                out.append(
                    {
                        "email": c["email"],
                        "name": c["display_name"],
                        "initials": c["initials"],
                        "status": c["status"],
                        "is_owner": bool(c["is_owner"]),
                        "projects": names,
                        "has_password": bool(c["password_hash"]),
                        "invite_pending": bool(pending),
                    }
                )
            return out
