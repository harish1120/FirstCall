import json
import os
from collections.abc import AsyncGenerator
from typing import Any, Literal

import redis
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.logger import get_logger
from app.protocols.loader import get_first_aid_protocol
from app.triage import Severity, get_emergency_number, triage_severity

load_dotenv()
logger = get_logger("agent")
client = AsyncOpenAI()
_redis_ssl = os.getenv("REDIS_SSL", "false").lower() == "true"

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, ssl=_redis_ssl, ssl_cert_reqs="none"
)
_SIMPLE_ACK = {
    "okay",
    "ok",
    "done",
    "yes",
    "got it",
    "next",
    "alright",
    "ready",
    "sure",
    "yep",
    "yup",
}
_REPEAT_WORDS = {"repeat", "again", "what", "sorry", "understand", "huh"}


class CallState(BaseModel):
    severity: Literal["ROUTINE", "URGENT", "CRITICAL"]
    condition: str
    protocol_step: int
    caller_confirmed: bool
    next_instruction: str
    needs_911: bool
    protocol_complete: bool


class CallSummary(BaseModel):
    severity: Literal["ROUTINE", "URGENT", "CRITICAL"]
    condition: str
    steps_completed: int
    key_actions_taken: list[str]
    called_911: bool
    summary: str


async def extract_call_state(
    transcript: str,
    conversation_history: list[str],
    country_code: str = "US",
    last_state: "CallState | None" = None,
) -> "CallState":
    full_text = " ".join(conversation_history) + " " + transcript
    severity = triage_severity(full_text)
    protocol = get_first_aid_protocol(full_text)
    emergency_number = get_emergency_number(country_code)
    history_text = "\n".join(conversation_history) if conversation_history else "No history yet."

    prior_state = ""
    if last_state:
        prior_state = (
            f"\nPrevious state: step={last_state.protocol_step}, "
            f"condition={last_state.condition}, confirmed={last_state.caller_confirmed}"
        )

    response = await client.beta.chat.completions.parse(
        model="gpt-5.4-nano-2026-03-17",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a medical call state extractor. "
                    "Given a transcript, conversation history, and first aid protocol, extract the current state. "
                    "Use the previous state to determine step progression — only advance the step if the caller confirmed the previous action. "
                    "Be precise and clinical. Never add conversational language."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Latest transcript: {transcript}\n"
                    f"Conversation so far:\n{history_text}\n"
                    f"Severity: {severity}\n"
                    f"Emergency number: {emergency_number}\n"
                    f"Protocol:\n{protocol}"
                    f"{prior_state}"
                ),
            },
        ],
        response_format=CallState,
    )
    result = response.choices[0].message.parsed
    if result is None:
        raise ValueError("Protocol agent failed to extract call state")
    return result


async def generate_call_summary(
    conversation_history: list[str], country_code: str = "US"
) -> CallSummary:
    response = await client.beta.chat.completions.parse(
        model="gpt-5.4-nano-2026-03-17",
        messages=[
            {
                "role": "system",
                "content": """You are a medical call summarizer. Given a complete emergency call transcript, extract a concise structured summary, do not include any personal details this is very important. 
                Focus on what actions were taken and what the responders need to know on arrival.""",
            },
            {
                "role": "user",
                "content": f"""
                Conversation so far: {conversation_history},
                country code: {country_code}""",
            },
        ],
        response_format=CallSummary,
    )

    result = response.choices[0].message.parsed

    if result is None:
        raise ValueError("Summarizer Agent Failed")

    return result


