"""
FastAPI API Server.

REST API for managing verification calls, specialist directory,
and the human review queue. Runs separately from the LiveKit agent.

Start with:
    uvicorn src.api_server:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
load_dotenv(".env.local")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.calls import router as calls_router
from src.api.specialists import router as specialists_router
from src.api.reviews import router as reviews_router
from src.api.middleware import RequestIdMiddleware, RateLimitMiddleware
from src.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle hooks."""
    logger.info("api_server_starting")
    yield
    logger.info("api_server_stopping")


app = FastAPI(
    title="SD Intelligence Service API",
    description="Voice AI system for healthcare specialist directory verification",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware (order matters — outermost first)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(calls_router)
app.include_router(specialists_router)
app.include_router(reviews_router)


@app.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "sd-intelligence-service"}


@app.get("/", tags=["System"])
async def root() -> dict[str, str]:
    """API root."""
    return {
        "service": "SD Intelligence Service",
        "version": "0.1.0",
        "docs": "/docs",
    }
