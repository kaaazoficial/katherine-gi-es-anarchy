from __future__ import annotations

import pytest

import main as bot_main


@pytest.mark.asyncio
async def test_edited_message_with_disallowed_link_warns_without_unsupported_delete(monkeypatch):
    inspected = []
    sent = []
    api_calls = []

    async def fake_inspect(text, fetch_page):
        inspected.append(text)
        return {"blocked": True, "reason": "comunidad no permitida", "category": "other_anarchy_community"}

    async def fake_api(method, path, source="developer", body=None):
        api_calls.append((method, path, source))
        return {"ok": False, "status": 404, "data": {"anarchy:error": "message_not_found"}}

    async def fake_send(thread_id, content, source="developer", reply_id=None, buttons=None):
        sent.append((thread_id, content, source))

    monkeypatch.setattr(bot_main, "inspect_message_links", fake_inspect)
    monkeypatch.setattr(bot_main, "api", fake_api)
    monkeypatch.setattr(bot_main, "send_message", fake_send)
    monkeypatch.setattr(bot_main, "BOT_IDENTITIES", {"developer": {"uid": "bot-uid"}})
    before = bot_main.metrics.get("edited_link_warnings", 0)

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

    assert inspected == [event["message"]["content"]]
    assert api_calls == []
    assert bot_main.metrics["edited_link_warnings"] == before + 1
    assert len(sent) == 1
    assert "siguen visibles" in sent[0][1]
    assert "moderador" in sent[0][1]
    assert "no permite" in sent[0][1]


@pytest.mark.asyncio
async def test_incoming_disallowed_link_warns_without_attempting_to_delete_user_message(monkeypatch):
    sent = []
    api_calls = []

    async def fake_inspect(text, fetch_page):
        return {"blocked": True, "reason": "comunidad no permitida", "category": "other_anarchy_community"}

    async def fake_api(method, path, source="developer", body=None):
        api_calls.append((method, path, source))
        return {"ok": False, "status": 404, "data": {"anarchy:error": "message_not_found"}}

    async def fake_send(thread_id, content, source="developer", reply_id=None, buttons=None):
        sent.append((thread_id, content, source))

    monkeypatch.setattr(bot_main, "check_antispam_message", lambda event, source: (False, None))
    monkeypatch.setattr(bot_main, "inspect_message_links", fake_inspect)
    monkeypatch.setattr(bot_main, "api", fake_api)
    monkeypatch.setattr(bot_main, "send_message", fake_send)

    event = {
        "type": "message",
        "threadId": "room-124",
        "message": {
            "id": "message-457",
            "content": "mira https://app.anarchyapps.org/c/22",
            "author": {"uid": "user-789", "nickname": "Viajero"},
        },
    }
    await bot_main.dispatch(event, "developer")

    assert api_calls == []
    assert len(sent) == 1
    assert "siguen visibles" in sent[0][1]
    assert "moderador" in sent[0][1]


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
