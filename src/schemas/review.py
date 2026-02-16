"""
Data models for human review queue.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel


class UpdateStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ReviewItemResponse(BaseModel):
    id: UUID
    call_id: UUID
    specialist_id: UUID
    field_name: str
    old_value: Optional[Any] = None
    new_value: Any
    confidence_score: float
    status: UpdateStatus
    created_at: datetime

    class Config:
        from_attributes = True


class ReviewAction(BaseModel):
    """Action taken by a human reviewer."""
    status: UpdateStatus  # APPROVED or REJECTED
    rejection_reason: Optional[str] = None
    reviewed_by: Optional[str] = None
