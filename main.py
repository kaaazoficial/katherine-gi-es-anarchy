# Katherine - Bot de Anarchy Apps (Python)
# Requiere Python 3.11+
import os
import re
import json
import time
import hmac
import hashlib
import html
import random
import secrets
import asyncio
import logging
import sqlite3
import sys
import unicodedata
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import psutil
except ImportError:
    psutil = None

try:
    import av as pyav
    import numpy as np
    from livekit import rtc as livekit_rtc
    DJ_IMPORT_ERROR = ""
except Exception as exc:
    pyav = None
    np = None
    livekit_rtc = None
    DJ_IMPORT_ERROR = str(exc)

try:
    import edge_tts
    TTS_IMPORT_ERROR = ""
except Exception as exc:
    edge_tts = None
    TTS_IMPORT_ERROR = str(exc)

from contextlib import contextmanager
from pathlib import Path
from collections import defaultdict, deque
from typing import Any
from html.parser import HTMLParser
from urllib.parse import urljoin, quote, urlparse

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from katherine_nexus import (
    cleanup_community_events,
    extract_message_id,
    format_source_health,
    interpret_natural_request,
    parse_command_invocation,
    toggle_community_participant,
)
from katherine_astra import (
    is_recoverable_dj_failure,
    resolve_music_mood,
)
from katherine_ai import KatherineAI, is_bot_author, validate_ai_tool_call

AI_CORE = KatherineAI()

# ---------------------------------------------------------------------------
# CONFIGURACION
# En Render: BOT_TOKEN, WEBHOOK_SECRET. Para bot de comunidad:
# COMMUNITY_BOT_TOKEN y COMMUNITY_WEBHOOK_SECRET.
# ---------------------------------------------------------------------------
API = "https://api.anarchyapps.org/bot-api/v1"
GAME_API = "https://genshin-db-api.vercel.app/api/v5"
PORT = int(os.getenv("PORT", "3000"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
COMMUNITY_TOKEN = os.getenv("COMMUNITY_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
COMMUNITY_SECRET = os.getenv("COMMUNITY_WEBHOOK_SECRET", "").strip()
PREFIX = os.getenv("COMMAND_PREFIX", "!").strip() or "!"
if len(PREFIX) > 3 or any(ch.isspace() for ch in PREFIX):
    PREFIX = "!"
COMMAND_PREFIXES = tuple(sorted({PREFIX, "!", "-"}, key=len, reverse=True))
def parse_admin_uids(value: str | None) -> set[str]:
    """Parse trusted administrator UIDs from comma/semicolon/newline-separated config."""
    return {
        item.strip()
        for item in re.split(r"[,;\r\n]+", str(value or ""))
        if item.strip()
    }


ADMIN_UIDS = parse_admin_uids(os.getenv("ADMIN_UIDS") or os.getenv("ADMINUIDS") or "")
ADMIN_ONLY_COMMANDS = frozenset({
    "!estado", "!fuentes", "!saludfuentes", "!autotest",
    "!centro", "!nexus",
    "!admin", "!paneladmin", "!guardian",
    "!respuestas", "!respuesta", "!advertir", "!advertencias",
    "!quitaradvertencia", "!antispam", "!perfilbot", "!avatarbot",
})
STARTED = time.time()
STARTED_MONOTONIC = time.monotonic()
HTTP_CLIENT: httpx.AsyncClient | None = None
PROCESS = psutil.Process(os.getpid()) if psutil else None
LAST_EVENT_AT = None
LAST_COMMAND_AT = None
LAST_COMMAND_NAME = None
LAST_COMMAND_USER = None

if not BOT_TOKEN:
    logging.warning("Falta BOT_TOKEN.")
if not WEBHOOK_SECRET:
    logging.warning("Falta WEBHOOK_SECRET. El webhook del bot principal rechazara eventos.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI(title="Katherine Anarchy Bot")

# ---------------------------------------------------------------------------
# BASE DE DATOS LIGERA: SQLite (incluida en Python, sin PostgreSQL).
# En Render, define DATA_DIR=/var/data y monta un Persistent Disk en /var/data
# para conservar datos entre despliegues. Sin disco, Render borra el archivo
# al reiniciar o desplegar; el bot seguirá funcionando, pero reiniciará datos.
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "katherine.sqlite3"


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_session():
    conn = db_connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_session() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS users (
            uid TEXT PRIMARY KEY,
            nickname TEXT NOT NULL,
            bio TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            points INTEGER NOT NULL DEFAULT 0,
            message_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            last_seen REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS affinities (
            uid TEXT NOT NULL,
            target_uid TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            last_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(uid, target_uid)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_uid TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            target_uid TEXT NOT NULL,
            target_name TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS wish_state (
            uid TEXT PRIMARY KEY,
            pity5 INTEGER NOT NULL DEFAULT 0,
            pity4 INTEGER NOT NULL DEFAULT 0,
            guaranteed INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS reward_claims (
            uid TEXT NOT NULL,
            action TEXT NOT NULL,
            claimed_at REAL NOT NULL,
            PRIMARY KEY(uid, action)
        )""")
        # Retira tablas de funciones personales que ya no forman parte del bot.
        for obsolete_table in (
            "saved_builds",
            "saved_teams",
            "farm_materials",
            "exploration_progress",
            "genshin_notes"
        ):
            db.execute(f"DROP TABLE IF EXISTS {obsolete_table}")

        # Elimina también la columna UID heredada de Enka, si todavía existe.
        user_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
        }
        if "genshin_uid" in user_columns:
            try:
                db.execute("ALTER TABLE users DROP COLUMN genshin_uid")
            except sqlite3.OperationalError:
                # Compatibilidad con SQLite antiguo: deja el campo vacío y sin uso.
                db.execute("UPDATE users SET genshin_uid=''")

        db.execute("CREATE INDEX IF NOT EXISTS idx_gifts_target ON gifts(target_uid, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_activity ON users(message_count DESC)")
        db.execute("""CREATE TABLE IF NOT EXISTS chat_settings (
            thread_id TEXT NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(thread_id, setting_key)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS moderation_warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            target_uid TEXT NOT NULL,
            target_name TEXT NOT NULL,
            moderator_uid TEXT NOT NULL,
            moderator_name TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            automatic INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            removed_at REAL,
            removed_by TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_warnings_target ON moderation_warnings(thread_id,target_uid,active,id DESC)")
        db.execute("""CREATE TABLE IF NOT EXISTS dj_playlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(thread_id, name_key)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS dj_playlist_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(playlist_id) REFERENCES dj_playlists(id) ON DELETE CASCADE
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_playlist_tracks ON dj_playlist_tracks(playlist_id,id)")

        db.execute("""CREATE TABLE IF NOT EXISTS chat_activity_daily (
            thread_id TEXT NOT NULL,
            uid TEXT NOT NULL,
            nickname TEXT NOT NULL,
            day_key TEXT NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(thread_id, uid, day_key)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_activity_daily_day ON chat_activity_daily(thread_id,day_key)")
        db.execute("""CREATE TABLE IF NOT EXISTS chat_activity_hourly (
            thread_id TEXT NOT NULL,
            day_key TEXT NOT NULL,
            hour_of_day INTEGER NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(thread_id, day_key, hour_of_day)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            actor_uid TEXT NOT NULL,
            actor_name TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_thread ON admin_audit_log(thread_id,id DESC)")
        db.execute("""CREATE TABLE IF NOT EXISTS auto_reply_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            trigger_phrase TEXT NOT NULL,
            response_text TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(thread_id, trigger_phrase)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS translation_daily_usage (
            day_key TEXT PRIMARY KEY,
            characters_used INTEGER NOT NULL DEFAULT 0
        )""")


def record_user(uid: str, nickname: str, count_message: bool = False):
    now = time.time()
    with db_session() as db:
        db.execute("""INSERT INTO users(uid,nickname,created_at,last_seen,message_count)
            VALUES(?,?,?,?,?)
            ON CONFLICT(uid) DO UPDATE SET nickname=excluded.nickname,
            last_seen=excluded.last_seen,
            message_count=users.message_count + excluded.message_count""",
            (uid, nickname[:80], now, now, 1 if count_message else 0))


def scoped_uid(scope_key: str, uid: str) -> str:
    return f"{scope_key}:{uid}" if scope_key else str(uid)


def get_user(uid: str):
    with db_session() as db:
        row = db.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else None


def find_user_by_nickname(nickname: str, scope_key: str | None = None):
    with db_session() as db:
        sql = "SELECT * FROM users WHERE lower(nickname)=lower(?)"
        params = [nickname.lstrip("@")]
        if scope_key:
            prefix = scope_key + ":"
            sql += " AND substr(uid,1,?)=?"
            params.extend([len(prefix), prefix])
        sql += " ORDER BY last_seen DESC LIMIT 1"
        row = db.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None


def get_chat_setting(thread_id: str, key: str, default: str = "") -> str:
    with db_session() as db:
        row = db.execute(
            "SELECT setting_value FROM chat_settings WHERE thread_id=? AND setting_key=?",
            (str(thread_id), str(key))
        ).fetchone()
        return str(row["setting_value"]) if row else str(default)


def set_chat_setting(thread_id: str, key: str, value: str):
    with db_session() as db:
        db.execute(
            """INSERT INTO chat_settings(thread_id,setting_key,setting_value,updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(thread_id,setting_key) DO UPDATE SET
               setting_value=excluded.setting_value, updated_at=excluded.updated_at""",
            (str(thread_id), str(key), str(value), time.time())
        )


def is_admin_uid(uid: str) -> bool:
    """Authorize only a non-empty, exact UID supplied by Anarchy Apps."""
    candidate = str(uid or "").strip()
    return bool(candidate) and candidate in ADMIN_UIDS


def add_moderation_warning(
    thread_id: str, target_uid: str, target_name: str,
    moderator_uid: str, moderator_name: str, reason: str = "",
    automatic: bool = False
) -> int:
    with db_session() as db:
        cursor = db.execute(
            """INSERT INTO moderation_warnings
               (thread_id,target_uid,target_name,moderator_uid,moderator_name,
                reason,automatic,active,created_at)
               VALUES(?,?,?,?,?,?,?,1,?)""",
            (
                str(thread_id), str(target_uid), str(target_name)[:80],
                str(moderator_uid), str(moderator_name)[:80],
                str(reason or "Sin motivo indicado")[:240],
                1 if automatic else 0, time.time()
            )
        )
        return int(cursor.lastrowid)


def get_moderation_warnings(thread_id: str, target_uid: str, limit: int = 10):
    with db_session() as db:
        rows = db.execute(
            """SELECT id,target_name,moderator_name,reason,automatic,active,created_at
               FROM moderation_warnings
               WHERE thread_id=? AND target_uid=?
               ORDER BY id DESC LIMIT ?""",
            (str(thread_id), str(target_uid), max(1, min(30, int(limit))))
        ).fetchall()
        return [dict(row) for row in rows]


def revoke_moderation_warning(thread_id: str, warning_id: int, moderator_uid: str) -> bool:
    with db_session() as db:
        cursor = db.execute(
            """UPDATE moderation_warnings SET active=0,removed_at=?,removed_by=?
               WHERE id=? AND thread_id=? AND active=1""",
            (time.time(), str(moderator_uid), int(warning_id), str(thread_id))
        )
        return cursor.rowcount > 0


def create_dj_playlist(thread_id: str, name: str, creator_uid: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(name or "")).strip()
    if not 2 <= len(normalized) <= 40:
        return False
    try:
        with db_session() as db:
            db.execute(
                """INSERT INTO dj_playlists(thread_id,name,name_key,created_by,created_at)
                   VALUES(?,?,?,?,?)""",
                (str(thread_id), normalized, normalized.casefold(), str(creator_uid), time.time())
            )
        return True
    except sqlite3.IntegrityError:
        return False


def add_dj_playlist_track(thread_id: str, playlist_name: str, track_name: str) -> bool:
    key = re.sub(r"\s+", " ", str(playlist_name or "")).strip().casefold()
    with db_session() as db:
        row = db.execute(
            "SELECT id FROM dj_playlists WHERE thread_id=? AND name_key=?",
            (str(thread_id), key)
        ).fetchone()
        if not row:
            return False
        db.execute(
            "INSERT INTO dj_playlist_tracks(playlist_id,title,source,created_at) VALUES(?,?,?,?)",
            (int(row["id"]), str(track_name)[:160], str(track_name), time.time())
        )
        return True


def list_dj_playlists(thread_id: str):
    with db_session() as db:
        rows = db.execute(
            """SELECT p.id,p.name,COUNT(t.id) AS track_count
               FROM dj_playlists p LEFT JOIN dj_playlist_tracks t ON t.playlist_id=p.id
               WHERE p.thread_id=? GROUP BY p.id ORDER BY lower(p.name)""",
            (str(thread_id),)
        ).fetchall()
        return [dict(row) for row in rows]


def get_dj_playlist(thread_id: str, playlist_name: str):
    key = re.sub(r"\s+", " ", str(playlist_name or "")).strip().casefold()
    with db_session() as db:
        playlist = db.execute(
            "SELECT id,name FROM dj_playlists WHERE thread_id=? AND name_key=?",
            (str(thread_id), key)
        ).fetchone()
        if not playlist:
            return None, []
        rows = db.execute(
            "SELECT title,source FROM dj_playlist_tracks WHERE playlist_id=? ORDER BY id",
            (int(playlist["id"]),)
        ).fetchall()
        return dict(playlist), [dict(row) for row in rows]


def delete_dj_playlist(thread_id: str, playlist_name: str) -> bool:
    key = re.sub(r"\s+", " ", str(playlist_name or "")).strip().casefold()
    with db_session() as db:
        row = db.execute(
            "SELECT id FROM dj_playlists WHERE thread_id=? AND name_key=?",
            (str(thread_id), key)
        ).fetchone()
        if not row:
            return False
        db.execute("DELETE FROM dj_playlist_tracks WHERE playlist_id=?", (int(row["id"]),))
        db.execute("DELETE FROM dj_playlists WHERE id=?", (int(row["id"]),))
        return True




def save_dj_favorite(thread_id: str, uid: str, track: dict) -> bool:
    """Keep favorites in memory only; they expire after 24 hours or a restart."""
    url = str(track.get("url") or "").strip()
    if not url or not dj_audio_url_allowed(url):
        return False
    key = (str(thread_id), str(uid))
    now = time.time()
    favorites = [
        item for item in DJ_FAVORITES.get(key, [])
        if float(item.get("expires", 0)) > now
    ]
    if any(item.get("url") == url for item in favorites):
        DJ_FAVORITES[key] = favorites
        return False
    next_id = max((int(item.get("id", 0)) for item in favorites), default=0) + 1
    favorite = {
        "id": next_id,
        "title": str(track.get("title") or "Pista")[:160],
        "url": url,
        "creator": str(track.get("creator") or "")[:120],
        "license": str(track.get("license") or "")[:100],
        "source": str(track.get("source") or "")[:80],
        "extension": str(track.get("extension") or ".mp3")[:8],
        "expires": now + DJ_FAVORITES_TTL,
    }
    favorites.append(favorite)
    DJ_FAVORITES[key] = favorites[-50:]
    return True


def list_dj_favorites(thread_id: str, uid: str, limit: int = 20) -> list[dict]:
    key = (str(thread_id), str(uid))
    now = time.time()
    favorites = [
        item for item in DJ_FAVORITES.get(key, [])
        if float(item.get("expires", 0)) > now
    ]
    DJ_FAVORITES[key] = favorites
    return [dict(item) for item in reversed(favorites[-max(1, min(50, int(limit))):])]


def delete_dj_favorite(thread_id: str, uid: str, favorite_id: int) -> bool:
    key = (str(thread_id), str(uid))
    favorites = DJ_FAVORITES.get(key, [])
    updated = [item for item in favorites if int(item.get("id", -1)) != int(favorite_id)]
    DJ_FAVORITES[key] = updated
    return len(updated) != len(favorites)


def anti_spam_configuration(thread_id: str) -> dict:
    def bounded(key, default, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(get_chat_setting(thread_id, key, str(default)))))
        except (TypeError, ValueError):
            return default
    return {
        "enabled": get_chat_setting(thread_id, "antispam_enabled", "1") == "1",
        "max_messages": bounded("antispam_max_messages", 7, 3, 20),
        "window_seconds": bounded("antispam_window_seconds", 8, 3, 60),
        "repeat_limit": bounded("antispam_repeat_limit", 3, 2, 10),
        "max_links": bounded("antispam_max_links", 4, 2, 12),
        "max_mentions": bounded("antispam_max_mentions", 8, 3, 20)
    }


def calculate_volume_target(values: list[str], current: int, last: int) -> int | None:
    """Interpreta controles de volumen comunes en porcentaje (0-100)."""
    if not values:
        return None
    action = str(values[0]).strip().lower()
    extra = values[1].strip() if len(values) > 1 else ""

    if action in ("subir", "up", "+"):
        amount = abs(int(extra)) if extra else 10
        target = current + amount
    elif action in ("bajar", "down", "-"):
        amount = abs(int(extra)) if extra else 10
        target = current - amount
    elif action in ("mute", "silencio", "silenciar", "mudo", "0"):
        target = 0
    elif action in ("max", "máximo", "maximo"):
        target = 100
    elif action in ("restaurar", "normal", "unmute"):
        target = last
    elif action.startswith(("+", "-")):
        target = current + int(action)
    else:
        target = int(action.replace("%", ""))

    if not 0 <= target <= 100:
        raise ValueError("El volumen debe estar entre 0 y 100.")
    return target


def update_tts_volume(thread_id: str, state: dict, target: int) -> int:
    """Actualiza y persiste el volumen TTS sin modificar el volumen musical."""
    target = max(0, min(100, int(target)))
    if target > 0:
        state["last_tts_volume"] = target / 100.0
    state["tts_volume"] = target / 100.0
    set_chat_setting(thread_id, "tts_volume", str(target))
    set_chat_setting(
        thread_id,
        "tts_last_volume",
        str(round(float(state.get("last_tts_volume", 0.75)) * 100))
    )
    return target


def check_antispam_message(event: dict, source: str):
    if source != "developer":
        return False, None
    thread_id = str(event.get("threadId") or "")
    message = event.get("message") or {}
    author = message.get("author") or {}
    uid = str(author.get("uid") or author.get("userId") or "")
    nickname = str(author.get("nickname") or author.get("name") or "Usuario")
    content = re.sub(r"\s+", " ", str(message.get("content") or "")).strip().casefold()
    identity = BOT_IDENTITIES.get(source) or {}
    if (
        not thread_id or not uid or not content or is_admin_uid(uid)
        or uid == str(identity.get("uid") or "")
        or any(
            str(author.get(key, "")).lower() in ("true", "1", "yes")
            for key in ("bot", "isBot", "is_bot")
        )
    ):
        return False, None

    config = anti_spam_configuration(thread_id)
    if not config["enabled"]:
        return False, None

    now = time.time()
    key = (thread_id, uid)
    activity = USER_MESSAGE_ACTIVITY[key]
    cutoff = now - config["window_seconds"]
    while activity and activity[0][0] < cutoff:
        activity.popleft()
    digest = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
    activity.append((now, digest))
    repeated = sum(1 for _, item_digest in activity if item_digest == digest)
    link_count = len(re.findall(r"(?:https?://|www\.)\S+", content, flags=re.I))
    mention_count = len(message.get("mentions") or [])
    reason = ""
    if link_count > config["max_links"]:
        reason = f"Exceso de enlaces en un mensaje ({link_count})."
    elif mention_count > config["max_mentions"]:
        reason = f"Menciones masivas en un mensaje ({mention_count})."
    elif len(activity) >= config["max_messages"] or repeated >= config["repeat_limit"]:
        reason = "Antispam automático: demasiados mensajes o contenido repetido."
    if not reason:
        return False, None

    warn_key = f"{thread_id}:{uid}"
    if USER_SPAM_WARNED_UNTIL.get(warn_key, 0) > now:
        return True, None
    USER_SPAM_WARNED_UNTIL[warn_key] = now + 60
    target_uid = scoped_uid(f"developer:{thread_id}", uid)
    warning_id = add_moderation_warning(
        thread_id, target_uid, nickname, "Katherine", "Katherine",
        reason,
        automatic=True
    )
    metrics["antispam_blocked"] += 1
    logging.warning(
        "[MODERACION] Antispam | sala=%s | usuario=%s | advertencia=%s",
        thread_id, nickname, warning_id
    )
    return True, {"warning_id": warning_id, "nickname": nickname}


def get_wish_state(uid: str) -> dict:
    with db_session() as db:
        row = db.execute(
            "SELECT pity5,pity4,guaranteed FROM wish_state WHERE uid=?", (uid,)
        ).fetchone()
        if row:
            return {"pity5": int(row["pity5"]), "pity4": int(row["pity4"]),
                    "guaranteed": bool(row["guaranteed"])}
        db.execute(
            "INSERT OR IGNORE INTO wish_state(uid,pity5,pity4,guaranteed,updated_at) VALUES(?,0,0,0,?)",
            (uid, time.time())
        )
        return {"pity5": 0, "pity4": 0, "guaranteed": False}


def save_wish_state(uid: str, state: dict):
    with db_session() as db:
        db.execute(
            """INSERT INTO wish_state(uid,pity5,pity4,guaranteed,updated_at)
               VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET
               pity5=excluded.pity5,pity4=excluded.pity4,
               guaranteed=excluded.guaranteed,updated_at=excluded.updated_at""",
            (uid, int(state["pity5"]), int(state["pity4"]),
             int(bool(state["guaranteed"])), time.time())
        )


def update_user_field(uid: str, field: str, value: str):
    allowed = {"bio", "status"}
    if field not in allowed:
        return
    with db_session() as db:
        db.execute(f"UPDATE users SET {field}=? WHERE uid=?", (value, uid))


def add_points(uid: str, amount: int):
    with db_session() as db:
        db.execute("UPDATE users SET points=points+? WHERE uid=?", (int(amount), uid))


def claim_reward(uid: str, action: str, cooldown: int, low: int, high: int):
    """Entrega recompensas con cooldown persistente y transacción atómica."""
    now = time.time()
    with db_session() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT claimed_at FROM reward_claims WHERE uid=? AND action=?",
            (uid, action)
        ).fetchone()
        if row:
            remaining = cooldown - (now - float(row["claimed_at"]))
            if remaining > 0:
                return None, int(remaining) + 1

        db.execute(
            """INSERT INTO reward_claims(uid,action,claimed_at)
               VALUES(?,?,?)
               ON CONFLICT(uid,action) DO UPDATE SET claimed_at=excluded.claimed_at""",
            (uid, action, now)
        )
    return random.randint(low, high), 0


def transfer_points(sender_uid: str, target_uid: str, amount: int) -> str:
    """Transfiere puntos atómicamente y nunca permite un saldo negativo."""
    if amount <= 0 or sender_uid == target_uid:
        return "invalid"
    with db_session() as db:
        db.execute("BEGIN IMMEDIATE")
        sender = db.execute(
            "SELECT points FROM users WHERE uid=?", (sender_uid,)
        ).fetchone()
        target = db.execute(
            "SELECT points FROM users WHERE uid=?", (target_uid,)
        ).fetchone()
        if not sender or not target:
            return "missing"
        if int(sender["points"]) < amount:
            return "insufficient"
        db.execute("UPDATE users SET points=points-? WHERE uid=?", (amount, sender_uid))
        db.execute("UPDATE users SET points=points+? WHERE uid=?", (amount, target_uid))
    return "ok"


def get_affinity(uid: str, target_uid: str) -> int:
    with db_session() as db:
        row = db.execute(
            "SELECT score FROM affinities WHERE uid=? AND target_uid=?",
            (uid, target_uid)
        ).fetchone()
        return int(row["score"]) if row else 0


def add_affinity(uid: str, target_uid: str, cooldown: int = 30) -> int:
    now = time.time()
    with db_session() as db:
        row = db.execute(
            "SELECT score,last_at FROM affinities WHERE uid=? AND target_uid=?",
            (uid, target_uid)
        ).fetchone()

        if row and now - float(row["last_at"]) < cooldown:
            return int(row["score"])

        score = (int(row["score"]) if row else 0) + 1
        db.execute("""INSERT INTO affinities(uid,target_uid,score,last_at) VALUES(?,?,?,?)
            ON CONFLICT(uid,target_uid) DO UPDATE SET
            score=excluded.score,last_at=excluded.last_at""",
            (uid, target_uid, score, now))
        return score


def save_gift(sender_uid, sender_name, target_uid, target_name, message):
    with db_session() as db:
        db.execute(
            """INSERT INTO gifts(
                sender_uid,sender_name,target_uid,target_name,message,created_at
            ) VALUES(?,?,?,?,?,?)""",
            (sender_uid, sender_name[:80], target_uid, target_name[:80],
             message[:120], time.time())
        )


def get_top_users(column: str, limit: int = 10, scope_key: str | None = None):
    if column not in {"points", "message_count"}:
        column = "points"
    sql = f"SELECT nickname,{column} AS value FROM users WHERE {column}>0"
    params = []
    if scope_key:
        prefix = scope_key + ":"
        sql += " AND substr(uid,1,?)=?"
        params.extend([len(prefix), prefix])
    sql += " ORDER BY value DESC LIMIT ?"
    params.append(limit)
    with db_session() as db:
        rows = db.execute(sql, tuple(params)).fetchall()
        return [(r["nickname"], int(r["value"])) for r in rows]


def get_recent_gifts(target_uid: str, limit: int = 5):
    with db_session() as db:
        rows = db.execute(
            """SELECT sender_name,message FROM gifts
               WHERE target_uid=? ORDER BY id DESC LIMIT ?""",
            (target_uid, limit)
        ).fetchall()
        return [(r["sender_name"], r["message"]) for r in rows]


# ---------------------------------------------------------------------------
# ESTADO TEMPORAL Y CACHES
# ---------------------------------------------------------------------------
seen_events = deque(maxlen=3000)
seen_event_set = set()
last_bot_send = 0.0
last_chat_send = defaultdict(float)
send_lock = asyncio.Lock()
send_history_bot = deque()
send_history_chat = defaultdict(deque)

cache: dict[str, tuple[float, Any]] = {}
polls: dict[str, dict] = {}
AUTO_REPLY_CHAT_COOLDOWN: dict[str, float] = {}
AUTO_REPLY_USER_COOLDOWN: dict[tuple[str, str], float] = {}

EXTERNAL_GLOBAL_LIMIT = asyncio.Semaphore(10)
EXTERNAL_HOST_LIMITS = {}
SOURCE_HEALTH = {}
poll_button_map: dict[str, str] = {}
BUTTON_ACTIONS: dict[str, dict] = {}
COMMUNITY_EVENTS: dict[str, dict] = {}
DJ_ROOMS: dict[str, dict] = {}
DJ_READY_EVENTS: dict[str, asyncio.Event] = {}
VOICE_CONNECT_LOCKS: dict[str, asyncio.Lock] = {}
BOT_IDENTITIES: dict[str, dict] = {}
TTS_VOICE = os.getenv("TTS_VOICE", "es-CL-CatalinaNeural").strip() or "es-CL-CatalinaNeural"
TTS_VOICE_OPTIONS = {
    "catalina": "es-CL-CatalinaNeural",
    "francisca": "es-CL-CatalinaNeural",
    "alvaro": "es-ES-AlvaroNeural",
    "elvira": "es-ES-ElviraNeural",
    "dalia": "es-MX-DaliaNeural",
    "jorge": "es-MX-JorgeNeural",
    "lucia": "es-AR-ElenaNeural",
    "carlos": "es-CO-GonzaloNeural"
}
USER_MESSAGE_ACTIVITY = defaultdict(deque)
USER_SPAM_WARNED_UNTIL = {}
DJ_MUSIC_DIR = Path(os.getenv("DJ_MUSIC_DIR", "music")).resolve()
DJ_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".oga", ".flac", ".m4a", ".aac", ".opus"}
DJ_SEARCH_RESULTS: dict[str, dict] = {}
DJ_FAVORITES: dict[tuple[str, str], list[dict]] = {}
DJ_FAVORITES_TTL = 86400

DJ_ARCHIVE_CACHE: dict[str, dict] = {}
DJ_SOURCE_CACHE: dict[str, dict] = {}
JAMENDO_CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "").strip()
IA_BASE = "https://archive.org"
JAMENDO_API = "https://api.jamendo.com/v3.0/tracks/"
WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
IA_ALLOWED_LICENSES = {
    "creativecommons.org/publicdomain/zero/1.0": "CC0 · Dominio público",
    "creativecommons.org/publicdomain/mark/1.0": "Public Domain Mark",
    "creativecommons.org/licenses/by/4.0": "CC BY 4.0 · Requiere atribución",
    "creativecommons.org/licenses/by-sa/4.0": "CC BY-SA 4.0 · Requiere atribución",
    "creativecommons.org/licenses/by/3.0": "CC BY 3.0 · Requiere atribución",
    "creativecommons.org/licenses/by-sa/3.0": "CC BY-SA 3.0 · Requiere atribución"
}

# Contadores del proceso actual. Se reinician cuando Render reinicia el servicio.
metrics = defaultdict(int)
metrics["api_latency_total_ms"] = 0.0
metrics["api_latency_last_ms"] = 0.0
metrics["api_latency_max_ms"] = 0.0
background_tasks: set[asyncio.Task] = set()
CLEANUP_TASK: asyncio.Task | None = None

trivia = {}
number_games = {}
wishes = defaultdict(lambda: {
    "pity5": 0,
    "pity4": 0,
    "guaranteed": False
})

curiosity_bag = []
last_curiosity = None


# ---------------------------------------------------------------------------
# COMANDOS SOCIALES
# ---------------------------------------------------------------------------
SOCIAL = {
    "abrazo": ["abraza con cariño a", "rodea con sus brazos a", "le da un abrazo reconfortante a"],
    "abrazar": ["abraza con cariño a", "rodea con sus brazos a", "le da un abrazo reconfortante a"],
    "beso": ["le da un beso a", "deja un beso cariñoso a", "se acerca y besa suavemente a"],
    "besar": ["le da un beso a", "deja un beso cariñoso a", "se acerca y besa suavemente a"],
    "patada": ["le da una patada ficticia a", "lanza una patada teatral hacia", "intenta sorprender con una patada a"],
    "golpe": ["golpea de broma a", "lanza un golpe ficticio hacia", "reta a un combate amistoso a"],
    "cachetada": ["le da una cachetada de broma a", "finge una cachetada dramática a", "le dedica una cachetada de telenovela a"],
    "acariciar": ["acaricia suavemente a", "le da unas caricias a", "le acomoda el cabello a"],
    "mimar": ["mima con cariño a", "consiente un momento a", "llena de atenciones a"],
    "morder": ["le da una mordida juguetona a", "finge morder a", "intenta darle un mordisquito a"],
    "consolar": ["consuela a", "acompaña con cariño a", "intenta animar a"],
    "felicitar": ["felicita con entusiasmo a", "reconoce el esfuerzo de", "celebra los logros de"],
    "saludar": ["saluda amistosamente a", "le dedica un saludo a", "levanta la mano para saludar a"],
    "proteger": ["protege a", "se coloca delante de", "promete cuidar a"],
    "bailar": ["baila con", "invita a bailar a", "improvisa unos pasos junto a"],
    "cosquillas": ["le hace cosquillas a", "intenta hacer reír con cosquillas a", "ataca con cosquillas a"],
    "chocar": ["choca los cinco con", "celebra chocando la mano con", "saluda con un high five a"],
    "highfive": ["choca los cinco con", "celebra chocando la mano con", "saluda con un high five a"],
    "ayudar": ["ayuda a", "se ofrece para ayudar a", "echa una mano a"],
    "regalar": ["le hace un regalo simbólico a", "sorprende con un obsequio a", "prepara un pequeño presente para"],
    "acompañar": ["acompaña a", "se queda un rato junto a", "decide no dejar solo a"],
    "asustar": ["intenta asustar a", "aparece de repente frente a", "prepara un susto para"],
    "curar": ["cura a", "atiende las heridas de", "prepara un remedio para"],
    "cargar": ["carga en brazos a", "ayuda a levantarse a", "lleva con cuidado a"],
    "empujar": ["empuja suavemente a", "da un empujoncito a", "reta a moverse a"],
    "regañar": ["regaña con seriedad a", "le llama la atención a", "da un pequeño sermón a"],
    "admirar": ["admira a", "observa con respeto a", "elogia sinceramente a"],
    "apoyar": ["apoya a", "anima a", "le recuerda que cuenta con el apoyo de"],
    "dormir": ["duerme junto a", "se queda descansando cerca de", "acompaña mientras descansa a"],
    "ignorar": ["ignora por completo a", "finge no escuchar a", "decide hacerse el distraído frente a"],
    "mirar": ["mira fijamente a", "observa con curiosidad a", "dirige su mirada hacia"],
    "invitar": ["invita a salir a", "propone un plan a", "extiende una invitación a"]
}

JOKES = [
    "¿Por qué Cyno nunca pierde una discusión? Porque siempre tiene una sentencia preparada.",
    "¿Qué le dijo un slime a otro? Nos vemos en la próxima reacción.",
    "¿Por qué el jugador nunca tiene Mora? Porque todo se le va en artefactos.",
    "El artefacto tenía cuatro substats buenos. Al subirlo, todos fueron a DEF%.",
    "El Abismo pidió refuerzos, pero todos estaban gastando resina."
]

TRIVIA = [
    ("¿Qué elemento utiliza Cyno?", ["Pyro", "Electro", "Dendro", "Anemo"], 1),
    ("¿Quién es la Arconte Dendro?", ["Ei", "Nahida", "Furina", "Mavuika"], 1),
    ("¿Quién dirige la Funeraria Wangsheng?", ["Ganyu", "Yelan", "Hu Tao", "Keqing"], 2),
    ("¿Cuántos corazones tiene un pulpo?", ["Uno", "Dos", "Tres", "Cuatro"], 2),
    ("¿Qué planeta se conoce como el planeta rojo?", ["Venus", "Marte", "Saturno", "Júpiter"], 1),
    ("¿Qué organización tiene once Heraldos?", ["Fatui", "Adeptus", "Caballeros", "Gremio"], 0),
    ("¿Cuál es el arma de Dehya?", ["Arco", "Catalizador", "Mandoble", "Lanza"], 2)
]

CURIOSITIES = [
    "En Genshin Impact, los nombres de los Arcontes están inspirados en demonios de la Ars Goetia.",
    "La región de Sumeru combina referencias culturales del sur de Asia y Oriente Medio.",
    "Cyno es conocido como General Mahamatra y supervisa a los investigadores de la Akademiya.",
    "En Teyvat, las Visiones permiten a ciertos humanos manipular elementos."
]


# ---------------------------------------------------------------------------
# FUNCIONES GENERALES
# ---------------------------------------------------------------------------
def token_for(source: str) -> str:
    return COMMUNITY_TOKEN if source == "community" else BOT_TOKEN


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def get_process_memory() -> tuple[float | None, float | None]:
    """RAM RSS actual y máximo del proceso, en MB, sin psutil."""
    current_mb = None
    peak_mb = None
    try:
        status_file = Path("/proc/self/status")
        if status_file.exists():
            values = {}
            for line in status_file.read_text(encoding="utf-8").splitlines():
                if line.startswith(("VmRSS:", "VmHWM:")):
                    key, raw = line.split(":", 1)
                    number = re.search(r"(\d+)", raw)
                    if number:
                        values[key] = int(number.group(1)) / 1024
            current_mb = values.get("VmRSS")
            peak_mb = values.get("VmHWM")
    except (OSError, ValueError):
        pass

    if peak_mb is None:
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux devuelve KB; macOS devuelve bytes.
            peak_mb = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
        except (ImportError, OSError, ValueError):
            pass

    return current_mb, peak_mb


def get_container_memory_usage() -> str:
    """Lee el consumo de memoria del contenedor Linux (cgroup v2 o v1)."""
    candidates = [
        ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
        ("/sys/fs/cgroup/memory/memory.usage_in_bytes",
         "/sys/fs/cgroup/memory/memory.limit_in_bytes")
    ]
    for used_path, limit_path in candidates:
        try:
            used_raw = Path(used_path).read_text().strip()
            limit_raw = Path(limit_path).read_text().strip()
            if not used_raw.isdigit():
                continue
            used_mb = int(used_raw) / 1048576
            if limit_raw.isdigit() and int(limit_raw) < 2**60:
                limit_mb = int(limit_raw) / 1048576
                return f"{used_mb:.1f} / {limit_mb:.1f} MB"
            return f"{used_mb:.1f} MB (límite no informado)"
        except (OSError, ValueError):
            continue
    return "No disponible en este entorno"


def cleanup_expired_state():
    """Elimina cachés, encuestas, partidas y métricas temporales vencidas."""
    now = time.time()
    for store in (cache,):
        for key, item in list(store.items()):
            if not isinstance(item, tuple) or len(item) < 2 or item[0] <= now:
                store.pop(key, None)
    for store in (trivia, number_games, polls):
        for key, item in list(store.items()):
            if item.get("expires", now + 1) <= now:
                store.pop(key, None)
    for key, action in list(BUTTON_ACTIONS.items()):
        if action.get("expires", now + 1) <= now:
            BUTTON_ACTIONS.pop(key, None)
    cleanup_community_events(COMMUNITY_EVENTS, now=now)
    AI_CORE.cleanup(now=now)
    active_poll_ids = set(polls)
    for button_key, poll_id in list(poll_button_map.items()):
        if poll_id not in active_poll_ids:
            poll_button_map.pop(button_key, None)
    cutoff = time.monotonic() - 600
    for key, history in list(send_history_chat.items()):
        while history and history[0] < cutoff:
            history.popleft()
        if not history:
            send_history_chat.pop(key, None)
            last_chat_send.pop(key, None)


async def cleanup_loop():
    while True:
        await asyncio.sleep(300)
        cleanup_expired_state()


def get_database_stats() -> tuple[float, int | None]:
    """Devuelve el tamaño aproximado de SQLite (incluido WAL/SHM) y usuarios."""
    total_bytes = 0
    for file_path in (
        DB_PATH,
        Path(str(DB_PATH) + "-wal"),
        Path(str(DB_PATH) + "-shm")
    ):
        try:
            if file_path.exists():
                total_bytes += file_path.stat().st_size
        except OSError:
            pass

    user_count = None
    try:
        with db_session() as db:
            row = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()
            user_count = int(row["total"])
    except (sqlite3.Error, OSError):
        pass

    return total_bytes / (1024 * 1024), user_count


async def build_status_message(source: str, nickname: str, thread_id: str | None = None) -> str:
    """Informe técnico con métricas reales del proceso y prueba de API."""
    started_check = time.perf_counter()
    result = await api("GET", "/me", source)
    api_latency_ms = (time.perf_counter() - started_check) * 1000
    api_ok = bool(result.get("ok"))
    api_status = result.get("status", "?")
    response_data = result.get("data") or {}
    if not isinstance(response_data, dict):
        response_data = {}

    thread_ids = response_data.get("threadIds") or []
    if not isinstance(thread_ids, list):
        thread_ids = []

    current_ram, peak_ram = get_process_memory()
    db_size_mb, user_count = get_database_stats()
    uptime = format_duration(time.monotonic() - STARTED_MONOTONIC)

    cpu_line = "No disponible (instala psutil)"
    system_memory_line = "No disponible"
    container_memory_line = get_container_memory_usage()
    if psutil and PROCESS:
        try:
            cpu_line = f"{PROCESS.cpu_percent(interval=None):.1f}% del proceso"
            vm = psutil.virtual_memory()
            system_memory_line = (
                f"{vm.percent:.1f}% usada "
                f"({vm.used / 1048576:.0f}/{vm.total / 1048576:.0f} MB)"
            )
        except (psutil.Error, OSError):
            pass

    ram_line = f"{current_ram:.1f} MB RSS" if current_ram is not None else "No disponible"
    if peak_ram is not None:
        ram_line += f" | pico {peak_ram:.1f} MB"

    db_line = f"{db_size_mb:.2f} MB"
    if user_count is not None:
        db_line += f" | {user_count} perfiles"

    api_line = (
        f"🟢 Conectada · {api_latency_ms:.0f} ms (HTTP {api_status})"
        if api_ok else f"🔴 Sin conexión · {api_latency_ms:.0f} ms (HTTP {api_status})"
    )
    bot_info = response_data.get("bot") or {}
    bot_name = bot_info.get("name") if isinstance(bot_info, dict) else None
    avg_api = (
        metrics["api_latency_total_ms"] / metrics["api_requests"]
        if metrics["api_requests"] else 0
    )
    last_event = (
        format_duration(time.time() - LAST_EVENT_AT) + " atrás"
        if LAST_EVENT_AT else "Sin eventos"
    )
    last_command = (
        f"{LAST_COMMAND_NAME} por {LAST_COMMAND_USER}"
        if LAST_COMMAND_NAME else "Ninguno desde el inicio"
    )

    lines = [
        "╔════ KATHERINE · ESTADO ════╗",
        f"👋 Solicitud de: {nickname}",
        f"🤖 Proceso: {'🟢 Operativo' if api_ok else '🟡 Activo, API no disponible'}",
        f"📡 Anarchy API: {api_line}",
        f"• Bot: {bot_name or 'No informado por API'}",
        f"⏱️ Uptime: {uptime}",
        f"🧠 RAM del proceso: {ram_line}",
        f"⚙️ CPU del proceso: {cpu_line}",
        f"📦 RAM del contenedor: {container_memory_line}",
        f"💻 Memoria del sistema/host: {system_memory_line}",
        "",
        "📊 ACTIVIDAD",
        f"• Webhooks aceptados: {metrics['webhook_events']}",
        f"• Mensajes recibidos: {metrics['text_messages_received']}",
        f"• Comandos recibidos: {metrics['commands_received']}",
        f"• Último comando: {last_command}",
        f"• Último evento: {last_event}",
        f"• Interacciones: {metrics['interactions_received']}",
        f"• Respuestas enviadas: {metrics['messages_sent']}",
        f"• Tareas activas: {sum(1 for task in background_tasks if not task.done())}",
        f"• Limpieza automática: {'activa' if CLEANUP_TASK is not None and not CLEANUP_TASK.done() else 'inactiva'}",
        f"• Trivias / juegos: {len(trivia)} / {len(number_games)}",
        f"• Encuestas activas: {len(polls)}",
        f"• Caché: {len(cache)} entradas",
        f"• Eventos recientes: {len(seen_events)}",
        "",
        "🌐 FUENTES EXTERNAS",
        f"• Dominios consultados: {len(SOURCE_HEALTH)}",
        f"• Fuentes con fallos registrados: {sum(1 for item in SOURCE_HEALTH.values() if item.get('failures', 0) and not item.get('last_success_at'))}",
        f"• Solicitudes externas: {sum(item.get('requests', 0) for item in SOURCE_HEALTH.values())}",
        f"• Errores externos: {sum(item.get('failures', 0) for item in SOURCE_HEALTH.values())}",
        f"• Circuit breakers activos: {sum(1 for item in SOURCE_HEALTH.values() if item.get('circuit_open_until', 0) > time.time())}",
        f"• Consultas omitidas por circuito: {metrics['external_circuit_open']}",
        "",
        "🌐 API Y RENDIMIENTO",
        f"• Solicitudes API: {metrics['api_requests']}",
        f"• Errores API: {metrics['api_errors']}",
        f"• Latencia API reciente: {metrics['api_latency_last_ms']:.0f} ms",
        f"• Latencia API promedio: {avg_api:.0f} ms",
        f"• Latencia API máxima: {metrics['api_latency_max_ms']:.0f} ms",
        "",
        "💾 DATOS",
        f"• SQLite: {db_line}",
        f"• Caché Genshin: {len(cache)} entradas",
        f"• DJ: {metrics['dj_tracks_started']} pistas iniciadas · {metrics['dj_fallback_tracks_started']} alternativas automáticas",
        f"• DJ: {metrics['dj_fallback_tracks_skipped']} pistas inválidas omitidas · {metrics['dj_playback_retries']} reintentos",
        f"• TTS: {metrics['tts_priority_enqueued']} prioritarios · {metrics['tts_regular_enqueued']} normales · {metrics['tts_duplicates_skipped']} duplicados omitidos",
        "",
        "🔐 CONFIGURACIÓN Y PROCESO",
        f"• Python: {sys.version.split()[0]} | PID: {os.getpid()}",
        f"• Cliente HTTP: {'activo' if HTTP_CLIENT is not None and not HTTP_CLIENT.is_closed else 'inactivo'}",
        f"• Directorio de datos: {'escribible' if DATA_DIR.exists() and os.access(DATA_DIR, os.W_OK) else 'no disponible'}",
        f"• Token desarrollador: {'Configurado' if BOT_TOKEN else 'No configurado'}",
        f"• Token comunidad: {'Configurado' if COMMUNITY_TOKEN else 'No configurado'}",
        f"• Chats vinculados al token: {len(thread_ids)}",
        "Los contadores corresponden a este proceso desde su último reinicio."
    ]
    audio_state = DJ_ROOMS.get(str(thread_id)) if thread_id else None
    if audio_state:
        lines.extend([
            "",
            "🎙️ DIAGNÓSTICO DE AUDIO",
            f"• LiveKit: {'conectado' if audio_state.get('room') else 'no conectado'}",
            f"• Fuente de audio: {'publicada' if audio_state.get('audio_track') else 'no publicada'}",
            f"• DJ: {'reproduciendo' if audio_state.get('task') and not audio_state['task'].done() else 'en espera'}",
            f"• Pista: {audio_state.get('filename') or 'ninguna'}",
            f"• Pausa: {'sí' if not audio_state['pause_event'].is_set() else 'no'}",
            f"• Cola musical: {len(audio_state.get('queue', []))}",
            f"• Volumen: {round(float(audio_state.get('volume', 0.75))*100)}%",
            f"• Lectura TTS: {'activa' if audio_state.get('reading_enabled') else 'inactiva'}",
            f"• Cola TTS: {audio_state['speech_queue'].qsize()}",
            f"• Volumen TTS: {round(float(audio_state.get('tts_volume', 0.75))*100)}%",
            f"• Voz TTS: {audio_state.get('tts_voice', TTS_VOICE)} ({audio_state.get('tts_rate', '+0%')})"
        ])
    else:
        lines.extend(["", "🎙️ DIAGNÓSTICO DE AUDIO", "• No hay una conexión de audio activa en esta sala."])
    lines.extend([
        f"• Errores de reproducción desde inicio: {metrics['dj_playback_errors']}",
        f"• Reintentos automáticos: {metrics['dj_playback_retries']}",
        f"• Canciones saltadas: {metrics['dj_tracks_skipped']}",
        f"• Errores de validación: {metrics['dj_validation_errors']}"
    ])
    return "\n".join(lines)



def clean_text(value: Any) -> str:
    return re.sub(r"[`*_]", "", str(value or "")).strip()


async def external_request(method: str, url: str, **kwargs):
    """HTTP compartido con concurrencia limitada, telemetría y reintentos seguros."""
    host_match = re.match(r"https?://([^/:]+)", str(url), flags=re.I)
    host = host_match.group(1).lower() if host_match else "unknown"
    host_state = SOURCE_HEALTH.setdefault(host, {
        "requests": 0, "successes": 0, "failures": 0,
        "last_status": None, "last_latency_ms": 0.0,
        "last_success_at": None, "last_error": None,
        "consecutive_failures": 0, "circuit_open_until": 0.0
    })
    if float(host_state.get("circuit_open_until", 0) or 0) > time.time():
        metrics["external_circuit_open"] += 1
        raise RuntimeError(f"Fuente {host} temporalmente aislada por fallos consecutivos.")
    host_semaphore = EXTERNAL_HOST_LIMITS.setdefault(host, asyncio.Semaphore(3))
    safe_retry = str(method).upper() in ("GET", "HEAD")
    attempts = 3 if safe_retry else 1
    last_error = None

    async with EXTERNAL_GLOBAL_LIMIT:
        async with host_semaphore:
            for attempt in range(attempts):
                started = time.perf_counter()
                try:
                    if HTTP_CLIENT is not None and not HTTP_CLIENT.is_closed:
                        response = await HTTP_CLIENT.request(method, url, **kwargs)
                    else:
                        async with httpx.AsyncClient(timeout=20) as temporary_client:
                            response = await temporary_client.request(method, url, **kwargs)

                    elapsed_ms = (time.perf_counter() - started) * 1000
                    state = SOURCE_HEALTH.setdefault(host, {
                        "requests": 0, "successes": 0, "failures": 0,
                        "last_status": None, "last_latency_ms": 0.0,
                        "last_success_at": None, "last_error": None
                    })
                    state["requests"] += 1
                    state["last_status"] = response.status_code
                    state["last_latency_ms"] = elapsed_ms

                    if response.is_success:
                        state["successes"] += 1
                        state["last_success_at"] = time.time()
                        state["last_error"] = None
                        state["consecutive_failures"] = 0
                        state["circuit_open_until"] = 0.0
                        return response

                    state["failures"] += 1
                    state["last_error"] = f"HTTP {response.status_code}"
                    if response.status_code in (429, 500, 502, 503, 504):
                        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
                        if state["consecutive_failures"] >= 6:
                            state["circuit_open_until"] = time.time() + 30
                            logging.warning("[NEXUS] Circuit breaker abierto para %s durante 30 s", host)
                    else:
                        state["consecutive_failures"] = 0

                    if (
                        safe_retry
                        and response.status_code in (429, 500, 502, 503, 504)
                        and attempt + 1 < attempts
                    ):
                        retry_after = response.headers.get("Retry-After", "")
                        try:
                            delay = min(3.0, max(0.4, float(retry_after)))
                        except (TypeError, ValueError):
                            delay = 0.45 * (attempt + 1)
                        await asyncio.sleep(delay)
                        continue

                    return response

                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                    last_error = exc
                    state = SOURCE_HEALTH.setdefault(host, {
                        "requests": 0, "successes": 0, "failures": 0,
                        "last_status": None, "last_latency_ms": 0.0,
                        "last_success_at": None, "last_error": None
                    })
                    state["requests"] += 1
                    state["failures"] += 1
                    state["last_latency_ms"] = (time.perf_counter() - started) * 1000
                    state["last_error"] = type(exc).__name__
                    state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
                    if state["consecutive_failures"] >= 6:
                        state["circuit_open_until"] = time.time() + 30
                        logging.warning("[NEXUS] Circuit breaker abierto para %s durante 30 s", host)
                    if not safe_retry or attempt + 1 >= attempts:
                        raise
                    await asyncio.sleep(0.35 * (attempt + 1))

    if last_error:
        raise last_error
    raise httpx.RequestError("Solicitud externa no completada", request=None)



async def api(method: str, path: str, source="developer", body=None):
    token = token_for(source)
    if not token:
        return {
            "ok": False,
            "status": 500,
            "data": {"error": f"Falta token {source}."}
        }

    headers = {
        "Authorization": f"Bot {token}",
        "Accept": "application/json"
    }

    try:
        metrics["api_requests"] += 1
        request_started = time.perf_counter()
        response = await external_request(
            method,
            API + path,
            headers=headers,
            json=body,
            timeout=20
        )
        elapsed_ms = (time.perf_counter() - request_started) * 1000
        metrics["api_latency_last_ms"] = elapsed_ms
        metrics["api_latency_total_ms"] += elapsed_ms
        metrics["api_latency_max_ms"] = max(
            metrics["api_latency_max_ms"], elapsed_ms
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:1000]}

        logging.info(
            "[ANARCHY] %s %s -> %s",
            method,
            path,
            response.status_code
        )
        if not response.is_success:
            metrics["api_errors"] += 1

        return {
            "ok": response.is_success,
            "status": response.status_code,
            "data": data,
            "retry_after": response.headers.get("retry-after")
        }

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - request_started) * 1000 if "request_started" in locals() else 0.0
        metrics["api_latency_last_ms"] = elapsed_ms
        metrics["api_latency_total_ms"] += elapsed_ms
        metrics["api_latency_max_ms"] = max(
            metrics["api_latency_max_ms"], elapsed_ms
        )
        metrics["api_errors"] += 1
        logging.exception("Error de conexion con Anarchy")
        return {
            "ok": False,
            "status": 500,
            "data": {"error": str(exc)}
        }


async def api_upload_bytes(path: str, source: str, content: bytes, content_type: str):
    """Envía bytes binarios a una ruta de carga de Anarchy Apps."""
    token = token_for(source)
    if not token:
        return {
            "ok": False,
            "status": 500,
            "data": {"error": f"Falta token {source}."}
        }

    headers = {
        "Authorization": f"Bot {token}",
        "Accept": "application/json",
        "Content-Type": content_type
    }

    request_started = time.perf_counter()
    try:
        metrics["api_requests"] += 1
        response = await external_request(
            "PUT",
            API + path,
            headers=headers,
            content=content,
            timeout=20
        )
        elapsed_ms = (time.perf_counter() - request_started) * 1000
        metrics["api_latency_last_ms"] = elapsed_ms
        metrics["api_latency_total_ms"] += elapsed_ms
        metrics["api_latency_max_ms"] = max(
            metrics["api_latency_max_ms"], elapsed_ms
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:1000]}

        logging.info(
            "[ANARCHY] PUT %s -> %s",
            path,
            response.status_code
        )
        if not response.is_success:
            metrics["api_errors"] += 1

        return {
            "ok": response.is_success,
            "status": response.status_code,
            "data": data,
            "retry_after": response.headers.get("retry-after")
        }

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - request_started) * 1000
        metrics["api_latency_last_ms"] = elapsed_ms
        metrics["api_latency_total_ms"] += elapsed_ms
        metrics["api_latency_max_ms"] = max(
            metrics["api_latency_max_ms"], elapsed_ms
        )
        metrics["api_errors"] += 1
        logging.exception("Error al subir el icono de Anarchy")
        return {
            "ok": False,
            "status": 500,
            "data": {"error": str(exc)}
        }


def split_message_content(content: str, limit: int = 2000) -> list[str]:
    """Divide mensajes en bloques de hasta 'limit' caracteres."""
    text = clean_text(content)
    if not text:
        return []

    chunks = []
    while len(text) > limit:
        newline_at = text.rfind("\n", 0, limit + 1)
        paragraph_at = text.rfind("\n\n", 0, limit + 1)
        sentence_ends = [
            match.end()
            for match in re.finditer(r"[.!?…](?:[\"')\]]?)(?:\s+|$)", text[:limit + 1])
            if limit * 0.65 <= match.end() <= limit
        ]
        sentence_at = sentence_ends[-1] if sentence_ends else -1
        space_at = text.rfind(" ", 0, limit + 1)
        cut = max(paragraph_at, newline_at, sentence_at, space_at)

        # Evita crear un bloque demasiado corto por un salto temprano.
        if cut < limit // 2:
            cut = limit

        chunk = text[:cut].rstrip()
        if not chunk:
            cut = limit
            chunk = text[:cut]

        chunks.append(chunk)
        text = text[cut:].lstrip()

    if text:
        chunks.append(text)

    return chunks


# ---------------------------------------------------------------------------
# SALIDA SEGURA DE CHAT · SUPRESIÓN GLOBAL DE ENLACES
# ---------------------------------------------------------------------------
CHAT_URL_TOKEN_RE = re.compile(
    r"""(?ix)
    (?:https?|ftp)://[^\s<>{}\[\]\"']+
    |(?<![\w@])www\.[^\s<>{}\[\]\"']+
    """
)
CHAT_BARE_DOMAIN_RE = re.compile(
    r"""(?ix)
    (?<![\w@])
    (?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+
    [a-z]{2,24}
    (?::\d{2,5})?
    (?:/[^\s<>{}\[\]\"']*)?
    """
)
CHAT_NON_DOMAIN_SUFFIXES = {
    "py", "js", "ts", "tsx", "jsx", "json", "yaml", "yml", "toml",
    "md", "txt", "csv", "xml", "html", "css", "sql", "sh", "bat",
    "exe", "dll", "so", "zip", "rar", "mp3", "wav", "ogg", "flac",
    "m4a", "aac", "opus", "png", "jpg", "jpeg", "webp", "gif", "svg",
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx"
}


def sanitize_chat_text(content: Any) -> str:
    """Remove clickable links from chat output while keeping their readable labels.

    This is an output-only filter: API endpoints, source URLs, downloads and
    internal navigation continue to work inside the bot.
    """
    text = str(content or "")

    # Keep the human-readable label of Markdown links and images.
    text = re.sub(
        r"(?is)!?\[([^\]\n]*)\]\(\s*(?:<[^>]*>|[^)]*)\s*\)",
        r"\1",
        text,
    )

    # Keep anchor text, but discard the HTML link wrapper and destination.
    text = re.sub(r"(?is)<a\b[^>]*>(.*?)</a>", r"\1", text)

    # Remove Markdown reference definitions that would otherwise leave links.
    text = re.sub(
        r"(?im)^\s*\[[^]\n]+\]:\s*(?:https?://|www\.)\S+.*$",
        "",
        text,
    )

    def strip_url_token(match):
        token = match.group(0)
        trailing = ""
        while token and token[-1] in ".,;:!?":
            trailing = token[-1] + trailing
            token = token[:-1]
        while token.endswith((")", "]", "}")):
            closing = token[-1]
            opening = {")": "(", "]": "[", "}": "{"}[closing]
            if token.count(closing) > token.count(opening):
                trailing = closing + trailing
                token = token[:-1]
            else:
                break
        return trailing

    # Remove explicit schemes and www links first.
    text = CHAT_URL_TOKEN_RE.sub(strip_url_token, text)

    # Also catch naked domains, invite links and domains with a path.
    def strip_bare_domain(match):
        token = match.group(0)
        suffix = token.rsplit(".", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
        if suffix in CHAT_NON_DOMAIN_SUFFIXES:
            return token
        return strip_url_token(match)

    text = CHAT_BARE_DOMAIN_RE.sub(strip_bare_domain, text)

    # Remove link-only wrappers left after a URL was stripped.
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:])", r"\1", text)
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return text.strip()


async def send_message(
    thread_id: str,
    content: str,
    source="developer",
    reply_id=None,
    buttons=None
):
    global last_bot_send

    if not thread_id:
        return None

    content = sanitize_chat_text(content)
    parts = split_message_content(content, 2000)
    if not parts:
        return None

    if buttons:
        buttons = [
            {
                "id": str(b["id"])[:32],
                "label": (sanitize_chat_text(b.get("label") or "Opción") or "Opción")[:40]
            }
            for b in buttons[:5]
            if isinstance(b, dict) and b.get("id") is not None
        ]

    last_result = None

    # El bloqueo cubre toda la secuencia para respetar el límite global
    # y evitar que dos respuestas se mezclen entre sí.
    async with send_lock:
        for index, part in enumerate(parts):
            now = time.monotonic()

            wait = max(
                0,
                1.05 - (now - last_bot_send),
                2.05 - (now - last_chat_send[f"{source}:{thread_id}"])
            )

            while send_history_bot and send_history_bot[0] < now - 3600:
                send_history_bot.popleft()

            history = send_history_chat[f"{source}:{thread_id}"]
            while history and history[0] < now - 600:
                history.popleft()

            if len(send_history_bot) >= 300:
                wait = max(wait, send_history_bot[0] + 3600 - now)

            if len(history) >= 60:
                wait = max(wait, history[0] + 600 - now)

            if wait > 0:
                await asyncio.sleep(wait)

            body = {"content": part}

            # La respuesta al mensaje original se aplica solo al primer bloque.
            if reply_id and index == 0:
                body["replyMessageId"] = str(reply_id)

            # Los botones van al último bloque para que aparezcan debajo del texto.
            if buttons and index == len(parts) - 1:
                body["buttons"] = buttons

            result = await api(
                "POST",
                f"/threads/{thread_id}/messages",
                source,
                body
            )

            if result.get("status") == 429:
                error_data = result.get("data") or {}
                if not isinstance(error_data, dict):
                    error_data = {}

                retry = (
                    result.get("retry_after")
                    or error_data.get("retryAfter")
                    or 2
                )

                try:
                    await asyncio.sleep(min(float(retry), 60))
                except (ValueError, TypeError):
                    await asyncio.sleep(2)

                result = await api(
                    "POST",
                    f"/threads/{thread_id}/messages",
                    source,
                    body
                )

            last_result = result

            if result.get("ok"):
                metrics["messages_sent"] += 1
                now = time.monotonic()
                last_bot_send = now
                last_chat_send[f"{source}:{thread_id}"] = now
                send_history_bot.append(now)
                send_history_chat[f"{source}:{thread_id}"].append(now)
            else:
                logging.error(
                    "No se pudo enviar el bloque %s/%s: %s",
                    index + 1,
                    len(parts),
                    result
                )
                return result

    return last_result


# ---------------------------------------------------------------------------
# PROGRESO EDITABLE · NEXUS
# ---------------------------------------------------------------------------
async def start_progress_message(
    thread_id: str, content: str, source: str = "developer", reply_id=None
) -> dict:
    """Publica el primer estado y conserva el ID si la API lo devuelve."""
    result = await send_message(thread_id, content, source, reply_id)
    message_id = extract_message_id((result or {}).get("data"))
    return {
        "thread_id": str(thread_id),
        "source": source,
        "message_id": message_id,
        "created_at": time.time(),
        "enabled": bool(message_id),
    }


async def update_progress_message(context: dict | None, content: str) -> bool:
    """Edita el mensaje solo dentro de la ventana de 15 minutos documentada."""
    if not isinstance(context, dict) or not context.get("enabled"):
        return False
    if time.time() - float(context.get("created_at", 0)) >= 14 * 60:
        context["enabled"] = False
        return False
    result = await api(
        "PATCH",
        f"/threads/{context['thread_id']}/messages/{context['message_id']}",
        context.get("source", "developer"),
        {"content": sanitize_chat_text(content)[:2000]},
    )
    if result.get("ok"):
        metrics["progress_edits"] += 1
        return True
    context["enabled"] = False
    metrics["progress_edit_failures"] += 1
    logging.warning(
        "[NEXUS] No pude editar el mensaje de progreso (HTTP %s).",
        result.get("status")
    )
    return False


async def finish_progress_message(
    context: dict | None,
    content: str,
    thread_id: str,
    source: str,
    reply_id=None,
):
    if await update_progress_message(context, content):
        return {"ok": True, "edited": True}
    return await send_message(thread_id, content, source, reply_id)


async def send_nexus_center(
    thread_id: str, source: str, raw_uid: str, nickname: str, reply_id=None
):
    choices = [
        {"label": "Ayuda", "value": "ayuda"},
        {"label": "Menú", "value": "menu"},
        {"label": "Estado", "value": "estado"},
        {"label": "Fuentes", "value": "fuentes"},
        {"label": "Cola DJ", "value": "cola"},
    ]
    owner_uid = scoped_uid(f"{source}:{thread_id}", str(raw_uid))
    buttons = register_buttons(
        source, thread_id, "run_command", choices,
        {"raw_uid": str(raw_uid), "nickname": nickname},
        owner_uid=owner_uid, ttl=1800
    )
    await send_message(
        thread_id,
        "KATHERINE · CENTRO DE CONTROL\n"
        "Accesos directos a las funciones principales. "
        "Los controles ejecutan los comandos existentes con sus permisos habituales.",
        source, reply_id, buttons
    )


async def community_event_message(thread_id: str, event: dict) -> str:
    participants = event.get("participants") or {}
    remaining = max(0, int(float(event.get("expires_at", 0)) - time.time()))
    return (
        "EVENTO DE COMUNIDAD · KATHERINE\n"
        f"{event.get('title', 'Actividad')}\n\n"
        f"Participantes: {len(participants)}/{int(event.get('capacity', 40))}\n"
        f"Vigencia restante: {format_duration(remaining)}\n"
        "Usa los botones para unirte o retirarte. La participación es voluntaria."
    )


async def handle_community_event_command(
    thread_id: str, source: str, raw_uid: str, nickname: str,
    args: list[str], reply_id=None, admin_uid: str | None = None
):
    sub = args[0].casefold() if args else "estado"
    current = COMMUNITY_EVENTS.get(str(thread_id))
    if current and (
        current.get("closed") or float(current.get("expires_at", 0)) <= time.time()
    ):
        COMMUNITY_EVENTS.pop(str(thread_id), None)
        current = None

    if sub in ("crear", "crear", "convocar", "iniciar"):
        title = " ".join(args[1:]).strip()
        if not 4 <= len(title) <= 100:
            await send_message(
                thread_id,
                "Uso: !evento crear <título de 4 a 100 caracteres>. "
                "Los eventos duran una hora y admiten hasta 40 participantes.",
                source, reply_id
            )
            return
        if current:
            await send_message(
                thread_id,
                "Ya hay un evento activo en esta sala. Usa !evento estado o ciérralo antes.",
                source, reply_id
            )
            return
        event_id = secrets.token_hex(6)
        current = {
            "id": event_id,
            "title": title,
            "owner_uid": str(raw_uid),
            "created_at": time.time(),
            "expires_at": time.time() + 3600,
            "capacity": 40,
            "participants": {str(raw_uid): str(nickname)[:80]},
            "closed": False,
        }
        COMMUNITY_EVENTS[str(thread_id)] = current
        buttons = register_buttons(
            source, thread_id, "community_event",
            [
                {"label": "Unirme", "value": "join"},
                {"label": "Retirarme", "value": "leave"},
            ],
            {"event_id": event_id}, ttl=3600
        )
        await send_message(
            thread_id, await community_event_message(thread_id, current),
            source, reply_id, buttons
        )
        metrics["community_events_created"] += 1
        return

    if sub in ("cerrar", "terminar", "finalizar"):
        if not current:
            await send_message(thread_id, "No hay un evento activo en esta sala.", source, reply_id)
            return
        if str(raw_uid) != str(current.get("owner_uid")) and not is_admin_uid(admin_uid):
            await send_message(thread_id, "Solo quien creó el evento o un administrador puede cerrarlo.", source, reply_id)
            return
        current["closed"] = True
        count = len(current.get("participants") or {})
        for key, action in list(BUTTON_ACTIONS.items()):
            payload = action.get("payload") or {}
            if action.get("type") == "community_event" and payload.get("event_id") == current.get("id"):
                BUTTON_ACTIONS.pop(key, None)
        COMMUNITY_EVENTS.pop(str(thread_id), None)
        metrics["community_events_closed"] += 1
        await send_message(
            thread_id, f"EVENTO CERRADO · {current.get('title')}\nParticipantes que se habían unido: {count}.",
            source, reply_id
        )
        return

    if not current:
        await send_message(
            thread_id,
            "No hay un evento activo. Crea uno con !evento crear <título>.",
            source, reply_id
        )
        return
    await send_message(thread_id, await community_event_message(thread_id, current), source, reply_id)


# ---------------------------------------------------------------------------
# MENÚS INTERACTIVOS, BOTONES Y REPRODUCTOR DJ
# ---------------------------------------------------------------------------
def register_buttons(
    source: str,
    thread_id: str,
    action_type: str,
    choices: list[dict],
    payload: dict | None = None,
    owner_uid: str | None = None,
    ttl: int = 900
) -> list[dict]:
    """Registra acciones efímeras y genera IDs admitidos por Anarchy Apps."""
    now = time.time()
    for key, action in list(BUTTON_ACTIONS.items()):
        if action.get("expires", now + 1) <= now:
            BUTTON_ACTIONS.pop(key, None)

    buttons = []
    nonce = secrets.token_hex(5)
    base_payload = dict(payload or {})
    for index, option in enumerate(choices[:5]):
        button_id = f"k{nonce}_{index}"
        BUTTON_ACTIONS[button_id] = {
            "type": action_type,
            "source": source,
            "thread": str(thread_id),
            "owner_uid": owner_uid,
            "payload": {**base_payload, "value": option.get("value")},
            "created_at": now,
            "expires": now + ttl
        }
        buttons.append({
            "id": button_id,
            "label": str(option.get("label") or "Elegir")[:40]
        })

    while len(BUTTON_ACTIONS) > 2000:
        oldest = min(
            BUTTON_ACTIONS,
            key=lambda key: BUTTON_ACTIONS[key].get("created_at", 0)
        )
        BUTTON_ACTIONS.pop(oldest, None)
    return buttons


async def remove_pressed_buttons(event: dict, source: str, remove=True):
    thread_id = str(event.get("threadId") or "")
    message_id = str(event.get("messageId") or "")
    if not thread_id or not message_id:
        return
    button_id = str(event.get("buttonId") or "")
    body = {"removeButtons": True if remove is True else [button_id]}
    await api(
        "PATCH",
        f"/threads/{thread_id}/messages/{message_id}",
        source,
        body
    )


MENU_ROOT_CHOICES = [
    {"label": "Genshin", "value": "genshin"},
    {"label": "Social", "value": "social"},
    {"label": "Juegos", "value": "games"},
    {"label": "Comunidad", "value": "community"},
    {"label": "Sistema", "value": "system"}
]

MENU_SECTIONS = {
    "genshin": {
        "title": "GENSHIN IMPACT",
        "description": (
            "Consultas rápidas disponibles. Para fichas y builds completas, "
            "indica el nombre del personaje u objeto en el comando."
        ),
        "commands": [("Banners", "banner"), ("Calendario", "calendario")]
    },
    "social": {
        "title": "SOCIAL Y PERFILES",
        "description": (
            "Usa !interaccion @usuario para abrir acciones con botones. "
            "También puedes consultar perfiles, afinidad y actividad."
        ),
        "commands": [("Mi perfil", "perfil"), ("Ranking", "ranking"), ("Actividad", "actividad")]
    },
    "games": {
        "title": "JUEGOS",
        "description": "Elige un juego. Las trivias y encuestas admiten botones.",
        "commands": [
            ("Trivia", "trivia"),
            ("Adivina", "adivina"),
            ("Dado", "dado"),
            ("Verdad", "verdad"),
            ("Moneda", "moneda")
        ]
    },
    "community": {
        "title": "COMUNIDAD",
        "description": (
            "Consulta salas, reclama recompensas y revisa tus puntos. "
            "El sistema económico es ficticio y solo funciona dentro de Katherine."
        ),
        "commands": [
            ("Salas en vivo", "salas"),
            ("Recompensa diaria", "diario"),
            ("Trabajar", "trabajar"),
            ("Mis puntos", "puntos")
        ]
    },
    "system": {
        "title": "SISTEMA KATHERINE",
        "description": "Diagnóstico y utilidades del bot.",
        "commands": [
            ("Estado", "estado"),
            ("Fecha de Chile", "fecha"),
            ("Autotest", "autotest")
        ]
    }
}


async def send_interactive_menu(
    thread_id: str,
    source: str,
    raw_uid: str,
    nickname: str,
    category: str | None = None,
    reply_id: str | None = None
):
    owner_uid = scoped_uid(f"{source}:{thread_id}", str(raw_uid))
    if category is None:
        buttons = register_buttons(
            source,
            thread_id,
            "menu_category",
            MENU_ROOT_CHOICES,
            {"raw_uid": str(raw_uid), "nickname": nickname},
            owner_uid=owner_uid,
            ttl=1800
        )
        await send_message(
            thread_id,
            "KATHERINE · MENÚ INTERACTIVO\nSelecciona una categoría:",
            source,
            reply_id,
            buttons
        )
        return

    section = MENU_SECTIONS.get(category)
    if not section:
        return

    commands = [
        {"label": label, "value": command}
        for label, command in section["commands"]
    ]
    buttons = register_buttons(
        source,
        thread_id,
        "run_command",
        commands,
        {"raw_uid": str(raw_uid), "nickname": nickname},
        owner_uid=owner_uid,
        ttl=1800
    )
    content = (
        f"KATHERINE · {section['title']}\n"
        f"{section['description']}\n\n"
        + "\n".join(f"• {PREFIX}{command}" for _, command in section["commands"])
    )
    if category == "genshin":
        content += (
            f"\nPara consultar un personaje: {PREFIX}guia nombre. "
            f"También: {PREFIX}equipos nombre y {PREFIX}rotacion nombre."
        )
    elif category == "social":
        content += (
            f"\nInteracciones: {PREFIX}interaccion @usuario. "
            f"Regalos: {PREFIX}regalo @usuario mensaje."
        )
    await send_message(thread_id, content, source, reply_id, buttons)


def list_dj_tracks() -> list[str]:
    DJ_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return sorted(
            path.name for path in DJ_MUSIC_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in DJ_AUDIO_EXTENSIONS
        )
    except OSError:
        return []


def resolve_dj_track(name: str) -> Path | None:
    if not name or Path(name).name != name:
        return None
    path = (DJ_MUSIC_DIR / name).resolve()
    if path.parent != DJ_MUSIC_DIR.resolve():
        return None
    if path.suffix.lower() not in DJ_AUDIO_EXTENSIONS:
        return None
    try:
        if not path.is_file() or path.stat().st_size > 80 * 1024 * 1024:
            return None
    except OSError:
        return None
    return path


def clean_archive_value(value: Any, limit: int = 120) -> str:
    if isinstance(value, list):
        value = ", ".join(str(part) for part in value[:4])
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]*>", "", value)
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def archive_license_label(license_url: str) -> str | None:
    normalized = str(license_url or "").strip().lower().rstrip("/")
    for marker, label in IA_ALLOWED_LICENSES.items():
        if marker in normalized:
            return label
    return None


def archive_audio_download_url(identifier: str, filename: str, payload: dict) -> str:
    """Build a direct Internet Archive storage URL, avoiding HTML item pages."""
    server = str(payload.get("d1") or "").strip().lower()
    directory = str(payload.get("dir") or "").strip()
    safe_directory_parts = [
        part for part in directory.strip("/").split("/")
        if part and part not in (".", "..")
    ]
    if (
        server
        and re.fullmatch(r"[a-z0-9.-]+", server)
        and dj_audio_host_allowed(server)
        and directory.startswith("/")
        and safe_directory_parts
        and len(safe_directory_parts) == len(
            [part for part in directory.strip("/").split("/") if part]
        )
    ):
        encoded_directory = "/".join(
            quote(part, safe="") for part in safe_directory_parts
        )
        return (
            f"https://{server}/{encoded_directory}/"
            f"{quote(filename, safe='')}"
        )

    # Compatibility fallback for metadata records without storage coordinates.
    return (
        f"{IA_BASE}/download/{quote(identifier, safe='')}/"
        f"{quote(filename, safe='')}"
    )


async def archive_item_details(identifier: str) -> dict | None:
    """Obtiene metadatos públicos y valida licencia y formato antes de ofrecer audio."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", str(identifier or "")):
        return None

    cached = DJ_ARCHIVE_CACHE.get(identifier)
    if cached and cached.get("expires", 0) > time.time():
        return cached.get("track")

    response = await external_request(
        "GET",
        f"{IA_BASE}/metadata/{quote(identifier, safe='')}",
        headers={"Accept": "application/json", "User-Agent": "Katherine-AnarchyBot/3.1 (GPT-6; https://github.com/kaaazoficial/katherine-gi-es-anarchy)"},
        timeout=25
    )
    if not response.is_success:
        return None

    try:
        payload = response.json()
    except Exception:
        return None

    metadata = payload.get("metadata") or {}
    license_url = metadata.get("licenseurl") or metadata.get("license_url") or ""
    license_label = archive_license_label(str(license_url))
    if not license_label:
        # No se reproduce contenido sin una licencia inequívoca en sus metadatos.
        return None

    allowed_files = []
    for audio_file in payload.get("files") or []:
        if not isinstance(audio_file, dict):
            continue
        filename = str(audio_file.get("name") or "")
        extension = Path(filename).suffix.lower()
        if extension not in {".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus"}:
            continue
        if "/" in filename or "\\" in filename or filename.startswith("."):
            continue
        try:
            file_size = int(audio_file.get("size") or 0)
        except (TypeError, ValueError):
            file_size = 0
        if file_size and file_size > 150 * 1024 * 1024:
            continue
        if audio_file.get("private") == "true":
            continue
        allowed_files.append((filename, file_size))

    if not allowed_files:
        return None

    extension_priority = {
        ".mp3": 0, ".ogg": 1, ".m4a": 2,
        ".opus": 3, ".wav": 4, ".flac": 5
    }
    allowed_files.sort(
        key=lambda pair: (
            extension_priority.get(Path(pair[0]).suffix.lower(), 99),
            pair[1] if pair[1] else 10**15
        )
    )
    filename = allowed_files[0][0]
    title = clean_archive_value(metadata.get("title") or identifier)
    creator = clean_archive_value(
        metadata.get("creator") or metadata.get("artist") or "Autor no indicado"
    )
    year = clean_archive_value(metadata.get("year") or "", 12)
    item_url = f"{IA_BASE}/details/{quote(identifier, safe='')}"
    audio_url = archive_audio_download_url(identifier, filename, payload)
    track = {
        "identifier": identifier,
        "title": title or identifier,
        "creator": creator,
        "year": year,
        "license": license_label,
        "license_url": str(license_url),
        "item_url": item_url,
        "url": audio_url,
        "filename": filename,
        "extension": Path(filename).suffix.lower(),
        "source": "Internet Archive",
        "source_key": "archive"
    }
    DJ_ARCHIVE_CACHE[identifier] = {
        "track": track,
        "expires": time.time() + 21600
    }
    if len(DJ_ARCHIVE_CACHE) > 500:
        oldest = min(
            DJ_ARCHIVE_CACHE,
            key=lambda key: DJ_ARCHIVE_CACHE[key].get("expires", 0)
        )
        DJ_ARCHIVE_CACHE.pop(oldest, None)
    return track


async def search_archive_audio(query: str, limit: int = 5) -> list[dict]:
    """Busca audio en Internet Archive y solo devuelve pistas con licencias permitidas."""
    query = clean_archive_value(query, 80)
    terms = re.findall(r"[\wáéíóúñü-]+", query, flags=re.I)[:5]
    if not terms:
        return []

    normalized_query = " ".join(terms).casefold()
    cached = DJ_ARCHIVE_CACHE.get("search:" + normalized_query)
    if cached and cached.get("expires", 0) > time.time():
        return cached.get("tracks", [])[:limit]

    phrase = " ".join(terms)
    escaped_phrase = phrase.replace('"', "")
    lucene_query = (
        'mediatype:audio AND ('
        f'title:"{escaped_phrase}" OR '
        f'subject:"{escaped_phrase}" OR '
        f'description:"{escaped_phrase}")'
    )
    params = [
        ("q", lucene_query),
        ("fl[]", "identifier"),
        ("fl[]", "title"),
        ("fl[]", "creator"),
        ("rows", "35"),
        ("page", "1"),
        ("output", "json"),
        ("sort[]", "downloads desc")
    ]
    response = await external_request(
        "GET",
        f"{IA_BASE}/advancedsearch.php",
        params=params,
        headers={"Accept": "application/json", "User-Agent": "KatherineBot/1.0"},
        timeout=30
    )
    if not response.is_success:
        raise RuntimeError(f"Internet Archive respondió HTTP {response.status_code}.")

    try:
        documents = (
            response.json().get("response", {}).get("docs", [])
        )
    except Exception as exc:
        raise RuntimeError("No pude interpretar los resultados de Internet Archive.") from exc

    identifiers = []
    for document in documents:
        identifier = str(document.get("identifier") or "")
        if identifier and identifier not in identifiers:
            identifiers.append(identifier)

    semaphore = asyncio.Semaphore(3)

    async def get_checked(identifier: str):
        async with semaphore:
            try:
                return await archive_item_details(identifier)
            except Exception:
                logging.exception("[DJ] No se pudo validar el item IA %s", identifier)
                return None

    checked = await asyncio.gather(*(get_checked(identifier) for identifier in identifiers))
    tracks = [track for track in checked if track]
    DJ_ARCHIVE_CACHE["search:" + normalized_query] = {
        "tracks": tracks,
        "expires": time.time() + 900
    }
    if len(DJ_ARCHIVE_CACHE) > 500:
        oldest = min(
            DJ_ARCHIVE_CACHE,
            key=lambda key: DJ_ARCHIVE_CACHE[key].get("expires", 0)
        )
        DJ_ARCHIVE_CACHE.pop(oldest, None)
    return tracks[:limit]


def dj_audio_host_allowed(host: str) -> bool:
    """Allow only the official hosts used by the validated music catalogs."""
    normalized = str(host or "").lower().rstrip(".")
    return (
        normalized == "archive.org"
        or normalized.endswith(".archive.org")
        or normalized == "upload.wikimedia.org"
        or normalized == "jamendo.com"
        or normalized.endswith(".jamendo.com")
    )


def dj_audio_url_allowed(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return parsed.scheme == "https" and dj_audio_host_allowed(parsed.hostname or "")


async def search_wikimedia_audio(query: str, limit: int = 5) -> list[dict]:
    """Search Wikimedia Commons and accept only audio files with compatible licenses."""
    query = clean_archive_value(query, 80)
    if not query:
        return []
    cache_key = "commons:" + query.casefold()
    cached = DJ_SOURCE_CACHE.get(cache_key)
    if cached and cached.get("expires", 0) > time.time():
        return cached.get("tracks", [])[:limit]

    response = await external_request(
        "GET",
        WIKIMEDIA_API,
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"{query} filetype:audio",
            "gsrnamespace": 6,
            "gsrlimit": max(10, min(30, limit * 4)),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|mediatype|extmetadata",
            "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|Credit|ObjectName",
        },
        headers={
            "Accept": "application/json",
            "User-Agent": "Katherine-AnarchyBot/3.2 (music catalog; https://github.com/kaaazoficial/katherine-gi-es-anarchy)",
        },
        timeout=25,
    )
    if not response.is_success:
        raise RuntimeError(f"Wikimedia Commons respondió HTTP {response.status_code}.")

    try:
        pages = (response.json().get("query") or {}).get("pages") or {}
    except Exception as exc:
        raise RuntimeError("No pude interpretar los resultados de Wikimedia Commons.") from exc

    tracks = []
    seen_urls = set()
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        title = str(page.get("title") or "")
        info_list = page.get("imageinfo") or []
        if not info_list or not isinstance(info_list[0], dict):
            continue
        info = info_list[0]
        extension = Path(title).suffix.lower()
        if extension not in DJ_AUDIO_EXTENSIONS:
            continue
        if str(info.get("mediatype") or "").upper() not in ("AUDIO", ""):
            continue
        if not str(info.get("mime") or "").lower().startswith("audio/"):
            continue
        try:
            size = int(info.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size > 150 * 1024 * 1024:
            continue

        metadata = info.get("extmetadata") or {}
        license_url = str((metadata.get("LicenseUrl") or {}).get("value") or "")
        license_label = archive_license_label(license_url)
        audio_url = str(info.get("url") or "")
        if (
            not license_label
            or not dj_audio_url_allowed(audio_url)
            or audio_url in seen_urls
        ):
            continue

        artist = clean_archive_value(
            (metadata.get("Artist") or {}).get("value") or
            (metadata.get("Credit") or {}).get("value") or
            "Autor no indicado"
        )
        object_name = clean_archive_value(
            (metadata.get("ObjectName") or {}).get("value") or
            title.removeprefix("File:")
        )
        tracks.append({
            "identifier": str(page.get("pageid") or title),
            "title": object_name or title.removeprefix("File:"),
            "creator": artist,
            "license": license_label,
            "license_url": license_url,
            "item_url": str(info.get("descriptionurl") or
                            "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"))),
            "url": audio_url,
            "extension": extension,
            "source": "Wikimedia Commons",
            "source_key": "commons",
        })
        seen_urls.add(audio_url)
        if len(tracks) >= limit:
            break

    DJ_SOURCE_CACHE[cache_key] = {
        "tracks": tracks,
        "expires": time.time() + 900,
    }
    if len(DJ_SOURCE_CACHE) > 300:
        oldest = min(DJ_SOURCE_CACHE, key=lambda key: DJ_SOURCE_CACHE[key].get("expires", 0))
        DJ_SOURCE_CACHE.pop(oldest, None)
    return tracks[:limit]


async def search_jamendo_audio(query: str, limit: int = 5) -> list[dict]:
    """Search Jamendo; download only tracks that artists explicitly allow to download."""
    if not JAMENDO_CLIENT_ID:
        return []
    query = clean_archive_value(query, 80)
    if not query:
        return []
    cache_key = "jamendo:" + query.casefold()
    cached = DJ_SOURCE_CACHE.get(cache_key)
    if cached and cached.get("expires", 0) > time.time():
        return cached.get("tracks", [])[:limit]

    response = await external_request(
        "GET",
        JAMENDO_API,
        params={
            "client_id": JAMENDO_CLIENT_ID,
            "format": "json",
            "limit": max(10, min(30, limit * 4)),
            "search": query,
            "include": "licenses",
            "audioformat": "mp32",
            "audiodlformat": "mp32",
        },
        headers={
            "Accept": "application/json",
            "User-Agent": "Katherine-AnarchyBot/3.2 (music catalog; https://github.com/kaaazoficial/katherine-gi-es-anarchy)",
        },
        timeout=25,
    )
    if not response.is_success:
        raise RuntimeError(f"Jamendo respondió HTTP {response.status_code}.")

    try:
        payload = response.json()
        tracks_data = payload.get("results") or []
        api_status = (payload.get("headers") or {}).get("status")
        if api_status and str(api_status).lower() != "success":
            raise RuntimeError("Jamendo no aceptó la búsqueda.")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("No pude interpretar los resultados de Jamendo.") from exc

    tracks = []
    for item in tracks_data:
        if not isinstance(item, dict):
            continue
        allowed = item.get("audiodownload_allowed")
        if allowed is not True and str(allowed).lower() != "true":
            continue
        license_url = str(item.get("license_ccurl") or "")
        license_label = archive_license_label(license_url)
        download_url = str(item.get("audiodownload") or "")
        if not license_label or not dj_audio_url_allowed(download_url):
            continue
        track_id = str(item.get("id") or "")
        if not track_id:
            continue
        tracks.append({
            "identifier": track_id,
            "title": clean_archive_value(item.get("name") or "Pista de Jamendo", 160),
            "creator": clean_archive_value(item.get("artist_name") or "Autor no indicado"),
            "year": clean_archive_value(item.get("releasedate") or "", 12),
            "license": license_label,
            "license_url": license_url,
            "item_url": f"https://www.jamendo.com/track/{quote(track_id, safe='')}",
            "url": download_url,
            "extension": ".mp3",
            "source": "Jamendo",
            "source_key": "jamendo",
        })
        if len(tracks) >= limit:
            break

    DJ_SOURCE_CACHE[cache_key] = {
        "tracks": tracks,
        "expires": time.time() + 900,
    }
    if len(DJ_SOURCE_CACHE) > 300:
        oldest = min(DJ_SOURCE_CACHE, key=lambda key: DJ_SOURCE_CACHE[key].get("expires", 0))
        DJ_SOURCE_CACHE.pop(oldest, None)
    return tracks[:limit]


async def search_music_catalog(query: str, limit: int = 5) -> list[dict]:
    """Search every configured catalog concurrently and fairly mix their results."""
    providers = [
        ("archive", search_archive_audio),
        ("commons", search_wikimedia_audio),
    ]
    if JAMENDO_CLIENT_ID:
        providers.append(("jamendo", search_jamendo_audio))

    results = await asyncio.gather(
        *(searcher(query, max(limit, limit * 2)) for _, searcher in providers),
        return_exceptions=True,
    )
    buckets = []
    errors = []
    for (provider_name, _), result in zip(providers, results):
        if isinstance(result, Exception):
            errors.append(provider_name)
            metrics[f"dj_search_errors_{provider_name}"] += 1
            logging.warning("[DJ] Falló el catálogo %s: %s", provider_name, type(result).__name__)
            buckets.append([])
        else:
            buckets.append(result if isinstance(result, list) else [])

    mixed = []
    seen = set()
    for index in range(max((len(bucket) for bucket in buckets), default=0)):
        for bucket in buckets:
            if index >= len(bucket):
                continue
            track = bucket[index]
            identity = str(track.get("url") or track.get("identifier") or "")
            if identity and identity not in seen:
                mixed.append(track)
                seen.add(identity)
            if len(mixed) >= limit:
                break
        if len(mixed) >= limit:
            break

    if not mixed and errors and len(errors) == len(providers):
        raise RuntimeError("Todos los catálogos musicales están temporalmente fuera de servicio.")
    return mixed[:limit]


async def ensure_voice_room(thread_id: str):
    """Conecta a Katherine a la llamada asociada al hilo y publica su pista de micrófono."""
    thread_id = str(thread_id)
    if not thread_id:
        return False, "No pude identificar la sala de esta conversación."
    if not COMMUNITY_TOKEN:
        return False, "Falta COMMUNITY_BOT_TOKEN en las variables de Render."
    if livekit_rtc is None:
        return False, "LiveKit no está disponible en Render. Revisa las dependencias."

    lock = VOICE_CONNECT_LOCKS.setdefault(thread_id, asyncio.Lock())
    async with lock:
        if thread_id in DJ_ROOMS:
            return True, "Katherine ya está dentro de la llamada."

        ready = asyncio.Event()
        DJ_READY_EVENTS[thread_id] = ready
        room = None
        joined_api = False
        try:
            result = await api("POST", f"/live-rooms/{thread_id}/join", "community")
            if not result.get("ok"):
                data = result.get("data") or {}
                if not isinstance(data, dict):
                    data = {"message": str(data)}
                reason = (
                    data.get("anarchy:error")
                    or data.get("error")
                    or data.get("message")
                )
                return False, (
                    f"No pude entrar a la llamada (HTTP {result.get('status')}): "
                    f"{reason or 'Anarchy Apps rechazó la solicitud'}."
                )

            joined_api = True
            grant = result.get("data") or {}
            room_url = grant.get("url")
            room_token = grant.get("token")
            if not room_url or not room_token:
                await api("POST", f"/live-rooms/{thread_id}/leave", "community")
                return False, "Anarchy no devolvió las credenciales temporales de LiveKit."

            room = livekit_rtc.Room()
            await room.connect(room_url, room_token)
            await asyncio.wait_for(ready.wait(), timeout=20)

            audio_source = livekit_rtc.AudioSource(48000, 1)
            audio_track = livekit_rtc.LocalAudioTrack.create_audio_track(
                "katherine-voice",
                audio_source
            )
            await room.local_participant.publish_track(
                audio_track,
                livekit_rtc.TrackPublishOptions(
                    source=livekit_rtc.TrackSource.SOURCE_MICROPHONE
                )
            )

            DJ_ROOMS[thread_id] = {
                "room": room,
                "audio_source": audio_source,
                "audio_track": audio_track,
                "stop_event": asyncio.Event(),
                "playback_stop_event": asyncio.Event(),
                "task": None,
                "speech_task": None,
                "speech_queue": asyncio.Queue(maxsize=20),
                "speech_priority_queue": asyncio.Queue(maxsize=5),
                "reading_enabled": False,
                "speaking": asyncio.Event(),
                "filename": None,
                "track": None,
                "looping": False,
                "volume": 0.75,
                "last_volume": 0.75,
                "temporary_audio_path": None,
                "queue": deque(),
                "pause_event": asyncio.Event(),
                "skip_event": asyncio.Event(),
                "skip_requested": False,
                "tts_voice": TTS_VOICE,
                "tts_rate": "+0%",
                "tts_volume": 0.75,
                "last_tts_volume": 0.75,
                "tts_read_links": False,
                "tts_read_mentions": False,
                "started_at": time.time()
            }
            room_state = DJ_ROOMS[thread_id]
            room_state["tts_voice"] = get_chat_setting(thread_id, "tts_voice", TTS_VOICE)
            room_state["tts_rate"] = get_chat_setting(thread_id, "tts_rate", "+0%")
            try:
                saved_tts_volume = float(get_chat_setting(thread_id, "tts_volume", "75"))
            except (TypeError, ValueError):
                saved_tts_volume = 75.0
            try:
                saved_tts_last_volume = float(
                    get_chat_setting(thread_id, "tts_last_volume", str(saved_tts_volume))
                )
            except (TypeError, ValueError):
                saved_tts_last_volume = saved_tts_volume
            room_state["tts_volume"] = max(0.0, min(100.0, saved_tts_volume)) / 100.0
            room_state["last_tts_volume"] = max(
                0.0, min(100.0, saved_tts_last_volume)
            ) / 100.0
            room_state["tts_read_links"] = get_chat_setting(thread_id, "tts_read_links", "0") == "1"
            room_state["tts_read_mentions"] = get_chat_setting(thread_id, "tts_read_mentions", "0") == "1"
            logging.info("[VOZ] Katherine conectada a la llamada %s", thread_id)
            return True, "Katherine se unió a la llamada."
        except asyncio.TimeoutError:
            if room is not None:
                try:
                    await room.disconnect()
                except Exception:
                    pass
            if joined_api:
                await api("POST", f"/live-rooms/{thread_id}/leave", "community")
            return False, "La sala no confirmó que Katherine pudiera publicar audio."
        except Exception as exc:
            if room is not None:
                try:
                    await room.disconnect()
                except Exception:
                    pass
            if joined_api:
                await api("POST", f"/live-rooms/{thread_id}/leave", "community")
            logging.exception("[VOZ] No se pudo conectar a LiveKit")
            return False, f"No pude conectarme a la llamada ({type(exc).__name__})."
        finally:
            DJ_READY_EVENTS.pop(thread_id, None)
            if not DJ_ROOMS.get(thread_id):
                VOICE_CONNECT_LOCKS.pop(thread_id, None)


async def stop_dj_room(
    thread_id: str,
    request_leave: bool = True,
    cancel_playback: bool = True
):
    thread_id = str(thread_id)
    state = DJ_ROOMS.pop(thread_id, None)
    if state:
        for event_name in ("stop_event", "playback_stop_event"):
            event = state.get(event_name)
            if event:
                event.set()

        current_task = asyncio.current_task()
        for task_name in ("task", "speech_task"):
            task = state.get(task_name)
            if (
                task
                and task is not current_task
                and not task.done()
                and (cancel_playback or task_name == "speech_task")
            ):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logging.exception("[VOZ] Error deteniendo %s", task_name)

        room = state.get("room")
        if room is not None:
            try:
                await room.disconnect()
            except Exception:
                logging.exception("[VOZ] Error cerrando LiveKit")

        temporary_audio = state.get("temporary_audio_path")
        if temporary_audio:
            try:
                Path(temporary_audio).unlink(missing_ok=True)
            except OSError:
                logging.warning("[DJ] No pude borrar el audio temporal de %s", thread_id)

    if request_leave:
        await api("POST", f"/live-rooms/{thread_id}/leave", "community")


def clean_speech_text(content: str, include_links: bool = False, include_mentions: bool = False) -> str:
    """Limpia texto y convierte enlaces/menciones en frases legibles si están habilitados."""
    text = html.unescape(str(content or ""))
    if include_links:
        text = re.sub(
            r"https?://([^/\s?#]+)[^\s]*",
            lambda match: f"enlace a {match.group(1)}",
            text,
            flags=re.I
        )
    else:
        text = re.sub(r"https?://\S+", " ", text, flags=re.I)
    if include_mentions:
        text = re.sub(r"<@!?([^>]+)>", "mención a usuario", text)
    else:
        text = re.sub(r"<@!?[^>]+>", " ", text)
    text = re.sub(r"[*_~`>#|]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:700]


async def stream_speech_file(thread_id: str, file_path: str):
    state = DJ_ROOMS.get(str(thread_id))
    if not state or pyav is None or np is None:
        return
    container = pyav.open(file_path, mode="r")
    try:
        audio_stream = next(
            (stream for stream in container.streams if stream.type == "audio"),
            None
        )
        if audio_stream is None:
            raise ValueError("El audio TTS no contiene una pista de sonido.")
        resampler = pyav.AudioResampler(
            format="s16",
            layout="mono",
            rate=48000
        )
        for decoded in container.decode(audio_stream):
            if (
                DJ_ROOMS.get(str(thread_id)) is not state
                or state["stop_event"].is_set()
                or not state.get("reading_enabled")
            ):
                break
            for frame in resampler.resample(decoded):
                samples = np.asarray(frame.to_ndarray(), dtype=np.int16).reshape(-1)
                for offset in range(0, len(samples), 480):
                    if (
                        DJ_ROOMS.get(str(thread_id)) is not state
                        or state["stop_event"].is_set()
                        or not state.get("reading_enabled")
                    ):
                        return
                    chunk = samples[offset:offset + 480]
                    if not len(chunk):
                        continue
                    # Volumen independiente para TTS; se lee en cada bloque para
                    # permitir cambios en tiempo real mientras Katherine habla.
                    tts_volume = max(
                        0.0, min(1.0, float(state.get("tts_volume", 0.75)))
                    )
                    if tts_volume <= 0:
                        chunk = np.zeros_like(chunk)
                    elif tts_volume < 1:
                        chunk = np.clip(
                            chunk.astype(np.float32) * tts_volume,
                            -32768, 32767
                        ).astype(np.int16)
                    audio_frame = livekit_rtc.AudioFrame.create(
                        48000, 1, len(chunk)
                    )
                    destination = np.frombuffer(audio_frame.data, dtype=np.int16)
                    destination[:len(chunk)] = chunk
                    await state["audio_source"].capture_frame(audio_frame)
    finally:
        container.close()


async def speech_queue_worker(thread_id: str):
    state = DJ_ROOMS.get(str(thread_id))
    if not state or edge_tts is None:
        return
    while not state["stop_event"].is_set():
        queue = state.get("speech_priority_queue")
        try:
            text_to_read = queue.get_nowait()
        except asyncio.QueueEmpty:
            queue = state["speech_queue"]
            try:
                text_to_read = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

        temp_path = None
        try:
            if not state.get("reading_enabled") or not text_to_read:
                continue
            fd, temp_path = tempfile.mkstemp(
                prefix="katherine-tts-",
                suffix=".mp3"
            )
            os.close(fd)
            communicate = edge_tts.Communicate(
                text_to_read,
                state.get("tts_voice", TTS_VOICE),
                rate=state.get("tts_rate", "+0%")
            )
            await communicate.save(temp_path)

            if (
                DJ_ROOMS.get(str(thread_id)) is state
                and state.get("reading_enabled")
                and not state["stop_event"].is_set()
            ):
                state["speaking"].set()
                await stream_speech_file(thread_id, temp_path)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("[TTS] No se pudo leer un mensaje en %s", thread_id)
        finally:
            state["speaking"].clear()
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            queue.task_done()


async def enable_room_reading(thread_id: str):
    if edge_tts is None:
        return False, "Falta edge-tts en Render; revisa la instalación de requirements.txt."
    if pyav is None or np is None or livekit_rtc is None:
        return False, "Faltan las dependencias de audio para activar la lectura."
    ok, message = await ensure_voice_room(thread_id)
    if not ok:
        return False, message

    state = DJ_ROOMS[str(thread_id)]
    state["reading_enabled"] = True
    task = state.get("speech_task")
    if task is None or task.done():
        state["speech_task"] = asyncio.create_task(
            speech_queue_worker(str(thread_id))
        )
    return True, "Lectura en voz alta activada. Leeré los mensajes de texto de esta sala."


async def disable_room_reading(thread_id: str):
    state = DJ_ROOMS.get(str(thread_id))
    if not state:
        return True, "La lectura ya está desactivada; Katherine no está conectada a esta sala."
    state["reading_enabled"] = False
    queue = state.get("speech_queue")
    if queue:
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                break

    task = state.get("speech_task")
    state["speech_task"] = None
    if task and task is not asyncio.current_task() and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    state["speaking"].clear()
    return True, "Lectura en voz alta desactivada. Katherine seguirá dentro de la llamada."


async def enqueue_message_for_speech(event: dict, source: str):
    thread_id = str(event.get("threadId") or "")
    state = DJ_ROOMS.get(thread_id)
    if not state or not state.get("reading_enabled"):
        return

    message = event.get("message") or {}
    author = message.get("author") or {}
    if not isinstance(author, dict):
        return
    if any(
        str(author.get(key, "")).lower() in ("true", "1", "yes")
        for key in ("bot", "isBot", "is_bot")
    ):
        return

    author_uid = str(author.get("uid") or author.get("userId") or "")
    identity = BOT_IDENTITIES.get(source) or {}
    bot_uid = str(identity.get("uid") or "")
    bot_name = str(identity.get("name") or "")
    author_name = str(author.get("nickname") or author.get("name") or "")
    if bot_uid and author_uid and author_uid == bot_uid:
        return
    if bot_name and author_name and author_name.casefold() == bot_name.casefold():
        return

    content = str(message.get("content") or "").strip()
    if not content or content.startswith(("!", "-", PREFIX)):
        return

    if state.get("tts_read_mentions"):
        for mention in message.get("mentions") or []:
            if not isinstance(mention, dict):
                continue
            mention_uid = str(mention.get("uid") or mention.get("userId") or mention.get("id") or "")
            mention_name = str(
                mention.get("nickname") or mention.get("username")
                or mention.get("displayName") or mention.get("name") or "usuario"
            )
            if mention_uid:
                content = content.replace(f"<@{mention_uid}>", mention_name)
                content = content.replace(f"<@!{mention_uid}>", mention_name)

    speech_text = clean_speech_text(
        content,
        include_links=bool(state.get("tts_read_links")),
        include_mentions=bool(state.get("tts_read_mentions"))
    )
    if len(speech_text) < 2:
        return

    speech_text = speech_text[:700]
    digest = hashlib.sha256(speech_text.casefold().encode("utf-8")).hexdigest()
    recent = state.setdefault("speech_recent", {})
    now = time.time()
    for old_digest, stamp in list(recent.items()):
        if now - stamp > 15:
            recent.pop(old_digest, None)
    if digest in recent:
        metrics["tts_duplicates_skipped"] += 1
        return
    recent[digest] = now
    is_directed = bool(re.match(r"^(?:oye[, ]+)?katherine\b", content, flags=re.I))
    queue = (
        state.get("speech_priority_queue") or state["speech_queue"]
        if is_directed else state["speech_queue"]
    )
    if queue.full():
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            pass
    try:
        queue.put_nowait(speech_text)
        metrics["tts_priority_enqueued" if is_directed else "tts_regular_enqueued"] += 1
    except asyncio.QueueFull:
        logging.warning("[TTS] Cola %s llena; se omitió un mensaje en %s", "prioritaria" if is_directed else "normal", thread_id)


async def download_dj_audio(url: str, file_extension: str | None = None) -> Path:
    """Download a validated catalog track to a temporary local file."""
    parsed = urlparse(str(url))
    if not dj_audio_url_allowed(url):
        raise ValueError("El enlace no pertenece a una fuente musical autorizada.")

    suffix = Path(parsed.path).suffix.lower()
    if suffix not in DJ_AUDIO_EXTENSIONS:
        suffix = str(file_extension or ".mp3").lower()
    if suffix not in DJ_AUDIO_EXTENSIONS:
        raise ValueError("El enlace no tiene un formato de audio compatible.")

    fd, temp_name = tempfile.mkstemp(prefix="katherine-dj-", suffix=suffix)
    temp_path = Path(temp_name)
    total_bytes = 0
    max_bytes = 150 * 1024 * 1024

    try:
        timeout = httpx.Timeout(60.0, connect=20.0)
        headers = {
            "User-Agent": "Katherine-AnarchyBot/3.1",
            "Accept": "audio/*,application/octet-stream;q=0.9,*/*;q=0.5"
        }
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers
        ) as client:
            async with client.stream("GET", str(url)) as response:
                if not response.is_success:
                    raise RuntimeError(
                        f"La fuente musical respondió HTTP {response.status_code}."
                    )
                if not dj_audio_url_allowed(str(response.url)):
                    raise ValueError("La descarga redirigió a un dominio no autorizado.")
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type or "application/json" in content_type:
                    raise RuntimeError(
                        "La fuente musical devolvió una página web en vez del audio."
                    )
                try:
                    content_length = int(response.headers.get("content-length", "0"))
                except (TypeError, ValueError):
                    content_length = 0
                if content_length > max_bytes:
                    raise RuntimeError("La pista supera el límite de 150 MB.")

                with os.fdopen(fd, "wb") as output:
                    fd = -1
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            raise RuntimeError("La pista supera el límite de 150 MB.")
                        output.write(chunk)

        if total_bytes < 1024:
            raise RuntimeError("El archivo descargado está vacío o incompleto.")

        container = pyav.open(str(temp_path), mode="r")
        try:
            audio_stream = next(
                (stream for stream in container.streams if stream.type == "audio"),
                None
            )
            if audio_stream is None:
                raise RuntimeError("El archivo descargado no contiene una pista de audio.")
            first_frame = next(container.decode(audio_stream), None)
            if first_frame is None:
                raise RuntimeError("La pista no contiene frames de audio decodificables.")
        finally:
            container.close()

        logging.info(
            "[DJ] Audio descargado y validado | %.2f MB | %s",
            total_bytes / (1024 * 1024),
            suffix
        )
        return temp_path

    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


async def _dj_play_single(thread_id: str, path: Path, state: dict) -> bool:
    """Reproduce una pista; False indica salto o interrupción."""
    container = pyav.open(str(path), mode="r")
    try:
        audio_stream = next(
            (stream for stream in container.streams if stream.type == "audio"),
            None
        )
        if audio_stream is None:
            raise ValueError("El archivo no contiene una pista de audio.")
        resampler = pyav.AudioResampler(format="s16", layout="mono", rate=48000)
        for decoded in container.decode(audio_stream):
            if state["stop_event"].is_set() or state["playback_stop_event"].is_set():
                return False
            for frame in resampler.resample(decoded):
                samples = np.asarray(frame.to_ndarray(), dtype=np.int16).reshape(-1)
                volume = max(0.0, min(1.0, float(state.get("volume", 0.75))))
                if volume <= 0:
                    samples = np.zeros_like(samples)
                elif volume < 1:
                    samples = np.clip(
                        samples.astype(np.float32) * volume, -32768, 32767
                    ).astype(np.int16)
                for offset in range(0, len(samples), 480):
                    if state["stop_event"].is_set() or state["playback_stop_event"].is_set():
                        return False
                    if state["skip_event"].is_set():
                        return False
                    while not state["pause_event"].is_set():
                        if state["stop_event"].is_set() or state["playback_stop_event"].is_set():
                            return False
                        if state["skip_event"].is_set():
                            return False
                        await asyncio.sleep(0.04)
                    while state["speaking"].is_set():
                        if state["stop_event"].is_set() or state["playback_stop_event"].is_set():
                            return False
                        if state["skip_event"].is_set():
                            return False
                        await asyncio.sleep(0.02)
                    chunk = samples[offset:offset + 480]
                    if not len(chunk):
                        continue
                    audio_frame = livekit_rtc.AudioFrame.create(48000, 1, len(chunk))
                    destination = np.frombuffer(audio_frame.data, dtype=np.int16)
                    destination[:len(chunk)] = chunk
                    await state["audio_source"].capture_frame(audio_frame)
        return True
    finally:
        container.close()


async def dj_playback_loop(
    thread_id: str,
    file_path: Path | str,
    looping: bool,
    audio_source,
    display_name: str | None = None
):
    state = DJ_ROOMS.get(str(thread_id))
    if not state:
        return

    current_path = Path(file_path)
    current_temporary = state.get("temporary_audio_path")
    current_looping = bool(looping)
    current_title = display_name or current_path.name

    try:
        while not state["stop_event"].is_set() and not state["playback_stop_event"].is_set():
            state["filename"] = current_title
            state["track_started_at"] = time.time()
            state["skip_event"].clear()
            try:
                while not state["stop_event"].is_set() and not state["playback_stop_event"].is_set():
                    last_playback_error = None
                    for attempt in range(3):
                        try:
                            played = await _dj_play_single(thread_id, current_path, state)
                            last_playback_error = None
                            break
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            last_playback_error = exc
                            if attempt >= 2:
                                raise
                            metrics["dj_playback_retries"] += 1
                            delay = min(4.0, 0.75 * (2 ** attempt))
                            logging.warning(
                                "[DJ] Reintento %s/2 para %s en sala %s tras %s",
                                attempt + 1, current_title, thread_id, type(exc).__name__
                            )
                            await asyncio.sleep(delay)
                    if last_playback_error is not None:
                        raise last_playback_error
                    if (
                        current_looping and played
                        and not state["skip_event"].is_set()
                        and not state["stop_event"].is_set()
                        and not state["playback_stop_event"].is_set()
                    ):
                        continue
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                metrics["dj_playback_errors"] += 1
                logging.exception("[DJ] Falló la pista %s en sala %s", current_title, thread_id)
                await send_message(
                    str(thread_id),
                    f"⚠️ No pude reproducir «{current_title}» ({type(exc).__name__}). "
                    "La saltaré y continuaré con la cola.",
                    "developer"
                )

            if current_temporary:
                try:
                    Path(current_temporary).unlink(missing_ok=True)
                except OSError:
                    logging.warning("[DJ] No pude eliminar una pista temporal de %s", thread_id)
            current_temporary = None
            state["temporary_audio_path"] = None

            if state["stop_event"].is_set() or state["playback_stop_event"].is_set():
                break
            if state["skip_event"].is_set():
                state["skip_event"].clear()
                metrics["dj_tracks_skipped"] += 1

            if state["queue"]:
                next_item = state["queue"].popleft()
                current_path = Path(next_item["path"])
                current_temporary = next_item.get("temporary")
                current_title = next_item.get("title") or current_path.name
                current_looping = bool(next_item.get("looping", False))
                state["track"] = next_item.get("metadata")
                state["temporary_audio_path"] = current_temporary
                state["filename"] = current_title
                metrics["dj_queue_advanced"] += 1
                continue
            break

    except asyncio.CancelledError:
        raise
    finally:
        if current_temporary:
            try:
                Path(current_temporary).unlink(missing_ok=True)
            except OSError:
                pass
        if DJ_ROOMS.get(str(thread_id)) is state and state.get("task") is asyncio.current_task():
            state["task"] = None
            state["filename"] = None
            state["track"] = None
            if not state.get("reading_enabled") and not state["stop_event"].is_set():
                await stop_dj_room(
                    str(thread_id),
                    request_leave=True,
                    cancel_playback=False
                )


def validate_local_dj_audio(path: Path):
    resolved = Path(path).resolve()
    music_root = DJ_MUSIC_DIR.resolve()
    if not resolved.is_relative_to(music_root):
        raise ValueError("El archivo está fuera del directorio de música.")
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise ValueError("El archivo no existe o está vacío.")
    if resolved.stat().st_size > 80 * 1024 * 1024:
        raise ValueError("El archivo supera el límite local de 80 MB.")
    if resolved.suffix.lower() not in DJ_AUDIO_EXTENSIONS:
        raise ValueError("Formato de audio no compatible.")
    container = pyav.open(str(resolved), mode="r")
    try:
        audio_stream = next(
            (stream for stream in container.streams if stream.type == "audio"),
            None
        )
        if audio_stream is None:
            raise ValueError("El archivo no contiene una pista de audio.")
        if next(container.decode(audio_stream), None) is None:
            raise ValueError("La pista no contiene audio decodificable.")
    finally:
        container.close()
    return resolved


async def start_dj_room(
    thread_id: str,
    file_path: Path | str,
    looping: bool,
    track_metadata: dict | None = None,
    enqueue: bool = False
):
    if pyav is None or livekit_rtc is None or np is None:
        return False, "Faltan dependencias de LiveKit, PyAV o NumPy en Render."

    temporary_audio = None
    original_source = file_path
    if isinstance(file_path, str) and file_path.lower().startswith("https://"):
        try:
            temporary_audio = await download_dj_audio(
                file_path,
                file_extension=(track_metadata or {}).get("extension")
            )
            file_path = temporary_audio
        except Exception as exc:
            logging.exception("[DJ] No se pudo preparar la pista remota")
            return False, (
                "No pude descargar o validar la canción "
                f"({type(exc).__name__}: {str(exc)[:140]})."
            )
    else:
        try:
            file_path = validate_local_dj_audio(Path(file_path))
        except Exception as exc:
            metrics["dj_validation_errors"] += 1
            return False, f"El archivo no pasó la validación de audio: {str(exc)[:140]}."

    ok, message = await ensure_voice_room(str(thread_id))
    if not ok:
        if temporary_audio:
            Path(temporary_audio).unlink(missing_ok=True)
        return False, message

    state = DJ_ROOMS[str(thread_id)]
    display_name = (
        (track_metadata or {}).get("title")
        or (Path(str(original_source)).name if isinstance(original_source, (str, Path)) else "Audio")
    )
    active_task = state.get("task")

    if enqueue and active_task and not active_task.done():
        if len(state["queue"]) >= 50:
            if temporary_audio:
                Path(temporary_audio).unlink(missing_ok=True)
            return False, "La cola alcanzó su límite de 50 canciones."
        state["queue"].append({
            "path": str(file_path),
            "temporary": str(temporary_audio) if temporary_audio else None,
            "title": display_name,
            "metadata": track_metadata,
            "looping": bool(looping)
        })
        metrics["dj_tracks_queued"] += 1
        return True, f"«{display_name}» añadida a la cola (posición {len(state['queue'])})."

    if active_task and not active_task.done():
        state["task"] = None
        state["playback_stop_event"].set()
        active_task.cancel()
        try:
            await active_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logging.exception("[DJ] Error reemplazando la pista anterior")

    for queued in list(state.get("queue", [])):
        if queued.get("temporary"):
            try:
                Path(queued["temporary"]).unlink(missing_ok=True)
            except OSError:
                pass
    state["queue"].clear()

    old_temporary = state.get("temporary_audio_path")
    if old_temporary:
        try:
            Path(old_temporary).unlink(missing_ok=True)
        except OSError:
            logging.warning("[DJ] No pude borrar la pista temporal anterior.")

    state["playback_stop_event"] = asyncio.Event()
    state["skip_event"].clear()
    state["pause_event"].set()
    state["filename"] = display_name
    state["track"] = track_metadata
    state["looping"] = bool(looping)
    state["temporary_audio_path"] = str(temporary_audio) if temporary_audio else None
    state.setdefault("volume", 0.75)
    state.setdefault("last_volume", state["volume"])
    state["task"] = asyncio.create_task(
        dj_playback_loop(
            str(thread_id), Path(file_path), bool(looping),
            state["audio_source"], display_name
        )
    )
    metrics["dj_tracks_started"] += 1
    return True, "Reproducción iniciada."


# ---------------------------------------------------------------------------
# GENSHIN DATABASE API# ---------------------------------------------------------------------------
# GENSHIN DATABASE API
# ---------------------------------------------------------------------------
async def game_data(folder: str, query: str, extra_params: dict | None = None):
    options_key = json.dumps(extra_params or {}, sort_keys=True, ensure_ascii=False)
    key = f"{folder}:{query.casefold()}:{options_key}"

    if key in cache and cache[key][0] > time.time():
        return cache[key][1]

    params = {
        "query": query,
        "dumpResult": "true",
        "matchNames": "true",
        "matchAltNames": "true",
        "matchAliases": "true",
        "queryLanguages": "english,spanish",
        "resultLanguage": "spanish"
    }
    if extra_params:
        params.update(extra_params)

    try:
        r = await external_request(
            "GET", f"{GAME_API}/{folder}", params=params, timeout=18
        )
        r.raise_for_status()
        data = r.json()

        if isinstance(data, dict):
            if "result" in data and ("folder" in data or "query" in data):
                data = data.get("result")
            else:
                data = data.get("data", data.get("result", data))

        cache[key] = (time.time() + 21600, data)

        if len(cache) > 400:
            cleanup_expired_state()
            while len(cache) > 400:
                cache.pop(next(iter(cache)))

        return data

    except Exception as exc:
        logging.warning(
            "Genshin API %s/%s: %s",
            folder,
            query,
            exc
        )
        return None


def clean_game_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</\s*(?:p|div|li|h[1-6])\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def game_excerpt(value: Any, max_chars: int = 850) -> str:
    text = clean_game_text(value)
    if len(text) <= max_chars:
        return text

    sample = text[:max_chars + 1]
    boundaries = [
        match.end()
        for match in re.finditer(r"[.!?…](?:[\"')\]]?)(?:\s+|$)", sample)
        if match.end() >= int(max_chars * 0.55)
    ]

    if boundaries:
        cut = boundaries[-1]
    else:
        cut = sample.rfind(" ")
        if cut < int(max_chars * 0.55):
            cut = max_chars

    return text[:cut].rstrip(" ,;:-") + "…"


def game_items_text(items: Any, limit: int = 7) -> list[str]:
    if not isinstance(items, list):
        return []

    lines = []
    for item in items[:limit]:
        if isinstance(item, str):
            value = clean_game_text(item)
        elif isinstance(item, dict):
            value = (
                item.get("name")
                or item.get("nameText")
                or item.get("title")
                or item.get("description")
            )
            value = clean_game_text(value)
            count = item.get("count")
            rarity = item.get("rarity")
            if count is not None:
                value += f" ×{count}"
            if rarity is not None:
                value += f" ({rarity}★)"
        else:
            continue

        if value:
            lines.append(value[:180])

    return lines


def describe_game(data: Any, fallback: str, folder: str | None = None) -> str:
    if isinstance(data, dict) and "result" in data and (
        "folder" in data or "query" in data
    ):
        data = data.get("result")

    if not isinstance(data, dict):
        if isinstance(data, list) and data:
            return (
                f"RESULTADOS DE GENSHIN — {fallback}\n"
                + "\n".join(f"• {clean_game_text(x)}" for x in data[:15])
            )
        return f"No encontré información de «{fallback}»."

    name = clean_game_text(data.get("name") or data.get("Name") or fallback)
    lines = [f"GENSHIN IMPACT — {name}"]
    folder = (folder or "").casefold()

    def add(label: str, value: Any, cap: int = 500):
        if value is None or value == "":
            return
        if isinstance(value, list):
            parts = game_items_text(value, limit=6)
            value_text = ", ".join(parts)
        elif isinstance(value, dict):
            value_text = clean_game_text(
                value.get("name")
                or value.get("nameText")
                or value.get("description")
                or value.get("text")
                or ""
            )
        else:
            value_text = clean_game_text(value)
        if value_text:
            lines.append(f"{label}: {game_excerpt(value_text, cap)}")

    if folder == "enemies":
        add("Categoría", data.get("categoryText"))
        add("Tipo", data.get("enemyType") or data.get("monsterType"))
        add("Nombre especial", data.get("specialNames"), 240)
        add("Descripción", data.get("description"), 950)

        investigation = data.get("investigation")
        if isinstance(investigation, dict):
            inv_name = investigation.get("name")
            inv_desc = investigation.get("description")
            if inv_name and clean_game_text(inv_name) != name:
                add("Registro de archivo", inv_name, 180)
            if inv_desc and clean_game_text(inv_desc) != clean_game_text(data.get("description")):
                add("Información de archivo", inv_desc, 500)

        rewards = game_items_text(data.get("rewardPreview"), limit=8)
        if rewards:
            lines.append("Recompensas registradas:")
            lines.extend(f"• {item}" for item in rewards)

        lines.append("Nota: esta ficha muestra los datos disponibles en la base; no inventa estadísticas de combate.")

    elif folder == "weapons":
        add("Rareza", f"{data.get('rarity')}★" if data.get("rarity") else None)
        add("Tipo", data.get("weaponText"))
        add("Ataque base", data.get("baseAtkValue"))
        add("Atributo secundario", data.get("baseStatText") or data.get("mainStatText"))
        add("Habilidad pasiva", data.get("effectName"))
        refine = data.get("r1")
        if isinstance(refine, dict):
            add("Efecto R1", refine.get("description") or refine.get("effectDescription"), 700)
        add("Descripción", data.get("description"), 550)
        add("Historia", data.get("story"), 350)

    elif folder == "artifacts":
        rarity = data.get("rarityList") or data.get("rarity")
        add("Rareza", [f"{x}★" for x in rarity] if isinstance(rarity, list) else rarity)
        add("Efecto de 1 pieza", data.get("effect1Pc"), 350)
        add("Efecto de 2 piezas", data.get("effect2Pc"), 500)
        add("Efecto de 4 piezas", data.get("effect4Pc"), 650)
        for slot, label in (
            ("flower", "Flor"),
            ("plume", "Pluma"),
            ("sands", "Reloj"),
            ("goblet", "Copa"),
            ("circlet", "Corona")
        ):
            detail = data.get(slot)
            if isinstance(detail, dict):
                detail_name = detail.get("name")
                detail_desc = detail.get("description")
                if detail_name:
                    add(label, detail_name, 130)
                if detail_desc:
                    add(f"{label} - descripción", detail_desc, 180)

    else:
        fields = (
            ("title", "Título"),
            ("rarity", "Rareza"),
            ("elementText", "Elemento"),
            ("weaponText", "Tipo de arma"),
            ("region", "Región"),
            ("substatText", "Atributo secundario"),
            ("constellation", "Constelación"),
            ("birthday", "Cumpleaños"),
            ("affiliation", "Afiliación"),
            ("categoryText", "Categoría"),
            ("enemyType", "Tipo"),
            ("domainTypeText", "Tipo de dominio"),
            ("foodType", "Tipo de comida"),
            ("effect", "Efecto"),
            ("effectName", "Nombre del efecto"),
            ("mainStatText", "Estadística principal"),
            ("baseAtkValue", "Ataque base"),
            ("source", "Obtención")
        )

        for key, label in fields:
            if key in data:
                add(label, data.get(key), 300)

        add("Descripción", data.get("description"), 800)
        add("Efecto", data.get("effect2Pc") or data.get("effect4Pc"), 650)
        add("Ingredientes", data.get("ingredients"), 300)
        add("Recompensas", data.get("rewardPreview"), 300)
        add("Historia", data.get("story"), 350)

    if len(lines) == 1:
        # Respaldo legible para categorías cuyos campos cambien en futuras versiones.
        for key, value in data.items():
            if key in ("id", "images", "costs", "stats", "url", "filename"):
                continue
            if isinstance(value, (str, int, float)) and str(value).strip():
                label = key.replace("_", " ").capitalize()
                add(label, value, 260)
            if len(lines) >= 8:
                break

    return "\n".join(lines)


async def game_stats(folder: str, query: str):
    key = f"stats:{folder}:{query.casefold()}"

    if key in cache and cache[key][0] > time.time():
        return cache[key][1]

    params = {
        "folder": folder,
        "query": query,
        "queryLanguages": "english,spanish"
    }

    try:
        response = await external_request(
            "GET", f"{GAME_API}/stats", params=params, timeout=18
        )
        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict):
            if "result" in data and isinstance(data["result"], dict):
                data = data["result"]
            cache[key] = (time.time() + 21600, data)
            return data

    except Exception as exc:
        logging.warning(
            "No se pudieron consultar estadísticas de %s (%s): %s",
            query,
            folder,
            exc
        )

    return None


async def character_stats(query: str):
    return await game_stats("characters", query)


async def get_character(query: str):
    return await game_data("characters", query)


def format_number(value):
    try:
        return f"{float(value):,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return "—"


def get_level90(stats):
    if not isinstance(stats, dict):
        return None

    return stats.get("90") or stats.get("90+") or stats.get("89")


def get_level_stats(stats: Any, level: str):
    """Obtiene un nivel concreto del endpoint de estadísticas de Genshin-DB."""
    if not isinstance(stats, dict):
        return None
    level = str(level).strip()
    if not re.fullmatch(r"(?:1|20\+?|40\+?|50\+?|60\+?|70\+?|80\+?|90)", level):
        return None
    value = stats.get(level)
    return value if isinstance(value, dict) else None


def aggregate_material_costs(costs: Any) -> dict[str, int]:
    """Suma materiales incluso cuando la API cambia la profundidad de los datos."""
    totals: dict[str, int] = {}

    def walk(value):
        if isinstance(value, list):
            for entry in value:
                walk(entry)
            return
        if not isinstance(value, dict):
            return

        name = value.get("name") or value.get("nameText")
        count = value.get("count")
        if name is not None and count is not None:
            try:
                amount = int(count)
                if amount > 0:
                    key = clean_game_text(name)
                    totals[key] = totals.get(key, 0) + amount
            except (TypeError, ValueError):
                pass
            return

        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child)

    walk(costs)
    return totals


def parse_percent(value: str) -> float:
    return float(str(value).replace("%", "").replace(",", "."))


# ---------------------------------------------------------------------------
# MATRIZ DE REGRESIÓN DEL SISTEMA DE MENSAJES
# Cada fila valida una longitud exacta de contenido desde 0 hasta 5300.
# La matriz permite detectar regresiones en el límite de 2000 caracteres,
# especialmente en los bordes de bloque y en el último fragmento.
# ---------------------------------------------------------------------------
MESSAGE_SPLIT_REGRESSION_CASES = (
    (0, 0),
    (1, 1),
    (2, 1),
    (3, 1),
    (4, 1),
    (5, 1),
    (6, 1),
    (7, 1),
    (8, 1),
    (9, 1),
    (10, 1),
    (11, 1),
    (12, 1),
    (13, 1),
    (14, 1),
    (15, 1),
    (16, 1),
    (17, 1),
    (18, 1),
    (19, 1),
    (20, 1),
    (21, 1),
    (22, 1),
    (23, 1),
    (24, 1),
    (25, 1),
    (26, 1),
    (27, 1),
    (28, 1),
    (29, 1),
    (30, 1),
    (31, 1),
    (32, 1),
    (33, 1),
    (34, 1),
    (35, 1),
    (36, 1),
    (37, 1),
    (38, 1),
    (39, 1),
    (40, 1),
    (41, 1),
    (42, 1),
    (43, 1),
    (44, 1),
    (45, 1),
    (46, 1),
    (47, 1),
    (48, 1),
    (49, 1),
    (50, 1),
    (51, 1),
    (52, 1),
    (53, 1),
    (54, 1),
    (55, 1),
    (56, 1),
    (57, 1),
    (58, 1),
    (59, 1),
    (60, 1),
    (61, 1),
    (62, 1),
    (63, 1),
    (64, 1),
    (65, 1),
    (66, 1),
    (67, 1),
    (68, 1),
    (69, 1),
    (70, 1),
    (71, 1),
    (72, 1),
    (73, 1),
    (74, 1),
    (75, 1),
    (76, 1),
    (77, 1),
    (78, 1),
    (79, 1),
    (80, 1),
    (81, 1),
    (82, 1),
    (83, 1),
    (84, 1),
    (85, 1),
    (86, 1),
    (87, 1),
    (88, 1),
    (89, 1),
    (90, 1),
    (91, 1),
    (92, 1),
    (93, 1),
    (94, 1),
    (95, 1),
    (96, 1),
    (97, 1),
    (98, 1),
    (99, 1),
    (100, 1),
    (101, 1),
    (102, 1),
    (103, 1),
    (104, 1),
    (105, 1),
    (106, 1),
    (107, 1),
    (108, 1),
    (109, 1),
    (110, 1),
    (111, 1),
    (112, 1),
    (113, 1),
    (114, 1),
    (115, 1),
    (116, 1),
    (117, 1),
    (118, 1),
    (119, 1),
    (120, 1),
    (121, 1),
    (122, 1),
    (123, 1),
    (124, 1),
    (125, 1),
    (126, 1),
    (127, 1),
    (128, 1),
    (129, 1),
    (130, 1),
    (131, 1),
    (132, 1),
    (133, 1),
    (134, 1),
    (135, 1),
    (136, 1),
    (137, 1),
    (138, 1),
    (139, 1),
    (140, 1),
    (141, 1),
    (142, 1),
    (143, 1),
    (144, 1),
    (145, 1),
    (146, 1),
    (147, 1),
    (148, 1),
    (149, 1),
    (150, 1),
    (151, 1),
    (152, 1),
    (153, 1),
    (154, 1),
    (155, 1),
    (156, 1),
    (157, 1),
    (158, 1),
    (159, 1),
    (160, 1),
    (161, 1),
    (162, 1),
    (163, 1),
    (164, 1),
    (165, 1),
    (166, 1),
    (167, 1),
    (168, 1),
    (169, 1),
    (170, 1),
    (171, 1),
    (172, 1),
    (173, 1),
    (174, 1),
    (175, 1),
    (176, 1),
    (177, 1),
    (178, 1),
    (179, 1),
    (180, 1),
    (181, 1),
    (182, 1),
    (183, 1),
    (184, 1),
    (185, 1),
    (186, 1),
    (187, 1),
    (188, 1),
    (189, 1),
    (190, 1),
    (191, 1),
    (192, 1),
    (193, 1),
    (194, 1),
    (195, 1),
    (196, 1),
    (197, 1),
    (198, 1),
    (199, 1),
    (200, 1),
    (201, 1),
    (202, 1),
    (203, 1),
    (204, 1),
    (205, 1),
    (206, 1),
    (207, 1),
    (208, 1),
    (209, 1),
    (210, 1),
    (211, 1),
    (212, 1),
    (213, 1),
    (214, 1),
    (215, 1),
    (216, 1),
    (217, 1),
    (218, 1),
    (219, 1),
    (220, 1),
    (221, 1),
    (222, 1),
    (223, 1),
    (224, 1),
    (225, 1),
    (226, 1),
    (227, 1),
    (228, 1),
    (229, 1),
    (230, 1),
    (231, 1),
    (232, 1),
    (233, 1),
    (234, 1),
    (235, 1),
    (236, 1),
    (237, 1),
    (238, 1),
    (239, 1),
    (240, 1),
    (241, 1),
    (242, 1),
    (243, 1),
    (244, 1),
    (245, 1),
    (246, 1),
    (247, 1),
    (248, 1),
    (249, 1),
    (250, 1),
    (251, 1),
    (252, 1),
    (253, 1),
    (254, 1),
    (255, 1),
    (256, 1),
    (257, 1),
    (258, 1),
    (259, 1),
    (260, 1),
    (261, 1),
    (262, 1),
    (263, 1),
    (264, 1),
    (265, 1),
    (266, 1),
    (267, 1),
    (268, 1),
    (269, 1),
    (270, 1),
    (271, 1),
    (272, 1),
    (273, 1),
    (274, 1),
    (275, 1),
    (276, 1),
    (277, 1),
    (278, 1),
    (279, 1),
    (280, 1),
    (281, 1),
    (282, 1),
    (283, 1),
    (284, 1),
    (285, 1),
    (286, 1),
    (287, 1),
    (288, 1),
    (289, 1),
    (290, 1),
    (291, 1),
    (292, 1),
    (293, 1),
    (294, 1),
    (295, 1),
    (296, 1),
    (297, 1),
    (298, 1),
    (299, 1),
    (300, 1),
    (301, 1),
    (302, 1),
    (303, 1),
    (304, 1),
    (305, 1),
    (306, 1),
    (307, 1),
    (308, 1),
    (309, 1),
    (310, 1),
    (311, 1),
    (312, 1),
    (313, 1),
    (314, 1),
    (315, 1),
    (316, 1),
    (317, 1),
    (318, 1),
    (319, 1),
    (320, 1),
    (321, 1),
    (322, 1),
    (323, 1),
    (324, 1),
    (325, 1),
    (326, 1),
    (327, 1),
    (328, 1),
    (329, 1),
    (330, 1),
    (331, 1),
    (332, 1),
    (333, 1),
    (334, 1),
    (335, 1),
    (336, 1),
    (337, 1),
    (338, 1),
    (339, 1),
    (340, 1),
    (341, 1),
    (342, 1),
    (343, 1),
    (344, 1),
    (345, 1),
    (346, 1),
    (347, 1),
    (348, 1),
    (349, 1),
    (350, 1),
    (351, 1),
    (352, 1),
    (353, 1),
    (354, 1),
    (355, 1),
    (356, 1),
    (357, 1),
    (358, 1),
    (359, 1),
    (360, 1),
    (361, 1),
    (362, 1),
    (363, 1),
    (364, 1),
    (365, 1),
    (366, 1),
    (367, 1),
    (368, 1),
    (369, 1),
    (370, 1),
    (371, 1),
    (372, 1),
    (373, 1),
    (374, 1),
    (375, 1),
    (376, 1),
    (377, 1),
    (378, 1),
    (379, 1),
    (380, 1),
    (381, 1),
    (382, 1),
    (383, 1),
    (384, 1),
    (385, 1),
    (386, 1),
    (387, 1),
    (388, 1),
    (389, 1),
    (390, 1),
    (391, 1),
    (392, 1),
    (393, 1),
    (394, 1),
    (395, 1),
    (396, 1),
    (397, 1),
    (398, 1),
    (399, 1),
    (400, 1),
    (401, 1),
    (402, 1),
    (403, 1),
    (404, 1),
    (405, 1),
    (406, 1),
    (407, 1),
    (408, 1),
    (409, 1),
    (410, 1),
    (411, 1),
    (412, 1),
    (413, 1),
    (414, 1),
    (415, 1),
    (416, 1),
    (417, 1),
    (418, 1),
    (419, 1),
    (420, 1),
    (421, 1),
    (422, 1),
    (423, 1),
    (424, 1),
    (425, 1),
    (426, 1),
    (427, 1),
    (428, 1),
    (429, 1),
    (430, 1),
    (431, 1),
    (432, 1),
    (433, 1),
    (434, 1),
    (435, 1),
    (436, 1),
    (437, 1),
    (438, 1),
    (439, 1),
    (440, 1),
    (441, 1),
    (442, 1),
    (443, 1),
    (444, 1),
    (445, 1),
    (446, 1),
    (447, 1),
    (448, 1),
    (449, 1),
    (450, 1),
    (451, 1),
    (452, 1),
    (453, 1),
    (454, 1),
    (455, 1),
    (456, 1),
    (457, 1),
    (458, 1),
    (459, 1),
    (460, 1),
    (461, 1),
    (462, 1),
    (463, 1),
    (464, 1),
    (465, 1),
    (466, 1),
    (467, 1),
    (468, 1),
    (469, 1),
    (470, 1),
    (471, 1),
    (472, 1),
    (473, 1),
    (474, 1),
    (475, 1),
    (476, 1),
    (477, 1),
    (478, 1),
    (479, 1),
    (480, 1),
    (481, 1),
    (482, 1),
    (483, 1),
    (484, 1),
    (485, 1),
    (486, 1),
    (487, 1),
    (488, 1),
    (489, 1),
    (490, 1),
    (491, 1),
    (492, 1),
    (493, 1),
    (494, 1),
    (495, 1),
    (496, 1),
    (497, 1),
    (498, 1),
    (499, 1),
    (500, 1),
    (501, 1),
    (502, 1),
    (503, 1),
    (504, 1),
    (505, 1),
    (506, 1),
    (507, 1),
    (508, 1),
    (509, 1),
    (510, 1),
    (511, 1),
    (512, 1),
    (513, 1),
    (514, 1),
    (515, 1),
    (516, 1),
    (517, 1),
    (518, 1),
    (519, 1),
    (520, 1),
    (521, 1),
    (522, 1),
    (523, 1),
    (524, 1),
    (525, 1),
    (526, 1),
    (527, 1),
    (528, 1),
    (529, 1),
    (530, 1),
    (531, 1),
    (532, 1),
    (533, 1),
    (534, 1),
    (535, 1),
    (536, 1),
    (537, 1),
    (538, 1),
    (539, 1),
    (540, 1),
    (541, 1),
    (542, 1),
    (543, 1),
    (544, 1),
    (545, 1),
    (546, 1),
    (547, 1),
    (548, 1),
    (549, 1),
    (550, 1),
    (551, 1),
    (552, 1),
    (553, 1),
    (554, 1),
    (555, 1),
    (556, 1),
    (557, 1),
    (558, 1),
    (559, 1),
    (560, 1),
    (561, 1),
    (562, 1),
    (563, 1),
    (564, 1),
    (565, 1),
    (566, 1),
    (567, 1),
    (568, 1),
    (569, 1),
    (570, 1),
    (571, 1),
    (572, 1),
    (573, 1),
    (574, 1),
    (575, 1),
    (576, 1),
    (577, 1),
    (578, 1),
    (579, 1),
    (580, 1),
    (581, 1),
    (582, 1),
    (583, 1),
    (584, 1),
    (585, 1),
    (586, 1),
    (587, 1),
    (588, 1),
    (589, 1),
    (590, 1),
    (591, 1),
    (592, 1),
    (593, 1),
    (594, 1),
    (595, 1),
    (596, 1),
    (597, 1),
    (598, 1),
    (599, 1),
    (600, 1),
    (601, 1),
    (602, 1),
    (603, 1),
    (604, 1),
    (605, 1),
    (606, 1),
    (607, 1),
    (608, 1),
    (609, 1),
    (610, 1),
    (611, 1),
    (612, 1),
    (613, 1),
    (614, 1),
    (615, 1),
    (616, 1),
    (617, 1),
    (618, 1),
    (619, 1),
    (620, 1),
    (621, 1),
    (622, 1),
    (623, 1),
    (624, 1),
    (625, 1),
    (626, 1),
    (627, 1),
    (628, 1),
    (629, 1),
    (630, 1),
    (631, 1),
    (632, 1),
    (633, 1),
    (634, 1),
    (635, 1),
    (636, 1),
    (637, 1),
    (638, 1),
    (639, 1),
    (640, 1),
    (641, 1),
    (642, 1),
    (643, 1),
    (644, 1),
    (645, 1),
    (646, 1),
    (647, 1),
    (648, 1),
    (649, 1),
    (650, 1),
    (651, 1),
    (652, 1),
    (653, 1),
    (654, 1),
    (655, 1),
    (656, 1),
    (657, 1),
    (658, 1),
    (659, 1),
    (660, 1),
    (661, 1),
    (662, 1),
    (663, 1),
    (664, 1),
    (665, 1),
    (666, 1),
    (667, 1),
    (668, 1),
    (669, 1),
    (670, 1),
    (671, 1),
    (672, 1),
    (673, 1),
    (674, 1),
    (675, 1),
    (676, 1),
    (677, 1),
    (678, 1),
    (679, 1),
    (680, 1),
    (681, 1),
    (682, 1),
    (683, 1),
    (684, 1),
    (685, 1),
    (686, 1),
    (687, 1),
    (688, 1),
    (689, 1),
    (690, 1),
    (691, 1),
    (692, 1),
    (693, 1),
    (694, 1),
    (695, 1),
    (696, 1),
    (697, 1),
    (698, 1),
    (699, 1),
    (700, 1),
    (701, 1),
    (702, 1),
    (703, 1),
    (704, 1),
    (705, 1),
    (706, 1),
    (707, 1),
    (708, 1),
    (709, 1),
    (710, 1),
    (711, 1),
    (712, 1),
    (713, 1),
    (714, 1),
    (715, 1),
    (716, 1),
    (717, 1),
    (718, 1),
    (719, 1),
    (720, 1),
    (721, 1),
    (722, 1),
    (723, 1),
    (724, 1),
    (725, 1),
    (726, 1),
    (727, 1),
    (728, 1),
    (729, 1),
    (730, 1),
    (731, 1),
    (732, 1),
    (733, 1),
    (734, 1),
    (735, 1),
    (736, 1),
    (737, 1),
    (738, 1),
    (739, 1),
    (740, 1),
    (741, 1),
    (742, 1),
    (743, 1),
    (744, 1),
    (745, 1),
    (746, 1),
    (747, 1),
    (748, 1),
    (749, 1),
    (750, 1),
    (751, 1),
    (752, 1),
    (753, 1),
    (754, 1),
    (755, 1),
    (756, 1),
    (757, 1),
    (758, 1),
    (759, 1),
    (760, 1),
    (761, 1),
    (762, 1),
    (763, 1),
    (764, 1),
    (765, 1),
    (766, 1),
    (767, 1),
    (768, 1),
    (769, 1),
    (770, 1),
    (771, 1),
    (772, 1),
    (773, 1),
    (774, 1),
    (775, 1),
    (776, 1),
    (777, 1),
    (778, 1),
    (779, 1),
    (780, 1),
    (781, 1),
    (782, 1),
    (783, 1),
    (784, 1),
    (785, 1),
    (786, 1),
    (787, 1),
    (788, 1),
    (789, 1),
    (790, 1),
    (791, 1),
    (792, 1),
    (793, 1),
    (794, 1),
    (795, 1),
    (796, 1),
    (797, 1),
    (798, 1),
    (799, 1),
    (800, 1),
    (801, 1),
    (802, 1),
    (803, 1),
    (804, 1),
    (805, 1),
    (806, 1),
    (807, 1),
    (808, 1),
    (809, 1),
    (810, 1),
    (811, 1),
    (812, 1),
    (813, 1),
    (814, 1),
    (815, 1),
    (816, 1),
    (817, 1),
    (818, 1),
    (819, 1),
    (820, 1),
    (821, 1),
    (822, 1),
    (823, 1),
    (824, 1),
    (825, 1),
    (826, 1),
    (827, 1),
    (828, 1),
    (829, 1),
    (830, 1),
    (831, 1),
    (832, 1),
    (833, 1),
    (834, 1),
    (835, 1),
    (836, 1),
    (837, 1),
    (838, 1),
    (839, 1),
    (840, 1),
    (841, 1),
    (842, 1),
    (843, 1),
    (844, 1),
    (845, 1),
    (846, 1),
    (847, 1),
    (848, 1),
    (849, 1),
    (850, 1),
    (851, 1),
    (852, 1),
    (853, 1),
    (854, 1),
    (855, 1),
    (856, 1),
    (857, 1),
    (858, 1),
    (859, 1),
    (860, 1),
    (861, 1),
    (862, 1),
    (863, 1),
    (864, 1),
    (865, 1),
    (866, 1),
    (867, 1),
    (868, 1),
    (869, 1),
    (870, 1),
    (871, 1),
    (872, 1),
    (873, 1),
    (874, 1),
    (875, 1),
    (876, 1),
    (877, 1),
    (878, 1),
    (879, 1),
    (880, 1),
    (881, 1),
    (882, 1),
    (883, 1),
    (884, 1),
    (885, 1),
    (886, 1),
    (887, 1),
    (888, 1),
    (889, 1),
    (890, 1),
    (891, 1),
    (892, 1),
    (893, 1),
    (894, 1),
    (895, 1),
    (896, 1),
    (897, 1),
    (898, 1),
    (899, 1),
    (900, 1),
    (901, 1),
    (902, 1),
    (903, 1),
    (904, 1),
    (905, 1),
    (906, 1),
    (907, 1),
    (908, 1),
    (909, 1),
    (910, 1),
    (911, 1),
    (912, 1),
    (913, 1),
    (914, 1),
    (915, 1),
    (916, 1),
    (917, 1),
    (918, 1),
    (919, 1),
    (920, 1),
    (921, 1),
    (922, 1),
    (923, 1),
    (924, 1),
    (925, 1),
    (926, 1),
    (927, 1),
    (928, 1),
    (929, 1),
    (930, 1),
    (931, 1),
    (932, 1),
    (933, 1),
    (934, 1),
    (935, 1),
    (936, 1),
    (937, 1),
    (938, 1),
    (939, 1),
    (940, 1),
    (941, 1),
    (942, 1),
    (943, 1),
    (944, 1),
    (945, 1),
    (946, 1),
    (947, 1),
    (948, 1),
    (949, 1),
    (950, 1),
    (951, 1),
    (952, 1),
    (953, 1),
    (954, 1),
    (955, 1),
    (956, 1),
    (957, 1),
    (958, 1),
    (959, 1),
    (960, 1),
    (961, 1),
    (962, 1),
    (963, 1),
    (964, 1),
    (965, 1),
    (966, 1),
    (967, 1),
    (968, 1),
    (969, 1),
    (970, 1),
    (971, 1),
    (972, 1),
    (973, 1),
    (974, 1),
    (975, 1),
    (976, 1),
    (977, 1),
    (978, 1),
    (979, 1),
    (980, 1),
    (981, 1),
    (982, 1),
    (983, 1),
    (984, 1),
    (985, 1),
    (986, 1),
    (987, 1),
    (988, 1),
    (989, 1),
    (990, 1),
    (991, 1),
    (992, 1),
    (993, 1),
    (994, 1),
    (995, 1),
    (996, 1),
    (997, 1),
    (998, 1),
    (999, 1),
    (1000, 1),
    (1001, 1),
    (1002, 1),
    (1003, 1),
    (1004, 1),
    (1005, 1),
    (1006, 1),
    (1007, 1),
    (1008, 1),
    (1009, 1),
    (1010, 1),
    (1011, 1),
    (1012, 1),
    (1013, 1),
    (1014, 1),
    (1015, 1),
    (1016, 1),
    (1017, 1),
    (1018, 1),
    (1019, 1),
    (1020, 1),
    (1021, 1),
    (1022, 1),
    (1023, 1),
    (1024, 1),
    (1025, 1),
    (1026, 1),
    (1027, 1),
    (1028, 1),
    (1029, 1),
    (1030, 1),
    (1031, 1),
    (1032, 1),
    (1033, 1),
    (1034, 1),
    (1035, 1),
    (1036, 1),
    (1037, 1),
    (1038, 1),
    (1039, 1),
    (1040, 1),
    (1041, 1),
    (1042, 1),
    (1043, 1),
    (1044, 1),
    (1045, 1),
    (1046, 1),
    (1047, 1),
    (1048, 1),
    (1049, 1),
    (1050, 1),
    (1051, 1),
    (1052, 1),
    (1053, 1),
    (1054, 1),
    (1055, 1),
    (1056, 1),
    (1057, 1),
    (1058, 1),
    (1059, 1),
    (1060, 1),
    (1061, 1),
    (1062, 1),
    (1063, 1),
    (1064, 1),
    (1065, 1),
    (1066, 1),
    (1067, 1),
    (1068, 1),
    (1069, 1),
    (1070, 1),
    (1071, 1),
    (1072, 1),
    (1073, 1),
    (1074, 1),
    (1075, 1),
    (1076, 1),
    (1077, 1),
    (1078, 1),
    (1079, 1),
    (1080, 1),
    (1081, 1),
    (1082, 1),
    (1083, 1),
    (1084, 1),
    (1085, 1),
    (1086, 1),
    (1087, 1),
    (1088, 1),
    (1089, 1),
    (1090, 1),
    (1091, 1),
    (1092, 1),
    (1093, 1),
    (1094, 1),
    (1095, 1),
    (1096, 1),
    (1097, 1),
    (1098, 1),
    (1099, 1),
    (1100, 1),
    (1101, 1),
    (1102, 1),
    (1103, 1),
    (1104, 1),
    (1105, 1),
    (1106, 1),
    (1107, 1),
    (1108, 1),
    (1109, 1),
    (1110, 1),
    (1111, 1),
    (1112, 1),
    (1113, 1),
    (1114, 1),
    (1115, 1),
    (1116, 1),
    (1117, 1),
    (1118, 1),
    (1119, 1),
    (1120, 1),
    (1121, 1),
    (1122, 1),
    (1123, 1),
    (1124, 1),
    (1125, 1),
    (1126, 1),
    (1127, 1),
    (1128, 1),
    (1129, 1),
    (1130, 1),
    (1131, 1),
    (1132, 1),
    (1133, 1),
    (1134, 1),
    (1135, 1),
    (1136, 1),
    (1137, 1),
    (1138, 1),
    (1139, 1),
    (1140, 1),
    (1141, 1),
    (1142, 1),
    (1143, 1),
    (1144, 1),
    (1145, 1),
    (1146, 1),
    (1147, 1),
    (1148, 1),
    (1149, 1),
    (1150, 1),
    (1151, 1),
    (1152, 1),
    (1153, 1),
    (1154, 1),
    (1155, 1),
    (1156, 1),
    (1157, 1),
    (1158, 1),
    (1159, 1),
    (1160, 1),
    (1161, 1),
    (1162, 1),
    (1163, 1),
    (1164, 1),
    (1165, 1),
    (1166, 1),
    (1167, 1),
    (1168, 1),
    (1169, 1),
    (1170, 1),
    (1171, 1),
    (1172, 1),
    (1173, 1),
    (1174, 1),
    (1175, 1),
    (1176, 1),
    (1177, 1),
    (1178, 1),
    (1179, 1),
    (1180, 1),
    (1181, 1),
    (1182, 1),
    (1183, 1),
    (1184, 1),
    (1185, 1),
    (1186, 1),
    (1187, 1),
    (1188, 1),
    (1189, 1),
    (1190, 1),
    (1191, 1),
    (1192, 1),
    (1193, 1),
    (1194, 1),
    (1195, 1),
    (1196, 1),
    (1197, 1),
    (1198, 1),
    (1199, 1),
    (1200, 1),
    (1201, 1),
    (1202, 1),
    (1203, 1),
    (1204, 1),
    (1205, 1),
    (1206, 1),
    (1207, 1),
    (1208, 1),
    (1209, 1),
    (1210, 1),
    (1211, 1),
    (1212, 1),
    (1213, 1),
    (1214, 1),
    (1215, 1),
    (1216, 1),
    (1217, 1),
    (1218, 1),
    (1219, 1),
    (1220, 1),
    (1221, 1),
    (1222, 1),
    (1223, 1),
    (1224, 1),
    (1225, 1),
    (1226, 1),
    (1227, 1),
    (1228, 1),
    (1229, 1),
    (1230, 1),
    (1231, 1),
    (1232, 1),
    (1233, 1),
    (1234, 1),
    (1235, 1),
    (1236, 1),
    (1237, 1),
    (1238, 1),
    (1239, 1),
    (1240, 1),
    (1241, 1),
    (1242, 1),
    (1243, 1),
    (1244, 1),
    (1245, 1),
    (1246, 1),
    (1247, 1),
    (1248, 1),
    (1249, 1),
    (1250, 1),
    (1251, 1),
    (1252, 1),
    (1253, 1),
    (1254, 1),
    (1255, 1),
    (1256, 1),
    (1257, 1),
    (1258, 1),
    (1259, 1),
    (1260, 1),
    (1261, 1),
    (1262, 1),
    (1263, 1),
    (1264, 1),
    (1265, 1),
    (1266, 1),
    (1267, 1),
    (1268, 1),
    (1269, 1),
    (1270, 1),
    (1271, 1),
    (1272, 1),
    (1273, 1),
    (1274, 1),
    (1275, 1),
    (1276, 1),
    (1277, 1),
    (1278, 1),
    (1279, 1),
    (1280, 1),
    (1281, 1),
    (1282, 1),
    (1283, 1),
    (1284, 1),
    (1285, 1),
    (1286, 1),
    (1287, 1),
    (1288, 1),
    (1289, 1),
    (1290, 1),
    (1291, 1),
    (1292, 1),
    (1293, 1),
    (1294, 1),
    (1295, 1),
    (1296, 1),
    (1297, 1),
    (1298, 1),
    (1299, 1),
    (1300, 1),
    (1301, 1),
    (1302, 1),
    (1303, 1),
    (1304, 1),
    (1305, 1),
    (1306, 1),
    (1307, 1),
    (1308, 1),
    (1309, 1),
    (1310, 1),
    (1311, 1),
    (1312, 1),
    (1313, 1),
    (1314, 1),
    (1315, 1),
    (1316, 1),
    (1317, 1),
    (1318, 1),
    (1319, 1),
    (1320, 1),
    (1321, 1),
    (1322, 1),
    (1323, 1),
    (1324, 1),
    (1325, 1),
    (1326, 1),
    (1327, 1),
    (1328, 1),
    (1329, 1),
    (1330, 1),
    (1331, 1),
    (1332, 1),
    (1333, 1),
    (1334, 1),
    (1335, 1),
    (1336, 1),
    (1337, 1),
    (1338, 1),
    (1339, 1),
    (1340, 1),
    (1341, 1),
    (1342, 1),
    (1343, 1),
    (1344, 1),
    (1345, 1),
    (1346, 1),
    (1347, 1),
    (1348, 1),
    (1349, 1),
    (1350, 1),
    (1351, 1),
    (1352, 1),
    (1353, 1),
    (1354, 1),
    (1355, 1),
    (1356, 1),
    (1357, 1),
    (1358, 1),
    (1359, 1),
    (1360, 1),
    (1361, 1),
    (1362, 1),
    (1363, 1),
    (1364, 1),
    (1365, 1),
    (1366, 1),
    (1367, 1),
    (1368, 1),
    (1369, 1),
    (1370, 1),
    (1371, 1),
    (1372, 1),
    (1373, 1),
    (1374, 1),
    (1375, 1),
    (1376, 1),
    (1377, 1),
    (1378, 1),
    (1379, 1),
    (1380, 1),
    (1381, 1),
    (1382, 1),
    (1383, 1),
    (1384, 1),
    (1385, 1),
    (1386, 1),
    (1387, 1),
    (1388, 1),
    (1389, 1),
    (1390, 1),
    (1391, 1),
    (1392, 1),
    (1393, 1),
    (1394, 1),
    (1395, 1),
    (1396, 1),
    (1397, 1),
    (1398, 1),
    (1399, 1),
    (1400, 1),
    (1401, 1),
    (1402, 1),
    (1403, 1),
    (1404, 1),
    (1405, 1),
    (1406, 1),
    (1407, 1),
    (1408, 1),
    (1409, 1),
    (1410, 1),
    (1411, 1),
    (1412, 1),
    (1413, 1),
    (1414, 1),
    (1415, 1),
    (1416, 1),
    (1417, 1),
    (1418, 1),
    (1419, 1),
    (1420, 1),
    (1421, 1),
    (1422, 1),
    (1423, 1),
    (1424, 1),
    (1425, 1),
    (1426, 1),
    (1427, 1),
    (1428, 1),
    (1429, 1),
    (1430, 1),
    (1431, 1),
    (1432, 1),
    (1433, 1),
    (1434, 1),
    (1435, 1),
    (1436, 1),
    (1437, 1),
    (1438, 1),
    (1439, 1),
    (1440, 1),
    (1441, 1),
    (1442, 1),
    (1443, 1),
    (1444, 1),
    (1445, 1),
    (1446, 1),
    (1447, 1),
    (1448, 1),
    (1449, 1),
    (1450, 1),
    (1451, 1),
    (1452, 1),
    (1453, 1),
    (1454, 1),
    (1455, 1),
    (1456, 1),
    (1457, 1),
    (1458, 1),
    (1459, 1),
    (1460, 1),
    (1461, 1),
    (1462, 1),
    (1463, 1),
    (1464, 1),
    (1465, 1),
    (1466, 1),
    (1467, 1),
    (1468, 1),
    (1469, 1),
    (1470, 1),
    (1471, 1),
    (1472, 1),
    (1473, 1),
    (1474, 1),
    (1475, 1),
    (1476, 1),
    (1477, 1),
    (1478, 1),
    (1479, 1),
    (1480, 1),
    (1481, 1),
    (1482, 1),
    (1483, 1),
    (1484, 1),
    (1485, 1),
    (1486, 1),
    (1487, 1),
    (1488, 1),
    (1489, 1),
    (1490, 1),
    (1491, 1),
    (1492, 1),
    (1493, 1),
    (1494, 1),
    (1495, 1),
    (1496, 1),
    (1497, 1),
    (1498, 1),
    (1499, 1),
    (1500, 1),
    (1501, 1),
    (1502, 1),
    (1503, 1),
    (1504, 1),
    (1505, 1),
    (1506, 1),
    (1507, 1),
    (1508, 1),
    (1509, 1),
    (1510, 1),
    (1511, 1),
    (1512, 1),
    (1513, 1),
    (1514, 1),
    (1515, 1),
    (1516, 1),
    (1517, 1),
    (1518, 1),
    (1519, 1),
    (1520, 1),
    (1521, 1),
    (1522, 1),
    (1523, 1),
    (1524, 1),
    (1525, 1),
    (1526, 1),
    (1527, 1),
    (1528, 1),
    (1529, 1),
    (1530, 1),
    (1531, 1),
    (1532, 1),
    (1533, 1),
    (1534, 1),
    (1535, 1),
    (1536, 1),
    (1537, 1),
    (1538, 1),
    (1539, 1),
    (1540, 1),
    (1541, 1),
    (1542, 1),
    (1543, 1),
    (1544, 1),
    (1545, 1),
    (1546, 1),
    (1547, 1),
    (1548, 1),
    (1549, 1),
    (1550, 1),
    (1551, 1),
    (1552, 1),
    (1553, 1),
    (1554, 1),
    (1555, 1),
    (1556, 1),
    (1557, 1),
    (1558, 1),
    (1559, 1),
    (1560, 1),
    (1561, 1),
    (1562, 1),
    (1563, 1),
    (1564, 1),
    (1565, 1),
    (1566, 1),
    (1567, 1),
    (1568, 1),
    (1569, 1),
    (1570, 1),
    (1571, 1),
    (1572, 1),
    (1573, 1),
    (1574, 1),
    (1575, 1),
    (1576, 1),
    (1577, 1),
    (1578, 1),
    (1579, 1),
    (1580, 1),
    (1581, 1),
    (1582, 1),
    (1583, 1),
    (1584, 1),
    (1585, 1),
    (1586, 1),
    (1587, 1),
    (1588, 1),
    (1589, 1),
    (1590, 1),
    (1591, 1),
    (1592, 1),
    (1593, 1),
    (1594, 1),
    (1595, 1),
    (1596, 1),
    (1597, 1),
    (1598, 1),
    (1599, 1),
    (1600, 1),
    (1601, 1),
    (1602, 1),
    (1603, 1),
    (1604, 1),
    (1605, 1),
    (1606, 1),
    (1607, 1),
    (1608, 1),
    (1609, 1),
    (1610, 1),
    (1611, 1),
    (1612, 1),
    (1613, 1),
    (1614, 1),
    (1615, 1),
    (1616, 1),
    (1617, 1),
    (1618, 1),
    (1619, 1),
    (1620, 1),
    (1621, 1),
    (1622, 1),
    (1623, 1),
    (1624, 1),
    (1625, 1),
    (1626, 1),
    (1627, 1),
    (1628, 1),
    (1629, 1),
    (1630, 1),
    (1631, 1),
    (1632, 1),
    (1633, 1),
    (1634, 1),
    (1635, 1),
    (1636, 1),
    (1637, 1),
    (1638, 1),
    (1639, 1),
    (1640, 1),
    (1641, 1),
    (1642, 1),
    (1643, 1),
    (1644, 1),
    (1645, 1),
    (1646, 1),
    (1647, 1),
    (1648, 1),
    (1649, 1),
    (1650, 1),
    (1651, 1),
    (1652, 1),
    (1653, 1),
    (1654, 1),
    (1655, 1),
    (1656, 1),
    (1657, 1),
    (1658, 1),
    (1659, 1),
    (1660, 1),
    (1661, 1),
    (1662, 1),
    (1663, 1),
    (1664, 1),
    (1665, 1),
    (1666, 1),
    (1667, 1),
    (1668, 1),
    (1669, 1),
    (1670, 1),
    (1671, 1),
    (1672, 1),
    (1673, 1),
    (1674, 1),
    (1675, 1),
    (1676, 1),
    (1677, 1),
    (1678, 1),
    (1679, 1),
    (1680, 1),
    (1681, 1),
    (1682, 1),
    (1683, 1),
    (1684, 1),
    (1685, 1),
    (1686, 1),
    (1687, 1),
    (1688, 1),
    (1689, 1),
    (1690, 1),
    (1691, 1),
    (1692, 1),
    (1693, 1),
    (1694, 1),
    (1695, 1),
    (1696, 1),
    (1697, 1),
    (1698, 1),
    (1699, 1),
    (1700, 1),
    (1701, 1),
    (1702, 1),
    (1703, 1),
    (1704, 1),
    (1705, 1),
    (1706, 1),
    (1707, 1),
    (1708, 1),
    (1709, 1),
    (1710, 1),
    (1711, 1),
    (1712, 1),
    (1713, 1),
    (1714, 1),
    (1715, 1),
    (1716, 1),
    (1717, 1),
    (1718, 1),
    (1719, 1),
    (1720, 1),
    (1721, 1),
    (1722, 1),
    (1723, 1),
    (1724, 1),
    (1725, 1),
    (1726, 1),
    (1727, 1),
    (1728, 1),
    (1729, 1),
    (1730, 1),
    (1731, 1),
    (1732, 1),
    (1733, 1),
    (1734, 1),
    (1735, 1),
    (1736, 1),
    (1737, 1),
    (1738, 1),
    (1739, 1),
    (1740, 1),
    (1741, 1),
    (1742, 1),
    (1743, 1),
    (1744, 1),
    (1745, 1),
    (1746, 1),
    (1747, 1),
    (1748, 1),
    (1749, 1),
    (1750, 1),
    (1751, 1),
    (1752, 1),
    (1753, 1),
    (1754, 1),
    (1755, 1),
    (1756, 1),
    (1757, 1),
    (1758, 1),
    (1759, 1),
    (1760, 1),
    (1761, 1),
    (1762, 1),
    (1763, 1),
    (1764, 1),
    (1765, 1),
    (1766, 1),
    (1767, 1),
    (1768, 1),
    (1769, 1),
    (1770, 1),
    (1771, 1),
    (1772, 1),
    (1773, 1),
    (1774, 1),
    (1775, 1),
    (1776, 1),
    (1777, 1),
    (1778, 1),
    (1779, 1),
    (1780, 1),
    (1781, 1),
    (1782, 1),
    (1783, 1),
    (1784, 1),
    (1785, 1),
    (1786, 1),
    (1787, 1),
    (1788, 1),
    (1789, 1),
    (1790, 1),
    (1791, 1),
    (1792, 1),
    (1793, 1),
    (1794, 1),
    (1795, 1),
    (1796, 1),
    (1797, 1),
    (1798, 1),
    (1799, 1),
    (1800, 1),
    (1801, 1),
    (1802, 1),
    (1803, 1),
    (1804, 1),
    (1805, 1),
    (1806, 1),
    (1807, 1),
    (1808, 1),
    (1809, 1),
    (1810, 1),
    (1811, 1),
    (1812, 1),
    (1813, 1),
    (1814, 1),
    (1815, 1),
    (1816, 1),
    (1817, 1),
    (1818, 1),
    (1819, 1),
    (1820, 1),
    (1821, 1),
    (1822, 1),
    (1823, 1),
    (1824, 1),
    (1825, 1),
    (1826, 1),
    (1827, 1),
    (1828, 1),
    (1829, 1),
    (1830, 1),
    (1831, 1),
    (1832, 1),
    (1833, 1),
    (1834, 1),
    (1835, 1),
    (1836, 1),
    (1837, 1),
    (1838, 1),
    (1839, 1),
    (1840, 1),
    (1841, 1),
    (1842, 1),
    (1843, 1),
    (1844, 1),
    (1845, 1),
    (1846, 1),
    (1847, 1),
    (1848, 1),
    (1849, 1),
    (1850, 1),
    (1851, 1),
    (1852, 1),
    (1853, 1),
    (1854, 1),
    (1855, 1),
    (1856, 1),
    (1857, 1),
    (1858, 1),
    (1859, 1),
    (1860, 1),
    (1861, 1),
    (1862, 1),
    (1863, 1),
    (1864, 1),
    (1865, 1),
    (1866, 1),
    (1867, 1),
    (1868, 1),
    (1869, 1),
    (1870, 1),
    (1871, 1),
    (1872, 1),
    (1873, 1),
    (1874, 1),
    (1875, 1),
    (1876, 1),
    (1877, 1),
    (1878, 1),
    (1879, 1),
    (1880, 1),
    (1881, 1),
    (1882, 1),
    (1883, 1),
    (1884, 1),
    (1885, 1),
    (1886, 1),
    (1887, 1),
    (1888, 1),
    (1889, 1),
    (1890, 1),
    (1891, 1),
    (1892, 1),
    (1893, 1),
    (1894, 1),
    (1895, 1),
    (1896, 1),
    (1897, 1),
    (1898, 1),
    (1899, 1),
    (1900, 1),
    (1901, 1),
    (1902, 1),
    (1903, 1),
    (1904, 1),
    (1905, 1),
    (1906, 1),
    (1907, 1),
    (1908, 1),
    (1909, 1),
    (1910, 1),
    (1911, 1),
    (1912, 1),
    (1913, 1),
    (1914, 1),
    (1915, 1),
    (1916, 1),
    (1917, 1),
    (1918, 1),
    (1919, 1),
    (1920, 1),
    (1921, 1),
    (1922, 1),
    (1923, 1),
    (1924, 1),
    (1925, 1),
    (1926, 1),
    (1927, 1),
    (1928, 1),
    (1929, 1),
    (1930, 1),
    (1931, 1),
    (1932, 1),
    (1933, 1),
    (1934, 1),
    (1935, 1),
    (1936, 1),
    (1937, 1),
    (1938, 1),
    (1939, 1),
    (1940, 1),
    (1941, 1),
    (1942, 1),
    (1943, 1),
    (1944, 1),
    (1945, 1),
    (1946, 1),
    (1947, 1),
    (1948, 1),
    (1949, 1),
    (1950, 1),
    (1951, 1),
    (1952, 1),
    (1953, 1),
    (1954, 1),
    (1955, 1),
    (1956, 1),
    (1957, 1),
    (1958, 1),
    (1959, 1),
    (1960, 1),
    (1961, 1),
    (1962, 1),
    (1963, 1),
    (1964, 1),
    (1965, 1),
    (1966, 1),
    (1967, 1),
    (1968, 1),
    (1969, 1),
    (1970, 1),
    (1971, 1),
    (1972, 1),
    (1973, 1),
    (1974, 1),
    (1975, 1),
    (1976, 1),
    (1977, 1),
    (1978, 1),
    (1979, 1),
    (1980, 1),
    (1981, 1),
    (1982, 1),
    (1983, 1),
    (1984, 1),
    (1985, 1),
    (1986, 1),
    (1987, 1),
    (1988, 1),
    (1989, 1),
    (1990, 1),
    (1991, 1),
    (1992, 1),
    (1993, 1),
    (1994, 1),
    (1995, 1),
    (1996, 1),
    (1997, 1),
    (1998, 1),
    (1999, 1),
    (2000, 1),
    (2001, 2),
    (2002, 2),
    (2003, 2),
    (2004, 2),
    (2005, 2),
    (2006, 2),
    (2007, 2),
    (2008, 2),
    (2009, 2),
    (2010, 2),
    (2011, 2),
    (2012, 2),
    (2013, 2),
    (2014, 2),
    (2015, 2),
    (2016, 2),
    (2017, 2),
    (2018, 2),
    (2019, 2),
    (2020, 2),
    (2021, 2),
    (2022, 2),
    (2023, 2),
    (2024, 2),
    (2025, 2),
    (2026, 2),
    (2027, 2),
    (2028, 2),
    (2029, 2),
    (2030, 2),
    (2031, 2),
    (2032, 2),
    (2033, 2),
    (2034, 2),
    (2035, 2),
    (2036, 2),
    (2037, 2),
    (2038, 2),
    (2039, 2),
    (2040, 2),
    (2041, 2),
    (2042, 2),
    (2043, 2),
    (2044, 2),
    (2045, 2),
    (2046, 2),
    (2047, 2),
    (2048, 2),
    (2049, 2),
    (2050, 2),
    (2051, 2),
    (2052, 2),
    (2053, 2),
    (2054, 2),
    (2055, 2),
    (2056, 2),
    (2057, 2),
    (2058, 2),
    (2059, 2),
    (2060, 2),
    (2061, 2),
    (2062, 2),
    (2063, 2),
    (2064, 2),
    (2065, 2),
    (2066, 2),
    (2067, 2),
    (2068, 2),
    (2069, 2),
    (2070, 2),
    (2071, 2),
    (2072, 2),
    (2073, 2),
    (2074, 2),
    (2075, 2),
    (2076, 2),
    (2077, 2),
    (2078, 2),
    (2079, 2),
    (2080, 2),
    (2081, 2),
    (2082, 2),
    (2083, 2),
    (2084, 2),
    (2085, 2),
    (2086, 2),
    (2087, 2),
    (2088, 2),
    (2089, 2),
    (2090, 2),
    (2091, 2),
    (2092, 2),
    (2093, 2),
    (2094, 2),
    (2095, 2),
    (2096, 2),
    (2097, 2),
    (2098, 2),
    (2099, 2),
    (2100, 2),
    (2101, 2),
    (2102, 2),
    (2103, 2),
    (2104, 2),
    (2105, 2),
    (2106, 2),
    (2107, 2),
    (2108, 2),
    (2109, 2),
    (2110, 2),
    (2111, 2),
    (2112, 2),
    (2113, 2),
    (2114, 2),
    (2115, 2),
    (2116, 2),
    (2117, 2),
    (2118, 2),
    (2119, 2),
    (2120, 2),
    (2121, 2),
    (2122, 2),
    (2123, 2),
    (2124, 2),
    (2125, 2),
    (2126, 2),
    (2127, 2),
    (2128, 2),
    (2129, 2),
    (2130, 2),
    (2131, 2),
    (2132, 2),
    (2133, 2),
    (2134, 2),
    (2135, 2),
    (2136, 2),
    (2137, 2),
    (2138, 2),
    (2139, 2),
    (2140, 2),
    (2141, 2),
    (2142, 2),
    (2143, 2),
    (2144, 2),
    (2145, 2),
    (2146, 2),
    (2147, 2),
    (2148, 2),
    (2149, 2),
    (2150, 2),
    (2151, 2),
    (2152, 2),
    (2153, 2),
    (2154, 2),
    (2155, 2),
    (2156, 2),
    (2157, 2),
    (2158, 2),
    (2159, 2),
    (2160, 2),
    (2161, 2),
    (2162, 2),
    (2163, 2),
    (2164, 2),
    (2165, 2),
    (2166, 2),
    (2167, 2),
    (2168, 2),
    (2169, 2),
    (2170, 2),
    (2171, 2),
    (2172, 2),
    (2173, 2),
    (2174, 2),
    (2175, 2),
    (2176, 2),
    (2177, 2),
    (2178, 2),
    (2179, 2),
    (2180, 2),
    (2181, 2),
    (2182, 2),
    (2183, 2),
    (2184, 2),
    (2185, 2),
    (2186, 2),
    (2187, 2),
    (2188, 2),
    (2189, 2),
    (2190, 2),
    (2191, 2),
    (2192, 2),
    (2193, 2),
    (2194, 2),
    (2195, 2),
    (2196, 2),
    (2197, 2),
    (2198, 2),
    (2199, 2),
    (2200, 2),
    (2201, 2),
    (2202, 2),
    (2203, 2),
    (2204, 2),
    (2205, 2),
    (2206, 2),
    (2207, 2),
    (2208, 2),
    (2209, 2),
    (2210, 2),
    (2211, 2),
    (2212, 2),
    (2213, 2),
    (2214, 2),
    (2215, 2),
    (2216, 2),
    (2217, 2),
    (2218, 2),
    (2219, 2),
    (2220, 2),
    (2221, 2),
    (2222, 2),
    (2223, 2),
    (2224, 2),
    (2225, 2),
    (2226, 2),
    (2227, 2),
    (2228, 2),
    (2229, 2),
    (2230, 2),
    (2231, 2),
    (2232, 2),
    (2233, 2),
    (2234, 2),
    (2235, 2),
    (2236, 2),
    (2237, 2),
    (2238, 2),
    (2239, 2),
    (2240, 2),
    (2241, 2),
    (2242, 2),
    (2243, 2),
    (2244, 2),
    (2245, 2),
    (2246, 2),
    (2247, 2),
    (2248, 2),
    (2249, 2),
    (2250, 2),
    (2251, 2),
    (2252, 2),
    (2253, 2),
    (2254, 2),
    (2255, 2),
    (2256, 2),
    (2257, 2),
    (2258, 2),
    (2259, 2),
    (2260, 2),
    (2261, 2),
    (2262, 2),
    (2263, 2),
    (2264, 2),
    (2265, 2),
    (2266, 2),
    (2267, 2),
    (2268, 2),
    (2269, 2),
    (2270, 2),
    (2271, 2),
    (2272, 2),
    (2273, 2),
    (2274, 2),
    (2275, 2),
    (2276, 2),
    (2277, 2),
    (2278, 2),
    (2279, 2),
    (2280, 2),
    (2281, 2),
    (2282, 2),
    (2283, 2),
    (2284, 2),
    (2285, 2),
    (2286, 2),
    (2287, 2),
    (2288, 2),
    (2289, 2),
    (2290, 2),
    (2291, 2),
    (2292, 2),
    (2293, 2),
    (2294, 2),
    (2295, 2),
    (2296, 2),
    (2297, 2),
    (2298, 2),
    (2299, 2),
    (2300, 2),
    (2301, 2),
    (2302, 2),
    (2303, 2),
    (2304, 2),
    (2305, 2),
    (2306, 2),
    (2307, 2),
    (2308, 2),
    (2309, 2),
    (2310, 2),
    (2311, 2),
    (2312, 2),
    (2313, 2),
    (2314, 2),
    (2315, 2),
    (2316, 2),
    (2317, 2),
    (2318, 2),
    (2319, 2),
    (2320, 2),
    (2321, 2),
    (2322, 2),
    (2323, 2),
    (2324, 2),
    (2325, 2),
    (2326, 2),
    (2327, 2),
    (2328, 2),
    (2329, 2),
    (2330, 2),
    (2331, 2),
    (2332, 2),
    (2333, 2),
    (2334, 2),
    (2335, 2),
    (2336, 2),
    (2337, 2),
    (2338, 2),
    (2339, 2),
    (2340, 2),
    (2341, 2),
    (2342, 2),
    (2343, 2),
    (2344, 2),
    (2345, 2),
    (2346, 2),
    (2347, 2),
    (2348, 2),
    (2349, 2),
    (2350, 2),
    (2351, 2),
    (2352, 2),
    (2353, 2),
    (2354, 2),
    (2355, 2),
    (2356, 2),
    (2357, 2),
    (2358, 2),
    (2359, 2),
    (2360, 2),
    (2361, 2),
    (2362, 2),
    (2363, 2),
    (2364, 2),
    (2365, 2),
    (2366, 2),
    (2367, 2),
    (2368, 2),
    (2369, 2),
    (2370, 2),
    (2371, 2),
    (2372, 2),
    (2373, 2),
    (2374, 2),
    (2375, 2),
    (2376, 2),
    (2377, 2),
    (2378, 2),
    (2379, 2),
    (2380, 2),
    (2381, 2),
    (2382, 2),
    (2383, 2),
    (2384, 2),
    (2385, 2),
    (2386, 2),
    (2387, 2),
    (2388, 2),
    (2389, 2),
    (2390, 2),
    (2391, 2),
    (2392, 2),
    (2393, 2),
    (2394, 2),
    (2395, 2),
    (2396, 2),
    (2397, 2),
    (2398, 2),
    (2399, 2),
    (2400, 2),
    (2401, 2),
    (2402, 2),
    (2403, 2),
    (2404, 2),
    (2405, 2),
    (2406, 2),
    (2407, 2),
    (2408, 2),
    (2409, 2),
    (2410, 2),
    (2411, 2),
    (2412, 2),
    (2413, 2),
    (2414, 2),
    (2415, 2),
    (2416, 2),
    (2417, 2),
    (2418, 2),
    (2419, 2),
    (2420, 2),
    (2421, 2),
    (2422, 2),
    (2423, 2),
    (2424, 2),
    (2425, 2),
    (2426, 2),
    (2427, 2),
    (2428, 2),
    (2429, 2),
    (2430, 2),
    (2431, 2),
    (2432, 2),
    (2433, 2),
    (2434, 2),
    (2435, 2),
    (2436, 2),
    (2437, 2),
    (2438, 2),
    (2439, 2),
    (2440, 2),
    (2441, 2),
    (2442, 2),
    (2443, 2),
    (2444, 2),
    (2445, 2),
    (2446, 2),
    (2447, 2),
    (2448, 2),
    (2449, 2),
    (2450, 2),
    (2451, 2),
    (2452, 2),
    (2453, 2),
    (2454, 2),
    (2455, 2),
    (2456, 2),
    (2457, 2),
    (2458, 2),
    (2459, 2),
    (2460, 2),
    (2461, 2),
    (2462, 2),
    (2463, 2),
    (2464, 2),
    (2465, 2),
    (2466, 2),
    (2467, 2),
    (2468, 2),
    (2469, 2),
    (2470, 2),
    (2471, 2),
    (2472, 2),
    (2473, 2),
    (2474, 2),
    (2475, 2),
    (2476, 2),
    (2477, 2),
    (2478, 2),
    (2479, 2),
    (2480, 2),
    (2481, 2),
    (2482, 2),
    (2483, 2),
    (2484, 2),
    (2485, 2),
    (2486, 2),
    (2487, 2),
    (2488, 2),
    (2489, 2),
    (2490, 2),
    (2491, 2),
    (2492, 2),
    (2493, 2),
    (2494, 2),
    (2495, 2),
    (2496, 2),
    (2497, 2),
    (2498, 2),
    (2499, 2),
    (2500, 2),
    (2501, 2),
    (2502, 2),
    (2503, 2),
    (2504, 2),
    (2505, 2),
    (2506, 2),
    (2507, 2),
    (2508, 2),
    (2509, 2),
    (2510, 2),
    (2511, 2),
    (2512, 2),
    (2513, 2),
    (2514, 2),
    (2515, 2),
    (2516, 2),
    (2517, 2),
    (2518, 2),
    (2519, 2),
    (2520, 2),
    (2521, 2),
    (2522, 2),
    (2523, 2),
    (2524, 2),
    (2525, 2),
    (2526, 2),
    (2527, 2),
    (2528, 2),
    (2529, 2),
    (2530, 2),
    (2531, 2),
    (2532, 2),
    (2533, 2),
    (2534, 2),
    (2535, 2),
    (2536, 2),
    (2537, 2),
    (2538, 2),
    (2539, 2),
    (2540, 2),
    (2541, 2),
    (2542, 2),
    (2543, 2),
    (2544, 2),
    (2545, 2),
    (2546, 2),
    (2547, 2),
    (2548, 2),
    (2549, 2),
    (2550, 2),
    (2551, 2),
    (2552, 2),
    (2553, 2),
    (2554, 2),
    (2555, 2),
    (2556, 2),
    (2557, 2),
    (2558, 2),
    (2559, 2),
    (2560, 2),
    (2561, 2),
    (2562, 2),
    (2563, 2),
    (2564, 2),
    (2565, 2),
    (2566, 2),
    (2567, 2),
    (2568, 2),
    (2569, 2),
    (2570, 2),
    (2571, 2),
    (2572, 2),
    (2573, 2),
    (2574, 2),
    (2575, 2),
    (2576, 2),
    (2577, 2),
    (2578, 2),
    (2579, 2),
    (2580, 2),
    (2581, 2),
    (2582, 2),
    (2583, 2),
    (2584, 2),
    (2585, 2),
    (2586, 2),
    (2587, 2),
    (2588, 2),
    (2589, 2),
    (2590, 2),
    (2591, 2),
    (2592, 2),
    (2593, 2),
    (2594, 2),
    (2595, 2),
    (2596, 2),
    (2597, 2),
    (2598, 2),
    (2599, 2),
    (2600, 2),
    (2601, 2),
    (2602, 2),
    (2603, 2),
    (2604, 2),
    (2605, 2),
    (2606, 2),
    (2607, 2),
    (2608, 2),
    (2609, 2),
    (2610, 2),
    (2611, 2),
    (2612, 2),
    (2613, 2),
    (2614, 2),
    (2615, 2),
    (2616, 2),
    (2617, 2),
    (2618, 2),
    (2619, 2),
    (2620, 2),
    (2621, 2),
    (2622, 2),
    (2623, 2),
    (2624, 2),
    (2625, 2),
    (2626, 2),
    (2627, 2),
    (2628, 2),
    (2629, 2),
    (2630, 2),
    (2631, 2),
    (2632, 2),
    (2633, 2),
    (2634, 2),
    (2635, 2),
    (2636, 2),
    (2637, 2),
    (2638, 2),
    (2639, 2),
    (2640, 2),
    (2641, 2),
    (2642, 2),
    (2643, 2),
    (2644, 2),
    (2645, 2),
    (2646, 2),
    (2647, 2),
    (2648, 2),
    (2649, 2),
    (2650, 2),
    (2651, 2),
    (2652, 2),
    (2653, 2),
    (2654, 2),
    (2655, 2),
    (2656, 2),
    (2657, 2),
    (2658, 2),
    (2659, 2),
    (2660, 2),
    (2661, 2),
    (2662, 2),
    (2663, 2),
    (2664, 2),
    (2665, 2),
    (2666, 2),
    (2667, 2),
    (2668, 2),
    (2669, 2),
    (2670, 2),
    (2671, 2),
    (2672, 2),
    (2673, 2),
    (2674, 2),
    (2675, 2),
    (2676, 2),
    (2677, 2),
    (2678, 2),
    (2679, 2),
    (2680, 2),
    (2681, 2),
    (2682, 2),
    (2683, 2),
    (2684, 2),
    (2685, 2),
    (2686, 2),
    (2687, 2),
    (2688, 2),
    (2689, 2),
    (2690, 2),
    (2691, 2),
    (2692, 2),
    (2693, 2),
    (2694, 2),
    (2695, 2),
    (2696, 2),
    (2697, 2),
    (2698, 2),
    (2699, 2),
    (2700, 2),
    (2701, 2),
    (2702, 2),
    (2703, 2),
    (2704, 2),
    (2705, 2),
    (2706, 2),
    (2707, 2),
    (2708, 2),
    (2709, 2),
    (2710, 2),
    (2711, 2),
    (2712, 2),
    (2713, 2),
    (2714, 2),
    (2715, 2),
    (2716, 2),
    (2717, 2),
    (2718, 2),
    (2719, 2),
    (2720, 2),
    (2721, 2),
    (2722, 2),
    (2723, 2),
    (2724, 2),
    (2725, 2),
    (2726, 2),
    (2727, 2),
    (2728, 2),
    (2729, 2),
    (2730, 2),
    (2731, 2),
    (2732, 2),
    (2733, 2),
    (2734, 2),
    (2735, 2),
    (2736, 2),
    (2737, 2),
    (2738, 2),
    (2739, 2),
    (2740, 2),
    (2741, 2),
    (2742, 2),
    (2743, 2),
    (2744, 2),
    (2745, 2),
    (2746, 2),
    (2747, 2),
    (2748, 2),
    (2749, 2),
    (2750, 2),
    (2751, 2),
    (2752, 2),
    (2753, 2),
    (2754, 2),
    (2755, 2),
    (2756, 2),
    (2757, 2),
    (2758, 2),
    (2759, 2),
    (2760, 2),
    (2761, 2),
    (2762, 2),
    (2763, 2),
    (2764, 2),
    (2765, 2),
    (2766, 2),
    (2767, 2),
    (2768, 2),
    (2769, 2),
    (2770, 2),
    (2771, 2),
    (2772, 2),
    (2773, 2),
    (2774, 2),
    (2775, 2),
    (2776, 2),
    (2777, 2),
    (2778, 2),
    (2779, 2),
    (2780, 2),
    (2781, 2),
    (2782, 2),
    (2783, 2),
    (2784, 2),
    (2785, 2),
    (2786, 2),
    (2787, 2),
    (2788, 2),
    (2789, 2),
    (2790, 2),
    (2791, 2),
    (2792, 2),
    (2793, 2),
    (2794, 2),
    (2795, 2),
    (2796, 2),
    (2797, 2),
    (2798, 2),
    (2799, 2),
    (2800, 2),
    (2801, 2),
    (2802, 2),
    (2803, 2),
    (2804, 2),
    (2805, 2),
    (2806, 2),
    (2807, 2),
    (2808, 2),
    (2809, 2),
    (2810, 2),
    (2811, 2),
    (2812, 2),
    (2813, 2),
    (2814, 2),
    (2815, 2),
    (2816, 2),
    (2817, 2),
    (2818, 2),
    (2819, 2),
    (2820, 2),
    (2821, 2),
    (2822, 2),
    (2823, 2),
    (2824, 2),
    (2825, 2),
    (2826, 2),
    (2827, 2),
    (2828, 2),
    (2829, 2),
    (2830, 2),
    (2831, 2),
    (2832, 2),
    (2833, 2),
    (2834, 2),
    (2835, 2),
    (2836, 2),
    (2837, 2),
    (2838, 2),
    (2839, 2),
    (2840, 2),
    (2841, 2),
    (2842, 2),
    (2843, 2),
    (2844, 2),
    (2845, 2),
    (2846, 2),
    (2847, 2),
    (2848, 2),
    (2849, 2),
    (2850, 2),
    (2851, 2),
    (2852, 2),
    (2853, 2),
    (2854, 2),
    (2855, 2),
    (2856, 2),
    (2857, 2),
    (2858, 2),
    (2859, 2),
    (2860, 2),
    (2861, 2),
    (2862, 2),
    (2863, 2),
    (2864, 2),
    (2865, 2),
    (2866, 2),
    (2867, 2),
    (2868, 2),
    (2869, 2),
    (2870, 2),
    (2871, 2),
    (2872, 2),
    (2873, 2),
    (2874, 2),
    (2875, 2),
    (2876, 2),
    (2877, 2),
    (2878, 2),
    (2879, 2),
    (2880, 2),
    (2881, 2),
    (2882, 2),
    (2883, 2),
    (2884, 2),
    (2885, 2),
    (2886, 2),
    (2887, 2),
    (2888, 2),
    (2889, 2),
    (2890, 2),
    (2891, 2),
    (2892, 2),
    (2893, 2),
    (2894, 2),
    (2895, 2),
    (2896, 2),
    (2897, 2),
    (2898, 2),
    (2899, 2),
    (2900, 2),
    (2901, 2),
    (2902, 2),
    (2903, 2),
    (2904, 2),
    (2905, 2),
    (2906, 2),
    (2907, 2),
    (2908, 2),
    (2909, 2),
    (2910, 2),
    (2911, 2),
    (2912, 2),
    (2913, 2),
    (2914, 2),
    (2915, 2),
    (2916, 2),
    (2917, 2),
    (2918, 2),
    (2919, 2),
    (2920, 2),
    (2921, 2),
    (2922, 2),
    (2923, 2),
    (2924, 2),
    (2925, 2),
    (2926, 2),
    (2927, 2),
    (2928, 2),
    (2929, 2),
    (2930, 2),
    (2931, 2),
    (2932, 2),
    (2933, 2),
    (2934, 2),
    (2935, 2),
    (2936, 2),
    (2937, 2),
    (2938, 2),
    (2939, 2),
    (2940, 2),
    (2941, 2),
    (2942, 2),
    (2943, 2),
    (2944, 2),
    (2945, 2),
    (2946, 2),
    (2947, 2),
    (2948, 2),
    (2949, 2),
    (2950, 2),
    (2951, 2),
    (2952, 2),
    (2953, 2),
    (2954, 2),
    (2955, 2),
    (2956, 2),
    (2957, 2),
    (2958, 2),
    (2959, 2),
    (2960, 2),
    (2961, 2),
    (2962, 2),
    (2963, 2),
    (2964, 2),
    (2965, 2),
    (2966, 2),
    (2967, 2),
    (2968, 2),
    (2969, 2),
    (2970, 2),
    (2971, 2),
    (2972, 2),
    (2973, 2),
    (2974, 2),
    (2975, 2),
    (2976, 2),
    (2977, 2),
    (2978, 2),
    (2979, 2),
    (2980, 2),
    (2981, 2),
    (2982, 2),
    (2983, 2),
    (2984, 2),
    (2985, 2),
    (2986, 2),
    (2987, 2),
    (2988, 2),
    (2989, 2),
    (2990, 2),
    (2991, 2),
    (2992, 2),
    (2993, 2),
    (2994, 2),
    (2995, 2),
    (2996, 2),
    (2997, 2),
    (2998, 2),
    (2999, 2),
    (3000, 2),
    (3001, 2),
    (3002, 2),
    (3003, 2),
    (3004, 2),
    (3005, 2),
    (3006, 2),
    (3007, 2),
    (3008, 2),
    (3009, 2),
    (3010, 2),
    (3011, 2),
    (3012, 2),
    (3013, 2),
    (3014, 2),
    (3015, 2),
    (3016, 2),
    (3017, 2),
    (3018, 2),
    (3019, 2),
    (3020, 2),
    (3021, 2),
    (3022, 2),
    (3023, 2),
    (3024, 2),
    (3025, 2),
    (3026, 2),
    (3027, 2),
    (3028, 2),
    (3029, 2),
    (3030, 2),
    (3031, 2),
    (3032, 2),
    (3033, 2),
    (3034, 2),
    (3035, 2),
    (3036, 2),
    (3037, 2),
    (3038, 2),
    (3039, 2),
    (3040, 2),
    (3041, 2),
    (3042, 2),
    (3043, 2),
    (3044, 2),
    (3045, 2),
    (3046, 2),
    (3047, 2),
    (3048, 2),
    (3049, 2),
    (3050, 2),
    (3051, 2),
    (3052, 2),
    (3053, 2),
    (3054, 2),
    (3055, 2),
    (3056, 2),
    (3057, 2),
    (3058, 2),
    (3059, 2),
    (3060, 2),
    (3061, 2),
    (3062, 2),
    (3063, 2),
    (3064, 2),
    (3065, 2),
    (3066, 2),
    (3067, 2),
    (3068, 2),
    (3069, 2),
    (3070, 2),
    (3071, 2),
    (3072, 2),
    (3073, 2),
    (3074, 2),
    (3075, 2),
    (3076, 2),
    (3077, 2),
    (3078, 2),
    (3079, 2),
    (3080, 2),
    (3081, 2),
    (3082, 2),
    (3083, 2),
    (3084, 2),
    (3085, 2),
    (3086, 2),
    (3087, 2),
    (3088, 2),
    (3089, 2),
    (3090, 2),
    (3091, 2),
    (3092, 2),
    (3093, 2),
    (3094, 2),
    (3095, 2),
    (3096, 2),
    (3097, 2),
    (3098, 2),
    (3099, 2),
    (3100, 2),
    (3101, 2),
    (3102, 2),
    (3103, 2),
    (3104, 2),
    (3105, 2),
    (3106, 2),
    (3107, 2),
    (3108, 2),
    (3109, 2),
    (3110, 2),
    (3111, 2),
    (3112, 2),
    (3113, 2),
    (3114, 2),
    (3115, 2),
    (3116, 2),
    (3117, 2),
    (3118, 2),
    (3119, 2),
    (3120, 2),
    (3121, 2),
    (3122, 2),
    (3123, 2),
    (3124, 2),
    (3125, 2),
    (3126, 2),
    (3127, 2),
    (3128, 2),
    (3129, 2),
    (3130, 2),
    (3131, 2),
    (3132, 2),
    (3133, 2),
    (3134, 2),
    (3135, 2),
    (3136, 2),
    (3137, 2),
    (3138, 2),
    (3139, 2),
    (3140, 2),
    (3141, 2),
    (3142, 2),
    (3143, 2),
    (3144, 2),
    (3145, 2),
    (3146, 2),
    (3147, 2),
    (3148, 2),
    (3149, 2),
    (3150, 2),
    (3151, 2),
    (3152, 2),
    (3153, 2),
    (3154, 2),
    (3155, 2),
    (3156, 2),
    (3157, 2),
    (3158, 2),
    (3159, 2),
    (3160, 2),
    (3161, 2),
    (3162, 2),
    (3163, 2),
    (3164, 2),
    (3165, 2),
    (3166, 2),
    (3167, 2),
    (3168, 2),
    (3169, 2),
    (3170, 2),
    (3171, 2),
    (3172, 2),
    (3173, 2),
    (3174, 2),
    (3175, 2),
    (3176, 2),
    (3177, 2),
    (3178, 2),
    (3179, 2),
    (3180, 2),
    (3181, 2),
    (3182, 2),
    (3183, 2),
    (3184, 2),
    (3185, 2),
    (3186, 2),
    (3187, 2),
    (3188, 2),
    (3189, 2),
    (3190, 2),
    (3191, 2),
    (3192, 2),
    (3193, 2),
    (3194, 2),
    (3195, 2),
    (3196, 2),
    (3197, 2),
    (3198, 2),
    (3199, 2),
    (3200, 2),
    (3201, 2),
    (3202, 2),
    (3203, 2),
    (3204, 2),
    (3205, 2),
    (3206, 2),
    (3207, 2),
    (3208, 2),
    (3209, 2),
    (3210, 2),
    (3211, 2),
    (3212, 2),
    (3213, 2),
    (3214, 2),
    (3215, 2),
    (3216, 2),
    (3217, 2),
    (3218, 2),
    (3219, 2),
    (3220, 2),
    (3221, 2),
    (3222, 2),
    (3223, 2),
    (3224, 2),
    (3225, 2),
    (3226, 2),
    (3227, 2),
    (3228, 2),
    (3229, 2),
    (3230, 2),
    (3231, 2),
    (3232, 2),
    (3233, 2),
    (3234, 2),
    (3235, 2),
    (3236, 2),
    (3237, 2),
    (3238, 2),
    (3239, 2),
    (3240, 2),
    (3241, 2),
    (3242, 2),
    (3243, 2),
    (3244, 2),
    (3245, 2),
    (3246, 2),
    (3247, 2),
    (3248, 2),
    (3249, 2),
    (3250, 2),
    (3251, 2),
    (3252, 2),
    (3253, 2),
    (3254, 2),
    (3255, 2),
    (3256, 2),
    (3257, 2),
    (3258, 2),
    (3259, 2),
    (3260, 2),
    (3261, 2),
    (3262, 2),
    (3263, 2),
    (3264, 2),
    (3265, 2),
    (3266, 2),
    (3267, 2),
    (3268, 2),
    (3269, 2),
    (3270, 2),
    (3271, 2),
    (3272, 2),
    (3273, 2),
    (3274, 2),
    (3275, 2),
    (3276, 2),
    (3277, 2),
    (3278, 2),
    (3279, 2),
    (3280, 2),
    (3281, 2),
    (3282, 2),
    (3283, 2),
    (3284, 2),
    (3285, 2),
    (3286, 2),
    (3287, 2),
    (3288, 2),
    (3289, 2),
    (3290, 2),
    (3291, 2),
    (3292, 2),
    (3293, 2),
    (3294, 2),
    (3295, 2),
    (3296, 2),
    (3297, 2),
    (3298, 2),
    (3299, 2),
    (3300, 2),
    (3301, 2),
    (3302, 2),
    (3303, 2),
    (3304, 2),
    (3305, 2),
    (3306, 2),
    (3307, 2),
    (3308, 2),
    (3309, 2),
    (3310, 2),
    (3311, 2),
    (3312, 2),
    (3313, 2),
    (3314, 2),
    (3315, 2),
    (3316, 2),
    (3317, 2),
    (3318, 2),
    (3319, 2),
    (3320, 2),
    (3321, 2),
    (3322, 2),
    (3323, 2),
    (3324, 2),
    (3325, 2),
    (3326, 2),
    (3327, 2),
    (3328, 2),
    (3329, 2),
    (3330, 2),
    (3331, 2),
    (3332, 2),
    (3333, 2),
    (3334, 2),
    (3335, 2),
    (3336, 2),
    (3337, 2),
    (3338, 2),
    (3339, 2),
    (3340, 2),
    (3341, 2),
    (3342, 2),
    (3343, 2),
    (3344, 2),
    (3345, 2),
    (3346, 2),
    (3347, 2),
    (3348, 2),
    (3349, 2),
    (3350, 2),
    (3351, 2),
    (3352, 2),
    (3353, 2),
    (3354, 2),
    (3355, 2),
    (3356, 2),
    (3357, 2),
    (3358, 2),
    (3359, 2),
    (3360, 2),
    (3361, 2),
    (3362, 2),
    (3363, 2),
    (3364, 2),
    (3365, 2),
    (3366, 2),
    (3367, 2),
    (3368, 2),
    (3369, 2),
    (3370, 2),
    (3371, 2),
    (3372, 2),
    (3373, 2),
    (3374, 2),
    (3375, 2),
    (3376, 2),
    (3377, 2),
    (3378, 2),
    (3379, 2),
    (3380, 2),
    (3381, 2),
    (3382, 2),
    (3383, 2),
    (3384, 2),
    (3385, 2),
    (3386, 2),
    (3387, 2),
    (3388, 2),
    (3389, 2),
    (3390, 2),
    (3391, 2),
    (3392, 2),
    (3393, 2),
    (3394, 2),
    (3395, 2),
    (3396, 2),
    (3397, 2),
    (3398, 2),
    (3399, 2),
    (3400, 2),
    (3401, 2),
    (3402, 2),
    (3403, 2),
    (3404, 2),
    (3405, 2),
    (3406, 2),
    (3407, 2),
    (3408, 2),
    (3409, 2),
    (3410, 2),
    (3411, 2),
    (3412, 2),
    (3413, 2),
    (3414, 2),
    (3415, 2),
    (3416, 2),
    (3417, 2),
    (3418, 2),
    (3419, 2),
    (3420, 2),
    (3421, 2),
    (3422, 2),
    (3423, 2),
    (3424, 2),
    (3425, 2),
    (3426, 2),
    (3427, 2),
    (3428, 2),
    (3429, 2),
    (3430, 2),
    (3431, 2),
    (3432, 2),
    (3433, 2),
    (3434, 2),
    (3435, 2),
    (3436, 2),
    (3437, 2),
    (3438, 2),
    (3439, 2),
    (3440, 2),
    (3441, 2),
    (3442, 2),
    (3443, 2),
    (3444, 2),
    (3445, 2),
    (3446, 2),
    (3447, 2),
    (3448, 2),
    (3449, 2),
    (3450, 2),
    (3451, 2),
    (3452, 2),
    (3453, 2),
    (3454, 2),
    (3455, 2),
    (3456, 2),
    (3457, 2),
    (3458, 2),
    (3459, 2),
    (3460, 2),
    (3461, 2),
    (3462, 2),
    (3463, 2),
    (3464, 2),
    (3465, 2),
    (3466, 2),
    (3467, 2),
    (3468, 2),
    (3469, 2),
    (3470, 2),
    (3471, 2),
    (3472, 2),
    (3473, 2),
    (3474, 2),
    (3475, 2),
    (3476, 2),
    (3477, 2),
    (3478, 2),
    (3479, 2),
    (3480, 2),
    (3481, 2),
    (3482, 2),
    (3483, 2),
    (3484, 2),
    (3485, 2),
    (3486, 2),
    (3487, 2),
    (3488, 2),
    (3489, 2),
    (3490, 2),
    (3491, 2),
    (3492, 2),
    (3493, 2),
    (3494, 2),
    (3495, 2),
    (3496, 2),
    (3497, 2),
    (3498, 2),
    (3499, 2),
    (3500, 2),
    (3501, 2),
    (3502, 2),
    (3503, 2),
    (3504, 2),
    (3505, 2),
    (3506, 2),
    (3507, 2),
    (3508, 2),
    (3509, 2),
    (3510, 2),
    (3511, 2),
    (3512, 2),
    (3513, 2),
    (3514, 2),
    (3515, 2),
    (3516, 2),
    (3517, 2),
    (3518, 2),
    (3519, 2),
    (3520, 2),
    (3521, 2),
    (3522, 2),
    (3523, 2),
    (3524, 2),
    (3525, 2),
    (3526, 2),
    (3527, 2),
    (3528, 2),
    (3529, 2),
    (3530, 2),
    (3531, 2),
    (3532, 2),
    (3533, 2),
    (3534, 2),
    (3535, 2),
    (3536, 2),
    (3537, 2),
    (3538, 2),
    (3539, 2),
    (3540, 2),
    (3541, 2),
    (3542, 2),
    (3543, 2),
    (3544, 2),
    (3545, 2),
    (3546, 2),
    (3547, 2),
    (3548, 2),
    (3549, 2),
    (3550, 2),
    (3551, 2),
    (3552, 2),
    (3553, 2),
    (3554, 2),
    (3555, 2),
    (3556, 2),
    (3557, 2),
    (3558, 2),
    (3559, 2),
    (3560, 2),
    (3561, 2),
    (3562, 2),
    (3563, 2),
    (3564, 2),
    (3565, 2),
    (3566, 2),
    (3567, 2),
    (3568, 2),
    (3569, 2),
    (3570, 2),
    (3571, 2),
    (3572, 2),
    (3573, 2),
    (3574, 2),
    (3575, 2),
    (3576, 2),
    (3577, 2),
    (3578, 2),
    (3579, 2),
    (3580, 2),
    (3581, 2),
    (3582, 2),
    (3583, 2),
    (3584, 2),
    (3585, 2),
    (3586, 2),
    (3587, 2),
    (3588, 2),
    (3589, 2),
    (3590, 2),
    (3591, 2),
    (3592, 2),
    (3593, 2),
    (3594, 2),
    (3595, 2),
    (3596, 2),
    (3597, 2),
    (3598, 2),
    (3599, 2),
    (3600, 2),
    (3601, 2),
    (3602, 2),
    (3603, 2),
    (3604, 2),
    (3605, 2),
    (3606, 2),
    (3607, 2),
    (3608, 2),
    (3609, 2),
    (3610, 2),
    (3611, 2),
    (3612, 2),
    (3613, 2),
    (3614, 2),
    (3615, 2),
    (3616, 2),
    (3617, 2),
    (3618, 2),
    (3619, 2),
    (3620, 2),
    (3621, 2),
    (3622, 2),
    (3623, 2),
    (3624, 2),
    (3625, 2),
    (3626, 2),
    (3627, 2),
    (3628, 2),
    (3629, 2),
    (3630, 2),
    (3631, 2),
    (3632, 2),
    (3633, 2),
    (3634, 2),
    (3635, 2),
    (3636, 2),
    (3637, 2),
    (3638, 2),
    (3639, 2),
    (3640, 2),
    (3641, 2),
    (3642, 2),
    (3643, 2),
    (3644, 2),
    (3645, 2),
    (3646, 2),
    (3647, 2),
    (3648, 2),
    (3649, 2),
    (3650, 2),
    (3651, 2),
    (3652, 2),
    (3653, 2),
    (3654, 2),
    (3655, 2),
    (3656, 2),
    (3657, 2),
    (3658, 2),
    (3659, 2),
    (3660, 2),
    (3661, 2),
    (3662, 2),
    (3663, 2),
    (3664, 2),
    (3665, 2),
    (3666, 2),
    (3667, 2),
    (3668, 2),
    (3669, 2),
    (3670, 2),
    (3671, 2),
    (3672, 2),
    (3673, 2),
    (3674, 2),
    (3675, 2),
    (3676, 2),
    (3677, 2),
    (3678, 2),
    (3679, 2),
    (3680, 2),
    (3681, 2),
    (3682, 2),
    (3683, 2),
    (3684, 2),
    (3685, 2),
    (3686, 2),
    (3687, 2),
    (3688, 2),
    (3689, 2),
    (3690, 2),
    (3691, 2),
    (3692, 2),
    (3693, 2),
    (3694, 2),
    (3695, 2),
    (3696, 2),
    (3697, 2),
    (3698, 2),
    (3699, 2),
    (3700, 2),
    (3701, 2),
    (3702, 2),
    (3703, 2),
    (3704, 2),
    (3705, 2),
    (3706, 2),
    (3707, 2),
    (3708, 2),
    (3709, 2),
    (3710, 2),
    (3711, 2),
    (3712, 2),
    (3713, 2),
    (3714, 2),
    (3715, 2),
    (3716, 2),
    (3717, 2),
    (3718, 2),
    (3719, 2),
    (3720, 2),
    (3721, 2),
    (3722, 2),
    (3723, 2),
    (3724, 2),
    (3725, 2),
    (3726, 2),
    (3727, 2),
    (3728, 2),
    (3729, 2),
    (3730, 2),
    (3731, 2),
    (3732, 2),
    (3733, 2),
    (3734, 2),
    (3735, 2),
    (3736, 2),
    (3737, 2),
    (3738, 2),
    (3739, 2),
    (3740, 2),
    (3741, 2),
    (3742, 2),
    (3743, 2),
    (3744, 2),
    (3745, 2),
    (3746, 2),
    (3747, 2),
    (3748, 2),
    (3749, 2),
    (3750, 2),
    (3751, 2),
    (3752, 2),
    (3753, 2),
    (3754, 2),
    (3755, 2),
    (3756, 2),
    (3757, 2),
    (3758, 2),
    (3759, 2),
    (3760, 2),
    (3761, 2),
    (3762, 2),
    (3763, 2),
    (3764, 2),
    (3765, 2),
    (3766, 2),
    (3767, 2),
    (3768, 2),
    (3769, 2),
    (3770, 2),
    (3771, 2),
    (3772, 2),
    (3773, 2),
    (3774, 2),
    (3775, 2),
    (3776, 2),
    (3777, 2),
    (3778, 2),
    (3779, 2),
    (3780, 2),
    (3781, 2),
    (3782, 2),
    (3783, 2),
    (3784, 2),
    (3785, 2),
    (3786, 2),
    (3787, 2),
    (3788, 2),
    (3789, 2),
    (3790, 2),
    (3791, 2),
    (3792, 2),
    (3793, 2),
    (3794, 2),
    (3795, 2),
    (3796, 2),
    (3797, 2),
    (3798, 2),
    (3799, 2),
    (3800, 2),
    (3801, 2),
    (3802, 2),
    (3803, 2),
    (3804, 2),
    (3805, 2),
    (3806, 2),
    (3807, 2),
    (3808, 2),
    (3809, 2),
    (3810, 2),
    (3811, 2),
    (3812, 2),
    (3813, 2),
    (3814, 2),
    (3815, 2),
    (3816, 2),
    (3817, 2),
    (3818, 2),
    (3819, 2),
    (3820, 2),
    (3821, 2),
    (3822, 2),
    (3823, 2),
    (3824, 2),
    (3825, 2),
    (3826, 2),
    (3827, 2),
    (3828, 2),
    (3829, 2),
    (3830, 2),
    (3831, 2),
    (3832, 2),
    (3833, 2),
    (3834, 2),
    (3835, 2),
    (3836, 2),
    (3837, 2),
    (3838, 2),
    (3839, 2),
    (3840, 2),
    (3841, 2),
    (3842, 2),
    (3843, 2),
    (3844, 2),
    (3845, 2),
    (3846, 2),
    (3847, 2),
    (3848, 2),
    (3849, 2),
    (3850, 2),
    (3851, 2),
    (3852, 2),
    (3853, 2),
    (3854, 2),
    (3855, 2),
    (3856, 2),
    (3857, 2),
    (3858, 2),
    (3859, 2),
    (3860, 2),
    (3861, 2),
    (3862, 2),
    (3863, 2),
    (3864, 2),
    (3865, 2),
    (3866, 2),
    (3867, 2),
    (3868, 2),
    (3869, 2),
    (3870, 2),
    (3871, 2),
    (3872, 2),
    (3873, 2),
    (3874, 2),
    (3875, 2),
    (3876, 2),
    (3877, 2),
    (3878, 2),
    (3879, 2),
    (3880, 2),
    (3881, 2),
    (3882, 2),
    (3883, 2),
    (3884, 2),
    (3885, 2),
    (3886, 2),
    (3887, 2),
    (3888, 2),
    (3889, 2),
    (3890, 2),
    (3891, 2),
    (3892, 2),
    (3893, 2),
    (3894, 2),
    (3895, 2),
    (3896, 2),
    (3897, 2),
    (3898, 2),
    (3899, 2),
    (3900, 2),
    (3901, 2),
    (3902, 2),
    (3903, 2),
    (3904, 2),
    (3905, 2),
    (3906, 2),
    (3907, 2),
    (3908, 2),
    (3909, 2),
    (3910, 2),
    (3911, 2),
    (3912, 2),
    (3913, 2),
    (3914, 2),
    (3915, 2),
    (3916, 2),
    (3917, 2),
    (3918, 2),
    (3919, 2),
    (3920, 2),
    (3921, 2),
    (3922, 2),
    (3923, 2),
    (3924, 2),
    (3925, 2),
    (3926, 2),
    (3927, 2),
    (3928, 2),
    (3929, 2),
    (3930, 2),
    (3931, 2),
    (3932, 2),
    (3933, 2),
    (3934, 2),
    (3935, 2),
    (3936, 2),
    (3937, 2),
    (3938, 2),
    (3939, 2),
    (3940, 2),
    (3941, 2),
    (3942, 2),
    (3943, 2),
    (3944, 2),
    (3945, 2),
    (3946, 2),
    (3947, 2),
    (3948, 2),
    (3949, 2),
    (3950, 2),
    (3951, 2),
    (3952, 2),
    (3953, 2),
    (3954, 2),
    (3955, 2),
    (3956, 2),
    (3957, 2),
    (3958, 2),
    (3959, 2),
    (3960, 2),
    (3961, 2),
    (3962, 2),
    (3963, 2),
    (3964, 2),
    (3965, 2),
    (3966, 2),
    (3967, 2),
    (3968, 2),
    (3969, 2),
    (3970, 2),
    (3971, 2),
    (3972, 2),
    (3973, 2),
    (3974, 2),
    (3975, 2),
    (3976, 2),
    (3977, 2),
    (3978, 2),
    (3979, 2),
    (3980, 2),
    (3981, 2),
    (3982, 2),
    (3983, 2),
    (3984, 2),
    (3985, 2),
    (3986, 2),
    (3987, 2),
    (3988, 2),
    (3989, 2),
    (3990, 2),
    (3991, 2),
    (3992, 2),
    (3993, 2),
    (3994, 2),
    (3995, 2),
    (3996, 2),
    (3997, 2),
    (3998, 2),
    (3999, 2),
    (4000, 2),
    (4001, 3),
    (4002, 3),
    (4003, 3),
    (4004, 3),
    (4005, 3),
    (4006, 3),
    (4007, 3),
    (4008, 3),
    (4009, 3),
    (4010, 3),
    (4011, 3),
    (4012, 3),
    (4013, 3),
    (4014, 3),
    (4015, 3),
    (4016, 3),
    (4017, 3),
    (4018, 3),
    (4019, 3),
    (4020, 3),
    (4021, 3),
    (4022, 3),
    (4023, 3),
    (4024, 3),
    (4025, 3),
    (4026, 3),
    (4027, 3),
    (4028, 3),
    (4029, 3),
    (4030, 3),
    (4031, 3),
    (4032, 3),
    (4033, 3),
    (4034, 3),
    (4035, 3),
    (4036, 3),
    (4037, 3),
    (4038, 3),
    (4039, 3),
    (4040, 3),
    (4041, 3),
    (4042, 3),
    (4043, 3),
    (4044, 3),
    (4045, 3),
    (4046, 3),
    (4047, 3),
    (4048, 3),
    (4049, 3),
    (4050, 3),
    (4051, 3),
    (4052, 3),
    (4053, 3),
    (4054, 3),
    (4055, 3),
    (4056, 3),
    (4057, 3),
    (4058, 3),
    (4059, 3),
    (4060, 3),
    (4061, 3),
    (4062, 3),
    (4063, 3),
    (4064, 3),
    (4065, 3),
    (4066, 3),
    (4067, 3),
    (4068, 3),
    (4069, 3),
    (4070, 3),
    (4071, 3),
    (4072, 3),
    (4073, 3),
    (4074, 3),
    (4075, 3),
    (4076, 3),
    (4077, 3),
    (4078, 3),
    (4079, 3),
    (4080, 3),
    (4081, 3),
    (4082, 3),
    (4083, 3),
    (4084, 3),
    (4085, 3),
    (4086, 3),
    (4087, 3),
    (4088, 3),
    (4089, 3),
    (4090, 3),
    (4091, 3),
    (4092, 3),
    (4093, 3),
    (4094, 3),
    (4095, 3),
    (4096, 3),
    (4097, 3),
    (4098, 3),
    (4099, 3),
    (4100, 3),
    (4101, 3),
    (4102, 3),
    (4103, 3),
    (4104, 3),
    (4105, 3),
    (4106, 3),
    (4107, 3),
    (4108, 3),
    (4109, 3),
    (4110, 3),
    (4111, 3),
    (4112, 3),
    (4113, 3),
    (4114, 3),
    (4115, 3),
    (4116, 3),
    (4117, 3),
    (4118, 3),
    (4119, 3),
    (4120, 3),
    (4121, 3),
    (4122, 3),
    (4123, 3),
    (4124, 3),
    (4125, 3),
    (4126, 3),
    (4127, 3),
    (4128, 3),
    (4129, 3),
    (4130, 3),
    (4131, 3),
    (4132, 3),
    (4133, 3),
    (4134, 3),
    (4135, 3),
    (4136, 3),
    (4137, 3),
    (4138, 3),
    (4139, 3),
    (4140, 3),
    (4141, 3),
    (4142, 3),
    (4143, 3),
    (4144, 3),
    (4145, 3),
    (4146, 3),
    (4147, 3),
    (4148, 3),
    (4149, 3),
    (4150, 3),
    (4151, 3),
    (4152, 3),
    (4153, 3),
    (4154, 3),
    (4155, 3),
    (4156, 3),
    (4157, 3),
    (4158, 3),
    (4159, 3),
    (4160, 3),
    (4161, 3),
    (4162, 3),
    (4163, 3),
    (4164, 3),
    (4165, 3),
    (4166, 3),
    (4167, 3),
    (4168, 3),
    (4169, 3),
    (4170, 3),
    (4171, 3),
    (4172, 3),
    (4173, 3),
    (4174, 3),
    (4175, 3),
    (4176, 3),
    (4177, 3),
    (4178, 3),
    (4179, 3),
    (4180, 3),
    (4181, 3),
    (4182, 3),
    (4183, 3),
    (4184, 3),
    (4185, 3),
    (4186, 3),
    (4187, 3),
    (4188, 3),
    (4189, 3),
    (4190, 3),
    (4191, 3),
    (4192, 3),
    (4193, 3),
    (4194, 3),
    (4195, 3),
    (4196, 3),
    (4197, 3),
    (4198, 3),
    (4199, 3),
    (4200, 3),
    (4201, 3),
    (4202, 3),
    (4203, 3),
    (4204, 3),
    (4205, 3),
    (4206, 3),
    (4207, 3),
    (4208, 3),
    (4209, 3),
    (4210, 3),
    (4211, 3),
    (4212, 3),
    (4213, 3),
    (4214, 3),
    (4215, 3),
    (4216, 3),
    (4217, 3),
    (4218, 3),
    (4219, 3),
    (4220, 3),
    (4221, 3),
    (4222, 3),
    (4223, 3),
    (4224, 3),
    (4225, 3),
    (4226, 3),
    (4227, 3),
    (4228, 3),
    (4229, 3),
    (4230, 3),
    (4231, 3),
    (4232, 3),
    (4233, 3),
    (4234, 3),
    (4235, 3),
    (4236, 3),
    (4237, 3),
    (4238, 3),
    (4239, 3),
    (4240, 3),
    (4241, 3),
    (4242, 3),
    (4243, 3),
    (4244, 3),
    (4245, 3),
    (4246, 3),
    (4247, 3),
    (4248, 3),
    (4249, 3),
    (4250, 3),
    (4251, 3),
    (4252, 3),
    (4253, 3),
    (4254, 3),
    (4255, 3),
    (4256, 3),
    (4257, 3),
    (4258, 3),
    (4259, 3),
    (4260, 3),
    (4261, 3),
    (4262, 3),
    (4263, 3),
    (4264, 3),
    (4265, 3),
    (4266, 3),
    (4267, 3),
    (4268, 3),
    (4269, 3),
    (4270, 3),
    (4271, 3),
    (4272, 3),
    (4273, 3),
    (4274, 3),
    (4275, 3),
    (4276, 3),
    (4277, 3),
    (4278, 3),
    (4279, 3),
    (4280, 3),
    (4281, 3),
    (4282, 3),
    (4283, 3),
    (4284, 3),
    (4285, 3),
    (4286, 3),
    (4287, 3),
    (4288, 3),
    (4289, 3),
    (4290, 3),
    (4291, 3),
    (4292, 3),
    (4293, 3),
    (4294, 3),
    (4295, 3),
    (4296, 3),
    (4297, 3),
    (4298, 3),
    (4299, 3),
    (4300, 3),
    (4301, 3),
    (4302, 3),
    (4303, 3),
    (4304, 3),
    (4305, 3),
    (4306, 3),
    (4307, 3),
    (4308, 3),
    (4309, 3),
    (4310, 3),
    (4311, 3),
    (4312, 3),
    (4313, 3),
    (4314, 3),
    (4315, 3),
    (4316, 3),
    (4317, 3),
    (4318, 3),
    (4319, 3),
    (4320, 3),
    (4321, 3),
    (4322, 3),
    (4323, 3),
    (4324, 3),
    (4325, 3),
    (4326, 3),
    (4327, 3),
    (4328, 3),
    (4329, 3),
    (4330, 3),
    (4331, 3),
    (4332, 3),
    (4333, 3),
    (4334, 3),
    (4335, 3),
    (4336, 3),
    (4337, 3),
    (4338, 3),
    (4339, 3),
    (4340, 3),
    (4341, 3),
    (4342, 3),
    (4343, 3),
    (4344, 3),
    (4345, 3),
    (4346, 3),
    (4347, 3),
    (4348, 3),
    (4349, 3),
    (4350, 3),
    (4351, 3),
    (4352, 3),
    (4353, 3),
    (4354, 3),
    (4355, 3),
    (4356, 3),
    (4357, 3),
    (4358, 3),
    (4359, 3),
    (4360, 3),
    (4361, 3),
    (4362, 3),
    (4363, 3),
    (4364, 3),
    (4365, 3),
    (4366, 3),
    (4367, 3),
    (4368, 3),
    (4369, 3),
    (4370, 3),
    (4371, 3),
    (4372, 3),
    (4373, 3),
    (4374, 3),
    (4375, 3),
    (4376, 3),
    (4377, 3),
    (4378, 3),
    (4379, 3),
    (4380, 3),
    (4381, 3),
    (4382, 3),
    (4383, 3),
    (4384, 3),
    (4385, 3),
    (4386, 3),
    (4387, 3),
    (4388, 3),
    (4389, 3),
    (4390, 3),
    (4391, 3),
    (4392, 3),
    (4393, 3),
    (4394, 3),
    (4395, 3),
    (4396, 3),
    (4397, 3),
    (4398, 3),
    (4399, 3),
    (4400, 3),
    (4401, 3),
    (4402, 3),
    (4403, 3),
    (4404, 3),
    (4405, 3),
    (4406, 3),
    (4407, 3),
    (4408, 3),
    (4409, 3),
    (4410, 3),
    (4411, 3),
    (4412, 3),
    (4413, 3),
    (4414, 3),
    (4415, 3),
    (4416, 3),
    (4417, 3),
    (4418, 3),
    (4419, 3),
    (4420, 3),
    (4421, 3),
    (4422, 3),
    (4423, 3),
    (4424, 3),
    (4425, 3),
    (4426, 3),
    (4427, 3),
    (4428, 3),
    (4429, 3),
    (4430, 3),
    (4431, 3),
    (4432, 3),
    (4433, 3),
    (4434, 3),
    (4435, 3),
    (4436, 3),
    (4437, 3),
    (4438, 3),
    (4439, 3),
    (4440, 3),
    (4441, 3),
    (4442, 3),
    (4443, 3),
    (4444, 3),
    (4445, 3),
    (4446, 3),
    (4447, 3),
    (4448, 3),
    (4449, 3),
    (4450, 3),
    (4451, 3),
    (4452, 3),
    (4453, 3),
    (4454, 3),
    (4455, 3),
    (4456, 3),
    (4457, 3),
    (4458, 3),
    (4459, 3),
    (4460, 3),
    (4461, 3),
    (4462, 3),
    (4463, 3),
    (4464, 3),
    (4465, 3),
    (4466, 3),
    (4467, 3),
    (4468, 3),
    (4469, 3),
    (4470, 3),
    (4471, 3),
    (4472, 3),
    (4473, 3),
    (4474, 3),
    (4475, 3),
    (4476, 3),
    (4477, 3),
    (4478, 3),
    (4479, 3),
    (4480, 3),
    (4481, 3),
    (4482, 3),
    (4483, 3),
    (4484, 3),
    (4485, 3),
    (4486, 3),
    (4487, 3),
    (4488, 3),
    (4489, 3),
    (4490, 3),
    (4491, 3),
    (4492, 3),
    (4493, 3),
    (4494, 3),
    (4495, 3),
    (4496, 3),
    (4497, 3),
    (4498, 3),
    (4499, 3),
    (4500, 3),
    (4501, 3),
    (4502, 3),
    (4503, 3),
    (4504, 3),
    (4505, 3),
    (4506, 3),
    (4507, 3),
    (4508, 3),
    (4509, 3),
    (4510, 3),
    (4511, 3),
    (4512, 3),
    (4513, 3),
    (4514, 3),
    (4515, 3),
    (4516, 3),
    (4517, 3),
    (4518, 3),
    (4519, 3),
    (4520, 3),
    (4521, 3),
    (4522, 3),
    (4523, 3),
    (4524, 3),
    (4525, 3),
    (4526, 3),
    (4527, 3),
    (4528, 3),
    (4529, 3),
    (4530, 3),
    (4531, 3),
    (4532, 3),
    (4533, 3),
    (4534, 3),
    (4535, 3),
    (4536, 3),
    (4537, 3),
    (4538, 3),
    (4539, 3),
    (4540, 3),
    (4541, 3),
    (4542, 3),
    (4543, 3),
    (4544, 3),
    (4545, 3),
    (4546, 3),
    (4547, 3),
    (4548, 3),
    (4549, 3),
    (4550, 3),
    (4551, 3),
    (4552, 3),
    (4553, 3),
    (4554, 3),
    (4555, 3),
    (4556, 3),
    (4557, 3),
    (4558, 3),
    (4559, 3),
    (4560, 3),
    (4561, 3),
    (4562, 3),
    (4563, 3),
    (4564, 3),
    (4565, 3),
    (4566, 3),
    (4567, 3),
    (4568, 3),
    (4569, 3),
    (4570, 3),
    (4571, 3),
    (4572, 3),
    (4573, 3),
    (4574, 3),
    (4575, 3),
    (4576, 3),
    (4577, 3),
    (4578, 3),
    (4579, 3),
    (4580, 3),
    (4581, 3),
    (4582, 3),
    (4583, 3),
    (4584, 3),
    (4585, 3),
    (4586, 3),
    (4587, 3),
    (4588, 3),
    (4589, 3),
    (4590, 3),
    (4591, 3),
    (4592, 3),
    (4593, 3),
    (4594, 3),
    (4595, 3),
    (4596, 3),
    (4597, 3),
    (4598, 3),
    (4599, 3),
    (4600, 3),
    (4601, 3),
    (4602, 3),
    (4603, 3),
    (4604, 3),
    (4605, 3),
    (4606, 3),
    (4607, 3),
    (4608, 3),
    (4609, 3),
    (4610, 3),
    (4611, 3),
    (4612, 3),
    (4613, 3),
    (4614, 3),
    (4615, 3),
    (4616, 3),
    (4617, 3),
    (4618, 3),
    (4619, 3),
    (4620, 3),
    (4621, 3),
    (4622, 3),
    (4623, 3),
    (4624, 3),
    (4625, 3),
    (4626, 3),
    (4627, 3),
    (4628, 3),
    (4629, 3),
    (4630, 3),
    (4631, 3),
    (4632, 3),
    (4633, 3),
    (4634, 3),
    (4635, 3),
    (4636, 3),
    (4637, 3),
    (4638, 3),
    (4639, 3),
    (4640, 3),
    (4641, 3),
    (4642, 3),
    (4643, 3),
    (4644, 3),
    (4645, 3),
    (4646, 3),
    (4647, 3),
    (4648, 3),
    (4649, 3),
    (4650, 3),
    (4651, 3),
    (4652, 3),
    (4653, 3),
    (4654, 3),
    (4655, 3),
    (4656, 3),
    (4657, 3),
    (4658, 3),
    (4659, 3),
    (4660, 3),
    (4661, 3),
    (4662, 3),
    (4663, 3),
    (4664, 3),
    (4665, 3),
    (4666, 3),
    (4667, 3),
    (4668, 3),
    (4669, 3),
    (4670, 3),
    (4671, 3),
    (4672, 3),
    (4673, 3),
    (4674, 3),
    (4675, 3),
    (4676, 3),
    (4677, 3),
    (4678, 3),
    (4679, 3),
    (4680, 3),
    (4681, 3),
    (4682, 3),
    (4683, 3),
    (4684, 3),
    (4685, 3),
    (4686, 3),
    (4687, 3),
    (4688, 3),
    (4689, 3),
    (4690, 3),
    (4691, 3),
    (4692, 3),
    (4693, 3),
    (4694, 3),
    (4695, 3),
    (4696, 3),
    (4697, 3),
    (4698, 3),
    (4699, 3),
    (4700, 3),
    (4701, 3),
    (4702, 3),
    (4703, 3),
    (4704, 3),
    (4705, 3),
    (4706, 3),
    (4707, 3),
    (4708, 3),
    (4709, 3),
    (4710, 3),
    (4711, 3),
    (4712, 3),
    (4713, 3),
    (4714, 3),
    (4715, 3),
    (4716, 3),
    (4717, 3),
    (4718, 3),
    (4719, 3),
    (4720, 3),
    (4721, 3),
    (4722, 3),
    (4723, 3),
    (4724, 3),
    (4725, 3),
    (4726, 3),
    (4727, 3),
    (4728, 3),
    (4729, 3),
    (4730, 3),
    (4731, 3),
    (4732, 3),
    (4733, 3),
    (4734, 3),
    (4735, 3),
    (4736, 3),
    (4737, 3),
    (4738, 3),
    (4739, 3),
    (4740, 3),
    (4741, 3),
    (4742, 3),
    (4743, 3),
    (4744, 3),
    (4745, 3),
    (4746, 3),
    (4747, 3),
    (4748, 3),
    (4749, 3),
    (4750, 3),
    (4751, 3),
    (4752, 3),
    (4753, 3),
    (4754, 3),
    (4755, 3),
    (4756, 3),
    (4757, 3),
    (4758, 3),
    (4759, 3),
    (4760, 3),
    (4761, 3),
    (4762, 3),
    (4763, 3),
    (4764, 3),
    (4765, 3),
    (4766, 3),
    (4767, 3),
    (4768, 3),
    (4769, 3),
    (4770, 3),
    (4771, 3),
    (4772, 3),
    (4773, 3),
    (4774, 3),
    (4775, 3),
    (4776, 3),
    (4777, 3),
    (4778, 3),
    (4779, 3),
    (4780, 3),
    (4781, 3),
    (4782, 3),
    (4783, 3),
    (4784, 3),
    (4785, 3),
    (4786, 3),
    (4787, 3),
    (4788, 3),
    (4789, 3),
    (4790, 3),
    (4791, 3),
    (4792, 3),
    (4793, 3),
    (4794, 3),
    (4795, 3),
    (4796, 3),
    (4797, 3),
    (4798, 3),
    (4799, 3),
    (4800, 3),
    (4801, 3),
    (4802, 3),
    (4803, 3),
    (4804, 3),
    (4805, 3),
    (4806, 3),
    (4807, 3),
    (4808, 3),
    (4809, 3),
    (4810, 3),
    (4811, 3),
    (4812, 3),
    (4813, 3),
    (4814, 3),
    (4815, 3),
    (4816, 3),
    (4817, 3),
    (4818, 3),
    (4819, 3),
    (4820, 3),
    (4821, 3),
    (4822, 3),
    (4823, 3),
    (4824, 3),
    (4825, 3),
    (4826, 3),
    (4827, 3),
    (4828, 3),
    (4829, 3),
    (4830, 3),
    (4831, 3),
    (4832, 3),
    (4833, 3),
    (4834, 3),
    (4835, 3),
    (4836, 3),
    (4837, 3),
    (4838, 3),
    (4839, 3),
    (4840, 3),
    (4841, 3),
    (4842, 3),
    (4843, 3),
    (4844, 3),
    (4845, 3),
    (4846, 3),
    (4847, 3),
    (4848, 3),
    (4849, 3),
    (4850, 3),
    (4851, 3),
    (4852, 3),
    (4853, 3),
    (4854, 3),
    (4855, 3),
    (4856, 3),
    (4857, 3),
    (4858, 3),
    (4859, 3),
    (4860, 3),
    (4861, 3),
    (4862, 3),
    (4863, 3),
    (4864, 3),
    (4865, 3),
    (4866, 3),
    (4867, 3),
    (4868, 3),
    (4869, 3),
    (4870, 3),
    (4871, 3),
    (4872, 3),
    (4873, 3),
    (4874, 3),
    (4875, 3),
    (4876, 3),
    (4877, 3),
    (4878, 3),
    (4879, 3),
    (4880, 3),
    (4881, 3),
    (4882, 3),
    (4883, 3),
    (4884, 3),
    (4885, 3),
    (4886, 3),
    (4887, 3),
    (4888, 3),
    (4889, 3),
    (4890, 3),
    (4891, 3),
    (4892, 3),
    (4893, 3),
    (4894, 3),
    (4895, 3),
    (4896, 3),
    (4897, 3),
    (4898, 3),
    (4899, 3),
    (4900, 3),
    (4901, 3),
    (4902, 3),
    (4903, 3),
    (4904, 3),
    (4905, 3),
    (4906, 3),
    (4907, 3),
    (4908, 3),
    (4909, 3),
    (4910, 3),
    (4911, 3),
    (4912, 3),
    (4913, 3),
    (4914, 3),
    (4915, 3),
    (4916, 3),
    (4917, 3),
    (4918, 3),
    (4919, 3),
    (4920, 3),
    (4921, 3),
    (4922, 3),
    (4923, 3),
    (4924, 3),
    (4925, 3),
    (4926, 3),
    (4927, 3),
    (4928, 3),
    (4929, 3),
    (4930, 3),
    (4931, 3),
    (4932, 3),
    (4933, 3),
    (4934, 3),
    (4935, 3),
    (4936, 3),
    (4937, 3),
    (4938, 3),
    (4939, 3),
    (4940, 3),
    (4941, 3),
    (4942, 3),
    (4943, 3),
    (4944, 3),
    (4945, 3),
    (4946, 3),
    (4947, 3),
    (4948, 3),
    (4949, 3),
    (4950, 3),
    (4951, 3),
    (4952, 3),
    (4953, 3),
    (4954, 3),
    (4955, 3),
    (4956, 3),
    (4957, 3),
    (4958, 3),
    (4959, 3),
    (4960, 3),
    (4961, 3),
    (4962, 3),
    (4963, 3),
    (4964, 3),
    (4965, 3),
    (4966, 3),
    (4967, 3),
    (4968, 3),
    (4969, 3),
    (4970, 3),
    (4971, 3),
    (4972, 3),
    (4973, 3),
    (4974, 3),
    (4975, 3),
    (4976, 3),
    (4977, 3),
    (4978, 3),
    (4979, 3),
    (4980, 3),
    (4981, 3),
    (4982, 3),
    (4983, 3),
    (4984, 3),
    (4985, 3),
    (4986, 3),
    (4987, 3),
    (4988, 3),
    (4989, 3),
    (4990, 3),
    (4991, 3),
    (4992, 3),
    (4993, 3),
    (4994, 3),
    (4995, 3),
    (4996, 3),
    (4997, 3),
    (4998, 3),
    (4999, 3),
    (5000, 3),
    (5001, 3),
    (5002, 3),
    (5003, 3),
    (5004, 3),
    (5005, 3),
    (5006, 3),
    (5007, 3),
    (5008, 3),
    (5009, 3),
    (5010, 3),
    (5011, 3),
    (5012, 3),
    (5013, 3),
    (5014, 3),
    (5015, 3),
    (5016, 3),
    (5017, 3),
    (5018, 3),
    (5019, 3),
    (5020, 3),
    (5021, 3),
    (5022, 3),
    (5023, 3),
    (5024, 3),
    (5025, 3),
    (5026, 3),
    (5027, 3),
    (5028, 3),
    (5029, 3),
    (5030, 3),
    (5031, 3),
    (5032, 3),
    (5033, 3),
    (5034, 3),
    (5035, 3),
    (5036, 3),
    (5037, 3),
    (5038, 3),
    (5039, 3),
    (5040, 3),
    (5041, 3),
    (5042, 3),
    (5043, 3),
    (5044, 3),
    (5045, 3),
    (5046, 3),
    (5047, 3),
    (5048, 3),
    (5049, 3),
    (5050, 3),
    (5051, 3),
    (5052, 3),
    (5053, 3),
    (5054, 3),
    (5055, 3),
    (5056, 3),
    (5057, 3),
    (5058, 3),
    (5059, 3),
    (5060, 3),
    (5061, 3),
    (5062, 3),
    (5063, 3),
    (5064, 3),
    (5065, 3),
    (5066, 3),
    (5067, 3),
    (5068, 3),
    (5069, 3),
    (5070, 3),
    (5071, 3),
    (5072, 3),
    (5073, 3),
    (5074, 3),
    (5075, 3),
    (5076, 3),
    (5077, 3),
    (5078, 3),
    (5079, 3),
    (5080, 3),
    (5081, 3),
    (5082, 3),
    (5083, 3),
    (5084, 3),
    (5085, 3),
    (5086, 3),
    (5087, 3),
    (5088, 3),
    (5089, 3),
    (5090, 3),
    (5091, 3),
    (5092, 3),
    (5093, 3),
    (5094, 3),
    (5095, 3),
    (5096, 3),
    (5097, 3),
    (5098, 3),
    (5099, 3),
    (5100, 3),
    (5101, 3),
    (5102, 3),
    (5103, 3),
    (5104, 3),
    (5105, 3),
    (5106, 3),
    (5107, 3),
    (5108, 3),
    (5109, 3),
    (5110, 3),
    (5111, 3),
    (5112, 3),
    (5113, 3),
    (5114, 3),
    (5115, 3),
    (5116, 3),
    (5117, 3),
    (5118, 3),
    (5119, 3),
    (5120, 3),
    (5121, 3),
    (5122, 3),
    (5123, 3),
    (5124, 3),
    (5125, 3),
    (5126, 3),
    (5127, 3),
    (5128, 3),
    (5129, 3),
    (5130, 3),
    (5131, 3),
    (5132, 3),
    (5133, 3),
    (5134, 3),
    (5135, 3),
    (5136, 3),
    (5137, 3),
    (5138, 3),
    (5139, 3),
    (5140, 3),
    (5141, 3),
    (5142, 3),
    (5143, 3),
    (5144, 3),
    (5145, 3),
    (5146, 3),
    (5147, 3),
    (5148, 3),
    (5149, 3),
    (5150, 3),
    (5151, 3),
    (5152, 3),
    (5153, 3),
    (5154, 3),
    (5155, 3),
    (5156, 3),
    (5157, 3),
    (5158, 3),
    (5159, 3),
    (5160, 3),
    (5161, 3),
    (5162, 3),
    (5163, 3),
    (5164, 3),
    (5165, 3),
    (5166, 3),
    (5167, 3),
    (5168, 3),
    (5169, 3),
    (5170, 3),
    (5171, 3),
    (5172, 3),
    (5173, 3),
    (5174, 3),
    (5175, 3),
    (5176, 3),
    (5177, 3),
    (5178, 3),
    (5179, 3),
    (5180, 3),
    (5181, 3),
    (5182, 3),
    (5183, 3),
    (5184, 3),
    (5185, 3),
    (5186, 3),
    (5187, 3),
    (5188, 3),
    (5189, 3),
    (5190, 3),
    (5191, 3),
    (5192, 3),
    (5193, 3),
    (5194, 3),
    (5195, 3),
    (5196, 3),
    (5197, 3),
    (5198, 3),
    (5199, 3),
    (5200, 3),
    (5201, 3),
    (5202, 3),
    (5203, 3),
    (5204, 3),
    (5205, 3),
    (5206, 3),
    (5207, 3),
    (5208, 3),
    (5209, 3),
    (5210, 3),
    (5211, 3),
    (5212, 3),
    (5213, 3),
    (5214, 3),
    (5215, 3),
    (5216, 3),
    (5217, 3),
    (5218, 3),
    (5219, 3),
    (5220, 3),
    (5221, 3),
    (5222, 3),
    (5223, 3),
    (5224, 3),
    (5225, 3),
    (5226, 3),
    (5227, 3),
    (5228, 3),
    (5229, 3),
    (5230, 3),
    (5231, 3),
    (5232, 3),
    (5233, 3),
    (5234, 3),
    (5235, 3),
    (5236, 3),
    (5237, 3),
    (5238, 3),
    (5239, 3),
    (5240, 3),
    (5241, 3),
    (5242, 3),
    (5243, 3),
    (5244, 3),
    (5245, 3),
    (5246, 3),
    (5247, 3),
    (5248, 3),
    (5249, 3),
    (5250, 3),
    (5251, 3),
    (5252, 3),
    (5253, 3),
    (5254, 3),
    (5255, 3),
    (5256, 3),
    (5257, 3),
    (5258, 3),
    (5259, 3),
    (5260, 3),
    (5261, 3),
    (5262, 3),
    (5263, 3),
    (5264, 3),
    (5265, 3),
    (5266, 3),
    (5267, 3),
    (5268, 3),
    (5269, 3),
    (5270, 3),
    (5271, 3),
    (5272, 3),
    (5273, 3),
    (5274, 3),
    (5275, 3),
    (5276, 3),
    (5277, 3),
    (5278, 3),
    (5279, 3),
    (5280, 3),
    (5281, 3),
    (5282, 3),
    (5283, 3),
    (5284, 3),
    (5285, 3),
    (5286, 3),
    (5287, 3),
    (5288, 3),
    (5289, 3),
    (5290, 3),
    (5291, 3),
    (5292, 3),
    (5293, 3),
    (5294, 3),
    (5295, 3),
    (5296, 3),
    (5297, 3),
    (5298, 3),
    (5299, 3),
    (5300, 3),
)


def run_message_split_regression() -> dict:
    """Comprueba todos los tamaños registrados y devuelve un resumen."""
    total = 0
    failure_count = 0
    failures = []
    for length, expected_parts in MESSAGE_SPLIT_REGRESSION_CASES:
        total += 1
        content = "K" * length
        parts = split_message_content(content, 2000)
        valid = (
            len(parts) == expected_parts
            and all(0 < len(part) <= 2000 for part in parts)
            and "".join(parts) == content
        )
        if not valid:
            failure_count += 1
            if len(failures) < 25:
                failures.append(
                    f"Longitud {length}: bloques esperados {expected_parts}, "
                    f"recibidos {len(parts)}"
                )
    return {
        "total": total,
        "passed": total - failure_count,
        "failed": failure_count,
        "failures": failures
    }


def run_katherine_self_tests() -> dict:
    """Ejecuta las pruebas de división de mensajes y utilidades de Genshin."""
    message_results = run_message_split_regression()
    failures = list(message_results["failures"])
    checks = message_results["total"]
    passed = message_results["passed"]

    percentage_tests = (
        ("75", 75.0),
        ("75%", 75.0),
        ("75,5", 75.5),
        ("75,5%", 75.5),
        ("0", 0.0),
        ("100%", 100.0),
        ("12.34%", 12.34),
        (" 50 ", 50.0)
    )
    for raw, expected in percentage_tests:
        checks += 1
        try:
            good = abs(parse_percent(raw) - expected) < 0.000001
        except (TypeError, ValueError):
            good = False
        if good:
            passed += 1
        elif len(failures) < 25:
            failures.append(f"parse_percent({raw!r})")

    number_tests = (
        (0, "0"),
        (1, "1"),
        (1000, "1.000"),
        (1000000, "1.000.000"),
        (None, "—"),
        ("invalid", "—")
    )
    for raw, expected in number_tests:
        checks += 1
        if format_number(raw) == expected:
            passed += 1
        elif len(failures) < 25:
            failures.append(f"format_number({raw!r})")

    fake_stats = {
        "1": {"hp": 100},
        "20": {"hp": 500},
        "20+": {"hp": 700},
        "40": {"hp": 1000},
        "40+": {"hp": 1200},
        "50": {"hp": 1500},
        "50+": {"hp": 1800},
        "60": {"hp": 2100},
        "60+": {"hp": 2400},
        "70": {"hp": 2700},
        "70+": {"hp": 3000},
        "80": {"hp": 3300},
        "80+": {"hp": 3600},
        "90": {"hp": 4000}
    }
    for level, expected in fake_stats.items():
        checks += 1
        if get_level_stats(fake_stats, level) == expected:
            passed += 1
        elif len(failures) < 25:
            failures.append(f"get_level_stats({level})")

    for invalid in ("0", "100", "abc", "80++", "-1", "20.5"):
        checks += 1
        if get_level_stats(fake_stats, invalid) is None:
            passed += 1
        elif len(failures) < 25:
            failures.append(f"get_level_stats invalid({invalid})")

    sample_materials = [
        {"name": "Fragmento de prueba", "count": 2},
        {"name": "Fragmento de prueba", "count": 3},
        {"name": "Núcleo de prueba", "count": 7}
    ]
    aggregate = aggregate_material_costs(sample_materials)
    checks += 2
    if aggregate.get("Fragmento de prueba") == 5:
        passed += 1
    else:
        failures.append("aggregate_material_costs suma duplicados")
    if aggregate.get("Núcleo de prueba") == 7:
        passed += 1
    else:
        failures.append("aggregate_material_costs conserva materiales distintos")

    sample_guide_html = """
    <h2>Armas recomendadas</h2><ul><li>Espada de prueba</li></ul>
    <h2>Mejores artefactos</h2><ul><li>Set de prueba</li></ul>
    <h2>Estadísticas principales</h2><p>Reloj ATQ%, Copa Daño Cryo, Corona Prob. CRIT</p>
    <h2>Equipos recomendados</h2><a>Skirk</a><a>Furina</a><a>Escoffier</a><a>Shenhe</a>
    <h2>Rotación</h2><p>Habilidad, definitiva y ataques normales.</p>
    """
    parsed_guide = parse_community_guide_page(
        sample_guide_html, "https://example.invalid/guide",
        {"skirk", "furina", "escoffier", "shenhe"}
    )
    parser_checks = (
        ("weapons", bool(parsed_guide and parsed_guide.get("weapons"))),
        ("artifacts", bool(parsed_guide and parsed_guide.get("artifacts"))),
        ("stats", bool(parsed_guide and parsed_guide.get("stats"))),
        ("teams", bool(parsed_guide and parsed_guide.get("teams"))),
        ("rotation", bool(parsed_guide and parsed_guide.get("rotation")))
    )
    for label, good in parser_checks:
        checks += 1
        if good:
            passed += 1
        elif len(failures) < 25:
            failures.append(f"parser de guías: {label}")

    sample_html = "<p>Texto de prueba</p><br><strong>Genshin</strong>"
    cleaned = clean_game_text(sample_html)
    checks += 1
    if "<" not in cleaned and "Texto de prueba" in cleaned and "Genshin" in cleaned:
        passed += 1
    else:
        failures.append("clean_game_text elimina etiquetas")

    return {
        "checks": checks,
        "passed": passed,
        "failed": checks - passed,
        "failures": failures[:25]
    }


def target_from_message(msg, args, scope_key: str | None = None):
    mentions = msg.get("mentions") or []

    # La API puede entregar distintos campos según el tipo de mención.
    for mention in mentions:
        if not isinstance(mention, dict):
            continue

        raw_uid = (
            mention.get("uid")
            or mention.get("userId")
            or mention.get("id")
        )
        nickname = (
            mention.get("nickname")
            or mention.get("username")
            or mention.get("displayName")
            or mention.get("name")
        )

        if raw_uid:
            return (
                scoped_uid(scope_key or "", str(raw_uid)),
                str(nickname or raw_uid).lstrip("@")
            )

        if nickname:
            return None, str(nickname).lstrip("@")

    # También reconoce menciones escritas como @usuario o <@usuario>.
    raw_text = " ".join(str(x) for x in (args or []))
    match = re.search(r"@([^\s,;]+)", raw_text)

    if match:
        nickname = match.group(1).rstrip(">.,;:!?)")
        found = find_user_by_nickname(nickname, scope_key)

        if found:
            return found["uid"], found["nickname"]

        return None, nickname

    return None, None


# ---------------------------------------------------------------------------
# EQUIPOS Y ROTACIONES PREDEFINIDAS
# ---------------------------------------------------------------------------
BUILD_TEAMS = {
    "cyno": (
        "Cyno · Nahida · Baizhu · Furina",
        "Nahida aplica Dendro; Furina aporta daño y aplicación Hydro; "
        "Baizhu protege y cura; Cyno mantiene la ventana de daño."
    ),
    "nahida": (
        "Nahida · Kuki Shinobu · Xingqiu · Yelan",
        "Nahida conduce la reacción; Kuki activa Hyperbloom; "
        "Xingqiu y Yelan mantienen Hydro."
    ),
    "hu tao": (
        "Hu Tao · Xingqiu · Yelan · Zhongli",
        "Zhongli protege; Xingqiu y Yelan aplican Hydro; "
        "Hu Tao realiza sus ataques cargados durante la habilidad."
    ),
    "raiden": (
        "Raiden · Xiangling · Bennett · Xingqiu",
        "Xingqiu prepara Hydro; Bennett usa definitiva; "
        "Xiangling lanza Guoba y Pyronado; Raiden cierra con su definitiva."
    ),
    "neuvillette": (
        "Neuvillette · Furina · Kazuha · Baizhu",
        "Furina prepara su habilidad y definitiva; Kazuha agrupa y dispersa; "
        "Baizhu protege; Neuvillette ejecuta sus cargados."
    ),
    "arlecchino": (
        "Arlecchino · Bennett · Kazuha · Zhongli",
        "Zhongli protege; Bennett potencia; Kazuha agrupa y reduce resistencias; "
        "Arlecchino ejecuta su ventana de daño."
    ),
    "alhaitham": (
        "Alhacén · Nahida · Kuki Shinobu · Xingqiu",
        "Doble Dendro aporta energía; Kuki activa Sobreflorecimiento; Xingqiu mantiene Hydro."
    ),
    "furina": (
        "Furina · Neuvillette · Kazuha · Baizhu",
        "Furina potencia al equipo; Kazuha agrupa y dispersa Hydro; Baizhu cura y protege."
    ),
    "navia": (
        "Navia · Zhongli · Bennett · Xiangling",
        "Doble Geo facilita energía; Pyro permite Cristalización; Navia consume fragmentos."
    ),
    "nilou": (
        "Nilou · Nahida · Kokomi · Collei",
        "Equipo Hydro/Dendro para Núcleos Abundantes, con aplicación constante y curación."
    ),
    "ayaka": (
        "Ayaka · Shenhe · Kokomi · Kazuha",
        "Congelación, reducción de resistencia Cryo, agrupación y aplicación Hydro."
    ),
    "ganyu": (
        "Ganyu · Mona · Diona · Venti",
        "Equipo de Congelación con agrupación; adapta la composición a enemigos inmunes al control."
    ),
    "xiao": (
        "Xiao · Faruzan · Xianyun · Furina",
        "Faruzan potencia Anemo; Xianyun mejora ataques descendentes y cura; Furina aporta daño."
    ),
    "wanderer": (
        "Errante · Faruzan · Bennett · Zhongli",
        "Faruzan mejora el daño Anemo; Bennett potencia; Zhongli protege durante los ataques."
    ),
    "childe": (
        "Tartaglia · Xiangling · Bennett · Kazuha",
        "Aplicación Hydro amplia para Vaporización; Xiangling concentra el daño fuera de campo."
    ),
    "itto": (
        "Itto · Gorou · Albedo · Zhongli",
        "Triple Geo aprovecha las bonificaciones de Gorou y la resonancia Geo."
    ),
    "wriothesley": (
        "Wriothesley · Furina · Charlotte · Kazuha",
        "Furina aporta daño e Hydro; Charlotte cura; Kazuha agrupa y potencia Cryo."
    )
}

ARTIFACT_GUIDES = {
    "cyno": {
        "name": "Cyno",
        "sets": "4 piezas Sueños Áureos o 4 piezas Furia del Trueno",
        "main": "Reloj: EM o ATQ% · Copa: Bono D Electro · Corona: CRIT",
        "subs": "Prob. CRIT, Daño CRIT, EM, Recarga y ATQ%",
        "note": "Ajusta la Maestría Elemental y la Recarga según el equipo y la duración de la rotación."
    },
    "nahida": {
        "name": "Nahida",
        "sets": "4 piezas Recuerdos del Bosque si nadie más lo lleva; 4 piezas Sueños Áureos como alternativa",
        "main": "EM / EM / EM o EM / Dendro / CRIT según equipo y arma",
        "subs": "EM, CRIT, Recarga y ATQ%",
        "note": "No es obligatorio llegar a 1.000 EM en todas las composiciones; busca equilibrio."
    },
    "hu tao": {
        "name": "Hu Tao",
        "sets": "4 piezas Bruja Carmesí en Llamas o 4 piezas Reminiscencia de la Purificación",
        "main": "EM o Vida% / Bono D Pyro / CRIT",
        "subs": "CRIT, EM, Vida% y Recarga moderada",
        "note": "La Maestría Elemental gana mucho valor en equipos de Vaporización."
    },
    "raiden": {
        "name": "Shogun Raiden",
        "sets": "4 piezas Emblema del Destino",
        "main": "Recarga o ATQ% / Bono D Electro o ATQ% / CRIT",
        "subs": "CRIT, Recarga, ATQ% y EM en equipos de reacción",
        "note": "El arma y el equipo cambian la mejor combinación de reloj y copa."
    },
    "neuvillette": {
        "name": "Neuvillette",
        "sets": "4 piezas Cazador Fantasmal",
        "main": "Vida% / Bono D Hydro / Daño CRIT o Prob. CRIT",
        "subs": "Daño CRIT, Prob. CRIT, Vida% y Recarga",
        "note": "Controla la Prob. CRIT efectiva cuando utilizas Cazador Fantasmal."
    },
    "arlecchino": {
        "name": "Arlecchino",
        "sets": "4 piezas Fragmento de la Armonía Fantasiosa; Bruja Carmesí como alternativa",
        "main": "ATQ% / Bono D Pyro / CRIT",
        "subs": "CRIT, ATQ%, EM para Vaporización y Recarga moderada",
        "note": "Administra el pacto vital y adapta las estadísticas a su rotación."
    },
    "furina": {
        "name": "Furina",
        "sets": "4 piezas Compañía Dorada",
        "main": "Vida% o Recarga / Bono D Hydro o Vida% / CRIT",
        "subs": "Recarga, CRIT y Vida%",
        "note": "Asegura la Recarga necesaria para mantener la definitiva."
    },
    "yelan": {
        "name": "Yelan",
        "sets": "4 piezas Emblema del Destino",
        "main": "Vida% o Recarga / Bono D Hydro / CRIT",
        "subs": "Recarga, CRIT y Vida%",
        "note": "La Recarga requerida depende del arma y de las partículas del equipo."
    },
    "xingqiu": {
        "name": "Xingqiu",
        "sets": "4 piezas Emblema del Destino",
        "main": "ATQ% o Recarga / Bono D Hydro / CRIT",
        "subs": "Recarga hasta cubrir la rotación, después CRIT y ATQ%",
        "note": "Con Espada de Sacrificio suele necesitar menos Recarga."
    },
    "xiangling": {
        "name": "Xiangling",
        "sets": "4 piezas Emblema del Destino",
        "main": "Recarga o EM / Bono D Pyro / CRIT",
        "subs": "Recarga, CRIT, EM y ATQ%",
        "note": "La Recarga es importante porque su daño principal viene de la definitiva."
    },
    "kazuha": {
        "name": "Kaedehara Kazuha",
        "sets": "4 piezas Sombra Verde Esmeralda",
        "main": "EM / EM / EM",
        "subs": "Recarga, EM y Prob. CRIT si utiliza Favonius",
        "note": "La reducción de resistencia requiere que Kazuha active Dispersión."
    },
    "baizhu": {
        "name": "Baizhu",
        "sets": "4 piezas Recuerdos del Bosque si nadie más lo lleva; 2+2 Vida% o 4 piezas Molusco",
        "main": "Vida% o Recarga / Vida% / Vida% o Bono de Curación",
        "subs": "Vida%, Recarga y Vida plana",
        "note": "Cubre la Recarga necesaria y aprovecha sus pasivas de curación."
    },
    "fischl": {
        "name": "Fischl",
        "sets": "4 piezas Compañía Dorada",
        "main": "ATQ% / Bono D Electro / CRIT",
        "subs": "CRIT, ATQ% y EM en equipos de Aceleración",
        "note": "Su daño fuera de campo aprovecha las bonificaciones de habilidad elemental."
    },
    "alhaitham": {
        "name": "Alhacén",
        "sets": "4 piezas Sueños Áureos o 4 piezas Recuerdos del Bosque si es quien lo activa",
        "main": "EM o ATQ% / Bono D Dendro / CRIT",
        "subs": "CRIT, EM, Recarga y ATQ%",
        "note": "Busca EM y CRIT sin descuidar la disponibilidad de su definitiva."
    },
    "navia": {
        "name": "Navia",
        "sets": "4 piezas Murmullo Nocturno en el Bosque de los Ecos",
        "main": "ATQ% / Bono D Geo / CRIT",
        "subs": "CRIT, ATQ% y Recarga",
        "note": "Mantén aplicación elemental para generar fragmentos de Cristalización."
    },
    "nilou": {
        "name": "Nilou",
        "sets": "2 piezas Vida% + 2 piezas Vida%; 4 piezas Flor Olvidada del Paraíso como alternativa",
        "main": "Vida% / Vida% o EM / Vida%",
        "subs": "Vida% hasta el objetivo de la pasiva, luego EM y Recarga",
        "note": "Su pasiva especial requiere un equipo compuesto solo por Hydro y Dendro."
    },
    "ayaka": {
        "name": "Kamisato Ayaka",
        "sets": "4 piezas Nómada del Invierno",
        "main": "ATQ% / Bono D Cryo / Daño CRIT",
        "subs": "Daño CRIT, ATQ%, Recarga y Prob. CRIT moderada",
        "note": "Congelación y el conjunto aportan Prob. CRIT adicional."
    },
    "ganyu": {
        "name": "Ganyu",
        "sets": "4 piezas Nómada del Invierno en Congelación o 4 piezas Orquesta del Errante en Derretido",
        "main": "ATQ% / Bono D Cryo / CRIT",
        "subs": "CRIT, ATQ%, EM en Derretido y Recarga",
        "note": "Adapta las estadísticas al equipo y al tipo de rotación."
    },
    "xiao": {
        "name": "Xiao",
        "sets": "4 piezas Vermis del Ocaso o combinaciones de 2 piezas ATQ% / Anemo",
        "main": "ATQ% / Bono D Anemo / CRIT",
        "subs": "CRIT, ATQ% y Recarga",
        "note": "Necesita batería y una rotación estable para recuperar su definitiva."
    },
    "wanderer": {
        "name": "Errante",
        "sets": "4 piezas Crónica del Pabellón del Desierto o 4 piezas Cazador Fantasmal con Furina",
        "main": "ATQ% / Bono D Anemo / CRIT",
        "subs": "CRIT, ATQ% y Recarga",
        "note": "La presencia de Furina y el tipo de equipo pueden cambiar el conjunto óptimo."
    }
}



WEAPON_GUIDES = {
    "cyno": {
        "name": "Cyno",
        "weapons": [
            "Báculo de las Arenas Escarlatas",
            "Lanza Primordial de Jade",
            "Balada de los Fiordos",
            "Combate a Muerte",
            "Borla Blanca"
        ],
        "note": "El Báculo aprovecha la Maestría Elemental. Balada de los Fiordos y Borla Blanca son opciones accesibles."
    },
    "nahida": {
        "name": "Nahida",
        "weapons": [
            "Sueños de las Mil Noches",
            "Axioma de la Kagura",
            "Perla Solar",
            "Fragmentos de Sacrificio",
            "Guía Mágica"
        ],
        "note": "Prioriza Maestría Elemental sin descuidar CRIT cuando Nahida juega en campo."
    },
    "hu tao": {
        "name": "Hu Tao",
        "weapons": [
            "Báculo de Homa",
            "Báculo de las Arenas Escarlatas",
            "Perdición del Dragón",
            "Balada de los Fiordos",
            "Combate a Muerte"
        ],
        "note": "Perdición del Dragón funciona muy bien en Vaporización; valora EM, Vida y CRIT."
    },
    "raiden": {
        "name": "Shogun Raiden",
        "weapons": [
            "Luz del Segador",
            "La Captura",
            "Aleta Cortacielos",
            "Báculo de Homa",
            "Lanza de Favonius"
        ],
        "note": "La Captura es una opción gratuita destacada. Ajusta ATQ y Recarga según el equipo."
    },
    "neuvillette": {
        "name": "Neuvillette",
        "weapons": [
            "Tomo del Flujo Eterno",
            "Sacrificio de Jade",
            "Oración Perdida a los Vientos Sagrados",
            "Prototipo Ámbar",
            "Anillo de Hakushin"
        ],
        "note": "Prototipo Ámbar aporta Vida, energía y curación; controla la Prob. CRIT con Cazador Fantasmal."
    },
    "arlecchino": {
        "name": "Arlecchino",
        "weapons": [
            "Semblante de la Luna Carmesí",
            "Lanza Primordial de Jade",
            "Báculo de Homa",
            "Combate a Muerte",
            "Borla Blanca"
        ],
        "note": "Busca ATQ y CRIT; la Maestría Elemental gana valor en equipos de Vaporización."
    },
    "furina": {
        "name": "Furina",
        "weapons": [
            "Esplendor de las Aguas Quietas",
            "Deseo Ponzoñoso",
            "Fleuve Cendre Ferryman",
            "Espada de Favonius",
            "Cortador de Jade Primordial"
        ],
        "note": "La Recarga es prioritaria para mantener la definitiva durante todas las rotaciones."
    },
    "yelan": {
        "name": "Yelan",
        "weapons": [
            "Aqua Simulacra",
            "Elegía del Fin",
            "Arco de Favonius",
            "Arco del Sacrificio",
            "Arco Recurvado"
        ],
        "note": "Aqua potencia el daño; Favonius y Sacrificio ayudan a cubrir necesidades de energía."
    },
    "xingqiu": {
        "name": "Xingqiu",
        "weapons": [
            "Espada del Sacrificio",
            "Espada de Favonius",
            "Cortador de Jade Primordial",
            "Amenoma Kageuchi"
        ],
        "note": "La Recarga permite lanzar la definitiva a tiempo; Sacrificio facilita reiniciar la habilidad."
    },
    "xiangling": {
        "name": "Xiangling",
        "weapons": [
            "Luz del Segador",
            "La Captura",
            "Perdición del Dragón",
            "Aleta Cortacielos",
            "Lanza de Favonius"
        ],
        "note": "La Captura es una alternativa excelente; en Vaporización también importa la Maestría Elemental."
    },
    "kazuha": {
        "name": "Kaedehara Kazuha",
        "weapons": [
            "Juramento por la Libertad",
            "Luz Lunar de Xiphos",
            "Espada de Favonius",
            "Picadura de Hierro"
        ],
        "note": "En soporte suele priorizar Maestría Elemental y Recarga antes que el daño personal."
    },
    "baizhu": {
        "name": "Baizhu",
        "weapons": [
            "Esplendor del Jade Caído",
            "Prototipo Ámbar",
            "Códice de Favonius",
            "Memorias de Sacrificio"
        ],
        "note": "Vida y Recarga son sus prioridades habituales; Prototipo Ámbar es una opción accesible."
    },
    "fischl": {
        "name": "Fischl",
        "weapons": [
            "Agitador del Relámpago",
            "Arpa Celestial",
            "Cazador del Callejón",
            "El Sin Cuerda",
            "Honda"
        ],
        "note": "El Sin Cuerda mejora la habilidad y las reacciones; ajusta EM según la composición."
    },
    "alhaitham": {
        "name": "Alhacén",
        "weapons": [
            "Luz de Incisión Foliar",
            "Cortador de Jade Primordial",
            "Picadura de Hierro",
            "Colmillo del Lobo",
            "Toukabou Shigure"
        ],
        "note": "Combina Maestría Elemental y CRIT; el arma ideal depende del equilibrio de estadísticas."
    },
    "navia": {
        "name": "Navia",
        "weapons": [
            "Veredicto",
            "Espina de la Serpiente",
            "Sombra de Marea",
            "Prototipo Arcaico"
        ],
        "note": "ATQ y CRIT son sus prioridades; Sombra de Marea funciona con una fuente de curación."
    },
    "nilou": {
        "name": "Nilou",
        "weapons": [
            "Llave de la Bóveda de Khaj-Nisut",
            "Asistente del Estibador",
            "Espada de Favonius",
            "Xiphos"
        ],
        "note": "En equipos de Núcleos Abundantes, maximiza Vida hasta el límite de su pasiva."
    },
    "ayaka": {
        "name": "Kamisato Ayaka",
        "weapons": [
            "Reforjadora de la Niebla",
            "Amenoma Kageuchi",
            "Final de las Profundidades",
            "Cortador de Jade Primordial"
        ],
        "note": "La Recarga depende del equipo; aprovecha los bonos de CRIT de Congelación cuando corresponda."
    },
    "ganyu": {
        "name": "Ganyu",
        "weapons": [
            "Arco de Amos",
            "Aqua Simulacra",
            "Prototipo de la Media Luna",
            "Hamayumi"
        ],
        "note": "El conjunto y el arma cambian según juegue en Congelación o Derretido."
    },
    "xiao": {
        "name": "Xiao",
        "weapons": [
            "Lanza Primordial de Jade",
            "Báculo de Homa",
            "Combate a Muerte",
            "Balada de los Fiordos"
        ],
        "note": "Necesita ATQ, CRIT y suficiente Recarga o una batería Anemo fiable."
    },
    "wanderer": {
        "name": "Errante",
        "weapons": [
            "Reminiscencia de Tulaytullah",
            "Oración Perdida a los Vientos Sagrados",
            "Atlas Celestial",
            "Sinfonía de los Merodeadores",
            "Cuentos de Dodoco"
        ],
        "note": "La Sinfonía ofrece un rendimiento variable por sus efectos aleatorios."
    }
}

TALENT_PRIORITIES = {
    "cyno": ("Habilidad definitiva (Q) > Habilidad elemental (E) > Ataque normal", "La definitiva mejora la postura de Pactsworn Pathclearer; los ataques normales dentro de ella aprovechan su nivel."),
    "nahida": ("Habilidad elemental (E) > Definitiva (Q) > Ataque normal", "Su habilidad aporta gran parte del daño y aplicación Dendro fuera de campo."),
    "hu tao": ("Ataque normal > Habilidad elemental (E) > Definitiva (Q)", "Los ataques cargados potenciados son su fuente principal de daño."),
    "raiden": ("Definitiva (Q) > Habilidad elemental (E) > Ataque normal", "La definitiva concentra su ventana de daño; la E apoya al equipo."),
    "neuvillette": ("Ataque normal > Definitiva (Q) ≈ Habilidad elemental (E)", "El ataque cargado potenciado es el núcleo de su daño."),
    "arlecchino": ("Ataque normal > Habilidad elemental (E) > Definitiva (Q)", "La prioridad refleja su daño durante el pacto vital."),
    "furina": ("Definitiva (Q) > Habilidad elemental (E) > Ataque normal", "La definitiva potencia al equipo; la habilidad mantiene daño Hydro fuera de campo."),
    "yelan": ("Definitiva (Q) > Habilidad elemental (E) > Ataque normal", "La definitiva concentra su aplicación y daño fuera de campo."),
    "xingqiu": ("Definitiva (Q) > Habilidad elemental (E) > Ataque normal", "La definitiva aporta gran parte de la aplicación Hydro y el daño."),
    "xiangling": ("Definitiva (Q) > Habilidad elemental (E) > Ataque normal", "Pyronado es su principal fuente de daño."),
    "kazuha": ("Definitiva (Q) ≈ Habilidad elemental (E) > Ataque normal", "En soporte de EM los talentos pueden tener menor prioridad que subirlo al nivel 90."),
    "baizhu": ("Habilidad elemental (E) ≈ Definitiva (Q) > Ataque normal", "Sube E/Q según cuánto necesite el equipo sus curas y escudos."),
    "fischl": ("Habilidad elemental (E) > Definitiva (Q) > Ataque normal", "Oz aporta el daño fuera de campo; Q ayuda a renovar su presencia."),
    "alhaitham": ("Habilidad elemental (E) > Definitiva (Q) ≈ Ataque normal", "Su daño se reparte entre espejos, ataques y definitiva."),
    "navia": ("Habilidad elemental (E) > Definitiva (Q) > Ataque normal", "Su habilidad elemental concentra el daño de los fragmentos."),
    "nilou": ("Habilidad elemental (E) > Definitiva (Q) > Ataque normal", "En equipos de Núcleos Abundantes suele priorizarse su pasiva y estadísticas antes que talentos."),
    "ayaka": ("Definitiva (Q) > Ataque normal > Habilidad elemental (E)", "La definitiva es su principal fuente de daño en el rol de DPS."),
    "ganyu": ("Ataque normal > Definitiva (Q) > Habilidad elemental (E)", "En cargados se prioriza el ataque normal; en soporte cambia la prioridad."),
    "xiao": ("Ataque normal > Definitiva (Q) > Habilidad elemental (E)", "El nivel de ataque normal mejora los golpes descendentes durante la definitiva."),
    "wanderer": ("Ataque normal > Habilidad elemental (E) > Definitiva (Q)", "Los ataques potenciados durante la habilidad forman su ciclo principal.")
}


# ---------------------------------------------------------------------------
# GESTOR PRINCIPAL DE COMANDOS
# ---------------------------------------------------------------------------

CONSTELLATION_GUIDES = {
    "cyno": ("C2 es una mejora ofensiva importante; C0 ya funciona.", "C1 mejora fluidez y C2 potencia Electro; no son necesarias."),
    "nahida": ("C2 es su constelación de mayor impacto general.", "C0 ya ofrece gran utilidad; C1 y C2 son mejoras opcionales."),
    "hu tao": ("C1 mejora comodidad y ejecución; C0 funciona.", "Reduce el coste de resistencia de ataques cargados durante su habilidad."),
    "raiden": ("C2 es una mejora opcional de daño personal.", "C0 cumple como soporte y conductora."),
    "neuvillette": ("C1 destaca por resistencia a interrupción y comodidad.", "C0 ya es fuerte con protección adecuada."),
    "arlecchino": ("C1 es una mejora ofensiva notable; C0 es funcional.", "No es requisito para aprovechar su rol."),
    "furina": ("C2 mejora mucho la acumulación de Fanfarria.", "C0 ya aporta daño y potenciación."),
    "yelan": ("C1 mejora la disponibilidad de habilidad; C2 potencia aplicación y daño.", "C0 funciona bien."),
    "xingqiu": ("C6 es una de sus constelaciones más destacadas.", "Las copias se consiguen con el tiempo; no es requisito."),
    "xiangling": ("C4 es su constelación más destacada.", "Aumenta la duración de Pyronado; C0 funciona."),
    "kazuha": ("C0 suele ser suficiente para su rol de soporte.", "Las constelaciones no son necesarias para su función principal."),
    "fischl": ("C6 aporta una mejora importante con ataques coordinados.", "C0 ya es una gran sub-DPS."),
    "alhaitham": ("C0 es suficiente para su rol principal.", "Prioriza equipo, arma y estadísticas."),
    "navia": ("C0 es funcional; C1 ayuda a energía y rotación.", "Las constelaciones son mejoras opcionales."),
    "nilou": ("C0 activa su mecánica central.", "C2 puede potenciar reacciones, pero C0 cumple su función."),
    "ayaka": ("C0 funciona; las constelaciones altas requieren mucha inversión.", "C2 puede potenciar su definitiva en situaciones adecuadas."),
    "ganyu": ("C0 es completamente jugable; C1 mejora daño y energía.", "No son obligatorias para sus equipos."),
    "xiao": ("C0 es suficiente; C6 es muy específica.", "No priorices C6 como primera inversión."),
    "wanderer": ("C0 es suficiente; C1 y C2 son mejoras progresivas.", "La prioridad sigue siendo una buena build.")
}
CHARACTER_GUIDE_ALIASES = {
    "hutao": "hu tao", "shogun raiden": "raiden", "raiden shogun": "raiden",
    "kamisato ayaka": "ayaka", "kaedehara kazuha": "kazuha",
    "alhacén": "alhaitham", "alhacen": "alhaitham",
    "errante": "wanderer", "scaramouche": "wanderer",
    "arconte dendro": "nahida"
}


# ---------------------------------------------------------------------------
# FUENTES SECUNDARIAS PARA GUIAS DE PERSONAJES
# ---------------------------------------------------------------------------
HELPER_BUILDS_TREE_URL = (
    "https://api.github.com/repos/Genshin-Impact-Helper-Team/"
    "genshin-builds/git/trees/main?recursive=1"
)
HELPER_BUILDS_RAW_BASE = (
    "https://raw.githubusercontent.com/Genshin-Impact-Helper-Team/"
    "genshin-builds/main/"
)
HELPER_BUILDS_CACHE_TTL = 21600
KQM_QUICKGUIDE_BASE = "https://keqingmains.com/q/"

HELPER_CHARACTER_SLUGS = {
    "raiden": "shogun-raiden",
    "raiden-shogun": "shogun-raiden",
    "kazuha": "kaedehara-kazuha",
    "ayaka": "kamisato-ayaka",
    "ayato": "kamisato-ayato",
    "itto": "arataki-itto",
    "kokomi": "sangonomiya-kokomi",
    "sara": "kujou-sara",
    "heizou": "shikanoin-heizou",
    "yae": "yae-miko",
    "miko": "yae-miko",
    "childe": "tartaglia",
    "scaramouche": "wanderer",
    "hutao": "hu-tao",
    "yunjin": "yun-jin",
    "yun-jin": "yun-jin",
    "viajero": "traveler",
    "traveler": "traveler"
}


def guide_slugify(value: Any) -> str:
    """Normaliza nombres localizados para buscar carpetas de personajes."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return HELPER_CHARACTER_SLUGS.get(slug, slug)


async def fetch_helper_build_json(path: str):
    """Descarga y cachea un JSON público del repositorio Genshin Builds."""
    if not path or ".." in path.split("/") or not path.startswith("src/"):
        return None

    key = f"helper-builds:{path}"
    cached = cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        response = await external_request(
            "GET",
            HELPER_BUILDS_RAW_BASE + path,
            timeout=15,
            headers={"User-Agent": "Katherine-AnarchyBot/3.0"}
        )
        if response.status_code != 200:
            return None
        data = response.json()
        cache[key] = (time.time() + HELPER_BUILDS_CACHE_TTL, data)
        return data
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logging.debug("Genshin Builds JSON no disponible (%s): %s", path, exc)
        return None


async def get_helper_build_tree():
    """Obtiene una sola vez el índice de guías publicado en GitHub."""
    key = "helper-builds:tree"
    cached = cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        response = await external_request(
            "GET",
            HELPER_BUILDS_TREE_URL,
            timeout=22,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Katherine-AnarchyBot/3.0"
            }
        )
        if response.status_code != 200:
            logging.warning(
                "Genshin Builds tree respondió HTTP %s",
                response.status_code
            )
            return None

        payload = response.json()
        entries = payload.get("tree") if isinstance(payload, dict) else None
        if not isinstance(entries, list) or payload.get("truncated"):
            logging.warning("El índice remoto de builds está incompleto.")
            return None

        paths = {
            entry.get("path")
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and isinstance(entry.get("path"), str)
        }
        result = {
            "paths": paths,
            "tree_sha": payload.get("sha")
        }
        cache[key] = (time.time() + HELPER_BUILDS_CACHE_TTL, result)
        return result
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logging.warning("No se pudo leer el índice de Genshin Builds: %s", exc)
        return None


def helper_character_candidates(character: dict | None, query: str, name: str, key: str):
    raw_values = [name, query, key]
    if isinstance(character, dict):
        raw_values.extend([
            character.get("id"),
            character.get("name"),
            character.get("fullname")
        ])

    candidates = []
    for value in raw_values:
        slug = guide_slugify(value)
        if slug and slug not in candidates:
            candidates.append(slug)
    return candidates


def helper_element_slug(character: dict | None) -> str | None:
    if not isinstance(character, dict):
        return None

    raw = (
        character.get("elementText")
        or character.get("element")
        or character.get("vision")
        or ""
    )
    slug = guide_slugify(raw)
    return slug if slug in {
        "anemo", "cryo", "dendro", "electro", "geo", "hydro", "pyro"
    } else None


async def load_helper_character_file(root: str, build_name: str, filename: str, paths: set):
    candidates = []
    if build_name and build_name != "__character__":
        candidates.append(f"{root}/{build_name}/{filename}")
    candidates.append(f"{root}/{filename}")

    for candidate in candidates:
        if candidate in paths:
            return await fetch_helper_build_json(candidate)
    return None


async def load_helper_build_translations():
    files = {
        "weapons": "src/i18n/es/weapons.json",
        "artifacts": "src/i18n/es/artifact-sets.json",
        "stats": "src/i18n/es/stats.json",
        "abilities": "src/i18n/es/abilities.json"
    }
    names = list(files)
    values = await asyncio.gather(
        *(fetch_helper_build_json(files[name]) for name in names)
    )
    return {
        name: value if isinstance(value, dict) else {}
        for name, value in zip(names, values)
    }


def helper_translate_id(value: Any, category: str, translations: dict) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("id") or ""
    if not isinstance(value, str) or not value.strip():
        return "No informado"

    key = value.strip()
    dictionary = translations.get(category, {})
    if key in dictionary:
        return str(dictionary[key])

    if category == "artifacts":
        stats = translations.get("stats", {})
        if key in stats:
            return str(stats[key])
    if category == "stats":
        stats = translations.get("stats", {})
        if key in stats:
            return str(stats[key])

    return key.replace("-", " ").replace("_", " ").title()


def helper_format_weapons(data: Any, translations: dict, build_label: str) -> str | None:
    if not isinstance(data, dict):
        return None
    ranking = data.get("weapons")
    if not isinstance(ranking, list):
        return None

    rows = []
    for position, group in enumerate(ranking[:8], 1):
        if not isinstance(group, dict):
            continue
        items = group.get("items") or []
        if not isinstance(items, list):
            continue
        names = []
        for item in items:
            name = helper_translate_id(item, "weapons", translations)
            if name != "No informado" and name not in names:
                names.append(name)
        if names:
            rows.append(f"{position}. " + " / ".join(names))

    if not rows:
        return None
    return f"{build_label}\n" + "\n".join(rows)


def helper_format_artifacts(sets_data: Any, main_data: Any, sub_data: Any,
                            translations: dict, build_label: str) -> str | None:
    lines = [build_label]
    has_data = False

    if isinstance(sets_data, dict):
        ranked_sets = sets_data.get("artifact_sets") or []
        if isinstance(ranked_sets, list):
            set_rows = []
            for rank, ranking in enumerate(ranked_sets[:5], 1):
                if not isinstance(ranking, dict):
                    continue
                for group in (ranking.get("groups") or [])[:3]:
                    if not isinstance(group, dict):
                        continue
                    items = group.get("items") or []
                    if not isinstance(items, list):
                        continue
                    rendered = []
                    for item in items:
                        if isinstance(item, str):
                            item = {"name": item}
                        if not isinstance(item, dict):
                            continue
                        item_name = helper_translate_id(
                            item.get("name"), "artifacts", translations
                        )
                        pieces = item.get("pieces")
                        rendered.append(
                            f"{pieces} piezas {item_name}"
                            if pieces else item_name
                        )
                    if rendered:
                        separator = " / " if group.get("choose") else " + "
                        set_rows.append(f"{rank}. " + separator.join(rendered))
            if set_rows:
                lines.append("Sets:\n" + "\n".join(set_rows[:8]))
                has_data = True

    if isinstance(main_data, dict):
        main_stats = main_data.get("main_stats") or {}
        if isinstance(main_stats, dict):
            stat_labels = (
                ("sands", "Reloj"),
                ("goblet", "Copa"),
                ("circlet", "Corona")
            )
            stat_rows = []
            for field, label in stat_labels:
                values = main_stats.get(field) or []
                if not isinstance(values, list):
                    continue
                names = [
                    helper_translate_id(item, "stats", translations)
                    for item in values
                ]
                names = [name for name in names if name != "No informado"]
                if names:
                    stat_rows.append(f"{label}: " + " / ".join(names))
            if stat_rows:
                lines.append("Stats principales:\n" + "\n".join(stat_rows))
                has_data = True

    if isinstance(sub_data, dict):
        priorities = sub_data.get("substats_priority") or []
        if isinstance(priorities, list):
            sub_rows = []
            for item in priorities:
                if isinstance(item, dict) and isinstance(item.get("items"), list):
                    values = item["items"]
                else:
                    values = [item]
                names = [
                    helper_translate_id(value, "stats", translations)
                    for value in values
                ]
                names = [name for name in names if name != "No informado"]
                if names:
                    sub_rows.append(" / ".join(names))
            if sub_rows:
                lines.append("Prioridad de substats: " + " > ".join(sub_rows))
                has_data = True

    return "\n".join(lines) if has_data else None


def helper_format_talents(data: Any, translations: dict, build_label: str) -> str | None:
    if not isinstance(data, dict):
        return None
    ranking = data.get("talents")
    if not isinstance(ranking, list):
        return None

    priorities = []
    for group in ranking:
        if not isinstance(group, dict):
            continue
        items = group.get("items") or []
        if not isinstance(items, list):
            continue
        names = [
            helper_translate_id(item, "abilities", translations)
            for item in items
        ]
        names = [name for name in names if name != "No informado"]
        if names:
            priorities.append(" / ".join(names))

    if not priorities:
        return None
    return f"{build_label}\nPrioridad: " + " > ".join(priorities)



# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# NORMALIZACIÓN DE TERMINOLOGÍA DE GUÍAS AL ESPAÑOL
# Se evita traducir párrafos y nombres propios con traductores genéricos.
# Las fuentes en español tienen prioridad; este glosario normaliza respaldos.
# ---------------------------------------------------------------------------
GUIDE_TERM_TRANSLATIONS = {
    "energy recharge": "Recarga de Energía",
    "elemental mastery": "Maestría Elemental",
    "crit rate": "Prob. CRIT",
    "critical rate": "Prob. CRIT",
    "crit dmg": "Daño CRIT",
    "crit damage": "Daño CRIT",
    "attack percent": "ATQ%",
    "atk%": "ATQ%",
    "hp%": "Vida%",
    "def%": "DEF%",
    "physical dmg": "Daño Físico",
    "physical damage": "Daño Físico",
    "pyro dmg": "Daño Pyro",
    "pyro damage": "Daño Pyro",
    "hydro dmg": "Daño Hydro",
    "hydro damage": "Daño Hydro",
    "cryo dmg": "Daño Cryo",
    "cryo damage": "Daño Cryo",
    "electro dmg": "Daño Electro",
    "electro damage": "Daño Electro",
    "dendro dmg": "Daño Dendro",
    "dendro damage": "Daño Dendro",
    "anemo dmg": "Daño Anemo",
    "anemo damage": "Daño Anemo",
    "geo dmg": "Daño Geo",
    "geo damage": "Daño Geo",
    "sands": "Reloj",
    "goblet": "Copa",
    "circlet": "Corona",
    "substats": "Subestadísticas",
    "substat priority": "Prioridad de subestadísticas",
    "main stats": "Estadísticas principales",
    "recommended main stats": "Estadísticas principales recomendadas",
    "normal attack": "Ataque Normal",
    "charged attack": "Ataque Cargado",
    "plunging attack": "Ataque Descendente",
    "elemental skill": "Habilidad Elemental",
    "elemental burst": "Habilidad Definitiva",
    "talent priority": "Prioridad de talentos",
    "main dps": "DPS en campo",
    "sub dps": "DPS secundario",
    "support": "Soporte",
    "healer": "Sanador",
    "shield": "Escudo",
    "off-field": "fuera del campo",
    "on-field": "en campo",
    "freeze": "Congelación",
    "melt": "Derretido",
    "vaporize": "Vaporizado",
    "hyperbloom": "Sobreflorecimiento",
    "burgeon": "Crepitar",
    "aggravate": "Aceleración",
    "spread": "Propagación",
    "best in slot": "mejor opción",
    "free to play": "F2P",
    "team composition": "Composición del equipo",
    "rotation": "Rotación",
    "weapons": "Armas",
    "artifacts": "Artefactos",
    "teams": "Equipos"
}

GUIDE_OFFICIAL_NAME_TRANSLATIONS = {
    "emblem of severed fate": "Emblema del Destino",
    "deepwood memories": "Recuerdos del Bosque",
    "gilded dreams": "Sueños Áureos",
    "golden troupe": "Compañía Dorada",
    "marechaussee hunter": "Cazador Fantasmal",
    "fragment of harmonic whimsy": "Fragmento de la Armonía Fantasiosa",
    "scroll of the hero of cinder city": "Pergamino del Héroe de la Ciudad de las Cenizas",
    "nighttime whispers in the echoing woods": "Murmullo Nocturno en el Bosque de los Ecos",
    "obsidian codex": "Códice de Obsidiana",
    "echoes of an offering": "Eco del Sacrificio",
    "vermillion hereafter": "Reminiscencia de la Purificación",
    "desert pavilion chronicle": "Épica del Pabellón del Desierto",
    "blizzard strayer": "Nómada del Invierno",
    "gladiator's finale": "Final del Gladiador",
    "crimson witch of flames": "Bruja Carmesí en Llamas",
    "heart of depth": "Corazón de las Profundidades",
    "tenacity of the millelith": "Tenacidad de la Geoarmada",
    "noblesse oblige": "Ritual Antiguo de la Nobleza",
    "viridescent venerer": "Sombra Verde Esmeralda",
    "wanderer's troupe": "Orquesta del Errante",
    "staff of homa": "Báculo de Homa",
    "staff of the scarlet sands": "Báculo de las Arenas Escarlatas",
    "primordial jade winged-spear": "Lanza Primordial de Jade",
    "the catch": "La Captura",
    "engulfing lightning": "Luz del Segador",
    "aqua simulacra": "Aqua Simulacra",
    "splendor of tranquil waters": "Esplendor de las Aguas Quietas",
    "crimson moon's semblance": "Semblante de la Luna Carmesí",
    "the black sword": "Espada Negra",
    "freedom-sworn": "Juramento por la Libertad",
    "favonius sword": "Espada de Favonius",
    "favonius lance": "Lanza de Favonius",
    "prototype amber": "Prototipo Ámbar",
    "sacrificial fragments": "Fragmentos de Sacrificio"
}


def localize_guide_text(value: Any) -> str:
    """Normaliza términos del juego; no altera frases ni nombres desconocidos."""
    text = str(value or "")
    for english, spanish in sorted(
        GUIDE_OFFICIAL_NAME_TRANSLATIONS.items(),
        key=lambda item: len(item[0]),
        reverse=True
    ):
        text = re.sub(
            re.escape(english),
            spanish,
            text,
            flags=re.IGNORECASE
        )
    for english, spanish in sorted(
        GUIDE_TERM_TRANSLATIONS.items(),
        key=lambda item: len(item[0]),
        reverse=True
    ):
        pattern = r"(?<![A-Za-z])" + re.escape(english) + r"(?![A-Za-z])"
        text = re.sub(pattern, spanish, text, flags=re.IGNORECASE)
    text = re.sub(r"\bATK\b", "ATQ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bHP\b", "Vida", text, flags=re.IGNORECASE)
    text = re.sub(r"\bER\b", "Recarga", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def localize_guide_fields(data: dict, fields):
    """Aplica glosario solo a campos del juego, nunca a URLs ni IDs."""
    if not isinstance(data, dict):
        return data
    result = dict(data)
    for field in fields:
        value = result.get(field)
        if isinstance(value, str):
            result[field] = localize_guide_text(value)
        elif isinstance(value, list):
            result[field] = [
                localize_guide_text(item) if isinstance(item, str) else item
                for item in value
            ]
    return result


async def fetch_helper_character_guide(
    character: dict | None,
    query: str,
    name: str,
    key: str,
    needs: dict
):
    """Busca build data estructurada cuando faltan recomendaciones locales."""
    tree_data = await get_helper_build_tree()
    if not isinstance(tree_data, dict):
        return None
    paths = tree_data.get("paths")
    if not isinstance(paths, set):
        return None

    candidate_slugs = helper_character_candidates(character, query, name, key)
    element = helper_element_slug(character)
    rarity = None
    if isinstance(character, dict):
        try:
            raw_rarity = int(float(character.get("rarity")))
            rarity = str(raw_rarity) if raw_rarity in (4, 5) else None
        except (TypeError, ValueError):
            rarity = None

    roots = []
    for path in paths:
        if not path.startswith("src/content/") or not path.endswith("/metadata.json"):
            continue
        parts = path.split("/")
        if len(parts) != 6:
            continue
        slug = parts[4]
        if slug not in candidate_slugs:
            continue
        if element and parts[2] != element:
            continue
        if rarity and parts[3] != rarity:
            continue
        roots.append((path, parts[2], parts[3], slug))

    if not roots:
        return None

    def root_score(item):
        _, root_element, root_rarity, root_slug = item
        score = 0
        if root_slug == candidate_slugs[0]:
            score += 10
        if element and root_element == element:
            score += 5
        if rarity and root_rarity == rarity:
            score += 3
        return score

    roots.sort(key=root_score, reverse=True)
    if not element and roots[0][3] == "traveler" and len(roots) > 1:
        return None

    metadata_path, _, _, _ = roots[0]
    root = metadata_path.rsplit("/", 1)[0]
    metadata = await fetch_helper_build_json(metadata_path)
    if not isinstance(metadata, dict):
        metadata = {}

    build_files = {}
    for path in paths:
        if not path.startswith(root + "/"):
            continue
        relative = path[len(root) + 1:].split("/")
        if len(relative) >= 2 and relative[-1].endswith(".json"):
            build_name = relative[0]
            build_files.setdefault(build_name, set()).add(relative[-1])

    build_names = sorted(build_files)
    if not build_names:
        build_names = ["__character__"]
    else:
        build_names = build_names[:3]

    async def load_bundle(build_name):
        notes = await load_helper_character_file(
            root, build_name, "build-notes.json", paths
        )
        version = (
            (notes or {}).get("last_updated")
            or metadata.get("last_updated")
            or ""
        )
        if str(version).strip().upper() == "WIP":
            return None

        requests = {}
        if needs.get("weapons"):
            requests["weapons"] = load_helper_character_file(
                root, build_name, "weapons.json", paths
            )
        if needs.get("artifacts"):
            requests["artifact_sets"] = load_helper_character_file(
                root, build_name, "artifacts-sets.json", paths
            )
            requests["artifact_main"] = load_helper_character_file(
                root, build_name, "artifacts-mainstats.json", paths
            )
            requests["artifact_sub"] = load_helper_character_file(
                root, build_name, "artifacts-substats.json", paths
            )
        if needs.get("talents"):
            requests["talents"] = load_helper_character_file(
                root, build_name, "talents.json", paths
            )

        names = list(requests)
        values = await asyncio.gather(*(requests[item] for item in names))
        bundle = dict(zip(names, values))
        bundle["build"] = build_name
        bundle["version"] = version or metadata.get("last_updated") or "No informada"
        return bundle

    bundles = await asyncio.gather(*(load_bundle(build) for build in build_names))
    bundles = [bundle for bundle in bundles if isinstance(bundle, dict)]
    if not bundles:
        return None

    translation_names = []
    if needs.get("weapons"):
        translation_names.append("weapons")
    if needs.get("artifacts"):
        translation_names.extend(["artifacts", "stats"])
    if needs.get("talents"):
        translation_names.append("abilities")

    translations = await load_helper_build_translations() if translation_names else {}
    result = {
        "artifacts": [],
        "weapons": [],
        "talents": [],
        "source_url": (
            "https://github.com/Genshin-Impact-Helper-Team/genshin-builds/tree/main/"
            + root
        ),
        "version": metadata.get("last_updated") or ""
    }

    for bundle in bundles:
        build_name = bundle.get("build") or "build"
        build_label = (
            "Variante: " + build_name.replace("-", " ").replace("_", " ").title()
            if build_name != "__character__" else "Configuración general"
        )
        if needs.get("weapons"):
            formatted = helper_format_weapons(
                bundle.get("weapons"), translations, build_label
            )
            if formatted:
                result["weapons"].append(formatted)
        if needs.get("artifacts"):
            formatted = helper_format_artifacts(
                bundle.get("artifact_sets"),
                bundle.get("artifact_main"),
                bundle.get("artifact_sub"),
                translations,
                build_label
            )
            if formatted:
                result["artifacts"].append(formatted)
        if needs.get("talents"):
            formatted = helper_format_talents(
                bundle.get("talents"), translations, build_label
            )
            if formatted:
                result["talents"].append(formatted)

        if bundle.get("version") and not result["version"]:
            result["version"] = bundle["version"]

    if not any(result[field] for field in ("artifacts", "weapons", "talents")):
        return None

    for field in ("artifacts", "weapons", "talents"):
        result[field] = "\n\n".join(result[field])
    result["build_count"] = len(bundles)
    return localize_guide_fields(
        result, ("artifacts", "weapons", "talents")
    )


class KQMTeamParser(HTMLParser):
    """Extrae composiciones de equipo de las imágenes accesibles de KQM."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.heading_stack = []
        self.heading_level = None
        self.heading_parts = []
        self.teams = []

    def current_path(self):
        return " ".join(value for _, value in self.heading_stack).casefold()

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_level = int(tag[1])
            self.heading_parts = []
        elif tag == "img" and "team" in self.current_path():
            attributes = dict(attrs)
            self.add_team(attributes.get("alt") or "")

    def handle_data(self, data):
        if self.heading_level is not None:
            self.heading_parts.append(data)
        elif "team" in self.current_path():
            self.add_team(data)

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            if self.heading_level == level:
                title = re.sub(r"\s+", " ", "".join(self.heading_parts)).strip()
                self.heading_stack = [
                    item for item in self.heading_stack if item[0] < level
                ]
                if title:
                    self.heading_stack.append((level, title))
                self.heading_level = None
                self.heading_parts = []

    def add_team(self, value):
        line = re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()
        if not line or len(line) > 150:
            return
        separators = line.count("—") + line.count("–")
        separators += line.count(",") + line.count("/") + line.count("+")
        if separators >= 2 and line not in self.teams:
            self.teams.append(line)


async def fetch_kqm_teams(character: dict | None, query: str, name: str, key: str):
    """Busca equipos en la guía rápida de KQM cuando no hay equipo local."""
    raw_values = [key, name, query]
    if isinstance(character, dict):
        raw_values.extend([character.get("id"), character.get("name")])

    candidates = []
    for value in raw_values:
        slug = guide_slugify(value)
        if slug and slug not in candidates:
            candidates.append(slug)

    # Algunos nombres del juego tienen slugs editoriales diferentes.
    for slug in list(candidates):
        if slug == "shogun-raiden":
            candidates.append("raiden")
        elif slug == "tartaglia":
            candidates.append("childe")
        elif slug == "kaedehara-kazuha":
            candidates.append("kazuha")
        elif slug == "kamisato-ayaka":
            candidates.append("ayaka")
        elif slug == "sangonomiya-kokomi":
            candidates.append("kokomi")

    for slug in candidates[:2]:
        url = f"{KQM_QUICKGUIDE_BASE}{slug}-quickguide/"
        key_cache = f"kqm-teams:{slug}"
        cached = cache.get(key_cache)
        if cached and cached[0] > time.time():
            if cached[1]:
                return cached[1]
            continue

        try:
            response = await external_request(
                "GET",
                url,
                timeout=18,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 Katherine-AnarchyBot/3.0"}
            )
            if response.status_code != 200:
                cache[key_cache] = (time.time() + 3600, None)
                continue

            parser = KQMTeamParser()
            parser.feed(response.text)
            teams = parser.teams[:6]
            if teams:
                result = {"teams": teams, "url": str(response.url)}
                cache[key_cache] = (
                    time.time() + HELPER_BUILDS_CACHE_TTL, result
                )
                return result

            cache[key_cache] = (time.time() + 3600, None)
        except (httpx.HTTPError, ValueError) as exc:
            logging.debug("Guía KQM no disponible para %s: %s", slug, exc)

    return None




# ---------------------------------------------------------------------------
# RESPALDO AMPLIADO DE BUILDS: GENSHIN.GG E ICY VEINS
# ---------------------------------------------------------------------------
GENSHIN_GG_CHARACTER_BASE = "https://genshin.gg/characters/"
ICY_VEINS_GUIDE_BASE = "https://www.icy-veins.com/genshin-impact/"
COMMUNITY_GUIDE_CACHE_TTL = 21600


def community_guide_slug_candidates(character, query: str, name: str, key: str):
    """Genera slugs alternativos para nombres localizados y nombres oficiales."""
    raw_values = [name, query, key]
    if isinstance(character, dict):
        raw_values.extend([
            character.get("id"),
            character.get("name"),
            character.get("fullname")
        ])

    aliases = {
        "raiden": ["raiden-shogun", "shogun-raiden"],
        "raiden-shogun": ["shogun-raiden", "raiden"],
        "shogun-raiden": ["raiden-shogun", "raiden"],
        "kazuha": ["kaedehara-kazuha"],
        "kaedehara-kazuha": ["kazuha"],
        "ayaka": ["kamisato-ayaka"],
        "kamisato-ayaka": ["ayaka"],
        "ayato": ["kamisato-ayato"],
        "kamisato-ayato": ["ayato"],
        "kokomi": ["sangonomiya-kokomi"],
        "sangonomiya-kokomi": ["kokomi"],
        "childe": ["tartaglia"],
        "tartaglia": ["childe"],
        "hutao": ["hu-tao"],
        "hu-tao": ["hutao"],
        "itto": ["arataki-itto"],
        "arataki-itto": ["itto"],
        "wanderer": ["scaramouche"],
        "scaramouche": ["wanderer"],
        "alhacen": ["alhaitham"],
        "alhaitham": ["alhacen"],
        "yunjin": ["yun-jin"],
        "yun-jin": ["yunjin"]
    }

    candidates = []
    for value in raw_values:
        if not value:
            continue
        normalized = unicodedata.normalize("NFKD", str(value))
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
        if slug and slug not in candidates:
            candidates.append(slug)
        for alternative in aliases.get(slug, []):
            if alternative not in candidates:
                candidates.append(alternative)

    return candidates[:6]


class CommunityGuideHTMLParser(HTMLParser):
    """Extrae secciones de builds desde páginas HTML públicas."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current_section = None
        self.heading_tag = None
        self.heading_parts = []
        self.anchor_parts = None
        self.ignored_tag = None
        self.sections = {
            "weapons": [],
            "artifacts": [],
            "stats": [],
            "talents": [],
            "teams": [],
            "rotation": []
        }
        self.headings = {
            "weapons": [],
            "artifacts": [],
            "stats": [],
            "talents": [],
            "teams": [],
            "rotation": []
        }
        self.anchors = {
            "weapons": [],
            "artifacts": [],
            "stats": [],
            "talents": [],
            "teams": [],
            "rotation": []
        }

    @staticmethod
    def classify_heading(value: str):
        title = re.sub(r"\s+", " ", str(value or "")).casefold()
        if "teammate" in title or "team mate" in title:
            return None
        if any(word in title for word in ("rotation", "combo", "rotacion", "rotación")):
            return "rotation"
        if any(word in title for word in ("weapon", "arma recomendada", "armas recomendadas", "mejores armas")):
            return "weapons"
        if any(word in title for word in ("artifact", "artefact", "set de artefactos", "artefactos recomendados", "mejores artefactos")):
            if any(word in title for word in ("stat", "main", "substat", "estadística", "estadistica")):
                return "stats"
            return "artifacts"
        if any(word in title for word in (
            "team", "teambuild", "composition", "compositions",
            "equipo", "equipos", "composición", "composicion"
        )):
            return "teams"
        if any(word in title for word in (
            "talent priority", "talents priority", "prioridad de talentos",
            "prioridad de talento"
        )):
            return "talents"
        if any(word in title for word in (
            "stat priority", "best stats", "stats recomendadas",
            "estadísticas recomendadas", "estadisticas recomendadas",
            "estadísticas principales", "estadisticas principales",
            "substats priorizadas"
        )):
            return "stats"
        return None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.ignored_tag = tag
            return

        if re.fullmatch(r"h[1-6]", tag):
            self.heading_tag = tag
            self.heading_parts = []
            return

        if self.ignored_tag:
            return

        if tag == "a" and self.current_section:
            self.anchor_parts = []

        if tag == "img" and self.current_section:
            attributes = dict(attrs)
            alt = re.sub(r"\s+", " ", str(attributes.get("alt") or "")).strip()
            if alt and alt.casefold() not in ("image", "icon"):
                self.sections[self.current_section].append(alt)
                if self.anchor_parts is not None:
                    self.anchor_parts.append(alt)

    def handle_data(self, data):
        if self.ignored_tag:
            return
        if self.heading_tag:
            self.heading_parts.append(data)
            return

        value = re.sub(r"\s+", " ", str(data or "")).strip()
        if value and self.current_section:
            self.sections[self.current_section].append(value)
            if self.anchor_parts is not None:
                self.anchor_parts.append(value)

    def handle_endtag(self, tag):
        if self.ignored_tag:
            if tag == self.ignored_tag:
                self.ignored_tag = None
            return

        if self.heading_tag == tag:
            title = re.sub(
                r"\s+", " ", "".join(self.heading_parts)
            ).strip()
            section = self.classify_heading(title)
            if section:
                self.current_section = section
            if title and self.current_section:
                self.headings[self.current_section].append(title)
            self.heading_tag = None
            self.heading_parts = []
            return

        if tag == "a" and self.anchor_parts is not None:
            label = re.sub(
                r"\s+", " ", " ".join(self.anchor_parts)
            ).strip()
            if label and self.current_section:
                self.anchors[self.current_section].append(label)
            self.anchor_parts = None


def community_guide_entries(parser: CommunityGuideHTMLParser, section: str,
                            maximum: int = 7):
    """Obtiene nombres cortos sin duplicar alt-text y texto visible."""
    values = (
        parser.headings.get(section, [])
        + parser.sections.get(section, [])
    )
    result = []
    seen = set()
    ignored_exact = {
        "weapons", "best weapons", "artifacts", "best artifacts",
        "artifact sets", "best stats", "stats", "teams", "best teams",
        "talents", "talent priority", "image", "main dps", "sub dps",
        "support", "main stats", "substats"
    }
    ignored_prefixes = (
        "base atk", "atk:", "hp:", "def:", "crit rate:",
        "crit dmg:", "physical dmg:", "2-piece set:", "4-piece set:",
        "2 piece set:", "4 piece set:", "sands:", "goblet:",
        "circlet:", "substats:", "substats priority:"
    )

    for raw in values:
        value = html.unescape(str(raw or ""))
        value = re.sub(r"^\s*image\s*:\s*", "", value, flags=re.I)
        value = re.sub(r"^\s*\d+\s*[.)-]?\s*", "", value)
        value = re.sub(r"\s+", " ", value).strip(" \t\r\n:|•-")
        folded = value.casefold()
        if not value or folded in ignored_exact:
            continue
        if any(folded.startswith(prefix) for prefix in ignored_prefixes):
            continue
        if re.fullmatch(r"[\d\s.,%★☆/+-]+", value):
            continue
        if len(value) > 68:
            continue
        if folded.startswith(("best ", "recommended ", "image ")):
            continue
        if value.count(".") > 1 or value.endswith((".", "!", "?")):
            continue
        normalized = re.sub(r"\s*\([^)]*\)\s*", " ", folded)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
        if len(result) >= maximum:
            break

    return result


def community_guide_stats(parser: CommunityGuideHTMLParser):
    values = parser.sections.get("stats", [])
    if not values:
        return None

    value = re.sub(r"\s+", " ", " ".join(values)).strip()
    value = re.sub(r"(?i)\bSands(?: of Eon)?\s*:", "Reloj:", value)
    value = re.sub(r"(?i)\bCaliz\s*[-:]?", "Copa:", value)
    value = re.sub(r"(?i)\bTiara\s*[-:]?", "Corona:", value)
    value = re.sub(r"(?i)\bStats principales\s*:", "Estadísticas principales:", value)
    value = re.sub(r"(?i)\bSubstats priorizadas\s*:", "Subestadísticas prioritarias:", value)
    value = re.sub(r"(?i)\bProb\. Crit\b", "Prob. CRIT", value)
    value = re.sub(r"(?i)\bDano Crit\b", "Daño CRIT", value)
    value = re.sub(r"(?i)\bGoblet(?: of Eonothem)?\s*:", "Copa:", value)
    value = re.sub(r"(?i)\bCirclet(?: of Logos)?\s*:", "Corona:", value)
    value = re.sub(r"(?i)\bSubstats?(?: Priority)?\s*:", "Substats:", value)
    value = re.sub(r"(?i)\bATK\b", "ATQ", value)
    value = re.sub(r"(?i)\bCryo DMG\b", "Daño Cryo", value)
    value = re.sub(r"(?i)\bHydro DMG\b", "Daño Hydro", value)
    value = re.sub(r"(?i)\bPyro DMG\b", "Daño Pyro", value)
    value = re.sub(r"(?i)\bDendro DMG\b", "Daño Dendro", value)
    value = re.sub(r"(?i)\bElectro DMG\b", "Daño Electro", value)
    value = re.sub(r"(?i)\bAnemo DMG\b", "Daño Anemo", value)
    value = re.sub(r"(?i)\bGeo DMG\b", "Daño Geo", value)
    return game_excerpt(value, 650) if value else None


def community_guide_team_groups(parser: CommunityGuideHTMLParser,
                                target_slugs: set[str]):
    """Agrupa los enlaces consecutivos de personajes en equipos de cuatro."""
    values = []
    ignored = {
        "best teams", "team guide", "teams", "show more", "show less",
        "read more", "team compositions"
    }
    for raw in parser.anchors.get("teams", []):
        value = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not value or value.casefold() in ignored or len(value) > 35:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize(
            "NFKD", value
        ).encode("ascii", "ignore").decode("ascii").casefold()).strip("-")
        if slug:
            values.append((value, slug))

    teams = []
    seen = set()
    for index in range(max(0, len(values) - 3)):
        group = values[index:index + 4]
        if len(group) != 4:
            continue
        if group[0][1] not in target_slugs:
            continue
        names = [item[0] for item in group]
        signature = "|".join(name.casefold() for name in names)
        if signature in seen:
            continue
        seen.add(signature)
        teams.append(" · ".join(names))
        if len(teams) >= 6:
            break
    return teams


def parse_community_guide_page(page_html: str, url: str,
                               target_slugs: set[str] | None = None):
    parser = CommunityGuideHTMLParser()
    try:
        parser.feed(page_html or "")
        parser.close()
    except Exception as exc:
        logging.debug("No se pudo interpretar una guía externa: %s", exc)
        return None

    weapons = community_guide_entries(parser, "weapons", 7)
    artifacts = community_guide_entries(parser, "artifacts", 6)
    talents_raw = " ".join(parser.sections.get("talents", []))
    talents_raw = re.sub(r"\s+", " ", talents_raw).strip()
    talents = game_excerpt(talents_raw, 480) if talents_raw else None
    rotation_raw = " ".join(parser.sections.get("rotation", []))
    rotation_raw = re.sub(r"\s+", " ", rotation_raw).strip()
    rotation = game_excerpt(rotation_raw, 700) if rotation_raw else None

    return {
        "weapons": weapons,
        "artifacts": artifacts,
        "stats": community_guide_stats(parser),
        "talents": talents,
        "teams": community_guide_team_groups(
            parser, target_slugs or set()
        ),
        "rotation": rotation,
        "url": url
    }



def extract_guide_update_label(page_text: str):
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(page_text or "")))
    text = re.sub(r"\s+", " ", text)
    patterns = (
        r"(?i)(?:last\s+updated|updated\s+on|updated|actualizado(?:\s+el)?|actualizada(?:\s+el)?)\s*[:\-]?\s*((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})",
        r"(?i)(?:versión|version)\s*(\d+(?:\.\d+){1,2})",
        r"(?i)(?:updated\s+for|actualizado\s+para)\s+(?:version|versión)\s*(\d+(?:\.\d+){1,2})"
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


async def fetch_community_guide_url(url: str, cache_key: str,
                                    target_slugs: set[str] | None = None):
    cached = cache.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        response = await external_request(
            "GET",
            url,
            timeout=14,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Katherine-AnarchyBot/4.0)",
                "Accept": "text/html,application/xhtml+xml"
            }
        )
        if response.status_code != 200:
            cache[cache_key] = (time.time() + 1800, None)
            return None

        parsed = parse_community_guide_page(
            response.text, str(response.url), target_slugs
        )
        parsed["source_updated"] = extract_guide_update_label(response.text)
        parsed["fetched_at"] = time.time()
        if not parsed or not any(
            parsed.get(field)
            for field in ("weapons", "artifacts", "stats", "talents", "teams", "rotation")
        ):
            cache[cache_key] = (time.time() + 1800, None)
            return None

        cache[cache_key] = (
            time.time() + COMMUNITY_GUIDE_CACHE_TTL, parsed
        )
        return parsed
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logging.debug("Fuente de guía no disponible (%s): %s", url, exc)
        cache[cache_key] = (time.time() + 900, None)
        return None


async def fetch_first_community_guide(base_urls, candidates, cache_prefix,
                                      target_slugs=None):
    for slug in candidates[:4]:
        for suffix in base_urls:
            url = suffix.format(slug=slug)
            result = await fetch_community_guide_url(
                url,
                f"{cache_prefix}:{slug}:{suffix}",
                target_slugs
            )
            if result:
                return result
    return None



# Fuentes adicionales de guías. Se prueban de forma independiente y sin publicar URLs.
EXTRA_CHARACTER_GUIDE_SOURCES = {
    "genshin_guides": [
        "https://genshinguides.com/characters/{slug}/",
        "https://genshinguides.com/character/{slug}/"
    ],
    "genshin_build": [
        "https://genshin-build.com/characters/{slug}/",
        "https://genshin-build.com/character/{slug}/"
    ],
    "genshin_guide": [
        "https://genshinguide.com/characters/{slug}/",
        "https://genshinguide.com/character/{slug}/"
    ],
    "game8": [
        "https://game8.co/games/Genshin-Impact/archives/{slug}"
    ]
}


async def fetch_extra_character_guides(candidates, target_slugs):
    async def fetch_source(source_name, templates):
        return await fetch_first_community_guide(
            templates, candidates, f"extra-guide:{source_name}", target_slugs
        )

    names = list(EXTRA_CHARACTER_GUIDE_SOURCES.items())
    results = await asyncio.gather(
        *(fetch_source(name, urls) for name, urls in names),
        return_exceptions=True
    )
    return {
        name: result
        for (name, _), result in zip(names, results)
        if isinstance(result, dict)
    }


async def fetch_community_character_guide(
    character: dict | None,
    query: str,
    name: str,
    key: str,
    needs: dict
):
    """Completa las recomendaciones locales con varias guías públicas."""
    candidates = community_guide_slug_candidates(
        character, query, name, key
    )
    if not candidates:
        return {}

    target_slugs = set(candidates)
    tasks = []

    sources = {}
    needs_build = any(needs.get(field) for field in (
        "weapons", "artifacts", "stats", "talents"
    ))
    if needs_build:
        builds_data = await fetch_genshin_builds_guide(candidates)
        if builds_data:
            sources["genshin_builds"] = builds_data
        tasks.extend([
            (
                "kqm",
                fetch_first_community_guide(
                    [
                        "https://keqingmains.com/es/q/{slug}/",
                        "https://keqingmains.com/q/{slug}/"
                    ],
                    candidates,
                    "kqm-full-guide",
                    target_slugs
                )
            ),
            (
                "genshin_gg",
                fetch_first_community_guide(
                    [GENSHIN_GG_CHARACTER_BASE + "{slug}/"],
                    candidates,
                    "genshin-gg-guide",
                    target_slugs
                )
            ),
            (
                "icy_veins",
                fetch_first_community_guide(
                    [ICY_VEINS_GUIDE_BASE + "{slug}-guide-best-builds"],
                    candidates,
                    "icy-veins-build",
                    target_slugs
                )
            )
        ])

    extra_sources = await fetch_extra_character_guides(candidates, target_slugs)
    sources.update(extra_sources)

    if needs.get("teams"):
        builds_teams_data = await fetch_genshin_builds_guide(candidates)
        if builds_teams_data and builds_teams_data.get("teams"):
            sources["genshin_builds_teams"] = builds_teams_data
        tasks.append((
            "icy_veins_teams",
            fetch_first_community_guide(
                [ICY_VEINS_GUIDE_BASE + "{slug}-team-guide"],
                candidates,
                "icy-veins-teams",
                target_slugs
            )
        ))

    fetched = await asyncio.gather(
        *(task for _, task in tasks),
        return_exceptions=True
    )
    for (source_name, _), result in zip(tasks, fetched):
        if isinstance(result, dict):
            sources[source_name] = result

    merged = {
        "weapons": None,
        "artifacts": None,
        "stats": None,
        "talents": None,
        "teams": None,
        "rotation": None,
        "source_updated": None,
        "fetched_at": None,
        "sources": [],
        "field_sources": {}
    }
    field_sources = (
        ("weapons", "weapons"),
        ("artifacts", "artifacts"),
        ("stats", "stats"),
        ("talents", "talents"),
        ("rotation", "rotation")
    )

    for field, source_field in field_sources:
        if not needs.get(field):
            continue
        for source_name in (
            "genshin_builds", "genshin_guides", "genshin_build",
            "genshin_guide", "kqm", "genshin_gg", "icy_veins", "game8"
        ):
            data = sources.get(source_name) or {}
            value = data.get(source_field)
            if value:
                if isinstance(value, list):
                    merged[field] = "\n".join(
                        f"{index}. {item}"
                        for index, item in enumerate(value, 1)
                    )
                else:
                    merged[field] = value
                merged["sources"].append(source_name)
                merged["field_sources"][field] = source_name
                merged[field + "_url"] = data.get("url")
                merged["source_updated"] = merged.get("source_updated") or data.get("source_updated")
                merged["fetched_at"] = max(
                    merged.get("fetched_at") or 0, data.get("fetched_at") or 0
                ) or None
                break

    if needs.get("teams"):
        data = next((
            sources.get(source_name) for source_name in (
                "genshin_builds_teams", "genshin_guides", "genshin_build",
                "genshin_guide", "kqm", "icy_veins_teams", "game8"
            ) if (sources.get(source_name) or {}).get("teams")
        ), {})
        if data.get("teams"):
            merged["teams"] = "\n".join(
                f"{index}. {item}"
                for index, item in enumerate(data["teams"], 1)
            )
            merged["teams_url"] = data.get("url")
            team_source = next((
                source_name for source_name in (
                    "genshin_builds_teams", "genshin_guides", "genshin_build",
                    "genshin_guide", "kqm", "icy_veins_teams", "game8"
                ) if (sources.get(source_name) or {}).get("teams")
            ), "unknown")
            merged["sources"].append(team_source)
            merged["field_sources"]["teams"] = team_source

    source_labels = {
        "genshin_builds": "Genshin-Builds (español)",
        "genshin_builds_teams": "Genshin-Builds (español) · equipos",
        "genshin_guides": "Genshin Guides",
        "genshin_build": "Genshin Build",
        "genshin_guide": "Genshin Guide",
        "game8": "Game8",
        "kqm": "KeqingMains",
        "genshin_gg": "Genshin.gg",
        "icy_veins": "Icy Veins",
        "icy_veins_teams": "Icy Veins · equipos",
    "genshin_guides": "Genshin Guides",
    "genshin_build": "Genshin Build",
    "genshin_guide": "Genshin Guide",
    "game8": "Game8"
    }
    merged["source_labels"] = list(dict.fromkeys(
        source_labels[source] for source in merged["sources"]
    ))
    merged["sources"] = list(dict.fromkeys(merged["sources"]))
    return localize_guide_fields(
        merged, ("weapons", "artifacts", "stats", "talents", "teams")
    )



# ---------------------------------------------------------------------------
# GENSHIN-BUILDS.COM · CONTENIDO ESTRUCTURADO EN ESPAÑOL
# ---------------------------------------------------------------------------
GENSHIN_BUILDS_BASE = "https://genshin-builds.com/es"


async def fetch_genshin_builds_guide(slugs):
    """Prueba páginas Markdown y HTML públicas de Genshin-Builds en español."""
    for slug in slugs[:5]:
        for url in (
            f"{GENSHIN_BUILDS_BASE}/character/{slug}.md",
            f"{GENSHIN_BUILDS_BASE}/character/{slug}"
        ):
            cache_key = "genshin-builds-es:" + url
            cached = cache.get(cache_key)
            if cached and cached[0] > time.time():
                if cached[1]:
                    return cached[1]
                continue
            try:
                response = await external_request(
                    "GET", url, timeout=18, follow_redirects=True,
                    headers={
                        "User-Agent": "Mozilla/5.0 Katherine-AnarchyBot/4.0",
                        "Accept": "text/markdown,text/plain,text/html,*/*"
                    }
                )
                if response.status_code != 200 or len(response.text or "") < 100:
                    cache[cache_key] = (time.time() + 1800, None)
                    continue
                result = parse_genshin_builds_content(response.text, str(response.url))
                if result and any(result.get(k) for k in (
                    "weapons", "artifacts", "stats", "talents", "teams"
                )):
                    cache[cache_key] = (time.time() + COMMUNITY_GUIDE_CACHE_TTL, result)
                    return result
                cache[cache_key] = (time.time() + 1800, None)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                logging.debug("Genshin-Builds no disponible (%s): %s", url, exc)
                cache[cache_key] = (time.time() + 900, None)
    return None


def parse_genshin_builds_content(content: str, url: str):
    """Extrae texto por secciones; no mezcla títulos ni párrafos de otras áreas."""
    text = html.unescape(str(content or ""))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip(" -*#\t\r") for line in text.splitlines()]
    lines = [line for line in lines if line]
    sections = {"weapons": [], "artifacts": [], "stats": [], "talents": [], "teams": []}
    current = None
    headings = (
        ("weapons", ("best weapons", "weapons", "armas recomendadas", "mejores armas")),
        ("artifacts", ("best artifacts", "artifact sets", "artefactos recomendados", "mejores artefactos")),
        ("stats", ("main stats", "substats", "recommended stats", "estadísticas principales", "estadisticas principales")),
        ("talents", ("talent priority", "talents priority", "prioridad de talentos")),
        ("teams", ("best teams", "team compositions", "equipos recomendados", "mejores equipos"))
    )
    for line in lines:
        low = line.casefold()
        matched = False
        for field, phrases in headings:
            if any(phrase in low for phrase in phrases):
                current = field
                matched = True
                break
        if matched:
            continue
        if current and len(line) <= 180:
            sections[current].append(line)
        if low in ("character overview", "character information", "overview", "resumen del personaje"):
            current = None

    def compact(field, limit):
        result, seen = [], set()
        ignored = {"weapons", "artifacts", "teams", "main stats", "substats", "best teams"}
        for line in sections[field]:
            clean = re.sub(r"\s+", " ", line).strip()
            if len(clean) < 3 or clean.casefold() in ignored:
                continue
            if re.fullmatch(r"[\d.,%+\-/ ]+", clean):
                continue
            if clean.casefold() in seen:
                continue
            seen.add(clean.casefold())
            result.append(clean)
            if len(result) >= limit:
                break
        return result

    return {
        "weapons": compact("weapons", 7),
        "artifacts": compact("artifacts", 6),
        "stats": " · ".join(compact("stats", 10)) or None,
        "talents": " · ".join(compact("talents", 6)) or None,
        "teams": compact("teams", 6),
        "url": url
    }




# ---------------------------------------------------------------------------
# COBERTURA DE GUÍAS POR PERSONAJE
# ---------------------------------------------------------------------------
GUIDE_COVERAGE_SOURCE_NAMES = {
    "local": "Katherine · recomendaciones revisadas",
    "genshin_db": "Genshin-DB",
    "helper": "Genshin Impact Helper Team",
    "genshin_builds": "Genshin-Builds",
    "kqm": "KeqingMains",
    "genshin_gg": "Genshin.gg",
    "icy_veins": "Icy Veins",
    "icy_veins_teams": "Icy Veins · equipos"
}


async def collect_character_guide_data(
    query: str, progress_callback=None
):
    """Construye una guía multicapa y tolerante a fallos sin bloquear por una fuente."""
    async def bounded_source(label: str, awaitable, timeout: float = 28):
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            metrics["guide_source_failures"] += 1
            logging.warning(
                "[NEXUS] Fuente %s no disponible (%s): %s",
                label, type(exc).__name__, str(exc)[:180]
            )
            return None

    async def progress(message: str):
        if progress_callback:
            try:
                await progress_callback(message)
            except Exception:
                logging.debug("[NEXUS] No se pudo actualizar el progreso.", exc_info=True)

    character, stats = await asyncio.gather(
        bounded_source("genshin-db-ficha", get_character(query), 24),
        bounded_source("genshin-db-estadisticas", character_stats(query), 24)
    )
    name = (
        clean_game_text(character.get("name") or query)
        if isinstance(character, dict) else clean_game_text(query)
    )
    key = normalize_character_guide_key(name)
    artifact = ARTIFACT_GUIDES.get(key)
    weapons = WEAPON_GUIDES.get(key)
    priority = TALENT_PRIORITIES.get(key)
    team = BUILD_TEAMS.get(key)

    await progress(f"FICHA · {name}\nFicha básica consultada. Contrastando builds y equipos en paralelo…")
    needs_helper = {
        "artifacts": not bool(artifact),
        "weapons": not bool(weapons),
        "talents": not bool(priority)
    }
    helper_task = (
        bounded_source(
            "genshin-impact-helper",
            fetch_helper_character_guide(character, query, name, key, needs_helper),
            28
        ) if any(needs_helper.values()) else asyncio.sleep(0, result=None)
    )
    kqm_task = (
        bounded_source("keqingmains", fetch_kqm_teams(character, query, name, key), 28)
        if not team else asyncio.sleep(0, result=None)
    )
    helper_data, kqm_data = await asyncio.gather(helper_task, kqm_task)

    needs_community = {
        "artifacts": not bool(artifact) and not bool(helper_data and helper_data.get("artifacts")),
        "weapons": not bool(weapons) and not bool(helper_data and helper_data.get("weapons")),
        "stats": not bool(artifact) and not bool(helper_data and helper_data.get("artifacts")),
        "talents": not bool(priority) and not bool(helper_data and helper_data.get("talents")),
        "teams": not bool(team) and not bool(kqm_data and kqm_data.get("teams"))
    }
    community_data = None
    if any(needs_community.values()):
        await progress(
            f"RESPALDO · {name}\nLas fuentes principales no cubrieron todos los apartados. Consultando alternativas…"
        )
        community_data = await bounded_source(
            "guias-comunitarias",
            fetch_community_character_guide(character, query, name, key, needs_community),
            32
        )
    await progress(f"ANÁLISIS · {name}\nFuentes contrastadas. Preparando la guía final…")
    return {
        "query": query, "name": name, "key": key,
        "character": character, "stats": stats, "artifact": artifact,
        "weapons": weapons, "priority": priority, "team": team,
        "helper_data": helper_data, "kqm_data": kqm_data,
        "community_data": community_data,
        "rotation": (community_data or {}).get("rotation"),
        "source_updated": (community_data or {}).get("source_updated"),
        "fetched_at": (community_data or {}).get("fetched_at")
    }




# ---------------------------------------------------------------------------
# NOTICIAS OFICIALES: BANNERS Y EVENTOS (FUENTE HOYOVERSE EN ESPAÑOL)
# ---------------------------------------------------------------------------
OFFICIAL_NEWS_INDEX = "https://genshin.hoyoverse.com/es/news"


class OfficialNewsLinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.current = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.ignored += 1
        if tag == "a" and not self.ignored:
            href = dict(attrs).get("href")
            if href and "/news/detail/" in href:
                self.current = {"url": urljoin(OFFICIAL_NEWS_INDEX, href), "parts": []}

    def handle_data(self, data):
        if self.current and not self.ignored:
            value = re.sub(r"\s+", " ", str(data or "")).strip()
            if value:
                self.current["parts"].append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.ignored:
            self.ignored -= 1
        if tag == "a" and self.current:
            title = re.sub(r"\s+", " ", " ".join(self.current["parts"])).strip()
            if title:
                self.current["title"] = title
                self.items.append(self.current)
            self.current = None


async def fetch_official_news(category: str):
    cache_key = f"official-news-es:{category}"
    cached = cache.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        response = await external_request(
            "GET", OFFICIAL_NEWS_INDEX, timeout=18, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 Katherine-AnarchyBot/4.0",
                     "Accept": "text/html,application/xhtml+xml"}
        )
        if response.status_code != 200:
            return []
        parser = OfficialNewsLinkParser()
        parser.feed(response.text)
        parser.close()
        candidates = []
        for item in parser.items:
            title = item["title"]
            folded = title.casefold()
            if category == "banner":
                matched = any(term in folded for term in (
                    "gachapón", "gachapon", "gachapones", "invocación",
                    "invocacion", "event wish", "wish notice"
                ))
            else:
                matched = any(term in folded for term in (
                    "eventos", "evento", "event overview", "calendario"
                ))
            if matched and item["url"] not in {x["url"] for x in candidates}:
                candidates.append(item)

        results = []
        for item in candidates[:8]:
            detail = await external_request(
                "GET", item["url"], timeout=16, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 Katherine-AnarchyBot/4.0",
                         "Accept": "text/html,application/xhtml+xml"}
            )
            if detail.status_code != 200:
                continue
            safe_html = re.sub(
                r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)\s*>",
                " ", detail.text, flags=re.I | re.S
            )
            body = clean_game_text(safe_html)
            body = re.sub(r"\s+", " ", body)
            published = extract_guide_update_label(detail.text)
            results.append({
                "title": item["title"],
                "body": game_excerpt(body, 900),
                "published": published
            })
        cache[cache_key] = (time.time() + 1800, results)
        return results
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logging.warning("Noticias oficiales no disponibles (%s): %s", category, exc)
        return []


def format_official_news(items, heading: str, empty: str):
    if not items:
        return empty
    lines = [heading]
    for item in items[:6]:
        lines.append("")
        lines.append("• " + item["title"])
        if item.get("published"):
            lines.append("  Publicación/actualización: " + item["published"])
        lines.append("  " + item["body"])
    lines.append("")
    lines.append("Información extraída de anuncios oficiales de HoYoverse. Las fechas futuras se muestran como anunciadas, no como eventos activos.")
    return "\n".join(lines)


async def get_character_material_summary(query: str):
    data, stats = await asyncio.gather(get_character(query), character_stats(query))
    if not isinstance(data, dict):
        return None
    costs = aggregate_material_costs(data.get("costs"))
    if not costs:
        return None
    name = clean_game_text(data.get("name") or query)
    lines = [f"MATERIALES · {name.upper()}"]
    for material, amount in sorted(costs.items(), key=lambda item: item[0].casefold()):
        lines.append(f"• {material}: {amount}")
    return "\n".join(lines)


def get_team_suggestions(character_key: str, community_data: dict | None):
    teams = BUILD_TEAMS.get(character_key)
    if teams:
        return teams if isinstance(teams, list) else [str(teams)]
    if community_data and community_data.get("teams"):
        return str(community_data["teams"]).splitlines()
    return []


def build_character_guide_coverage(data: dict):
    """Registra qué apartados existen y quién proporcionó cada uno."""
    character = data.get("character")
    stats = data.get("stats")
    artifact = data.get("artifact")
    weapons = data.get("weapons")
    priority = data.get("priority")
    team = data.get("team")
    helper = data.get("helper_data") or {}
    kqm = data.get("kqm_data") or {}
    community = data.get("community_data") or {}
    field_sources = community.get("field_sources") or {}

    def source_for(field, fallback=None):
        source = field_sources.get(field)
        if source:
            return GUIDE_COVERAGE_SOURCE_NAMES.get(source, source)
        return fallback or "Sin fuente"

    def helper_text(field):
        return str(helper.get(field) or "")

    helper_artifacts = helper_text("artifacts")
    helper_weapons = helper_text("weapons")
    helper_talents = helper_text("talents")

    skills = []
    passives = []
    constellations = []
    costs = None
    if isinstance(character, dict):
        skills = character.get("skillTalents") or character.get("talents") or []
        passives = character.get("passiveTalents") or []
        constellations = (
            character.get("constellations")
            or character.get("Constellations")
            or []
        )
        if isinstance(skills, dict):
            skills = list(skills.values())
        if isinstance(passives, dict):
            passives = list(passives.values())
        if isinstance(constellations, dict):
            constellations = list(constellations.values())
        costs = aggregate_material_costs(character.get("costs"))

    if not isinstance(skills, list):
        skills = []
    if not isinstance(passives, list):
        passives = []
    if not isinstance(constellations, list):
        constellations = []

    base_stats = get_level90(stats)
    if isinstance(base_stats, dict):
        base_stats_ok = any(base_stats.get(k) is not None for k in ("hp", "attack", "defense"))
    else:
        base_stats_ok = False

    helper_has_sets = "sets:" in helper_artifacts.casefold()
    helper_has_main = "stats principales:" in helper_artifacts.casefold()
    helper_has_sub = "prioridad de substats:" in helper_artifacts.casefold()
    community_artifacts = bool(community.get("artifacts"))
    community_stats = bool(community.get("stats"))

    rows = [
        {
            "label": "Ficha del personaje",
            "ok": isinstance(character, dict),
            "source": "Genshin-DB" if isinstance(character, dict) else "Sin fuente"
        },
        {
            "label": "Estadísticas base · nivel 90",
            "ok": base_stats_ok,
            "source": "Genshin-DB" if base_stats_ok else "Sin fuente"
        },
        {
            "label": "Sets de artefactos",
            "ok": bool(
                (artifact and artifact.get("sets"))
                or helper_has_sets
                or community_artifacts
            ),
            "source": (
                "Katherine · guía revisada" if artifact and artifact.get("sets")
                else "Genshin Impact Helper Team" if helper_has_sets
                else source_for("artifacts") if community_artifacts
                else "Sin fuente"
            )
        },
        {
            "label": "Estadísticas principales",
            "ok": bool(
                (artifact and artifact.get("main"))
                or helper_has_main
                or community_stats
            ),
            "source": (
                "Katherine · guía revisada" if artifact and artifact.get("main")
                else "Genshin Impact Helper Team" if helper_has_main
                else source_for("stats") if community_stats
                else "Sin fuente"
            )
        },
        {
            "label": "Subestadísticas",
            "ok": bool(
                (artifact and artifact.get("subs"))
                or helper_has_sub
                or community_stats
            ),
            "source": (
                "Katherine · guía revisada" if artifact and artifact.get("subs")
                else "Genshin Impact Helper Team" if helper_has_sub
                else source_for("stats") if community_stats
                else "Sin fuente"
            )
        },
        {
            "label": "Armas recomendadas",
            "ok": bool(
                (weapons and weapons.get("weapons"))
                or helper_weapons
                or community.get("weapons")
            ),
            "source": (
                "Katherine · guía revisada" if weapons and weapons.get("weapons")
                else "Genshin Impact Helper Team" if helper_weapons
                else source_for("weapons") if community.get("weapons")
                else "Sin fuente"
            )
        },
        {
            "label": "Equipos recomendados",
            "ok": bool(
                team
                or kqm.get("teams")
                or community.get("teams")
            ),
            "source": (
                "Katherine · guía revisada" if team
                else "KeqingMains" if kqm.get("teams")
                else source_for("teams") if community.get("teams")
                else "Sin fuente"
            )
        },
        {
            "label": "Prioridad de talentos",
            "ok": bool(priority or helper_talents or community.get("talents")),
            "source": (
                "Katherine · guía revisada" if priority
                else "Genshin Impact Helper Team" if helper_talents
                else source_for("talents") if community.get("talents")
                else "Sin fuente"
            )
        },
        {
            "label": "Habilidades de combate",
            "ok": bool(skills),
            "source": "Genshin-DB" if skills else "Sin fuente"
        },
        {
            "label": "Habilidades pasivas",
            "ok": bool(passives),
            "source": "Genshin-DB" if passives else "Sin fuente"
        },
        {
            "label": "Detalles de constelaciones",
            "ok": len(constellations) >= 6,
            "source": "Genshin-DB" if len(constellations) >= 6 else "Sin fuente completa"
        },
        {
            "label": "Recomendación de constelaciones",
            "ok": bool(CONSTELLATION_GUIDES.get(data.get("key"))),
            "source": (
                "Katherine · guía revisada"
                if CONSTELLATION_GUIDES.get(data.get("key")) else "Sin fuente"
            )
        },
        {
            "label": "Materiales de ascensión",
            "ok": bool(costs),
            "source": "Genshin-DB" if costs else "Sin fuente"
        }
    ]

    # Calidad real: valida contenido mínimo, no solo que exista un título.
    for row in rows:
        if row["ok"] and row["source"] == "Sin fuente":
            row["ok"] = False
    if community.get("rotation"):
        rows.append({
            "label": "Rotación / combos",
            "ok": True,
            "source": source_for("rotation", "Fuente comunitaria")
        })
    else:
        rows.append({
            "label": "Rotación / combos",
            "ok": False,
            "source": "Sin fuente"
        })

    total = len(rows)
    found = sum(1 for row in rows if row["ok"])
    missing = total - found
    percentage = round((found / total) * 100) if total else 0
    return {
        "character": data.get("name") or data.get("query") or "Personaje",
        "rows": rows,
        "found": found,
        "missing": missing,
        "total": total,
        "percentage": percentage
    }


def format_character_guide_coverage(data: dict):
    """Presenta el diagnóstico con estado y fuente de cada apartado."""
    coverage = build_character_guide_coverage(data)
    if coverage["percentage"] >= 90:
        state = "COBERTURA ALTA"
    elif coverage["percentage"] >= 65:
        state = "COBERTURA PARCIAL"
    else:
        state = "GUÍA INCOMPLETA"

    lines = [
        f"COBERTURA DE GUÍA · {coverage['character'].upper()}",
        f"Resultado: {coverage['found']}/{coverage['total']} apartados · {coverage['percentage']}% · {state}",
        ""
    ]
    for row in coverage["rows"]:
        icon = "🟢" if row["ok"] else "🔴"
        status = "Disponible" if row["ok"] else "Falta"
        lines.append(f"{icon} {row['label']}: {status}")
        lines.append(f"   Fuente: {row['source']}")

    missing_rows = [row["label"] for row in coverage["rows"] if not row["ok"]]
    lines.extend(["", "APARTADOS PENDIENTES"])
    lines.append(", ".join(missing_rows) if missing_rows else "Ninguno.")
    lines.append(
        "La cobertura indica datos encontrados en las fuentes consultadas; "
        "no significa que todas las recomendaciones tengan el mismo nivel de detalle."
    )
    return "\n".join(lines)


def normalize_character_guide_key(name: str) -> str:
    key = re.sub(r"\s+", " ", str(name).casefold().strip())
    return CHARACTER_GUIDE_ALIASES.get(key, key)

AVATAR_MAX_BYTES = 512 * 1024
AVATAR_MIME_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif"
}


def avatar_meta_image(markup: str) -> str | None:
    """Obtiene la imagen principal de un artículo desde Open Graph/Twitter o sus imágenes."""
    tags = re.findall(r"<meta\b[^>]*>", markup or "", flags=re.I)
    for tag in tags:
        name = re.search(
            r"""(?:property|name)\s*=\s*["'](?:og:image|og:image:url|twitter:image)["']""",
            tag, flags=re.I
        )
        content = re.search(r"""content\s*=\s*["']([^"']+)["']""", tag, flags=re.I)
        if name and content:
            return html.unescape(content.group(1)).strip()

    image_tags = re.findall(r"<img\b[^>]*>", markup or "", flags=re.I)
    for tag in image_tags:
        label = " ".join(re.findall(
            r"""(?:alt|title)\s*=\s*["']([^"']*)["']""",
            tag, flags=re.I
        )).casefold()
        src = re.search(
            r"""(?:src|data-src)\s*=\s*["']([^"']+)["']""",
            tag, flags=re.I
        )
        if src and ("캐서린" in label or "katheryne" in label):
            return html.unescape(src.group(1)).strip()

    return None


async def resolve_avatar_image(source_url: str) -> tuple[bytes, str]:
    """Acepta imagen directa o artículo Namu y devuelve bytes validados + MIME."""
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or host in {"localhost", "127.0.0.1", "::1"}
        or host.endswith((".local", ".internal"))
    ):
        raise ValueError("Usa una URL HTTPS pública de una imagen o artículo.")

    candidates = [source_url]
    if host in {"en.namu.wiki", "namu.wiki"}:
        candidates.append(
            "https://www.namu.moe" + parsed.path
            + (f"?{parsed.query}" if parsed.query else "")
        )

    last_problem = "No pude obtener una imagen de esa dirección."
    for candidate in dict.fromkeys(candidates):
        response = await external_request(
            "GET",
            candidate,
            follow_redirects=True,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Katherine-AnarchyBot/4.0)",
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,text/html"
            }
        )
        if response.status_code != 200:
            last_problem = f"La página de origen respondió HTTP {response.status_code}."
            continue

        mime = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        image_url = None
        if mime in AVATAR_MIME_TYPES:
            image_bytes = response.content
        elif mime in {"text/html", "application/xhtml+xml"} or not mime:
            image_url = avatar_meta_image(response.text)
            if not image_url:
                last_problem = "La página no expone una imagen principal reconocible."
                continue
            image_url = urljoin(str(response.url), image_url)
            image_response = await external_request(
                "GET",
                image_url,
                follow_redirects=True,
                timeout=20,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Katherine-AnarchyBot/4.0)",
                    "Accept": "image/png,image/jpeg,image/webp,image/gif"
                }
            )
            if image_response.status_code != 200:
                last_problem = f"La imagen de la página respondió HTTP {image_response.status_code}."
                continue
            mime = str(image_response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            image_bytes = image_response.content
        else:
            last_problem = "El enlace no devuelve una imagen compatible."
            continue

        if mime not in AVATAR_MIME_TYPES:
            last_problem = "La imagen debe ser PNG, JPG, WebP o GIF."
            continue
        if not image_bytes:
            last_problem = "El archivo de imagen está vacío."
            continue
        if len(image_bytes) > AVATAR_MAX_BYTES:
            last_problem = "La imagen supera el límite de 512 KB que acepta Anarchy Apps."
            continue

        return image_bytes, mime

    raise ValueError(last_problem)




def record_chat_activity(thread_id: str, uid: str, nickname: str):
    local_now = datetime.now(ZoneInfo("America/Santiago"))
    day_key = local_now.strftime("%Y-%m-%d")
    hour = int(local_now.hour)
    with db_session() as db:
        db.execute(
            """INSERT INTO chat_activity_daily(thread_id,uid,nickname,day_key,message_count)
               VALUES(?,?,?,?,1)
               ON CONFLICT(thread_id,uid,day_key) DO UPDATE SET
               nickname=excluded.nickname,
               message_count=chat_activity_daily.message_count+1""",
            (str(thread_id), str(uid), str(nickname)[:80], day_key)
        )
        db.execute(
            """INSERT INTO chat_activity_hourly(thread_id,day_key,hour_of_day,message_count)
               VALUES(?,?,?,1)
               ON CONFLICT(thread_id,day_key,hour_of_day) DO UPDATE SET
               message_count=chat_activity_hourly.message_count+1""",
            (str(thread_id), day_key, hour)
        )


def build_activity_report(thread_id: str, period: str = "semana") -> str:
    local_now = datetime.now(ZoneInfo("America/Santiago"))
    today = local_now.date()
    start_date = today if period == "hoy" else today - timedelta(days=6)
    start_key = start_date.strftime("%Y-%m-%d")
    with db_session() as db:
        totals = db.execute(
            """SELECT COALESCE(SUM(message_count),0) AS total,
                      COUNT(DISTINCT uid) AS users
               FROM chat_activity_daily WHERE thread_id=? AND day_key>=?""",
            (str(thread_id), start_key)
        ).fetchone()
        top = db.execute(
            """SELECT uid, MAX(nickname) AS nickname, SUM(message_count) AS total
               FROM chat_activity_daily
               WHERE thread_id=? AND day_key>=?
               GROUP BY uid ORDER BY total DESC LIMIT 5""",
            (str(thread_id), start_key)
        ).fetchall()
        peak = db.execute(
            """SELECT hour_of_day, SUM(message_count) AS total
               FROM chat_activity_hourly
               WHERE thread_id=? AND day_key>=?
               GROUP BY hour_of_day ORDER BY total DESC LIMIT 1""",
            (str(thread_id), start_key)
        ).fetchone()
        days = db.execute(
            """SELECT day_key, SUM(message_count) AS total
               FROM chat_activity_daily
               WHERE thread_id=? AND day_key>=?
               GROUP BY day_key ORDER BY day_key DESC LIMIT 7""",
            (str(thread_id), start_key)
        ).fetchall()

    period_label = "hoy" if period == "hoy" else "últimos 7 días"
    lines = [
        f"ACTIVIDAD DEL CHAT · {period_label.upper()}",
        f"Mensajes registrados: {int(totals['total'] or 0)}",
        f"Usuarios activos: {int(totals['users'] or 0)}"
    ]
    if peak and int(peak["total"] or 0) > 0:
        lines.append(
            f"Hora de mayor actividad: {int(peak['hour_of_day']):02d}:00 "
            f"({int(peak['total'])} mensajes)"
        )
    if top:
        lines.append("\nUSUARIOS MÁS ACTIVOS")
        lines.extend(
            f"{index}. {row['nickname']} — {int(row['total'])} mensajes"
            for index, row in enumerate(top, 1)
        )
    else:
        lines.append("\nAún no hay actividad registrada desde esta actualización.")
    if days:
        lines.append("\nACTIVIDAD POR DÍA")
        lines.extend(
            f"{row['day_key']} — {int(row['total'])} mensajes"
            for row in days
        )
    return "\n".join(lines)


def log_admin_action(
    thread_id: str, actor_uid: str, actor_name: str,
    action: str, details: str = ""
):
    with db_session() as db:
        db.execute(
            """INSERT INTO admin_audit_log
               (thread_id,actor_uid,actor_name,action,details,created_at)
               VALUES(?,?,?,?,?,?)""",
            (
                str(thread_id), str(actor_uid), str(actor_name)[:80],
                str(action)[:50], str(details or "")[:240], time.time()
            )
        )


def get_admin_audit(thread_id: str, limit: int = 10):
    with db_session() as db:
        rows = db.execute(
            """SELECT actor_name,action,details,created_at
               FROM admin_audit_log WHERE thread_id=?
               ORDER BY id DESC LIMIT ?""",
            (str(thread_id), max(1, min(20, int(limit))))
        ).fetchall()
        return [dict(row) for row in rows]


def get_recent_chat_warnings(thread_id: str, limit: int = 10):
    with db_session() as db:
        rows = db.execute(
            """SELECT id,target_name,moderator_name,reason,automatic,active,created_at
               FROM moderation_warnings WHERE thread_id=?
               ORDER BY id DESC LIMIT ?""",
            (str(thread_id), max(1, min(20, int(limit))))
        ).fetchall()
        return [dict(row) for row in rows]


def add_auto_reply_rule(thread_id: str, trigger_phrase: str, response_text: str, created_by: str) -> int:
    phrase = re.sub(r"\s+", " ", str(trigger_phrase or "")).strip().casefold()
    response = str(response_text or "").strip()
    if not phrase or len(phrase) > 50 or not response or len(response) > 450:
        raise ValueError("La palabra o frase debe tener hasta 50 caracteres y la respuesta hasta 450.")
    with db_session() as db:
        db.execute(
            """INSERT INTO auto_reply_rules
               (thread_id,trigger_phrase,response_text,enabled,created_by,created_at)
               VALUES(?,?,?,1,?,?)
               ON CONFLICT(thread_id,trigger_phrase) DO UPDATE SET
               response_text=excluded.response_text,enabled=1,
               created_by=excluded.created_by,created_at=excluded.created_at""",
            (str(thread_id), phrase, response, str(created_by), time.time())
        )
        row = db.execute(
            "SELECT id FROM auto_reply_rules WHERE thread_id=? AND trigger_phrase=?",
            (str(thread_id), phrase)
        ).fetchone()
        return int(row["id"])


def remove_auto_reply_rule(thread_id: str, rule_id: int) -> bool:
    with db_session() as db:
        cursor = db.execute(
            "DELETE FROM auto_reply_rules WHERE thread_id=? AND id=?",
            (str(thread_id), int(rule_id))
        )
        return cursor.rowcount > 0


def list_auto_reply_rules(thread_id: str):
    with db_session() as db:
        rows = db.execute(
            """SELECT id,trigger_phrase,response_text,enabled
               FROM auto_reply_rules WHERE thread_id=? ORDER BY id DESC LIMIT 30""",
            (str(thread_id),)
        ).fetchall()
        return [dict(row) for row in rows]


async def maybe_send_auto_reply(thread_id: str, uid: str, nickname: str, content: str):
    if get_chat_setting(thread_id, "auto_replies_enabled", "0") != "1":
        return
    now = time.time()
    if AUTO_REPLY_CHAT_COOLDOWN.get(str(thread_id), 0) > now:
        return
    if AUTO_REPLY_USER_COOLDOWN.get((str(thread_id), str(uid)), 0) > now:
        return
    normalized = re.sub(r"\s+", " ", str(content or "")).strip().casefold()
    if not normalized or len(normalized) > 800:
        return
    rules = list_auto_reply_rules(thread_id)
    for rule in sorted(rules, key=lambda item: len(item["trigger_phrase"]), reverse=True):
        phrase = str(rule["trigger_phrase"] or "").strip().casefold()
        if not phrase or not rule["enabled"]:
            continue
        pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
        if re.search(pattern, normalized, flags=re.UNICODE):
            response = str(rule["response_text"]).replace("{usuario}", str(nickname)[:80])
            AUTO_REPLY_CHAT_COOLDOWN[str(thread_id)] = now + 4
            AUTO_REPLY_USER_COOLDOWN[(str(thread_id), str(uid))] = now + 30
            metrics["auto_replies_sent"] += 1
            await send_message(thread_id, response, "developer")
            return True
    return False


def format_poll_results(poll: dict) -> str:
    options = poll.get("options") or []
    votes = poll.get("votes") or {}
    total = len(votes)
    lines = [
        f"RESULTADOS DE ENCUESTA · {poll.get('code', '------')}",
        str(poll.get("question") or "Encuesta"),
        f"Votos registrados: {total}"
    ]
    for index, option in enumerate(options):
        count = sum(1 for value in votes.values() if value == index)
        percent = (count / total * 100) if total else 0
        lines.append(f"{option}: {count} ({percent:.1f}%)")
    if poll.get("anonymous"):
        lines.append("Votación anónima: no se muestran identidades.")
    return "\n".join(lines)




LANGUAGE_ALIASES = {
    "es": "es", "español": "es", "spanish": "es",
    "en": "en", "ingles": "en", "inglés": "en", "english": "en",
    "pt": "pt", "portugues": "pt", "portugués": "pt", "portuguese": "pt",
    "fr": "fr", "frances": "fr", "francés": "fr", "french": "fr",
    "de": "de", "aleman": "de", "alemán": "de", "german": "de", "deutsch": "de",
    "it": "it", "italiano": "it", "italian": "it",
    "ja": "ja", "japones": "ja", "japonés": "ja", "japanese": "ja",
    "ko": "ko", "coreano": "ko", "korean": "ko",
    "zh": "zh", "chino": "zh", "chinese": "zh",
    "ru": "ru", "ruso": "ru", "russian": "ru",
    "ar": "ar", "arabe": "ar", "árabe": "ar", "arabic": "ar"
}
LANGUAGE_NAMES = {
    "es": "español", "en": "inglés", "pt": "portugués", "fr": "francés",
    "de": "alemán", "it": "italiano", "ja": "japonés", "ko": "coreano",
    "zh": "chino", "ru": "ruso", "ar": "árabe"
}


def normalize_language_code(value: str) -> str | None:
    key = unicodedata.normalize("NFC", str(value or "")).strip().casefold()
    return LANGUAGE_ALIASES.get(key)


def detect_language_code(text: str) -> str:
    value = str(text or "")
    if re.search(r"[\u3040-\u30ff]", value):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", value):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", value):
        return "zh"
    if re.search(r"[\u0600-\u06ff]", value):
        return "ar"
    if re.search(r"[\u0400-\u04ff]", value):
        return "ru"
    words = re.findall(r"[a-záéíóúàèìòùâêîôûãõçäöüßñ]+", value.casefold())
    dictionaries = {
        "es": {"hola", "gracias", "como", "cómo", "que", "qué", "por", "para", "buenos", "buenas", "estoy", "tengo", "una", "los", "las", "del", "con", "pero"},
        "en": {"hello", "thanks", "thank", "you", "the", "and", "with", "what", "how", "are", "is", "this", "that", "please", "good", "morning"},
        "pt": {"olá", "obrigado", "obrigada", "você", "voce", "que", "uma", "não", "nao", "com", "para", "bom", "boa", "estou"},
        "fr": {"bonjour", "merci", "avec", "pour", "vous", "nous", "est", "une", "des", "les", "comment", "salut"},
        "de": {"hallo", "danke", "und", "der", "die", "das", "ist", "nicht", "mit", "ich", "guten", "morgen"},
        "it": {"ciao", "grazie", "buongiorno", "come", "per", "con", "una", "che", "sono", "non", "del"}
    }
    scores = {
        language: sum(1 for word in words if word in vocab)
        for language, vocab in dictionaries.items()
    }
    best = max(scores, key=scores.get)
    if scores[best] > 0:
        return best
    if any(char in value for char in "¿¡ñáéíóú"):
        return "es"
    return "en"


async def translate_text(text: str, target_language: str) -> str:
    content = str(text or "").strip()
    target = normalize_language_code(target_language)
    if not target:
        raise ValueError("Idioma no reconocido. Usa español, inglés, portugués, francés, alemán, italiano, japonés, coreano, chino, ruso o árabe.")
    if not content:
        raise ValueError("Escribe el texto que quieres traducir.")
    if len(content.encode("utf-8")) > 500:
        raise ValueError("MyMemory permite hasta 500 bytes por fragmento. Divide el texto y vuelve a intentarlo.")
    source = detect_language_code(content)
    if source == target:
        return f"El texto parece estar ya en {LANGUAGE_NAMES[target]}."
    day_key = datetime.now(ZoneInfo("America/Santiago")).strftime("%Y-%m-%d")
    chars = len(content)
    with db_session() as db:
        row = db.execute(
            "SELECT characters_used FROM translation_daily_usage WHERE day_key=?",
            (day_key,)
        ).fetchone()
        used = int(row["characters_used"]) if row else 0
        if used + chars > 4500:
            raise RuntimeError("Se alcanzó el límite diario preventivo de traducción gratuita de Katherine. Inténtalo mañana.")
        db.execute(
            """INSERT INTO translation_daily_usage(day_key,characters_used)
               VALUES(?,?) ON CONFLICT(day_key) DO UPDATE SET
               characters_used=translation_daily_usage.characters_used+excluded.characters_used""",
            (day_key, chars)
        )
    response = await external_request(
        "GET",
        "https://api.mymemory.translated.net/get",
        params={"q": content, "langpair": f"{source}|{target}", "mt": "1"},
        timeout=18
    )
    if not response.is_success:
        raise RuntimeError(f"El servicio de traducción respondió HTTP {response.status_code}.")
    payload = response.json()
    translated = (payload.get("responseData") or {}).get("translatedText")
    if not translated:
        detail = clean_text(payload.get("responseDetails") or "No se recibió una traducción.")
        raise RuntimeError(detail[:220])
    translated = html.unescape(str(translated))
    return (
        f"TRADUCCIÓN · {LANGUAGE_NAMES.get(source, source)} → {LANGUAGE_NAMES[target]}\n"
        f"{translated}\n\n"
        "El idioma de origen se estimó automáticamente; revisa el resultado si el texto es importante."
    )


async def search_multimedia(kind: str, query: str, limit: int = 5) -> str:
    search_text = re.sub(r"\s+", " ", str(query or "")).strip()
    if not search_text or len(search_text) > 120:
        raise ValueError("La búsqueda debe tener entre 1 y 120 caracteres.")
    kind = str(kind or "").casefold()
    if kind == "imagen":
        response = await external_request(
            "GET",
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": search_text,
                "gsrnamespace": 6,
                "gsrlimit": max(1, min(5, int(limit))),
                "prop": "imageinfo",
                "iiprop": "url|extmetadata",
                "iiextmetadatafilter": "LicenseShortName|Artist",
                "format": "json",
                "formatversion": 2
            },
            timeout=18,
            headers={"User-Agent": "Katherine-AnarchyBot/3.1 (Wikimedia Commons search)"}
        )
        if not response.is_success:
            raise RuntimeError(f"Wikimedia Commons respondió HTTP {response.status_code}.")
        data = response.json()
        pages = ((data.get("query") or {}).get("pages") or [])
        if not pages:
            return f"No encontré imágenes para «{search_text}» en Wikimedia Commons."
        lines = [f"IMÁGENES · WIKIMEDIA COMMONS · {search_text}"]
        for index, page in enumerate(pages[:limit], 1):
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("descriptionurl") or info.get("url") or ""
            metadata = info.get("extmetadata") or {}
            license_data = metadata.get("LicenseShortName") or {}
            artist_data = metadata.get("Artist") or {}
            license_name = re.sub(r"<[^>]+>", "", str(license_data.get("value") or "Licencia no indicada"))
            artist = re.sub(r"<[^>]+>", "", str(artist_data.get("value") or ""))
            title = str(page.get("title") or "Archivo")
            line = f"{index}. {title}\n{url}"
            if license_name:
                line += f"\nLicencia: {html.unescape(license_name)[:90]}"
            if artist:
                line += f" · Autor: {html.unescape(artist)[:90]}"
            lines.append(line)
        lines.append("Comprueba la licencia y las condiciones de atribución en cada ficha antes de reutilizar una imagen.")
        return "\n\n".join(lines)

    archive_type = {"video": "movies", "audio": "audio"}.get(kind)
    if not archive_type:
        raise ValueError("Tipos disponibles: imagen, video o audio.")
    terms = re.findall(r"[\wÀ-ÿ]+", search_text, flags=re.UNICODE)[:6]
    safe_terms = [term for term in terms if term]
    if not safe_terms:
        raise ValueError("No encontré términos válidos para esa búsqueda.")
    archive_query = f"mediatype:{archive_type} AND " + " AND ".join(safe_terms)
    response = await external_request(
        "GET",
        "https://archive.org/advancedsearch.php",
        params={
            "q": archive_query,
            "fl[]": ["identifier", "title", "creator", "year"],
            "rows": max(1, min(5, int(limit))),
            "page": 1,
            "output": "json"
        },
        timeout=18
    )
    if not response.is_success:
        raise RuntimeError(f"Internet Archive respondió HTTP {response.status_code}.")
    data = response.json()
    docs = ((data.get("response") or {}).get("docs") or [])
    if not docs:
        return f"No encontré resultados de {kind} para «{search_text}» en Internet Archive."
    lines = [f"{kind.upper()} · INTERNET ARCHIVE · {search_text}"]
    for index, item in enumerate(docs[:limit], 1):
        identifier = str(item.get("identifier") or "").strip()
        if not identifier:
            continue
        title = clean_text(item.get("title") or identifier)[:150]
        creator = clean_text(item.get("creator") or "")[:80]
        year = clean_text(item.get("year") or "")[:12]
        details = " · ".join(value for value in (creator, year) if value)
        lines.append(
            f"{index}. {title}" + (f"\n{details}" if details else "")
            + f"\nhttps://archive.org/details/{quote(identifier)}"
        )
    if kind == "audio":
        lines.append("La búsqueda muestra páginas de origen; revisa derechos y licencia. Para reproducir audio con licencia compatible usa !dj buscar.")
    return "\n\n".join(lines)


async def send_admin_panel(
    thread_id: str, source: str, raw_uid: str,
    category: str | None = None, reply_id: str | None = None
):
    if not is_admin_uid(raw_uid):
        await send_message(thread_id, "No tienes permisos para abrir el panel de administración.", source, reply_id)
        return
    if category is None:
        buttons = register_buttons(
            source, thread_id, "admin_panel",
            [
                {"label": "Resumen", "value": "summary"},
                {"label": "Actividad", "value": "activity"},
                {"label": "Antispam", "value": "antispam"},
                {"label": "Moderación", "value": "warnings"},
                {"label": "Auditoría", "value": "audit"}
            ],
            ttl=1200
        )
        await send_message(
            thread_id,
            "KATHERINE · PANEL DE ADMINISTRACIÓN\nSelecciona el módulo que quieres consultar:",
            source, reply_id, buttons
        )
        return

    if category == "summary":
        with db_session() as db:
            users = db.execute(
                "SELECT COUNT(*) AS total FROM users WHERE uid LIKE ?",
                (f"{source}:{thread_id}:%",)
            ).fetchone()["total"]
            rules = db.execute(
                "SELECT COUNT(*) AS total FROM auto_reply_rules WHERE thread_id=?",
                (str(thread_id),)
            ).fetchone()["total"]
            active_warnings = db.execute(
                "SELECT COUNT(*) AS total FROM moderation_warnings WHERE thread_id=? AND active=1",
                (str(thread_id),)
            ).fetchone()["total"]
            daily = db.execute(
                "SELECT COALESCE(SUM(message_count),0) AS total FROM chat_activity_daily WHERE thread_id=? AND day_key=?",
                (str(thread_id), datetime.now(ZoneInfo("America/Santiago")).strftime("%Y-%m-%d"))
            ).fetchone()["total"]
        antispam = anti_spam_configuration(thread_id)
        answer = (
            "RESUMEN DE ADMINISTRACIÓN\n"
            f"Perfiles registrados: {int(users)}\n"
            f"Mensajes de hoy: {int(daily)}\n"
            f"Advertencias activas: {int(active_warnings)}\n"
            f"Reglas de respuesta: {int(rules)}\n"
            f"Antispam: {'activado' if antispam['enabled'] else 'desactivado'}\n"
            f"Respuestas automáticas: {'activadas' if get_chat_setting(thread_id, 'auto_replies_enabled', '0') == '1' else 'desactivadas'}"
        )
        await send_message(thread_id, answer, source, reply_id)
        return

    if category == "activity":
        await send_message(thread_id, build_activity_report(thread_id, "semana"), source, reply_id)
        return

    if category == "antispam":
        current = anti_spam_configuration(thread_id)
        enabled = get_chat_setting(thread_id, "antispam_enabled", "1") == "1"
        buttons = register_buttons(
            source, thread_id, "admin_antispam",
            [{"label": "Activar", "value": "on"}, {"label": "Desactivar", "value": "off"}],
            ttl=900
        )
        await send_message(
            thread_id,
            "CONFIGURACIÓN ANTISPAM\n"
            f"Estado: {'activado' if enabled else 'desactivado'}\n"
            f"Límite: {current['max_messages']} mensajes / {current['window_seconds']} s\n"
            f"Repeticiones: {current['repeat_limit']}",
            source, reply_id, buttons
        )
        return

    if category == "warnings":
        warnings = get_recent_chat_warnings(thread_id, 10)
        lines = ["MODERACIÓN · ÚLTIMAS ADVERTENCIAS"]
        if not warnings:
            lines.append("No hay advertencias registradas.")
        for item in warnings:
            status = "activa" if item["active"] else "retirada"
            automatic = " · automática" if item["automatic"] else ""
            lines.append(
                f"#{item['id']} · {item['target_name']} · {status}{automatic}\n"
                f"{item['reason']} · por {item['moderator_name']}"
            )
        await send_message(thread_id, "\n".join(lines), source, reply_id)
        return

    if category == "audit":
        entries = get_admin_audit(thread_id, 12)
        lines = ["AUDITORÍA · CAMBIOS DE ADMINISTRACIÓN"]
        if not entries:
            lines.append("Todavía no hay cambios registrados.")
        for item in entries:
            stamp = datetime.fromtimestamp(item["created_at"], ZoneInfo("America/Santiago")).strftime("%d/%m %H:%M")
            lines.append(
                f"{stamp} · {item['actor_name']} · {item['action']}"
                + (f"\n{item['details']}" if item["details"] else "")
            )
        await send_message(thread_id, "\n".join(lines), source, reply_id)


async def handle_command(event: dict, source: str):
    global LAST_EVENT_AT, LAST_COMMAND_AT, LAST_COMMAND_NAME, LAST_COMMAND_USER
    LAST_EVENT_AT = time.time()
    msg = event.get("message") or {}
    author = msg.get("author") or {}

    thread = str(event.get("threadId") or "")
    content = str(msg.get("content") or "").strip()

    if not thread or not content:
        return

    metrics["text_messages_received"] += 1
    admin_uid = str(author.get("uid") or "").strip()
    raw_uid = admin_uid or str(author.get("nickname") or "unknown")
    scope_key = f"{source}:{thread}"
    uid = scoped_uid(scope_key, raw_uid)
    game_key = uid
    nickname = author.get("nickname") or "Viajero"
    message_id = msg.get("messageId")

    record_user(uid, nickname, count_message=True)
    record_chat_activity(thread, uid, nickname)

    used_prefix = next(
        (candidate for candidate in COMMAND_PREFIXES if content.startswith(candidate)),
        None,
    )
    if not used_prefix:
        await maybe_send_auto_reply(thread, raw_uid, nickname, content)
        return

    parsed_command = parse_command_invocation(content, used_prefix)
    if not parsed_command:
        return

    metrics["commands_received"] += 1
    command, args = parsed_command

    query = " ".join(args).strip()
    reply = message_id
    LAST_COMMAND_AT = time.time()
    LAST_COMMAND_NAME = command
    LAST_COMMAND_USER = nickname

    if command in ADMIN_ONLY_COMMANDS and not is_admin_uid(admin_uid):
        await send_message(
            thread,
            "Acceso restringido. Este comando requiere un UID incluido en ADMIN_UIDS. "
            "Usa !miuid para consultar tu UID y solicita a un administrador que lo configure en Render.",
            source,
            reply
        )
        return

    logging.info(
        "[COMANDO] %s | %s | %s",
        command,
        nickname,
        thread
    )

    # Identificador de la cuenta que envió el comando. Solo se devuelve al
    # propio usuario para que pueda configurarse como administrador en Render.
    if command in ("!miuid", "!mi-id", "!mi_uid"):
        if not admin_uid:
            await send_message(
                thread,
                "Anarchy Apps no incluyó un UID en este mensaje. No puedo mostrar un identificador verificable.",
                source,
                reply
            )
        else:
            await send_message(
                thread,
                f"Tu UID de Anarchy Apps es: {admin_uid}\\n"
                "Copia este identificador y configúralo en Render en ADMIN_UIDS.",
                source,
                reply
            )
        return



    if command == "!asistente":
        request_text = query.strip()
        if request_text and await maybe_send_auto_reply(thread, raw_uid, nickname, request_text):
            return
        session_key = f"{source}:{thread}:{raw_uid}"
        ai_result = await AI_CORE.assist(request_text, session_key, external_request)
        fallback_command = str(event.get("_nexus_fallback_command") or "").strip()
        fallback_error = str(ai_result.get("answer") or "").startswith((
            "No pude conectar con el servicio de IA.",
            "No pude completar la consulta de IA.",
        ))
        if fallback_command and not fallback_command.startswith("asistente ") and fallback_error:
            routed_event = dict(event)
            routed_message = dict(msg)
            routed_message["content"] = f"{PREFIX}{fallback_command}"
            routed_event["message"] = routed_message
            logging.warning("[NEXUS] IA no disponible; se usa la ruta determinista.")
            await handle_command(routed_event, source)
            return
        if ai_result.get("kind") == "command":
            invocation = validate_ai_tool_call(ai_result.get("command", ""), ai_result.get("args", ""))
            if invocation:
                AI_CORE.remember_tool_action(session_key, invocation)
                routed_event = dict(event)
                routed_message = dict(msg)
                routed_message["content"] = f"{PREFIX}{invocation}"
                routed_event["message"] = routed_message
                logging.info("[AI] Herramienta permitida: %s", invocation.split()[0])
                await handle_command(routed_event, source)
                return
        await send_message(thread, ai_result.get("answer") or "No pude completar tu solicitud.", source, reply)
        return

    if command == "!ia":
        sub = args[0].casefold() if args else "ayuda"
        rest = " ".join(args[1:]).strip()
        session_key = f"{source}:{thread}:{raw_uid}"
        if sub in ("ayuda", "help"):
            await send_message(thread,
                "ASTRA INTELLIGENCE\n!ia estado / probar / olvidar / preguntar <consulta> / genshin <consulta>\n"
                "!ia musica <descripción> / traducir idioma | texto\n"
                "!ia guardian revisar <texto> (administradores)\n"
                "!ia analista activar|resumen|limpiar|desactivar (administradores)\n"
                "!ia diagnostico (UID de administrador).\n"
                "Los subcomandos administrativos requieren un UID configurado en ADMIN_UIDS.", source, reply)
            return
        if sub in ("estado", "status"):
            await send_message(thread, AI_CORE.status(thread), source, reply)
            return
        if sub in ("probar", "test", "ping"):
            if not is_admin_uid(admin_uid):
                await send_message(thread, "Solo un administrador puede probar la conexión de IA.", source, reply)
                return
            try:
                await AI_CORE.test_connection(external_request, rate_key=session_key)
                answer = f"Conexión con OpenAI verificada. Modelo: {AI_CORE.model}."
            except Exception as exc:
                logging.warning("[AI] Prueba de conexión fallida: %s", type(exc).__name__)
                detail = str(exc)
                if "HTTP 401" in detail or "HTTP 403" in detail:
                    answer = "OpenAI rechazó la credencial (HTTP 401/403). Revisa la API Key y los permisos del proyecto."
                elif "HTTP 429" in detail:
                    answer = "OpenAI respondió HTTP 429: revisa los límites, créditos y facturación del proyecto."
                else:
                    answer = "No se pudo verificar la conexión con OpenAI. Revisa la configuración y los registros de Render."
            await send_message(thread, "ASTRA INTELLIGENCE · PRUEBA\n" + answer, source, reply)
            return
        if sub in ("olvidar", "forget"):
            removed = AI_CORE.forget(session_key)
            await send_message(thread, "Contexto temporal eliminado." if removed else "No había contexto temporal para eliminar.", source, reply)
            return
        if sub in ("preguntar", "genshin", "consulta"):
            if not rest:
                await send_message(thread, "Uso: !ia preguntar <consulta>.", source, reply)
                return
            prompt = ("Consulta de Genshin Impact. Usa la enciclopedia o guías existentes para verificar. " if sub == "genshin" else "") + rest
            result = await AI_CORE.assist(prompt, session_key, external_request)
            if result.get("kind") == "command":
                invocation = validate_ai_tool_call(result.get("command", ""), result.get("args", ""))
                if invocation:
                    AI_CORE.remember_tool_action(session_key, invocation)
                    routed_event = dict(event)
                    routed_message = dict(msg)
                    routed_message["content"] = f"{PREFIX}{invocation}"
                    routed_event["message"] = routed_message
                    await handle_command(routed_event, source)
                    return
            await send_message(thread, result.get("answer") or "No pude responder.", source, reply)
            return
        if sub == "musica":
            if not rest:
                await send_message(thread, "Uso: !ia musica <ambiente o descripción>.", source, reply)
                return
            try:
                optimized = await AI_CORE.optimize_music_query(rest, external_request, rate_key=session_key)
                invocation = validate_ai_tool_call("dj", f"buscar {optimized}")
                if not invocation:
                    raise ValueError("Búsqueda no válida")
                AI_CORE.remember_tool_action(session_key, invocation)
                routed_event = dict(event)
                routed_message = dict(msg)
                routed_message["content"] = f"{PREFIX}{invocation}"
                routed_event["message"] = routed_message
                await handle_command(routed_event, source)
            except Exception:
                logging.exception("[AI] Error optimizando consulta musical")
                await send_message(thread, "No pude optimizar la búsqueda. Prueba !dj buscar <tema>.", source, reply)
            return
        if sub == "traducir":
            pair = rest.split("|", 1)
            if len(pair) != 2 or not pair[0].strip() or not pair[1].strip():
                await send_message(thread, "Uso: !ia traducir idioma | texto.", source, reply)
                return
            try:
                answer = await AI_CORE.translate(
                    pair[0].strip(), pair[1].strip(), external_request, rate_key=session_key
                )
            except Exception:
                logging.exception("[AI] Error de traducción")
                answer = "No pude completar la traducción con IA. Comprueba la configuración del proveedor."
            await send_message(thread, answer, source, reply)
            return
        if sub == "guardian":
            if not is_admin_uid(admin_uid):
                await send_message(thread, "Solo un administrador puede usar Guardian AI.", source, reply)
                return
            parts = rest.split(maxsplit=1)
            if len(parts) != 2 or parts[0].casefold() not in ("revisar", "analizar"):
                await send_message(thread, "Uso: !ia guardian revisar <texto>.", source, reply)
                return
            try:
                answer = await AI_CORE.review_guardian(parts[1], external_request, rate_key=session_key)
            except Exception:
                logging.exception("[AI] Error en Guardian AI")
                answer = "No pude analizar el texto. Comprueba la conexión con el proveedor."
            await send_message(thread, "GUARDIAN AI · REVISIÓN PREVENTIVA\n" + answer, source, reply)
            return
        if sub == "analista":
            if not is_admin_uid(admin_uid):
                await send_message(thread, "Solo un administrador puede controlar el analizador.", source, reply)
                return
            mode = rest.casefold()
            if mode in ("activar", "on"):
                AI_CORE.set_analyst(thread, True)
                await send_message(
                    thread,
                    "AVISO · AI CHAT ANALYST ACTIVADO\n"
                    "Desde este momento, Katherine recopilará temporalmente los mensajes nuevos "
                    "de esta sala para generar resúmenes solicitados por un administrador. "
                    "El contenido se mantiene solo en memoria del proceso, hasta una hora "
                    "y máximo 200 mensajes; no se consulta el historial anterior. "
                    "Un administrador puede detenerlo con !ia analista desactivar.",
                    source, reply
                )
                return
            elif mode in ("desactivar", "off"):
                AI_CORE.set_analyst(thread, False)
                answer = "AI Chat Analyst desactivado y buffer temporal eliminado."
            elif mode in ("limpiar", "vaciar"):
                count = AI_CORE.clear_analyst_messages(thread)
                answer = f"Buffer temporal limpiado: {count} mensajes descartados."
            elif mode in ("resumen", "resumir"):
                if not AI_CORE.analyst_messages(thread):
                    answer = "No hay mensajes temporales. Activa primero el analizador."
                else:
                    await send_message(thread, "AI CHAT ANALYST\nPreparando resumen del buffer temporal…", source, reply)
                    try:
                        answer = await AI_CORE.summarize_chat(
                            thread, external_request, rate_key=session_key
                        )
                    except Exception:
                        logging.exception("[AI] Error en Chat Analyst")
                        answer = "No pude generar el resumen. Los mensajes siguen en memoria hasta caducar o desactivar."
                    await send_message(thread, answer, source)
                    return
            else:
                answer = "Uso: !ia analista activar|resumen|limpiar|desactivar."
            await send_message(thread, answer, source, reply)
            return
        if sub in ("diagnostico", "diagnóstico"):
            if not is_admin_uid(admin_uid):
                await send_message(thread, "Solo un administrador puede usar Astra Admin AI.", source, reply)
                return
            try:
                diagnostic = await build_status_message(source, nickname, thread)
                answer = await AI_CORE.explain_diagnostics(
                    diagnostic, external_request, rate_key=session_key
                )
            except Exception:
                logging.exception("[AI] Error en Astra Admin AI")
                answer = "No pude interpretar el diagnóstico. Consulta !estado para ver los datos técnicos."
            await send_message(thread, "ASTRA ADMIN AI\n" + answer, source, reply)
            return
        await send_message(thread, "Subcomando IA desconocido. Usa !ia ayuda.", source, reply)
        return

    if command in ("!centro", "!nexus"):
        await send_nexus_center(thread, source, raw_uid, nickname, reply)
        return

    if command in ("!fuentes", "!saludfuentes"):
        await send_message(thread, format_source_health(SOURCE_HEALTH), source, reply)
        return

    if command in ("!evento", "!eventos"):
        await handle_community_event_command(
            thread, source, raw_uid, nickname, args, reply, admin_uid=admin_uid
        )
        return

    if command in ("!admin", "!paneladmin", "!guardian"):
        if not is_admin_uid(admin_uid):
            await send_message(thread, "No tienes permisos de administración.", source, reply)
            return
        category = None
        if args:
            category = {
                "resumen": "summary", "actividad": "activity",
                "antispam": "antispam", "avisos": "warnings",
                "advertencias": "warnings", "historial": "audit",
                "auditoria": "audit", "auditoría": "audit"
            }.get(args[0].casefold())
        if args and category is None:
            await send_message(thread, "Uso: !admin [resumen|actividad|antispam|avisos|historial].", source, reply)
            return
        await send_admin_panel(thread, source, admin_uid, category, reply)
        return

    if command == "!respuestas":
        if not is_admin_uid(admin_uid):
            await send_message(thread, "Solo un administrador puede configurar respuestas automáticas.", source, reply)
            return
        sub = args[0].casefold() if args else "estado"
        if sub in ("on", "activar", "off", "desactivar"):
            enabled = sub in ("on", "activar")
            set_chat_setting(thread, "auto_replies_enabled", "1" if enabled else "0")
            log_admin_action(thread, raw_uid, nickname, "respuestas", "activadas" if enabled else "desactivadas")
            answer = f"Respuestas automáticas {'activadas' if enabled else 'desactivadas'}."
        elif sub in ("estado", "status"):
            enabled = get_chat_setting(thread, "auto_replies_enabled", "0") == "1"
            answer = (
                f"Respuestas automáticas: {'activadas' if enabled else 'desactivadas'}.\n"
                "Configura reglas con !respuesta añadir palabra | respuesta."
            )
        else:
            answer = "Uso: !respuestas on/off/estado."
        await send_message(thread, answer, source, reply)
        return

    if command == "!respuesta":
        if not is_admin_uid(admin_uid):
            await send_message(thread, "Solo un administrador puede gestionar reglas de respuesta.", source, reply)
            return
        parts = query.split(maxsplit=1)
        sub = parts[0].casefold() if parts else "ayuda"
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub in ("añadir", "agregar", "crear"):
            pair = rest.split("|", 1)
            if len(pair) != 2:
                answer = "Uso: !respuesta añadir palabra o frase | texto de respuesta. Puedes usar {usuario} para insertar el nombre."
            else:
                try:
                    rule_id = add_auto_reply_rule(thread, pair[0], pair[1], raw_uid)
                    log_admin_action(thread, raw_uid, nickname, "respuesta_creada", pair[0][:80])
                    answer = f"Regla #{rule_id} guardada. Activa el sistema con !respuestas on."
                except ValueError as exc:
                    answer = str(exc)
        elif sub in ("eliminar", "borrar", "quitar"):
            try:
                rule_id = int(rest)
                removed = remove_auto_reply_rule(thread, rule_id)
            except ValueError:
                removed = False
                rule_id = 0
            if removed:
                log_admin_action(thread, raw_uid, nickname, "respuesta_eliminada", f"Regla #{rule_id}")
            answer = "Regla eliminada." if removed else "No encontré esa regla. Uso: !respuesta eliminar ID."
        elif sub in ("lista", "listar"):
            rules = list_auto_reply_rules(thread)
            lines = ["REGLAS DE RESPUESTA"]
            lines.extend(
                f"#{item['id']} · {item['trigger_phrase']} → {item['response_text'][:120]}"
                for item in rules
            )
            answer = "\n".join(lines) if rules else "No hay reglas. Crea una con !respuesta añadir palabra | respuesta."
        else:
            answer = (
                "RESPUESTAS INTELIGENTES\n"
                "!respuestas on/off/estado — activa o pausa el sistema.\n"
                "!respuesta añadir palabra | respuesta — crea o actualiza una regla.\n"
                "!respuesta lista — muestra reglas.\n"
                "!respuesta eliminar ID — borra una regla.\n"
                "Usa {usuario} en una respuesta para mencionar el nombre."
            )
        await send_message(thread, answer, source, reply)
        return

    if command == "!traducir":
        pair = query.split("|", 1)
        if len(pair) != 2:
            await send_message(
                thread,
                "Uso: !traducir idioma_destino | texto. Ejemplo: !traducir inglés | Buenos días, ¿cómo estás?",
                source, reply
            )
            return
        try:
            answer = await translate_text(pair[1].strip(), pair[0].strip())
        except Exception as exc:
            answer = f"No pude traducir el texto: {str(exc)[:260]}"
        await send_message(thread, answer, source, reply)
        return

    media_aliases = {
        "!imagen": "imagen", "!video": "video", "!audio": "audio"
    }
    if command in ("!multimedia", "!media") or command in media_aliases:
        if command in media_aliases:
            media_kind = media_aliases[command]
            media_query = query
        else:
            media_kind = args[0].casefold() if args else ""
            media_query = " ".join(args[1:]).strip()
        if not media_kind or not media_query:
            await send_message(
                thread,
                "Uso: !multimedia imagen|video|audio <búsqueda>. También puedes usar !imagen, !video o !audio.",
                source, reply
            )
            return
        try:
            answer = await search_multimedia(media_kind, media_query, 5)
        except Exception as exc:
            answer = f"No pude completar la búsqueda multimedia: {str(exc)[:240]}"
        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # AYUDA
    # -----------------------------------------------------------------------
    if command in ("!ayuda", "!comandos"):
        help_sections = [
            ("00 · INICIO RÁPIDO", [
                "También puedes dirigirte a Katherine: «Katherine, ...». La mención aislada no activa el bot.",
                f"{PREFIX}ayuda / comandos — muestra este catálogo.",
                f"{PREFIX}miuid / mi-id / mi_uid — muestra tu UID real de Anarchy Apps.",
                f"{PREFIX}asistente <consulta> — consulta el núcleo conversacional de Katherine.",
            ]),
            ("01 · GENSHIN IMPACT · GUÍAS Y ARCHIVO", [
                f"{PREFIX}guia <personaje> — guía de build, armas, artefactos, talentos y equipos.",
                f"{PREFIX}cobertura <personaje> — apartados disponibles y fuentes de la guía.",
                f"{PREFIX}equipos <personaje> — composiciones recomendadas.",
                f"{PREFIX}rotacion <personaje> — rotación documentada.",
                f"{PREFIX}sinergia <personaje 1>, <personaje 2> — composiciones que incluyen a ambos.",
                f"{PREFIX}materiales <personaje> — materiales de ascensión y talentos.",
                f"{PREFIX}jefes — lista de jefes reconocidos.",
                f"{PREFIX}banner / calendario — banners y eventos con fuentes verificables.",
                f"{PREFIX}enciclopedia / archivo / teyvat <nombre> — consulta el archivo de Teyvat.",
                f"{PREFIX}buscargenshin <texto> — busca personajes y objetos.",
                f"{PREFIX}lista personajes|enemigos [página] — listados por categoría.",
                f"{PREFIX}enemigo / geografia / elemento <nombre> — fichas de Teyvat.",
                f"{PREFIX}reacciones <elemento> — reacciones elementales.",
                f"{PREFIX}curiosidad — dato aleatorio de Genshin.",
            ]),
            ("02 · GENSHIN IMPACT · SIMULACIÓN", [
                f"{PREFIX}deseo — simulador de deseos con pity independiente.",
                f"{PREFIX}pity — consulta el contador del simulador.",
                f"{PREFIX}resetpity — reinicia tu pity simulado.",
                "La simulación no modifica tu cuenta real de Genshin Impact.",
            ]),
            ("03 · SOCIAL Y PERFILES", [
                f"{PREFIX}perfil [@usuario] — consulta un perfil.",
                f"{PREFIX}bio <texto> — cambia tu biografía.",
                f"{PREFIX}miestado <texto> — cambia tu estado.",
                f"{PREFIX}afinidad / amistad @usuario — consulta afinidad.",
                f"{PREFIX}regalo @usuario [mensaje] — envía un regalo simbólico.",
                f"{PREFIX}muro — muestra tus regalos recibidos.",
                f"{PREFIX}actividad [hoy|semana] — actividad y horas pico de la sala.",
                f"{PREFIX}interaccion @usuario — abre interacciones mediante botones.",
                f"{PREFIX}diario / daily — reclama tu recompensa diaria.",
                f"{PREFIX}trabajar — gana puntos con tiempo de espera.",
                f"{PREFIX}pagar / transferir @usuario <cantidad> — transfiere puntos.",
                f"{PREFIX}roast @usuario — broma amistosa.",
            ]),
            ("04 · INTERACCIONES SOCIALES", [
                "Usa !comando @usuario para estas acciones:",
                "abrazo, abrazar, beso, besar, patada, golpe, cachetada, acariciar, mimar, morder.",
                "consolar, felicitar, saludar, proteger, bailar, cosquillas, chocar, highfive.",
                "ayudar, regalar, acompañar, asustar, curar, cargar, empujar, regañar.",
                "admirar, apoyar, dormir, ignorar, mirar e invitar.",
            ]),
            ("05 · JUEGOS Y ENTRETENIMIENTO", [
                f"{PREFIX}trivia / responder <1-4> — trivia por turnos.",
                f"{PREFIX}adivina / intento <número> — juego de adivinar.",
                f"{PREFIX}puntos / reputacion — consulta tu saldo.",
                f"{PREFIX}ranking / top — clasificación de puntos.",
                f"{PREFIX}encuesta <pregunta> | <opciones> — crea una encuesta.",
                f"{PREFIX}encuesta resultados <ID> / cerrar <ID> — gestiona tu encuesta.",
                f"{PREFIX}elige opción 1 | opción 2 — elección con botones.",
                f"{PREFIX}duelo @usuario — desafío entre usuarios.",
                f"{PREFIX}ship <usuario 1> <usuario 2> — compatibilidad ficticia.",
                f"{PREFIX}azar <mínimo> <máximo> / dado / moneda — azar.",
                f"{PREFIX}verdad / 8ball <pregunta> — respuestas de entretenimiento.",
                f"{PREFIX}chiste / frase / suerte / reto — entretenimiento y retos.",
            ]),
            ("06 · VOZ, TTS Y REPRODUCTOR", [
                f"{PREFIX}join — conecta a Katherine a la llamada.",
                f"{PREFIX}leer / noleer — activa o desactiva lectura en voz alta.",
                f"{PREFIX}tts ajustes / voces — consulta ajustes y voces disponibles.",
                f"{PREFIX}tts voz <nombre> / velocidad <-50 a 100> / volumen <0-100>.",
                f"{PREFIX}tts enlaces on/off / menciones on/off — filtros de lectura.",
                f"{PREFIX}volumenlectura / subirvolumenlectura / bajarvolumenlectura / silenciolectura.",
                f"{PREFIX}dj buscar <tema> — busca en Internet Archive, Commons y Jamendo.",
                f"{PREFIX}buscar <tema> — alias de búsqueda musical.",
                f"{PREFIX}dj ambiente <mood> — búsqueda por ambiente.",
                f"{PREFIX}dj fuentes — consulta catálogos disponibles.",
                f"{PREFIX}dj favorito lista / guardar <número> / reproducir <id> / quitar <id>.",
                f"{PREFIX}dj pausa / reanudar / saltar / cola / actual — controles de reproducción.",
                f"{PREFIX}dj añadir <archivo> / lista — administra la cola y audios locales.",
                f"{PREFIX}dj <archivo.mp3> / reproducir <archivo.mp3> — reproduce audio local.",
                f"{PREFIX}playlist crear / agregar / ver / reproducir / borrar / lista.",
                f"{PREFIX}volumen / volume [0-100] — consulta o ajusta música.",
                f"{PREFIX}subirvolumen / bajarvolumen [cantidad] / silencio.",
                f"{PREFIX}djdetener / djstop / salirsala — detiene y sale de la sala.",
                f"{PREFIX}salas — consulta salas en vivo.",
            ]),
            ("07 · UTILIDADES Y HERRAMIENTAS", [
                f"{PREFIX}fecha — fecha y hora de Chile.",
                f"{PREFIX}zona <Area/Ciudad> — hora de otra zona horaria.",
                f"{PREFIX}menu / menú — abre el menú interactivo.",
                f"{PREFIX}economia — explica puntos y recompensas.",
                f"{PREFIX}traducir <idioma> | <texto> — traduce texto.",
                f"{PREFIX}multimedia / media imagen|video|audio <consulta> — busca recursos públicos.",
                f"{PREFIX}imagen / video / audio <consulta> — accesos directos multimedia.",
                f"{PREFIX}evento / eventos crear <título> — crea un evento temporal de sala.",
                f"{PREFIX}evento estado / cerrar — consulta o cierra el evento (creador o administrador).",
                f"{PREFIX}ia ayuda / estado / olvidar — ayuda y contexto de tu sesión.",
                f"{PREFIX}ia preguntar <consulta> / genshin <consulta> — conversación y Teyvat AI.",
                f"{PREFIX}ia musica <descripción> / traducir <idioma> | <texto> — Eclipse y Language AI.",
            ]),
            ("08 · SISTEMA Y ADMINISTRACIÓN · UID REQUERIDO", [
                f"{PREFIX}estado — diagnóstico interno del bot. [ADMIN]",
                f"{PREFIX}fuentes / saludfuentes — salud de proveedores externos. [ADMIN]",
                f"{PREFIX}autotest — ejecuta pruebas internas. [ADMIN]",
                f"{PREFIX}centro / nexus — centro de control de Katherine. [ADMIN]",
                f"{PREFIX}admin / paneladmin / guardian — panel de administración. [ADMIN]",
                f"{PREFIX}antispam estado/on/off — consulta o controla antispam. [ADMIN]",
                f"{PREFIX}antispam limite <3-20> / ventana <3-60> / repeticiones <2-10>.",
                f"{PREFIX}antispam enlaces <2-12> / menciones <3-20> — configura filtros. [ADMIN]",
                f"{PREFIX}advertir @usuario <motivo> — registra una advertencia. [ADMIN]",
                f"{PREFIX}advertencias @usuario — consulta advertencias. [ADMIN]",
                f"{PREFIX}quitaradvertencia <ID> — retira una advertencia. [ADMIN]",
                f"{PREFIX}perfilbot <nombre> | <estado> — modifica el perfil del bot. [ADMIN]",
                f"{PREFIX}avatarbot <URL> — cambia el avatar del bot. [ADMIN]",
                f"{PREFIX}respuestas on/off/estado — activa o pausa respuestas automáticas. [ADMIN]",
                f"{PREFIX}respuesta añadir/lista/eliminar — administra reglas de respuesta. [ADMIN]",
                f"{PREFIX}ia probar — comprueba el proveedor de IA. [ADMIN]",
                f"{PREFIX}ia guardian revisar <texto> — revisión preventiva. [ADMIN]",
                f"{PREFIX}ia analista activar/resumen/limpiar/desactivar — análisis temporal. [ADMIN]",
                f"{PREFIX}ia diagnostico — explicación asistida del estado técnico. [ADMIN]",
                "Los comandos [ADMIN] validan el UID real recibido, nunca el nickname.",
                "Configura ADMIN_UIDS en Render con los UID autorizados; separa varios con coma o punto y coma.",
            ]),
        ]

        page_count = len(help_sections)
        await send_message(
            thread,
            "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮\n"
            "│       K A T H E R I N E       │\n"
            "│     COMMAND DIRECTORY     │\n"
            "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯\n"
            f"Prefijo: {PREFIX}  ·  También se aceptan ! y -\n"
            "Usa los ejemplos tal como aparecen.",
            source,
            reply
        )
        reply = None

        for page_index, (section_title, entries) in enumerate(help_sections, 1):
            panel_lines = [
                "╭━━━━━━━━━━━━━━━━━━━━━━━━━━━━╮",
                f"│  {section_title}",
                f"│  PÁGINA {page_index:02d}/{page_count:02d}",
                "├────────────────────────────",
            ]
            panel_lines.extend(f"│  ✦ {entry}" for entry in entries)
            panel_lines.extend([
                "╰━━━━━━━━━━━━━━━━━━━━━━━━━━━━╯",
                "Katherine · Centro de ayuda"
            ])
            await send_message(thread, "\n".join(panel_lines), source, reply)
        return

    if command in ("!menu", "!menú"):
        await send_interactive_menu(
            thread, source, raw_uid, nickname, reply_id=reply
        )
        return

    if command == "!autotest":
        results = await asyncio.to_thread(run_katherine_self_tests)
        lines = [
            "AUTOTEST — KATHERINE",
            f"Pruebas ejecutadas: {results['checks']}",
            f"Correctas: {results['passed']}",
            f"Fallidas: {results['failed']}"
        ]
        if results["failures"]:
            lines.append("\nERRORES")
            lines.extend("• " + value for value in results["failures"][:15])
        else:
            lines.append("\nTodas las pruebas internas finalizaron correctamente.")
        await send_message(thread, "\n".join(lines), source, reply)
        return

    if command == "!interaccion":
        target_uid, target_name = target_from_message(msg, args, scope_key)
        if not target_name:
            await send_message(thread, "Uso: !interaccion @usuario", source, reply)
            return
        if target_uid == uid:
            await send_message(thread, "Elige a otra persona para esta interacción.", source, reply)
            return

        buttons = register_buttons(
            source,
            thread,
            "social_action",
            [
                {"label": "Abrazo", "value": "abrazo"},
                {"label": "Beso", "value": "beso"},
                {"label": "Acariciar", "value": "acariciar"},
                {"label": "Consolar", "value": "consolar"},
                {"label": "Patada", "value": "patada"}
            ],
            {
                "actor_uid": uid,
                "actor_name": nickname,
                "target_uid": target_uid,
                "target_name": target_name
            },
            owner_uid=uid,
            ttl=600
        )
        await send_message(
            thread,
            f"INTERACCIÓN CON {target_name.upper()}\nElige qué quieres hacer:",
            source,
            reply,
            buttons
        )
        return

    # -----------------------------------------------------------------------
    # INTERACCIONES SOCIALES
    # -----------------------------------------------------------------------
    if command[1:] in SOCIAL:
        target = next(
            (
                m.get("nickname") or m.get("uid")
                for m in msg.get("mentions", [])
                if isinstance(m, dict)
            ),
            None
        )

        target = target or next(
            (x for x in args if x.startswith("@")),
            None
        )

        if not target and query:
            target = query.split()[0]

        if not target:
            await send_message(
                thread,
                f"Uso: {command} @usuario",
                source,
                reply
            )
            return

        actions = SOCIAL[command[1:]]
        target_uid, target_name = target_from_message(msg, args, scope_key)

        if target_uid and target_uid != uid:
            add_affinity(uid, target_uid)

        await send_message(
            thread,
            f"{nickname} {random.choice(actions)} {target}.",
            source,
            reply
        )
        return

    if command == "!roast":
        target_uid, target_name = target_from_message(msg, args, scope_key)

        # Respaldo: algunas variantes del evento incluyen la mención sin UID.
        if not target_name:
            for mention in (msg.get("mentions") or []):
                if not isinstance(mention, dict):
                    continue
                target_name = (
                    mention.get("nickname")
                    or mention.get("username")
                    or mention.get("displayName")
                    or mention.get("name")
                    or mention.get("uid")
                    or mention.get("userId")
                    or mention.get("id")
                )
                if target_name:
                    target_name = str(target_name).lstrip("@")
                    break

        # Acepta @usuario, <@usuario> y también un nickname escrito directamente.
        if not target_name and args:
            raw_target = str(args[0]).strip()
            target_name = re.sub(r"^(?:<@!?|@)", "", raw_target).rstrip(">.,;:!?")

        if not target_name:
            await send_message(
                thread,
                f"Uso: {PREFIX}roast @usuario",
                source,
                reply
            )
            return

        roasts = [
            "tiene menos suerte que una cuenta sin resina.",
            "farmea tanta DEF que los artefactos ya le tienen cariño.",
            "necesita una guía hasta para encontrar el botón de iniciar.",
            "hace tan poco daño que los slimes piden que no se contenga.",
            "su build tiene potencial… algún día lo encontraremos.",
            "sus artefactos tienen más DEF que ganas de cooperar.",
            "cuando ve un crítico, piensa que fue un error visual.",
            "su rotación es tan caótica que ni Cyno encuentra el chiste.",
            "el Abismo lo vio entrar y decidió cerrar por mantenimiento."
        ]

        await send_message(
            thread,
            f"🔥 Katherine contraataca: {target_name}, {random.choice(roasts)}",
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # PERFIL SOCIAL
    # -----------------------------------------------------------------------
    if command == "!bio":
        if not query:
            await send_message(
                thread,
                "Uso: !bio tu descripción (máximo 240 caracteres).",
                source,
                reply
            )
            return

        if len(query) > 240:
            await send_message(
                thread,
                "La biografía admite hasta 240 caracteres.",
                source,
                reply
            )
            return

        update_user_field(uid, "bio", query)
        await send_message(thread, "Tu biografía fue actualizada.", source, reply)
        return

    if command == "!miestado":
        if not query:
            user = get_user(uid) or {}
            await send_message(
                thread,
                f"Tu estado actual: {user.get('status') or 'Sin estado'}. "
                "Usa !miestado texto o !miestado borrar.",
                source,
                reply
            )
            return

        value = "" if query.lower() in ("borrar", "limpiar", "-", "off") else query

        if len(value) > 80:
            await send_message(
                thread,
                "El estado admite hasta 80 caracteres.",
                source,
                reply
            )
            return

        update_user_field(uid, "status", value)

        await send_message(
            thread,
            "Estado actualizado." if value else "Estado eliminado.",
            source,
            reply
        )
        return

    if command == "!perfil":
        target_uid, target_name = target_from_message(msg, args, scope_key)

        if target_uid:
            user = get_user(target_uid)
        elif target_name:
            user = find_user_by_nickname(target_name, scope_key)
        else:
            user = get_user(uid)

        if not user:
            await send_message(
                thread,
                "No encuentro ese perfil todavía. La persona debe escribir "
                "al menos un comando en este chat para que Katherine pueda registrarla.",
                source,
                reply
            )
            return

        affinity = get_affinity(uid, user["uid"]) if user["uid"] != uid else 0

        lines = [
            f"PERFIL — {user['nickname']}",
            f"Estado: {user['status'] or 'Sin estado'}",
            f"Biografía: {user['bio'] or 'Sin biografía'}",
            f"Puntos: {user['points']} | Mensajes vistos: {user['message_count']}"
        ]

        if user["uid"] != uid:
            lines.append(f"Afinidad contigo: {affinity}")

        await send_message(
            thread,
            "\n".join(lines),
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # AFINIDAD Y REGALOS
    # -----------------------------------------------------------------------
    if command in ("!afinidad", "!amistad"):
        target_uid, target_name = target_from_message(msg, args, scope_key)

        if not target_name:
            await send_message(
                thread,
                "Uso: !afinidad @usuario.",
                source,
                reply
            )
            return

        if not target_uid:
            await send_message(
                thread,
                f"No tengo registrado el UID de {target_name}. "
                "Esa persona debe escribir un comando primero.",
                source,
                reply
            )
            return

        score = get_affinity(uid, target_uid)

        await send_message(
            thread,
            f"Afinidad entre {nickname} y {target_name}: {score} puntos. "
            "Las interacciones suman como máximo 1 punto cada 30 segundos.",
            source,
            reply
        )
        return

    if command == "!regalo":
        target_uid, target_name = target_from_message(msg, args, scope_key)

        if not target_name:
            await send_message(
                thread,
                "Uso: !regalo @usuario [mensaje opcional].",
                source,
                reply
            )
            return

        if not target_uid:
            await send_message(
                thread,
                f"No puedo registrar el regalo porque {target_name} "
                "aún no está en los perfiles de Katherine.",
                source,
                reply
            )
            return

        if target_uid == uid:
            await send_message(
                thread,
                "No puedes enviarte un regalo a ti mismo.",
                source,
                reply
            )
            return

        message = " ".join(
            x for x in args if not x.startswith("@")
        )[:120]

        save_gift(uid, nickname, target_uid, target_name, message)
        add_affinity(uid, target_uid)

        await send_message(
            thread,
            f"🎁 {nickname} le envió un regalo a {target_name}."
            + (f"\nMensaje: {message}" if message else ""),
            source,
            reply
        )
        return

    if command == "!muro":
        gifts = get_recent_gifts(uid)

        text_gifts = "\n".join(
            f"• {sender}: {message or 'Te envió un regalo.'}"
            for sender, message in gifts
        )

        await send_message(
            thread,
            "TU MURO DE REGALOS\n\n"
            + (text_gifts or "Aún no has recibido regalos."),
            source,
            reply
        )
        return

    if command == "!actividad":
        period = args[0].casefold() if args else "semana"
        if period not in ("hoy", "semana"):
            await send_message(thread, "Uso: !actividad [hoy|semana].", source, reply)
            return
        await send_message(thread, build_activity_report(thread, period), source, reply)
        return

    # -----------------------------------------------------------------------
    # GUÍAS, ESTADÍSTICAS Y BÚSQUEDA AMPLIADA DE GENSHIN
    # -----------------------------------------------------------------------
    if command == "!materiales":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}materiales personaje", source, reply)
            return
        answer = await get_character_material_summary(query)
        await send_message(
            thread,
            answer or f"No encontré materiales de ascensión para «{query}» en la base consultada.",
            source, reply
        )
        return

    if command == "!equipos":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}equipos personaje", source, reply)
            return
        data = await collect_character_guide_data(query)
        teams = get_team_suggestions(data["key"], data.get("community_data"))
        answer = (
            f"EQUIPOS RECOMENDADOS · {data['name'].upper()}\n\n"
            + "\n".join(f"{i}. {team}" for i, team in enumerate(teams[:8], 1))
            if teams else f"No encontré composiciones verificables para {data['name']}."
        )
        await send_message(thread, answer, source, reply)
        return

    if command == "!rotacion":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}rotacion personaje", source, reply)
            return
        data = await collect_character_guide_data(query)
        rotation = data.get("rotation")
        answer = (
            f"ROTACIÓN · {data['name'].upper()}\n\n{rotation}"
            if rotation else f"No encontré una rotación publicada y verificable para {data['name']}. No voy a inventar una."
        )
        await send_message(thread, answer, source, reply)
        return

    if command == "!sinergia":
        names = [part.strip() for part in re.split(r"[,;+]", query) if part.strip()]
        if len(names) < 2:
            await send_message(thread, f"Uso: {PREFIX}sinergia personaje 1, personaje 2", source, reply)
            return
        if len(names) > 4:
            await send_message(thread, "Analizo un máximo de cuatro personajes por consulta.", source, reply)
            return
        datasets = await asyncio.gather(*(collect_character_guide_data(name) for name in names))
        keys = {item["key"] for item in datasets}
        shared_teams = []
        for item in datasets:
            for team in get_team_suggestions(item["key"], item.get("community_data")):
                folded = str(team).casefold()
                if all(name.casefold() in folded for name in names):
                    shared_teams.append(str(team))
        if shared_teams:
            answer = "SINERGIA · " + " + ".join(item["name"] for item in datasets) + "\n\n"
            answer += "Composiciones encontradas que incluyen a todos:\n"
            answer += "\n".join(f"• {team}" for team in dict.fromkeys(shared_teams)[:6])
            answer += "\n\nEstas coincidencias provienen de equipos publicados; no son una simulación de daño."
        else:
            answer = (
                "SINERGIA · " + " + ".join(item["name"] for item in datasets)
                + "\n\nNo encontré un equipo publicado que incluya a todos los personajes. "
                "No voy a afirmar que tienen sinergia solo por sus elementos."
            )
        await send_message(thread, answer, source, reply)
        return

    if command == "!jefes":
        if query:
            data = await game_data("enemies", query)
            answer = describe_game(data, query, "enemies") if isinstance(data, dict) else f"No encontré el jefe «{query}»."
        else:
            data = await game_data("enemies", "boss")
            if isinstance(data, dict):
                answer = describe_game(data, "Jefes", "enemies")
            elif isinstance(data, list):
                answer = "JEFES DISPONIBLES\n\n" + "\n".join(
                    f"• {clean_game_text(item.get('name') if isinstance(item, dict) else item)}"
                    for item in data[:35]
                )
            else:
                answer = "La base de datos no entregó una lista de jefes clasificable."
        await send_message(thread, answer, source, reply)
        return

    if command in ("!banner", "!calendario"):
        category = "banner" if command == "!banner" else "events"
        items = await fetch_official_news(category)
        answer = format_official_news(
            items,
            "GACHAPONES OFICIALES · GENSHIN IMPACT" if category == "banner"
            else "EVENTOS OFICIALES · GENSHIN IMPACT",
            "No encontré anuncios oficiales de esta categoría en la página pública en español. Inténtalo más tarde."
        )
        await send_message(thread, answer, source, reply)
        return

    if command == "!cobertura":
        if not query:
            await send_message(
                thread,
                f"Uso: {PREFIX}cobertura nombre del personaje",
                source,
                reply
            )
            return
        progress = await start_progress_message(
            thread, f"COBERTURA · {query}\nRevisando los apartados y las fuentes disponibles…",
            source, reply
        )
        async def coverage_progress_update(text):
            await update_progress_message(progress, text)
        guide_data = await collect_character_guide_data(
            query, progress_callback=coverage_progress_update
        )
        if not isinstance(guide_data.get("character"), dict):
            await send_message(
                thread,
                f"No encontré la ficha de «{query}» en Genshin-DB. "
                "No puedo calcular una cobertura fiable.",
                source,
                reply
            )
            return
        await finish_progress_message(
            progress,
            format_character_guide_coverage(guide_data),
            thread, source, reply
        )
        return

    if command == "!guia":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}guia nombre del personaje", source, reply)
            return

        progress = await start_progress_message(
            thread, f"GUÍA · {query}\nKatherine está consultando la ficha y contrastando fuentes…",
            source, reply
        )
        async def guide_progress_update(text):
            await update_progress_message(progress, text)
        guide_data = await collect_character_guide_data(
            query, progress_callback=guide_progress_update
        )
        character = guide_data["character"]
        stats = guide_data["stats"]
        name = guide_data["name"]
        key = guide_data["key"]
        artifact = guide_data["artifact"]
        weapons = guide_data["weapons"]
        priority = guide_data["priority"]
        team = guide_data["team"]
        helper_data = guide_data["helper_data"]
        kqm_data = guide_data["kqm_data"]
        community_data = guide_data["community_data"]

        if not isinstance(character, dict) and not helper_data and not kqm_data:
            await send_message(
                thread,
                f"No encontré información de «{query}» en Genshin-DB, Genshin Builds ni KQM.",
                source,
                reply
            )
            return

        sections = [f"╔══ GUÍA COMPLETA · {name.upper()} ══╗"]
        if isinstance(character, dict):
            sections.append(describe_game(character, name, "characters"))
            base = get_level90(stats)
            if isinstance(base, dict):
                sections.append(
                    "ESTADÍSTICAS BASE · NIVEL 90\n"
                    f"Vida: {format_number(base.get('hp'))} · ATQ: {format_number(base.get('attack'))} · "
                    f"DEF: {format_number(base.get('defense'))}\n"
                    f"Atributo de ascensión: {base.get('specialized', 'No informado')}"
                )
        else:
            sections.append(
                "La ficha básica no respondió desde Genshin-DB. "
                "Se muestran las recomendaciones recuperadas de las fuentes secundarias."
            )

        if artifact:
            sections.append(
                "ARTEFACTOS RECOMENDADOS\n"
                f"Sets: {artifact['sets']}\n"
                f"Reloj / Copa / Corona: {artifact['main']}\n"
                f"Substats: {artifact['subs']}\n"
                f"Consejo: {artifact['note']}"
            )
        elif helper_data and helper_data.get("artifacts"):
            sections.append(
                "ARTEFACTOS RECOMENDADOS · GENSHIN IMPACT HELPER TEAM\n"
                + helper_data["artifacts"]
            )
        elif community_data and (
            community_data.get("artifacts") or community_data.get("stats")
        ):
            artifact_lines = ["ARTEFACTOS RECOMENDADOS · FUENTES EXTERNAS"]
            if community_data.get("artifacts"):
                artifact_lines.append("Sets:\n" + community_data["artifacts"])
            if community_data.get("stats"):
                artifact_lines.append("Estadísticas:\n" + community_data["stats"])
            sections.append("\n".join(artifact_lines))
        else:
            sections.append(
                "ARTEFACTOS\nNo se encontró una recomendación verificable en las fuentes consultadas."
            )

        if weapons:
            sections.append(
                "ARMAS RECOMENDADAS\n"
                + "\n".join(f"{i}. {value}" for i, value in enumerate(weapons["weapons"], 1))
                + f"\nNota: {weapons['note']}"
            )
        elif helper_data and helper_data.get("weapons"):
            sections.append(
                "ARMAS RECOMENDADAS · GENSHIN IMPACT HELPER TEAM\n"
                + helper_data["weapons"]
            )
        elif community_data and community_data.get("weapons"):
            sections.append(
                "ARMAS RECOMENDADAS · FUENTES EXTERNAS\n"
                + community_data["weapons"]
            )
        else:
            sections.append(
                "ARMAS\nNo se encontró una clasificación verificable en las fuentes consultadas."
            )

        if team:
            sections.append(f"EQUIPO RECOMENDADO\n{team[0]}\nSinergia: {team[1]}")
        elif kqm_data and kqm_data.get("teams"):
            sections.append(
                "EQUIPOS RECOMENDADOS · KQM\n"
                + "\n".join(
                    f"{index}. {value}"
                    for index, value in enumerate(kqm_data["teams"], 1)
                )
                + "\nLas composiciones se muestran como aparecen en la guía original."
            )
        elif community_data and community_data.get("teams"):
            sections.append(
                "EQUIPOS RECOMENDADOS · " + str(
                    GUIDE_COVERAGE_SOURCE_NAMES.get(
                        (community_data.get("field_sources") or {}).get("teams"),
                        "FUENTE EXTERNA"
                    )
                ) + "\n" + community_data["teams"]
            )
        else:
            sections.append(
                "EQUIPOS\nNo se encontró una composición verificable en las fuentes consultadas."
            )

        if priority:
            sections.append(
                f"PRIORIDAD DE TALENTOS\n{priority[0]}\nMotivo: {priority[1]}"
            )
        elif helper_data and helper_data.get("talents"):
            sections.append(
                "PRIORIDAD DE TALENTOS · GENSHIN IMPACT HELPER TEAM\n"
                + helper_data["talents"]
            )
        elif community_data and community_data.get("talents"):
            sections.append(
                "PRIORIDAD DE TALENTOS · FUENTE EXTERNA\n"
                + community_data["talents"]
            )
        else:
            sections.append(
                "PRIORIDAD DE TALENTOS\nNo se encontró una prioridad verificable en las fuentes consultadas."
            )

        if isinstance(character, dict):
            talent_lines = ["HABILIDADES Y PASIVAS"]
            skill_entries = character.get("skillTalents") or character.get("talents") or []
            passive_entries = character.get("passiveTalents") or []
            if isinstance(skill_entries, dict):
                skill_entries = list(skill_entries.values())
            if isinstance(passive_entries, dict):
                passive_entries = list(passive_entries.values())
            if not isinstance(skill_entries, list):
                skill_entries = []
            if not isinstance(passive_entries, list):
                passive_entries = []
            for index, entry in enumerate(skill_entries, 1):
                if not isinstance(entry, dict):
                    continue
                title = clean_game_text(entry.get("name") or entry.get("unlock") or f"Habilidad {index}")
                desc = clean_game_text(entry.get("description") or "")
                talent_lines.append(f"{index}. {title}")
                if desc:
                    talent_lines.append(game_excerpt(desc, 300))
            for index, entry in enumerate(passive_entries, 1):
                if not isinstance(entry, dict):
                    continue
                title = clean_game_text(entry.get("name") or f"Pasiva {index}")
                desc = clean_game_text(entry.get("description") or "")
                talent_lines.append(f"Pasiva {index}. {title}")
                if desc:
                    talent_lines.append(game_excerpt(desc, 200))
            if len(talent_lines) > 1:
                sections.append("\n".join(talent_lines))

            constellation = CONSTELLATION_GUIDES.get(key)
            constellation_lines = ["CONSTELACIONES Y PRIORIDAD"]
            if constellation:
                constellation_lines.extend([
                    f"Recomendación: {constellation[0]}",
                    constellation[1]
                ])
            else:
                constellation_lines.append(
                    "C0 es un punto de partida válido; revisa las mejoras según tu inversión."
                )
            constellation_data = (
                character.get("constellations")
                or character.get("Constellations")
                or []
            )
            if isinstance(constellation_data, dict):
                constellation_data = list(constellation_data.values())
            if isinstance(constellation_data, list):
                for index, entry in enumerate(constellation_data[:6], 1):
                    if not isinstance(entry, dict):
                        continue
                    title = clean_game_text(entry.get("name") or f"C{index}")
                    description = clean_game_text(entry.get("description") or "")
                    constellation_lines.append(f"C{index} · {title}")
                    if description:
                        constellation_lines.append(game_excerpt(description, 180))
            sections.append("\n".join(constellation_lines))

            totals = aggregate_material_costs(character.get("costs"))
            if totals:
                sections.append(
                    "MATERIALES DE ASCENSIÓN · TOTAL\n"
                    + "\n".join(
                        f"• {material}: {amount}"
                        for material, amount in totals.items()
                    )
                )

        if helper_data:
            version = helper_data.get("version") or "No informada"
            sections.append(
                "FUENTE SECUNDARIA · GENSHIN IMPACT HELPER TEAM\n"
                f"Datos de build: versión {version}\n"
                f"Repositorio: {helper_data.get('source_url')}"
            )

        if kqm_data and kqm_data.get("url"):
            sections.append(
                "FUENTE DE EQUIPOS · KEQINGMAINS\n"
                + kqm_data["url"]
                + "\nLa guía original puede estar en inglés."
            )

        if community_data:
            source_lines = []
            if community_data.get("source_labels"):
                source_lines.extend(community_data["source_labels"])
            for field in ("weapons", "artifacts", "stats", "talents", "teams"):
                url = community_data.get(field + "_url")
                if url:
                    source_lines.append(url)
            if source_lines:
                sections.append(
                    "FUENTES DE RESPALDO CONSULTADAS\n"
                    + "\n".join(dict.fromkeys(source_lines))
                )

        if community_data and community_data.get("rotation"):
            sections.append("ROTACIÓN Y COMBOS\n" + game_excerpt(community_data["rotation"], 700))
        if community_data and community_data.get("source_updated"):
            sections.append("VIGENCIA DE LA FUENTE\nActualización indicada por la página: " + str(community_data["source_updated"]))
        sections.append(format_character_guide_coverage(guide_data))
        sections.append(
            "RECOMENDACIÓN FINAL\n"
            "Ajusta las estadísticas y armas a tu equipo, rotación y necesidades de energía. "
            "Las clasificaciones pueden variar según la composición y la versión del juego."
        )
        await finish_progress_message(
            progress,
            "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n".join(sections),
            thread, source, reply
        )
        return

    if command == "!reacciones":
        if not query:
            await send_message(
                thread,
                f"Uso: {PREFIX}reacciones pyro, hydro, dendro, electro, cryo, anemo o geo.",
                source,
                reply
            )
            return
        element = query.casefold().strip()
        matrix = {
            "pyro": [
                "Hydro: Vaporización",
                "Cryo: Derretido",
                "Electro: Sobrecarga",
                "Dendro: Quemadura",
                "Dendro + Hydro: Florecimiento"
            ],
            "hydro": [
                "Pyro: Vaporización",
                "Cryo: Congelado",
                "Electro: Electrocargado",
                "Dendro: Florecimiento"
            ],
            "cryo": [
                "Pyro: Derretido",
                "Hydro: Congelado",
                "Electro: Superconductor"
            ],
            "electro": [
                "Pyro: Sobrecarga",
                "Hydro: Electrocargado",
                "Cryo: Superconductor",
                "Dendro: Aceleración (Agravado/Propagación)"
            ],
            "dendro": [
                "Hydro: Florecimiento",
                "Electro: Aceleración",
                "Pyro: Quemadura"
            ],
            "anemo": [
                "Pyro/Hydro/Electro/Cryo: Dispersión",
                "No dispersa Dendro ni Geo"
            ],
            "geo": [
                "Pyro/Hydro/Electro/Cryo: Cristalización",
                "No cristaliza con Anemo ni Dendro"
            ]
        }
        values = matrix.get(element)
        if not values:
            await send_message(
                thread,
                "Elemento no reconocido. Usa Pyro, Hydro, Cryo, Electro, Dendro, Anemo o Geo.",
                source,
                reply
            )
            return
        await send_message(
            thread,
            f"REACCIONES DE {element.upper()}\n"
            + "\n".join(f"• {value}" for value in values)
            + "\n\nLa reacción concreta depende del orden de aplicación y de las auras.",
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # EXPLORADOR DINÁMICO DE GENSHIN-DB
    # -----------------------------------------------------------------------
    if command in ("!enciclopedia", "!archivo", "!teyvat"):
        if not query:
            await send_message(
                thread,
                """ENCICLOPEDIA DE TEYVAT
Consulta personajes, armas, artefactos, enemigos, materiales, comida, dominios, regiones y elementos.
Uso: !enciclopedia <nombre>""",
                source,
                reply
            )
            return
        categories = [
            ("Personajes", "characters"),
            ("Armas", "weapons"),
            ("Artefactos", "artifacts"),
            ("Enemigos", "enemies"),
            ("Materiales", "materials"),
            ("Comida", "foods"),
            ("Dominios", "domains"),
            ("Geografía", "geographies"),
            ("Elementos", "elements"),
        ]
        results = await asyncio.gather(
            *(game_data(folder, query) for _, folder in categories),
            return_exceptions=True
        )
        sections = []
        for (label, folder), data in zip(categories, results):
            if isinstance(data, Exception) or data is None:
                continue
            if isinstance(data, dict):
                name = clean_game_text(data.get("name") or data.get("Name") or query)
                if name.casefold() == query.casefold() or query.casefold() in name.casefold():
                    detail = describe_game(data, query, folder)
                    if detail and not detail.startswith("No encontré"):
                        sections.append(f"【{label}】\n{detail}")
            elif isinstance(data, list) and data:
                matches = [
                    clean_game_text(item.get("name") if isinstance(item, dict) else item)
                    for item in data[:8]
                ]
                if any(query.casefold() in item.casefold() for item in matches):
                    sections.append(f"【{label}】\n" + ", ".join(matches[:6]))
        if sections:
            answer = f"ARCHIVO DE TEYVAT · {query}\n\n" + "\n\n".join(sections[:5])
        else:
            answer = (
                f"No encontré una ficha exacta de «{query}» en la enciclopedia. "
                "Prueba con el nombre oficial, un alias o una consulta más específica."
            )
        await send_message(thread, answer, source, reply)
        return

    if command == "!buscargenshin":
        if not query:
            await send_message(
                thread,
                f"Uso: {PREFIX}buscargenshin nombre. Busca en personajes, armas, artefactos, enemigos, materiales, comida y dominios.",
                source,
                reply
            )
            return

        folders = [
            ("Personaje", "characters"),
            ("Arma", "weapons"),
            ("Artefacto", "artifacts"),
            ("Enemigo", "enemies"),
            ("Material", "materials"),
            ("Comida", "foods"),
            ("Dominio", "domains")
        ]
        results = await asyncio.gather(
            *(game_data(folder, query) for _, folder in folders)
        )

        found = []
        for (label, folder), data in zip(folders, results):
            if isinstance(data, dict):
                name = clean_game_text(data.get("name") or data.get("Name") or query)
                found.append(f"• {label}: {name} — usa {PREFIX}{ {'characters':'personaje','weapons':'arma','artifacts':'artefacto','enemies':'enemigo','materials':'material','foods':'comida','domains':'dominio'}[folder] } {query}")
            elif isinstance(data, list) and data:
                found.append(f"• {label}: {', '.join(clean_game_text(x) for x in data[:3])}")

        answer = f"BÚSQUEDA GENSHIN — {query}\n\n" + (
            "\n".join(found) if found else "No encontré coincidencias en las categorías consultadas."
        )
        await send_message(thread, answer, source, reply)
        return

    if command == "!lista":
        categories = {
            "personajes": "characters",
            "personaje": "characters",
            "enemigos": "enemies",
            "enemigo": "enemies"
        }

        args_list = query.split()
        if not args_list or args_list[0].casefold() not in categories:
            await send_message(
                thread,
                f"Uso: {PREFIX}lista categoría [página]. Categorías: personajes o enemigos.",
                source,
                reply
            )
            return

        category = args_list[0].casefold()
        try:
            page = max(1, int(args_list[1])) if len(args_list) > 1 else 1
        except ValueError:
            page = 1

        folder = categories[category]
        names = await game_data(
            folder,
            "names",
            {"matchCategories": "true"}
        )

        if isinstance(names, dict) and isinstance(names.get("result"), list):
            names = names["result"]

        if not isinstance(names, list) or not names:
            await send_message(
                thread,
                f"No pude obtener la lista de {category} desde Genshin-DB.",
                source,
                reply
            )
            return

        names = [clean_game_text(x) for x in names if isinstance(x, str)]
        per_page = 20
        pages = max(1, (len(names) + per_page - 1) // per_page)
        if page > pages:
            await send_message(thread, f"Solo existen {pages} página(s) para {category}.", source, reply)
            return

        selected = names[(page - 1) * per_page:page * per_page]
        answer = (
            f"LISTA DE {category.upper()} — PÁGINA {page}/{pages}\n"
            + "\n".join(f"{(page - 1) * per_page + i + 1}. {name}" for i, name in enumerate(selected))
            + f"\n\nTotal registrado: {len(names)}"
        )
        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # CONSULTAS DE OBJETOS DE GENSHIN
    # -----------------------------------------------------------------------
    object_commands = {
        "!enemigo": "enemies",
        "!geografia": "geographies",
        "!elemento": "elements"
    }

    if command in object_commands:
        if not query:
            await send_message(
                thread,
                f"Uso: {command} nombre",
                source,
                reply
            )
            return

        data = await game_data(object_commands[command], query)

        await send_message(
            thread,
            describe_game(data, query, object_commands[command]),
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # CURIOSIDADES
    # -----------------------------------------------------------------------
    if command == "!curiosidad":
        global curiosity_bag, last_curiosity

        if not curiosity_bag:
            curiosity_bag = CURIOSITIES.copy()
            random.shuffle(curiosity_bag)

            if (
                len(curiosity_bag) > 1
                and curiosity_bag[-1] == last_curiosity
            ):
                curiosity_bag[0], curiosity_bag[-1] = (
                    curiosity_bag[-1],
                    curiosity_bag[0]
                )

        last_curiosity = curiosity_bag.pop()

        await send_message(
            thread,
            "CURIOSIDAD\n" + last_curiosity,
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # DESEOS SIMULADOS
    # -----------------------------------------------------------------------
    if command == "!deseo":
        w = get_wish_state(uid)
        w["pity5"] += 1
        w["pity4"] += 1
        five_star_chance = (
            1.0 if w["pity5"] >= 90
            else min(1.0, 0.006 + max(0, w["pity5"] - 73) * 0.06)
        )

        if random.random() < five_star_chance:
            featured_characters = ["Cyno", "Nahida", "Furina", "Arlecchino", "Mavuika"]
            standard_characters = ["Jean", "Diluc", "Mona", "Qiqi", "Keqing", "Tighnari", "Dehya"]
            is_featured = w["guaranteed"] or random.random() < 0.5
            name = random.choice(featured_characters if is_featured else standard_characters)
            w.update(pity5=0, pity4=0, guaranteed=not is_featured)
            save_wish_state(uid, w)
            result = (
                f"RESULTADO SIMULADO\n⭐⭐⭐⭐⭐\n{name}\n"
                + ("¡Ganaste el destacado!" if is_featured
                   else "Perdiste el 50/50. Tu próximo 5 estrellas estará garantizado.")
                + "\nNo afecta tu cuenta real de Genshin."
            )
            await send_message(thread, result, source, reply)
        elif w["pity4"] >= 10 or random.random() < 0.051:
            w["pity4"] = 0
            save_wish_state(uid, w)
            await send_message(
                thread,
                "RESULTADO SIMULADO\n⭐⭐⭐⭐\n"
                + random.choice(["Bennett", "Xingqiu", "Fischl", "Sucrose", "Diona"]),
                source,
                reply
            )
        else:
            save_wish_state(uid, w)
            await send_message(
                thread,
                "RESULTADO SIMULADO\n⭐⭐⭐\nArma de 3 estrellas",
                source,
                reply
            )
        return

    if command == "!pity":
        w = get_wish_state(uid)
        await send_message(
            thread,
            "DESEOS SIMULADOS\n"
            f"5 estrellas: {w['pity5']}\n"
            f"4 estrellas: {w['pity4']}\n"
            f"Garantizado: {'Sí' if w['guaranteed'] else 'No'}",
            source,
            reply
        )
        return

    if command == "!resetpity":
        w = {"pity5": 0, "pity4": 0, "guaranteed": False}
        save_wish_state(uid, w)
        await send_message(
            thread,
            "Contador simulado reiniciado. No modifica tu cuenta.",
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # TRIVIA
    # -----------------------------------------------------------------------
    if command == "!trivia":
        q = random.choice(TRIVIA)
        game_id = secrets.token_hex(5)
        trivia[game_key] = {
            "q": q,
            "tries": 0,
            "game_id": game_id,
            "owner_uid": uid,
            "expires": time.time() + 180
        }
        buttons = register_buttons(
            source,
            thread,
            "trivia_answer",
            [{"label": answer, "value": i} for i, answer in enumerate(q[1])],
            {"game_key": game_key, "game_id": game_id},
            owner_uid=uid,
            ttl=180
        )
        await send_message(
            thread,
            "TRIVIA KATHERINE\n\n" + q[0] + "\n\nSelecciona tu respuesta:",
            source,
            reply,
            buttons
        )
        return

    if command == "!responder":
        game = trivia.get(game_key)

        if not game or game["expires"] < time.time():
            trivia.pop(game_key, None)

            await send_message(
                thread,
                "No hay una trivia activa. Usa !trivia.",
                source,
                reply
            )
            return

        try:
            choice = int(args[0]) - 1
        except (ValueError, IndexError):
            choice = -1

        game["tries"] += 1

        if choice == game["q"][2]:
            add_points(uid, 10)
            trivia.pop(game_key, None)
            answer = "¡Correcto! Ganaste 10 puntos."

        elif game["tries"] >= 3:
            answer = (
                "Se acabaron los intentos. Era: "
                f"{game['q'][1][game['q'][2]]}."
            )
            trivia.pop(game_key, None)

        else:
            answer = (
                f"No es correcto. Te quedan "
                f"{3-game['tries']} intento(s)."
            )

        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # ADIVINAR NÚMERO
    # -----------------------------------------------------------------------
    if command == "!adivina":
        number_games[game_key] = {
            "number": random.randint(1, 100),
            "tries": 0,
            "expires": time.time() + 300
        }

        await send_message(
            thread,
            "He elegido un número entre 1 y 100. "
            "Tienes siete intentos. Usa !intento número.",
            source,
            reply
        )
        return

    if command == "!intento":
        game = number_games.get(game_key)

        try:
            n = int(args[0])
        except (ValueError, IndexError):
            n = 0

        if not game or game["expires"] < time.time():
            number_games.pop(game_key, None)
            answer = "No hay partida activa. Usa !adivina."

        elif not 1 <= n <= 100:
            answer = "Escribe un entero del 1 al 100."

        else:
            game["tries"] += 1

            if n == game["number"]:
                add_points(uid, 15)
                answer = f"¡Correcto! Era {n}. Ganaste 15 puntos."
                number_games.pop(game_key, None)

            elif game["tries"] >= 7:
                answer = f"Se acabaron los intentos. Era {game['number']}."
                number_games.pop(game_key, None)

            else:
                answer = "Es mayor." if n < game["number"] else "Es menor."

        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # PUNTOS Y RANKING
    # -----------------------------------------------------------------------
    if command in ("!puntos", "!reputacion"):
        user = get_user(uid) or {}

        await send_message(
            thread,
            f"{nickname}, tienes {user.get('points', 0)} puntos.",
            source,
            reply
        )
        return

    if command in ("!ranking", "!top"):
        top = get_top_users("points", 10, scope_key)

        answer = "RANKING KATHERINE\n\n" + (
            "\n".join(
                f"{i}. {u} — {p} puntos"
                for i, (u, p) in enumerate(top, 1)
            )
            or "Aún no hay puntos."
        )

        await send_message(thread, answer, source, reply)
        return

    if command in ("!diario", "!daily"):
        amount, remaining = claim_reward(uid, "daily", 86400, 25, 50)
        if amount is None:
            answer = f"Ya reclamaste tu recompensa diaria. Vuelve en {format_duration(remaining)}."
        else:
            add_points(uid, amount)
            answer = (
                f"🎁 Recompensa diaria: +{amount} puntos. "
                f"Ahora tienes {(get_user(uid) or {}).get('points', 0)} puntos."
            )
        await send_message(thread, answer, source, reply)
        return

    if command == "!trabajar":
        amount, remaining = claim_reward(uid, "work", 3600, 8, 22)
        if amount is None:
            answer = f"Ya trabajaste recientemente. Vuelve en {format_duration(remaining)}."
        else:
            add_points(uid, amount)
            answer = f"🛠️ Terminaste tu jornada y ganaste {amount} puntos."
        await send_message(thread, answer, source, reply)
        return

    if command in ("!pagar", "!transferir"):
        target_uid, target_name = target_from_message(msg, args, scope_key)
        amounts = [int(value) for value in args if re.fullmatch(r"\d+", value)]
        amount = amounts[-1] if amounts else 0

        if not target_name or not target_uid or not amount:
            answer = "Uso: !pagar @usuario cantidad"
        elif amount > 10000:
            answer = "El límite por transferencia es de 10.000 puntos."
        else:
            result = transfer_points(uid, target_uid, amount)
            if result == "ok":
                answer = f"💸 Transferiste {amount} puntos a {target_name}."
            elif result == "insufficient":
                answer = "No tienes suficientes puntos para esa transferencia."
            elif result == "missing":
                answer = "Ambos perfiles deben estar registrados en este chat."
            else:
                answer = "La transferencia no es válida."
        await send_message(thread, answer, source, reply)
        return

    if command == "!economia":
        await send_message(
            thread,
            "ECONOMÍA DE KATHERINE\n"
            "!puntos — consulta tu saldo.\n"
            "!diario — reclama 25-50 puntos cada 24 horas.\n"
            "!trabajar — gana 8-22 puntos cada hora.\n"
            "!pagar @usuario cantidad — transfiere hasta 10.000 puntos.",
            source,
            reply
        )
        return


    if command == "!encuesta":
        sub = args[0].casefold() if args else ""
        if sub in ("resultados", "cerrar"):
            if len(args) < 2 or not args[1].isdigit():
                await send_message(thread, "Uso: !encuesta resultados ID o !encuesta cerrar ID.", source, reply)
                return
            code = args[1]
            selected = next(
                (
                    item for item in polls.values()
                    if item.get("code") == code
                    and item.get("source") == source
                    and item.get("thread") == thread
                ),
                None
            )
            if not selected:
                await send_message(thread, "No encontré esa encuesta en esta sala; pudo haber caducado o Katherine reinició.", source, reply)
                return
            if sub == "cerrar":
                if selected.get("creator_uid") != raw_uid and not is_admin_uid(admin_uid):
                    await send_message(thread, "Solo quien creó la encuesta o un administrador puede cerrarla.", source, reply)
                    return
                selected["closed"] = True
                await send_message(thread, "Encuesta cerrada.\n\n" + format_poll_results(selected), source, reply)
            else:
                await send_message(thread, format_poll_results(selected), source, reply)
            return

        parts = [value.strip() for value in query.split("|") if value.strip()]
        if len(parts) < 3:
            await send_message(
                thread,
                "Uso: !encuesta pregunta | opción 1 | opción 2 [| duracion=15m | anonima]. Duración de 1 minuto a 7 días.",
                source, reply
            )
            return
        question = parts[0][:180]
        options = []
        duration_seconds = 86400
        anonymous = False
        for value in parts[1:]:
            lowered = value.casefold().strip()
            duration_match = re.fullmatch(r"(?:duracion\s*=\s*)?(\d{1,4})\s*([mhd])", lowered)
            if lowered in ("anonima", "anónima", "anonymous"):
                anonymous = True
            elif duration_match:
                amount = int(duration_match.group(1))
                unit = duration_match.group(2)
                duration_seconds = amount * {"m": 60, "h": 3600, "d": 86400}[unit]
            else:
                options.append(value[:40])
        if len(options) < 2:
            await send_message(thread, "Una encuesta necesita al menos dos opciones válidas.", source, reply)
            return
        options = options[:5]
        if not 60 <= duration_seconds <= 604800:
            await send_message(thread, "La duración debe estar entre 1 minuto y 7 días.", source, reply)
            return

        created_at = time.time()
        closes_at = created_at + duration_seconds
        poll_nonce = str(time.time_ns())[-15:]
        poll_id = f"{source}:{thread}:{poll_nonce}"
        poll_code = str(secrets.randbelow(900000) + 100000)
        while any(item.get("code") == poll_code for item in polls.values()):
            poll_code = str(secrets.randbelow(900000) + 100000)
        polls[poll_id] = {
            "code": poll_code,
            "question": question,
            "options": options,
            "votes": {},
            "source": source,
            "thread": thread,
            "creator_uid": raw_uid,
            "creator_name": nickname,
            "anonymous": anonymous,
            "closed": False,
            "created_at": created_at,
            "closes_at": closes_at,
            "expires": closes_at + 86400
        }
        if len(polls) > 500:
            oldest_id = min(polls, key=lambda key: polls[key].get("created_at", 0))
            polls.pop(oldest_id, None)

        buttons = [
            {"id": f"p{poll_nonce}_{index}", "label": option}
            for index, option in enumerate(options)
        ]
        poll_button_map[poll_nonce] = poll_id
        minutes = max(1, int(duration_seconds // 60))
        mode = "anónima" if anonymous else "pública"
        await send_message(
            thread,
            f"ENCUESTA · ID {poll_code}\n{question}\n"
            f"Modalidad: {mode} · Cierre: {minutes} min.\n"
            "Selecciona una opción. Puedes cambiar tu voto antes del cierre.",
            source, reply, buttons
        )
        return

        parts = [
            x.strip()
            for x in query.split("|")
            if x.strip()
        ]

        if len(parts) < 3:
            await send_message(
                thread,
                "Uso: !encuesta Pregunta | Opción 1 | Opción 2 [| Opción 3...]",
                source,
                reply
            )
            return

        question = parts[0]
        options = parts[1:6]
        poll_nonce = str(time.time_ns())[-15:]
        poll_id = f"{source}:{thread}:{poll_nonce}"

        polls[poll_id] = {
            "question": question,
            "options": options,
            "votes": {},
            "source": source,
            "thread": thread,
            "created_at": time.time(),
            "expires": time.time() + 86400
        }
        if len(polls) > 500:
            oldest_id = min(polls, key=lambda key: polls[key].get("created_at", 0))
            polls.pop(oldest_id, None)

        buttons = [
            {
                "id": f"p{poll_id.split(':')[-1]}_{i}",
                "label": opt
            }
            for i, opt in enumerate(options)
        ]

        poll_button_map[poll_id.split(":")[-1]] = poll_id

        await send_message(
            thread,
            "ENCUESTA\n" + question + "\nElige una opción:",
            source,
            reply,
            buttons
        )
        return

    # -----------------------------------------------------------------------
    # ENTRETENIMIENTO
    # -----------------------------------------------------------------------
    if command == "!elige":
        choices = [
            value.strip()
            for value in query.split("|")
            if value.strip()
        ][:5]
        if not choices:
            await send_message(
                thread,
                "Uso: !elige opción 1 | opción 2 [| opción 3...]",
                source,
                reply
            )
            return
        buttons = register_buttons(
            source,
            thread,
            "choose_option",
            [{"label": option, "value": option} for option in choices],
            {"created_by": nickname},
            ttl=900
        )
        await send_message(
            thread,
            f"ELECCIÓN DE KATHERINE\n{nickname} propone elegir entre:",
            source,
            reply,
            buttons
        )
        return

    if command == "!duelo":
        target_uid, target_name = target_from_message(msg, args, scope_key)
        if not target_name:
            target_name = "un rival misterioso"

        buttons = register_buttons(
            source,
            thread,
            "duel_response",
            [
                {"label": "Aceptar duelo", "value": "accept"},
                {"label": "Rechazar", "value": "decline"}
            ],
            {
                "challenger_uid": uid,
                "challenger_name": nickname,
                "target_uid": target_uid,
                "target_name": target_name
            },
            owner_uid=target_uid,
            ttl=300
        )
        await send_message(
            thread,
            f"⚔️ {nickname} ha retado a {target_name} a un duelo. ¿Aceptas el desafío?",
            source,
            reply,
            buttons
        )
        return

    if command == "!ship":
        if len(args) < 2:
            answer = "Uso: !ship usuario1 usuario2"
        else:
            pair = "|".join(
                sorted((args[0].casefold(), args[1].casefold()))
            )

            score = int(
                hashlib.sha256(pair.encode()).hexdigest()[:8],
                16
            ) % 101

            answer = (
                f"Compatibilidad de {args[0]} y {args[1]}: {score}% 💞"
            )

        await send_message(thread, answer, source, reply)
        return

    if command == "!azar":
        parts = query.replace(",", " ").split()
        try:
            if len(parts) != 2:
                raise ValueError
            low, high = map(int, parts)
            if low > high or high - low > 1_000_000:
                raise ValueError
            answer = f"🎲 Resultado aleatorio: {random.randint(low, high)}"
        except ValueError:
            answer = "Uso: !azar mínimo máximo (rango máximo: 1.000.000)."
        await send_message(thread, answer, source, reply)
        return

    if command == "!verdad":
        truths = [
            "¿Qué personaje de Genshin representa mejor tu personalidad?",
            "¿Cuál fue tu peor decisión gastando Protogemas?",
            "¿Qué personaje te gusta aunque casi nunca lo uses?",
            "¿Qué juego te da vergüenza admitir que disfrutas?",
            "¿Cuál es tu mayor manía cuando juegas?"
        ]
        await send_message(thread, "VERDAD\n" + random.choice(truths), source, reply)
        return

    if command == "!dado":
        await send_message(
            thread,
            f"🎲 Resultado: {random.randint(1, 6)}",
            source,
            reply
        )
        return

    if command == "!moneda":
        await send_message(
            thread,
            random.choice(["🪙 Cara", "🪙 Cruz"]),
            source,
            reply
        )
        return

    if command == "!8ball":
        await send_message(
            thread,
            random.choice([
                "Sí.",
                "No.",
                "Es probable.",
                "No puedo asegurarlo.",
                "Definitivamente.",
                "Vuelve a preguntar más tarde."
            ]),
            source,
            reply
        )
        return

    if command == "!chiste":
        await send_message(
            thread,
            random.choice(JOKES),
            source,
            reply
        )
        return

    if command == "!frase":
        await send_message(
            thread,
            random.choice([
                "La paciencia también es una forma de fuerza.",
                "Incluso la noche más larga termina.",
                "No subestimes los pequeños pasos."
            ]),
            source,
            reply
        )
        return

    if command == "!reto":
        await send_message(
            thread,
            random.choice([
                "Completa un dominio sin comida.",
                "Usa un personaje que nunca incluyes en tu equipo.",
                "Explora una zona sin teletransportarte.",
                "Derrota un jefe sin usar la definitiva."
            ]),
            source,
            reply
        )
        return

    if command == "!suerte":
        await send_message(
            thread,
            random.choice([
                "Hoy tendrás buena suerte.",
                "La paciencia será tu aliada.",
                "Una oportunidad inesperada aparecerá."
            ]),
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # SISTEMA Y ESTADO DEL BOT
    # -----------------------------------------------------------------------
    if command == "!estado":
        answer = await build_status_message(source, nickname, thread)
        await send_message(thread, answer, source, reply)
        return

    if command == "!fecha":
        try:
            now = datetime.now(ZoneInfo("America/Santiago"))
        except Exception:
            now = datetime.now()

        await send_message(
            thread,
            now.strftime("Fecha: %d/%m/%Y\nHora Chile: %H:%M:%S"),
            source,
            reply
        )
        return

    if command == "!zona":
        try:
            zone = ZoneInfo(query)

            await send_message(
                thread,
                datetime.now(zone).strftime("%Y-%m-%d %H:%M:%S %Z"),
                source,
                reply
            )

        except Exception:
            await send_message(
                thread,
                "Zona horaria inválida. Ejemplo: !zona America/Santiago",
                source,
                reply
            )

        return

    if command == "!perfilbot":
        if not is_admin_uid(admin_uid):
            await send_message(
                thread,
                "No tienes permiso para modificar el perfil de Katherine. "
                "Configura ADMIN_UIDS en Render con los UID autorizados.",
                source,
                reply
            )
            return

        pieces = query.split("|", 1)
        name = pieces[0].strip()
        status = pieces[1].strip() if len(pieces) > 1 else None

        if not 2 <= len(name) <= 32 or (
            status is not None and len(status) > 40
        ):
            await send_message(
                thread,
                "Uso: !perfilbot Nombre | Estado "
                "(nombre 2-32 caracteres; estado máximo 40). "
                "Usa - para borrar el estado.",
                source,
                reply
            )
            return

        result = await api(
            "PATCH",
            "/me",
            "community",
            {
                "name": name,
                "status": None if status == "-" else status,
                "commandPrefix": PREFIX
            }
        )

        await send_message(
            thread,
            "Perfil actualizado."
            if result["ok"]
            else f"No se pudo actualizar: HTTP {result['status']}.",
            source,
            reply
        )
        return

    if command == "!avatarbot":
        if not is_admin_uid(admin_uid):
            await send_message(
                thread,
                "No tienes permiso para cambiar el avatar de Katherine.",
                source,
                reply
            )
            return

        avatar_value = query.strip().strip("<>")
        if not avatar_value:
            await send_message(
                thread,
                "Uso: !avatarbot URL_DE_IMAGEN_O_ARTICULO\n"
                "Acepta una imagen HTTPS directa o una página de Namu Wiki. "
                "Formatos: PNG, JPG, WebP o GIF; máximo 512 KB.",
                source,
                reply
            )
            return

        if avatar_value.casefold() in {
            "eliminar", "quitar", "borrar", "reset", "default"
        }:
            await send_message(
                thread,
                "La API permite cambiar el icono mediante PUT /me/icon, "
                "pero no documenta una operación para eliminarlo. No realicé cambios.",
                source,
                reply
            )
            return

        try:
            image_bytes, mime = await resolve_avatar_image(avatar_value)
        except Exception as exc:
            await send_message(
                thread,
                f"No pude preparar el avatar: {str(exc)[:220]}",
                source,
                reply
            )
            return

        # Actualiza el mismo bot que recibió el comando (developer o community).
        result = await api_upload_bytes(
            "/me/icon",
            source,
            image_bytes,
            mime
        )

        if not result.get("ok"):
            payload = result.get("data") or {}
            api_error = (
                payload.get("anarchy:error")
                or payload.get("anarchy:message")
                or payload.get("error")
                if isinstance(payload, dict)
                else None
            )
            detail = f" ({api_error})" if api_error else ""
            await send_message(
                thread,
                f"No se pudo actualizar el avatar. HTTP {result.get('status', '?')}{detail}.",
                source,
                reply
            )
            return

        payload = result.get("data") or {}
        bot_data = payload.get("bot") if isinstance(payload, dict) else {}
        icon_url = bot_data.get("icon") if isinstance(bot_data, dict) else None

        if not icon_url:
            # La respuesta documentada incluye bot.icon; consultamos /me solo
            # como respaldo para confirmar que el icono quedó asociado al bot.
            check = await api("GET", "/me", "community")
            check_data = check.get("data") if check.get("ok") else {}
            check_bot = check_data.get("bot") if isinstance(check_data, dict) else {}
            icon_url = check_bot.get("icon") if isinstance(check_bot, dict) else None

        if icon_url:
            await send_message(
                thread,
                f"Avatar de Katherine actualizado y confirmado por Anarchy Apps "
                f"({len(image_bytes) // 1024} KB).",
                source,
                reply
            )
        else:
            await send_message(
                thread,
                "La carga fue aceptada por Anarchy Apps, pero no pude confirmar "
                "el campo bot.icon en la respuesta ni en GET /me. "
                "No marcaré el cambio como verificado.",
                source,
                reply
            )
        return

    if command == "!join":
        ok, message = await ensure_voice_room(thread)
        await send_message(
            thread,
            f"🎙️ {message}" if ok else f"Voz: {message}",
            source,
            reply
        )
        return

    if command == "!leer":
        ok, message = await enable_room_reading(thread)
        await send_message(
            thread,
            f"🔊 {message}" if ok else f"Lectura: {message}",
            source,
            reply
        )
        return

    if command == "!noleer":
        ok, message = await disable_room_reading(thread)
        await send_message(
            thread,
            f"🔇 {message}" if ok else f"Lectura: {message}",
            source,
            reply
        )
        return

    if command in (
        "!volumenlectura", "!subirvolumenlectura",
        "!bajarvolumenlectura", "!silenciolectura"
    ):
        state = DJ_ROOMS.get(thread)
        if not state:
            await send_message(
                thread,
                "No hay una llamada activa. Usa !join o !leer para configurar el volumen de lectura.",
                source, reply
            )
            return
        current = int(round(float(state.get("tts_volume", 0.75)) * 100))
        last = int(round(float(state.get("last_tts_volume", 0.75)) * 100))
        if command == "!silenciolectura":
            values = ["mute"]
        elif command == "!subirvolumenlectura":
            values = ["subir"] + args
        elif command == "!bajarvolumenlectura":
            values = ["bajar"] + args
        else:
            values = args
        if not values:
            await send_message(
                thread,
                f"🔊 Volumen de lectura actual: {current}%\n"
                "Usa !volumenlectura 0-100, !subirvolumenlectura [cantidad], "
                "!bajarvolumenlectura [cantidad] o !silenciolectura.",
                source, reply
            )
            return
        try:
            target = calculate_volume_target(values, current, last)
        except (TypeError, ValueError):
            await send_message(
                thread,
                "Volumen de lectura inválido. Usa un valor entre 0 y 100 o un ajuste +10/-10.",
                source, reply
            )
            return
        if target is None:
            await send_message(thread, f"Volumen de lectura actual: {current}%.", source, reply)
            return
        target = update_tts_volume(thread, state, target)
        await send_message(
            thread,
            f"🔊 Volumen de lectura ajustado a {target}%."
            + (" Silencio de lectura activado." if target == 0 else ""),
            source, reply
        )
        return

    if command in ("!volumen", "!volume", "!subirvolumen", "!bajarvolumen", "!silencio"):
        state = DJ_ROOMS.get(thread)
        if not state:
            await send_message(
                thread,
                "No hay una llamada activa. Usa !join o !dj para conectar a Katherine primero.",
                source,
                reply
            )
            return

        current = int(round(float(state.get("volume", 0.75)) * 100))
        target = current
        amount = 10
        raw_value = (args[0].strip().lower() if args else "")

        try:
            if command == "!silencio":
                target = 0
            elif command == "!subirvolumen":
                amount = abs(int(raw_value)) if raw_value else 10
                target = min(100, current + amount)
            elif command == "!bajarvolumen":
                amount = abs(int(raw_value)) if raw_value else 10
                target = max(0, current - amount)
            elif not raw_value:
                await send_message(
                    thread,
                    f"🔊 Volumen de música actual: {current}%\n"
                    "Usa !volumen 0-100, !volumen +10, !volumen -10, "
                    "!volumen subir, !volumen bajar, !volumen mute o !volumen max.",
                    source,
                    reply
                )
                return
            elif raw_value in ("subir", "up", "+"):
                amount = abs(int(args[1])) if len(args) > 1 else 10
                target = min(100, current + amount)
            elif raw_value in ("bajar", "down", "-"):
                amount = abs(int(args[1])) if len(args) > 1 else 10
                target = max(0, current - amount)
            elif raw_value in ("mute", "silencio", "mudo", "0"):
                target = 0
            elif raw_value in ("max", "máximo", "maximo"):
                target = 100
            elif raw_value in ("restaurar", "normal", "unmute"):
                target = int(round(float(state.get("last_volume", 0.75)) * 100))
            elif raw_value.startswith(("+", "-")):
                target = max(0, min(100, current + int(raw_value)))
            else:
                target = int(raw_value.replace("%", ""))
                if not 0 <= target <= 100:
                    raise ValueError("rango")
        except (TypeError, ValueError):
            await send_message(
                thread,
                "Volumen inválido. Usa un valor de 0 a 100 o un ajuste como +10 / -10.",
                source,
                reply
            )
            return

        target = max(0, min(100, target))
        if target > 0:
            state["last_volume"] = target / 100.0
        state["volume"] = target / 100.0
        await send_message(
            thread,
            f"🔊 Volumen de música ajustado a {target}%."
            + (" Silencio activado." if target == 0 else " El cambio se aplica en tiempo real."),
            source,
            reply
        )
        return

    if command in ("!pausa", "!pausar", "!reanudar", "!continuar", "!saltar", "!cola", "!dj"):
        subcommand = args[0].lower() if args else ""
        if command == "!dj" and subcommand in (
            "pausa", "pausar", "reanudar", "continuar", "saltar", "skip",
            "cola", "queue", "actual", "añadir", "agregar"
        ):
            action = subcommand
        elif command != "!dj":
            action = {
                "!pausa": "pausa", "!pausar": "pausa",
                "!reanudar": "reanudar", "!continuar": "reanudar",
                "!saltar": "saltar", "!cola": "cola"
            }[command]
        else:
            action = ""

        if action:
            state = DJ_ROOMS.get(thread)
            if not state:
                await send_message(thread, "Katherine no está conectada a una llamada. Usa !join o !dj primero.", source, reply)
                return
            if action in ("pausa", "pausar"):
                state["pause_event"].clear()
                await send_message(thread, "⏸️ Música pausada.", source, reply)
            elif action in ("reanudar", "continuar"):
                state["pause_event"].set()
                await send_message(thread, "▶️ Reproducción reanudada.", source, reply)
            elif action in ("saltar", "skip"):
                if not state.get("task") or state["task"].done():
                    await send_message(thread, "No hay una canción reproduciéndose.", source, reply)
                else:
                    state["skip_event"].set()
                    await send_message(thread, "⏭️ Saltando a la siguiente canción…", source, reply)
            elif action in ("cola", "queue"):
                lines = [
                    f"🎧 Reproduciendo: {state.get('filename') or 'Nada'}",
                    f"Estado: {'Pausada' if not state['pause_event'].is_set() else 'Reproduciendo'}",
                    f"Volumen: {round(float(state.get('volume', 0.75))*100)}%"
                ]
                for index, item in enumerate(list(state.get("queue", []))[:15], 1):
                    lines.append(f"{index}. {item.get('title') or Path(item['path']).name}")
                if not state.get("queue"):
                    lines.append("La cola está vacía.")
                await send_message(thread, "\n".join(lines), source, reply)
            elif action == "actual":
                await send_message(
                    thread,
                    f"🎧 Ahora suena: {state.get('filename') or 'Sin pista'}\n"
                    f"En cola: {len(state.get('queue', []))}\n"
                    f"Volumen: {round(float(state.get('volume', 0.75))*100)}%",
                    source, reply
                )
            elif action in ("añadir", "agregar"):
                name = " ".join(args[1:]).strip()
                path = resolve_dj_track(name)
                if not path:
                    await send_message(thread, "Indica un archivo local válido. Ejemplo: !dj añadir tema.mp3", source, reply)
                else:
                    ok, result = await start_dj_room(thread, path, False, enqueue=True)
                    await send_message(thread, f"🎶 {result}" if ok else f"DJ: {result}", source, reply)
            return

    if command == "!tts":
        state = DJ_ROOMS.get(thread)
        if not state:
            await send_message(thread, "Conecta primero a una llamada con !join o !leer para configurar la lectura.", source, reply)
            return
        if not args or args[0].lower() in ("ajustes", "estado", "config"):
            await send_message(
                thread,
                "🔊 AJUSTES DE LECTURA\n"
                f"Voz: {state.get('tts_voice', TTS_VOICE)}\n"
                f"Velocidad: {state.get('tts_rate', '+0%')}\n"
                f"Volumen de lectura: {round(float(state.get('tts_volume', 0.75))*100)}%\n"
                f"Leer enlaces: {'sí' if state.get('tts_read_links') else 'no'}\n"
                f"Leer menciones: {'sí' if state.get('tts_read_mentions') else 'no'}\n"
                f"En cola: {state['speech_queue'].qsize()}\n"
                "Usos: !tts voz <nombre>, !tts velocidad <-50 a 100>, "
                "!tts volumen <0-100/+10/-10/mute/restaurar>, "
                "!tts enlaces on/off, !tts menciones on/off, !tts voces.",
                source, reply
            )
            return
        setting = args[0].lower()
        if setting in ("volumen", "volume"):
            current = int(round(float(state.get("tts_volume", 0.75)) * 100))
            last = int(round(float(state.get("last_tts_volume", 0.75)) * 100))
            if len(args) < 2:
                await send_message(
                    thread,
                    f"🔊 Volumen de lectura actual: {current}%\n"
                    "Usa !tts volumen 0-100, +10, -10, subir, bajar, mute o restaurar.",
                    source, reply
                )
                return
            try:
                target = calculate_volume_target(args[1:], current, last)
            except (TypeError, ValueError):
                await send_message(
                    thread,
                    "Volumen de lectura inválido. Usa 0-100, +10, -10, subir, bajar, mute o restaurar.",
                    source, reply
                )
                return
            if target is None:
                await send_message(thread, f"Volumen de lectura actual: {current}%.", source, reply)
                return
            target = update_tts_volume(thread, state, target)
            await send_message(
                thread,
                f"🔊 Volumen de lectura ajustado a {target}%."
                + (" Silencio de lectura activado." if target == 0 else " Se aplica en tiempo real."),
                source, reply
            )
            return
        if setting == "voces":
            await send_message(thread, "Voces disponibles: " + ", ".join(TTS_VOICE_OPTIONS.keys()), source, reply)
            return
        if len(args) < 2:
            await send_message(
                thread,
                "Uso: !tts voz <nombre> | velocidad <porcentaje> | volumen <0-100> | enlaces on/off | menciones on/off",
                source, reply
            )
            return
        value = " ".join(args[1:]).strip().lower()
        if setting == "voz":
            voice = TTS_VOICE_OPTIONS.get(value)
            if not voice:
                await send_message(thread, "Voz desconocida. Usa !tts voces para ver opciones.", source, reply)
                return
            state["tts_voice"] = voice
            set_chat_setting(thread, "tts_voice", voice)
            answer = f"Voz de lectura cambiada a {value}."
        elif setting in ("velocidad", "rate"):
            try:
                rate_value = int(value.replace("%", "").replace("+", ""))
                if not -50 <= rate_value <= 100:
                    raise ValueError("rango")
            except ValueError:
                await send_message(thread, "La velocidad debe estar entre -50% y +100%.", source, reply)
                return
            state["tts_rate"] = f"{rate_value:+d}%"
            set_chat_setting(thread, "tts_rate", state["tts_rate"])
            answer = f"Velocidad de lectura ajustada a {state['tts_rate']}."
        elif setting in ("enlaces", "links", "menciones", "mentions"):
            enabled = value in ("on", "si", "sí", "true", "1", "activar")
            allowed = ("on", "off", "si", "sí", "no", "true", "false", "1", "0", "activar", "desactivar")
            if value not in allowed:
                await send_message(thread, "Usa on u off.", source, reply)
                return
            key = "tts_read_links" if setting in ("enlaces", "links") else "tts_read_mentions"
            state[key] = enabled
            set_chat_setting(thread, key, "1" if enabled else "0")
            answer = f"Lectura de {'enlaces' if key == 'tts_read_links' else 'menciones'} {'activada' if enabled else 'desactivada'}."
        else:
            await send_message(thread, "Ajuste desconocido. Usa !tts ajustes.", source, reply)
            return
        await send_message(thread, f"✅ {answer}", source, reply)
        return

    if command == "!playlist":
        if not args:
            await send_message(
                thread,
                "PLAYLISTS\n!playlist crear <nombre>\n!playlist agregar <nombre> | <archivo local>\n"
                "!playlist ver <nombre>\n!playlist reproducir <nombre>\n!playlist borrar <nombre>\n!playlist lista",
                source, reply
            )
            return
        sub, _, rest = query.partition(" ")
        sub = sub.lower()
        if sub == "crear":
            ok = create_dj_playlist(thread, rest, raw_uid)
            await send_message(thread, "Playlist creada." if ok else "No se pudo crear. El nombre debe tener 2-40 caracteres y no repetirse.", source, reply)
        elif sub in ("lista", "listar"):
            playlists = list_dj_playlists(thread)
            content = "\n".join(f"• {p['name']} ({p['track_count']} pistas)" for p in playlists) or "No hay playlists."
            await send_message(thread, content, source, reply)
        elif sub in ("agregar", "añadir"):
            playlist_name, sep, track_name = rest.partition("|")
            track_name = track_name.strip()
            path = resolve_dj_track(track_name)
            if not sep or not path:
                await send_message(thread, "Uso: !playlist agregar <nombre> | <archivo existente en music/>.", source, reply)
            elif add_dj_playlist_track(thread, playlist_name, track_name):
                await send_message(thread, f"«{track_name}» añadida a «{playlist_name.strip()}».", source, reply)
            else:
                await send_message(thread, "No existe esa playlist. Créala primero.", source, reply)
        elif sub == "ver":
            playlist, tracks = get_dj_playlist(thread, rest)
            if not playlist:
                await send_message(thread, "No encontré esa playlist.", source, reply)
            else:
                listing = "\n".join(f"{i}. {track['title']}" for i, track in enumerate(tracks, 1))
                await send_message(thread, f"PLAYLIST · {playlist['name']}\n" + (listing or "Sin canciones."), source, reply)
        elif sub in ("borrar", "eliminar"):
            await send_message(thread, "Playlist eliminada." if delete_dj_playlist(thread, rest) else "No encontré esa playlist.", source, reply)
        elif sub in ("reproducir", "play"):
            playlist, tracks = get_dj_playlist(thread, rest)
            if not playlist or not tracks:
                await send_message(thread, "La playlist no existe o no contiene canciones.", source, reply)
                return
            valid_tracks = []
            for item in tracks:
                path = resolve_dj_track(item["source"])
                if path:
                    valid_tracks.append(path)
            if not valid_tracks:
                await send_message(thread, "No quedan archivos válidos de esa playlist en music/.", source, reply)
                return
            ok, message = await start_dj_room(thread, valid_tracks[0], False)
            added = 0
            if ok:
                for path in valid_tracks[1:]:
                    queued_ok, _ = await start_dj_room(thread, path, False, enqueue=True)
                    if queued_ok:
                        added += 1
            await send_message(
                thread,
                f"▶️ Playlist «{playlist['name']}» iniciada: 1 canción y {added} en cola."
                if ok else f"DJ: {message}",
                source, reply
            )
        else:
            await send_message(thread, "Subcomando desconocido. Usa !playlist.", source, reply)
        return

    if command in ("!advertir", "!advertencias", "!quitaradvertencia", "!antispam"):
        if not is_admin_uid(admin_uid):
            await send_message(thread, "No tienes permisos de administración. Configura ADMIN_UIDS en Render con tu UID.", source, reply)
            return
        if command == "!antispam":
            current = anti_spam_configuration(thread)
            if not args or args[0].lower() in ("estado", "config"):
                await send_message(
                    thread,
                    f"🛡️ Antispam: {'activado' if current['enabled'] else 'desactivado'}\n"
                    f"Límite: {current['max_messages']} mensajes / {current['window_seconds']} s\n"
                    f"Repeticiones: {current['repeat_limit']}\n"
                    f"Máximo de enlaces: {current['max_links']} · menciones: {current['max_mentions']}\n"
                    "Usa !antispam on/off, limite <3-20>, ventana <3-60>, "
                    "repeticiones <2-10>, enlaces <2-12>, menciones <3-20>.",
                    source, reply
                )
                return
            sub = args[0].lower()
            if sub in ("on", "activar", "off", "desactivar"):
                enabled = sub in ("on", "activar")
                set_chat_setting(thread, "antispam_enabled", "1" if enabled else "0")
                log_admin_action(thread, raw_uid, nickname, "antispam", "activado" if enabled else "desactivado")
                answer = f"Antispam {'activado' if enabled else 'desactivado'}."
            elif sub in ("limite", "ventana", "repeticiones", "enlaces", "menciones") and len(args) > 1:
                try:
                    number = int(args[1])
                    bounds = {
                        "limite": (3, 20), "ventana": (3, 60),
                        "repeticiones": (2, 10), "enlaces": (2, 12),
                        "menciones": (3, 20)
                    }
                    low, high = bounds[sub]
                    if not low <= number <= high:
                        raise ValueError("rango")
                    key = {
                        "limite": "antispam_max_messages",
                        "ventana": "antispam_window_seconds",
                        "repeticiones": "antispam_repeat_limit",
                        "enlaces": "antispam_max_links",
                        "menciones": "antispam_max_mentions"
                    }[sub]
                    set_chat_setting(thread, key, str(number))
                    log_admin_action(thread, raw_uid, nickname, "antispam_config", f"{sub}={number}")
                    answer = f"Antispam: {sub} ajustado a {number}."
                except ValueError:
                    answer = "Valor fuera de rango."
            else:
                answer = "Uso: !antispam on/off, limite <3-20>, ventana <3-60>, repeticiones <2-10>, enlaces <2-12>, menciones <3-20>."
            await send_message(thread, answer, source, reply)
            return

        if command == "!quitaradvertencia":
            try:
                warning_id = int(args[0])
            except (IndexError, ValueError):
                await send_message(thread, "Uso: !quitaradvertencia <id>.", source, reply)
                return
            removed = revoke_moderation_warning(thread, warning_id, raw_uid)
            if removed:
                log_admin_action(thread, raw_uid, nickname, "advertencia_retirada", f"ID #{warning_id}")
            await send_message(thread, "Advertencia retirada." if removed else "No encontré una advertencia activa con ese ID.", source, reply)
            return

        target_uid, target_name = target_from_message(msg, args, scope_key)
        if command == "!advertencias" and not target_uid:
            target_uid, target_name = uid, nickname
        if not target_uid and target_name:
            found = find_user_by_nickname(target_name, scope_key)
            if found:
                target_uid, target_name = found["uid"], found["nickname"]
        if not target_uid:
            await send_message(thread, "Indica o menciona a un usuario. Ejemplo: !advertir @usuario motivo.", source, reply)
            return

        if command == "!advertir":
            reason = re.sub(r"@[^\s,;]+", "", query, count=1).strip()
            warning_id = add_moderation_warning(
                thread, target_uid, target_name or "Usuario",
                raw_uid, nickname, reason or "Sin motivo indicado", automatic=False
            )
            log_admin_action(thread, raw_uid, nickname, "advertencia_creada", f"ID #{warning_id} · {target_name}: {reason or 'Sin motivo indicado'}")
            active_count = sum(
                1 for item in get_moderation_warnings(thread, target_uid, 30)
                if item["active"]
            )
            await send_message(thread, f"⚠️ Advertencia #{warning_id} registrada para {target_name}. Total activas: {active_count}.", source, reply)
        else:
            warnings = get_moderation_warnings(thread, target_uid, 20)
            active = [item for item in warnings if item["active"]]
            lines = [f"⚠️ Advertencias de {target_name}: {len(active)} activas"]
            lines.extend(
                f"#{item['id']} · {datetime.fromtimestamp(item['created_at']).strftime('%d/%m %H:%M')} · {item['reason']}"
                for item in warnings
            )
            await send_message(thread, "\n".join(lines), source, reply)
        return

    if command in ("!dj", "!reproducir"):
        dj_source = source
        if not COMMUNITY_TOKEN:
            await send_message(
                thread,
                "El DJ necesita COMMUNITY_BOT_TOKEN configurado en las variables de Render.",
                dj_source,
                reply
            )
            return
        if pyav is None or livekit_rtc is None or np is None:
            await send_message(
                thread,
                "El reproductor DJ no está instalado correctamente en Render.",
                dj_source,
                reply
            )
            return

        if args and args[0].lower() in ("fuentes", "fuente", "sources"):
            jamendo_status = (
                "configurado" if JAMENDO_CLIENT_ID else
                "pendiente: añade JAMENDO_CLIENT_ID en Render"
            )
            await send_message(
                thread,
                "FUENTES MUSICALES DE KATHERINE\n"
                "• Internet Archive: búsqueda y validación de licencia automática.\n"
                "• Wikimedia Commons: audio libre con licencia verificable.\n"
                f"• Jamendo: {jamendo_status}; requiere Client ID gratuito.\n"
                "Las búsquedas combinan los catálogos disponibles, validan las pistas antes de reproducirlas y pueden usar recomendaciones por ambiente: estudiar, trabajar, relajarse, dormir, jugar, combate, triste o feliz.",
                dj_source,
                reply
            )
            return

        if not args or args[0].lower() in ("lista", "list"):
            tracks = list_dj_tracks()
            await send_message(
                thread,
                "KATHERINE DJ\n"
                + ("Archivos locales:\n" + "\n".join(f"• {name}" for name in tracks)
                   if tracks else "No hay audios locales.")
                + "\n\nPara buscar música pública: !dj buscar <tema>\n"
                "Ejemplos: !dj buscar jazz, !dj buscar piano, !dj buscar ambient.\n"
                "También puedes reproducir archivos locales con !dj archivo.mp3.",
                dj_source,
                reply
            )
            return

        if args[0].lower() in ("favorito", "favoritos"):
            sub = args[1].lower() if len(args) > 1 else "lista"
            if sub in ("lista", "listar"):
                favorites = list_dj_favorites(thread, raw_uid)
                if not favorites:
                    await send_message(thread, "Todavía no tienes canciones favoritas en esta sala.", dj_source, reply)
                    return
                listing = "\n".join(
                    f"{item['id']}. {item['title']} — {item['creator']} ({item['source']})"
                    for item in favorites
                )
                await send_message(thread, "DJ · FAVORITOS\n" + listing, dj_source, reply)
                return
            if sub in ("guardar", "agregar", "añadir"):
                try:
                    index = int(args[2]) - 1
                    track = (DJ_SEARCH_RESULTS.get(thread) or {}).get("tracks", [])[index]
                except (ValueError, IndexError):
                    await send_message(thread, "Primero busca música con !dj buscar y elige un número válido.", dj_source, reply)
                    return
                saved = save_dj_favorite(thread, raw_uid, track)
                await send_message(thread, "⭐ Canción guardada en favoritos." if saved else "No se pudo guardar; quizá ya existe.", dj_source, reply)
                return
            if sub in ("quitar", "eliminar", "borrar") and len(args) > 2:
                try:
                    favorite_id = int(args[2])
                except ValueError:
                    favorite_id = -1
                removed = delete_dj_favorite(thread, raw_uid, favorite_id)
                await send_message(thread, "Favorito eliminado." if removed else "No encontré ese favorito.", dj_source, reply)
                return
            if sub in ("reproducir", "play") and len(args) > 2:
                try:
                    favorite_id = int(args[2])
                except ValueError:
                    favorite_id = -1
                favorite = next((item for item in list_dj_favorites(thread, raw_uid, 50) if item["id"] == favorite_id), None)
                if not favorite:
                    await send_message(thread, "No encontré ese favorito.", dj_source, reply)
                    return
                metadata = {
                    "title": favorite["title"], "creator": favorite["creator"],
                    "license": favorite["license"], "source": favorite["source"],
                    "extension": favorite["extension"], "url": favorite["url"]
                }
                ok, message = await start_dj_room(thread, favorite["url"], False, track_metadata=metadata)
                await send_message(thread, f"🎧 {message}" if ok else f"DJ: {message}", dj_source, reply)
                return
            await send_message(thread, "Usa !dj favorito lista, guardar <número>, reproducir <id> o quitar <id>.", dj_source, reply)
            return

        if args[0].lower() in ("ambiente", "mood", "recomendar"):
            mood = " ".join(args[1:]).strip().casefold()
            search_term = resolve_music_mood(mood)
            args = ["buscar", search_term]
        if args[0].lower() in ("buscar", "search"):
            search_term = " ".join(args[1:]).strip()
            if not search_term:
                await send_message(
                    thread,
                    "Uso: !dj buscar <tema o género>. Ejemplos: jazz, piano, ambient, clásica.",
                    dj_source,
                    reply
                )
                return
            try:
                results = await search_music_catalog(search_term, limit=5)
            except Exception as exc:
                logging.exception("[DJ] Búsqueda musical multifuente fallida")
                await send_message(
                    thread,
                    f"No pude consultar el catálogo musical ahora mismo ({type(exc).__name__}).",
                    dj_source,
                    reply
                )
                return

            if not results:
                await send_message(
                    thread,
                    "No encontré pistas reproducibles con licencias permitidas para esa búsqueda. "
                    "Prueba con otro género o palabra.",
                    dj_source,
                    reply
                )
                return

            DJ_SEARCH_RESULTS[thread] = {
                "tracks": results,
                "expires": time.time() + 900
            }
            for old_thread, saved in list(DJ_SEARCH_RESULTS.items()):
                if saved.get("expires", 0) <= time.time():
                    DJ_SEARCH_RESULTS.pop(old_thread, None)

            buttons = register_buttons(
                dj_source,
                thread,
                "dj_select",
                [
                    {
                        "label": f"{index + 1}. {track['title']}"[:40],
                        "value": str(index)
                    }
                    for index, track in enumerate(results)
                ],
                ttl=900
            )
            listing = "\n".join(
                f"{index + 1}. {track['title']} — {track['creator']} "
                f"({track['license']} · {track.get('source', 'catálogo')})"
                for index, track in enumerate(results)
            )
            await send_message(
                thread,
                f"CATÁLOGO MUSICAL · BÚSQUEDA MULTIFUENTE\nBúsqueda: {search_term}\n\n"
                f"{listing}\n\nSelecciona una pista para reproducirla en la sala:",
                dj_source,
                reply,
                buttons
            )
            return

        if args[0].lower() in ("detener", "stop", "parar"):
            if thread not in DJ_ROOMS:
                await send_message(
                    thread,
                    "Katherine no está reproduciendo audio en esta sala.",
                    dj_source,
                    reply
                )
                return
            await stop_dj_room(thread, request_leave=True)
            await send_message(
                thread,
                "⏹️ Reproducción detenida. Katherine salió de la sala.",
                dj_source,
                reply
            )
            return

        track_path = resolve_dj_track(args[0])
        if not track_path:
            await send_message(
                thread,
                "No encuentro ese archivo en music/. También puedes buscar audio público "
                "con !dj buscar <tema>.",
                dj_source,
                reply
            )
            return

        looping = any(
            value.lower() in ("bucle", "loop", "repetir")
            for value in args[1:]
        )
        room_id = thread
        if len(args) > 1 and args[-1].isdigit():
            room_id = args[-1]

        ok, message = await start_dj_room(room_id, track_path, looping)
        await send_message(
            thread,
            f"🎧 {message}" if ok else f"DJ: {message}",
            dj_source,
            reply
        )
        return

    if command in ("!djdetener", "!djstop", "!salirsala"):
        if thread not in DJ_ROOMS:
            await send_message(thread, "Katherine no está conectada a una llamada en esta sala.", source, reply)
            return
        await stop_dj_room(thread, request_leave=True)
        await send_message(thread, "⏹️ Katherine salió de la llamada y detuvo cualquier audio.", source, reply)
        return

    if command == "!salas":
        result = await api("GET", "/live-rooms", "community")

        rooms = (
            result["data"].get("roomList", [])
            if result["ok"]
            else []
        )

        answer = "SALAS EN VIVO\n\n" + (
            "\n".join(
                f"• {r.get('title') or 'Sala'} — "
                f"{r.get('participantCount', 0)} participantes "
                f"| {r.get('threadId')}"
                for r in rooms
            )
            or "No hay salas disponibles o falta el token de comunidad."
        )

        await send_message(thread, answer, source, reply)
        return

    # Respuesta explícita para comandos inexistentes: nunca dejar al usuario
    # sin respuesta por haber escrito un nombre no registrado.
    await send_message(
        thread,
        f"No reconozco el comando {command}. Usa !ayuda para consultar los comandos disponibles.",
        source,
        reply
    )


# ---------------------------------------------------------------------------
# INTERACCIONES DE BOTONES: ENCUESTAS, MENÚS, JUEGOS Y ACCIONES
# ---------------------------------------------------------------------------
async def start_dj_search_with_fallback(
    thread_id: str, tracks: list[dict], selected_index: int = 0
) -> tuple[bool, str, dict | None, int | None]:
    """Try the selected track and later search results if a file cannot be validated."""
    if not tracks:
        return False, "No hay pistas disponibles.", None, None
    start = max(0, min(len(tracks) - 1, int(selected_index)))
    last_error = "No pude preparar ninguna pista."
    candidate_order = list(range(start, len(tracks))) + list(range(0, start))
    for index in candidate_order:
        track = tracks[index]
        ok, message = await start_dj_room(
            thread_id,
            track["url"],
            looping=False,
            track_metadata=track
        )
        if ok:
            if index != start:
                metrics["dj_fallback_tracks_started"] += 1
                logging.info(
                    "[DJ] Pista alternativa %s iniciada tras fallo de validación en sala %s",
                    index + 1, thread_id
                )
                message = f"La pista seleccionada no se pudo validar; inicié «{track.get('title', 'otra pista')}» como alternativa."
            return True, message, track, index
        last_error = message
        recoverable = is_recoverable_dj_failure(message)
        if not recoverable:
            break
        metrics["dj_fallback_tracks_skipped"] += 1
    return False, last_error, None, None


async def handle_interaction(event: dict, source: str):
    button_id = str(event.get("buttonId") or "")
    thread_id = str(event.get("threadId") or "")
    user_data = event.get("user") or {}
    raw_user_uid = str(user_data.get("uid") or "unknown")
    user_name = str(user_data.get("nickname") or "Viajero")
    scoped_user_uid = scoped_uid(f"{source}:{thread_id}", raw_user_uid)

    # Las encuestas permiten cambiar el voto hasta el cierre.
    match = re.fullmatch(r"p(\d+)_(\d+)", button_id)
    if match:
        poll = polls.get(poll_button_map.get(match.group(1), ""))
        if not poll:
            return
        if (
            poll.get("source") != source
            or str(event.get("threadId") or "") != poll.get("thread")
        ):
            return
        if poll.get("closed") or float(poll.get("closes_at", 0)) <= time.time():
            poll["closed"] = True
            await remove_pressed_buttons(event, source, remove=True)
            await send_message(
                thread_id,
                f"La encuesta {poll.get('code')} ya está cerrada.\n\n" + format_poll_results(poll),
                source
            )
            return

        index = int(match.group(2))
        if index >= len(poll["options"]):
            return
        voter_key = (
            hashlib.sha256(f"{poll.get('code')}:{raw_user_uid}".encode("utf-8")).hexdigest()
            if poll.get("anonymous") else raw_user_uid
        )
        poll["votes"][voter_key] = index
        await send_message(thread_id, format_poll_results(poll), source)
        return

    action = BUTTON_ACTIONS.get(button_id)
    if not action:
        return
    if (
        action.get("source") != source
        or action.get("thread") != thread_id
        or action.get("expires", 0) <= time.time()
    ):
        BUTTON_ACTIONS.pop(button_id, None)
        return

    owner_uid = action.get("owner_uid")
    if owner_uid and owner_uid != scoped_user_uid:
        await send_message(
            thread_id,
            "Este botón pertenece a la interacción de otra persona.",
            source
        )
        return

    action_type = action.get("type")
    payload = action.get("payload") or {}
    choice = payload.get("value")



    if action_type == "community_event":
        event = COMMUNITY_EVENTS.get(thread_id)
        payload_event_id = str(payload.get("event_id") or "")
        if not event or event.get("id") != payload_event_id:
            await send_message(
                thread_id, "Este evento ya terminó o caducó. Puedes crear otro con !evento crear <título>.",
                source
            )
            return
        joined, count = toggle_community_participant(
            event, raw_user_uid, user_name, str(choice) == "join"
        )
        messages = {
            "joined": f"Te uniste a «{event['title']}». Participantes: {count}/{event['capacity']}.",
            "already_joined": f"Ya formas parte de «{event['title']}». Participantes: {count}/{event['capacity']}.",
            "full": f"«{event['title']}» alcanzó su capacidad máxima.",
            "left": f"Saliste de «{event['title']}». Participantes: {count}/{event['capacity']}.",
            "not_joined": "No estabas inscrito en este evento.",
            "expired": "El evento ya caducó.",
            "closed": "El evento fue cerrado.",
            "invalid": "No pude identificar tu cuenta en esta interacción.",
        }
        if joined == "joined":
            metrics["community_event_joins"] += 1
        elif joined == "left":
            metrics["community_event_leaves"] += 1
        await send_message(thread_id, messages.get(joined, "No pude actualizar tu participación."), source)
        return

    if action_type == "admin_panel":
        BUTTON_ACTIONS.pop(button_id, None)
        await remove_pressed_buttons(event, source, remove=[button_id])
        if not is_admin_uid(raw_user_uid):
            await send_message(thread_id, "No tienes permisos de administración.", source)
            return
        section = str(choice or "")
        log_admin_action(thread_id, raw_user_uid, user_name, "panel_consulta", section)
        await send_admin_panel(thread_id, source, raw_user_uid, section)
        return

    if action_type == "admin_antispam":
        BUTTON_ACTIONS.pop(button_id, None)
        await remove_pressed_buttons(event, source, remove=[button_id])
        if not is_admin_uid(raw_user_uid):
            await send_message(thread_id, "No tienes permisos de administración.", source)
            return
        enabled = str(choice or "") == "on"
        set_chat_setting(thread_id, "antispam_enabled", "1" if enabled else "0")
        log_admin_action(thread_id, raw_user_uid, user_name, "antispam", "activado" if enabled else "desactivado")
        await send_message(
            thread_id,
            f"Antispam {'activado' if enabled else 'desactivado'} desde el panel.",
            source
        )
        await send_admin_panel(thread_id, source, raw_user_uid, "antispam")
        return

    if action_type == "menu_category":
        BUTTON_ACTIONS.pop(button_id, None)
        await remove_pressed_buttons(event, source, remove=[button_id])
        await send_interactive_menu(
            thread_id,
            source,
            payload.get("raw_uid", raw_user_uid),
            payload.get("nickname", user_name),
            category=str(choice)
        )
        return

    if action_type == "run_command":
        BUTTON_ACTIONS.pop(button_id, None)
        await remove_pressed_buttons(event, source, remove=[button_id])
        command_name = str(choice or "").strip().lower()
        if not re.fullmatch(r"[a-záéíóúñ0-9_]+", command_name):
            return
        fake_event = {
            "threadId": thread_id,
            "message": {
                "content": PREFIX + command_name,
                "author": {
                    "uid": payload.get("raw_uid", raw_user_uid),
                    "nickname": payload.get("nickname", user_name)
                }
            }
        }
        await handle_command(fake_event, source)
        return

    if action_type == "social_action":
        BUTTON_ACTIONS.pop(button_id, None)
        await remove_pressed_buttons(event, source, remove=[button_id])
        target_name = str(payload.get("target_name") or "esa persona")
        actor_name = str(payload.get("actor_name") or user_name)
        actor_uid = str(payload.get("actor_uid") or scoped_user_uid)
        target_uid = payload.get("target_uid")
        action_key = str(choice or "")
        phrases = SOCIAL.get(action_key)
        if not phrases:
            return
        affinity = None
        if target_uid and target_uid != actor_uid:
            affinity = add_affinity(actor_uid, str(target_uid))
        answer = f"{actor_name} {random.choice(phrases)} {target_name}."
        if affinity is not None:
            answer += f"\nAfinidad: {affinity}"
        await send_message(thread_id, answer, source)
        return

    if action_type == "trivia_answer":
        game_key = str(payload.get("game_key") or "")
        game = trivia.get(game_key)
        if (
            not game
            or game.get("expires", 0) <= time.time()
            or game.get("game_id") != payload.get("game_id")
        ):
            BUTTON_ACTIONS.pop(button_id, None)
            await remove_pressed_buttons(event, source, remove=True)
            await send_message(thread_id, "Esta trivia ya terminó. Usa !trivia para iniciar otra.", source)
            return

        try:
            selected = int(choice)
        except (TypeError, ValueError):
            return

        if selected == game["q"][2]:
            owner = game.get("owner_uid")
            if owner:
                add_points(owner, 10)
            trivia.pop(game_key, None)
            for key, value in list(BUTTON_ACTIONS.items()):
                data = value.get("payload") or {}
                if data.get("game_key") == game_key and data.get("game_id") == game.get("game_id"):
                    BUTTON_ACTIONS.pop(key, None)
            await remove_pressed_buttons(event, source, remove=True)
            await send_message(
                thread_id,
                f"¡Correcto, {user_name}! Ganaste 10 puntos.",
                source
            )
        else:
            game["tries"] += 1
            BUTTON_ACTIONS.pop(button_id, None)
            if game["tries"] >= 3:
                answer = game["q"][1][game["q"][2]]
                trivia.pop(game_key, None)
                for key, value in list(BUTTON_ACTIONS.items()):
                    data = value.get("payload") or {}
                    if data.get("game_key") == game_key and data.get("game_id") == game.get("game_id"):
                        BUTTON_ACTIONS.pop(key, None)
                await remove_pressed_buttons(event, source, remove=True)
                await send_message(
                    thread_id,
                    f"No era esa. Se acabaron los intentos; la respuesta era {answer}.",
                    source
                )
            else:
                await remove_pressed_buttons(event, source, remove=[button_id])
                await send_message(
                    thread_id,
                    f"No es correcto. Te quedan {3 - game['tries']} intentos.",
                    source
                )
        return

    if action_type == "dj_select":
        BUTTON_ACTIONS.pop(button_id, None)
        # La interacción llega por el bot comunitario, pero el mensaje con
        # botones pertenece al bot developer. Usamos developer para editarlo y responder.
        dj_source = "developer"
        await remove_pressed_buttons(event, dj_source, remove=True)

        saved = DJ_SEARCH_RESULTS.get(thread_id) or {}
        if saved.get("expires", 0) <= time.time():
            DJ_SEARCH_RESULTS.pop(thread_id, None)
            await send_message(
                thread_id,
                "Los resultados de búsqueda caducaron. Usa !dj buscar <tema> otra vez.",
                dj_source
            )
            return
        try:
            selected_index = int(choice)
            track = saved.get("tracks", [])[selected_index]
        except (TypeError, ValueError, IndexError):
            await send_message(thread_id, "Esa selección ya no está disponible.", dj_source)
            return

        ok, message, selected_track, selected_index = await start_dj_search_with_fallback(
            thread_id, saved.get("tracks", []), selected_index
        )
        if ok and selected_track:
            track = selected_track
            credit = (
                f"🎧 Ahora suena: {track['title']} — {track['creator']}\n"
                f"Fuente: {track.get('source', 'catálogo musical')}\n"
                f"Licencia: {track['license']}\n"
                f"Créditos: {track.get('item_url', 'sin ficha pública')}\n"
                f"Licencia: {track.get('license_url', 'ver ficha de origen')}"
            )
            await send_message(thread_id, credit, dj_source)
        else:
            await send_message(thread_id, f"DJ: {message}", dj_source)
        return

    if action_type == "choose_option":
        BUTTON_ACTIONS.pop(button_id, None)
        await remove_pressed_buttons(event, source, remove=[button_id])
        await send_message(
            thread_id,
            f"🗳️ {user_name} eligió: {choice}",
            source
        )
        return

    if action_type == "duel_response":
        BUTTON_ACTIONS.pop(button_id, None)
        await remove_pressed_buttons(event, source, remove=True)
        challenger_name = str(payload.get("challenger_name") or "El retador")
        challenger_uid = str(payload.get("challenger_uid") or "")
        opponent_uid = scoped_user_uid
        target_name = str(payload.get("target_name") or user_name)
        if choice == "decline":
            await send_message(
                thread_id,
                f"🛡️ {user_name} rechazó el duelo de {challenger_name}.",
                source
            )
            return

        winner_is_challenger = random.choice((True, False))
        if winner_is_challenger:
            winner_name = challenger_name
            winner_uid = challenger_uid
        else:
            winner_name = user_name
            winner_uid = opponent_uid

        add_points(winner_uid, 10)
        await send_message(
            thread_id,
            f"⚔️ Duelo entre {challenger_name} y {target_name}. "
            f"¡{winner_name} ganó y recibió 10 puntos!",
            source
        )
        return

# ---------------------------------------------------------------------------
# EVENTOS Y WEBHOOKS
# ---------------------------------------------------------------------------
async def dispatch(event: dict, source: str):
    kind = event.get("type")

    if kind == "message":
        # Ambos bots reciben el mismo evento de chat. Solo el bot developer
        # procesa texto, comandos y TTS; el community queda reservado para voz.
        if source != "developer":
            return
        blocked, spam_info = check_antispam_message(event, source)
        if blocked:
            if spam_info:
                await send_message(
                    str(event.get("threadId") or ""),
                    f"🛡️ {spam_info['nickname']}, baja la frecuencia de mensajes. "
                    f"Se registró la advertencia #{spam_info['warning_id']}.",
                    "developer"
                )
            return
        message_data = event.get("message") or {}
        author_data = message_data.get("author") or {}
        incoming_text = str(message_data.get("content") or "").strip()
        author_is_bot = is_bot_author(
            author_data, (BOT_IDENTITIES.get(source) or {}).get("uid")
        )
        AI_CORE.record_chat_message(
            str(event.get("threadId") or ""),
            str(author_data.get("nickname") or author_data.get("name") or "Usuario"),
            incoming_text,
            is_bot=author_is_bot,
        )
        await enqueue_message_for_speech(event, source)

        # NEXUS conserva el enrutamiento determinista cuando IA no está configurada.
        # existentes. No inventa acciones ni llama endpoints nuevos.
        if incoming_text and not incoming_text.startswith((PREFIX, "!", "-")):
            routed_command = interpret_natural_request(incoming_text)
            if routed_command:
                routed_event = dict(event)
                routed_message = dict(message_data)
                if AI_CORE.is_enabled:
                    routed_event["_nexus_fallback_command"] = routed_command
                    routed_message["content"] = f"{PREFIX}asistente {incoming_text}"
                    logging.info("[AI] Solicitud explícita enviada al núcleo conversacional")
                else:
                    routed_message["content"] = f"{PREFIX}{routed_command}"
                    logging.info("[NEXUS] Solicitud dirigida interpretada como %s", routed_command.split()[0])
                routed_event["message"] = routed_message
                await handle_command(routed_event, source)
            else:
                # Conversación ordinaria: se mantienen estadísticas de actividad,
                # pero no se activa el asistente ni las respuestas automáticas.
                author = message_data.get("author") or {}
                raw_uid = str(author.get("uid") or author.get("nickname") or "unknown")
                nickname = author.get("nickname") or "Viajero"
                scope_key = f"{source}:{event.get('threadId')}"
                record_user(scoped_uid(scope_key, raw_uid), nickname, count_message=True)
                record_chat_activity(str(event.get("threadId") or ""), scoped_uid(scope_key, raw_uid), nickname)
                metrics["unaddressed_messages_ignored"] += 1
        else:
            # Los comandos clásicos con prefijo siguen funcionando.
            await handle_command(event, source)

    elif kind == "interaction":
        await handle_interaction(event, source)

    elif kind in (
        "room.join",
        "room.speaker",
        "room.leave",
        "room.end",
        "room.ready",
        "room.kicked"
    ):
        thread_id = str(event.get("threadId") or "")
        metrics[f"event_{kind}"] += 1
        if kind == "room.ready":
            ready = DJ_READY_EVENTS.get(thread_id)
            if ready:
                ready.set()
        elif kind in ("room.end", "room.kicked"):
            await stop_dj_room(thread_id, request_leave=False)
        logging.info("[SALA] %s | %s", kind, thread_id)

    elif kind in ("thread.added", "thread.removed", "message.edited", "message.deleted"):
        metrics[f"event_{kind}"] += 1
        logging.info("[EVENTO] %s | %s", kind, event.get("threadId"))

    else:
        logging.info("[EVENTO] Ignorado: %s", kind)


async def safe_dispatch(event: dict, source: str):
    try:
        await dispatch(event, source)
    except Exception:
        logging.exception(
            "Error procesando evento %s en origen %s",
            event.get("type"),
            source
        )


def verify_webhook(raw: bytes, headers, secret: str):
    ts = headers.get("X-Anarchy-Bot-Timestamp", "")
    sig = headers.get("X-Anarchy-Bot-Signature", "")

    if (
        not ts.isdigit()
        or abs(time.time() - int(ts)) > 300
        or not sig.startswith("v1=")
    ):
        return None

    expected = hmac.new(
        secret.encode(),
        ts.encode() + b"." + raw,
        hashlib.sha256
    ).hexdigest()

    received = sig[3:]

    if (
        len(received) != len(expected)
        or not hmac.compare_digest(received, expected)
    ):
        return None

    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


async def webhook(request: Request, source: str | None):
    global LAST_EVENT_AT
    raw = await request.body()
    event = None
    verified_source = source

    # El endpoint raíz acepta webhooks de ambos bots. Esto evita que un
    # webhook comunitario configurado por error en "/" sea validado con
    # WEBHOOK_SECRET en vez de COMMUNITY_WEBHOOK_SECRET.
    candidates = (
        (source,)
        if source in ("developer", "community")
        else ("developer", "community")
    )
    valid_sources = []
    for candidate in candidates:
        secret = COMMUNITY_SECRET if candidate == "community" else WEBHOOK_SECRET
        if not secret:
            continue
        candidate_event = verify_webhook(raw, request.headers, secret)
        if candidate_event is not None:
            valid_sources.append((candidate, candidate_event))

    if not valid_sources:
        configured = {
            "developer": bool(WEBHOOK_SECRET),
            "community": bool(COMMUNITY_SECRET)
        }
        logging.warning(
            "[WEBHOOK] Firma rechazada en %s | secretos configurados: %s",
            request.url.path,
            configured
        )
        if not any(configured.values()):
            raise HTTPException(
                status_code=503,
                detail="Ningún secreto de webhook está configurado."
            )
        raise HTTPException(
            status_code=401,
            detail="Firma inválida para los bots configurados."
        )

    if len(valid_sources) == 1:
        verified_source, event = valid_sources[0]
    else:
        # Si por configuración ambos secretos coinciden, el botId permite
        # identificar el emisor sin confiar en datos no firmados.
        candidate_event = valid_sources[0][1]
        bot_id = str(candidate_event.get("botId") or "")
        matched = next(
            (
                item for item in valid_sources
                if str(
                    (BOT_IDENTITIES.get(item[0]) or {}).get("bot_id") or ""
                ) == bot_id and bot_id
            ),
            None
        )
        verified_source, event = matched or valid_sources[0]

    event_id = str(event.get("eventId") or "")

    if event_id and event_id in seen_event_set:
        return JSONResponse({
            "ok": True,
            "duplicate": True
        })

    metrics["webhook_events"] += 1
    LAST_EVENT_AT = time.time()
    if event.get("type") == "interaction":
        metrics["interactions_received"] += 1

    if event_id:
        if len(seen_events) == seen_events.maxlen:
            old = seen_events.popleft()
            seen_event_set.discard(old)

        seen_events.append(event_id)
        seen_event_set.add(event_id)

    logging.info(
        "[WEBHOOK] Evento %s recibido por %s desde %s",
        event.get("type"),
        verified_source,
        request.url.path
    )

    # Responder rápido a Anarchy; el procesamiento continúa en segundo plano.
    task = asyncio.create_task(safe_dispatch(event, verified_source))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# RUTAS FASTAPI
# ---------------------------------------------------------------------------
@app.post("/")
async def main_webhook(request: Request):
    return await webhook(request, None)


@app.post("/webhook")
async def main_webhook_alias(request: Request):
    return await webhook(request, None)


@app.post("/community")
async def community_webhook(request: Request):
    return await webhook(request, "community")


@app.get("/")
async def health():
    return {
        "bot": "Katherine",
        "status": "online",
        "uptime": int(time.time() - STARTED)
    }


@app.get("/health")
async def health_check():
    return {
        "ok": True,
        "bot": "Katherine"
    }


@app.head("/")
async def health_head():
    return JSONResponse(
        status_code=200,
        content={"ok": True}
    )


# ---------------------------------------------------------------------------
# INICIO
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup():
    global HTTP_CLIENT, CLEANUP_TASK
    HTTP_CLIENT = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=8.0),
        limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
        headers={"User-Agent": "Katherine-AnarchyBot/3.0"}
    )
    init_db()
    cleanup_expired_state()
    CLEANUP_TASK = asyncio.create_task(cleanup_loop())

    logging.info("SQLite activo en %s", DB_PATH)
    DJ_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    logging.info(
        "Katherine DJ: %s | directorio: %s",
        "disponible" if livekit_rtc is not None and pyav is not None and np is not None else "dependencias ausentes",
        DJ_MUSIC_DIR
    )

    if not os.getenv("DATA_DIR"):
        logging.warning(
            "DATA_DIR no está configurado. En Render los datos locales "
            "no sobrevivirán a un redeploy/reinicio."
        )

    logging.info(
        "Katherine Python iniciando en puerto %s",
        PORT
    )
    logging.info("[AI] Credencial %s | modelo=%s", "detectada" if AI_CORE.is_enabled else "no detectada", AI_CORE.model if AI_CORE.is_enabled else "pendiente")
    logging.info(
        "[WEBHOOK] Secretos configurados | developer=%s | community=%s",
        bool(WEBHOOK_SECRET),
        bool(COMMUNITY_SECRET)
    )

    for source in ("developer", "community"):
        if token_for(source):
            me = await api("GET", "/me", source)

            if me["ok"]:
                bot = me["data"].get("bot", {})
                if isinstance(bot, dict):
                    BOT_IDENTITIES[source] = {
                        "bot_id": str(bot.get("botId") or ""),
                        "uid": str(bot.get("uid") or bot.get("id") or bot.get("userId") or ""),
                        "name": str(bot.get("name") or "")
                    }

                logging.info(
                    "[BOT %s] %s | chats: %s",
                    source.upper(),
                    bot.get("name") if isinstance(bot, dict) else "Katherine",
                    me["data"].get("threadIds", [])
                )

            else:
                logging.error(
                    "[BOT %s] HTTP %s: %s",
                    source.upper(),
                    me["status"],
                    me["data"]
                )


@app.on_event("shutdown")
async def shutdown():
    global HTTP_CLIENT, CLEANUP_TASK
    for thread_id in list(DJ_ROOMS):
        await stop_dj_room(thread_id, request_leave=False)
    if CLEANUP_TASK is not None:
        CLEANUP_TASK.cancel()
        try:
            await CLEANUP_TASK
        except asyncio.CancelledError:
            pass
        CLEANUP_TASK = None
    if HTTP_CLIENT is not None:
        await HTTP_CLIENT.aclose()
        HTTP_CLIENT = None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT
    )
