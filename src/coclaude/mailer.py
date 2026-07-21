"""Invite email delivery via the owner's mailcow SMTP (STARTTLS)."""

import smtplib
from email.message import EmailMessage

from .config import settings

_INVITE_BODY = """\
Hi {name},

{owner_name} invited you to co-work on "{project_names}" through CoClaude —
a shared Google Doc you and your Claude can both read and write.

Getting connected takes about a minute:

1. In Claude (claude.ai or the app), open Settings → Connectors →
   "Add custom connector".
2. Name it CoClaude and paste this URL:

   {connector_url}

3. Click "Connect" — a CoClaude sign-in page opens. Enter this invite code
   and choose a password:

   Invite code: {code}

   (The code works once and expires in 7 days. Your email + password sign
   you back in if you ever reconnect.)

That's it. In any chat you can now say "catch me up on {first_project}" or
"log this to the doc" and Claude will read/write the shared doc directly.

— CoClaude
"""


def send_invite(
    to_email: str,
    name: str,
    code: str,
    project_names: list[str],
) -> None:
    s = settings()
    msg = EmailMessage()
    msg["Subject"] = f"You're invited to co-work on {', '.join(project_names)} (CoClaude)"
    msg["From"] = s.mail_from
    msg["To"] = to_email
    msg.set_content(
        _INVITE_BODY.format(
            name=name,
            owner_name=s.owner_name,
            project_names=", ".join(project_names),
            first_project=project_names[0] if project_names else "the project",
            connector_url=f"{s.public_url.rstrip('/')}/mcp",
            code=code,
        )
    )
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_pass)
        smtp.send_message(msg)
