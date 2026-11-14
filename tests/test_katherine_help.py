"""Tests for Katherine's plain-text contextual command help."""
from katherine_help import build_commands_help_text, filter_commands_help_text


def test_help_supports_category_aliases_and_custom_prefix():
    text = build_commands_help_text("$")
    assert "$ayuda [categoria]" in text
    assert "$diagnostico" in text
    assert "$ayuda" in filter_commands_help_text(text, "administración")
    assert "GENSHIN IMPACT" not in filter_commands_help_text(text, "administración")


def test_help_unknown_category_returns_available_categories():
    response = filter_commands_help_text(build_commands_help_text(), "desconocida")
    assert "Categoría no reconocida" in response
    assert "genshin" in response


def test_help_without_topic_returns_full_catalog():
    text = build_commands_help_text()
    assert filter_commands_help_text(text, None) == text
    assert "MÚSICA Y VOZ" in text
    assert "SOCIAL Y PERFILES" in text



def test_full_command_help_is_split_into_standalone_category_blocks():
    from katherine_help import build_commands_help_blocks

    blocks = build_commands_help_blocks("$")
    expected_categories = [
        "AYUDA E INTELIGENCIA ARTIFICIAL",
        "GENSHIN IMPACT",
        "SOCIAL Y PERFILES",
        "JUEGOS, ECONOMÍA Y COMUNIDAD",
        "MÚSICA Y VOZ",
        "UTILIDADES Y MULTIMEDIA",
        "ADMINISTRACIÓN · UID AUTORIZADO",
    ]

    assert len(blocks) == len(expected_categories)
    for block, category in zip(blocks, expected_categories):
        assert "Prefijo principal: $" in block
        assert f"── {category} ──" in block
        assert _category_heading_count(block) == 1
        assert len(block.encode("utf-16-le")) // 2 <= 1800


def _category_heading_count(block: str) -> int:
    return sum(
        1 for line in block.splitlines()
        if line.startswith("── ") and line.endswith(" ──")
    )


def test_oversized_category_continues_without_mixing_the_next_category():
    from katherine_help import build_commands_help_blocks

    blocks = build_commands_help_blocks(max_units=800)
    music_blocks = [
        block for block in blocks
        if "MÚSICA Y VOZ" in block
    ]
    assert music_blocks
    for block in music_blocks:
        assert _category_heading_count(block) == 1
        assert "GENSHIN IMPACT" not in block
        assert "SOCIAL Y PERFILES" not in block



def test_reading_volume_command_is_listed_under_music_and_voice():
    from katherine_help import build_commands_help_text, filter_commands_help_text

    music_help = filter_commands_help_text(build_commands_help_text(), "voz")

    assert "!volumenlectura <0-100>" in music_help
    assert "!subirvolumenlectura [cantidad]" in music_help
    assert "!bajarvolumenlectura [cantidad]" in music_help
    assert "!silenciolectura" in music_help
