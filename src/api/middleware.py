"""
API Middleware.

Request ID injection, rate limiting, and structured audit logging
for every incoming API request.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.logging_config import get_logger, trace_id_var, generate_trace_id

logger = get_logger(__name__)

# Simple in-memory rate limiter (use Redis for production multi-instance)
_rate_counts: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 100    # requests per window per IP


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID into every request and response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID", generate_trace_id())
        trace_id_var.set(request_id)

        start = time.monotonic()

        response = await call_next(request)

        elapsed_ms = round((time.monotonic() - start) * 1000, 1)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(elapsed_ms)

        logger.info(
            "api_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            request_id=request_id,
        )

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple token-bucket rate limiter per client IP."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # Clean old entries
        _rate_counts[client_ip] = [
            t for t in _rate_counts[client_ip]
            if now - t < RATE_LIMIT_WINDOW
        ]

        if len(_rate_counts[client_ip]) >= RATE_LIMIT_MAX:
            logger.warning("rate_limit_exceeded", client_ip=client_ip)
            return Response(
                content='{"error": "Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
            )

        _rate_counts[client_ip].append(now)
        return await call_next(request)
