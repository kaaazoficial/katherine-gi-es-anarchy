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
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import psutil
except ImportError:
    psutil = None
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
if len(PREFIX) > 3 or any(ch.isspace() for ch in PREFIX):
    PREFIX = "!"
ADMIN_UIDS = {x.strip() for x in os.getenv("ADMIN_UIDS", "").split(",") if x.strip()}
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
        db.execute("""CREATE TABLE IF NOT EXISTS wish_state (
            uid TEXT PRIMARY KEY,
            pity5 INTEGER NOT NULL DEFAULT 0,
            pity4 INTEGER NOT NULL DEFAULT 0,
            guaranteed INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_gifts_target ON gifts(target_uid, id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_activity ON users(message_count DESC)")


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
    allowed = {"bio", "status", "genshin_uid"}
    if field not in allowed:
        return
    with db_session() as db:
        db.execute(f"UPDATE users SET {field}=? WHERE uid=?", (value, uid))


def add_points(uid: str, amount: int):
    with db_session() as db:
        db.execute("UPDATE users SET points=points+? WHERE uid=?", (int(amount), uid))


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
poll_button_map: dict[str, str] = {}
enka_cache: dict[str, tuple[float, Any]] = {}

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
    for store in (cache, enka_cache):
        for key, item in list(store.items()):
            if not isinstance(item, tuple) or len(item) < 2 or item[0] <= now:
                store.pop(key, None)
    for store in (trivia, number_games, polls):
        for key, item in list(store.items()):
            if item.get("expires", now + 1) <= now:
                store.pop(key, None)
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


async def build_status_message(source: str, nickname: str) -> str:
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
        f"• Tareas activas: {len(background_tasks)}",
        f"• Partidas activas: {len(trivia) + len(number_games)}",
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
        f"• Caché Enka: {len(enka_cache)} entradas",
        "",
        "🔐 CONFIGURACIÓN",
        f"• Python: {sys.version.split()[0]} | PID: {os.getpid()}",
        f"• Token desarrollador: {'Configurado' if BOT_TOKEN else 'No configurado'}",
        f"• Token comunidad: {'Configurado' if COMMUNITY_TOKEN else 'No configurado'}",
        f"• Chats vinculados al token: {len(thread_ids)}",
        "Los contadores corresponden a este proceso desde su último reinicio."
    ]
    return "\n".join(lines)[:1950]



def clean_text(value: Any) -> str:
    return re.sub(r"[`*_]", "", str(value or "")).strip()


async def external_request(method: str, url: str, **kwargs):
    """Reutiliza conexiones HTTP durante la vida del proceso."""
    if HTTP_CLIENT is not None:
        return await HTTP_CLIENT.request(method, url, **kwargs)
    async with httpx.AsyncClient(timeout=20) as temporary_client:
        return await temporary_client.request(method, url, **kwargs)



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


def split_message_content(content: str, limit: int = 500) -> list[str]:
    """Divide mensajes en bloques de hasta 'limit' caracteres."""
    text = clean_text(content)
    if not text:
        return []

    chunks = []
    while len(text) > limit:
        newline_at = text.rfind("\n", 0, limit + 1)
        space_at = text.rfind(" ", 0, limit + 1)
        cut = max(newline_at, space_at)

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

    parts = split_message_content(content, 500)
    if not parts:
        return None

    if buttons:
        buttons = [
            {
                "id": str(b["id"])[:32],
                "label": str(b["label"])[:40]
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
        r = await external_request(
            "GET", f"{GAME_API}/{folder}", params=params, timeout=18
        )
        r.raise_for_status()
        data = r.json()

        if isinstance(data, dict):
            data = data.get("data", data.get("result", data))

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
        ("title", "Título"),
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
        response = await external_request(
            "GET", f"{GAME_API}/stats", params=params, timeout=18
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
        response = await external_request(
            "GET", url, headers=headers, timeout=20
        )

        if response.status_code != 200:
            return None, response.status_code

        data = response.json()
        ttl = data.get("ttl", 300) if isinstance(data, dict) else 300

        try:
            ttl = max(60, min(int(ttl), 3600))
        except (ValueError, TypeError):
            ttl = 300

        enka_cache[cache_key] = (time.time() + ttl, data)
        if len(enka_cache) > 300:
            cleanup_expired_state()
            while len(enka_cache) > 300:
                enka_cache.pop(next(iter(enka_cache)))
        return data, None

    except Exception as exc:
        logging.warning("Enka UID %s: %s", uid, exc)
        return None, 500


def format_number(value):
    try:
        return f"{float(value):,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return "—"


def get_level90(stats):
    if not isinstance(stats, dict):
        return None

    return stats.get("90") or stats.get("90+") or stats.get("89")


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
    )
}


# ---------------------------------------------------------------------------
# GESTOR PRINCIPAL DE COMANDOS
# ---------------------------------------------------------------------------
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
    raw_uid = str(author.get("uid") or author.get("nickname") or "unknown")
    scope_key = f"{source}:{thread}"
    uid = scoped_uid(scope_key, raw_uid)
    game_key = scope_key
    nickname = author.get("nickname") or "Viajero"
    message_id = msg.get("messageId")

    record_user(uid, nickname, count_message=True)

    prefix_candidates = sorted({PREFIX, "!", "-"}, key=len, reverse=True)
    used_prefix = next((p for p in prefix_candidates if content.startswith(p)), None)
    if not used_prefix:
        return

    metrics["commands_received"] += 1
    pieces = content.split()
    command_token = pieces[0]
    if not command_token.startswith(used_prefix) or len(command_token) <= len(used_prefix):
        return
    command = "!" + command_token[len(used_prefix):].lower()
    args = pieces[1:]
    query = " ".join(args).strip()
    reply = message_id
    LAST_COMMAND_AT = time.time()
    LAST_COMMAND_NAME = command
    LAST_COMMAND_USER = nickname

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
        help_lines = [
            "KATHERINE — AYUDA COMPLETA",
            f"Prefijo principal: {PREFIX} (también se reconocen ! y -).",
            "",
            "GENSHIN IMPACT",
            f"{PREFIX}personaje <nombre> / {PREFIX}ficha-personaje <nombre> — ficha general del personaje.",
            f"{PREFIX}estadisticas <nombre> / {PREFIX}statspersonaje <nombre> — estadísticas base al nivel 90.",
            f"{PREFIX}talentos <nombre> — talentos de combate del personaje.",
            f"{PREFIX}pasivas <nombre> — habilidades pasivas del personaje.",
            f"{PREFIX}materiales <nombre> / {PREFIX}ascension <nombre> — materiales para ascenderlo.",
            f"{PREFIX}comparar <personaje 1> | <personaje 2> — compara dos personajes.",
            f"{PREFIX}equipos <personaje> — recomienda un equipo predefinido.",
            f"{PREFIX}rotacion <personaje> — explica la rotación sugerida del equipo.",
            f"{PREFIX}calculadora cv <CR> <CD> — calcula el valor crítico de artefactos.",
            f"{PREFIX}calculadora critico <CR> <CD> — calcula la relación entre Prob. y Daño CRIT.",
            f"{PREFIX}build <UID> [personaje] — consulta la build pública de Enka Network.",
            f"{PREFIX}arma <nombre> — consulta información de armas.",
            f"{PREFIX}artefacto <nombre> — consulta información de artefactos.",
            f"{PREFIX}dominio <nombre> — consulta información de dominios.",
            f"{PREFIX}enemigo <nombre> — consulta información de enemigos.",
            f"{PREFIX}comida <nombre> — consulta platos y comida.",
            f"{PREFIX}material <nombre> — consulta materiales del juego.",
            f"{PREFIX}logro <nombre> — busca información de logros.",
            f"{PREFIX}talento <nombre> — busca materiales/libros de talento.",
            f"{PREFIX}constelacion <personaje> — muestra las constelaciones.",
            f"{PREFIX}reaccion <elemento 1> <elemento 2> — explica la reacción elemental.",
            f"{PREFIX}genshinuid <UID> — vincula tu cuenta; usa 'borrar' para desvincularla.",
            f"{PREFIX}ficha [UID] — muestra la información pública del perfil de Genshin.",
            f"{PREFIX}abismo [UID] — consulta los datos públicos del Abismo.",
            f"{PREFIX}curiosidad — muestra un dato curioso de Genshin.",
            f"{PREFIX}deseo — simula un deseo del banner y guarda el pity.",
            f"{PREFIX}pity — consulta tus contadores y garantía del simulador.",
            f"{PREFIX}resetpity — reinicia el pity del simulador.",
            "",
            "SOCIAL Y PERFILES",
            f"{PREFIX}perfil [@usuario] — muestra tu perfil o el de otra persona.",
            f"{PREFIX}bio <texto> — cambia la biografía de tu perfil.",
            f"{PREFIX}miestado <texto> — actualiza tu estado personal.",
            f"{PREFIX}afinidad @usuario / {PREFIX}amistad @usuario — consulta la afinidad entre usuarios.",
            f"{PREFIX}regalo @usuario [mensaje] — envía un regalo simbólico.",
            f"{PREFIX}muro — muestra los regalos recibidos.",
            f"{PREFIX}actividad — muestra el ranking de actividad del chat.",
            f"{PREFIX}roast @usuario — lanza una broma amistosa sobre alguien.",
            "",
            "INTERACCIONES",
            f"{PREFIX}abrazo / {PREFIX}abrazar @usuario — da un abrazo.",
            f"{PREFIX}beso / {PREFIX}besar @usuario — da un beso.",
            f"{PREFIX}patada @usuario — da una patada de broma.",
            f"{PREFIX}golpe @usuario — golpea de forma ficticia.",
            f"{PREFIX}cachetada @usuario — da una cachetada de broma.",
            f"{PREFIX}acariciar @usuario — acaricia a alguien.",
            f"{PREFIX}mimar @usuario — trata con cariño a alguien.",
            f"{PREFIX}morder @usuario — muerde de forma ficticia.",
            f"{PREFIX}consolar @usuario — ofrece consuelo.",
            f"{PREFIX}felicitar @usuario — felicita a alguien.",
            f"{PREFIX}saludar @usuario — saluda a alguien.",
            f"{PREFIX}proteger @usuario — protege a alguien.",
            f"{PREFIX}bailar @usuario — baila con alguien.",
            f"{PREFIX}cosquillas @usuario — hace cosquillas.",
            f"{PREFIX}chocar / {PREFIX}highfive @usuario — choca los cinco.",
            f"{PREFIX}ayudar @usuario — ayuda a alguien.",
            f"{PREFIX}regalar @usuario — hace un regalo simbólico.",
            f"{PREFIX}acompañar @usuario — acompaña a alguien.",
            f"{PREFIX}asustar @usuario — intenta asustar.",
            f"{PREFIX}curar @usuario — cura de forma ficticia.",
            f"{PREFIX}cargar @usuario — carga a alguien.",
            f"{PREFIX}empujar @usuario — empuja de forma ficticia.",
            f"{PREFIX}regañar @usuario — regaña a alguien.",
            f"{PREFIX}admirar @usuario — expresa admiración.",
            f"{PREFIX}apoyar @usuario — muestra apoyo.",
            f"{PREFIX}dormir @usuario — duerme junto a alguien.",
            f"{PREFIX}ignorar @usuario — ignora de forma ficticia.",
            f"{PREFIX}mirar @usuario — mira a alguien.",
            f"{PREFIX}invitar @usuario — invita a alguien.",
            "",
            "ENTRETENIMIENTO Y JUEGOS",
            f"{PREFIX}trivia — inicia una pregunta de trivia.",
            f"{PREFIX}responder <1-4> — responde la trivia activa.",
            f"{PREFIX}adivina — inicia el juego de adivinar un número.",
            f"{PREFIX}intento <número> — prueba un número del 1 al 100.",
            f"{PREFIX}puntos / {PREFIX}reputacion — consulta tus puntos.",
            f"{PREFIX}ranking / {PREFIX}top — muestra el ranking de puntos.",
            f"{PREFIX}encuesta <pregunta> | <opción 1> | <opción 2> — crea una encuesta con botones.",
            f"{PREFIX}elige <opción 1> | <opción 2> — elige al azar entre opciones.",
            f"{PREFIX}duelo @usuario — reta a otra persona.",
            f"{PREFIX}ship <usuario 1> <usuario 2> — calcula compatibilidad ficticia.",
            f"{PREFIX}dado — lanza un dado.",
            f"{PREFIX}moneda — lanza una moneda.",
            f"{PREFIX}azar <mínimo> <máximo> — genera un número aleatorio.",
            f"{PREFIX}verdad — entrega una pregunta de verdad.",
            f"{PREFIX}8ball <pregunta> — responde como bola mágica.",
            f"{PREFIX}chiste — cuenta un chiste.",
            f"{PREFIX}frase — muestra una frase aleatoria.",
            f"{PREFIX}reto — propone un reto de Genshin.",
            f"{PREFIX}suerte — entrega un mensaje de suerte.",
            "",
            "SISTEMA Y KATHERINE",
            f"{PREFIX}estado / {PREFIX}diagnostico / {PREFIX}statsbot — muestra estado, recursos y actividad del bot.",
            f"{PREFIX}ping — comprueba conexión y latencia de la API.",
            f"{PREFIX}latencia — mide el tiempo de respuesta de Anarchy.",
            f"{PREFIX}memoria — muestra uso de RAM y CPU del proceso.",
            f"{PREFIX}uptime — muestra cuánto lleva activa Katherine.",
            f"{PREFIX}fecha — muestra fecha y hora de Chile.",
            f"{PREFIX}zona <Area/Ciudad> — muestra la hora de una zona horaria.",
            f"{PREFIX}salas — consulta las salas en vivo de comunidad.",
            f"{PREFIX}perfilbot <Nombre> | <Estado> — modifica el perfil del bot (solo administradores).",
            "",
            "Los mensajes de Katherine se envían en bloques de máximo 500 caracteres."
        ]

        await send_message(
            thread,
            "\n".join(help_lines),
            source,
            reply
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
            f"UID de Genshin: {user['genshin_uid'] or 'No vinculado'}",
            f"Puntos: {user['points']} | Mensajes vistos: {user['message_count']}"
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

        if player.get("level") is not None:
            lines.append(f"Rango de aventura: {player.get('level')}")

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
        top = get_top_users("message_count", 10, scope_key)

        answer = "ACTIVIDAD DEL CHAT\n\n" + (
            "\n".join(
                f"{i}. {name} — {count} mensajes vistos"
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

            if base.get("specialized") is not None:
                answer += (
                    f"\nAtributo de ascensión: "
                    f"{float(base['specialized'])*100:.1f}% (si es porcentual)"
                )

        answer += (
            "\n\nUsa !materiales "
            + str(data.get("name") or query)
            + " para ver la ascensión."
        )

        await send_message(thread, answer[:1950], source, reply)
        return

    if command in ("!estadisticas", "!statspersonaje"):
        if not query:
            await send_message(thread, "Uso: !estadisticas nombre", source, reply)
            return
        data, stats = await asyncio.gather(
            get_character(query), character_stats(query)
        )
        base = get_level90(stats)
        if not isinstance(data, dict) or not isinstance(base, dict):
            await send_message(
                thread, f"No pude obtener las estadísticas de {query}.",
                source, reply
            )
            return
        answer = (
            f"ESTADÍSTICAS BASE — {data.get('name', query)} · NIVEL 90\n"
            f"Vida: {format_number(base.get('hp'))}\n"
            f"ATQ: {format_number(base.get('attack'))}\n"
            f"DEF: {format_number(base.get('defense'))}\n"
            f"Atributo de ascensión: {base.get('specialized', 'No informado')}\n"
            "Valores base; no incluyen armas, artefactos ni bonificaciones."
        )
        await send_message(thread, answer, source, reply)
        return

    if command in ("!talentos", "!pasivas"):
        if not query:
            await send_message(thread, f"Uso: {command} nombre del personaje", source, reply)
            return
        data = await get_character(query)
        if not isinstance(data, dict):
            await send_message(thread, f"No encontré a {query}.", source, reply)
            return
        if command == "!talentos":
            entries = data.get("skillTalents") or data.get("talents") or []
            heading = "TALENTOS DE COMBATE"
        else:
            entries = data.get("passiveTalents") or []
            heading = "TALENTOS PASIVOS"
        if isinstance(entries, dict):
            entries = list(entries.values())
        if not isinstance(entries, list) or not entries:
            await send_message(
                thread, "La base de datos no entregó esa información.",
                source, reply
            )
            return
        lines = [f"{heading} — {data.get('name', query)}"]
        for item in entries[:6]:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("unlock") or "Talento"
            desc = re.sub(r"<[^>]+>", "", str(item.get("description") or ""))
            lines.append(f"\n{name}\n{desc[:350]}")
        await send_message(thread, "\n".join(lines)[:1900], source, reply)
        return

    # -----------------------------------------------------------------------
    # GENSHIN IMPACT: MATERIALES DE ASCENSIÓN
    # -----------------------------------------------------------------------
    if command in ("!materiales", "!ascension"):
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
            f"{team[1]}\n\n"
            "Ajusta el orden según energía, duración de habilidades y enemigos.",
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

            if not (0 <= first <= 1000 and 0 <= second <= 1000):
                raise ValueError

        except ValueError:
            await send_message(
                thread,
                "Introduce dos números válidos.",
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
            if first > 100 or second > 400:
                await send_message(
                    thread,
                    "Para critico usa CR entre 0 y 100 y CD entre 0 y 400.",
                    source,
                    reply
                )
                return

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

        avatar = next(
            (
                x for x in avatars
                if str(x.get("avatarId") or x.get("avatarID")) == str(avatar_id)
            ),
            None
        )

        if not avatar:
            await send_message(
                thread,
                f"{character.get('name', query)} no aparece en el escaparate "
                "público de ese UID. Añádelo al escaparate dentro del juego y vuelve a intentar.",
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

            refinement_value = int(
                next(
                    iter(
                        (weapon_data.get("affixMap") or {"1": 0}).values()
                    )
                )
            ) + 1

            weapon_name = (weapon.get("flat") or {}).get("nameText") or "Arma equipada"
            weapon_rarity = (weapon.get("flat") or {}).get("rankLevel")
            lines.append(
                f"Arma: {weapon_name} | nivel {weapon_data.get('level', '?')} "
                f"| R{refinement_value}"
                + (f" | {weapon_rarity}★" if weapon_rarity else "")
            )

        artifact_slots = {
            "EQUIP_BRACER": "Flor",
            "EQUIP_NECKLACE": "Pluma",
            "EQUIP_SHOES": "Reloj",
            "EQUIP_RING": "Copa",
            "EQUIP_DRESS": "Corona"
        }

        for item in equip:
            flat = item.get("flat") or {}

            if flat.get("itemType") != "ITEM_RELIQUARY":
                continue

            main = flat.get("reliquaryMainstat") or {}

            prop = str(
                main.get("mainPropId")
                or main.get("propType")
                or "Estadística"
            )

            val = main.get("statValue", main.get("propValue"))
            slot = artifact_slots.get(flat.get("equipType"), "Artefacto")

            if val is not None:
                main_text = (
                    f"{prop.replace('FIGHT_PROP_', '').replace('_', ' ')} "
                    f"{float(val):g}"
                )
            else:
                main_text = "Principal no disponible"

            level_info = (item.get("reliquary") or {}).get("level")
            substats = flat.get("reliquarySubstats") or []
            sub_text = []
            for sub in substats[:4]:
                if not isinstance(sub, dict):
                    continue
                sub_name = str(sub.get("appendPropId") or sub.get("propType") or "Substat")
                sub_value = sub.get("statValue", sub.get("value"))
                if sub_value is not None:
                    sub_text.append(
                        f"{sub_name.replace('FIGHT_PROP_', '').replace('_', ' ')} {float(sub_value):g}"
                    )
            set_name = flat.get("setNameText")
            artifact_line = f"{slot}: {main_text}"
            if level_info is not None:
                artifact_line += f" (+{level_info})"
            if sub_text:
                artifact_line += " | " + ", ".join(sub_text)
            if set_name:
                artifact_line += f" | {set_name}"
            lines.append(artifact_line)

        lines.append(f"Piezas equipadas detectadas: {len(equip)}")
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
            frozenset(("pyro", "electro")): "Sobrecarga",
            frozenset(("cryo", "electro")): "Superconductor",
            frozenset(("electro", "hydro")): "Electrocargado",
            frozenset(("cryo", "hydro")): "Congelado",
            frozenset(("dendro", "hydro")): "Florecimiento",
            frozenset(("dendro", "pyro")): "Quemadura",
            frozenset(("dendro", "electro")): "Aceleración (Quicken; permite Agravado y Propagación)",
            frozenset(("geo", "pyro")): "Cristalización",
            frozenset(("geo", "hydro")): "Cristalización",
            frozenset(("geo", "electro")): "Cristalización",
            frozenset(("geo", "cryo")): "Cristalización",
            frozenset(("anemo", "pyro")): "Dispersión",
            frozenset(("anemo", "hydro")): "Dispersión",
            frozenset(("anemo", "cryo")): "Dispersión",
            frozenset(("anemo", "electro")): "Dispersión",
            frozenset(("anemo", "dendro")): "Anemo no dispersa Dendro",
            frozenset(("anemo", "geo")): "No produce una reacción elemental directa"
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

        trivia[game_key] = {
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
    if command in ("!estado", "!diagnostico", "!statsbot"):
        answer = await build_status_message(source, nickname)
        await send_message(thread, answer, source, reply)
        return

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

    if command == "!latencia":
        started = time.perf_counter()
        result = await api("GET", "/me", source)
        elapsed = (time.perf_counter() - started) * 1000
        state = "🟢 API disponible" if result["ok"] else "🔴 API no disponible"
        await send_message(
            thread,
            f"{state}\nLatencia de Anarchy: {elapsed:.0f} ms\nHTTP: {result['status']}",
            source, reply
        )
        return

    if command == "!memoria":
        current, peak = get_process_memory()
        if current is None:
            answer = "No pude leer la memoria RSS del proceso en este entorno."
        else:
            answer = f"RAM del proceso: {current:.1f} MB RSS"
            answer += f"\nRAM del contenedor: {get_container_memory_usage()}"
            if peak is not None:
                answer += f"\nMáximo registrado: {peak:.1f} MB"
            if psutil and PROCESS:
                try:
                    answer += f"\nCPU del proceso: {PROCESS.cpu_percent(interval=None):.1f}%"
                except psutil.Error:
                    pass
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

        if not ADMIN_UIDS or raw_uid not in ADMIN_UIDS:
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

    if not poll:
        return

    if poll.get("source") != source or str(event.get("threadId") or "") != poll.get("thread"):
        return

    index = int(match.group(2))

    if index >= len(poll["options"]):
        return

    user = str(
        (event.get("user") or {}).get("uid")
        or "unknown"
    )

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
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


async def webhook(request: Request, source: str):
    global LAST_EVENT_AT
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

    # Responder rápido a Anarchy; el procesamiento continúa en segundo plano
    # dentro del mismo proceso de la aplicación.
    task = asyncio.create_task(safe_dispatch(event, source))
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

            if me["ok"]:
                bot = me["data"].get("bot", {})

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


@app.on_event("shutdown")
async def shutdown():
    global HTTP_CLIENT, CLEANUP_TASK
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
