"""
CLI tool to dispatch an outbound verification call.

Usage:
    python scripts/make_call.py <specialist_id> [--priority 1.0]
    python scripts/make_call.py --phone +15551234567 --name "Dr. Smith"

Examples:
    # Call a specialist by ID (from the DB)
    python scripts/make_call.py abc123-def456

    # Quick test call to a phone number (creates a temporary specialist)
    python scripts/make_call.py --phone +15551234567 --name "Test Office"
"""

import argparse
import asyncio
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv(".env.local")

from src.logging_config import setup_logging, get_logger
from src.services.call_orchestrator import CallOrchestrator
from src.db import get_db

setup_logging()
logger = get_logger(__name__)


async def make_call(
    specialist_id: str | None = None,
    phone: str | None = None,
    name: str = "Test Specialist",
    priority: float = 1.0,
) -> None:
    """Dispatch a single outbound call."""
    orchestrator = CallOrchestrator()
    await orchestrator.initialize()

    try:
        if not specialist_id and phone:
            # Create a temporary specialist record for testing
            db = get_db()
            import random
            result = db.client.table("specialists").insert({
                "name": name,
                "specialty": "General",
                "clinic_name": f"{name}'s Office",
                "phone": phone,
                "npi": f"TEST-{phone[-4:]}-{random.randint(1000, 9999)}",
            }).execute()

            if not result.data:
                print("Failed to create specialist record")
                return

            specialist_id = result.data[0]["id"]
            print(f"Created temporary specialist: {specialist_id}")

        if not specialist_id:
            print("Error: specify --specialist-id or --phone")
            return

        # Schedule the call
        call_id = await orchestrator.schedule_call(
            specialist_id=specialist_id,
            priority=priority,
            metadata={"source": "cli"},
        )
        print(f"Call scheduled: {call_id}")

        # Dispatch immediately
        success = await orchestrator.dispatch_call(call_id)
        if success:
            print(f"Call dispatched successfully!")
            print(f"Monitor at your LiveKit dashboard.")
        else:
            print(f"Call dispatch failed. Check logs for details.")

    finally:
        await orchestrator.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch an outbound verification call")
    parser.add_argument("specialist_id", nargs="?", help="Specialist UUID from DB")
    parser.add_argument("--phone", help="Phone number for quick test call")
    parser.add_argument("--name", default="Test Specialist", help="Name for test specialist")
    parser.add_argument("--priority", type=float, default=1.0, help="Call priority (higher = sooner)")

    args = parser.parse_args()

    if not args.specialist_id and not args.phone:
        parser.error("Provide either specialist_id or --phone")

    asyncio.run(make_call(
        specialist_id=args.specialist_id,
        phone=args.phone,
        name=args.name,
        priority=args.priority,
    ))


if __name__ == "__main__":
    main()
