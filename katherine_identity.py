"""Recognize deliberate calls and affectionate messages addressed to Katherine."""
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
    r"disculpa|perdona|escucha|gracias)\s*[,!:;]?\s+",
    flags=re.IGNORECASE,
)
_CALL_PREFIX_RE = re.compile(
    r"^[¡!¿?]*\s*(?:oye|ey|hey|escucha|disculpa|perdona)\s*[,!:;]?\s+",
    flags=re.IGNORECASE,
)
_NAME_TOKEN_RE = re.compile(
    r"(?<![\w])@?([^\W\d_][\w'-]{1,23})(?![\w])",
    flags=re.UNICODE,
)
_DIRECT_REQUEST_RE = re.compile(
    r"\b(?:que|qué|como|cómo|quien|quién|cuando|cuándo|donde|dónde|"
    r"puedes|podrias|podrías|quieres|ayudarme|ayudame|ayúdame|ayudas|"
    r"dime|explica|explicame|explícame|revisa|mira|escucha|consulta|"
    r"busca|recomiendame|recomiéndame|cuentame|cuéntame|"
    r"haz|resuelve|enseña|ensename|enséñame|"
    r"opinas|crees|sabes|puedes|me ayudas)\b",
    flags=re.IGNORECASE,
)
_DIRECT_CALL_START_RE = re.compile(
    r"^[\s,;:!?¡¿]*(?:puedes|podrias|podrías|quieres|ayudarme|ayudame|ayúdame|"
    r"ayudas|dime|explica|explicame|explícame|revisa|mira|escucha|consulta|"
    r"busca|recomiendame|recomiéndame|cuentame|cuéntame|haz|resuelve|"
    r"enseña|ensename|enséñame|me ayudas)\b"
    r"|^[\s,;:!?¡¿]*¿\s*(?:que|qué|como|cómo|quien|quién|cuando|cuándo|donde|dónde)\b",
    flags=re.IGNORECASE,
)
_AFFECTION_RE = re.compile(r"\bte\s+(?:amo|quiero)\b", flags=re.IGNORECASE)
_NEGATED_AFFECTION_RE = re.compile(
    r"\b(?:no|nunca|jamas|jamás|ya\s+no)\s+te\s+(?:amo|quiero)\b",
    flags=re.IGNORECASE,
)

_WARM_VOCATIVE_RE = re.compile(
    r"\b(?:gracias|mil\s+gracias|te\s+agradezco|tqm|tkm|te\s+(?:amo|quiero|adoro)|"
    r"me\s+caes\s+(?:muy\s+)?bien|eres\s+(?:(?:muy|tan|super)\s+)?"
    r"(?:linda|bonita|hermosa|preciosa|guapa|adorable|tierna|encantadora|"
    r"genial|increible|especial|maravillosa|unica)|"
    r"me\s+encanta\s+hablar\s+contigo|te\s+extra[nñ]e|"
    r"que\s+(?:linda|bonita|hermosa|preciosa|genial|tierna)\s+(?:eres|te\s+ves)|"
    r"eres\s+(?:la\s+mejor|un\s+amor|un\s+sol)|me\s+haces\s+feliz)\b",
    flags=re.IGNORECASE,
)


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


def _is_direct_vocative(text: str, match: re.Match) -> bool:
    """Avoid reacting to casual third-person mentions while allowing any sentence position."""
    if match.start() == 0:
        return True
    if text[match.start():match.end()].startswith("@"):
        return True

    before = text[:match.start()]
    after = text[match.end():]
    before_trimmed = before.rstrip()
    after_trimmed = after.lstrip()

    # Commas and similar separators are the strongest signal of a vocative.
    if before_trimmed.endswith((",", ":", ";")) or after_trimmed.startswith((",", ":", ";")):
        return True

    # Names at the end are accepted when the sentence is a greeting, request,
    # question, or explicit affectionate expression.
    if not after_trimmed.strip(" .!?¡¿"):
        return bool(
            _GREETING_RE.match(before_trimmed + " ")
            or _DIRECT_REQUEST_RE.search(before)
            or _AFFECTION_RE.search(before)
            or _WARM_VOCATIVE_RE.search(before)
            or "?" in before
            or "¿" in before
            or "!" in before
        )

    # Mid-sentence invocations must look like a request/question, not a casual
    # reference such as "Hoy hablé con Katherine en el chat".
    return bool(
        _DIRECT_REQUEST_RE.search(before)
        or _DIRECT_CALL_START_RE.search(after)
        or "?" in before
        or "¿" in before
    )


