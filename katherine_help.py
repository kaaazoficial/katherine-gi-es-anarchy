"""Command help matching the exact Anarchy [CB]/[C] presentation requested."""
from __future__ import annotations

import unicodedata
from functools import lru_cache


SEPARATOR = "[C] ───────────── ✦ ─────────────"
CATEGORY_TITLES = (
    "1. AYUDA E INTELIGENCIA ARTIFICIAL",
    "2. GENSHIN IMPACT",
    "3. SOCIAL Y PERFILES",
    "4. JUEGOS, ECONOMÍA Y COMUNIDAD",
    "5. MÚSICA Y VOZ",
    "6. UTILIDADES Y MULTIMEDIA",
    "7. ADMINISTRACIÓN Y ESTADO",
)


def _command_help_sections(prefix: str):
    prefix = str(prefix or "!").strip() or "!"

    sections = [
        (CATEGORY_TITLES[0], [
            (True, "▸ Ayuda y Navegación"),
            (False, f"• {prefix}ayuda [categoría] — Muestra todo el catálogo o una sección concreta."),
            (True, "▸ Asistente IA"),
            (False, f"• {prefix}asistente <consulta> — Conversación y solicitudes en lenguaje natural."),
            (False, f"• {prefix}ia ayuda — Guía de IA."),
            (False, f"• {prefix}ia preguntar <consulta> — Consulta directa al asistente."),
            (False, f"• {prefix}ia genshin <consulta> — Consulta especializada de Teyvat."),
            (False, f"• {prefix}ia estado — Revisa el proveedor y la sesión actual."),
            (False, f"• {prefix}ia olvidar — Borra el contexto temporal de la charla."),
            (True, "▸ Investigación y Herramientas"),
            (False, f"• {prefix}investigar <tema> — Investigación (opciones: comparar, verificar, cronología, informe)."),
            (False, f"• {prefix}analizartexto <modo> <texto> — Modos: resumir, corregir, reescribir, extraer o contradicciones."),
            (False, f"• {prefix}calcular <operación> — Porcentajes, raíces, trigonometría y matemáticas."),
            (True, "▸ Administración de IA [ADMIN]"),
            (False, f"• {prefix}ia probar | {prefix}ia guardian | {prefix}ia diagnostico — Diagnóstico y controles del sistema."),
        ]),
        (CATEGORY_TITLES[1], [
            (True, "▸ Personajes y Equipos"),
            (False, f"• {prefix}guia <personaje> — Builds y talentos."),
            (False, f"• {prefix}materiales <personaje> — Materiales de ascensión."),
            (False, f"• {prefix}equipos <personaje> — Composiciones de equipo."),
            (False, f"• {prefix}rotacion <personaje> — Rotaciones de combate."),
            (False, f"• {prefix}sinergia <personaje> — Sinergias y compañeros recomendados."),
            (True, "▸ Mundo y Datos"),
            (False, f"• {prefix}jefes — Lista de jefes."),
            (False, f"• {prefix}banner — Banners activos."),
            (False, f"• {prefix}calendario — Eventos y fechas."),
            (False, f"• {prefix}cobertura <personaje> — Contenido del personaje."),
            (False, f"• {prefix}reacciones <elemento> — Reacciones elementales."),
            (False, f"• {prefix}enciclopedia | {prefix}archivo | {prefix}teyvat <nombre> — Fichas y datos del lore."),
            (False, f"• {prefix}buscargenshin <texto> — Búsqueda general."),
            (False, f"• {prefix}lista <categoría> [página] — Listados por categoría."),
            (False, f"• {prefix}enemigo | {prefix}geografia | {prefix}elemento <nombre> — Consultas específicas del juego."),
            (False, f"• {prefix}curiosidad — Dato aleatorio de Genshin Impact."),
            (True, "▸ Deseos (Gachapón)"),
            (False, f"• {prefix}deseo — Simulador de tiradas."),
            (False, f"• {prefix}pity — Consulta tu contador actual."),
            (False, f"• {prefix}resetpity — Reinicia tu contador."),
        ]),
        (CATEGORY_TITLES[2], [
            (True, "▸ Perfil"),
            (False, f"• {prefix}perfil [@usuario] — Consulta un perfil."),
            (False, f"• {prefix}bio <texto> — Edita tu biografía."),
            (False, f"• {prefix}miestado <texto> — Actualiza tu estado."),
            (False, f"• {prefix}actividad [hoy|semana] — Consulta la actividad."),
            (True, "▸ Interacciones (@usuario)"),
            (False, f"• {prefix}interaccion @usuario — Guía de acciones sociales."),
            (False, f"• Afecto: {prefix}abrazo | {prefix}beso | {prefix}acariciar | {prefix}mimar"),
            (False, f"• Físicas: {prefix}patada | {prefix}golpe | {prefix}cachetada | {prefix}morder | {prefix}empujar"),
            (False, f"• Apoyo: {prefix}consolar | {prefix}felicitar | {prefix}saludar | {prefix}proteger | {prefix}ayudar | {prefix}apoyar"),
            (False, f"• Sociales: {prefix}bailar | {prefix}cosquillas | {prefix}chocar | {prefix}acompañar"),
            (False, f"• Otras: {prefix}asustar | {prefix}curar | {prefix}cargar | {prefix}regañar | {prefix}admirar | {prefix}dormir | {prefix}ignorar | {prefix}mirar | {prefix}invitar | {prefix}regalar"),
            (True, "▸ Vínculos y Regalos"),
            (False, f"• {prefix}roast @usuario — Lanza una broma."),
            (False, f"• {prefix}afinidad @usuario — Mide el vínculo con alguien."),
            (False, f"• {prefix}regalo @usuario [mensaje] — Envía un regalo."),
            (False, f"• {prefix}muro — Consulta los regalos recibidos."),
        ]),
        (CATEGORY_TITLES[3], [
            (True, "▸ Juegos y Entretenimiento"),
            (False, f"• {prefix}trivia — Inicia trivia | {prefix}responder <1-4> — Responde."),
            (False, f"• {prefix}adivina — Inicia adivinanza | {prefix}intento <número> — Prueba tu respuesta."),
            (False, f"• {prefix}duelo @usuario — Reta a alguien."),
            (False, f"• {prefix}ship <usuario1> <usuario2> — Calcula compatibilidad."),
            (False, f"• {prefix}azar <mín> <máx> | {prefix}azar dado | {prefix}azar moneda — Número aleatorio, dado o moneda."),
            (False, f"• {prefix}verdad | {prefix}8ball <pregunta> — Preguntas al azar."),
            (False, f"• {prefix}elige <opciones> — Elección aleatoria."),
            (False, f"• {prefix}chiste | {prefix}frase | {prefix}reto — Entretenimiento rápido."),
            (True, "▸ Economía"),
            (False, f"• {prefix}puntos — Consulta tu saldo."),
            (False, f"• {prefix}ranking — Clasificación general."),
            (False, f"• {prefix}diario — Reclama tu recompensa diaria."),
            (False, f"• {prefix}trabajar — Gana puntos."),
            (False, f"• {prefix}pagar @usuario <cantidad> — Transfiere puntos."),

            (True, "▸ Comunidad"),
            (False, f"• {prefix}encuesta <pregunta> | <opciones> — Crea una votación."),
            (False, f"• {prefix}evento | {prefix}eventos — Consulta, crea o cierra eventos (según permisos)."),
        ]),
        (CATEGORY_TITLES[4], [
            (True, "▸ Llamadas"),
            (False, f"• {prefix}join | {prefix}salirsala | {prefix}salas — Entrar, salir y consultar salas."),
            (False, f"• {prefix}voz participantes|hablando|estado — Consultar participantes y roles."),
            (True, "▸ Reproductor DJ"),
            (False, f"• {prefix}dj buscar <tema> | resultados 1|2 — Buscar hasta diez pistas."),
            (False, f"• {prefix}dj reproducir <n> | encolar <n> — Reproducir o añadir resultados."),
            (False, f"• {prefix}dj pausa | {prefix}dj reanudar | {prefix}dj saltar | {prefix}dj cola | {prefix}dj actual — Controles."),
            (False, f"• {prefix}dj quitar <n> | mover <a> <b> | vaciar — Gestionar la cola."),
            (False, f"• {prefix}dj repetir off|una|cola | transicion <ms> | duck on|off — Ajustes."),
            (False, f"• {prefix}dj detener | {prefix}dj fuentes | {prefix}dj ambiente — Control y búsqueda."),
            (False, f"• {prefix}dj favorito | {prefix}dj probar <tema> — Favoritos y diagnóstico [ADMIN]."),
            (False, f"• {prefix}reproducir <archivo> — Reproduce un archivo local."),
            (True, "▸ Listas, Volumen y Lectura"),
            (False, f"• {prefix}playlist crear|agregar|ver|reproducir|borrar — Gestiona tus listas."),
            (False, f"• {prefix}volumen <0-100> | {prefix}subirvolumen | {prefix}bajarvolumen | {prefix}silencio — Controles de música."),
            (False, f"• {prefix}leer | {prefix}noleer — Activa o desactiva la lectura."),
            (False, f"• {prefix}tts voces|voz|velocidad|volumen|enlaces|menciones — Configura la lectura."),
            (False, f"• {prefix}volumenlectura <0-100> | {prefix}subirvolumenlectura | {prefix}bajarvolumenlectura | {prefix}silenciolectura — Controles de voz."),
        ]),
        (CATEGORY_TITLES[5], [
            (True, "▸ Fecha, Hora e Idiomas"),
            (False, f"• {prefix}fecha — Fecha y hora actual."),
            (False, f"• {prefix}zona <Area/Ciudad> — Hora de otra zona horaria."),
            (False, f"• {prefix}traducir <idioma> | <texto> — Traduce un texto."),
            (True, "▸ Búsqueda Multimedia"),
            (False, f"• {prefix}multimedia imagen|video|audio <búsqueda> — Búsqueda por tipo de recurso."),
            (False, f"• {prefix}imagen <búsqueda> — Busca imágenes."),
            (False, f"• {prefix}video <búsqueda> — Busca vídeos."),
            (False, f"• {prefix}audio <búsqueda> — Busca recursos de audio."),
        ]),
        (CATEGORY_TITLES[6], [
            (True, "▸ Identidad y Estado"),
            (False, f"• {prefix}miuid — Consulta tu UID."),
            (False, f"• {prefix}estado — Estado general del bot [ADMIN]."),
            (True, "▸ Fuentes y Paneles"),
            (False, f"• {prefix}fuentes — Estado de proveedores [ADMIN]."),
            (False, f"• {prefix}metricas — Métricas agregadas del sistema [ADMIN]."),
            (False, f"• {prefix}autotest — Ejecuta pruebas internas [ADMIN]."),
            (False, f"• {prefix}centro — Panel de investigación [ADMIN]."),
            (False, f"• {prefix}admin [resumen|actividad|antispam|avisos|historial] — Panel de control [ADMIN]."),
            (True, "▸ Moderación y Configuración"),
            (False, f"• {prefix}antispam estado|on|off|límite N|ventana N|repeticiones N|enlaces N|menciones N [ADMIN]."),
            (False, f"• {prefix}advertir @usuario [motivo] | {prefix}advertencias [@usuario] | {prefix}quitaradvertencia ID [ADMIN]."),
            (False, f"• {prefix}respuestas estado|on|off | {prefix}respuesta añadir|lista|eliminar ID [ADMIN]."),
            (False, f"• {prefix}perfilbot ... | {prefix}avatarbot ... — Edita identidad e icono [ADMIN]."),
            (False, f"• {prefix}eliminarmensajes — Elimina mensajes propios identificados en esta ejecución [ADMIN]."),
            (False, "Nota: Los comandos ADMIN requieren un UID autorizado en ADMIN_UIDS (el nickname no concede permisos)."),
        ]),
    ]
    return prefix, sections


