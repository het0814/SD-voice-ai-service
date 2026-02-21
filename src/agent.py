"""
LiveKit Voice Agent for Specialist Directory Verification.

This is the entry point for the LiveKit Agents framework. It defines the
conversational AI agent that calls specialist offices and collects
directory information (insurance, scheduling, capacity, etc).

Run with:
    python src/agent.py dev          # local development
    python src/agent.py start        # production
    python src/agent.py download-files  # download VAD model files first
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv(".env.local")

from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import noise_cancellation, silero, elevenlabs, openai, deepgram
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.config import get_settings
from src.logging_config import setup_logging, get_logger, call_id_var, generate_trace_id

# Bootstrap logging before anything else
setup_logging()
logger = get_logger(__name__)
settings = get_settings()


# System Prompt
SYSTEM_INSTRUCTIONS = f"""You are an automated directory verification assistant calling on behalf of {settings.clinic_name}. Your job is to verify and update specialist office information in our referral directory.

PERSONALITY:
- Professional, warm, and efficient
- Speak naturally like a friendly office coordinator
- Keep responses concise — this is a phone call, not a chat
- Never use markdown, bullet points, or special formatting in speech

CURRENT TASK:
- You are calling a specialist office to verify their directory information
- Ask about: new patient capacity, accepted insurance plans, scheduling process, required referral documents, wait times, and contact info
- Be conversational — don't read questions like a survey

IMPORTANT RULES:
- If you reach voicemail, leave a brief message and end the call
- If asked who you are, explain you're an automated system verifying directory info
- If they ask to be removed or refuse, thank them politely and end the call
- If transferred, re-introduce yourself to the new person
- Do NOT discuss patient information — you only handle directory data
- Keep the call under 3 minutes
"""


class VerificationAgent(Agent):
    """
    LiveKit Agent that conducts specialist verification calls.
    """

    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_INSTRUCTIONS)
        logger.info("VerificationAgent initialized")


# LiveKit Agent Server Setup
server = agents.AgentServer()


@server.rtc_session()
async def handle_session(session: AgentSession) -> None:
    """
    Called when a new call session starts (inbound or outbound).

    LiveKit dispatches this function for each new room/call. The
    AgentSession wires together STT → LLM → TTS automatically.
    """
    # Generate a trace ID for this call session
    trace_id = generate_trace_id()
    call_id_var.set(trace_id)

    logger.info(
        "call_session_started",
        trace_id=trace_id,
        room=session.room.name if session.room else "unknown",
    )

    try:
        # Configure the voice pipeline
        agent_session = AgentSession(
            # Speech-to-Text: Deepgram Nova-3
            stt=deepgram.STT(model="nova-3-general"),
            # LLM: OpenAI GPT-4o
            llm=openai.LLM(model="gpt-4o-mini"),
            # Text-to-Speech: ElevenLabs
            tts=elevenlabs.TTS(
                model="eleven_flash_v2_5",
                voice_id="cgSgspJ2msm6clMCkdW9",
            ),
            # Voice Activity Detection: Silero
            vad=silero.VAD.load(),
        )

        # Start the agent in the room
        await agent_session.start(
            agent=VerificationAgent(),
            room=session.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        )

        # Generate the opening greeting
        await agent_session.generate_reply(
            instructions="Greet the person who answered. Introduce yourself as the "
            f"automated directory verification system calling on behalf of "
            f"{settings.clinic_name}. Ask if this is a good time for a quick "
            f"2-minute verification of their directory information."
        )

        logger.info("agent_started_successfully", trace_id=trace_id)

    except Exception as e:
        logger.error("agent_session_error", trace_id=trace_id, error=str(e))
        raise


# Entry Point
if __name__ == "__main__":
    agents.cli.run_app(server)
