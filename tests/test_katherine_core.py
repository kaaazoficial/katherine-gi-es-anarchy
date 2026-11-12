"""Regression tests for Katherine's local, deterministic core behavior."""
from __future__ import annotations

import pytest

import main as katherine


@pytest.fixture
def database(tmp_path, monkeypatch):
    """Give every database test a fresh SQLite file."""
    monkeypatch.setattr(katherine, "DB_PATH", tmp_path / "katherine.sqlite3")
    katherine.init_db()
    return katherine


def test_database_initializes_expected_core_tables(database):
    with database.db_session() as db:
        names = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert {
        "users",
        "chat_settings",
        "moderation_warnings",
        "dj_playlists",
        "dj_playlist_tracks",
        "chat_activity_daily",
        "admin_audit_log",
        "auto_reply_rules",
    }.issubset(names)


def test_user_upsert_updates_nickname_and_counts_messages(database):
    database.record_user("user-1", "Viajero", count_message=True)
    database.record_user("user-1", "Viajero actualizado", count_message=True)

    user = database.get_user("user-1")
    assert user is not None
    assert user["nickname"] == "Viajero actualizado"
    assert user["message_count"] == 2


def test_database_session_rolls_back_on_exception(database):
    database.record_user("user-rollback", "Nombre original")

    with pytest.raises(RuntimeError):
        with database.db_session() as db:
            db.execute(
                "UPDATE users SET nickname=? WHERE uid=?",
                ("Cambio que no debe guardarse", "user-rollback"),
            )
            raise RuntimeError("forzar rollback")

    assert database.get_user("user-rollback")["nickname"] == "Nombre original"


def test_chat_settings_round_trip_and_default(database):
    assert database.get_chat_setting("room-1", "idioma", "es") == "es"

    database.set_chat_setting("room-1", "idioma", "pt")
    assert database.get_chat_setting("room-1", "idioma") == "pt"


def test_points_transfer_is_atomic_and_rejects_invalid_cases(database):
    database.record_user("sender", "Emisor")
    database.record_user("target", "Receptor")
    database.add_points("sender", 50)

    assert database.transfer_points("sender", "target", 20) == "ok"
    assert database.get_user("sender")["points"] == 30
    assert database.get_user("target")["points"] == 20

    assert database.transfer_points("sender", "target", 99) == "insufficient"
    assert database.transfer_points("sender", "target", 0) == "invalid"
    assert database.transfer_points("sender", "sender", 1) == "invalid"
    assert database.transfer_points("sender", "missing", 1) == "missing"

    # A rejected transfer must not alter either balance.
    assert database.get_user("sender")["points"] == 30
    assert database.get_user("target")["points"] == 20


def test_reward_claim_obeys_persistent_cooldown(database):
    database.record_user("reward-user", "Aventurero")

    reward, remaining = database.claim_reward(
        "reward-user", "daily", 3600, 5, 15
    )
    assert reward is not None and 5 <= reward <= 15
    assert remaining == 0

    second_reward, second_remaining = database.claim_reward(
        "reward-user", "daily", 3600, 5, 15
    )
    assert second_reward is None
    assert second_remaining > 0


def test_moderation_warning_lifecycle_is_chat_scoped(database):
    warning_id = database.add_moderation_warning(
        "room-1", "user-2", "Usuario", "admin-1", "Moderador",
        "Prueba automática", automatic=True,
    )

    rows = database.get_moderation_warnings("room-1", "user-2")
    assert len(rows) == 1
    assert rows[0]["id"] == warning_id
    assert rows[0]["active"] == 1
    assert rows[0]["automatic"] == 1
    assert database.get_moderation_warnings("room-2", "user-2") == []

    assert database.revoke_moderation_warning("room-1", warning_id, "admin-1")
    assert not database.revoke_moderation_warning(
        "room-1", warning_id, "admin-1"
    )
    assert database.get_moderation_warnings("room-1", "user-2")[0]["active"] == 0


def test_dj_playlist_creation_tracks_and_deletion(database):
    assert database.create_dj_playlist("room-1", "Música favorita", "user-1")
    assert not database.create_dj_playlist(
        "room-1", " música   favorita ", "user-2"
    )
    assert not database.create_dj_playlist("room-1", "x", "user-1")

    assert database.add_dj_playlist_track(
        "room-1", "MÚSICA FAVORITA", "Tema de prueba"
    )
    assert not database.add_dj_playlist_track(
        "room-1", "no existe", "Otro tema"
    )

    playlists = database.list_dj_playlists("room-1")
    assert len(playlists) == 1
    assert playlists[0]["track_count"] == 1

    playlist, tracks = database.get_dj_playlist("room-1", "Música favorita")
    assert playlist["name"] == "Música favorita"
    assert tracks[0]["title"] == "Tema de prueba"

    assert database.delete_dj_playlist("room-1", "Música favorita")
    assert database.get_dj_playlist("room-1", "Música favorita") == (None, [])


