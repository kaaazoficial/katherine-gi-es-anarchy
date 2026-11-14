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
