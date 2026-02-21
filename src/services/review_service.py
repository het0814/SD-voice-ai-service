"""
Review Service.

Manages the human review queue for low-confidence or conflicting
data updates. Handles creating review items, fetching the queue,
and processing approve/reject actions with audit logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src.db import get_db
from src.logging_config import get_logger
from src.schemas.extraction import ExtractedField
from src.schemas.review import UpdateStatus

logger = get_logger(__name__)


async def queue_for_review(
    call_id: str,
    specialist_id: str,
    field: ExtractedField,
    old_value: Any = None,
) -> dict[str, Any] | None:
    """
    Add an extracted field to the review queue.

    Called when a field's confidence is below the review threshold
    or when a conflict with existing data is detected.
    """
    db = get_db()

    payload = {
        "call_id": call_id,
        "specialist_id": specialist_id,
        "field_name": field.field_name,
        "old_value": _serialize_value(old_value),
        "new_value": _serialize_value(field.value),
        "confidence_score": field.confidence,
        "requires_review": True,
        "status": "pending",
    }

    try:
        result = db.client.table("data_updates").insert(payload).execute()
        if result.data:
            logger.info(
                "queued_for_review",
                update_id=result.data[0]["id"],
                field=field.field_name,
                confidence=field.confidence,
            )
            return result.data[0]
        return None
    except Exception as e:
        logger.error("review_queue_error", field=field.field_name, error=str(e))
        return None


async def get_pending_reviews(
    limit: int = 50,
    specialist_id: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch pending review items, optionally filtered by specialist."""
    db = get_db()

    try:
        query = (
            db.client.table("data_updates")
            .select("*, specialists(name, clinic_name), verification_calls(created_at)")
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if specialist_id:
            query = query.eq("specialist_id", specialist_id)

        result = query.execute()
        return result.data or []
    except Exception as e:
        logger.error("fetch_reviews_error", error=str(e))
        return []


async def approve_update(
    update_id: str,
    reviewed_by: str = "system",
) -> dict[str, Any] | None:
    """
    Approve a pending update and apply it to the specialist record.

    1. Mark the data_update as approved
    2. Apply the change to the specialist's current_data
    3. Log the change in audit_log
    """
    db = get_db()

    try:
        # Fetch the update
        update = (
            db.client.table("data_updates")
            .select("*")
            .eq("id", update_id)
            .single()
            .execute()
        ).data

        if not update or update["status"] != "pending":
            logger.warning("approve_invalid_update", update_id=update_id)
            return None

        # Mark as approved
        db.client.table("data_updates").update({
            "status": "approved",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_by": reviewed_by,
        }).eq("id", update_id).execute()

        # Apply to specialist's current_data
        specialist = (
            db.client.table("specialists")
            .select("current_data")
            .eq("id", update["specialist_id"])
            .single()
            .execute()
        ).data

        if specialist:
            current_data = specialist.get("current_data", {}) or {}
            current_data[update["field_name"]] = update["new_value"]

            db.client.table("specialists").update({
                "current_data": current_data,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", update["specialist_id"]).execute()

        # Audit log
        await _log_audit(
            entity_type="data_update",
            entity_id=update_id,
            action="approve",
            actor=reviewed_by,
            changes={"old": update["old_value"], "new": update["new_value"]},
        )

        logger.info("update_approved", update_id=update_id, field=update["field_name"])
        return update

    except Exception as e:
        logger.error("approve_error", update_id=update_id, error=str(e))
        return None


async def reject_update(
    update_id: str,
    reviewed_by: str = "system",
    reason: str = "",
) -> dict[str, Any] | None:
    """Reject a pending update with an optional reason."""
    db = get_db()

    try:
        result = (
            db.client.table("data_updates")
            .update({
                "status": "rejected",
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "reviewed_by": reviewed_by,
                "rejection_reason": reason,
            })
            .eq("id", update_id)
            .execute()
        )

        await _log_audit(
            entity_type="data_update",
            entity_id=update_id,
            action="reject",
            actor=reviewed_by,
            changes={"reason": reason},
        )

        logger.info("update_rejected", update_id=update_id, reason=reason)
        return result.data[0] if result.data else None

    except Exception as e:
        logger.error("reject_error", update_id=update_id, error=str(e))
        return None


async def auto_apply_update(
    call_id: str,
    specialist_id: str,
    field: ExtractedField,
    old_value: Any = None,
) -> dict[str, Any] | None:
    """
    Directly apply a high-confidence update without human review.

    Creates the data_update record as 'approved' and applies the change.
    """
    db = get_db()

    try:
        # Create pre-approved record
        payload = {
            "call_id": call_id,
            "specialist_id": specialist_id,
            "field_name": field.field_name,
            "old_value": _serialize_value(old_value),
            "new_value": _serialize_value(field.value),
            "confidence_score": field.confidence,
            "requires_review": False,
            "status": "approved",
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reviewed_by": "auto",
        }
        result = db.client.table("data_updates").insert(payload).execute()

        # Apply to specialist
        specialist = (
            db.client.table("specialists")
            .select("current_data")
            .eq("id", specialist_id)
            .single()
            .execute()
        ).data

        if specialist:
            current_data = specialist.get("current_data", {}) or {}
            current_data[field.field_name] = field.value

            db.client.table("specialists").update({
                "current_data": current_data,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", specialist_id).execute()

        await _log_audit(
            entity_type="specialist",
            entity_id=specialist_id,
            action="auto_update",
            actor="system",
            changes={"field": field.field_name, "old": old_value, "new": field.value},
        )

        logger.info(
            "auto_applied",
            field=field.field_name,
            confidence=field.confidence,
            specialist_id=specialist_id,
        )
        return result.data[0] if result.data else None

    except Exception as e:
        logger.error("auto_apply_error", field=field.field_name, error=str(e))
        return None


async def _log_audit(
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str = "system",
    changes: dict[str, Any] | None = None,
) -> None:
    """Write an immutable audit log entry."""
    db = get_db()
    try:
        db.client.table("audit_log").insert({
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "actor": actor,
            "changes": changes or {},
        }).execute()
    except Exception as e:
        logger.error("audit_log_error", entity_id=entity_id, error=str(e))


def _serialize_value(value: Any) -> Any:
    """Ensure a value is JSON-serializable for Supabase jsonb columns."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        return value
    return str(value)
