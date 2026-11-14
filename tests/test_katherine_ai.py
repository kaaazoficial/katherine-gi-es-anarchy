"""Tests for the bounded Katherine AI integration."""
import asyncio
import json
import time
import pytest
from katherine_ai import (
    ACTION_SCHEMA, ANSWER_SCHEMA, KatherineAI, build_ai_session_key, build_ai_rate_key,
    compact_tool_result_for_context, output_token_budget_for_difficulty,
    is_bot_author, sanitize_ai_trace_text, validate_ai_tool_call,
)

class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_success = status_code < 400
        self.headers = {}
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
    ("admin", "resumen"), ("guardian", ""), ("advertir", "@usuario"),
    ("pagar", "@usuario"), ("avatarbot", "https://example.com"),
    ("dj", "subirvolumen 100"), ("multimedia", "ejecutar programa"),
    ("actividad", "mes"), ("zona", "x"), ("enciclopedia", ""),
    ("traducir", "solo texto"),
])
def test_allowlist_rejects_sensitive_or_malformed_actions(command, args):
    assert validate_ai_tool_call(command, args) is None


def test_metrics_command_is_admin_only_and_read_only():
    from katherine_ai import AI_ADMIN_COMMANDS, AI_PLAN_SAFE_COMMANDS
    assert validate_ai_tool_call("metricas", "") is None
    assert validate_ai_tool_call("metricas", "", AI_ADMIN_COMMANDS) == "metricas"
    assert "metricas" not in AI_PLAN_SAFE_COMMANDS


def test_conversational_questions_are_not_misclassified_as_actions():
    from katherine_ai import classify_request_intent
    assert classify_request_intent("Katherine, ¿qué opinas del nuevo diseño?") == "conversation"
    assert classify_request_intent("Katherine, explícame cómo funciona una API") == "conversation"
    assert classify_request_intent("Katherine, investiga las novedades de FastAPI") == "research"
    assert classify_request_intent("Katherine, pausa la música") == "action"


def test_explicit_user_actions_are_available_but_plans_stay_read_only():
    from katherine_ai import (
        AI_ADMIN_COMMANDS, AI_PLAN_SAFE_COMMANDS, AI_PUBLIC_COMMANDS,
        classify_request_intent, build_ai_instructions,
    )
    assert validate_ai_tool_call("evento", "crear reunión") == "evento crear reunión"
    assert validate_ai_tool_call("pagar", "@Ana 100") == "pagar @Ana 100"
    assert validate_ai_tool_call("bio", "Me gusta la música") == "bio Me gusta la música"
    assert validate_ai_tool_call("antispam", "off", AI_ADMIN_COMMANDS) == "antispam off"
    assert "pagar" not in AI_PLAN_SAFE_COMMANDS
    assert "evento" not in AI_PLAN_SAFE_COMMANDS
    assert classify_request_intent("Katherine, transfiere 100 puntos a Ana") == "action"
    assert classify_request_intent("Katherine, investiga novedades de Python") == "research"
    assert classify_request_intent(
        "Katherine, investiga cambios de Python y analiza este artículo"
    ) == "multi_action"
    assert classify_request_intent("Katherine, investiga Python y consulta la guía de Cyno") == "genshin"
    public_prompt = build_ai_instructions(AI_PUBLIC_COMMANDS, "action")
    admin_prompt = build_ai_instructions(AI_PUBLIC_COMMANDS | AI_ADMIN_COMMANDS, "action")
    assert "antispam:" not in public_prompt
    assert "antispam:" in admin_prompt


@pytest.mark.asyncio
async def test_assist_uses_role_scoped_command_allowlist(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    async def fake_request(method, url, **kwargs):
        assert "antispam:" not in kwargs["json"]["instructions"]
        return FakeResponse(response_payload({
            "kind": "command", "answer": "", "command": "antispam",
            "args": "off", "steps": [],
        }))
    result = await ai.assist(
        "desactiva el antispam", "room:user", fake_request,
        allowed_commands={"ayuda"},
    )
    assert result["kind"] == "answer"
    assert result["fallback"] is True

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

def test_ai_context_defaults_are_short_and_bounded():
    ai = KatherineAI()
    assert ai.memory_ttl == 300
    assert ai.memory_turns == 2
    assert ai.analyst_ttl == 900
    assert ai.analyst_limit == 60


def test_chat_analyst_activation_expires_automatically():
    ai = KatherineAI()
    ai.set_analyst("room", True)
    ai.record_chat_message("room", "Ana", "mensaje temporal")
    ai._analyst_enabled_at["room"] = time.time() - 901
    ai.cleanup()
    assert ai.analyst_messages("room") == []
    assert "room" not in ai._analyst_enabled


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
    assert captured["json"]["text"]["format"]["schema"] == ANSWER_SCHEMA
    assert captured["json"]["max_output_tokens"] == 256

@pytest.mark.asyncio
async def test_rapid_requests_are_queued_not_rejected(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    calls = []
    async def fake_request(method, url, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": ""
        }))
    assert await ai.translate("inglés", "hola", fake_request, rate_key="room:user") == "OK"
    assert await ai.translate("inglés", "adiós", fake_request, rate_key="room:user") == "OK"
    assert len(calls) == 2


def test_ai_responses_have_no_artificial_output_token_cap(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    captured = {}
    async def fake_request(method, url, **kwargs):
        captured.update(kwargs["json"])
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "A" * 12000, "command": "", "args": ""
        }))
    result = pytest.importorskip("asyncio").run(ai.test_connection(fake_request))
    assert len(result) == 12000
    assert "max_output_tokens" not in captured


