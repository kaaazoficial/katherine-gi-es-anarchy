"""Tests for Katherine Nexus routing, health reporting and ephemeral events."""
import pytest

from katherine_nexus import (
    cleanup_community_events, extract_message_id, format_source_health,
    interpret_natural_request, toggle_community_participant,
)


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Katherine, abre el menú interactivo", "menu"),
        ("abre el centro de control", "centro"),
        ("¿Cómo está Katherine?", "estado"),
        ("diagnostica Katherine", "estado"),
        ("qué fuentes", "fuentes"),
        ("haz un autotest", "autotest"),
        ("pausa la música", "dj pausa"),
        ("reanuda la reproducción", "dj reanudar"),
        ("salta la canción", "dj saltar"),
        ("muestra la cola musical", "dj cola"),
        ("qué está sonando", "dj actual"),
        ("entra a la llamada", "join"),
        ("lee los mensajes en voz alta", "leer"),
        ("deja de leer", "noleer"),
        ("convoca un evento", "evento"),
        ("muestra el evento", "evento"),
        ("muestra la actividad del chat", "actividad"),
        ("¿Qué hora es en America/Santiago?", "zona america/santiago"),
        ("dame una guía de Cyno", "guia cyno"),
        ("build de Nahida", "guia nahida"),
        ("equipos para Dehya", "equipos dehya"),
        ("rotación de Cyno", "rotacion cyno"),
        ("qué materiales necesita Furina", "materiales furina"),
        ("qué cobertura tiene Cyno", "cobertura cyno"),
        ("busca en Genshin Hermanubis", "buscargenshin hermanubis"),
        ("reproduce piano relajante", "dj buscar piano relajante"),
        ("busca imágenes de lobos", "multimedia imagen lobos"),
        ("busca videos de Genshin", "multimedia video genshin"),
        ("busca audios de lluvia", "multimedia audio lluvia"),
    ],
)
def test_high_confidence_requests_route_to_existing_commands(utterance, expected):
    assert interpret_natural_request(utterance) == expected


@pytest.mark.parametrize("utterance", [
    "", "hola", "buenos días", "¿qué opinas de Cyno?",
    "cuéntame algo interesante", "!guia Cyno", "-estado", "reproduce",
    "busca imágenes de", "traduce al inglés:", "x" * 501,
])
def test_ambiguous_or_ordinary_chat_is_not_routed(utterance):
    assert interpret_natural_request(utterance) is None


def test_translation_routes_to_existing_translation_command():
    assert interpret_natural_request("traduce al inglés: buenos días") == (
        "traducir ingles | buenos días"
    )


@pytest.mark.parametrize(("payload", "expected"), [
    ({"messageId": "abc"}, "abc"),
    ({"message": {"id": "xyz"}}, "xyz"),
    ({"data": {"message": {"messageId": "nested"}}}, "nested"),
])
def test_extract_message_id_handles_api_envelopes(payload, expected):
    assert extract_message_id(payload) == expected


def test_extract_message_id_returns_none_for_unrelated_payload():
    assert extract_message_id({"ok": True, "status": 200, "data": {"success": True}}) is None


def test_community_event_participation_is_ephemeral_and_capacity_limited():
    event = {"expires_at": 1000, "capacity": 1, "participants": {}}
    assert toggle_community_participant(event, "u1", "Uno", True, now=10) == ("joined", 1)
    assert toggle_community_participant(event, "u1", "Uno", True, now=10) == ("already_joined", 1)
    assert toggle_community_participant(event, "u2", "Dos", True, now=10) == ("full", 1)
    assert toggle_community_participant(event, "u1", "Uno", False, now=10) == ("left", 0)
    assert toggle_community_participant(event, "u1", "Uno", False, now=10) == ("not_joined", 0)


def test_community_event_rejects_expired_and_closed_states():
    event = {"expires_at": 5, "participants": {}}
    assert toggle_community_participant(event, "u1", "Uno", True, now=5) == ("expired", 0)
    event = {"expires_at": 100, "closed": True, "participants": {}}
    assert toggle_community_participant(event, "u1", "Uno", True, now=1) == ("closed", 0)


def test_cleanup_community_events_removes_expired_and_closed_only():
    events = {
        "active": {"expires_at": 100, "participants": {}},
        "expired": {"expires_at": 1, "participants": {}},
        "closed": {"expires_at": 100, "closed": True, "participants": {}},
    }
    assert cleanup_community_events(events, now=10) == 2
    assert set(events) == {"active"}


def test_format_source_health_summarizes_success_and_failures():
    answer = format_source_health({"example.org": {
        "requests": 5, "successes": 4, "failures": 1,
        "last_status": 200, "last_latency_ms": 125.5,
    }})
    assert "example.org" in answer
    assert "4/5 correctas" in answer
    assert "126 ms" in answer
