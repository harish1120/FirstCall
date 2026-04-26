from dotenv import load_dotenv
from openai import OpenAI

from app.protocols.loader import get_first_aid_protocol
from app.triage import Severity, get_emergency_number, triage_severity

load_dotenv()

client = OpenAI()

sessions: dict = {}

SYSTEM_PROMPT = """You are FirstCall, a calm and clear emergency first aid assistant.
You are on a live phone call with someone in a medical emergency.

Rules:
- Be calm, clear, and specific. Short sentences only.
- Never tell someone NOT to call 911.
- For CRITICAL emergencies, always start with the 911 escalation message before any guidance.
- Adapt instructions if the caller says they don't understand or asks what's next.
- You are the bridge between the emergency and the ambulance arriving.
- Give ONE instruction at a time. Never list multiple steps at once.                                                                                                              
- End every response with a short prompt like "Tell me when you're done" or "Let me know when that's ready."                                                                      
- Wait for the caller to confirm before moving to the next step.                                                                                                                  
- Keep each response under 2 sentences.  
- If the caller says "done", "ready", "okay", or "next" — move to the next step.                                                                                                  
- If the caller says "repeat" or "again" — repeat the last instruction.                                                                                                           
- If the caller says "help" or "I don't understand" — simplify the instruction.
- For CPR, count out loud with the caller. Say "push... push... push" to set the rhythm.  
"""

CRITICAL_ESCALATION = (
    "This is a 911 emergency. Call 911 right now — I'll stay with you. "
    "Tell the operator what you told me. While you wait, here is what to do: "
)


def build_response(description: str, call_sid: str, country_code: str = "US") -> str:
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

    dynamic_system = SYSTEM_PROMPT + f"""                                                                                                                                                                                                                                                                                                                    
    Current situation:                                        
    - Severity: {severity}                                                                                                                                                            
    - Emergency number: {emergency_number}
    - Protocol to follow: {protocol}                                                                                                                                                  
    """

    if call_sid not in sessions:
        sessions[call_sid] = {}
        messages = [
            {"role": "system", "content": dynamic_system},
            {"role": "user", "content": f"{prefix}\n\nSituation: {description}\n\nProtocol hint: {protocol}"},
        ]
        sessions[call_sid]["messages"] = messages
        sessions[call_sid]["severity"] = severity
        sessions[call_sid]["condition"] = description

    else:
        sessions[call_sid]["messages"].append(
            {"role": "user", "content": description})

    # TODO: stream this response back through TTS
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=300,
        messages=sessions[call_sid]["messages"],
    )
    reply = response.choices[0].message.content
    sessions[call_sid]["messages"].append(
        {"role": "assistant", "content": reply})
    return reply


def get_session_meta(call_sid: str) -> dict:
    return sessions.get(call_sid, {})


if __name__ == "__main__":
    print(build_response("my dad collapsed and isn't breathing", "test_123"))
