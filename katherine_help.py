"""Plain-text command help and category filtering for Katherine."""
from __future__ import annotations

import unicodedata


def _command_help_groups(prefix: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Return the canonical command catalogue in readable, functional groups."""
    prefix = str(prefix or "!").strip() or "!"
    groups = [
        ("AYUDA E INTELIGENCIA ARTIFICIAL", [
            "AYUDA Y NAVEGACIÓN",
            f"• {prefix}ayuda [categoría] — muestra una sección concreta; sin categoría, muestra toda la guía.",
            f"• {prefix}comandos / {prefix}menu / {prefix}menú — muestran el catálogo completo.",
            "ASISTENTE",
            f"• {prefix}asistente <consulta> — conversación y solicitudes en lenguaje natural.",
            f"• {prefix}ia ayuda — guía de IA; {prefix}ia preguntar <consulta> — consulta al asistente.",
            f"• {prefix}ia genshin <consulta> — consulta especializada de Teyvat.",
            f"• {prefix}ia estado — proveedor y sesión; {prefix}ia olvidar — borra el contexto temporal.",
            "INVESTIGACIÓN Y HERRAMIENTAS",
            f"• {prefix}investigar <tema> — investigación con opciones comparar, verificar, cronología e informe.",
            f"• {prefix}analizartexto resumir|corregir|reescribir|extraer|contradicciones <texto> — análisis puntual.",
            f"• {prefix}calcular <operación> — porcentajes, raíces, trigonometría y operaciones matemáticas.",
            "ADMINISTRACIÓN DE IA",
            f"• {prefix}ia probar | {prefix}ia guardian | {prefix}ia diagnostico — diagnóstico y controles [ADMIN].",
        ]),
        ("GENSHIN IMPACT", [
            "PERSONAJES Y EQUIPOS",
            f"• {prefix}guia <personaje> — builds y talentos; {prefix}materiales <personaje> — materiales.",
            f"• {prefix}equipos <personaje> — equipos; {prefix}rotacion <personaje> — rotaciones.",
            f"• {prefix}sinergia <personaje> — sinergias y compañeros recomendados.",
            "MUNDO Y DATOS",
            f"• {prefix}jefes — jefes; {prefix}banner — banners; {prefix}calendario — eventos y fechas.",
            f"• {prefix}cobertura <personaje> — contenido; {prefix}reacciones <elemento> — reacciones elementales.",
            f"• {prefix}enciclopedia <nombre> | {prefix}archivo <nombre> | {prefix}teyvat <nombre> — fichas y datos.",
            f"• {prefix}buscargenshin <texto> — búsqueda; {prefix}lista <categoría> [página] — listados.",
            f"• {prefix}enemigo <nombre> | {prefix}geografia <nombre> | {prefix}elemento <nombre> — consultas del juego.",
            f"• {prefix}curiosidad — dato aleatorio de Genshin Impact.",
            "DESEOS",
            f"• {prefix}deseo — simulador; {prefix}pity — consulta el contador.",
            f"• {prefix}resetpity — reinicia tu contador.",
        ]),
        ("SOCIAL Y PERFILES", [
            "PERFIL",
            f"• {prefix}perfil [@usuario] — consulta un perfil; {prefix}bio <texto> — edita tu biografía.",
            f"• {prefix}miestado <texto> — actualiza tu estado; {prefix}actividad [hoy|semana] — consulta actividad.",
            "INTERACCIONES",
            f"• {prefix}interaccion @usuario — guía de acciones sociales.",
            f"• {prefix}abrazo|{prefix}abrazar|{prefix}beso|{prefix}besar|{prefix}acariciar|{prefix}mimar @usuario — afecto.",
            f"• {prefix}patada|{prefix}golpe|{prefix}cachetada|{prefix}morder|{prefix}empujar @usuario — acciones físicas.",
            f"• {prefix}consolar|{prefix}felicitar|{prefix}saludar|{prefix}proteger|{prefix}ayudar|{prefix}apoyar @usuario — apoyo.",
            f"• {prefix}bailar|{prefix}cosquillas|{prefix}chocar|{prefix}highfive|{prefix}acompañar @usuario — acciones sociales.",
            f"• {prefix}asustar|{prefix}curar|{prefix}cargar|{prefix}regañar|{prefix}admirar|{prefix}dormir @usuario — otras acciones.",
            f"• {prefix}ignorar|{prefix}mirar|{prefix}invitar @usuario — acciones adicionales.",
            "VÍNCULOS Y REGALOS",
            f"• {prefix}roast @usuario — broma; {prefix}afinidad @usuario — vínculo.",
            f"• {prefix}regalo @usuario [mensaje] — envía un regalo; {prefix}muro — consulta los recibidos.",
        ]),
        ("JUEGOS, ECONOMÍA Y COMUNIDAD", [
            "JUEGOS",
            f"• {prefix}trivia — inicia trivia; {prefix}responder <1-4> — responde.",
            f"• {prefix}adivina — inicia adivinanza; {prefix}intento <número> — prueba tu respuesta.",
            f"• {prefix}duelo @usuario — reta a alguien; {prefix}ship <usuario1> <usuario2> — compatibilidad.",
            f"• {prefix}azar <mínimo> <máximo> | {prefix}dado | {prefix}moneda — azar.",
            f"• {prefix}verdad | {prefix}8ball <pregunta> — preguntas; {prefix}elige <opciones> — elección aleatoria.",
            f"• {prefix}chiste | {prefix}frase | {prefix}reto | {prefix}suerte — entretenimiento.",
            "ECONOMÍA",
            f"• {prefix}puntos — saldo; {prefix}ranking — clasificación; {prefix}diario — recompensa diaria.",
            f"• {prefix}trabajar — gana puntos; {prefix}pagar @usuario <cantidad> — transfiere puntos.",
            f"• {prefix}economia — resumen de la economía comunitaria.",
            "COMUNIDAD",
            f"• {prefix}encuesta <pregunta> | <opciones> — crea una votación.",
            f"• {prefix}evento | {prefix}eventos — consulta, crea o cierra eventos según permisos.",
        ]),
        ("MÚSICA Y VOZ", [
            "LLAMADAS",
            f"• {prefix}join — entra a la llamada; {prefix}salirsala — sale de ella; {prefix}salas — lista salas.",
            f"• {prefix}voz participantes — muestra quién está en la llamada.",
            f"• {prefix}voz hablando — muestra quién está hablando; {prefix}voz estado — estado de la llamada.",
            "REPRODUCTOR DJ",
            f"• {prefix}dj buscar <tema> — busca música; {prefix}reproducir <pista> — reproduce una pista local.",
            f"• {prefix}dj pausa | {prefix}dj reanudar — pausa o continúa la reproducción.",
            f"• {prefix}dj saltar | {prefix}dj cola | {prefix}dj actual — controla y consulta la cola.",
            f"• {prefix}dj detener — detiene la música sin salir de la llamada.",
            f"• {prefix}dj fuentes — consulta catálogos y pausas temporales; {prefix}dj ambiente <mood> — recomienda por ambiente.",
            f"• {prefix}dj probar <tema> — prueba búsqueda, descarga y reproducción real [ADMIN].",
            f"• {prefix}dj favorito lista|guardar <número>|reproducir <id>|quitar <id> — administra favoritos.",
            "LISTAS Y VOLUMEN",
            f"• {prefix}playlist crear|lista|ver|reproducir|borrar <nombre> — administra playlists de archivos locales.",
            f"• {prefix}playlist agregar <nombre> | <archivo local> — añade un audio existente.",
            f"• {prefix}volumen <0-100> | {prefix}subirvolumen | {prefix}bajarvolumen | {prefix}silencio — controles de música.",
            "LECTURA Y TTS",
            f"• {prefix}leer | {prefix}noleer — activa o desactiva la lectura de mensajes.",
            f"• {prefix}volumenlectura <0-100> | {prefix}subirvolumenlectura [cantidad] | {prefix}bajarvolumenlectura [cantidad] — volumen de lectura.",
            f"• {prefix}silenciolectura — silencia la lectura; {prefix}tts voces|voz|velocidad|volumen — configuración de voz.",
        ]),
        ("UTILIDADES Y MULTIMEDIA", [
            "FECHA, HORA E IDIOMAS",
            f"• {prefix}fecha — fecha y hora; {prefix}zona <Area/Ciudad> — hora de otra zona.",
            f"• {prefix}traducir <idioma> | <texto> — traduce un texto.",
            "BÚSQUEDA MULTIMEDIA",
            f"• {prefix}multimedia imagen|video|audio <búsqueda> — búsqueda por tipo de recurso.",
            f"• {prefix}imagen <búsqueda> — imágenes; {prefix}video <búsqueda> — vídeos.",
            f"• {prefix}audio <búsqueda> — recursos de audio.",
        ]),
        ("ADMINISTRACIÓN · UID AUTORIZADO", [
            "IDENTIDAD Y ESTADO",
            f"• {prefix}miuid | {prefix}mi-id | {prefix}mi_uid — consulta tu UID.",
            f"• {prefix}estado — estado general; {prefix}diagnostico — almacenamiento, SQLite y proveedores [ADMIN].",
            "FUENTES Y PANELES",
            f"• {prefix}fuentes | {prefix}saludfuentes — estado de proveedores.",
            f"• {prefix}metricas — métricas; {prefix}autotest — pruebas internas.",
            f"• {prefix}centro | {prefix}nexus — investigación; {prefix}admin | {prefix}paneladmin | {prefix}guardian — paneles.",
            "MODERACIÓN Y CONFIGURACIÓN",
            f"• {prefix}antispam — protección antispam.",
            f"• {prefix}advertir | {prefix}advertencias | {prefix}quitaradvertencia — herramientas de moderación.",
            f"• {prefix}respuestas | {prefix}respuesta — reglas automáticas.",
            f"• {prefix}perfilbot — perfil; {prefix}avatarbot — icono del bot.",
            f"• {prefix}eliminarmensajes — elimina mensajes propios identificados en esta ejecución.",
            "Los comandos ADMIN requieren un UID autorizado en ADMIN_UIDS. El nickname no concede permisos.",
        ]),
    ]
    return prefix, groups


def _utf16_length(value: str) -> int:
    """Count message length the same way Anarchy's UTF-16 API does."""
    return len(value.encode("utf-16-le")) // 2


def _help_intro(prefix: str, *, continuation: bool = False) -> str:
    heading = "KATHERINE · GUÍA DE COMANDOS"
    if continuation:
        heading += " · CONTINUACIÓN"
    return (
        f"{heading}\n"
        f"Prefijo principal: {prefix} · También acepta ! y -.\n"
        f"Usa {prefix}ayuda <categoría> para consultar una sola sección."
    )


def build_commands_help_text(prefix: str = "!") -> str:
    """Complete command index with clear subsections and one command family per line."""
    prefix, groups = _command_help_groups(prefix)
    lines = [
        _help_intro(prefix),
        "",
    ]
    for title, entries in groups:
        lines.extend([f"── {title} ──", *entries, ""])
    lines.append(
        f"Guía de texto: {prefix}ayuda, {prefix}comandos y {prefix}menu. "
        "La ayuda se entrega en mensajes de texto, sin paneles ni botones."
    )
    return "\n".join(lines)


def build_commands_help_blocks(prefix: str = "!", max_units: int = 1800) -> list[str]:
    """Build ordered, readable category messages without mixing categories."""
    if max_units < 100:
        raise ValueError("El tamaño mínimo de bloque debe ser al menos 100 unidades UTF-16.")

    prefix, groups = _command_help_groups(prefix)
    intro = _help_intro(prefix)
    blocks: list[str] = []

    for title, entries in groups:
        category_header = f"── {title} ──"
        current_header = f"{intro}\n\n{category_header}"
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
            continuation_intro = _help_intro(prefix, continuation=True)
            current_header = (
                f"{continuation_intro}\n\n"
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
    """Return one consistently formatted category, or the complete guide."""
    requested = _normalize(topic or "")
    if not requested or requested in {"todo", "todos", "completo", "all"}:
        return str(help_text)

    title = _CATEGORIES.get(requested)
    if title is None:
        options = ", ".join(sorted(_CATEGORIES))
        prefix_match = unicodedata.normalize("NFKC", str(help_text)).splitlines()
        prefix = "!"
        if len(prefix_match) > 1 and prefix_match[1].startswith("Prefijo principal: "):
            prefix = prefix_match[1].split(":", 1)[1].split("·", 1)[0].strip() or "!"
        return (
            f"Categoría no reconocida. Disponibles: {options}.\n"
            f"Ejemplo: {prefix}ayuda genshin."
        )

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

    section = lines[section_start:section_end]
    while section and not section[-1].strip():
        section.pop()

    prefix = "!"
    for line in lines:
        if line.startswith("Prefijo principal: "):
            prefix = line.split(":", 1)[1].split("·", 1)[0].strip() or "!"
            break

    return "\n".join([
        "KATHERINE · AYUDA POR CATEGORÍA",
        f"Prefijo principal: {prefix} · También acepta ! y -.",
        "",
        *section,
    ])