def test_antispam_configuration_applies_safe_bounds(database):
    defaults = database.anti_spam_configuration("room-1")
    assert defaults == {
        "enabled": True,
        "max_messages": 7,
        "window_seconds": 8,
        "repeat_limit": 3,
        "max_links": 4,
        "max_mentions": 8,
    }

    database.set_chat_setting("room-1", "antispam_enabled", "0")
    database.set_chat_setting("room-1", "antispam_max_messages", "999")
    database.set_chat_setting("room-1", "antispam_window_seconds", "1")
    database.set_chat_setting("room-1", "antispam_repeat_limit", "99")
    database.set_chat_setting("room-1", "antispam_max_links", "99")
    database.set_chat_setting("room-1", "antispam_max_mentions", "1")

    config = database.anti_spam_configuration("room-1")
    assert config["enabled"] is False
    assert config["max_messages"] == 20
    assert config["window_seconds"] == 3
    assert config["repeat_limit"] == 10
    assert config["max_links"] == 12
    assert config["max_mentions"] == 3


def test_volume_controls_handle_limits_and_restore():
    assert katherine.calculate_volume_target(["subir", "20"], 50, 35) == 70
    assert katherine.calculate_volume_target(["bajar"], 50, 35) == 40
    assert katherine.calculate_volume_target(["mute"], 50, 35) == 0
    assert katherine.calculate_volume_target(["restaurar"], 10, 35) == 35
    assert katherine.calculate_volume_target(["75%"], 10, 35) == 75

    with pytest.raises(ValueError):
        katherine.calculate_volume_target(["101"], 50, 35)


def test_tts_volume_is_clamped_and_persisted(database):
    state = {"tts_volume": 0.5, "last_tts_volume": 0.5}

    assert database.update_tts_volume("room-1", state, 125) == 100
    assert state["tts_volume"] == 1.0
    assert state["last_tts_volume"] == 1.0
    assert database.get_chat_setting("room-1", "tts_volume") == "100"

    assert database.update_tts_volume("room-1", state, 0) == 0
    assert state["tts_volume"] == 0.0
    assert state["last_tts_volume"] == 1.0


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (59, "59s"),
        (60, "1m 0s"),
        (3600, "1h 0m 0s"),
        (86400, "1d 0h 0m 0s"),
    ],
)
def test_duration_formatting(seconds, expected):
    assert katherine.format_duration(seconds) == expected


@pytest.mark.parametrize("length", [1, 1999, 2000, 2001, 3999, 4000, 4001, 12000])
def test_message_splitter_preserves_content_and_limit(length):
    content = "K" * length
    parts = katherine.split_message_content(content, 2000)

    assert parts
    assert all(0 < len(part) <= 2000 for part in parts)
    assert "".join(parts) == content


def test_builtin_katherine_regression_suite_passes():
    """Run the project's own Genshin, parser, formatting and split regressions."""
    result = katherine.run_katherine_self_tests()

    assert result["checks"] > 0
    assert result["failed"] == 0, result["failures"]
    assert result["passed"] == result["checks"]

def test_dj_favorites_are_scoped_to_user_and_room(database, monkeypatch):
    monkeypatch.setattr(katherine, "DJ_FAVORITES", {})
    track = {
        "url": "https://audio.jamendo.com/track.mp3",
        "title": "Tema favorito",
        "creator": "Artista",
        "license": "CC BY",
        "source": "Jamendo",
        "extension": ".mp3",
    }
    assert database.save_dj_favorite("room-1", "user-1", track)
    assert not database.save_dj_favorite("room-1", "user-1", track)
    assert database.list_dj_favorites("room-1", "user-1")[0]["title"] == "Tema favorito"
    assert database.list_dj_favorites("room-1", "user-2") == []
    assert database.list_dj_favorites("room-2", "user-1") == []
    favorite_id = database.list_dj_favorites("room-1", "user-1")[0]["id"]
    assert database.delete_dj_favorite("room-1", "user-1", favorite_id)
    assert database.list_dj_favorites("room-1", "user-1") == []

def test_guardian_warns_for_link_bursts_and_records_reason(database, monkeypatch):
    monkeypatch.setattr(katherine, "ADMIN_UIDS", set())
    katherine.USER_MESSAGE_ACTIVITY.clear()
    katherine.USER_SPAM_WARNED_UNTIL.clear()
    event = {
        "threadId": "room-guardian",
        "message": {
            "content": "https://one.example https://two.example https://three.example",
            "author": {"uid": "user-guardian", "nickname": "Viajero"},
            "mentions": [],
        },
    }
    blocked, info = katherine.check_antispam_message(event, "developer")
    assert blocked is True
    assert info and info["warning_id"]
    target_uid = katherine.scoped_uid("developer:room-guardian", "user-guardian")
    warnings = database.get_moderation_warnings("room-guardian", target_uid)
    assert warnings[0]["automatic"] == 1
    assert "Exceso de enlaces" in warnings[0]["reason"]


def test_external_circuit_breaker_skips_open_host_without_network(monkeypatch):
    host = "astra-circuit.test"
    monkeypatch.setitem(
        katherine.SOURCE_HEALTH,
        host,
        {"circuit_open_until": katherine.time.time() + 60},
    )
    before = katherine.metrics["external_circuit_open"]
    with pytest.raises(RuntimeError, match="temporalmente aislada"):
        import asyncio
        asyncio.run(katherine.external_request("GET", f"https://{host}/status"))
    assert katherine.metrics["external_circuit_open"] == before + 1
    katherine.SOURCE_HEALTH.pop(host, None)

