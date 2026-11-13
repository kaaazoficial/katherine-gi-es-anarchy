"""Tests for opt-in, high-confidence command spelling suggestions."""
import pytest

from katherine_matching import normalize_command_name, suggest_command


def test_command_normalization_removes_prefix_and_symbols():
    assert normalize_command_name("!Comándos") == "comndos"
    assert normalize_command_name("!metricas") == "metricas"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("enciclopediaa", "enciclopedia"),
        ("metricas", "metricas"),
        ("comandso", "comandos"),
    ],
)
def test_high_confidence_command_suggestions(query, expected):
    result = suggest_command(query, {"ayuda", "comandos", "enciclopedia", "metricas"})
    assert result is not None
    assert result[0] == expected
    assert result[1] >= 88


def test_ambiguous_short_or_unavailable_commands_are_not_suggested():
    assert suggest_command("abc", {"ayuda", "comandos"}) is None
    assert suggest_command("zzzzzzzz", {"ayuda", "comandos"}) is None
    assert suggest_command("metrias", set()) is None


def test_suggestion_never_executes_or_mutates_the_command_catalog():
    choices = {"ayuda", "pagar", "advertir"}
    result = suggest_command("pagarr", choices)
    assert result == ("pagar", 91) or (result and result[0] == "pagar")
    assert choices == {"ayuda", "pagar", "advertir"}
