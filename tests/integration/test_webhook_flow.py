"""End-to-end webhook tests against the real FastAPI ASGI application.

All platform dispatch side effects are stubbed. These tests use signed synthetic
Anarchy events and never call Anarchy, Gemini, Groq, or any external service.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import httpx
import pytest

import main as bot_main


DEVELOPER_SECRET = "integration-developer-secret"
COMMUNITY_SECRET = "integration-community-secret"


@pytest.fixture
def isolated_webhook_state(monkeypatch):
    """Reset process-local webhook state and install non-production secrets."""
    monkeypatch.setattr(bot_main, "WEBHOOK_SECRET", DEVELOPER_SECRET)
    monkeypatch.setattr(bot_main, "COMMUNITY_SECRET", COMMUNITY_SECRET)
    monkeypatch.setattr(bot_main, "BOT_IDENTITIES", {})

    bot_main.seen_events.clear()
    bot_main.seen_event_set.clear()
    bot_main.event_dispatch_locks.clear()
    bot_main.webhook_tasks.clear()
    bot_main.background_tasks.clear()
    metrics_before = dict(bot_main.metrics)

    yield

    bot_main.seen_events.clear()
    bot_main.seen_event_set.clear()
    bot_main.event_dispatch_locks.clear()
    bot_main.webhook_tasks.clear()
    bot_main.background_tasks.clear()
    bot_main.metrics.clear()
    bot_main.metrics.update(metrics_before)


def signed_request(
    event: dict,
    secret: str,
    *,
    timestamp: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    """Build the exact timestamp.body HMAC format used by Anarchy webhooks."""
    raw = json.dumps(
        event, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    ts = str(int(time.time()) if timestamp is None else timestamp)
    digest = hmac.new(
        secret.encode("utf-8"),
        ts.encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return raw, {
        "content-type": "application/json",
        "X-Anarchy-Bot-Timestamp": ts,
        "X-Anarchy-Bot-Signature": f"v1={digest}",
    }


async def drain_webhook_tasks() -> None:
    """Wait for tasks created by webhook() before asserting their effects."""
    tasks = tuple(bot_main.webhook_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def post_signed(
    client: httpx.AsyncClient,
    path: str,
    event: dict,
    secret: str,
    *,
    timestamp: int | None = None,
) -> httpx.Response:
    raw, headers = signed_request(event, secret, timestamp=timestamp)
    return await client.post(path, content=raw, headers=headers)


@pytest.fixture
def asgi_client():
    """Use the app directly; startup and external APIs are not invoked."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bot_main.app),
        base_url="http://katherine.test",
    )


@pytest.mark.asyncio
async def test_signed_developer_webhook_is_accepted_and_dispatched(
    isolated_webhook_state, asgi_client, monkeypatch
):
    received = []

    async def capture_dispatch(event, source):
        received.append((event, source))

    monkeypatch.setattr(bot_main, "safe_dispatch", capture_dispatch)
    event = {
        "eventId": "integration-developer-001",
        "type": "thread.added",
        "threadId": "integration-room",
    }

    async with asgi_client as client:
        response = await post_signed(client, "/webhook", event, DEVELOPER_SECRET)
        await drain_webhook_tasks()

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert received == [(event, "developer")]
    assert bot_main.metrics["webhook_events"] == 1


@pytest.mark.asyncio
async def test_community_route_uses_community_secret_only(
    isolated_webhook_state, asgi_client, monkeypatch
):
    received = []

    async def capture_dispatch(event, source):
        received.append((event, source))

    monkeypatch.setattr(bot_main, "safe_dispatch", capture_dispatch)
    event = {
        "eventId": "integration-community-001",
        "type": "thread.added",
        "threadId": "community-room",
    }

    async with asgi_client as client:
        rejected = await post_signed(client, "/community", event, DEVELOPER_SECRET)
        accepted = await post_signed(client, "/community", event, COMMUNITY_SECRET)
        await drain_webhook_tasks()

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert received == [(event, "community")]


@pytest.mark.asyncio
async def test_root_alias_identifies_community_secret_correctly(
    isolated_webhook_state, asgi_client, monkeypatch
):
    received = []

    async def capture_dispatch(event, source):
        received.append((event, source))

    monkeypatch.setattr(bot_main, "safe_dispatch", capture_dispatch)
    event = {
        "eventId": "integration-root-community-001",
        "type": "thread.added",
        "threadId": "community-root-room",
    }

    async with asgi_client as client:
        response = await post_signed(client, "/", event, COMMUNITY_SECRET)
        await drain_webhook_tasks()

    assert response.status_code == 200
    assert received == [(event, "community")]


@pytest.mark.asyncio
async def test_duplicate_event_id_is_accepted_once(
    isolated_webhook_state, asgi_client, monkeypatch
):
    received = []

    async def capture_dispatch(event, source):
        received.append((event, source))

    monkeypatch.setattr(bot_main, "safe_dispatch", capture_dispatch)
    event = {
        "eventId": "integration-duplicate-001",
        "type": "thread.added",
        "threadId": "duplicate-room",
    }

    async with asgi_client as client:
        first = await post_signed(client, "/webhook", event, DEVELOPER_SECRET)
        await drain_webhook_tasks()
        second = await post_signed(client, "/webhook", event, DEVELOPER_SECRET)
        await drain_webhook_tasks()

    assert first.status_code == 200
    assert first.json() == {"ok": True}
    assert second.status_code == 200
    assert second.json() == {"ok": True, "duplicate": True}
    assert received == [(event, "developer")]
    assert bot_main.metrics["webhook_events"] == 1


