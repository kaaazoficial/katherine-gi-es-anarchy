from katherine_text import format_katherine_text


def test_first_line_title_and_body_are_styled_without_rewriting_words():
    assert format_katherine_text("Hola\nHola.") == "[CB] ✦ Hola ✦\n[C] Hola."


def test_simple_single_line_message_is_centered():
    assert format_katherine_text("Hola.") == "[C] Hola."


def test_section_titles_get_light_decoration():
    text = format_katherine_text("ESTADO DE KATHERINE\n\nConexión activa.\nUsuarios: 12")
    assert text == "[CB] ✦ ESTADO DE KATHERINE ✦\n\n[C] Conexión activa.\n[C] Usuarios: 12"


def test_already_styled_help_is_not_double_formatted():
    text = "[CB] ✦ GUÍA ✦\n[C] Primer comando\n[CB] ◈ Música\n[C] !dj cola"
    assert format_katherine_text(text) == text


def test_code_blocks_are_preserved_verbatim():
    text = "Ejemplo\n```python\nprint('hola')\n```"
    assert format_katherine_text(text) == text


def test_empty_content_is_preserved():
    assert format_katherine_text("") == ""