def test_provider_retry_header_parser_handles_seconds_and_units():
    from katherine_ai import _retry_header_seconds
    assert _retry_header_seconds("2.5") == 2.5
    assert _retry_header_seconds("1m2.5s") == 62.5
    assert _retry_header_seconds("250ms") == 0.25

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
    assert "max_output_tokens" not in captured["json"]

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
async def test_groq_retries_json_schema_rejection_once_with_json_object(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-groq-key")
    monkeypatch.setenv("KATHERINE_AI_API_URL", "https://api.groq.com/openai/v1/responses")
    monkeypatch.setenv("KATHERINE_AI_MODEL", "openai/gpt-oss-20b")
    ai = KatherineAI()
    captured = []

    async def fake_request(method, url, **kwargs):
        captured.append(kwargs["json"])
        if len(captured) == 1:
            return FakeResponse({
                "error": {
                    "code": "invalid_request_error",
                    "message": "response_format json_schema is not supported",
                }
            }, status_code=400)
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": "", "steps": []
        }))

    assert await ai.test_connection(fake_request) == "OK"
    assert len(captured) == 2
    assert captured[0]["text"]["format"]["type"] == "json_schema"
    assert captured[1]["text"]["format"] == {"type": "json_object"}
    assert "store" not in captured[1]


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
    assert ai.metrics["ai_tool_context_chars_original"] >= ai.metrics["ai_tool_context_chars_stored"]


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
async def test_chat_summary_respects_short_retention_and_hard_message_cap(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI(analyst_limit=200)
    assert ai.analyst_limit == 60
    assert ai.analyst_ttl == 900
    ai.set_analyst("room", True)
    for index in range(100):
        ai.record_chat_message(
            "room", f"User{index:03d}", f"MARKER{index:03d} " + ("x" * 680)
        )

    retained = ai.analyst_messages("room")
    assert len(retained) == 60
    assert retained[0]["author"].startswith("Participante-")
    assert "User040" not in str(retained)
    assert retained[0]["content"].startswith("MARKER040")

    captured = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "Resumen de los mensajes recientes.",
            "command": "", "args": ""
        }))

    answer = await ai.summarize_chat("room", fake_request)
    inputs = captured["json"]["input"]
    all_input = "\n".join(item["content"] for item in inputs)
    assert answer == "Resumen de los mensajes recientes."
    assert len(inputs) > 1
    assert all(len(item["content"]) <= 4000 for item in inputs)
    assert all(f"MARKER{index:03d}" in all_input for index in range(40, 100))
    assert all(f"MARKER{index:03d}" not in all_input for index in range(40))


def test_groq_status_does_not_claim_store_disabled(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    monkeypatch.setenv(
        "KATHERINE_AI_API_URL", "https://api.groq.com/openai/v1/responses"
    )
    status = KatherineAI().status()
    assert "no se envía el parámetro store" in status
    assert "Almacenamiento de conversaciones: desactivado" not in status



def test_open_topic_request_is_recognized_as_missing_subject():
    from katherine_ai import clarification_for_unspecified_topic
    assert clarification_for_unspecified_topic(
        "Katherine Necesito ayuda para saber de un tema"
    ) == (
        "¡Claro! ¿Sobre qué tema necesitas información? Dime el nombre del tema "
        "o qué te gustaría entender y te ayudo."
    )
    assert clarification_for_unspecified_topic(
        "Oye Katherine, quiero saber sobre algún tema"
    )
    assert clarification_for_unspecified_topic(
        "Katherine: necesito ayuda con un tema"
    )
    assert clarification_for_unspecified_topic("Katherine, necesito ayuda")
    assert clarification_for_unspecified_topic("Oye Katherine, ¿puedes ayudarme?")
    assert clarification_for_unspecified_topic("Katherine tengo una pregunta")
    assert clarification_for_unspecified_topic(
        "Katherine, necesito ayuda para saber de electricidad"
    ) is None


def test_contextual_genshin_followups_require_a_recent_genshin_question():
    from katherine_ai import (
        is_contextual_followup_request, is_contextual_genshin_followup,
    )
    history = [{"role": "user", "content": "¿Quién es Nahida?"}]
    assert is_contextual_followup_request("Katherine, ¿y qué armas le sirven?")
    assert is_contextual_genshin_followup(
        "Katherine, ¿y qué armas le sirven?", history
    )
    assert not is_contextual_genshin_followup(
        "Katherine, explícame circuitos eléctricos", history
    )
    assert not is_contextual_genshin_followup(
        "Katherine, dime la fecha", history
    )
    assert is_contextual_genshin_followup(
        "Katherine, explícame sus constelaciones", history
    )
    assert not is_contextual_genshin_followup(
        "Katherine, cambiando de tema, ¿y qué armas compro?", history
    )


@pytest.mark.asyncio
async def test_canonical_faq_drops_previous_genshin_context(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    ai._append_history("room:user", "user", "¿Quién es Nahida?")
    ai._append_history("room:user", "assistant", "Nahida es una arconte.")
    result = await ai.assist(
        "Katherine, ¿qué es Anarchy Apps?",
        "room:user",
        lambda *args, **kwargs: None,
    )
    assert result["kind"] == "answer"
    assert "Anarchy apps es una red social" in result["answer"]
    assert "Nahida" not in str(ai._history("room:user"))


@pytest.mark.asyncio
async def test_open_topic_request_asks_for_subject_without_calling_provider(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    ai._append_history("room:user", "user", "¿Quién es Cyno?")
    ai._append_history("room:user", "assistant", "Cyno es un personaje de Genshin.")
    calls = []

    async def unexpected_request(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Una solicitud sin tema debe pedir aclaración localmente.")

    result = await ai.assist(
        "Katherine Necesito ayuda para saber de un tema",
        "room:user",
        unexpected_request,
    )
    assert result["kind"] == "answer"
    assert "¿Sobre qué tema" in result["answer"]
    assert calls == []
    assert "Cyno" not in str(ai._history("room:user"))
    assert ai._history("room:user")[-1]["content"] == result["answer"]


@pytest.mark.asyncio
async def test_new_general_topic_drops_stale_genshin_context(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    ai._append_history("room:user", "user", "¿Quién es Nahida?")
    ai._append_history("room:user", "assistant", "Nahida es una arconte.")
    captured = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "Los circuitos eléctricos tienen componentes.",
            "command": "", "args": "",
        }))

    result = await ai.assist(
        "Katherine, explícame circuitos eléctricos",
        "room:user",
        fake_request,
    )
    assert result["kind"] == "answer"
    sent = "\n".join(item["content"] for item in captured["json"]["input"])
    assert "Nahida" not in sent
    assert "Teyvat" not in sent


@pytest.mark.asyncio
async def test_genshin_followup_keeps_only_clear_recent_game_context(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    ai._append_history("room:user", "user", "¿Quién es Nahida?")
    ai._append_history("room:user", "assistant", "Nahida es un personaje de Genshin.")
    captured = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "Puede usar catalizador y armas de apoyo.",
            "command": "", "args": "",
        }))

    result = await ai.assist("Katherine, ¿y qué armas le sirven?", "room:user", fake_request)
    assert result["kind"] == "answer"
    assert "Nahida" in captured["json"]["input"][0]["content"]
    assert "INTENCIÓN: Genshin Impact" in captured["json"]["instructions"]


