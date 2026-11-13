"""Regression tests for ASTRA NOVA pure intent and recovery helpers."""
from katherine_astra import (
    build_jamendo_download_url,
    is_livekit_audio_source_failure,
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



def test_livekit_audio_source_failure_detection_is_specific():
    assert is_livekit_audio_source_failure(
        "an RtcError occurred: InvalidState - failed to capture frame"
    )
    assert is_livekit_audio_source_failure("failed to capture frame")
    assert not is_livekit_audio_source_failure(
        "edge_tts returned a temporary HTTP 503"
    )


def test_dj_remote_download_http_errors_are_recoverable_but_voice_403_is_not():
    assert is_recoverable_dj_failure(
        "No pude descargar o validar la canción "
        "(RuntimeError: La fuente musical respondió HTTP 403)."
    )
    assert is_recoverable_dj_failure(
        "No pude descargar o validar la canción "
        "(RuntimeError: La fuente musical respondió HTTP 404)."
    )
    assert not is_recoverable_dj_failure(
        "No pude entrar a la llamada (HTTP 403): not_in_chat."
    )

def test_jamendo_uses_authenticated_file_endpoint_and_validates_inputs():
    url = build_jamendo_download_url("client-123", "1781193")
    assert url == (
        "https://api.jamendo.com/v3.0/tracks/file/"
        "?client_id=client-123&id=1781193&audioformat=mp32"
    )
    assert build_jamendo_download_url("", "1781193") is None
    assert build_jamendo_download_url("client-123", "not-a-track") is None
