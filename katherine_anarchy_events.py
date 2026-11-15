"""Estado efímero de participantes en llamadas de Anarchy.

No guarda mensajes ni escribe participantes a disco. Solo conserva el último
estado de voz en RAM, con TTL y límites globales.
"""
from __future__ import annotations

import time
from typing import Any

VOICE_STATE_TTL_SECONDS = 30 * 60
MAX_VOICE_ROOMS = 40
MAX_VOICE_PARTICIPANTS_PER_ROOM = 100

VOICE_ROOMS: dict[str, dict[str, Any]] = {}


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "si", "sí", "on", "speaker_role"}:
            return True
        if normalized in {"false", "0", "no", "off", "silent", "not_speaking"}:
            return False
    return None


def _containers(event: dict) -> list[dict]:
    containers = [event]
    for key in ("data", "payload", "participant", "member", "user", "author", "speaker"):
        value = event.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for parent in tuple(containers):
        for key in ("participant", "member", "user", "author", "speaker"):
            value = parent.get(key)
            if isinstance(value, dict) and value not in containers:
                containers.append(value)
    return containers


def _participant(event: dict) -> tuple[str, str] | None:
    containers = _containers(event)
    uid = ""
    name = ""
    for item in containers:
        for field in (
            "uid", "userId", "user_id", "participantId",
            "participant_id", "memberId", "member_id",
        ):
            value = item.get(field)
            if value is not None and str(value).strip():
                uid = str(value).strip()[:160]
                break
        if uid:
            break

    if not uid:
        for item in containers[1:]:
            value = item.get("id")
            if value is not None and str(value).strip():
                uid = str(value).strip()[:160]
                break

    if not uid:
        return None

    for item in containers:
        for field in ("nickname", "displayName", "display_name", "name", "username"):
            value = item.get(field)
            if value is not None and str(value).strip():
                name = " ".join(str(value).split())[:80]
                break
        if name:
            break
    return uid, name or uid[:24]


def _speaker_role_value(event: dict, default: bool = False) -> bool:
    """Read Anarchy's speaker/listener role flag, not microphone activity."""
    for item in _containers(event):
        parsed = _as_bool(item.get("speaker"))
        if parsed is not None:
            return parsed
    return default


def _prune(now: float) -> None:
    expired_rooms = []
    for thread_id, state in tuple(VOICE_ROOMS.items()):
        participants = state.get("participants")
        if not isinstance(participants, dict):
            expired_rooms.append(thread_id)
            continue
        for uid, participant in tuple(participants.items()):
            if now - float(participant.get("last_seen", 0)) > VOICE_STATE_TTL_SECONDS:
                participants.pop(uid, None)
        if not participants or now - float(state.get("updated_at", 0)) > VOICE_STATE_TTL_SECONDS:
            expired_rooms.append(thread_id)
    for thread_id in expired_rooms:
        VOICE_ROOMS.pop(thread_id, None)

    while len(VOICE_ROOMS) > MAX_VOICE_ROOMS:
        oldest = min(
            VOICE_ROOMS,
            key=lambda key: float(VOICE_ROOMS[key].get("updated_at", 0)),
        )
        VOICE_ROOMS.pop(oldest, None)


def update_voice_room_event(event: dict, now: float | None = None) -> bool:
    """Apply one room event; returns False when the payload lacks a usable identity."""
    if not isinstance(event, dict):
        return False
    kind = str(event.get("type") or "").strip()
    thread_id = str(event.get("threadId") or event.get("thread_id") or "").strip()
    if not thread_id:
        return False

    timestamp = time.monotonic() if now is None else float(now)
    _prune(timestamp)

    if kind in {"room.end", "room.kicked"}:
        VOICE_ROOMS.pop(thread_id, None)
        return True

    actor = _participant(event)
    if actor is None:
        return False
    uid, name = actor

    if kind == "room.leave":
        state = VOICE_ROOMS.get(thread_id)
        if state:
            state["participants"].pop(uid, None)
            state["updated_at"] = timestamp
            if not state["participants"]:
                VOICE_ROOMS.pop(thread_id, None)
        return True

    if kind not in {"room.join", "room.speaker"}:
        return False

    state = VOICE_ROOMS.setdefault(
        thread_id,
        {"participants": {}, "updated_at": timestamp},
    )
    participants = state["participants"]
    participant = participants.get(uid, {})
    participant.update({
        "name": name,
        "speaker_role": _speaker_role_value(
            event, default=bool(participant.get("speaker_role"))
        ),
        "last_seen": timestamp,
    })
    participants[uid] = participant
    state["updated_at"] = timestamp

    while len(participants) > MAX_VOICE_PARTICIPANTS_PER_ROOM:
        oldest_uid = min(
            participants,
            key=lambda key: float(participants[key].get("last_seen", 0)),
        )
        participants.pop(oldest_uid, None)
    _prune(timestamp)
    return True


def clear_voice_room(thread_id: str) -> None:
    """Forget transient participant state when a room ends or the bot is removed."""
    VOICE_ROOMS.pop(str(thread_id or ""), None)


def get_voice_participants(thread_id: str, now: float | None = None) -> list[dict[str, Any]]:
    timestamp = time.monotonic() if now is None else float(now)
    _prune(timestamp)
    state = VOICE_ROOMS.get(str(thread_id or ""))
    if not state:
        return []
    return sorted(
        (dict(item, uid=uid) for uid, item in state["participants"].items()),
        key=lambda item: (not bool(item.get("speaker_role")), str(item.get("name", "")).casefold()),
    )


def format_voice_state(
    thread_id: str, mode: str = "participantes", now: float | None = None
) -> str:
    """Format current participant state without exposing internal identifiers."""
    participants = get_voice_participants(thread_id, now=now)
    selected_mode = str(mode or "participantes").strip().casefold()
    if selected_mode in {"hablando", "speakers", "speaker_role"}:
        selected = [item for item in participants if item.get("speaker_role")]
        heading = "🎙️ PARTICIPANTES CON ROL DE HABLANTE"
        empty = "No hay participantes con rol de hablante detectados en esta llamada."
    else:
        selected = participants
        heading = "🎧 PARTICIPANTES EN LA LLAMADA"
        empty = "Todavía no tengo datos recientes de participantes para esta llamada."

    if not selected:
        return empty

    shown = selected[:15]
    lines = [heading]
    for item in shown:
        role = "sí" if item.get("speaker_role") else "no"
        lines.append(f"• {item['name']} · ROL DE HABLANTE: {role}")
    if len(selected) > len(shown):
        lines.append(f"… y {len(selected) - len(shown)} más.")
    return "\n".join(lines)