def _remove_vocative(text: str, start: int, end: int) -> str:
    """Remove the name and its adjacent vocative separators without mangling the sentence."""
    before = text[:start].rstrip()
    after = text[end:].lstrip()

    # Remove an address prefix that only served to call Katherine.
    greeting = _CALL_PREFIX_RE.match(before + " ")
    if greeting:
        before = before[greeting.end():].strip()

    if before.endswith((",", ":", ";")):
        before = before[:-1].rstrip()
    after = re.sub(r"^[,;:!]+\s*", "", after)

    if before and after:
        result = f"{before} {after}"
    else:
        result = before or after
    result = re.sub(r"\s+([,;:!?])", r"\1", result)
    result = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", result)
    result = re.sub(r"\s{2,}", " ", result)
    return result.strip(" ,;:—")


def extract_katherine_call(message: str) -> str | None:
    """Return the message body when Katherine is addressed at the start, middle or end.

    Exact nicknames and close spelling variants are recognized as separate name
    tokens. Mid/end-position occurrences require vocative punctuation, a request,
    a question or an affectionate expression to avoid casual third-person triggers.
    """
    text = str(message or "").strip()
    if not text:
        return None

    for match in _NAME_TOKEN_RE.finditer(text):
        if not _is_katherine_name(match.group(1)):
            continue
        if not _is_direct_vocative(text, match):
            continue
        return _remove_vocative(text, match.start(), match.end())
    return None


def detect_affectionate_expression(message: str) -> str | None:
    """Classify clear warm messages addressed to Katherine without overreading them."""
    text = _normalize_name(str(message or ""))
    if not text:
        return None

    # Explicit rejection/negation always wins over a positive keyword nearby.
    if re.search(
        r"\b(?:no|nunca|jamas|ya\s+no)\s+(?:te\s+(?:amo|quiero|adoro)|"
        r"eres\s+(?:linda|bonita|hermosa|genial))\b",
        text,
    ):
        return None
    if _NEGATED_AFFECTION_RE.search(text):
        return None

    if re.search(r"\b(?:te\s+amo|te\s+adoro|te\s+quiero\s+con\s+todo)\b", text):
        return "love"
    if re.search(r"\b(?:te\s+quiero|te\s+aprecio|tqm|tkm|me\s+caes\s+muy\s+bien)\b", text):
        return "care"
    if re.search(r"\b(?:gracias|mil\s+gracias|te\s+agradezco|gracias\s+por)\b", text):
        return "gratitude"
    if re.search(
        r"\b(?:eres|estas|sos)\s+(?:(?:muy|tan|super|s[uú]per)\s+)?"
        r"(?:linda|bonita|hermosa|preciosa|guapa|adorable|tierna|"
        r"encantadora|genial|increible|especial|maravillosa|unica)\b"
        r"|\bme\s+encanta\s+(?:hablar\s+contigo|tu\s+forma\s+de\s+ser)\b"
        r"|\bme\s+alegras\s+el\s+dia\b"
        r"|\bque\s+(?:linda|bonita|hermosa|preciosa|genial|tierna)\s+(?:eres|te\s+ves)\b"
        r"|\beres\s+(?:la\s+mejor|un\s+amor|un\s+sol)\b"
        r"|\bme\s+haces\s+feliz\b",
        text,
    ):
        return "compliment"
    if re.search(
        r"^(?:hola|holi|buenas|buenos\s+dias|buenas\s+tardes|"
        r"buenas\s+noches|que\s+tal|como\s+estas)[.!?¡¿,\s]*$",
        text,
    ):
        return "greeting"
    if re.search(r"\b(?:te\s+extra[nñ]e|te\s+ech[eé]\s+de\s+menos)\b", text):
        return "miss_you"
    return None
