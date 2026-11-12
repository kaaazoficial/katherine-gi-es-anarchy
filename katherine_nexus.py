"""Katherine Nexus: deterministic natural-language routing to supported commands.

This module deliberately does not call external services, store user content, or
invent API capabilities. It only translates high-confidence Spanish requests
into commands already implemented by Katherine.
"""
from __future__ import annotations

import re
import unicodedata


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("¿?¡!.,;: ")


def interpret_natural_request(value: str) -> str | None:
    """Return an existing Katherine command (without prefix), or None.

    Only anchored, high-confidence patterns are routed. Ordinary conversation
    remains untouched and continues through Katherine's existing auto-reply
    behavior.
    """
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw or len(raw) > 500 or raw.startswith(("!", "-")):
        return None
    text = _normalize(raw)
    if not text:
        return None

    text = re.sub(r"^(?:por favor\s+)?(?:katherine[, ]+)?", "", text)
    text = re.sub(r"\s+(?:por favor|porfa)$", "", text).strip()
    if not text:
        return None

    patterns = (
        (r"^(?:abre|abrir|muestra|mostrar) (?:el )?(?:menu|panel)(?: interactivo)?$", "menu"),
        (r"^(?:como esta|estado de) katherine$", "estado"),
        (r"^(?:ejecuta|haz|realiza) (?:un )?(?:autotest|prueba interna)$", "autotest"),
        (r"^(?:pausa|pausar|deten|detener) (?:la )?(?:musica|reproduccion)$", "dj pausa"),
        (r"^(?:reanuda|reanudar|continua|continuar) (?:la )?(?:musica|reproduccion)$", "dj reanudar"),
        (r"^(?:salta|saltar) (?:la )?(?:cancion|pista|tema)$", "dj saltar"),
        (r"^(?:muestra|mostrar|consulta) (?:la )?cola musical$", "dj cola"),
        (r"^(?:entra|entrar|unete|unirse) (?:a )?(?:la )?(?:llamada|sala de voz)$", "join"),
        (r"^(?:lee|leer) (?:los )?mensajes en voz alta$", "leer"),
        (r"^(?:deja de leer|desactiva la lectura en voz alta)$", "noleer"),
        (r"^(?:que )?hora es en ([a-z0-9_+/-]+)$", None),
    )
    for pattern, command in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            if command is not None:
                return command
            zone = match.group(1).strip()
            return f"zona {zone}" if re.fullmatch(r"[a-z0-9_+/-]{2,64}", zone) else None

    subject_patterns = (
        (r"^(?:dame|muestra|busca)?\s*(?:una )?guia (?:de |del )?(.+)$", "guia"),
        (r"^(?:como armo|como construir|build de|build para|equipa) (.+)$", "guia"),
        (r"^(?:que equipos hay para|equipos para|equipo de|composiciones para) (.+)$", "equipos"),
        (r"^(?:cual es la )?rotacion (?:de |para )?(.+)$", "rotacion"),
        (r"^(?:que materiales necesita|materiales para|materiales de) (.+)$", "materiales"),
        (r"^(?:busca en genshin|busqueda de genshin|busca genshin) (.+)$", "buscargenshin"),
        (r"^(?:reproduce|pon|busca musica de|busca la cancion) (.+)$", "dj buscar"),
        (r"^(?:busca imagenes? de|imagenes? de) (.+)$", "imagen"),
        (r"^(?:busca videos? de|videos? de) (.+)$", "video"),
        (r"^(?:busca audios? de|audios? de) (.+)$", "audio"),
    )
    for pattern, command in subject_patterns:
        match = re.fullmatch(pattern, text)
        if match:
            subject = re.sub(r"\s+", " ", match.group(1)).strip(" .?!")
            if not subject or len(subject) > 120:
                return None
            if command in {"imagen", "video", "audio"}:
                return f"multimedia {command} {subject}"
            return f"{command} {subject}"

    translation = re.fullmatch(
        r"(?:katherine[, ]+)?(?:traduce|traducir) (?:esto )?al?\s*([a-záéíóúñ ]{2,30})\s*[:|,]\s*(.{1,350})",
        raw,
        flags=re.IGNORECASE,
    )
    if translation:
        language = _normalize(translation.group(1))
        payload = translation.group(2).strip()
        if payload:
            return f"traducir {language} | {payload}"

    return None
