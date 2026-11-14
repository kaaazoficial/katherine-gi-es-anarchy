"""Anarchy-formatted command guide for Katherine."""
from __future__ import annotations

import re
import unicodedata


_SEPARATOR = "[C] ══════════════════════════════"
_CATEGORY_TITLES = (
    "1. AYUDA E INTELIGENCIA ARTIFICIAL",
    "2. GENSHIN IMPACT",
    "3. SOCIAL Y PERFILES",
    "4. JUEGOS, ECONOMÍA Y COMUNIDAD",
    "5. MÚSICA Y VOZ",
    "6. UTILIDADES Y MULTIMEDIA",
    "7. ADMINISTRACIÓN (UID AUTORIZADO)",
)


def _command_help_groups(prefix: str) -> tuple[str, list[tuple[str, list[tuple[bool, str]]]]]:
    """Return the command catalog grouped exactly as the public help presents it."""
    prefix = str(prefix or "!").strip() or "!"

    def cmd(name: str) -> str:
        return f"{prefix}{name}"

    groups: list[tuple[str, list[tuple[bool, str]]]] = [
        (_CATEGORY_TITLES[0], [
            (True, "▸ Ayuda y Navegación"),
            (False, f"• {cmd('ayuda')} <categoría> — Muestra una sección concreta; sin categoría muestra toda la guía."),
            (False, f"• {cmd('comandos')} / {cmd('menu')} / {cmd('menú')} — Muestra el catálogo completo."),
            (True, "▸ Asistente IA"),
            (False, f"• {cmd('asistente')} <consulta> — Conversación y solicitudes en lenguaje natural."),
            (False, f"• {cmd('ia ayuda')} — Guía de IA."),
            (False, f"• {cmd('ia preguntar')} <consulta> — Consulta directa al asistente."),
            (False, f"• {cmd('ia genshin')} <consulta> — Consulta especializada de Teyvat."),
            (False, f"• {cmd('ia estado')} — Revisa el proveedor y la sesión actual."),
            (False, f"• {cmd('ia olvidar')} — Borra el contexto temporal de la charla."),
            (True, "▸ Investigación y Herramientas"),
            (False, f"• {cmd('investigar')} <tema> — Investigación: comparar, verificar, cronología o informe."),
            (False, f"• {cmd('analizartexto')} <modo> <texto> — Resumir, corregir, reescribir, extraer o contradicciones."),
            (False, f"• {cmd('calcular')} <operación> — Porcentajes, raíces, trigonometría y matemáticas."),
            (True, "▸ Administración de IA [ADMIN]"),
            (False, f"• {cmd('ia probar')} | {cmd('ia guardian')} | {cmd('ia diagnostico')} — Diagnóstico y controles del sistema."),
        ]),
        (_CATEGORY_TITLES[1], [
            (True, "▸ Personajes y Equipos"),
            (False, f"• {cmd('guia')} <personaje> — Builds y talentos."),
            (False, f"• {cmd('materiales')} <personaje> — Materiales de ascensión."),
            (False, f"• {cmd('equipos')} <personaje> — Composiciones de equipo."),
            (False, f"• {cmd('rotacion')} <personaje> — Rotaciones de combate."),
            (False, f"• {cmd('sinergia')} <personaje> — Sinergias y compañeros recomendados."),
            (True, "▸ Mundo y Datos"),
            (False, f"• {cmd('jefes')} — Lista de jefes."),
            (False, f"• {cmd('banner')} — Banners activos."),
            (False, f"• {cmd('calendario')} — Eventos y fechas."),
            (False, f"• {cmd('cobertura')} <personaje> — Contenido del personaje."),
            (False, f"• {cmd('reacciones')} <elemento> — Reacciones elementales."),
            (False, f"• {cmd('enciclopedia')} | {cmd('archivo')} | {cmd('teyvat')} <nombre> — Fichas y datos del lore."),
            (False, f"• {cmd('buscargenshin')} <texto> — Búsqueda general."),
            (False, f"• {cmd('lista')} <categoría> [página] — Listados por categoría."),
            (False, f"• {cmd('enemigo')} | {cmd('geografia')} | {cmd('elemento')} <nombre> — Consultas específicas del juego."),
            (False, f"• {cmd('curiosidad')} — Dato aleatorio de Genshin Impact."),
            (True, "▸ Deseos (Gachapón)"),
            (False, f"• {cmd('deseo')} — Simulador de tiradas."),
            (False, f"• {cmd('pity')} — Consulta tu contador actual."),
            (False, f"• {cmd('resetpity')} — Reinicia tu contador."),
        ]),
        (_CATEGORY_TITLES[2], [
            (True, "▸ Perfil"),
            (False, f"• {cmd('perfil')} [@usuario] — Consulta un perfil."),
            (False, f"• {cmd('bio')} <texto> — Edita tu biografía."),
            (False, f"• {cmd('miestado')} <texto> — Actualiza tu estado."),
            (False, f"• {cmd('actividad')} [hoy|semana] — Consulta la actividad."),
            (True, "▸ Interacciones (@usuario)"),
            (False, f"• {cmd('interaccion')} @usuario — Guía de acciones sociales."),
            (False, f"• Afecto: {cmd('abrazo')} | {cmd('abrazar')} | {cmd('beso')} | {cmd('besar')} | {cmd('acariciar')} | {cmd('mimar')}"),
            (False, f"• Físicas: {cmd('patada')} | {cmd('golpe')} | {cmd('cachetada')} | {cmd('morder')} | {cmd('empujar')}"),
            (False, f"• Apoyo: {cmd('consolar')} | {cmd('felicitar')} | {cmd('saludar')} | {cmd('proteger')} | {cmd('ayudar')} | {cmd('apoyar')}"),
            (False, f"• Sociales: {cmd('bailar')} | {cmd('cosquillas')} | {cmd('chocar')} | {cmd('highfive')} | {cmd('acompañar')}"),
            (False, f"• Otras: {cmd('asustar')} | {cmd('curar')} | {cmd('cargar')} | {cmd('regañar')} | {cmd('admirar')} | {cmd('dormir')}"),
            (False, f"• Adicionales: {cmd('ignorar')} | {cmd('mirar')} | {cmd('invitar')}"),
            (True, "▸ Vínculos y Regalos"),
            (False, f"• {cmd('roast')} @usuario — Lanza una broma."),
            (False, f"• {cmd('afinidad')} @usuario — Mide el vínculo con alguien."),
            (False, f"• {cmd('regalo')} @usuario [mensaje] — Envía un regalo."),
            (False, f"• {cmd('muro')} — Consulta los regalos recibidos."),
        ]),
        (_CATEGORY_TITLES[3], [
            (True, "▸ Juegos y Entretenimiento"),
            (False, f"• {cmd('trivia')} — Inicia trivia | {cmd('responder')} <1-4> — Responde."),
            (False, f"• {cmd('adivina')} — Inicia adivinanza | {cmd('intento')} <número> — Prueba tu respuesta."),
            (False, f"• {cmd('duelo')} @usuario — Reta a alguien."),
            (False, f"• {cmd('ship')} <usuario1> <usuario2> — Calcula compatibilidad."),
            (False, f"• {cmd('azar')} <mín> <máx> | {cmd('dado')} | {cmd('moneda')} — Juegos de azar."),
            (False, f"• {cmd('verdad')} | {cmd('8ball')} <pregunta> — Preguntas al azar."),
            (False, f"• {cmd('elige')} <opciones> — Elección aleatoria."),
            (False, f"• {cmd('chiste')} | {cmd('frase')} | {cmd('reto')} | {cmd('suerte')} — Entretenimiento rápido."),
            (True, "▸ Economía"),
            (False, f"• {cmd('puntos')} — Consulta tu saldo."),
            (False, f"• {cmd('ranking')} — Clasificación general."),
            (False, f"• {cmd('diario')} — Reclama tu recompensa diaria."),
            (False, f"• {cmd('trabajar')} — Gana puntos."),
            (False, f"• {cmd('pagar')} @usuario <cantidad> — Transfiere puntos."),
            (False, f"• {cmd('economia')} — Resumen de la economía comunitaria."),
            (True, "▸ Comunidad"),
            (False, f"• {cmd('encuesta')} <pregunta> | <opciones> — Crea una votación."),
            (False, f"• {cmd('evento')} | {cmd('eventos')} — Consulta, crea o cierra eventos (según permisos)."),
        ]),
        (_CATEGORY_TITLES[4], [
            (True, "▸ Llamadas"),
            (False, f"• {cmd('join')} — Entra a la llamada | {cmd('salirsala')} — Sale de ella."),
            (False, f"• {cmd('salas')} — Lista las salas disponibles."),
            (False, f"• {cmd('voz participantes')} — Muestra quién está en la llamada."),
            (False, f"• {cmd('voz hablando')} — Muestra quién está hablando."),
            (False, f"• {cmd('voz estado')} — Estado actual de la llamada."),
            (True, "▸ Reproductor DJ"),
            (False, f"• {cmd('dj buscar')} <tema> — Busca música en los catálogos disponibles."),
            (False, f"• {cmd('reproducir')} <pista> — Reproduce una pista local."),
            (False, f"• {cmd('dj pausa')} | {cmd('dj reanudar')} — Pausa o continúa la reproducción."),
            (False, f"• {cmd('dj saltar')} | {cmd('dj cola')} | {cmd('dj actual')} — Controla y consulta la cola."),
            (False, f"• {cmd('dj detener')} — Detiene la música sin salir de la llamada."),
            (False, f"• {cmd('dj fuentes')} — Consulta catálogos y su estado."),
            (False, f"• {cmd('dj ambiente')} <mood> — Recomienda música por ambiente."),
            (False, f"• {cmd('dj favorito')} lista|guardar <número>|reproducir <id>|quitar <id> — Administra favoritos."),
            (False, f"• {cmd('dj probar')} <tema> — Prueba búsqueda, descarga, validación y reproducción [ADMIN]."),
            (True, "▸ Listas y Volumen"),
            (False, f"• {cmd('playlist')} crear|lista|ver|reproducir|borrar <nombre> — Administra listas locales."),
            (False, f"• {cmd('playlist agregar')} <nombre> | <archivo local> — Añade un archivo existente."),
            (False, f"• {cmd('volumen')} <0-100> — Ajusta el volumen exacto."),
            (False, f"• {cmd('subirvolumen')} | {cmd('bajarvolumen')} | {cmd('silencio')} — Controles rápidos."),
            (True, "▸ Lectura y TTS (Texto a Voz)"),
            (False, f"• {cmd('leer')} | {cmd('noleer')} — Activa o desactiva la lectura de mensajes."),
            (False, f"• {cmd('volumenlectura')} <0-100> — Fija el volumen de lectura."),
            (False, f"• {cmd('subirvolumenlectura')} [cantidad] | {cmd('bajarvolumenlectura')} [cantidad] — Ajusta el volumen."),
            (False, f"• {cmd('silenciolectura')} — Silencia la lectura."),
            (False, f"• {cmd('tts')} voces|voz|velocidad|volumen — Configuración de voz."),
        ]),
        (_CATEGORY_TITLES[5], [
            (True, "▸ Fecha, Hora e Idiomas"),
            (False, f"• {cmd('fecha')} — Fecha y hora actual."),
            (False, f"• {cmd('zona')} <Area/Ciudad> — Hora de otra zona horaria."),
            (False, f"• {cmd('traducir')} <idioma> | <texto> — Traduce un texto."),
            (True, "▸ Búsqueda Multimedia"),
            (False, f"• {cmd('multimedia')} imagen|video|audio <búsqueda> — Búsqueda por tipo de recurso."),
            (False, f"• {cmd('imagen')} <búsqueda> — Busca imágenes."),
            (False, f"• {cmd('video')} <búsqueda> — Busca vídeos."),
            (False, f"• {cmd('audio')} <búsqueda> — Busca recursos de audio."),
        ]),
        (_CATEGORY_TITLES[6], [
            (True, "▸ Identidad y Estado"),
            (False, f"• {cmd('miuid')} | {cmd('mi-id')} | {cmd('mi_uid')} — Consulta tu UID."),
            (False, f"• {cmd('estado')} — Estado general del bot."),
            (False, f"• {cmd('diagnostico')} — Almacenamiento, SQLite y proveedores [ADMIN]."),
            (True, "▸ Fuentes y Paneles"),
            (False, f"• {cmd('fuentes')} | {cmd('saludfuentes')} — Estado de proveedores."),
            (False, f"• {cmd('metricas')} — Métricas del sistema."),
            (False, f"• {cmd('autotest')} — Pruebas internas."),
            (False, f"• {cmd('centro')} | {cmd('nexus')} — Paneles de investigación."),
            (False, f"• {cmd('admin')} | {cmd('paneladmin')} | {cmd('guardian')} — Paneles de control."),
            (True, "▸ Moderación y Configuración"),
            (False, f"• {cmd('antispam')} — Protección contra spam."),
            (False, f"• {cmd('advertir')} | {cmd('advertencias')} | {cmd('quitaradvertencia')} — Sistema de moderación."),
            (False, f"• {cmd('respuestas')} | {cmd('respuesta')} — Reglas automáticas."),
            (False, f"• {cmd('perfilbot')} | {cmd('avatarbot')} — Edita el perfil e icono del bot."),
            (False, f"• {cmd('eliminarmensajes')} — Elimina mensajes propios de esta ejecución."),
            (False, "Nota: Los comandos ADMIN requieren un UID autorizado en ADMIN_UIDS (el nickname no concede permisos)."),
        ]),
    ]
    return prefix, groups


