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

    result = asyncio.run(
        katherine._dj_play_single(
            thread_id, __import__("pathlib").Path("unused.mp3"),
            katherine.DJ_ROOMS[thread_id],
        )
    )

    assert result is True
    assert len(enqueued) == 1
    assert np.array_equal(enqueued[0], delayed)



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
    monkeypatch.setattr(katherine, "DJ_ROOMS", {thread_id: state})

    result = asyncio.run(
        katherine._dj_play_single(
            thread_id, __import__("pathlib").Path("unused.mp3"), state
        )
    )

    assert result is True
    assert len(enqueued) == 2
    assert np.array_equal(enqueued[0], decoded_samples[:480])
    assert np.array_equal(enqueued[1][:120], decoded_samples[480:])
    assert np.count_nonzero(enqueued[1][120:]) == 0
    assert state["music_decoder_finished"] is True