@pytest.mark.asyncio
async def test_persistent_domain_drift_uses_neutral_recovery_not_teyvat_rejection(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    responses = [
        FakeResponse(response_payload({
            "kind": "answer",
            "answer": "Ese asunto se relaciona con Genshin Impact y Teyvat.",
            "command": "", "args": "",
        })),
        FakeResponse(response_payload({
            "kind": "answer",
            "answer": "La consulta continúa sin tema suficiente en Genshin Impact y Teyvat.",
            "command": "", "args": "",
        })),
    ]

    async def fake_request(method, url, **kwargs):
        return responses.pop(0)

    result = await ai.assist(
        "Katherine, ¿puedes explicarme qué es un capacitor?",
        "room:user",
        fake_request,
    )
    assert result["kind"] == "answer"
    assert "Me desvié de tu pregunta" in result["answer"]
    assert "enciclopedia de Teyvat" not in result["answer"]


@pytest.mark.asyncio
async def test_unrelated_question_cannot_execute_genshin_tool(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    requests = []
    responses = [
        FakeResponse(response_payload({
            "kind": "command", "answer": "", "command": "enciclopedia",
            "args": "Anarchy Apps",
        })),
        FakeResponse(response_payload({
            "kind": "answer",
            "answer": "Anarchy Apps es la plataforma donde funciona esta comunidad.",
            "command": "", "args": "",
        })),
    ]

    async def fake_request(method, url, **kwargs):
        requests.append(kwargs)
        return responses.pop(0)

    result = await ai.assist("qué ofrece el ecosistema comunitario de esta aplicación", "room:user", fake_request)
    assert result["kind"] == "answer"
    assert "anarchy apps" in result["answer"].casefold()
    assert len(requests) == 0  # Canonical answer served locally; no API tokens consumed.


@pytest.mark.asyncio
async def test_unrelated_direct_answer_cannot_drift_to_genshin(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    responses = [
        FakeResponse(response_payload({
            "kind": "answer",
            "answer": "Anarchy Apps es una comunidad de Genshin en Teyvat.",
            "command": "", "args": "",
        })),
        FakeResponse(response_payload({
            "kind": "answer",
            "answer": "Anarchy Apps es la plataforma de la comunidad.",
            "command": "", "args": "",
        })),
    ]

    async def fake_request(method, url, **kwargs):
        return responses.pop(0)

    result = await ai.assist("explícame qué hace esta plataforma comunitaria", "room:user", fake_request)
    assert result["kind"] == "answer"
    assert "plataforma" in result["answer"]
    assert "Genshin" not in result["answer"]


@pytest.mark.asyncio
async def test_genshin_question_can_still_use_teyvat_tool(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()

    async def fake_request(method, url, **kwargs):
        return FakeResponse(response_payload({
            "kind": "command", "answer": "", "command": "enciclopedia",
            "args": "Nahida",
        }))

    result = await ai.assist("quién es Nahida", "room:user", fake_request)
    assert result["kind"] == "command"
    assert result["command"] == "enciclopedia"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("¿Qué es Anarchy Apps?", "Anarchy apps es una red social para crear comunidades, unirse a ellas y hablar con la gente"),
        ("Katherine, ¿qué es Anarchy Apps?", "Anarchy apps es una red social para crear comunidades, unirse a ellas y hablar con la gente"),
        ("¿Quién es tu creador?", "Mi creador es Sinner."),
        ("Katherine, ¿quién te creó?", "Mi creador es Sinner."),
        ("¿Quién eres?", "Soy Katherine, un bot de entretenimiento de la comunidad Genshin Impact ES dentro de Anarchy Apps. Tengo funciones de IA, música, juegos y más para entretener y acompañar a la comunidad. No soy un bot de ayuda ni de soporte de Anarchy Apps."),
        ("Katherine, preséntate", "Soy Katherine, un bot de entretenimiento de la comunidad Genshin Impact ES dentro de Anarchy Apps. Tengo funciones de IA, música, juegos y más para entretener y acompañar a la comunidad. No soy un bot de ayuda ni de soporte de Anarchy Apps."),
    ],
)
async def test_katherine_canonical_identity_facts(monkeypatch, question, expected):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()

    async def unexpected_request(*args, **kwargs):
        raise AssertionError("La respuesta canónica no debe depender del proveedor.")

    result = await ai.assist(question, "room:verified-user", unexpected_request)
    assert result["kind"] == "answer"
    assert result["answer"] == expected
    assert result["command"] == ""


@pytest.mark.asyncio
async def test_katherine_identity_faq_works_without_ai_credentials(monkeypatch):
    monkeypatch.delenv("KATHERINE_AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ai = KatherineAI()

    async def unexpected_request(*args, **kwargs):
        raise AssertionError("La FAQ debe funcionar sin credenciales.")

    result = await ai.assist("¿Quién eres?", "room:verified-user", unexpected_request)
    assert "bot de entretenimiento" in result["answer"]
    assert "no soy un bot de ayuda ni de soporte" in result["answer"].casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Katherine, preséntate", "bot de entretenimiento"),
        ("Katherine, ¿quién eres?", "bot de entretenimiento"),
        ("Katherine, ¿quién es tu creador?", "Mi creador es Sinner."),
        ("Katherine, ¿quién es su creador?", "Mi creador es Sinner."),
        ("¿Quién es el creador de Katherine?", "Mi creador es Sinner."),
    ],
)
async def test_katherine_identity_questions_with_name_prefix(monkeypatch, question, expected):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()

    async def unexpected_request(*args, **kwargs):
        raise AssertionError("La identidad canónica no debe depender del proveedor.")

    result = await ai.assist(question, "room:verified-user", unexpected_request)
    assert result["kind"] == "answer"
    if expected == "bot de entretenimiento":
        assert expected in result["answer"]
    else:
        assert result["answer"] == expected


def test_research_tools_are_allowed_and_validated():
    assert validate_ai_tool_call("investigar", "cambios recientes de Python") == (
        "investigar cambios recientes de Python"
    )
    assert validate_ai_tool_call("enlace", "https://example.com/articulo") == (
        "enlace https://example.com/articulo"
    )
    assert validate_ai_tool_call("enlace", "http://example.com") is None
    assert validate_ai_tool_call("enlace", "https://127.0.0.1/admin") is None
    assert validate_ai_tool_call("investigar", "https://example.com") is None


def test_research_sanitizer_extracts_page_text_and_rejects_unsafe_urls():
    from katherine_research import extract_page_content, is_safe_public_url, normalize_public_url
    title, text = extract_page_content(
        "<html><head><title>Artículo útil</title></head><body>"
        "<nav>Menú ignorado</nav><h1>Información</h1>"
        "<p>Contenido de prueba suficientemente extenso para evaluar una página.</p>"
        "<script>no ejecutar esto</script></body></html>"
    )
    assert title == "Artículo útil"
    assert "Información" in text
    assert "Menú ignorado" not in text
    assert "no ejecutar esto" not in text
    assert is_safe_public_url("https://example.com/noticia")
    assert not is_safe_public_url("http://example.com")
    assert not is_safe_public_url("https://localhost/admin")
    assert not is_safe_public_url("https://127.0.0.1/admin")
    assert not is_safe_public_url("https://example.com:8443/")
    assert normalize_public_url("https://EXAMPLE.com/noticia#section") == (
        "https://example.com/noticia"
    )


def test_research_search_parser_deduplicates_domains_and_unwraps_results():
    from urllib.parse import quote
    from katherine_research import parse_search_results
    one = quote("https://example.com/a", safe="")
    two = quote("https://example.com/b", safe="")
    three = quote("https://another.example.org/b", safe="")
    markup = (
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg=' + one + '">Primera fuente</a>'
        '<div class="result__snippet">Extracto uno con datos</div>'
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg=' + two + '">Duplicada</a>'
        '<div class="result__snippet">Extracto duplicado</div>'
        '<a class="result__a" href="https://duckduckgo.com/l/?uddg=' + three + '">Segunda fuente</a>'
    )
    results = parse_search_results(markup, limit=5)
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/a"
    assert results[1]["domain"] == "another.example.org"


@pytest.mark.asyncio
async def test_ai_plan_validates_all_steps_before_returning_plan(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()

    async def fake_request(method, url, **kwargs):
        return FakeResponse(response_payload({
            "kind": "plan",
            "answer": "",
            "command": "",
            "args": "",
            "steps": [
                {"command": "investigar", "args": "cambios recientes de Python"},
                {"command": "enlace", "args": "https://example.com/articulo"},
            ],
        }))

    result = await ai.assist(
        "investiga los cambios de Python y analiza este artículo",
        "room:user",
        fake_request,
    )
    assert result["kind"] == "plan"
    assert result["steps"] == [
        "investigar cambios recientes de Python",
        "enlace https://example.com/articulo",
    ]


@pytest.mark.asyncio
async def test_ai_plan_rejects_entire_sequence_if_one_step_is_forbidden(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()

    async def fake_request(method, url, **kwargs):
        return FakeResponse(response_payload({
            "kind": "plan",
            "answer": "",
            "command": "",
            "args": "",
            "steps": [
                {"command": "investigar", "args": "cambios recientes de Python"},
                {"command": "admin", "args": "eliminar mensajes"},
            ],
        }))

    result = await ai.assist(
        "investiga y luego elimina mensajes",
        "room:user",
        fake_request,
    )
    assert result["kind"] == "answer"
    assert result["fallback"] is True
    assert "No ejecutaré una secuencia parcial" in result["answer"]


def test_structured_ai_schema_uses_provider_compatible_steps_and_app_limits():
    assert "steps" in ACTION_SCHEMA["required"]
    assert "maxItems" not in ACTION_SCHEMA["properties"]["steps"]
    assert ACTION_SCHEMA["properties"]["steps"]["items"]["required"] == [
        "command", "args"
    ]


def test_ai_trace_redacts_secrets_and_personal_identifiers():
    value = (
        "gsk_1234567890abcdefghijklmnop user@example.com "
        "@SomeUser uid 12345678901 Bearer token-value"
    )
    cleaned = sanitize_ai_trace_text(value)
    assert "gsk_" not in cleaned
    assert "user@example.com" not in cleaned
    assert "@SomeUser" not in cleaned
    assert "12345678901" not in cleaned
    assert "token-value" not in cleaned


@pytest.mark.asyncio
async def test_provider_retries_one_transient_429(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    calls = []
    class RateLimited(FakeResponse):
        def __init__(self):
            super().__init__({"error": {"message": "rate limited"}}, status_code=429)
            self.headers = {"Retry-After": "0.5"}
    async def fake_request(method, url, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return RateLimited()
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": ""
        }))
    assert await ai.test_connection(fake_request) == "OK"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_provider_does_not_retry_non_429_errors(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    calls = []
    async def fake_request(method, url, **kwargs):
        calls.append(1)
        return FakeResponse({"error": {"message": "bad request"}}, status_code=400)
    with pytest.raises(RuntimeError, match="HTTP 400"):
        await ai.test_connection(fake_request)
    assert len(calls) == 1


def test_provider_adapts_request_and_token_pacing_from_headers(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    monkeypatch.setenv("KATHERINE_AI_API_URL", "https://api.groq.com/openai/v1/responses")
    ai = KatherineAI()

    class LimitedHeadersResponse(FakeResponse):
        def __init__(self):
            super().__init__({"usage": {"input_tokens": 100, "output_tokens": 50}})
            self.headers = {
                "x-ratelimit-limit-requests": "60",
                "x-ratelimit-limit-tokens": "6000",
                "x-ratelimit-remaining-requests": "50",
                "x-ratelimit-remaining-tokens": "5000",
            }

    ai._update_provider_limits(LimitedHeadersResponse())
    state = ai._provider_runtime_state["Groq"]
    assert state["min_interval"] == pytest.approx(1.05)
    assert state["token_interval"] == pytest.approx(1.575)


def test_provider_detects_daily_quota_and_uses_reset_window(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()

    class DailyQuotaResponse(FakeResponse):
        def __init__(self):
            super().__init__({
                "error": {"code": "rate_limit_exceeded", "message": "Daily token limit reached"}
            }, status_code=429)
            self.headers = {"x-ratelimit-reset-tokens": "1d"}

    delay = ai._daily_quota_reset_delay(DailyQuotaResponse())
    assert delay == pytest.approx(86400)


def test_provider_token_pacing_works_when_remaining_request_header_is_absent(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    monkeypatch.setenv("KATHERINE_AI_API_URL", "https://api.groq.com/openai/v1/responses")
    ai = KatherineAI()

    class PartialHeadersResponse(FakeResponse):
        def __init__(self):
            super().__init__({"usage": {"input_tokens": 80, "output_tokens": 20}})
            self.headers = {"x-ratelimit-limit-tokens": "6000"}

    ai._update_provider_limits(PartialHeadersResponse())
    assert ai._provider_runtime_state["Groq"]["token_interval"] == pytest.approx(1.05)


def chat_completion_payload(result):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": json.dumps(result)}
        }],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
    }


def gemini_generate_content_payload(result):
    return {
        "candidates": [{
            "content": {"role": "model", "parts": [{"text": json.dumps(result)}]}
        }],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 8},
    }


@pytest.mark.asyncio
async def test_gemini_is_primary_and_uses_native_generate_content(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.delenv("KATHERINE_AI_API_URL", raising=False)
    ai = KatherineAI()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(gemini_generate_content_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": "", "steps": []
        }))

    assert await ai.test_connection(fake_request) == "OK"
    assert calls[0][0] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert calls[0][1]["headers"]["x-goog-api-key"] == "gemini-test-key"
    assert "Authorization" not in calls[0][1]["headers"]
    assert "systemInstruction" in calls[0][1]["json"]
    assert calls[0][1]["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert calls[0][1]["json"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "minimal"
    assert "maxOutputTokens" not in calls[0][1]["json"]["generationConfig"]
    assert calls[0][1]["timeout"] == 8
    assert calls[0][1]["json"]["contents"][0]["role"] == "user"
    assert ai.last_provider_name == "Google Gemini"
    assert ai.last_model == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_gemini_failure_falls_back_to_groq(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.delenv("KATHERINE_AI_API_URL", raising=False)
    ai = KatherineAI()
    calls = []

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("katherine_ai.asyncio.sleep", no_sleep)

    async def fake_request(method, url, **kwargs):
        calls.append((url, kwargs))
        if "generativelanguage.googleapis.com" in url:
            return FakeResponse(
                {"error": {"message": "temporary upstream failure"}},
                status_code=503,
            )
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "Groq respondió.", "command": "", "args": ""
        }))

    result = await ai._request_json(
        "Devuelve una respuesta de prueba.",
        [{"role": "user", "content": "Prueba"}],
        fake_request,
    )
    assert result["answer"] == "Groq respondió."
    assert [item[0] for item in calls] == [
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent",
    ] * 2 + [
        "https://api.groq.com/openai/v1/responses",
    ]
    assert calls[0][1]["headers"]["x-goog-api-key"] == "gemini-test-key"
    assert calls[2][1]["headers"]["Authorization"] == "Bearer groq-test-key"
    assert calls[0][1]["json"]["systemInstruction"]["parts"][0]["text"]
    assert ai.last_provider_name == "Groq"
    assert ai.last_model == "openai/gpt-oss-20b"
    assert ai.metrics["ai_provider_failovers"] == 1
    assert ai.metrics["ai_provider_retries"] == 1


