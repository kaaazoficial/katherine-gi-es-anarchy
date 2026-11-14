"""Tests for Katherine's licensed multi-source DJ catalogs."""
from __future__ import annotations

import asyncio

import main as katherine


def test_dj_audio_hosts_are_exact_and_https_only():
    assert katherine.dj_audio_url_allowed(
        "https://archive.org/download/item/song.mp3"
    )
    assert katherine.dj_audio_url_allowed(
        "https://upload.wikimedia.org/wikipedia/commons/song.ogg"
    )
    assert katherine.dj_audio_url_allowed(
        "https://audio.jamendo.com/song.mp3"
    )
    assert katherine.dj_audio_url_allowed(
        "https://prod-1.storage.jamendo.com/download/track/123/mp32/"
    )
    assert not katherine.dj_audio_url_allowed(
        "https://user:pass@archive.org/download/item/song.mp3"
    )
    assert not katherine.dj_audio_url_allowed(
        "http://archive.org/download/item/song.mp3"
    )
    assert not katherine.dj_audio_url_allowed(
        "https://archive.org.evil.example/song.mp3"
    )
    assert not katherine.dj_audio_url_allowed(
        "https://example.com/song.mp3"
    )




def test_jamendo_search_uses_official_audiodownload_url(monkeypatch):
    class FakeResponse:
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {
                "headers": {"status": "success"},
                "results": [{
                    "id": "12345",
                    "name": "Ambient track",
                    "artist_name": "Test Artist",
                    "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
                    "audiodownload_allowed": True,
                    "audiodownload": (
                        "https://prod-1.storage.jamendo.com/"
                        "download/track/12345/mp32/"
                    ),
                }],
            }

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(katherine, "JAMENDO_CLIENT_ID", "test-client")
    monkeypatch.setattr(katherine, "external_request", fake_request)
    katherine.DJ_SOURCE_CACHE.pop("jamendo:ambient direct url", None)

    tracks = asyncio.run(katherine.search_jamendo_audio("ambient direct url"))

    assert len(tracks) == 1
    assert tracks[0]["url"].endswith("/download/track/12345/mp32/")
    assert tracks[0]["source"] == "Jamendo"


def test_dj_source_cooldown_is_host_scoped_and_expires(monkeypatch):
    katherine.DJ_SOURCE_COOLDOWNS.clear()
    monkeypatch.setattr(katherine.time, "monotonic", lambda: 100.0)
    katherine.cool_down_dj_source("prod-1.storage.jamendo.com", 600)

    assert katherine.dj_source_is_cooled("prod-1.storage.jamendo.com")
    assert not katherine.dj_source_is_cooled("upload.wikimedia.org")
    assert not katherine.dj_source_is_cooled("prod-1.storage.jamendo.com", now=701.0)


def test_dj_remote_limits_are_bounded_for_free_instance():
    assert katherine.DJ_REMOTE_AUDIO_MAX_BYTES == 80 * 1024 * 1024
    assert katherine.DJ_QUEUE_MAX_TRACKS == 10

def test_jamendo_is_optional_without_client_id(monkeypatch):
    monkeypatch.setattr(katherine, "JAMENDO_CLIENT_ID", "")
    assert asyncio.run(katherine.search_jamendo_audio("ambient")) == []


def test_music_catalog_combines_sources_and_survives_one_failure(monkeypatch):
    async def archive(query, limit):
        return [
            {"url": "https://archive.org/a.mp3", "identifier": "a"},
            {"url": "https://archive.org/b.mp3", "identifier": "b"},
        ]

    async def commons(query, limit):
        return [
            {"url": "https://upload.wikimedia.org/c.ogg", "identifier": "c"},
        ]

    async def jamendo(query, limit):
        raise RuntimeError("Jamendo temporalmente fuera")

    monkeypatch.setattr(katherine, "search_archive_audio", archive)
    monkeypatch.setattr(katherine, "search_wikimedia_audio", commons)
    monkeypatch.setattr(katherine, "search_jamendo_audio", jamendo)
    monkeypatch.setattr(katherine, "JAMENDO_CLIENT_ID", "test-client")

    tracks = asyncio.run(katherine.search_music_catalog("ambient", limit=5))

    assert [track["identifier"] for track in tracks] == ["a", "c", "b"]
    assert katherine.metrics["dj_search_errors_jamendo"] >= 1


