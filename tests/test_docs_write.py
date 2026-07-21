from conftest import make_doc

from coclaude.google.docs_read import build_sections, find_section, u16len
from coclaude.google.docs_write import _append_requests
from coclaude.template import build_scaffold_requests


def test_append_to_populated_section():
    doc = make_doc(
        [
            ("Live", "HEADING_1", False),
            ("first entry", "NORMAL_TEXT", False),
            ("Deciding", "HEADING_1", False),
        ]
    )
    sec = find_section(build_sections(doc), "Live")
    anchor, reqs = _append_requests(sec, "second entry")
    last = sec.paras[-1]
    assert anchor == last.end - 1  # before the trailing newline
    assert reqs[0]["insertText"] == {"location": {"index": anchor}, "text": "\nsecond entry"}
    style_range = reqs[1]["updateParagraphStyle"]["range"]
    assert style_range["startIndex"] == anchor + 1
    assert style_range["endIndex"] == anchor + u16len("\nsecond entry")


def test_append_to_empty_section_normalizes_heading_style():
    doc = make_doc(
        [
            ("Live", "HEADING_1", False),
            ("Deciding", "HEADING_1", False),
        ]
    )
    sec = find_section(build_sections(doc), "Live")
    anchor, reqs = _append_requests(sec, "entry")
    # empty section: anchor inside the heading paragraph, then restyled to NORMAL_TEXT
    assert anchor == sec.heading_end - 1
    assert reqs[1]["updateParagraphStyle"]["paragraphStyle"] == {"namedStyleType": "NORMAL_TEXT"}
    assert reqs[2]["deleteParagraphBullets"]["range"]["startIndex"] == anchor + 1


def _simulate(requests):
    """Apply insertText/updateParagraphStyle requests to a virtual doc; return
    (text, heading_ranges). Indexes are UTF-16 code units starting at 1, like
    the real API, so the simulation operates on utf-16-le bytes."""
    buf = bytearray()
    for req in requests:
        if "insertText" in req:
            at = (req["insertText"]["location"]["index"] - 1) * 2
            ins = req["insertText"]["text"].encode("utf-16-le")
            buf[at:at] = ins
    headings = []
    for req in requests:
        if "updateParagraphStyle" in req:
            r = req["updateParagraphStyle"]["range"]
            chunk = bytes(buf[(r["startIndex"] - 1) * 2 : (r["endIndex"] - 1) * 2])
            headings.append(chunk.decode("utf-16-le"))
    return buf.decode("utf-16-le"), headings


def test_scaffold_heading_ranges_align():
    reqs = build_scaffold_requests("Trip", "A trip doc.", "Always tag airline ideas [maybe].")
    text, headings = _simulate(reqs)
    # every heading range must cover exactly one section title + its newline
    assert sorted(h.strip() for h in headings) == sorted(
        ["Overview & Instructions", "Live", "Deciding", "Decided"]
    )
    for h in headings:
        assert h.endswith("\n")
    assert "Always tag airline ideas [maybe]." in text
    assert "librarian" in text


def test_scaffold_custom_sections():
    reqs = build_scaffold_requests("X", sections=["Overview", "Ideas 💡", "Locked"])
    _, headings = _simulate(reqs)
    # style requests are emitted in descending index order
    assert [h.strip() for h in headings] == ["Locked", "Ideas 💡", "Overview"]
