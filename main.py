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
        db.execute("""CREATE TABLE IF NOT EXISTS saved_builds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            build_name TEXT NOT NULL,
            character_name TEXT NOT NULL,
            weapon_name TEXT NOT NULL DEFAULT '',
            artifact_set TEXT NOT NULL DEFAULT '',
            main_stats TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS saved_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            team_name TEXT NOT NULL,
            members_json TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            UNIQUE(uid, team_name)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS farm_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            item_name TEXT NOT NULL,
            current_amount INTEGER NOT NULL DEFAULT 0,
            target_amount INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            UNIQUE(uid, item_name)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS exploration_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            region_name TEXT NOT NULL,
            percentage REAL NOT NULL DEFAULT 0,
            chest_count INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL,
            UNIQUE(uid, region_name)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS genshin_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            note_title TEXT NOT NULL,
            note_body TEXT NOT NULL,
            updated_at REAL NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_saved_builds_uid ON saved_builds(uid,id DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_saved_teams_uid ON saved_teams(uid,team_name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_farm_materials_uid ON farm_materials(uid,item_name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_exploration_uid ON exploration_progress(uid,region_name)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_genshin_notes_uid ON genshin_notes(uid,id DESC)")
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
    for store in (cache,):
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
        "",
        "🔐 CONFIGURACIÓN",
        f"• Python: {sys.version.split()[0]} | PID: {os.getpid()}",
        f"• Token desarrollador: {'Configurado' if BOT_TOKEN else 'No configurado'}",
        f"• Token comunidad: {'Configurado' if COMMUNITY_TOKEN else 'No configurado'}",
        f"• Chats vinculados al token: {len(thread_ids)}",
        "Los contadores corresponden a este proceso desde su último reinicio."
    ]
    return "\n".join(lines)



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

    parts = split_message_content(content, 2000)
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


async def weapon_stats(query: str):
    return await game_stats("weapons", query)


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


def describe_cost_stages(costs: Any, title: str) -> str:
    """Expone las fases de ascensión y añade el total agregado."""
    if not isinstance(costs, dict):
        return f"{title}\nLa base de datos no publicó un desglose de materiales."

    lines = [title]
    for stage, entries in costs.items():
        if not isinstance(entries, list):
            continue
        rendered = game_items_text(entries, limit=30)
        if rendered:
            label = re.sub(r"([A-Z])", r" \1", str(stage))
            label = label.replace("_", " ").strip().capitalize()
            lines.append(f"\n{label}")
            lines.extend(f"• {item}" for item in rendered)

    totals = aggregate_material_costs(costs)
    if totals:
        lines.append("\nTOTALES")
        lines.extend(f"• {name}: {amount}" for name, amount in sorted(totals.items()))
    return "\n".join(lines)


def get_talent_entries(character: dict, field: str):
    entries = character.get(field) or []
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def describe_talent_guide(character: dict, fallback: str) -> str:
    name = clean_game_text(character.get("name") or fallback)
    lines = [f"GUÍA DE TALENTOS — {name}"]

    sections = (
        ("skillTalents", "TALENTOS DE COMBATE"),
        ("passiveTalents", "TALENTOS PASIVOS")
    )
    for field, heading in sections:
        entries = get_talent_entries(character, field)
        if not entries:
            continue
        lines.append(f"\n{heading}")
        for index, entry in enumerate(entries[:8], 1):
            title = clean_game_text(
                entry.get("name") or entry.get("unlock") or f"Talento {index}"
            )
            desc = clean_game_text(entry.get("description") or "")
            lines.append(f"\n{index}. {title}")
            if desc:
                lines.append(game_excerpt(desc, 650))
            if entry.get("cooldown") is not None:
                lines.append(f"Tiempo de reutilización: {entry.get('cooldown')}")
            if entry.get("cost") is not None:
                lines.append(f"Costo: {clean_game_text(entry.get('cost'))}")

    constellations = character.get("constellations") or character.get("Constellations")
    if isinstance(constellations, dict):
        lines.append("\nCONSTELACIONES")
        for index, entry in enumerate(list(constellations.values())[:6], 1):
            if not isinstance(entry, dict):
                continue
            title = clean_game_text(entry.get("name") or f"C{index}")
            desc = clean_game_text(entry.get("description") or "")
            lines.append(f"\nC{index} · {title}")
            if desc:
                lines.append(game_excerpt(desc, 420))

    return "\n".join(lines)


def parse_percent(value: str) -> float:
    return float(str(value).replace("%", "").replace(",", "."))


# ---------------------------------------------------------------------------
# SISTEMAS PERSONALES PERSISTENTES DE GENSHIN
# ---------------------------------------------------------------------------
def save_build_record(uid, build_name, character_name, weapon_name="", artifact_set="", main_stats="", notes=""):
    with db_session() as db:
        cursor = db.execute(
            """INSERT INTO saved_builds(
                uid,build_name,character_name,weapon_name,artifact_set,
                main_stats,notes,created_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (uid, build_name[:50], character_name[:80], weapon_name[:100],
             artifact_set[:120], main_stats[:180], notes[:500], time.time())
        )
        return int(cursor.lastrowid)


def list_build_records(uid, character_name=None, limit=20):
    query = "SELECT * FROM saved_builds WHERE uid=?"
    params = [uid]
    if character_name:
        query += " AND lower(character_name)=lower(?)"
        params.append(character_name)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(50, int(limit))))
    with db_session() as db:
        return [dict(row) for row in db.execute(query, tuple(params)).fetchall()]


def get_build_record(uid, record_id):
    with db_session() as db:
        row = db.execute(
            "SELECT * FROM saved_builds WHERE uid=? AND id=?",
            (uid, int(record_id))
        ).fetchone()
        return dict(row) if row else None


def delete_build_record(uid, record_id):
    with db_session() as db:
        cursor = db.execute(
            "DELETE FROM saved_builds WHERE uid=? AND id=?",
            (uid, int(record_id))
        )
        return cursor.rowcount > 0


def update_build_record(uid, record_id, field, value):
    allowed = {
        "nombre": ("build_name", 50),
        "personaje": ("character_name", 80),
        "arma": ("weapon_name", 100),
        "set": ("artifact_set", 120),
        "stats": ("main_stats", 180),
        "nota": ("notes", 500)
    }
    column = allowed.get(str(field).casefold())
    if not column:
        raise ValueError("Campo válido: nombre, personaje, arma, set, stats o nota.")
    column_name, max_length = column
    with db_session() as db:
        cursor = db.execute(
            f"UPDATE saved_builds SET {column_name}=? WHERE uid=? AND id=?",
            (str(value)[:max_length], uid, int(record_id))
        )
        return cursor.rowcount > 0


