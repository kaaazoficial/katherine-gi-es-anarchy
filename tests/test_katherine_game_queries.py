"""Tests for intent-aware Genshin entity parsing and API result validation."""
import pytest

from katherine_game_queries import (
    infer_genshin_data_folder,
    resolve_genshin_entity_intent,
    strip_game_scope_qualifier,
    unwrap_genshin_db_response,
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Natlan Genshin Impact", "Natlan"),
        ("Natlan en Genshin Impact", "Natlan"),
        ("Natlan de Genshin Impact", "Natlan"),
        ("Fontaine (Genshin Impact)", "Fontaine"),
        ("Genshin Impact", "Genshin Impact"),
    ],
)
def test_game_scope_is_removed_without_corrupting_entity_names(query, expected):
    assert strip_game_scope_qualifier(query) == expected.casefold()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("qué es Natlan Genshin Impact", ("geografia", "Natlan")),
        ("qué es la región de Natlan en Genshin Impact", ("geografia", "Natlan")),
        ("dónde está Fontaine", ("geografia", "Fontaine")),
        ("qué es el elemento Dendro", ("elemento", "Dendro")),
        ("qué es Pyro", ("elemento", "Pyro")),
        ("quién es Mualani en Genshin Impact", ("enciclopedia", "mualani")),
        ("qué materiales necesita Mualani Genshin Impact", ("materiales", "mualani")),
        ("qué armas usa Mualani", ("guia", "mualani")),
        ("qué equipos hay para Nahida", ("equipos", "nahida")),
        ("cuál es la rotación de Cyno", ("rotacion", "cyno")),
    ],
)
def test_entity_intent_resolver_maps_question_type_to_existing_command(query, expected):
    assert resolve_genshin_entity_intent(query) == expected


def test_unrelated_questions_do_not_get_forced_into_genshin_tools():
    assert resolve_genshin_entity_intent("qué materiales necesita mi laptop") is None
    assert resolve_genshin_entity_intent("qué equipos necesito para mi PC") is None


@pytest.mark.parametrize(
    ("query", "folder"),
    [
        ("Natlan", "geographies"),
        ("Natlan Genshin Impact", "geographies"),
        ("Dendro", "elements"),
        ("Pyro", "elements"),
        ("Mualani", None),
    ],
)
def test_entity_folder_inference(query, folder):
    assert infer_genshin_data_folder(query) == folder


def test_dump_result_no_match_envelope_is_discarded():
    payload = {
        "query": "natlan genshin impact",
        "folder": "characters",
        "matchFolder": "characters",
        "matchtype": "none",
        "result": None,
    }
    assert unwrap_genshin_db_response(payload) is None


def test_dump_result_envelope_returns_only_the_matched_entity():
    entity = {"name": "Natlan", "description": "Una de las naciones de Teyvat."}
    payload = {
        "query": "natlan",
        "folder": "geographies",
        "matchFolder": "geographies",
        "matchtype": "matchName",
        "result": entity,
    }
    assert unwrap_genshin_db_response(payload) is entity


def test_dump_result_metadata_without_entity_is_not_displayable():
    payload = {
        "query": "natlan",
        "folder": "characters",
        "matchFolder": "characters",
        "matchtype": "none",
    }
    assert unwrap_genshin_db_response(payload) is None


def test_plain_entity_payload_is_preserved():
    entity = {"name": "Mualani", "description": "Una guía."}
    assert unwrap_genshin_db_response(entity) is entity