@pytest.mark.asyncio
async def test_gemini_read_timeout_fails_over_without_waiting_for_more_retries(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.delenv("KATHERINE_AI_API_URL", raising=False)
    ai = KatherineAI()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((url, kwargs))
        if "generativelanguage.googleapis.com" in url:
            raise TimeoutError("Gemini read timed out")
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "Groq respondió rápido.", "command": "", "args": ""
        }))

    result = await ai._request_json(
        "Responde brevemente.",
        [{"role": "user", "content": "Hola"}],
        fake_request,
    )
    assert result["answer"] == "Groq respondió rápido."
    assert len(calls) == 2
    assert "generativelanguage.googleapis.com" in calls[0][0]
    assert calls[0][1]["timeout"] == 8
    assert "api.groq.com" in calls[1][0]
    assert ai.metrics["ai_provider_retries"] == 0
    assert ai.metrics["ai_provider_failovers"] == 1


@pytest.mark.asyncio
async def test_direct_gemini_probe_does_not_fall_back_to_groq(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.delenv("KATHERINE_AI_API_URL", raising=False)
    async def no_sleep(_delay):
        return None
    monkeypatch.setattr("katherine_ai.asyncio.sleep", no_sleep)
    ai = KatherineAI()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append(url)
        return FakeResponse({"error": {"message": "still overloaded"}}, status_code=503)

    with pytest.raises(RuntimeError, match="HTTP 503"):
        await ai.test_provider_connection("gemini", fake_request)
    assert calls == [
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    ] * 2


@pytest.mark.asyncio
async def test_direct_groq_probe_targets_groq_only(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    ai = KatherineAI()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": ""
        }))

    assert await ai.test_provider_connection("groq", fake_request) == "OK"
    assert len(calls) == 1
    assert calls[0][0] == "https://api.groq.com/openai/v1/responses"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer groq-test-key"


