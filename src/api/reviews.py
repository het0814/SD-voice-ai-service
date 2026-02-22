"""
API Router — Review Queue Endpoints.

Human review workflow for low-confidence or conflicting data updates.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.logging_config import get_logger
from src.services import review_service

logger = get_logger(__name__)
router = APIRouter(prefix="/review-queue", tags=["Reviews"])


class ReviewActionRequest(BaseModel):
    reviewed_by: str = "admin"
    rejection_reason: str = ""


@router.get("/")
async def get_pending_reviews(
    limit: int = 50,
    specialist_id: str | None = None,
) -> dict[str, Any]:
    """Fetch items pending human review."""
    items = await review_service.get_pending_reviews(
        limit=limit,
        specialist_id=specialist_id,
    )
    return {
        "data": items,
        "total": len(items),
    }


@router.post("/{update_id}/approve")
async def approve_update(
    update_id: str,
    body: ReviewActionRequest | None = None,
) -> dict[str, Any]:
    """Approve a pending data update and apply it to the specialist record."""
    reviewed_by = body.reviewed_by if body else "admin"

    result = await review_service.approve_update(
        update_id=update_id,
        reviewed_by=reviewed_by,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Update not found or already processed")

    return {"status": "approved", "update_id": update_id}


@router.post("/{update_id}/reject")
async def reject_update(
    update_id: str,
    body: ReviewActionRequest,
) -> dict[str, Any]:
    """Reject a pending data update with a reason."""
    result = await review_service.reject_update(
        update_id=update_id,
        reviewed_by=body.reviewed_by,
        reason=body.rejection_reason,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Update not found or already processed")

    return {"status": "rejected", "update_id": update_id}
