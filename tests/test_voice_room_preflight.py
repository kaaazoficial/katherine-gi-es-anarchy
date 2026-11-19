from __future__ import annotations

import pytest

import main as bot_main


def test_find_live_room_for_thread_matches_exact_thread_id():
    rooms = [
        {"threadId": "885", "title": "Otra sala"},
        {"threadId": "1393", "title": "Comunidad"},
    ]

    assert bot_main.find_live_room_for_thread(rooms, "1393") == rooms[1]
    assert bot_main.find_live_room_for_thread(rooms, "13") is None


@pytest.mark.parametrize("room_list", [None, {}, "invalid"])
def test_find_live_room_for_thread_rejects_invalid_payload(room_list):
    assert bot_main.find_live_room_for_thread(room_list, "1393") is None


@pytest.mark.asyncio
async def test_ensure_voice_room_explains_when_chat_has_no_active_call(monkeypatch):
    calls = []

    async def fake_api(method, path, source="community", body=None):
        calls.append((method, path, source))
        return {"ok": True, "status": 200, "data": {"roomList": [
            {"threadId": "885", "title": "Otra sala"},
        ]}}

    monkeypatch.setattr(bot_main, "ACTIVE_BOT_TOKEN", "test-community-token")
    monkeypatch.setattr(bot_main, "api", fake_api)
    monkeypatch.setattr(
        bot_main,
        "load_livekit_dependency",
        lambda: (_ for _ in ()).throw(AssertionError("LiveKit no debe cargarse sin llamada activa.")),
    )

    ok, message = await bot_main.ensure_voice_room("1393")

    assert not ok
    assert "1393" in message
    assert "llamada en vivo activa" in message
    assert calls == [("GET", "/live-rooms", "community")]


@pytest.mark.asyncio
async def test_ensure_voice_room_reports_community_bot_access_error(monkeypatch):
    async def fake_api(method, path, source="community", body=None):
        return {
            "ok": False,
            "status": 403,
            "data": {"anarchy:error": "bot_not_in_community"},
        }

    monkeypatch.setattr(bot_main, "ACTIVE_BOT_TOKEN", "test-community-token")
    monkeypatch.setattr(bot_main, "api", fake_api)

    ok, message = await bot_main.ensure_voice_room("1393")

    assert not ok
    assert "HTTP 403" in message
    assert "bot comunitario" in message
