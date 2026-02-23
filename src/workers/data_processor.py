"""
Data Processor Worker.

Runs post-call processing: transcript extraction, confidence scoring,
conflict detection, auto-applying high-confidence updates, and queueing
low-confidence items for human review.

Start with:
    python -m src.workers.data_processor
"""

from __future__ import annotations

import asyncio
import signal
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from dotenv import load_dotenv
load_dotenv(".env.local")

from src.config import get_settings
from src.db import get_db
from src.logging_config import setup_logging, get_logger
from src.services.data_extraction import (
    extract_from_transcript,
    fields_needing_review,
    fields_to_apply,
    REJECTION_THRESHOLD,
)
from src.services.review_service import queue_for_review, auto_apply_update

setup_logging()
logger = get_logger(__name__)
settings = get_settings()

# How often to check for completed calls that need processing
POLL_INTERVAL = 10.0


class DataProcessorWorker:
    """
    Monitors for completed calls and runs the data extraction pipeline.

    Flow:
    1. Query Supabase for calls with status = 'completed' that haven't been processed
    2. Run LLM extraction on the transcript
    3. Auto-apply high-confidence fields
    4. Queue low-confidence fields for human review
    5. Mark the call as 'processed'
    """

    def __init__(self) -> None:
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("data_processor_started", poll_interval=POLL_INTERVAL)

        while self._running:
            try:
                processed = await self._process_completed_calls()
                if not processed:
                    await asyncio.sleep(POLL_INTERVAL)
                else:
                    # Small delay between processing multiple calls
                    await asyncio.sleep(1.0)
            except Exception as e:
                logger.error("data_processor_error", error=str(e))
                await asyncio.sleep(POLL_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        logger.info("data_processor_stopped")

    async def _process_completed_calls(self) -> bool:
        """
        Find and process one completed call.

        Returns True if a call was processed.
        """
        db = get_db()

        try:
            # Find completed calls that haven't been processed yet
            # We use a convention: status 'completed' means call ended,
            # we update to 'completed' + transcript present but no data_updates yet
            result = (
                db.client.table("verification_calls")
                .select("id, specialist_id, transcript")
                .eq("status", "completed")
                .not_.is_("transcript", "null")
                .gte("retry_count", 0)
                .order("ended_at", desc=False)
                .limit(1)
                .execute()
            )

            if not result.data:
                return False

            call = result.data[0]
            call_id = call["id"]
            specialist_id = call["specialist_id"]
            transcript = call.get("transcript", "")

            if not transcript:
                logger.warning("empty_transcript", call_id=call_id)
                return False

            # Check if already processed (has data_updates)
            existing = (
                db.client.table("data_updates")
                .select("id")
                .eq("call_id", call_id)
                .limit(1)
                .execute()
            )
            if existing.data:
                # Mark call as processed using retry_count=-1 to avoid enum errors
                await db.update_call_status(call_id, "completed", {"retry_count": -1})
                logger.info("call_already_processed_marking_done", call_id=call_id)
                return True

            logger.info("processing_call", call_id=call_id, specialist_id=specialist_id)

            # Get existing specialist data for conflict detection
            specialist = await db.get_specialist(specialist_id)
            existing_data = specialist.get("current_data", {}) if specialist else {}

            # Run LLM extraction
            extraction = await extract_from_transcript(
                transcript=transcript,
                call_id=call_id,
                specialist_id=specialist_id,
                existing_data=existing_data,
            )
            logger.info("extraction", extraction=extraction)

            # Categorize and process fields
            auto_fields = fields_to_apply(extraction.fields)
            review_fields = fields_needing_review(extraction.fields)

            # Auto-apply high-confidence fields
            for field in auto_fields:
                old_value = existing_data.get(field.field_name)
                await auto_apply_update(
                    call_id=call_id,
                    specialist_id=specialist_id,
                    field=field,
                    old_value=old_value,
                )

            # Queue low-confidence fields for review
            for field in review_fields:
                old_value = existing_data.get(field.field_name)
                await queue_for_review(
                    call_id=call_id,
                    specialist_id=specialist_id,
                    field=field,
                    old_value=old_value,
                )

            # Discard fields below rejection threshold (just log)
            discarded = [
                f for f in extraction.fields
                if f.confidence < REJECTION_THRESHOLD
                and f not in review_fields
            ]
            if discarded:
                logger.info(
                    "fields_discarded",
                    call_id=call_id,
                    count=len(discarded),
                    fields=[f.field_name for f in discarded],
                )

            # Mark call as processed using retry_count=-1 so we don't process it again
            await db.update_call_status(call_id, "completed", {"retry_count": -1})

            # Mark specialist as verified
            if specialist:
                await db.update_specialist(specialist_id, {
                    "is_verified": True,
                    "last_verified_at": "now()",
                })

            logger.info(
                "call_processed",
                call_id=call_id,
                total_fields=len(extraction.fields),
                auto_applied=len(auto_fields),
                queued_for_review=len(review_fields),
                discarded=len(discarded),
            )

            return True

        except Exception as e:
            logger.error("process_call_error", error=str(e))
            return False


async def main() -> None:
    worker = DataProcessorWorker()

    loop = asyncio.get_event_loop()

    def shutdown_handler() -> None:
        logger.info("shutdown_signal_received")
        asyncio.ensure_future(worker.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            pass

    try:
        await worker.start()
    except KeyboardInterrupt:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
