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
import asyncio
from dotenv import load_dotenv

load_dotenv(".env.local")

from livekit import agents, rtc
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import noise_cancellation, silero, elevenlabs, openai, deepgram
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from src.config import get_settings
from src.logging_config import setup_logging, get_logger, call_id_var, generate_trace_id
from src.services.conversation_manager import (
    ConversationManager,
    ConversationState,
    CallEndReason,
)

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

CONVERSATION FLOW:
- First, greet them and ask how they are
- After they respond, ask if they have about 2 minutes for a quick directory verification
- If they say yes, begin the verification questions one at a time
- If they say no, ask when would be a better time and end politely

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

    Uses ConversationManager to drive a structured 7-question protocol.
    LLM function tools let the model signal when questions are answered,
    when clarification is needed, or when the call should end.
    """

    def __init__(self, conversation: ConversationManager) -> None:
        super().__init__(instructions=SYSTEM_INSTRUCTIONS)
        self.conversation = conversation
        logger.info("VerificationAgent initialized")

    async def update_instructions(self) -> None:
        """Refresh agent instructions based on conversation progress."""
        context = self.conversation.get_current_instructions()
        await super().update_instructions(SYSTEM_INSTRUCTIONS + "\n\nCURRENT CONTEXT:\n" + context)


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

    call_id = ""
    if session.room and session.room.name.startswith("verify-"):
        call_id = session.room.name.replace("verify-", "")

    # Load specialist data
    specialist_data = {}
    if call_id:
        from src.db import get_db
        db = get_db()
        try:
            call_res = db.client.table("verification_calls").select("specialist_id").eq("id", call_id).execute()
            if call_res.data:
                sp_id = call_res.data[0]["specialist_id"]
                sp_data = await db.get_specialist(sp_id)
                if sp_data:
                    specialist_data = sp_data
                    specialist_data["call_id"] = call_id
        except Exception as e:
            logger.error("failed_to_fetch_specialist", error=str(e))

    # Initialize conversation manager with specialist context
    conversation = ConversationManager(specialist_data=specialist_data)
    agent = VerificationAgent(conversation=conversation)

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
            agent=agent,
            room=session.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        )

        # Event: Agent finished speaking
        @agent_session.on("agent_speech_committed")
        def on_agent_speech_committed(msg: rtc.ChatMessage):
            if msg.message:
                conversation.add_transcript("agent", msg.message)

        # Event: User finished speaking
        @agent_session.on("user_speech_committed")
        def on_user_speech_committed(msg: rtc.ChatMessage):
            if msg.message:
                conversation.add_transcript("user", msg.message)

        # We save the transcript during the job shutdown phase to prevent task cancellation issues.
        async def on_shutdown():
            logger.info("agent_shutting_down", call_id=call_id)
            if call_id:
                # Build the final transcript directly from the LiveKit managed history
                transcript_text = ""
                if hasattr(agent_session, "history"):
                    lines = []
                    for msg in agent_session.history.messages():
                        if msg.role in ("user", "assistant"):
                            content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
                            if content_str.strip():
                                lines.append(f"{msg.role.upper()}: {content_str}")
                    transcript_text = "\n".join(lines)
                
                if not transcript_text:
                    transcript_text = conversation.get_full_transcript()
                
                # Fetch orchestrator and complete the call in DB/Redis
                from src.services.call_orchestrator import CallOrchestrator
                
                logger.info("saving_transcript", length=len(transcript_text))
                orch = CallOrchestrator()
                await orch.initialize()
                try:
                    await orch.call_completed(call_id, transcript=transcript_text)
                    logger.info("transcript_saved_successfully")
                except Exception as e:
                    logger.error("error_saving_transcript", error=str(e))
                finally:
                    await orch.close()

        # Register the shutdown callback with the JobContext
        session.add_shutdown_callback(on_shutdown)

        # Transition to greeting and generate the opening
        greeting_instructions = conversation.get_current_instructions()
        await agent_session.generate_reply(
            instructions=greeting_instructions
        )

        # Move to questions phase after greeting
        conversation.advance_to_questions()
        await agent.update_instructions()

        logger.info("agent_started_successfully", trace_id=trace_id)

    except Exception as e:
        logger.error("agent_session_error", trace_id=trace_id, error=str(e))
        raise

# Entry Point
if __name__ == "__main__":
    agents.cli.run_app(server)
