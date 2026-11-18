"""Deterministic, privacy-minimizing moderation policy primitives.

This module contains pure policy decisions only. Callers own persistence and
platform actions; this module never stores message bodies, user profiles or
conversation history.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_SPACE_RE = re.compile(r"\s+")
_LINK_RE = re.compile(r"(?:https?://|www\.)\S+", flags=re.IGNORECASE)
_BOT_TRUE_VALUES = frozenset({"true", "1", "yes"})


@dataclass(frozen=True, slots=True)
class AntiSpamSettings:
    """Validated anti-spam limits for one community room."""

    enabled: bool = True
    max_messages: int = 7
    window_seconds: int = 8
    repeat_limit: int = 3
    max_links: int = 4
    max_mentions: int = 8

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AntiSpamSettings":
        """Build settings from string-like room preferences with hard bounds."""
        def bounded(key: str, default: int, minimum: int, maximum: int) -> int:
            try:
                raw = int(values.get(key, default))
            except (TypeError, ValueError, OverflowError):
                return default
            return max(minimum, min(maximum, raw))

        enabled_value = str(values.get("antispam_enabled", "1")).strip().casefold()
        return cls(
            enabled=enabled_value in {"1", "true", "yes", "on"},
            max_messages=bounded("antispam_max_messages", 7, 3, 20),
            window_seconds=bounded("antispam_window_seconds", 8, 3, 60),
            repeat_limit=bounded("antispam_repeat_limit", 3, 2, 10),
            max_links=bounded("antispam_max_links", 4, 2, 12),
            max_mentions=bounded("antispam_max_mentions", 8, 3, 20),
        )


@dataclass(frozen=True, slots=True)
class MessageSignals:
    """Ephemeral signals derived from one incoming message."""

    normalized_content: str
    content_digest: str
    link_count: int
    mention_count: int


@dataclass(frozen=True, slots=True)
class AntiSpamDecision:
    """A moderation verdict without side effects or personal data storage."""

    blocked: bool
    reason: str = ""
    repeated_count: int = 0
    link_count: int = 0
    mention_count: int = 0


def normalize_message_content(content: Any, *, max_chars: int = 20_000) -> str:
    """Normalize whitespace and bound the transient input before analysis."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    return _SPACE_RE.sub(" ", str(content or "")[:max_chars]).strip().casefold()


def is_platform_bot_author(author: Mapping[str, Any] | None) -> bool:
    """Recognize bot flags without trusting truthy strings such as 'false'."""
    if not isinstance(author, Mapping):
        return False
    return any(
        str(author.get(field, "")).strip().casefold() in _BOT_TRUE_VALUES
        for field in ("bot", "isBot", "is_bot")
    )


def derive_message_signals(
    content: Any,
    mentions: Any = None,
    *,
    max_chars: int = 20_000,
    max_mentions: int = 100,
) -> MessageSignals:
    """Derive short-lived digest and counts; never return or retain raw content."""
    normalized = normalize_message_content(content, max_chars=max_chars)
    digest = hashlib.sha256(
        normalized.encode("utf-8", errors="ignore")
    ).hexdigest() if normalized else ""
    if isinstance(mentions, (list, tuple, set, frozenset)):
        mention_count = min(len(mentions), max_mentions)
    else:
        mention_count = 0
    return MessageSignals(
        normalized_content=normalized,
        content_digest=digest,
        link_count=len(_LINK_RE.findall(normalized)),
        mention_count=mention_count,
    )


def evaluate_antispam(
    *,
    settings: AntiSpamSettings,
    message_count: int,
    repeated_count: int,
    link_count: int,
    mention_count: int,
) -> AntiSpamDecision:
    """Evaluate deterministic limits in priority order."""
    if not settings.enabled:
        return AntiSpamDecision(False)
    reason = ""
    if link_count > settings.max_links:
        reason = f"Exceso de enlaces en un mensaje ({link_count})."
    elif mention_count > settings.max_mentions:
        reason = f"Menciones masivas en un mensaje ({mention_count})."
    elif message_count >= settings.max_messages or repeated_count >= settings.repeat_limit:
        reason = "Antispam automático: demasiados mensajes o contenido repetido."
    return AntiSpamDecision(
        blocked=bool(reason),
        reason=reason,
        repeated_count=max(0, int(repeated_count)),
        link_count=max(0, int(link_count)),
        mention_count=max(0, int(mention_count)),
    )


def prune_activity_history(
    history: Any,
    *,
    cutoff: float,
) -> int:
    """Remove timestamped digest tuples older than cutoff from a deque-like object."""
    removed = 0
    while history and float(history[0][0]) < cutoff:
        history.popleft()
        removed += 1
    return removed


def bounded_oldest_keys(mapping: Mapping[Any, Any], limit: int) -> tuple[Any, ...]:
    """Return the oldest keys from timestamped histories without mutating mapping."""
    if limit <= 0:
        return ()
    def timestamp(item: tuple[Any, Any]) -> float:
        value = item[1]
        try:
            return float(value[-1][0]) if value else 0.0
        except (TypeError, ValueError, IndexError, KeyError):
            return 0.0
    return tuple(key for key, _ in sorted(mapping.items(), key=timestamp)[:limit])


def bounded_expiry_keys(mapping: Mapping[Any, Any], limit: int) -> tuple[Any, ...]:
    """Return earliest-expiring keys from a cooldown mapping."""
    if limit <= 0:
        return ()
    def expiry(item: tuple[Any, Any]) -> float:
        try:
            return float(item[1])
        except (TypeError, ValueError, OverflowError):
            return 0.0
    return tuple(key for key, _ in sorted(mapping.items(), key=expiry)[:limit])
