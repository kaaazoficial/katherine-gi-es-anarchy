import math

import pytest

from katherine_utilities import calculate_expression, format_calculation_result
from katherine_link_analysis import inspect_link_signals


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3 * 4", 14),
        ("(25*4)+10", 110),
        ("15% de 240", 36),
        ("sqrt(144)", 12),
        ("pi * 2", math.pi * 2),
        ("sin(0)", 0),
        ("1,5 + 2", 3.5),
    ],
)
def test_calculator_supported_expressions(expression, expected):
    assert calculate_expression(expression) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "1 / 0",
        "2 ** 1000",
        "x + 1",
    ],
)
def test_calculator_rejects_unsafe_or_invalid_expressions(expression):
    with pytest.raises(ValueError):
        calculate_expression(expression)


def test_calculator_formats_results():
    assert format_calculation_result(12.0) == "12"
    assert format_calculation_result(1 / 3) == "0.333333333333"


def test_link_inspector_flags_known_adult_domain():
    result = inspect_link_signals("https://www.pornhub.com/")
    assert result["adult"] is True
    assert result["adult_label"] == "Posible contenido +18"


def test_link_inspector_does_not_mark_general_social_domain_as_adult():
    result = inspect_link_signals(
        "https://duckapps.com.br/",
        title="DuckApps - comunidade",
        page_text="Conecte-se com pessoas e participe de comunidades.",
    )
    assert result["adult"] is False
    assert result["risk"] == "bajo"


def test_link_inspector_flags_credential_bait_and_redirect():
    result = inspect_link_signals(
        "https://example.com/login",
        final_url="https://different.example/login",
        redirect_chain=["https://example.com/login", "https://different.example/login"],
        title="Verify your account",
        page_text="Enter your password to reset your account.",
    )
    assert result["risk"] in {"medio", "alto"}
    assert any("credenciales" in item.lower() for item in result["warnings"])


def test_link_inspector_flags_executable_download():
    result = inspect_link_signals("https://example.com/download/setup.exe")
    assert result["risk"] == "alto"
    assert result["malicious_suspected"] is True
