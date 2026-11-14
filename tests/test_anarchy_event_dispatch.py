from __future__ import annotations

import pytest

import main as bot_main


@pytest.mark.asyncio
async def test_edited_message_with_disallowed_link_is_deleted(monkeypatch):
    calls = []

    async def fake_inspect(text, fetch_page):
        calls.append(("inspect", text))
        return {"blocked": True, "reason": "comunidad no permitida", "category": "other_anarchy_community"}

    async def fake_api(method, path, source="developer", body=None):
        calls.append(("api", method, path, source))
        return {"ok": True, "status": 204, "data": {}}

    async def fake_send(*args, **kwargs):
        calls.append(("send", args, kwargs))

    monkeypatch.setattr(bot_main, "inspect_message_links", fake_inspect)
    monkeypatch.setattr(bot_main, "api", fake_api)
    monkeypatch.setattr(bot_main, "send_message", fake_send)
    monkeypatch.setattr(bot_main, "BOT_IDENTITIES", {"developer": {"uid": "bot-uid"}})
    before = bot_main.metrics.get("edited_link_blocks", 0)

    event = {
        "type": "message.edited",
        "threadId": "room-123",
        "message": {
            "id": "message-456",
            "content": "mira https://app.anarchyapps.org/c/22",
            "author": {"uid": "user-789", "nickname": "Viajero"},
        },
    }
    await bot_main.dispatch(event, "developer")

    assert ("inspect", event["message"]["content"]) in calls
    assert ("api", "DELETE", "/threads/room-123/messages/message-456", "developer") in calls
    assert bot_main.metrics["edited_link_blocks"] == before + 1
    assert not any(item[0] == "send" for item in calls)


@pytest.mark.asyncio
async def test_edited_message_from_bot_is_ignored_by_link_guard(monkeypatch):
    async def fail_inspect(*args, **kwargs):
        raise AssertionError("No debe revisar mensajes propios.")

    monkeypatch.setattr(bot_main, "inspect_message_links", fail_inspect)
    monkeypatch.setattr(bot_main, "BOT_IDENTITIES", {"developer": {"uid": "bot-uid"}})

    await bot_main.dispatch({
        "type": "message.edited",
        "threadId": "room-123",
        "message": {
            "id": "message-456",
            "content": "https://app.anarchyapps.org/c/22",
            "author": {"uid": "bot-uid"},
        },
    }, "developer")
