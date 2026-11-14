"""Recognize deliberate calls to Katherine without catching casual mentions."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz.fuzz import ratio


NAME_ALIASES = frozenset({
    "katherine",
    "katherin",
    "katherina",
    "kathrine",
    "katerine",
    "katheryne",
    "kat",
    "kath",
    "kathy",
    "kathi",
    "katy",
    "catherine",
})

_GREETING_RE = re.compile(
    r"^[¡!¿?]*\s*(?:oye|ey|hey|hola|buenas(?:\s+(?:tardes|noches|dias))?|"
    r"disculpa|perdona|escucha)\s*[,!:;]?\s+",
    flags=re.IGNORECASE,
)
_NAME_RE = re.compile(
    r"^@?([^\W\d_][\w'-]{1,23})(?=$|[\s,.:;!?¡¿—-])",
    flags=re.UNICODE,
)
_LEADING_SEPARATORS = " \t,.:;!?—-"


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _is_katherine_name(value: str) -> bool:
    candidate = _normalize_name(value).strip()
    if candidate in NAME_ALIASES:
        return True
    if not 7 <= len(candidate) <= 10:
        return False
    return ratio(candidate, "katherine") >= 82


def extract_katherine_call(message: str) -> str | None:
    """Return the body of a leading vocative, or None for ordinary mentions.

    Only a name at the start of the message (optionally after a short greeting)
    can activate the bot. Misspellings are accepted only when they are close
    to Katherine, and short nicknames must match the explicit alias list.
    """
    text = str(message or "").strip()
    if not text:
        return None

    greeting = _GREETING_RE.match(text)
    if greeting:
        text = text[greeting.end():].lstrip()

    match = _NAME_RE.match(text)
    if not match or not _is_katherine_name(match.group(1)):
        return None

    return text[match.end():].lstrip(_LEADING_SEPARATORS).strip()
