"""Tests for Katherine Nexus natural-language intent routing."""
import pytest

from katherine_nexus import interpret_natural_request


@pytest.mark.parametrize(
    ("request", "expected"),
    [
        ("Katherine, abre el menú interactivo", "menu"),
        ("¿Cómo está Katherine?", "estado"),
        ("haz un autotest", "autotest"),
        ("pausa la música", "dj pausa"),
        ("reanuda la reproducción", "dj reanudar"),
        ("salta la canción", "dj saltar"),
        ("muestra la cola musical", "dj cola"),
        ("entra a la llamada", "join"),
        ("lee los mensajes en voz alta", "leer"),
        ("deja de leer", "noleer"),
        ("¿Qué hora es en America/Santiago?", "zona america/santiago"),
        ("dame una guía de Cyno", "guia cyno"),
        ("build de Nahida", "guia nahida"),
        ("equipos para Dehya", "equipos dehya"),
        ("rotación de Cyno", "rotacion cyno"),
        ("qué materiales necesita Furina", "materiales furina"),
        ("busca en Genshin Hermanubis", "buscargenshin hermanubis"),
        ("reproduce piano relajante", "dj buscar piano relajante"),
        ("busca imágenes de lobos", "multimedia imagen lobos"),
        ("busca videos de Genshin", "multimedia video genshin"),
        ("busca audios de lluvia", "multimedia audio lluvia"),
    ],
)
def test_high_confidence_requests_route_to_existing_commands(request, expected):
    assert interpret_natural_request(request) == expected


@pytest.mark.parametrize(
    "request",
    [
        "",
        "hola",
        "buenos días",
        "¿qué opinas de Cyno?",
        "cuéntame algo interesante",
        "!guia Cyno",
        "-estado",
        "reproduce",
        "busca imágenes de",
        "traduce al inglés:",
        "x" * 501,
    ],
)
def test_ambiguous_or_ordinary_chat_is_not_routed(request):
    assert interpret_natural_request(request) is None


def test_translation_routes_to_existing_translation_command():
    assert interpret_natural_request("traduce al inglés: buenos días") == (
        "traducir ingles | buenos días"
    )
