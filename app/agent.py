from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

from app.protocols.loader import get_first_aid_protocol
from app.triage import Severity, get_emergency_number, triage_severity

load_dotenv()

client = AsyncOpenAI()

sessions: dict[str, Any] = {}

SYSTEM_PROMPT = """You are FirstCall, a calm and clear emergency first aid assistant.
You are on a live phone call with someone in a medical emergency.

Rules:
- Speak like a calm, trained first responder on the phone. Natural sentences, not bullet points read aloud.
- Be concise but not clipped.
- The severity level has already been assessed and is provided to you. Trust it. Do not override it.
- Only mention 911 if severity is CRITICAL. Never bring up 911 for ROUTINE or URGENT cases unless the caller asks.
- For CRITICAL cases, always escalate to 911 before any guidance.
- For ROUTINE and URGENT cases, focus on first aid guidance only.
- Adapt instructions if the caller says they don't understand or asks what's next.
- You are the bridge between the emergency and the ambulance arriving.
- Give ONE instruction at a time. Never list multiple steps at once.
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


async def build_response(description: str, call_sid: str, country_code: str = "US") -> str:
    severity = triage_severity(description)
    emergency_number = get_emergency_number(country_code)
    protocol = get_first_aid_protocol(description)

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

    if call_sid not in sessions:
        sessions[call_sid] = {}
        messages = [
            {"role": "system", "content": dynamic_system},
            {
                "role": "user",
                "content": f"{prefix}\n\nSituation: {description}\n\nProtocol hint: {protocol}",
            },
        ]
        sessions[call_sid]["messages"] = messages
        sessions[call_sid]["severity"] = severity
        sessions[call_sid]["condition"] = description

    else:
        sessions[call_sid]["messages"].append({"role": "user", "content": description})

    try:
        response = await client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=sessions[call_sid]["messages"],
        )
        reply = response.choices[0].message.content or ""
    except Exception as e:
        print(f"OpenAI error: {e}")
        reply = "I am having trouble connecting. Please call 911 directly."

    sessions[call_sid]["messages"].append({"role": "assistant", "content": reply})
    return reply


def get_session_meta(call_sid: str) -> dict[str, Any]:
    return sessions[call_sid] if call_sid in sessions else {}  # noqa: SIM401


def clear_session(call_sid: str) -> None:
    sessions.pop(call_sid, None)


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(build_response("my dad collapsed and isn't breathing", "test_123")))
