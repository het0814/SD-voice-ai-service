"""
Conversation Manager for Specialist Directory Verification.

Manages the structured 7-question protocol, tracks conversation state,
detects intents (voicemail, refusal, transfer), and provides dynamic
instructions to the LLM agent as the call progresses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.config import get_settings
from src.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class ConversationState(str, Enum):
    """High-level phases of the verification call."""
    GREETING = "greeting"
    QUESTIONS = "questions"
    CLARIFICATION = "clarification"
    WRAP_UP = "wrap_up"
    ENDED = "ended"


class CallEndReason(str, Enum):
    """Why the call ended — used for analytics and retry logic."""
    COMPLETED = "completed"
    VOICEMAIL = "voicemail"
    REFUSED = "refused"
    TRANSFERRED = "transferred"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class VerificationQuestion:
    """A single question in the verification protocol."""
    id: str
    topic: str
    prompt: str
    follow_up: str  # What to ask if the answer is unclear
    answered: bool = False
    answer: Optional[str] = None
    extracted_data: dict[str, Any] = field(default_factory=dict)


# The 7 verification questions — ordered for natural conversation flow
VERIFICATION_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "accepting_patients",
        "topic": "New Patient Capacity",
        "prompt": "Are you currently accepting new patient referrals?",
        "follow_up": "Just to clarify, are you open to new patients at this time, or is there a waitlist?",
    },
    {
        "id": "insurance_plans",
        "topic": "Insurance Acceptance",
        "prompt": "Which insurance plans are you currently accepting? We have {existing_insurances} on file — is that still accurate?",
        "follow_up": "Have there been any recent changes to which insurance plans you accept?",
    },
    {
        "id": "wait_time",
        "topic": "Wait Times",
        "prompt": "What's the current wait time for a new patient appointment?",
        "follow_up": "Roughly how many weeks out would a new patient be looking at?",
    },
    {
        "id": "scheduling_process",
        "topic": "Scheduling Process",
        "prompt": "What's the best way for patients to schedule — should they call directly, or is there an online portal?",
        "follow_up": "Is there a specific number or website patients should use to book?",
    },
    {
        "id": "referral_requirements",
        "topic": "Referral Documentation",
        "prompt": "What documentation do you need for a referral? Any specific forms or prior authorization requirements?",
        "follow_up": "Do you require a formal referral letter, or can patients self-refer?",
    },
    {
        "id": "contact_info",
        "topic": "Contact Verification",
        "prompt": "Can I confirm your main office number and fax? We have {existing_phone} on file.",
        "follow_up": "Has your phone number, fax, or office address changed recently?",
    },
    {
        "id": "additional_info",
        "topic": "Additional Updates",
        "prompt": "Is there anything else that's changed recently that we should update in our directory?",
        "follow_up": "Any new providers, changed hours, or other updates we should know about?",
    },
]


class ConversationManager:
    """
    Tracks conversation progress and generates contextual LLM instructions.

    The agent calls `get_current_instructions()` to get dynamic system
    prompts based on where we are in the verification protocol. As the
    agent processes responses, it calls `mark_answered()` to advance
    through the question list.
    """

    def __init__(self, specialist_data: dict[str, Any] | None = None) -> None:
        self.state = ConversationState.GREETING
        self.specialist_data = specialist_data or {}
        self.end_reason: Optional[CallEndReason] = None
        self.transcript_segments: list[dict[str, str]] = []

        # Build question objects from the template
        self.questions = [
            VerificationQuestion(**q) for q in VERIFICATION_QUESTIONS
        ]
        self._current_question_index = 0

        logger.info(
            "conversation_manager_initialized",
            specialist=self.specialist_data.get("name", "unknown"),
        )

    @property
    def current_question(self) -> VerificationQuestion | None:
        """The question we're currently asking, or None if all done."""
        if self._current_question_index >= len(self.questions):
            return None
        return self.questions[self._current_question_index]

    @property
    def progress(self) -> str:
        """Human-readable progress like '3/7'."""
        answered = sum(1 for q in self.questions if q.answered)
        return f"{answered}/{len(self.questions)}"

    @property
    def all_answered(self) -> bool:
        return all(q.answered for q in self.questions)

    def advance_to_questions(self) -> None:
        """Transition from GREETING to QUESTIONS phase."""
        self.state = ConversationState.QUESTIONS
        logger.info("conversation_state_changed", state=self.state.value)

    def mark_answered(
        self,
        question_id: str,
        answer: str,
        extracted_data: dict[str, Any] | None = None,
    ) -> None:
        """Mark a question as answered and advance to the next one."""
        for q in self.questions:
            if q.id == question_id:
                q.answered = True
                q.answer = answer
                q.extracted_data = extracted_data or {}
                logger.info(
                    "question_answered",
                    question_id=question_id,
                    progress=self.progress,
                )
                break

        # Advance index to next unanswered question
        self._advance_to_next_unanswered()

        # If all done, move to wrap-up
        if self.all_answered:
            self.state = ConversationState.WRAP_UP
            logger.info("conversation_state_changed", state=self.state.value)

    def request_clarification(self, question_id: str) -> str | None:
        """Get the follow-up prompt for a question that needs clarification."""
        self.state = ConversationState.CLARIFICATION
        for q in self.questions:
            if q.id == question_id:
                return q.follow_up
        return None

    def end_call(self, reason: CallEndReason) -> None:
        """Mark the conversation as ended."""
        self.state = ConversationState.ENDED
        self.end_reason = reason
        logger.info("conversation_ended", reason=reason.value, progress=self.progress)

    def add_transcript(self, role: str, text: str) -> None:
        """Append a transcript segment."""
        self.transcript_segments.append({"role": role, "text": text})

    def get_full_transcript(self) -> str:
        """Return the full conversation as text."""
        return "\n".join(
            f"{seg['role'].upper()}: {seg['text']}"
            for seg in self.transcript_segments
        )

    def get_current_instructions(self) -> str:
        """
        Generate dynamic LLM instructions based on current conversation state.

        This is the core method — it tells the LLM what to do next based
        on where we are in the verification flow.
        """
        if self.state == ConversationState.GREETING:
            return self._greeting_instructions()
        elif self.state == ConversationState.QUESTIONS:
            return self._question_instructions()
        elif self.state == ConversationState.CLARIFICATION:
            return self._clarification_instructions()
        elif self.state == ConversationState.WRAP_UP:
            return self._wrap_up_instructions()
        else:
            return "The call has ended. Say goodbye politely."

    def get_extracted_data_summary(self) -> dict[str, Any]:
        """Return all extracted data across all questions."""
        result: dict[str, Any] = {}
        for q in self.questions:
            if q.answered and q.extracted_data:
                result[q.id] = {
                    "answer": q.answer,
                    "data": q.extracted_data,
                }
        return result

    # Private instruction builders

    def _greeting_instructions(self) -> str:
        name = self.specialist_data.get("name", "the specialist")
        clinic = self.specialist_data.get("clinic_name", "your office")
        return (
            f"You just called {clinic} ({name}'s office). "
            f"Someone answered the phone. Say a brief, warm greeting like "
            f"'Hi, this is the automated directory verification system calling "
            f"on behalf of {settings.clinic_name}. How are you today?' "
            f"Then STOP and wait for them to respond. "
            f"Do NOT ask if they have time yet — just greet them and pause."
        )

    def _question_instructions(self) -> str:
        q = self.current_question
        if q is None:
            return self._wrap_up_instructions()

        # Substitute specialist data into the prompt template
        prompt = q.prompt
        existing_data = self.specialist_data.get("current_data", {})
        if "{existing_insurances}" in prompt:
            insurances = existing_data.get("insurances", [])
            prompt = prompt.replace(
                "{existing_insurances}",
                ", ".join(insurances) if insurances else "no specific plans",
            )
        if "{existing_phone}" in prompt:
            phone = self.specialist_data.get("phone", "the number on file")
            prompt = prompt.replace("{existing_phone}", phone)

        answered_summary = ""
        answered_qs = [q for q in self.questions if q.answered]
        if answered_qs:
            answered_summary = (
                "\n\nYou've already covered: "
                + ", ".join(q.topic for q in answered_qs)
                + "."
            )

        return (
            f"You are on question {self._current_question_index + 1} of {len(self.questions)}. "
            f"Topic: {q.topic}.\n\n"
            f"Ask naturally: {prompt}\n\n"
            f"Listen to their response carefully. If the answer is clear, "
            f"acknowledge it and move to the next question. If the answer "
            f"is vague or unclear, ask a follow-up to clarify."
            f"{answered_summary}"
        )

    def _clarification_instructions(self) -> str:
        q = self.current_question
        if q is None:
            return self._wrap_up_instructions()
        return (
            f"The previous answer about {q.topic} was unclear. "
            f"Ask this follow-up naturally: {q.follow_up}\n\n"
            f"If they still can't answer, that's fine — acknowledge it and move on."
        )

    def _wrap_up_instructions(self) -> str:
        return (
            "You've covered all the verification questions. "
            "Thank them for their time, let them know the directory will be updated, "
            "and say goodbye. Keep it brief and warm."
        )

    def _advance_to_next_unanswered(self) -> None:
        """Move the index to the next unanswered question."""
        while (
            self._current_question_index < len(self.questions)
            and self.questions[self._current_question_index].answered
        ):
            self._current_question_index += 1

        # Back to questions state if we were clarifying
        if self.state == ConversationState.CLARIFICATION and self.current_question:
            self.state = ConversationState.QUESTIONS
