"""
API Router — Call Management Endpoints.

Handles scheduling, initiating, and querying verification calls.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db import get_db
from src.logging_config import get_logger
from src.services.call_orchestrator import CallOrchestrator

logger = get_logger(__name__)
router = APIRouter(prefix="/calls", tags=["Calls"])

# Shared orchestrator instance (initialized on first use)
_orchestrator: CallOrchestrator | None = None


async def _get_orchestrator() -> CallOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = CallOrchestrator()
        await _orchestrator.initialize()
    return _orchestrator


class ScheduleCallRequest(BaseModel):
    specialist_id: str
    priority: float = 0.0


class ScheduleCallResponse(BaseModel):
    call_id: str
    status: str = "queued"


@router.post("/schedule", response_model=ScheduleCallResponse)
async def schedule_call(body: ScheduleCallRequest) -> ScheduleCallResponse:
    """Add a specialist to the outbound call queue."""
    orchestrator = await _get_orchestrator()

    try:
        call_id = await orchestrator.schedule_call(
            specialist_id=body.specialist_id,
            priority=body.priority,
        )
        return ScheduleCallResponse(call_id=call_id)
    except Exception as e:
        logger.error("schedule_call_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/initiate/{specialist_id}")
async def initiate_call(specialist_id: str) -> dict[str, Any]:
    """Schedule and immediately dispatch a call to a specialist."""
    orchestrator = await _get_orchestrator()

    try:
        call_id = await orchestrator.schedule_call(
            specialist_id=specialist_id,
            priority=10.0,  # High priority for manual dispatch
        )
        success = await orchestrator.dispatch_call(call_id)

        return {
            "call_id": call_id,
            "status": "dispatched" if success else "failed",
            "specialist_id": specialist_id,
        }
    except Exception as e:
        logger.error("initiate_call_error", specialist_id=specialist_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{call_id}/status")
async def get_call_status(call_id: str) -> dict[str, Any]:
    """Get the current status and transcript of a call."""
    db = get_db()

    try:
        result = (
            db.client.table("verification_calls")
            .select("*")
            .eq("id", call_id)
            .single()
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Call not found")
        return result.data
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_call_status_error", call_id=call_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/queue/stats")
async def get_queue_stats() -> dict[str, Any]:
    """Get current call queue statistics."""
    orchestrator = await _get_orchestrator()

    return {
        "queue_size": await orchestrator.get_queue_size(),
        "active_calls": await orchestrator.get_active_count(),
    }
