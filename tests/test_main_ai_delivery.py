"""Regression tests for AI tool output delivery capture."""
import asyncio
import inspect

import pytest

import main as bot_main


def test_ai_plan_executor_has_one_safe_definition():
    parameters = inspect.signature(bot_main.execute_ai_plan).parameters
    assert "request_text" in parameters


@pytest.mark.asyncio
async def test_ai_plan_executor_rejects_side_effect_tools_before_execution(monkeypatch):
    calls = []

    async def fake_execute(*args, **kwargs):
        calls.append((args, kwargs))
        return {"success": True, "output": "fake"}

    monkeypatch.setattr(bot_main, "execute_ai_tool", fake_execute)
    executed = await bot_main.execute_ai_plan(
        {"threadId": "room", "message": {}},
        "developer",
        "session",
        ["pagar @Ana 100"],
        request_text="consulta",
    )
    assert executed is False
    assert calls == []



def test_genshin_db_no_match_metadata_is_never_rendered_as_an_entity():
    payload = {
        "query": "natlan genshin impact",
        "folder": "characters",
        "matchFolder": "characters",
        "matchtype": "none",
    }
    answer = bot_main.describe_game(payload, "Natlan", "characters")
    assert answer == "No encontré información de «Natlan»."
    assert "Matchtype" not in answer
    assert "Query:" not in answer
    assert "Folder:" not in answer


def test_geography_descriptions_use_region_fields():
    data = {
        "name": "Natlan",
        "description": "Una de las naciones de Teyvat.",
        "archon": "Pyro",
    }
    answer = bot_main.describe_game(data, "Natlan", "geographies")
    assert "GENSHIN IMPACT — Natlan" in answer
    assert "Arconte: Pyro" in answer
    assert "Una de las naciones de Teyvat" in answer


@pytest.mark.asyncio
async def test_failed_message_delivery_is_not_saved_to_ai_context(monkeypatch):
    captured = []
    token = bot_main.AI_TOOL_OUTPUT_CAPTURE.set(captured)
    monkeypatch.setattr(bot_main, "last_bot_send", 0.0)
    monkeypatch.setitem(bot_main.last_chat_send, "developer:test-room", 0.0)

    async def failed_api(method, path, source="developer", body=None):
        return {
            "ok": False,
            "status": 403,
            "data": {"error": "forbidden"},
        }

    monkeypatch.setattr(bot_main, "api", failed_api)
    try:
        result = await bot_main.send_message(
            "test-room", "El resultado de herramienta no se entregó."
        )
    finally:
        bot_main.AI_TOOL_OUTPUT_CAPTURE.reset(token)

    assert result["ok"] is False
    assert captured == []


@pytest.mark.asyncio
async def test_successful_message_delivery_is_saved_to_ai_context(monkeypatch):
    captured = []
    token = bot_main.AI_TOOL_OUTPUT_CAPTURE.set(captured)
    monkeypatch.setattr(bot_main, "last_bot_send", 0.0)
    monkeypatch.setitem(bot_main.last_chat_send, "developer:test-room-success", 0.0)

    async def successful_api(method, path, source="developer", body=None):
        return {
            "ok": True,
            "status": 200,
            "data": {"messageId": "test-message"},
        }

    monkeypatch.setattr(bot_main, "api", successful_api)
    try:
        result = await bot_main.send_message(
            "test-room-success", "El resultado de herramienta sí se entregó."
        )
    finally:
        bot_main.AI_TOOL_OUTPUT_CAPTURE.reset(token)

    assert result["ok"] is True
    assert captured == ["El resultado de herramienta sí se entregó."]



def test_long_messages_are_split_into_blocks_of_at_most_2000_utf16_units():
    content = "A" * 4500
    parts = bot_main.split_message_content(content)
    assert [bot_main.utf16_length(part) for part in parts] == [2000, 2000, 500]
    assert "".join(parts) == content


