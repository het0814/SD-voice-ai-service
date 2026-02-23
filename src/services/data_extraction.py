"""
Data Extraction Service.

Processes conversation transcripts to extract structured data using LLM
function-calling. Assigns confidence scores to each field and detects
conflicts with existing database records.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from src.config import get_settings
from src.logging_config import get_logger
from src.schemas.extraction import ExtractedField, ExtractionResult

settings = get_settings()
logger = get_logger(__name__)

# Confidence thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.70  # Below this, flag for human review
REJECTION_THRESHOLD = 0.40  # Below this, discard the extraction


# The extraction prompt sent to the LLM after the call ends.
# Uses function-calling to get structured JSON output.
EXTRACTION_PROMPT = """You are a data extraction specialist. Analyze the following conversation transcript between a verification agent and a specialist office staff member.

Extract ALL relevant directory information mentioned in the conversation. For each piece of information, provide:
1. The field name (use the exact keys listed below)
2. The extracted value
3. A confidence score (0.0 to 1.0) based on how clearly and directly the information was stated
4. The exact quote from the transcript that supports this extraction

FIELD KEYS:
- accepting_new_patients: boolean (true/false)
- insurance_plans_accepted: list of insurance plan names
- insurance_plans_removed: list of insurance plans they no longer accept
- wait_time_weeks: number (estimated weeks for new patient appointment)
- scheduling_method: string (e.g., "phone", "online portal", "both")
- scheduling_phone: string (phone number for scheduling)
- scheduling_url: string (URL for online scheduling)
- referral_required: boolean (true/false)
- required_documents: list of required document types
- office_phone: string (main office phone)
- office_fax: string (fax number)
- office_address: string (if changed)
- office_hours: string (if mentioned)
- additional_notes: string (any other relevant updates)

CONFIDENCE SCORING GUIDE:
- 1.0: Explicitly stated with no ambiguity ("Yes, we accept Blue Cross")
- 0.8-0.9: Clearly implied or stated with minor hedging ("I believe we still take Aetna")
- 0.6-0.7: Indirectly stated or partially answered ("We take most major plans")
- 0.4-0.5: Vague or uncertain ("I'm not sure, maybe check our website")
- 0.1-0.3: Inferred from context but not directly stated

TRANSCRIPT:
{transcript}

