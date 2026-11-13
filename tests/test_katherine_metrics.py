"""Tests for aggregate-only Prometheus metrics export."""
from katherine_metrics import render_prometheus_metrics


def test_metrics_export_contains_aggregate_counts_without_identity_labels():
    payload, content_type = render_prometheus_metrics(
        {"commands_received": 7, "api_requests": 12, "api_errors": 2},
        {"example.org": {"requests": 4, "failures": 1}},
        uptime_seconds=120,
        memory_bytes=1024,
        cpu_percent=2.5,
    )
    text = payload.decode("utf-8")
    assert "katherine_commands_received_total 7.0" in text
    assert "katherine_api_requests_total 12.0" in text
    assert "katherine_external_requests_total 4.0" in text
    assert "katherine_process_uptime_seconds 120.0" in text
    assert "katherine_tts_messages_enqueued_total 0.0" in text
    assert "text/plain" in content_type
    assert "uid=" not in text
    assert "nickname=" not in text
    assert "thread=" not in text


def test_metrics_export_handles_missing_and_invalid_counter_values():
    payload, _ = render_prometheus_metrics(
        {"commands_received": "invalid", "api_requests": float("nan")},
        uptime_seconds=float("inf"),
    )
    text = payload.decode("utf-8")
    assert "katherine_commands_received_total 0.0" in text
    assert "katherine_api_requests_total 0.0" in text
    assert "katherine_process_uptime_seconds 0.0" in text