def save_team_record(uid, name, members, notes=""):
    safe_members = [str(value).strip()[:80] for value in members if str(value).strip()]
    if not 1 <= len(safe_members) <= 4:
        raise ValueError("Un equipo debe tener entre 1 y 4 personajes.")
    with db_session() as db:
        db.execute(
            """INSERT INTO saved_teams(uid,team_name,members_json,notes,created_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(uid,team_name) DO UPDATE SET
               members_json=excluded.members_json,
               notes=excluded.notes,
               created_at=excluded.created_at""",
            (uid, name.strip()[:50], json.dumps(safe_members, ensure_ascii=False),
             notes[:300], time.time())
        )
    return safe_members


def list_team_records(uid):
    with db_session() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM saved_teams WHERE uid=? ORDER BY team_name COLLATE NOCASE",
            (uid,)
        ).fetchall()]


def get_team_record(uid, name):
    with db_session() as db:
        row = db.execute(
            "SELECT * FROM saved_teams WHERE uid=? AND lower(team_name)=lower(?)",
            (uid, name)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["members"] = json.loads(data.pop("members_json"))
        except (ValueError, TypeError):
            data["members"] = []
        return data


def delete_team_record(uid, name):
    with db_session() as db:
        cursor = db.execute(
            "DELETE FROM saved_teams WHERE uid=? AND lower(team_name)=lower(?)",
            (uid, name)
        )
        return cursor.rowcount > 0


def upsert_farm_material(uid, item_name, current, target):
    if not item_name.strip():
        raise ValueError("El material necesita un nombre.")
    if not 0 <= current <= 10_000_000 or not 1 <= target <= 10_000_000:
        raise ValueError("Las cantidades deben ser positivas y razonables.")
    with db_session() as db:
        db.execute(
            """INSERT INTO farm_materials(uid,item_name,current_amount,target_amount,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(uid,item_name) DO UPDATE SET
               current_amount=excluded.current_amount,
               target_amount=excluded.target_amount,
               updated_at=excluded.updated_at""",
            (uid, item_name.strip()[:100], current, target, time.time())
        )


def adjust_farm_material(uid, item_name, delta):
    with db_session() as db:
        row = db.execute(
            """SELECT current_amount,target_amount FROM farm_materials
               WHERE uid=? AND lower(item_name)=lower(?)""",
            (uid, item_name)
        ).fetchone()
        if not row:
            return None
        current = max(0, int(row["current_amount"]) + int(delta))
        db.execute(
            """UPDATE farm_materials SET current_amount=?,updated_at=?
               WHERE uid=? AND lower(item_name)=lower(?)""",
            (current, time.time(), uid, item_name)
        )
        return current, int(row["target_amount"])


def list_farm_materials(uid):
    with db_session() as db:
        return [dict(row) for row in db.execute(
            """SELECT item_name,current_amount,target_amount,updated_at
               FROM farm_materials WHERE uid=?
               ORDER BY (target_amount-current_amount) DESC,item_name COLLATE NOCASE""",
            (uid,)
        ).fetchall()]


def delete_farm_material(uid, item_name):
    with db_session() as db:
        cursor = db.execute(
            "DELETE FROM farm_materials WHERE uid=? AND lower(item_name)=lower(?)",
            (uid, item_name)
        )
        return cursor.rowcount > 0


def upsert_exploration(uid, region, percentage, chests=0, note=""):
    if not 0 <= percentage <= 100:
        raise ValueError("La exploración debe estar entre 0 y 100.")
    if not 0 <= chests <= 100_000:
        raise ValueError("La cantidad de cofres no es válida.")
    with db_session() as db:
        db.execute(
            """INSERT INTO exploration_progress(
                uid,region_name,percentage,chest_count,note,updated_at
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(uid,region_name) DO UPDATE SET
            percentage=excluded.percentage,
            chest_count=excluded.chest_count,
            note=excluded.note,
            updated_at=excluded.updated_at""",
            (uid, region[:80], percentage, chests, note[:300], time.time())
        )


def list_exploration(uid):
    with db_session() as db:
        return [dict(row) for row in db.execute(
            """SELECT region_name,percentage,chest_count,note,updated_at
               FROM exploration_progress WHERE uid=?
               ORDER BY percentage ASC,region_name COLLATE NOCASE""",
            (uid,)
        ).fetchall()]


def save_genshin_note(uid, title, body):
    with db_session() as db:
        cursor = db.execute(
            """INSERT INTO genshin_notes(uid,note_title,note_body,updated_at)
               VALUES(?,?,?,?)""",
            (uid, title[:80], body[:1500], time.time())
        )
        return int(cursor.lastrowid)


def list_genshin_notes(uid, limit=20):
    with db_session() as db:
        return [dict(row) for row in db.execute(
            """SELECT id,note_title,note_body,updated_at
               FROM genshin_notes WHERE uid=? ORDER BY id DESC LIMIT ?""",
            (uid, max(1, min(50, int(limit))))
        ).fetchall()]


def delete_genshin_note(uid, note_id):
    with db_session() as db:
        cursor = db.execute(
            "DELETE FROM genshin_notes WHERE uid=? AND id=?",
            (uid, int(note_id))
        )
        return cursor.rowcount > 0


def build_database_summary(uid):
    return {
        "builds": len(list_build_records(uid, limit=50)),
        "teams": len(list_team_records(uid)),
        "farm_items": len(list_farm_materials(uid)),
        "exploration_regions": len(list_exploration(uid)),
        "notes": len(list_genshin_notes(uid, limit=50))
    }


# ---------------------------------------------------------------------------
# FIN DE LOS SISTEMAS PERSONALES DE GENSHIN
# ---------------------------------------------------------------------------


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
            f"{PREFIX}ayuda / {PREFIX}comandos — muestra esta lista de comandos.",
            f"Prefijo principal: {PREFIX} (también se reconocen ! y -).",
            "",
            "GENSHIN IMPACT",
            f"{PREFIX}guia <personaje> — ficha, estadísticas base y nombres de talentos.",
            f"{PREFIX}talentoguia <personaje> — explica habilidades, pasivas y constelaciones.",
            f"{PREFIX}statsnivel <personaje|arma> <nombre> <nivel> — estadísticas a nivel concreto, como 80+.",
            f"{PREFIX}farm <personaje> — reúne ficha, estadísticas y materiales de ascensión.",
            f"{PREFIX}farmmeta <material> | <actual> | <objetivo> — lleva un inventario personal de materiales.",
            f"{PREFIX}materialesdetalle <personaje> — desglose por fase y totales de ascensión.",
            f"{PREFIX}drops <enemigo> / {PREFIX}recompensas <enemigo> — muestra las recompensas registradas.",
            f"{PREFIX}artefactos <personaje> — recomienda sets, estadísticas principales y subestadísticas.",
            f"{PREFIX}mejorarma <personaje> — lista armas recomendadas y alternativas.",
            f"{PREFIX}prioridadtalentos <personaje> — indica el orden recomendado de talentos.",
            f"{PREFIX}recomendacion <personaje> — reúne arma, artefactos, talentos y equipo.",
            f"{PREFIX}comparararmas <arma 1> | <arma 2> — compara estadísticas base y pasivas.",
            f"{PREFIX}compararartefactos <set 1> | <set 2> — compara bonificaciones de conjuntos.",
            f"{PREFIX}buildeditar <id> | <campo> | <valor> — edita una build guardada.",
            f"{PREFIX}daño <ATQ> <multiplicador%> <bono%> <CR> <CD> — estima daño crítico y promedio.",
            f"{PREFIX}cv <CR> <CD> — calcula el valor crítico de un artefacto.",
            f"{PREFIX}reacciones <elemento> — muestra reacciones relacionadas con un elemento.",
            f"{PREFIX}buildguardar nombre | personaje | arma | set | stats | nota — guarda una build personal.",
            f"{PREFIX}misbuilds [personaje] — lista tus builds guardadas.",
            f"{PREFIX}buildlocal <id> — muestra una build guardada.",
            f"{PREFIX}buildborrar <id> — elimina una build tuya.",
            f"{PREFIX}equipo guardar <nombre> | <personajes> | <nota> — guarda un equipo.",
            f"{PREFIX}equipo ver <nombre> / {PREFIX}equipo borrar <nombre> — consulta o elimina un equipo.",
            f"{PREFIX}equiposguardados — lista tus equipos guardados.",
            f"{PREFIX}farmmeta material | actual | objetivo — registra materiales por conseguir.",
            f"{PREFIX}farmlista — muestra tu lista de materiales pendientes.",
            f"{PREFIX}farmprogreso material cantidad — actualiza tu inventario.",
            f"{PREFIX}farmquitar material — elimina un material de tu lista.",
            f"{PREFIX}exploracion región | porcentaje | cofres — guarda progreso de exploración.",
            f"{PREFIX}mapaprogreso — muestra el progreso de tus regiones.",
            f"{PREFIX}notagenshin título | texto — guarda una nota personal.",
            f"{PREFIX}misnotas / {PREFIX}borrarnota id — consulta o elimina notas.",
            f"{PREFIX}resumengen — resume tus datos guardados de Genshin.",
            f"{PREFIX}buscargenshin <texto> — busca a la vez en varias categorías del juego.",
            f"{PREFIX}lista <categoría> [página] — lista nombres de personajes, armas, enemigos y más.",
            f"{PREFIX}statsarma <arma> — estadísticas del arma al nivel 90.",
            f"{PREFIX}materialesarma <arma> — materiales para ascender un arma.",
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
            f"{PREFIX}receta <nombre> — busca platos (alias de comida).",
            f"{PREFIX}jefe <nombre> — busca jefes y enemigos.",
            f"{PREFIX}animal <nombre> — consulta animales y fauna.",
            f"{PREFIX}tarjeta / {PREFIX}nombrecard <nombre> — consulta tarjetas de nombre.",
            f"{PREFIX}atuendo <nombre> — consulta atuendos de personajes.",
            f"{PREFIX}planeador <nombre> — consulta planeadores.",
            f"{PREFIX}fabricacion <nombre> — consulta recetas de fabricación.",
            f"{PREFIX}geografia <nombre> — consulta ubicaciones y geografía.",
            f"{PREFIX}elemento <nombre> — consulta información de elementos.",
            f"{PREFIX}rangoaventura <rango> — consulta rangos de aventura.",
            f"{PREFIX}grupologros <nombre> — consulta grupos de logros.",
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
            f"{PREFIX}autotest — ejecuta pruebas internas de mensajes, estadísticas y cálculos.",
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
            "Los mensajes de Katherine se envían en bloques de máximo 2000 caracteres."
        ]

        await send_message(
            thread,
            "\n".join(help_lines),
            source,
            reply
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

        await send_message(thread, answer, source, reply)
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
        await send_message(thread, "\n".join(lines), source, reply)
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
            "\n".join(lines),
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
            "\n".join(lines),
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
    # GUÍAS, ESTADÍSTICAS Y BÚSQUEDA AMPLIADA DE GENSHIN
    # -----------------------------------------------------------------------
    if command == "!statsarma":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}statsarma nombre del arma", source, reply)
            return

        weapon, stats = await asyncio.gather(
            game_data("weapons", query),
            weapon_stats(query)
        )
        base = get_level90(stats)

        if not isinstance(weapon, dict) or not isinstance(base, dict):
            await send_message(
                thread,
                f"No pude obtener la ficha y las estadísticas de «{query}».",
                source,
                reply
            )
            return

        answer = (
            f"ESTADÍSTICAS DEL ARMA — {weapon.get('name', query)}\n"
            f"Rareza: {weapon.get('rarity', '?')}★\n"
            f"Tipo: {weapon.get('weaponText', 'No disponible')}\n"
            f"Ataque base nivel 90: {format_number(base.get('attack'))}\n"
            f"Atributo de ascensión: {weapon.get('mainStatText') or 'Ninguno'}\n"
            f"Valor de ascensión nivel 90: {base.get('specialized', 'No disponible')}\n"
            "Valores de la base de datos; no incluyen refinamiento ni bonificaciones."
        )
        await send_message(thread, answer, source, reply)
        return

    if command == "!materialesarma":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}materialesarma nombre del arma", source, reply)
            return

        weapon = await game_data("weapons", query)
        costs = weapon.get("costs") if isinstance(weapon, dict) else None

        if not isinstance(costs, dict):
            await send_message(thread, f"No encontré los materiales de «{query}».", source, reply)
            return

        totals = defaultdict(int)
        for stage in costs.values():
            if isinstance(stage, list):
                for item in stage:
                    if isinstance(item, dict) and item.get("name"):
                        try:
                            totals[str(item["name"])] += int(item.get("count", 0))
                        except (TypeError, ValueError):
                            continue

        if not totals:
            await send_message(thread, "La API no devolvió materiales de ascensión para esa arma.", source, reply)
            return

        lines = [
            f"MATERIALES DEL ARMA — {weapon.get('name', query)}",
            "Totales para ascenderla al máximo:"
        ]
        lines.extend(f"• {name}: {amount}" for name, amount in totals.items())
        await send_message(thread, "\n".join(lines), source, reply)
        return

    if command == "!guia":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}guia nombre del personaje", source, reply)
            return

        character, stats = await asyncio.gather(
            get_character(query),
            character_stats(query)
        )
        if not isinstance(character, dict):
            await send_message(thread, f"No encontré al personaje «{query}».", source, reply)
            return

        parts = [describe_game(character, query, "characters")]
        base = get_level90(stats)
        if isinstance(base, dict):
            parts.append(
                "ESTADÍSTICAS BASE NIVEL 90\n"
                f"Vida: {format_number(base.get('hp'))} | "
                f"ATQ: {format_number(base.get('attack'))} | "
                f"DEF: {format_number(base.get('defense'))}"
            )

        talents = character.get("skillTalents") or character.get("talents") or []
        if isinstance(talents, dict):
            talents = list(talents.values())
        if isinstance(talents, list) and talents:
            names = [
                clean_game_text(x.get("name") or x.get("unlock") or "")
                for x in talents[:6] if isinstance(x, dict)
            ]
            names = [x for x in names if x]
            if names:
                parts.append("TALENTOS\n" + "\n".join(f"• {x}" for x in names))

        await send_message(thread, "\n\n".join(parts), source, reply)
        return

    # -----------------------------------------------------------------------
    # GENSHIN: HERRAMIENTAS AVANZADAS
    # -----------------------------------------------------------------------
    if command == "!statsnivel":
        if len(args) < 3:
            await send_message(
                thread,
                f"Uso: {PREFIX}statsnivel personaje Cyno 80+ o {PREFIX}statsnivel arma Staff of Homa 90",
                source,
                reply
            )
            return

        kind = args[0].casefold()
        level = args[-1].strip()
        target_name = " ".join(args[1:-1]).strip()
        folder = "characters" if kind in ("personaje", "personajes", "char") else (
            "weapons" if kind in ("arma", "armas", "weapon") else None
        )
        if folder is None or not target_name:
            await send_message(
                thread,
                "Indica personaje o arma, nombre y nivel. Ejemplo: !statsnivel personaje Cyno 80+",
                source,
                reply
            )
            return

        stats = await game_stats(folder, target_name)
        selected = get_level_stats(stats, level)
        if not selected:
            available = ", ".join(
                key for key in stats.keys()
                if re.fullmatch(r"\d{1,2}\+?", str(key))
            ) if isinstance(stats, dict) else ""
            await send_message(
                thread,
                f"No encontré el nivel {level} para {target_name}. "
                + (f"Niveles disponibles: {available}" if available else "Comprueba el nombre y el nivel."),
                source,
                reply
            )
            return

        details = [
            f"ESTADÍSTICAS — {target_name} · NIVEL {level}",
            f"Vida: {format_number(selected.get('hp'))}",
            f"ATQ: {format_number(selected.get('attack'))}",
            f"DEF: {format_number(selected.get('defense'))}"
        ]
        if selected.get("specialized") is not None:
            details.append(f"Atributo de ascensión: {selected.get('specialized')}")
        details.append("Valores base; no incluyen armas, artefactos, talentos ni bonificaciones.")
        await send_message(thread, "\n".join(details), source, reply)
        return

    if command == "!talentoguia":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}talentoguia nombre del personaje", source, reply)
            return
        character = await get_character(query)
        if not isinstance(character, dict):
            await send_message(thread, f"No encontré al personaje «{query}».", source, reply)
            return
        await send_message(
            thread,
            describe_talent_guide(character, query),
            source,
            reply
        )
        return

    if command == "!materialesdetalle":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}materialesdetalle nombre", source, reply)
            return
        character = await get_character(query)
        costs = character.get("costs") if isinstance(character, dict) else None
        if not isinstance(costs, dict):
            await send_message(thread, f"No encontré los materiales de «{query}».", source, reply)
            return
        await send_message(
            thread,
            describe_cost_stages(
                costs,
                f"MATERIALES DETALLADOS — {character.get('name', query)}"
            ),
            source,
            reply
        )
        return

    if command == "!farm":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}farm nombre del personaje", source, reply)
            return
        character, stats = await asyncio.gather(
            get_character(query),
            character_stats(query)
        )
        if not isinstance(character, dict):
            await send_message(thread, f"No encontré al personaje «{query}».", source, reply)
            return

        sections = [
            f"PLAN DE PROGRESIÓN — {character.get('name', query)}",
            f"Rareza: {character.get('rarity', '?')}★ · "
            f"{character.get('elementText', 'Elemento no informado')} · "
            f"{character.get('weaponText', 'Arma no informada')}"
        ]
        level90 = get_level90(stats)
        if isinstance(level90, dict):
            sections.append(
                "ESTADÍSTICAS BASE NIVEL 90\n"
                f"Vida: {format_number(level90.get('hp'))} · "
                f"ATQ: {format_number(level90.get('attack'))} · "
                f"DEF: {format_number(level90.get('defense'))}"
            )
        costs = character.get("costs")
        if isinstance(costs, dict):
            totals = aggregate_material_costs(costs)
            sections.append(
                "MATERIALES DE ASCENSIÓN\n"
                + ("\n".join(f"• {name}: {amount}" for name, amount in sorted(totals.items()))
                   if totals else "La API no entregó totales de materiales.")
            )
        sections.append(
            f"Usa {PREFIX}materialesdetalle {character.get('name', query)} "
            "para revisar cada fase por separado."
        )
        await send_message(thread, "\n\n".join(sections), source, reply)
        return

    if command in ("!drops", "!recompensas"):
        if not query:
            await send_message(thread, f"Uso: {PREFIX}drops nombre del enemigo", source, reply)
            return
        enemy = await game_data("enemies", query)
        if not isinstance(enemy, dict):
            await send_message(thread, f"No encontré al enemigo «{query}».", source, reply)
            return

        drops = (
            enemy.get("rewardPreview")
            or enemy.get("drops")
            or enemy.get("rewards")
            or enemy.get("reward")
        )
        rendered = game_items_text(drops, limit=25)
        if not rendered:
            await send_message(
                thread,
                f"RECOMPENSAS — {enemy.get('name', query)}\n"
                "La base consultada no publicó una lista de drops para este enemigo.",
                source,
                reply
            )
            return
        await send_message(
            thread,
            f"RECOMPENSAS REGISTRADAS — {enemy.get('name', query)}\n"
            + "\n".join(f"• {item}" for item in rendered)
            + "\n\nLos drops pueden variar según nivel, mundo y dificultad.",
            source,
            reply
        )
        return

    if command == "!artefactos":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}artefactos nombre del personaje", source, reply)
            return
        key = query.casefold().strip()
        guide = ARTIFACT_GUIDES.get(key)
        if not guide:
            guide = next(
                (value for name, value in ARTIFACT_GUIDES.items()
                 if name in key or key in name),
                None
            )
        if not guide:
            await send_message(
                thread,
                "Todavía no tengo una guía guardada para ese personaje. "
                "Puedes consultar conjuntos con !artefacto nombre del set.",
                source,
                reply
            )
            return
        answer = (
            f"GUÍA DE ARTEFACTOS — {guide['name']}\n"
            f"Conjuntos: {guide['sets']}\n"
            f"Estadísticas principales: {guide['main']}\n"
            f"Subestadísticas: {guide['subs']}\n"
            f"Nota: {guide['note']}\n\n"
            "Guía orientativa: ajusta Recarga, EM y estadísticas según arma, "
            "constelaciones, equipo y rotación."
        )
        await send_message(thread, answer, source, reply)
        return

    # -----------------------------------------------------------------------
    # RECOMENDACIONES DE ARMAS Y PRIORIDAD DE TALENTOS
    # -----------------------------------------------------------------------
    def normalize_character_guide_key(name):
        key = re.sub(r"\s+", " ", name.casefold().strip())
        aliases = {
            "hu tao": "hu tao",
            "hutao": "hu tao",
            "shogun raiden": "raiden",
            "raiden shogun": "raiden",
            "kamisato ayaka": "ayaka",
            "kaedehara kazuha": "kazuha",
            "alhacén": "alhaitham",
            "alhacen": "alhaitham",
            "errante": "wanderer",
            "scaramouche": "wanderer",
            "shikanoin heizou": "heizou"
        }
        return aliases.get(key, key)

    def resolve_character_guide(name):
        key = normalize_character_guide_key(name)
        guide = WEAPON_GUIDES.get(key)
        if not guide:
            guide = next(
                (value for alias, value in WEAPON_GUIDES.items()
                 if alias in key or key in alias),
                None
            )
        return guide

    if command == "!mejorarma":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}mejorarma nombre del personaje", source, reply)
            return
        guide = resolve_character_guide(query)
        if not guide:
            await send_message(
                thread,
                "Todavía no tengo una selección curada para ese personaje. "
                "Consulta !arma nombre para revisar cualquier arma de la base.",
                source,
                reply
            )
            return
        await send_message(
            thread,
            f"ARMAS RECOMENDADAS — {guide['name']}\n"
            + "\n".join(f"{i}. {name}" for i, name in enumerate(guide["weapons"], 1))
            + f"\n\nConsideración: {guide['note']}\n"
            "La mejor opción cambia según refinamiento, equipo y estadísticas disponibles.",
            source,
            reply
        )
        return

    if command == "!prioridadtalentos":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}prioridadtalentos nombre del personaje", source, reply)
            return
        key = normalize_character_guide_key(query)
        guide = TALENT_PRIORITIES.get(key)
        if not guide:
            await send_message(
                thread,
                "No tengo una prioridad guardada para ese personaje. "
                "Puedes consultar sus habilidades con !talentoguia nombre.",
                source,
                reply
            )
            return
        await send_message(
            thread,
            f"PRIORIDAD DE TALENTOS — {query}\n"
            f"Orden orientativo: {guide[0]}\n"
            f"Motivo: {guide[1]}\n"
            "La prioridad puede cambiar según el rol, la constelación y el equipo.",
            source,
            reply
        )
        return

    if command == "!recomendacion":
        if not query:
            await send_message(thread, f"Uso: {PREFIX}recomendacion nombre del personaje", source, reply)
            return
        character, stats = await asyncio.gather(
            get_character(query),
            character_stats(query)
        )
        if not isinstance(character, dict):
            await send_message(thread, f"No encontré al personaje «{query}».", source, reply)
            return

        name = clean_game_text(character.get("name") or query)
        key = normalize_character_guide_key(query)
        weapon_guide = resolve_character_guide(key)
        artifact_guide = ARTIFACT_GUIDES.get(key)
        talent_guide = TALENT_PRIORITIES.get(key)
        team = BUILD_TEAMS.get(key)
        base = get_level90(stats)

        lines = [
            f"RECOMENDACIÓN GENERAL — {name}",
            f"Elemento: {character.get('elementText', 'No informado')}",
            f"Arma: {character.get('weaponText', 'No informada')}"
        ]
        if isinstance(base, dict):
            lines.append(
                f"Base Nv. 90: Vida {format_number(base.get('hp'))} · "
                f"ATQ {format_number(base.get('attack'))} · "
                f"DEF {format_number(base.get('defense'))}"
            )
        if weapon_guide:
            lines.append("\nARMAS")
            lines.extend(f"• {item}" for item in weapon_guide["weapons"][:4])
            lines.append(weapon_guide["note"])
        if artifact_guide:
            lines.append(
                "\nARTEFACTOS\n"
                f"Sets: {artifact_guide['sets']}\n"
                f"Principal: {artifact_guide['main']}\n"
                f"Substats: {artifact_guide['subs']}"
            )
        if talent_guide:
            lines.append(f"\nTALENTOS\n{talent_guide[0]}")
        if team:
            lines.append(f"\nEQUIPO ORIENTATIVO\n{team[0]}")
        lines.append(
            "\nGuía general, no una simulación. Ajusta Recarga, EM y CRIT a tu equipo."
        )
        await send_message(thread, "\n".join(lines), source, reply)
        return

    # -----------------------------------------------------------------------
    # COMPARADORES DIRECTOS DE EQUIPO Y ARTEFACTOS
    # -----------------------------------------------------------------------
    if command == "!comparararmas":
        parts = [part.strip() for part in query.split("|") if part.strip()]
        if len(parts) != 2:
            await send_message(thread, f"Uso: {PREFIX}comparararmas arma 1 | arma 2", source, reply)
            return
        weapon_a, weapon_b, stats_a, stats_b = await asyncio.gather(
            game_data("weapons", parts[0]),
            game_data("weapons", parts[1]),
            weapon_stats(parts[0]),
            weapon_stats(parts[1])
        )
        if not isinstance(weapon_a, dict) or not isinstance(weapon_b, dict):
            await send_message(
                thread,
                "No pude encontrar ambas armas. Revisa los nombres en la base de Genshin.",
                source,
                reply
            )
            return

        level_a, level_b = get_level90(stats_a), get_level90(stats_b)
        def weapon_summary(weapon, level, fallback):
            lines = [
                f"{weapon.get('name', fallback)}",
                f"Rareza: {weapon.get('rarity', '?')}★",
                f"Tipo: {weapon.get('weaponText', 'No informado')}",
                f"ATQ Nv. 90: {format_number(level.get('attack')) if isinstance(level, dict) else 'No disponible'}",
                f"Secundaria: {weapon.get('baseStatText') or weapon.get('mainStatText') or 'No informada'}",
                f"Pasiva: {weapon.get('effectName') or 'No informada'}"
            ]
            refine = weapon.get("r1")
            if isinstance(refine, dict):
                description = refine.get("description") or refine.get("effectDescription")
                if description:
                    lines.append("R1: " + game_excerpt(description, 220))
            return "\n".join(lines)

        answer = (
            "COMPARACIÓN DE ARMAS\n\n"
            + weapon_summary(weapon_a, level_a, parts[0])
            + "\n\nVS.\n\n"
            + weapon_summary(weapon_b, level_b, parts[1])
            + "\n\nLa comparación no simula el daño final: depende del personaje y la rotación."
        )
        await send_message(thread, answer, source, reply)
        return

    if command == "!compararartefactos":
        parts = [part.strip() for part in query.split("|") if part.strip()]
        if len(parts) != 2:
            await send_message(thread, f"Uso: {PREFIX}compararartefactos set 1 | set 2", source, reply)
            return
        first, second = await asyncio.gather(
            game_data("artifacts", parts[0]),
            game_data("artifacts", parts[1])
        )
        if not isinstance(first, dict) or not isinstance(second, dict):
            await send_message(
                thread,
                "No encontré ambos conjuntos de artefactos.",
                source,
                reply
            )
            return

        def artifact_summary(item, fallback):
            title = clean_game_text(item.get("name") or fallback)
            two = clean_game_text(item.get("effect2Pc") or "Sin efecto de 2 piezas disponible.")
            four = clean_game_text(item.get("effect4Pc") or "Sin efecto de 4 piezas disponible.")
            rarity = item.get("rarityList") or item.get("rarity") or "No informada"
            if isinstance(rarity, list):
                rarity = ", ".join(str(value) + "★" for value in rarity)
            return (
                f"{title}\nRareza: {rarity}\n"
                f"2 piezas: {game_excerpt(two, 350)}\n"
                f"4 piezas: {game_excerpt(four, 500)}"
            )

        await send_message(
            thread,
            "COMPARACIÓN DE ARTEFACTOS\n\n"
            + artifact_summary(first, parts[0])
            + "\n\nVS.\n\n"
            + artifact_summary(second, parts[1])
            + "\n\nLa eficiencia del set depende de las habilidades y composición del personaje.",
            source,
            reply
        )
        return

    if command in ("!cv", "!valorcritico"):
        if len(args) != 2:
            await send_message(thread, f"Uso: {PREFIX}cv 75 160", source, reply)
            return
        try:
            cr, cd = parse_percent(args[0]), parse_percent(args[1])
            if not 0 <= cr <= 100 or not 0 <= cd <= 500:
                raise ValueError
        except ValueError:
            await send_message(thread, "Usa Prob. CRIT entre 0-100 y Daño CRIT entre 0-500.", source, reply)
            return
        await send_message(
            thread,
            f"VALOR CRÍTICO\nProb. CRIT: {cr:g}% · Daño CRIT: {cd:g}%\n"
            f"CV = 2 × {cr:g} + {cd:g} = {2*cr+cd:.1f}",
            source,
            reply
        )
        return

    if command == "!daño":
        if len(args) != 5:
            await send_message(
                thread,
                f"Uso: {PREFIX}daño ATQ multiplicador% bono% CR CD\n"
                f"Ejemplo: {PREFIX}daño 1800 250 46 75 160",
                source,
                reply
            )
            return
        try:
            attack, multiplier, bonus, cr, cd = [parse_percent(x) for x in args]
            if not (
                attack > 0
                and 0 <= multiplier <= 5000
                and 0 <= bonus <= 1000
                and 0 <= cr <= 100
                and 0 <= cd <= 500
            ):
                raise ValueError
        except ValueError:
            await send_message(
                thread,
                "Valores inválidos. Revisa ATQ, multiplicador, bono, Prob. CRIT y Daño CRIT.",
                source,
                reply
            )
            return

        raw_hit = attack * (multiplier / 100)
        boosted_hit = raw_hit * (1 + bonus / 100)
        critical_hit = boosted_hit * (1 + cd / 100)
        average_hit = boosted_hit * (1 + (cr / 100) * (cd / 100))
        await send_message(
            thread,
            "CALCULADORA DE DAÑO — ESTIMACIÓN\n"
            f"Daño sin crítico: {format_number(boosted_hit)}\n"
            f"Golpe crítico: {format_number(critical_hit)}\n"
            f"Daño medio esperado: {format_number(average_hit)}\n"
            "No aplica DEF, RES, reacción, reducción, buffs externos ni redondeos del juego.",
            source,
            reply
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
    # BUILDS PERSONALES
    # -----------------------------------------------------------------------
    if command == "!buildguardar":
        parts = [value.strip() for value in query.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            await send_message(thread, f"Uso: {PREFIX}buildguardar nombre | personaje | arma | set | stats | nota", source, reply)
            return
        parts = (parts + ["", "", "", ""])[:6]
        try:
            record_id = save_build_record(uid, parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])
            await send_message(
                thread,
                f"BUILD GUARDADA · #{record_id}\nNombre: {parts[0]}\nPersonaje: {parts[1]}\n"
                f"Arma: {parts[2] or 'No registrada'}\nSet: {parts[3] or 'No registrado'}\n"
                f"Stats principales: {parts[4] or 'No registradas'}\n"
                f"Notas: {parts[5] or 'Sin notas'}",
                source,
                reply
            )
        except Exception as exc:
            logging.exception("Error guardando build")
            await send_message(thread, f"No pude guardar la build: {clean_text(exc)}", source, reply)
        return

    if command == "!misbuilds":
        records = list_build_records(uid, query or None)
        if not records:
            await send_message(
                thread,
                "No tienes builds guardadas. "
                f"Usa {PREFIX}buildguardar nombre | personaje | arma | set | stats | nota.",
                source,
                reply
            )
            return
        lines = ["TUS BUILDS GUARDADAS"]
        for row in records:
            lines.append(f"#{row['id']} · {row['build_name']} — {row['character_name']} · {row['weapon_name'] or 'Sin arma'}")
        await send_message(thread, "\n".join(lines), source, reply)
        return

    if command == "!buildeditar":
        parts = [value.strip() for value in query.split("|", 2)]
        if len(parts) != 3:
            await send_message(
                thread,
                f"Uso: {PREFIX}buildeditar id | campo | valor\nCampos: nombre, personaje, arma, set, stats, nota.",
                source,
                reply
            )
            return
        try:
            record_id = int(parts[0])
            updated = update_build_record(uid, record_id, parts[1], parts[2])
        except (ValueError, TypeError) as exc:
            await send_message(thread, f"No pude editar la build: {exc}", source, reply)
            return
        await send_message(
            thread,
            "Build actualizada." if updated else "No encontré una build tuya con ese ID.",
            source,
            reply
        )
        return

    if command == "!buildlocal":
        try:
            record_id = int(args[0]) if args else 0
        except ValueError:
            record_id = 0
        row = get_build_record(uid, record_id)
        if not row:
            await send_message(thread, "No encontré esa build en tus registros.", source, reply)
            return
        await send_message(
            thread,
            f"BUILD PERSONAL #{row['id']} — {row['build_name']}\n"
            f"Personaje: {row['character_name']}\nArma: {row['weapon_name'] or 'No registrada'}\n"
            f"Set: {row['artifact_set'] or 'No registrado'}\nNotas: {row['notes'] or 'Sin notas'}",
            source,
            reply
        )
        return

    if command == "!buildborrar":
        try:
            record_id = int(args[0]) if args else 0
        except ValueError:
            record_id = 0
        deleted = delete_build_record(uid, record_id)
        await send_message(thread, "Build eliminada." if deleted else "No encontré una build tuya con ese ID.", source, reply)
        return

    # -----------------------------------------------------------------------
    # EQUIPOS PERSONALES
    # -----------------------------------------------------------------------
    if command == "!equipo":
        parts = [part.strip() for part in query.split("|")]
        first = parts[0].split(maxsplit=1) if parts else []
        action = first[0].casefold() if first else ""
        team_name = first[1].strip() if len(first) > 1 else ""

        if action == "guardar":
            if not team_name or len(parts) < 2 or not parts[1]:
                await send_message(
                    thread,
                    f"Uso: {PREFIX}equipo guardar nombre | Cyno, Nahida, Baizhu, Fischl | nota",
                    source,
                    reply
                )
                return
            members = [value.strip() for value in re.split(r"[,;/]", parts[1]) if value.strip()]
            note = parts[2] if len(parts) > 2 else ""
            try:
                saved = save_team_record(uid, team_name, members, note)
                await send_message(
                    thread,
                    f"EQUIPO GUARDADO — {team_name}\n"
                    + "\n".join(f"{i}. {name}" for i, name in enumerate(saved, 1))
                    + (f"\nNota: {note}" if note else ""),
                    source,
                    reply
                )
            except ValueError as exc:
                await send_message(thread, str(exc), source, reply)
            return

        if action == "ver":
            name = team_name or "|".join(parts[1:]).strip()
            team = get_team_record(uid, name)
            if not team:
                await send_message(thread, "No encontré ese equipo guardado.", source, reply)
                return
            await send_message(
                thread,
                f"EQUIPO — {team['team_name']}\n"
                + "\n".join(f"{i}. {name}" for i, name in enumerate(team["members"], 1))
                + (f"\nNotas: {team['notes']}" if team["notes"] else ""),
                source,
                reply
            )
            return

        if action == "borrar":
            name = team_name or "|".join(parts[1:]).strip()
            deleted = delete_team_record(uid, name) if name else False
            await send_message(
                thread,
                "Equipo eliminado." if deleted else "No encontré ese equipo.",
                source,
                reply
            )
            return

        await send_message(
            thread,
            f"Uso: {PREFIX}equipo guardar nombre | personajes | nota; "
            f"{PREFIX}equipo ver nombre; {PREFIX}equipo borrar nombre.",
            source,
            reply
        )
        return

    if command == "!equiposguardados":
        teams = list_team_records(uid)
        if not teams:
            await send_message(thread, "No tienes equipos guardados. Usa !equipo guardar.", source, reply)
            return
        await send_message(thread, "TUS EQUIPOS\n" + "\n".join(f"• {row['team_name']}" for row in teams), source, reply)
        return

    # -----------------------------------------------------------------------
    # PLANIFICACIÓN DE MATERIALES
    # -----------------------------------------------------------------------
    if command == "!farmmeta":
        parts = [part.strip() for part in query.split("|")]
        if len(parts) != 3:
            await send_message(thread, f"Uso: {PREFIX}farmmeta Material | cantidad actual | objetivo", source, reply)
            return
        try:
            current, target = int(parts[1]), int(parts[2])
            upsert_farm_material(uid, parts[0], current, target)
            await send_message(
                thread,
                f"MATERIAL REGISTRADO — {parts[0]}\nActual: {current}\nObjetivo: {target}\nPendiente: {max(0, target-current)}",
                source,
                reply
            )
        except ValueError as exc:
            await send_message(thread, f"Datos no válidos: {exc}", source, reply)
        return

    if command == "!farmlista":
        records = list_farm_materials(uid)
        if not records:
            await send_message(thread, "Tu lista de farmeo está vacía.", source, reply)
            return
        lines = ["MATERIALES POR CONSEGUIR"]
        for row in records:
            remaining = max(0, row["target_amount"] - row["current_amount"])
            progress = min(100, row["current_amount"] / max(1, row["target_amount"]) * 100)
            lines.append(f"{row['item_name']}: {row['current_amount']}/{row['target_amount']} ({progress:.1f}%) · faltan {remaining}")
        await send_message(thread, "\n".join(lines), source, reply)
        return

    if command == "!farmprogreso":
        if len(args) < 2:
            await send_message(thread, f"Uso: {PREFIX}farmprogreso nombre del material cantidad (+/-)", source, reply)
            return
        item_name = " ".join(args[:-1])
        try:
            amount = int(args[-1])
        except ValueError:
            await send_message(thread, "La cantidad debe ser un número entero.", source, reply)
            return
        result = adjust_farm_material(uid, item_name, amount)
        if result is None:
            await send_message(thread, "Ese material no está en tu lista.", source, reply)
            return
        current, target = result
        await send_message(thread, f"{item_name}: {current}/{target}. Pendiente: {max(0, target-current)}.", source, reply)
        return

    if command == "!farmquitar":
        deleted = delete_farm_material(uid, query)
        await send_message(thread, "Material eliminado." if deleted else "No encontré ese material.", source, reply)
        return

    # -----------------------------------------------------------------------
    # PROGRESO DE EXPLORACIÓN
    # -----------------------------------------------------------------------
    if command == "!exploracion":
        parts = [part.strip() for part in query.split("|")]
        if len(parts) < 2:
            await send_message(thread, f"Uso: {PREFIX}exploracion Fontaine | 83.5 | 120 | nota", source, reply)
            return
        try:
            percentage = float(parts[1].replace(",", ".").replace("%", ""))
            chests = int(parts[2]) if len(parts) > 2 and parts[2] else 0
            note = parts[3] if len(parts) > 3 else ""
            upsert_exploration(uid, parts[0], percentage, chests, note)
            await send_message(thread, f"EXPLORACIÓN ACTUALIZADA — {parts[0]}\nProgreso: {percentage:.1f}%\nCofres registrados: {chests}", source, reply)
        except ValueError as exc:
            await send_message(thread, f"Datos no válidos: {exc}", source, reply)
        return

    if command == "!mapaprogreso":
        regions = list_exploration(uid)
        if not regions:
            await send_message(thread, "Todavía no registras regiones. Usa !exploracion Región | porcentaje | cofres.", source, reply)
            return
        average = sum(row["percentage"] for row in regions) / len(regions)
        lines = ["PROGRESO DE EXPLORACIÓN"]
        for row in regions:
            filled = round(row["percentage"] / 10)
            bar = "■" * filled + "□" * (10 - filled)
            lines.append(f"{row['region_name']}: {bar} {row['percentage']:.1f}% · {row['chest_count']} cofres")
            if row["note"]:
                lines.append("  Nota: " + row["note"])
        lines.append(f"\nPromedio de las regiones registradas: {average:.1f}%")
        await send_message(thread, "\n".join(lines), source, reply)
        return

    # -----------------------------------------------------------------------
    # NOTAS PERSONALES DE GENSHIN
    # -----------------------------------------------------------------------
    if command == "!notagenshin":
        parts = [part.strip() for part in query.split("|", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            await send_message(thread, f"Uso: {PREFIX}notagenshin título | texto", source, reply)
            return
        note_id = save_genshin_note(uid, parts[0], parts[1])
        await send_message(thread, f"Nota guardada con ID #{note_id}.", source, reply)
        return

    if command == "!misnotas":
        notes = list_genshin_notes(uid)
        if not notes:
            await send_message(thread, "No tienes notas guardadas.", source, reply)
            return
        lines = ["TUS NOTAS DE GENSHIN"]
        for note in notes:
            lines.append(f"#{note['id']} · {note['note_title']}: {game_excerpt(note['note_body'], 180)}")
        await send_message(thread, "\n".join(lines), source, reply)
        return

    if command == "!borrarnota":
        try:
            note_id = int(args[0]) if args else 0
        except ValueError:
            note_id = 0
        deleted = delete_genshin_note(uid, note_id)
        await send_message(thread, "Nota eliminada." if deleted else "No encontré una nota tuya con ese ID.", source, reply)
        return

    if command == "!resumengen":
        summary = build_database_summary(uid)
        await send_message(
            thread,
            "RESUMEN DE TUS DATOS DE GENSHIN\n"
            f"Builds: {summary['builds']}\nEquipos: {summary['teams']}\n"
            f"Materiales en seguimiento: {summary['farm_items']}\n"
            f"Regiones: {summary['exploration_regions']}\nNotas: {summary['notes']}\n"
            "Los registros son privados y se guardan en este chat.",
            source,
            reply
        )
        return

    # -----------------------------------------------------------------------
    # EXPLORADOR DINÁMICO DE GENSHIN-DB
    # -----------------------------------------------------------------------
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
            "armas": "weapons",
            "arma": "weapons",
            "artefactos": "artifacts",
            "artefacto": "artifacts",
            "enemigos": "enemies",
            "enemigo": "enemies",
            "dominios": "domains",
            "dominio": "domains",
            "comidas": "foods",
            "comida": "foods",
            "materiales": "materials",
            "material": "materials",
            "talentos": "talents",
            "talento": "talents",
            "logros": "achievements",
            "animales": "animals",
            "atuendos": "outfits",
            "tarjetas": "namecards",
            "planeadores": "windgliders",
            "elementos": "elements",
            "fabricaciones": "crafts"
        }

        args_list = query.split()
        if not args_list or args_list[0].casefold() not in categories:
            await send_message(
                thread,
                f"Uso: {PREFIX}lista categoría [página]. Categorías: personajes, armas, artefactos, enemigos, dominios, comidas, materiales, talentos, logros, animales, atuendos, tarjetas, planeadores, elementos o fabricaciones.",
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
        "!arma": "weapons",
        "!artefacto": "artifacts",
        "!dominio": "domains",
        "!enemigo": "enemies",
        "!jefe": "enemies",
        "!comida": "foods",
        "!receta": "foods",
        "!material": "materials",
        "!logro": "achievements",
        "!talento": "talents",
        "!animal": "animals",
        "!nombrecard": "namecards",
        "!tarjeta": "namecards",
        "!atuendo": "outfits",
        "!planeador": "windgliders",
        "!fabricacion": "crafts",
        "!geografia": "geographies",
        "!elemento": "elements",
        "!rangoaventura": "adventureranks",
        "!grupologros": "achievementgroups"
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

    if command == "!constelacion" and len(args) > 1:
        character_query = " ".join(args[:-1]).strip()
        selected = args[-1].upper()
        match = re.fullmatch(r"C?([1-6])", selected)
        if not match:
            await send_message(thread, "Usa C1, C2, C3, C4, C5 o C6.", source, reply)
            return
        character = await get_character(character_query)
        constellations = (
            character.get("constellations") or character.get("Constellations")
            if isinstance(character, dict) else None
        )
        entries = list(constellations.values()) if isinstance(constellations, dict) else []
        index = int(match.group(1)) - 1
        if index >= len(entries) or not isinstance(entries[index], dict):
            await send_message(thread, f"No encontré C{index + 1} para {character_query}.", source, reply)
            return
        entry = entries[index]
        await send_message(
            thread,
            f"CONSTELACIÓN C{index + 1} — {character.get('name', character_query)}\n"
            f"{entry.get('name', 'Sin nombre')}\n"
            f"{clean_game_text(entry.get('description') or 'Sin descripción disponible.')}",
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
