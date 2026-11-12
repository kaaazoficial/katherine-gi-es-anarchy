"""Regression tests for ASTRA NOVA pure intent and recovery helpers."""
from katherine_astra import (
    is_recoverable_dj_failure,
    normalize_music_mood,
    resolve_music_mood,
)


def test_music_moods_map_to_catalog_search_terms():
    assert resolve_music_mood("Estudiar") == "instrumental focus study ambient"
    assert resolve_music_mood("concentración") == "instrumental focus study ambient"
    assert resolve_music_mood("jugar") == "epic cinematic game soundtrack"
    assert resolve_music_mood("jazz nocturno") == "jazz nocturno"
    assert resolve_music_mood("") == "ambient instrumental"


def test_music_mood_normalization_is_accent_and_space_insensitive():
    assert normalize_music_mood("  CONCENTRACIÓN   ") == "concentracion"


def test_dj_recovery_only_skips_invalid_audio_errors():
    assert is_recoverable_dj_failure(
        "No pude descargar o validar la canción (RuntimeError: HTML)."
    )
    assert is_recoverable_dj_failure("El archivo no pasó la validación de audio.")
    assert not is_recoverable_dj_failure("No pude entrar a la llamada (HTTP 403).")
    assert not is_recoverable_dj_failure("La sala no confirmó que Katherine publicara audio.")
