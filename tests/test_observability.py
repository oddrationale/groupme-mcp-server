from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

import httpx2
import pytest
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import AsyncOpenTelemetryTransportHttpx2
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    format_span_id,
    format_trace_id,
)

from groupme_mcp_server import observability
from groupme_mcp_server.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def fresh_logger(monkeypatch: pytest.MonkeyPatch) -> Iterator[logging.Logger]:
    """Reset the idempotency guard and restore logger state afterwards."""
    logger = logging.getLogger(observability.LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    propagate = logger.propagate
    monkeypatch.setattr(observability, "_configured", False)
    yield logger
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def make_record(message: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name=observability.LOGGER_NAME,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )


def test_configure_is_idempotent(fresh_logger: logging.Logger) -> None:
    before = len(fresh_logger.handlers)
    observability.configure_observability(Settings(log_level="DEBUG"))
    observability.configure_observability(Settings(log_level="DEBUG"))
    assert len(fresh_logger.handlers) == before + 1
    assert fresh_logger.level == logging.DEBUG


def test_handler_targets_stderr_and_formats_trace_fields(
    fresh_logger: logging.Logger,
) -> None:
    observability.configure_observability(Settings())
    handler = fresh_logger.handlers[-1]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr  # stdout would corrupt the stdio transport
    record = make_record()
    for log_filter in handler.filters:
        assert isinstance(log_filter, logging.Filter)
        assert log_filter.filter(record)
    line = handler.format(record)
    assert "trace_id=-" in line
    assert "span_id=-" in line
    assert "\n" not in line


def test_records_do_not_propagate_to_root(fresh_logger: logging.Logger) -> None:
    observability.configure_observability(Settings())
    assert fresh_logger.propagate is False


def test_multiline_messages_are_flattened(fresh_logger: logging.Logger) -> None:
    observability.configure_observability(Settings())
    handler = fresh_logger.handlers[-1]
    record = make_record("line one\nline two")
    assert isinstance(handler, logging.StreamHandler)
    for log_filter in handler.filters:
        assert isinstance(log_filter, logging.Filter)
        log_filter.filter(record)
    line = handler.format(record)
    assert "\n" not in line
    assert "line one\\nline two" in line


def test_exceptions_are_flattened_to_one_line(fresh_logger: logging.Logger) -> None:
    observability.configure_observability(Settings())
    handler = fresh_logger.handlers[-1]
    try:
        msg = "boom"
        raise RuntimeError(msg)  # noqa: TRY301
    except RuntimeError:
        record = make_record("failed")
        record.exc_info = sys.exc_info()
    observability.TraceContextFilter().filter(record)
    line = handler.format(record)
    assert "\n" not in line
    assert "RuntimeError" in line


def test_filter_defaults_without_active_span() -> None:
    record = make_record()
    assert observability.TraceContextFilter().filter(record) is True
    assert record.__dict__["otel_trace_id"] == "-"
    assert record.__dict__["otel_span_id"] == "-"


def test_filter_injects_ids_from_active_span() -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    tracer = provider.get_tracer("test")
    record = make_record()
    with tracer.start_as_current_span("work") as span:
        observability.TraceContextFilter().filter(record)
    context = span.get_span_context()
    assert record.__dict__["otel_trace_id"] == format_trace_id(context.trace_id)
    assert record.__dict__["otel_trace_id"] != "-"
    assert record.__dict__["otel_span_id"] != "-"


def test_filter_uses_valid_context_of_non_recording_span() -> None:
    context = SpanContext(
        trace_id=0x0123456789ABCDEF0123456789ABCDEF,
        span_id=0x0123456789ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.DEFAULT),  # sampled out, still correlatable
    )
    record = make_record()
    with trace.use_span(NonRecordingSpan(context)):
        observability.TraceContextFilter().filter(record)
    assert record.__dict__["otel_trace_id"] == format_trace_id(context.trace_id)
    assert record.__dict__["otel_span_id"] == format_span_id(context.span_id)


