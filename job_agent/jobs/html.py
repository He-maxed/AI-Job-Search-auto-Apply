from __future__ import annotations

import html as html_lib
import re

_BLOCK_RE = re.compile(r"</(?:p|div|li|ul|ol|h[1-6]|tr|section)>|<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw: str | None) -> str:
    """Very small HTML->text converter for job description `content`."""
    if not raw:
        return ""
    text = _BLOCK_RE.sub("\n", raw)
    text = _TAG_RE.sub("", text)
    text = html_lib.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)