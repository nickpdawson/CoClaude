"""Default doc scaffold: Overview & Instructions / Live / Deciding / Decided.

build_scaffold_requests() returns Docs batchUpdate requests that populate a
freshly created (empty) document: one insertText at index 1 with the full text,
then heading styles applied to the section-title paragraphs.
"""

from .google.docs_read import u16len

DEFAULT_SECTIONS = ["Overview & Instructions", "Live", "Deciding", "Decided"]

_OVERVIEW_BODY = """\
{description}

How this doc works — instructions for any AI chat reading it

This document is shared context for humans co-working through their own AI \
assistants (via the CoClaude connector). If you are an AI reading this, you are \
a librarian for this doc, not just a reader. Follow these conventions:

Live is the raw brainstorm layer. Append new ideas there, stamped \
[<initials> <date>] and tagged [idea], [maybe], [?], or [wild]. Never prune it.

Deciding holds items that need a human call before they're settled. Move an idea \
here when it needs resolution.

Decided is the locked layer: settled calls with a one-line rationale. Only \
promote something when the humans have clearly decided.

When a user says "log it", file their new material into the right sections. \
When a user says "catch me up", read the doc and summarize what changed since \
they last looked, section by section.

Use strikethrough (never deletion) to retire text — history stays visible. If \
collaborators disagree, preserve both positions; do not resolve disagreements \
unilaterally.
{instructions}"""


def build_scaffold_requests(
    project_name: str,
    description: str = "",
    instructions: str = "",
    sections: list[str] | None = None,
) -> list[dict]:
    sections = sections or DEFAULT_SECTIONS
    desc = description.strip() or f"Shared working doc for the {project_name!r} project."
    extra = f"\nProject-specific instructions\n\n{instructions.strip()}" if instructions.strip() else ""
    overview = _OVERVIEW_BODY.format(description=desc, instructions=extra)

    blocks: list[tuple[str, bool]] = []  # (paragraph text, is_heading)
    for i, name in enumerate(sections):
        blocks.append((name, True))
        if i == 0:
            for para in overview.split("\n"):
                blocks.append((para, False))
        else:
            blocks.append(("", False))

    full_text = "\n".join(text for text, _ in blocks)
    requests = [{"insertText": {"location": {"index": 1}, "text": full_text}}]

    # Heading ranges, applied in DESCENDING order (style ops don't shift indexes,
    # but keep the discipline uniform).
    pos = 1
    heading_ranges: list[tuple[int, int]] = []
    for text, is_heading in blocks:
        end = pos + u16len(text)
        if is_heading:
            heading_ranges.append((pos, end + 1))  # include the newline -> whole paragraph
        pos = end + 1  # +1 for the joining newline
    for start, end in sorted(heading_ranges, reverse=True):
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "fields": "namedStyleType",
                }
            }
        )
    return requests