@pytest.mark.asyncio
async def test_admin_cleanup_deletes_only_tracked_own_message_ids(monkeypatch):
    key = ("developer", "cleanup-room")
    bot_main.BOT_SENT_MESSAGES[key].clear()
    now = bot_main.time.time()
    bot_main.BOT_SENT_MESSAGES[key].extend([
        {"message_id": "own-1", "created_at": now},
        {"message_id": "own-2", "created_at": now + 1},
    ])
    bot_main.BOT_SENT_MESSAGES[("community", "cleanup-room")].append(
        {"message_id": "other-bot", "created_at": 3}
    )
    calls = []

    async def fake_api(method, path, source="developer", body=None):
        calls.append((method, path, source))
        if path.endswith("own-2"):
            return {"ok": False, "status": 403, "data": {}}
        return {"ok": True, "status": 204, "data": {}}

    monkeypatch.setattr(bot_main, "api", fake_api)
    result = await bot_main.delete_katherine_messages("cleanup-room", "developer")
    assert result == {"attempted": 2, "deleted": 1, "failed": 1}
    assert calls == [
        ("DELETE", "/threads/cleanup-room/messages/own-1", "developer"),
        ("DELETE", "/threads/cleanup-room/messages/own-2", "developer"),
    ]
    assert [item["message_id"] for item in bot_main.BOT_SENT_MESSAGES[key]] == ["own-2"]
    assert bot_main.BOT_SENT_MESSAGES[("community", "cleanup-room")][0]["message_id"] == "other-bot"
    bot_main.BOT_SENT_MESSAGES.pop(key, None)
    bot_main.BOT_SENT_MESSAGES.pop(("community", "cleanup-room"), None)



def test_cleanup_command_is_privileged_and_not_available_to_public_ai():
    from katherine_ai import AI_ADMIN_COMMANDS, AI_PUBLIC_COMMANDS, AI_PLAN_SAFE_COMMANDS
    assert "!eliminarmensajes" in bot_main.ADMIN_ONLY_COMMANDS
    assert "eliminarmensajes" in AI_ADMIN_COMMANDS
    assert "eliminarmensajes" not in AI_PUBLIC_COMMANDS
    assert "eliminarmensajes" not in AI_PLAN_SAFE_COMMANDS



@pytest.mark.asyncio
async def test_admin_cleanup_ignores_expired_ephemeral_message_ids(monkeypatch):
    key = ("developer", "cleanup-expired-room")
    bot_main.BOT_SENT_MESSAGES[key].clear()
    bot_main.BOT_SENT_MESSAGES[key].extend([
        {"message_id": "expired", "created_at": 1},
    ])
    calls = []

    async def fake_api(method, path, source="developer", body=None):
        calls.append(path)
        return {"ok": True, "status": 204, "data": {}}

    monkeypatch.setattr(bot_main, "api", fake_api)
    result = await bot_main.delete_katherine_messages("cleanup-expired-room", "developer")
    assert result == {"attempted": 0, "deleted": 0, "failed": 0}
    assert calls == []
    assert key not in bot_main.BOT_SENT_MESSAGES



def test_periodic_cleanup_removes_expired_own_message_ids():
    key = ("developer", "cleanup-periodic-room")
    bot_main.BOT_SENT_MESSAGES[key].clear()
    bot_main.BOT_SENT_MESSAGES[key].append({"message_id": "expired", "created_at": 1})
    bot_main.cleanup_expired_state()
    assert key not in bot_main.BOT_SENT_MESSAGES