def test_music_catalog_raises_only_when_every_source_fails(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(katherine, "search_archive_audio", fail)
    monkeypatch.setattr(katherine, "search_wikimedia_audio", fail)
    monkeypatch.setattr(katherine, "JAMENDO_CLIENT_ID", "")

    try:
        asyncio.run(katherine.search_music_catalog("ambient", limit=5))
    except RuntimeError as exc:
        assert "Todos los catálogos" in str(exc)
    else:
        raise AssertionError("Se esperaba un error si todos los catálogos fallan")

def test_archive_download_uses_direct_storage_server_when_available():
    url = katherine.archive_audio_download_url(
        "sample_item",
        "track name.mp3",
        {"d1": "ia601500.us.archive.org", "dir": "/14/items/sample_item"},
    )
    assert url == (
        "https://ia601500.us.archive.org/14/items/sample_item/track%20name.mp3"
    )


def test_archive_download_falls_back_to_canonical_download_url():
    url = katherine.archive_audio_download_url(
        "sample_item", "track.mp3", {}
    )
    assert url == "https://archive.org/download/sample_item/track.mp3"


def test_archive_download_rejects_untrusted_storage_host():
    url = katherine.archive_audio_download_url(
        "sample_item",
        "track.mp3",
        {"d1": "evil.example", "dir": "/14/items/sample_item"},
    )
    assert url == "https://archive.org/download/sample_item/track.mp3"

def test_chat_output_removes_urls_but_keeps_readable_text():
    text = katherine.sanitize_chat_text(
        "Guía: https://example.com/guia y www.example.org/manual"
    )
    assert text == "Guía: y"


def test_chat_output_keeps_markdown_and_html_link_labels():
    assert katherine.sanitize_chat_text(
        "[Abrir guía](https://example.com/guia)"
    ) == "Abrir guía"
    assert katherine.sanitize_chat_text(
        '<a href="https://example.com">Documentación</a>'
    ) == "Documentación"


def test_chat_output_removes_bare_domains_without_corrupting_emails_or_code_files():
    text = katherine.sanitize_chat_text(
        "Contacto user@example.com; archivo main.py; entra discord.gg/abc123"
    )
    assert "user@example.com" in text
    assert "main.py" in text
    assert "discord.gg" not in text
    assert "abc123" not in text


def test_chat_output_keeps_plain_text_and_internal_commands():
    text = katherine.sanitize_chat_text(
        "Usa !dj buscar ambient y luego !dj cola."
    )
    assert text == "Usa !dj buscar ambient y luego !dj cola."

def test_dj_search_fallback_skips_invalid_track_and_starts_next(monkeypatch):
    calls = []

    async def fake_start(thread_id, url, looping, track_metadata=None, enqueue=False):
        calls.append(url)
        if url.endswith("broken.mp3"):
            return False, "No pude descargar o validar la canción (RuntimeError: HTML)."
        return True, "Reproducción iniciada."

    monkeypatch.setattr(katherine, "start_dj_room", fake_start)
    tracks = [
        {"url": "https://audio.jamendo.com/broken.mp3", "title": "No válida"},
        {"url": "https://audio.jamendo.com/good.mp3", "title": "Válida"},
    ]
    ok, message, selected, index = asyncio.run(
        katherine.start_dj_search_with_fallback("room-1", tracks, 0)
    )
    assert ok is True
    assert selected["title"] == "Válida"
    assert index == 1
    assert "alternativa" in message
    assert calls == [tracks[0]["url"], tracks[1]["url"]]

