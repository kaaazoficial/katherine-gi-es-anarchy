"""Regression tests for AI tool output delivery capture."""
import pytest

import main as bot_main


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
