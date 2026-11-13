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
    ("katherine_messages_sent", "messages_sent", "Messages sent by this process."),
    ("katherine_api_requests", "api_requests", "Anarchy API requests made by this process."),
    ("katherine_api_errors", "api_errors", "Anarchy API request errors recorded by this process."),
    ("katherine_dj_tracks_started", "dj_tracks_started", "DJ tracks started by this process."),
    ("katherine_dj_playback_errors", "dj_playback_errors", "DJ playback errors recorded by this process."),
    ("katherine_tts_messages_enqueued", "tts_priority_enqueued", "Priority TTS messages enqueued by this process."),
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