You MUST return your response ONLY as a JSON object containing a "fields" array, where each element matches this exact structure:
{
  "fields": [
    {
      "field_name": "accepting_new_patients",
      "value": true,
      "confidence": 0.95,
      "source_segment": "Yes, we are taking new patients right now."
    }
  ]
}
Only include fields that were actually discussed and changed or confirmed."""


async def extract_from_transcript(
    transcript: str,
    call_id: str,
    specialist_id: str,
    existing_data: dict[str, Any] | None = None,
) -> ExtractionResult:
    """
    Run LLM extraction on a call transcript and return structured results.

    Args:
        transcript: Full conversation transcript.
        call_id: UUID of the verification call.
        specialist_id: UUID of the specialist.
        existing_data: Current specialist data from DB for conflict detection.

    Returns:
        ExtractionResult with all extracted fields and confidence scores.
    """
    logger.info(
        "extraction_started",
        call_id=call_id,
        specialist_id=specialist_id,
        transcript_length=len(transcript),
    )

    try:
        extracted_fields = await _call_llm_for_extraction(transcript)
    except Exception as e:
        logger.error("extraction_llm_error", call_id=call_id, error=str(e))
        return ExtractionResult(
            call_id=call_id,
            specialist_id=specialist_id,
            fields=[],
            summary=f"Extraction failed: {str(e)}",
        )

    # Run conflict detection if we have existing data
    if existing_data:
        for field in extracted_fields:
            conflict = detect_conflict(field, existing_data)
            if conflict:
                field.source_segment = (
                    f"{field.source_segment or ''} [CONFLICT: {conflict}]"
                )
                # Lower confidence slightly for conflicting values
                field.confidence = max(0.0, field.confidence - 0.1)

    result = ExtractionResult(
        call_id=call_id,
        specialist_id=specialist_id,
        fields=extracted_fields,
        summary=_build_summary(extracted_fields),
    )

    logger.info(
        "extraction_complete",
        call_id=call_id,
        fields_extracted=len(extracted_fields),
        avg_confidence=_avg_confidence(extracted_fields),
    )

    return result


async def _call_llm_for_extraction(transcript: str) -> list[ExtractedField]:
    """
    Call OpenAI with function-calling to extract structured data.

    Uses the chat completions API with a JSON response format to
    get structured extraction results.
    """
    prompt = EXTRACTION_PROMPT.replace("{transcript}", transcript)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "You are a precise data extraction system. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,  # Low temperature for consistent extraction
            },
        )
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"]

    import json
    parsed = json.loads(content)

    # The LLM should return a JSON object with a "fields" array
    raw_fields = parsed.get("fields", parsed.get("extractions", parsed.get("directory_information", [])))
    if isinstance(parsed, list):
        raw_fields = parsed

    fields: list[ExtractedField] = []
    for item in raw_fields:
        try:
            val = item.get("value")
            if val is None:
                val = item.get("extracted_value")

            fields.append(ExtractedField(
                field_name=item.get("field_name", item.get("field", "")),
                value=val,
                confidence=float(item.get("confidence", item.get("confidence_score", 0.5))),
                source_segment=item.get("source_segment", item.get("exact_quote", item.get("quote", None))),
            ))
        except Exception as e:
            logger.warning("skipping_malformed_field", item=item, error=str(e))

    return fields


def detect_conflict(
    field: ExtractedField,
    existing_data: dict[str, Any],
) -> str | None:
    """
    Check if an extracted field conflicts with existing DB data.

    Returns a description of the conflict, or None if no conflict.
    """
    existing_value = existing_data.get(field.field_name)
    if existing_value is None:
        return None  # New field, no conflict

    # Compare values
    if isinstance(existing_value, list) and isinstance(field.value, list):
        # For lists (e.g., insurance plans), check for removals
        removed = set(existing_value) - set(field.value)
        if removed:
            return f"Removed items: {', '.join(str(r) for r in removed)}"
    elif existing_value != field.value:
        return f"Changed from '{existing_value}' to '{field.value}'"

    return None


def fields_needing_review(fields: list[ExtractedField]) -> list[ExtractedField]:
    """Return fields that should be flagged for human review."""
    return [
        f for f in fields
        if f.confidence < REVIEW_THRESHOLD
        or (f.source_segment and "CONFLICT" in (f.source_segment or ""))
    ]


def fields_to_apply(fields: list[ExtractedField]) -> list[ExtractedField]:
    """Return fields confident enough to auto-apply."""
    return [
        f for f in fields
        if f.confidence >= HIGH_CONFIDENCE_THRESHOLD
        and "CONFLICT" not in (f.source_segment or "")
    ]


def _avg_confidence(fields: list[ExtractedField]) -> float:
    if not fields:
        return 0.0
    return round(sum(f.confidence for f in fields) / len(fields), 3)


def _build_summary(fields: list[ExtractedField]) -> str:
    """Build a human-readable summary of extractions."""
    if not fields:
        return "No data extracted from this call."

    high = [f for f in fields if f.confidence >= HIGH_CONFIDENCE_THRESHOLD]
    review = [f for f in fields if REJECTION_THRESHOLD <= f.confidence < REVIEW_THRESHOLD]
    low = [f for f in fields if f.confidence < REJECTION_THRESHOLD]

    parts = [f"Extracted {len(fields)} fields."]
    if high:
        parts.append(f"{len(high)} high-confidence (auto-apply).")
    if review:
        parts.append(f"{len(review)} need human review.")
    if low:
        parts.append(f"{len(low)} too low-confidence (discarded).")

    return " ".join(parts)
