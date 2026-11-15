"""Conservative Spanish spelling feedback for public chat messages.

This module keeps no message history. The spell checker and a bounded allowlist
are loaded in process memory; callers decide when a gentle correction is sent.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

from rapidfuzz.fuzz import ratio


_TOKEN_RE = re.compile(r"(?<![@\w])([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{4,})(?![\w])")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_MENTION_RE = re.compile(r"@[\w.-]+")
_CODE_RE = re.compile(r"`[^`]*`|\x60\x60\x60[\s\S]*?\x60\x60\x60")

# High-confidence chat typos that a dictionary can miss or treat as another
# valid word. Ambiguous pairs such as "esta/está" and "mas/más" are excluded.
_COMMON_FIXES = {
    "grasias": "gracias",
    "grasia": "gracia",
    "estoi": "estoy",
    "kiero": "quiero",
    "qiero": "quiero",
    "nesesito": "necesito",
    "nesesitas": "necesitas",
    "exelente": "excelente",
    "eselente": "excelente",
    "aora": "ahora",
    "haci": "así",
    "conoser": "conocer",
}

_SPANISH_MARKERS = frozenset({
    "a", "al", "algo", "con", "como", "cuando", "de", "del", "donde",
    "el", "ella", "ellas", "ellos", "en", "eres", "es", "esta", "está",
    "estoy", "fue", "gracias", "hay", "hola", "la", "las", "le", "les",
    "lo", "los", "me", "mi", "mucho", "muy", "necesito", "no", "nos",
    "para", "pero", "por", "porque", "que", "qué", "se", "si", "sí",
    "soy", "te", "tengo", "todo", "tu", "tú", "un", "una", "uno", "y",
    "ya", "yo", "por", "favor", "puedes", "quiero", "ayuda", "ayudar",
    "necesitas", "pregunta", "preguntas", "hablar", "contigo", "estas",
    "buenos", "dias", "días", "tardes", "noches", "problema", "electricidad",
    "escribir", "perdon", "perdón",
})
_CHAT_SLANG = frozenset({
    "bro", "crack", "jaja", "jajaja", "jeje", "jejeje", "xd", "uwu",
    "lol", "lmao", "xD", "k", "ke", "q", "xq", "pq", "pa", "na",
    "tmb", "tb", "ntp", "xfa", "pls", "okey",
})
_AMBIGUOUS_ACCENT_WORDS = frozenset({
    "aun", "como", "cuando", "cuanto", "cual", "cuales", "de", "el",
    "esta", "este", "mas", "mi", "que", "quien", "solo", "si", "se",
    "tu", "donde",
})

_DOMAIN_WORDS = frozenset({
    "genshin", "teyvat", "hoyoverse", "mihoyo", "anarchy", "katherine",
    "katherin", "katherina", "kathrine", "katheryne", "cyno", "nahida",
    "baizhu", "furina", "neuvillette", "arlecchino", "clorinde", "navia",
    "alhaitham", "kaveh", "tighnari", "collei", "dehya", "nilou", "candace",
    "layla", "faruzan", "wanderer", "scaramouche", "zhongli", "raiden",
    "ei", "venti", "xiao", "ganyu", "shenhe", "yelan", "hu", "tao",
    "kazuha", "ayaka", "ayato", "yoimiya", "itto", "gorou", "kokomi",
    "kirara", "lynette", "lyney", "freminet", "sumeru", "fontaine",
    "natlan", "mondstadt", "liyue", "inazuma", "snezhnaya", "primogemas",
    "abyss", "abyssal", "build", "builds", "main", "subdps", "dps",
    "arle", "fischl", "xiangling", "xingqiu", "bennett", "chevreuse",
    "kaeya", "albedo", "noelle", "tartaglia", "xianyun", "chiori",
    "sigewinne", "wriothesley", "dainsleif", "enkanomiya", "focalors",
    "openai", "openrouter", "python", "javascript", "typescript", "html",
    "css", "sql", "api", "github", "render", "livekit", "edge", "tts",
    "discord", "bandit", "ruff", "pyspellchecker", "anarchyapps",
})

_spellchecker: Any = None
_spellchecker_attempted = False
_spellchecker_lock = threading.Lock()
_spell_lock = threading.Lock()


@dataclass(frozen=True)
class SpellingCorrection:
    corrected_text: str
    corrections: tuple[tuple[str, str], ...]


def _preserve_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _ignored_ranges(text: str) -> list[tuple[int, int]]:
    return [
        (match.start(), match.end())
        for pattern in (_URL_RE, _MENTION_RE, _CODE_RE)
        for match in pattern.finditer(text)
    ]


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _get_spellchecker():
    global _spellchecker, _spellchecker_attempted
    with _spellchecker_lock:
        if _spellchecker is not None:
            return _spellchecker
        if _spellchecker_attempted:
            return None
        _spellchecker_attempted = True
        try:
            from spellchecker import SpellChecker

            checker = SpellChecker(language="es", distance=1)
            checker.word_frequency.load_words(_DOMAIN_WORDS | _CHAT_SLANG)
            _spellchecker = checker
        except Exception:
            # A missing dictionary must never break chat delivery.
            _spellchecker = None
        return _spellchecker


def detect_spanish_spelling_issues(
    text: str,
    spell_checker: Any = None,
) -> SpellingCorrection | None:
    """Return a conservative correction only for clear Spanish typos.

    Proper names, Genshin terms, short chat slang, mentions, URLs, code and
    ambiguous accent pairs are deliberately left untouched.
    """
    original = str(text or "").strip()
    if not original or len(original) > 700 or original.startswith(("!", "-", "/")):
        return None

    ranges = _ignored_ranges(original)
    visible = original
    for start, end in sorted(ranges, reverse=True):
        visible = visible[:start] + (" " * (end - start)) + visible[end:]

    tokens = list(_TOKEN_RE.finditer(visible))
    if not tokens or len(tokens) > 40:
        return None

    normalized_tokens = [match.group(1).casefold() for match in tokens]
    greeting_match = re.match(
        r"^([¡!¿?]*\\s*)(ola)\\b",
        visible,
        flags=re.IGNORECASE,
    )
    explicit_greeting = bool(
        greeting_match
        and not _overlaps(*greeting_match.span(2), ranges)
    )
    explicit_present = (
        any(word in _COMMON_FIXES for word in normalized_tokens)
        or explicit_greeting
    )
    if not explicit_present and not any(
        word in _SPANISH_MARKERS for word in normalized_tokens
    ):
        return None

    checker = spell_checker or _get_spellchecker()
    replacements: list[tuple[int, int, str, str]] = []
    if explicit_greeting and greeting_match:
        start, end = greeting_match.span(2)
        original_word = greeting_match.group(2)
        replacements.append((
            start, end, _preserve_case(original_word, "hola"), original_word
        ))

    # Context resolves a few common accent pairs without guessing from a word
    # in isolation (e.g. "como estas?" -> "cómo estás?").
    contextual_patterns = [
        (r"\bcomo\s+estas\b", "cómo estás", True),
        (r"\bcomo\s+te\s+llamas\b", "cómo te llamas", True),
        (r"\bque\s+haces\b", "qué haces", True),
        (r"\bque\s+tal\b", "qué tal", True),
        (r"\bque\s+opinas\b", "qué opinas", True),
        (r"\bque\s+quieres\b", "qué quieres", True),
        (r"\bque\s+necesitas\b", "qué necesitas", True),
        (r"\bdonde\s+estas\b", "dónde estás", True),
        (r"\bdonde\s+vives\b", "dónde vives", True),
        (r"\bquien\s+eres\b", "quién eres", True),
        (r"\bquien\s+es\b", "quién es", True),
        (r"\bcuando\s+vienes\b", "cuándo vienes", True),
        (r"\besta\s+(?:muy\s+|tan\s+)?(?:bien|mal|genial|excelente|lindo|linda)\b",
         None, False),
    ]
    question_context = "?" in visible or "¿" in visible
    for pattern, phrase_replacement, needs_question in contextual_patterns:
        if needs_question and not question_context:
            continue
        for match in re.finditer(pattern, visible, flags=re.IGNORECASE):
            start, end = match.span()
            if _overlaps(start, end, ranges):
                continue
            if phrase_replacement is None:
                original_phrase = match.group(0)
                phrase_replacement = re.sub(
                    r"^esta\b", "está", original_phrase, flags=re.IGNORECASE
                )
            corrected_phrase = _preserve_case(match.group(0), phrase_replacement)
            replacements.append((
                start, end, corrected_phrase, match.group(0)
            ))
            if len(replacements) >= 2:
                break
        if len(replacements) >= 2:
            break

    for match in tokens:
        start, end = match.span(1)
        if _overlaps(start, end, ranges):
            continue
        word = match.group(1)
        normalized = word.casefold()
        explicit = _COMMON_FIXES.get(normalized)
        if explicit:
            replacements.append((
                start, end, _preserve_case(word, explicit), word
            ))
        if len(replacements) >= 2:
            break

    if checker is not None and len(replacements) < 2:
        with _spell_lock:
            for match in tokens:
                start, end = match.span(1)
                if _overlaps(start, end, ranges):
                    continue
                word = match.group(1)
                normalized = word.casefold()
                if (
                    len(normalized) < 5
                    or normalized in _DOMAIN_WORDS
                    or normalized in _CHAT_SLANG
                    or normalized in _COMMON_FIXES
                    or normalized in _AMBIGUOUS_ACCENT_WORDS

                    or any(old == word for _a, _b, _c, old in replacements)
                    or (word.isupper() and len(word) <= 8)
                ):
                    continue
                if not checker.unknown([normalized]):
                    continue
                candidate = checker.correction(normalized)
                if not candidate:
                    continue
                candidate = str(candidate).casefold()
                if (
                    candidate == normalized
                    or not checker.known([candidate])
                    or ratio(normalized, candidate) < 72
                ):
                    continue
                # Avoid rewriting capitalized names in the middle of a sentence.
                if word[:1].isupper() and start > 0:
                    continue
                replacements.append((
                    start, end, _preserve_case(word, candidate), word
                ))
                if len(replacements) >= 2:
                    break

    if not replacements:
        return None

    # Several unknown words usually indicate a name, another language, or
    # stylized slang. Do not pretend to know how to rewrite that message.
    if not replacements or len(replacements) > 2:
        return None

    corrected = original
    for start, end, replacement, _old in sorted(replacements, reverse=True):
        corrected = corrected[:start] + replacement + corrected[end:]

    corrections = tuple((old, replacement) for _start, _end, replacement, old in replacements)
    if corrected == original:
        return None
    return SpellingCorrection(corrected_text=corrected, corrections=corrections)
