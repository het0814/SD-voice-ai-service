"""
Call Orchestrator Service.

Manages the lifecycle of outbound verification calls: scheduling,
dispatching via LiveKit SIP, tracking state with Redis, retry logic,
and concurrency control.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import redis.asyncio as aioredis

from src.config import get_settings
from src.db import get_db
from src.logging_config import get_logger
from src.schemas.call import CallStatus

settings = get_settings()
logger = get_logger(__name__)

# Redis key prefixes
CALL_QUEUE_KEY = "calls:queue"           # Sorted set (priority queue)
CALL_STATE_KEY = "calls:state:{}"        # Hash per call
ACTIVE_CALLS_KEY = "calls:active"        # Set of currently active call IDs
RETRY_KEY = "calls:retry:{}"             # Retry metadata per call


class CallOrchestrator:
    """
    Manages outbound call dispatch and state tracking.

    Uses Redis sorted sets for priority queuing and hashes for
    per-call state. LiveKit API handles the actual SIP dispatch.
    """

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._lk_api: Any = None

    async def initialize(self) -> None:
        """Connect to Redis and LiveKit API."""
        self._redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        logger.info("call_orchestrator_initialized")

    async def close(self) -> None:
        """Cleanup connections."""
        if self._redis:
            await self._redis.close()

    @property
    def redis(self) -> aioredis.Redis:
        if not self._redis:
            raise RuntimeError("CallOrchestrator not initialized. Call initialize() first.")
        return self._redis

    # -- Scheduling --

    async def schedule_call(
        self,
        specialist_id: str,
        priority: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Add a specialist to the call queue.

        Args:
            specialist_id: UUID of the specialist to call.
            priority: Higher = called sooner. Default 0.0.
            metadata: Extra data passed to the agent (e.g., specialist info).

        Returns:
            The call_id of the created call record.
        """
        db = get_db()

        # Create call record in Supabase
        call = await db.create_call_record(specialist_id)
        if not call:
            raise RuntimeError(f"Failed to create call record for specialist {specialist_id}")

        call_id = call["id"]

        # Store call metadata in Redis
        call_data = {
            "call_id": call_id,
            "specialist_id": specialist_id,
            "status": CallStatus.QUEUED.value,
            "scheduled_at": time.time(),
            "retry_count": 0,
            "metadata": json.dumps(metadata or {}),
        }
        await self.redis.hset(CALL_STATE_KEY.format(call_id), mapping=call_data)

        # Add to priority queue (score = priority, higher = sooner)
        await self.redis.zadd(CALL_QUEUE_KEY, {call_id: priority})

        logger.info(
            "call_scheduled",
            call_id=call_id,
            specialist_id=specialist_id,
            priority=priority,
        )
        return call_id

    async def get_next_call(self) -> dict[str, Any] | None:
        """
        Pop the highest-priority call from the queue.

        Returns None if queue is empty or concurrency limit reached.
        """
        # Check concurrency limit
        active_count = await self.redis.scard(ACTIVE_CALLS_KEY)
        if active_count >= settings.max_concurrent_calls:
            logger.debug(
                "concurrency_limit_reached",
                active=active_count,
                max=settings.max_concurrent_calls,
            )
            return None

        # Pop highest priority (highest score)
        results = await self.redis.zpopmax(CALL_QUEUE_KEY, count=1)
        if not results:
            return None

        call_id, _score = results[0]
        call_data = await self.redis.hgetall(CALL_STATE_KEY.format(call_id))
        return call_data if call_data else None

    # -- Dispatching --

    async def dispatch_call(self, call_id: str) -> bool:
        """
        Dispatch a call via LiveKit SIP.

        Creates a LiveKit room, dispatches the agent, then creates a
        SIP participant to dial the specialist's phone number.
        """
        from livekit.api import LiveKitAPI
        from livekit.protocol.sip import CreateSIPParticipantRequest
        from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

        call_data = await self.redis.hgetall(CALL_STATE_KEY.format(call_id))
        if not call_data:
            logger.error("dispatch_missing_call", call_id=call_id)
            return False

        specialist_id = call_data["specialist_id"]
        metadata = json.loads(call_data.get("metadata", "{}"))

        # Get specialist from DB
        db = get_db()
        specialist = await db.get_specialist(specialist_id)
        if not specialist:
            logger.error("dispatch_missing_specialist", specialist_id=specialist_id)
            await self._update_state(call_id, CallStatus.FAILED, failure_reason="Specialist not found")
            return False

        phone_number = specialist.get("phone", "")
        if not phone_number:
            logger.error("dispatch_no_phone", specialist_id=specialist_id)
            await self._update_state(call_id, CallStatus.FAILED, failure_reason="No phone number")
            return False

        # Room name = unique per call (full UUID)
        room_name = f"verify-{call_id}"

        try:
            async with LiveKitAPI() as api:
                from livekit.protocol.room import CreateRoomRequest

                # Create the room explicitly with metadata so the auto-dispatched agent gets it
                await api.room.create_room(
                    CreateRoomRequest(
                        name=room_name,
                        empty_timeout=600,
                        metadata=json.dumps({
                            **specialist,
                            "call_id": call_id,
                        }),
                    )
                )

                # Create SIP participant (dials the phone)
                await api.sip.create_sip_participant(
                    CreateSIPParticipantRequest(
                        room_name=room_name,
                        sip_trunk_id=settings.sip_outbound_trunk_id,
                        sip_call_to=phone_number,
                        participant_identity=f"phone-{specialist_id[:8]}",
                    )
                )

            # Update state
            await self._update_state(call_id, CallStatus.DISPATCHED)
            await self.redis.sadd(ACTIVE_CALLS_KEY, call_id)
            await self.redis.hset(
                CALL_STATE_KEY.format(call_id),
                "livekit_room", room_name,
            )

            # Update Supabase
            await db.update_call_status(call_id, CallStatus.DISPATCHED.value, {
                "livekit_room_id": room_name,
            })

            logger.info(
                "call_dispatched",
                call_id=call_id,
                room=room_name,
                phone=phone_number,
            )
            return True

        except Exception as e:
            logger.error("dispatch_error", call_id=call_id, error=str(e))
            await self._handle_failure(call_id, str(e))
            return False

    # -- State Management --

    async def call_completed(self, call_id: str, transcript: str = "") -> None:
        """Mark a call as completed and remove from active set."""
        await self._update_state(call_id, CallStatus.COMPLETED)
        await self.redis.srem(ACTIVE_CALLS_KEY, call_id)

        db = get_db()
        await db.update_call_status(call_id, CallStatus.COMPLETED.value, {
            "ended_at": "now()",
            "transcript": transcript,
        })

        logger.info("call_completed", call_id=call_id)

    async def call_failed(self, call_id: str, reason: str = "") -> None:
        """Handle a failed call — retry if attempts remain."""
        await self.redis.srem(ACTIVE_CALLS_KEY, call_id)
        await self._handle_failure(call_id, reason)

    async def get_call_state(self, call_id: str) -> dict[str, Any]:
        """Get current call state from Redis."""
        return await self.redis.hgetall(CALL_STATE_KEY.format(call_id))

    async def get_queue_size(self) -> int:
        """Number of calls waiting in the queue."""
        return await self.redis.zcard(CALL_QUEUE_KEY)

    async def get_active_count(self) -> int:
        """Number of currently active calls."""
        return await self.redis.scard(ACTIVE_CALLS_KEY)

    # -- Retry Logic --

    async def _handle_failure(self, call_id: str, reason: str) -> None:
        """Retry with exponential backoff, or mark as permanently failed."""
        state = await self.redis.hgetall(CALL_STATE_KEY.format(call_id))
        retry_count = int(state.get("retry_count", 0))

        if retry_count < settings.max_retry_attempts:
            # Exponential backoff: 30s, 120s, 480s
            delay = 30 * (4 ** retry_count)
            retry_count += 1

            await self.redis.hset(CALL_STATE_KEY.format(call_id), mapping={
                "retry_count": str(retry_count),
                "status": CallStatus.QUEUED.value,
                "last_failure": reason,
            })

            # Re-queue with lower priority
            await self.redis.zadd(CALL_QUEUE_KEY, {call_id: -retry_count})

            logger.info(
                "call_retry_scheduled",
                call_id=call_id,
                retry=retry_count,
                delay_seconds=delay,
            )
        else:
            # Max retries exceeded
            await self._update_state(call_id, CallStatus.FAILED, failure_reason=reason)

            db = get_db()
            await db.update_call_status(call_id, CallStatus.FAILED.value, {
                "failure_reason": reason,
                "retry_count": retry_count,
            })

            logger.warning(
                "call_permanently_failed",
                call_id=call_id,
                retries=retry_count,
                reason=reason,
            )

    async def _update_state(
        self,
        call_id: str,
        status: CallStatus,
        failure_reason: str = "",
    ) -> None:
        """Update call state in Redis."""
        updates = {"status": status.value}
        if failure_reason:
            updates["failure_reason"] = failure_reason
        await self.redis.hset(CALL_STATE_KEY.format(call_id), mapping=updates)
