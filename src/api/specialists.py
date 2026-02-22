"""
API Router — Specialist Directory Endpoints.

CRUD operations for the specialist directory.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from src.db import get_db
from src.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/specialists", tags=["Specialists"])


@router.get("/")
async def list_specialists(
    limit: int = 50,
    offset: int = 0,
    specialty: str | None = None,
    verified_only: bool = False,
) -> dict[str, Any]:
    """List specialists with optional filtering."""
    db = get_db()

    try:
        query = (
            db.client.table("specialists")
            .select("*", count="exact")
            .order("name")
            .range(offset, offset + limit - 1)
        )

        if specialty:
            query = query.eq("specialty", specialty)
        if verified_only:
            query = query.eq("is_verified", True)

        result = query.execute()

        return {
            "data": result.data or [],
            "total": result.count,
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error("list_specialists_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{specialist_id}")
async def get_specialist(specialist_id: str) -> dict[str, Any]:
    """Get a specialist record with freshness metadata."""
    db = get_db()
    specialist = await db.get_specialist(specialist_id)

    if not specialist:
        raise HTTPException(status_code=404, detail="Specialist not found")

    return specialist


@router.get("/{specialist_id}/calls")
async def get_specialist_calls(
    specialist_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Get recent verification calls for a specialist."""
    db = get_db()

    try:
        result = (
            db.client.table("verification_calls")
            .select("*")
            .eq("specialist_id", specialist_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error("get_specialist_calls_error", specialist_id=specialist_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{specialist_id}/updates")
async def get_specialist_updates(specialist_id: str) -> list[dict[str, Any]]:
    """Get all data updates (extractions) for a specialist."""
    db = get_db()

    try:
        result = (
            db.client.table("data_updates")
            .select("*")
            .eq("specialist_id", specialist_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error("get_specialist_updates_error", specialist_id=specialist_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
