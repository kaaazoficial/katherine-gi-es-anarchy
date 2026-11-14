"""Plain-text command help and category filtering for Katherine."""
from __future__ import annotations

import unicodedata


def _command_help_groups(prefix: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Return the canonical command catalog grouped by category."""
    prefix = str(prefix or "!").strip() or "!"
    groups = [
        ("AYUDA E INTELIGENCIA ARTIFICIAL", [
            f"{prefix}ayuda [categoria] / {prefix}comandos / {prefix}menu / {prefix}menú — muestra esta guía o una categoría.",
            f"{prefix}asistente <consulta> — conversación y solicitudes en lenguaje natural.",
            f"{prefix}ia ayuda — guía; {prefix}ia preguntar <consulta> — chat; {prefix}ia genshin <consulta> — consulta de Teyvat.",
            f"{prefix}ia estado — proveedor y sesión; {prefix}ia olvidar — borra contexto temporal.",
            f"{prefix}ia investigar <tema> | {prefix}ia enlace <URL> | {prefix}ia musica <estilo> | {prefix}ia traducir <idioma> | <texto>.",
            f"{prefix}investigar <tema> | {prefix}investiga <tema> | {prefix}investigacion <tema> | {prefix}investigación <tema> — investigación: comparar, verificar, cronologia o informe.",
            f"{prefix}enlace <URL> | {prefix}analizarenlace <URL> | {prefix}leerweb <URL> — analiza una página pública.",
            f"{prefix}ia probar | {prefix}ia trazas | {prefix}ia guardian | {prefix}ia analista | {prefix}ia diagnostico — diagnóstico y controles [ADMIN].",
        ]),
        ("GENSHIN IMPACT", [
            f"{prefix}guia <personaje> — build y talentos; {prefix}materiales <personaje> — materiales.",
            f"{prefix}equipos <personaje> | {prefix}rotacion <personaje> | {prefix}sinergia <personaje> — equipos, rotación y sinergias.",
            f"{prefix}jefes — jefes; {prefix}banner — banners; {prefix}calendario — eventos y calendario.",
            f"{prefix}cobertura <personaje> — contenido; {prefix}reacciones <elemento> — reacciones.",
            f"{prefix}enciclopedia <nombre> | {prefix}archivo <nombre> | {prefix}teyvat <nombre> — consulta la base de datos.",
            f"{prefix}buscargenshin <texto> — búsqueda; {prefix}lista <categoría> [página] — listados.",
            f"{prefix}enemigo <nombre> | {prefix}geografia <nombre> | {prefix}elemento <nombre> — fichas de datos; {prefix}curiosidad — dato aleatorio.",
            f"{prefix}deseo — simulador; {prefix}pity — consulta contador; {prefix}resetpity — reinicia el contador.",
        ]),
        ("SOCIAL Y PERFILES", [
            f"{prefix}perfil [@usuario] — consulta perfil; {prefix}bio <texto> — edita biografía.",
            f"{prefix}miestado <texto> — actualiza estado; {prefix}actividad [hoy|semana] — actividad.",
            f"{prefix}interaccion @usuario — acciones sociales; también puedes usar comandos directos:",
            f"{prefix}abrazo|{prefix}abrazar|{prefix}beso|{prefix}besar|{prefix}patada|{prefix}golpe|{prefix}cachetada @usuario.",
            f"{prefix}acariciar|{prefix}mimar|{prefix}morder|{prefix}consolar|{prefix}felicitar|{prefix}saludar|{prefix}proteger @usuario.",
            f"{prefix}bailar|{prefix}cosquillas|{prefix}chocar|{prefix}highfive|{prefix}ayudar|{prefix}regalar|{prefix}acompañar @usuario.",
            f"{prefix}asustar|{prefix}curar|{prefix}cargar|{prefix}empujar|{prefix}regañar|{prefix}admirar|{prefix}apoyar @usuario.",
            f"{prefix}dormir|{prefix}ignorar|{prefix}mirar|{prefix}invitar @usuario.",
            f"{prefix}roast @usuario — broma; {prefix}afinidad @usuario | {prefix}amistad @usuario — vínculo.",
            f"{prefix}regalo @usuario [mensaje] — envía regalo; {prefix}muro — regalos recibidos.",
        ]),
        ("JUEGOS, ECONOMÍA Y COMUNIDAD", [
            f"{prefix}trivia / {prefix}responder <1-4> — trivia; {prefix}adivina / {prefix}intento <número> — adivinanza.",
            f"{prefix}puntos | {prefix}reputacion — saldo; {prefix}ranking | {prefix}top — clasificación; {prefix}diario | {prefix}daily — recompensa diaria.",
            f"{prefix}trabajar — recompensa; {prefix}pagar @usuario <cantidad> | {prefix}transferir @usuario <cantidad> — transfiere puntos; {prefix}economia — resumen.",
            f"{prefix}encuesta <pregunta> | <opciones> — votación; {prefix}elige <opciones> — elección aleatoria.",
            f"{prefix}duelo @usuario — reta; {prefix}ship <usuario1> <usuario2> — compatibilidad.",
            f"{prefix}azar <mínimo> <máximo> — número; {prefix}verdad — pregunta; {prefix}dado / {prefix}moneda — azar.",
            f"{prefix}8ball <pregunta> — respuesta; {prefix}chiste / {prefix}frase / {prefix}reto / {prefix}suerte — entretenimiento.",
            f"{prefix}evento|{prefix}eventos — eventos de comunidad; crear, consultar estado o cerrar según permisos.",
        ]),
        ("MÚSICA Y VOZ", [
            f"{prefix}join — entra a la llamada; {prefix}salirsala — sale; {prefix}salas — lista salas.",
            f"{prefix}dj buscar <tema> — busca música; {prefix}reproducir <pista> — reproduce archivo local.",
            f"{prefix}dj pausa|reanudar|saltar|cola|actual|detener — controles de reproducción.",
            f"{prefix}pausa | {prefix}pausar | {prefix}reanudar | {prefix}continuar | {prefix}saltar | {prefix}cola — accesos rápidos; {prefix}djdetener | {prefix}djstop — detiene música sin salir.",
            f"{prefix}playlist <acción> — listas; {prefix}volumen | {prefix}volume <0-100> — volumen.",
            f"{prefix}subirvolumen | {prefix}bajarvolumen | {prefix}silencio — controles rápidos; {prefix}leer | {prefix}noleer — lectura de chat.",
            f"{prefix}silenciolectura | {prefix}subirvolumenlectura | {prefix}bajarvolumenlectura — ajustes de lectura.",
            f"{prefix}tts voces|voz|velocidad|volumen — configuración de voz y lectura.",
        ]),
        ("UTILIDADES Y MULTIMEDIA", [
            f"{prefix}fecha — fecha y hora; {prefix}zona <Area/Ciudad> — hora de otra zona.",
            f"{prefix}traducir <idioma> | <texto> — traducción; {prefix}multimedia imagen|video|audio <búsqueda> — busca recursos.",
            f"{prefix}media <tipo> <búsqueda> — alias multimedia; {prefix}imagen <búsqueda> | {prefix}video <búsqueda> | {prefix}audio <búsqueda> — accesos directos.",
        ]),
        ("ADMINISTRACIÓN · UID AUTORIZADO", [
            f"{prefix}miuid | {prefix}mi-id | {prefix}mi_uid — consulta UID; {prefix}estado — estado general.",
            f"{prefix}fuentes | {prefix}saludfuentes — proveedores; {prefix}metricas — métricas; {prefix}autotest — pruebas internas.",
            f"{prefix}diagnostico — almacenamiento, integridad de SQLite, configuración y proveedores [ADMIN].",
            f"{prefix}centro | {prefix}nexus — panel de investigación; {prefix}admin | {prefix}paneladmin | {prefix}guardian — herramientas administrativas.",
            f"{prefix}antispam — protección; {prefix}advertir | {prefix}advertencias | {prefix}quitaradvertencia — moderación.",
            f"{prefix}respuestas | {prefix}respuesta — reglas automáticas; {prefix}perfilbot — perfil; {prefix}avatarbot — avatar.",
            f"{prefix}eliminarmensajes — limpia mensajes propios identificados en esta ejecución.",
            "Los comandos ADMIN requieren un UID autorizado incluido en ADMIN_UIDS. No se autoriza por nickname.",
        ]),
    ]

    return prefix, groups

def _utf16_length(value: str) -> int:
    """Count message length the same way Anarchy's UTF-16 API does."""
    return len(value.encode("utf-16-le")) // 2


def build_commands_help_text(prefix: str = "!") -> str:
    """Complete command index in compact plain-text; no interactive buttons."""
    prefix, groups = _command_help_groups(prefix)
    lines = [
        "KATHERINE · LISTA COMPLETA DE COMANDOS",
        f"Prefijo principal: {prefix} · También acepta ! y -.",
        "Los argumentos entre < > son obligatorios; [ ] son opcionales.",
        "",
    ]
    for title, entries in groups:
        lines.append(f"── {title} ──")
        lines.extend(entries)
        lines.append("")
    lines.append(
        f"Guía de texto: {prefix}ayuda, {prefix}comandos y {prefix}menu. "
        "sin paneles ni botones."
    )
    return "\n".join(lines)


def build_commands_help_blocks(prefix: str = "!", max_units: int = 1800) -> list[str]:
    """Build ordered standalone messages, never mixing adjacent categories."""
    if max_units < 100:
        raise ValueError("El tamaño mínimo de bloque debe ser al menos 100 unidades UTF-16.")

    prefix, groups = _command_help_groups(prefix)
    intro = (
        "KATHERINE · LISTA DE COMANDOS\n"
        f"Prefijo principal: {prefix} · También acepta ! y -.\n"
        "Los argumentos entre < > son obligatorios; [ ] son opcionales."
    )
    blocks: list[str] = []

    for title, entries in groups:
        current_header = f"{intro}\n\n── {title} ──"
        current_lines: list[str] = []

        for entry in entries:
            candidate_lines = [*current_lines, entry]
            candidate = current_header + "\n" + "\n".join(candidate_lines)
            if _utf16_length(candidate) <= max_units:
                current_lines.append(entry)
                continue

            if not current_lines:
                raise ValueError(
                    f"Una línea de la categoría {title!r} supera el límite de bloque."
                )

            blocks.append(current_header + "\n" + "\n".join(current_lines))
            current_header = (
                "KATHERINE · LISTA DE COMANDOS (CONTINUACIÓN)\n"
                f"Prefijo principal: {prefix} · También acepta ! y -.\n"
                f"── {title} · CONTINUACIÓN ──"
            )
            current_lines = [entry]
            continued = current_header + "\n" + entry
            if _utf16_length(continued) > max_units:
                raise ValueError(
                    f"Una línea de continuación de {title!r} supera el límite de bloque."
                )

        if current_lines:
            blocks.append(current_header + "\n" + "\n".join(current_lines))

    return blocks




_CATEGORIES = {
    "ia": "AYUDA E INTELIGENCIA ARTIFICIAL",
    "inteligencia": "AYUDA E INTELIGENCIA ARTIFICIAL",
    "genshin": "GENSHIN IMPACT",
    "teyvat": "GENSHIN IMPACT",
    "social": "SOCIAL Y PERFILES",
    "perfiles": "SOCIAL Y PERFILES",
    "juegos": "JUEGOS, ECONOMÍA Y COMUNIDAD",
    "economia": "JUEGOS, ECONOMÍA Y COMUNIDAD",
    "comunidad": "JUEGOS, ECONOMÍA Y COMUNIDAD",
    "musica": "MÚSICA Y VOZ",
    "voz": "MÚSICA Y VOZ",
    "multimedia": "UTILIDADES Y MULTIMEDIA",
    "utilidades": "UTILIDADES Y MULTIMEDIA",
    "admin": "ADMINISTRACIÓN · UID AUTORIZADO",
    "administracion": "ADMINISTRACIÓN · UID AUTORIZADO",
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


def filter_commands_help_text(help_text: str, topic: str | None = None) -> str:
    """Return one help section, or all sections when topic is empty/all."""
    requested = _normalize(topic or "")
    if not requested or requested in {"todo", "todos", "completo", "all"}:
        return str(help_text)
    title = _CATEGORIES.get(requested)
    if title is None:
        options = ", ".join(sorted(_CATEGORIES))
        return f"Categoría no reconocida. Prueba: {options}."
    lines = str(help_text).splitlines()
    section_start = None
    section_end = None
    for index, line in enumerate(lines):
        if line.startswith("── ") and line.endswith(" ──"):
            current = line[3:-3]
            if current == title:
                section_start = index
            elif section_start is not None:
                section_end = index
                break
    if section_start is None:
        return "No encontré esa categoría en la ayuda."
    if section_end is None:
        section_end = len(lines)
    footer = next(
        (line for line in reversed(lines) if line.startswith("Guía de texto:")),
        "",
    )
    section = lines[section_start:section_end]
    while section and not section[-1].strip():
        section.pop()
    return "\n".join([
        "KATHERINE · AYUDA POR CATEGORÍA",
        *section,
        "",
        footer,
    ])
