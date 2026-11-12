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
        ("Katherine, abre el centro de control", "centro"),
        ("Katherine, revisa el estado", "estado"),
        ("Katherine, diagnostica Katherine", "estado"),
        ("Katherine, qué fuentes", "fuentes"),
        ("Katherine, abre el panel de administración", "guardian"),
        ("Katherine, cuéntame un chiste", "chiste"),
        ("Katherine, dame una frase", "frase"),
        ("Katherine, lanza un dado", "dado"),
        ("Katherine, tira una moneda", "moneda"),
        ("Katherine, consulta mis puntos", "puntos"),
        ("Katherine, muestra el ranking", "ranking"),
        ("Katherine, muestra mi perfil", "perfil"),
        ("Katherine, haz un roast a @usuario", "roast @usuario"),
        ("Katherine, crea una encuesta sobre música", "encuesta música"),
        ("Katherine, recomiéndame música para estudiar", "dj ambiente estudiar"),
        ("Katherine, consulta la enciclopedia de Genshin sobre Cyno", "enciclopedia cyno"),
        ("Katherine, quién es Nahida", "enciclopedia nahida"),
        ("Katherine, haz un autotest", "autotest"),
        ("Katherine, pausa la música", "dj pausa"),
        ("Katherine, reanuda la reproducción", "dj reanudar"),
        ("Katherine, salta la canción", "dj saltar"),
        ("Katherine, muestra la cola musical", "dj cola"),
        ("Katherine, qué está sonando", "dj actual"),
        ("Katherine, entra a la llamada", "join"),
        ("Katherine, lee los mensajes en voz alta", "leer"),
        ("Katherine, deja de leer", "noleer"),
        ("Katherine, convoca un evento", "evento"),
        ("Katherine, muestra el evento", "evento"),
        ("Katherine, muestra la actividad del chat", "actividad"),
        ("Katherine, ¿Qué hora es en America/Santiago?", "zona america/santiago"),
        ("Katherine, dame una guía de Cyno", "guia cyno"),
        ("Katherine, build de Nahida", "guia nahida"),
        ("Katherine, equipos para Dehya", "equipos dehya"),
        ("Katherine, rotación de Cyno", "rotacion cyno"),
        ("Katherine, qué materiales necesita Furina", "materiales furina"),
        ("Katherine, qué cobertura tiene Cyno", "cobertura cyno"),
        ("Katherine, busca en Genshin Hermanubis", "buscargenshin hermanubis"),
        ("Katherine, reproduce piano relajante", "dj buscar piano relajante"),
        ("Katherine, busca imágenes de lobos", "multimedia imagen lobos"),
        ("Katherine, busca videos de Genshin", "multimedia video genshin"),
        ("Katherine, busca audios de lluvia", "multimedia audio lluvia"),
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
    assert interpret_natural_request("Katherine, traduce al inglés: buenos días") == (
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

def test_natural_language_requires_explicit_katherine_invocation():
    assert interpret_natural_request("hola, ¿cómo estás?") is None
    assert interpret_natural_request("pon música para estudiar") is None
    assert interpret_natural_request("Katherine") == "ayuda"
    assert interpret_natural_request("Oye Katherine, haz algo inesperado") == (
        "asistente haz algo inesperado"
    )
    assert interpret_natural_request("kAtHeRiNe: consulta la enciclopedia de Genshin sobre Cyno") == (
        "enciclopedia cyno"
    )