def test_ai_status_exposes_primary_and_secondary_without_secrets(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-secret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-secret")
    ai = KatherineAI()
    status = ai.status()
    assert "Principal: Google Gemini" in status
    assert "• Groq:" in status and "configurado" in status
    assert "gemini-test-secret" not in status
    assert "groq-test-secret" not in status


def test_gemini_defaults_to_flash_lite_for_a_lightweight_bot(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert KatherineAI()._primary_provider_config()["model"] == "gemini-3.5-flash-lite"


def test_ai_tool_catalog_is_scoped_to_intent_and_topic():
    from katherine_ai import AI_PUBLIC_COMMANDS, select_ai_tool_commands
    assert not select_ai_tool_commands(
        "explícame cómo funciona una API", AI_PUBLIC_COMMANDS, "conversation"
    )
    music = select_ai_tool_commands(
        "reproduce música tranquila", AI_PUBLIC_COMMANDS, "action"
    )
    assert "dj" in music
    assert "enciclopedia" not in music
    research = select_ai_tool_commands(
        "investiga las novedades", AI_PUBLIC_COMMANDS, "research"
    )
    assert research == frozenset({"investigar", "enlace"})


def test_groq_provider_lock_does_not_share_gemini_lock():
    import asyncio
    ai = KatherineAI()
    gemini = ai._provider_locks.setdefault("google gemini", asyncio.Lock())
    groq = ai._provider_locks.setdefault("groq", asyncio.Lock())
    assert gemini is not groq


@pytest.mark.asyncio
async def test_groq_uses_low_reasoning_and_output_cap(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("KATHERINE_AI_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    ai = KatherineAI()
    captured = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": ""
        }))

    assert await ai.test_provider_connection("groq", fake_request) == "OK"
    assert captured["json"]["reasoning"] == {"effort": "low"}
    assert "max_output_tokens" not in captured["json"]


def test_ai_rate_key_uses_stable_ids_not_nicknames():
    first = build_ai_rate_key("developer", "room", {"uid": "user-1", "nickname": "Ana"})
    second = build_ai_rate_key("developer", "room", {"uid": "user-1", "nickname": "Another"})
    other = build_ai_rate_key("developer", "room", {"uid": "user-2", "nickname": "Ana"})
    anonymous_a = build_ai_rate_key("developer", "room", {"nickname": "Ana"})
    anonymous_b = build_ai_rate_key("developer", "room", {"nickname": "Luis"})
    assert first == second
    assert first != other
    assert anonymous_a == anonymous_b


def test_openrouter_defaults_to_free_responses_router(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    config = KatherineAI()._openrouter_provider_config()
    assert config["name"] == "OpenRouter"
    assert config["model"] == "openrouter/free"
    assert config["url"] == "https://openrouter.ai/api/v1/responses"


@pytest.mark.asyncio
async def test_direct_openrouter_probe_uses_openrouter_responses_api(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("KATHERINE_AI_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    ai = KatherineAI()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(response_payload({
            "kind": "answer", "answer": "OK", "command": "", "args": "", "steps": []
        }))

    assert await ai.test_provider_connection("openrouter", fake_request) == "OK"
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://openrouter.ai/api/v1/responses"
    assert kwargs["headers"]["Authorization"] == "Bearer openrouter-test-key"
    assert kwargs["headers"]["X-Title"] == "Katherine"
    assert kwargs["json"]["model"] == "openrouter/free"
    assert "max_output_tokens" not in kwargs["json"]
    assert kwargs["json"]["text"]["format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_provider_chain_falls_back_in_gemini_groq_openrouter_order(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    ai = KatherineAI()
    calls = []

    async def fake_provider_call(*args, **kwargs):
        calls.append(ai.provider_name)
        if ai.provider_name != "OpenRouter":
            raise RuntimeError("provider unavailable")
        return {"kind": "answer", "answer": "OK", "command": "", "args": "", "steps": []}

    ai._request_json_with_provider = fake_provider_call
    result = await ai._request_json(
        "JSON", [{"role": "user", "content": "hola"}],
        lambda *args, **kwargs: None,
    )
    assert result["answer"] == "OK"
    assert calls == ["Google Gemini", "Groq", "OpenRouter"]
    assert ai.metrics["ai_provider_failovers"] == 1


@pytest.mark.asyncio
async def test_user_ai_rate_limit_is_ephemeral_and_per_identity():
    ai = KatherineAI()
    key_a = build_ai_rate_key("developer", "room", {"uid": "user-a"})
    key_b = build_ai_rate_key("developer", "room", {"uid": "user-b"})
    for _ in range(ai.user_request_limit):
        ai._enforce_user_rate_limit(key_a)
    with pytest.raises(RuntimeError, match="IA_LIMITE_USUARIO"):
        ai._enforce_user_rate_limit(key_a)
    ai._enforce_user_rate_limit(key_b)
    assert ai.metrics["ai_user_rate_limited"] == 1
    assert all("user-a" not in item for item in ai._user_request_windows)


def test_ai_status_reports_three_providers_without_secrets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-secret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-secret")
    status = KatherineAI().status()
    assert "Google Gemini" in status
    assert "Groq" in status
    assert "OpenRouter" in status
    assert "openrouter/free" in status
    assert "openrouter-test-secret" not in status


@pytest.mark.asyncio
async def test_provider_circuit_skips_recently_failed_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    ai = KatherineAI()
    ai._provider_runtime_state["Google Gemini"] = {
        "circuit_open_until": time.monotonic() + 60,
        "cooldown_until": 0,
        "daily_quota_reset_at": 0,
    }
    calls = []

    async def fake_provider_call(*args, **kwargs):
        calls.append(ai.provider_name)
        return {"kind": "answer", "answer": "OK", "command": "", "args": "", "steps": []}

    ai._request_json_with_provider = fake_provider_call
    result = await ai._request_json(
        "JSON", [{"role": "user", "content": "hola"}],
        lambda *args, **kwargs: None,
    )
    assert result["answer"] == "OK"
    assert calls == ["Groq"]
    assert ai.metrics["ai_provider_circuit_skips"] == 1


def test_non_genshin_topic_does_not_expose_teyvat_tools():
    from katherine_ai import AI_PUBLIC_COMMANDS, classify_request_intent, select_ai_tool_commands
    text = "Explícame cómo funciona una API REST"
    intent = classify_request_intent(text)
    tools = select_ai_tool_commands(text, AI_PUBLIC_COMMANDS, intent)
    assert intent == "conversation"
    assert not tools
    assert classify_request_intent("Katherine, busca información sobre OpenRouter") == "research"

def test_anarchy_apps_semantic_faq_does_not_require_llm():
    from katherine_ai import resolve_community_faq
    answer = resolve_community_faq("¿Qué ofrece el ecosistema comunitario de esta aplicación?")
    assert answer == "Anarchy apps es una red social para crear comunidades, unirse a ellas y hablar con la gente"


def test_common_greeting_is_answered_locally():
    from katherine_ai import resolve_community_faq
    assert resolve_community_faq("Hola Katherine") == "¡Hola! ¿Qué hacemos hoy?"



@pytest.mark.asyncio
async def test_simple_standalone_answers_use_short_ram_cache(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    ai = KatherineAI()
    calls = []

    async def fake_request(instructions, messages, request_func, **kwargs):
        calls.append(messages)
        return {"kind": "answer", "answer": "Una API permite que dos sistemas se comuniquen.", "command": "", "args": ""}

    ai._request_json = fake_request
    first = await ai.assist("¿Qué es una API?", "session-user-room", lambda *args, **kwargs: None, rate_key="user-room-1")
    second = await ai.assist("¿Qué es una API?", "session-user-room", lambda *args, **kwargs: None, rate_key="user-room-1")
    assert first["answer"] == second["answer"]
    assert len(calls) == 1
    assert ai.metrics["ai_cache_hits"] == 1
    assert len(ai._simple_answer_cache) == 1


@pytest.mark.asyncio
async def test_simple_answer_cache_isolated_by_user_identity(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    ai = KatherineAI()
    calls = []

    async def fake_request(instructions, messages, request_func, **kwargs):
        calls.append(messages)
        return {"kind": "answer", "answer": "Respuesta general.", "command": "", "args": ""}

    ai._request_json = fake_request
    await ai.assist("¿Qué es una API?", "session-a", lambda *args, **kwargs: None, rate_key="user-a")
    await ai.assist("¿Qué es una API?", "session-b", lambda *args, **kwargs: None, rate_key="user-b")
    assert len(calls) == 1
    assert ai.metrics["ai_shared_cache_hits"] == 1
    assert ai.metrics["ai_cache_hits"] == 1


@pytest.mark.asyncio
async def test_personal_questions_are_never_cached(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    ai = KatherineAI()
    calls = []

    async def fake_request(instructions, messages, request_func, **kwargs):
        calls.append(messages)
        return {"kind": "answer", "answer": "Cuéntame un poco más.", "command": "", "args": ""}

    ai._request_json = fake_request
    await ai.assist("¿Qué opinas de mi situación?", "session", lambda *args, **kwargs: None, rate_key="user")
    await ai.assist("¿Qué opinas de mi situación?", "session", lambda *args, **kwargs: None, rate_key="user")
    assert len(calls) == 2
    assert ai.metrics["ai_cache_hits"] == 0


def test_followup_detector_only_keeps_context_for_referential_questions():
    assert KatherineAI.is_contextual_followup(
        "¿Y por qué?", [{"role": "assistant", "content": "Respuesta anterior"}]
    )
    assert not KatherineAI.is_contextual_followup(
        "¿Qué es una API?", [{"role": "assistant", "content": "Respuesta anterior"}]
    )


@pytest.mark.asyncio
async def test_simultaneous_simple_queries_share_one_provider_request(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    ai = KatherineAI()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def fake_request(instructions, messages, request_func, **kwargs):
        calls.append(1)
        entered.set()
        await release.wait()
        return {"kind": "answer", "answer": "Respuesta compartida.", "command": "", "args": ""}

    ai._request_json = fake_request
    first = asyncio.create_task(ai.assist("¿Qué es una API?", "session-one", lambda *args, **kwargs: None, rate_key="user-one"))
    await entered.wait()
    second = asyncio.create_task(ai.assist("¿Qué es una API?", "session-one", lambda *args, **kwargs: None, rate_key="user-one"))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)
    assert len(calls) == 1
    assert all(item["answer"] == "Respuesta compartida." for item in results)
    assert ai.metrics["ai_singleflight_waits"] == 1



def test_output_budgets_preserve_complex_answers_without_a_fixed_cap():
    assert output_token_budget_for_difficulty("simple") == 256
    assert output_token_budget_for_difficulty("standard") == 640
    assert output_token_budget_for_difficulty("complex") is None
    assert output_token_budget_for_difficulty("research") is None


def test_tool_context_compaction_keeps_start_and_end_and_caps_storage():
    source = "\n".join(
        [f"Sección {index}: " + ("dato relevante " * 40) for index in range(30)]
    )
    compacted = compact_tool_result_for_context(source, max_chars=900)
    assert len(compacted) <= 900
    assert "Sección 0" in compacted
    assert "Sección 29" in compacted
    assert "contenido intermedio omitido" in compacted


def test_conversation_prompt_uses_minimal_answer_contract():
    from katherine_ai import build_ai_instructions
    prompt = build_ai_instructions(set(), "conversation")
    assert "únicamente answer" in prompt
    assert "kind, answer, command, args, steps" not in prompt


@pytest.mark.asyncio
async def test_generic_cache_is_shared_but_personal_context_is_not(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    calls = []

    async def fake_request(method, url, **kwargs):
        calls.append(kwargs["json"])
        return FakeResponse(response_payload({
            "answer": "Una API permite que dos programas se comuniquen."
        }))

    first = await ai.assist("¿Qué es una API?", "room:user-a", fake_request, rate_key="user-a")
    second = await ai.assist("¿Qué es una API?", "room:user-b", fake_request, rate_key="user-b")
    assert first["answer"] == second["answer"]
    assert len(calls) == 1
    assert ai.metrics["ai_shared_cache_hits"] == 1

    personal = await ai.assist("¿Qué sabes de mi cuenta?", "room:user-c", fake_request, rate_key="user-c")
    assert personal["answer"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_action_requests_keep_full_action_schema_and_standard_budget(monkeypatch):
    monkeypatch.setenv("KATHERINE_AI_API_KEY", "test-key")
    ai = KatherineAI()
    captured = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs["json"])
        return FakeResponse(response_payload({
            "kind": "command", "answer": "", "command": "pausa",
            "args": "", "steps": []
        }))

    result = await ai.assist("por favor pausa la música después de terminar esta canción", "room:user", fake_request)
    assert result["kind"] == "command"
    assert captured["text"]["format"]["schema"] == ACTION_SCHEMA
    assert captured["max_output_tokens"] == 640


def test_ai_tool_selection_is_specific_to_the_requested_music_action():
    from katherine_ai import AI_PUBLIC_COMMANDS, select_ai_tool_commands

    selected = select_ai_tool_commands(
        "Katherine, pausa la música", AI_PUBLIC_COMMANDS, "action"
    )
    assert "pausa" in selected
    assert "pausar" in selected
    assert "dj" in selected
    assert "saltar" not in selected
    assert "reanudar" not in selected
    assert "playlist" not in selected
    assert "enciclopedia" not in selected


def test_ai_tool_selection_keeps_admin_commands_inside_verified_allowlist():
    from katherine_ai import AI_ADMIN_COMMANDS, AI_PUBLIC_COMMANDS, select_ai_tool_commands

    request = "Katherine, desactiva el antispam"
    public = select_ai_tool_commands(request, AI_PUBLIC_COMMANDS, "action")
    privileged = select_ai_tool_commands(
        request, AI_PUBLIC_COMMANDS | AI_ADMIN_COMMANDS, "action"
    )
    assert "antispam" not in public
    assert privileged == frozenset({"antispam"})


def test_ai_tool_selection_is_narrow_for_research_and_genshin():
    from katherine_ai import AI_PUBLIC_COMMANDS, select_ai_tool_commands

    research = select_ai_tool_commands(
        "Investiga las novedades de Python", AI_PUBLIC_COMMANDS, "research"
    )
    assert research == frozenset({"investigar", "enlace"})

    genshin = select_ai_tool_commands(
        "¿Qué build y equipos recomiendas para Cyno en Genshin?",
        AI_PUBLIC_COMMANDS,
        "genshin",
    )
    assert "guia" in genshin
    assert "equipos" in genshin
    assert "sinergia" in genshin
    assert "banner" not in genshin
    assert "materiales" not in genshin
    assert "pagar" not in genshin


def test_retained_ai_history_is_bounded_without_truncating_live_text():
    ai = KatherineAI()
    user_text = "pregunta " + ("x" * 3000)
    assistant_text = "respuesta " + ("y" * 3000)

    ai._append_history("session", "user", user_text)
    ai._append_history("session", "assistant", assistant_text)

    history = ai._history("session")
    assert len(history[0]["content"]) <= 2401
    assert len(history[1]["content"]) <= 1601
    assert history[0]["content"].startswith("pregunta")
    assert history[1]["content"].startswith("respuesta")
    assert user_text.startswith("pregunta") and len(user_text) > len(history[0]["content"])
    assert assistant_text.startswith("respuesta") and len(assistant_text) > len(history[1]["content"])


def test_admin_intent_hints_select_only_privileged_tools_when_authorized():
    from katherine_ai import AI_ADMIN_COMMANDS, AI_PUBLIC_COMMANDS, select_ai_tool_commands

    request = "Katherine, revisa la moderación de la sala"
    public = select_ai_tool_commands(request, AI_PUBLIC_COMMANDS, "action")
    admin = select_ai_tool_commands(
        request, AI_PUBLIC_COMMANDS | AI_ADMIN_COMMANDS, "action"
    )
    assert public == frozenset()
    assert admin == frozenset({"advertir", "advertencias", "quitaradvertencia"})


def test_ai_tool_selection_does_not_match_incidental_command_words():
    from katherine_ai import AI_PUBLIC_COMMANDS, select_ai_tool_commands

    selected = select_ai_tool_commands(
        "Katherine, crea una lista de libros para leer",
        AI_PUBLIC_COMMANDS,
        "action",
    )
    assert selected == frozenset()

