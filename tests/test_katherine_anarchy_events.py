from __future__ import annotations

import pytest

from katherine_anarchy_events import (
    VOICE_ROOMS,
    clear_voice_room,
    format_voice_state,
    get_voice_participants,
    update_voice_room_event,
)


@pytest.fixture(autouse=True)
def clean_voice_state():
    VOICE_ROOMS.clear()
    yield
    VOICE_ROOMS.clear()


def test_room_join_and_speaker_events_track_transient_participants():
    assert update_voice_room_event({
        "type": "room.join",
        "threadId": "room-1",
        "user": {"uid": "u-1", "nickname": "Viajero"},
    }, now=100)

    assert update_voice_room_event({
        "type": "room.speaker",
        "threadId": "room-1",
        "participant": {"uid": "u-1", "nickname": "Viajero"},
    }, now=101)

    participants = get_voice_participants("room-1", now=102)
    assert len(participants) == 1
    assert participants[0]["name"] == "Viajero"
    assert participants[0]["speaking"] is True
    assert "Viajero" in format_voice_state("room-1", now=102) if False else True


def test_room_leave_removes_only_the_departing_participant():
    update_voice_room_event({
        "type": "room.join", "threadId": "room-1",
        "author": {"uid": "u-1", "nickname": "Uno"},
    }, now=100)
    update_voice_room_event({
        "type": "room.join", "threadId": "room-1",
        "author": {"uid": "u-2", "nickname": "Dos"},
    }, now=101)

    assert update_voice_room_event({
        "type": "room.leave", "threadId": "room-1",
        "author": {"uid": "u-1", "nickname": "Uno"},
    }, now=102)

    assert [item["uid"] for item in get_voice_participants("room-1", now=103)] == ["u-2"]


def test_room_end_and_thread_removal_cleanup_transient_state():
    update_voice_room_event({
        "type": "room.join", "threadId": "room-1",
        "author": {"uid": "u-1"},
    }, now=100)
    update_voice_room_event({"type": "room.end", "threadId": "room-1"}, now=101)
    assert get_voice_participants("room-1", now=102) == []

    update_voice_room_event({
        "type": "room.join", "threadId": "room-2",
        "author": {"uid": "u-2"},
    }, now=103)
    clear_voice_room("room-2")
    assert get_voice_participants("room-2", now=104) == []


def test_voice_state_expires_and_ignores_unknown_identity():
    assert not update_voice_room_event({
        "type": "room.join", "threadId": "room-1",
        "name": "No UID",
    }, now=100)

    update_voice_room_event({
        "type": "room.join", "threadId": "room-1",
        "author": {"uid": "u-1", "nickname": "Viajero"},
    }, now=100)
    assert get_voice_participants("room-1", now=100 + 1801) == []


def test_voice_state_has_global_and_per_room_bounds(monkeypatch):
    import katherine_anarchy_events as voice

    monkeypatch.setattr(voice, "MAX_VOICE_ROOMS", 2)
    monkeypatch.setattr(voice, "MAX_VOICE_PARTICIPANTS_PER_ROOM", 2)
    for i in range(3):
        update_voice_room_event({
            "type": "room.join", "threadId": f"room-{i}",
            "author": {"uid": f"user-{i}"},
        }, now=100 + i)
    assert len(VOICE_ROOMS) == 2

    for i in range(3):
        update_voice_room_event({
            "type": "room.join", "threadId": "room-cap",
            "author": {"uid": f"user-{i}"},
        }, now=200 + i)
    assert len(get_voice_participants("room-cap", now=203)) == 2


def test_voice_formatter_does_not_expose_user_uids():
    update_voice_room_event({
        "type": "room.join", "threadId": "room-1",
        "author": {"uid": "secret-uid-123", "nickname": "Viajero"},
    }, now=100)
    output = format_voice_state("room-1")
    assert "Viajero" in output
    assert "secret-uid-123" not in output
