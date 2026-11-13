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
