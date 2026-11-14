"""Deterministic, offline quality benchmark for Katherine's local AI router."""
import pytest

from katherine_ai import (
    KatherineAI,
    classify_request_difficulty,
    classify_request_intent,
    select_ai_tool_commands,
)


@pytest.mark.parametrize(
    ("request", "expected"),
    [
        ("¿Qué es una API?", "conversation"),
        ("¿Quién es Cyno en Genshin Impact?", "genshin"),
        ("Investiga fuentes sobre baterías de sodio", "research"),
        ("Pon música jazz", "action"),
        ("Reproduce jazz y después pausa la música", "multi_action"),
    ],
)
def test_intent_router_benchmark(request, expected):
    assert classify_request_intent(request) == expected


@pytest.mark.parametrize(
    ("request", "intent", "expected"),
    [
        ("¿Qué es una API?", "conversation", "simple"),
        ("Explícame cómo funciona una base de datos relacional", "conversation", "standard"),
        ("Compara los protocolos I2C y SPI; analiza sus diferencias paso a paso", "conversation", "complex"),
        ("Investiga fuentes sobre baterías de sodio", "research", "complex"),
    ],
)
def test_difficulty_router_benchmark(request, intent, expected):
    assert classify_request_difficulty(request, intent) == expected


def test_tool_selection_benchmark_does_not_expose_unrelated_commands():
    music = select_ai_tool_commands("Pon música jazz", intent="action")
    research = select_ai_tool_commands(
        "Investiga fuentes sobre baterías de sodio", intent="research"
    )
    assert "dj" in music
    assert "investigar" in research
    assert "perfil" not in music
    assert "dj" not in research


def test_context_selection_benchmark_keeps_only_clear_followups():
    history = [{"role": "assistant", "content": "La explicación anterior."}]
    assert KatherineAI.is_contextual_followup("¿Y por qué?", history)
    assert not KatherineAI.is_contextual_followup("¿Qué es una API?", history)
