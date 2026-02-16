"""
Data models for verification calls and telephony events.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class CallStatus(str, Enum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RINGING = "ringing"
    CONNECTED = "connected"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    VOICEMAIL = "voicemail"


class CallCreate(BaseModel):
    specialist_id: UUID
    direction: str = "outbound"


class CallResponse(BaseModel):
    id: UUID
    specialist_id: UUID
    status: CallStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    transcript: Optional[str] = None
    recording_url: Optional[str] = None
    failure_reason: Optional[str] = None

    class Config:
        from_attributes = True
