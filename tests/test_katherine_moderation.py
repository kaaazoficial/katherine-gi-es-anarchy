"""Pure policy tests for Katheryne's anti-spam and moderation primitives."""
import pytest

from katherine_moderation import (
    AntiSpamSettings,
    bounded_expiry_keys,
    bounded_oldest_keys,
    derive_message_signals,
    evaluate_antispam,
    is_platform_bot_author,
    normalize_message_content,
    prune_activity_history,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"antispam_enabled": "0"}, False),
        ({"antispam_enabled": "true"}, True),
        ({"antispam_enabled": "off"}, False),
        ({}, True),
    ],
)
def test_settings_parse_enabled_flag(raw, expected):
    assert AntiSpamSettings.from_mapping(raw).enabled is expected


def test_settings_clamp_untrusted_room_values():
    settings = AntiSpamSettings.from_mapping({
        "antispam_max_messages": "-100",
        "antispam_window_seconds": "999",
        "antispam_repeat_limit": "0",
        "antispam_max_links": "not-a-number",
        "antispam_max_mentions": "1000",
    })
    assert settings.max_messages == 3
    assert settings.window_seconds == 60
    assert settings.repeat_limit == 2
    assert settings.max_links == 4
    assert settings.max_mentions == 20


def test_normalization_bounds_and_collapses_input():
    assert normalize_message_content("  HOLA\n   MUNDO  ") == "hola mundo"
    assert normalize_message_content("A" * 20, max_chars=5) == "aaaaa"
    with pytest.raises(ValueError):
        normalize_message_content("text", max_chars=0)


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ({"bot": True}, True),
        ({"isBot": "YES"}, True),
        ({"is_bot": "1"}, True),
        ({"bot": "false"}, False),
        ({"bot": False}, False),
        (None, False),
        ("not-a-mapping", False),
    ],
)
def test_bot_author_flags_are_strict(author, expected):
    assert is_platform_bot_author(author) is expected


def test_message_signals_hash_and_count_without_returning_message_body():
    signals = derive_message_signals(
        "Mira https://example.com y www.example.org",
        mentions=[{"uid": "a"}, {"uid": "b"}],
    )
    assert signals.normalized_content.startswith("mira ")
    assert len(signals.content_digest) == 64
    assert signals.link_count == 2
    assert signals.mention_count == 2
    assert "example.com" not in signals.content_digest


def test_message_signals_bound_mention_count_and_empty_digest():
    signals = derive_message_signals("  ", mentions=list(range(1000)), max_mentions=5)
    assert signals.content_digest == ""
    assert signals.link_count == 0
    assert signals.mention_count == 5


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ((7, 1, 0, 0), "Antispam automático: demasiados mensajes o contenido repetido."),
        ((2, 3, 0, 0), "Antispam automático: demasiados mensajes o contenido repetido."),
        ((1, 1, 5, 0), "Exceso de enlaces en un mensaje (5)."),
        ((1, 1, 0, 9), "Menciones masivas en un mensaje (9)."),
        ((1, 1, 0, 0), ""),
    ],
)
def test_antispam_policy_prioritizes_hard_limits(counts, expected):
    decision = evaluate_antispam(
        settings=AntiSpamSettings(),
        message_count=counts[0],
        repeated_count=counts[1],
        link_count=counts[2],
        mention_count=counts[3],
    )
    assert decision.reason == expected
    assert decision.blocked is bool(expected)


def test_disabled_antispam_never_blocks():
    decision = evaluate_antispam(
        settings=AntiSpamSettings(enabled=False),
        message_count=100,
        repeated_count=100,
        link_count=100,
        mention_count=100,
    )
    assert not decision.blocked
    assert not decision.reason


def test_history_pruning_removes_only_expired_entries():
    from collections import deque
    history = deque([(1.0, "a"), (2.0, "b"), (4.0, "c")])
    assert prune_activity_history(history, cutoff=3.0) == 2
    assert list(history) == [(4.0, "c")]


def test_oldest_and_expiry_selection_are_deterministic():
    histories = {"new": [(4.0, "x")], "old": [(1.0, "x")], "mid": [(2.0, "x")]}
    assert bounded_oldest_keys(histories, 2) == ("old", "mid")
    assert bounded_oldest_keys(histories, 0) == ()
    cooldowns = {"later": 9.0, "earlier": 2.0, "middle": 5.0}
    assert bounded_expiry_keys(cooldowns, 2) == ("earlier", "middle")