def _format_section(title: str, entries: list[tuple[bool, str]]) -> list[str]:
    lines = [f"[CB] ✦ {title} ✦", "[C]"]
    for is_heading, value in entries:
        lines.append(f"[CB] {value}" if is_heading else f"[C] {value}")
    return lines


def _intro_lines(prefix: str) -> list[str]:
    accepted_prefixes = " / ".join(dict.fromkeys((prefix, "!", "-")))
    return [
        "[CB] ✦ KATHERINE · GUÍA DE COMANDOS ✦",
        f"[C] Prefijos aceptados: {accepted_prefixes}",
        f"[C] Usa {prefix}ayuda [categoría] para consultar todo o filtrar una sección.",
        "[C] Catálogo de comandos · Accesos y funciones.",
        "[C]",
    ]


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


@lru_cache(maxsize=8)
def build_commands_help_text(prefix: str = "!") -> str:
    """Build and cache the immutable full guide for each configured prefix."""
    prefix, sections = _command_help_sections(prefix)
    lines = _intro_lines(prefix)
    for index, (title, entries) in enumerate(sections):
        lines.extend([SEPARATOR, "[C]"])
        lines.extend(_format_section(title, entries))
    return "\n".join(lines)


@lru_cache(maxsize=16)
def _build_commands_help_blocks_cached(
    prefix: str = "!", max_units: int = 1800
) -> tuple[str, ...]:
    """Cache immutable blocks; callers receive a fresh list from the public API."""
    if max_units < 200:
        raise ValueError("El tamaño mínimo de bloque debe ser al menos 200 unidades UTF-16.")

    prefix, sections = _command_help_sections(prefix)
    intro = _intro_lines(prefix)
    blocks: list[str] = []
    for index, (title, entries) in enumerate(sections):
        category_lines = [
            SEPARATOR,
            "[C]",
            *_format_section(title, entries),
        ]
        if index == 0:
            category_lines = [*intro, *category_lines]
        block = "\n".join(category_lines)
        if _utf16_length(block) > max_units:
            raise ValueError(f"La categoría {title!r} supera el límite del mensaje.")
        blocks.append(block)
    return tuple(blocks)


