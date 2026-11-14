import pytest

from katherine_identity import extract_katherine_call


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Katherine, ¿puedes ayudarme?", "¿puedes ayudarme?"),
        ("Katherin, explícame esto", "explícame esto"),
        ("Kathrine: hola", "hola"),
        ("Katherina ayúdame", "ayúdame"),
        ("Oye, Kathy, ¿quién es Cyno?", "¿quién es Cyno?"),
        ("Hey Kat mira esto", "mira esto"),
        ("@Kathi revisa mi pregunta", "revisa mi pregunta"),
        ("¡Ey, Katheryne! ¿Estás ahí?", "¿Estás ahí?"),
        ("Katherine", ""),
    ],
)
def test_recognizes_katherine_name_typos_and_nicknames(message, expected):
    assert extract_katherine_call(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "Me gusta Katherine como nombre.",
        "Hoy hablé con Kathy en el chat.",
        "Katrina, ¿puedes venir?",
        "KatherineBot, inicia.",
        "Hola a todos.",
        "",
    ],
)
def test_does_not_trigger_for_ordinary_mentions_or_unrelated_names(message):
    assert extract_katherine_call(message) is None
