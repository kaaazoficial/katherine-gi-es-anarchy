"""Nexus helpers for Katherine: safe routing and ephemeral state."""
from __future__ import annotations
import re
import time
import unicodedata
from typing import Any

from katherine_topics import is_genshin_related_request


GENSHIN_COMMANDS = frozenset({
    "enciclopedia", "guia", "equipos", "rotacion", "materiales",
    "cobertura", "buscargenshin",
})


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().strip("¿?¡!.,;: ")

COMMAND_ALIASES = {
    "!buscar": ("!dj", ("buscar",)),
    "!guardian": ("!admin", ()),
}


def parse_command_invocation(
    content: str, used_prefix: str
) -> tuple[str, list[str]] | None:
    """Parse one prefixed command and canonicalize aliases in a single pass."""
    text = str(content or "").strip()
    prefix = str(used_prefix or "")
    if not text or not prefix:
        return None

    parts = text.split()
    token = parts[0]
    if not token.startswith(prefix) or len(token) <= len(prefix):
        return None

    command = "!" + token[len(prefix):].casefold()
    args = parts[1:]
    alias = COMMAND_ALIASES.get(command)
    if alias:
        command, prepended_args = alias
        args = [*prepended_args, *args]
    return command, args


def should_execute_deterministic_fallback(
    request_text: str,
    fallback_command: str,
    provider_failed: bool = False,
) -> bool:
    """Only use NEXUS fallback when the request is an explicit action/query.

    A conversational or knowledge question must not be overridden by a
    speculative command inferred from broad words such as "what is".
    """
    invocation = re.sub(r"\s+", " ", str(fallback_command or "")).strip()
    if not invocation or invocation.casefold().startswith("asistente "):
        return False
    raw = re.sub(r"\s+", " ", str(request_text or "")).strip()
    addressed = re.match(
        r"^(?:oye[, ]+)?katherine(?:[, :]+|$)\s*(.*)$",
        raw,
        flags=re.IGNORECASE,
    )
    if addressed:
        raw = addressed.group(1).strip()
    text = _normalize(raw)
    if not text:
        return False
    # When the provider is unavailable, retain the deterministic fallback.
    # The caller still validates the command and the original handler checks
    # permissions, while the interpreter below filters generic Teyvat queries.
    if provider_failed:
        return True
    explicit_starters = (
        "ayuda", "comandos disponibles", "que puedes hacer", "que sabes hacer",
        "abre ", "abrir ", "muestra ", "mostrar ", "consulta ", "revisa ",
        "dime ", "cuentame ", "haz ", "ejecuta ", "realiza ",
        "pausa ", "pausar ", "deten ", "detener ", "reanuda ", "reanudar ",
        "continua ", "continuar ", "salta ", "saltar ", "entra ", "entrar ",
        "unete ", "unirse ", "lee ", "leer ", "deja de leer", "desactiva ",
        "crea ", "crear ", "convoca ", "dame ", "di ", "lanza ", "tira ",
        "recomiendame ", "ponme ", "reproduce ", "pon ", "busca ",
        "investiga ", "investigar ", "haz una investigacion ",
        "busca informacion ", "analiza ", "resume ", "traduce ", "traducir ",
        "que equipos hay para ", "equipos para ", "equipo de ",
        "composiciones para ", "cual es la rotacion ", "rotacion de ",
        "rotacion para ", "que materiales necesita ", "materiales para ",
        "materiales de ", "que cobertura tiene ", "cobertura de ",
        "como armo ", "como construir ", "build de ", "build para ",
        "equipa ", "que hora es en ",
    )
    return any(text.startswith(prefix) for prefix in explicit_starters)