def build_commands_help_blocks(prefix: str = "!", max_units: int = 1800) -> list[str]:
    """Return fresh category blocks backed by a short-lived immutable cache."""
    return list(_build_commands_help_blocks_cached(prefix, max_units))


_CATEGORY_ALIASES = {
    "ia": 0,
    "inteligencia": 0,
    "inteligencia artificial": 0,
    "genshin": 1,
    "teyvat": 1,
    "social": 2,
    "perfiles": 2,
    "juegos": 3,
    "economia": 3,
    "comunidad": 3,
    "musica": 4,
    "voz": 4,
    "dj": 4,
    "tts": 4,
    "multimedia": 5,
    "utilidades": 5,
    "admin": 6,
    "administracion": 6,
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


@lru_cache(maxsize=64)
def filter_commands_help_text(help_text: str, topic: str | None = None) -> str:
    """Cache immutable full/category help text for frequently requested topics."""
    requested = _normalize(topic or "")
    if not requested or requested in {"todo", "todos", "completo", "all"}:
        return str(help_text)

    category_index = _CATEGORY_ALIASES.get(requested)
    if category_index is None:
        prefix = "!"
        for line in str(help_text).splitlines():
            if line.startswith("[C] Prefijo principal: "):
                prefix = line.removeprefix("[C] Prefijo principal: ").split(" (", 1)[0].strip() or "!"
                break
        return (
            "[CB] ✦ KATHERINE · GUÍA DE COMANDOS ✦\n"
            f"[C] Categoría no reconocida. Disponibles: ia, genshin, social, juegos, musica, multimedia, admin.\n"
            f"[C] Ejemplo: {prefix}ayuda genshin."
        )

    prefix = "!"
    for line in str(help_text).splitlines():
        if line.startswith("[C] Prefijo principal: "):
            prefix = line.removeprefix("[C] Prefijo principal: ").split(" (", 1)[0].strip() or "!"
            break

    _, sections = _command_help_sections(prefix)
    title, entries = sections[category_index]
    return "\n".join([
        *_intro_lines(prefix),
        SEPARATOR,
        "[C]",
        *_format_section(title, entries),
    ])
