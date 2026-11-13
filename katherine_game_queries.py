"""Intent-aware Genshin question parsing and safe API response validation."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


REGION_ALIASES = {
    "mondstadt": "Mondstadt",
    "liyue": "Liyue",
    "inazuma": "Inazuma",
    "sumeru": "Sumeru",
    "fontaine": "Fontaine",
    "natlan": "Natlan",
    "snezhnaya": "Snezhnaya",
    "nod-krai": "Nod-Krai",
    "nod krai": "Nod-Krai",
    "khaenriah": "Khaenri'ah",
    "khaenri'ah": "Khaenri'ah",
    "dragonspine": "Espinadragón",
    "enkanomiya": "Enkanomiya",
    "the chasm": "La Sima",
    "chasm": "La Sima",
}
ELEMENT_ALIASES = {
    "pyro": "Pyro", "fuego": "Pyro",
    "hydro": "Hydro", "agua": "Hydro",
    "electro": "Electro", "rayo": "Electro",
    "cryo": "Cryo", "hielo": "Cryo",
    "dendro": "Dendro", "naturaleza": "Dendro",
    "anemo": "Anemo", "viento": "Anemo",
    "geo": "Geo", "tierra": "Geo",
}


def normalize_game_query(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9' -]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .?!,;:")


def strip_game_scope_qualifier(value: str) -> str:
    """Remove a trailing game qualifier without deleting it from the entity name."""
    text = normalize_game_query(value)
    if text in {"genshin", "genshin impact", "teyvat"}:
        return text
    text = re.sub(
        r"\s*(?:\(|,)?\s*(?:(?:en|de|del|dentro de|del juego)\s+)?"
        r"(?:genshin(?:\s+impact)?|teyvat)\)?$",
        "",
        text,
    )
    return text.strip(" ,.-")


def resolve_genshin_entity_intent(value: str) -> tuple[str, str] | None:
    """Return (existing command, clean entity) for high-confidence game questions.

    The resolver identifies what the user is asking about, not just whether the
    message contains the words 'Genshin Impact'.
    """
    text = normalize_game_query(value)
    explicit_game = bool(re.search(r"\b(?:genshin(?: impact)?|teyvat)\b", text))
    prefixes = (
        r"^(?:que es|que significa|hablame de|cuentame de|que sabes de|"
        r"que region es|donde esta|donde se encuentra|informacion sobre|"
        r"explicame)\s+(?:la region de\s+|region\s+)?(.+)$",
    )
    for pattern in prefixes:
        match = re.fullmatch(pattern, text)
        if not match:
            continue
        subject = strip_game_scope_qualifier(match.group(1))
        if subject in REGION_ALIASES:
            return "geografia", REGION_ALIASES[subject]
        element_match = re.fullmatch(r"(?:el elemento\s+)?(.+)", subject)
        if element_match and element_match.group(1) in ELEMENT_ALIASES:
            return "elemento", ELEMENT_ALIASES[element_match.group(1)]
        if explicit_game and subject:
            return "enciclopedia", subject

    patterns = (
        (r"^(?:quien es|quien fue|quien es el|quien es la)\s+(.+)$", "enciclopedia"),
        (r"^(?:que arma usa|que armas usa|mejor arma para|armas para|build de|guia de)\s+(.+)$", "guia"),
        (r"^(?:que materiales necesita|materiales para|materiales de ascension de)\s+(.+)$", "materiales"),
        (r"^(?:que equipos hay para|equipos para|equipo de|composiciones para)\s+(.+)$", "equipos"),
        (r"^(?:cual es la rotacion de|rotacion de|rotacion para)\s+(.+)$", "rotacion"),
        (r"^(?:que reacciones tiene|reacciones de)\s+(.+)$", "reacciones"),
    )
    for pattern, command in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            subject = strip_game_scope_qualifier(match.group(1))
            if subject and (explicit_game or command != "enciclopedia"):
                return command, subject

    # A region can be named without the exact "qué es" framing.
    for raw_alias, display_name in REGION_ALIASES.items():
        if re.search(rf"\b{re.escape(raw_alias)}\b", text):
            if any(marker in text for marker in (
                "region", "pais", "nacion", "territorio", "donde esta", "que es",
            )):
                return "geografia", display_name
    return None


def infer_genshin_data_folder(value: str) -> str | None:
    """Infer the correct Genshin-DB folder for a canonical entity name."""
    subject = strip_game_scope_qualifier(value)
    if subject in REGION_ALIASES:
        return "geographies"
    if subject in ELEMENT_ALIASES:
        return "elements"
    return None


def unwrap_genshin_db_response(payload: Any) -> Any:
    """Unwrap dumpResult metadata and discard explicit no-match responses."""
    if not isinstance(payload, dict):
        return payload
    metadata_keys = {
        "matchtype", "matchType", "matchfolder", "matchFolder",
        "folder", "query", "filename", "options", "match",
    }
    if not metadata_keys.intersection(payload):
        return payload
    match_type = str(payload.get("matchtype") or payload.get("matchType") or "").strip().casefold()
    if match_type in {"none", "no match", "nomatch", "no_match"}:
        return None
    if "result" in payload:
        result = payload.get("result")
        return result if isinstance(result, (dict, list)) and result else None
    # Never pass the API's diagnostic envelope off as a real entity.
    return None