@pytest.mark.asyncio
async def test_invalid_and_expired_signatures_are_rejected(
    isolated_webhook_state, asgi_client, monkeypatch
):
    received = []

    async def capture_dispatch(event, source):
        received.append((event, source))

    monkeypatch.setattr(bot_main, "safe_dispatch", capture_dispatch)
    event = {
        "eventId": "integration-invalid-signature-001",
        "type": "thread.added",
        "threadId": "signature-room",
    }
    raw, headers = signed_request(event, DEVELOPER_SECRET)
    headers["X-Anarchy-Bot-Signature"] = "v1=" + ("0" * 64)

    async with asgi_client as client:
        invalid = await client.post("/webhook", content=raw, headers=headers)
        expired = await post_signed(
            client,
            "/webhook",
            {**event, "eventId": "integration-expired-001"},
            DEVELOPER_SECRET,
            timestamp=int(time.time()) - 301,
        )
        await drain_webhook_tasks()

    assert invalid.status_code == 401
    assert expired.status_code == 401
    assert received == []
    assert bot_main.metrics["webhook_events"] == 0


@pytest.mark.asyncio
async def test_signed_malformed_json_is_rejected(
    isolated_webhook_state, asgi_client, monkeypatch
):
    received = []
    raw = b"{not valid json"
    timestamp = str(int(time.time()))
    signature = hmac.new(
        DEVELOPER_SECRET.encode(),
        timestamp.encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()

    async def capture_dispatch(event, source):
        received.append((event, source))

    monkeypatch.setattr(bot_main, "safe_dispatch", capture_dispatch)
    async with asgi_client as client:
        response = await client.post(
            "/webhook",
            content=raw,
            headers={
                "content-type": "application/json",
                "X-Anarchy-Bot-Timestamp": timestamp,
                "X-Anarchy-Bot-Signature": f"v1={signature}",
            },
        )
        await drain_webhook_tasks()

    assert response.status_code == 401
    assert received == []


@pytest.mark.asyncio
async def test_oversized_webhook_is_rejected_before_signature_validation(
    isolated_webhook_state, asgi_client, monkeypatch
):
    received = []

    async def capture_dispatch(event, source):
        received.append((event, source))

    monkeypatch.setattr(bot_main, "safe_dispatch", capture_dispatch)
    payload = b"x" * (bot_main.MAX_WEBHOOK_BODY_BYTES + 1)

    async with asgi_client as client:
        response = await client.post(
            "/webhook",
            content=payload,
            headers={"content-type": "application/json"},
        )
        await drain_webhook_tasks()

    assert response.status_code == 413
    assert received == []


@pytest.mark.asyncio
async def test_webhook_dispatches_signed_command_through_real_dispatch_pipeline(
    isolated_webhook_state, asgi_client, monkeypatch
):
    """Exercise webhook -> safe_dispatch -> dispatch -> command router."""
    handled = []

    monkeypatch.setattr(
        bot_main, "check_antispam_message", lambda *_args: (False, None)
    )
    monkeypatch.setattr(
        bot_main.AI_CORE,
        "record_chat_message",
        lambda *_args, **_kwargs: False,
    )

    async def no_speech(*_args, **_kwargs):
        return None

    async def capture_command(event, source):
        handled.append((event["message"]["content"], source))

    monkeypatch.setattr(bot_main, "enqueue_message_for_speech", no_speech)
    monkeypatch.setattr(bot_main, "handle_command", capture_command)

    event = {
        "eventId": "integration-command-001",
        "type": "message",
        "threadId": "command-room",
        "message": {
            "content": "!ayuda ia",
            "author": {"uid": "synthetic-user-1", "nickname": "Prueba"},
            "messageId": "synthetic-message-1",
        },
    }

    async with asgi_client as client:
        response = await post_signed(client, "/webhook", event, DEVELOPER_SECRET)
        await drain_webhook_tasks()

    assert response.status_code == 200
    assert handled == [("!ayuda ia", "developer")]
    assert bot_main.metrics["webhook_dispatch_completed"] >= 1


@pytest.mark.asyncio
async def test_webhook_returns_503_when_no_secret_is_configured(
    isolated_webhook_state, asgi_client, monkeypatch
):
    monkeypatch.setattr(bot_main, "WEBHOOK_SECRET", "")
    monkeypatch.setattr(bot_main, "COMMUNITY_SECRET", "")
    event = {
        "eventId": "integration-no-secret-001",
        "type": "thread.added",
        "threadId": "no-secret-room",
    }

    async with asgi_client as client:
        response = await post_signed(client, "/webhook", event, DEVELOPER_SECRET)
        await drain_webhook_tasks()

    assert response.status_code == 503
    assert response.json()["detail"] == "Ningún secreto de webhook está configurado."


@pytest.mark.asyncio
async def test_health_endpoint_is_available_without_starting_external_clients(
    isolated_webhook_state, asgi_client
):
    async with asgi_client as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "bot": "Katherine"}