SYSTEM_PROMPT = """You are FirstCall, an emergency first aid voice assistant on a live phone call.

YOUR ROLE: You are the voice — calm, clear, and human. A Protocol Agent analyzes each caller message and tells you exactly what to say next in the "Current call state" block. Your job is to deliver those instructions as a trained first responder would speak, not read from a list.

ABSOLUTE SAFETY RULES — NEVER OVERRIDE:
- If the caller asks whether to call 911 — always say YES immediately. No exceptions.
- If needs_911 is true — your very first words must be "Call 911 right now, I'll stay with you." Nothing else until they respond.
- Never suggest you are a replacement for emergency services.

HOW TO SPEAK:
- Natural spoken sentences only. Never read bullet points aloud.
- One instruction per response. Never list multiple steps at once.
- Two sentences maximum. If you need to say more, stop and wait for a response first.
- After any physical action (CPR, applying pressure, positioning), end with a short prompt: "Tell me when you're done."
- After questions or information, speak naturally — no confirmation prompt needed.

TRUST THE PROTOCOL AGENT:
- The Protocol Agent has determined the severity, condition, and next instruction. Do not override these.
- Deliver next_instruction naturally — you may rephrase for clarity, but do not skip, add, or reorder steps.
- If protocol_complete is true, reassure the caller and tell them to stay calm until help arrives.
- If the caller says "done", "okay", or "next" — deliver the next step from next_instruction.
- If the caller says "repeat" or "again" — repeat the last instruction in simpler words.
- If the caller says "I don't understand" — simplify and slow down.
"""

CRITICAL_ESCALATION = (
    "This is a 9-1-1 emergency. Call 9-1-1 right now — I'll stay with you. "
    "Tell the operator what you told me. While you wait, here is what to do: "
)


def get_session(call_sid):
    data = r.get(call_sid)
    return json.loads(data) if data else None


def save_session(call_sid, session_data):
    r.setex(call_sid, 3600, json.dumps(session_data))  # 1 hour TTL


def clear_session(call_sid):
    r.delete(call_sid)


async def build_response(
    description: str, call_sid: str, country_code: str = "US"
) -> AsyncGenerator[str, None]:
    severity = triage_severity(description)
    emergency_number = get_emergency_number(country_code)
    protocol = get_first_aid_protocol(description)
    buffer = ""
    reply = ""
    if severity == Severity.CRITICAL:
        prefix = f"This is a {emergency_number} emergency. Call {emergency_number} right now. "
        prefix += "While you wait, here is what to do: "
    elif severity == Severity.URGENT:
        prefix = "This needs medical attention. Here is what to do right now: "
    else:
        prefix = "Here is what to do: "

    dynamic_system = (
        SYSTEM_PROMPT
        + f"""
    Current situation:
    - Severity: {severity}
    - Emergency number: {emergency_number}
    - Protocol to follow: {protocol}
    """
    )

    session = get_session(call_sid)
    if session is None:
        session = {
            "messages": [
                {"role": "system", "content": dynamic_system},
                {
                    "role": "user",
                    "content": f"{prefix}\n\nSituation: {description}\n\nProtocol hint: {protocol}",
                },
            ],
            "severity": severity,
            "condition": description,
        }

        save_session(call_sid, session)
    else:
        session["messages"].append({"role": "user", "content": description})
        session["severity"] = severity
        save_session(call_sid, session)

    try:
        response = await client.chat.completions.create(
            model="gpt-5.4-mini", messages=session["messages"], stream=True
        )
        async for chunk in response:
            token = chunk.choices[0].delta.content or ""
            buffer += token
            reply += token
            if buffer.endswith(("\n", ".", "!", "?")):
                yield buffer.strip()
                buffer = ""
    except Exception as e:
        logger.error("OpenAI connection failed", extra={"error": str(e)})
        reply = "I am having trouble connecting. Please call 911 directly."
        yield reply

    session["messages"].append({"role": "assistant", "content": reply})
    save_session(call_sid, session)


def get_session_meta(call_sid: str) -> dict[str, Any]:
    session = get_session(call_sid)
    return {} if session is None else session  # noqa: SIM401


def classify_turn(transcript: str) -> str:
    t = transcript.lower().strip().rstrip(".").rstrip("!")
    if t in _SIMPLE_ACK or t.startswith("i'm done") or t.startswith("im done"):
        return "advance"
    if any(w in t for w in _REPEAT_WORDS):
        return "repeat"
    return "new_info"


if __name__ == "__main__":

    async def main():
        async for sentence in build_response("my dad collapsed and isn't breathing", "test_123"):
            print(sentence)
