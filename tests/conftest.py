import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def make_doc(paragraphs):
    """Build a minimal Docs API document JSON from (text, style, strike) tuples.

    Each paragraph's text should NOT include the trailing newline; it is added
    here. Indexes are UTF-16 code units starting at 1, like the real API.
    """

    def u16(s):
        return len(s.encode("utf-16-le")) // 2

    content = []
    pos = 1
    for text, style, strike in paragraphs:
        full = text + "\n"
        end = pos + u16(full)
        content.append(
            {
                "startIndex": pos,
                "endIndex": end,
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": pos,
                            "endIndex": end,
                            "textRun": {
                                "content": full,
                                "textStyle": {"strikethrough": True} if strike else {},
                            },
                        }
                    ],
                    "paragraphStyle": {"namedStyleType": style},
                },
            }
        )
        pos = end
    return {"body": {"content": content}}
