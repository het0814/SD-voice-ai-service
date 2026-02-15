"""
Structured JSON logging with correlation IDs.

Uses structlog to produce machine-parseable JSON logs in production
and human-readable colored output in development. Every log entry
automatically includes a ``trace_id`` for cross-component correlation.

Usage:
    from src.logging_config import get_logger

    logger = get_logger(__name__)
    logger.info("call_started", call_id="abc-123", specialist_id="sp-456")
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

from src.config import get_settings

# ── Context variable for per-request / per-call trace IDs ────────
# Set this at the start of an API request or call session so every
# log entry in that context automatically includes the trace ID.
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
call_id_var: ContextVar[str] = ContextVar("call_id", default="")


def _inject_context_vars(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Inject trace_id and call_id from context vars into every log entry."""
    trace_id = trace_id_var.get("")
    if trace_id:
        event_dict["trace_id"] = trace_id

    call_id = call_id_var.get("")
    if call_id:
        event_dict["call_id"] = call_id

    return event_dict


def generate_trace_id() -> str:
    """Generate a short, unique trace ID for request/call correlation."""
    return uuid.uuid4().hex[:12]


def setup_logging() -> None:
    """
    Configure structlog and stdlib logging.

    - **Production**: JSON output to stdout (for log aggregators).
    - **Development**: Colored, human-readable console output.
    """
    settings = get_settings()
    is_prod = settings.is_production

    # ── Shared processors applied to every log entry ─────────
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _inject_context_vars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_prod:
        # JSON output for production log ingestion
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        # Pretty, colored output for local development
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── Configure stdlib root logger so third-party libs also
    #    emit structured output through our pipeline ──────────
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # Quiet down noisy third-party loggers
    for noisy in ("httpx", "httpcore", "websockets", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a named, structured logger.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A bound structlog logger with all shared processors attached.
    """
    return structlog.get_logger(name)
