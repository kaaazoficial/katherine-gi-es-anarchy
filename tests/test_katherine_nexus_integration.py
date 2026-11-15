"""Integration-focused unit tests for Katherine Nexus phases."""
from __future__ import annotations

import asyncio

import main as katherine


def test_progress_message_edits_with_existing_documented_patch(monkeypatch):
    calls = []

    async def fake_api(method, path, source, body):
        calls.append((method, path, source, body))
        return {"ok": True, "status": 200, "data": {}}

    monkeypatch.setattr(katherine, "api", fake_api)
    context = {
        "thread_id": "thread-1",
        "source": "developer",
        "message_id": "message-1",
        "created_at": katherine.time.time(),
        "enabled": True,
    }

    assert asyncio.run(katherine.update_progress_message(context, "Fase 2"))
    assert calls == [
        (
            "PATCH",
            "/threads/thread-1/messages/message-1",
            "developer",
            {"content": "Fase 2"},
        )
    ]


def test_progress_message_disables_editing_after_api_failure(monkeypatch):
    calls = []

    async def fake_api(method, path, source, body):
        calls.append(method)
        return {"ok": False, "status": 404, "data": {"error": "not editable"}}

    monkeypatch.setattr(katherine, "api", fake_api)
    context = {
        "thread_id": "thread-1",
        "source": "developer",
        "message_id": "missing-message",
        "created_at": katherine.time.time(),
        "enabled": True,
    }

    assert not asyncio.run(katherine.update_progress_message(context, "Final"))
    assert context["enabled"] is False
    assert calls == ["PATCH"]


def test_progress_falls_back_to_separate_final_message_when_no_id(monkeypatch):
    sent = []

    async def fake_send(thread_id, content, source="developer", reply_id=None, buttons=None):
        sent.append((thread_id, content, source, reply_id))
        return {"ok": True, "status": 200, "data": {"threadId": thread_id}}

    monkeypatch.setattr(katherine, "send_message", fake_send)
    context = asyncio.run(
        katherine.start_progress_message("thread-1", "Consultando…", "developer", "reply-1")
    )
    assert context["enabled"] is False
    assert asyncio.run(
        katherine.finish_progress_message(
            context, "Resultado final", "thread-1", "developer", "reply-1"
        )
    )["ok"]
    assert [item[1] for item in sent] == ["Consultando…", "Resultado final"]


def test_guide_sources_run_in_parallel_and_return_partial_data(monkeypatch):
    active = 0
    maximum_active = 0
    progress = []

    async def get_character(query):
        return {"name": "NexusRegressionCharacter"}

    async def character_stats(query):
        return {}

    async def helper_guide(*args):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            await asyncio.sleep(0.02)
            return {
                "artifacts": "Set de prueba",
                "weapons": "Arma de prueba",
                "talents": "1-2-3",
            }
        finally:
            active -= 1

    async def kqm_teams(*args):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            await asyncio.sleep(0.02)
            return {"teams": ["Equipo de prueba"]}
        finally:
            active -= 1

    monkeypatch.setattr(katherine, "get_character", get_character)
    monkeypatch.setattr(katherine, "character_stats", character_stats)
    monkeypatch.setattr(katherine, "fetch_helper_character_guide", helper_guide)
    monkeypatch.setattr(katherine, "fetch_kqm_teams", kqm_teams)

    async def callback(text):
        progress.append(text)

    result = asyncio.run(
        katherine.collect_character_guide_data(
            "NexusRegressionCharacter", progress_callback=callback
        )
    )

    assert maximum_active >= 2
    assert result["helper_data"]["artifacts"] == "Set de prueba"
    assert result["kqm_data"]["teams"] == ["Equipo de prueba"]
    assert progress
    assert any("RESPALDO" in item or "FICHA" in item for item in progress)

