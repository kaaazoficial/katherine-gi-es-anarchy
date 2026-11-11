# Katherine - Bot de Anarchy Apps (Python)
# Requiere Python 3.11+
import os
import re
import json
import time
import hmac
import hashlib
import random
import asyncio
import logging
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections import defaultdict, deque
from typing import Any

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# CONFIGURACION
# En Render: BOT_TOKEN, WEBHOOK_SECRET. Para bot de comunidad:
# COMMUNITY_BOT_TOKEN y COMMUNITY_WEBHOOK_SECRET.
# ---------------------------------------------------------------------------
API = "https://api.anarchyapps.org/bot-api/v1"
GAME_API = "https://genshin-db-api.vercel.app/api/v5"
ENKA_API = "https://enka.network/api"
PORT = int(os.getenv("PORT", "3000"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
COMMUNITY_TOKEN = os.getenv("COMMUNITY_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
COMMUNITY_SECRET = os.getenv("COMMUNITY_WEBHOOK_SECRET", "").strip()
PREFIX = os.getenv("COMMAND_PREFIX", "!").strip() or "!"
if (
    not 1 <= len(PREFIX) <= 3
    or any(char.isalnum() or char.isspace() for char in PREFIX)
):
    logging.warning("COMMAND_PREFIX debe tener entre 1 y 3 símbolos; se usará !")
    PREFIX = "!"
STARTED = time.time()

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
            genshin_uid TEXT NOT NULL DEFAULT '',
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
        db.execute("CREATE INDEX IF NOT EXISTS idx_gifts_target ON gifts(target_uid, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_activity ON users(message_count DESC)")
        db.execute("""CREATE TABLE IF NOT EXISTS wishes (
            uid TEXT PRIMARY KEY,
            pity5 INTEGER NOT NULL DEFAULT 0,
            pity4 INTEGER NOT NULL DEFAULT 0,
            guaranteed INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
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

def get_user(uid: str):
    with db_session() as db:
        row = db.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else None

def find_user_by_nickname(nickname: str):
    with db_session() as db:
        row = db.execute(
            "SELECT * FROM users WHERE lower(nickname)=lower(?) ORDER BY last_seen DESC LIMIT 1",
            (nickname.lstrip("@"),)
        ).fetchone()
        return dict(row) if row else None

def update_user_field(uid: str, field: str, value: str):
    allowed = {"bio", "status", "genshin_uid"}
    if field not in allowed:
        return
    with db_session() as db:
        db.execute(f"UPDATE users SET {field}=? WHERE uid=?", (value, uid))

def add_points(uid: str, amount: int):
    with db_session() as db:
        db.execute("UPDATE users SET points=points+? WHERE uid=?", (int(amount), uid))

def get_affinity(uid: str, target_uid: str) -> int:
    if not uid or not target_uid or uid == target_uid:
        return 0
    with db_session() as db:
        row = db.execute(
            """SELECT COALESCE(SUM(score),0) AS score FROM affinities
               WHERE (uid=? AND target_uid=?) OR (uid=? AND target_uid=?)""",
            (uid, target_uid, target_uid, uid)
        ).fetchone()
        return int(row["score"] or 0)

def add_affinity(uid: str, target_uid: str, cooldown: int = 30) -> int:
    if not uid or not target_uid or uid == target_uid:
        return 0

    now = time.time()
    first, second = sorted((uid, target_uid))

    with db_session() as db:
        rows = db.execute(
            """SELECT score,last_at FROM affinities
               WHERE (uid=? AND target_uid=?) OR (uid=? AND target_uid=?)""",
            (first, second, second, first)
        ).fetchall()

        current_score = sum(int(row["score"]) for row in rows)
        last_at = max((float(row["last_at"]) for row in rows), default=0.0)

        if rows and now - last_at < cooldown:
            return current_score

        new_score = current_score + 1
        db.execute(
            "DELETE FROM affinities WHERE (uid=? AND target_uid=?) OR (uid=? AND target_uid=?)",
            (first, second, second, first)
        )
        db.execute(
            "INSERT INTO affinities(uid,target_uid,score,last_at) VALUES(?,?,?,?)",
            (first, second, new_score, now)
        )
        return new_score

def save_gift(sender_uid, sender_name, target_uid, target_name, message):
    with db_session() as db:
        db.execute(
            """INSERT INTO gifts(
                sender_uid,sender_name,target_uid,target_name,message,created_at
            ) VALUES(?,?,?,?,?,?)""",
            (sender_uid, sender_name[:80], target_uid, target_name[:80],
             message[:120], time.time())
        )

def get_top_users(column: str, limit: int = 10):
    if column not in {"points", "message_count"}:
        column = "points"
    with db_session() as db:
        rows = db.execute(
            f"""SELECT nickname,{column} AS value FROM users
                WHERE {column}>0 ORDER BY {column} DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [(r["nickname"], int(r["value"])) for r in rows]

def get_recent_gifts(target_uid: str, limit: int = 5):
    with db_session() as db:
        rows = db.execute(
            """SELECT sender_name,message FROM gifts
               WHERE target_uid=? ORDER BY id DESC LIMIT ?""",
            (target_uid, limit)
        ).fetchall()
        return [(r["sender_name"], r["message"]) for r in rows]

def get_wish_state(uid: str):
    with db_session() as db:
        row = db.execute(
            "SELECT pity5,pity4,guaranteed FROM wishes WHERE uid=?",
            (uid,)
        ).fetchone()
        if not row:
            return {"pity5": 0, "pity4": 0, "guaranteed": False}
        return {
            "pity5": int(row["pity5"]),
            "pity4": int(row["pity4"]),
            "guaranteed": bool(row["guaranteed"])
        }

def save_wish_state(uid: str, state: dict):
    with db_session() as db:
        db.execute(
            """INSERT INTO wishes(uid,pity5,pity4,guaranteed,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(uid) DO UPDATE SET
               pity5=excluded.pity5,
               pity4=excluded.pity4,
               guaranteed=excluded.guaranteed,
               updated_at=excluded.updated_at""",
            (
                uid,
                int(state.get("pity5", 0)),
                int(state.get("pity4", 0)),
                int(bool(state.get("guaranteed", False))),
                time.time()
            )
        )

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
poll_button_map: dict[str, str] = {}
enka_cache: dict[str, tuple[float, Any]] = {}
background_tasks: set[asyncio.Task] = set()

trivia = {}
number_games = {}
curiosity_bag = []
last_curiosity = None

# ---------------------------------------------------------------------------
# COMANDOS SOCIALES
# ---------------------------------------------------------------------------
SOCIAL = {
    "abrazo": ["abraza con cariño a"],
    "abrazar": ["abraza con cariño a"],
    "beso": ["le da un beso a"],
    "besar": ["le da un beso a"],
    "patada": ["le da una patada a"],
    "golpe": ["golpea a"],
    "cachetada": ["le da una cachetada a"],
    "acariciar": ["acaricia a"],
    "mimar": ["mima a"],
    "morder": ["muerde a"],
    "consolar": ["consuela a"],
    "felicitar": ["felicita a"],
    "saludar": ["saluda a"],
    "proteger": ["protege a"],
    "bailar": ["baila con"],
    "cosquillas": ["le hace cosquillas a"],
    "chocar": ["choca los cinco con"],
    "highfive": ["choca los cinco con"],
    "ayudar": ["ayuda a"],
    "regalar": ["le hace un regalo a"],
    "acompañar": ["acompaña a"],
    "asustar": ["asusta a"],
    "curar": ["cura a"],
    "cargar": ["carga a"],
    "empujar": ["empuja a"],
    "regañar": ["regaña a"],
    "admirar": ["admira a"],
    "apoyar": ["apoya a"],
    "dormir": ["duerme junto a"],
    "ignorar": ["ignora a"],
    "mirar": ["mira a"],
    "invitar": ["invita a"]
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

def clean_text(value: Any) -> str:
    # No borrar guiones bajos, asteriscos ni acentos: pueden formar parte
    # de nombres, UID, texto de usuario o formato enviado por la API.
    return str(value or "").replace("\x00", "").strip()

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
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(
                method,
                API + path,
                headers=headers,
                json=body
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

        return {
            "ok": response.is_success,
            "status": response.status_code,
            "data": data,
            "retry_after": response.headers.get("retry-after")
        }

    except Exception as exc:
        logging.exception("Error de conexion con Anarchy")
        return {
            "ok": False,
            "status": 500,
            "data": {"error": str(exc)}
        }

async def send_message(
    thread_id: str,
    content: str,
    source="developer",
    reply_id=None,
    buttons=None
):
    global last_bot_send

    content = clean_text(content)[:2000]

    if not thread_id or not content:
        return

    if buttons:
        buttons = [
            {
                "id": str(b["id"])[:32],
                "label": str(b["label"])[:40]
            }
            for b in buttons[:5]
        ]

    async with send_lock:
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

        body = {"content": content}

        if reply_id:
            body["replyMessageId"] = str(reply_id)

        if buttons:
            body["buttons"] = buttons

        result = await api(
            "POST",
            f"/threads/{thread_id}/messages",
            source,
            body
        )

        if result["status"] == 429:
            error_data = result.get("data")
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

        if result["ok"]:
            now = time.monotonic()
            last_bot_send = now
            last_chat_send[f"{source}:{thread_id}"] = now

            send_history_bot.append(now)
            send_history_chat[f"{source}:{thread_id}"].append(now)
        else:
            logging.error("No se pudo enviar mensaje: %s", result)

        return result

# ---------------------------------------------------------------------------
# GENSHIN DATABASE API
# ---------------------------------------------------------------------------
async def game_data(folder: str, query: str):
    key = f"{folder}:{query.casefold()}"

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

    try:
        async with httpx.AsyncClient(timeout=18) as client:
            r = await client.get(
                f"{GAME_API}/{folder}",
                params=params
            )
            r.raise_for_status()
            data = r.json()

        if isinstance(data, dict):
            # Con dumpResult=true Genshin-DB envuelve el objeto real en "result".
            if "result" in data:
                data = data.get("result")
            elif "data" in data:
                data = data.get("data")

        cache[key] = (time.time() + 21600, data)

        if len(cache) > 300:
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

def describe_game(data: Any, fallback: str) -> str:
    if not isinstance(data, dict):
        return f"No encontré información de «{fallback}»."

    name = data.get("name") or data.get("Name") or fallback

    fields = (
        ("rarity", "Rareza"),
        ("elementText", "Elemento"),
        ("weaponText", "Arma"),
        ("region", "Región"),
        ("substatText", "Atributo de ascensión"),
        ("constellation", "Constelación"),
        ("birthday", "Cumpleaños"),
        ("affiliation", "Afiliación"),
        ("description", "Descripción")
    )

    lines = [f"GENSHIN IMPACT — {name}"]

    for key, label in fields:
        value = data.get(key)

        if isinstance(value, (str, int, float)) and str(value).strip():
            lines.append(f"{label}: {value}")

    if len(lines) == 1:
        for k, v in list(data.items())[:10]:
            if k in {"id", "name", "Name"}:
                continue
            if isinstance(v, (str, int, float)):
                lines.append(f"{k}: {v}")

    return "\n".join(lines)[:1950]

async def character_stats(query: str):
    key = f"stats:{query.casefold()}"

    if key in cache and cache[key][0] > time.time():
        return cache[key][1]

    params = {
        "folder": "characters",
        "query": query,
        "queryLanguages": "english,spanish"
    }

    try:
        async with httpx.AsyncClient(timeout=18) as client:
            response = await client.get(
                f"{GAME_API}/stats",
                params=params
            )
            response.raise_for_status()
            data = response.json()

        if isinstance(data, dict):
            cache[key] = (time.time() + 21600, data)
            return data

    except Exception as exc:
        logging.warning(
            "No se pudieron consultar estadísticas de %s: %s",
            query,
            exc
        )

    return None

async def get_character(query: str):
    return await game_data("characters", query)

# ---------------------------------------------------------------------------
# ENKA NETWORK
# ---------------------------------------------------------------------------
async def get_enka(uid: str, info_only: bool = False):
    cache_key = f"{uid}:{'info' if info_only else 'full'}"
    cached = enka_cache.get(cache_key)

    if cached and cached[0] > time.time():
        return cached[1], None

    url = f"{ENKA_API}/uid/{uid}/" + ("?info" if info_only else "")

    headers = {
        "User-Agent": "Katherine-AnarchyBot/2.0 (community bot; contact through Anarchy Apps)"
    }

    try:
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            response = await client.get(url)

        if response.status_code != 200:
            return None, response.status_code

        data = response.json()
        ttl = data.get("ttl", 300) if isinstance(data, dict) else 300

        try:
            # Enka indica cuántos segundos faltan para que convenga pedir UID otra vez.
            ttl = max(1, int(ttl))
        except (ValueError, TypeError):
            ttl = 300

        enka_cache[cache_key] = (time.time() + ttl, data)
        return data, None

    except Exception as exc:
        logging.warning("Enka UID %s: %s", uid, exc)
        return None, 500

def format_number(value, decimals: int = 0):
    try:
        number = float(value)
        if not math.isfinite(number):
            return "—"
        return f"{number:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "—"

def format_enka_prop(prop_id: Any, value: Any) -> str:
    """Traduce propiedades comunes de armas/artefactos a etiquetas legibles."""
    prop_id = str(prop_id or "")
    labels = {
        "FIGHT_PROP_HP": "Vida",
        "FIGHT_PROP_ATTACK": "ATQ",
        "FIGHT_PROP_DEFENSE": "DEF",
        "FIGHT_PROP_HP_PERCENT": "Vida",
        "FIGHT_PROP_ATTACK_PERCENT": "ATQ",
        "FIGHT_PROP_DEFENSE_PERCENT": "DEF",
        "FIGHT_PROP_CRITICAL": "Prob. CRIT",
        "FIGHT_PROP_CRITICAL_HURT": "Daño CRIT",
        "FIGHT_PROP_CHARGE_EFFICIENCY": "Recarga",
        "FIGHT_PROP_ELEMENT_MASTERY": "Maestría elemental",
        "FIGHT_PROP_HEAL_ADD": "Bono de curación",
        "FIGHT_PROP_PHYSICAL_ADD_HURT": "Daño físico",
        "FIGHT_PROP_FIRE_ADD_HURT": "Daño Pyro",
        "FIGHT_PROP_ELEC_ADD_HURT": "Daño Electro",
        "FIGHT_PROP_WATER_ADD_HURT": "Daño Hydro",
        "FIGHT_PROP_GRASS_ADD_HURT": "Daño Dendro",
        "FIGHT_PROP_WIND_ADD_HURT": "Daño Anemo",
        "FIGHT_PROP_ROCK_ADD_HURT": "Daño Geo",
        "FIGHT_PROP_ICE_ADD_HURT": "Daño Cryo"
    }
    label = labels.get(prop_id, prop_id.replace("FIGHT_PROP_", "").replace("_", " ").title() or "Estadística")
    percent = (
        prop_id.endswith("_PERCENT")
        or prop_id in {
            "FIGHT_PROP_CRITICAL",
            "FIGHT_PROP_CRITICAL_HURT",
            "FIGHT_PROP_CHARGE_EFFICIENCY",
            "FIGHT_PROP_HEAL_ADD",
            "FIGHT_PROP_PHYSICAL_ADD_HURT",
            "FIGHT_PROP_FIRE_ADD_HURT",
            "FIGHT_PROP_ELEC_ADD_HURT",
            "FIGHT_PROP_WATER_ADD_HURT",
            "FIGHT_PROP_GRASS_ADD_HURT",
            "FIGHT_PROP_WIND_ADD_HURT",
            "FIGHT_PROP_ROCK_ADD_HURT",
            "FIGHT_PROP_ICE_ADD_HURT"
        }
    )
    try:
        shown = f"{float(value):g}"
    except (TypeError, ValueError):
        shown = str(value)
    return f"{label}: {shown}{'%' if percent else ''}"

def get_level90(stats):
    if not isinstance(stats, dict):
        return None

    # 90+ es el nivel 90 después de la última ascensión; tiene prioridad
    # sobre el nivel 90 sin ascender.
    return stats.get("90+") or stats.get("90") or stats.get("89")

def format_specialized_stat(data: dict, base: dict) -> str | None:
    raw = base.get("specialized")
    if raw is None:
        return None
    try:
        value = float(raw)
        if not math.isfinite(value):
            return None
    except (TypeError, ValueError):
        return None

    label = str(data.get("substatText") or data.get("substat") or "Atributo especial")
    normalized = label.casefold()

    # Maestría elemental es un valor plano. Los atributos porcentuales de
    # Genshin-DB suelen venir como fracción (por ejemplo, 0.384 = 38.4%).
    is_flat = any(term in normalized for term in (
        "maestría elemental", "maestria elemental", "elemental mastery"
    ))
    percent_terms = (
        "%", "crit", "crítico", "critico", "critical", "daño", "damage",
        "bonus", "bono", "recarga", "recharge", "curación", "curacion",
        "healing", "resistencia", "resistance", "vida", "hp", "ataque", "atk"
    )
    is_percent = not is_flat and any(term in normalized for term in percent_terms)

    if is_percent:
        if abs(value) <= 5:
            value *= 100
        return f"{label}: {value:.1f}%"

    return f"{label}: {value:.1f}"

def target_from_message(msg, args):
    mentions = msg.get("mentions") or []

    for mention in mentions:
        if isinstance(mention, dict) and mention.get("uid"):
            return (
                str(mention["uid"]),
                str(mention.get("nickname") or mention["uid"])
            )

    raw = next((x for x in args if x.startswith("@")), None)

    if raw:
        nickname = raw.lstrip("@")
        found = find_user_by_nickname(nickname)

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
        "Baizhu protege y cura; Cyno mantiene la ventana de daño.",
        "Furina E/Q → Nahida E/Q → Baizhu E/Q → Cyno E/Q; "
        "mantén los ataques de Cyno y usa E cuando aparezca el indicador de "
        "Ojo del Juicio. La rotación puede variar según energía y duración de marcas."
    ),
    "nahida": (
        "Nahida · Kuki Shinobu · Xingqiu · Yelan",
        "Nahida conduce la reacción; Kuki activa Hyperbloom; "
        "Xingqiu y Yelan mantienen Hydro.",
        "Xingqiu E/Q → Yelan E/Q → Kuki E → Nahida E/Q y ataques normales; "
        "renueva las habilidades cuando terminen."
    ),
    "hu tao": (
        "Hu Tao · Xingqiu · Yelan · Zhongli",
        "Zhongli protege; Xingqiu y Yelan aplican Hydro; "
        "Hu Tao realiza sus ataques cargados durante la habilidad.",
        "Zhongli mantener E → Xingqiu E/Q → Yelan E/Q → Hu Tao E; "
        "realiza ataques normales+cargados y termina con Q según necesidad."
    ),
    "raiden": (
        "Raiden · Xiangling · Bennett · Xingqiu",
        "Xingqiu prepara Hydro; Bennett usa definitiva; "
        "Xiangling lanza Guoba y Pyronado; Raiden cierra con su definitiva.",
        "Xingqiu Q/E → Bennett Q/E → Xiangling Q/E → Raiden E/Q; "
        "ejecuta ataques durante la definitiva de Raiden."
    ),
    "neuvillette": (
        "Neuvillette · Furina · Kazuha · Baizhu",
        "Furina aporta daño y Hydro; Kazuha agrupa y dispersa; "
        "Baizhu protege; Neuvillette ejecuta sus cargados.",
        "Furina E/Q → Baizhu E/Q → Kazuha E/Q → Neuvillette E, cargado, Q "
        "y cargados. Ajusta el orden para aprovechar las gotas de fuente."
    ),
    "arlecchino": (
        "Arlecchino · Bennett · Kazuha · Zhongli",
        "Zhongli protege; Bennett potencia; Kazuha agrupa y reduce resistencias; "
        "Arlecchino ejecuta su ventana de daño.",
        "Zhongli mantener E → Arlecchino E para marcar → Kazuha E/Q para "
        "dispersar Pyro cuando sea posible → Bennett Q → Arlecchino cargado "
        "para absorber la marca y ataques normales. Usa Q para curación o reinicio."
    )
}

# ---------------------------------------------------------------------------
# GESTOR PRINCIPAL DE COMANDOS
# ---------------------------------------------------------------------------
async def handle_command(event: dict, source: str):
    msg = event.get("message") or {}
    author = msg.get("author") or {}

    thread = str(event.get("threadId") or "")
    content = str(msg.get("content") or "").strip()

    if not thread or not content:
        return

    uid = str(author.get("uid") or author.get("nickname") or "unknown")
    nickname = str(author.get("nickname") or "Viajero")
    message_id = msg.get("messageId")

    record_user(uid, nickname, count_message=True)

    if content.startswith(PREFIX):
        command_text = content[len(PREFIX):].strip()
    elif PREFIX != "!" and content.startswith("!"):
        command_text = content[1:].strip()
    elif content.startswith("!") or content.startswith("-"):
        # Compatibilidad con el prefijo clásico y el alias antiguo con guion.
        command_text = content[1:].strip()
    else:
        return

    if not command_text:
        return

    command_parts = command_text.split(maxsplit=1)
    command_name = command_parts[0].casefold()
    query = command_parts[1].strip() if len(command_parts) > 1 else ""
    if not re.fullmatch(r"[a-z0-9_-]+", command_name):
        return

    command = "!" + command_name
    args = query.split()
    reply = message_id

    logging.info(
        "[COMANDO] %s | %s | %s",
        command,
        nickname,
        thread
    )

    # -----------------------------------------------------------------------
    # AYUDA
    # -----------------------------------------------------------------------
    if command in ("!ayuda", "!comandos"):
        answer = (
            "KATHERINE — COMANDOS\n\nGENSHIN IMPACT\n"
            "!personaje nombre | !materiales nombre | !comparar nombre 1 | nombre 2\n"
            "!build nombre (requiere UID vinculado) | !equipos personaje | !rotacion personaje\n"
            "!calculadora cv CR CD | !calculadora critico CR CD\n"
            "!arma nombre | !artefacto nombre | !dominio nombre | !enemigo nombre\n"
            "!comida nombre | !material nombre | !logro nombre | !talento nombre\n"
            "!constelacion nombre | !reaccion elemento elemento | !ficha [UID] | !abismo [UID]\n"
            "!genshinuid 123456789 | !curiosidad | !deseo | !pity | !resetpity\n\n"
            "SOCIAL\n!perfil [@usuario] | !bio texto | !estado texto\n"
            "!afinidad @usuario | !amistad @usuario | !regalo @usuario [mensaje] | !muro | !actividad\n"
            "!abrazo @usuario | !beso @usuario | !patada @usuario | !golpe @usuario\n"
            "!cachetada | !acariciar | !mimar | !morder | !consolar | !felicitar\n"
            "!saludar | !proteger | !bailar | !cosquillas | !highfive | !ayudar\n"
            "!regalar | !acompañar | !asustar | !curar | !cargar | !empujar\n"
            "!regañar | !admirar | !apoyar | !dormir | !ignorar | !mirar | !invitar\n\n"
            "ENTRETENIMIENTO\n!trivia | !responder 1-4 | !adivina | !intento número\n"
            "!puntos | !ranking | !encuesta pregunta | opción 1 | opción 2\n"
            "!elige | !duelo @usuario | !ship usuario1 usuario2 | !dado | !moneda\n"
            "!8ball pregunta | !chiste | !frase | !reto | !suerte\n\n"
            "SISTEMA\n!ping | !uptime | !fecha | !zona America/Santiago | !salas\n"
            "Deseos/pity son simulados. Los perfiles se guardan en SQLite; "
            "la persistencia depende del almacenamiento configurado en Render."
        )

        if PREFIX != "!":
            answer = answer.replace("!", PREFIX)
        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # INTERACCIONES SOCIALES
    # -----------------------------------------------------------------------
    if command[1:] in SOCIAL:
        target = next(
            (
                m.get("nickname") or m.get("uid")
                for m in (msg.get("mentions") or [])
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
        target_uid, target_name = target_from_message(msg, args)

        if target_uid and target_uid != uid:
            add_affinity(uid, target_uid)

        await send_message(
            thread,
            f"{nickname} {random.choice(actions)} {target}.",
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

    if command == "!estado":
        if not query:
            await send_message(
                thread,
                "Uso: !estado tu estado | usa !estado borrar para quitarlo.",
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
        target_uid, target_name = target_from_message(msg, args)

        if target_uid:
            user = get_user(target_uid)
        elif target_name:
            user = find_user_by_nickname(target_name)
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
            f"UID de Genshin: {user['genshin_uid'] or 'No vinculado'}",
            f"Puntos: {user['points']} | Mensajes registrados: {user['message_count']}"
        ]

        if user["uid"] != uid:
            lines.append(f"Afinidad contigo: {affinity}")

        await send_message(
            thread,
            "\n".join(lines)[:1900],
            source,
            reply
        )
        return

    if command == "!genshinuid":
        if not query:
            user = get_user(uid)

            await send_message(
                thread,
                f"UID vinculado: {user['genshin_uid'] or 'ninguno'}. "
                "Usa !genshinuid 123456789 para vincularlo o !genshinuid borrar.",
                source,
                reply
            )
            return

        value = query.strip()

        if value.lower() in ("borrar", "eliminar", "off"):
            update_user_field(uid, "genshin_uid", "")
            await send_message(
                thread,
                "Tu UID de Genshin fue desvinculado.",
                source,
                reply
            )
            return

        if not re.fullmatch(r"\d{8,10}", value):
            await send_message(
                thread,
                "El UID debe contener entre 8 y 10 dígitos.",
                source,
                reply
            )
            return

        update_user_field(uid, "genshin_uid", value)

        await send_message(
            thread,
            "UID guardado. Usa !ficha para consultar los datos públicos de tu perfil.",
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # FICHA DE JUGADOR EN ENKA
    # -----------------------------------------------------------------------
    if command == "!ficha":
        requested_uid = (
            query.strip()
            if query
            else (get_user(uid) or {}).get("genshin_uid", "")
        )

        if not re.fullmatch(r"\d{8,10}", requested_uid or ""):
            await send_message(
                thread,
                "Uso: !ficha UID o vincula primero tu cuenta con !genshinuid 123456789.",
                source,
                reply
            )
            return

        data, status = await get_enka(requested_uid, info_only=True)

        if not data:
            messages = {
                400: "El UID no tiene un formato válido.",
                404: "No se encontró esa cuenta.",
                424: "Enka está en mantenimiento por una actualización del juego.",
                429: "Enka limitó temporalmente las consultas. Intenta más tarde."
            }

            await send_message(
                thread,
                messages.get(status, f"No se pudo consultar Enka (HTTP {status})."),
                source,
                reply
            )
            return

        player = data.get("playerInfo") or {}

        lines = [
            f"FICHA DE VIAJERO — {player.get('nickname') or 'Jugador'}",
            f"UID: {requested_uid}"
        ]

        if player.get("worldLevel") is not None:
            lines.append(f"Nivel de mundo: {player.get('worldLevel')}")

        if player.get("finishAchievementNum") is not None:
            lines.append(f"Logros: {player.get('finishAchievementNum')}")

        if player.get("towerFloorIndex") is not None:
            lines.append(
                f"Abismo: piso {player.get('towerFloorIndex')}, "
                f"sala {player.get('towerLevelIndex', '?')}"
            )

        if player.get("signature"):
            lines.append(f"Firma: {str(player['signature'])[:180]}")

        lines.append("Datos públicos obtenidos desde Enka.Network.")

        await send_message(
            thread,
            "\n".join(lines)[:1900],
            source,
            reply
        )
        return

    if command == "!abismo":
        requested_uid = (
            query.strip()
            if query
            else (get_user(uid) or {}).get("genshin_uid", "")
        )

        if not re.fullmatch(r"\d{8,10}", requested_uid or ""):
            await send_message(
                thread,
                "Uso: !abismo UID o vincula tu cuenta con !genshinuid 123456789.",
                source,
                reply
            )
            return

        data, status = await get_enka(requested_uid, info_only=True)

        if not data:
            await send_message(
                thread,
                f"No pude consultar Enka (HTTP {status}).",
                source,
                reply
            )
            return

        player = data.get("playerInfo") or {}
        floor = player.get("towerFloorIndex")
        chamber = player.get("towerLevelIndex")

        if floor is None:
            answer = "Enka no devolvió datos del Abismo para ese perfil."
        else:
            answer = (
                "PROGRESO DEL ABISMO\n"
                f"Piso: {floor}\n"
                f"Sala: {chamber if chamber is not None else 'No disponible'}\n"
                f"Logros: {player.get('finishAchievementNum', 'No disponible')}\n\n"
                "Datos del perfil público de Enka; no incluye equipos ni estrellas por sala."
            )

        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # AFINIDAD Y REGALOS
    # -----------------------------------------------------------------------
    if command in ("!afinidad", "!amistad"):
        target_uid, target_name = target_from_message(msg, args)

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
        target_uid, target_name = target_from_message(msg, args)

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
        top = get_top_users("message_count", 10)

        answer = "ACTIVIDAD DEL CHAT\n\n" + (
            "\n".join(
                f"{i}. {name} — {count} mensajes registrados"
                for i, (name, count) in enumerate(top, 1)
            )
            or "Aún no hay actividad registrada."
        )

        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # GENSHIN IMPACT: PERSONAJES
    # -----------------------------------------------------------------------
    if command in ("!personaje", "!ficha-personaje"):
        if not query:
            await send_message(
                thread,
                "Uso: !personaje nombre",
                source,
                reply
            )
            return

        data = await get_character(query)

        if not isinstance(data, dict):
            await send_message(
                thread,
                f"No encontré al personaje «{query}».",
                source,
                reply
            )
            return

        stats = await character_stats(query)
        base = get_level90(stats)
        answer = describe_game(data, query)

        if isinstance(base, dict):
            answer += "\n\nESTADÍSTICAS BASE — NIVEL 90\n"
            answer += (
                f"Vida: {format_number(base.get('hp'))} | "
                f"ATQ: {format_number(base.get('attack'))} | "
                f"DEF: {format_number(base.get('defense'))}"
            )

            specialized = format_specialized_stat(data, base)
            if specialized:
                answer += f"\n{specialized} (valor base a nivel 90 ascendido)"

        answer += (
            "\n\nUsa !materiales "
            + str(data.get("name") or query)
            + " para ver la ascensión."
        )

        await send_message(thread, answer[:1950], source, reply)
        return

    # -----------------------------------------------------------------------
    # GENSHIN IMPACT: MATERIALES DE ASCENSIÓN
    # -----------------------------------------------------------------------
    if command == "!materiales":
        if not query:
            await send_message(
                thread,
                "Uso: !materiales nombre del personaje",
                source,
                reply
            )
            return

        data = await get_character(query)
        costs = data.get("costs") if isinstance(data, dict) else None

        if not isinstance(costs, dict):
            await send_message(
                thread,
                f"No encontré los materiales de ascensión de «{query}».",
                source,
                reply
            )
            return

        totals = defaultdict(int)

        for stage in costs.values():
            if isinstance(stage, list):
                for item in stage:
                    if isinstance(item, dict) and item.get("name"):
                        try:
                            totals[str(item["name"])] += int(item.get("count", 0))
                        except (TypeError, ValueError):
                            pass

        if not totals:
            await send_message(
                thread,
                "La base de datos no devolvió materiales de ascensión para ese personaje.",
                source,
                reply
            )
            return

        lines = [
            f"MATERIALES DE ASCENSIÓN — {data.get('name', query)}",
            "Totales para subir al nivel 90:"
        ]

        lines.extend(
            f"• {name}: {format_number(amount)}"
            for name, amount in totals.items()
        )

        lines.append("\nNo incluye materiales para subir talentos.")

        await send_message(
            thread,
            "\n".join(lines)[:1900],
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # GENSHIN IMPACT: COMPARAR PERSONAJES
    # -----------------------------------------------------------------------
    if command == "!comparar":
        parts = [x.strip() for x in query.split("|") if x.strip()]

        if len(parts) != 2:
            await send_message(
                thread,
                "Uso: !comparar personaje 1 | personaje 2",
                source,
                reply
            )
            return

        results = await asyncio.gather(
            get_character(parts[0]),
            get_character(parts[1]),
            character_stats(parts[0]),
            character_stats(parts[1])
        )

        a, b, sa, sb = results

        if not isinstance(a, dict) or not isinstance(b, dict):
            await send_message(
                thread,
                "No pude encontrar ambos personajes. Revisa los nombres.",
                source,
                reply
            )
            return

        la, lb = get_level90(sa), get_level90(sb)

        if not isinstance(la, dict) or not isinstance(lb, dict):
            await send_message(
                thread,
                "No pude obtener las estadísticas de ambos personajes.",
                source,
                reply
            )
            return

        lines = [
            f"COMPARACIÓN — {a.get('name', parts[0])} / {b.get('name', parts[1])}"
        ]

        for key, label in (
            ("hp", "Vida"),
            ("attack", "ATQ"),
            ("defense", "DEF")
        ):
            lines.append(
                f"{label}: {format_number(la.get(key))} / {format_number(lb.get(key))}"
            )

        special_a = format_specialized_stat(a, la)
        special_b = format_specialized_stat(b, lb)
        if special_a or special_b:
            label_a = special_a or "Atributo no disponible"
            label_b = special_b or "Atributo no disponible"
            lines.append(f"Ascensión: {label_a} / {label_b}")
        lines.append(
            f"Rareza: {a.get('rarity', '?')}★ / {b.get('rarity', '?')}★"
        )
        lines.append(
            f"Elemento: {a.get('elementText', '?')} / {b.get('elementText', '?')}"
        )
        lines.append(
            "Valores base a nivel 90; no incluyen arma, artefactos ni bonificaciones."
        )

        await send_message(
            thread,
            "\n".join(lines)[:1900],
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # GENSHIN IMPACT: EQUIPOS Y ROTACIONES
    # -----------------------------------------------------------------------
    if command == "!equipos":
        key = query.casefold().strip()
        team = BUILD_TEAMS.get(key)

        if not team:
            await send_message(
                thread,
                "Aún no tengo una composición guardada para ese personaje. "
                "Prueba Cyno, Nahida, Hu Tao, Raiden, Neuvillette o Arlecchino.",
                source,
                reply
            )
            return

        await send_message(
            thread,
            f"EQUIPO ORIENTATIVO — {query}\n"
            f"{team[0]}\n\n"
            f"Sinergia: {team[1]}\n\n"
            "Es una propuesta general, no una simulación de daño.",
            source,
            reply
        )
        return

    if command == "!rotacion":
        key = query.casefold().strip()
        team = BUILD_TEAMS.get(key)

        if not team:
            await send_message(
                thread,
                "No tengo una rotación guardada para ese personaje. "
                "Prueba Cyno, Nahida, Hu Tao, Raiden, Neuvillette o Arlecchino.",
                source,
                reply
            )
            return

        await send_message(
            thread,
            f"ROTACIÓN ORIENTATIVA — {query}\n"
            f"Equipo: {team[0]}\n\n"
            f"{team[2]}\n\n"
            "Es una secuencia sugerida; adapta el orden a las constelaciones, "
            "recarga y enemigos.",
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # GENSHIN IMPACT: CALCULADORA
    # -----------------------------------------------------------------------
    if command == "!calculadora":
        parts = query.split()

        if len(parts) != 3:
            await send_message(
                thread,
                "Uso: !calculadora cv 10.5 21 o !calculadora critico 75 160",
                source,
                reply
            )
            return

        mode = parts[0].lower()

        try:
            first = float(parts[1].replace(",", "."))
            second = float(parts[2].replace(",", "."))

            if not (math.isfinite(first) and math.isfinite(second)):
                raise ValueError
            if not (0 <= first <= 100 and 0 <= second <= 400):
                raise ValueError

        except ValueError:
            await send_message(
                thread,
                "Introduce CR entre 0 y 100 y CD entre 0 y 400. No uses NaN ni infinito.",
                source,
                reply
            )
            return

        if mode == "cv":
            answer = (
                "VALOR CRÍTICO (CV)\n"
                f"CR: {first:g}% | CD: {second:g}%\n"
                f"CV = 2 × CR + CD = {2*first+second:.1f}"
            )

        elif mode in ("critico", "crítico", "media"):
            average = 1 + (first / 100) * (second / 100)

            answer = (
                "DAÑO MEDIO ESPERADO\n"
                f"CR: {first:g}% | CD: {second:g}%\n"
                f"Multiplicador medio crítico: ×{average:.3f}\n"
                "No incluye DEF, RES, reacción ni bonificaciones."
            )

        else:
            await send_message(
                thread,
                "Modos disponibles: cv o critico.",
                source,
                reply
            )
            return

        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # GENSHIN IMPACT: BUILD REAL DESDE EL ESCAPARATE DE ENKA
    # -----------------------------------------------------------------------
    if command == "!build":
        if not query:
            await send_message(
                thread,
                "Uso: !build personaje. Primero vincula tu UID con "
                "!genshinuid 123456789; la build se lee del escaparate público de Enka.",
                source,
                reply
            )
            return

        user = get_user(uid) or {}
        game_uid = user.get("genshin_uid", "")

        if not game_uid:
            await send_message(
                thread,
                "Primero vincula tu UID con !genshinuid 123456789.",
                source,
                reply
            )
            return

        character = await get_character(query)

        if not isinstance(character, dict):
            await send_message(
                thread,
                f"No encontré al personaje «{query}» en la base de datos.",
                source,
                reply
            )
            return

        data, status = await get_enka(game_uid, info_only=False)

        if not data:
            messages = {
                404: "No se encontró esa cuenta.",
                424: "Enka está en mantenimiento por una actualización.",
                429: "Enka limitó las consultas. Intenta más tarde."
            }

            await send_message(
                thread,
                messages.get(status, f"No pude leer el escaparate (HTTP {status})."),
                source,
                reply
            )
            return

        avatar_id = character.get("id")
        avatars = data.get("avatarInfoList") or []

        if avatar_id is None:
            await send_message(
                thread,
                "La base de datos no devolvió el ID interno del personaje; "
                "no puedo vincularlo con el escaparate de Enka de forma segura.",
                source,
                reply
            )
            return

        avatar = next(
            (
                x for x in avatars
                if str(x.get("avatarId") or x.get("avatarID") or "") == str(avatar_id)
            ),
            None
        )

        if not avatar:
            await send_message(
                thread,
                f"No encontré a {character.get('name', query)} en la lista pública de personajes de ese UID. "
                "El escaparate puede estar cerrado o no tener ese personaje expuesto.",
                source,
                reply
            )
            return

        props = avatar.get("propMap") or {}
        fight = avatar.get("fightPropMap") or {}

        def stat(*keys):
            for key in keys:
                value = fight.get(key)
                if value is not None:
                    return value
            return None

        level = None

        for key in ("4001", "1002", "1"):
            item = props.get(key)

            if isinstance(item, dict) and item.get("val") is not None:
                level = item.get("val")
                break

        equip = avatar.get("equipList") or []

        weapon = next(
            (
                x for x in equip
                if (x.get("flat") or {}).get("itemType") == "ITEM_WEAPON"
            ),
            None
        )

        lines = [
            f"BUILD DEL ESCAPARATE — {character.get('name', query)}",
            f"UID: {game_uid}"
        ]

        if level is not None:
            lines.append(f"Nivel: {level}")

        stats_to_show = [
            (("2000",), "Vida"),
            (("2001",), "ATQ"),
            (("2002",), "DEF"),
            (("28",), "Maestría elemental"),
            (("20",), "Prob. CRIT"),
            (("22",), "Daño CRIT"),
            (("23",), "Recarga")
        ]

        for keys, label in stats_to_show:
            value = stat(*keys)

            if value is not None:
                suffix = "%" if label in (
                    "Prob. CRIT",
                    "Daño CRIT",
                    "Recarga"
                ) else ""

                if suffix and float(value) <= 5:
                    shown = f"{float(value)*100:.1f}{suffix}"
                else:
                    shown = f"{float(value):.1f}{suffix}"

                lines.append(f"{label}: {shown}")

        if weapon:
            weapon_data = weapon.get("weapon") or {}
            affix_map = weapon_data.get("affixMap") or {}
            refinement_raw = next(iter(affix_map.values()), 0)
            try:
                refinement_value = max(1, min(5, int(refinement_raw) + 1))
            except (TypeError, ValueError):
                refinement_value = None

            refinement_text = (
                f" | refinamiento R{refinement_value}"
                if refinement_value is not None else ""
            )
            lines.append(
                f"Arma equipada: nivel {weapon_data.get('level', '?')}"
                f"{refinement_text}"
            )
            weapon_stats = (weapon.get("flat") or {}).get("weaponStats") or []
            for weapon_stat in weapon_stats[:2]:
                if isinstance(weapon_stat, dict):
                    prop_id = weapon_stat.get("appendPropID") or weapon_stat.get("mainPropId")
                    prop_value = weapon_stat.get("propValue")
                    if prop_id and prop_value is not None:
                        lines.append("Substat de arma: " + format_enka_prop(prop_id, prop_value))

        artifact_slots = {
            "EQUIP_BRACER": "Flor",
            "EQUIP_NECKLACE": "Pluma",
            "EQUIP_SHOES": "Reloj",
            "EQUIP_RING": "Copa",
            "EQUIP_DRESS": "Corona"
        }

        artifact_count = 0
        for item in equip:
            flat = item.get("flat") or {}

            if flat.get("itemType") != "ITEM_RELIQUARY":
                continue

            artifact_count += 1
            main = flat.get("reliquaryMainstat") or {}
            prop = str(
                main.get("mainPropId")
                or main.get("appendPropID")
                or "Estadística"
            )
            val = main.get("propValue")
            slot = artifact_slots.get(flat.get("equipType"), "Artefacto")
            artifact_data = item.get("reliquary") or {}
            artifact_level = artifact_data.get("level")
            level_text = f" +{max(0, int(artifact_level) - 1)}" if artifact_level is not None else ""

            if val is not None:
                lines.append(
                    f"{slot}{level_text}: {format_enka_prop(prop, val)}"
                )

            substats = flat.get("reliquarySubstats") or []
            formatted_substats = []
            for substat in substats[:4]:
                if not isinstance(substat, dict):
                    continue
                sub_prop = substat.get("appendPropID") or substat.get("mainPropId")
                sub_value = substat.get("propValue")
                if sub_prop and sub_value is not None:
                    formatted_substats.append(format_enka_prop(sub_prop, sub_value))
            if formatted_substats:
                lines.append("  Secundarias: " + " | ".join(formatted_substats))

        lines.append(f"Artefactos detectados: {artifact_count}/5")
        lines.append(f"Escaparate: https://enka.network/u/{game_uid}/")
        lines.append(
            "Solo se muestran personajes del escaparate público. "
            "Enka aplica límites y caché por UID."
        )

        await send_message(
            thread,
            "\n".join(lines)[:1900],
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # CONSULTAS DE OBJETOS DE GENSHIN
    # -----------------------------------------------------------------------
    object_commands = {
        "!arma": "weapons",
        "!artefacto": "artifacts",
        "!dominio": "domains",
        "!enemigo": "enemies",
        "!comida": "food",
        "!material": "materials",
        "!logro": "achievements",
        "!talento": "talents"
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
            describe_game(data, query),
            source,
            reply
        )
        return

    if command == "!constelacion":
        data = await game_data("characters", query)

        if isinstance(data, dict):
            consts = (
                data.get("constellations")
                or data.get("Constellations")
            )

            if isinstance(consts, dict):
                answer = (
                    f"CONSTELACIONES — {data.get('name', query)}\n"
                    + "\n".join(
                        f"{i}. {v.get('name', '')} — {v.get('description', '')}"
                        for i, v in enumerate(consts.values(), 1)
                    )
                )
            else:
                answer = describe_game(data, query)
        else:
            answer = f"No encontré el personaje {query}."

        await send_message(thread, answer, source, reply)
        return

    if command == "!reaccion":
        if len(args) < 2:
            await send_message(
                thread,
                "Uso: !reaccion electro dendro",
                source,
                reply
            )
            return

        reactions = {
            frozenset(("pyro", "hydro")): "Vaporización",
            frozenset(("cryo", "pyro")): "Derretido",
            frozenset(("dendro", "electro")): "Aceleración (Quicken); después puede activarse Agravado con Electro o Propagación con Dendro",
            frozenset(("dendro", "hydro")): "Florecimiento",
            frozenset(("dendro", "pyro")): "Quemadura",
            frozenset(("pyro", "electro")): "Sobrecarga",
            frozenset(("cryo", "electro")): "Superconductor",
            frozenset(("anemo", "pyro")): "Dispersión",
            frozenset(("geo", "pyro")): "Cristalización",
            frozenset(("geo", "hydro")): "Cristalización",
            frozenset(("geo", "electro")): "Cristalización",
            frozenset(("geo", "cryo")): "Cristalización",
            frozenset(("electro", "hydro")): "Electrocargado",
            frozenset(("cryo", "hydro")): "Congelado",
            frozenset(("anemo", "hydro")): "Dispersión",
            frozenset(("anemo", "cryo")): "Dispersión",
            frozenset(("anemo", "electro")): "Dispersión",
            frozenset(("anemo", "dendro")): "No produce una reacción elemental directa",
            frozenset(("geo", "dendro")): "No produce Cristalización"
        }

        pair = frozenset(x.lower() for x in args[:2])

        await send_message(
            thread,
            f"Reacción: {reactions.get(pair, 'No se reconoce una reacción directa clásica.')}",
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

        # Simulación aproximada: base 0,6%, aumento suave desde el pity 74,
        # garantizado a 90. No pretende replicar cada detalle del banner real.
        chance5 = min(1.0, 0.006 + max(0, w["pity5"] - 73) * 0.06)

        if w["pity5"] >= 90 or random.random() < chance5:
            w["pity5"] = 0
            w["pity4"] = 0

            featured = ["Cyno", "Nahida", "Dehya", "Furina", "Arlecchino"]
            standard = ["Diluc", "Jean", "Qiqi", "Mona", "Keqing", "Tighnari", "Dehya"]

            if w["guaranteed"] or random.random() < 0.5:
                name = random.choice(featured)
                w["guaranteed"] = False
                result_label = "Personaje promocional"
            else:
                name = random.choice(standard)
                w["guaranteed"] = True
                result_label = "Personaje del grupo permanente"

            save_wish_state(uid, w)
            await send_message(
                thread,
                f"RESULTADO SIMULADO\n⭐⭐⭐⭐⭐\n{name}\n{result_label}.\n"
                "Pool fijo de demostración; no representa el banner actual. "
                "El siguiente 5★ queda garantizado como promocional si este no lo fue. "
                "No afecta tu cuenta de Genshin.",
                source,
                reply
            )
            return

        if w["pity4"] >= 10 or random.random() < 0.051:
            w["pity4"] = 0
            name = random.choice(["Bennett", "Xingqiu", "Fischl", "Sucrose", "Diona"])
            save_wish_state(uid, w)
            await send_message(
                thread,
                f"RESULTADO SIMULADO\n⭐⭐⭐⭐\n{name}\nNo modifica tu cuenta.",
                source,
                reply
            )
            return

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
            f"DESEOS SIMULADOS\n"
            f"5 estrellas: {w['pity5']}\n"
            f"4 estrellas: {w['pity4']}\n"
            f"Garantizado promocional: {'Sí' if w['guaranteed'] else 'No'}",
            source,
            reply
        )
        return

    if command == "!resetpity":
        save_wish_state(uid, {
            "pity5": 0,
            "pity4": 0,
            "guaranteed": False
        })
        await send_message(
            thread,
            "Contadores simulados reiniciados. No modifica tu cuenta.",
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # TRIVIA
    # -----------------------------------------------------------------------
    if command == "!trivia":
        q = random.choice(TRIVIA)

        trivia[thread] = {
            "q": q,
            "tries": 0,
            "expires": time.time() + 120
        }

        await send_message(
            thread,
            "TRIVIA KATHERINE\n\n"
            + q[0]
            + "\n"
            + "\n".join(
                f"{i+1}. {a}"
                for i, a in enumerate(q[1])
            )
            + "\n\nResponde !responder 1, 2, 3 o 4.",
            source,
            reply
        )
        return

    if command == "!responder":
        game = trivia.get(thread)

        if not game or game["expires"] < time.time():
            trivia.pop(thread, None)

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
            trivia.pop(thread, None)
            answer = "¡Correcto! Ganaste 10 puntos."

        elif game["tries"] >= 3:
            answer = (
                "Se acabaron los intentos. Era: "
                f"{game['q'][1][game['q'][2]]}."
            )
            trivia.pop(thread, None)

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
        number_games[thread] = {
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
        game = number_games.get(thread)

        try:
            n = int(args[0])
        except (ValueError, IndexError):
            n = 0

        if not game or game["expires"] < time.time():
            number_games.pop(thread, None)
            answer = "No hay partida activa. Usa !adivina."

        elif not 1 <= n <= 100:
            answer = "Escribe un entero del 1 al 100."

        else:
            game["tries"] += 1

            if n == game["number"]:
                add_points(uid, 15)
                answer = f"¡Correcto! Era {n}. Ganaste 15 puntos."
                number_games.pop(thread, None)

            elif game["tries"] >= 7:
                answer = f"Se acabaron los intentos. Era {game['number']}."
                number_games.pop(thread, None)

            else:
                answer = "Es mayor." if n < game["number"] else "Es menor."

        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # PUNTOS Y RANKING
    # -----------------------------------------------------------------------
    if command == "!puntos":
        user = get_user(uid) or {}

        await send_message(
            thread,
            f"{nickname}, tienes {user.get('points', 0)} puntos.",
            source,
            reply
        )
        return

    if command == "!ranking":
        top = get_top_users("points", 10)

        answer = "RANKING KATHERINE\n\n" + (
            "\n".join(
                f"{i}. {u} — {p} puntos"
                for i, (u, p) in enumerate(top, 1)
            )
            or "Aún no hay puntos."
        )

        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # ENCUESTAS
    # -----------------------------------------------------------------------
    if command == "!encuesta":
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
        poll_id = f"{thread}:{time.time_ns()}"

        # Eliminar encuestas caducadas y mantener memoria acotada.
        now = time.time()
        for old_id, old_poll in list(polls.items()):
            if old_poll.get("expires", 0) <= now:
                polls.pop(old_id, None)
                poll_button_map.pop(old_id.split(":")[-1], None)
        while len(polls) >= 100:
            oldest_id = next(iter(polls))
            polls.pop(oldest_id, None)
            poll_button_map.pop(oldest_id.split(":")[-1], None)

        polls[poll_id] = {
            "question": question[:300],
            "options": [option[:40] for option in options],
            "votes": {},
            "source": source,
            "expires": now + 86400
        }

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
            x.strip()
            for x in query.split("|")
            if x.strip()
        ]

        await send_message(
            thread,
            f"Mi elección: {random.choice(choices)}"
            if choices
            else "Uso: !elige opción 1 | opción 2",
            source,
            reply
        )
        return

    if command == "!duelo":
        target = next(
            (x for x in args if x.startswith("@")),
            "un rival misterioso"
        )

        await send_message(
            thread,
            f"⚔️ {nickname} desafía a {target}. "
            f"¡La victoria es de {'Katherine' if random.random() < .5 else nickname}!",
            source,
            reply
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
    if command == "!ping":
        started = time.monotonic()
        result = await api("GET", "/me", source)

        if result["ok"]:
            answer = (
                f"PONG, {nickname}.\n"
                "Katherine está en línea.\n"
                f"Latencia API: {int((time.monotonic()-started)*1000)} ms."
            )
        else:
            answer = f"Anarchy respondió HTTP {result['status']}."

        await send_message(thread, answer, source, reply)
        return

    if command == "!uptime":
        seconds = int(time.time() - STARTED)

        await send_message(
            thread,
            f"Katherine lleva activa {seconds//3600} h "
            f"{(seconds%3600)//60} min.",
            source,
            reply
        )
        return

    if command == "!fecha":
        from datetime import datetime
        from zoneinfo import ZoneInfo

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
        from zoneinfo import ZoneInfo
        from datetime import datetime

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
        if source != "community":
            await send_message(
                thread,
                "Este comando requiere el token del bot de comunidad.",
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

        profile_body = {
            "name": name,
            "commandPrefix": PREFIX
        }
        if status is not None:
            profile_body["status"] = None if status in ("", "-") else status

        result = await api(
            "PATCH",
            "/me",
            "community",
            profile_body
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

    if command == "!salas":
        result = await api("GET", "/live-rooms", "community")

        response_data = result.get("data")
        rooms = (
            response_data.get("roomList", [])
            if result["ok"] and isinstance(response_data, dict)
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

# ---------------------------------------------------------------------------
# INTERACCIONES DE BOTONES DE ENCUESTAS
# ---------------------------------------------------------------------------
async def handle_interaction(event: dict, source: str):
    button_id = str(event.get("buttonId") or "")
    match = re.fullmatch(r"p(\d+)_(\d+)", button_id)

    if not match:
        return

    poll = polls.get(
        poll_button_map.get(match.group(1), "")
    )

    if not poll or poll.get("expires", 0) <= time.time():
        polls.pop(poll_button_map.get(match.group(1), ""), None)
        poll_button_map.pop(match.group(1), None)
        return

    index = int(match.group(2))

    if index >= len(poll["options"]):
        return

    interaction_user = event.get("user") or {}
    user = str(interaction_user.get("uid") or "unknown")
    if user != "unknown":
        record_user(user, interaction_user.get("nickname") or "Viajero")

    poll["votes"][user] = index

    counts = [
        sum(v == i for v in poll["votes"].values())
        for i in range(len(poll["options"]))
    ]

    result = (
        "RESULTADOS — "
        + poll["question"]
        + "\n"
        + "\n".join(
            f"{opt}: {counts[i]} voto(s)"
            for i, opt in enumerate(poll["options"])
        )
    )

    await send_message(
        str(event.get("threadId") or ""),
        result,
        source
    )

# ---------------------------------------------------------------------------
# EVENTOS Y WEBHOOKS
# ---------------------------------------------------------------------------
async def dispatch(event: dict, source: str):
    kind = event.get("type")

    if kind == "message":
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
        logging.info(
            "[SALA] %s | %s",
            kind,
            event.get("threadId")
        )

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
        event = json.loads(raw)
        return event if isinstance(event, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None

async def webhook(request: Request, source: str):
    secret = (
        COMMUNITY_SECRET
        if source == "community"
        else WEBHOOK_SECRET
    )

    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Webhook secret no configurado."
        )

    raw = await request.body()
    event = verify_webhook(raw, request.headers, secret)

    if event is None:
        raise HTTPException(
            status_code=401,
            detail="Firma inválida."
        )

    event_id = str(event.get("eventId") or "")

    if event_id and event_id in seen_event_set:
        return JSONResponse({
            "ok": True,
            "duplicate": True
        })

    if event_id:
        if len(seen_events) == seen_events.maxlen:
            old = seen_events.popleft()
            seen_event_set.discard(old)

        seen_events.append(event_id)
        seen_event_set.add(event_id)

    # Responder rápido a Anarchy; el procesamiento continúa en segundo plano
    # dentro del mismo proceso de la aplicación.
    task = asyncio.create_task(safe_dispatch(event, source))
    # Mantener una referencia evita que algunas implementaciones del loop
    # pierdan tareas no retenidas antes de que finalicen.
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return JSONResponse({"ok": True})

# ---------------------------------------------------------------------------
# RUTAS FASTAPI
# ---------------------------------------------------------------------------
@app.post("/")
async def main_webhook(request: Request):
    return await webhook(request, "developer")

@app.post("/webhook")
async def main_webhook_alias(request: Request):
    return await webhook(request, "developer")

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
    init_db()

    logging.info("SQLite activo en %s", DB_PATH)

    if not os.getenv("DATA_DIR"):
        logging.warning(
            "DATA_DIR no está configurado. En Render los datos locales "
            "no sobrevivirán a un redeploy/reinicio."
        )

    logging.info(
        "Katherine Python iniciando en puerto %s",
        PORT
    )

    for source in ("developer", "community"):
        if token_for(source):
            me = await api("GET", "/me", source)

            if me["ok"] and isinstance(me.get("data"), dict):
                bot = me["data"].get("bot", {})
                if not isinstance(bot, dict):
                    bot = {}

                logging.info(
                    "[BOT %s] %s | chats: %s",
                    source.upper(),
                    bot.get("name"),
                    me["data"].get("threadIds", [])
                )

            else:
                logging.error(
                    "[BOT %s] HTTP %s: %s",
                    source.upper(),
                    me["status"],
                    me["data"]
                )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT
    )
