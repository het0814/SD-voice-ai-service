"""
Database Seeding Script.

Populates the `specialists` table with sample data for testing.
"""

import asyncio
import os
import sys

# Add project root to path so we can import src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db import get_db
from src.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

SAMPLE_SPECIALISTS = [
    {
        "name": "Dr. Rhonda Alkatib",
        "npi": "1234567890",
        "specialty": "Allergy",
        "clinic_name": "xyz clinic",
        "phone": "+15550101",
        "city": "San Francisco",
        "state": "CA",
        "current_data": {"accepting_new_patients": True, "insurances": ["Blue Cross", "Aetna"]}
    },
    {
        "name": "Dr. Doyle Hansen",
        "npi": "0987654321",
        "specialty": "Dermatology",
        "clinic_name": "abc Area Dermatology",
        "phone": "+15550102",
        "city": "Oakland",
        "state": "CA",
        "current_data": {"accepting_new_patients": False}
    },
    {
        "name": "Dr. Ali Salami",
        "npi": "1122334455",
        "specialty": "Cardiology",
        "clinic_name": "ABC Clinic",
        "phone": "+15550103",
        "city": "Berkeley",
        "state": "CA",
        "current_data": {"accepting_new_patients": True, "wait_time_weeks": 4}
    }
]

async def seed():
    db = get_db()
    
    logger.info("Seeding database...")
    
    for specialist in SAMPLE_SPECIALISTS:
        # Check if exists by NPI
        existing = db.client.table("specialists").select("id").eq("npi", specialist["npi"]).execute()
        
        if existing.data:
            logger.info(f"Skipping {specialist['name']} (already exists)")
        else:
            result = db.client.table("specialists").insert(specialist).execute()
            if result.data:
                logger.info(f"Created {specialist['name']}", id=result.data[0]['id'])
            else:
                logger.error(f"Failed to create {specialist['name']}")

    logger.info("Seeding complete.")

if __name__ == "__main__":
    asyncio.run(seed())
