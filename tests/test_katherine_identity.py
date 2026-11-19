import pytest

from katherine_identity import detect_affectionate_expression, extract_katherine_call


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
        ("Hola, Katherine, ¿cómo estás?", "Hola ¿cómo estás?"),
        ("¿Qué opinas, Katherine, de Cyno?", "¿Qué opinas de Cyno?"),
        ("Puedes ayudarme Katherine?", "Puedes ayudarme?"),
        ("Te quiero mucho, Katherine", "Te quiero mucho"),
        ("¿Me ayudas con mi build? Kathy", "¿Me ayudas con mi build?"),
        ("Revisa mi equipo, Katherin", "Revisa mi equipo"),
        ("Katherine, dime qué opinas de esta canción", "dime qué opinas de esta canción"),
    ],
)
def test_recognizes_katherine_name_at_start_middle_or_end(message, expected):
    assert extract_katherine_call(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "Me gusta Katherine como nombre.",
        "Hoy hablé con Kathy en el chat.",
        "Mi amiga Katherine llegó ayer.",
        "KatherineBot, inicia.",
        "Katrina, ¿puedes venir?",
        "Hola a todos.",
        "",
    ],
)
def test_does_not_trigger_for_casual_mentions_or_unrelated_names(message):
    assert extract_katherine_call(message) is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("te amo", "love"),
        ("Te amo mucho, Katherine", "love"),
        ("Katherine, te quiero", "care"),
        ("te quiero un montón", "care"),
        ("No te quiero", None),
        ("Ya no te amo", None),
        ("Nunca te quise", None),
        ("me gusta esa frase: te amo", "love"),
    ],
)
def test_detects_affectionate_expressions_without_explicit_negation(message, expected):
    assert detect_affectionate_expression(message) == expected



@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Katherine, te adoro", "love"),
        ("Katherine, te aprecio", "care"),
        ("Katherine, mil gracias por escucharme", "gratitude"),
        ("Katherine, eres muy linda", "compliment"),
        ("Eres genial, Katherine", "compliment"),
        ("Me caes muy bien, Katherine", "care"),
        ("Katherine, holi", "greeting"),
        ("Katherine, te extrañé", "miss_you"),
        ("Katherine, no eres linda", None),
        ("Katherine, no te adoro", None),
    ],
)
def test_warm_messages_cover_gratitude_compliments_and_greetings(message, expected):
    call = extract_katherine_call(message)
    assert call is not None
    assert detect_affectionate_expression(call) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Qué linda eres, Katherine", "compliment"),
        ("Eres la mejor, Katherine", "compliment"),
        ("Katherine, eres un sol", "compliment"),
        ("Katherine, me haces feliz", "compliment"),
    ],
)
def test_warm_replies_recognize_more_natural_compliments(message, expected):
    call = extract_katherine_call(message)
    assert call is not None
    assert detect_affectionate_expression(call) == expected


@pytest.mark.parametrize(("message", "expected"), [
    ("Katheryne, ¿puedes ayudarme?", "¿puedes ayudarme?"),
    ("@Katheryne revisa mi pregunta", "revisa mi pregunta"),
    ("Oye, Katheryne: explícame esto", "explícame esto"),
])
def test_recognizes_canonical_katheryne_vocative(message, expected):
    assert extract_katherine_call(message) == expected
