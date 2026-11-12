"""Integration-focused unit tests for Katherine Nexus phases."""
from __future__ import annotations

import asyncio

import main as katherine


def test_progress_message_edits_with_existing_documented_patch(monkeypatch):
    calls = []

    async def fake_api(method, path, source, body):
        calls.append((method, path, source, body))
        return {"ok": True, "status": 200, "data": {}}

    monkeypatch.setattr(katherine, "api", fake_api)
    context = {
        "thread_id": "thread-1",
        "source": "developer",
        "message_id": "message-1",
        "created_at": katherine.time.time(),
        "enabled": True,
    }

    assert asyncio.run(katherine.update_progress_message(context, "Fase 2"))
    assert calls == [
        (
            "PATCH",
            "/threads/thread-1/messages/message-1",
            "developer",
            {"content": "Fase 2"},
        )
    ]


def test_progress_message_disables_editing_after_api_failure(monkeypatch):
    calls = []

    async def fake_api(method, path, source, body):
        calls.append(method)
        return {"ok": False, "status": 404, "data": {"error": "not editable"}}

    monkeypatch.setattr(katherine, "api", fake_api)
    context = {
        "thread_id": "thread-1",
        "source": "developer",
        "message_id": "missing-message",
        "created_at": katherine.time.time(),
        "enabled": True,
    }

    assert not asyncio.run(katherine.update_progress_message(context, "Final"))
    assert context["enabled"] is False
    assert calls == ["PATCH"]


def test_progress_falls_back_to_separate_final_message_when_no_id(monkeypatch):
    sent = []

    async def fake_send(thread_id, content, source="developer", reply_id=None, buttons=None):
        sent.append((thread_id, content, source, reply_id))
        return {"ok": True, "status": 200, "data": {"threadId": thread_id}}

    monkeypatch.setattr(katherine, "send_message", fake_send)
    context = asyncio.run(
        katherine.start_progress_message("thread-1", "Consultando…", "developer", "reply-1")
    )
    assert context["enabled"] is False
    assert asyncio.run(
        katherine.finish_progress_message(
            context, "Resultado final", "thread-1", "developer", "reply-1"
        )
    )["ok"]
    assert [item[1] for item in sent] == ["Consultando…", "Resultado final"]


def test_guide_sources_run_in_parallel_and_return_partial_data(monkeypatch):
    active = 0
    maximum_active = 0
    progress = []

    async def get_character(query):
        return {"name": "NexusRegressionCharacter"}

    async def character_stats(query):
        return {}

    async def helper_guide(*args):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            await asyncio.sleep(0.02)
            return {
                "artifacts": "Set de prueba",
                "weapons": "Arma de prueba",
                "talents": "1-2-3",
            }
        finally:
            active -= 1

    async def kqm_teams(*args):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            await asyncio.sleep(0.02)
            return {"teams": ["Equipo de prueba"]}
        finally:
            active -= 1

    monkeypatch.setattr(katherine, "get_character", get_character)
    monkeypatch.setattr(katherine, "character_stats", character_stats)
    monkeypatch.setattr(katherine, "fetch_helper_character_guide", helper_guide)
    monkeypatch.setattr(katherine, "fetch_kqm_teams", kqm_teams)

    async def callback(text):
        progress.append(text)

    result = asyncio.run(
        katherine.collect_character_guide_data(
            "NexusRegressionCharacter", progress_callback=callback
        )
    )

    assert maximum_active >= 2
    assert result["helper_data"]["artifacts"] == "Set de prueba"
    assert result["kqm_data"]["teams"] == ["Equipo de prueba"]
    assert progress
    assert any("RESPALDO" in item or "FICHA" in item for item in progress)

def test_unaddressed_chat_is_tracked_but_does_not_activate_katherine(monkeypatch):
    calls = {"commands": [], "users": [], "activity": []}

    monkeypatch.setattr(katherine, "check_antispam_message", lambda event, source: (False, None))

    async def no_speech(event, source):
        return None

    async def capture_command(event, source):
        calls["commands"].append(event["message"]["content"])

    monkeypatch.setattr(katherine, "enqueue_message_for_speech", no_speech)
    monkeypatch.setattr(katherine, "handle_command", capture_command)
    monkeypatch.setattr(katherine, "record_user", lambda uid, nickname, count_message=False: calls["users"].append((uid, nickname, count_message)))
    monkeypatch.setattr(katherine, "record_chat_activity", lambda thread, uid, nickname: calls["activity"].append((thread, uid, nickname)))

    event = {
        "type": "message",
        "threadId": "room-natural",
        "message": {
            "content": "hola a todos",
            "author": {"uid": "user-1", "nickname": "Viajero"},
        },
    }
    asyncio.run(katherine.dispatch(event, "developer"))

    assert calls["commands"] == []
    assert calls["users"] == [("developer:room-natural:user-1", "Viajero", True)]
    assert calls["activity"] == [("room-natural", "developer:room-natural:user-1", "Viajero")]


def test_addressed_natural_request_routes_to_existing_command(monkeypatch):
    calls = []

    monkeypatch.setattr(katherine, "check_antispam_message", lambda event, source: (False, None))

    async def no_speech(event, source):
        return None

    async def capture_command(event, source):
        calls.append(event["message"]["content"])

    monkeypatch.setattr(katherine, "enqueue_message_for_speech", no_speech)
    monkeypatch.setattr(katherine, "handle_command", capture_command)
    monkeypatch.setattr(katherine, "record_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(katherine, "record_chat_activity", lambda *args, **kwargs: None)

    event = {
        "type": "message",
        "threadId": "room-natural",
        "message": {
            "content": "Katherine, pausa la música",
            "author": {"uid": "user-1", "nickname": "Viajero"},
        },
    }
    asyncio.run(katherine.dispatch(event, "developer"))
    assert calls == ["!dj pausa"]

