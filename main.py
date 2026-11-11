
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
from collections import defaultdict, deque
from typing import Any

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

# CONFIGURACION
API = "https://api.anarchyapps.org/bot-api/v1"
GAME_API = "https://genshin-db-api.vercel.app/api/v5"
PORT = int(os.getenv("PORT", "3000"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
COMMUNITY_TOKEN = os.getenv("COMMUNITY_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
COMMUNITY_SECRET = os.getenv("COMMUNITY_WEBHOOK_SECRET", "").strip()
PREFIX = os.getenv("COMMAND_PREFIX", "!").strip() or "!"
STARTED = time.time()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI(title="Katherine Anarchy Bot")

# Estado temporal
seen_events = deque(maxlen=3000)
seen_event_set = set()
last_bot_send = 0.0
last_chat_send = defaultdict(float)
send_lock = asyncio.Lock()
send_history_bot = deque()
send_history_chat = defaultdict(deque)
cache: dict[str, tuple[float, Any]] = {}
polls: dict[str, dict] = {}
points = defaultdict(int)
trivia = {}
number_games = {}
wishes = defaultdict(lambda: {"pity5": 0, "pity4": 0, "guaranteed": False})
curiosity_bag = []
last_curiosity = None
poll_button_map: dict[str, str] = {}

SOCIAL = {
    "abrazo": ["abraza con cariño a"], "abrazar": ["abraza con cariño a"],
    "beso": ["le da un beso a"], "besar": ["le da un beso a"],
    "patada": ["le da una patada a"], "golpe": ["golpea a"],
    "cachetada": ["le da una cachetada a"], "acariciar": ["acaricia a"],
    "mimar": ["mima a"], "morder": ["muerde a"], "consolar": ["consuela a"],
    "felicitar": ["felicita a"], "saludar": ["saluda a"], "proteger": ["protege a"],
    "bailar": ["baila con"], "cosquillas": ["le hace cosquillas a"],
    "chocar": ["choca los cinco con"], "highfive": ["choca los cinco con"],
    "ayudar": ["ayuda a"], "regalar": ["le hace un regalo a"],
    "acompañar": ["acompaña a"], "asustar": ["asusta a"], "curar": ["cura a"],
    "cargar": ["carga a"], "empujar": ["empuja a"], "regañar": ["regaña a"],
    "admirar": ["admira a"], "apoyar": ["apoya a"], "dormir": ["duerme junto a"],
    "ignorar": ["ignora a"], "mirar": ["mira a"], "invitar": ["invita a"]
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


def token_for(source: str) -> str:
    return COMMUNITY_TOKEN if source == "community" else BOT_TOKEN


def clean_text(value: Any) -> str:
    return re.sub(r"[`*_]", "", str(value or "")).strip()


async def api(method: str, path: str, source="developer", body=None):
    token = token_for(source)
    if not token:
        return {"ok": False, "status": 500, "data": {"error": f"Falta token {source}."}}

    headers = {
        "Authorization": f"Bot {token}",
        "Accept": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(
                method, API + path, headers=headers, json=body
            )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:1000]}

        logging.info("[ANARCHY] %s %s -> %s", method, path, response.status_code)

        return {
            "ok": response.is_success,
            "status": response.status_code,
            "data": data,
            "retry_after": response.headers.get("retry-after")
        }

    except Exception as exc:
        logging.exception("Error de conexion con Anarchy")
        return {"ok": False, "status": 500, "data": {"error": str(exc)}}


async def send_message(thread_id: str, content: str, source="developer",
                       reply_id=None, buttons=None):
    global last_bot_send

    content = clean_text(content)[:2000]

    if not thread_id or not content:
        return

    if buttons:
        buttons = [
            {"id": str(b["id"])[:32], "label": str(b["label"])[:40]}
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
            retry = result.get("retry_after") or result["data"].get("retryAfter") or 2

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
            r = await client.get(f"{GAME_API}/{folder}", params=params)
            r.raise_for_status()
            data = r.json()

        if isinstance(data, dict):
            data = data.get("data", data.get("result", data))

        cache[key] = (time.time() + 21600, data)

        if len(cache) > 300:
            cache.pop(next(iter(cache)))

        return data

    except Exception as exc:
        logging.warning("Genshin API %s/%s: %s", folder, query, exc)
        return None


def describe_game(data: Any, fallback: str) -> str:
    if not isinstance(data, dict):
        return f"No encontré información de «{fallback}»."

    name = data.get("name") or data.get("Name") or fallback
    lines = [f"GENSHIN IMPACT — {name}"]

    for key, label in (
        ("rarity", "Rareza"),
        ("element", "Elemento"),
        ("weapon", "Arma"),
        ("region", "Región"),
        ("description", "Descripción"),
        ("fetter", "Historia")
    ):
        value = data.get(key) or data.get(key.title())

        if isinstance(value, (str, int, float)) and str(value).strip():
            lines.append(f"{label}: {value}")

    if len(lines) == 1:
        for k, v in list(data.items())[:12]:
            if isinstance(v, (str, int, float)):
                lines.append(f"{k}: {v}")

    return "\n".join(lines)[:1950]


async def handle_command(event: dict, source: str):
    global curiosity_bag, last_curiosity

    msg = event.get("message") or {}
    author = msg.get("author") or {}
    thread = str(event.get("threadId") or "")
    content = str(msg.get("content") or "").strip()

    if not thread or not content:
        return

    uid = str(author.get("uid") or author.get("nickname") or "unknown")
    nickname = author.get("nickname") or "Viajero"
    message_id = msg.get("messageId")

    if not content.startswith(("!", "-")):
        return

    pieces = content.split()
    command = pieces[0].lower().replace("-", "!", 1)
    args = pieces[1:]
    query = " ".join(args).strip()
    reply = message_id

    logging.info("[COMANDO] %s | %s | %s", command, nickname, thread)

    if command in ("!ayuda", "!comandos"):
        answer = (
            "KATHERINE — COMANDOS\n\nGENSHIN IMPACT\n"
            "!personaje nombre | !build nombre | !materiales nombre | !equipos nombre\n"
            "!arma nombre | !artefacto nombre | !dominio nombre | !enemigo nombre\n"
            "!comida nombre | !material nombre | !logro nombre | !talento nombre\n"
            "!constelacion nombre | !reaccion electro dendro | !curiosidad\n"
            "!deseo | !pity | !resetpity\n\nENTRETENIMIENTO\n"
            "!trivia | !responder 1-4 | !adivina | !intento número\n"
            "!puntos | !ranking | !encuesta pregunta | opción 1 | opción 2\n"
            "!elige | !duelo @usuario | !ship usuario1 usuario2\n"
            "!dado | !moneda | !8ball pregunta | !chiste | !frase | !reto | !suerte\n\n"
            "SOCIAL\n!abrazo @usuario | !beso @usuario | !patada @usuario | !golpe @usuario\n"
            "!cachetada | !acariciar | !mimar | !morder | !consolar | !felicitar\n"
            "!saludar | !proteger | !bailar | !cosquillas | !highfive | !ayudar\n"
            "!regalar | !acompañar | !asustar | !curar | !cargar | !empujar\n"
            "!regañar | !admirar | !apoyar | !dormir | !ignorar | !mirar | !invitar\n\n"
            "SISTEMA\n!ping | !uptime | !fecha | !zona America/Santiago | !salas\n"
            "En comandos con usuario, indica @usuario."
        )
        await send_message(thread, answer, source, reply)
        return

    if command[1:] in SOCIAL:
        target = next(
            (m.get("nickname") or m.get("uid") for m in msg.get("mentions", [])
             if isinstance(m, dict)),
            None
        )

        target = target or next((x for x in args if x.startswith("@")), None)

        if not target and query:
            target = query.split()[0]

        if not target:
            await send_message(thread, f"Uso: {command} @usuario", source, reply)
            return

        actions = SOCIAL[command[1:]]
        await send_message(
            thread,
            f"{nickname} {random.choice(actions)} {target}.",
            source,
            reply
        )
        return

    if command in ("!personaje", "!build", "!materiales", "!equipos"):
        if not query:
            await send_message(thread, f"Uso: {command} nombre", source, reply)
            return

        data = await game_data("characters", query)

        if command == "!build":
            answer = (
                f"Las builds detalladas se consultan desde Genshin Builds. "
                f"Personaje: {query}\n{describe_game(data, query)}"
            )
        elif command == "!materiales":
            answer = f"MATERIALES — {query}\n{describe_game(data, query)}"
        elif command == "!equipos":
            answer = (
                f"EQUIPOS — {query}\n"
                "Consulta la ficha del personaje para verificar sus sinergias.\n"
                f"{describe_game(data, query)}"
            )
        else:
            answer = describe_game(data, query)

        await send_message(thread, answer, source, reply)
        return

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
            await send_message(thread, f"Uso: {command} nombre", source, reply)
            return

        data = await game_data(object_commands[command], query)
        await send_message(thread, describe_game(data, query), source, reply)
        return

    if command == "!constelacion":
        data = await game_data("characters", query)

        if isinstance(data, dict):
            consts = data.get("constellations") or data.get("Constellations")

            if isinstance(consts, dict):
                answer = (
                    f"CONSTELACIONES — {data.get('name', query)}\n"
                    + "\n".join(
                        f"{i}. {v.get('name','')} — {v.get('description','')}"
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
            await send_message(thread, "Uso: !reaccion electro dendro", source, reply)
            return

        reactions = {
            ("pyro", "hydro"): "Vaporización",
            ("cryo", "pyro"): "Derretido",
            ("dendro", "electro"): "Agravado / Propagación",
            ("dendro", "hydro"): "Florecimiento",
            ("anemo", "pyro"): "Dispersión",
            ("geo", "pyro"): "Cristalización",
            ("electro", "hydro"): "Electrocargado",
            ("cryo", "hydro"): "Congelado"
        }

        pair = tuple(sorted(x.lower() for x in args[:2]))
        await send_message(
            thread,
            f"Reacción: {reactions.get(pair, 'No se reconoce una reacción directa clásica.')}",
            source,
            reply
        )
        return

    if command == "!curiosidad":
        if not curiosity_bag:
            curiosity_bag = CURIOSITIES.copy()
            random.shuffle(curiosity_bag)

            if len(curiosity_bag) > 1 and curiosity_bag[-1] == last_curiosity:
                curiosity_bag[0], curiosity_bag[-1] = curiosity_bag[-1], curiosity_bag[0]

        last_curiosity = curiosity_bag.pop()
        await send_message(thread, "CURIOSIDAD\n" + last_curiosity, source, reply)
        return

    if command == "!deseo":
        w = wishes[uid]
        w["pity5"] += 1
        w["pity4"] += 1

        if w["pity5"] >= 90 or random.random() < (0.006 + max(0, w["pity5"] - 73) * 0.06):
            w.update(pity5=0, pity4=0)
            name = random.choice(["Cyno", "Nahida", "Dehya", "Furina", "Arlecchino"])
            answer = f"RESULTADO SIMULADO\n⭐⭐⭐⭐⭐\n{name}\nNo afecta tu cuenta de Genshin."
        elif w["pity4"] >= 10 or random.random() < 0.051:
            w["pity4"] = 0
            answer = "RESULTADO SIMULADO\n⭐⭐⭐⭐\n" + random.choice(
                ["Bennett", "Xingqiu", "Fischl", "Sucrose", "Diona"]
            )
        else:
            answer = "RESULTADO SIMULADO\n⭐⭐⭐\nArma de 3 estrellas"

        await send_message(thread, answer, source, reply)
        return

    if command == "!pity":
        w = wishes[uid]
        await send_message(
            thread,
            f"DESEOS SIMULADOS\n5 estrellas: {w['pity5']}\n"
            f"4 estrellas: {w['pity4']}\nGarantizado: No",
            source,
            reply
        )
        return

    if command == "!resetpity":
        wishes[uid] = {"pity5": 0, "pity4": 0, "guaranteed": False}
        await send_message(
            thread,
            "Contador simulado reiniciado. No modifica tu cuenta.",
            source,
            reply
        )
        return

    if command == "!trivia":
        q = random.choice(TRIVIA)
        trivia[thread] = {
            "q": q,
            "tries": 0,
            "expires": time.time() + 120
        }

        answer = (
            "TRIVIA KATHERINE\n\n" + q[0] + "\n"
            + "\n".join(f"{i+1}. {a}" for i, a in enumerate(q[1]))
            + "\n\nResponde !responder 1, 2, 3 o 4."
        )

        await send_message(thread, answer, source, reply)
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
            points[uid] += 10
            trivia.pop(thread, None)
            answer = "¡Correcto! Ganaste 10 puntos."
        elif game["tries"] >= 3:
            answer = f"Se acabaron los intentos. Era: {game['q'][1][game['q'][2]]}."
            trivia.pop(thread, None)
        else:
            answer = f"No es correcto. Te quedan {3-game['tries']} intento(s)."

        await send_message(thread, answer, source, reply)
        return

    if command == "!adivina":
        number_games[thread] = {
            "number": random.randint(1, 100),
            "tries": 0,
            "expires": time.time() + 300
        }

        await send_message(
            thread,
            "He elegido un número entre 1 y 100. Tienes siete intentos. Usa !intento número.",
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
                points[uid] += 15
                answer = f"¡Correcto! Era {n}. Ganaste 15 puntos."
                number_games.pop(thread, None)
            elif game["tries"] >= 7:
                answer = f"Se acabaron los intentos. Era {game['number']}."
                number_games.pop(thread, None)
            else:
                answer = "Es mayor." if n < game["number"] else "Es menor."

        await send_message(thread, answer, source, reply)
        return

    if command == "!puntos":
        await send_message(
            thread,
            f"{nickname}, tienes {points[uid]} puntos.",
            source,
            reply
        )
        return

    if command == "!ranking":
        top = sorted(points.items(), key=lambda x: x[1], reverse=True)[:10]

        answer = "RANKING KATHERINE\n\n" + (
            "\n".join(
                f"{i}. {u} — {p} puntos"
                for i, (u, p) in enumerate(top, 1)
            ) or "Aún no hay puntos."
        )

        await send_message(thread, answer, source, reply)
        return

    if command == "!encuesta":
        parts = [x.strip() for x in query.split("|") if x.strip()]

        if len(parts) < 3:
            await send_message(
                thread,
                "Uso: !encuesta Pregunta | Opción 1 | Opción 2 [| Opción 3...]",
                source,
                reply
            )
            return

        question, options = parts[0], parts[1:6]
        poll_id = f"{thread}:{int(time.time() * 1000)}"

        polls[poll_id] = {
            "question": question,
            "options": options,
            "votes": {},
            "source": source
        }

        stamp = poll_id.split(":")[-1]
        buttons = [
            {"id": f"p{stamp}_{i}", "label": opt}
            for i, opt in enumerate(options)
        ]

        poll_button_map[stamp] = poll_id

        await send_message(
            thread,
            "ENCUESTA\n" + question + "\nElige una opción:",
            source,
            reply,
            buttons
        )
        return

    if command == "!elige":
        choices = [x.strip() for x in query.split("|") if x.strip()]
        answer = (
            f"Mi elección: {random.choice(choices)}"
            if choices else "Uso: !elige opción 1 | opción 2"
        )
        await send_message(thread, answer, source, reply)
        return

    if command == "!duelo":
        target = next((x for x in args if x.startswith("@")), "un rival misterioso")
        winner = "Katherine" if random.random() < 0.5 else nickname
        await send_message(
            thread,
            f"⚔️ {nickname} desafía a {target}. ¡La victoria es de {winner}!",
            source,
            reply
        )
        return

    if command == "!ship":
        if len(args) < 2:
            answer = "Uso: !ship usuario1 usuario2"
        else:
            answer = f"Compatibilidad de {args[0]} y {args[1]}: {random.randint(0,100)}% 💞"

        await send_message(thread, answer, source, reply)
        return

    if command == "!dado":
        await send_message(thread, f"🎲 Resultado: {random.randint(1,6)}", source, reply)
        return

    if command == "!moneda":
        await send_message(thread, random.choice(["🪙 Cara", "🪙 Cruz"]), source, reply)
        return

    if command == "!8ball":
        answer = random.choice([
            "Sí.", "No.", "Es probable.", "No puedo asegurarlo.",
            "Definitivamente.", "Vuelve a preguntar más tarde."
        ])
        await send_message(thread, answer, source, reply)
        return

    if command == "!chiste":
        await send_message(thread, random.choice(JOKES), source, reply)
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

    if command == "!ping":
        started = time.monotonic()
        result = await api("GET", "/me", source)

        if result["ok"]:
            answer = (
                f"PONG, {nickname}.\nKatherine está en línea.\n"
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
            f"Katherine lleva activa {seconds//3600} h {(seconds%3600)//60} min.",
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
            answer = datetime.now(zone).strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            answer = "Zona horaria inválida. Ejemplo: !zona America/Santiago"

        await send_message(thread, answer, source, reply)
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

        if not 2 <= len(name) <= 32 or (status is not None and len(status) > 40):
            await send_message(
                thread,
                "Uso: !perfilbot Nombre | Estado (nombre 2-32 caracteres; estado máximo 40). Usa - para borrar el estado.",
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
                "commandPrefix": "!"
            }
        )

        answer = (
            "Perfil actualizado."
            if result["ok"]
            else f"No se pudo actualizar: HTTP {result['status']}."
        )

        await send_message(thread, answer, source, reply)
        return

    if command == "!salas":
        result = await api("GET", "/live-rooms", "community")
        rooms = result["data"].get("roomList", []) if result["ok"] else []

        answer = "SALAS EN VIVO\n\n" + (
            "\n".join(
                f"• {r.get('title') or 'Sala'} — "
                f"{r.get('participantCount', 0)} participantes | {r.get('threadId')}"
                for r in rooms
            ) or "No hay salas disponibles o falta el token de comunidad."
        )

        await send_message(thread, answer, source, reply)
        return


async def handle_interaction(event: dict, source: str):
    button_id = str(event.get("buttonId") or "")
    match = re.fullmatch(r"p(\d+)_(\d+)", button_id)

    if not match:
        return

    poll = polls.get(poll_button_map.get(match.group(1), ""))

    if not poll:
        return

    index = int(match.group(2))

    if index >= len(poll["options"]):
        return

    user = str((event.get("user") or {}).get("uid") or "unknown")
    poll["votes"][user] = index

    counts = [
        sum(v == i for v in poll["votes"].values())
        for i in range(len(poll["options"]))
    ]

    result = "RESULTADOS — " + poll["question"] + "\n" + "\n".join(
        f"{opt}: {counts[i]} voto(s)"
        for i, opt in enumerate(poll["options"])
    )

    await send_message(str(event.get("threadId") or ""), result, source)


async def dispatch(event: dict, source: str):
    kind = event.get("type")

    if kind == "message":
        await handle_command(event, source)
    elif kind == "interaction":
        await handle_interaction(event, source)
    elif kind in (
        "room.join", "room.speaker", "room.leave",
        "room.end", "room.ready", "room.kicked"
    ):
        logging.info("[SALA] %s | %s", kind, event.get("threadId"))
    else:
        logging.info("[EVENTO] Ignorado: %s", kind)


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

    if len(received) != len(expected) or not hmac.compare_digest(received, expected):
        return None

    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


async def webhook(request: Request, source: str):
    secret = COMMUNITY_SECRET if source == "community" else WEBHOOK_SECRET

    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Webhook secret no configurado."
        )

    raw = await request.body()
    event = verify_webhook(raw, request.headers, secret)

    if event is None:
        raise HTTPException(status_code=401, detail="Firma inválida.")

    event_id = str(event.get("eventId") or "")

    if event_id and event_id in seen_event_set:
        return JSONResponse({"ok": True, "duplicate": True})

    if event_id:
        if len(seen_events) == seen_events.maxlen:
            old = seen_events.popleft()
            seen_event_set.discard(old)

        seen_events.append(event_id)
        seen_event_set.add(event_id)

    asyncio.create_task(dispatch(event, source))
    return JSONResponse({"ok": True})


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
    return {"ok": True, "bot": "Katherine"}


@app.on_event("startup")
async def startup():
    logging.info("Katherine Python iniciando en puerto %s", PORT)

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
