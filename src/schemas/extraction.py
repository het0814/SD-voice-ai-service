"""
Data models for structured data extraction results.
"""

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ExtractedField(BaseModel):
    """A single field extracted from conversation."""
    field_name: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_segment: Optional[str] = None  # The quote that justified this extraction


class ExtractionResult(BaseModel):
    """Full extraction result from a call."""
    call_id: UUID
    specialist_id: UUID
    fields: list[ExtractedField]
    summary: Optional[str] = None
