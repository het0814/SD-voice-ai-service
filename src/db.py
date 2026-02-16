"""
Supabase Database Client.

Provides a singleton instance of the Supabase client and typed helper methods
for common database operations. This abstraction allows us to swap the
underlying client or add logging/metrics later without changing business logic.
"""

from __future__ import annotations

from typing import Any, Optional

from supabase import Client, create_client

from src.config import get_settings
from src.logging_config import get_logger

logger = get_logger(__name__)


class DatabaseClient:
    """Wrapper around the official Supabase Python client."""

    _instance: Optional[DatabaseClient] = None
    _client: Client

    def __new__(cls) -> DatabaseClient:
        """Singleton pattern to ensure only one client instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            settings = get_settings()
            
            if not settings.supabase_url or not settings.supabase_service_key:
                logger.warning(
                    "Supabase credentials missing. Database operations will fail.",
                    url=bool(settings.supabase_url),
                    key=bool(settings.supabase_service_key),
                )
            
            try:
                cls._instance._client = create_client(
                    settings.supabase_url, 
                    settings.supabase_service_key
                )
                logger.info("Supabase client initialized", url=settings.supabase_url)
            except Exception as e:
                logger.error("Failed to initialize Supabase client", error=str(e))
                raise

        return cls._instance

    @property
    def client(self) -> Client:
        """Access the raw Supabase client."""
        return self._client

    async def get_specialist(self, specialist_id: str) -> dict[str, Any] | None:
        """Fetch a specialist by UUID."""
        try:
            response = (
                self.client.table("specialists")
                .select("*")
                .eq("id", specialist_id)
                .single()
                .execute()
            )
            return response.data
        except Exception as e:
            logger.error("Error fetching specialist", id=specialist_id, error=str(e))
            return None

    async def update_specialist(self, specialist_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Update a specialist record."""
        try:
            response = (
                self.client.table("specialists")
                .update(updates)
                .eq("id", specialist_id)
                .execute()
            )
            # Supabase update returns a list, usually with 1 item
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
        except Exception as e:
            logger.error("Error updating specialist", id=specialist_id, error=str(e))
            return None
            
    async def create_call_record(self, specialist_id: str, direction: str = "outbound") -> dict[str, Any] | None:
        """Create a new verification call record."""
        try:
            payload = {
                "specialist_id": specialist_id,
                "direction": direction,
                "status": "queued",
            }
            response = self.client.table("verification_calls").insert(payload).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error("Error creating call record", specialist_id=specialist_id, error=str(e))
            return None

    async def update_call_status(
        self, call_id: str, status: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Update call status and optional metadata (e.g. ended_at, duration)."""
        try:
            updates = {"status": status, **(metadata or {})}
            response = (
                self.client.table("verification_calls")
                .update(updates)
                .eq("id", call_id)
                .execute()
            )
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error("Error updating call status", call_id=call_id, status=status, error=str(e))
            return None


# Global accessor
def get_db() -> DatabaseClient:
    return DatabaseClient()