def interpret_natural_request(value: str) -> str | None:
    """Route only high-confidence Spanish requests to existing commands."""
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw or len(raw) > 500 or raw.startswith(("!", "-")):
        return None
    # Procesar lenguaje natural únicamente cuando el usuario invoque a Katherine.
    addressed = re.match(
        r"^(?:oye[, ]+)?katherine(?:[, :]+|$)\s*(.*)$",
        raw,
        flags=re.IGNORECASE,
    )
    if not addressed:
        return None
    raw = addressed.group(1).strip()
    text = _normalize(raw)
    text = re.sub(r"\s+(?:por favor|porfa)$", "", text).strip()
    if not text:
        # Mencionar solo el nombre (o "Oye Katherine") no abre menús ni responde.
        return None
    patterns = (
        (r"^(?:abre|abrir|muestra|mostrar) (?:el )?(?:menu|panel)(?: interactivo)?$", "menu"),
        (r"^(?:ayuda|que puedes hacer|que sabes hacer|comandos disponibles)$", "ayuda"),
        (r"^(?:revisa|consulta|muestra) (?:el )?(?:estado|salud|diagnostico)(?: del bot| de katherine)?$", "estado"),
        (r"^(?:abre|muestra|consulta) (?:el )?(?:panel de administracion|guardian)$", "guardian"),
        (r"^(?:dime|cuentame) (?:un )?chiste$", "chiste"),
        (r"^(?:dame|di) (?:una )?frase$", "frase"),
        (r"^(?:lanza|tira) (?:un )?dado$", "dado"),
        (r"^(?:lanza|tira) (?:una )?moneda$", "moneda"),
        (r"^(?:consulta|muestra|dime) (?:mis )?puntos$", "puntos"),
        (r"^(?:muestra|abre|consulta) (?:el )?ranking$", "ranking"),
        (r"^(?:muestra|consulta) (?:mi )?perfil$", "perfil"),
        (r"^(?:dime|consulta) (?:la )?fecha$", "fecha"),

        (r"^(?:abre|abrir|muestra|mostrar) (?:el )?centro(?: de control)?$", "centro"),
        (r"^(?:como esta|estado de|diagnostico de) katherine$", "estado"),
        (r"^(?:diagnostica) katherine$", "estado"),
        (r"^(?:que fuentes|fuentes disponibles|estado de las fuentes)$", "fuentes"),
        (r"^(?:ejecuta|haz|realiza) (?:un )?(?:autotest|prueba interna)$", "autotest"),
        (r"^(?:pausa|pausar|deten|detener) (?:la )?(?:musica|reproduccion)$", "dj pausa"),
        (r"^(?:reanuda|reanudar|continua|continuar) (?:la )?(?:musica|reproduccion)$", "dj reanudar"),
        (r"^(?:salta|saltar) (?:la )?(?:cancion|pista|tema)$", "dj saltar"),
        (r"^(?:muestra|mostrar|consulta) (?:la )?cola musical$", "dj cola"),
        (r"^(?:que esta sonando|cancion actual|pista actual)$", "dj actual"),
        (r"^(?:entra|entrar|unete|unirse) (?:a )?(?:la )?(?:llamada|sala de voz)$", "join"),
        (r"^(?:lee|leer) (?:los )?mensajes en voz alta$", "leer"),
        (r"^(?:deja de leer|desactiva la lectura en voz alta)$", "noleer"),
        (r"^(?:crea|crear|convoca) (?:un )?(?:evento|actividad)(?: de comunidad)?$", "evento"),
        (r"^(?:muestra|mostrar|consulta) (?:el )?evento(?: de la sala)?$", "evento"),
        (r"^(?:muestra|mostrar|consulta) (?:la )?actividad(?: del chat)?$", "actividad"),
        (r"^(?:que )?hora es en ([a-z0-9_+/-]+)$", None),
    )
    for pattern, command in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            if command:
                return command
            zone = match.group(1).strip()
            return f"zona {zone}" if re.fullmatch(r"[a-z0-9_+/-]{2,64}", zone) else None
    subjects = (
        (r"^(?:investiga|investigar|haz una investigacion sobre|busca informacion sobre|investiga en internet) (.+)$", "investigar"),
        (r"^(?:analiza|resume|lee) (?:este )?(?:enlace|link|articulo) (https://\S+)$", "enlace"),
        (r"^(?:resume|analiza) (https://\S+)$", "enlace"),
        (r"^(?:dame|muestra|busca)?\s*(?:una )?guia (?:de |del )?(.+)$", "guia"),
        (r"^(?:como armo|como construir|build de|build para|equipa) (.+)$", "guia"),
        (r"^(?:que equipos hay para|equipos para|equipo de|composiciones para) (.+)$", "equipos"),
        (r"^(?:cual es la )?rotacion (?:de |para )?(.+)$", "rotacion"),
        (r"^(?:que materiales necesita|materiales para|materiales de) (.+)$", "materiales"),
        (r"^(?:que cobertura tiene|cobertura de|apartados de la guia de) (.+)$", "cobertura"),
        (r"^(?:busca en genshin|busqueda de genshin|busca genshin) (.+)$", "buscargenshin"),
        (r"^(?:haz un roast a|roast a|insulta amistosamente a) (.+)$", "roast"),
        (r"^(?:crea|haz) (?:una )?encuesta (?:sobre )?(.+)$", "encuesta"),
        (r"^(?:consulta|abre|busca en)?\s*(?:la )?enciclopedia(?: de genshin)? (?:sobre |de |del )?(.+)$", "enciclopedia"),
        (r"^(?:quien es|que es|que sabes de) (.+)$", "enciclopedia"),
        (r"^(?:reproduce|pon|busca musica de|busca la cancion) (.+)$", "dj buscar"),
        (r"^(?:recomiendame|ponme) musica (?:para|de) (.+)$", "dj ambiente"),
        (r"^(?:busca imagenes? de|imagenes? de) (.+)$", "imagen"),
        (r"^(?:busca videos? de|videos? de) (.+)$", "video"),
        (r"^(?:busca audios? de|audios? de) (.+)$", "audio"),
    )
    for pattern, command in subjects:
        match = re.fullmatch(pattern, text)
        if match:
            subject = re.sub(r"\s+", " ", match.group(1)).strip(" .?!")
            if not subject or len(subject) > 120:
                return None
            if command == "enciclopedia" and re.match(
                r"^(?:quien es|que es|que sabes de)\b", text
            ):
                # A trailing "en Genshin" describes the setting, not necessarily
                # an encyclopedia entity. Require the subject itself to be a
                # recognizable game entity/topic, otherwise let the AI answer.
                subject_for_domain = re.sub(
                    r"\s*(?:,|\b(?:en|de))\s*genshin(?: impact)?$",
                    "",
                    _normalize(subject),
                ).strip()
                if not is_genshin_related_request(subject_for_domain):
                    return f"asistente {raw[:300]}"
            if command in GENSHIN_COMMANDS and not is_genshin_related_request(text):
                # Never route a generic app/platform/person question to Teyvat.
                continue
            return f"multimedia {command} {subject}" if command in {"imagen", "video", "audio"} else f"{command} {subject}"
    translation = re.fullmatch(
        r"(?:traduce|traducir) (?:esto )?al?\s*([a-záéíóúñ ]{2,30})\s*[:|,]\s*(.{1,350})",
        raw, flags=re.IGNORECASE
    )
    if translation:
        language = _normalize(translation.group(1))
        payload = translation.group(2).strip()
        if payload:
            return f"traducir {language} | {payload}"
    # Una invocación explícita siempre recibe una respuesta, incluso si la
    # solicitud todavía no tiene una acción específica registrada.
    return f"asistente {raw[:300]}"

