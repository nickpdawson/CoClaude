"""Section-anchored write operations against Google Docs.

Discipline (do not relax):
- Always documents.get() fresh immediately before computing indexes.
- One batchUpdate per logical operation; independent ops sorted by DESCENDING
  anchor index so earlier requests never shift later ones' targets.
- Per-doc lock so concurrent tool calls can't interleave get/update.
"""

import threading
from collections import defaultdict

from . import client as gclient
from .docs_read import (
    Section,
    build_sections,
    find_section,
    find_text_range,
    u16len,
)

_doc_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def doc_url(google_doc_id: str) -> str:
    return f"https://docs.google.com/document/d/{google_doc_id}/edit"


def get_doc(google_doc_id: str) -> dict:
    return gclient.docs_service().documents().get(documentId=google_doc_id).execute()


def _batch(google_doc_id: str, requests: list[dict]) -> None:
    gclient.docs_service().documents().batchUpdate(
        documentId=google_doc_id, body={"requests": requests}
    ).execute()


def _append_requests(sec: Section, entry_text: str) -> tuple[int, list[dict]]:
    """Requests appending entry_text as normal-text paragraph(s) at a section's end.

    Inserts just before the final newline of the section's last paragraph (or the
    heading itself when the section is empty), then normalizes the new paragraphs'
    style — an insert splitting a heading/bulleted paragraph inherits its style.
    """
    last_end = sec.paras[-1].end if sec.paras else sec.heading_end
    at = last_end - 1
    text = "\n" + entry_text.strip("\n")
    new_start, new_end = at + 1, at + u16len(text)
    return at, [
        {"insertText": {"location": {"index": at}, "text": text}},
        {
            "updateParagraphStyle": {
                "range": {"startIndex": new_start, "endIndex": new_end},
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "fields": "namedStyleType",
            }
        },
        {"deleteParagraphBullets": {"range": {"startIndex": new_start, "endIndex": new_end}}},
    ]


def append_to_section(google_doc_id: str, section_name: str, entry_text: str) -> None:
    with _doc_locks[google_doc_id]:
        doc = get_doc(google_doc_id)
        sec = find_section(build_sections(doc), section_name)
        _, reqs = _append_requests(sec, entry_text)
        _batch(google_doc_id, reqs)


def strike_text(google_doc_id: str, needle: str, occurrence: int = 1) -> None:
    with _doc_locks[google_doc_id]:
        doc = get_doc(google_doc_id)
        start, end = find_text_range(doc, needle, occurrence)
        _batch(
            google_doc_id,
            [
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {"strikethrough": True},
                        "fields": "strikethrough",
                    }
                }
            ],
        )


def replace_text(google_doc_id: str, find: str, replace: str, occurrence: int = 1) -> None:
    with _doc_locks[google_doc_id]:
        doc = get_doc(google_doc_id)
        start, end = find_text_range(doc, find, occurrence)
        _batch(
            google_doc_id,
            [
                {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}},
                {"insertText": {"location": {"index": start}, "text": replace}},
            ],
        )


def promote(
    google_doc_id: str,
    needle: str,
    entry_text: str,
    from_section: str,
    to_section: str,
) -> None:
    """Strike needle in from_section and append entry_text to to_section, atomically."""
    with _doc_locks[google_doc_id]:
        doc = get_doc(google_doc_id)
        sections = build_sections(doc)
        src = find_section(sections, from_section)
        dst = find_section(sections, to_section)
        start, end = find_text_range(doc, needle, within=src)
        strike_op = (
            start,
            [
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {"strikethrough": True},
                        "fields": "strikethrough",
                    }
                }
            ],
        )
        append_op = _append_requests(dst, entry_text)
        ops = sorted([strike_op, append_op], key=lambda op: op[0], reverse=True)
        _batch(google_doc_id, [r for _, reqs in ops for r in reqs])


def create_scaffold_doc(title: str, scaffold_requests: list[dict], share_emails: list[str]) -> str:
    """Create a doc owned by the app identity, apply the template, share to humans."""
    docs = gclient.docs_service()
    created = docs.documents().create(body={"title": title}).execute()
    google_doc_id = created["documentId"]
    if scaffold_requests:
        _batch(google_doc_id, scaffold_requests)
    drive = gclient.drive_service()
    for email in share_emails:
        drive.permissions().create(
            fileId=google_doc_id,
            body={"role": "writer", "type": "user", "emailAddress": email},
            sendNotificationEmail=True,
        ).execute()
    return google_doc_id


def doc_modified_time(google_doc_id: str) -> str:
    meta = (
        gclient.drive_service()
        .files()
        .get(fileId=google_doc_id, fields="modifiedTime")
        .execute()
    )
    return meta.get("modifiedTime", "")