def _intro_lines(prefix: str) -> list[str]:
    return [
        "[CB] KATHERINE · GUÍA DE COMANDOS",
        f"[C] Prefijo principal: {prefix} (También acepta ! y -)",
        f"[C] Usa {prefix}ayuda <categoría> para consultar una sola sección.",
        "[C]",
    ]


def _format_section(title: str, entries: list[tuple[bool, str]]) -> list[str]:
    lines = [f"[CB] {title}", "[C]"]
    for is_heading, value in entries:
        lines.append(f"[CB] {value}" if is_heading else f"[C] {value}")
    return lines


def build_commands_help_text(prefix: str = "!") -> str:
    """Build the complete seven-section guide using Anarchy's [CB]/[C] markers."""
    prefix, groups = _command_help_groups(prefix)
    lines = _intro_lines(prefix)
    for index, (title, entries) in enumerate(groups):
        if index:
            lines.extend(["[C]", _SEPARATOR, "[C]"])
        lines.extend(_format_section(title, entries))
    return "\n".join(lines)


def _utf16_length(value: str) -> int:
    """Count message length in UTF-16 code units."""
    return len(value.encode("utf-16-le")) // 2


def build_commands_help_blocks(prefix: str = "!", max_units: int = 1800) -> list[str]:
    """Send one self-contained Anarchy message per category, respecting API limits."""
    if max_units < 200:
        raise ValueError("El tamaño mínimo de bloque debe ser al menos 200 unidades UTF-16.")

    prefix, groups = _command_help_groups(prefix)
    intro = _intro_lines(prefix)
    blocks: list[str] = []
    for title, entries in groups:
        section = _format_section(title, entries)
        block = "\n".join([*intro, *section])
        if _utf16_length(block) <= max_units:
            blocks.append(block)
            continue

        current = [*intro, f"[CB] {title} · CONTINUACIÓN", "[C]"]
        for is_heading, value in entries:
            line = f"[CB] {value}" if is_heading else f"[C] {value}"
            candidate = "\n".join([*current, line])
            if _utf16_length(candidate) > max_units and len(current) > len(intro) + 2:
                blocks.append("\n".join(current))
                current = [*intro, f"[CB] {title} · CONTINUACIÓN", "[C]", line]
            elif _utf16_length(candidate) > max_units:
                raise ValueError(f"Una línea de {title!r} supera el límite del mensaje.")
            else:
                current.append(line)
        if len(current) > len(intro) + 2:
            blocks.append("\n".join(current))
    return blocks