@pytest.mark.asyncio
async def test_send_message_retries_429_until_block_is_accepted(monkeypatch):
    calls = []

    async def no_wait(_seconds):
        return None

    async def rate_limited_then_success(method, path, source="developer", body=None):
        calls.append((method, path, body))
        if len(calls) == 1:
            return {"ok": False, "status": 429, "retry_after": "0.5", "data": {}}
        return {"ok": True, "status": 200, "data": {"messageId": "retry-success"}}

    monkeypatch.setattr(bot_main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(bot_main, "api", rate_limited_then_success)
    monkeypatch.setattr(bot_main, "last_bot_send", 0.0)
    monkeypatch.setitem(bot_main.last_chat_send, "developer:retry-room", 0.0)
    key = ("developer", "retry-room")
    bot_main.BOT_SENT_MESSAGES.pop(key, None)

    result = await bot_main.send_message("retry-room", "Respuesta recuperable")

    assert result["ok"] is True
    assert len(calls) == 2
    assert bot_main.metrics["message_send_429"] >= 1
    assert bot_main.metrics["message_send_retries"] >= 1
    assert bot_main.BOT_SENT_MESSAGES[key][-1]["message_id"] == "retry-success"
    bot_main.BOT_SENT_MESSAGES.pop(key, None)


@pytest.mark.asyncio
async def test_pending_delivery_retries_only_definitive_429(monkeypatch):
    calls = []

    async def no_wait(_seconds):
        return None

    async def rate_limited_then_success(method, path, source="developer", body=None):
        calls.append((method, path, body))
        if len(calls) == 1:
            return {"ok": False, "status": 429, "retry_after": "0.5", "data": {}}
        return {"ok": True, "status": 200, "data": {"messageId": "pending-success"}}

    monkeypatch.setattr(bot_main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(bot_main, "api", rate_limited_then_success)
    monkeypatch.setattr(bot_main, "last_bot_send", 0.0)
    monkeypatch.setitem(bot_main.last_chat_send, "developer:pending-room", 0.0)
    key = ("developer", "pending-room")
    bot_main.BOT_SENT_MESSAGES.pop(key, None)

    await bot_main.retry_pending_delivery(
        "pending-room", "developer", [{"content": "Bloque pendiente"}], expires_in=60
    )

    assert len(calls) == 2
    assert calls[0][2] == {"content": "Bloque pendiente"}
    assert bot_main.metrics["message_send_recovered"] >= 1
    assert bot_main.BOT_SENT_MESSAGES[key][-1]["message_id"] == "pending-success"
    bot_main.BOT_SENT_MESSAGES.pop(key, None)


@pytest.mark.asyncio
async def test_pending_delivery_does_not_retry_ambiguous_server_errors(monkeypatch):
    calls = []

    async def no_wait(_seconds):
        return None

    async def server_error(method, path, source="developer", body=None):
        calls.append(method)
        return {"ok": False, "status": 500, "data": {"error": "server"}}

    monkeypatch.setattr(bot_main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(bot_main, "api", server_error)

    await bot_main.retry_pending_delivery(
        "pending-ambiguous-room", "developer", [{"content": "No duplicar"}], expires_in=60
    )

    assert calls == ["POST"]
    assert bot_main.metrics["message_send_pending_abandoned"] >= 1


@pytest.mark.asyncio
async def test_rate_limited_response_is_marked_queued_to_prevent_fallback_duplicates(monkeypatch):
    calls = []
    scheduled = []

    async def no_wait(_seconds):
        return None

    async def always_rate_limited(method, path, source="developer", body=None):
        calls.append((method, path, body))
        return {"ok": False, "status": 429, "retry_after": "0.5", "data": {}}

    def capture_schedule(thread_id, source, pending_bodies):
        scheduled.append((thread_id, source, pending_bodies))
        return True

    monkeypatch.setattr(bot_main.asyncio, "sleep", no_wait)
    monkeypatch.setattr(bot_main, "api", always_rate_limited)
    monkeypatch.setattr(bot_main, "schedule_pending_delivery", capture_schedule)
    monkeypatch.setattr(bot_main, "last_bot_send", 0.0)
    monkeypatch.setitem(bot_main.last_chat_send, "developer:queued-room", 0.0)

    result = await bot_main.send_message("queued-room", "Se recuperará")

    assert result["ok"] is True
    assert result["queued"] is True
    assert result["data"]["queuedBlocks"] == 1
    assert len(calls) == 4
    assert len(scheduled) == 1


@pytest.mark.asyncio
async def test_ai_tool_failure_is_not_reported_as_success(monkeypatch):
    async def failing_command(event, source):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(bot_main, "handle_command", failing_command)
    result = await bot_main.execute_ai_tool(
        {"threadId": "room", "message": {"content": "consulta"}},
        "developer", "room:user", "ayuda", silent=True,
    )
    assert result["output"]
    assert result["success"] is False


def test_webhook_limits_are_explicit():
    assert bot_main.MAX_WEBHOOK_BODY_BYTES == 1_048_576
    assert bot_main.MAX_WEBHOOK_BACKGROUND_TASKS == 100


@pytest.mark.asyncio
async def test_webhook_body_reader_rejects_oversized_stream():
    class FakeRequest:
        headers = {}

        async def stream(self):
            yield b"1234"
            yield b"5678"

    with pytest.raises(bot_main.HTTPException) as error:
        await bot_main.read_limited_webhook_body(FakeRequest(), limit=6)
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_webhook_body_reader_accepts_bounded_stream():
    class FakeRequest:
        headers = {"content-length": "5"}

        async def stream(self):
            yield b"he"
            yield b"llo"

    assert await bot_main.read_limited_webhook_body(FakeRequest(), limit=5) == b"hello"


@pytest.mark.asyncio
async def test_avatarbot_updates_and_verifies_both_bot_identities_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_main, "DB_PATH", tmp_path / "avatar-bot-test.sqlite3")
    bot_main.init_db()
    monkeypatch.setattr(bot_main, "ADMIN_UIDS", {"trusted-admin"})
    calls = []
    sent = []

    async def fake_resolve(_url):
        return b"fake-image", "image/png"

    async def fake_upload(path, target, content, mime):
        calls.append((path, target, content, mime))
        return {"ok": True, "status": 200, "data": {"bot": {"icon": f"https://cdn.example/{target}.png"}}}

    async def fake_send(thread_id, content, source="developer", reply_id=None, buttons=None, **kwargs):
        sent.append(str(content))
        return {"ok": True}

    monkeypatch.setattr(bot_main, "resolve_avatar_image", fake_resolve)
    monkeypatch.setattr(bot_main, "api_upload_bytes", fake_upload)
    monkeypatch.setattr(bot_main, "send_message", fake_send)

    event = {
        "threadId": "avatar-admin-room",
        "message": {
            "content": "!avatarbot https://images.example/katherine.png",
            "author": {"uid": "trusted-admin", "nickname": "Admin"},
        },
    }
    await bot_main.handle_command(event, "developer")

    assert [item[1] for item in calls] == ["developer", "community"]
    assert all(item[2:] == (b"fake-image", "image/png") for item in calls)
    assert sent and "developer: actualizado" in sent[0] and "community: actualizado" in sent[0]


def test_admin_bot_target_parser_defaults_to_both_and_accepts_explicit_target():
    assert bot_main.parse_admin_bot_targets("https://example.com/avatar.png") == (
        ["developer", "community"], "https://example.com/avatar.png"
    )
    assert bot_main.parse_admin_bot_targets("comunidad https://example.com/a.png") == (
        ["community"], "https://example.com/a.png"
    )
    assert bot_main.parse_admin_bot_targets("desarrollador Nombre") == (
        ["developer"], "Nombre"
    )


@pytest.mark.asyncio
async def test_avatarbot_remains_uid_protected(monkeypatch, tmp_path):
    monkeypatch.setattr(bot_main, "DB_PATH", tmp_path / "avatar-admin-test.sqlite3")
    bot_main.init_db()
    monkeypatch.setattr(bot_main, "ADMIN_UIDS", {"trusted-admin"})
    sent = []

    async def fake_send(thread_id, content, source="developer", reply_id=None, buttons=None, **kwargs):
        sent.append(str(content))
        return {"ok": True}

    monkeypatch.setattr(bot_main, "send_message", fake_send)
    event = {
        "threadId": "avatar-admin-room",
        "message": {
            "content": "!avatarbot https://images.example/katherine.png",
            "author": {"uid": "not-admin", "nickname": "trusted-admin"},
        },
    }
    await bot_main.handle_command(event, "developer")
    assert sent and "Acceso restringido" in sent[0]


@pytest.mark.asyncio
async def test_research_source_links_are_preserved_only_when_explicitly_allowed(monkeypatch):
    safe_url = "https://example.org/article"
    safe = bot_main.sanitize_chat_text(f"Fuente [Abrir fuente]({safe_url})", allow_safe_links=True)
    assert safe == f"Fuente [Abrir fuente]({safe_url})"
    ordinary = bot_main.sanitize_chat_text(f"Visita [Abrir fuente]({safe_url})")
    assert "https://" not in ordinary
    unsafe = bot_main.sanitize_chat_text(
        "Fuente [interna](https://127.0.0.1/admin)", allow_safe_links=True
    )
    assert "127.0.0.1" not in unsafe



def test_reply_reference_accepts_flat_and_nested_webhook_shapes():
    flat_id, flat_text = bot_main.extract_reply_reference({"replyMessageId": "katherine-1"})
    nested_id, nested_text = bot_main.extract_reply_reference({
        "replyTo": {"message": {"id": "katherine-2", "content": "Respuesta anterior"}},
    })
    assert flat_id == "katherine-1" and flat_text == ""
    assert nested_id == "katherine-2" and nested_text == "Respuesta anterior"


def test_only_confirmed_katherine_messages_trigger_reply_routing():
    key = ("developer", "reply-room")
    bot_main.BOT_SENT_MESSAGES[key].clear()
    bot_main.BOT_SENT_MESSAGES[key].append({
        "message_id": "katherine-known", "created_at": bot_main.time.time(),
    })
    assert bot_main.is_known_katherine_message("katherine-known", "developer", "reply-room")
    assert not bot_main.is_known_katherine_message("another-user-id", "developer", "reply-room")
    bot_main.BOT_SENT_MESSAGES.pop(key, None)


@pytest.mark.asyncio
async def test_reply_to_katherine_routes_plain_text_to_assistant(monkeypatch):
    key = ("developer", "reply-route-room")
    bot_main.BOT_SENT_MESSAGES[key].clear()
    bot_main.BOT_SENT_MESSAGES[key].append({
        "message_id": "katherine-reply-target", "created_at": bot_main.time.time(),
    })
    monkeypatch.setattr(bot_main, "check_antispam_message", lambda *_: (False, None))
    monkeypatch.setattr(bot_main.AI_CORE, "record_chat_message", lambda *args, **kwargs: False)

    async def no_speech(*_args, **_kwargs):
        return None
    monkeypatch.setattr(bot_main, "enqueue_message_for_speech", no_speech)
    monkeypatch.setattr(bot_main, "interpret_natural_request", lambda _text: None)
    captured = []

    async def capture_command(event, source):
        captured.append((event, source))
    monkeypatch.setattr(bot_main, "handle_command", capture_command)
    await bot_main.dispatch({
        "type": "message", "threadId": "reply-route-room",
        "message": {
            "messageId": "user-reply-message",
            "replyMessageId": "katherine-reply-target",
            "content": "¿Puedes explicar un poco más?",
            "author": {"uid": "user-1", "nickname": "Usuario"},
        },
    }, "developer")
    assert len(captured) == 1
    routed_event, source = captured[0]
    assert source == "developer"
    assert routed_event["message"]["content"].startswith("!asistente ")
    assert routed_event["message"]["messageId"] == "user-reply-message"
    assert bot_main.metrics["ai_reply_triggers"] >= 1
    bot_main.BOT_SENT_MESSAGES.pop(key, None)


@pytest.mark.asyncio
async def test_stopping_music_keeps_katherine_connected_to_livekit():
    room = type("FakeRoom", (), {"disconnected": False})()
    key = "keep-call-room"
    state = {
        "room": room, "stop_event": bot_main.asyncio.Event(),
        "playback_stop_event": bot_main.asyncio.Event(), "task": None,
        "speech_task": None, "queue": bot_main.deque(),
        "filename": "track.mp3", "track": {"title": "track"},
        "temporary_audio_path": None,
    }
    bot_main.DJ_ROOMS[key] = state
    try:
        assert await bot_main.stop_dj_playback(key)
        assert bot_main.DJ_ROOMS[key] is state
        assert not state["stop_event"].is_set()
        assert state["playback_stop_event"].is_set()
        assert state["filename"] is None and state["track"] is None
        assert not room.disconnected
    finally:
        bot_main.DJ_ROOMS.pop(key, None)


def test_command_help_is_complete_and_keeps_main_categories():
    help_text = bot_main.build_commands_help_text()
    assert len(help_text) > 1800
    assert "GENSHIN IMPACT" in help_text and "MÚSICA Y VOZ" in help_text
    assert "!menu" in help_text and "UID autorizado" in help_text
    assert "!salirsala" in help_text and "!djstop" in help_text
    assert "sin paneles ni botones" in help_text



def test_command_help_lists_all_groups_and_legacy_aliases_without_button_menu():
    help_text = bot_main.build_commands_help_text()
    required = (
        "!asistente", "!ia preguntar", "!investigar", "!analizartexto", "!calcular",
        "!guia", "!materiales", "!equipos", "!rotacion", "!buscargenshin",
        "!enemigo", "!elemento", "!curiosidad", "!deseo", "!resetpity",
        "!interaccion", "!afinidad", "!regalo", "!actividad",
        "!trivia", "!responder", "!adivina", "!intento", "!encuesta",
        "!duelo", "!ship", "!pagar", "!ranking", "!8ball",
        "!join", "!salirsala", "!salas", "!playlist", "!tts",
        "!fecha", "!zona", "!multimedia", "!imagen", "!audio",
        "!miuid", "!antispam", "!advertir", "!perfilbot",
    )
    for command in required:
        assert command in help_text
    assert "paneles ni botones" in help_text
    assert "panel interactivo" not in help_text.casefold()
    assert len(help_text) > 1800


@pytest.mark.asyncio
async def test_send_message_keeps_at_name_literal_without_undocumented_mentions(monkeypatch):
    captured = []
    monkeypatch.setattr(bot_main, "last_bot_send", 0.0)
    monkeypatch.setitem(bot_main.last_chat_send, "developer:mention-test", 0.0)

    async def successful_api(method, path, source="developer", body=None):
        captured.append(body)
        return {"ok": True, "status": 200, "data": {"messageId": "mention-test-id"}}

    monkeypatch.setattr(bot_main, "api", successful_api)
    result = await bot_main.send_message(
        "mention-test", "Hola @Ana, aquí tienes tu respuesta."
    )

    assert result["ok"] is True
    assert captured == [{"content": "Hola @Ana, aquí tienes tu respuesta."}]


@pytest.mark.asyncio
async def test_safe_dispatch_serializes_events_inside_same_room(monkeypatch):
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    processed = []
    key = ("developer", "dispatch-same-room")
    bot_main.event_dispatch_locks.pop(key, None)

    async def fake_dispatch(event, source):
        if event["id"] == "first":
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        processed.append(event["id"])

    monkeypatch.setattr(bot_main, "dispatch", fake_dispatch)
    first = asyncio.create_task(bot_main.safe_dispatch(
        {"type": "message", "threadId": key[1], "id": "first"}, key[0]
    ))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(bot_main.safe_dispatch(
        {"type": "interaction", "threadId": key[1], "id": "second"}, key[0]
    ))
    await asyncio.sleep(0)
    assert not second_started.is_set()

    release_first.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)

    assert processed == ["first", "second"]
    assert key not in bot_main.event_dispatch_locks
    assert bot_main.metrics["webhook_dispatch_queue_waits"] >= 1


@pytest.mark.asyncio
async def test_safe_dispatch_keeps_different_rooms_parallel(monkeypatch):
    started = {
        "room-left": asyncio.Event(),
        "room-right": asyncio.Event(),
    }
    release = asyncio.Event()

    async def fake_dispatch(event, source):
        room = event["threadId"]
        started[room].set()
        await release.wait()

    monkeypatch.setattr(bot_main, "dispatch", fake_dispatch)
    tasks = [
        asyncio.create_task(bot_main.safe_dispatch(
            {"type": "message", "threadId": room}, "developer"
        ))
        for room in started
    ]

    try:
        await asyncio.wait_for(
            asyncio.gather(*(item.wait() for item in started.values())),
            timeout=1,
        )
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert not bot_main.event_dispatch_locks


@pytest.mark.asyncio
async def test_safe_dispatch_counts_failures_without_leaking_room_locks(monkeypatch):
    key = ("developer", "dispatch-failing-room")

    async def failing_dispatch(event, source):
        raise RuntimeError("simulated dispatch failure")

    monkeypatch.setattr(bot_main, "dispatch", failing_dispatch)
    before = bot_main.metrics["webhook_dispatch_failures"]
    await bot_main.safe_dispatch(
        {"type": "message", "threadId": key[1]}, key[0]
    )

    assert bot_main.metrics["webhook_dispatch_failures"] == before + 1
    assert key not in bot_main.event_dispatch_locks



@pytest.mark.asyncio
async def test_command_help_blocks_are_sent_in_order_and_reply_only_to_first(monkeypatch):
    blocks = ["bloque IA", "bloque Genshin", "bloque Social"]
    calls = []

    async def fake_send(thread_id, content, source="developer", reply_id=None):
        calls.append((thread_id, content, source, reply_id))
        return {"ok": True}

    monkeypatch.setattr(bot_main, "build_commands_help_blocks", lambda _prefix: blocks)
    monkeypatch.setattr(bot_main, "send_message", fake_send)

    await bot_main.send_command_help_blocks("room-help", "community", "reply-123")

    assert calls == [
        ("room-help", "bloque IA", "community", "reply-123"),
        ("room-help", "bloque Genshin", "community", None),
        ("room-help", "bloque Social", "community", None),
    ]


@pytest.mark.asyncio
async def test_command_help_delivery_stops_after_first_failed_block(monkeypatch):
    blocks = ["bloque IA", "bloque Genshin", "bloque Social"]
    calls = []

    async def fake_send(thread_id, content, source="developer", reply_id=None):
        calls.append(content)
        if content == "bloque Genshin":
            return {"ok": False, "status": 503}
        return {"ok": True}

    monkeypatch.setattr(bot_main, "build_commands_help_blocks", lambda _prefix: blocks)
    monkeypatch.setattr(bot_main, "send_message", fake_send)

    await bot_main.send_command_help_blocks("room-help-failure", "developer")

    assert calls == ["bloque IA", "bloque Genshin"]



def test_audio_dependencies_are_loaded_only_when_requested(monkeypatch):
    from types import SimpleNamespace

    calls = []
    fake_rtc = SimpleNamespace(Room=object)
    fake_av = SimpleNamespace(open=object)
    fake_numpy = SimpleNamespace(asarray=object)

    monkeypatch.setattr(bot_main, "livekit_rtc", None)
    monkeypatch.setattr(bot_main, "pyav", None)
    monkeypatch.setattr(bot_main, "np", None)
    monkeypatch.setattr(bot_main, "_LIVEKIT_IMPORT_ATTEMPTED", False)
    monkeypatch.setattr(bot_main, "_AUDIO_IMPORT_ATTEMPTED", False)

    def fake_import(name):
        calls.append(name)
        return {
            "livekit.rtc": fake_rtc,
            "av": fake_av,
            "numpy": fake_numpy,
        }[name]

    monkeypatch.setattr(bot_main.importlib, "import_module", fake_import)

    assert bot_main.load_audio_dependencies() is True
    assert bot_main.livekit_rtc is fake_rtc
    assert bot_main.pyav is fake_av
    assert bot_main.np is fake_numpy
    assert calls == ["av", "numpy", "livekit.rtc"]

    assert bot_main.load_audio_dependencies() is True
    assert calls == ["av", "numpy", "livekit.rtc"]


def test_tts_dependency_is_loaded_only_when_reading_is_requested(monkeypatch):
    from types import SimpleNamespace

    calls = []
    fake_edge_tts = SimpleNamespace(Communicate=object)
    monkeypatch.setattr(bot_main, "edge_tts", None)
    monkeypatch.setattr(bot_main, "_TTS_IMPORT_ATTEMPTED", False)

    def fake_import(name):
        calls.append(name)
        assert name == "edge_tts"
        return fake_edge_tts

    monkeypatch.setattr(bot_main.importlib, "import_module", fake_import)

    assert bot_main.load_tts_dependency() is True
    assert bot_main.edge_tts is fake_edge_tts
    assert bot_main.load_tts_dependency() is True
    assert calls == ["edge_tts"]


def test_optional_audio_import_failure_is_contained(monkeypatch):
    monkeypatch.setattr(bot_main, "livekit_rtc", None)
    monkeypatch.setattr(bot_main, "_LIVEKIT_IMPORT_ATTEMPTED", False)
    monkeypatch.setattr(bot_main, "DJ_IMPORT_ERROR", "")

    def fail_import(name):
        raise ImportError("optional audio module unavailable")

    monkeypatch.setattr(bot_main.importlib, "import_module", fail_import)

    assert bot_main.load_livekit_dependency() is False
    assert bot_main.livekit_rtc is None
    assert "optional audio module unavailable" in bot_main.DJ_IMPORT_ERROR



def test_concurrent_audio_dependency_loading_is_single_initialization(monkeypatch):
    import concurrent.futures
    import threading
    import time as time_module
    from types import SimpleNamespace

    calls = []
    calls_lock = threading.Lock()
    fake_rtc = SimpleNamespace(Room=object)
    fake_av = SimpleNamespace(open=object)
    fake_numpy = SimpleNamespace(asarray=object)

    monkeypatch.setattr(bot_main, "livekit_rtc", None)
    monkeypatch.setattr(bot_main, "pyav", None)
    monkeypatch.setattr(bot_main, "np", None)
    monkeypatch.setattr(bot_main, "_LIVEKIT_IMPORT_ATTEMPTED", False)
    monkeypatch.setattr(bot_main, "_AUDIO_IMPORT_ATTEMPTED", False)

    def fake_import(name):
        with calls_lock:
            calls.append(name)
        time_module.sleep(0.01)
        return {
            "livekit.rtc": fake_rtc,
            "av": fake_av,
            "numpy": fake_numpy,
        }[name]

    monkeypatch.setattr(bot_main.importlib, "import_module", fake_import)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: bot_main.load_audio_dependencies(), range(4)))

    assert results == [True, True, True, True]
    assert sorted(calls) == ["av", "livekit.rtc", "numpy"]
