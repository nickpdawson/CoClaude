import pytest
from conftest import make_doc

from coclaude.google.docs_read import (
    SectionNotFound,
    build_sections,
    find_section,
    find_text_range,
    render_markdown,
    u16len,
)


@pytest.fixture
def doc():
    return make_doc(
        [
            ("Overview & Instructions", "HEADING_1", False),
            ("This doc is shared context.", "NORMAL_TEXT", False),
            ("Live", "HEADING_1", False),
            ("[ND 2026-07-20] ski Zermatt [idea]", "NORMAL_TEXT", False),
            ("[AB 2026-07-20] 🎿 heli day [wild]", "NORMAL_TEXT", False),
            ("Deciding", "HEADING_1", False),
            ("Decided", "HEADING_1", False),
        ]
    )


def test_sections(doc):
    secs = build_sections(doc)
    assert [s.name for s in secs] == ["Overview & Instructions", "Live", "Deciding", "Decided"]
    live = find_section(secs, "live")
    assert len(live.paras) == 2
    deciding = find_section(secs, "Deciding")
    assert deciding.paras == []


def test_fuzzy_section_match(doc):
    secs = build_sections(doc)
    assert find_section(secs, "🔥 Live").name == "Live"
    assert find_section(secs, "overview").name == "Overview & Instructions"
    with pytest.raises(SectionNotFound) as e:
        find_section(secs, "Nonexistent")
    assert "Live" in str(e.value)


def test_find_text_range_utf16(doc):
    # '🎿' is a surrogate pair: ranges after it must account for 2 units per emoji
    start, end = find_text_range(doc, "heli day")
    text_before = "[AB 2026-07-20] 🎿 "
    live_second_para = next(
        el for el in doc["body"]["content"] if "heli day" in el["paragraph"]["elements"][0]["textRun"]["content"]
    )
    expected_start = live_second_para["startIndex"] + u16len(text_before)
    assert start == expected_start
    assert end == expected_start + u16len("heli day")


def test_find_text_within_section(doc):
    secs = build_sections(doc)
    live = find_section(secs, "Live")
    start, end = find_text_range(doc, "ski Zermatt", within=live)
    assert end - start == u16len("ski Zermatt")


def test_render_markdown(doc):
    md = render_markdown(doc)
    assert "# Live" in md
    assert "[ND 2026-07-20] ski Zermatt [idea]" in md


def test_render_strikethrough():
    doc = make_doc(
        [
            ("Deciding", "HEADING_1", False),
            ("old plan", "NORMAL_TEXT", True),
        ]
    )
    assert "~~old plan~~" in render_markdown(doc)
