"""Tests for the bounded Katherine AI integration."""
import json
import time
import pytest
from katherine_ai import ACTION_SCHEMA, KatherineAI, validate_ai_tool_call

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_success = status_code < 400
    def json(self):
        return self._payload

def response_payload(result):
    return {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(result)}
    ]}]}

def test_default_model_is_current_mini(monkeypatch):
    monkeypatch.delenv("KATHERINE_AI_MODEL", raising=False)
    assert KatherineAI().model == "gpt-5.4-mini"

def test_allowlist_accepts_safe_existing_commands():
    assert validate_ai_tool_call("enciclopedia", "Cyno") == "enciclopedia Cyno"
    assert validate_ai_tool_call("dj", "buscar ambient piano") == "dj buscar ambient piano"
    assert validate_ai_tool_call("multimedia", "video Genshin") == "multimedia video Genshin"
    assert validate_ai_tool_call("traducir", "inglés | buenos días") == "traducir inglés | buenos días"

@pytest.mark.parametrize(("command", "args"), [
    ("admin", "resumen"), ("guardian", ""), ("evento", "crear actividad"), ("advertir", "@usuario"),
    ("pagar", "@usuario 100"), ("avatarbot", "https://example.com"),
    ("dj", "subirvolumen 100"), ("multimedia", "ejecutar programa"),
    ("actividad", "mes"), ("zona", "x"), ("enciclopedia", ""),
    ("traducir", "solo texto"),
])
def test_allowlist_rejects_sensitive_or_malformed_actions(command, args):
    assert validate_ai_tool_call(command, args) is None

def test_memory_is_isolated_and_expires():
    ai = KatherineAI(memory_ttl=60)
    ai._append_history("room:user-a", "user", "¿Quién es Cyno?")
    assert len(ai._history("room:user-a")) == 1
    assert ai._history("room:user-b") == []
    ai._memory["room:user-a"]["expires"] = time.time() - 1
    assert ai._history("room:user-a") == []

def test_memory_turn_limit():
    ai = KatherineAI(memory_turns=2)
    for index in range(8):
        ai._append_history("session", "user", f"m{index}")
    history = ai._history("session")
    assert len(history) == 4
    assert history[-1]["content"] == "m7"

def test_chat_analyst_is_disabled_by_default_and_records_only_when_enabled():
    ai = KatherineAI()
    assert not ai.record_chat_message("room", "Ana", "hola")
    ai.set_analyst("room", True)
    assert ai.record_chat_message("room", "Ana", "hola")
    assert ai.record_chat_message("room", "Luis", "hablemos del evento")
    assert len(ai.analyst_messages("room")) == 2
    ai.set_analyst("room", False)
    assert ai.analyst_messages("room") == []

def test_chat_analyst_ignores_bot_and_commands_and_can_clear():
    ai = KatherineAI()
    ai.set_analyst("room", True)
    assert not ai.record_chat_message("room", "Bot", "hola", is_bot=True)
    assert not ai.record_chat_message("room", "Ana", "!estado")
    assert ai.record_chat_message("room", "Ana", "acordamos reunirnos")
    assert ai.clear_analyst_messages("room") == 1
    assert ai.analyst_messages("room") == []

def test_extract_responses_api_output_text():
    ai = KatherineAI()
    payload = {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": "respuesta"}
    ]}]}
    assert ai._extract_output_text(payload) == "respuesta"
    assert ai._extract_output_text({"output_text": "directa"}) == "directa"

@pytest.mark.asyncio
async def test_api_request_uses_store_false_and_structured_output(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    monkeypatch.setenv("KATHERINE_AI_MODEL", "test-model")
    ai = KatherineAI()
    captured = {}
    async def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "Hola.", "command": "", "args": ""
        }))
    result = await ai.assist("saluda", "room:user", fake_request)
    assert result["answer"] == "Hola."
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/responses")
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["store"] is False
    assert captured["json"]["model"] == "test-model"
    assert captured["json"]["text"]["format"]["schema"] == ACTION_SCHEMA

@pytest.mark.asyncio
async def test_task_requests_apply_per_user_cooldown(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    async def fake_request(method, url, **kwargs):
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": ""
        }))
    assert await ai.translate("inglés", "hola", fake_request, rate_key="room:user") == "OK"
    with pytest.raises(RuntimeError, match="IA_ENFRIAMIENTO"):
        await ai.translate("inglés", "adiós", fake_request, rate_key="room:user")

@pytest.mark.asyncio
async def test_provider_connection_probe(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    captured = {}
    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": ""
        }))
    assert await ai.test_connection(fake_request) == "OK"
    assert captured["json"]["store"] is False
    assert captured["json"]["max_output_tokens"] == 250

@pytest.mark.asyncio
async def test_model_cannot_request_unapproved_tool(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    async def fake_request(method, url, **kwargs):
        return FakeResponse(response_payload({
            "kind": "command", "answer": "", "command": "admin",
            "args": "eliminar mensajes",
        }))
    result = await ai.assist("haz algo administrativo", "room:user", fake_request)
    assert result["kind"] == "answer"
    assert "no está disponible" in result["answer"]

@pytest.mark.asyncio
async def test_no_credentials_keeps_safe_fallback(monkeypatch):
    monkeypatch.delenv("KATHERINE_AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ai = KatherineAI()
    result = await ai.assist("explica Cyno", "room:user", lambda *a, **k: None)
    assert result["kind"] == "answer"
    assert "todavía no está conectada" in result["answer"]
