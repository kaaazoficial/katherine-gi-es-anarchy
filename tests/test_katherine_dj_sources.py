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
    assert not katherine.dj_audio_url_allowed(
        "http://archive.org/download/item/song.mp3"
    )
    assert not katherine.dj_audio_url_allowed(
        "https://archive.org.evil.example/song.mp3"
    )
    assert not katherine.dj_audio_url_allowed(
        "https://example.com/song.mp3"
    )


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

