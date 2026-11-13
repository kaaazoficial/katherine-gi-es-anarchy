"""Safe fuzzy matching for command-name suggestions (never auto-executes)."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable

try:
    from rapidfuzz import fuzz, process
except ImportError:  # Keep local test environments usable without production extras.
    fuzz = None
    process = None


def normalize_command_name(value: str) -> str:
    """Normalize one command token without changing its arguments."""
    token = unicodedata.normalize("NFKD", str(value or "").casefold().lstrip("!"))
    token = "".join(character for character in token if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9_]", "", token)


def suggest_command(
    value: str,
    allowed_commands: Iterable[str],
    *,
    score_cutoff: int = 86,
) -> tuple[str, int] | None:
    """Return a high-confidence suggestion; callers must ask before executing."""
    query = normalize_command_name(value)
    if len(query) < 4:
        return None

    choices = sorted({
        normalize_command_name(command)
        for command in allowed_commands
        if normalize_command_name(command)
    })
    if not choices:
        return None

    cutoff = max(80, min(100, int(score_cutoff)))
    if process is not None and fuzz is not None:
        match = process.extractOne(query, choices, scorer=fuzz.ratio, score_cutoff=cutoff)
        if not match:
            return None
        candidate, score, _ = match
        return candidate, int(round(score))

    best = max(
        choices,
        key=lambda candidate: SequenceMatcher(None, query, candidate).ratio(),
    )
    score = int(round(SequenceMatcher(None, query, best).ratio() * 100))
    return (best, score) if score >= cutoff else None
