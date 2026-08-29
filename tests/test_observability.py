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
from opentelemetry.util.http import SanitizeValue, get_custom_headers

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


def test_tracing_respects_otel_service_name(
    fresh_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    del fresh_logger
    calls: list[object] = []
    monkeypatch.setattr(observability, "set_tracer_provider", calls.append)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.test:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "custom-name")
    observability.configure_observability(Settings())
    provider = calls[0]
    assert isinstance(provider, TracerProvider)
    assert provider.resource.attributes["service.name"] == "custom-name"
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


def test_token_rule_is_prepended_ahead_of_user_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    # The mandatory rule must come first: a later alternative can be
    # swallowed by a preceding pattern, never the other way around.
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "Authorization")
    observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-Access-Token,Authorization"


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


def test_token_rule_added_when_pattern_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A substring-style lookalike must not count as coverage: this regex
    # never matches the real header name, so the exact rule is prepended.
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "not-x-access-token-extra")
    observability.instrumented_async_transport()
    expected = "X-Access-Token,not-x-access-token-extra"
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
    assert "Dropping invalid or incompatible regex" in caplog.text


def test_fields_invalid_only_in_combination_are_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "(?i)foo" compiles on its own but breaks OTel's joined alternation
    # ("Authorization|(?i)foo" is a re.error), so it must be dropped.
    logger = logging.getLogger(observability.LOGGER_NAME)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "Authorization,(?i)foo")
    observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-Access-Token,Authorization"


def test_only_invalid_sanitize_regexes_still_get_token_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = logging.getLogger(observability.LOGGER_NAME)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "(")
    observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-Access-Token"


def test_verbose_comment_field_cannot_swallow_token_rule(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # "(?x)Authorization#comment" compiles alone, but appending
    # "|X-Access-Token" after it would be swallowed by the trailing verbose-
    # mode comment. Prepending forces a re.error ("global flags not at the
    # start"), so the pathological field is dropped and the rule survives.
    logger = logging.getLogger(observability.LOGGER_NAME)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "(?x)Authorization#comment")
    with caplog.at_level(logging.WARNING, logger=observability.LOGGER_NAME):
        observability.instrumented_async_transport()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-Access-Token"
    assert "(?x)Authorization#comment" in caplog.text
    # Proof via OTel's own sanitizer machinery, not a reimplementation.
    sanitizer = SanitizeValue(get_custom_headers(observability._SANITIZE_HEADERS_ENV))
    assert sanitizer.sanitize_header_value("X-Access-Token", "super-secret") == "[REDACTED]"


def test_user_patterns_still_sanitize_after_prepending(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "Authorization,Proxy-Auth.*")
    observability.instrumented_async_transport()
    fields = get_custom_headers(observability._SANITIZE_HEADERS_ENV)
    assert fields == ["X-Access-Token", "Authorization", "Proxy-Auth.*"]
    sanitizer = SanitizeValue(fields)
    assert sanitizer.sanitize_header_value("X-Access-Token", "super-secret") == "[REDACTED]"
    assert sanitizer.sanitize_header_value("authorization", "Bearer abc") == "[REDACTED]"
    assert sanitizer.sanitize_header_value("Proxy-Authorization", "Basic xyz") == "[REDACTED]"
    assert sanitizer.sanitize_header_value("Accept", "application/json") == "application/json"


def test_final_coverage_check_drops_user_fields_as_last_resort(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Belt and braces: the mandatory rule is the first alternative, so no
    # real input reaches this branch — but if the combined pattern ever
    # failed OTel's match semantics, only the mandatory rule may survive.
    logger = logging.getLogger(observability.LOGGER_NAME)
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setattr(observability, "_sanitize_covers_token", lambda _fields: False)
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "Authorization")
    with caplog.at_level(logging.WARNING, logger=observability.LOGGER_NAME):
        observability._ensure_token_header_sanitized()
    assert os.environ[observability._SANITIZE_HEADERS_ENV] == "X-Access-Token"
    assert "Authorization" in caplog.text


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


async def test_token_redacted_in_spans_despite_verbose_user_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a verbose-regex user field must not defeat token redaction."""
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST", "X-.*")
    monkeypatch.setenv(observability._SANITIZE_HEADERS_ENV, "(?x)Authorization#comment")
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
