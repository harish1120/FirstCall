import json
import os
from collections.abc import AsyncGenerator
from typing import Any, Literal

import redis
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.protocols.loader import get_first_aid_protocol
from app.triage import Severity, get_emergency_number, triage_severity

load_dotenv()

client = AsyncOpenAI()
_redis_ssl = os.getenv("REDIS_SSL", "false").lower() == "true"

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"), port=6379, db=0, ssl=_redis_ssl, ssl_cert_reqs="none"
)


class CallState(BaseModel):
    severity: Literal["ROUTINE", "URGENT", "CRITICAL"]
    condition: str
    protocol_step: int
    caller_confirmed: bool
    next_instruction: str
    needs_911: bool
    protocol_complete: bool


class CallSummary(BaseModel):
    condition: str
    steps_completed: int
    key_actions_taken: list[str]
    called_911: bool
    summary: str


async def extract_call_state(
    transcript: str, conversation_history: list[str], country_code: str = "US"
) -> CallState:

    severity = triage_severity(transcript)
    protocol = get_first_aid_protocol(transcript)
    emergency_number = get_emergency_number(country_code)
    history_text = "\n".join(conversation_history) if conversation_history else "No history yet."

    response = await client.beta.chat.completions.parse(
        model="gpt-5.4-nano-2026-03-17",
        messages=[
            {
                "role": "system",
                "content": """You are a medical call state extractor.                                                                                                                                        
                Given a conversation transcript and a first aid protocol, extract the current state.
                Be precise and clinical. Determine which protocol step the caller is on based on the conversation history.
                Never add conversational language.""",
            },
            {
                "role": "user",
                "content": f"""                                                                                                                                                                                
                Transcript: {transcript}
                Conversation so far: {history_text}                                                                                                                                                                                         
                Severity: {severity}
                Emergency number: {emergency_number}                                                                                                                                                                                        
                Protocol: {protocol}                    
                """,
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


SYSTEM_PROMPT = """You are FirstCall, a calm and clear emergency first aid assistant.
You are on a live phone call with someone in a medical emergency.

ABSOLUTE SAFETY RULES — NEVER OVERRIDE THESE:
- If the caller asks "should I call 911?", "should I call emergency services?", or any variation — ALWAYS say YES immediately. No exceptions, no hesitation.
- If severity is CRITICAL, your very first words must always be "Call 911 right now." Never delay this.
- Never position yourself as a replacement for emergency services.
- When in doubt about severity, always recommend calling 911.

Rules:
- Speak like a calm, trained first responder on the phone. Natural sentences, not bullet points read aloud.
- Be concise but not clipped.
- The severity level has already been assessed and is provided to you. Trust it. Do not override it.
- Only mention 911 if severity is CRITICAL. Never bring up 911 for ROUTINE or URGENT cases unless the caller asks.
- For CRITICAL cases, your FIRST response must be ONE short sentence only: "Call 911 right now, I'll stay with you." Nothing else. Wait for them to respond before giving any guidance.
- For ROUTINE and URGENT cases, focus on first aid guidance only.
- Adapt instructions if the caller says they don't understand or asks what's next.
- You are the bridge between the emergency and the ambulance arriving.
- Give ONE instruction at a time. Never list multiple steps at once.
- Keep every response under 2 sentences maximum. If you need to say more, wait for confirmation first.
- End every response with a short prompt like "Tell me when you're done" or "Let me know when that's ready."
- Only ask for confirmation after steps that require physical action (CPR compressions, applying pressure, etc.).
- For informational responses or questions, just speak naturally — don't prompt for confirmation.
- Wait for the caller to confirm before moving to the next step.
- If the caller says "done", "ready", "okay", or "next" — move to the next step.
- If the caller says "repeat" or "again" — repeat the last instruction.
- If the caller says "help" or "I don't understand" — simplify the instruction.
- For CPR, count out loud with the caller. Say "push... push... push" to set the rhythm.
- If the description is vague or missing key details, ask one focused question before giving guidance.
    Example: "Where is the cut and how deep does it look?"
- Never ask more than one question at a time.
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
        print(f"OpenAI error: {e}")
        reply = "I am having trouble connecting. Please call 911 directly."
        yield reply

    session["messages"].append({"role": "assistant", "content": reply})
    save_session(call_sid, session)


def get_session_meta(call_sid: str) -> dict[str, Any]:
    session = get_session(call_sid)
    return {} if session is None else session  # noqa: SIM401


if __name__ == "__main__":

    async def main():
        async for sentence in build_response("my dad collapsed and isn't breathing", "test_123"):
            print(sentence)
