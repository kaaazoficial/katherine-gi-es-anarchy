"""Privacy-safe Prometheus exposition for process-local Katherine metrics."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, generate_latest


_COUNTERS = (
    ("katherine_webhook_events", "webhook_events", "Webhook events accepted by this process."),
    ("katherine_text_messages_received", "text_messages_received", "Text messages received by this process."),
    ("katherine_commands_received", "commands_received", "Commands received by this process."),
    ("katherine_ai_requests", "ai_requests", "AI requests handled by this process."),
    ("katherine_ai_errors", "ai_errors", "AI provider errors and fallbacks recorded by this process."),
    ("katherine_messages_sent", "messages_sent", "Messages sent by this process."),
    ("katherine_api_requests", "api_requests", "Anarchy API requests made by this process."),
    ("katherine_api_errors", "api_errors", "Anarchy API request errors recorded by this process."),
    ("katherine_dj_tracks_started", "dj_tracks_started", "DJ tracks started by this process."),
    ("katherine_dj_playback_errors", "dj_playback_errors", "DJ playback errors recorded by this process."),
    ("katherine_message_send_retries", "message_send_retries", "Rate-limited outgoing message retries."),
    ("katherine_message_send_429", "message_send_429", "Outgoing message rate-limit responses."),
    ("katherine_message_send_failures", "message_send_failures", "Outgoing message blocks that could not be delivered."),
    ("katherine_message_send_recovered", "message_send_recovered", "Outgoing message blocks recovered from a definitive rate limit."),
    ("katherine_message_send_pending_queued", "message_send_pending_queued", "Outgoing message blocks temporarily queued in process memory."),
    ("katherine_message_send_pending_expired", "message_send_pending_expired", "Pending message blocks that expired before delivery."),
    ("katherine_message_send_pending_abandoned", "message_send_pending_abandoned", "Pending message blocks abandoned after a non-rate-limit error."),
)


def _finite_number(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return max(0.0, number) if math.isfinite(number) else 0.0


def render_prometheus_metrics(
    counters: Mapping[str, Any],
    source_health: Mapping[str, Any] | None = None,
    *,
    uptime_seconds: float = 0.0,
    memory_bytes: float | None = None,
    cpu_percent: float | None = None,
) -> tuple[bytes, str]:
    """Render aggregate-only metrics; never attach user, room or message labels."""
    registry = CollectorRegistry()
    for metric_name, key, description in _COUNTERS:
        metric = Counter(metric_name, description, registry=registry)
        metric.inc(_finite_number(counters.get(key, 0)))

    external_requests = 0.0
    external_failures = 0.0
    for item in (source_health or {}).values():
        if not isinstance(item, Mapping):
            continue
        external_requests += _finite_number(item.get("requests", 0))
        external_failures += _finite_number(item.get("failures", 0))

    for metric_name, key, description in (
        ("katherine_ai_provider_requests", "ai_provider_requests", "AI provider requests made by this process."),
        ("katherine_ai_provider_429", "ai_provider_429", "AI provider rate-limit responses."),
        ("katherine_ai_provider_retries", "ai_provider_retries", "AI provider retries after rate limits."),
        ("katherine_ai_provider_daily_quota_hits", "ai_provider_daily_quota_hits", "AI daily quota exhaustion events."),
        ("katherine_ai_provider_input_tokens", "ai_provider_input_tokens", "Input tokens reported by the AI provider."),
        ("katherine_ai_provider_output_tokens", "ai_provider_output_tokens", "Output tokens reported by the AI provider."),
        ("katherine_ai_provider_responses_completed", "ai_provider_responses_completed", "Structured AI responses completed."),
    ):
        Counter(metric_name, description, registry=registry).inc(
            _finite_number(counters.get(key, 0))
        )

    Gauge(
        "katherine_ai_provider_queue_wait_seconds",
        "Cumulative seconds AI requests waited for provider capacity.",
        registry=registry,
    ).set(_finite_number(counters.get("ai_provider_queue_wait_seconds", 0)))
    Gauge(
        "katherine_message_send_retry_wait_seconds",
        "Cumulative seconds spent waiting before outgoing message retries.",
        registry=registry,
    ).set(_finite_number(counters.get("message_send_retry_wait_seconds", 0)))

    Counter(
        "katherine_external_requests",
        "External source requests made by this process.",
        registry=registry,
    ).inc(external_requests)
    Counter(
        "katherine_external_failures",
        "External source failures recorded by this process.",
        registry=registry,
    ).inc(external_failures)
    Counter(
        "katherine_tts_messages_enqueued",
        "Priority and regular TTS messages enqueued by this process.",
        registry=registry,
    ).inc(
        _finite_number(counters.get("tts_priority_enqueued", 0))
        + _finite_number(counters.get("tts_regular_enqueued", 0))
    )

    Gauge(
        "katherine_process_uptime_seconds",
        "Seconds since this Katherine process started.",
        registry=registry,
    ).set(_finite_number(uptime_seconds))
    if memory_bytes is not None:
        Gauge(
            "katherine_process_memory_bytes",
            "Resident memory used by the Katherine process.",
            registry=registry,
        ).set(_finite_number(memory_bytes))
    if cpu_percent is not None:
        Gauge(
            "katherine_process_cpu_percent",
            "CPU percentage reported for the Katherine process.",
            registry=registry,
        ).set(_finite_number(cpu_percent))

    return generate_latest(registry), CONTENT_TYPE_LATEST
