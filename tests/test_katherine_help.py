from katherine_help import (
    build_commands_help_blocks,
    build_commands_help_text,
    filter_commands_help_text,
)


def test_full_help_matches_the_supplied_anarchy_layout():
    text = build_commands_help_text()
    assert text.startswith("[CB] ✦ KATHERINE · GUÍA DE COMANDOS ✦\n[C] Prefijos aceptados: ! / -")
    assert "[C] Usa !ayuda [categoría] para consultar todo o filtrar una sección." in text
    expected = [
        "1. AYUDA E INTELIGENCIA ARTIFICIAL",
        "2. GENSHIN IMPACT",
        "3. SOCIAL Y PERFILES",
        "4. JUEGOS, ECONOMÍA Y COMUNIDAD",
        "5. MÚSICA Y VOZ",
        "6. UTILIDADES Y MULTIMEDIA",
        "7. ADMINISTRACIÓN Y ESTADO",
    ]
    positions = [text.index(f"[CB] ✦ {title} ✦") for title in expected]
    assert positions == sorted(positions)
    assert text.count("[C] ───────────── ✦ ─────────────") == 7
    assert "[CB] ▸ Ayuda y Navegación" in text
    assert "[CB] ▸ Reproductor DJ" in text
    assert "[CB] ▸ Moderación y Configuración" in text
    assert "!economia" not in text
    assert "!suerte" not in text
    assert "!azar <mín> <máx> | !azar dado | !azar moneda" in text
    assert text.count("!ayuda [categoría]") == 1
    assert "!menu" in text
    assert "sin paneles ni botones" in text
    assert "!dado" not in text and "!moneda" not in text and "!suerte" not in text
    assert "!abrazar" not in text and "!besar" not in text and "!highfive" not in text
    assert "!regalar" in text


def test_help_prefix_and_aliases_are_preserved():
    text = build_commands_help_text("$")
    assert "$ayuda [categoría]" in text
    assert "[C] Prefijos aceptados: $ / ! / -" in text
    assert "$dj probar <tema>" in text


def test_category_help_uses_same_visual_format():
    full = build_commands_help_text()
    for alias, title in [
        ("ia", "1. AYUDA E INTELIGENCIA ARTIFICIAL"),
        ("genshin", "2. GENSHIN IMPACT"),
        ("social", "3. SOCIAL Y PERFILES"),
        ("juegos", "4. JUEGOS, ECONOMÍA Y COMUNIDAD"),
        ("musica", "5. MÚSICA Y VOZ"),
        ("multimedia", "6. UTILIDADES Y MULTIMEDIA"),
        ("admin", "7. ADMINISTRACIÓN Y ESTADO"),
    ]:
        section = filter_commands_help_text(full, alias)
        assert section.startswith("[CB] ✦ KATHERINE · GUÍA DE COMANDOS ✦")
        assert f"[CB] ✦ {title} ✦" in section
        assert "[C] ───────────── ✦ ─────────────" in section


def test_unknown_category_uses_requested_layout_and_prefix():
    response = filter_commands_help_text(build_commands_help_text("$"), "inexistente")
    assert "[CB] ✦ KATHERINE · GUÍA DE COMANDOS ✦" in response
    assert "Ejemplo: $ayuda genshin." in response


def test_full_help_is_split_into_seven_category_messages():
    blocks = build_commands_help_blocks()
    assert len(blocks) == 7
    for i, block in enumerate(blocks):
        assert "[C] ───────────── ✦ ─────────────" in block
        assert f"[CB] ✦ {i + 1}." in block
        assert len(block.encode("utf-16-le")) // 2 <= 1800


def test_music_section_has_current_music_controls():
    music = filter_commands_help_text(build_commands_help_text(), "musica")
    for command in (
        "!dj buscar", "!dj pausa", "!dj reanudar", "!dj saltar",
        "!dj cola", "!dj actual", "!dj detener", "!dj fuentes",
        "!dj ambiente", "!dj favorito", "!dj probar", "!playlist",
        "!volumen", "!leer", "!noleer", "!tts", "!volumenlectura",
        "!subirvolumenlectura", "!bajarvolumenlectura", "!silenciolectura",
    ):
        assert command in music


def test_help_builders_cache_immutable_results_without_sharing_mutable_lists():
    first_text = build_commands_help_text("!")
    second_text = build_commands_help_text("!")
    assert first_text is second_text

    first_blocks = build_commands_help_blocks("!")
    second_blocks = build_commands_help_blocks("!")
    assert first_blocks == second_blocks
    assert first_blocks is not second_blocks
    first_blocks.pop()
    assert len(build_commands_help_blocks("!")) == 7

    first_category = filter_commands_help_text(first_text, "musica")
    second_category = filter_commands_help_text(second_text, "musica")
    assert first_category is second_category
    assert "!dj buscar" in first_category
