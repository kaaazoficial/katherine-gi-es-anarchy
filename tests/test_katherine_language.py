from __future__ import annotations

from katherine_language import detect_spanish_spelling_issues


class FakeSpellChecker:
    def unknown(self, words):
        return {word for word in words if word == "necesitto"}

    def correction(self, word):
        return "necesito" if word == "necesitto" else word

    def known(self, words):
        return {"necesito"} if "necesito" in words else set()


def test_clear_common_typos_are_corrected_without_changing_the_sentence():
    result = detect_spanish_spelling_issues(
        "Ola, grasias por tu ayuda.",
        spell_checker=FakeSpellChecker(),
    )
    assert result is not None
    assert result.corrected_text == "Hola, gracias por tu ayuda."
    assert result.corrections == (("Ola", "Hola"), ("grasias", "gracias"))


def test_dictionary_correction_requires_high_confidence():
    result = detect_spanish_spelling_issues(
        "Hola, necesito ayuda con esto: necesitto.",
        spell_checker=FakeSpellChecker(),
    )
    assert result is not None
    assert result.corrected_text.endswith("necesito.")


def test_do_not_correct_genshin_names_slang_or_short_abbreviations():
    message = "Katherin, mi build de Cyno está genial bro jajaja xq sí."
    assert detect_spanish_spelling_issues(
        message, spell_checker=FakeSpellChecker()
    ) is None


def test_mentions_urls_and_code_are_not_rewritten():
    message = "Ola @Katherine mira https://example.com/test"
    result = detect_spanish_spelling_issues(
        message, spell_checker=FakeSpellChecker()
    )
    assert result is not None
    assert result.corrected_text == "Hola @Katherine mira https://example.com/test"


def test_foreign_or_non_spanish_text_is_not_guessed():
    assert detect_spanish_spelling_issues(
        "Good morning, see you later.", spell_checker=FakeSpellChecker()
    ) is None


def test_command_messages_are_never_auto_corrected():
    assert detect_spanish_spelling_issues(
        "!dj buscar genshin", spell_checker=FakeSpellChecker()
    ) is None


def test_ambiguous_missing_accents_are_not_rewritten():
    assert detect_spanish_spelling_issues(
        "Esta es mi tarea, como la ves?", spell_checker=FakeSpellChecker()
    ) is None
