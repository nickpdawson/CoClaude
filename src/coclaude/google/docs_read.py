"""Parse Google Docs API document JSON into sections, rendered markdown, and
text-offset maps.

All indexes are UTF-16 code units (the Docs API's unit), so offset math goes
through u16len() rather than len().
"""

import re
from dataclasses import dataclass, field


def u16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


_HEADING_LEVELS = {f"HEADING_{i}": i for i in range(1, 7)}


@dataclass
class Run:
    start: int
    content: str
    strikethrough: bool


@dataclass
class Para:
    start: int
    end: int
    text: str            # includes trailing newline
    heading_level: int   # 0 = normal text
    bulleted: bool
    runs: list[Run] = field(default_factory=list)


@dataclass
class Section:
    name: str
    heading_start: int
    heading_end: int
    content_start: int
    content_end: int
    paras: list[Para] = field(default_factory=list)


def parse_paragraphs(doc: dict) -> list[Para]:
    paras: list[Para] = []
    for el in doc.get("body", {}).get("content", []):
        p = el.get("paragraph")
        if not p:
            continue
        runs, text = [], ""
        for pe in p.get("elements", []):
            tr = pe.get("textRun")
            if not tr:
                continue
            content = tr.get("content", "")
            runs.append(
                Run(
                    start=pe["startIndex"],
                    content=content,
                    strikethrough=bool(tr.get("textStyle", {}).get("strikethrough")),
                )
            )
            text += content
        style = p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        paras.append(
            Para(
                start=el["startIndex"],
                end=el["endIndex"],
                text=text,
                heading_level=_HEADING_LEVELS.get(style, 0),
                bulleted="bullet" in p,
            )
        )
        paras[-1].runs = runs
    return paras


def doc_end_index(doc: dict) -> int:
    content = doc.get("body", {}).get("content", [])
    return content[-1]["endIndex"] if content else 1


def normalize_heading(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def build_sections(doc: dict) -> list[Section]:
    paras = parse_paragraphs(doc)
    end = doc_end_index(doc)
    headings = [(i, p) for i, p in enumerate(paras) if p.heading_level > 0]
    sections: list[Section] = []
    for n, (i, hp) in enumerate(headings):
        content_end = headings[n + 1][1].start if n + 1 < len(headings) else end
        sec = Section(
            name=hp.text.strip(),
            heading_start=hp.start,
            heading_end=hp.end,
            content_start=hp.end,
            content_end=content_end,
        )
        sec.paras = [p for p in paras if p.start >= sec.content_start and p.end <= content_end]
        sections.append(sec)
    return sections


class SectionNotFound(Exception):
    pass


def find_section(sections: list[Section], name: str) -> Section:
    want = normalize_heading(name)
    for sec in sections:
        if normalize_heading(sec.name) == want:
            return sec
    for sec in sections:  # fuzzy: containment either way
        norm = normalize_heading(sec.name)
        if want and (want in norm or norm in want):
            return sec
    available = ", ".join(repr(s.name) for s in sections) or "none"
    raise SectionNotFound(f"No section matching {name!r}. Sections in this doc: {available}.")


class TextNotFound(Exception):
    pass


def find_text_range(
    doc: dict, needle: str, occurrence: int = 1, within: Section | None = None
) -> tuple[int, int]:
    """Locate the Nth occurrence of needle; returns (start, end) in doc indexes."""
    paras = within.paras if within else parse_paragraphs(doc)
    chars: list[tuple[str, int]] = []  # (python char, utf-16 index)
    for p in paras:
        for run in p.runs:
            pos = run.start
            for ch in run.content:
                chars.append((ch, pos))
                pos += u16len(ch)
    text = "".join(c for c, _ in chars)
    idx, found = -1, 0
    while found < occurrence:
        idx = text.find(needle, idx + 1)
        if idx < 0:
            where = f" in section {within.name!r}" if within else ""
            raise TextNotFound(
                f"Text {needle!r} (occurrence {occurrence}) not found{where}. "
                "Read the doc and pass the exact text as it appears."
            )
        found += 1
    start = chars[idx][1]
    last_ch, last_pos = chars[idx + len(needle) - 1]
    return start, last_pos + u16len(last_ch)


def render_markdown(doc: dict) -> str:
    lines: list[str] = []
    for p in parse_paragraphs(doc):
        body = ""
        for run in p.runs:
            chunk = run.content.rstrip("\n")
            if run.strikethrough and chunk.strip():
                chunk = f"~~{chunk}~~"
            body += chunk
        if p.heading_level:
            lines.append(f"{'#' * p.heading_level} {body.strip()}")
        elif p.bulleted:
            lines.append(f"- {body}")
        else:
            lines.append(body)
    # collapse runs of blank lines
    out, prev_blank = [], False
    for ln in lines:
        blank = not ln.strip()
        if not (blank and prev_blank):
            out.append(ln)
        prev_blank = blank
    return "\n".join(out).strip()
