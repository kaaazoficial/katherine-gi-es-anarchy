"""ASTRA NOVA pure helpers: music intent and safe DJ recovery rules."""
from __future__ import annotations

import re
import unicodedata


DJ_MOOD_QUERIES = {
    "estudiar": "instrumental focus study ambient",
    "concentracion": "instrumental focus study ambient",
    "trabajar": "lofi chill instrumental",
    "relajarse": "calm ambient piano relaxing",
    "dormir": "soft ambient sleep meditation",
    "jugar": "epic cinematic game soundtrack",
    "gaming": "epic cinematic game soundtrack",
    "combate": "epic battle orchestral",
    "triste": "melancholic piano ambient",
    "feliz": "upbeat cheerful instrumental",
    "anime": "anime instrumental soundtrack",
}


def normalize_music_mood(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


def resolve_music_mood(value: str) -> str:
    """Map common Spanish moods to useful catalog search terms."""
    normalized = normalize_music_mood(value)
    return DJ_MOOD_QUERIES.get(normalized, normalized or "ambient instrumental")


def is_recoverable_dj_failure(message: str) -> bool:
    """Only skip to another result when the selected audio itself is invalid."""
    value = str(message or "").casefold()
    return any(marker in value for marker in (
        "no pude descargar o validar",
        "no pasó la validación de audio",
        "formato de audio no compatible",
        "no contiene una pista de audio",
    ))