def extract_message_id(payload: Any) -> str | None:
    """Extract message ids from common POST response envelopes."""
    if isinstance(payload, dict):
        for key in ("messageId", "message_id"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
        for key in ("message", "data", "result", "item"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                found = extract_message_id(nested)
                if found:
                    return found
                if key == "message":
                    value = nested.get("id")
                    if isinstance(value, (str, int)) and str(value).strip():
                        return str(value)
    # Una invocación explícita siempre obtiene respuesta. Si no existe una
    # intención segura conocida, el núcleo devuelve sus capacidades disponibles.
    return None

def cleanup_community_events(events: dict, now: float | None = None) -> int:
    now = time.time() if now is None else float(now)
    expired = [key for key, item in events.items()
               if not isinstance(item, dict) or item.get("closed")
               or float(item.get("expires_at", 0)) <= now]
    for key in expired:
        events.pop(key, None)
    return len(expired)

def toggle_community_participant(event: dict, user_id: str, nickname: str,
                                 join: bool, now: float | None = None) -> tuple[str, int]:
    now = time.time() if now is None else float(now)
    if not isinstance(event, dict) or event.get("closed"):
        return "closed", 0
    if float(event.get("expires_at", 0)) <= now:
        return "expired", 0
    participants = event.setdefault("participants", {})
    user_id = str(user_id or "").strip()
    if not user_id:
        return "invalid", len(participants)
    if join:
        if user_id in participants:
            return "already_joined", len(participants)
        capacity = max(1, min(200, int(event.get("capacity", 40))))
        if len(participants) >= capacity:
            return "full", len(participants)
        participants[user_id] = str(nickname or "Viajero")[:80]
        return "joined", len(participants)
    if user_id not in participants:
        return "not_joined", len(participants)
    participants.pop(user_id, None)
    return "left", len(participants)

def format_source_health(source_health: dict, limit: int = 12) -> str:
    if not isinstance(source_health, dict) or not source_health:
        return "FUENTES EXTERNAS\nTodavía no hay mediciones de fuentes en este proceso."
    lines = ["FUENTES EXTERNAS · SALUD DEL PROCESO"]
    ranked = sorted(source_health.items(), key=lambda item: (
        int((item[1] or {}).get("failures", 0)),
        int((item[1] or {}).get("requests", 0))), reverse=True)
    for host, raw in ranked[:max(1, min(20, int(limit)))]:
        item = raw if isinstance(raw, dict) else {}
        requests = int(item.get("requests", 0))
        successes = int(item.get("successes", 0))
        failures = int(item.get("failures", 0))
        latency = float(item.get("last_latency_ms", 0) or 0)
        status = item.get("last_status") or "—"
        lines.append(f"• {str(host)[:80]} · HTTP {status} · {latency:.0f} ms\n"
                     f"  {successes}/{requests} correctas · {failures} fallidas")
        error = str(item.get("last_error") or "").strip()
        if error:
            lines.append(f"  Último error: {error[:110]}")
    lines.append("Los contadores se reinician cuando Render reinicia el proceso.")
    return "\n".join(lines)
