"""
Call Scheduler Worker.

Polls the Redis call queue and dispatches outbound calls at a
controlled rate. Runs as a long-lived background process.

Start with:
    python -m src.workers.call_scheduler
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
from src.logging_config import setup_logging, get_logger
from src.services.call_orchestrator import CallOrchestrator

setup_logging()
logger = get_logger(__name__)
settings = get_settings()

# How often to check the queue (seconds)
POLL_INTERVAL = 5.0
# Minimum gap between dispatching calls (rate limit: ~1 call/sec)
DISPATCH_INTERVAL = 1.5


class CallSchedulerWorker:
    """
    Continuously polls the call queue and dispatches calls.

    Respects concurrency limits and dispatch rate limits to avoid
    overwhelming the telephony infrastructure.
    """

    def __init__(self) -> None:
        self._running = False
        self._orchestrator = CallOrchestrator()

    async def start(self) -> None:
        """Start the polling loop."""
        await self._orchestrator.initialize()
        self._running = True

        logger.info(
            "call_scheduler_started",
            poll_interval=POLL_INTERVAL,
            max_concurrent=settings.max_concurrent_calls,
        )

        while self._running:
            try:
                dispatched = await self._poll_and_dispatch()
                if not dispatched:
                    # No calls to dispatch — wait before polling again
                    await asyncio.sleep(POLL_INTERVAL)
                else:
                    # Rate limit between dispatches
                    await asyncio.sleep(DISPATCH_INTERVAL)
            except Exception as e:
                logger.error("scheduler_poll_error", error=str(e))
                await asyncio.sleep(POLL_INTERVAL)

    async def stop(self) -> None:
        """Gracefully stop the polling loop."""
        self._running = False
        await self._orchestrator.close()
        logger.info("call_scheduler_stopped")

    async def _poll_and_dispatch(self) -> bool:
        """
        Check for the next call in the queue and dispatch it.

        Returns True if a call was dispatched, False if queue is empty
        or concurrency limit reached.
        """
        call_data = await self._orchestrator.get_next_call()
        if not call_data:
            return False

        call_id = call_data.get("call_id", "")
        logger.info("dispatching_queued_call", call_id=call_id)

        success = await self._orchestrator.dispatch_call(call_id)
        if success:
            logger.info("queued_call_dispatched", call_id=call_id)
        else:
            logger.warning("queued_call_dispatch_failed", call_id=call_id)

        return True


async def main() -> None:
    worker = CallSchedulerWorker()

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()

    def shutdown_handler() -> None:
        logger.info("shutdown_signal_received")
        asyncio.ensure_future(worker.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await worker.start()
    except KeyboardInterrupt:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
