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


def test_metrics_export_includes_ai_provider_and_delivery_signals():
    payload, _ = render_prometheus_metrics(
        {
            "ai_provider_requests": 9,
            "ai_provider_429": 2,
            "ai_provider_retries": 3,
            "ai_provider_input_tokens": 1200,
            "ai_provider_output_tokens": 450,
            "message_send_retries": 4,
            "message_send_429": 2,
            "message_send_failures": 1,
            "message_send_retry_wait_seconds": 12.5,
        }
    )
    text = payload.decode("utf-8")
    assert "katherine_ai_provider_requests_total 9.0" in text
    assert "katherine_ai_provider_429_total 2.0" in text
    assert "katherine_ai_provider_input_tokens_total 1200.0" in text
    assert "katherine_message_send_retries_total 4.0" in text
    assert "katherine_message_send_failures_total 1.0" in text
    assert "katherine_message_send_retry_wait_seconds 12.5" in text
    assert "uid=" not in text and "thread=" not in text



def test_metrics_export_includes_bounded_provider_model_token_totals():
    payload, _ = render_prometheus_metrics(provider_usage=[{
        "provider": "Google Gemini", "model": "gemini-test-model",
        "responses": 3, "input_tokens": 120, "output_tokens": 45,
    }])
    rendered = payload.decode("utf-8")
    assert "katherine_ai_provider_model_input_tokens_total" in rendered
    assert 'model="gemini-test-model"' in rendered
    assert 'provider="Google Gemini"' in rendered
    assert " 120.0" in rendered
    assert " 45.0" in rendered
    assert "user=" not in rendered and "thread=" not in rendered
