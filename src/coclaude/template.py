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

This document is shared context for people co-working through their own AI \
assistants (via the CoClaude connector). If you are an AI reading this, you are \
a librarian for this doc, not just a reader.

People use this doc in two ways, sometimes both at once: to open things up \
(explore, gather ideas, think out loud) and to settle things (weigh options, \
make a call). Different collaborators may be in different modes at the same \
time. Read which mode the person you're helping is in and match it — help \
exploration widen without forcing it toward a decision, and help decisions \
converge only when someone is actually ready to make one. When in doubt, ask \
rather than pushing to closure.

The sections are places to put things, not a pipeline every idea must pass \
through:

Live is the open layer — brainstorms, notes, questions, half-formed ideas. \
Append new material here, stamped [<initials> <date>] and tagged [idea], \
[maybe], [?], or [wild]. Plenty of good thinking lives here indefinitely; it \
doesn't have to go anywhere. Never prune it.

Deciding holds things someone wants to resolve that need a human call. Move an \
idea here only when there's real intent to settle it — not just because it has \
been sitting in Live.

Decided is the settled layer: calls that have been made, each with a one-line \
rationale. Only promote something once the humans have clearly decided.

When a user says "log it", file their new material into the right section. \
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
