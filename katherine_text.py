"""Consistent Anarchy chat text styling for Katherine."""
from __future__ import annotations

import re


_TAG = re.compile(r"^\[(?:CB|C)\]\s*", re.IGNORECASE)
_BULLET = re.compile(r"^(?:[-*•▪◦▸]|\d+[.)])\s+")
_CODE_FENCE = re.compile(r"^\\s*```")
_TITLE_PUNCTUATION = (".", "!", "?", "…")


def _is_heading(line: str, *, first_content_line: bool, line_count: int) -> bool:
    value = line.strip()
    if not value or len(value) > 88 or _BULLET.match(value):
        return False
    if value.startswith(("http://", "https://", "`", "[", "{")):
        return False
    if value.endswith(_TITLE_PUNCTUATION):
        return False
    if value.endswith(":"):
        return True

    letters = [char for char in value if char.isalpha()]
    if not letters:
        return False
    uppercase_ratio = sum(char.isupper() for char in letters) / len(letters)
    if len(letters) >= 4 and uppercase_ratio >= 0.72:
        return True

    return first_content_line and line_count > 1 and len(value) <= 68


def format_katherine_text(content: str) -> str:
    """Center message text and emphasize headings without rewriting its words."""
    text = str(content or "")
    if not text.strip():
        return text

    lines = text.splitlines()
    nonempty = [line.strip() for line in lines if line.strip()]
    if not nonempty or any(_CODE_FENCE.match(line) for line in lines):
        return text

    tagged = sum(1 for line in nonempty if _TAG.match(line))
    if tagged == len(nonempty):
        return text

    first_line_index = next(
        (index for index, line in enumerate(lines) if line.strip()), -1
    )
    output: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            output.append("")
            continue
        if _TAG.match(stripped):
            output.append(stripped)
            continue

        heading = _is_heading(
            stripped,
            first_content_line=index == first_line_index,
            line_count=len(nonempty),
        )
        if heading and index == first_line_index:
            output.append(f"[CB] ✦ {stripped} ✦")
        elif heading:
            output.append(f"[CB] ◈ {stripped}")
        else:
            output.append(f"[C] {stripped}")

    return "\n".join(output)
