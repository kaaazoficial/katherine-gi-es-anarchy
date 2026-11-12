"""Tests for the bounded Katherine AI integration."""
import json
import time
import pytest
from katherine_ai import (
    ACTION_SCHEMA, KatherineAI, build_ai_session_key, is_bot_author,
    validate_ai_tool_call,
)

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

def test_bot_author_detection_requires_a_real_uid_match():
    assert is_bot_author({"uid": "bot-123"}, "bot-123")
    assert is_bot_author({"isBot": True}, "")
    assert not is_bot_author({"nickname": "Ana"}, "")
    assert not is_bot_author({"uid": "user-123"}, "bot-123")


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
    assert result["fallback"] is True

@pytest.mark.asyncio
async def test_no_credentials_keeps_safe_fallback(monkeypatch):
    monkeypatch.delenv("KATHERINE_AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ai = KatherineAI()
    result = await ai.assist("explica Cyno", "room:user", lambda *a, **k: None)
    assert result["kind"] == "answer"
    assert "todavía no está conectada" in result["answer"]


@pytest.mark.asyncio
async def test_groq_responses_omits_unsupported_store_and_uses_json_object(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-groq-key")
    monkeypatch.setenv("KATHERINE_AI_API_URL", "https://api.groq.com/openai/v1/responses")
    monkeypatch.delenv("KATHERINE_AI_MODEL", raising=False)
    ai = KatherineAI()
    captured = {}

    async def fake_request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": ""
        }))

    assert ai.provider_name == "Groq"
    assert ai.model == "openai/gpt-oss-20b"
    assert await ai.test_connection(fake_request) == "OK"
    assert "store" not in captured["json"]
    assert captured["json"]["text"]["format"]["type"] == "json_schema"
    assert captured["json"]["text"]["format"]["strict"] is True


@pytest.mark.asyncio
async def test_groq_model_without_strict_schema_uses_json_object_mode(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-groq-key")
    monkeypatch.setenv("KATHERINE_AI_API_URL", "https://api.groq.com/openai/v1/responses")
    monkeypatch.setenv("KATHERINE_AI_MODEL", "llama-3.3-70b-versatile")
    ai = KatherineAI()
    captured = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": ""
        }))

    assert await ai.test_connection(fake_request) == "OK"
    assert "store" not in captured["json"]
    assert captured["json"]["text"]["format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_provider_error_exposes_only_safe_error_fields(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "must-not-appear")
    ai = KatherineAI()

    async def fake_request(method, url, **kwargs):
        return FakeResponse({
            "error": {
                "code": "model_not_found",
                "type": "invalid_request_error",
                "message": "The model is not available.",
            }
        }, status_code=400)

    with pytest.raises(RuntimeError) as error:
        await ai.test_connection(fake_request)
    message = str(error.value)
    assert "HTTP 400" in message
    assert "model_not_found" in message
    assert "invalid_request_error" in message
    assert "The model is not available." in message
    assert "must-not-appear" not in message



def test_tool_results_are_retained_for_follow_up_context():
    ai = KatherineAI()
    ai._append_history("room:uid:user-1", "user", "¿Qué materiales necesita Cyno?")
    ai.remember_tool_result(
        "room:uid:user-1", "materiales Cyno",
        "Cyno necesita pergaminos, fruta del loto y materiales de jefe."
    )
    history = ai._history("room:uid:user-1")
    assert history[-1]["role"] == "assistant"
    assert "Resultado de herramienta" in history[-1]["content"]
    assert "materiales de jefe" in history[-1]["content"]


def test_ai_session_key_never_uses_nickname_as_persistent_identity():
    first = build_ai_session_key(
        "developer", "room", {"nickname": "Luna"}, "message-1"
    )
    second = build_ai_session_key(
        "developer", "room", {"nickname": "Luna"}, "message-2"
    )
    assert first != second
    stable_a = build_ai_session_key(
        "developer", "room", {"uid": "verified-user"}, "message-1"
    )
    stable_b = build_ai_session_key(
        "developer", "room", {"uid": "verified-user"}, "message-2"
    )
    assert stable_a == stable_b
    assert "Luna" not in first


@pytest.mark.asyncio
async def test_chat_summary_sends_every_message_without_silent_truncation(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI(analyst_limit=200)
    ai.set_analyst("room", True)
    for index in range(100):
        ai.record_chat_message(
            "room", f"User{index:03d}", f"MARKER{index:03d} " + ("x" * 680)
        )

    captured = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "Resumen de los 100 mensajes.",
            "command": "", "args": ""
        }))

    answer = await ai.summarize_chat("room", fake_request)
    inputs = captured["json"]["input"]
    all_input = "\n".join(item["content"] for item in inputs)
    assert answer == "Resumen de los 100 mensajes."
    assert len(inputs) > 1
    assert all(len(item["content"]) <= 4000 for item in inputs)
    assert all(f"MARKER{index:03d}" in all_input for index in range(100))


def test_groq_status_does_not_claim_store_disabled(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    monkeypatch.setenv(
        "KATHERINE_AI_API_URL", "https://api.groq.com/openai/v1/responses"
    )
    status = KatherineAI().status()
    assert "no se envía el parámetro store" in status
    assert "Almacenamiento de conversaciones: desactivado" not in status
