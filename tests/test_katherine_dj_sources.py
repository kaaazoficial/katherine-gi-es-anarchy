"""Tests for Katherine's licensed multi-source DJ catalogs."""
from __future__ import annotations

import asyncio

import numpy as np

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
    assert katherine.dj_audio_url_allowed(
        "https://ccmixter.org/content/artist/song.mp3"
    )
    assert katherine.dj_audio_url_allowed(
        "https://api.audius.co/v1/tracks/123/stream"
    )
    assert katherine.dj_audio_url_allowed(
        "https://content-node.audius.co/audio/123.mp3"
    )
    assert katherine.dj_audio_url_allowed(
        "https://freesound.org/data/previews/12/12345_67890-hq.mp3"
    )
    assert katherine.dj_audio_url_allowed(
        "https://cdn.freesound.org/previews/12/12345_67890-hq.mp3"
    )
    assert katherine.dj_audio_url_allowed(
        "https://tile.loc.gov/streaming-services/iiif/test/default.mp3"
    )
    assert not katherine.dj_audio_url_allowed(
        "https://evil.loc.gov/streaming-services/test.mp3"
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
    assert not katherine.dj_audio_url_allowed(
        "https://ccmixter.org.evil.example/song.mp3"
    )
    assert not katherine.dj_audio_url_allowed("https://[broken-host/audio.mp3")




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
    # Do not leak a synthetic monotonic timestamp into other catalog tests.
    katherine.DJ_SOURCE_COOLDOWNS.clear()


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

    async def ccmixter(query, limit):
        return []

    async def audius(query, limit):
        return []

    async def freesound(query, limit):
        return []

    monkeypatch.setattr(katherine, "search_archive_audio", archive)
    monkeypatch.setattr(katherine, "search_wikimedia_audio", commons)
    monkeypatch.setattr(katherine, "search_ccmixter_audio", ccmixter)
    monkeypatch.setattr(katherine, "search_audius_audio", audius)
    monkeypatch.setattr(katherine, "search_freesound_audio", freesound)
    monkeypatch.setattr(katherine, "FREESOUND_API_KEY", "test-freesound-key")
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
    monkeypatch.setattr(katherine, "search_ccmixter_audio", fail)
    monkeypatch.setattr(katherine, "search_audius_audio", fail)
    monkeypatch.setattr(katherine, "search_openverse_audio", fail)
    monkeypatch.setattr(katherine, "search_loc_national_jukebox_audio", fail)
    monkeypatch.setattr(katherine, "search_freesound_audio", fail)
    monkeypatch.setattr(katherine, "FREESOUND_API_KEY", "test-freesound-key")
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




def test_archive_search_rejects_files_over_actual_download_limit(monkeypatch):
    class FakeResponse:
        is_success = True

        @staticmethod
        def json():
            return {
                "metadata": {
                    "title": "Oversized audio",
                    "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
                },
                "files": [
                    {"name": "large.mp3", "size": str(katherine.DJ_REMOTE_AUDIO_MAX_BYTES + 1)}
                ],
            }

    async def fake_request(*args, **kwargs):
        return FakeResponse()

    katherine.DJ_ARCHIVE_CACHE.pop("oversized-audio", None)
    monkeypatch.setattr(katherine, "external_request", fake_request)
    assert asyncio.run(katherine.archive_item_details("oversized-audio")) is None


def test_dj_source_cooldown_normalizes_provider_cdn_hosts(monkeypatch):
    katherine.DJ_SOURCE_COOLDOWNS.clear()
    monkeypatch.setattr(katherine.time, "monotonic", lambda: 100.0)
    katherine.cool_down_dj_source("prod-1.storage.jamendo.com", 600)
    assert katherine.dj_source_is_cooled("api.jamendo.com")
    assert katherine.dj_source_is_cooled("prod-1.storage.jamendo.com")
    katherine.cool_down_dj_source("ia601500.us.archive.org", 600)
    assert katherine.dj_source_is_cooled("archive.org")
    katherine.cool_down_dj_source("upload.wikimedia.org", 600)
    assert katherine.dj_source_is_cooled("upload.wikimedia.org")
    katherine.cool_down_dj_source("content-node.audius.co", 600)
    assert katherine.dj_source_is_cooled("api.audius.co")
    katherine.cool_down_dj_source("ccmixter.org", 600)
    assert katherine.dj_source_is_cooled("ccmixter.org")
    katherine.DJ_SOURCE_COOLDOWNS.clear()


def test_http_access_failures_trigger_provider_cooldown(monkeypatch):
    katherine.DJ_SOURCE_COOLDOWNS.clear()
    monkeypatch.setattr(katherine.time, "monotonic", lambda: 200.0)
    assert katherine.cool_down_dj_source_on_http_failure("upload.wikimedia.org", 403)
    assert katherine.dj_source_is_cooled("upload.wikimedia.org")
    assert not katherine.cool_down_dj_source_on_http_failure("upload.wikimedia.org", 404)
    katherine.DJ_SOURCE_COOLDOWNS.clear()


def test_music_fallback_skips_remaining_tracks_from_blocked_provider(monkeypatch):
    calls = []

    async def fake_start(thread_id, url, looping, track_metadata=None, enqueue=False):
        calls.append(url)
        if track_metadata.get("source_key") == "commons":
            return False, "No pude descargar o validar la canción (RuntimeError: HTTP 403)."
        return True, "Reproducción iniciada."

    monkeypatch.setattr(katherine, "start_dj_room", fake_start)
    katherine.DJ_SOURCE_COOLDOWNS.clear()
    tracks = [
        {"url": "https://upload.wikimedia.org/one.ogg", "title": "Bloqueada 1", "source_key": "commons"},
        {"url": "https://upload.wikimedia.org/two.ogg", "title": "Bloqueada 2", "source_key": "commons"},
        {"url": "https://archive.org/download/item/good.mp3", "title": "Alternativa", "source_key": "archive"},
    ]
    ok, _, selected, index = asyncio.run(
        katherine.start_dj_search_with_fallback("room-403", tracks, 0)
    )
    assert ok is True
    assert selected["title"] == "Alternativa"
    assert index == 2
    assert calls == [
        "https://upload.wikimedia.org/one.ogg",
        "https://archive.org/download/item/good.mp3",
    ]
    katherine.DJ_SOURCE_COOLDOWNS.clear()



def test_dj_probe_command_is_admin_only_and_does_not_search_for_public_users(monkeypatch):
    sent = []
    searched = []

    async def fake_send(thread_id, content, source="developer", reply_id=None, buttons=None):
        sent.append(str(content))
        return {"ok": True}

    async def fake_search(*args, **kwargs):
        searched.append((args, kwargs))
        return [{"url": "https://archive.org/download/item/song.mp3"}]

    monkeypatch.setattr(katherine, "COMMUNITY_TOKEN", "test-community-token")
    monkeypatch.setattr(katherine, "is_admin_uid", lambda uid: uid == "admin-uid")
    monkeypatch.setattr(katherine, "record_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(katherine, "record_chat_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(katherine, "send_message", fake_send)
    monkeypatch.setattr(katherine, "search_music_catalog", fake_search)

    event = {
        "threadId": "dj-probe-public-room",
        "message": {
            "messageId": "probe-message-1",
            "content": "!dj probar ambient",
            "author": {"uid": "public-uid", "nickname": "Usuario"},
        },
    }
    asyncio.run(katherine._handle_command_impl(event, "developer"))

    assert searched == []
    assert sent and "restringida" in sent[-1]


def test_admin_dj_probe_runs_real_catalog_and_playback_flow(monkeypatch):
    sent = []
    searches = []
    playback = []

    async def fake_send(thread_id, content, source="developer", reply_id=None, buttons=None):
        sent.append(str(content))
        return {"ok": True}

    async def fake_search(query, limit=5):
        searches.append((query, limit))
        return [{
            "url": "https://archive.org/download/item/song.mp3",
            "title": "Ambient Test",
            "creator": "Test Artist",
            "license": "CC0",
            "source": "Internet Archive",
            "source_key": "archive",
        }]

    async def fake_play(thread_id, tracks, selected_index=0):
        playback.append((thread_id, tracks, selected_index))
        return True, "Reproducción iniciada", tracks[0], 0

    monkeypatch.setattr(katherine, "COMMUNITY_TOKEN", "test-community-token")
    monkeypatch.setattr(katherine, "is_admin_uid", lambda uid: uid == "admin-uid")
    monkeypatch.setattr(katherine, "record_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(katherine, "record_chat_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(katherine, "send_message", fake_send)
    monkeypatch.setattr(katherine, "search_music_catalog", fake_search)
    monkeypatch.setattr(katherine, "start_dj_search_with_fallback", fake_play)
    katherine.DJ_ARCHIVE_CACHE.clear()
    katherine.DJ_SOURCE_CACHE.clear()

    event = {
        "threadId": "dj-probe-admin-room",
        "message": {
            "messageId": "probe-message-2",
            "content": "!dj probar ambient",
            "author": {"uid": "admin-uid", "nickname": "Admin"},
        },
    }
    asyncio.run(katherine._handle_command_impl(event, "developer"))

    assert searches == [("ambient", 5)]
    assert len(playback) == 1
    assert playback[0][0] == "dj-probe-admin-room"
    assert any("PRUEBA MUSICAL COMPLETADA" in item for item in sent)



def test_dj_search_ranking_deduplicates_and_prefers_exact_title():
    tracks = [
        {"url": "https://archive.org/a.mp3", "title": "Ambient piano", "creator": "Other"},
        {"url": "https://archive.org/b.mp3", "title": "Piano", "creator": "Ambient Artist"},
        {"url": "https://archive.org/c.mp3", "title": "Ambient piano", "creator": "Other"},
        {"url": "https://archive.org/d.mp3", "title": "Ambient piano night", "creator": "Composer"},
    ]
    ranked = katherine.rank_dj_results("ambient piano", tracks, limit=10)
    assert ranked[0]["url"] == "https://archive.org/a.mp3"
    assert len(ranked) == 3
    assert all("_search_score" in track for track in ranked)


def test_dj_queue_move_uses_one_based_positions():
    queue = __import__("collections").deque([
        {"title": "A"}, {"title": "B"}, {"title": "C"}
    ])
    assert katherine.move_dj_queue_item(queue, 3, 1)
    assert [item["title"] for item in queue] == ["C", "A", "B"]
    assert not katherine.move_dj_queue_item(queue, 0, 2)
    assert not katherine.move_dj_queue_item(queue, 1, 4)


def test_dj_mixer_ducks_music_and_limits_peak(monkeypatch):
    monkeypatch.setattr(katherine, "np", np)
    music = np.full(480, 20000, dtype=np.int16)
    speech = np.full(480, 20000, dtype=np.int16)
    mixed = katherine.mix_audio_samples(music, speech, music_volume=1.0, duck_gain=0.25)
    assert mixed.dtype == np.int16
    assert int(mixed[0]) == 25000
    clipped = katherine.mix_audio_samples(music, speech, music_volume=1.0, duck_gain=1.0)
    assert int(np.max(np.abs(clipped.astype(np.int32)))) <= 30000


def test_dj_remote_queue_item_is_enqueued_without_downloading(monkeypatch):
    async def fake_ensure(thread_id):
        return True, "connected"

    async def fail_download(*args, **kwargs):
        raise AssertionError("Queued tracks must not download before playback")

    monkeypatch.setattr(katherine, "load_audio_dependencies", lambda: True)
    monkeypatch.setattr(katherine, "ensure_voice_room", fake_ensure)
    monkeypatch.setattr(katherine, "download_dj_audio", fail_download)

    async def scenario():
        state = {
            "task": asyncio.create_task(asyncio.sleep(60)),
            "queue": __import__("collections").deque(),
            "volume": 0.75,
            "audio_source": object(),
            "pause_event": asyncio.Event(),
            "skip_event": asyncio.Event(),
            "stop_event": asyncio.Event(),
            "playback_stop_event": asyncio.Event(),
        }
        state["pause_event"].set()
        katherine.DJ_ROOMS["deferred-test"] = state
        try:
            ok, message = await katherine.start_dj_room(
                "deferred-test",
                "https://archive.org/download/item/song.mp3",
                False,
                track_metadata={"title": "Queued song", "extension": ".mp3"},
                enqueue=True,
            )
            assert ok
            assert len(state["queue"]) == 1
            assert state["queue"][0]["remote"] is True
            assert state["queue"][0]["path"].startswith("https://")
            assert "descargará" in message
        finally:
            task = state.get("task")
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            katherine.DJ_ROOMS.pop("deferred-test", None)

    asyncio.run(scenario())



def test_take_audio_frame_preserves_samples_and_pads_short_frames(monkeypatch):
    monkeypatch.setattr(katherine, "np", np)
    first = np.arange(700, dtype=np.int16)
    frame1, remainder = katherine.take_audio_frame(
        np.empty(0, dtype=np.int16), first, frame_size=480
    )
    assert len(frame1) == 480
    assert np.array_equal(frame1, first[:480])
    assert np.array_equal(remainder, first[480:])

    frame2, remainder2 = katherine.take_audio_frame(
        remainder, np.empty(0, dtype=np.int16), frame_size=480
    )
    assert len(frame2) == 480
    assert np.array_equal(frame2[:220], first[480:])
    assert np.all(frame2[220:] == 0)
    assert len(remainder2) == 0


def test_take_audio_frame_keeps_extra_input_for_next_tick(monkeypatch):
    monkeypatch.setattr(katherine, "np", np)
    pending = np.arange(100, dtype=np.int16)
    incoming = np.arange(900, dtype=np.int16) + 100
    frame, remainder = katherine.take_audio_frame(pending, incoming, frame_size=480)
    expected = np.concatenate((pending, incoming))
    assert np.array_equal(frame, expected[:480])
    assert np.array_equal(remainder, expected[480:])



def test_split_complete_audio_frames_keeps_decoder_boundaries_contiguous(monkeypatch):
    monkeypatch.setattr(katherine, "np", np)
    first = np.arange(300, dtype=np.int16)
    second = np.arange(300, dtype=np.int16) + 300

    frames, pending = katherine.split_complete_audio_frames(
        np.empty(0, dtype=np.int16), first
    )
    assert frames == []
    assert len(pending) == 300

    frames, pending = katherine.split_complete_audio_frames(pending, second)
    assert len(frames) == 1
    assert len(frames[0]) == 480
    assert np.array_equal(frames[0], np.arange(480, dtype=np.int16))
    assert len(pending) == 120
    assert np.array_equal(pending, np.arange(480, 600, dtype=np.int16))

    third = np.arange(840, dtype=np.int16) + 600
    frames, pending = katherine.split_complete_audio_frames(pending, third)
    assert len(frames) == 2
    assert all(len(frame) == 480 for frame in frames)
    joined = np.concatenate(frames + [pending])
    assert np.array_equal(joined, np.arange(480, 1440, dtype=np.int16))


def test_music_mixer_ignores_tts_queue_and_publishes_music_only(monkeypatch):
    import types

    monkeypatch.setattr(katherine, "np", np)
    thread_id = "music-mixer-isolation-test"
    captured = []

    class FakeAudioSource:
        async def capture_frame(self, frame):
            captured.append(np.frombuffer(bytes(frame.data), dtype=np.int16).copy())

    class FakeAudioFrame:
        @staticmethod
        def create(sample_rate, channels, samples):
            assert (sample_rate, channels, samples) == (48000, 1, 960)
            return types.SimpleNamespace(data=bytearray(samples * 2))

    monkeypatch.setattr(
        katherine, "livekit_rtc",
        types.SimpleNamespace(AudioFrame=FakeAudioFrame),
    )

    async def scenario():
        state = {
            "stop_event": asyncio.Event(),
            "music_audio_queue": asyncio.Queue(maxsize=8),
            "speech_audio_queue": asyncio.Queue(maxsize=8),
            "speaking": asyncio.Event(),
            "audio_source": FakeAudioSource(),
            "volume": 1.0,
            "duck_enabled": True,
            "music_duck_gain": 1.0,
            "music_buffer_started": True,
            "mixer_task": None,
        }
        katherine.DJ_ROOMS[thread_id] = state
        try:
            task = asyncio.create_task(katherine.audio_mixer_loop(thread_id))
            state["mixer_task"] = task
            await asyncio.sleep(0.025)
            assert captured == []

            await state["speech_audio_queue"].put(
                np.full(480, 1234, dtype=np.int16)
            )
            await asyncio.sleep(0.025)
            assert captured == []

            await state["music_audio_queue"].put(
                np.full(480, 4321, dtype=np.int16)
            )
            await asyncio.sleep(0.025)
            assert captured == []
            await state["music_audio_queue"].put(
                np.full(480, 4321, dtype=np.int16)
            )
            for _ in range(20):
                if captured:
                    break
                await asyncio.sleep(0.01)
            assert len(captured) == 1
            assert np.all(captured[0] == 4321)
            assert state["speech_audio_queue"].qsize() == 1
        finally:
            state["stop_event"].set()
            task = state.get("mixer_task")
            if task and not task.done():
                await task
            katherine.DJ_ROOMS.pop(thread_id, None)

    asyncio.run(scenario())


def test_music_mixer_keeps_internal_buffer_bounded(monkeypatch):
    import types

    monkeypatch.setattr(katherine, "np", np)
    thread_id = "music-mixer-bounded-buffer-test"
    captured = []

    class FakeAudioSource:
        queued_duration = 0.0

        async def capture_frame(self, frame):
            captured.append(np.frombuffer(bytes(frame.data), dtype=np.int16).copy())

    class FakeAudioFrame:
        @staticmethod
        def create(sample_rate, channels, samples):
            assert (sample_rate, channels, samples) == (48000, 1, 960)
            return types.SimpleNamespace(data=bytearray(samples * 2))

    monkeypatch.setattr(
        katherine, "livekit_rtc",
        types.SimpleNamespace(AudioFrame=FakeAudioFrame),
    )

    async def scenario():
        queue = asyncio.Queue(maxsize=16)
        for value in range(16):
            await queue.put(np.full(480, value + 1, dtype=np.int16))
        state = {
            "stop_event": asyncio.Event(),
            "music_audio_queue": queue,
            "speaking": asyncio.Event(),
            "audio_source": FakeAudioSource(),
            "volume": 1.0,
            "duck_enabled": True,
            "music_duck_gain": 1.0,
            "music_buffer_started": True,
            "music_decoder_finished": False,
            "mixer_task": None,
            "task": object(),
        }
        katherine.DJ_ROOMS[thread_id] = state
        try:
            task = asyncio.create_task(katherine.audio_mixer_loop(thread_id))
            state["mixer_task"] = task
            for _ in range(30):
                if len(captured) >= 3:
                    break
                await asyncio.sleep(0.005)
            assert len(captured) >= 3
            assert queue.qsize() >= 10
            assert state["music_pending_samples"] <= 960
        finally:
            state["stop_event"].set()
            task = state.get("mixer_task")
            if task and not task.done():
                await task
            katherine.DJ_ROOMS.pop(thread_id, None)

    asyncio.run(scenario())


def test_stop_music_clears_livekit_native_queue_and_stops_mixer(monkeypatch):
    thread_id = "stop-music-clears-source-test"
    cleared = []

    class FakeAudioSource:
        def clear_queue(self):
            cleared.append(True)

    async def scenario():
        queue = asyncio.Queue(maxsize=4)
        await queue.put(np.ones(480, dtype=np.int16))
        state = {
            "stop_event": asyncio.Event(),
            "playback_stop_event": asyncio.Event(),
            "music_audio_queue": queue,
            "audio_source": FakeAudioSource(),
            "music_pending_samples": 480,
            "music_buffer_started": True,
            "music_decoder_finished": False,
            "music_duck_gain": 0.28,
            "queue": [],
            "filename": "track.mp3",
            "track": {"title": "track"},
            "temporary_audio_path": None,
            "task": None,
            "mixer_task": None,
        }
        katherine.DJ_ROOMS[thread_id] = state
        try:
            assert await katherine.stop_dj_playback(thread_id)
            assert cleared == [True]
            assert queue.empty()
            assert state["music_pending_samples"] == 0
            assert state["music_duck_gain"] == 1.0
            assert state["playback_stop_event"].is_set()
            assert not state["stop_event"].is_set()
        finally:
            katherine.DJ_ROOMS.pop(thread_id, None)

    asyncio.run(scenario())


def test_stream_speech_file_uses_independent_livekit_source(monkeypatch):
    import types

    monkeypatch.setattr(katherine, "np", np)
    thread_id = "tts-independent-source-test"
    captured = []

    class FakeAudioSource:
        async def capture_frame(self, frame):
            captured.append(np.frombuffer(bytes(frame.data), dtype=np.int16).copy())

    class FakeAudioFrame:
        @staticmethod
        def create(sample_rate, channels, samples):
            assert (sample_rate, channels, samples) == (48000, 1, 480)
            return types.SimpleNamespace(data=bytearray(samples * 2))

    class FakeDecodedFrame:
        def to_ndarray(self):
            return np.full((1, 480), 3210, dtype=np.int16)

    class FakeResampler:
        def __init__(self, **kwargs):
            pass

        def resample(self, frame):
            return [FakeDecodedFrame()] if frame is not None else []

    class FakeContainer:
        streams = [types.SimpleNamespace(type="audio")]

        def decode(self, stream):
            yield object()

        def close(self):
            pass

    monkeypatch.setattr(
        katherine, "livekit_rtc",
        types.SimpleNamespace(AudioFrame=FakeAudioFrame),
    )
    monkeypatch.setattr(
        katherine, "pyav",
        types.SimpleNamespace(
            open=lambda *args, **kwargs: FakeContainer(),
            AudioResampler=FakeResampler,
        ),
    )
    monkeypatch.setattr(katherine, "load_audio_dependencies", lambda: True)
    state = {
        "stop_event": asyncio.Event(),
        "reading_enabled": True,
        "tts_volume": 1.0,
        "speech_audio_source": FakeAudioSource(),
    }
    monkeypatch.setattr(katherine, "DJ_ROOMS", {thread_id: state})

    asyncio.run(katherine.stream_speech_file(thread_id, "fake-tts.mp3"))

    assert len(captured) == 1
    assert np.all(captured[0] == 3210)


def test_enqueue_message_for_speech_puts_plain_user_text_in_the_queue(monkeypatch):
    thread_id = "tts-enqueue-test"
    monkeypatch.setattr(katherine, "ensure_audio_mixer_running", lambda *args: True)
    monkeypatch.setattr(katherine, "ensure_speech_worker_running", lambda *args: True)

    async def scenario():
        state = {
            "reading_enabled": True,
            "speech_queue": asyncio.Queue(maxsize=20),
            "speech_priority_queue": asyncio.Queue(maxsize=5),
            "speech_recent": {},
            "tts_read_links": False,
            "tts_read_mentions": False,
        }
        katherine.DJ_ROOMS[thread_id] = state
        try:
            event = {
                "threadId": thread_id,
                "message": {
                    "content": "Hola, buenas tardes a todos.",
                    "author": {"uid": "member-123", "nickname": "Viajero"},
                    "mentions": [],
                },
            }
            await katherine.enqueue_message_for_speech(event, "developer")
            assert state["speech_queue"].qsize() == 1
            assert state["speech_queue"].get_nowait() == "Hola, buenas tardes a todos."
            assert state["speech_priority_queue"].empty()

            directed_event = {
                "threadId": thread_id,
                "message": {
                    "content": "Katherine, ¿puedes ayudarme?",
                    "author": {"uid": "member-124", "nickname": "Viajero"},
                    "mentions": [],
                },
            }
            await katherine.enqueue_message_for_speech(directed_event, "developer")
            assert state["speech_priority_queue"].qsize() == 1
            assert state["speech_priority_queue"].get_nowait() == "Katherine, ¿puedes ayudarme?"
        finally:
            katherine.DJ_ROOMS.pop(thread_id, None)

    asyncio.run(scenario())


def test_enable_room_reading_starts_tts_without_starting_music_mixer(monkeypatch):
    thread_id = "tts-enable-test"

    async def fake_ensure_voice_room(value):
        assert value == thread_id
        katherine.DJ_ROOMS[thread_id] = {
            "stop_event": asyncio.Event(),
            "audio_source": object(),
            "speech_audio_source": object(),
            "reading_enabled": False,
            "speech_task": None,
            "mixer_task": None,
        }
        return True, "connected"

    async def wait_until_stopped(value):
        await katherine.DJ_ROOMS[value]["stop_event"].wait()

    monkeypatch.setattr(katherine, "load_tts_dependency", lambda: True)
    monkeypatch.setattr(katherine, "load_audio_dependencies", lambda: True)
    monkeypatch.setattr(katherine, "ensure_voice_room", fake_ensure_voice_room)
    monkeypatch.setattr(katherine, "speech_queue_worker", wait_until_stopped)

    async def scenario():
        try:
            ok, message = await katherine.enable_room_reading(thread_id)
            assert ok, message
            state = katherine.DJ_ROOMS[thread_id]
            assert state["reading_enabled"] is True
            assert state["mixer_task"] is None
            assert state["speech_task"] is not None
            assert not state["speech_task"].done()
        finally:
            state = katherine.DJ_ROOMS.get(thread_id)
            if state:
                state["stop_event"].set()
                task = state.get("speech_task")
                if task:
                    await asyncio.gather(task, return_exceptions=True)
            katherine.DJ_ROOMS.pop(thread_id, None)

    asyncio.run(scenario())


def test_music_enqueue_restarts_a_stopped_mixer_and_delivers_frames(monkeypatch):
    monkeypatch.setattr(katherine, "np", np)
    monkeypatch.setattr(katherine, "livekit_rtc", object())
    thread_id = "dj-mixer-recovery-test"
    consumed = []
    finished = asyncio.Event()

    async def fake_mixer(key):
        state = katherine.DJ_ROOMS[key]
        queue = state["music_audio_queue"]
        while not state["stop_event"].is_set():
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                if len(consumed) >= 2:
                    finished.set()
                    return
                continue
            consumed.append(frame.copy())
            if len(consumed) >= 2:
                finished.set()
                return

    monkeypatch.setattr(katherine, "audio_mixer_loop", fake_mixer)

    async def scenario():
        state = {
            "stop_event": asyncio.Event(),
            "playback_stop_event": asyncio.Event(),
            "skip_event": asyncio.Event(),
            "music_audio_queue": asyncio.Queue(maxsize=1),
            "mixer_task": None,
            "audio_source": object(),
        }
        katherine.DJ_ROOMS[thread_id] = state
        try:
            samples = np.arange(960, dtype=np.int16)
            assert await katherine.enqueue_music_audio_samples(thread_id, state, samples)
            await asyncio.wait_for(finished.wait(), timeout=1.0)
            assert len(consumed) == 2
            assert np.array_equal(np.concatenate(consumed), samples)
            assert state["mixer_task"] is not None
        finally:
            state["stop_event"].set()
            task = state.get("mixer_task")
            if task and not task.done():
                await task
            katherine.DJ_ROOMS.pop(thread_id, None)

    asyncio.run(scenario())


def test_dj_playback_flushes_samples_retained_by_pyav_resampler(monkeypatch):
    import types

    monkeypatch.setattr(katherine, "np", np)

    thread_id = "dj-resampler-flush-test"
    delayed = np.full(480, 2468, dtype=np.int16)
    enqueued = []

    class FakeFrame:
        def to_ndarray(self):
            return delayed.reshape(1, -1)

    class FakeResampler:
        def __init__(self, **kwargs):
            self.flush_called = False

        def resample(self, frame):
            if frame is None:
                self.flush_called = True
                return [FakeFrame()]
            return []

    class FakeContainer:
        streams = [types.SimpleNamespace(type="audio", duration=None, time_base=None)]

        def decode(self, stream):
            yield object()

        def close(self):
            pass

    monkeypatch.setattr(
        katherine, "pyav",
        types.SimpleNamespace(
            open=lambda *args, **kwargs: FakeContainer(),
            AudioResampler=FakeResampler,
        ),
    )

    async def fake_enqueue(key, state, samples):
        enqueued.append(np.asarray(samples, dtype=np.int16).copy())
        return True

    monkeypatch.setattr(katherine, "enqueue_music_audio_samples", fake_enqueue)
    monkeypatch.setattr(katherine, "DJ_ROOMS", {thread_id: {
        "stop_event": asyncio.Event(),
        "playback_stop_event": asyncio.Event(),
        "skip_event": asyncio.Event(),
        "pause_event": asyncio.Event(),
        "music_audio_queue": asyncio.Queue(maxsize=2),
        "transition_ms": 0,
    }})
    katherine.DJ_ROOMS[thread_id]["pause_event"].set()

    result = asyncio.run(
        katherine._dj_play_single(
            thread_id, __import__("pathlib").Path("unused.mp3"),
            katherine.DJ_ROOMS[thread_id],
        )
    )

    assert result is True
    assert len(enqueued) == 1
    assert np.array_equal(enqueued[0][:480], delayed)
    assert np.count_nonzero(enqueued[0][480:]) == 0



def test_split_complete_audio_frames_preserves_samples_across_decoder_boundaries(monkeypatch):
    monkeypatch.setattr(katherine, "np", np)
    first = np.arange(300, dtype=np.int16)
    second = np.arange(300, 600, dtype=np.int16)

    frames, pending = katherine.split_complete_audio_frames(
        np.empty(0, dtype=np.int16), first, frame_size=480
    )
    assert frames == []
    assert np.array_equal(pending, first)

    frames, pending = katherine.split_complete_audio_frames(
        pending, second, frame_size=480
    )
    assert len(frames) == 1
    assert np.array_equal(frames[0], np.arange(480, dtype=np.int16))
    assert np.array_equal(pending, np.arange(480, 600, dtype=np.int16))


def test_dj_decoder_pads_only_the_final_partial_pcm_frame(monkeypatch):
    import types

    monkeypatch.setattr(katherine, "np", np)
    thread_id = "dj-pcm-continuity-test"
    decoded_samples = np.arange(600, dtype=np.int16)
    enqueued = []

    class FakeFrame:
        def __init__(self, values):
            self.values = values

        def to_ndarray(self):
            return self.values.reshape(1, -1)

    class FakeResampler:
        def __init__(self, **kwargs):
            pass

        def resample(self, frame):
            if frame is None:
                return []
            return [
                FakeFrame(decoded_samples[:300]),
                FakeFrame(decoded_samples[300:]),
            ]

    class FakeContainer:
        streams = [types.SimpleNamespace(type="audio", duration=None, time_base=None)]

        def decode(self, stream):
            yield object()

        def close(self):
            pass

    monkeypatch.setattr(
        katherine, "pyav",
        types.SimpleNamespace(
            open=lambda *args, **kwargs: FakeContainer(),
            AudioResampler=FakeResampler,
        ),
    )

    async def fake_enqueue(key, state, samples):
        enqueued.append(np.asarray(samples, dtype=np.int16).copy())
        return True

    monkeypatch.setattr(katherine, "enqueue_music_audio_samples", fake_enqueue)
    state = {
        "stop_event": asyncio.Event(),
        "playback_stop_event": asyncio.Event(),
        "skip_event": asyncio.Event(),
        "pause_event": asyncio.Event(),
        "music_audio_queue": asyncio.Queue(maxsize=2),
        "transition_ms": 0,
    }
    state["pause_event"].set()
    monkeypatch.setattr(katherine, "DJ_ROOMS", {thread_id: state})

    result = asyncio.run(
        katherine._dj_play_single(
            thread_id, __import__("pathlib").Path("unused.mp3"), state
        )
    )

    assert result is True
    assert len(enqueued) == 1
    assert np.array_equal(enqueued[0][:600], decoded_samples)
    assert np.count_nonzero(enqueued[0][600:]) == 0
    assert state["music_decoder_finished"] is True

def test_music_mixer_does_not_start_before_optional_audio_dependencies(monkeypatch):
    thread_id = "dj-dependency-startup-guard"
    state = {
        "stop_event": asyncio.Event(),
        "audio_source": object(),
        "mixer_task": None,
    }
    monkeypatch.setattr(katherine, "DJ_ROOMS", {thread_id: state})
    monkeypatch.setattr(katherine, "np", None)
    monkeypatch.setattr(katherine, "livekit_rtc", None)

    assert katherine.ensure_audio_mixer_running(thread_id, state) is False
    assert state["mixer_task"] is None


def test_audius_search_uses_official_stream_endpoint_and_ignores_unstreamable_tracks(monkeypatch):
    class FakeResponse:
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {
                "data": [
                    {
                        "id": "track_123",
                        "title": "Ambient Lights",
                        "isStreamable": True,
                        "user": {"name": "Independent Artist"},
                        "permalink": "https://audius.co/artist/ambient-lights",
                    },
                    {
                        "id": "track_456",
                        "title": "Unavailable Track",
                        "isStreamable": False,
                        "user": {"name": "Other Artist"},
                    },
                ]
            }

    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(katherine, "external_request", fake_request)
    katherine.DJ_SOURCE_CACHE.pop("audius:ambient lights", None)

    tracks = asyncio.run(katherine.search_audius_audio("ambient lights", limit=5))

    assert len(tracks) == 1
    assert tracks[0]["source"] == "Audius"
    assert tracks[0]["creator"] == "Independent Artist"
    assert tracks[0]["url"] == (
        "https://api.audius.co/v1/tracks/track_123/stream"
        "?app_name=KatherineAnarchy"
    )
    assert calls[0][1].endswith("/tracks/search")
    assert calls[0][2]["params"]["query"] == "ambient lights"


def test_ccmixter_search_requires_allowed_license_and_resolves_audio_playlist(monkeypatch):
    class FakeResponse:
        def __init__(self, payload=None, text=""):
            self.payload = payload
            self.text = text
            self.is_success = True
            self.status_code = 200

        def json(self):
            return self.payload

    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url == katherine.CCMIXTER_API:
            return FakeResponse(payload={
                "records": [
                    {
                        "upload_id": "123",
                        "upload_name": "Creative Ambient",
                        "user_name": "Artist One",
                        "license_url": "https://creativecommons.org/licenses/by/3.0/",
                        "file_page_url": "https://ccmixter.org/files/artistone/123",
                        "stream_url": "https://ccmixter.org/api/query/stream.m3u?f=m3u&ids=123",
                    },
                    {
                        "upload_id": "456",
                        "upload_name": "Restricted Track",
                        "user_name": "Artist Two",
                        "license_url": "https://creativecommons.org/licenses/by-nc-nd/3.0/",
                        "stream_url": "https://ccmixter.org/api/query/stream.m3u?f=m3u&ids=456",
                    },
                ]
            })
        return FakeResponse(
            text="#EXTM3U\n#EXTINF:180,Creative Ambient\n"
            "https://ccmixter.org/content/artistone/creative-ambient.mp3\n"
        )

    monkeypatch.setattr(katherine, "external_request", fake_request)
    katherine.DJ_SOURCE_CACHE.pop("ccmixter:ambient", None)

    tracks = asyncio.run(katherine.search_ccmixter_audio("ambient", limit=5))

    assert len(tracks) == 1
    assert tracks[0]["source"] == "ccMixter"
    assert tracks[0]["title"] == "Creative Ambient"
    assert tracks[0]["license"] == "CC BY 3.0 · Requiere atribución"
    assert tracks[0]["url"].endswith("/creative-ambient.mp3")
    assert len(calls) == 2


def test_music_catalog_keeps_results_when_new_sources_fail(monkeypatch):
    async def ok_archive(query, limit):
        return [{"url": "https://archive.org/good.mp3", "title": "Safe track"}]

    async def fail(*args, **kwargs):
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(katherine, "search_archive_audio", ok_archive)
    monkeypatch.setattr(katherine, "search_wikimedia_audio", fail)
    monkeypatch.setattr(katherine, "search_ccmixter_audio", fail)
    monkeypatch.setattr(katherine, "search_audius_audio", fail)
    monkeypatch.setattr(katherine, "search_loc_national_jukebox_audio", fail)
    monkeypatch.setattr(katherine, "JAMENDO_CLIENT_ID", "")

    tracks = asyncio.run(katherine.search_music_catalog("safe track", limit=5))

    assert len(tracks) == 1
    assert tracks[0]["url"] == "https://archive.org/good.mp3"



def test_dj_mixer_drains_full_prebuffer_before_first_livekit_frame(monkeypatch):
    import types

    monkeypatch.setattr(katherine, "np", np)
    captured = []
    first_frame_sent = asyncio.Event()

    class FakeAudioSource:
        queued_duration = 0.0

        async def capture_frame(self, frame):
            captured.append(np.frombuffer(bytes(frame.data), dtype=np.int16).copy())
            first_frame_sent.set()

    class FakeAudioFrame:
        @staticmethod
        def create(sample_rate, channels, samples):
            assert (sample_rate, channels, samples) == (48000, 1, 960)
            return types.SimpleNamespace(data=bytearray(samples * 2))

    monkeypatch.setattr(
        katherine,
        "livekit_rtc",
        types.SimpleNamespace(AudioFrame=FakeAudioFrame),
    )

    async def scenario():
        thread_id = "dj-prebuffer-deadlock-regression"
        stop_event = asyncio.Event()
        music_queue = asyncio.Queue(maxsize=32)
        for index in range(katherine.DJ_MIXER_PREBUFFER_FRAMES):
            await music_queue.put(
                np.full(katherine.DJ_MIXER_FRAME_SAMPLES, index + 1, dtype=np.int16)
            )

        state = {
            "stop_event": stop_event,
            "music_audio_queue": music_queue,
            "audio_source": FakeAudioSource(),
            "speaking": asyncio.Event(),
            "volume": 1.0,
            "duck_enabled": False,
            "music_duck_gain": 1.0,
            "music_buffer_started": False,
            "music_decoder_finished": False,
            "music_pending_samples": 0,
            "mixer_task": None,
        }
        katherine.DJ_ROOMS[thread_id] = state
        task = asyncio.create_task(katherine.audio_mixer_loop(thread_id))
        try:
            await asyncio.wait_for(first_frame_sent.wait(), timeout=0.5)
            assert state["music_buffer_started"] is True
            assert len(captured) >= 1
            expected = np.concatenate([
                np.full(katherine.DJ_MIXER_FRAME_SAMPLES, index + 1, dtype=np.int16)
                for index in range(2)
            ])
            assert np.array_equal(captured[0], expected)
        finally:
            stop_event.set()
            await asyncio.wait_for(task, timeout=0.5)
            katherine.DJ_ROOMS.pop(thread_id, None)

    asyncio.run(scenario())



def test_freesound_search_uses_official_page_links_and_only_commercial_licenses(monkeypatch):
    class FakeResponse:
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {
                "results": [
                    {
                        "id": 12345,
                        "name": "Ambient Piano",
                        "username": "ArtistOne",
                        "license": "Attribution",
                        "duration": 120.0,
                        "filesize": 1024,
                        "url": "https://freesound.org/people/ArtistOne/sounds/12345/",
                        "previews": {
                            "preview-hq-mp3": "https://cdn.freesound.org/previews/12/12345_67890-hq.mp3"
                        },
                    },
                    {
                        "id": 12347,
                        "name": "Soft Ambient",
                        "username": "ArtistThree",
                        "license": "Creative Commons 0",
                        "duration": 90.0,
                        "filesize": 2048,
                        "url": "https://freesound.org/people/ArtistThree/sounds/12347/",
                        "previews": {
                            "preview-hq-ogg": "https://freesound.org/data/previews/12/12347_67890-hq.ogg"
                        },
                    },
                    {
                        "id": 12346,
                        "name": "Noncommercial track",
                        "username": "ArtistTwo",
                        "license": "Attribution NonCommercial",
                        "duration": 180.0,
                        "filesize": 1024,
                        "url": "https://freesound.org/people/ArtistTwo/sounds/12346/",
                        "previews": {
                            "preview-hq-mp3": "https://cdn.freesound.org/previews/12/12346_67890-hq.mp3"
                        },
                    },
                    {
                        "id": 12348,
                        "name": "Untrusted preview",
                        "username": "ArtistFour",
                        "license": "Attribution",
                        "duration": 120.0,
                        "filesize": 1024,
                        "url": "https://freesound.org/people/ArtistFour/sounds/12348/",
                        "previews": {
                            "preview-hq-mp3": "https://evil.example/preview.mp3"
                        },
                    },
                    {
                        "id": 12349,
                        "name": "Untrusted page URL",
                        "username": "ArtistFive",
                        "license": "Attribution",
                        "duration": 120.0,
                        "filesize": 1024,
                        "url": "https://evil.example/sounds/12349/",
                        "previews": {
                            "preview-hq-mp3": "https://cdn.freesound.org/previews/12/12349_67890-hq.mp3"
                        },
                    },
                ]
            }

    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(katherine, "FREESOUND_API_KEY", "test-freesound-key")
    monkeypatch.setattr(katherine, "external_request", fake_request)
    katherine.DJ_SOURCE_CACHE.pop("freesound:ambient piano", None)

    tracks = asyncio.run(katherine.search_freesound_audio("ambient piano", limit=5))

    assert [track["identifier"] for track in tracks] == ["12345", "12347"]
    by_id = {track["identifier"]: track for track in tracks}
    attribution = by_id["12345"]
    assert attribution["source"] == "Freesound"
    assert attribution["license"] == "CC BY 4.0 · Requiere atribución"
    assert attribution["item_url"] == "https://freesound.org/people/ArtistOne/sounds/12345/"
    assert attribution["url"].endswith("12345_67890-hq.mp3")
    assert "[Ambient Piano](https://freesound.org/people/ArtistOne/sounds/12345/)" in attribution["credit_line"]
    assert "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)" in attribution["credit_line"]
    assert by_id["12347"]["license"] == "CC0 · Dominio público"
    assert by_id["12347"]["credit_line"] == ""

    method, url, request = calls[0]
    assert method == "GET"
    assert url == "https://freesound.org/apiv2/search/"
    assert request["headers"]["Authorization"] == "Token test-freesound-key"
    assert request["params"]["filter"] == 'duration:[30 TO *] license:(Attribution OR "Creative Commons 0")'
    assert "previews" in request["params"]["fields"]
    assert "url" in request["params"]["fields"]


def test_freesound_search_is_disabled_without_an_api_key(monkeypatch):
    async def unexpected_request(*args, **kwargs):
        raise AssertionError("Freesound must not be called without its API key")

    monkeypatch.setattr(katherine, "FREESOUND_API_KEY", "")
    monkeypatch.setattr(katherine, "external_request", unexpected_request)
    assert asyncio.run(katherine.search_freesound_audio("ambient piano")) == []


def test_recoverable_dj_failure_includes_track_valueerror_and_runtimeerror_markers():
    from katherine_astra import is_recoverable_dj_failure

    assert is_recoverable_dj_failure("El enlace de audio está mal formado.")
    assert is_recoverable_dj_failure("El archivo descargado no se puede decodificar (ValueError).")
    assert is_recoverable_dj_failure("La descarga redirigió a un dominio no autorizado.")
    assert not is_recoverable_dj_failure("No pude conectarme a la llamada (RuntimeError).")







def test_loc_national_jukebox_uses_only_pre_1923_downloadable_music(monkeypatch):
    class FakeResponse:
        is_success = True
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url == katherine.LOC_JUKEBOX_API:
            return FakeResponse({
                "results": [
                    {
                        "id": "http://www.loc.gov/item/jukebox-1910/",
                        "title": "Old Waltz",
                        "date": "1910-04-12",
                        "genre": ["Popular Music"],
                    },
                    {
                        "id": "http://www.loc.gov/item/jukebox-restricted/",
                        "title": "Restricted Waltz",
                        "date": "1912",
                        "genre": ["Popular Music"],
                    },
                    {
                        "id": "http://www.loc.gov/item/jukebox-1924/",
                        "title": "Protected Recording",
                        "date": "1924",
                        "genre": ["Popular Music"],
                    },
                ]
            })

        if "jukebox-1910" in url:
            return FakeResponse({
                "item": {
                    "title": "Old Waltz",
                    "recording_date": "1910-04-12",
                    "genre": ["Popular Music"],
                    "audio_type": "Musical",
                    "contributor_names": ["The Example Orchestra"],
                    "access_restricted": False,
                },
                "resources": [{
                    "files": [[{
                        "download": "https://tile.loc.gov/streaming-services/iiif/old-waltz/default.mp3",
                        "filename": "https://tile.loc.gov/storage-services/service/old-waltz.mp3",
                        "mimetype": "audio/mpeg",
                        "rights_restricted": False,
                        "canDownload": True,
                        "duration": 125,
                    }]]
                }],
            })
        if "jukebox-restricted" in url:
            return FakeResponse({
                "item": {
                    "title": "Restricted Waltz",
                    "recording_date": "1912",
                    "genre": ["Popular Music"],
                    "audio_type": "Musical",
                },
                "resources": [{
                    "files": [[{
                        "download": "https://tile.loc.gov/streaming-services/iiif/restricted/default.mp3",
                        "mimetype": "audio/mpeg",
                        "rights_restricted": True,
                        "canDownload": False,
                        "duration": 180,
                    }]]
                }],
            })
        raise AssertionError(f"Unexpected Library of Congress request: {url}")

    monkeypatch.setattr(katherine, "external_request", fake_request)
    katherine.DJ_SOURCE_CACHE.pop("loc_national_jukebox:old waltz", None)

    tracks = asyncio.run(
        katherine.search_loc_national_jukebox_audio("old waltz", limit=5)
    )

    assert len(tracks) == 1
    track = tracks[0]
    assert track["source"] == "Library of Congress"
    assert track["source_key"] == "loc_national_jukebox"
    assert track["title"] == "Old Waltz"
    assert track["creator"] == "The Example Orchestra"
    assert track["license"].startswith("Dominio público en EE. UU.")
    assert track["url"].endswith("/default.mp3")
    assert "Library of Congress" in track["credit_line"]

    search_call = calls[0]
    assert search_call[1] == katherine.LOC_JUKEBOX_API
    assert search_call[2]["params"]["dates"] == "1900/1922"
    assert "partof_repository:recorded sound section" in search_call[2]["params"]["fa"]
    assert search_call[2]["safe_public"] is True
    detail_calls = [call for call in calls if call[1] != katherine.LOC_JUKEBOX_API]
    assert len(detail_calls) == 2
    assert all(call[2]["safe_public"] is True for call in detail_calls)


def test_loc_national_jukebox_returns_empty_for_blank_query(monkeypatch):
    async def unexpected_request(*args, **kwargs):
        raise AssertionError("Blank National Jukebox queries must not call the API")

    monkeypatch.setattr(katherine, "external_request", unexpected_request)
    assert asyncio.run(katherine.search_loc_national_jukebox_audio("   ")) == []



def test_uncommon_ear_search_uses_verified_commercial_tracks_and_preserves_credit(monkeypatch):
    class FakeResponse:
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {
                "tracks": [
                    {
                        "id": "ue-cc0", "title": "Quiet Dawn", "artist": "Artist One",
                        "kind": "music", "duration_s": 95,
                        "license": {"id": "cc0", "url": "https://creativecommons.org/publicdomain/zero/1.0/", "status": "verified"},
                        "terms": {"commercial": True, "credit": "none", "share_alike": False},
                        "attribution": "optional", "credit_line": "",
                        "preview_url": "https://media.uncommonear.com/p/ue-cc0.mp3",
                        "page_url": "https://uncommonear.com/browse?t=ue-cc0",
                    },
                    {
                        "id": "ue-by", "title": "Ambient Piano", "artist": "Artist Two",
                        "kind": "music", "duration_s": 120,
                        "license": {"id": "cc-by-4.0", "url": "https://creativecommons.org/licenses/by/4.0/", "status": "verified"},
                        "terms": {"commercial": True, "credit": "required", "share_alike": False},
                        "attribution": "required", "credit_line": '"Ambient Piano" by Artist Two (CC BY 4.0)',
                        "preview_url": "https://media.uncommonear.com/p/ue-by.mp3",
                        "page_url": "https://uncommonear.com/browse?t=ue-by",
                    },
                    {
                        "id": "ue-nc", "title": "Noncommercial", "artist": "Artist Three",
                        "kind": "music", "duration_s": 100,
                        "license": {"id": "cc-by-nc-4.0", "url": "https://creativecommons.org/licenses/by-nc/4.0/", "status": "verified"},
                        "terms": {"commercial": False, "credit": "required", "share_alike": False},
                        "attribution": "required", "credit_line": "Do not use",
                        "preview_url": "https://media.uncommonear.com/p/ue-nc.mp3",
                        "page_url": "https://uncommonear.com/browse?t=ue-nc",
                    },
                    {
                        "id": "ue-unverified", "title": "Unverified", "artist": "Artist Four",
                        "kind": "music", "duration_s": 100,
                        "license": {"id": "cc0", "url": "https://creativecommons.org/publicdomain/zero/1.0/", "status": "listed"},
                        "terms": {"commercial": True, "credit": "none", "share_alike": False},
                        "attribution": "optional", "credit_line": "",
                        "preview_url": "https://media.uncommonear.com/p/ue-unverified.mp3",
                        "page_url": "https://uncommonear.com/browse?t=ue-unverified",
                    },
                    {
                        "id": "ue-evil", "title": "Untrusted host", "artist": "Artist Five",
                        "kind": "music", "duration_s": 100,
                        "license": {"id": "cc0", "url": "https://creativecommons.org/publicdomain/zero/1.0/", "status": "verified"},
                        "terms": {"commercial": True, "credit": "none", "share_alike": False},
                        "attribution": "optional", "credit_line": "",
                        "preview_url": "https://evil.example/ue-evil.mp3",
                        "page_url": "https://uncommonear.com/browse?t=ue-evil",
                    },
                ]
            }

    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(katherine, "UNCOMMON_EAR_API_KEY", "test-uncommon-ear-key")
    monkeypatch.setattr(katherine, "external_request", fake_request)
    katherine.DJ_SOURCE_CACHE.pop("uncommon_ear:ambient piano", None)

    tracks = asyncio.run(katherine.search_uncommon_ear_audio("ambient piano", limit=5))

    assert [track["identifier"] for track in tracks] == ["ue-cc0", "ue-by"]
    assert tracks[0]["license"].startswith("CC0")
    assert tracks[0]["credit_line"] == ""
    assert tracks[1]["license"].startswith("CC BY 4.0")
    assert tracks[1]["credit_line"] == '"Ambient Piano" by Artist Two (CC BY 4.0)'
    method, url, request = calls[0]
    assert method == "GET"
    assert url == "https://api.uncommonear.com/v1/search"
    assert request["headers"]["Authorization"] == "Bearer test-uncommon-ear-key"
    assert request["params"]["kind"] == "music"
    assert request["params"]["commercial"] is True
    assert request["params"]["share_alike"] is False


def test_uncommon_ear_search_is_disabled_without_an_api_key(monkeypatch):
    async def unexpected_request(*args, **kwargs):
        raise AssertionError("Uncommon Ear must not be called without its API key")

    monkeypatch.setattr(katherine, "UNCOMMON_EAR_API_KEY", "")
    monkeypatch.setattr(katherine, "external_request", unexpected_request)
    assert asyncio.run(katherine.search_uncommon_ear_audio("ambient piano")) == []
    assert asyncio.run(katherine.search_uncommon_ear_audio("   ")) == []



def test_openverse_search_filters_licenses_and_preserves_attribution(monkeypatch):
    class FakeResponse:
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {
                "results": [
                    {
                        "id": "open-1",
                        "title": "Ambient piano",
                        "creator": "Example Artist",
                        "url": "https://upload.wikimedia.org/audio/ambient.mp3",
                        "foreign_landing_url": "https://example.org/audio/open-1",
                        "license": "by",
                        "license_url": "https://creativecommons.org/licenses/by/4.0/",
                        "duration": 120000,
                        "filesize": 1000000,
                        "attribution": "Ambient piano by Example Artist",
                    },
                    {
                        "id": "nc-1",
                        "title": "Non commercial",
                        "url": "https://upload.wikimedia.org/audio/nc.mp3",
                        "foreign_landing_url": "https://example.org/audio/nc-1",
                        "license": "by-nc",
                        "license_url": "https://creativecommons.org/licenses/by-nc/4.0/",
                        "duration": 120000,
                        "filesize": 1000000,
                    },
                    {
                        "id": "evil-1",
                        "title": "Untrusted host",
                        "url": "https://evil.example/audio.mp3",
                        "foreign_landing_url": "https://example.org/audio/evil-1",
                        "license": "cc0",
                        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                        "duration": 120000,
                        "filesize": 1000000,
                    },
                ]
            }

    calls = []

    async def fake_request(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse()

    monkeypatch.setattr(katherine, "external_request", fake_request)
    katherine.DJ_SOURCE_CACHE.pop("openverse:ambient piano", None)
    tracks = asyncio.run(katherine.search_openverse_audio("ambient piano"))

    assert len(tracks) == 1
    assert tracks[0]["source"] == "Openverse"
    assert tracks[0]["license"].startswith("CC BY")
    assert tracks[0]["credit_line"] == "Ambient piano by Example Artist"
    assert calls[0][1]["params"]["license_type"] == "commercial"
    assert calls[0][1]["params"]["license"] == "cc0,pdm,by,by-sa"


def test_openverse_search_returns_empty_for_blank_query():
    assert asyncio.run(katherine.search_openverse_audio("   ")) == []
