"""
Core data models for specialists and directory information.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class SpecialistBase(BaseModel):
    """Base specialist data."""
    name: str
    npi: str
    specialty: str
    clinic_name: str
    phone: str
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    current_data: dict[str, Any] = Field(default_factory=dict)


class SpecialistCreate(SpecialistBase):
    """Schema for creating a new specialist."""
    pass


class SpecialistResponse(SpecialistBase):
    """Schema for specialist response."""
    id: UUID
    created_at: datetime
    updated_at: datetime
    is_verified: bool
    last_verified_at: Optional[datetime] = None
    next_verification_due_at: Optional[datetime] = None

    class Config:
        from_attributes = True
