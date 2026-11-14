"""Tests for Katherine's Anarchy-formatted command help."""
from katherine_help import (
    build_commands_help_blocks,
    build_commands_help_text,
    filter_commands_help_text,
)


def test_help_has_seven_requested_categories_and_anarchy_markers():
    text = build_commands_help_text()
    assert text.startswith("[CB] KATHERINE · GUÍA DE COMANDOS")
    assert "[C] Prefijo principal: !" in text
    assert "[CB] 1. AYUDA E INTELIGENCIA ARTIFICIAL" in text
    assert "[CB] 2. GENSHIN IMPACT" in text
    assert "[CB] 3. SOCIAL Y PERFILES" in text
    assert "[CB] 4. JUEGOS, ECONOMÍA Y COMUNIDAD" in text
    assert "[CB] 5. MÚSICA Y VOZ" in text
    assert "[CB] 6. UTILIDADES Y MULTIMEDIA" in text
    assert "[CB] 7. ADMINISTRACIÓN (UID AUTORIZADO)" in text
    assert "[C] ══════════════════════════════" in text


def test_help_supports_custom_prefix_and_all_aliases():
    text = build_commands_help_text("$")
    assert "$ayuda <categoría>" in text
    assert "$comandos / $menu / $menú" in text
    assert "$diagnostico" in text
    assert "$dj probar" in text


def test_help_category_aliases_return_only_requested_section():
    full = build_commands_help_text()
    for alias, expected in [
        ("ia", "1. AYUDA E INTELIGENCIA ARTIFICIAL"),
        ("genshin", "2. GENSHIN IMPACT"),
        ("social", "3. SOCIAL Y PERFILES"),
        ("juegos", "4. JUEGOS, ECONOMÍA Y COMUNIDAD"),
        ("musica", "5. MÚSICA Y VOZ"),
        ("voz", "5. MÚSICA Y VOZ"),
        ("multimedia", "6. UTILIDADES Y MULTIMEDIA"),
        ("admin", "7. ADMINISTRACIÓN"),
    ]:
        section = filter_commands_help_text(full, alias)
        assert expected in section
        assert section.startswith("[CB] KATHERINE · GUÍA DE COMANDOS")
        assert "Prefijo principal: !" in section
        assert sum(line.startswith("[CB] ") and line[5:6].isdigit() for line in section.splitlines()) == 1


def test_unknown_category_includes_concrete_example_and_custom_prefix():
    response = filter_commands_help_text(build_commands_help_text("$"), "inexistente")
    assert "Categoría no reconocida" in response
    assert "Ejemplo: $ayuda genshin" in response


def test_help_without_topic_returns_complete_catalog():
    text = build_commands_help_text()
    assert filter_commands_help_text(text, None) == text
    assert filter_commands_help_text(text, "todos") == text


def test_full_help_is_sent_as_seven_ordered_category_blocks():
    blocks = build_commands_help_blocks("$")
    assert len(blocks) == 7
    expected = [
        "1. AYUDA E INTELIGENCIA ARTIFICIAL",
        "2. GENSHIN IMPACT",
        "3. SOCIAL Y PERFILES",
        "4. JUEGOS, ECONOMÍA Y COMUNIDAD",
        "5. MÚSICA Y VOZ",
        "6. UTILIDADES Y MULTIMEDIA",
        "7. ADMINISTRACIÓN (UID AUTORIZADO)",
    ]
    for block, category in zip(blocks, expected):
        assert "[CB] KATHERINE · GUÍA DE COMANDOS" in block
        assert f"[CB] {category}" in block
        assert len(block.encode("utf-16-le")) // 2 <= 1800


def test_music_help_includes_all_current_dj_and_tts_controls():
    music = filter_commands_help_text(build_commands_help_text(), "musica")
    for command in (
        "!dj buscar", "!dj pausa", "!dj reanudar", "!dj saltar",
        "!dj cola", "!dj actual", "!dj detener", "!dj fuentes",
        "!dj ambiente", "!dj favorito", "!dj probar", "!playlist",
        "!volumen", "!leer", "!noleer", "!volumenlectura",
        "!subirvolumenlectura", "!bajarvolumenlectura",
        "!silenciolectura", "!tts",
    ):
        assert command in music


def test_small_limit_splits_long_category_without_mixing_categories():
    blocks = build_commands_help_blocks(max_units=850)
    music_blocks = [block for block in blocks if "5. MÚSICA Y VOZ" in block]
    assert len(music_blocks) >= 1
    for block in music_blocks:
        assert "2. GENSHIN IMPACT" not in block
        assert "3. SOCIAL Y PERFILES" not in block
        assert len(block.encode("utf-16-le")) // 2 <= 850
