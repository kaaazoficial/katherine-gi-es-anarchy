"""Tests for Katherine Nexus routing, health reporting and ephemeral events."""
import pytest

from katherine_ai import AI_COMMAND_CATALOG

from katherine_nexus import (
    cleanup_community_events, extract_message_id, format_source_health,
    interpret_natural_request, parse_command_invocation, toggle_community_participant,
    should_execute_deterministic_fallback,
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
        ("Katherine, crea una encuesta sobre música", "encuesta musica"),
        ("Katherine, recomiéndame música para estudiar", "dj ambiente estudiar"),
        ("Katherine, consulta la enciclopedia de Genshin sobre Cyno", "enciclopedia cyno"),
        ("Katherine, quién es Nahida", "enciclopedia nahida"),
        ("Katherine, qué es Genshin Impact", "enciclopedia genshin impact"),
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


@pytest.mark.parametrize(("utterance", "expected"), [
    ("Katherine ayuda", "ayuda"),
    ("Katherine, comandos", "comandos"),
    ("Katherine !guia Cyno", "guia cyno"),
    ("Katherine -estado", "estado"),
    ("Oye Katherine, admin resumen", "admin resumen"),
    ("Katherine, dj pausa", "dj pausa"),
    ("Katherine, investigacion inteligencia artificial", "investigar inteligencia artificial"),
    ("Katherine, miuid", "miuid"),
    ("Katherine, subirvolumen", "subirvolumen"),
    ("Katherine, playlist crear favoritos", "playlist crear favoritos"),
])
def test_every_catalog_command_can_be_invoked_with_katherine(utterance, expected):
    assert interpret_natural_request(utterance) == expected


def test_every_catalog_entry_is_reachable_by_its_katherine_name():
    for command in AI_COMMAND_CATALOG:
        routed = interpret_natural_request(f"Katherine {command}")
        expected = "admin" if command == "guardian" else command
        assert routed == expected, (command, routed)


def test_unknown_prefixed_command_after_katherine_is_not_executed():
    assert interpret_natural_request("Katherine !notacommand") is None


def test_catalog_routing_does_not_activate_for_bare_name_or_unaddressed_commands():
    assert interpret_natural_request("Katherine") is None
    assert interpret_natural_request("ayuda") is None
    assert interpret_natural_request("!ayuda") is None


def test_translation_routes_to_existing_translation_command():
    assert interpret_natural_request("Katherine, traduce al inglés: buenos días") == (
        "traducir ingles | buenos días"
    )


@pytest.mark.parametrize(("payload", "expected"), [
    ({"messageId": "abc"}, "abc"),
    ({"id": "top-level-id"}, "top-level-id"),
    ({"data": {"id": "data-id"}}, "data-id"),
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

def test_open_topic_request_is_forwarded_to_the_ai_assistant():
    routed = interpret_natural_request(
        "Katherine Necesito ayuda para saber de un tema"
    )
    assert routed == "asistente Necesito ayuda para saber de un tema"


def test_natural_language_requires_explicit_katherine_invocation():
    assert interpret_natural_request("hola, ¿cómo estás?") is None
    assert interpret_natural_request("pon música para estudiar") is None
    assert interpret_natural_request("Katherine") is None
    assert interpret_natural_request("Oye Katherine") is None
    assert interpret_natural_request("Katherine,") is None
    assert interpret_natural_request("Oye, Katherine.") is None
    assert interpret_natural_request("Oye Katherine, haz algo inesperado") == (
        "asistente haz algo inesperado"
    )
    assert interpret_natural_request("kAtHeRiNe: consulta la enciclopedia de Genshin sobre Cyno") == (
        "enciclopedia cyno"
    )

@pytest.mark.parametrize(("content", "prefix", "expected"), [
    ("!dj buscar Cyno", "!", ("!dj", ["buscar", "Cyno"])),
    ("!buscar Cyno", "!", ("!dj", ["buscar", "Cyno"])),
    ("!guardian actividad", "!", ("!admin", ["actividad"])),
    ("-estado", "-", ("!estado", [])),
    ("?ayuda", "?", ("!ayuda", [])),
    ("!dj", "!", ("!dj", [])),
    ("!", "!", None),
    ("hola", "!", None),
    ("!dj   buscar   Cyno", "!", ("!dj", ["buscar", "Cyno"])),
])
def test_command_parser_normalizes_prefixes_and_aliases(content, prefix, expected):
    assert parse_command_invocation(content, prefix) == expected


def test_bare_katherine_mentions_never_open_help_or_menu():
    for mention in ("Katherine", "katherine", "Oye Katherine", "Katherine,", "Oye, Katherine."):
        assert interpret_natural_request(mention) is None




@pytest.mark.parametrize("utterance", [
    "Katherine, qué es Anarchy Apps",
    "Katherine, quién es Anarchy Apps",
    "Katherine, qué sabes de la API de Anarchy Apps",
    "Katherine, dame una guía de Anarchy Apps",
    "Katherine, equipos para mi servidor",
    "Katherine, consulta la enciclopedia sobre Anarchy Apps",
])
def test_unrelated_topics_never_route_to_genshin_tools(utterance):
    result = interpret_natural_request(utterance)
    assert result is not None
    assert result.startswith("asistente ")
    assert not result.startswith((
        "enciclopedia ", "guia ", "equipos ", "rotacion ",
        "materiales ", "cobertura ", "buscargenshin "
    ))


def test_genshin_entity_names_still_route_to_teyvat():
    assert interpret_natural_request("Katherine, quién es Cyno") == "enciclopedia cyno"
    assert interpret_natural_request("Katherine, dame una guía de Nahida") == "guia nahida"

@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Katherine, qué es Natlan Genshin Impact", "geografia Natlan"),
        ("Katherine, qué es Natlan", "geografia Natlan"),
        ("Katherine, qué es la región de Natlan en Genshin Impact", "geografia Natlan"),
        ("Katherine, dónde está Fontaine en Genshin", "geografia Fontaine"),
        ("Katherine, háblame sobre la región de Natlan", "geografia Natlan"),
        ("Katherine, qué se sabe de la región Natlan", "geografia Natlan"),
        ("Katherine, qué es el elemento Dendro", "elemento Dendro"),
        ("Katherine, qué es Pyro en Genshin Impact", "elemento Pyro"),
        ("Katherine, quién es Mualani en Genshin Impact", "enciclopedia mualani"),
        ("Katherine, qué materiales necesita Mualani Genshin Impact", "materiales mualani"),
        ("Katherine, qué armas usa Mualani", "guia mualani"),
        ("Katherine, qué materiales necesita mi laptop", "asistente qué materiales necesita mi laptop"),
    ],
)
def test_genshin_questions_are_routed_by_entity_and_requested_information(
    utterance, expected
):
    assert interpret_natural_request(utterance) == expected


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Katherine, investiga novedades de Python 3.14", "investigar novedades de python 3.14"),
        ("Katherine, busca información sobre ciberseguridad", "investigar ciberseguridad"),
        ("Katherine, analiza este enlace https://example.com/articulo", "enlace https://example.com/articulo"),
        ("Katherine, resume https://example.com/noticia", "enlace https://example.com/noticia"),
    ],
)
def test_public_research_requests_route_to_research_tools(utterance, expected):
    assert interpret_natural_request(utterance) == expected


