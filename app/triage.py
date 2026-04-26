from enum import Enum


class Severity(str, Enum):
    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    CRITICAL = "CRITICAL"


CRITICAL_KEYWORDS = [
    "not breathing", "cardiac arrest", "heart attack", "choking", "can't breathe",
    "severe bleeding", "won't stop bleeding", "stroke", "face drooping",
    "anaphylaxis", "throat closing", "allergic reaction", "seizure", "convulsing",
    "poisoning", "overdose", "drowning", "unresponsive", "unconscious",
]

URGENT_KEYWORDS = [
    "broken bone", "fracture", "deep cut", "laceration", "moderate burn",
    "head injury", "fell", "high fever", "chest pain",
]


def triage_severity(description: str) -> Severity:
    """Rule-based triage gate. HITL — not delegated to the LLM."""
    text = description.lower()
    if any(kw in text for kw in CRITICAL_KEYWORDS):
        return Severity.CRITICAL
    if any(kw in text for kw in URGENT_KEYWORDS):
        return Severity.URGENT
    return Severity.ROUTINE


def get_emergency_number(country_code: str) -> str:
    numbers = {
        "US": "911", "CA": "911",
        "GB": "999",
        "AU": "000",
        "DEFAULT": "112",
    }
    return numbers.get(country_code.upper(), numbers["DEFAULT"])
