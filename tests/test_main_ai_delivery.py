"""Regression tests for AI tool output delivery capture."""
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