@pytest.mark.parametrize(("utterance", "fallback", "expected"), [
    ("Katherine, qué es el gremio de aventureros", "enciclopedia el gremio", False),
    ("Katherine, quién es Anarchy Apps", "enciclopedia anarchy apps", False),
    ("Katherine, abre el menú", "menu", True),
    ("Katherine, pausa la música", "dj pausa", True),
    ("Katherine, investiga Python", "investigar python", True),
    ("Katherine, qué es Anarchy Apps", "enciclopedia anarchy apps", False),
])
def test_deterministic_fallback_requires_explicit_intent(utterance, fallback, expected):
    assert should_execute_deterministic_fallback(utterance, fallback) is expected


def test_provider_failure_can_use_existing_deterministic_fallback():
    assert should_execute_deterministic_fallback(
        "Katherine, qué es Anarchy Apps", "enciclopedia anarchy apps",
        provider_failed=True,
    )
    assert not should_execute_deterministic_fallback(
        "Katherine, hola", "asistente hola", provider_failed=True,
    )



def test_natural_language_own_message_cleanup_routes_to_admin_command():
    assert interpret_natural_request("Katherine, elimina tus mensajes") == "eliminarmensajes"
    assert interpret_natural_request("Katherine elima tus mensajes") == "eliminarmensajes"