def test_unaddressed_chat_only_updates_bounded_activity_and_does_not_activate_katherine(monkeypatch):
    calls = {"commands": [], "users": [], "activity": [], "auto_replies": []}

    monkeypatch.setattr(katherine, "check_antispam_message", lambda event, source: (False, None))

    async def capture_auto_reply(*args):
        calls["auto_replies"].append(args)

    monkeypatch.setattr(katherine, "maybe_send_auto_reply", capture_auto_reply)
    async def no_spelling(*_args, **_kwargs):
        return False
    monkeypatch.setattr(katherine, "maybe_send_spelling_feedback", no_spelling)

    async def no_speech(event, source):
        return None

    async def capture_command(event, source):
        calls["commands"].append(event["message"]["content"])

    monkeypatch.setattr(katherine, "enqueue_message_for_speech", no_speech)
    async def no_spelling(*_args, **_kwargs):
        return False
    monkeypatch.setattr(katherine, "maybe_send_spelling_feedback", no_spelling)
    monkeypatch.setattr(katherine, "detect_spanish_spelling_issues", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(katherine, "handle_command", capture_command)
    monkeypatch.setattr(katherine, "record_user", lambda uid, nickname, count_message=False: calls["users"].append((uid, nickname, count_message)))
    monkeypatch.setattr(katherine, "record_chat_activity", lambda thread, uid, nickname: calls["activity"].append((thread, uid, nickname)))

    event = {
        "type": "message",
        "threadId": "room-natural",
        "message": {
            "content": "hola a todos",
            "author": {"uid": "user-1", "nickname": "Viajero"},
        },
    }
    asyncio.run(katherine.dispatch(event, "developer"))

    assert calls["commands"] == []
    assert calls["users"] == []
    assert calls["activity"] == [("room-natural", "developer:room-natural:user-1", "Viajero")]
    assert calls["auto_replies"] == [("room-natural", "user-1", "Viajero", "hola a todos")]


def test_addressed_natural_request_routes_to_existing_command(monkeypatch):
    calls = []

    monkeypatch.setattr(katherine, "check_antispam_message", lambda event, source: (False, None))

    async def no_speech(event, source):
        return None

    async def capture_command(event, source):
        calls.append(event["message"]["content"])

    monkeypatch.setattr(katherine, "enqueue_message_for_speech", no_speech)
    monkeypatch.setattr(katherine, "handle_command", capture_command)
    monkeypatch.setattr(katherine, "record_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(katherine, "record_chat_activity", lambda *args, **kwargs: None)

    event = {
        "type": "message",
        "threadId": "room-natural",
        "message": {
            "content": "Katherine, pausa la música",
            "author": {"uid": "user-1", "nickname": "Viajero"},
        },
    }
    asyncio.run(katherine.dispatch(event, "developer"))
    assert calls == ["!dj pausa"]



def test_unaddressed_typo_gets_a_gentle_correction_without_persistent_history(monkeypatch):
    sent = []

    monkeypatch.setattr(katherine, "SPELLING_CHAT_COOLDOWN", {})
    monkeypatch.setattr(katherine, "SPELLING_USER_COOLDOWN", {})

    def fake_detect(text):
        from types import SimpleNamespace
        assert text == "Ola, como estas?"
        return SimpleNamespace(
            corrected_text="Hola, ¿cómo estás?",
            corrections=(("Ola", "Hola"),),
        )

    async def fake_send(thread, content, source="developer", reply_id=None, buttons=None):
        sent.append((thread, content, source))
        return {"ok": True}

    monkeypatch.setattr(katherine, "detect_spanish_spelling_issues", fake_detect)
    monkeypatch.setattr(katherine, "send_message", fake_send)

    async def scenario():
        sent_ok = await katherine.maybe_send_spelling_feedback(
            "room-spell", "user-1", "Viajero", "Ola, como estas?"
        )
        repeated = await katherine.maybe_send_spelling_feedback(
            "room-spell", "user-1", "Viajero", "Ola, como estas?"
        )
        return sent_ok, repeated

    assert asyncio.run(scenario()) == (True, False)
    assert len(sent) == 1
    assert "Hola, ¿cómo estás?" in sent[0][1]
    assert "Te entendí perfectamente" in sent[0][1]
    assert katherine.metrics["spelling_corrections_sent"] >= 1


def test_affectionate_compliment_to_katherine_gets_a_warm_reply(monkeypatch):
    sent = []
    command_calls = []

    monkeypatch.setattr(katherine, "check_antispam_message", lambda event, source: (False, None))

    async def no_speech(event, source):
        return None

    async def fake_send(thread, content, source="developer", reply_id=None, buttons=None):
        sent.append(content)
        return {"ok": True}

    async def capture_command(event, source):
        command_calls.append(event["message"]["content"])

    monkeypatch.setattr(katherine, "enqueue_message_for_speech", no_speech)
    async def no_spelling(*_args, **_kwargs):
        return False
    monkeypatch.setattr(katherine, "maybe_send_spelling_feedback", no_spelling)
    monkeypatch.setattr(katherine, "send_message", fake_send)
    monkeypatch.setattr(katherine, "handle_command", capture_command)
    monkeypatch.setattr(katherine, "record_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(katherine, "record_chat_activity", lambda *args, **kwargs: None)

    event = {
        "type": "message",
        "threadId": "room-warm",
        "message": {
            "content": "Eres muy linda, Katherine",
            "author": {"uid": "user-warm", "nickname": "Viajero"},
        },
    }
    asyncio.run(katherine.dispatch(event, "developer"))
    assert len(sent) == 1
    assert any(word in sent[0].casefold() for word in ("cumplido", "sonrojar", "ternura"))
    assert command_calls == []


def test_dispatch_tries_spelling_feedback_after_custom_rules(monkeypatch):
    calls = []

    monkeypatch.setattr(katherine, "check_antispam_message", lambda event, source: (False, None))

    async def no_speech(event, source):
        return None

    async def no_auto_reply(*args):
        return False

    async def capture_spelling(*args):
        calls.append(args)

    monkeypatch.setattr(katherine, "enqueue_message_for_speech", no_speech)
    monkeypatch.setattr(katherine, "maybe_send_auto_reply", no_auto_reply)
    monkeypatch.setattr(katherine, "maybe_send_spelling_feedback", capture_spelling)
    monkeypatch.setattr(katherine, "record_chat_activity", lambda *args, **kwargs: None)

    event = {
        "type": "message",
        "threadId": "room-spelling-dispatch",
        "message": {
            "content": "Ola, como estas?",
            "author": {"uid": "user-spelling", "nickname": "Viajero"},
        },
    }
    asyncio.run(katherine.dispatch(event, "developer"))
    assert calls == [("room-spelling-dispatch", "user-spelling", "Viajero", "Ola, como estas?")]