def test_tracing_skipped_without_endpoint(
    fresh_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fresh_logger
    calls: list[object] = []
    monkeypatch.setattr(observability, "set_tracer_provider", calls.append)
    observability.configure_observability(Settings())
    assert calls == []


def test_tracing_enabled_with_endpoint(
    fresh_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fresh_logger
    calls: list[object] = []
    monkeypatch.setattr(observability, "set_tracer_provider", calls.append)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.test:4318")
    observability.configure_observability(Settings())
    assert len(calls) == 1
    provider = calls[0]
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes["service.name"] == "groupme-mcp-server"
    provider.shutdown()


@pytest.mark.parametrize("disabled", ["1", "true", "TRUE", " yes ", "on"])
def test_tracing_skipped_when_sdk_disabled(
    fresh_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch, disabled: str
) -> None:
    del fresh_logger
    calls: list[object] = []
    monkeypatch.setattr(observability, "set_tracer_provider", calls.append)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.test:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", disabled)
    observability.configure_observability(Settings())
    assert calls == []


def test_tracing_runs_when_disabled_flag_is_falsy(
    fresh_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fresh_logger
    calls: list[object] = []
    monkeypatch.setattr(observability, "set_tracer_provider", calls.append)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.test:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    observability.configure_observability(Settings())
    assert len(calls) == 1


def test_instrumented_async_transport_wraps_httpx2() -> None:
    transport = observability.instrumented_async_transport()
    assert isinstance(transport, AsyncOpenTelemetryTransportHttpx2)
    assert isinstance(transport, httpx2.AsyncBaseTransport)


def test_transport_registers_token_header_for_sanitization() -> None:
    observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-Access-Token"


def test_sanitize_fields_are_appended_not_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "Authorization")
    observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "Authorization,X-Access-Token"


def test_sanitize_fields_untouched_when_already_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "x-access-token,Authorization")
    observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "x-access-token,Authorization"


def test_sanitize_fields_untouched_when_regex_covers_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "X-.*,")
    observability.instrumented_async_transport()
    # Normalized (empty trailing field removed), but no token rule appended.
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-.*"


def test_sanitize_fields_appended_when_pattern_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A substring-style lookalike must not count as coverage: this regex
    # never matches the real header name, so the exact rule is appended.
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "not-x-access-token-extra")
    observability.instrumented_async_transport()
    expected = "not-x-access-token-extra,X-Access-Token"
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == expected


def test_invalid_sanitize_regexes_are_dropped_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # An invalid field would make OTel's joined regex fail to compile at
    # capture time, so it is dropped rather than kept.
    logger = logging.getLogger(observability.LOGGER_NAME)
    monkeypatch.setattr(logger, "propagate", True)  # let caplog see the record
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "(,X-.*")
    with caplog.at_level(logging.WARNING, logger=observability.LOGGER_NAME):
        observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-.*"
    assert "Dropping invalid regex" in caplog.text


def test_fields_invalid_only_in_combination_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "(?i)foo" compiles on its own but breaks OTel's joined alternation
    # ("Authorization|(?i)foo" is a re.error), so it must be dropped.
    logger = logging.getLogger(observability.LOGGER_NAME)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "Authorization,(?i)foo")
    observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "Authorization,X-Access-Token"


def test_only_invalid_sanitize_regexes_still_get_token_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = logging.getLogger(observability.LOGGER_NAME)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "(")
    observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-Access-Token"


async def test_token_header_is_redacted_in_captured_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: with header capture on, the token never reaches a span."""
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST", "X-.*")
    observability._ensure_token_header_sanitized()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    inner = httpx2.MockTransport(lambda _: httpx2.Response(200, json={}))
    transport = AsyncOpenTelemetryTransportHttpx2(inner, tracer_provider=provider)
    async with httpx2.AsyncClient(
        transport=transport, base_url="https://api.groupme.test"
    ) as client:
        await client.get("/v3/users/me", headers={"X-Access-Token": "super-secret"})
    (span,) = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert attributes["http.request.header.x_access_token"] == ("[REDACTED]",)
    assert "super-secret" not in str(attributes)
