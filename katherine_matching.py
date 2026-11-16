"""Safe fuzzy matching for command-name suggestions (never auto-executes)."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable

_FUZZ = None
_PROCESS = None
_RAPIDFUZZ_ATTEMPTED = False


def _load_rapidfuzz():
    """Load the optional accelerator only when a typo suggestion is requested."""
    global _FUZZ, _PROCESS, _RAPIDFUZZ_ATTEMPTED
    if not _RAPIDFUZZ_ATTEMPTED:
        _RAPIDFUZZ_ATTEMPTED = True
        try:
            from rapidfuzz import fuzz, process
        except ImportError:  # Keep local test environments usable without production extras.
            _FUZZ = None
            _PROCESS = None
        else:
            _FUZZ = fuzz
            _PROCESS = process
    return _FUZZ, _PROCESS


def normalize_command_name(value: str) -> str:
    """Normalize one command token without changing its arguments."""
    token = unicodedata.normalize("NFKD", str(value or "").casefold().lstrip("!"))
    token = "".join(character for character in token if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9_]", "", token)


@lru_cache(maxsize=32)
def _normalized_command_choices(commands: frozenset[str]) -> tuple[str, ...]:
    """Normalize immutable command catalogs once for typo suggestions."""
    return tuple(sorted({
        normalized
        for command in commands
        if (normalized := normalize_command_name(command))
    }))


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

    commands = frozenset(str(command) for command in allowed_commands)
    choices = _normalized_command_choices(commands)
    if not choices:
        return None

    cutoff = max(80, min(100, int(score_cutoff)))
    fuzz, process = _load_rapidfuzz()
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