_CATEGORY_ALIASES = {
    "ia": 0,
    "inteligencia": 0,
    "inteligencia artificial": 0,
    "genshin": 1,
    "teyvat": 1,
    "personajes": 1,
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
    "administración": 6,
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


def filter_commands_help_text(help_text: str, topic: str | None = None) -> str:
    """Return the requested section in the same visual format as !comandos."""
    requested = _normalize(topic or "")
    if not requested or requested in {"todo", "todos", "completo", "all"}:
        return str(help_text)

    category_index = _CATEGORY_ALIASES.get(requested)
    if category_index is None:
        available = "ia, genshin, social, juegos, musica, multimedia, admin"
        prefix_match = re.search(r"Prefijo principal:\s*(.+?)\s*\(", str(help_text))
        prefix = prefix_match.group(1).strip() if prefix_match else "!"
        return (
            "[CB] KATHERINE · AYUDA POR CATEGORÍA\\n"
            f"[C] Categoría no reconocida. Disponibles: {available}.\\n"
            f"[C] Ejemplo: {prefix}ayuda genshin."
        )

    prefix_match = re.search(r"Prefijo principal:\s*(.+?)\s*\(", str(help_text))
    prefix = prefix_match.group(1).strip() if prefix_match else "!"
    _, groups = _command_help_groups(prefix)
    title, entries = groups[category_index]
    return "\n".join([*_intro_lines(prefix), *_format_section(title, entries)])
