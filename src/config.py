"""
Application configuration via environment variables.

Uses Pydantic BaseSettings to load and validate all config from env vars
or a .env.local file. Every setting has a sensible default for local
development so the app can start with minimal configuration.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Deployment environment selector."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """
    Central configuration for the SD Voice AI Service.

    Values are loaded from environment variables first, falling back
    to a `.env.local` file in the project root. Secrets should NEVER
    be committed — use `.env.example` as the template.
    """

    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Environment ──────────────────────────────────────────────
    environment: Environment = Environment.DEVELOPMENT

    # ── LiveKit ──────────────────────────────────────────────────
    livekit_url: str = Field(default="", description="LiveKit server WebSocket URL")
    livekit_api_key: str = Field(default="", description="LiveKit API key")
    livekit_api_secret: str = Field(default="", description="LiveKit API secret")

    # ── Twilio SIP Trunk ─────────────────────────────────────────
    sip_outbound_trunk_id: str = Field(default="", description="LiveKit SIP trunk ID for outbound calls")

    # ── AI Model Keys ────────────────────────────────────────────
    deepgram_api_key: str = Field(default="", description="Deepgram API key for STT")
    elevenlabs_api_key: str = Field(default="", description="ElevenLabs API key for TTS")
    openai_api_key: str = Field(default="", description="OpenAI API key for LLM")
    anthropic_api_key: str = Field(default="", description="Anthropic API key (fallback LLM)")

    # ── Supabase ─────────────────────────────────────────────────
    supabase_url: str = Field(default="", description="Supabase project URL")
    supabase_service_key: str = Field(default="", description="Supabase service-role key")

    # ── Redis ────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")

    # ── Caller Identity ──────────────────────────────────────────
    caller_id_number: str = Field(default="+15551234567", description="Outbound caller ID")
    clinic_name: str = Field(default="Referral Network", description="Name used in agent greeting")

    # ── Feature Flags ────────────────────────────────────────────
    feature_human_review: bool = Field(default=True, description="Require human review for low-confidence updates")
    feature_call_recording: bool = Field(default=True, description="Enable call recording")
    feature_fallback_tts: bool = Field(default=True, description="Fall back to Deepgram TTS if ElevenLabs fails")

    # ── Operational Limits ───────────────────────────────────────
    max_concurrent_calls: int = Field(default=10, ge=1, le=200, description="Max concurrent outbound calls")
    max_call_duration_seconds: int = Field(default=300, ge=60, le=900, description="Hard limit per call")
    max_retry_attempts: int = Field(default=3, ge=0, le=10, description="Max retry attempts per call")

    # ── Logging ──────────────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Root log level")

    # ── Derived helpers ──────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.environment == Environment.DEVELOPMENT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return cached Settings instance.

    Using lru_cache ensures we read env vars exactly once, and every
    module that calls ``get_settings()`` gets the same object.
    """
    return Settings()
