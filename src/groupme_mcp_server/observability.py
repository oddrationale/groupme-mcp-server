"""Logging and OpenTelemetry wiring for the server (imperative shell).

Logging always goes to **stderr**: stdout would corrupt the stdio MCP
transport, and FastMCP only configures its own ``fastmcp.*`` loggers.

Tracing is opt-in: FastMCP already emits spans for every ``tools/call`` via
``opentelemetry-api``, which no-op without an SDK. An SDK ``TracerProvider``
with an OTLP exporter is installed only when ``OTEL_EXPORTER_OTLP_ENDPOINT``
is set and ``OTEL_SDK_DISABLED`` is not truthy.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import TYPE_CHECKING

import httpx2
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import AsyncOpenTelemetryTransportHttpx2
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import format_span_id, format_trace_id, set_tracer_provider

if TYPE_CHECKING:
    from groupme_mcp_server.settings import Settings

LOGGER_NAME = "groupme_mcp_server"
"""Root of the server's logger hierarchy."""

_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "trace_id=%(otel_trace_id)s span_id=%(otel_span_id)s %(message)s"
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})

_SANITIZE_HEADERS_ENV = "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS"
_TOKEN_HEADER = "X-Access-Token"  # noqa: S105 - a header *name*, not a credential

_configured = False


class TraceContextFilter(logging.Filter):
    """Inject the current OTel trace/span ids into every log record.

    When no span context is active the fields default to ``"-"`` so the
    single-line format stays stable. Non-recording spans with a valid
    context (e.g. sampled-out traces) still contribute their ids.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach ``otel_trace_id`` / ``otel_span_id`` to ``record``.

        Args:
            record: The record being emitted.

        Returns:
            Always ``True``; the filter only annotates.
        """
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            record.otel_trace_id = format_trace_id(context.trace_id)
            record.otel_span_id = format_span_id(context.span_id)
        else:
            record.otel_trace_id = "-"
            record.otel_span_id = "-"
        return True


class SingleLineFormatter(logging.Formatter):
    """A formatter that flattens records (tracebacks included) to one line."""

    def format(self, record: logging.LogRecord) -> str:
        """Format ``record``, replacing newlines with an escaped marker.

        Args:
            record: The record being emitted.

        Returns:
            The formatted message, guaranteed newline-free.
        """
        line = super().format(record)
        return line.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(SingleLineFormatter(_LOG_FORMAT))
    handler.addFilter(TraceContextFilter())
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(handler)
    logger.setLevel(settings.log_level)
    # Keep records out of the root logger: no duplicates, no stdout handlers.
    logger.propagate = False


def _tracing_enabled() -> bool:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    return os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() not in _TRUTHY


def _configure_tracing() -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": "groupme-mcp-server"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    set_tracer_provider(provider)


def configure_observability(settings: Settings) -> None:
    """Configure logging and (when enabled) tracing exactly once.

    Idempotent: called both at ``server.py`` import time and from
    ``__main__.main``; repeat calls are no-ops.

    Args:
        settings: The runtime settings (supplies the log level).
    """
    global _configured  # noqa: PLW0603 - process-wide idempotency guard
    if _configured:
        return
    _configured = True
    _configure_logging(settings)
    if _tracing_enabled():
        _configure_tracing()


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error:
        return False
    return True


def _sanitize_covers_token(fields: list[str]) -> bool:
    """Report whether existing sanitize-fields already redact the token header.

    Mirrors OTel's own matching (the comma-separated fields are joined into
    one case-insensitive regex and ``search``-ed against the header name), so
    only patterns that would actually redact ``X-Access-Token`` count.

    Args:
        fields: The individually valid sanitize-field patterns.

    Returns:
        ``True`` if the token header would already be redacted.
    """
    if not fields:
        return False
    pattern = re.compile("|".join(fields), re.IGNORECASE)
    return pattern.search(_TOKEN_HEADER) is not None


def _ensure_token_header_sanitized() -> None:
    """Force OTel header capture to redact the GroupMe token header.

    The httpx instrumentation can capture request headers as span attributes
    when ``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST`` is set
    (e.g. to ``X-.*``). Listing ``X-Access-Token`` in the sanitize-fields
    variable guarantees the secret is recorded as ``[REDACTED]`` instead.
    Invalid regex fields are dropped (OTel would otherwise crash compiling
    the joined pattern at capture time, leaving nothing sanitized). The
    instrumentation reads the variable at transport construction, so this
    runs just before the wrapper is built.
    """
    current = os.environ.get(_SANITIZE_HEADERS_ENV, "")
    fields = [field.strip() for field in current.split(",") if field.strip()]
    # OTel compiles the fields joined with "|", so validate each field in
    # combination with the ones already accepted (e.g. an inline "(?i)foo"
    # compiles alone but not mid-alternation).
    valid_fields: list[str] = []
    dropped: list[str] = []
    for field in fields:
        if _compiles("|".join([*valid_fields, field])):
            valid_fields.append(field)
        else:
            dropped.append(field)
    if dropped:
        logging.getLogger(LOGGER_NAME).warning(
            "Dropping invalid regex(es) from %s: %s", _SANITIZE_HEADERS_ENV, ", ".join(dropped)
        )
    if not _sanitize_covers_token(valid_fields):
        valid_fields.append(_TOKEN_HEADER)
    os.environ[_SANITIZE_HEADERS_ENV] = ",".join(valid_fields)


def instrumented_async_transport() -> httpx2.AsyncBaseTransport:
    """Build the default outbound transport, wrapped for OTel spans.

    Used by ``GroupMeClient`` when no transport is injected, so test
    transports bypass instrumentation cleanly. The access-token header is
    always registered for sanitization so header capture cannot leak it.

    Returns:
        An ``httpx2`` transport that records a client span per request.
    """
    _ensure_token_header_sanitized()
    return AsyncOpenTelemetryTransportHttpx2(httpx2.AsyncHTTPTransport())
